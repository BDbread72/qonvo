"""qonvo 전용 presence 레지스트리.

Mattermost 의 online/offline 은 "채팅에 로그인됨"일 뿐이라 "지금 qonvo 에서
같이 일할 수 있는가"를 못 알려준다. 그래서 qonvo 가 따로 다룬다.

qonvo 클라이언트가 주기적으로 하트비트(merri username + 상태 + 현재 보드)를
보내면 여기에 TTL 로 보관한다. People 패널이 스냅샷을 읽어 표시한다.

상태값: "online"(qonvo 켜짐) / "working"(협업 보드 안) / (없으면 offline).
"""
from __future__ import annotations

import threading
from typing import Dict, Tuple


class PresenceRegistry:
    def __init__(self, ttl_seconds: float = 90.0):
        self._ttl = ttl_seconds
        self._d: Dict[str, Tuple[str, str, float]] = {}   # user -> (status, board, expires)
        self._lock = threading.Lock()

    def beat(self, username: str, status: str, board: str, now: float) -> None:
        if not username:
            return
        with self._lock:
            self._d[username] = (status or "online", board or "", now + self._ttl)

    def drop(self, username: str) -> None:
        with self._lock:
            self._d.pop(username, None)

    def snapshot(self, now: float) -> Dict[str, dict]:
        """살아있는 사용자만 {username: {status, board}} 로 반환(만료분 정리)."""
        out: Dict[str, dict] = {}
        with self._lock:
            for u in list(self._d.keys()):
                status, board, exp = self._d[u]
                if exp < now:
                    del self._d[u]
                    continue
                out[u] = {"status": status, "board": board}
        return out
