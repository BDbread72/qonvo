"""모딩 스타일 명령어 등록 API.

마인크래프트 모드를 짜듯이 명령어를 '선언'한다. 세 가지 방식 지원:

1) Command 클래스 상속 (권장) — 인자 트리/실행/권한을 한 곳에 캡슐화
    class HealCommand(Command):
        name = "heal"
        description = "대상을 회복"
        def build(self, root):
            return root.then(argument("targets", entities()).executes(self.run))
        def run(self, ctx):
            ...
            return 1

2) 데코레이터 — 인자 없는 간단한 명령
    @registry.command("ping")
    def ping(ctx):
        ctx.source.send_message("pong"); return 1

3) 모듈로 묶기 — 여러 명령을 한 모드처럼
    class FunMod(CommandModule):
        id = "fun"
        commands = [HealCommand, PingCommand]

등록 후 registry.build() 하면 CommandDispatcher가 나온다.
"""

from .builder import literal
from .dispatcher import CommandDispatcher


class Command:
    """모드처럼 정의하는 명령어 한 개.

    하위 클래스에서 보통 이것만 손대면 된다:
        name        : 최상위 단어 (필수)
        aliases     : 별칭들
        description : 도움말/툴팁
        permission  : callable(source)->bool, None이면 항상 허용
        build(root) : 인자 트리 구성 (기본은 인자 없는 명령)
        run(ctx)    : 실제 동작. 성공 개수(int) 반환 권장
    """

    name = None
    aliases = ()
    description = ""
    permission = None

    # --- 하위 클래스가 오버라이드 ---------------------------------------
    def build(self, root):
        """root(LiteralArgumentBuilder)에 인자를 붙여 반환. 기본: 인자 없음."""
        return root.executes(self.run)

    def run(self, ctx):
        raise NotImplementedError(f"Command '{self.name}' must implement run()")

    # --- 내부 동작 -------------------------------------------------------
    def can_use(self, source):
        if self.permission is None:
            return True
        try:
            return self.permission(source)
        except Exception:
            return False

    def to_builders(self):
        """등록할 LiteralArgumentBuilder들 (별칭 포함)."""
        if not self.name:
            raise ValueError(f"{type(self).__name__}.name 이 지정되지 않았습니다")
        builders = []
        for nm in (self.name, *self.aliases):
            root = literal(nm)
            if self.permission is not None:
                root.requires(self.can_use)
            builders.append(self.build(root))
        return builders

    def install(self, dispatcher):
        """디스패처에 자신을 등록. 고급 명령(execute 등)은 이걸 오버라이드한다."""
        for b in self.to_builders():
            dispatcher.register(b)


class _FunctionCommand(Command):
    """데코레이터로 만든 함수 기반 명령."""

    def __init__(self, name, fn, aliases=(), description="", build=None, permission=None):
        self.name = name
        self.aliases = tuple(aliases)
        self.description = description
        self.permission = permission
        self._fn = fn
        self._build = build

    def build(self, root):
        if self._build is not None:
            return self._build(root)
        return root.executes(self.run)

    def run(self, ctx):
        return self._fn(ctx)


class CommandModule:
    """여러 Command를 한 모드 단위로 묶는다."""

    id = "module"
    commands = ()

    def get_commands(self):
        result = []
        for c in self.commands:
            result.append(c() if isinstance(c, type) else c)
        return result

    def register(self, registry):
        for cmd in self.get_commands():
            registry.add(cmd)


class CommandRegistry:
    """명령어들을 모았다가 build()로 디스패처를 만든다."""

    def __init__(self):
        self._commands = []

    def add(self, command):
        self._commands.append(command)
        return command

    def add_module(self, module):
        module.register(self)
        return module

    def command(self, name, aliases=(), description="", build=None, permission=None):
        """데코레이터. @registry.command('ping')."""
        def deco(fn):
            self.add(_FunctionCommand(name, fn, aliases, description, build, permission))
            return fn
        return deco

    def commands(self):
        return list(self._commands)

    def find(self, name):
        for c in self._commands:
            if c.name == name or name in getattr(c, "aliases", ()):
                return c
        return None

    def build(self, dispatcher=None):
        d = dispatcher if dispatcher is not None else CommandDispatcher()
        for cmd in self._commands:
            cmd.install(d)
        return d
