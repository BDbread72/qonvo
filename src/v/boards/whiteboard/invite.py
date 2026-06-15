"""초대 코드 — friendly-id 생성 + 초대 링크 포맷/파싱.

인앱 호스팅("딸깍 초대")과 전용 서버 둘 다 같은 UX 로 쓰기 위한 공용 모듈.

- friendly-id:  ``clever-otter-734`` 형태(형용사-동물-숫자). 보드 id 로 사용.
- 초대 링크:    ``clever-otter-734@qonvo.4myway.uk:9700``  (사람이 복붙)
                ``clever-otter-734@wss://qonvo.4myway.uk:9700``  (보안 연결)

파싱은 위 두 형태 + 순수 ``host:port`` 도 관대하게 받는다.
"""
from __future__ import annotations

import random
from typing import NamedTuple, Optional

_ADJ = [
    "clever", "brave", "calm", "swift", "lucky", "mighty", "gentle", "shiny",
    "cosmic", "happy", "silent", "golden", "frosty", "sunny", "bold", "quiet",
]
_ANIMAL = [
    "otter", "falcon", "panda", "tiger", "whale", "fox", "owl", "lynx",
    "koala", "heron", "moose", "raven", "gecko", "bison", "crane", "wolf",
]


def generate_id() -> str:
    """``clever-otter-734`` 형태의 읽기 쉬운 보드 id 를 생성한다."""
    return f"{random.choice(_ADJ)}-{random.choice(_ANIMAL)}-{random.randint(100, 999)}"


class Invite(NamedTuple):
    board_id: str
    host: str
    port: int
    secure: bool


def format_invite(board_id: str, host: str, port: int, secure: bool = False) -> str:
    """초대 링크 문자열을 만든다: ``board_id@[wss://]host:port``."""
    h = host.split("://", 1)[-1].rstrip("/")
    prefix = "wss://" if secure else ""
    return f"{board_id}@{prefix}{h}:{port}"


def parse_invite(text: str) -> Optional[Invite]:
    """초대 링크/주소를 파싱한다. 실패하면 None.

    허용 형태:
      board_id@host:port
      board_id@wss://host:port
      board_id@host           (port 기본 9700)
      host:port               (board_id 없음 → board_id="")
    """
    if not text:
        return None
    s = text.strip()
    board_id = ""
    if "@" in s:
        board_id, s = s.split("@", 1)
        board_id = board_id.strip()
    secure = False
    if "://" in s:
        scheme, s = s.split("://", 1)
        secure = scheme.lower() in ("wss", "https")
    s = s.rstrip("/")
    host, port = s, 9700
    if ":" in s:
        host, _, port_s = s.rpartition(":")
        try:
            port = int(port_s)
        except ValueError:
            return None
    host = host.strip()
    if not host:
        return None
    return Invite(board_id=board_id, host=host, port=port, secure=secure)
