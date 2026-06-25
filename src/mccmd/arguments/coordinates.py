"""좌표 인자 — 절대(1.5), 상대(~), 로컬(^).

    /tp @s 100 64 -200      절대
    /tp @s ~ ~10 ~          현재 위치 기준 상대 (y만 +10)
    /tp @s ^ ^ ^5           바라보는 방향 기준 로컬 5칸 앞

Vec3ArgumentType.parse는 Coordinates 객체를 돌려주고, resolve(source)로 실제
(x, y, z)를 계산한다.
"""

import math

from ..errors import CommandSyntaxError
from ..suggestions import Suggestions

RELATIVE = "~"
LOCAL = "^"


class _Coord:
    """한 축의 좌표값: 절대 / 상대(~) / 로컬(^)."""

    def __init__(self, kind, value):
        self.kind = kind      # 'abs' | 'rel' | 'local'
        self.value = value


class Coordinates:
    def __init__(self, x, y, z):
        self.x = x
        self.y = y
        self.z = z

    def resolve(self, source):
        if self.x.kind == "local":
            return self._resolve_local(source)
        base = source.position
        coords = []
        for axis, comp in zip(base, (self.x, self.y, self.z)):
            if comp.kind == "rel":
                coords.append(axis + comp.value)
            else:  # abs
                coords.append(comp.value)
        return tuple(coords)

    def _resolve_local(self, source):
        """^ 로컬 좌표: 시선 방향 기준. yaw/pitch가 있으면 정확히, 없으면 +z 정면 가정."""
        bx, by, bz = source.position
        left, up, forward = self.x.value, self.y.value, self.z.value
        yaw = pitch = 0.0
        if source.entity is not None and getattr(source.entity, "rotation", None):
            yaw, pitch = source.entity.rotation
        yaw_r = math.radians(yaw)
        pitch_r = math.radians(pitch)
        # 정면 벡터
        fx = -math.sin(yaw_r) * math.cos(pitch_r)
        fy = -math.sin(pitch_r)
        fz = math.cos(yaw_r) * math.cos(pitch_r)
        # 왼쪽 벡터 (수평)
        lx = math.cos(yaw_r)
        lz = math.sin(yaw_r)
        x = bx + forward * fx + left * lx
        y = by + forward * fy + up
        z = bz + forward * fz + left * lz
        return (x, y, z)


def _read_coord(reader, allow_local):
    if not reader.can_read():
        raise CommandSyntaxError("Expected coordinate", reader)
    c = reader.peek()
    if c == LOCAL:
        reader.skip()
        value = _read_optional_double(reader)
        return _Coord("local", value)
    if c == RELATIVE:
        reader.skip()
        value = _read_optional_double(reader)
        return _Coord("rel", value)
    return _Coord("abs", reader.read_float())


def _read_optional_double(reader):
    if not reader.can_read() or reader.peek() == " ":
        return 0.0
    return reader.read_float()


class Vec3ArgumentType:
    def __init__(self, center_integers=False):
        self.center_integers = center_integers

    @staticmethod
    def vec3():
        return Vec3ArgumentType()

    def parse(self, reader, source=None):
        start = reader.cursor
        x = _read_coord(reader, True)
        if not reader.can_read() or reader.read() != " ":
            reader.cursor = start
            raise CommandSyntaxError("Expected a 3-axis coordinate (x y z)", reader)
        local = x.kind == "local"
        y = _read_coord(reader, local)
        if not reader.can_read() or reader.read() != " ":
            reader.cursor = start
            raise CommandSyntaxError("Expected a 3-axis coordinate (x y z)", reader)
        z = _read_coord(reader, local)

        kinds = {x.kind == "local", y.kind == "local", z.kind == "local"}
        if len(kinds) > 1:
            reader.cursor = start
            raise CommandSyntaxError("Cannot mix ^ (local) with other coordinate types", reader)
        return Coordinates(x, y, z)

    def list_suggestions(self, context, builder):
        if builder.remaining == "":
            builder.suggest("~")
            builder.suggest("~ ~ ~")
            builder.suggest("^ ^ ^")
        return builder.build()

    def get_examples(self):
        return ["0 0 0", "~ ~ ~", "^ ^ ^", "~0.5 ~1 ~-5"]


class Vec2ArgumentType:
    """2축 좌표 (예: rotation yaw pitch, 또는 x z)."""

    def parse(self, reader, source=None):
        start = reader.cursor
        x = _read_coord(reader, False)
        if not reader.can_read() or reader.read() != " ":
            reader.cursor = start
            raise CommandSyntaxError("Expected a 2-axis coordinate", reader)
        y = _read_coord(reader, False)
        return (x, y)

    def list_suggestions(self, context, builder):
        return Suggestions.empty()
