"""엔티티 셀렉터 인자 — @p @a @r @e @s 와 [type=, distance=, limit=, sort=, name=].

EntityArgumentType.parse는 EntitySelector(아직 풀리지 않은 '질의')를 돌려준다.
실행 시점에 selector.resolve(source)로 실제 엔티티 목록을 얻는다.
"""

from ..errors import CommandSyntaxError
from ..suggestions import Suggestions

SELECTORS = {
    "p": ("nearest player", True),    # 단일
    "a": ("all players", False),      # 다중
    "r": ("random player", True),
    "s": ("self", True),
    "e": ("all entities", False),
}

# [key=value] 에서 허용하는 옵션과 간단한 도움말
OPTIONS = {
    "type": "엔티티 종류 (예: zombie, player, !player)",
    "name": "이름 (예: Steve, !Steve)",
    "distance": "거리 범위 (예: ..10, 3.., 2..8)",
    "limit": "최대 개수 (예: 1, 5)",
    "sort": "정렬 (nearest|furthest|arbitrary)",
    "tag": "태그",
}

SORTS = ("nearest", "furthest", "arbitrary", "random")


def _normalize_type(t):
    return t.split(":")[-1]  # minecraft:zombie -> zombie


class EntitySelector:
    def __init__(self, base, single, limit, sort, predicates):
        self.base = base            # 's','a','e','p','r' 또는 None(=이름)
        self.single = single
        self.limit = limit
        self.sort = sort
        self.predicates = predicates  # entity -> bool 리스트
        self.player_name = None       # @ 없이 이름으로 지정한 경우

    def resolve(self, source):
        if self.player_name is not None:
            return [e for e in source.all_entities()
                    if e.name == self.player_name]

        if self.base == "s":
            pool = [source.entity] if source.entity is not None else []
        elif self.base in ("a", "p", "r"):
            pool = [e for e in source.all_entities() if _normalize_type(e.type) == "player"]
        else:  # 'e'
            pool = source.all_entities()

        # 거리 술어는 source.position이 필요하므로 여기서 바인딩
        for p in self.predicates:
            if isinstance(p, _DistancePredicate):
                p.bind(source)
        pool = [e for e in pool if all(p(e) for p in self.predicates)]

        sort = self.sort
        if self.base == "p":
            sort = "nearest"
        if sort == "nearest":
            pool.sort(key=lambda e: e.distance_to(source.position))
        elif sort == "furthest":
            pool.sort(key=lambda e: e.distance_to(source.position), reverse=True)
        # arbitrary/random: 입력 순서 유지 (데모에서는 결정적으로)

        limit = self.limit
        if limit is not None:
            pool = pool[:limit]
        return pool

    def __repr__(self):
        if self.player_name:
            return f"<selector name={self.player_name}>"
        return f"<selector @{self.base} limit={self.limit}>"


def _parse_range(text):
    """'2..8', '..10', '3..', '5' -> (min, max) (None 허용)."""
    if ".." in text:
        lo, hi = text.split("..", 1)
        lo = float(lo) if lo else None
        hi = float(hi) if hi else None
        return lo, hi
    v = float(text)
    return v, v


class EntityArgumentType:
    def __init__(self, single=False, players_only=False):
        self.single = single
        self.players_only = players_only

    # 팩토리 -------------------------------------------------------------
    @staticmethod
    def entity():
        return EntityArgumentType(single=True, players_only=False)

    @staticmethod
    def entities():
        return EntityArgumentType(single=False, players_only=False)

    @staticmethod
    def player():
        return EntityArgumentType(single=True, players_only=True)

    @staticmethod
    def players():
        return EntityArgumentType(single=False, players_only=True)

    # 파싱 ---------------------------------------------------------------
    def parse(self, reader, source=None):
        start = reader.cursor
        if reader.can_read() and reader.peek() == "@":
            selector = self._parse_selector(reader)
        else:
            name = reader.read_unquoted_string()
            if not name:
                raise CommandSyntaxError("Expected selector or name", reader)
            selector = EntitySelector(None, True, None, "arbitrary", [])
            selector.player_name = name

        if self.single and selector.limit not in (None, 1) and not selector.single:
            reader.cursor = start
            raise CommandSyntaxError("Only one entity is allowed, but the provided selector allows more", reader)
        return selector

    def _parse_selector(self, reader):
        reader.skip()  # '@'
        if not reader.can_read():
            raise CommandSyntaxError("Missing selector type", reader)
        ch = reader.read()
        if ch not in SELECTORS:
            raise CommandSyntaxError(f"Unknown selector type '@{ch}'", reader)
        _, single = SELECTORS[ch]
        limit = 1 if ch in ("p", "r", "s") else None
        sort = "nearest" if ch == "p" else ("random" if ch == "r" else "arbitrary")
        predicates = []

        if reader.can_read() and reader.peek() == "[":
            reader.skip()
            limit, sort = self._parse_options(reader, predicates, limit, sort)

        return EntitySelector(ch, single, limit, sort, predicates)

    def _parse_options(self, reader, predicates, limit, sort):
        reader.skip_whitespace()
        if reader.can_read() and reader.peek() == "]":
            reader.skip()
            return limit, sort
        while True:
            reader.skip_whitespace()
            key = reader.read_unquoted_string()
            if not key:
                raise CommandSyntaxError("Expected option key", reader)
            reader.skip_whitespace()
            reader.expect("=")
            reader.skip_whitespace()
            negate = False
            if reader.can_read() and reader.peek() == "!":
                negate = True
                reader.skip()
            value = reader.read_unquoted_string()

            limit, sort = self._apply_option(reader, key, value, negate, predicates, limit, sort)

            reader.skip_whitespace()
            if not reader.can_read():
                raise CommandSyntaxError("Expected ',' or ']'", reader)
            sep = reader.read()
            if sep == "]":
                break
            if sep != ",":
                reader.cursor -= 1
                raise CommandSyntaxError("Expected ',' or ']'", reader)
        return limit, sort

    def _apply_option(self, reader, key, value, negate, predicates, limit, sort):
        if key == "type":
            want = _normalize_type(value)
            if negate:
                predicates.append(lambda e, w=want: _normalize_type(e.type) != w)
            else:
                predicates.append(lambda e, w=want: _normalize_type(e.type) == w)
        elif key == "name":
            if negate:
                predicates.append(lambda e, v=value: e.name != v)
            else:
                predicates.append(lambda e, v=value: e.name == v)
        elif key == "tag":
            predicates.append(lambda e, v=value: v in getattr(e, "tags", []))
        elif key == "distance":
            try:
                lo, hi = _parse_range(value)
            except ValueError:
                raise CommandSyntaxError(f"Invalid distance range '{value}'", reader)
            # 거리 술어는 source가 필요해서 resolve 시점에 바인딩된다
            predicates.append(_DistancePredicate(lo, hi))
        elif key == "limit":
            try:
                limit = int(value)
            except ValueError:
                raise CommandSyntaxError(f"Invalid limit '{value}'", reader)
        elif key == "sort":
            if value not in SORTS:
                raise CommandSyntaxError(f"Invalid sort '{value}'", reader)
            sort = value
        else:
            raise CommandSyntaxError(f"Unknown option '{key}'", reader)
        return limit, sort

    # 자동완성 -----------------------------------------------------------
    def list_suggestions(self, context, builder):
        remaining = builder.remaining
        # 아직 아무것도 / '@' 까지만 입력한 경우 → 셀렉터 종류 제안
        if remaining == "" or remaining == "@":
            for letter, (desc, _single) in SELECTORS.items():
                if self.players_only and letter == "e":
                    continue
                builder.suggest("@" + letter, desc)
            return builder.build()

        # '[' 이후 옵션 키 제안
        bracket = remaining.rfind("[")
        comma = remaining.rfind(",")
        if bracket != -1 and "]" not in remaining[bracket:]:
            sep = max(bracket, comma)
            typed = remaining[sep + 1:]
            if "=" not in typed:
                key_start = builder.start + sep + 1
                sub = builder.create_offset(key_start)
                for opt, desc in OPTIONS.items():
                    if opt.startswith(sub.remaining_lower):
                        sub.suggest(opt, desc)
                return sub.build()
        return Suggestions.empty()

    def get_examples(self):
        return ["Player", "0123", "@e", "@a", "@e[type=zombie]"]


class _DistancePredicate:
    """거리 필터. source.position을 알아야 하므로 resolve 시 호출되도록 callable."""

    def __init__(self, lo, hi):
        self.lo = lo
        self.hi = hi
        self._source = None

    def bind(self, source):
        self._source = source
        return self

    def __call__(self, entity):
        # source는 resolve 단계에서 EntitySelector가 주입한다
        if self._source is None:
            return True
        d = entity.distance_to(self._source.position)
        if self.lo is not None and d < self.lo:
            return False
        if self.hi is not None and d > self.hi:
            return False
        return True
