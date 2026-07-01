"""`/execute` 조건 헬퍼 — 범위(matches)·비교·절 토크나이저.

조건 평가 자체는 cc(plugin/data/server) 가 필요해 chat_commands 에 둔다.
여기는 순수 함수만: 범위 파싱/매칭, 비교 연산, 클로즈 토크나이즈.
"""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

_CMP_OPS = {"<", "<=", "=", "==", ">=", ">", "!="}


def to_number(v: Any) -> Optional[float]:
    """값을 수치로(불가·비유한이면 None). bool 은 0/1."""
    import math
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        n = float(v)
    else:
        try:
            n = float(str(v).strip())
        except (TypeError, ValueError):
            return None
    return n if math.isfinite(n) else None   # inf/nan → 조건에서 no-match


def parse_range(s: str) -> Tuple[Optional[float], Optional[float]]:
    """마크식 범위. '10'→(10,10) '10..'→(10,None) '..10'→(None,10) '5..10'→(5,10).

    실패 시 ValueError.
    """
    s = (s or "").strip()
    if not s:
        raise ValueError("범위가 비었습니다")
    if ".." in s:
        lo_s, hi_s = s.split("..", 1)
        lo = to_number(lo_s) if lo_s.strip() else None
        hi = to_number(hi_s) if hi_s.strip() else None
        if lo_s.strip() and lo is None:
            raise ValueError(f"범위 하한 '{lo_s}' 가 숫자가 아닙니다")
        if hi_s.strip() and hi is None:
            raise ValueError(f"범위 상한 '{hi_s}' 가 숫자가 아닙니다")
        return lo, hi
    n = to_number(s)
    if n is None:
        raise ValueError(f"'{s}' 는 숫자/범위가 아닙니다")
    return n, n


def match_range(value: Any, rng: Tuple[Optional[float], Optional[float]]) -> bool:
    n = to_number(value)
    if n is None:
        return False
    lo, hi = rng
    if lo is not None and n < lo:
        return False
    if hi is not None and n > hi:
        return False
    return True


def compare(a: Any, op: str, b: Any) -> bool:
    """두 수치 비교. op ∈ _CMP_OPS. 수치 변환 불가면 False."""
    x, y = to_number(a), to_number(b)
    if x is None or y is None:
        return False
    if op in ("=", "=="):
        return x == y
    if op == "!=":
        return x != y
    if op == "<":
        return x < y
    if op == "<=":
        return x <= y
    if op == ">":
        return x > y
    if op == ">=":
        return x >= y
    return False


def is_cmp_op(tok: str) -> bool:
    return tok in _CMP_OPS


def tokenize_pos(s: str) -> List[Tuple[str, int, int]]:
    """tokenize + 각 토큰의 (값, 시작, 끝) 위치. 끝 위치로 원문 명령을 슬라이스한다.

    따옴표/중괄호/대괄호 안 공백은 보존. 따옴표 토큰은 값에서 양끝 따옴표를 뗀다.
    """
    out: List[Tuple[str, int, int]] = []
    i = 0
    n = len(s)
    while i < n:
        while i < n and s[i].isspace():
            i += 1
        if i >= n:
            break
        tok_start = i
        c = s[i]
        if c in ("\"", "'"):
            q = c
            i += 1
            buf = []
            esc = False
            while i < n:
                ch = s[i]; i += 1
                if esc:
                    buf.append(ch); esc = False
                elif ch == "\\":
                    esc = True
                elif ch == q:
                    break
                else:
                    buf.append(ch)
            out.append(("".join(buf), tok_start, i))
        elif c in ("{", "["):
            close = "}" if c == "{" else "]"
            depth = 0
            start = i
            in_q = None
            esc = False
            while i < n:
                ch = s[i]; i += 1
                if in_q is not None:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == in_q:
                        in_q = None
                elif ch in ("\"", "'"):
                    in_q = ch
                elif ch == c:
                    depth += 1
                elif ch == close:
                    depth -= 1
                    if depth == 0:
                        break
            out.append((s[start:i], tok_start, i))
        else:
            start = i
            while i < n and not s[i].isspace():
                i += 1
            out.append((s[start:i], tok_start, i))
    return out


def tokenize(s: str) -> List[str]:
    """공백 분리(따옴표/괄호 안 공백 보존). 따옴표 토큰은 따옴표 제거."""
    return [t for t, _, _ in tokenize_pos(s)]
