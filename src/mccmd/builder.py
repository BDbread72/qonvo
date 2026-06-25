"""트리를 선언적으로 짓기 위한 빌더.

    literal("give")
        .then(argument("targets", entity())
            .then(argument("item", word())
                .executes(give_command)))

처럼 체이닝으로 명령어 트리를 만든다. Brigadier의 ArgumentBuilder 포팅.
"""

from .tree import LiteralCommandNode, ArgumentCommandNode


class ArgumentBuilder:
    def __init__(self):
        self.arguments_root = []   # then()으로 붙은 자식 빌더/노드
        self.command = None
        self.requirement = lambda src: True
        self.target = None         # redirect 대상 노드
        self.modifier = None
        self.forks = False
        self.suggestions_provider = None

    def then(self, node_or_builder):
        if self.target is not None:
            raise ValueError("Cannot add children to a redirected node")
        self.arguments_root.append(node_or_builder)
        return self

    def executes(self, command):
        self.command = command
        return self

    def requires(self, requirement):
        self.requirement = requirement
        return self

    def redirect(self, target, modifier=None):
        return self.forward(target, modifier, False)

    def fork(self, target, modifier):
        return self.forward(target, modifier, True)

    def forward(self, target, modifier, fork):
        if self.arguments_root:
            raise ValueError("Cannot forward a node with children")
        self.target = target
        self.modifier = modifier
        self.forks = fork
        return self

    def _build_children(self, node):
        for child in self.arguments_root:
            built = child.build() if isinstance(child, ArgumentBuilder) else child
            node.add_child(built)

    def build(self):
        raise NotImplementedError


class LiteralArgumentBuilder(ArgumentBuilder):
    def __init__(self, literal):
        super().__init__()
        self.literal = literal

    def build(self):
        node = LiteralCommandNode(
            self.literal,
            command=self.command,
            requirement=self.requirement,
            redirect=self.target,
            modifier=self.modifier,
            forks=self.forks,
        )
        self._build_children(node)
        return node


class RequiredArgumentBuilder(ArgumentBuilder):
    def __init__(self, arg_name, arg_type):
        super().__init__()
        self.arg_name = arg_name
        self.type = arg_type

    def suggests(self, provider):
        self.suggestions_provider = provider
        return self

    def build(self):
        node = ArgumentCommandNode(
            self.arg_name,
            self.type,
            command=self.command,
            requirement=self.requirement,
            redirect=self.target,
            modifier=self.modifier,
            forks=self.forks,
            suggestions_provider=self.suggestions_provider,
        )
        self._build_children(node)
        return node


def literal(name):
    return LiteralArgumentBuilder(name)


def argument(name, arg_type):
    return RequiredArgumentBuilder(name, arg_type)
