"""기본 ArgumentType들 — 정수, 실수, 불리언, 문자열(word/quotable/greedy).

ArgumentType 인터페이스:
    parse(reader, source) -> 값
    list_suggestions(context, builder) -> Suggestions
    get_examples() -> 예시 리스트 (선택)
"""

from ..errors import CommandSyntaxError
from ..suggestions import Suggestions


class ArgumentType:
    def parse(self, reader, source=None):
        raise NotImplementedError

    def list_suggestions(self, context, builder):
        return Suggestions.empty()

    def get_examples(self):
        return []


class IntegerArgumentType(ArgumentType):
    def __init__(self, minimum=-(2 ** 31), maximum=2 ** 31 - 1):
        self.minimum = minimum
        self.maximum = maximum

    def parse(self, reader, source=None):
        start = reader.cursor
        result = reader.read_int()
        if result < self.minimum:
            reader.cursor = start
            raise CommandSyntaxError(
                f"Integer must not be less than {self.minimum}, found {result}", reader
            )
        if result > self.maximum:
            reader.cursor = start
            raise CommandSyntaxError(
                f"Integer must not be more than {self.maximum}, found {result}", reader
            )
        return result

    def get_examples(self):
        return ["0", "123", "-123"]


class FloatArgumentType(ArgumentType):
    def __init__(self, minimum=-float("inf"), maximum=float("inf")):
        self.minimum = minimum
        self.maximum = maximum

    def parse(self, reader, source=None):
        start = reader.cursor
        result = reader.read_float()
        if result < self.minimum:
            reader.cursor = start
            raise CommandSyntaxError(
                f"Float must not be less than {self.minimum}, found {result}", reader
            )
        if result > self.maximum:
            reader.cursor = start
            raise CommandSyntaxError(
                f"Float must not be more than {self.maximum}, found {result}", reader
            )
        return result

    def get_examples(self):
        return ["0", "1.2", "-5", ".5"]


class BoolArgumentType(ArgumentType):
    def parse(self, reader, source=None):
        return reader.read_boolean()

    def list_suggestions(self, context, builder):
        for word in ("true", "false"):
            if word.startswith(builder.remaining_lower):
                builder.suggest(word)
        return builder.build()

    def get_examples(self):
        return ["true", "false"]


# 문자열 타입 ----------------------------------------------------------------
WORD = "word"            # 공백 없는 단순 단어
PHRASE = "phrase"        # 따옴표로 감싸면 공백 허용
GREEDY = "greedy"        # 남은 전부 (메시지)


class StringArgumentType(ArgumentType):
    def __init__(self, str_type):
        self.str_type = str_type

    def parse(self, reader, source=None):
        if self.str_type == GREEDY:
            text = reader.get_remaining()
            reader.cursor = reader.get_total_length()
            return text
        if self.str_type == WORD:
            return reader.read_unquoted_string()
        return reader.read_string()

    def get_examples(self):
        if self.str_type == GREEDY:
            return ["word", "words with spaces", '"and symbols"']
        if self.str_type == WORD:
            return ["word", "words_with_underscores"]
        return ['"quoted phrase"', "word", "''"]


# 팩토리 함수 (Brigadier 스타일) --------------------------------------------
def integer(minimum=-(2 ** 31), maximum=2 ** 31 - 1):
    return IntegerArgumentType(minimum, maximum)


def float_arg(minimum=-float("inf"), maximum=float("inf")):
    return FloatArgumentType(minimum, maximum)


def boolean():
    return BoolArgumentType()


def word():
    return StringArgumentType(WORD)


def string():
    return StringArgumentType(PHRASE)


def greedy_string():
    return StringArgumentType(GREEDY)
