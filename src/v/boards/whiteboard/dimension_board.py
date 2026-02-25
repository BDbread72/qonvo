"""
DimensionBoardWindow - Dimension 내부 보드를 보여주는 창
완전한 화이트보드 기능을 가진 독립적인 창
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel, QPushButton,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QKeySequence, QShortcut

from v.theme import Theme
from v.app import App
from .plugin import WhiteBoardPlugin
from .dimension_item import DimensionItem


class DimensionBoardWindow(QDialog):
    """Dimension 내부 보드를 보여주는 창

    완전한 WhiteBoardPlugin을 내장하여 메인 보드와 동일한 기능 제공.
    창을 닫을 때 자동으로 데이터를 DimensionItem에 저장.
    """

    def __init__(self, dimension_item: DimensionItem, parent_plugin, parent=None):
        super().__init__(parent)
        self.dimension_item = dimension_item
        self.parent_plugin = parent_plugin

        # 창 설정
        self.setWindowTitle(f"Dimension: {dimension_item._title}")
        self.setMinimumSize(800, 600)
        self.resize(1200, 800)
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowCloseButtonHint |
            Qt.WindowType.WindowMaximizeButtonHint |
            Qt.WindowType.WindowMinimizeButtonHint
        )

        # 독립적인 App 인스턴스
        self.app = App()

        # 독립적인 WhiteBoardPlugin
        self.plugin = WhiteBoardPlugin(self.app)
        self.plugin._parent_plugin = parent_plugin
        self.plugin._parent_dimension_item = dimension_item
        # 부모 보드의 _board_name 전달 (이미지 temp 경로 해석에 필수)
        self.plugin._board_name = getattr(parent_plugin, '_board_name', None)

        # UI 구성
        self._setup_ui()

        # 저장된 데이터 복원
        board_data = dimension_item.get_board_data()
        if board_data and board_data.get("nodes") is not None:
            self.plugin.restore_data(board_data)

    def _setup_ui(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #1a1a1a;
            }
            QLineEdit {
                background-color: #2a2a2a;
                border: 1px solid #3a3a3a;
                border-radius: 4px;
                padding: 6px 10px;
                color: #ddd;
                font-size: 14px;
            }
            QLineEdit:focus {
                border-color: #8e44ad;
            }
            QLabel {
                color: #888;
            }
            QPushButton {
                background-color: #3a3a3a;
                border: none;
                border-radius: 4px;
                padding: 6px 12px;
                color: #ddd;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 헤더
        header = QHBoxLayout()
        header.setContentsMargins(12, 8, 12, 8)
        header.setSpacing(10)

        # 제목 라벨
        title_label = QLabel("Title:")
        title_label.setFont(QFont("Segoe UI", 10))
        header.addWidget(title_label)

        # 제목 편집
        self.title_edit = QLineEdit(self.dimension_item._title)
        self.title_edit.setMaximumWidth(300)
        self.title_edit.textChanged.connect(self._on_title_changed)
        header.addWidget(self.title_edit)

        header.addStretch()

        # 정보 표시
        self.info_label = QLabel()
        self._update_info()
        header.addWidget(self.info_label)

        layout.addLayout(header)

        # 구분선
        line = QLabel()
        line.setFixedHeight(1)
        line.setStyleSheet("background-color: #333;")
        layout.addWidget(line)

        # 메인: WhiteboardView
        self.view = self.plugin.create_view()
        layout.addWidget(self.view)

        # Ctrl+S 단축키 추가
        self.save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        self.save_shortcut.activated.connect(self._save_to_dimension)

    def _on_title_changed(self, text: str):
        """제목 변경"""
        self.setWindowTitle(f"Dimension: {text}")

    def _update_info(self):
        """정보 업데이트"""
        board_data = self.dimension_item.get_board_data()
        node_count = len(board_data.get("nodes", []))
        edge_count = len(board_data.get("edges", []))
        self.info_label.setText(f"Nodes: {node_count} | Edges: {edge_count}")

    def _save_to_dimension(self):
        """현재 보드 데이터를 Dimension에 저장 (Ctrl+S)"""
        # 현재 보드 데이터 수집
        board_data = self.plugin.collect_data()

        # DimensionItem에 저장
        self.dimension_item.set_board_data(board_data)
        self.dimension_item.set_title(self.title_edit.text())

        # 부모 플러그인에 수정 알림 (메인 보드 저장 트리거)
        if self.parent_plugin:
            self.parent_plugin._notify_modified()

        # 정보 업데이트
        self._update_info()

    def closeEvent(self, event):
        """창 닫을 때 데이터 저장"""
        # 저장 (Ctrl+S와 동일한 로직)
        self._save_to_dimension()

        # 창 참조 정리
        if self.parent_plugin:
            if hasattr(self.parent_plugin, '_dimension_windows'):
                if self in self.parent_plugin._dimension_windows:
                    self.parent_plugin._dimension_windows.remove(self)

        super().closeEvent(event)
