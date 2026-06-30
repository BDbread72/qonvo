"""클라↔서버 공유 프로토콜 버전 (Qt-free).

GUI 클라(server_client.py)와 헤드리스 서버(server/app.py)가 **둘 다** import 한다.
그래서 PyQt6/aiohttp 어디에도 의존하지 않는다.

`PROTOCOL_VERSION` 은 **와이어 프로토콜이 깨질 때만** 올린다(앱 의미버전과 별개의 정수).
서버는 `[server] min_protocol` 로 "이 버전 미만 클라는 거부"를 켤 수 있다(마크식 요구버전).
- 0 = 전원 허용(구버전 클라 포함). 호환 깨는 변경을 내보낼 때만 서버에서 올린다.
- 구버전 클라는 auth 에 protocol 필드를 안 보내므로 0 으로 간주된다.

연동 이력(참고용 — 깨지는 변경에서 +1):
  v1: models/model_options 광고, preferred count, extra_input_defs 동기화,
      http_token 첨부, presence/chat/relay, ai_request 백그라운드화.
  v2: 커서 스킨(Dynamic Cursor) — presence 에 skin 해시 필드 + GET/PUT/DELETE /skin.
      하위호환(구버전 클라는 skin 무시·기본 화살표, 구버전 서버는 필드 미제공). 능력 감지용.
"""
from __future__ import annotations

PROTOCOL_VERSION = 2


def app_version() -> str:
    """앱 의미버전(예: 'beta-1.3.0'). build.toml 에서 읽음. 실패 시 ''."""
    try:
        from v.board import _get_app_version
        return _get_app_version() or ""
    except Exception:
        return ""
