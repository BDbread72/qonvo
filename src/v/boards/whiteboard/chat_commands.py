"""채팅 명령어 — 마인크래프트식 `/명령어` 시스템.

라이브 협업 채팅창에서 `/` 로 시작하면 명령, 아니면 채팅 메시지.
명령 엔진은 vendored `mccmd`(Brigadier 포팅) 를 그대로 쓴다 — 자동완성/사용법
힌트/권한 게이트가 전부 거기 들어있다. 여기서는 **qonvo 전용 명령들**과,
명령이 실행될 때 필요한 앱 컨텍스트(소스)를 정의·배선한다.

    controller = ChatCommandController(ui)
    controller.set_client(server_client)          # 서버 입장 시
    res = controller.execute("create chat")       # ExecutionResult
    comps = controller.suggest("cre", 3)          # [Completion ...]
    hint  = controller.ghost("create ", 7)        # "<node>" 등

현재 명령 세트(4개):
    /help  <명령|페이지=1>   명령 목록(페이지) 또는 특정 명령 사용법
    /clear                   내가 보는 채팅창을 지운다(로컬 전용)
    /create <노드> [위치] [params..]   노드 생성(기본 위치 = 커서)
    /move   <대상> <위치> [모드]       노드/카메라 이동(기본 모드 = emit 즉시)
"""
from __future__ import annotations

from typing import Any, Callable, List, Optional, Tuple

from v.logger import get_logger

logger = get_logger("qonvo.chat_commands")

from mccmd import Command, CommandRegistry, CommandService, literal, argument
from mccmd.arguments import word, vec2, greedy_string, integer
from mccmd.arguments.types import ArgumentType


# ── 권한 레벨 (서버 협업과 동일: Visitor0 / Member1 / Operator2) ──
VISITOR, MEMBER, OPERATOR = 0, 1, 2


# ---------------------------------------------------------------------------
# 컨텍스트 / 소스 — 명령이 손댈 수 있는 앱 객체들
# ---------------------------------------------------------------------------
class CommandContext:
    """명령 실행에 필요한 라이브 앱 핸들 묶음.

    ui 만 들고, plugin/view 는 매번 현재 값으로 해석(보드 전환에도 안전).
    client 는 서버 입장/퇴장 때 set_client 로 갱신.
    """

    def __init__(self, ui):
        self.ui = ui
        self.client = None            # ServerClient | None
        self.last_presence: list = []  # 최근 presence_list (move <user> 대상)
        # 출력 라우팅 훅 — 패널/말풍선 어느 쪽이든 꽂아 쓴다.
        self.chat_sink: Optional[Callable[[str], None]] = None   # 평문 채팅 전송
        self.clear_sink: Optional[Callable[[], None]] = None     # 로그/말풍선 비우기
        self._anims: list = []        # 이동 애니메이션 GC 방지용 강참조
        # /function 실행 — 컨트롤러가 run_command 를 주입(명령 줄 하나 실행).
        self.run_command: Optional[Callable[[str], Any]] = None
        self.controller: Any = None      # ChatCommandController (에디터가 씀)
        self._fn_depth: int = 0          # 현재 함수 중첩 깊이(=함수 안인지 판별)
        self._fn_steps: int = 0          # top-level 호출당 누적 명령 수(폭주 방지)
        self._return_pending: bool = False
        self._return_value: int = 0

    @property
    def plugin(self):
        return getattr(self.ui, "current_plugin", None)

    @property
    def view(self):
        p = self.plugin
        return getattr(p, "view", None) if p is not None else None

    @property
    def level(self) -> int:
        c = self.client
        if c is not None and getattr(c, "level", -1) >= 0:
            return int(c.level)
        return OPERATOR  # 솔로(서버 미접속)면 전권

    @property
    def in_server(self) -> bool:
        return self.client is not None and getattr(self.client, "is_connected", False)

    @property
    def my_name(self) -> str:
        c = self.client
        return (getattr(c, "username", "") or "") if c is not None else ""

    def cursor_scene_xy(self) -> Tuple[float, float]:
        """현재 마우스의 씬 좌표. 뷰 밖이면 뷰포트 중심으로 폴백."""
        view = self.view
        if view is not None:
            try:
                from PyQt6.QtGui import QCursor
                vp = view.viewport()
                p = vp.mapFromGlobal(QCursor.pos())
                if vp.rect().contains(p):
                    sp = view.mapToScene(p)
                    return (sp.x(), sp.y())
            except Exception:
                pass
        plugin = self.plugin
        if plugin is not None:
            try:
                c = plugin._cursor_scene_pos()
                return (c.x(), c.y())
            except Exception:
                pass
        return (0.0, 0.0)


class ChatSource:
    """mccmd 가 기대하는 소스. send_message 로 결과를 모으고, ctx 로 앱에 접근."""

    def __init__(self, ctx: CommandContext):
        self.ctx = ctx
        self._on_output: Optional[Callable[[str], None]] = None
        self.name = "you"
        # _make_source 가 주입하는 핸들들 (기본 None — bare 소스에서도 안전)
        self._panel: Any = None
        self._all_usage: Callable[[], List[str]] = lambda: []
        self._usage_for: Callable[[str], List[str]] = lambda _n: []

    def set_output(self, callback):
        self._on_output = callback
        return self

    def send_message(self, text):
        if self._on_output is not None:
            self._on_output(text)


def _require_level(level: int):
    """permission 콜백 — source.ctx.level 이 level 이상이어야 허용."""
    def check(source):
        try:
            return source.ctx.level >= level
        except Exception:
            return True
    return check


# ---------------------------------------------------------------------------
# 생성 가능한 노드 — 키 → (plugin, QPointF) 팩토리. 방사형(Tab) 메뉴와 동일한 집합.
# ---------------------------------------------------------------------------
CREATE_NODES: dict = {
    "chat":      lambda p, pos: p.add_node(pos),
    "function":  lambda p, pos: p.add_function(pos),
    "nixi":      lambda p, pos: p.add_nixi(pos),
    "prompt":    lambda p, pos: p.add_prompt_node(pos),
    "sticky":    lambda p, pos: p.add_sticky(pos),
    "text":      lambda p, pos: p.add_text_item(pos),
    "markdown":  lambda p, pos: p.add_markdown(pos),
    "checklist": lambda p, pos: p.add_checklist(pos),
    "image":     lambda p, pos: p.add_image_card("", pos),
    "file":      lambda p, pos: p.add_file_node(None, pos),
    "dimension": lambda p, pos: p.add_dimension_item(pos),
    "button":    lambda p, pos: p.add_button(pos),
    "switch":    lambda p, pos: p.add_switch(pos),
    "latch":     lambda p, pos: p.add_latch(pos),
    "not":       lambda p, pos: p.add_not_gate(pos),
    "and":       lambda p, pos: p.add_and_gate(pos),
    "or":        lambda p, pos: p.add_or_gate(pos),
    "xor":       lambda p, pos: p.add_xor_gate(pos),
    "bulb":      lambda p, pos: p.add_bulb(pos),
    "number":    lambda p, pos: p.add_number(pos),
    "math":      lambda p, pos: p.add_math(pos),
    "group":     lambda p, pos: p.add_group_frame(pos),
}

# 별칭 → 정식 키
CREATE_ALIASES: dict = {
    "node": "chat", "func": "function", "md": "markdown",
    "img": "image", "picture": "image", "files": "file",
    "dim": "dimension", "grp": "group", "check": "checklist",
}


def _resolve_node_key(raw: str) -> Optional[str]:
    raw = (raw or "").strip().lower()
    if raw in CREATE_NODES:
        return raw
    return CREATE_ALIASES.get(raw)


def _read_brace_block(reader):
    """현재 위치('{')부터 균형 잡힌 '}' 까지(따옴표 인식). raw 문자열(중괄호 포함) 반환."""
    from mccmd.errors import CommandSyntaxError
    start = reader.cursor
    depth = 0
    in_q = None
    esc = False
    while reader.can_read():
        c = reader.read()
        if in_q is not None:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == in_q:
                in_q = None
        else:
            if c in ("\"", "'"):
                in_q = c
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return reader.string[start:reader.cursor]
    reader.cursor = start
    raise CommandSyntaxError("데이터 태그의 '}' 가 닫히지 않았습니다", reader)


class NodeSpecArgumentType(ArgumentType):
    """생성할 노드 종류 + 선택적 데이터 태그.  `chat` 또는 `chat{model:"..."}`.

    parse → (node_key, raw_tag|None). raw_tag 의 실제 해석은 run 단계(친절한 안내).
    자동완성: 중괄호 밖 → 노드 키, 중괄호 안 → 파라미터 키/값.
    """

    def parse(self, reader, source=None):
        from mccmd.errors import CommandSyntaxError
        start = reader.cursor
        raw = reader.read_unquoted_string()
        if not raw:
            raise CommandSyntaxError("노드 종류를 입력하세요", reader)
        key = _resolve_node_key(raw)
        if key is None:
            reader.cursor = start
            raise CommandSyntaxError(f"알 수 없는 노드 '{raw}'", reader)
        tag = None
        if reader.can_read() and reader.peek() == "{":
            tag = _read_brace_block(reader)   # 균형 안 맞으면 CommandSyntaxError
        return (key, tag)

    def list_suggestions(self, context, builder):
        rem = builder.remaining
        brace = rem.find("{")
        if brace < 0:
            typed = builder.remaining_lower
            for key in CREATE_NODES:
                if key.startswith(typed):
                    builder.suggest(key)
            for alias, key in CREATE_ALIASES.items():
                if alias.startswith(typed):
                    builder.suggest(alias, tooltip=key)
            return builder.build()
        try:
            key = _resolve_node_key(rem[:brace])
            cc = getattr(context.source, "ctx", None)
            return _suggest_tag_inner(key, builder, rem, brace, cc)
        except Exception:
            return builder.build()

    def get_examples(self):
        return ["chat", "text{text:\"hi\"}", "number{value:42}"]


def node_spec_arg():
    return NodeSpecArgumentType()


def _suggest_tag_inner(node_key, builder, rem, brace, cc):
    """`{...}` 데이터 태그 안 자동완성 — 키/값. rem 의 brace 위치부터 스캔.

    NodeSpecArgumentType(노드에 붙은 태그)와 DataTagArgumentType(끝에 떨어진 태그)
    둘이 공유한다. 절대 오프셋 = builder.start + (rem 내 위치).
    """
    from .cmd_params import params_for, value_suggestions
    if node_key is None:
        return builder.build()
    inner_start = brace + 1
    seg_begin = inner_start
    cur_colon = -1
    used = []
    in_q = None
    esc = False
    j = inner_start
    while j < len(rem):
        c = rem[j]
        if in_q is not None:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == in_q:
                in_q = None
        elif c in ("\"", "'"):
            in_q = c
        elif c == ",":
            seg = rem[seg_begin:j]
            k = (seg.split(":", 1)[0] if ":" in seg else seg).strip()
            if k:
                used.append(k)
            seg_begin = j + 1
            cur_colon = -1
        elif c == ":" and cur_colon < 0:
            cur_colon = j
        j += 1

    if cur_colon >= 0:
        pkey = rem[seg_begin:cur_colon].strip()
        val_start = cur_colon + 1
        while val_start < len(rem) and rem[val_start].isspace():
            val_start += 1
        partial = rem[val_start:]
        ob = builder.create_offset(builder.start + val_start)
        for v in value_suggestions(node_key, pkey, cc):
            if v.lower().startswith(partial.lower()):
                ob.suggest(v)
        return ob.build()

    key_start = seg_begin
    while key_start < len(rem) and rem[key_start].isspace():
        key_start += 1
    partial = rem[key_start:].lower()
    ob = builder.create_offset(builder.start + key_start)
    spec = params_for(node_key)
    for pk in spec:
        if pk not in used and pk.startswith(partial):
            ob.suggest(pk + ":", tooltip=spec[pk].get("desc"))
    return ob.build()


class DataTagArgumentType(ArgumentType):
    """끝에 떨어진 데이터 태그 `{...}` (공백 뒤). `{` 로 시작 안 하면 매칭 안 됨
    → vec2 위치 인자와 충돌 없음. 노드 키는 이미 파싱된 'node' 인자에서 가져온다."""

    def parse(self, reader, source=None):
        from mccmd.errors import CommandSyntaxError
        if not reader.can_read() or reader.peek() != "{":
            raise CommandSyntaxError("데이터 태그는 { 로 시작합니다", reader)
        return _read_brace_block(reader)

    def list_suggestions(self, context, builder):
        rem = builder.remaining
        if not rem.startswith("{"):
            return builder.build()
        try:
            spec = context.get_argument("node")
            node_key = spec[0] if isinstance(spec, tuple) else spec
        except Exception:
            return builder.build()
        cc = getattr(context.source, "ctx", None)
        try:
            return _suggest_tag_inner(node_key, builder, rem, 0, cc)
        except Exception:
            return builder.build()

    def get_examples(self):
        return ["{text:\"hi\"}", "{model:\"gemini-2.5-flash\"}"]


def data_tag_arg():
    return DataTagArgumentType()


class MoveTargetArgumentType(ArgumentType):
    """이동 대상 — 노드 이름 / 접속 사용자 / @s(나). 검증은 run 에서(친절한 안내)."""

    def parse(self, reader, source=None):
        from mccmd.errors import CommandSyntaxError
        from mccmd.reader import _is_quoted_string_start
        if not reader.can_read():
            raise CommandSyntaxError("이동할 대상을 입력하세요", reader)
        if _is_quoted_string_start(reader.peek()):
            raw = reader.read_string()
        else:
            # 공백 전까지 통째로 읽기 — @s 처럼 unquoted 비허용 문자도 받기 위함.
            start = reader.cursor
            while reader.can_read() and reader.peek() != " ":
                reader.skip()
            raw = reader.string[start:reader.cursor]
        if not raw:
            raise CommandSyntaxError("이동할 대상을 입력하세요", reader)
        return raw

    def list_suggestions(self, context, builder):
        typed = builder.remaining_lower
        try:
            cc = context.source.ctx
            for kw in ("@s", "me", "카메라"):
                if kw.startswith(typed):
                    builder.suggest(kw, tooltip="내 카메라")
            plugin = cc.plugin
            if plugin is not None:
                nodes = getattr(plugin.app, "nodes", {})
                for nid in nodes:
                    try:
                        nm = plugin.get_node_name(nid)
                    except Exception:
                        nm = ""
                    if nm and nm.lower().startswith(typed):
                        suggestion = ('"%s"' % nm) if " " in nm else nm
                        builder.suggest(suggestion, tooltip=f"#{nid}")
            for u in (cc.last_presence or []):
                un = u.get("user", "")
                if un and un.lower().startswith(typed):
                    builder.suggest(un, tooltip="사용자")
        except Exception:
            pass
        return builder.build()

    def get_examples(self):
        return ["@s", "Chat", "100 64"]


def move_target_arg():
    return MoveTargetArgumentType()


class NodeNameArgumentType(ArgumentType):
    """노드 이름 하나 (delete/connect/run 대상). 접속 사용자/@s 는 제안 안 함."""

    def parse(self, reader, source=None):
        from mccmd.errors import CommandSyntaxError
        from mccmd.reader import _is_quoted_string_start
        if not reader.can_read():
            raise CommandSyntaxError("노드 이름을 입력하세요", reader)
        if _is_quoted_string_start(reader.peek()):
            return reader.read_string()
        start = reader.cursor
        while reader.can_read() and reader.peek() != " ":
            reader.skip()
        raw = reader.string[start:reader.cursor]
        if not raw:
            raise CommandSyntaxError("노드 이름을 입력하세요", reader)
        return raw

    def list_suggestions(self, context, builder):
        typed = builder.remaining_lower
        try:
            cc = context.source.ctx
            plugin = cc.plugin
            if plugin is not None:
                for nid in getattr(plugin.app, "nodes", {}):
                    try:
                        nm = plugin.get_node_name(nid)
                    except Exception:
                        nm = ""
                    if nm and nm.lower().startswith(typed):
                        s = ('"%s"' % nm) if " " in nm else nm
                        builder.suggest(s, tooltip=f"#{nid}")
        except Exception:
            pass
        return builder.build()

    def get_examples(self):
        return ["Chat", "\"내 메모\""]


def node_name_arg():
    return NodeNameArgumentType()


class FunctionNameArgumentType(ArgumentType):
    """함수 이름 하나(따옴표로 공백 포함 가능). 자동완성은 저장된 함수 목록."""

    def parse(self, reader, source=None):
        return _read_name_token(reader, "함수 이름을 입력하세요")

    def list_suggestions(self, context, builder):
        typed = builder.remaining_lower
        try:
            from .cmd_functions import list_functions
            for name in list_functions():
                if name.lower().startswith(typed):
                    builder.suggest(name)
        except Exception:
            pass
        return builder.build()

    def get_examples(self):
        return ["setup", "greet"]


def function_name_arg():
    return FunctionNameArgumentType()


# ---------------------------------------------------------------------------
# 공통 헬퍼
# ---------------------------------------------------------------------------
def _resolve_vec2(loc, base_xy: Tuple[float, float]) -> Tuple[float, float]:
    """vec2() 파싱 결과((xc, yc)) 를 실제 씬 좌표로. ~ 상대는 base 기준."""
    bx, by = base_xy
    xc, yc = loc
    x = (bx + xc.value) if xc.kind != "abs" else xc.value
    y = (by + yc.value) if yc.kind != "abs" else yc.value
    return float(x), float(y)


def _node_id_by_name(plugin, name: str) -> Optional[int]:
    """이름 또는 §#id§ 로 노드 id 찾기. 첫 일치 반환(이름 중복은 #id 로 구분). 없으면 None."""
    target = (name or "").strip()
    if not target or plugin is None:
        return None
    nodes = getattr(plugin.app, "nodes", {})
    if target.startswith("#") and target[1:].isdigit():   # #5 처럼 id 직접 지정
        nid = int(target[1:])
        return nid if nid in nodes else None
    low = target.lower()
    for nid in nodes:
        try:
            nm = plugin.get_node_name(nid)
        except Exception:
            nm = ""
        if nm and nm.lower() == low:
            return nid
    return None


def _nodes_by_name(plugin, name: str) -> list:
    """이름이 일치하는 모든 노드 id(중복 이름 감지용)."""
    low = (name or "").strip().lower()
    out = []
    if not low or plugin is None or low.startswith("#"):
        return out
    for nid in getattr(plugin.app, "nodes", {}):
        try:
            nm = plugin.get_node_name(nid)
        except Exception:
            nm = ""
        if nm and nm.lower() == low:
            out.append(nid)
    return out


def _movable_item(plugin, node_id: int):
    """node_id 의 위치 조정 가능한 QGraphicsItem(proxy 또는 직접그림 아이템)."""
    proxy = plugin.proxies.get(node_id)
    if proxy is not None:
        return proxy
    return getattr(plugin.app, "nodes", {}).get(node_id)


def _place_node(plugin, node_id: int, x: float, y: float):
    """노드를 (x, y) 로 즉시 옮기고 서버모드면 동기화."""
    item = _movable_item(plugin, node_id)
    if item is None:
        return False
    from PyQt6.QtCore import QPointF
    item.setPos(QPointF(x, y))
    if getattr(plugin, "server_mode", False) and hasattr(plugin, "_send_node_move_op"):
        try:
            plugin._send_node_move_op(node_id, x, y)
        except Exception:
            pass
    plugin._notify_modified()
    return True


def _animate(cc: CommandContext, start_xy, end_xy, apply_fn, done_fn=None, ms: int = 340):
    """start→end 로 부드럽게 보간하며 apply_fn(x, y) 호출. anim 은 ctx 가 강참조."""
    from PyQt6.QtCore import QVariantAnimation, QPointF, QEasingCurve
    anim = QVariantAnimation()
    anim.setDuration(ms)
    anim.setStartValue(QPointF(*start_xy))
    anim.setEndValue(QPointF(*end_xy))
    anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

    def _on_val(v):
        try:
            apply_fn(v.x(), v.y())
        except Exception:
            pass

    def _on_fin():
        if done_fn is not None:
            try:
                done_fn()
            except Exception:
                pass
        try:
            cc._anims.remove(anim)
        except ValueError:
            pass

    anim.valueChanged.connect(_on_val)
    anim.finished.connect(_on_fin)
    cc._anims.append(anim)
    anim.start()
    return anim


# ---------------------------------------------------------------------------
# delete / connect / run / grep 공통 헬퍼
# ---------------------------------------------------------------------------
def _delete_node_by_id(plugin, nid: int) -> bool:
    """node_id 를 타입에 맞는 삭제 경로로 제거. view._delete_selected_items 와 동일 디스패치."""
    if nid in plugin.proxies:                      # 모든 위젯 노드
        plugin.delete_proxy_item(plugin.proxies[nid])
        return True
    item = getattr(plugin.app, "nodes", {}).get(nid)
    if item is None:
        return False
    if nid in plugin.text_items:
        plugin.delete_text_item(item)
    elif nid in plugin.image_card_items:
        plugin.delete_scene_item(item)
    elif nid in plugin.file_node_items:
        plugin.delete_file_node(item)
    elif nid in plugin.group_frame_items:
        plugin.delete_group_frame(item)
    elif nid in plugin.dimension_items:
        plugin.delete_dimension_item(item)
    else:
        return False
    return True


def _ports_of(plugin, item, port_type):
    try:
        ports = plugin._collect_ports(item)
    except Exception:
        ports = []
    return [p for p in ports if getattr(p, "port_type", None) == port_type]


def _pick_port(plugin, item, port_type, prefer_data_type=None):
    """item 의 대표 포트 1개. 같은 데이터타입(prefer_data_type) 우선 → 신호(⚡)보다 데이터 우선."""
    cand = _ports_of(plugin, item, port_type)
    if not cand:
        return None
    if prefer_data_type is not None:
        same = [p for p in cand if getattr(p, "port_data_type", None) == prefer_data_type]
        if same:
            # 같은 타입 중에서도 ⚡ 보다 일반 우선
            data = [p for p in same if not str(getattr(p, "port_name", "") or "").startswith("⚡")]
            return (data or same)[0]
    data = [p for p in cand if not str(getattr(p, "port_name", "") or "").startswith("⚡")]
    return (data or cand)[0]


def _named_port(plugin, item, port_type, name):
    """이름으로 포트 찾기 — 정확일치 → 접두 → 부분포함(⚡ 이모지/공백 무시). 없으면 None."""
    nl = (name or "").strip().lower()
    cand = _ports_of(plugin, item, port_type)
    norm = [(p, (getattr(p, "port_name", "") or "").lower()) for p in cand]
    for p, pn in norm:                 # 정확 일치
        if pn == nl:
            return p
    for p, pn in norm:                 # 접두(이모지 제거 후 시작)
        if nl and pn.lstrip("⚡ ").startswith(nl):
            return p
    for p, pn in norm:                 # 부분 포함
        if nl and nl in pn:
            return p
    return None


def _port_names(plugin, item, port_type):
    """그 노드의 포트 이름 목록(에러 안내용)."""
    return [getattr(p, "port_name", "") or "?" for p in _ports_of(plugin, item, port_type)]


class PortNameArgumentType(ArgumentType):
    """노드의 포트 이름 하나. 자동완성은 앞서 파싱된 노드(node_arg)의 해당 방향 포트들."""

    def __init__(self, node_arg, port_type_name):
        self._node_arg = node_arg          # "from" | "to"
        self._port_type_name = port_type_name  # "OUTPUT" | "INPUT"

    def parse(self, reader, source=None):
        from mccmd.errors import CommandSyntaxError
        from mccmd.reader import _is_quoted_string_start
        if not reader.can_read():
            raise CommandSyntaxError("포트 이름을 입력하세요", reader)
        if _is_quoted_string_start(reader.peek()):
            return reader.read_string()
        start = reader.cursor
        while reader.can_read() and reader.peek() != " ":
            reader.skip()
        return reader.string[start:reader.cursor]

    def list_suggestions(self, context, builder):
        try:
            from .items import PortItem
            cc = context.source.ctx
            plugin = cc.plugin
            node_name = context.get_argument(self._node_arg)
            nid = _node_id_by_name(plugin, node_name)
            if nid is None:
                return builder.build()
            pt = getattr(PortItem, self._port_type_name)
            typed = builder.remaining_lower
            for pn in _port_names(plugin, _movable_item(plugin, nid), pt):
                if pn.lower().startswith(typed) or typed in pn.lower():
                    builder.suggest(('"%s"' % pn) if " " in pn else pn)
        except Exception:
            pass
        return builder.build()

    def get_examples(self):
        return ["값", "\"⚡ 증가\""]


def _run_node(node) -> Tuple[bool, str]:
    """노드를 '신호가 들어온 것처럼' 실행. (성공, 사유)."""
    if node is None:
        return False, "노드 없음"
    if hasattr(node, "_on_click"):            # 버튼: 클릭 = 신호 emit
        node._on_click()
        return True, "신호 emit"
    if hasattr(node, "on_signal_input"):      # 챗/함수/프롬프트/마크다운 등
        node.on_signal_input()
        return True, "실행"
    return False, "실행할 수 없는 노드"


def _node_search_text(plugin, nid: int, node) -> str:
    """grep 대상 텍스트 — 이름 + 본문/응답/값 등 알려진 필드를 모은다."""
    parts = []
    try:
        parts.append(plugin.get_node_name(nid) or "")
    except Exception:
        pass
    be = getattr(node, "body_edit", None)     # prompt / sticky
    if be is not None and hasattr(be, "toPlainText"):
        try:
            parts.append(be.toPlainText())
        except Exception:
            pass
    elif hasattr(node, "toPlainText"):        # 텍스트 아이템
        try:
            parts.append(node.toPlainText())
        except Exception:
            pass
    md = getattr(node, "_raw_md", None)
    if isinstance(md, str):
        parts.append(md)
    lbl = getattr(node, "_label", None)
    if isinstance(lbl, str):
        parts.append(lbl)
    for attr in ("_value", "_result"):
        v = getattr(node, attr, None)
        if isinstance(v, (int, float)):
            parts.append(str(v))
    hist = getattr(node, "_history", None)    # 챗 대화
    if isinstance(hist, list):
        for h in hist:
            if isinstance(h, dict):
                parts.append(str(h.get("user", "")))
                parts.append(str(h.get("response", "")))
    return "\n".join(p for p in parts if p)


def _node_type_label(plugin, nid: int, node) -> str:
    """grep/list 표시용 짧은 타입 이름."""
    from .node_title import default_name_for
    try:
        return default_name_for(node)
    except Exception:
        return type(node).__name__


# ---------------------------------------------------------------------------
# 명령어들
# ---------------------------------------------------------------------------
_HELP_PER_PAGE = 6


class HelpCommand(Command):
    name = "help"
    aliases = ("?", "도움말")
    description = "명령 목록(페이지) 또는 특정 명령의 상세 사용법"

    def build(self, root):
        root.executes(self.run_default)
        root.then(argument("arg", word()).executes(self.run_arg))
        return root

    def run_default(self, ctx):
        return self._list(ctx, 1)

    def run_arg(self, ctx):
        raw = (ctx.get_argument("arg") or "").strip()
        if raw.lstrip("-").isdigit():
            return self._list(ctx, int(raw))
        return self._detail(ctx, raw.lstrip("/").lower())

    def _list(self, ctx, page: int):
        cmds = CHAT_COMMANDS
        pages = max(1, (len(cmds) + _HELP_PER_PAGE - 1) // _HELP_PER_PAGE)
        page = max(1, min(page, pages))
        start = (page - 1) * _HELP_PER_PAGE
        ctx.source.send_message(f"§b◆ 명령어 §7(페이지 {page}/{pages})")
        for cmd in cmds[start:start + _HELP_PER_PAGE]:
            desc = (cmd.description or "").strip()
            ctx.source.send_message(f"  §b/{cmd.name}§r §7— {desc}")
        if pages > 1:
            nxt = page + 1 if page < pages else 1
            ctx.source.send_message(
                f"§7상세: §b/help <명령>§7   ·   다음장: §b/help {nxt}§7   ·   채팅은 그냥 입력")
        else:
            ctx.source.send_message("§7상세: §b/help <명령>§7   ·   채팅은 그냥 입력 후 Enter")
        return 1

    def _detail(self, ctx, name: str):
        target = None
        for cmd in CHAT_COMMANDS:
            if cmd.name == name or name in getattr(cmd, "aliases", ()):
                target = cmd
                break
        if target is None:
            ctx.source.send_message(f"§e'/{name}' 명령을 찾을 수 없습니다.§r  §7/help 로 목록 보기")
            return 0
        ctx.source.send_message(f"§b/{target.name}§r §7— {(target.description or '').strip()}")
        aliases = getattr(target, "aliases", ())
        if aliases:
            ctx.source.send_message("  §7별칭: " + ", ".join("/" + a for a in aliases))
        usage = ctx.source._usage_for(target.name)
        if usage:
            ctx.source.send_message("  §7사용법:")
            for ln in usage:
                ctx.source.send_message("    §b/" + ln)
        if target.name == "create":
            self._create_params(ctx)
        if target.name == "execute":
            self._execute_help(ctx)
        return 1

    def _execute_help(self, ctx):
        """/help execute — 조건 종류 레퍼런스."""
        lines = [
            "  §7조건(§bif§7/§bunless§7) 종류:",
            "    §bdata <키> [matches <범위>|<op> <수>]§7 — 저장소 값",
            "    §bvalue <노드> [matches <범위>|<op> <수>]§7 — number/math 노드 값",
            "    §bnode <이름>§7 — 노드 존재",
            "    §bonline <유저>§7 — 접속 중(서버)",
            "    §bcursor <유저> x|y <op> <수>§7 — 커서 좌표(서버)",
            "  §7범위: §b10§7(정확) §b10..§7(이상) §b..10§7(이하) §b5..10§7(사이)",
            "  §7예: §b/execute if value 카운터 matches 100.. run delete 카운터",
            "  §7  계산→저장: §b/execute store result data n run grep TODO",
        ]
        for ln in lines:
            ctx.source.send_message(ln)

    def _create_params(self, ctx):
        """/help create — 노드별 데이터 태그 키 목록(마크식 {key:value})."""
        try:
            from .cmd_params import PARAM_SCHEMA
        except Exception:
            return
        ctx.source.send_message("  §7데이터 태그 §b{key:value, ...}§7 (모든 노드 §bname§7 가능):")
        for node_key, spec in PARAM_SCHEMA.items():
            keys = ", ".join(spec.keys())
            ctx.source.send_message(f"    §b{node_key}§r §7— {keys}")
        ctx.source.send_message("  §7예: §b/create chat{model:\"gemini-2.5-flash\", name:\"분석\"}")


class ClearCommand(Command):
    name = "clear"
    aliases = ("cls",)
    description = "내가 보는 채팅창을 비운다(나에게만 적용)"

    def run(self, ctx):
        cc = ctx.source.ctx
        if cc.clear_sink is not None:
            try:
                cc.clear_sink()
                return 1
            except Exception:
                pass
        panel = getattr(ctx.source, "_panel", None)
        if panel is not None and hasattr(panel, "clear_log"):
            panel.clear_log()
        return 1


class CreateCommand(Command):
    name = "create"
    aliases = ("new", "spawn")
    description = "노드를 생성한다 (기본 위치 = 커서). 마크식 데이터 태그: §bchat{model:\"...\"}§r"
    permission = staticmethod(_require_level(MEMBER))

    def build(self, root):
        node = argument("node", node_spec_arg())
        node.executes(self.run_cursor)
        # 끝에 떨어진 태그도 허용: create prompt {text:"..."}
        node.then(argument("tag", data_tag_arg()).executes(self.run_cursor))
        loc = argument("location", vec2())
        loc.executes(self.run_loc)
        # 위치 뒤 태그도 허용: create prompt 0 0 {text:"..."}
        loc.then(argument("tag", data_tag_arg()).executes(self.run_loc))
        node.then(loc)
        root.then(node)
        return root

    def run_cursor(self, ctx):
        cc = ctx.source.ctx
        return self._spawn(ctx, ctx.get_argument("node"), cc.cursor_scene_xy())

    def run_loc(self, ctx):
        cc = ctx.source.ctx
        try:
            x, y = _resolve_vec2(ctx.get_argument("location"), cc.cursor_scene_xy())
        except Exception:
            ctx.source.send_message("§e위치 좌표를 해석할 수 없습니다 (예: §b100 64§e 또는 §b~ ~§e)")
            return 0
        return self._spawn(ctx, ctx.get_argument("node"), (x, y))

    def _spawn(self, ctx, spec, xy: Tuple[float, float]):
        # spec = (node_key, raw_tag|None). 끝에 떨어진 태그(tag 인자)가 있으면 그게 우선.
        key, raw_tag = spec if isinstance(spec, tuple) else (spec, None)
        try:
            trailing = ctx.get_argument("tag")
            if trailing:
                raw_tag = trailing
        except Exception:
            pass
        cc = ctx.source.ctx
        plugin = cc.plugin
        if plugin is None:
            ctx.source.send_message("§e활성 보드가 없습니다")
            return 0
        factory = CREATE_NODES.get(key)
        if factory is None:
            ctx.source.send_message(f"§e알 수 없는 노드 '{key}'")
            return 0
        # 데이터 태그 먼저 파싱 + 키 검증 — 잘못됐으면 노드를 아예 만들지 않는다(부분생성 방지).
        params = {}
        if raw_tag:
            from .cmd_params import parse_tag, TagSyntaxError, params_for, _KEY_ALIASES
            try:
                params = parse_tag(raw_tag)
            except TagSyntaxError as e:
                ctx.source.send_message(f"§e데이터 태그 오류: {e}")
                return 0
            valid = params_for(key)
            aliases = _KEY_ALIASES.get(key, {})
            unknown = [k for k in params if aliases.get(k, k) not in valid]
            if unknown:
                ctx.source.send_message(
                    f"§e'{key}' 가 모르는 키: §b{', '.join(unknown)}§e §7(가능: {', '.join(valid)})")
                return 0
        from PyQt6.QtCore import QPointF
        before = set(getattr(plugin.app, "nodes", {}).keys())
        try:
            factory(plugin, QPointF(xy[0], xy[1]))
        except Exception as e:
            logger.debug("create node failed: %s", e)
            ctx.source.send_message(f"§e노드 생성 실패: {e}")
            return 0
        ctx.source.send_message(f"§a'{key}' 노드를 생성했습니다 §7({int(xy[0])}, {int(xy[1])})")
        # 데이터 태그 적용 — 방금 생긴 노드를 app.nodes diff 로 찾는다.
        if params:
            new_ids = [i for i in getattr(plugin.app, "nodes", {}) if i not in before]
            if not new_ids:
                ctx.source.send_message("§e데이터 태그를 적용할 노드를 찾지 못했습니다")
                return 1
            from .cmd_params import apply_params
            nid = new_ids[0]
            node = plugin.app.nodes[nid]
            applied, errors = apply_params(plugin, key, nid, node, params)
            if applied:
                ctx.source.send_message("§7  적용: §b" + "§7, §b".join(applied))
            for err in errors:
                ctx.source.send_message(f"§e  {err}")
        return 1


class MoveCommand(Command):
    name = "move"
    aliases = ("tp", "goto")
    description = "노드/카메라를 위치로 이동 (대상=노드이름·@s, 모드=emit|fade)"
    permission = staticmethod(_require_level(MEMBER))

    def build(self, root):
        tgt = argument("target", move_target_arg())
        loc = argument("location", vec2())
        loc.executes(self.run_emit)
        loc.then(literal("emit").executes(self.run_emit))
        loc.then(literal("fade").executes(self.run_fade))
        loc.then(literal("ease").executes(self.run_fade))
        tgt.then(loc)
        root.then(tgt)
        return root

    def run_emit(self, ctx):
        return self._do(ctx, fade=False)

    def run_fade(self, ctx):
        return self._do(ctx, fade=True)

    def _do(self, ctx, fade: bool):
        cc = ctx.source.ctx
        plugin, view = cc.plugin, cc.view
        if plugin is None or view is None:
            ctx.source.send_message("§e활성 보드가 없습니다")
            return 0
        target = (ctx.get_argument("target") or "").strip()
        loc = ctx.get_argument("location")

        # 대상 판별 먼저 → ~ 상대좌표는 '대상의 현재 위치' 기준(마크 /tp 식).
        low = target.lower()
        is_self_cam = low in ("@s", "me", "카메라", "self") or (cc.my_name and low == cc.my_name.lower())
        if is_self_cam:
            c = view.mapToScene(view.viewport().rect().center())
            x, y = _resolve_vec2(loc, (c.x(), c.y()))
            return self._move_camera(ctx, view, cc, x, y, fade)

        nid = _node_id_by_name(plugin, target)
        if nid is not None:
            item = _movable_item(plugin, nid)
            base = (item.pos().x(), item.pos().y()) if item is not None else cc.cursor_scene_xy()
            x, y = _resolve_vec2(loc, base)
            return self._move_node(ctx, cc, plugin, nid, target, x, y, fade)

        # 접속 중인 다른 사용자?
        for u in (cc.last_presence or []):
            if u.get("user", "").lower() == low:
                ctx.source.send_message(
                    "§e다른 사용자의 카메라 이동은 아직 지원하지 않습니다 §7(서버 권위 필요)")
                return 0

        ctx.source.send_message(f"§e'{target}' 노드/사용자를 찾을 수 없습니다")
        return 0

    def _move_camera(self, ctx, view, cc, x, y, fade):
        def _apply(cx, cy):
            view.centerOn(cx, cy)

        def _done():
            if hasattr(view, "_notify_viewport_changed"):
                try:
                    view._notify_viewport_changed()
                except Exception:
                    pass

        if fade:
            c = view.mapToScene(view.viewport().rect().center())
            _animate(cc, (c.x(), c.y()), (x, y), _apply, _done)
            ctx.source.send_message(f"§a카메라 이동(fade) → §7({int(x)}, {int(y)})")
        else:
            _apply(x, y)
            _done()
            ctx.source.send_message(f"§a카메라 이동 → §7({int(x)}, {int(y)})")
        return 1

    def _move_node(self, ctx, cc, plugin, nid, name, x, y, fade):
        item = _movable_item(plugin, nid)
        if item is None:
            ctx.source.send_message(f"§e'{name}' 노드를 옮길 수 없습니다")
            return 0
        if fade:
            cur = item.pos()

            def _apply(nx, ny):
                from PyQt6.QtCore import QPointF
                item.setPos(QPointF(nx, ny))

            def _done():
                _place_node(plugin, nid, x, y)

            _animate(cc, (cur.x(), cur.y()), (x, y), _apply, _done)
            ctx.source.send_message(f"§a'{name}' 이동(fade) → §7({int(x)}, {int(y)})")
        else:
            _place_node(plugin, nid, x, y)
            ctx.source.send_message(f"§a'{name}' 이동 → §7({int(x)}, {int(y)})")
        return 1


class DeleteCommand(Command):
    name = "delete"
    aliases = ("remove", "del")
    description = "노드를 삭제한다 (이름으로 지정, 연결된 엣지도 제거)"
    permission = staticmethod(_require_level(MEMBER))

    def build(self, root):
        root.then(argument("target", node_name_arg()).executes(self.run))
        return root

    def run(self, ctx):
        cc = ctx.source.ctx
        plugin = cc.plugin
        if plugin is None:
            ctx.source.send_message("§e활성 보드가 없습니다")
            return 0
        target = (ctx.get_argument("target") or "").strip()
        nid = _node_id_by_name(plugin, target)
        if nid is None:
            ctx.source.send_message(f"§e'{target}' 노드를 찾을 수 없습니다 §7(/grep 으로 검색)")
            return 0
        dups = _nodes_by_name(plugin, target)
        if len(dups) > 1:
            ids = ", ".join("#" + str(i) for i in dups)
            ctx.source.send_message(
                f"§e'{target}' 이름이 여러 개({ids}) — §b#{nid}§e 를 삭제합니다 §7(#id 로 지정 가능)")
        node = plugin.app.nodes.get(nid)
        if getattr(node, "_running", False):
            ctx.source.send_message(f"§e'{target}' 는 작업 중이라 삭제하지 않았습니다")
            return 0
        try:
            ok = _delete_node_by_id(plugin, nid)
        except Exception as e:
            logger.debug("delete failed: %s", e)
            ctx.source.send_message(f"§e삭제 실패: {e}")
            return 0
        if not ok:
            ctx.source.send_message(f"§e'{target}' 는 삭제할 수 없는 종류입니다")
            return 0
        ctx.source.send_message(f"§a'{target}' 노드를 삭제했습니다")
        return 1


class ConnectCommand(Command):
    name = "connect"
    aliases = ("link", "wire")
    description = "두 노드를 엣지로 연결 (생략 시 첫 데이터 포트, [출력] [입력] 으로 포트 지정)"
    permission = staticmethod(_require_level(MEMBER))

    def build(self, root):
        src = argument("from", node_name_arg())
        to = argument("to", node_name_arg())
        to.executes(self.run)
        outp = argument("out", PortNameArgumentType("from", "OUTPUT"))
        outp.executes(self.run)
        inp = argument("in", PortNameArgumentType("to", "INPUT"))
        inp.executes(self.run)
        outp.then(inp)
        to.then(outp)
        src.then(to)
        root.then(src)
        return root

    def run(self, ctx):
        from .items import PortItem
        cc = ctx.source.ctx
        plugin = cc.plugin
        if plugin is None:
            ctx.source.send_message("§e활성 보드가 없습니다")
            return 0
        a = (ctx.get_argument("from") or "").strip()
        b = (ctx.get_argument("to") or "").strip()
        na = _node_id_by_name(plugin, a)
        nb = _node_id_by_name(plugin, b)
        if na is None:
            ctx.source.send_message(f"§e'{a}' 노드를 찾을 수 없습니다")
            return 0
        if nb is None:
            ctx.source.send_message(f"§e'{b}' 노드를 찾을 수 없습니다")
            return 0
        if na == nb:
            ctx.source.send_message("§e같은 노드끼리는 연결할 수 없습니다")
            return 0

        def _opt(arg):
            try:
                v = ctx.get_argument(arg)
                return v.strip() if v else None
            except Exception:
                return None
        out_name, in_name = _opt("out"), _opt("in")
        src_item, tgt_item = _movable_item(plugin, na), _movable_item(plugin, nb)

        if out_name:
            out_port = _named_port(plugin, src_item, PortItem.OUTPUT, out_name)
            if out_port is None:
                names = ", ".join(_port_names(plugin, src_item, PortItem.OUTPUT)) or "(없음)"
                ctx.source.send_message(f"§e'{a}' 출력 포트 '{out_name}' 없음 §7(가능: {names})")
                return 0
        else:
            out_port = _pick_port(plugin, src_item, PortItem.OUTPUT)
        if out_port is None:
            ctx.source.send_message(f"§e'{a}' 에 출력 포트가 없습니다")
            return 0
        if in_name:
            in_port = _named_port(plugin, tgt_item, PortItem.INPUT, in_name)
            if in_port is None:
                names = ", ".join(_port_names(plugin, tgt_item, PortItem.INPUT)) or "(없음)"
                ctx.source.send_message(f"§e'{b}' 입력 포트 '{in_name}' 없음 §7(가능: {names})")
                return 0
        else:
            # 기본 입력은 출력의 데이터타입과 같은 포트를 우선(예: 신호 출력 → 신호 입력)
            in_port = _pick_port(plugin, tgt_item, PortItem.INPUT,
                                 prefer_data_type=getattr(out_port, "port_data_type", None))
        if in_port is None:
            ctx.source.send_message(f"§e'{b}' 에 입력 포트가 없습니다")
            return 0
        # 단일 입력 포트가 이미 연결돼 있으면 create_edge 가 조용히 교체 → 미리 알림
        replaced = bool(getattr(in_port, "edges", None)) and not getattr(in_port, "multi_connect", False)
        edge = plugin.create_edge(out_port, in_port)
        if edge is None:
            ctx.source.send_message("§e연결할 수 없습니다 §7(이미 연결됐거나 같은 종류 포트)")
            return 0
        op_label = getattr(out_port, "port_name", "") or "출력"
        ip_label = getattr(in_port, "port_name", "") or "입력"
        ctx.source.send_message(f"§a'{a}'§7:{op_label} §7→ §a'{b}'§7:{ip_label} §7연결")
        # 데이터 타입 불일치 경고(끊지 않고 알림만 — 시그널↔문자 등은 의도일 수 있음)
        ot, it = getattr(out_port, "port_data_type", None), getattr(in_port, "port_data_type", None)
        if ot is not None and it is not None and ot != it:
            ctx.source.send_message("§7  ⚠ 포트 데이터 타입이 달라요 (의도면 무시)")
        if replaced:
            ctx.source.send_message("§7  ⚠ 기존 연결을 교체했습니다")
        return 1


class RunCommand(Command):
    name = "run"
    aliases = ("exec", "fire")
    description = "노드를 실행/트리거한다 (챗=AI호출·버튼=신호·all=모든 챗)"
    permission = staticmethod(_require_level(MEMBER))

    def build(self, root):
        root.then(argument("target", node_name_arg()).executes(self.run))
        return root

    def run(self, ctx):
        cc = ctx.source.ctx
        plugin = cc.plugin
        if plugin is None:
            ctx.source.send_message("§e활성 보드가 없습니다")
            return 0
        target = (ctx.get_argument("target") or "").strip()
        if target.lower() == "all":
            return self._run_all_chats(ctx, plugin)
        nid = _node_id_by_name(plugin, target)
        if nid is None:
            ctx.source.send_message(f"§e'{target}' 노드를 찾을 수 없습니다")
            return 0
        node = plugin.app.nodes.get(nid)
        if getattr(node, "_running", False):
            ctx.source.send_message(f"§e'{target}' 는 이미 작업 중입니다")
            return 0
        try:
            ok, why = _run_node(node)
        except Exception as e:
            logger.debug("run failed: %s", e)
            ctx.source.send_message(f"§e실행 실패: {e}")
            return 0
        if not ok:
            ctx.source.send_message(f"§e'{target}' §7— {why}")
            return 0
        ctx.source.send_message(f"§a'{target}' §7{why}")
        return 1

    def _run_all_chats(self, ctx, plugin):
        count = 0
        for nid in list(plugin.proxies.keys()):
            node = plugin.app.nodes.get(nid)
            if node is None or getattr(node, "_running", False):
                continue
            if type(node).__name__ == "ChatNodeWidget":
                try:
                    node.on_signal_input()
                    count += 1
                except Exception:
                    pass
        if count:
            ctx.source.send_message(f"§a챗 노드 {count}개를 실행했습니다")
        else:
            ctx.source.send_message("§7실행할 챗 노드가 없습니다")
        return 1


class GrepCommand(Command):
    name = "grep"
    aliases = ("search", "find", "q")
    description = "노드 내용을 검색한다 (이름·본문·대화, 대소문자 무시)"

    def build(self, root):
        root.then(argument("pattern", greedy_string()).executes(self.run))
        return root

    _LIMIT = 12

    def run(self, ctx):
        cc = ctx.source.ctx
        plugin = cc.plugin
        if plugin is None:
            ctx.source.send_message("§e활성 보드가 없습니다")
            return 0
        pat = (ctx.get_argument("pattern") or "").strip()
        if not pat:
            ctx.source.send_message("§e검색어를 입력하세요 §7(예: §b/grep TODO§7)")
            return 0
        low = pat.lower()
        hits = []
        for nid in list(getattr(plugin.app, "nodes", {}).keys()):
            node = plugin.app.nodes.get(nid)
            if node is None:
                continue
            text = _node_search_text(plugin, nid, node)
            if low in text.lower():
                name = ""
                try:
                    name = plugin.get_node_name(nid)
                except Exception:
                    name = ""
                hits.append((nid, name, _node_type_label(plugin, nid, node),
                             self._snippet(text, low)))
        if not hits:
            ctx.source.send_message(f"§7'{pat}' 에 맞는 노드가 없습니다")
            return 0
        ctx.source.send_message(f"§b◆ '{pat}' §7— {len(hits)}개")
        for nid, name, typ, snip in hits[:self._LIMIT]:
            label = name or typ
            extra = f"  §8{snip}" if snip else ""
            ctx.source.send_message(f"  §b{label} §7#{nid} §8({typ}){extra}")
        if len(hits) > self._LIMIT:
            ctx.source.send_message(f"  §7… 외 {len(hits) - self._LIMIT}개")
        return 1

    @staticmethod
    def _snippet(text: str, low: str, span: int = 24) -> str:
        flat = " ".join(text.split())
        i = flat.lower().find(low)
        if i < 0:
            return ""
        start = max(0, i - span // 2)
        end = min(len(flat), i + len(low) + span // 2)
        s = flat[start:end]
        if start > 0:
            s = "…" + s
        if end < len(flat):
            s = s + "…"
        return s


class SayCommand(Command):
    name = "say"
    aliases = ("echo",)
    description = "채팅창에 메시지를 보낸다 (함수 출력용)"

    def build(self, root):
        root.then(argument("text", greedy_string()).executes(self.run))
        return root

    def run(self, ctx):
        cc = ctx.source.ctx
        text = (ctx.get_argument("text") or "").strip()
        if not text:
            return 0
        if cc.chat_sink is not None:
            try:
                cc.chat_sink(text)
            except Exception:
                ctx.source.send_message(text)
        else:
            ctx.source.send_message(text)
        return 1


# /function 폭주 방지 한도
_FN_MAX_DEPTH = 16        # 중첩 호출 깊이
_FN_MAX_STEPS = 2048      # top-level 호출당 누적 명령 수


class ReturnCommand(Command):
    name = "return"
    aliases = ("ret",)
    description = "함수 실행을 즉시 멈추고 값을 돌려준다 (함수 안에서만)"

    def build(self, root):
        root.executes(self.run_bare)
        root.then(argument("value", integer()).executes(self.run_value))
        root.then(literal("run").then(
            argument("command", greedy_string()).executes(self.run_run)))
        return root

    def _guard(self, ctx) -> bool:
        if ctx.source.ctx._fn_depth <= 0:
            ctx.source.send_message("§e/return 은 함수 안에서만 쓸 수 있습니다")
            return False
        return True

    def run_bare(self, ctx):
        if not self._guard(ctx):
            return 0
        cc = ctx.source.ctx
        cc._return_pending = True
        cc._return_value = 0
        return 0

    def run_value(self, ctx):
        if not self._guard(ctx):
            return 0
        cc = ctx.source.ctx
        val = int(ctx.get_argument("value"))
        cc._return_pending = True
        cc._return_value = val
        return val

    def run_run(self, ctx):
        if not self._guard(ctx):
            return 0
        cc = ctx.source.ctx
        sub = (ctx.get_argument("command") or "").strip()
        val = 0
        if sub and cc.run_command is not None:
            try:
                res = cc.run_command(sub)
                for m in getattr(res, "messages", []) or []:
                    ctx.source.send_message(m)
                val = int(getattr(res, "value", 0) or 0)
            except Exception as e:
                ctx.source.send_message(f"§ereturn run 실패: {e}")
        # return run 직후 또 return 이 걸렸다면(중첩) 그대로 둔다.
        if not cc._return_pending:
            cc._return_pending = True
            cc._return_value = val
        return cc._return_value


class FunctionCommand(Command):
    name = "function"
    aliases = ("fn",)
    description = "명령들을 묶은 함수를 실행/정의한다 (마크식, 매크로 {args} + return)"

    def build(self, root):
        root.then(literal("list").executes(self.run_list))
        root.then(literal("show").then(
            argument("name", function_name_arg()).executes(self.run_show)))
        edit = literal("edit")
        edit.executes(self.run_edit)
        edit.then(argument("name", word()).executes(self.run_edit))
        root.then(edit)
        root.then(literal("remove").then(
            argument("name", function_name_arg()).executes(self.run_remove)))
        set_ = literal("set").then(argument("name", function_name_arg()).then(
            argument("body", greedy_string()).executes(self.run_set)))
        root.then(set_)
        add_ = literal("add").then(argument("name", function_name_arg()).then(
            argument("line", greedy_string()).executes(self.run_add)))
        root.then(add_)
        # 호출: /function <name> [ {args} ]
        call = argument("name", function_name_arg())
        call.executes(self.run_call)
        call.then(argument("args", greedy_string()).executes(self.run_call))
        root.then(call)
        return root

    # ── 정의/조회 (set/add 는 Member) ──
    def run_set(self, ctx):
        if not _require_level(MEMBER)(ctx.source):
            ctx.source.send_message("§e권한이 없습니다 (Member 이상)")
            return 0
        from .cmd_functions import set_function, split_commands
        name = (ctx.get_argument("name") or "").strip()
        body = ctx.get_argument("body") or ""
        if not name:
            ctx.source.send_message("§e함수 이름이 비었습니다")
            return 0
        if name in self._reserved():
            ctx.source.send_message(f"§e'{name}' 은 예약어라 함수명으로 못 씁니다")
            return 0
        try:
            set_function(name, body)
        except ValueError as e:
            ctx.source.send_message(f"§e저장 거부: {e}")
            return 0
        n = len(split_commands(body))
        ctx.source.send_message(f"§a함수 §b{name}§a 저장 §7({n}줄)")
        return 1

    def run_add(self, ctx):
        if not _require_level(MEMBER)(ctx.source):
            ctx.source.send_message("§e권한이 없습니다 (Member 이상)")
            return 0
        from .cmd_functions import append_line, split_commands
        name = (ctx.get_argument("name") or "").strip()
        line = (ctx.get_argument("line") or "").strip()
        if not name:
            ctx.source.send_message("§e함수 이름이 비었습니다")
            return 0
        if name in self._reserved():
            ctx.source.send_message(f"§e'{name}' 은 예약어입니다")
            return 0
        try:
            body = append_line(name, line)
        except ValueError as e:
            ctx.source.send_message(f"§e추가 거부: {e}")
            return 0
        ctx.source.send_message(f"§a함수 §b{name}§a 에 1줄 추가 §7(총 {len(split_commands(body))}줄)")
        return 1

    def run_remove(self, ctx):
        if not _require_level(MEMBER)(ctx.source):
            ctx.source.send_message("§e권한이 없습니다 (Member 이상)")
            return 0
        from .cmd_functions import delete_function
        name = (ctx.get_argument("name") or "").strip()
        if delete_function(name):
            ctx.source.send_message(f"§a함수 §b{name}§a 삭제")
            return 1
        ctx.source.send_message(f"§e함수 '{name}' 가 없습니다")
        return 0

    def run_list(self, ctx):
        from .cmd_functions import list_functions
        names = list_functions()
        if not names:
            ctx.source.send_message("§7저장된 함수가 없습니다 §7(/function set <이름> <명령;명령>)")
            return 0
        ctx.source.send_message(f"§b◆ 함수 §7— {len(names)}개")
        for nm in names:
            ctx.source.send_message(f"  §b{nm}")
        return 1

    def run_show(self, ctx):
        from .cmd_functions import get_function, split_commands, macro_keys
        name = (ctx.get_argument("name") or "").strip()
        body = get_function(name)
        if body is None:
            ctx.source.send_message(f"§e함수 '{name}' 가 없습니다")
            return 0
        ctx.source.send_message(f"§b{name}§7:")
        for ln in split_commands(body):
            ctx.source.send_message("  §b" + ln)
        keys = macro_keys(body)
        if keys:
            ctx.source.send_message("  §7매크로 변수: §b" + ", ".join(keys))
        return 1

    def run_edit(self, ctx):
        if not _require_level(MEMBER)(ctx.source):
            ctx.source.send_message("§e권한이 없습니다 (Member 이상)")
            return 0
        cc = ctx.source.ctx
        try:
            name = ctx.get_argument("name") or ""
        except Exception:
            name = ""
        controller = getattr(cc, "controller", None)
        if controller is None:
            ctx.source.send_message("§e에디터를 열 수 없습니다")
            return 0
        try:
            from .function_editor_web import open_function_editor, webengine_available
            if not webengine_available():
                ctx.source.send_message(
                    "§e함수 에디터는 PyQt6-WebEngine 이 필요합니다 §7(명령으로는 /function set 사용)")
                return 0
            dlg = open_function_editor(cc.ui, controller, (name or "").strip())
            if dlg is None:
                ctx.source.send_message("§e함수 에디터를 열지 못했습니다")
                return 0
            if hasattr(controller, "_editors"):
                controller._editors.append(dlg)
                dlg.destroyed.connect(
                    lambda *_a, d=dlg: controller._editors.remove(d)
                    if d in controller._editors else None)
            ctx.source.send_message("§a함수 에디터를 열었습니다")
            return 1
        except Exception as e:
            logger.debug("open editor failed: %s", e)
            ctx.source.send_message(f"§e에디터 오류: {e}")
            return 0

    # ── 실행 ──
    def run_call(self, ctx):
        from .cmd_functions import get_function
        cc = ctx.source.ctx
        name = (ctx.get_argument("name") or "").strip()
        body = get_function(name)
        if body is None:
            ctx.source.send_message(f"§e함수 '{name}' 가 없습니다 §7(/function list)")
            return 0
        args = {}
        raw_args = None
        try:
            raw_args = ctx.get_argument("args")
        except Exception:
            raw_args = None
        if raw_args:
            from .cmd_params import parse_tag, TagSyntaxError
            try:
                args = parse_tag(raw_args)
            except TagSyntaxError as e:
                ctx.source.send_message(f"§e인자 오류: {e}")
                return 0
        return self._invoke(ctx, cc, name, body, args)

    def _invoke(self, ctx, cc, name, body, args):
        from .cmd_functions import split_commands, is_macro_line, apply_macro, MACRO_PREFIX
        if cc._fn_depth >= _FN_MAX_DEPTH:
            ctx.source.send_message(f"§e함수 중첩이 너무 깊습니다 (>{_FN_MAX_DEPTH}) — 재귀 의심")
            return 0
        if cc.run_command is None:
            ctx.source.send_message("§e함수 실행 경로가 없습니다")
            return 0
        cc._fn_depth += 1
        result = 0
        try:
            for raw in split_commands(body):
                cc._fn_steps += 1
                if cc._fn_steps > _FN_MAX_STEPS:
                    ctx.source.send_message(f"§e명령 한도 초과 (>{_FN_MAX_STEPS}) — 중단")
                    break
                line = raw
                if is_macro_line(line):
                    try:
                        line = apply_macro(line.lstrip()[len(MACRO_PREFIX):].lstrip(), args)
                    except ValueError as e:
                        ctx.source.send_message(f"§e{name}: {e}")
                        break
                line = line.strip()
                if not line:
                    continue
                res = cc.run_command(line)
                for m in getattr(res, "messages", []) or []:
                    ctx.source.send_message(m)
                if getattr(res, "failed", False):
                    ctx.source.send_message(f"§e{name}: '{line}' 실패")
                result = int(getattr(res, "value", 0) or 0)
                if cc._return_pending:
                    result = cc._return_value
                    cc._return_pending = False
                    break
        finally:
            cc._fn_depth -= 1
            if cc._fn_depth == 0:
                cc._fn_steps = 0
        return result

    @staticmethod
    def _reserved() -> set:
        return {"list", "show", "set", "add", "remove", "edit"}


def _read_name_token(reader, empty_msg):
    """따옴표 문자열 또는 공백 전까지 — 이름/키(공백 포함 가능)를 읽는다."""
    from mccmd.errors import CommandSyntaxError
    from mccmd.reader import _is_quoted_string_start
    if not reader.can_read():
        raise CommandSyntaxError(empty_msg, reader)
    if _is_quoted_string_start(reader.peek()):
        return reader.read_string()
    start = reader.cursor
    while reader.can_read() and reader.peek() != " ":
        reader.skip()
    raw = reader.string[start:reader.cursor]
    if not raw:
        raise CommandSyntaxError(empty_msg, reader)
    return raw


class DataKeyArgumentType(ArgumentType):
    """데이터 키 하나(따옴표로 공백 포함 가능). 자동완성은 저장된 키 목록."""

    def parse(self, reader, source=None):
        return _read_name_token(reader, "데이터 키를 입력하세요")

    def list_suggestions(self, context, builder):
        typed = builder.remaining_lower
        try:
            from .cmd_data import data_keys
            for k in data_keys():
                if k.lower().startswith(typed):
                    builder.suggest(k)
        except Exception:
            pass
        return builder.build()

    def get_examples(self):
        return ["hp", "pos"]


def data_key_arg():
    return DataKeyArgumentType()


def _node_numeric(plugin, name: str):
    """노드 이름 → 수치 값(number/math 값·출력포트). 없으면 None."""
    nid = _node_id_by_name(plugin, name)
    if nid is None:
        return None
    node = plugin.app.nodes.get(nid)
    for attr in ("_value", "_result"):
        v = getattr(node, attr, None)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    op = getattr(node, "output_port", None)
    pv = getattr(op, "port_value", None) if op is not None else None
    if isinstance(pv, (int, float)) and not isinstance(pv, bool):
        return float(pv)
    return None


class DataCommand(Command):
    name = "data"
    aliases = ("db",)
    description = "자체 데이터 저장소 — set/get/merge/remove/list (값=SNBT)"

    def build(self, root):
        root.then(literal("set").then(argument("key", data_key_arg()).then(
            argument("value", greedy_string()).executes(self.run_set))))
        get_key = argument("key", data_key_arg())
        get_key.executes(self.run_get)
        get_key.then(argument("path", word()).executes(self.run_get))
        root.then(literal("get").then(get_key))
        root.then(literal("merge").then(argument("key", data_key_arg()).then(
            argument("compound", greedy_string()).executes(self.run_merge))))
        root.then(literal("remove").then(
            argument("key", data_key_arg()).executes(self.run_remove)))
        root.then(literal("list").executes(self.run_list))
        return root

    def run_set(self, ctx):
        if not _require_level(MEMBER)(ctx.source):
            ctx.source.send_message("§e권한이 없습니다 (Member 이상)")
            return 0
        from .cmd_data import data_set
        from .cmd_params import parse_value, TagSyntaxError
        key = (ctx.get_argument("key") or "").strip()
        if not key:
            ctx.source.send_message("§e데이터 키가 비었습니다")
            return 0
        if "." in key:
            ctx.source.send_message("§e데이터 키에 '.' 는 못 씁니다 §7('.'는 get 경로 구분자)")
            return 0
        raw = ctx.get_argument("value") or ""
        try:
            val = parse_value(raw)
        except TagSyntaxError as e:
            ctx.source.send_message(f"§e값 오류: {e}")
            return 0
        try:
            data_set(key, val)
        except ValueError as e:
            ctx.source.send_message(f"§e저장 거부: {e}")
            return 0
        ctx.source.send_message(f"§a데이터 §b{key}§a = §b{val}")
        return 1

    def run_get(self, ctx):
        from .cmd_data import data_get
        key = (ctx.get_argument("key") or "").strip()
        try:
            path = ctx.get_argument("path")
        except Exception:
            path = None
        found, val = data_get(key, path)
        if not found:
            where = f"{key}.{path}" if path else key
            ctx.source.send_message(f"§e데이터 '{where}' 가 없습니다")
            return 0
        ctx.source.send_message(f"§b{key}{('.' + path) if path else ''}§7 = §b{val}")
        from .cmd_execute import to_number
        n = to_number(val)
        return int(n) if n is not None else 1

    def run_merge(self, ctx):
        if not _require_level(MEMBER)(ctx.source):
            ctx.source.send_message("§e권한이 없습니다 (Member 이상)")
            return 0
        from .cmd_data import data_merge
        from .cmd_params import parse_tag, TagSyntaxError
        key = (ctx.get_argument("key") or "").strip()
        try:
            comp = parse_tag(ctx.get_argument("compound") or "")
        except TagSyntaxError as e:
            ctx.source.send_message(f"§e컴파운드 오류: {e}")
            return 0
        data_merge(key, comp)
        ctx.source.send_message(f"§a데이터 §b{key}§a 병합")
        return 1

    def run_remove(self, ctx):
        if not _require_level(MEMBER)(ctx.source):
            ctx.source.send_message("§e권한이 없습니다 (Member 이상)")
            return 0
        from .cmd_data import data_remove
        key = (ctx.get_argument("key") or "").strip()
        if data_remove(key):
            ctx.source.send_message(f"§a데이터 §b{key}§a 삭제")
            return 1
        ctx.source.send_message(f"§e데이터 '{key}' 가 없습니다")
        return 0

    def run_list(self, ctx):
        from .cmd_data import data_keys, data_get
        keys = data_keys()
        if not keys:
            ctx.source.send_message("§7저장된 데이터가 없습니다 §7(/data set <키> <값>)")
            return 0
        ctx.source.send_message(f"§b◆ 데이터 §7— {len(keys)}개")
        for k in keys:
            _, v = data_get(k)
            ctx.source.send_message(f"  §b{k}§7 = §8{v}")
        return 1


class ExecuteCommand(Command):
    name = "execute"
    aliases = ("ex",)
    description = "조건부/데이터 실행 — if|unless <조건> run <명령>, store result data <키> run …"
    permission = staticmethod(_require_level(MEMBER))

    def build(self, root):
        root.then(argument("clause", greedy_string()).executes(self.run))
        return root

    def run(self, ctx):
        from .cmd_execute import tokenize_pos
        cc = ctx.source.ctx
        if cc.plugin is None:
            ctx.source.send_message("§e활성 보드가 없습니다")
            return 0
        clause = (ctx.get_argument("clause") or "").strip()
        if not clause:
            ctx.source.send_message("§e사용법: §bexecute if <조건> run <명령>")
            return 0
        toks = tokenize_pos(clause)
        vals = [t[0] for t in toks]
        i = 0
        passed = True
        store_key = None
        cmd_text = None
        try:
            while i < len(vals):
                t = vals[i]
                if t in ("if", "unless"):
                    ok, consumed = self._eval_condition(cc, vals, i + 1)
                    i += 1 + consumed
                    cond = (not ok) if t == "unless" else ok
                    passed = passed and cond
                elif t == "store":
                    if vals[i + 1:i + 3] != ["result", "data"] or i + 3 >= len(vals):
                        ctx.source.send_message("§e사용법: §bexecute store result data <키> run <명령>")
                        return 0
                    store_key = vals[i + 3]
                    i += 4
                elif t == "run":
                    cmd_text = clause[toks[i][2]:].strip()   # run 토큰 이후 원문
                    break
                else:
                    ctx.source.send_message(f"§e'{t}' — if/unless/store/run 중 하나여야 합니다")
                    return 0
        except (IndexError, ValueError) as e:
            ctx.source.send_message(f"§e조건 해석 실패: {e}")
            return 0

        if cmd_text is None:                      # run 없음
            if store_key is not None:
                ctx.source.send_message("§estore 는 run 과 함께 써야 합니다 §7(store result data <키> run <명령>)")
                return 0
            ctx.source.send_message("§a조건 충족 ✓" if passed else "§7조건 불충족")
            return 1 if passed else 0
        if not passed:
            ctx.source.send_message("§7조건 불충족 — 실행 안 함")
            return 0
        if not cmd_text or cc.run_command is None:
            ctx.source.send_message("§e실행할 명령이 없습니다")
            return 0
        res = cc.run_command(cmd_text)
        for m in getattr(res, "messages", []) or []:
            ctx.source.send_message(m)
        if getattr(res, "failed", False):
            ctx.source.send_message(f"§e실행 실패: {cmd_text}")
        val = int(getattr(res, "value", 0) or 0)
        if store_key is not None:
            from .cmd_data import data_set
            try:
                data_set(store_key, val)
                ctx.source.send_message(f"§7  → data §b{store_key}§7 = §b{val}")
            except ValueError as e:
                ctx.source.send_message(f"§e store 거부: {e}")
        return val

    # ── 조건 평가 ──
    def _eval_condition(self, cc, vals, i):
        """(통과?, 소비 토큰수). kind 부터 카운트. 형식 오류는 ValueError."""
        from .cmd_data import data_get
        if i >= len(vals):
            raise ValueError("조건이 필요합니다")
        kind = vals[i]
        if kind in ("data", "value"):
            if i + 1 >= len(vals):
                raise ValueError(f"{kind} 뒤에 대상이 필요합니다")
            name = vals[i + 1]
            if kind == "data":
                found, value = data_get(name)
            else:
                value = _node_numeric(cc.plugin, name)
                found = value is not None
            ok, tail = self._match_tail(value, found, vals, i + 2)
            return ok, 2 + tail
        if kind == "node":
            if i + 1 >= len(vals):
                raise ValueError("node 뒤에 이름이 필요합니다")
            exists = _node_id_by_name(cc.plugin, vals[i + 1]) is not None
            return exists, 2
        if kind == "online":
            if i + 1 >= len(vals):
                raise ValueError("online 뒤에 유저가 필요합니다")
            user = vals[i + 1].lower()
            on = any((u.get("user", "") or "").lower() == user
                     for u in (cc.last_presence or []))
            return on, 2
        if kind == "cursor":
            if i + 4 >= len(vals):
                raise ValueError("cursor <유저> <x|y> <op> <수> 형식이 필요합니다")
            user, axis, op, num = vals[i + 1], vals[i + 2], vals[i + 3], vals[i + 4]
            coord = self._cursor_coord(cc, user, axis)
            if coord is None:
                return False, 5
            from .cmd_execute import compare
            return compare(coord, op, num), 5
        raise ValueError(f"알 수 없는 조건 '{kind}' (data/value/node/online/cursor)")

    @staticmethod
    def _match_tail(value, found, vals, j):
        """data/value 의 꼬리: 'matches <범위>' | '<op> <수>' | (없음=존재). (통과?, 소비)."""
        from .cmd_execute import parse_range, match_range, compare, is_cmp_op
        if j < len(vals) and vals[j] == "matches":
            if j + 1 >= len(vals):
                raise ValueError("matches 뒤에 범위가 필요합니다 (예: 10..)")
            rng = parse_range(vals[j + 1])
            return (found and match_range(value, rng)), 2
        if j + 1 < len(vals) and is_cmp_op(vals[j]):
            return (found and compare(value, vals[j], vals[j + 1])), 2
        return found, 0

    @staticmethod
    def _cursor_coord(cc, user, axis):
        if axis not in ("x", "y"):
            raise ValueError("커서 축은 x 또는 y 여야 합니다")
        ul = user.lower()
        for u in (cc.last_presence or []):
            if (u.get("user", "") or "").lower() == ul:
                cur = u.get("cursor")
                if isinstance(cur, dict):
                    return cur.get(axis)
        return None


# 등록되는 명령어 전체 (순서 = /help 목록 순서)
CHAT_COMMANDS = [
    HelpCommand, ClearCommand, CreateCommand, MoveCommand,
    DeleteCommand, ConnectCommand, RunCommand, GrepCommand,
    FunctionCommand, ReturnCommand, SayCommand,
    DataCommand, ExecuteCommand,
]


def humanize_command_error(cmd: str, error, known: set) -> str:
    """mccmd 영문 문법 오류를 친절한 한글 안내로. 우리 인자타입의 한글 오류는 그대로 살린다.

    뷰(_friendly_cmd_error)와 함수 실행 로그가 공유한다.
    """
    parts = (cmd or "").split()
    name = parts[0].lstrip("/") if parts else ""
    msg = str(error or "").strip()
    low = msg.lower()
    has_kr = any("가" <= ch <= "힣" for ch in msg)

    if name and name not in known:
        return f"§e'/{name}' 는 모르는 명령이에요.§r  §7/help 로 목록을 보세요"
    if has_kr:                                  # 우리 인자타입이 낸 한글 오류
        base = msg
    elif "unknown command" in low:
        return f"§e'/{name}' 는 모르는 명령이에요.§r  §7/help 로 목록을 보세요"
    elif "trailing" in low or "whitespace" in low:
        base = "인자가 너무 많거나 형식이 안 맞아요"
    elif "incorrect argument" in low:
        base = "인자가 맞지 않아요"
    elif "expected integer" in low or "invalid integer" in low:
        base = "정수가 필요해요"
    elif "expected float" in low:
        base = "숫자가 필요해요"
    elif "expected bool" in low:
        base = "true/false 가 필요해요"
    elif "quote" in low:
        base = "따옴표가 안 맞아요"
    else:
        base = msg or "형식이 안 맞아요"
    if name and name in known:
        return f"§e{base}.§r  §7/help {name} 로 사용법 확인"
    return f"§e{base}"


# ---------------------------------------------------------------------------
# 컨트롤러 — 패널/뷰가 들고 쓰는 진입점
# ---------------------------------------------------------------------------
class ChatCommandController:
    """채팅 입력이 사용하는 명령 엔진 래퍼.

    - execute(text) / suggest(text, cursor) / ghost(text, cursor) / all_usage()
    - text 는 선행 '/' 가 **제거된** 명령 문자열.
    """

    def __init__(self, ui, panel=None):
        self.ctx = CommandContext(ui)
        self.panel = panel
        registry = CommandRegistry()
        for cmd in CHAT_COMMANDS:
            registry.add(cmd())
        self.dispatcher = registry.build()
        self.service = CommandService(self.dispatcher, self._make_source)
        # /function 이 함수 본문의 각 줄을 실행할 때 쓰는 경로(같은 ctx 공유 → return 상태 유지).
        self.ctx.run_command = self.execute
        self.ctx.controller = self    # /function edit 가 에디터에 넘길 컨트롤러 핸들
        self._editors = []            # 열린 함수 에디터 다이얼로그(GC 방지)

    # --- 소스 팩토리 (실행/제안마다 새 소스) ---
    def _make_source(self) -> ChatSource:
        src = ChatSource(self.ctx)
        src._panel = self.panel          # ClearCommand / 로컬에코용
        src._all_usage = self.all_usage  # HelpCommand 용
        src._usage_for = self.usage_for
        return src

    # --- 외부 연동 ---
    def set_client(self, client):
        self.ctx.client = client

    def set_presence(self, users: list):
        self.ctx.last_presence = users or []

    def set_chat_sink(self, cb):
        """평문 채팅을 처리할 콜백. 뷰=말풍선, 패널=로그."""
        self.ctx.chat_sink = cb

    def set_clear_sink(self, cb):
        """/clear 가 호출할 콜백(말풍선/로그 비우기)."""
        self.ctx.clear_sink = cb

    def bind_panel(self, panel):
        self.panel = panel

    # --- 명령 처리 ---
    def execute(self, command_text: str):
        # 어떤 예외도 위로 전파시키지 않는다 — 원시 에러 노출/함수 실행 중단/앱 크래시 방지.
        try:
            return self.service.execute(command_text)
        except Exception as e:
            logger.debug("command execute crashed: %s", e)
            from mccmd.service import ExecutionResult
            return ExecutionResult(False, 0, [], f"명령 실행 오류: {e}")

    def suggest(self, command_text: str, cursor: Optional[int] = None):
        try:
            return self.service.suggest(command_text, cursor)
        except Exception as e:
            logger.debug("suggest failed: %s", e)
            return []

    def ghost(self, command_text: str, cursor: Optional[int] = None) -> str:
        try:
            return self.service.ghost(command_text, cursor) or ""
        except Exception:
            return ""

    def validate(self, command_text: str) -> str:
        """명령 한 줄을 **실행하지 않고** 문법만 검사. 정상이면 "", 아니면 오류 메시지.

        에디터 밑줄/린트용. 매크로/주석/빈 줄은 통과(런타임 치환 대상).
        """
        text = (command_text or "").strip()
        if not text or text.startswith("#") or text.startswith("$"):
            return ""
        try:
            src = self._make_source()
            parse = self.dispatcher.parse(text, src)
        except Exception as e:
            return str(e)
        first = text.split()[0]
        known = self.command_names()
        if parse.reader.can_read():
            if first not in known:
                return f"모르는 명령 '{first}'"
            rem = parse.reader.get_remaining().strip()
            return f"형식 오류 근처 '{rem[:16]}'"
        node = parse.context
        while node is not None:
            if getattr(node, "command", None) is not None:
                return ""   # 끝까지 소비 + 실행 가능한 명령 존재 = 정상
            node = getattr(node, "child", None)
        if first not in known:
            return f"모르는 명령 '{first}'"
        return "인자가 부족합니다"

    def command_names(self) -> set:
        """등록된 모든 명령 이름+별칭 집합(오류 안내에서 '아는 명령인지' 판별용)."""
        out = set()
        for cmd in CHAT_COMMANDS:
            out.add(cmd.name)
            out.update(getattr(cmd, "aliases", ()))
        return out

    def all_usage(self) -> List[str]:
        try:
            src = ChatSource(self.ctx)
            return self.dispatcher.get_all_usage(source=src)
        except Exception:
            return []

    def usage_for(self, name: str) -> List[str]:
        name = (name or "").strip().lower()
        out = []
        for ln in self.all_usage():
            head = ln.split(" ", 1)[0]
            if head == name:
                out.append(ln)
        return out
