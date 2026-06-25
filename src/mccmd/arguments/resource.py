"""리소스 ID 인자 — 아이템/블록처럼 '정해진 목록 중 하나'를 받는 제네릭 타입.

마인크래프트의 `minecraft:diamond_sword` 같은 네임스페이스 ID를 모델링한다.
ID 목록은 외부에서 주입한다(엔진은 특정 게임 콘텐츠를 들고 있지 않는다):

    from mccmd.arguments import resource
    item_arg = resource(["ruby", "mythril", ...], namespace="mymod", label="item")

레지스트리(허용 ID 집합)를 들고 있어서:
  - parse: 목록에 없는 ID는 거부
  - list_suggestions: 입력 접두사로 필터링한 후보를 제공 (대량이면 드롭다운 스크롤)
"""

from ..errors import CommandSyntaxError
from ..reader import is_allowed_in_unquoted_string


class ResourceArgumentType:
    """정해진 ID 목록 중 하나. 네임스페이스(minecraft:)는 선택적으로 받는다."""

    def __init__(self, ids, namespace="minecraft", label="id"):
        self.ids = list(ids)
        self._id_set = set(self.ids)
        self.namespace = namespace
        self.label = label

    def _read_id(self, reader):
        start = reader.cursor
        while reader.can_read() and (
            is_allowed_in_unquoted_string(reader.peek()) or reader.peek() == ":"
        ):
            reader.skip()
        return reader.string[start:reader.cursor]

    def parse(self, reader, source=None):
        start = reader.cursor
        raw = self._read_id(reader)
        if not raw:
            raise CommandSyntaxError(f"Expected {self.label}", reader)
        # 네임스페이스 분리
        short = raw.split(":", 1)[1] if ":" in raw else raw
        if short not in self._id_set:
            reader.cursor = start
            raise CommandSyntaxError(f"Unknown {self.label} '{raw}'", reader)
        return short

    def list_suggestions(self, context, builder):
        typed = builder.remaining_lower
        if ":" in typed:
            # 네임스페이스까지 입력 중이면 풀 ID로 제안
            for i in self.ids:
                full = f"{self.namespace}:{i}"
                if full.startswith(typed):
                    builder.suggest(full)
        else:
            for i in self.ids:
                if i.startswith(typed):
                    builder.suggest(i)
        return builder.build()

    def get_examples(self):
        return self.ids[:3]
