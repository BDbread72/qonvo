"""접속 세션 + presence 레지스트리.

각 WebSocket 연결은 하나의 Session 으로 표현된다. 보드별로 참가 세션을
묶어 op/AI 결과를 브로드캐스트한다. asyncio 단일 루프에서 동작하므로
별도 락 없이 안전하다(콜백은 모두 루프 스레드에서 실행).
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional, Set

from aiohttp import web


class Session:
    """단일 클라이언트 연결."""

    _counter = 0

    def __init__(self, ws: web.WebSocketResponse):
        Session._counter += 1
        self.id = Session._counter
        self.ws = ws
        self.username: str = ""
        self.level: int = -1
        self.board_id: str = ""
        self.authed: bool = False
        self.http_token: str = ""
        self.ping: int = 0            # 클라가 보고한 서버까지 RTT(ms)
        self.cursor: Optional[dict] = None   # {x, y} 보드 좌표 (라이브 커서)
        self.select: Optional[dict] = None   # {x,y,w,h} 영역 선택 사각형
        self.color: str = ""          # 커서/이름표 색

    async def send(self, msg: Dict[str, Any]) -> None:
        """JSON 메시지를 전송한다(연결 종료 시 무시)."""
        if self.ws.closed:
            return
        try:
            await self.ws.send_str(json.dumps(msg, ensure_ascii=False))
        except Exception:
            pass


class Registry:
    """전체 세션과 보드별 멤버십을 관리한다."""

    def __init__(self):
        self._sessions: Set[Session] = set()
        self._by_board: Dict[str, Set[Session]] = {}

    def add(self, s: Session) -> None:
        self._sessions.add(s)

    def remove(self, s: Session) -> None:
        self._sessions.discard(s)
        if s.board_id and s.board_id in self._by_board:
            self._by_board[s.board_id].discard(s)
            if not self._by_board[s.board_id]:
                del self._by_board[s.board_id]

    def join_board(self, s: Session, board_id: str) -> None:
        if s.board_id and s.board_id in self._by_board:
            self._by_board[s.board_id].discard(s)
        s.board_id = board_id
        self._by_board.setdefault(board_id, set()).add(s)

    def board_members(self, board_id: str) -> Set[Session]:
        return set(self._by_board.get(board_id, set()))

    def presence_list(self, board_id: str) -> list:
        """보드 참가자 목록(이름/레벨/핑/커서/색)을 반환한다."""
        out = []
        for s in self._by_board.get(board_id, set()):
            if not s.authed:
                continue
            out.append({"user": s.username, "level": s.level, "ping": s.ping,
                        "cursor": s.cursor, "select": s.select,
                        "color": s.color, "sid": s.id})
        out.sort(key=lambda d: d["user"].lower())
        return out

    def all_sessions(self):
        return list(self._sessions)

    def find_user(self, username: str) -> Optional[Session]:
        for s in self._sessions:
            if s.username == username:
                return s
        return None

    async def broadcast(self, board_id: str, msg: Dict[str, Any], exclude: Optional[Session] = None) -> None:
        """보드 멤버 전원(또는 exclude 제외)에게 메시지를 보낸다."""
        for s in self.board_members(board_id):
            if s is exclude:
                continue
            await s.send(msg)
