"""StringReader — 명령어 문자열을 커서로 한 글자씩 읽어나가는 파서 헬퍼.

Brigadier StringReader의 포팅. 모든 ArgumentType은 이 reader로부터 값을 읽는다.
"""

from .errors import CommandSyntaxError

SYNTAX_ESCAPE = "\\"
SYNTAX_DOUBLE_QUOTE = '"'
SYNTAX_SINGLE_QUOTE = "'"


def _is_allowed_number(c):
    return c.isdigit() or c == "." or c == "-"


def _is_quoted_string_start(c):
    return c == SYNTAX_DOUBLE_QUOTE or c == SYNTAX_SINGLE_QUOTE


def is_allowed_in_unquoted_string(c):
    return (
        "0" <= c <= "9"
        or "A" <= c <= "Z"
        or "a" <= c <= "z"
        or c == "_"
        or c == "-"
        or c == "."
        or c == "+"
    )


class StringReader:
    def __init__(self, source):
        # 다른 StringReader를 넘기면 복제 (커서 포함)
        if isinstance(source, StringReader):
            self.string = source.string
            self.cursor = source.cursor
        else:
            self.string = source
            self.cursor = 0

    # --- 기본 커서 조작 ---------------------------------------------------
    def get_remaining_length(self):
        return len(self.string) - self.cursor

    def get_total_length(self):
        return len(self.string)

    def get_read(self):
        return self.string[:self.cursor]

    def get_remaining(self):
        return self.string[self.cursor:]

    def can_read(self, length=1):
        return self.cursor + length <= len(self.string)

    def peek(self, offset=0):
        return self.string[self.cursor + offset]

    def read(self):
        c = self.string[self.cursor]
        self.cursor += 1
        return c

    def skip(self):
        self.cursor += 1

    def skip_whitespace(self):
        while self.can_read() and self.peek().isspace():
            self.skip()

    # --- 타입별 읽기 ------------------------------------------------------
    def read_int(self):
        start = self.cursor
        while self.can_read() and _is_allowed_number(self.peek()):
            self.skip()
        number = self.string[start:self.cursor]
        if not number:
            raise CommandSyntaxError("Expected integer", self)
        try:
            return int(number)
        except ValueError:
            self.cursor = start
            raise CommandSyntaxError(f"Invalid integer '{number}'", self)

    def read_float(self):
        start = self.cursor
        while self.can_read() and _is_allowed_number(self.peek()):
            self.skip()
        number = self.string[start:self.cursor]
        if not number:
            raise CommandSyntaxError("Expected float", self)
        try:
            return float(number)
        except ValueError:
            self.cursor = start
            raise CommandSyntaxError(f"Invalid float '{number}'", self)

    def read_unquoted_string(self):
        start = self.cursor
        while self.can_read() and is_allowed_in_unquoted_string(self.peek()):
            self.skip()
        return self.string[start:self.cursor]

    def read_quoted_string(self):
        if not self.can_read():
            return ""
        next_char = self.peek()
        if not _is_quoted_string_start(next_char):
            raise CommandSyntaxError("Expected quote to start a string", self)
        self.skip()
        return self.read_string_until(next_char)

    def read_string_until(self, terminator):
        result = []
        escaped = False
        while self.can_read():
            c = self.read()
            if escaped:
                if c == terminator or c == SYNTAX_ESCAPE:
                    result.append(c)
                    escaped = False
                else:
                    self.cursor -= 1
                    raise CommandSyntaxError(f"Invalid escape sequence '{c}'", self)
            elif c == SYNTAX_ESCAPE:
                escaped = True
            elif c == terminator:
                return "".join(result)
            else:
                result.append(c)
        raise CommandSyntaxError("Unclosed quoted string", self)

    def read_string(self):
        if not self.can_read():
            return ""
        next_char = self.peek()
        if _is_quoted_string_start(next_char):
            self.skip()
            return self.read_string_until(next_char)
        return self.read_unquoted_string()

    def read_boolean(self):
        start = self.cursor
        value = self.read_unquoted_string()
        if not value:
            raise CommandSyntaxError("Expected bool", self)
        if value == "true":
            return True
        if value == "false":
            return False
        self.cursor = start
        raise CommandSyntaxError(f"Invalid bool '{value}' (must be true or false)", self)

    def expect(self, c):
        if not self.can_read() or self.peek() != c:
            raise CommandSyntaxError(f"Expected '{c}'", self)
        self.skip()
