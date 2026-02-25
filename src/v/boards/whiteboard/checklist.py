"""
체크리스트 위젯
- 체크박스 + 텍스트 항목 목록
- 드래그/리사이즈 지원
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QCheckBox, QScrollArea, QFrame,
)
from PyQt6.QtCore import Qt

from v.theme import Theme
from .base_node import BaseNode
from .widgets import DraggableHeader, ResizeHandle


class ChecklistWidget(QWidget, BaseNode):
    """체크리스트 위젯"""

    def __init__(self, title="", items=None, on_modified=None):
        super().__init__()
        self.init_base_node(node_id=None, on_modified=on_modified)

        self.setMinimumSize(160, 120)
        self.resize(220, 180)
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {Theme.BG_TERTIARY};
                border: 1px solid {Theme.BG_HOVER};
                border-radius: 6px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 헤더 (드래그)
        self.header = DraggableHeader(self)
        self.header.setFixedHeight(30)
        self.header.setStyleSheet(f"""
            QFrame {{
                background-color: {Theme.BG_INPUT};
                border: none; border-bottom: 1px solid {Theme.GRID_LINE};
                border-top-left-radius: 6px; border-top-right-radius: 6px;
            }}
        """)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(8, 2, 4, 2)

        self.title_edit = QLineEdit(title or "Checklist")
        self.title_edit.setStyleSheet(f"""
            QLineEdit {{
                background: transparent; border: none;
                color: {Theme.TEXT_PRIMARY}; font-size: 12px; font-weight: bold;
                padding: 0;
            }}
        """)
        self.title_edit.textChanged.connect(self._on_change)
        header_layout.addWidget(self.title_edit)

        layout.addWidget(self.header)

        # 항목 리스트 (스크롤)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{
                width: 6px; background: transparent;
            }}
            QScrollBar::handle:vertical {{
                background: {Theme.BG_HOVER}; border-radius: 3px; min-height: 20px;
            }}
        """)

        self.list_container = QWidget()
        self.list_container.setStyleSheet("QWidget { background: transparent; border: none; }")
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(6, 4, 6, 4)
        self.list_layout.setSpacing(2)
        self.list_layout.addStretch()

        scroll.setWidget(self.list_container)
        layout.addWidget(scroll)

        # 추가 버튼
        btn_add = QPushButton("+")
        btn_add.setFixedHeight(24)
        btn_add.setStyleSheet(f"""
            QPushButton {{
                background: {Theme.BG_INPUT}; color: {Theme.TEXT_SECONDARY};
                border: none; border-top: 1px solid {Theme.GRID_LINE};
                border-bottom-left-radius: 6px; border-bottom-right-radius: 6px;
                font-size: 16px; font-weight: bold;
            }}
            QPushButton:hover {{ background: {Theme.BG_SECONDARY}; color: {Theme.TEXT_PRIMARY}; }}
        """)
        btn_add.clicked.connect(lambda: self._add_item("", False, focus=True))
        layout.addWidget(btn_add)

        # 리사이즈 핸들
        self.resize_handle = ResizeHandle(self)
        self.resize_handle.move(self.width() - 16, self.height() - 16)

        # 기존 항목 복원
        for item_data in (items or []):
            text = item_data.get("text", "") if isinstance(item_data, dict) else str(item_data)
            checked = item_data.get("checked", False) if isinstance(item_data, dict) else False
            self._add_item(text, checked)

    def _add_item(self, text="", checked=False, focus=False):
        row = QFrame()
        row.setStyleSheet("QFrame { background: transparent; border: none; }")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(2, 1, 2, 1)
        row_layout.setSpacing(4)

        cb = QCheckBox()
        cb.setChecked(checked)
        cb.setStyleSheet(f"""
            QCheckBox::indicator {{
                width: 16px; height: 16px;
                border: 1px solid {Theme.TEXT_DISABLED}; border-radius: 3px;
                background: {Theme.BG_SECONDARY};
            }}
            QCheckBox::indicator:checked {{
                background: {Theme.ACCENT_SUCCESS}; border-color: {Theme.ACCENT_SUCCESS};
            }}
        """)
        cb.stateChanged.connect(self._on_change)
        row_layout.addWidget(cb)

        line = QLineEdit(text)
        line.setStyleSheet(f"""
            QLineEdit {{
                background: transparent; border: none;
                color: {Theme.TEXT_PRIMARY}; font-size: 12px; padding: 2px;
            }}
        """)
        line.textChanged.connect(self._on_change)
        row_layout.addWidget(line, 1)

        btn_remove = QPushButton("✕")
        btn_remove.setFixedSize(18, 18)
        btn_remove.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {Theme.TEXT_DISABLED};
                border: none; font-size: 11px;
            }}
            QPushButton:hover {{ color: {Theme.ACCENT_DANGER}; }}
        """)
        btn_remove.clicked.connect(lambda: self._remove_item(row))
        row_layout.addWidget(btn_remove)

        # stretch 앞에 삽입
        idx = self.list_layout.count() - 1
        self.list_layout.insertWidget(idx, row)

        if focus:
            line.setFocus()

        self._on_change()

    def _remove_item(self, row):
        row.setParent(None)
        row.deleteLater()
        self._on_change()

    def _on_change(self):
        if self.on_modified:
            self.on_modified()

    def get_items(self):
        """모든 항목 데이터 반환"""
        items = []
        for i in range(self.list_layout.count() - 1):  # -1 for stretch
            widget = self.list_layout.itemAt(i).widget()
            if widget:
                layout = widget.layout()
                if layout and layout.count() >= 2:
                    cb = layout.itemAt(0).widget()
                    line = layout.itemAt(1).widget()
                    if isinstance(cb, QCheckBox) and isinstance(line, QLineEdit):
                        items.append({
                            "text": line.text(),
                            "checked": cb.isChecked(),
                        })
        return items

    def get_data(self):
        return {
            "type": "checklist",
            "node_id": self.node_id,
            "x": self.proxy.pos().x() if self.proxy else 0,
            "y": self.proxy.pos().y() if self.proxy else 0,
            "width": self.width(),
            "height": self.height(),
            "title": self.title_edit.text(),
            "items": self.get_items(),
        }
