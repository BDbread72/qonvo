"""명령어 파싱/실행 중 발생하는 예외.

Brigadier의 CommandSyntaxException에 해당. 메시지와 함께 '어디서' 틀렸는지를
커서 위치와 입력 문자열로 들고 다녀서, 사용자에게 친절한 화살표를 보여줄 수 있다.
"""


class CommandSyntaxError(Exception):
    """명령어 문법 오류.

    Args:
        message: 사람이 읽을 오류 메시지.
        reader: 오류가 난 StringReader (커서/입력을 뽑아내기 위함). 선택.
        cursor: 직접 지정할 커서 위치. 없으면 reader.cursor 사용.
    """

    CONTEXT_AMOUNT = 10  # 화살표 앞에 보여줄 최대 글자 수

    def __init__(self, message, reader=None, cursor=None):
        super().__init__(message)
        self.raw_message = message
        if reader is not None:
            self.input = reader.string
            self.cursor = reader.cursor if cursor is None else cursor
        else:
            self.input = None
            self.cursor = cursor

    def context(self):
        """오류 지점 주변 문맥을 '...앞부분<--[HERE]' 형태로 반환."""
        if self.input is None or self.cursor is None:
            return None
        cursor = min(len(self.input), self.cursor)
        if cursor > self.CONTEXT_AMOUNT:
            prefix = "..." + self.input[cursor - self.CONTEXT_AMOUNT:cursor]
        else:
            prefix = self.input[:cursor]
        return prefix + "<--[HERE]"

    def __str__(self):
        ctx = self.context()
        if ctx is None:
            return self.raw_message
        return f"{self.raw_message} at position {self.cursor}: {ctx}"
