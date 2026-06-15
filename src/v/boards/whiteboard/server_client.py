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
    error_received = pyqtSignal(str, str)
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
        self._http_token = ""   # 첨부 HTTP 전송용 토큰 (auth_ok 에서 수신)
        self._http_base = ""    # http(s)://host:port (ws url 에서 파생)
        self._ping_ms = 0
        self._last_cursor = None
        self._last_select = None
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
                          secure: bool = False, merri: bool = False):
        """서버에 WebSocket 연결을 시작하고 인증 스레드를 구성한다.

        secure=True 또는 host 에 wss:// 스킴이 있으면 TLS(wss) 로 접속한다.
        (Cloudflare 터널 등 공개 wss 주소 지원)
        """
        if not HAS_WEBSOCKET:
            self.auth_fail.emit("websocket-client not installed (pip install websocket-client)")
            return

        if self._ws_thread and self._ws_thread.isRunning():
            self._ws_thread.stop()
            self._ws_thread.wait(2000)

        self._username = username
        url = compose_ws_url(host, port, secure)
        # 첨부 HTTP 베이스: ws://host:port/ws -> http://host:port
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
                        options: dict | None = None):
        """서버에 AI 요청을 전송한다."""
        self._send({
            "type": "ai_request",
            "node_id": str(node_id),
            "params": {
                "model": model,
                "message": message,
                "files": files or [],
                "system_prompt": system_prompt,
                "options": options or {},
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
        # 내 핑(+커서/선택)을 서버에 보고 → 다른 사람 목록에 반영
        self._send({"type": "presence", "ping": self._ping_ms,
                    "cursor": self._last_cursor, "select": self._last_select})

    def update_cursor(self, x: float, y: float):
        """라이브 커서 위치 보고(초당 ~12회로 throttle)."""
        self._last_cursor = {"x": round(x, 1), "y": round(y, 1)}
        now = time.time()
        if now - self._last_cursor_sent >= 0.08:
            self._last_cursor_sent = now
            self._send({"type": "presence", "ping": self._ping_ms,
                        "cursor": self._last_cursor, "select": self._last_select})

    def update_selection(self, sel):
        """영역 선택 사각형 보고({x,y,w,h} 또는 None). 즉시 1회 전송."""
        self._last_select = sel
        self._send({"type": "presence", "ping": self._ping_ms,
                    "cursor": self._last_cursor, "select": sel})

    def send_chat(self, text: str):
        if text.strip():
            self._send({"type": "chat", "text": text})

    def _send(self, msg: dict):
        """웹소켓 스레드로 JSON 메시지를 전송한다."""
        if self._ws_thread and self._ws_thread.isRunning():
            self._ws_thread.send(json.dumps(msg, ensure_ascii=False))

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
            boards = msg.get("boards", [])
            self.auth_ok.emit(self._level, boards)

        elif msg_type == "auth_fail":
            self.auth_fail.emit(msg.get("reason", "Unknown"))

        elif msg_type == "sync":
            snapshot = msg.get("snapshot", {})
            seq = msg.get("seq", 0)
            if seq:
                self._last_seq = seq
            self.sync_received.emit(snapshot)

        elif msg_type == "delta":
            ops = msg.get("ops", [])
            seq = msg.get("seq", 0)
            if seq:
                self._last_seq = seq
            if ops:
                self.remote_ops.emit(ops, "")

        elif msg_type == "op":
            ops = msg.get("ops", [])
            author = msg.get("author", "")
            seq = msg.get("seq", 0)
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
            self.ai_complete.emit(
                msg.get("node_id", ""),
                msg.get("result", {}),
            )

        elif msg_type == "error":
            self.error_received.emit(
                msg.get("code", ""),
                msg.get("message", ""),
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
            self._ws.run_forever(ping_interval=30, ping_timeout=10)
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
            try:
                url = (f"{self._base}/board/{quote(self._board_id)}/attach/{quote(name)}"
                       f"?t={self._token}")
                tmp = dest_path + ".part"
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
