"""
페르소나 템플릿 시스템
- 기본 페르소나 템플릿 제공
- 커스텀 페르소나 저장/로드
"""

import json
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from v.settings import get_app_data_path


@dataclass
class Persona:
    """토론 참가자 페르소나"""
    id: str
    name: str
    icon: str
    system_prompt: str
    color: str
    is_builtin: bool = False
    model: str = ""  # 비어있으면 기본 모델 사용

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Persona":
        return cls(**data)


# 기본 페르소나 템플릿
DEFAULT_PERSONAS = [
    Persona(
        id="critic",
        name="비평가",
        icon="🔍",
        system_prompt="당신은 날카로운 비평가입니다. 모든 주장의 약점과 논리적 허점을 찾아내세요. 건설적이지만 철저하게 분석하세요.",
        color="#e74c3c",
        is_builtin=True
    ),
    Persona(
        id="optimist",
        name="낙관론자",
        icon="☀️",
        system_prompt="당신은 긍정적인 관점을 가진 낙관론자입니다. 모든 아이디어에서 가능성과 기회를 찾고, 희망적인 시각으로 토론에 참여하세요.",
        color="#2ecc71",
        is_builtin=True
    ),
    Persona(
        id="analyst",
        name="분석가",
        icon="📊",
        system_prompt="당신은 객관적인 분석가입니다. 감정을 배제하고 데이터와 증거를 바탕으로 냉정하게 분석하세요. 숫자와 사실에 집중하세요.",
        color="#3498db",
        is_builtin=True
    ),
    Persona(
        id="creative",
        name="창작자",
        icon="💡",
        system_prompt="당신은 창의적인 사고를 하는 혁신가입니다. 기존의 틀을 깨는 새로운 아이디어와 독창적인 해결책을 제시하세요. 엉뚱한 발상도 환영합니다.",
        color="#9b59b6",
        is_builtin=True
    ),
    Persona(
        id="devil_advocate",
        name="악마의 변호인",
        icon="😈",
        system_prompt="당신은 반대 입장을 대변하는 악마의 변호인입니다. 다수 의견에 도전하고, 숨겨진 위험과 간과된 문제점을 지적하세요.",
        color="#e67e22",
        is_builtin=True
    ),
    Persona(
        id="pragmatist",
        name="현실주의자",
        icon="🎯",
        system_prompt="당신은 실용적인 현실주의자입니다. 이론보다 실행 가능성에 집중하고, 구체적인 실천 방안과 현실적 제약을 고려하세요.",
        color="#1abc9c",
        is_builtin=True
    ),
    Persona(
        id="philosopher",
        name="철학자",
        icon="🤔",
        system_prompt="당신은 깊이 사고하는 철학자입니다. 근본적인 질문을 던지고, 윤리적 측면과 장기적 영향을 고려하세요. 'Why?'를 끊임없이 물으세요.",
        color="#34495e",
        is_builtin=True
    ),
]

# 기본 중재자 템플릿
DEFAULT_MODERATOR = Persona(
    id="moderator",
    name="중재자",
    icon="⚖️",
    system_prompt="당신은 토론의 중재자입니다. 각 참가자의 의견을 공정하게 요약하고, 합의점과 이견을 명확히 정리하세요. 토론이 건설적으로 진행되도록 방향을 제시하세요.",
    color="#f39c12",
    is_builtin=True
)


class PersonaManager:
    """페르소나 관리자 - 템플릿 로드/저장"""

    def __init__(self):
        self._custom_personas: list[Persona] = []
        self._load_custom_personas()

    def _get_personas_file(self) -> Path:
        """커스텀 페르소나 저장 경로"""
        return get_app_data_path() / "round_table_personas.json"

    def _load_custom_personas(self):
        """커스텀 페르소나 로드"""
        path = self._get_personas_file()
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._custom_personas = [Persona.from_dict(p) for p in data]
            except Exception as e:
                print(f"[PersonaManager] Failed to load custom personas: {e}")
                self._custom_personas = []

    def _save_custom_personas(self):
        """커스텀 페르소나 저장"""
        path = self._get_personas_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump([p.to_dict() for p in self._custom_personas], f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[PersonaManager] Failed to save custom personas: {e}")

    def get_all_personas(self) -> list[Persona]:
        """모든 페르소나 반환 (기본 + 커스텀)"""
        return DEFAULT_PERSONAS + self._custom_personas

    def get_builtin_personas(self) -> list[Persona]:
        """기본 페르소나만 반환"""
        return DEFAULT_PERSONAS.copy()

    def get_custom_personas(self) -> list[Persona]:
        """커스텀 페르소나만 반환"""
        return self._custom_personas.copy()

    def get_persona_by_id(self, persona_id: str) -> Optional[Persona]:
        """ID로 페르소나 찾기"""
        for p in self.get_all_personas():
            if p.id == persona_id:
                return p
        return None

    def get_default_moderator(self) -> Persona:
        """기본 중재자 반환"""
        return DEFAULT_MODERATOR

    def add_custom_persona(self, persona: Persona) -> Persona:
        """커스텀 페르소나 추가"""
        # ID가 없으면 생성
        if not persona.id:
            persona.id = f"custom_{uuid.uuid4().hex[:8]}"
        persona.is_builtin = False
        self._custom_personas.append(persona)
        self._save_custom_personas()
        return persona

    def update_custom_persona(self, persona: Persona):
        """커스텀 페르소나 업데이트"""
        for i, p in enumerate(self._custom_personas):
            if p.id == persona.id:
                self._custom_personas[i] = persona
                self._save_custom_personas()
                return

    def delete_custom_persona(self, persona_id: str) -> bool:
        """커스텀 페르소나 삭제"""
        for i, p in enumerate(self._custom_personas):
            if p.id == persona_id:
                del self._custom_personas[i]
                self._save_custom_personas()
                return True
        return False

    def create_persona(self, name: str, icon: str, system_prompt: str, color: str, model: str = "") -> Persona:
        """새 페르소나 생성 및 저장"""
        persona = Persona(
            id=f"custom_{uuid.uuid4().hex[:8]}",
            name=name,
            icon=icon,
            system_prompt=system_prompt,
            color=color,
            is_builtin=False,
            model=model
        )
        return self.add_custom_persona(persona)


# 싱글톤 인스턴스
_persona_manager: Optional[PersonaManager] = None

def get_persona_manager() -> PersonaManager:
    """PersonaManager 싱글톤 반환"""
    global _persona_manager
    if _persona_manager is None:
        _persona_manager = PersonaManager()
    return _persona_manager
