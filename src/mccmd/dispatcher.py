"""CommandDispatcher — 명령어 등록 / 파싱 / 실행 / 자동완성의 중심.

Brigadier CommandDispatcher 포팅. 사용 흐름:

    d = CommandDispatcher()
    d.register(literal("say").then(argument("msg", greedy_string()).executes(...)))
    d.execute("say hello", source)
    d.get_completion_suggestions(d.parse("sa", source))
"""

from functools import cmp_to_key

from .errors import CommandSyntaxError
from .reader import StringReader
from .tree import RootCommandNode, LiteralCommandNode
from .context import CommandContextBuilder, ParseResults
from .suggestions import Suggestions, SuggestionsBuilder

ARGUMENT_SEPARATOR = " "


def _potential_compare(a, b):
    a_can = a.reader.can_read()
    b_can = b.reader.can_read()
    if not a_can and b_can:
        return -1
    if a_can and not b_can:
        return 1
    a_err = len(a.exceptions) == 0
    b_err = len(b.exceptions) == 0
    if a_err and not b_err:
        return -1
    if not a_err and b_err:
        return 1
    return 0


class CommandDispatcher:
    def __init__(self, root=None):
        self.root = root if root is not None else RootCommandNode()

    # --- 등록 -------------------------------------------------------------
    def register(self, literal_builder):
        """LiteralArgumentBuilder를 등록하고, 만들어진 노드를 반환한다."""
        node = literal_builder.build()
        self.root.add_child(node)
        return node

    # --- 파싱 -------------------------------------------------------------
    def parse(self, command, source):
        reader = command if isinstance(command, StringReader) else StringReader(command)
        context = CommandContextBuilder(self, source, self.root, reader.cursor)
        return self._parse_nodes(self.root, reader, context)

    def _parse_nodes(self, node, original_reader, context_so_far):
        source = context_so_far.source
        errors = None
        potentials = None
        cursor = original_reader.cursor

        for child in node.get_relevant_nodes(original_reader):
            if not child.can_use(source):
                continue
            context = context_so_far.copy()
            reader = StringReader(original_reader)
            try:
                child.parse(reader, context)
                if reader.can_read() and reader.peek() != ARGUMENT_SEPARATOR:
                    raise CommandSyntaxError(
                        "Expected whitespace to end one argument, but found trailing data",
                        reader,
                    )
            except CommandSyntaxError as ex:
                if errors is None:
                    errors = {}
                errors[child.name] = ex
                reader.cursor = cursor
                continue

            context.with_command(child.command)
            need = 2 if child.redirect is not None else 1
            if reader.can_read(need):
                reader.skip()  # 인자 구분 공백 소비
                if child.redirect is not None:
                    child_context = CommandContextBuilder(self, source, child.redirect, reader.cursor)
                    parse = self._parse_nodes(child.redirect, reader, child_context)
                    context.with_child(parse.context)
                    return ParseResults(context, parse.reader, parse.exceptions)
                parse = self._parse_nodes(child, reader, context)
                if potentials is None:
                    potentials = []
                potentials.append(parse)
            else:
                if potentials is None:
                    potentials = []
                potentials.append(ParseResults(context, reader, {}))

        if potentials is not None:
            if len(potentials) > 1:
                potentials.sort(key=cmp_to_key(_potential_compare))
            return potentials[0]

        return ParseResults(context_so_far, original_reader, errors or {})

    # --- 실행 -------------------------------------------------------------
    def execute(self, command, source=None):
        if isinstance(command, ParseResults):
            parse = command
        else:
            parse = self.parse(command, source)
        return self.execute_parsed(parse)

    def execute_parsed(self, parse):
        if parse.reader.can_read():
            if len(parse.exceptions) == 1:
                raise next(iter(parse.exceptions.values()))
            if parse.context.range.is_empty():
                raise CommandSyntaxError("Unknown command", parse.reader)
            raise CommandSyntaxError("Incorrect argument for command", parse.reader)

        result = 0
        successful_forks = 0
        forked = False
        found_command = False
        command_text = parse.reader.string
        original = parse.context.build(command_text)
        contexts = [original]
        next_round = None

        while contexts is not None:
            for context in contexts:
                child = context.get_child()
                if child is not None:
                    forked = forked or context.forks
                    if child.command is not None or child.nodes:
                        found_command = True
                        modifier = context.modifier
                        if modifier is None:
                            if next_round is None:
                                next_round = []
                            next_round.append(child.copy_for(context.source))
                        else:
                            try:
                                results = modifier(context)
                                if results:
                                    if next_round is None:
                                        next_round = []
                                    for src in results:
                                        next_round.append(child.copy_for(src))
                            except CommandSyntaxError:
                                if not forked:
                                    raise
                elif context.command is not None:
                    found_command = True
                    try:
                        value = context.command(context)
                        if value is None:
                            value = 1
                        result += value
                        successful_forks += 1
                    except CommandSyntaxError:
                        if not forked:
                            raise
            contexts = next_round
            next_round = None

        if not found_command:
            raise CommandSyntaxError("Unknown command", parse.reader)
        return successful_forks if forked else result

    # --- 자동완성 ---------------------------------------------------------
    def get_completion_suggestions(self, parse, cursor=None):
        if cursor is None:
            cursor = parse.reader.get_total_length()
        context = parse.context
        node_before_cursor = context.find_suggestion_context(cursor)
        parent = node_before_cursor.parent
        start = min(node_before_cursor.start_pos, cursor)

        full_input = parse.reader.string
        truncated = full_input[:cursor]
        truncated_lower = truncated.lower()

        suggestions = []
        for node in parent.children.values():
            if not node.can_use(context.source):
                continue
            try:
                builder = SuggestionsBuilder(truncated, start, truncated_lower)
                suggestions.append(node.list_suggestions(context.build(truncated), builder))
            except CommandSyntaxError:
                continue

        return Suggestions.merge(full_input, suggestions)

    # --- 사용법 힌트(고스트 텍스트) --------------------------------------
    def get_usage_hint(self, parse, cursor=None):
        """커서 위치에서 '다음에 올 토큰'의 사용법 문자열을 돌려준다.

        입력창에 회색 고스트로 띄울 용도. 예:
            ""            -> "<command>"
            "gi"          -> "ve <targets> <item> [<count>]"
            "give @a "    -> "<item> [<count>]"
            "kill "       -> "[<targets>]"
        """
        try:
            if cursor is None:
                cursor = parse.reader.get_total_length()
            context = parse.context
            sctx = context.find_suggestion_context(cursor)
            parent = sctx.parent
            start = min(sctx.start_pos, cursor)
            source = context.source
            typed = parse.reader.string[start:cursor]

            if typed == "":
                cont = self._continuation(parent, source)
                if cont and not isinstance(parent, RootCommandNode) and parent.command is not None:
                    cont = "[" + cont + "]"
                return cont

            # 토큰 입력 중: 유일하게 매칭되는 리터럴이면 나머지 + 이어지는 사용법
            matches = [
                c for c in parent.children.values()
                if isinstance(c, LiteralCommandNode)
                and c.can_use(source)
                and c.literal.startswith(typed)
            ]
            if len(matches) == 1 and matches[0].literal != typed:
                node = matches[0]
                remainder = node.literal[len(typed):]
                cont = self._continuation(node, source)
                if not cont:
                    return remainder
                if node.command is not None:
                    return f"{remainder} [{cont}]"
                return f"{remainder} {cont}"
            return ""
        except Exception:
            return ""

    def _continuation(self, node, source):
        """node 다음에 올 수 있는 토큰들을 한 줄 사용법으로 요약."""
        if isinstance(node, RootCommandNode):
            return "<command>"
        children = [c for c in node.children.values() if c.can_use(source)]
        if not children:
            return ""
        if len(children) == 1:
            return self._chain(children[0], source)
        # 여러 갈래는 즉시 토큰만 (깊이 확장하지 않음)
        toks = list(dict.fromkeys(c.get_usage_text() for c in children))
        return "(" + " | ".join(toks) + ")"

    def _chain(self, child, source):
        """단일 경로를 깊이 따라가며 사용법을 잇는다."""
        token = child.get_usage_text()
        if child.redirect is not None:
            return token
        cont = self._continuation(child, source)
        if not cont:
            return token
        if child.command is not None:   # 여기까지로 실행 가능 → 이후는 선택
            return f"{token} [{cont}]"
        return f"{token} {cont}"

    # --- 편의 ------------------------------------------------------------
    def get_all_usage(self, node=None, source=None):
        """등록된 모든 명령어 경로를 'give <targets> <item>' 식 문자열 리스트로."""
        node = node if node is not None else self.root
        result = []
        self._collect_usage(node, "", source, result)
        return result

    def _collect_usage(self, node, prefix, source, result):
        for child in node.children.values():
            if source is not None and not child.can_use(source):
                continue
            text = (prefix + " " + child.get_usage_text()).strip()
            if child.command is not None:
                result.append(text)
            if child.redirect is not None:
                result.append(text + " -> " + (child.redirect.name or "root"))
            elif child.children:
                self._collect_usage(child, text, source, result)
