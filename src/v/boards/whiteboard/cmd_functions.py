"""마인크래프트식 `/function` — 명령 시퀀스 매크로 + 매크로 변수 + return.

함수 = 이름 붙은 **멀티라인 텍스트**(.mcfunction 처럼). 각 줄이 명령 하나.
settings `chat_functions` 에 {이름: 본문텍스트} 로 저장(에디터가 본문을 통째로 편집).

매크로:  '$' 로 시작하는 줄은 호출 시 전달된 {args} 로 `$(key)` 를 치환한 뒤 실행.
         예) 함수 본문 `$create text{text:"$(msg)"}`  →  /function f {msg:"hi"}

구분:    줄바꿈 또는 top-level ';' 로 명령을 나눈다('{}'·'[]'·따옴표 안의 ';' 은 무시).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from v.settings import get_setting, set_setting

_KEY = "chat_functions"
MACRO_PREFIX = "$"
_MACRO_RE = re.compile(r"\$\(([^)]+)\)")


# ── 저장소 (settings 글로벌) ────────────────────────────────────────────────
def load_functions() -> Dict[str, str]:
    d = get_setting(_KEY, {}) or {}
    return dict(d) if isinstance(d, dict) else {}


def save_functions(d: Dict[str, str]) -> None:
    set_setting(_KEY, d)


def get_function(name: str) -> Optional[str]:
    return load_functions().get(name)


def set_function(name: str, body: str) -> None:
    d = load_functions()
    d[name] = body
    save_functions(d)


def append_line(name: str, line: str) -> str:
    d = load_functions()
    body = d.get(name, "")
    body = (body + "\n" + line) if body else line
    d[name] = body
    save_functions(d)
    return body


def delete_function(name: str) -> bool:
    d = load_functions()
    if name in d:
        del d[name]
        save_functions(d)
        return True
    return False


def list_functions() -> List[str]:
    return sorted(load_functions().keys())


# ── 본문 → 명령 줄 분해 ─────────────────────────────────────────────────────
def split_commands(text: str) -> List[str]:
    """본문을 명령 줄 목록으로. 줄바꿈 + top-level ';' 로 분리(괄호/따옴표 인식).

    '#' 으로 시작하는 줄은 주석(무시). 빈 줄 제거.
    """
    out: List[str] = []
    for raw_line in (text or "").split("\n"):
        for cmd in _split_semicolons(raw_line):
            c = cmd.strip()
            if not c or c.startswith("#"):
                continue
            out.append(c)
    return out


def _split_semicolons(s: str) -> List[str]:
    parts: List[str] = []
    buf: List[str] = []
    depth = 0
    in_q: Optional[str] = None
    esc = False
    for c in s:
        if in_q is not None:
            buf.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == in_q:
                in_q = None
            continue
        if c in ("\"", "'"):
            in_q = c
            buf.append(c)
        elif c in ("{", "["):
            depth += 1
            buf.append(c)
        elif c in ("}", "]"):
            depth = max(0, depth - 1)
            buf.append(c)
        elif c == ";" and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(c)
    parts.append("".join(buf))
    return parts


# ── 매크로 치환 ─────────────────────────────────────────────────────────────
def is_macro_line(line: str) -> bool:
    return line.lstrip().startswith(MACRO_PREFIX)


def macro_keys(body: str) -> List[str]:
    """본문의 매크로 줄들이 참조하는 $(key) 이름 모음(중복 제거)."""
    keys: List[str] = []
    for line in split_commands(body):
        if is_macro_line(line):
            for m in _MACRO_RE.finditer(line):
                if m.group(1) not in keys:
                    keys.append(m.group(1))
    return keys


def apply_macro(line: str, args: Dict[str, Any]) -> str:
    """'$' 가 벗겨진 매크로 줄의 `$(key)` 를 args 로 치환. 없는 키는 ValueError."""
    def repl(m):
        key = m.group(1)
        if key not in args:
            raise ValueError(f"매크로 변수 '{key}' 값이 없습니다")
        return str(args[key])
    return _MACRO_RE.sub(repl, line)
