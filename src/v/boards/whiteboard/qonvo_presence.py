"""qonvo 전용 presence 클라이언트.

qonvo 앱이 켜져있는 동안 주기적으로 presence 허브(merri 를 광고하는 qonvo 서버,
예: qonvo.4myway.uk)에 하트비트를 보낸다. People 패널은 같은 허브에서 스냅샷을
읽어 동료의 qonvo 상태(온라인/작업중/오프라인)를 표시한다. Mattermost 상태와 무관.

상태: "online"(qonvo 켜짐) / "working"(협업 보드 안). 하트비트 끊기면 서버 TTL 로 offline.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from typing import Callable, Optional, Tuple
from urllib.parse import quote

from PyQt6.QtCore import QObject, QTimer

from v.settings import get_setting

_HUB = {"base": "", "ts": 0.0}     # 허브 base 캐시(5분)
_UA = "qonvo-presence"


def _discover_hub() -> str:
    """server_list 에서 merri 를 광고하는 qonvo 서버의 HTTP base 를 찾는다."""
    servers = get_setting("server_list", [])
    for s in servers if isinstance(servers, list) else []:
        host = s.get("host", ""); port = int(s.get("port", 9700)); secure = s.get("secure", False)
        scheme = "https" if (secure or str(host).startswith(("https://", "wss://"))) else "http"
        h = host.split("://", 1)[-1].rstrip("/")
        base = f"{scheme}://{h}" if ":" in h else f"{scheme}://{h}:{port}"
        try:
            with urllib.request.urlopen(base + "/", timeout=5) as r:
                d = json.loads(r.read().decode("utf-8"))
            if d.get("auth", {}).get("merri"):
                return base
        except Exception:
            continue
    return ""


def hub_base() -> str:
    now = time.time()
    if _HUB["base"] and now - _HUB["ts"] < 300:
        return _HUB["base"]
    base = _discover_hub()
    if base:
        _HUB.update(base=base, ts=now)
    return base


def fetch_presence(token: str) -> dict:
    """허브에서 {username: {status, board}} 스냅샷을 가져온다(블로킹 — 워커에서)."""
    base = hub_base()
    if not base or not token:
        return {}
    try:
        url = f"{base}/presence/list?t={quote(token)}"
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read().decode("utf-8")).get("presence", {})
    except Exception:
        return {}


class PresenceReporter(QObject):
    """앱 켜진 동안 주기적으로 내 상태를 허브에 하트비트한다.

    status_provider() -> (status, board). merri 프로필이 있어야 동작한다.
    """
    def __init__(self, status_provider: Callable[[], Tuple[str, str]], parent=None):
        super().__init__(parent)
        self._provider = status_provider
        self._timer = QTimer(self)
        self._timer.setInterval(40000)   # 40s (서버 TTL 90s)
        self._timer.timeout.connect(self._beat)

    def start(self):
        self._beat()
        self._timer.start()

    def stop(self, send_offline: bool = True):
        self._timer.stop()
        if send_offline:
            self._post("offline", "", drop=True)

    def _beat(self):
        status, board = self._provider()
        self._post(status, board)

    def _post(self, status: str, board: str, drop: bool = False):
        prof = self._profile()
        if not prof:
            return
        token = prof.get("token", "")
        base = hub_base()
        if not token or not base:
            return

        def work():
            try:
                body = json.dumps({"token": token, "status": status,
                                   "board": board, "drop": drop}).encode("utf-8")
                req = urllib.request.Request(base + "/presence", data=body, method="POST",
                                             headers={"Content-Type": "application/json",
                                                      "User-Agent": _UA})
                urllib.request.urlopen(req, timeout=8).read()
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _profile() -> Optional[dict]:
        from .profile import get_profile
        return get_profile()
