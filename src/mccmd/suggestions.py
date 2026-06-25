"""자동완성(suggestion) 자료구조.

SuggestionsBuilder에 후보를 모으고 build()로 Suggestions를 만든다.
StringRange는 '입력 문자열의 어느 구간을 교체할지'를 나타낸다.
"""


class StringRange:
    def __init__(self, start, end):
        self.start = start
        self.end = end

    @staticmethod
    def at(pos):
        return StringRange(pos, pos)

    @staticmethod
    def between(start, end):
        return StringRange(start, end)

    @staticmethod
    def encompassing(a, b):
        return StringRange(min(a.start, b.start), max(a.end, b.end))

    def get(self, string):
        return string[self.start:self.end]

    def is_empty(self):
        return self.start == self.end

    def length(self):
        return self.end - self.start

    def __eq__(self, other):
        return isinstance(other, StringRange) and self.start == other.start and self.end == other.end

    def __hash__(self):
        return hash((self.start, self.end))

    def __repr__(self):
        return f"StringRange[{self.start}, {self.end}]"


class Suggestion:
    """하나의 자동완성 후보. range 구간을 text로 교체하면 된다."""

    def __init__(self, range_, text, tooltip=None):
        self.range = range_
        self.text = text
        self.tooltip = tooltip

    def apply(self, input_):
        """후보를 입력 문자열에 적용한 결과 전체 문자열."""
        if self.range.start == 0 and self.range.end == len(input_):
            return self.text
        result = ""
        if self.range.start > 0:
            result += input_[:self.range.start]
        result += self.text
        if self.range.end < len(input_):
            result += input_[self.range.end:]
        return result

    def __eq__(self, other):
        return (
            isinstance(other, Suggestion)
            and self.range == other.range
            and self.text == other.text
            and self.tooltip == other.tooltip
        )

    def __hash__(self):
        return hash((self.range, self.text, self.tooltip))

    def __repr__(self):
        return f"Suggestion({self.text!r})"


class Suggestions:
    def __init__(self, range_, suggestions):
        self.range = range_
        self.list = suggestions

    def is_empty(self):
        return len(self.list) == 0

    @staticmethod
    def empty():
        return Suggestions(StringRange.at(0), [])

    @staticmethod
    def merge(command, inputs):
        """여러 노드가 만든 Suggestions들을 하나로 합친다."""
        if not inputs:
            return Suggestions.empty()
        if len(inputs) == 1:
            return inputs[0]
        texts = []
        for s in inputs:
            texts.extend(s.list)
        return Suggestions.create(command, texts)

    @staticmethod
    def create(command, suggestions):
        if not suggestions:
            return Suggestions.empty()
        start = min(s.range.start for s in suggestions)
        end = max(s.range.end for s in suggestions)
        full = StringRange(start, end)
        # 모든 후보의 range를 공통 구간으로 정규화
        normalized = set()
        for s in suggestions:
            if s.range == full:
                normalized.add(s)
            else:
                text = (
                    command[full.start:s.range.start]
                    + s.text
                    + command[s.range.end:full.end]
                )
                normalized.add(Suggestion(full, text, s.tooltip))
        result = sorted(normalized, key=lambda s: s.text.lower())
        return Suggestions(full, result)


class SuggestionsBuilder:
    """후보를 모으는 빌더. remaining(현재까지 입력된 단어)와의 prefix 비교에 쓴다."""

    def __init__(self, input_, start, input_lower=None):
        self.input = input_
        self.input_lower = input_lower if input_lower is not None else input_.lower()
        self.start = start
        self.remaining = input_[start:]
        self.remaining_lower = self.input_lower[start:]
        self._result = []

    def build(self):
        return Suggestions.create(self.input, self._result)

    def suggest(self, text, tooltip=None):
        if text != self.remaining:
            self._result.append(Suggestion(StringRange.between(self.start, len(self.input)), text, tooltip))
        return self

    def suggest_int(self, value, tooltip=None):
        self._result.append(Suggestion(StringRange.between(self.start, len(self.input)), str(value), tooltip))
        return self

    def create_offset(self, start):
        """다른 시작 위치로 새 빌더를 만든다 (중첩 파싱용)."""
        return SuggestionsBuilder(self.input, start, self.input_lower)

    def restart(self):
        return self.create_offset(self.start)
