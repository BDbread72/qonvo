from __future__ import annotations

import json
import time
import uuid
from typing import Any, Optional

from PyQt6.QtCore import QThread, pyqtSignal, QObject

from v.logger import get_logger

logger = get_logger("qonvo.server_client")

try:
    import websocket
    HAS_WEBSOCKET = True
except ImportError:
    HAS_WEBSOCKET = False


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

    def __init__(self, parent=None):
        """서버 연결 상태와 보드/동기화 상태를 초기화한다."""
        super().__init__(parent)
        self._ws_thread: Optional[_WebSocketThread] = None
        self._username = ""
        self._level = -1
        self._board_id = ""
        self._applying_remote = False
        self._last_seq: int = 0

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

    def connect_to_server(self, host: str, port: int, username: str, password: str):
        """서버에 WebSocket 연결을 시작하고 인증 스레드를 구성한다."""
        if not HAS_WEBSOCKET:
            self.auth_fail.emit("websocket-client not installed (pip install websocket-client)")
            return

        if self._ws_thread and self._ws_thread.isRunning():
            self._ws_thread.stop()
            self._ws_thread.wait(2000)

        self._username = username
        url = f"ws://{host}:{port}/ws"

        self._ws_thread = _WebSocketThread(url, username, password)
        self._ws_thread.message_received.connect(self._on_message)
        self._ws_thread.connection_closed.connect(self._on_closed)
        self._ws_thread.connection_error.connect(self._on_error)
        self._ws_thread.start()

    def disconnect_from_server(self):
        """서버 연결을 종료하고 상태를 초기화한다."""
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

    def __init__(self, url: str, username: str, password: str):
        """웹소켓 스레드를 초기화하고 인증 정보를 저장한다."""
        super().__init__()
        self._url = url
        self._username = username
        self._password = password
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
                auth_msg = json.dumps({
                    "type": "auth",
                    "user": self._username,
                    "pass": self._password,
                }, ensure_ascii=False)
                ws.send(auth_msg)
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
