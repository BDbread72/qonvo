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

from v.proto import PROTOCOL_VERSION, app_version

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
        # ⚠️ aiohttp 기본 client_max_size = 1MB → 1MB 넘는 이미지 PUT 이 413 으로
        # 조용히 거부되어 서버에 영구 미저장(재접속 시 유실)됐다. 첨부 업로드는 사진/
        # 스크린샷이 흔히 1MB 를 넘으므로 WS max_msg_size(64MB) 와 동급으로 넉넉히 올린다.
        self._app = web.Application(client_max_size=256 * 1024 * 1024)
        self._runner: Optional[web.AppRunner] = None

        srv = config["server"]
        self.host = srv.get("host", "0.0.0.0")
        self.port = int(srv.get("port", 9700))
        self.name = srv.get("name", "Qonvo Server")
        self.motd = srv.get("motd", "")
        # 마크식 요구 버전: 이 프로토콜 미만 클라는 접속 거부(0=전원 허용).
        # 호환 깨는 변경을 내보낼 때만 올린다. PROTOCOL_VERSION 은 현 서버가 말하는 버전.
        try:
            self.min_protocol = int(srv.get("min_protocol", 0) or 0)
        except Exception:
            self.min_protocol = 0
        self.app_version = app_version()
        self.default_model = config.get("ai", {}).get("default_model", "gemini-2.5-flash")
        self.chat_log_enabled = bool(config.get("chat", {}).get("log", True))

        # AI 실행 거버넌스 — 레벨별 모델/이미지/레이트/동시/쿼타 + 사용량 회계.
        from .ai_policy import AIPolicy
        self.policy = AIPolicy(config)

        net = config.get("network", {})
        self.upnp_enabled = bool(net.get("upnp", True))
        self.public_host = net.get("public_host", "") or ""
        self.public_url = (net.get("public_url", "") or "").rstrip("/")
        self.upnp_lease = int(net.get("upnp_lease", 3600))
        self._upnp = None
        self._upnp_task = None
        self._autosave_task = None
        self.connect_host = None  # 클라에게 안내할 접속 호스트(공인 IP/도메인)

        self._http_tokens: dict = {}  # http_token -> (username, level)

        from .presence import PresenceRegistry
        self.presence = PresenceRegistry()
        self._merri_user_cache: dict = {}   # merri token -> (username, expires)

        from .relay import RelayHub
        self.relay = RelayHub()

        self._app.router.add_get("/", self._health)
        self._app.router.add_get("/ws", self.ws_handler)
        self._app.router.add_get("/boards", self._boards_meta)   # 보드 목록 + 노드수
        self._app.router.add_post("/report", self._crash_report)  # 클라 크래시/오류 수집
        # qonvo 전용 presence — 앱 켜짐/작업중 하트비트
        self._app.router.add_post("/presence", self._presence_beat)
        self._app.router.add_get("/presence/list", self._presence_list)
        # 릴레이 — 잠긴 망 호스트도 외부 접속 받게(스팀 SDR 식). 보드는 호스트에 그대로
        self._app.router.add_get("/relay/host", self.relay.host_handler)
        self._app.router.add_get("/relay/c", self.relay.join_handler)
        # 첨부(이미지 등) HTTP 전송 — 서버모드에서 보드 이미지를 받고/올림
        self._app.router.add_get("/board/{bid}/manifest", self._attach_manifest)
        self._app.router.add_get("/board/{bid}/attach/{name}", self._attach_get)
        self._app.router.add_put("/board/{bid}/attach/{name}", self._attach_put)
        # 커서 스킨(Dynamic Cursor) — 계정 기준 호스팅, presence 엔 해시만(마크 스킨 식)
        self._app.router.add_get("/skin/{user}", self._skin_get)
        self._app.router.add_put("/skin", self._skin_put)
        self._app.router.add_delete("/skin", self._skin_delete)
        # OAuth 콜백은 공개 주소여야 한다(브라우저가 redirect 됨). public_host 우선.
        _oauth_host = self.public_host or "localhost"
        public_url = f"http://{_oauth_host}:{self.port}"
        # admin_base: 리버스 프록시 뒤 외부 https 주소(있으면 관리자/merri 착지를 이리로)
        self._oauth = MattermostOAuth(config, self.auth, public_url, admin_base=self.public_url)
        self._oauth.register(self._app)

        # Operator 전용 웹 관리자 페이지 (/admin)
        from .admin import AdminPanel
        self.admin = AdminPanel(self)
        self.admin.register(self._app)

    # ---- 인프라 ---------------------------------------------------------
    def _server_icon_b64(self) -> str:
        """서버 아이콘을 base64 data-URI 로 반환 — 마크 server-icon.png 식.

        우선순위: ``%APPDATA%/Qonvo/server/server-icon.png`` (운영자 커스텀, 1:1 권장)
        → 없으면 번들 기본 ``server/default-icon.png`` (qonvo 앱 로고).
        (경로, mtime) 기준 캐시. 256KB 초과(/health 비대화 방지)는 "".
        """
        try:
            from pathlib import Path as _Path
            from .config import get_server_dir
            custom = get_server_dir() / "server-icon.png"
            default = _Path(__file__).resolve().parent / "default-icon.png"
            p = custom if custom.exists() else default
            if not p.exists():
                self._icon_cache = ("", str(p), -1.0)
                return ""
            mtime = p.stat().st_mtime
            cached = getattr(self, "_icon_cache", None)
            if cached and cached[1] == str(p) and cached[2] == mtime:
                return cached[0]
            raw = p.read_bytes()
            if len(raw) > 256 * 1024:
                self._icon_cache = ("", str(p), mtime)
                return ""
            import base64 as _b64
            data = "data:image/png;base64," + _b64.b64encode(raw).decode("ascii")
            self._icon_cache = (data, str(p), mtime)
            return data
        except Exception:
            return ""

    async def _health(self, request: web.Request) -> web.Response:
        return web.json_response({
            "name": self.name,
            "version": self.app_version,
            "protocol": PROTOCOL_VERSION,
            "min_protocol": self.min_protocol,
            "boards": list_boards(),
            "online": sum(1 for s in self.registry.all_sessions() if s.authed),
            "icon": self._server_icon_b64(),   # 마크식 서버 아이콘(data-URI, 없으면 "")
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

    async def _crash_report(self, request: web.Request) -> web.Response:
        """클라가 보낸 크래시/오류 보고 1건을 reports/reports.jsonl 에 적는다.

        인증은 강제하지 않는다(크래시는 인증 전후 아무때나 나며, 앱이 죽는 중이라
        부드러운 수집이 우선). 대신 ①본문 크기 제한 ②파일 회전(상한 넘으면 새 파일)
        ③필드 화이트리스트로 남용/디스크 폭주를 막는다.
        """
        try:
            raw = await request.content.read(64 * 1024)  # 최대 64KB
        except Exception:
            return web.json_response({"error": "read"}, status=400)
        try:
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except Exception:
            return web.json_response({"error": "bad json"}, status=400)

        # 화이트리스트 + 길이 클램프(서버가 신뢰 못 하는 입력)
        def _s(v, n):
            return str(v or "")[:n]
        rec = {
            "recv_ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "remote": request.remote or "",
            "ts": _s(data.get("ts"), 40),
            "kind": _s(data.get("kind"), 32),
            "summary": _s(data.get("summary"), 500),
            "detail": _s(data.get("detail"), 16000),
            "version": _s(data.get("version"), 64),
            "user": _s(data.get("user"), 64),
            "server": _s(data.get("server"), 80),
            "platform": _s(data.get("platform"), 80),
            "session": _s(data.get("session"), 32),
            "app_id": _s(data.get("app_id"), 40),
        }
        try:
            import os as _os
            from .config import get_server_dir
            rdir = get_server_dir() / "reports"
            rdir.mkdir(parents=True, exist_ok=True)
            path = rdir / "reports.jsonl"
            # 회전: 8MB 넘으면 .1 로 밀고 새로 시작(1세대만 보관)
            try:
                if path.exists() and path.stat().st_size > 8 * 1024 * 1024:
                    _os.replace(path, rdir / "reports.jsonl.1")
            except Exception:
                pass
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            logger.warning("crash report: kind=%s ver=%s user=%s sum=%s",
                           rec["kind"], rec["version"], rec["user"], rec["summary"][:120])
        except Exception as e:
            logger.warning("crash report write failed: %s", e)
            return web.json_response({"error": "store"}, status=500)
        return web.json_response({"ok": True})

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
        # 만료 항목 스윕(무한 증가 방지 — 예전엔 만료돼도 dict 에서 안 빠졌음).
        if len(self._merri_user_cache) > 64:
            self._merri_user_cache = {
                k: v for k, v in self._merri_user_cache.items() if v[1] > now
            }
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

    def _authorize_board(self, request: web.Request):
        """토큰 검증 + 요청한 보드(bid)의 현재 멤버인지 확인. (username, level) 또는 None.

        http_token 이 보드-스코프가 아니라, 예전엔 아무 보드 토큰으로 GET/PUT
        /board/<다른보드>/attach 를 호출해 남의 보드 이미지를 읽거나(모든 레벨)
        덮어쓸(Member) 수 있었다. 지금 그 보드에 들어와 있는 사용자로 제한한다."""
        tok = self._check_http_token(request)
        if tok is None:
            return None
        bid = request.match_info.get("bid", "")
        if not self.registry.is_member(tok[0], bid):
            return None
        return tok

    async def _attach_manifest(self, request: web.Request) -> web.Response:
        """보드의 첨부 파일명 목록을 반환한다."""
        if self._authorize_board(request) is None:
            return web.json_response({"error": "unauthorized"}, status=401)
        board = self.boards.get(request.match_info["bid"])
        return web.json_response({"attachments": board.list_attachments()})

    async def _attach_get(self, request: web.Request) -> web.StreamResponse:
        """첨부 파일을 내려준다."""
        if self._authorize_board(request) is None:
            return web.Response(status=401, text="unauthorized")
        board = self.boards.get(request.match_info["bid"])
        path = board.attachment_path(request.match_info["name"])
        if path is None or not path.exists():
            return web.Response(status=404, text="not found")
        return web.FileResponse(path)

    async def _attach_put(self, request: web.Request) -> web.Response:
        """첨부 파일을 업로드 받는다(Member 이상)."""
        tok = self._authorize_board(request)
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

    # ---- 커서 스킨 HTTP (Dynamic Cursor) -------------------------------
    async def _skin_get(self, request: web.Request) -> web.StreamResponse:
        """계정의 커서 스킨 PNG 를 내려준다(없으면 404 → 클라는 기본 화살표)."""
        if self._check_http_token(request) is None:
            return web.Response(status=401, text="unauthorized")
        from .skin_store import skin_path
        p = skin_path(request.match_info["user"])
        if p is None or not p.exists():
            return web.Response(status=404, text="not found")
        return web.FileResponse(p)

    async def _skin_put(self, request: web.Request) -> web.Response:
        """내 커서 스킨을 업로드한다(Member 이상). 토큰의 username 에 귀속."""
        tok = self._check_http_token(request)
        if tok is None:
            return web.Response(status=401, text="unauthorized")
        if tok[1] < MEMBER:
            return web.Response(status=403, text="read-only")
        username = tok[0]
        data = await request.read()
        from .skin_store import save_skin
        h = save_skin(username, data)
        if not h:
            return web.Response(status=400, text="bad skin (PNG, <=512KB)")
        await self._refresh_user_skin(username, h)
        return web.json_response({"ok": True, "hash": h})

    async def _skin_delete(self, request: web.Request) -> web.Response:
        """내 커서 스킨을 삭제한다(기본 화살표로 복귀)."""
        tok = self._check_http_token(request)
        if tok is None:
            return web.Response(status=401, text="unauthorized")
        username = tok[0]
        from .skin_store import delete_skin
        delete_skin(username)
        await self._refresh_user_skin(username, "")
        return web.json_response({"ok": True})

    async def _refresh_user_skin(self, username: str, h: str) -> None:
        """접속 중 같은 계정의 세션들에 새 스킨 해시를 박고 해당 보드에 presence 재방송.

        (한 계정이 여러 클라로 접속할 수 있으므로 username 매칭 세션 전부 갱신)
        """
        boards = set()
        for s in self.registry.all_sessions():
            if s.username == username:
                s.skin = h
                if s.board_id:
                    boards.add(s.board_id)
        for bid in boards:
            await self._broadcast_presence(bid)

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
            self._spawn_ai(sess, data)
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
        if "state" in data:
            sess.state = str(data.get("state") or "")[:16]  # menu/typing/away
        if sess.board_id:
            await self._broadcast_presence(sess.board_id)

    async def _broadcast_presence(self, board_id: str) -> None:
        users = self.registry.presence_list(board_id)
        await self.registry.broadcast(board_id, {"type": "presence", "users": users})

    async def _handle_chat(self, sess: Session, data: dict) -> None:
        """보드 채팅 메시지를 멤버 전원에게 브로드캐스트하고(설정 시) 로그에 기록한다."""
        text = (data.get("text") or "").strip()
        if not text or not sess.board_id:
            return
        text = text[:2000]
        ts = int(time.time() * 1000)
        await self.registry.broadcast(sess.board_id, {
            "type": "chat", "user": sess.username, "color": sess.color,
            "text": text, "ts": ts,
        })
        if self.chat_log_enabled:
            self._append_chat_log(sess.board_id, sess.username, text, ts)

    def _append_chat_log(self, board_id: str, user: str, text: str, ts: int) -> None:
        """라이브 채팅을 boards/<id>/chat.log (jsonl) 에 한 줄씩 누적한다."""
        try:
            from .config import get_boards_dir
            d = get_boards_dir() / safe_board_id(board_id)
            d.mkdir(parents=True, exist_ok=True)
            with open(d / "chat.log", "a", encoding="utf-8") as f:
                f.write(json.dumps(
                    {"ts": ts, "user": user, "text": text}, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning("chat log write failed (board=%s): %s", board_id, e)

    async def _handle_auth(self, sess: Session, data: dict) -> None:
        # 이미 인증된 세션의 재-auth 는 거부한다. 예전엔 매번 새 http_token 을 발급하고
        # sess.http_token 만 교체해, 이전 토큰들이 _http_tokens 에 영구히 남아(누수+무한증가)
        # 끊긴 뒤에도 첨부 접근이 유효했고, 세션 도중 신원(username/level)이 바뀔 수 있었다.
        if sess.authed:
            await sess.send({"type": "error", "code": "already_authed",
                             "message": "already authenticated"})
            return
        username = (data.get("user") or "").strip()
        password = data.get("pass") or ""

        # 요구 버전 게이트(마크식): 구버전 클라는 접속 거부. 구버전 클라는 protocol
        # 필드를 안 보내 0 으로 간주되므로, min_protocol=0 이면 전원 통과(하위호환).
        try:
            client_proto = int(data.get("protocol", 0) or 0)
        except Exception:
            client_proto = 0
        if client_proto < self.min_protocol:
            await sess.send({"type": "auth_fail", "reason": (
                f"클라이언트 버전이 너무 낮습니다. qonvo를 업데이트하세요.\n"
                f"(서버 요구 protocol v{self.min_protocol} / 현재 v{client_proto})")})
            logger.info("auth reject old client sid=%s proto=%s < min=%s",
                        sess.id, client_proto, self.min_protocol)
            return

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
        # 커서 스킨(Dynamic Cursor): 계정에 저장된 스킨 해시를 로드해 presence 로 광고.
        # 작은 파일 + sha256(mtime 캐시)라 인라인이어도 루프를 막지 않는다.
        try:
            from .skin_store import skin_hash
            sess.skin = skin_hash(username)
        except Exception:
            sess.skin = ""
        # 첨부 HTTP 전송용 토큰 발급
        import uuid as _uuid
        http_token = _uuid.uuid4().hex
        self._http_tokens[http_token] = (username, level)
        sess.http_token = http_token
        await sess.send({"type": "auth_ok", "level": level, "boards": list_boards(),
                         "http_token": http_token,
                         "server_version": self.app_version,
                         "protocol": PROTOCOL_VERSION,
                         "min_protocol": self.min_protocol,
                         "models": await self._available_models(),
                         "model_options": getattr(self, "_model_options_cache", {})})
        logger.info("auth ok sid=%s user=%s level=%s(%s)", sess.id, username, level, reason)

    async def _available_models(self) -> dict:
        """서버가 돌릴 수 있는 모델 {id: name} — 클라 모델 피커용(라우터 1회 구성·캐시).

        ⚠️ build_router 는 플러그인(openai SDK 등) import 로 수백ms~수초가 걸려
        이벤트 루프를 막으면 ping/pong 타임아웃으로 접속이 끊긴다 → 반드시
        run_in_executor 로 루프 밖에서 구성한다. 첫 접속만 대기, 이후 캐시 히트.
        """
        cached = getattr(self, "_models_cache", None)
        if cached is not None:
            return cached
        loop = asyncio.get_running_loop()

        def _build():
            from .ai_runner import available_models, available_model_options
            r = self._ensure_router()
            return available_models(r), available_model_options(r)
        try:
            models, opts = await loop.run_in_executor(None, _build)
        except Exception as e:
            logger.warning("available_models failed: %s", e)
            models, opts = {}, {}
        self._models_cache = models
        self._model_options_cache = opts
        return models

    async def _handle_join(self, sess: Session, data: dict) -> None:
        board_id = safe_board_id(data.get("board_id", ""))
        last_seq = int(data.get("last_seq", 0) or 0)
        loop = asyncio.get_running_loop()
        # ⚠️ 보드 최초 로드(snapshot+oplog 디스크 읽기)와 sync 페이로드 구성(대형 doc
        # deepcopy)은 수십~수백ms 걸려 이벤트 루프를 막는다. 루프에서 직접 하면 그동안
        # 다른 세션의 WS ping 에 PONG 을 못 해 'ping/pong timed out' 으로 끊긴다
        # (대형 보드 join 시 특히). executor 로 빼서 루프가 계속 돌게 한다.
        board = await loop.run_in_executor(None, self.boards.get, board_id)
        # 보드 전환이면 이전 보드 멤버들에게 떠남을 알린다(안 하면 유령 presence 가 남는다).
        prev_board = sess.board_id
        # 멤버로 먼저 등록(이후 모든 op 를 빠짐없이 받게) → sync 전송. 등록과 sync
        # 사이/도중에 도달하는 op 는 클라가 sync 수신 전까지 버퍼링했다가 sync 의 seq
        # 보다 큰 것만 재생한다(server_client 의 _join_synced 버퍼). 발산/유실 방지.
        self.registry.join_board(sess, board.board_id)
        if prev_board and prev_board != board.board_id:
            await self.registry.broadcast(
                prev_board,
                {"type": "user_leave", "user": sess.username},
            )
            await self._broadcast_presence(prev_board)
        payload = await loop.run_in_executor(None, board.snapshot_for_join, last_seq)
        await sess.send(payload)
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
        new_seq, applied = board.apply_ops(ops, sess.username)
        # 서버에 실제 적용된 op 만 브로드캐스트(실패 op 제외 → 클라 문서 발산 방지).
        if applied:
            await self.registry.broadcast(
                sess.board_id,
                {"type": "op", "ops": applied, "author": sess.username, "seq": new_seq},
                exclude=sess,
            )

    def _spawn_ai(self, sess: Session, data: dict) -> None:
        """AI 요청을 백그라운드 태스크로 실행한다.

        ⚠️ AI(특히 GPT/이미지)는 20초 이상 걸린다. 수신 루프(async for msg in ws)
        안에서 인라인으로 await 하면 그 동안 그 연결이 소켓을 안 읽어 클라가 보내는
        WS ping 에 자동 PONG 을 못 한다 → 클라(run_forever ping_timeout=18s)가
        'ping/pong timed out' 으로 끊는다. 태스크로 분리하면 수신 루프가 계속 돌며
        ping 에 응답해 긴 AI 중에도 연결이 유지된다.
        """
        if not hasattr(self, "_ai_tasks"):
            self._ai_tasks = set()
        task = asyncio.create_task(self._handle_ai(sess, data))
        self._ai_tasks.add(task)

        def _done(t):
            self._ai_tasks.discard(t)
            try:
                exc = t.exception()
            except Exception:
                exc = None
            if exc:
                logger.warning("ai task failed sid=%s: %s", sess.id, exc)
        task.add_done_callback(_done)

    async def _handle_ai(self, sess: Session, data: dict) -> None:
        node_id = str(data.get("node_id", ""))
        if sess.level < MEMBER:
            await sess.send({"type": "error", "code": "perm",
                             "message": "read-only (Visitor)", "node_id": node_id})
            return
        params = data.get("params", {}) or {}
        model = params.get("model") or self.default_model
        message = params.get("message", "")
        files = params.get("files", [])
        system_prompt = params.get("system_prompt", "")
        options = params.get("options", {})
        board_id = sess.board_id

        # 클라가 보낸 입력 파일 참조(attachments/<name>·basename)를 서버 보드의
        # 실제 첨부 경로로 해석한다(이미지 input). 못 찾으면 원본 유지.
        files = self._resolve_input_files(board_id, files)

        try:
            count = int(params.get("count", 1))
        except Exception:
            count = 1

        # ── 거버넌스 게이트: 모델 허용/이미지/레이트/동시/쿼타 + count 클램프 ──
        try:
            from v.model_plugin import is_image_model
            is_image = is_image_model(model)
        except Exception:
            # fail-closed: 판정 실패 시 이미지로 간주해 allow_image 게이트를 적용한다.
            # (fail-open 이면 진짜 이미지 모델이 판정 예외로 게이트를 우회할 수 있었다.)
            is_image = True
        ok, reason, count = self.policy.authorize(sess.username, sess.level, model, count, is_image)
        if not ok:
            await sess.send({"type": "error", "code": "limit", "message": reason, "node_id": node_id})
            return

        try:
            router = self._ensure_router()
        except Exception as e:
            await sess.send({"type": "error", "code": "ai",
                             "message": f"router init failed: {e}", "node_id": node_id})
            return

        loop = asyncio.get_running_loop()
        from .ai_runner import run_ai

        # 동시/레이트 카운트 시작 — 완료(또는 예외) 시 반드시 end() 로 회계.
        # count(preferred N후보)를 넘겨 실제 동시 실행 수를 반영(한도 우회 방지).
        self.policy.begin(sess.username, count)
        tin = tout = 0
        try:
            # preferred: N개 후보를 동시 생성해 요청자에게만 candidates 로 보낸다(선택은 클라가).
            if count > 1:
                logger.info("ai_request(preferred x%d) sid=%s user=%s node=%s model=%s",
                            count, sess.id, sess.username, node_id, model)
                tasks = [
                    loop.run_in_executor(
                        None,
                        lambda: run_ai(router, model, message, files, system_prompt, options, None),
                    )
                    for _ in range(count)
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                candidates = []
                for r in results:
                    if isinstance(r, dict):
                        tin += int(r.get("tokens_in", 0) or 0)
                        tout += int(r.get("tokens_out", 0) or 0)
                        candidates.append({
                            "text": r.get("text", ""), "images": r.get("images", []),
                            "tokens_in": r.get("tokens_in", 0), "tokens_out": r.get("tokens_out", 0),
                            "error": r.get("error"),
                        })
                    else:
                        candidates.append({"text": "", "images": [], "error": str(r)})
                # 문서에 후보 저장(재접속 로그 복원). 클라로 보내는 candidates 는 즉시
                # 표시용 base64 이미지, 문서에는 첨부 파일로 영속하고 상대참조로 기록한다.
                try:
                    doc_cands = [
                        {"text": c.get("text", ""),
                         "images": self._persist_ai_images(board_id, c.get("images", []))}
                        for c in candidates
                    ]
                    self.boards.get(board_id).append_preferred_message(
                        node_id, user=message, model=model, candidates=doc_cands,
                        tokens_in=tin, tokens_out=tout,
                    )
                except Exception as e:
                    logger.warning("preferred persist failed node=%s: %s", node_id, e)
                await sess.send({"type": "ai_complete", "node_id": node_id,
                                 "result": {"candidates": candidates}})
                return

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

            logger.info("ai_request sid=%s user=%s node=%s model=%s",
                        sess.id, sess.username, node_id, model)
            result = await loop.run_in_executor(
                None,
                lambda: run_ai(router, model, message, files, system_prompt, options, on_chunk),
            )
            tin += int(result.get("tokens_in", 0) or 0)
            tout += int(result.get("tokens_out", 0) or 0)

            # AI 생성 이미지를 서버 첨부 파일로 저장하고 상대경로로 doc 에 기록.
            # (base64 를 doc 에 넣으면 snapshot 비대 + 재접속 시 경로 미해석 → 파일로 영속)
            try:
                rel_refs = self._persist_ai_images(board_id, result.get("images", []))
                self.boards.get(board_id).append_assistant_message(
                    node_id, result.get("text", ""), rel_refs,
                    user=message, model=model,
                    tokens_in=result.get("tokens_in", 0),
                    tokens_out=result.get("tokens_out", 0),
                )
            except Exception as e:
                logger.warning("ai persist failed node=%s: %s", node_id, e)

            payload = {
                "text": result.get("text", ""),
                "images": result.get("images", []),
                "tokens_in": result.get("tokens_in", 0),
                "tokens_out": result.get("tokens_out", 0),
                "error": result.get("error"),
            }
            # 그래프 부수효과(다운스트림 자동생성/신호)는 요청자만 실행해야 한다 →
            # 요청자에겐 requester 플래그 포함, 나머지 멤버에겐 표시 전용으로 보낸다.
            await sess.send({"type": "ai_complete", "node_id": node_id,
                             "result": payload, "requester": True})
            await self.registry.broadcast(
                board_id,
                {"type": "ai_complete", "node_id": node_id, "result": payload},
                exclude=sess,
            )
            if result.get("error"):
                await sess.send({"type": "error", "code": "ai",
                                 "message": result["error"], "node_id": node_id})
        except Exception as e:
            # run_ai 이후(persist/broadcast)에서 예외가 나도 요청자의 노드가
            # 영원히 'running' 으로 멈추지 않게 종료 신호를 반드시 보낸다.
            logger.warning("ai handler error node=%s: %s", node_id, e)
            try:
                await sess.send({"type": "ai_complete", "node_id": node_id,
                                 "result": {"text": "", "images": [],
                                            "error": f"server error: {e}"},
                                 "requester": True})
            except Exception:
                pass
        finally:
            self.policy.end(sess.username, model, tin, tout, count)

    def _resolve_input_files(self, board_id: str, files) -> list:
        """클라가 보낸 입력 파일 참조를 서버 보드의 실제 첨부 경로로 해석한다.

        클라는 'attachments/<name>' 또는 basename 을 보낸다(로컬 절대경로는 서버가
        못 읽으므로). 보드 첨부 디렉토리에서 같은 이름을 찾아 절대경로로 바꾼다.
        이미 존재하는 절대경로면 그대로, 못 찾으면 원본 유지(provider 가 처리/무시).
        """
        import os as _os
        if not files:
            return []
        try:
            board = self.boards.get(board_id)
        except Exception:
            board = None
        out = []
        for f in files:
            if not isinstance(f, str) or not f:
                continue
            if _os.path.isabs(f) and _os.path.exists(f):
                out.append(f)
                continue
            name = _os.path.basename(f)
            if board is not None:
                p = board.attachment_path(name)
                if p is not None and p.exists():
                    out.append(str(p))
                    continue
            out.append(f)
        return out

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
        # 같은 유저가 여러 번 접속했을 수 있다 — 세션 하나만 끊으면 나머지는 살아남는다.
        sessions = [s for s in self.registry.all_sessions() if s.username == username]
        if not sessions:
            return False
        for s in sessions:
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

    def set_user_level(self, username: str, level: int) -> None:
        """사용자 레벨 변경: 접속 중 세션 즉시 반영 + roles.json 에 영속(merri 포함 모든 인증방식)."""
        for s in self.registry.all_sessions():   # 다중 세션 모두 반영(하나만 바꾸면 나머지 stale)
            if s.username == username:
                s.level = level
        from . import auth as _a
        _a.set_role(username, int(level))

    # ---- 수명주기 -------------------------------------------------------
    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        logger.info("Qonvo server listening on %s:%s", self.host, self.port)
        self._autosave_task = asyncio.ensure_future(self._autosave_loop())
        # 라우터(provider 플러그인 import)를 미리 워밍한다. 안 하면 첫 접속자가
        # auth_ok 안에서 build_router 의 수초짜리 import 비용을 전부 떠안아 9/18초
        # 타임아웃에 걸려 '한 번에 접속 안 됨'이 난다(재시도는 캐시 히트로 성공).
        self._prewarm_task = asyncio.ensure_future(self._available_models())
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
        """임대 갱신 + 자가복구.

        기존엔 시작 때 캐시한 컨트롤 URL로만, 임대*0.7(~42분)마다 갱신해서
        라우터가 한 번 껌뻑이면(재부팅·UPnP 테이블 초기화) 매핑이 사라진 뒤 영영 복구
        못 했다(=외부 접속 자꾸 끊김). 이제 ①최소 10분마다 재확인 ②갱신 실패 시
        다시 발견(re-discover)해 재오픈 → 외부 의존성 없이 UPnP 만으로 스스로 복구한다.
        """
        base = max(60, int(self.upnp_lease * 0.7)) if self.upnp_lease else 1800
        interval = min(base, 600)   # 드리프트를 빨리 잡도록 최소 10분 간격
        # ⚠️ 예외 처리는 반드시 루프 '안'에 둔다. 예전엔 try 가 while 을 감싸서, 갱신 중
        # 단 한 번의 일시적 예외(네트워크/라우터)로 루프가 영영 종료 → 자가복구 불능이었다
        # (=이 루프가 막으려던 바로 그 실패). 이제 한 사이클이 터져도 다음 사이클로 계속한다.
        while True:
            try:
                await asyncio.sleep(interval)
                if not self._upnp:
                    continue
                ok = await self.loop.run_in_executor(None, self._upnp.add_mapping)
                if ok:
                    continue
                # 캐시된 IGD 컨트롤 URL이 무효일 수 있음 → 새로 발견 후 재오픈
                logger.warning("UPnP renew failed; re-discovering IGD…")

                def _rediscover():
                    from .upnp import UpnpManager
                    m = UpnpManager(self.port, description=self.name, lease=self.upnp_lease)
                    if m.discover() and m.add_mapping():
                        return m
                    return None

                fresh = await self.loop.run_in_executor(None, _rediscover)
                if fresh:
                    self._upnp = fresh
                    logger.info("UPnP re-established after re-discovery")
                else:
                    logger.warning("UPnP re-discovery failed; retrying next cycle")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("UPnP renew cycle error (계속 재시도): %s", e)

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
