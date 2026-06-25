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
from mccmd.arguments import greedy_string, word, vec2
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


class NodeTypeArgumentType(ArgumentType):
    """생성할 노드 종류 하나. CREATE_NODES 키(+별칭) 자동완성/검증."""

    def parse(self, reader, source=None):
        from mccmd.errors import CommandSyntaxError
        start = reader.cursor
        raw = reader.read_unquoted_string()
        if not raw:
            raise CommandSyntaxError("노드 종류를 입력하세요", reader)
        if _resolve_node_key(raw) is None:
            reader.cursor = start
            raise CommandSyntaxError(f"알 수 없는 노드 '{raw}'", reader)
        return _resolve_node_key(raw)

    def list_suggestions(self, context, builder):
        typed = builder.remaining_lower
        for key in CREATE_NODES:
            if key.startswith(typed):
                builder.suggest(key)
        for alias, key in CREATE_ALIASES.items():
            if alias.startswith(typed):
                builder.suggest(alias, tooltip=key)
        return builder.build()

    def get_examples(self):
        return ["chat", "prompt", "image"]


def node_type_arg():
    return NodeTypeArgumentType()


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
    """표시 이름(대소문자 무시)으로 노드 id 찾기. 없으면 None."""
    target = (name or "").strip().lower()
    if not target or plugin is None:
        return None
    for nid in getattr(plugin.app, "nodes", {}):
        try:
            nm = plugin.get_node_name(nid)
        except Exception:
            nm = ""
        if nm and nm.lower() == target:
            return nid
    return None


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
        return 1


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
    description = "노드를 생성한다 (기본 위치 = 커서)"
    permission = staticmethod(_require_level(MEMBER))

    def build(self, root):
        node = argument("node", node_type_arg())
        node.executes(self.run_cursor)
        loc = argument("location", vec2())
        loc.executes(self.run_loc)
        loc.then(argument("params", greedy_string()).executes(self.run_loc))
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

    def _spawn(self, ctx, key: str, xy: Tuple[float, float]):
        cc = ctx.source.ctx
        plugin = cc.plugin
        if plugin is None:
            ctx.source.send_message("§e활성 보드가 없습니다")
            return 0
        factory = CREATE_NODES.get(key)
        if factory is None:
            ctx.source.send_message(f"§e알 수 없는 노드 '{key}'")
            return 0
        from PyQt6.QtCore import QPointF
        try:
            factory(plugin, QPointF(xy[0], xy[1]))
        except Exception as e:
            logger.debug("create node failed: %s", e)
            ctx.source.send_message(f"§e노드 생성 실패: {e}")
            return 0
        ctx.source.send_message(f"§a'{key}' 노드를 생성했습니다 §7({int(xy[0])}, {int(xy[1])})")
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


# 등록되는 명령어 전체 (순서 = /help 목록 순서)
CHAT_COMMANDS = [
    HelpCommand, ClearCommand, CreateCommand, MoveCommand,
]


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
        return self.service.execute(command_text)

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
