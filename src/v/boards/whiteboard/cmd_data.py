"""`/data` — 명령 시스템 자체 데이터 저장소 (마크 `/data storage` 풍 자체 DB).

키-값 저장소. 값은 SNBT(숫자·문자열·bool·리스트·컴파운드). settings `chat_data` 에 영속.
`get` 은 점(.) 경로로 컴파운드/리스트 내부를 탐색한다.

    data_set("hp", 20)
    data_set("pos", {"x": 1, "y": 2})        # 컴파운드
    data_get("pos", "x")  -> 1
    data_get("list", "0") -> 첫 요소
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from v.settings import get_setting, set_setting

_KEY = "chat_data"


# ── 저장소 (settings 글로벌) ────────────────────────────────────────────────
def load_data() -> Dict[str, Any]:
    d = get_setting(_KEY, {}) or {}
    return dict(d) if isinstance(d, dict) else {}


def save_data(d: Dict[str, Any]) -> None:
    set_setting(_KEY, d)


def data_set(key: str, value: Any) -> None:
    d = load_data()
    d[key] = value
    save_data(d)


def data_merge(key: str, compound: Dict[str, Any]) -> None:
    """기존 컴파운드에 compound 를 얕게 병합(없으면 새로 만든다)."""
    d = load_data()
    cur = d.get(key)
    if isinstance(cur, dict) and isinstance(compound, dict):
        cur = dict(cur)
        cur.update(compound)
        d[key] = cur
    else:
        d[key] = compound
    save_data(d)


def data_remove(key: str) -> bool:
    d = load_data()
    if key in d:
        del d[key]
        save_data(d)
        return True
    return False


def data_keys() -> List[str]:
    return sorted(load_data().keys())


def navigate(value: Any, path: str) -> Tuple[bool, Any]:
    """점(.) 경로로 value 내부 탐색. (찾음?, 값). 빈 path 면 value 그대로."""
    path = (path or "").strip()
    if not path:
        return True, value
    cur = value
    for part in path.split("."):
        part = part.strip()
        if isinstance(cur, dict):
            if part not in cur:
                return False, None
            cur = cur[part]
        elif isinstance(cur, list):
            try:
                idx = int(part)
            except ValueError:
                return False, None
            if not (-len(cur) <= idx < len(cur)):
                return False, None
            cur = cur[idx]
        else:
            return False, None
    return True, cur


def data_get(key: str, path: Optional[str] = None) -> Tuple[bool, Any]:
    """(찾음?, 값). 키 없거나 경로 빗나가면 (False, None)."""
    d = load_data()
    if key not in d:
        return False, None
    return navigate(d[key], path)
