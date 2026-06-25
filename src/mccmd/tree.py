"""명령어 트리의 노드들.

- RootCommandNode: 트리의 뿌리.
- LiteralCommandNode: 고정 단어 (give, tp, execute ...).
- ArgumentCommandNode: 인자 (<targets>, <count> ...). ArgumentType이 파싱을 담당.

각 노드는 parse(reader, ctx)로 입력을 먹고, list_suggestions로 자동완성을 제공한다.
"""

from .errors import CommandSyntaxError
from .suggestions import StringRange


class CommandNode:
    def __init__(self, command=None, requirement=None, redirect=None,
                 modifier=None, forks=False):
        self.children = {}
        self.literals = {}
        self.arguments = {}
        self.command = command
        self.requirement = requirement if requirement is not None else (lambda src: True)
        self.redirect = redirect
        self.modifier = modifier
        self.forks = forks

    # --- 트리 구성 --------------------------------------------------------
    def add_child(self, node):
        if isinstance(node, RootCommandNode):
            raise ValueError("Cannot add a RootCommandNode as a child")
        existing = self.children.get(node.name)
        if existing is not None:
            # 같은 이름이면 병합 (Brigadier와 동일)
            if node.command is not None:
                existing.command = node.command
            for child in node.children.values():
                existing.add_child(child)
            return
        self.children[node.name] = node
        if isinstance(node, LiteralCommandNode):
            self.literals[node.name] = node
        elif isinstance(node, ArgumentCommandNode):
            self.arguments[node.name] = node

    def get_child(self, name):
        return self.children.get(name)

    def can_use(self, source):
        try:
            return self.requirement(source)
        except Exception:
            return False

    def get_relevant_nodes(self, reader):
        """현재 커서에서 시도해볼 자식 노드들 (리터럴 우선)."""
        if self.literals:
            cursor = reader.cursor
            while reader.can_read() and reader.peek() != " ":
                reader.skip()
            text = reader.string[cursor:reader.cursor]
            reader.cursor = cursor
            literal = self.literals.get(text)
            if literal is not None:
                return [literal]
            return list(self.arguments.values())
        return list(self.arguments.values())

    # 하위 클래스에서 구현
    def parse(self, reader, context_builder):
        raise NotImplementedError

    def list_suggestions(self, context, builder):
        raise NotImplementedError

    @property
    def name(self):
        raise NotImplementedError

    def get_usage_text(self):
        raise NotImplementedError


class RootCommandNode(CommandNode):
    @property
    def name(self):
        return ""

    def parse(self, reader, context_builder):
        pass

    def list_suggestions(self, context, builder):
        return builder.build()

    def get_usage_text(self):
        return ""

    def __repr__(self):
        return "<root>"


class LiteralCommandNode(CommandNode):
    def __init__(self, literal, command=None, requirement=None, redirect=None,
                 modifier=None, forks=False):
        super().__init__(command, requirement, redirect, modifier, forks)
        self.literal = literal
        self.literal_lower = literal.lower()

    @property
    def name(self):
        return self.literal

    def _parse(self, reader):
        start = reader.cursor
        if reader.can_read(len(self.literal)):
            end = start + len(self.literal)
            if reader.string[start:end] == self.literal:
                reader.cursor = end
                if not reader.can_read() or reader.peek() == " ":
                    return end
                reader.cursor = start
        return -1

    def parse(self, reader, context_builder):
        start = reader.cursor
        end = self._parse(reader)
        if end > -1:
            context_builder.with_node(self, StringRange.between(start, end))
            return
        raise CommandSyntaxError(
            f"Expected literal '{self.literal}'", reader
        )

    def list_suggestions(self, context, builder):
        if self.literal_lower.startswith(builder.remaining_lower):
            return builder.suggest(self.literal).build()
        return builder.build()

    def get_usage_text(self):
        return self.literal

    def __repr__(self):
        return f"<literal {self.literal}>"


class ArgumentCommandNode(CommandNode):
    USAGE_OPEN = "<"
    USAGE_CLOSE = ">"

    def __init__(self, arg_name, arg_type, command=None, requirement=None,
                 redirect=None, modifier=None, forks=False, suggestions_provider=None):
        super().__init__(command, requirement, redirect, modifier, forks)
        self.arg_name = arg_name
        self.type = arg_type
        self.suggestions_provider = suggestions_provider

    @property
    def name(self):
        return self.arg_name

    def parse(self, reader, context_builder):
        start = reader.cursor
        try:
            result = self.type.parse(reader, context_builder.source)
        except CommandSyntaxError:
            raise
        except Exception as ex:
            # 커스텀 인자 타입이 던진 예기치 못한 예외도 문법 오류로 취급
            # (자동완성 중 '아직 덜 친' 입력에서 엔진이 죽지 않도록)
            reader.cursor = start
            raise CommandSyntaxError(f"Invalid argument <{self.arg_name}>: {ex}", reader)
        from .context import ParsedArgument
        parsed = ParsedArgument(start, reader.cursor, result)
        context_builder.with_argument(self.arg_name, parsed)
        context_builder.with_node(self, parsed.range)

    def list_suggestions(self, context, builder):
        if self.suggestions_provider is not None:
            return self.suggestions_provider(context, builder)
        return self.type.list_suggestions(context, builder)

    def get_usage_text(self):
        return self.USAGE_OPEN + self.arg_name + self.USAGE_CLOSE

    def __repr__(self):
        return f"<argument {self.arg_name}>"
