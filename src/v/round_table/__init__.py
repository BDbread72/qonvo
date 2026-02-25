"""
Round Table - 다중 AI 토론 시스템
여러 AI 모델이 순차적으로 토론하는 독립적인 전체화면 인터페이스
"""

from .view import RoundTableView
from .personas import PersonaManager, Persona

__all__ = ["RoundTableView", "PersonaManager", "Persona"]
