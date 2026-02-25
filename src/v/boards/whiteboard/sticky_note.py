"""
?????? (Sticky Note) ???
- ??? + ??? + ??? ???
- ??????????? ????"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QLineEdit, QPushButton
)
from PyQt6.QtCore import Qt

from .base_node import BaseNode
from .widgets import DraggableHeader, ResizeHandle
from v.theme import Theme


STICKY_COLORS = {
    "yellow": ("#fef3cd", "#d4a017"),
    "green":  ("#d4edda", "#2d8659"),
    "blue":   ("#cce5ff", "#3a7bd5"),
    "pink":   ("#f8d7da", "#c0392b"),
    "orange": ("#ffe0b2", "#e67e22"),
    "purple": ("#e1bee7", "#8e44ad"),
}


class StickyNoteWidget(QWidget, BaseNode):
    """?????? ???"""

    def __init__(self, title="", body="", color="yellow", on_modified=None):
        super().__init__()

        # Initialize BaseNode
        self.init_base_node(node_id=None, on_modified=on_modified)

        # Sticky note specific attributes
        self.color = color if color in STICKY_COLORS else "yellow"

        self.setMinimumSize(140, 100)
        self.resize(200, 150)
        self._apply_style()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ??? (?????+ ??? ???)
        self.header = DraggableHeader(self)
        self.header.setFixedHeight(30)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(8, 2, 4, 2)
        header_layout.setSpacing(3)

        # ???
        self.title_edit = QLineEdit(title)
        self.title_edit.setPlaceholderText("???")
        self.title_edit.setStyleSheet("""
            QLineEdit {
                background: transparent; border: none;
                color: #333; font-size: 12px; font-weight: bold;
                padding: 0;
            }
        """)
        self.title_edit.textChanged.connect(self._on_change)
        header_layout.addWidget(self.title_edit, 1)

        # ??? ??? ??? (??? ??? ???)
        self._palette_btn = QPushButton()
        self._palette_btn.setFixedSize(20, 20)
        self._palette_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_palette_btn()
        self._palette_btn.clicked.connect(self._toggle_palette)
        header_layout.addWidget(self._palette_btn)

        layout.addWidget(self.header)

        # ??? ???????(?????
        self._color_bar = QWidget()
        self._color_bar.setFixedHeight(28)
        self._color_bar.hide()
        bar_layout = QHBoxLayout(self._color_bar)
        bar_layout.setContentsMargins(6, 2, 6, 2)
        bar_layout.setSpacing(0)
        bar_layout.addStretch()
        for cname, (cbg, cacc) in STICKY_COLORS.items():
            dot = QPushButton()
            dot.setFixedSize(20, 20)
            dot.setCursor(Qt.CursorShape.PointingHandCursor)
            dot.setStyleSheet(f"""
                QPushButton {{
                    background: {cacc}; border: 2px solid {cacc};
                    border-radius: 10px;
                }}
                QPushButton:hover {{
                    border: 2px solid #fff;
                }}
            """)
            dot.clicked.connect(lambda _, c=cname: self._pick_color(c))
            bar_layout.addWidget(dot)
            bar_layout.addSpacing(3)
        bar_layout.addStretch()
        self._color_bar.setStyleSheet("background: transparent; border: none;")
        layout.addWidget(self._color_bar)

        # ???
        self.body_edit = QTextEdit()
        self.body_edit.setPlaceholderText("???...")
        self.body_edit.setPlainText(body)
        self.body_edit.setStyleSheet("""
            QTextEdit {
                background: transparent; border: none;
                color: #333; font-size: 13px; padding: 6px 8px;
            }
        """)
        self.body_edit.textChanged.connect(self._on_change)
        layout.addWidget(self.body_edit)

        # ?????? ???
        self.resize_handle = ResizeHandle(self)
        self.resize_handle.move(self.width() - 16, self.height() - 16)

    def _apply_style(self):
        bg, accent = STICKY_COLORS[self.color]
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {bg};
                border: 1px solid {accent};
                border-radius: 6px;
            }}
        """)

    def _update_palette_btn(self):
        bg, accent = STICKY_COLORS[self.color]
        self._palette_btn.setStyleSheet(f"""
            QPushButton {{
                background: {accent}; border: none;
                border-radius: 10px;
            }}
            QPushButton:hover {{ border: 1px solid #333; }}
        """)

    def _toggle_palette(self):
        self._color_bar.setVisible(not self._color_bar.isVisible())

    def _pick_color(self, color):
        self._set_color(color)
        self._color_bar.hide()

    def _set_color(self, color):
        # 잘못된 색상 키가 들어오면 기본 색상 사용
        if color not in STICKY_COLORS:
            color = "yellow"
        self.color = color
        bg, accent = STICKY_COLORS[color]
        self._apply_style()
        self.header.setStyleSheet(f"""
            QFrame {{
                background-color: {accent}22;
                border: none; border-bottom: 1px solid {accent}44;
                border-top-left-radius: 6px; border-top-right-radius: 6px;
            }}
        """)
        self._update_palette_btn()
        self._on_change()

    def _on_change(self):
        if self.on_modified:
            self.on_modified()

    def get_data(self):
        return {
            "type": "sticky_note",
            "node_id": self.node_id,
            "x": self.proxy.pos().x() if self.proxy else 0,
            "y": self.proxy.pos().y() if self.proxy else 0,
            "width": self.width(),
            "height": self.height(),
            "title": self.title_edit.text(),
            "body": self.body_edit.toPlainText(),
            "color": self.color,
        }
