"""mccmd — 마인크래프트식 명령어 엔진 (Brigadier 파이썬 포팅).

핵심:
    CommandDispatcher   명령어 등록 / 파싱 / 실행 / 자동완성
    literal, argument   트리 빌더
    arguments           인자 타입 (integer, entity, vec3 ...)
    CommandSource/World/Entity  실행 주체와 가상 월드

예시는 commands.build_default_dispatcher() 참고.
"""

from .errors import CommandSyntaxError
from .reader import StringReader
from .dispatcher import CommandDispatcher
from .builder import literal, argument, LiteralArgumentBuilder, RequiredArgumentBuilder
from .source import CommandSource, World, Entity
from .suggestions import Suggestions, Suggestion, StringRange, SuggestionsBuilder
from .registry import Command, CommandModule, CommandRegistry
from .service import CommandService, Completion, ExecutionResult
from . import arguments

__all__ = [
    "CommandSyntaxError",
    "StringReader",
    "CommandDispatcher",
    "literal",
    "argument",
    "LiteralArgumentBuilder",
    "RequiredArgumentBuilder",
    "CommandSource",
    "World",
    "Entity",
    "Suggestions",
    "Suggestion",
    "StringRange",
    "SuggestionsBuilder",
    "Command",
    "CommandModule",
    "CommandRegistry",
    "CommandService",
    "Completion",
    "ExecutionResult",
    "arguments",
]
