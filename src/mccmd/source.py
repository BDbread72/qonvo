"""명령어 실행 주체(CommandSource)와 가상 월드 모델.

마인크래프트에서 명령어는 '누가(@s)', '어디서(위치)' 실행하느냐에 따라 결과가
달라진다. 셀렉터(@e, @p…)와 상대좌표(~ ^)를 풀려면 이 정보가 필요하다.
라이브러리 데모용으로 아주 단순한 엔티티/월드 모델을 둔다.
"""

import math


class Entity:
    def __init__(self, name, type, pos=(0.0, 0.0, 0.0), rotation=(0.0, 0.0)):
        self.name = name
        self.type = type
        self.pos = tuple(float(c) for c in pos)
        self.rotation = rotation  # (yaw, pitch)

    def distance_to(self, pos):
        dx = self.pos[0] - pos[0]
        dy = self.pos[1] - pos[1]
        dz = self.pos[2] - pos[2]
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    def __repr__(self):
        return f"{self.name}"


class World:
    def __init__(self, entities=None):
        self.entities = list(entities) if entities else []

    def add(self, entity):
        self.entities.append(entity)


class CommandSource:
    """명령어 실행 컨텍스트: 월드 + 실행 엔티티(@s) + 위치."""

    def __init__(self, world, entity=None, position=None, name="Server", on_output=None):
        self.world = world
        self.entity = entity
        if position is not None:
            self.position = tuple(float(c) for c in position)
        elif entity is not None:
            self.position = entity.pos
        else:
            self.position = (0.0, 0.0, 0.0)
        self.name = name
        self._on_output = on_output

    # 파생 source (execute 체이닝에서 사용) ------------------------------
    def with_entity(self, entity):
        return CommandSource(self.world, entity, entity.pos, entity.name, self._on_output)

    def with_position(self, position):
        return CommandSource(self.world, self.entity, position, self.name, self._on_output)

    # 출력 -----------------------------------------------------------------
    def set_output(self, callback):
        """메시지 콜백 교체. 서비스가 실행마다 출력을 가로채는 데 쓴다."""
        self._on_output = callback
        return self

    def send_message(self, text):
        if self._on_output is not None:
            self._on_output(text)

    def all_entities(self):
        return list(self.world.entities)
