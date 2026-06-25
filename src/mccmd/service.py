"""CommandService — 프론트엔드 무관 헤드리스 명령어 처리기.

뷰(터미널/Discord 봇/웹/게임 루프)는 이 서비스의 execute()/suggest()만 호출하면
된다. UI 로직과 명령어 처리 로직을 완전히 분리하는 경계선.

    service = CommandService(dispatcher, source_factory)
    result = service.execute("give @a diamond 5")   # -> ExecutionResult
    comps  = service.suggest("give @", cursor=6)     # -> [Completion, ...]
"""

from .errors import CommandSyntaxError


class Completion:
    """자동완성 후보 하나. 뷰가 그대로 화면에 꽂거나 apply()로 적용한다."""

    def __init__(self, text, start, end, tooltip=None, source_input=""):
        self.text = text          # 교체될 텍스트
        self.start = start        # 입력 문자열에서 교체 시작 인덱스
        self.end = end            # 교체 끝 인덱스
        self.tooltip = tooltip    # 설명(있으면)
        self.source_input = source_input

    def apply(self):
        """이 후보를 원본 입력에 적용한 전체 문자열."""
        return self.source_input[:self.start] + self.text + self.source_input[self.end:]

    def __repr__(self):
        return f"Completion({self.text!r})"


class ExecutionResult:
    """명령어 실행 결과. 뷰는 ok/messages/error만 보고 그리면 된다."""

    def __init__(self, ok, value=0, messages=None, error=None, error_cursor=None):
        self.ok = ok
        self.value = value                  # execute 반환값 (성공 개수 등)
        self.messages = messages or []      # source.send_message로 모인 출력
        self.error = error                  # 실패 시 메시지
        self.error_cursor = error_cursor    # 실패 위치 (밑줄/화살표용)

    @property
    def failed(self):
        return not self.ok

    def __repr__(self):
        if self.ok:
            return f"ExecutionResult(ok, value={self.value}, {len(self.messages)} msgs)"
        return f"ExecutionResult(error={self.error!r})"


class CommandService:
    def __init__(self, dispatcher, source_factory):
        """
        Args:
            dispatcher: CommandDispatcher
            source_factory: () -> CommandSource. 실행/제안 때마다 새 source를 만든다.
        """
        self.dispatcher = dispatcher
        self.source_factory = source_factory

    # --- 실행 -------------------------------------------------------------
    def execute(self, text, source=None):
        src = source if source is not None else self.source_factory()
        captured = []
        src.set_output(captured.append)
        try:
            value = self.dispatcher.execute(text, src)
            return ExecutionResult(True, value, captured)
        except CommandSyntaxError as e:
            return ExecutionResult(False, 0, captured, str(e), e.cursor)

    # --- 자동완성 ---------------------------------------------------------
    def suggest(self, text, cursor=None):
        """커서 위치에서 가능한 자동완성 후보 리스트."""
        if cursor is None:
            cursor = len(text)
        src = self.source_factory()
        parse = self.dispatcher.parse(text, src)
        suggestions = self.dispatcher.get_completion_suggestions(parse, cursor)
        return [
            Completion(s.text, s.range.start, s.range.end, s.tooltip, text)
            for s in suggestions.list
        ]

    # --- 사용법 고스트 ----------------------------------------------------
    def ghost(self, text, cursor=None):
        """입력창 회색 고스트로 띄울 '다음 토큰 사용법' 문자열.

        커서 뒤에 그대로 이어붙이면 된다. 예: 'gi' -> 've <targets> ...'.
        """
        if cursor is None:
            cursor = len(text)
        src = self.source_factory()
        parse = self.dispatcher.parse(text, src)
        return self.dispatcher.get_usage_hint(parse, cursor)

    # --- 도움말 -----------------------------------------------------------
    def usage(self, source=None):
        """등록된 모든 명령어 사용법 문자열 리스트."""
        src = source if source is not None else self.source_factory()
        return self.dispatcher.get_all_usage(source=src)
