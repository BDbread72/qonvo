"""인자 타입 모음. Brigadier의 Arguments 헬퍼처럼 팩토리 함수를 노출한다."""

from .types import (
    ArgumentType,
    integer,
    float_arg,
    boolean,
    word,
    string,
    greedy_string,
)
from .entity import EntityArgumentType
from .coordinates import Vec3ArgumentType, Vec2ArgumentType, Coordinates
from .resource import ResourceArgumentType


def entity():
    return EntityArgumentType.entity()


def entities():
    return EntityArgumentType.entities()


def player():
    return EntityArgumentType.player()


def players():
    return EntityArgumentType.players()


def vec3():
    return Vec3ArgumentType.vec3()


def vec2():
    return Vec2ArgumentType()


def resource(ids, namespace="minecraft", label="id"):
    return ResourceArgumentType(ids, namespace, label)


__all__ = [
    "ArgumentType",
    "integer",
    "float_arg",
    "boolean",
    "word",
    "string",
    "greedy_string",
    "entity",
    "entities",
    "player",
    "players",
    "vec3",
    "vec2",
    "resource",
    "Coordinates",
    "EntityArgumentType",
    "Vec3ArgumentType",
    "Vec2ArgumentType",
    "ResourceArgumentType",
]
