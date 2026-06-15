"""인앱 호스팅(listen server) — qonvo.exe 안에서 QonvoServer 를 띄운다.

스팀의 "친구 초대 → 내 게임 같이"에 해당. 호스트가 지금 연 보드를 자기 PC 의
임베드 서버에 seed 해서 그 자리에서 서빙하고(네트워크 업로드 X), 모두가
클라이언트로 접속한다(호스트 자신도 ws://localhost 로 자가접속 → 코드 경로 통일).

서버는 PyQt 의존이 전혀 없으므로 백그라운드 스레드의 asyncio 루프에서 그대로
돌릴 수 있다. Qt 메인스레드와는 시그널로만 통신한다(server_client.py 와 동일 패턴).

외부 도달은 UPnP 자동개방만 사용(cloudflared 미사용). 안 열리는 망에서는 LAN
주소로만 접속 가능.
"""
from __future__ import annotations

import asyncio
import socket
import threading
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal


def _free_port(preferred: int = 9700) -> int:
    """preferred 포트가 비었으면 그대로, 아니면 임의의 빈 포트를 반환한다."""
    for port in (preferred, 0):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", port))
            actual = s.getsockname()[1]
            s.close()
            return actual
        except OSError:
            continue
    return preferred


def _build_config(port: int, name: str, host_username: str,
                  upnp: bool, allow_guests: bool) -> dict:
    """임베드 서버용 config dict 를 만든다(사용자 dedicated config 와 무관하게 신규)."""
    from server.config import _DEFAULTS, _deep_merge
    overrides = {
        "server": {
            "host": "0.0.0.0", "port": port, "name": name,
            "default_level": 1, "allow_guests": allow_guests,
        },
        "network": {"upnp": upnp, "public_host": "", "upnp_lease": 3600},
        # 호스트 본인은 Operator 로(자가접속 시 관리 권한)
        "users": {host_username: 2} if host_username else {},
        # oauth 는 임베드에선 비활성(초대 링크가 곧 출입증)
        "oauth": {"mattermost": {"enabled": False}},
    }
    return _deep_merge(_DEFAULTS, overrides)


class EmbeddedHost(QObject):
    """백그라운드 스레드에서 QonvoServer 를 기동/종료한다.

    시그널:
      ready(port:int, connect_host:str)  서버 리슨 시작(UPnP 후 connect_host 채워짐, 실패 시 "")
      failed(reason:str)                 기동 실패
      stopped()                          종료 완료
    """
    ready = pyqtSignal(int, str)
    failed = pyqtSignal(str)
    stopped = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server = None
        self._stop_event: Optional[asyncio.Event] = None
        self.port = 0
        self.board_id = ""

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, board_id: str, qonvo_path: Optional[str] = None,
              host_username: str = "", name: str = "", upnp: bool = True,
              allow_guests: bool = True) -> None:
        """임베드 서버를 시작한다.

        qonvo_path 가 주어지면 그 .qonvo 를 board_id 로 seed 한 뒤 서빙한다
        (이미 서버 store 에 board_id 가 있으면 seed 생략하고 그대로 서빙).
        """
        if self.is_running():
            self.failed.emit("이미 호스팅 중입니다.")
            return
        self.board_id = board_id
        port = _free_port()
        self.port = port
        cfg = _build_config(port, name or f"{host_username or 'Qonvo'} 님의 보드",
                            host_username, upnp, allow_guests)
        self._thread = threading.Thread(
            target=self._run, args=(cfg, board_id, qonvo_path), daemon=True)
        self._thread.start()

    def _run(self, config: dict, board_id: str, qonvo_path: Optional[str]) -> None:
        # 1) 현재 보드를 서버 store 로 seed (worker 스레드 — GUI 안 막음)
        try:
            if qonvo_path:
                from server.seed_board import seed
                seed(qonvo_path, board_id)
        except Exception as e:
            self.failed.emit(f"보드 준비 실패: {e}")
            return

        # 2) 전용 asyncio 루프에서 서버 기동
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._stop_event = asyncio.Event()
        try:
            from server.app import QonvoServer
            server = QonvoServer(config)
            self._server = server
            loop.run_until_complete(server.start())
        except Exception as e:
            self.failed.emit(f"서버 기동 실패: {e}")
            try:
                loop.close()
            except Exception:
                pass
            return

        # connect_host 는 start() 안에서 UPnP 후 채워진다(실패 시 None)
        self.ready.emit(server.port, server.connect_host or "")
        try:
            loop.run_until_complete(self._stop_event.wait())
        finally:
            try:
                loop.run_until_complete(server.stop())
            except Exception:
                pass
            try:
                loop.close()
            except Exception:
                pass
            self._server = None
            self._loop = None
            self.stopped.emit()

    def stop(self) -> None:
        """서버를 종료한다(스레드 안전)."""
        loop, ev = self._loop, self._stop_event
        if loop and ev:
            try:
                loop.call_soon_threadsafe(ev.set)
            except Exception:
                pass
