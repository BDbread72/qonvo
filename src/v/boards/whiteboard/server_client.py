from __future__ import annotations

import json
import time
import uuid
from typing import Any, Optional

from PyQt6.QtCore import QThread, pyqtSignal, QObject, QTimer

from v.logger import get_logger

logger = get_logger("qonvo.server_client")

try:
    import websocket
    HAS_WEBSOCKET = True
except ImportError:
    HAS_WEBSOCKET = False


def compose_ws_url(host: str, port: int, secure: bool = False) -> str:
    """Host/Port/secure 로부터 WebSocket URL 을 만든다.

    - host 에 ``ws://``/``wss://`` 스킴이 있으면 그대로 사용(포트 무시).
    - ``https://``/``http://`` 면 각각 wss/ws 로 변환.
    - 스킴이 없으면 secure 에 따라 ws/wss, 포트가 명시 안 됐으면 port 부착.
    - 경로가 없으면 ``/ws`` 를 붙인다. (Cloudflare 터널: wss://xxx.trycloudflare.com → 443)
    """
    host = (host or "").strip()
    scheme = None
    for pre, sc in (("wss://", "wss"), ("ws://", "ws"),
                    ("https://", "wss"), ("http://", "ws")):
        if host.lower().startswith(pre):
            scheme = sc
            host = host[len(pre):]
            break

    host = host.rstrip("/")
    # 경로 분리 (예: xxx.com/ws 또는 xxx.com/path)
    if "/" in host:
        netloc, _, path = host.partition("/")
        path = "/" + path
    else:
        netloc, path = host, ""

    if scheme is None:
        scheme = "wss" if secure else "ws"
        # 평문(ws)일 때만 포트 부착. TLS(wss)는 보통 443(터널/리버스프록시)이므로
        # 포트를 붙이지 않는다. 커스텀 TLS 포트는 host 에 직접 명시(host:8443).
        if not secure and ":" not in netloc:
            netloc = f"{netloc}:{port}"

    if not path:
        path = "/ws"
    return f"{scheme}://{netloc}{path}"


class ServerClient(QObject):
    """Qonvo 서버와의 WebSocket 연결을 관리하는 QObject.

    UI 스레드와는 시그널로 통신한다.
    """

    connected = pyqtSignal()
    disconnected = pyqtSignal(str)
    auth_ok = pyqtSignal(int, list)
    auth_fail = pyqtSignal(str)
    sync_received = pyqtSignal(dict)
    remote_ops = pyqtSignal(list, str)
    user_joined = pyqtSignal(str, int)
    user_left = pyqtSignal(str)
    server_message = pyqtSignal(str)
    error_received = pyqtSignal(str, str, str)  # code, message, node_id("" 가능)
    ai_progress = pyqtSignal(str, str)
    ai_complete = pyqtSignal(str, dict)
    presence_received = pyqtSignal(list)   # [{user, level, ping, cursor, color, sid}]
    chat_received = pyqtSignal(dict)        # {user, color, text, ts}
    ping_updated = pyqtSignal(int)          # 서버까지 RTT(ms)

    def __init__(self, parent=None):
        """서버 연결 상태와 보드/동기화 상태를 초기화한다."""
        super().__init__(parent)
        self._ws_thread: Optional[_WebSocketThread] = None
        self._username = ""
        self._level = -1
        self._board_id = ""
        self._applying_remote = False
        self._last_seq: int = 0
        # join 시 sync/delta 수신 전에 도착한 op 는 버퍼링했다가 sync seq 보다
        # 큰 것만 재생한다(동시 접속 시 op-before-sync 경쟁으로 인한 발산/유실 방지).
        self._join_synced: bool = True
        self._op_buffer: list = []
        self._my_ai_nodes: set = set()  # 내가 AI 요청을 낸 node_id (요청자 판별용)
        self._http_token = ""   # 첨부 HTTP 전송용 토큰 (auth_ok 에서 수신)
        self._http_base = ""    # http(s)://host:port (ws url 에서 파생)
        self._server_models: dict = {}  # 서버가 돌릴 수 있는 모델 {id: name} (auth_ok)
        self._server_model_options: dict = {}  # 서버 모델 옵션 스키마 {id: {opt: schema}} (auth_ok)
        self._server_version = ""       # 서버 앱 의미버전 (auth_ok)
        self._server_protocol = 0       # 서버 프로토콜 버전 (auth_ok). 0=구버전(미보고)
        self._server_min_protocol = 0   # 서버가 요구하는 최소 클라 프로토콜 (auth_ok)
        self._ping_ms = 0
        self._last_cursor = None
        self._last_select = None
        self._last_state = ""
        self._last_cursor_sent = 0.0
        self._ping_timer = QTimer(self)
        self._ping_timer.setInterval(2000)
        self._ping_timer.timeout.connect(self._send_ping)

    @property
    def is_connected(self) -> bool:
        """현재 WebSocket 연결 상태를 반환한다."""
        return self._ws_thread is not None and self._ws_thread.isRunning()

    @property
    def username(self) -> str:
        """로그인한 사용자 이름을 반환한다."""
        return self._username

    @property
    def level(self) -> int:
        """인증된 사용자 권한 레벨을 반환한다."""
        return self._level

    @property
    def board_id(self) -> str:
        """현재 참가 중인 보드 ID를 반환한다."""
        return self._board_id

    @property
    def applying_remote(self) -> bool:
        """원격 op 적용 중 여부를 반환한다."""
        return self._applying_remote

    def connect_to_server(self, host: str, port: int, username: str, password: str,
                          secure: bool = False, merri: bool = False, ws_url: str = ""):
        """서버에 WebSocket 연결을 시작하고 인증 스레드를 구성한다.

        secure=True 또는 host 에 wss:// 스킴이 있으면 TLS(wss) 로 접속한다.
        ws_url 이 주어지면 그 URL 로 직접 접속한다(릴레이 /relay/c 등).
        """
        if not HAS_WEBSOCKET:
            self.auth_fail.emit("websocket-client not installed (pip install websocket-client)")
            return

        if self._ws_thread and self._ws_thread.isRunning():
            self._ws_thread.stop()
            self._ws_thread.wait(2000)

        self._username = username
        url = ws_url or compose_ws_url(host, port, secure)
        # 첨부 HTTP 베이스: ws://host:port/ws -> http://host:port
        # (릴레이 경유 시 첨부 HTTP 는 터널 안 됨 — 보드 구조는 동기화, 이미지는 제한)
        self._http_base = (url.replace("wss://", "https://")
                              .replace("ws://", "http://")).rsplit("/ws", 1)[0]

        self._ws_thread = _WebSocketThread(url, username, password, merri)
        self._ws_thread.message_received.connect(self._on_message)
        self._ws_thread.connection_closed.connect(self._on_closed)
        self._ws_thread.connection_error.connect(self._on_error)
        self._ws_thread.start()

    def disconnect_from_server(self):
        """서버 연결을 종료하고 상태를 초기화한다."""
        self._ping_timer.stop()
        if self._ws_thread and self._ws_thread.isRunning():
            self._ws_thread.stop()
            self._ws_thread.wait(2000)
        self._ws_thread = None
        self._board_id = ""
        self._level = -1

    @property
    def last_seq(self) -> int:
        """마지막으로 동기화된 시퀀스 번호를 반환한다."""
        return self._last_seq

    def join_board(self, board_id: str):
        """보드에 참여하며 마지막 시퀀스를 전달해 delta sync를 요청한다."""
        if self._board_id and self._board_id != board_id:
            self._last_seq = 0
        self._board_id = board_id
        self._join_synced = False
        self._op_buffer = []
        self._send({
            "type": "join_board",
            "board_id": board_id,
            "last_seq": self._last_seq,
        })
        if not self._ping_timer.isActive():
            self._ping_timer.start()
            self._send_ping()  # 즉시 1회

    def send_ai_request(self, node_id: str, model: str, message: str,
                        files: list | None = None, system_prompt: str = "",
                        options: dict | None = None, count: int = 1):
        """서버에 AI 요청을 전송한다. count>1 이면 preferred(N개 후보) 모드."""
        # 내가 낸 요청 노드를 기록 → ai_complete 수신 시 그래프 부수효과(자동생성/신호)는
        # 요청자만 실행(타 멤버 중복 카드/op 폭주 방지).
        self._my_ai_nodes.add(str(node_id))
        self._send({
            "type": "ai_request",
            "node_id": str(node_id),
            "params": {
                "model": model,
                "message": message,
                "files": files or [],
                "system_prompt": system_prompt,
                "options": options or {},
                "count": count,
            },
        })

    def send_ops(self, ops: list[dict]):
        """여러 CRDT op를 전송한다. 원격 적용 중이면 무시한다."""
        if not ops or self._applying_remote:
            return
        self._send({"type": "op", "ops": ops})

    def send_op(
        self,
        op_type: str,
        target: str,
        data: dict | None = None,
    ):
        """CRDT op을 서버에 전송한다. 원격 적용 중이면 전송하지 않는다."""
        if self._applying_remote:
            return
        op = {
            "op_id": uuid.uuid4().hex,
            "op_type": op_type,
            "target": str(target),
            "data": data or {},
            "timestamp": time.time() * 1000,
        }
        self.send_ops([op])

    @property
    def http_token(self) -> str:
        """첨부 HTTP 전송용 토큰."""
        return self._http_token

    @property
    def server_models(self) -> dict:
        """서버가 돌릴 수 있는 모델 {id: name} (auth_ok 에서 수신). 서버모드 피커용."""
        return dict(self._server_models)

    @property
    def server_model_options(self) -> dict:
        """서버 모델 옵션 스키마 {id: {opt: schema}} (auth_ok). 서버모드 옵션패널용."""
        return dict(self._server_model_options)

    @property
    def server_version(self) -> str:
        """서버 앱 의미버전 (auth_ok). 구버전 서버면 ''."""
        return self._server_version

    @property
    def server_protocol(self) -> int:
        """서버 프로토콜 버전 (auth_ok). 0=구버전(미보고)."""
        return self._server_protocol

    @property
    def server_min_protocol(self) -> int:
        """서버가 요구하는 최소 클라 프로토콜 (auth_ok)."""
        return self._server_min_protocol

    @property
    def http_base(self) -> str:
        """첨부 HTTP 베이스 URL (http(s)://host:port)."""
        return self._http_base

    def attachment_url(self, board_id: str, name: str) -> str:
        """첨부 다운로드 URL 을 구성한다."""
        from urllib.parse import quote
        return (f"{self._http_base}/board/{quote(board_id)}/attach/{quote(name)}"
                f"?t={self._http_token}")

    def start_attachment_download(self, board_id: str, dest_dir: str) -> "AttachmentDownloadThread":
        """서버 보드의 첨부 전체를 dest_dir 로 내려받는 스레드를 생성·반환한다.

        호출 측에서 progress/finished 시그널을 연결하고 start() 한다.
        """
        return AttachmentDownloadThread(self._http_base, self._http_token, board_id, dest_dir)

    # ---- 커서 스킨(Dynamic Cursor) -------------------------------------
    def skin_url(self, username: str) -> str:
        """계정의 커서 스킨 다운로드 URL."""
        from urllib.parse import quote
        return f"{self._http_base}/skin/{quote(username)}?t={self._http_token}"

    def start_skin_download(self, username: str, expected_hash: str,
                            dest_dir: str) -> "SkinDownloadThread":
        """username 의 스킨을 dest_dir/<hash>.png 로 받는 스레드 생성·반환(start 는 호출 측)."""
        return SkinDownloadThread(self._http_base, self._http_token,
                                  username, expected_hash, dest_dir)

    def upload_skin(self, data: bytes) -> str:
        """내 커서 스킨(PNG 바이트)을 서버에 올린다(동기). 성공 시 해시, 실패 시 ""."""
        import urllib.request
        if not self._http_base or not self._http_token or not data:
            return ""
        try:
            url = f"{self._http_base}/skin?t={self._http_token}"
            req = urllib.request.Request(url, data=data, method="PUT")
            with urllib.request.urlopen(req, timeout=30) as r:
                if r.status != 200:
                    return ""
                return json.loads(r.read().decode("utf-8")).get("hash", "")
        except Exception as e:
            logger.debug("skin upload failed: %s", e)
            return ""

    def delete_skin(self) -> bool:
        """내 커서 스킨을 삭제(기본 화살표 복귀). 성공 여부."""
        import urllib.request
        if not self._http_base or not self._http_token:
            return False
        try:
            url = f"{self._http_base}/skin?t={self._http_token}"
            req = urllib.request.Request(url, method="DELETE")
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status == 200
        except Exception as e:
            logger.debug("skin delete failed: %s", e)
            return False

    def upload_attachment(self, board_id: str, name: str, path: str) -> bool:
        """로컬 파일을 서버 보드 첨부로 업로드한다(동기, 작은 파일용)."""
        import urllib.request
        from urllib.parse import quote
        if not self._http_base or not self._http_token:
            return False
        try:
            with open(path, "rb") as f:
                data = f.read()
            url = (f"{self._http_base}/board/{quote(board_id)}/attach/{quote(name)}"
                   f"?t={self._http_token}")
            req = urllib.request.Request(url, data=data, method="PUT")
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status == 200
        except Exception as e:
            logger.debug("attachment upload failed %s: %s", name, e)
            return False

    # ---- ping / presence / chat ---------------------------------------
    @property
    def ping_ms(self) -> int:
        """서버까지 RTT(ms)."""
        return self._ping_ms

    def _send_ping(self):
        if self.is_connected:
            self._send({"type": "ping", "t": time.time() * 1000})

    def _on_pong(self, t):
        try:
            self._ping_ms = max(0, int(time.time() * 1000 - float(t)))
        except Exception:
            return
        self.ping_updated.emit(self._ping_ms)
        # 내 핑(+커서/선택/상태)을 서버에 보고 → 다른 사람 목록에 반영
        self._send({"type": "presence", "ping": self._ping_ms,
                    "cursor": self._last_cursor, "select": self._last_select,
                    "state": self._last_state})

    def update_cursor(self, x: float, y: float, state: str = ""):
        """라이브 커서 위치+상태 보고(초당 ~12회로 throttle)."""
        # 고배율 줌에서도 정확하도록 0.01 단위(0.1이면 줌 시 몇 px 어긋남)
        self._last_cursor = {"x": round(x, 2), "y": round(y, 2)}
        changed_state = (state != self._last_state)
        self._last_state = state
        now = time.time()
        if changed_state or now - self._last_cursor_sent >= 0.08:
            self._last_cursor_sent = now
            self._send({"type": "presence", "ping": self._ping_ms,
                        "cursor": self._last_cursor, "select": self._last_select,
                        "state": self._last_state})

    def update_selection(self, sel):
        """영역 선택 사각형 보고({x,y,w,h} 또는 None). 즉시 1회 전송."""
        self._last_select = sel
        self._send({"type": "presence", "ping": self._ping_ms,
                    "cursor": self._last_cursor, "select": sel, "state": self._last_state})

    def send_chat(self, text: str):
        if text.strip():
            self._send({"type": "chat", "text": text})

    def _send(self, msg: dict):
        """웹소켓 스레드로 JSON 메시지를 전송한다."""
        if self._ws_thread and self._ws_thread.isRunning():
            self._ws_thread.send(json.dumps(msg, ensure_ascii=False))

    def _flush_join_buffer(self, sync_seq: int):
        """join sync/delta 수신 직후, 그 전에 버퍼링한 op 중 sync_seq 보다 큰 것만 재생.

        sync_seq 이하 op 은 이미 스냅샷/delta 에 반영돼 있으므로 버린다(중복 적용 방지).
        """
        self._join_synced = True
        buffered, self._op_buffer = self._op_buffer, []
        for rec in buffered:
            seq = rec.get("seq", 0)
            if seq and seq <= sync_seq:
                continue  # 이미 스냅샷에 포함됨
            if seq:
                self._last_seq = seq
            ops = rec.get("ops", [])
            if ops:
                self.remote_ops.emit(ops, rec.get("author", ""))

    def _on_message(self, raw: str):
        """서버 메시지를 파싱해 유형별 시그널을 발생시킨다."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = msg.get("type", "")

        if msg_type == "auth_required":
            pass

        elif msg_type == "auth_ok":
            self._level = msg.get("level", 0)
            self._http_token = msg.get("http_token", "")
            self._server_models = msg.get("models") or {}
            self._server_model_options = msg.get("model_options") or {}
            self._server_version = msg.get("server_version", "") or ""
            try:
                self._server_protocol = int(msg.get("protocol", 0) or 0)
            except Exception:
                self._server_protocol = 0
            try:
                self._server_min_protocol = int(msg.get("min_protocol", 0) or 0)
            except Exception:
                self._server_min_protocol = 0
            boards = msg.get("boards", [])
            self.auth_ok.emit(self._level, boards)

        elif msg_type == "auth_fail":
            self.auth_fail.emit(msg.get("reason", "Unknown"))

        elif msg_type == "sync":
            snapshot = msg.get("snapshot")
            if snapshot is None and msg.get("snapshot_gz"):
                # 서버가 큰 스냅샷을 gzip+base64 로 압축해 보냄 → WS 스레드에서 푼다
                # (메인 스레드 블로킹 회피). 채팅 등 텍스트는 5~8배 작아져 로드가 빠르다.
                try:
                    import gzip as _gz, base64 as _b64
                    _data = _gz.decompress(_b64.b64decode(msg["snapshot_gz"]))
                    snapshot = json.loads(_data.decode("utf-8"))
                except Exception:
                    snapshot = {}
            if snapshot is None:
                snapshot = {}
            seq = msg.get("seq", 0)
            if seq:
                self._last_seq = seq
            self.sync_received.emit(snapshot)
            self._flush_join_buffer(seq)

        elif msg_type == "delta":
            ops = msg.get("ops", [])
            seq = msg.get("seq", 0)
            if seq:
                self._last_seq = seq
            if ops:
                self.remote_ops.emit(ops, "")
            self._flush_join_buffer(seq)

        elif msg_type == "op":
            ops = msg.get("ops", [])
            author = msg.get("author", "")
            seq = msg.get("seq", 0)
            if not self._join_synced:
                # sync/delta 수신 전 도착한 op — 버퍼링했다가 sync seq 보다 큰 것만 재생
                self._op_buffer.append({"ops": ops, "author": author, "seq": seq})
                return
            if seq:
                self._last_seq = seq
            if ops:
                self.remote_ops.emit(ops, author)

        elif msg_type == "user_join":
            self.user_joined.emit(msg.get("user", ""), msg.get("level", 0))

        elif msg_type == "user_leave":
            self.user_left.emit(msg.get("user", ""))

        elif msg_type == "server_msg":
            self.server_message.emit(msg.get("text", ""))

        elif msg_type == "ai_progress":
            self.ai_progress.emit(
                msg.get("node_id", ""),
                msg.get("chunk", ""),
            )

        elif msg_type == "ai_complete":
            node_id = msg.get("node_id", "")
            result = msg.get("result", {})
            # 요청자 판별: 서버 requester 플래그 또는 내가 보낸 요청 기록.
            mine = bool(msg.get("requester")) or (node_id in self._my_ai_nodes)
            self._my_ai_nodes.discard(node_id)
            if isinstance(result, dict):
                result = {**result, "_requester": mine}
            self.ai_complete.emit(node_id, result)

        elif msg_type == "error":
            self.error_received.emit(
                msg.get("code", ""),
                msg.get("message", ""),
                str(msg.get("node_id", "")),
            )

        elif msg_type == "pong":
            self._on_pong(msg.get("t"))

        elif msg_type == "presence":
            self.presence_received.emit(msg.get("users", []))

        elif msg_type == "chat":
            self.chat_received.emit(msg)

    def _on_closed(self, reason: str):
        """연결 종료 이벤트를 처리한다."""
        self.disconnected.emit(reason)

    def _on_error(self, err: str):
        """연결 오류를 처리한다."""
        self.disconnected.emit(err)

    def begin_remote_apply(self):
        """원격 op 적용 중 플래그를 켠다."""
        self._applying_remote = True

    def end_remote_apply(self):
        """원격 op 적용 중 플래그를 끈다."""
        self._applying_remote = False


class _WebSocketThread(QThread):
    """websocket-client 기반 QThread.

    auth_required 수신 시 자동으로 인증 메시지를 전송한다.
    """

    message_received = pyqtSignal(str)
    connection_closed = pyqtSignal(str)
    connection_error = pyqtSignal(str)

    def __init__(self, url: str, username: str, password: str, merri: bool = False):
        """웹소켓 스레드를 초기화하고 인증 정보를 저장한다."""
        super().__init__()
        self._url = url
        self._username = username
        self._password = password
        self._merri = merri
        self._ws: Optional[websocket.WebSocketApp] = None
        self._running = False

    def run(self):
        """웹소켓 루프를 실행하고 auth_required 메시지를 처리한다."""
        self._running = True

        def on_open(ws):
            pass

        def on_message(ws, raw):
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                self.message_received.emit(raw)
                return

            if msg.get("type") == "auth_required":
                payload = {
                    "type": "auth",
                    "user": self._username,
                    "pass": self._password,
                }
                # 버전 핸드셰이크: 서버가 요구버전(min_protocol) 검사·경고에 쓴다.
                try:
                    from v.proto import PROTOCOL_VERSION, app_version
                    payload["protocol"] = PROTOCOL_VERSION
                    payload["client_version"] = app_version()
                except Exception:
                    pass
                if self._merri:
                    payload["merri"] = True
                ws.send(json.dumps(payload, ensure_ascii=False))
            else:
                self.message_received.emit(raw)

        def on_error(ws, error):
            if self._running:
                self.connection_error.emit(str(error))

        def on_close(ws, close_status_code, close_msg):
            if self._running:
                reason = close_msg or "Connection closed"
                if isinstance(reason, bytes):
                    reason = reason.decode("utf-8", errors="replace")
                self.connection_closed.emit(str(reason))

        try:
            self._ws = websocket.WebSocketApp(
                self._url,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )
            # ping_timeout 을 넉넉히(18s) — 서버가 잠깐 바쁘거나 망이 출렁여도
            # 곧장 끊지 않는다(과거 10s 라 작업 중 'ping/pong timed out' 오진 빈발).
            self._ws.run_forever(ping_interval=20, ping_timeout=18)
        except Exception as e:
            if self._running:
                self.connection_error.emit(str(e))

    def send(self, text: str):
        """웹소켓으로 텍스트 메시지를 전송한다."""
        if self._ws:
            try:
                self._ws.send(text)
            except Exception as e:
                logger.debug("WS send failed: %s", e)

    def stop(self):
        """스레드 실행을 중지하고 소켓을 닫는다."""
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass


class AttachmentDownloadThread(QThread):
    """서버 보드의 첨부(이미지 등)를 temp 디렉토리로 내려받는 QThread.

    manifest 로 파일 목록을 받고, 이미 있는 파일은 건너뛴다(증분).
    """

    progress = pyqtSignal(int, int)        # (done, total)
    finished_dl = pyqtSignal(int, int)     # (ok, total)

    def __init__(self, http_base: str, token: str, board_id: str, dest_dir: str):
        super().__init__()
        self._base = http_base
        self._token = token
        self._board_id = board_id
        self._dest = dest_dir
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        import os
        import urllib.request
        from urllib.parse import quote

        os.makedirs(self._dest, exist_ok=True)
        names = self._fetch_manifest()
        total = len(names)
        if total == 0:
            self.finished_dl.emit(0, 0)
            return

        ok = 0
        for i, name in enumerate(names, 1):
            if self._cancel:
                break
            dest_path = os.path.join(self._dest, name)
            if os.path.exists(dest_path) and os.path.getsize(dest_path) > 0:
                ok += 1
                self.progress.emit(i, total)
                continue
            tmp = dest_path + ".part"
            try:
                url = (f"{self._base}/board/{quote(self._board_id)}/attach/{quote(name)}"
                       f"?t={self._token}")
                with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as out:
                    while True:
                        chunk = r.read(262144)
                        if not chunk:
                            break
                        out.write(chunk)
                os.replace(tmp, dest_path)
                ok += 1
            except Exception as e:
                logger.debug("attachment download failed %s: %s", name, e)
                # 부분 다운로드(.part) 잔재 정리 — 안 지우면 디스크에 쌓임.
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except Exception:
                    pass
            self.progress.emit(i, total)

        self.finished_dl.emit(ok, total)

    def _fetch_manifest(self) -> list:
        import json as _json
        import urllib.request
        from urllib.parse import quote
        try:
            url = f"{self._base}/board/{quote(self._board_id)}/manifest?t={self._token}"
            with urllib.request.urlopen(url, timeout=20) as r:
                return _json.loads(r.read().decode("utf-8")).get("attachments", [])
        except Exception as e:
            logger.debug("manifest fetch failed: %s", e)
            return []


class SkinDownloadThread(QThread):
    """한 계정의 커서 스킨(PNG)을 받아 dest_dir/<actual_hash>.png 로 저장하는 QThread.

    서버 /skin/{user} 는 **현재** 스킨을 돌려주므로, presence 가 알려준 expected_hash 와
    실제 받은 바이트의 해시(actual)가 다를 수 있다(업로드 직후 경쟁). 그래서 항상 **실제 해시**
    이름으로 저장하고, 둘을 모두 시그널에 실어 보낸다(호출 측이 매칭해 적용).
    """

    done = pyqtSignal(str, str, str, str)   # (username, expected_hash, actual_hash, path|"")

    def __init__(self, http_base: str, token: str, username: str,
                 expected_hash: str, dest_dir: str):
        super().__init__()
        self._base = http_base
        self._token = token
        self._user = username
        self._expected = expected_hash
        self._dest = dest_dir

    def run(self):
        import hashlib
        import os
        import urllib.request
        from urllib.parse import quote
        try:
            os.makedirs(self._dest, exist_ok=True)
            url = f"{self._base}/skin/{quote(self._user)}?t={self._token}"
            with urllib.request.urlopen(url, timeout=30) as r:
                data = r.read(1024 * 1024)  # 스킨 상한(서버 512KB)보다 넉넉히
            if not data:
                self.done.emit(self._user, self._expected, "", "")
                return
            actual = hashlib.sha256(data).hexdigest()[:16]
            path = os.path.join(self._dest, f"{actual}.png")
            if not (os.path.exists(path) and os.path.getsize(path) > 0):
                tmp = path + ".part"
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, path)
            self.done.emit(self._user, self._expected, actual, path)
        except Exception as e:
            logger.debug("skin download failed (%s): %s", self._user, e)
            self.done.emit(self._user, self._expected, "", "")
