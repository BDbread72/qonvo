"""
보드 플러그인 기본 인터페이스
모든 보드 타입은 이 클래스를 상속
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from PyQt6.QtWidgets import QWidget, QGraphicsScene


class BoardPlugin(ABC):
    """보드 플러그인 기본 클래스"""

    # 메타데이터 (서브클래스에서 오버라이드)
    NAME: str = "Base Board"
    DESCRIPTION: str = "기본 보드"
    VERSION: str = "1.0"
    ICON: str = "📋"  # 이모지 또는 아이콘 경로

    def __init__(self, app):
        """
        app: v.app.App 인스턴스
        """
        self.app = app
        self.scene: Optional[QGraphicsScene] = None
        self.on_modified = None  # 데이터 변경 시 콜백 (UI에서 설정)

    @abstractmethod
    def create_view(self) -> QWidget:
        """
        보드의 메인 뷰 위젯 생성
        Returns: QWidget (보통 QGraphicsView)
        """
        pass

    @abstractmethod
    def collect_data(self) -> Dict[str, Any]:
        """
        현재 보드 상태를 데이터로 수집 (저장용)
        Returns: 직렬화 가능한 dict
        """
        pass

    @abstractmethod
    def restore_data(self, data: Dict[str, Any]) -> None:
        """
        저장된 데이터로 보드 상태 복원
        """
        pass

    def get_scene(self) -> Optional[QGraphicsScene]:
        """씬 반환 (있는 경우)"""
        return self.scene

    @classmethod
    def get_info(cls) -> Dict[str, str]:
        """플러그인 정보 반환"""
        return {
            "name": cls.NAME,
            "description": cls.DESCRIPTION,
            "version": cls.VERSION,
            "icon": cls.ICON,
        }
