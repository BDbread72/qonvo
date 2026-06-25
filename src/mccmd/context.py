"""파싱 결과를 담는 컨텍스트 객체들.

- ParsedArgument: 파싱된 인자 하나(이름 없이 값+구간).
- ParsedCommandNode: 어떤 노드가 어느 구간을 먹었는지.
- CommandContext: 실행 시점에 커맨드 함수에 넘겨지는 '완성된' 컨텍스트.
- CommandContextBuilder: 파싱 도중 점진적으로 채워지는 가변 컨텍스트.
- ParseResults: parse()의 반환값 (컨텍스트 + 남은 reader + 오류들).
"""

from .suggestions import StringRange


class ParsedArgument:
    def __init__(self, start, end, result):
        self.range = StringRange.between(start, end)
        self.result = result


class ParsedCommandNode:
    def __init__(self, node, range_):
        self.node = node
        self.range = range_

    def __repr__(self):
        return f"{self.node}@{self.range}"


class SuggestionContext:
    def __init__(self, parent, start_pos):
        self.parent = parent
        self.start_pos = start_pos


class CommandContext:
    """실행 시 커맨드 함수가 받는 읽기 전용 컨텍스트."""

    def __init__(self, source, input_, arguments, command, root_node,
                 nodes, range_, child, modifier, forks):
        self.source = source
        self.input = input_
        self.arguments = arguments
        self.command = command
        self.root_node = root_node
        self.nodes = nodes
        self.range = range_
        self.child = child
        self.modifier = modifier
        self.forks = forks

    def get_argument(self, name):
        if name not in self.arguments:
            raise ValueError(f"No such argument '{name}'")
        return self.arguments[name].result

    def has_argument(self, name):
        return name in self.arguments

    def get_child(self):
        return self.child

    def get_last_child(self):
        result = self
        while result.child is not None:
            result = result.child
        return result

    def copy_for(self, source):
        if source is self.source:
            return self
        return CommandContext(source, self.input, self.arguments, self.command,
                              self.root_node, self.nodes, self.range, self.child,
                              self.modifier, self.forks)


class CommandContextBuilder:
    """파싱 중 점진적으로 채워지는 컨텍스트. build()로 불변 CommandContext가 된다."""

    def __init__(self, dispatcher, source, root_node, start):
        self.dispatcher = dispatcher
        self.source = source
        self.root_node = root_node
        self.arguments = {}
        self.command = None
        self.nodes = []
        self.range = StringRange.at(start)
        self.child = None
        self.modifier = None
        self.forks = False

    def with_source(self, source):
        self.source = source
        return self

    def with_argument(self, name, argument):
        self.arguments[name] = argument
        return self

    def with_command(self, command):
        self.command = command
        return self

    def with_node(self, node, range_):
        self.nodes.append(ParsedCommandNode(node, range_))
        self.range = StringRange.encompassing(self.range, range_)
        self.modifier = node.modifier
        self.forks = node.forks
        return self

    def with_child(self, child):
        self.child = child
        return self

    def copy(self):
        copy = CommandContextBuilder(self.dispatcher, self.source, self.root_node, self.range.start)
        copy.command = self.command
        copy.arguments.update(self.arguments)
        copy.nodes.extend(self.nodes)
        copy.child = self.child
        copy.range = self.range
        copy.modifier = self.modifier
        copy.forks = self.forks
        return copy

    def build(self, input_):
        return CommandContext(
            self.source,
            input_,
            self.arguments,
            self.command,
            self.root_node,
            self.nodes,
            self.range,
            None if self.child is None else self.child.build(input_),
            self.modifier,
            self.forks,
        )

    def get_last_child(self):
        result = self
        while result.child is not None:
            result = result.child
        return result

    def find_suggestion_context(self, cursor):
        """커서 위치에서 자동완성을 어느 노드의 자식들로 만들지 결정한다.

        Brigadier CommandContextBuilder.findSuggestionContext 포팅.
        """
        if self.range.start <= cursor:
            if self.range.end < cursor:
                if self.child is not None:
                    return self.child.find_suggestion_context(cursor)
                if self.nodes:
                    last = self.nodes[-1]
                    return SuggestionContext(last.node, last.range.end + 1)
                return SuggestionContext(self.root_node, self.range.start)
            else:
                prev = self.root_node
                for parsed in self.nodes:
                    node_range = parsed.range
                    if node_range.start <= cursor <= node_range.end:
                        return SuggestionContext(prev, node_range.start)
                    prev = parsed.node
                if prev is None:
                    raise RuntimeError("Can't find node before cursor")
                return SuggestionContext(prev, self.range.start)
        raise RuntimeError("Can't find node before cursor")


class ParseResults:
    def __init__(self, context, reader, exceptions):
        self.context = context
        self.reader = reader
        self.exceptions = exceptions
