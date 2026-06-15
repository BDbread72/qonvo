"""aiohttp 기반 Qonvo WebSocket 서버.

클라이언트 프로토콜(server_client.py)에 1:1 대응:
  연결 → {auth_required} → 클라 {auth} → {auth_ok|auth_fail}
  {join_board,last_seq} → {sync|delta}, 타 멤버에게 {user_join}
  {op,ops} → seq 부여·영속·타 멤버 브로드캐스트 {op,ops,author,seq}
  {ai_request,node_id,params} → {ai_progress,chunk}* → {ai_complete,result}

asyncio 단일 루프에서 동작. AI 는 블로킹이므로 executor 스레드로 실행하고,
청크는 run_coroutine_threadsafe 로 루프에 되돌려 브로드캐스트한다.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Optional

from aiohttp import web

from v.logger import get_logger

from .auth import Authenticator, MEMBER, LEVEL_NAMES
from .board_store import BoardManager, list_boards, safe_board_id
from .oauth import MattermostOAuth
from .session import Registry, Session

logger = get_logger("qonvo.server")

# 사용자별 커서/이름표 색 (세션 id 로 배정)
_USER_COLORS = [
    "#e74c3c", "#3498db", "#2ecc71", "#f39c12", "#9b59b6",
    "#1abc9c", "#e67e22", "#e84393", "#00cec9", "#6c5ce7",
]


class QonvoServer:
    """서버 상태(설정·인증·보드·세션)와 요청 처리를 담당한다."""

    def __init__(self, config: dict):
        self.config = config
        self.auth = Authenticator(config)
        self.boards = BoardManager()
        self.registry = Registry()
        self.router = None  # ProviderRouter (lazy)
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._app = web.Application()
        self._runner: Optional[web.AppRunner] = None

        srv = config["server"]
        self.host = srv.get("host", "0.0.0.0")
        self.port = int(srv.get("port", 9700))
        self.name = srv.get("name", "Qonvo Server")
        self.motd = srv.get("motd", "")
        self.default_model = config.get("ai", {}).get("default_model", "gemini-2.5-flash")

        net = config.get("network", {})
        self.upnp_enabled = bool(net.get("upnp", True))
        self.public_host = net.get("public_host", "") or ""
        self.upnp_lease = int(net.get("upnp_lease", 3600))
        self._upnp = None
        self._upnp_task = None
        self._autosave_task = None
        self.connect_host = None  # 클라에게 안내할 접속 호스트(공인 IP/도메인)

        self._http_tokens: dict = {}  # http_token -> (username, level)

        from .presence import PresenceRegistry
        self.presence = PresenceRegistry()
        self._merri_user_cache: dict = {}   # merri token -> (username, expires)

        self._app.router.add_get("/", self._health)
        self._app.router.add_get("/ws", self.ws_handler)
        self._app.router.add_get("/boards", self._boards_meta)   # 보드 목록 + 노드수
        # qonvo 전용 presence — 앱 켜짐/작업중 하트비트
        self._app.router.add_post("/presence", self._presence_beat)
        self._app.router.add_get("/presence/list", self._presence_list)
        # 첨부(이미지 등) HTTP 전송 — 서버모드에서 보드 이미지를 받고/올림
        self._app.router.add_get("/board/{bid}/manifest", self._attach_manifest)
        self._app.router.add_get("/board/{bid}/attach/{name}", self._attach_get)
        self._app.router.add_put("/board/{bid}/attach/{name}", self._attach_put)
        # OAuth 콜백은 공개 주소여야 한다(브라우저가 redirect 됨). public_host 우선.
        _oauth_host = self.public_host or "localhost"
        public_url = f"http://{_oauth_host}:{self.port}"
        self._oauth = MattermostOAuth(config, self.auth, public_url)
        self._oauth.register(self._app)

    # ---- 인프라 ---------------------------------------------------------
    async def _health(self, request: web.Request) -> web.Response:
        return web.json_response({
            "name": self.name,
            "boards": list_boards(),
            "online": sum(1 for s in self.registry.all_sessions() if s.authed),
            "auth": {
                # 클라가 서버 추가 창에서 "merri로 로그인" 노출 여부를 판단
                "merri": bool(self._oauth.enabled),
                "guests": self.auth.allow_guests,
            },
        })

    async def _boards_meta(self, request: web.Request) -> web.Response:
        """보드 목록 + 노드수(+첨부수). 인증된 http_token 필요."""
        if self._check_http_token(request) is None:
            return web.json_response({"error": "unauthorized"}, status=401)
        out = []
        for bid in list_boards():
            online = len(self.registry.board_members(bid))
            try:
                b = self.boards.get(bid)
                out.append({"id": bid, "nodes": b.node_count(),
                            "attachments": len(b.list_attachments()), "online": online})
            except Exception:
                out.append({"id": bid, "nodes": 0, "attachments": 0, "online": online})
        return web.json_response({"boards": out})

    # ---- qonvo presence -------------------------------------------------
    async def _resolve_merri_user(self, token: str) -> str:
        """merri 토큰을 username 으로 해석(캐시 5분). 실패 시 ""."""
        if not token:
            return ""
        now = time.time()
        c = self._merri_user_cache.get(token)
        if c and c[1] > now:
            return c[0]
        if not self._oauth.enabled:
            return ""
        res = await self._oauth.validate_token(token)
        if not res:
            return ""
        self._merri_user_cache[token] = (res[0], now + 300)
        return res[0]

    async def _presence_beat(self, request: web.Request) -> web.Response:
        """클라 하트비트: {token, status, board}. merri 신원으로 presence 갱신."""
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "bad json"}, status=400)
        user = await self._resolve_merri_user(data.get("token", ""))
        if not user:
            return web.json_response({"error": "unauthorized"}, status=401)
        if data.get("drop"):
            self.presence.drop(user)
        else:
            self.presence.beat(user, data.get("status", "online"), data.get("board", ""), time.time())
        return web.json_response({"ok": True})

    async def _presence_list(self, request: web.Request) -> web.Response:
        """현재 qonvo presence 스냅샷 {username: {status, board}}."""
        user = await self._resolve_merri_user(request.query.get("t", ""))
        if not user:
            return web.json_response({"error": "unauthorized"}, status=401)
        return web.json_response({"presence": self.presence.snapshot(time.time())})

    # ---- 첨부 HTTP (이미지 등) ------------------------------------------
    def _check_http_token(self, request: web.Request):
        """쿼리 t= 토큰을 검증하고 (username, level) 반환. 실패 시 None."""
        return self._http_tokens.get(request.query.get("t", ""))

    async def _attach_manifest(self, request: web.Request) -> web.Response:
        """보드의 첨부 파일명 목록을 반환한다."""
        if self._check_http_token(request) is None:
            return web.json_response({"error": "unauthorized"}, status=401)
        board = self.boards.get(request.match_info["bid"])
        return web.json_response({"attachments": board.list_attachments()})

    async def _attach_get(self, request: web.Request) -> web.StreamResponse:
        """첨부 파일을 내려준다."""
        if self._check_http_token(request) is None:
            return web.Response(status=401, text="unauthorized")
        board = self.boards.get(request.match_info["bid"])
        path = board.attachment_path(request.match_info["name"])
        if path is None or not path.exists():
            return web.Response(status=404, text="not found")
        return web.FileResponse(path)

    async def _attach_put(self, request: web.Request) -> web.Response:
        """첨부 파일을 업로드 받는다(Member 이상)."""
        tok = self._check_http_token(request)
        if tok is None:
            return web.Response(status=401, text="unauthorized")
        if tok[1] < MEMBER:
            return web.Response(status=403, text="read-only")
        board = self.boards.get(request.match_info["bid"])
        name = request.match_info["name"]
        if board.attachment_path(name) is None:
            return web.Response(status=400, text="bad name")
        data = await request.read()
        if board.save_attachment(name, data):
            return web.json_response({"ok": True, "name": name, "size": len(data)})
        return web.Response(status=400, text="save failed")

    def _ensure_router(self):
        if self.router is None:
            from .ai_runner import build_router
            self.router = build_router(self.config)
        return self.router

    # ---- WebSocket ------------------------------------------------------
    async def ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30, max_msg_size=64 * 1024 * 1024)
        await ws.prepare(request)
        sess = Session(ws)
        self.registry.add(sess)
        await sess.send({"type": "auth_required"})
        logger.info("connection opened (sid=%s)", sess.id)

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    await self._on_text(sess, msg.data)
                elif msg.type == web.WSMsgType.ERROR:
                    break
        except Exception as e:
            logger.warning("ws loop error sid=%s: %s", sess.id, e)
        finally:
            await self._on_disconnect(sess)
        return ws

    async def _on_text(self, sess: Session, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return
        mtype = data.get("type", "")

        if mtype == "auth":
            await self._handle_auth(sess, data)
            return
        if not sess.authed:
            await sess.send({"type": "error", "code": "unauth", "message": "not authenticated"})
            return

        if mtype == "join_board":
            await self._handle_join(sess, data)
        elif mtype == "op":
            await self._handle_op(sess, data)
        elif mtype == "ai_request":
            await self._handle_ai(sess, data)
        elif mtype == "ping":
            # 지연 측정: 받은 t 그대로 되돌려 클라가 RTT 계산
            await sess.send({"type": "pong", "t": data.get("t")})
        elif mtype == "presence":
            await self._handle_presence(sess, data)
        elif mtype == "chat":
            await self._handle_chat(sess, data)
        else:
            logger.debug("unknown message type: %s", mtype)

    async def _handle_presence(self, sess: Session, data: dict) -> None:
        """클라가 보고한 핑/커서/활동을 갱신하고 보드에 presence 를 브로드캐스트한다."""
        if "ping" in data:
            try:
                sess.ping = int(data["ping"])
            except Exception:
                pass
        if "cursor" in data:
            sess.cursor = data.get("cursor")  # {x,y} 또는 None
        if "select" in data:
            sess.select = data.get("select")  # {x,y,w,h} 또는 None
        if sess.board_id:
            await self._broadcast_presence(sess.board_id)

    async def _broadcast_presence(self, board_id: str) -> None:
        users = self.registry.presence_list(board_id)
        await self.registry.broadcast(board_id, {"type": "presence", "users": users})

    async def _handle_chat(self, sess: Session, data: dict) -> None:
        """보드 채팅 메시지를 멤버 전원에게 브로드캐스트한다."""
        text = (data.get("text") or "").strip()
        if not text or not sess.board_id:
            return
        await self.registry.broadcast(sess.board_id, {
            "type": "chat", "user": sess.username, "color": sess.color,
            "text": text[:2000], "ts": int(time.time() * 1000),
        })

    async def _handle_auth(self, sess: Session, data: dict) -> None:
        username = (data.get("user") or "").strip()
        password = data.get("pass") or ""

        # merri 프로필 토큰 인증: 토큰을 merri 에 검증해 실제 username 을 얻는다.
        if data.get("merri") and self._oauth.enabled:
            res = await self._oauth.validate_token(password)
            if res is None:
                await sess.send({"type": "auth_fail", "reason": "merri token invalid"})
                return
            username = res[0]
            ok, level, reason = self.auth.access_for(username)
            if not ok:
                await sess.send({"type": "auth_fail", "reason": reason})
                return
        else:
            ok, level, reason = self.auth.authenticate(username, password, time.time())
            if not ok:
                await sess.send({"type": "auth_fail", "reason": reason})
                logger.info("auth fail sid=%s user=%s (%s)", sess.id, username, reason)
                return
        sess.authed = True
        sess.username = username
        sess.level = level
        sess.color = _USER_COLORS[sess.id % len(_USER_COLORS)]
        # 첨부 HTTP 전송용 토큰 발급
        import uuid as _uuid
        http_token = _uuid.uuid4().hex
        self._http_tokens[http_token] = (username, level)
        sess.http_token = http_token
        await sess.send({"type": "auth_ok", "level": level, "boards": list_boards(),
                         "http_token": http_token})
        logger.info("auth ok sid=%s user=%s level=%s(%s)", sess.id, username, level, reason)

    async def _handle_join(self, sess: Session, data: dict) -> None:
        board_id = safe_board_id(data.get("board_id", ""))
        last_seq = int(data.get("last_seq", 0) or 0)
        board = self.boards.get(board_id)
        self.registry.join_board(sess, board.board_id)

        await sess.send(board.snapshot_for_join(last_seq))
        if self.motd:
            await sess.send({"type": "server_msg", "text": self.motd})
        await self.registry.broadcast(
            board.board_id,
            {"type": "user_join", "user": sess.username, "level": sess.level},
            exclude=sess,
        )
        await self._broadcast_presence(board.board_id)
        logger.info("join sid=%s user=%s board=%s", sess.id, sess.username, board.board_id)

    async def _handle_op(self, sess: Session, data: dict) -> None:
        if sess.level < MEMBER:
            await sess.send({"type": "error", "code": "perm", "message": "read-only (Visitor)"})
            return
        if not sess.board_id:
            return
        ops = data.get("ops", []) or []
        if not ops:
            return
        board = self.boards.get(sess.board_id)
        new_seq = board.apply_ops(ops, sess.username)
        await self.registry.broadcast(
            sess.board_id,
            {"type": "op", "ops": ops, "author": sess.username, "seq": new_seq},
            exclude=sess,
        )

    async def _handle_ai(self, sess: Session, data: dict) -> None:
        if sess.level < MEMBER:
            await sess.send({"type": "error", "code": "perm", "message": "read-only (Visitor)"})
            return
        node_id = str(data.get("node_id", ""))
        params = data.get("params", {}) or {}
        model = params.get("model") or self.default_model
        message = params.get("message", "")
        files = params.get("files", [])
        system_prompt = params.get("system_prompt", "")
        options = params.get("options", {})
        board_id = sess.board_id

        try:
            router = self._ensure_router()
        except Exception as e:
            await sess.send({"type": "error", "code": "ai", "message": f"router init failed: {e}"})
            return

        loop = asyncio.get_running_loop()
        accum: list[str] = []

        def on_chunk(text: str):
            accum.append(text)
            snapshot = "".join(accum)
            asyncio.run_coroutine_threadsafe(
                self.registry.broadcast(
                    board_id, {"type": "ai_progress", "node_id": node_id, "chunk": snapshot}
                ),
                loop,
            )

        from .ai_runner import run_ai

        logger.info("ai_request sid=%s node=%s model=%s", sess.id, node_id, model)
        result = await loop.run_in_executor(
            None,
            lambda: run_ai(router, model, message, files, system_prompt, options, on_chunk),
        )

        # AI 생성 이미지를 서버 첨부 파일로 저장하고 상대경로로 doc 에 기록.
        # (base64 를 doc 에 넣으면 snapshot 비대 + 재접속 시 경로 미해석 → 파일로 영속)
        rel_refs = self._persist_ai_images(board_id, result.get("images", []))
        try:
            self.boards.get(board_id).append_assistant_message(
                node_id, result.get("text", ""), rel_refs
            )
        except Exception:
            pass

        # 라이브 표시는 base64 즉시 전송(다운로드 왕복 없이 바로 보이게)
        await self.registry.broadcast(
            board_id,
            {"type": "ai_complete", "node_id": node_id, "result": {
                "text": result.get("text", ""),
                "images": result.get("images", []),
                "tokens_in": result.get("tokens_in", 0),
                "tokens_out": result.get("tokens_out", 0),
            }},
        )
        if result.get("error"):
            await sess.send({"type": "error", "code": "ai", "message": result["error"]})

    def _persist_ai_images(self, board_id: str, images_b64: list) -> list:
        """AI 이미지(base64)를 서버 첨부로 저장하고 'attachments/<uuid>.png' 목록 반환."""
        import base64 as _b64
        import uuid as _uuid
        if not images_b64:
            return []
        board = self.boards.get(board_id)
        refs = []
        for b64 in images_b64:
            try:
                raw = _b64.b64decode(b64) if isinstance(b64, str) else b64
            except Exception:
                continue
            if not isinstance(raw, (bytes, bytearray)):
                continue
            name = _uuid.uuid4().hex + ".png"
            if board.save_attachment(name, bytes(raw)):
                refs.append("attachments/" + name)
        return refs

    async def _on_disconnect(self, sess: Session) -> None:
        board_id = sess.board_id
        if sess.http_token:
            self._http_tokens.pop(sess.http_token, None)
        self.registry.remove(sess)
        if sess.authed and board_id:
            await self.registry.broadcast(
                board_id, {"type": "user_leave", "user": sess.username}
            )
            await self._broadcast_presence(board_id)
        logger.info("connection closed (sid=%s user=%s)", sess.id, sess.username)

    # ---- 콘솔용 헬퍼 (루프 스레드에서 호출) -----------------------------
    async def kick_user(self, username: str) -> bool:
        s = self.registry.find_user(username)
        if not s:
            return False
        await s.send({"type": "server_msg", "text": "You were kicked by an operator."})
        await s.ws.close()
        return True

    async def say(self, text: str) -> None:
        for s in self.registry.all_sessions():
            if s.authed:
                await s.send({"type": "server_msg", "text": text})

    def online_users(self):
        return [(s.username, LEVEL_NAMES.get(s.level, "?"), s.board_id)
                for s in self.registry.all_sessions() if s.authed]

    # ---- 수명주기 -------------------------------------------------------
    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        logger.info("Qonvo server listening on %s:%s", self.host, self.port)
        self._autosave_task = asyncio.ensure_future(self._autosave_loop())
        if self.upnp_enabled:
            await self._setup_upnp()

    async def _autosave_loop(self) -> None:
        """변경된 보드를 주기적으로 디스크에 저장(자동저장 — 온라인이라 수동저장 없음)."""
        try:
            while True:
                await asyncio.sleep(20)
                await self.loop.run_in_executor(None, self.boards.save_all)
        except asyncio.CancelledError:
            pass

    async def _setup_upnp(self) -> None:
        """UPnP로 포트를 자동개방하고 접속 호스트를 결정한다(블로킹은 executor로)."""
        from .upnp import UpnpManager

        loop = self.loop

        def _open():
            mgr = UpnpManager(self.port, description=self.name, lease=self.upnp_lease)
            if not mgr.discover():
                return None, None
            ok = mgr.add_mapping()
            ip = mgr.get_public_ip()
            return (mgr if ok else None), ip

        self._upnp, public_ip = await loop.run_in_executor(None, _open)
        # 안내 호스트: config public_host 우선, 없으면 감지된 공인 IP
        self.connect_host = self.public_host or public_ip
        if self._upnp:
            self._upnp_task = asyncio.ensure_future(self._upnp_renew_loop())
            logger.info("UPnP active. connect host=%s", self.connect_host)
        else:
            logger.info("UPnP unavailable; falling back to direct/manual. host=%s", self.connect_host)

    async def _upnp_renew_loop(self) -> None:
        """임대 만료 전에 주기적으로 매핑을 갱신한다."""
        interval = max(60, int(self.upnp_lease * 0.7)) if self.upnp_lease else 1800
        try:
            while True:
                await asyncio.sleep(interval)
                if self._upnp:
                    await self.loop.run_in_executor(None, self._upnp.add_mapping)
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        if getattr(self, "_autosave_task", None):
            self._autosave_task.cancel()
        self.boards.save_all()
        if self._upnp_task:
            self._upnp_task.cancel()
        if self._upnp:
            try:
                await self.loop.run_in_executor(None, self._upnp.delete_mapping)
            except Exception:
                pass
        if self._runner:
            await self._runner.cleanup()
        logger.info("server stopped")
