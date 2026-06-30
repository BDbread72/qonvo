"""`/create <노드>{데이터태그}` — 마인크래프트식 데이터 태그 파라미터.

마크의 `/give @s diamond_sword{Enchantments:[...]}` 처럼, 생성할 노드 종류 뒤에
**중괄호 데이터 태그**를 붙여 초기 속성을 지정한다.

    /create chat{model:"gemini-2.5-flash"}
    /create text{text:"안녕하세요", size:18} 100 64
    /create number{value:42, step:5}
    /create math{a:3, b:4, op:mul}
    /create sticky{text:"메모", color:blue, name:"중요"}

이 모듈은 세 가지를 제공한다:
  1. parse_tag()   — 관대한 SNBT/JSON 풍 파서 ( { } 안을 dict 로 )
  2. PARAM_SCHEMA   — 노드별 받을 수 있는 키 (자동완성·/help·검증의 단일 진실원)
  3. apply_params() — 생성된 라이브 노드에 값 적용 (저장·서버sync 는 _mark_node_dirty 가 처리)

새 파라미터 추가 = SCHEMA 한 줄 + APPLIERS 한 줄. 그 외 배선 불필요.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# 1. 관대한 SNBT/JSON 파서  —  { key:value, k2:"문자열", k3:[a,b], flag:true }
# ---------------------------------------------------------------------------
_QUOTES = ("\"", "'")


class TagSyntaxError(ValueError):
    """데이터 태그 파싱 실패. 사용자에게 그대로 보여줄 한글 메시지를 담는다."""


class _TagReader:
    def __init__(self, s: str):
        self.s = s
        self.i = 0
        self.n = len(s)

    def eof(self) -> bool:
        return self.i >= self.n

    def peek(self) -> str:
        return self.s[self.i]

    def ws(self):
        while not self.eof() and self.s[self.i].isspace():
            self.i += 1

    def read_quoted(self) -> str:
        q = self.s[self.i]
        self.i += 1
        out = []
        while not self.eof():
            c = self.s[self.i]; self.i += 1
            if c == "\\" and not self.eof():
                nxt = self.s[self.i]; self.i += 1
                out.append(nxt if nxt in (q, "\\") else "\\" + nxt)
            elif c == q:
                return "".join(out)
            else:
                out.append(c)
        raise TagSyntaxError("따옴표가 닫히지 않았습니다")

    def read_key(self) -> str:
        if self.eof():
            raise TagSyntaxError("키가 필요합니다")
        if self.peek() in _QUOTES:
            return self.read_quoted()
        start = self.i
        while not self.eof() and self.s[self.i] not in (":", ",", "}", "{", "[", "]") \
                and not self.s[self.i].isspace():
            self.i += 1
        key = self.s[start:self.i]
        if not key:
            raise TagSyntaxError(f"키가 비어 있습니다 (위치 {self.i})")
        return key

    def read_scalar(self) -> Any:
        """다음 콤마/닫는기호 전까지의 한 값 (따옴표 문자열 또는 bareword)."""
        if self.peek() in _QUOTES:
            return self.read_quoted()
        start = self.i
        while not self.eof() and self.s[self.i] not in (",", "}", "]"):
            self.i += 1
        return _coerce(self.s[start:self.i].strip())

    def read_value(self) -> Any:
        if self.eof():
            raise TagSyntaxError("값이 필요합니다")
        if self.peek() == "[":
            return self.read_list()
        return self.read_scalar()

    def read_list(self) -> list:
        self.i += 1  # consume '['
        out: list = []
        self.ws()
        if not self.eof() and self.peek() == "]":
            self.i += 1
            return out
        while True:
            self.ws()
            out.append(self.read_scalar())
            self.ws()
            if self.eof():
                raise TagSyntaxError("']' 가 필요합니다")
            c = self.peek()
            if c == ",":
                self.i += 1; continue
            if c == "]":
                self.i += 1; return out
            raise TagSyntaxError(f"',' 또는 ']' 가 필요합니다 (위치 {self.i})")


def _coerce(tok: str) -> Any:
    """bareword → bool / int / float / str 로 자동 변환."""
    if tok == "":
        return ""
    low = tok.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        return int(tok)
    except ValueError:
        pass
    try:
        return float(tok)
    except ValueError:
        pass
    return tok


def parse_tag(raw: str) -> Dict[str, Any]:
    """`{...}` 데이터 태그 문자열 → dict. 실패 시 TagSyntaxError(한글 메시지)."""
    s = (raw or "").strip()
    if not s:
        return {}
    if not (s.startswith("{") and s.endswith("}")):
        raise TagSyntaxError("데이터 태그는 { } 로 감싸야 합니다")
    r = _TagReader(s[1:-1])
    out: Dict[str, Any] = {}
    r.ws()
    if r.eof():
        return out
    while True:
        r.ws()
        key = r.read_key()
        r.ws()
        if r.eof() or r.peek() != ":":
            raise TagSyntaxError(f"'{key}' 뒤에 ':' 가 필요합니다")
        r.i += 1
        r.ws()
        out[key] = r.read_value()
        r.ws()
        if r.eof():
            break
        if r.peek() == ",":
            r.i += 1
            r.ws()
            if r.eof():   # 끝 콤마 허용
                break
            continue
        raise TagSyntaxError(f"',' 또는 '}}' 가 필요합니다 (위치 {r.i})")
    return out


def parse_value(s: str) -> Any:
    """SNBT 단일 값 하나 → 파이썬 값. `/data set` 등에서 씀.

    `{...}` → dict, `[...]` → list, `"..."`/숫자/bool/단어 → 스칼라.
    """
    s = (s or "").strip()
    if not s:
        return ""
    if s.startswith("{"):
        return parse_tag(s)          # 컴파운드
    return _TagReader(s).read_value()  # 리스트 또는 스칼라


# ---------------------------------------------------------------------------
# 2. 파라미터 스키마 — 노드별 받을 수 있는 키
#    values: 정적 리스트 / callable(cc)->list / None(자유값)
# ---------------------------------------------------------------------------
def _model_ids(cc) -> List[str]:
    try:
        from v.model_plugin import get_all_model_ids
        return list(get_all_model_ids())
    except Exception:
        return []


def _sticky_colors(cc) -> List[str]:
    try:
        from .sticky_note import STICKY_COLORS
        return list(STICKY_COLORS.keys())
    except Exception:
        return []


_MATH_OPS = ["add", "sub", "mul", "div", "mod", "pow",
             "min", "max", "gt", "lt", "ge", "le", "eq", "ne"]
_ROLES = ["system", "user", "assistant"]

# 모든 노드 공통
_UNIVERSAL: Dict[str, dict] = {
    "name": {"type": "str", "values": None, "desc": "노드 이름표"},
}

# 노드별 (universal 은 자동 병합)
PARAM_SCHEMA: Dict[str, Dict[str, dict]] = {
    "chat":     {"model": {"type": "str", "values": _model_ids, "desc": "사용할 모델"}},
    "text":     {"text": {"type": "str", "values": None, "desc": "본문"},
                 "size": {"type": "int", "values": None, "desc": "글자 크기(pt)"}},
    "markdown": {"text": {"type": "str", "values": None, "desc": "마크다운 본문"}},
    "prompt":   {"text": {"type": "str", "values": None, "desc": "프롬프트 본문"},
                 "role": {"type": "str", "values": _ROLES, "desc": "역할"}},
    "sticky":   {"text":  {"type": "str", "values": None, "desc": "메모 본문"},
                 "color": {"type": "str", "values": _sticky_colors, "desc": "색상"}},
    "number":   {"value": {"type": "num", "values": None, "desc": "초기값"},
                 "step":  {"type": "num", "values": None, "desc": "증감 단위"}},
    "math":     {"a":  {"type": "num", "values": None, "desc": "A 값"},
                 "b":  {"type": "num", "values": None, "desc": "B 값"},
                 "op": {"type": "str", "values": _MATH_OPS, "desc": "연산자"}},
    "button":   {"label": {"type": "str", "values": None, "desc": "버튼 라벨"}},
    "group":    {"label": {"type": "str", "values": None, "desc": "그룹 라벨"}},
}

# markdown 은 `md` 도 `text` 의 별칭으로 허용
_KEY_ALIASES: Dict[str, Dict[str, str]] = {
    "markdown": {"md": "text"},
}


def params_for(node_key: str) -> Dict[str, dict]:
    """node_key 가 받는 전체 파라미터(공통 + 전용). 키 → spec."""
    out = dict(_UNIVERSAL)
    out.update(PARAM_SCHEMA.get(node_key, {}))
    return out


def value_suggestions(node_key: str, param: str, cc) -> List[str]:
    """param 값 자동완성 후보(enum 이면 목록, 자유값이면 빈 리스트)."""
    spec = params_for(node_key).get(param)
    if not spec:
        return []
    vals = spec.get("values")
    if callable(vals):
        try:
            return list(vals(cc))
        except Exception:
            return []
    return list(vals) if vals else []


# ---------------------------------------------------------------------------
# 3. 적용기 — 생성된 라이브 노드에 값 반영
#    각 적용기: fn(plugin, node, value) ; 실패 시 ValueError(한글) raise
# ---------------------------------------------------------------------------
def _as_num(v) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        try:
            return float(str(v))
        except (TypeError, ValueError):
            raise ValueError("숫자가 필요합니다")
    return float(v)


def _chat_model(plugin, node, v):
    idx = node.model_combo.findData(str(v))
    if idx < 0:
        raise ValueError(f"모델 '{v}' 을(를) 찾을 수 없습니다")
    node.model_combo.setCurrentIndex(idx)


def _text_text(plugin, node, v):
    node.setPlainText(str(v))


def _text_size(plugin, node, v):
    size = int(_as_num(v))
    node._font_size = size
    font = node.font()
    font.setPointSize(size)
    node.setFont(font)


def _md_text(plugin, node, v):
    node.set_markdown(str(v))


def _prompt_text(plugin, node, v):
    node.body_edit.setPlainText(str(v))


def _prompt_role(plugin, node, v):
    idx = node.role_combo.findData(str(v))
    if idx < 0:
        raise ValueError(f"역할 '{v}' 은(는) system/user/assistant 중 하나여야 합니다")
    node.role_combo.setCurrentIndex(idx)


def _sticky_text(plugin, node, v):
    node.body_edit.setPlainText(str(v))


def _sticky_color(plugin, node, v):
    from .sticky_note import STICKY_COLORS
    name = str(v)
    if name not in STICKY_COLORS:
        raise ValueError(f"색상 '{v}' 없음 ({', '.join(STICKY_COLORS)})")
    node._set_color(name)


def _num_value(plugin, node, v):
    node.set_value(_as_num(v), propagate=True, mark=True)


def _num_step(plugin, node, v):
    node._step = _as_num(v)
    node.step_edit.blockSignals(True)
    node.step_edit.setText(node._fmt(node._step))
    node.step_edit.blockSignals(False)


def _math_a(plugin, node, v):
    node._a = _as_num(v)
    node._evaluate(propagate=True)


def _math_b(plugin, node, v):
    node._b = _as_num(v)
    node._evaluate(propagate=True)


def _math_op(plugin, node, v):
    op = str(v)
    idx = node.op_combo.findData(op)
    if idx < 0:
        raise ValueError(f"연산자 '{v}' 없음 ({', '.join(_MATH_OPS)})")
    node.op_combo.setCurrentIndex(idx)


def _button_label(plugin, node, v):
    label = str(v)
    node._label = label
    node.title_label.setText(label)


def _group_label(plugin, node, v):
    node._label.setPlainText(str(v))


APPLIERS: Dict[str, Dict[str, Callable]] = {
    "chat":     {"model": _chat_model},
    "text":     {"text": _text_text, "size": _text_size},
    "markdown": {"text": _md_text},
    "prompt":   {"text": _prompt_text, "role": _prompt_role},
    "sticky":   {"text": _sticky_text, "color": _sticky_color},
    "number":   {"value": _num_value, "step": _num_step},
    "math":     {"a": _math_a, "b": _math_b, "op": _math_op},
    "button":   {"label": _button_label},
    "group":    {"label": _group_label},
}


def apply_params(plugin, node_key: str, node_id: int, node,
                 params: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """노드에 데이터 태그 값을 적용. (적용된 키, 오류메시지) 반환.

    저장/서버 동기화는 마지막 _mark_node_dirty 한 번으로 처리(name 은 자체 sync).
    """
    applied: List[str] = []
    errors: List[str] = []
    aliases = _KEY_ALIASES.get(node_key, {})
    appliers = APPLIERS.get(node_key, {})
    touched = False
    for raw_key, value in params.items():
        key = aliases.get(raw_key, raw_key)
        try:
            if key == "name":
                plugin.rename_node(node_id, str(value))   # 자체 저장+sync
                applied.append("name")
                continue
            fn = appliers.get(key)
            if fn is None:
                valid = ", ".join(params_for(node_key))
                errors.append(f"'{raw_key}' 는 {node_key} 가 받지 않는 키예요 (가능: {valid})")
                continue
            fn(plugin, node, value)
            applied.append(key)
            touched = True
        except ValueError as e:
            errors.append(f"{raw_key}: {e}")
        except Exception as e:
            errors.append(f"{raw_key}: 적용 실패 ({e})")
    if touched:
        try:
            plugin._mark_node_dirty(node_id)   # 저장 + 서버 prop sync(throttle)
        except Exception:
            pass
    return applied, errors
