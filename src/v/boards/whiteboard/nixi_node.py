from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
)
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush, QRadialGradient, QFont,
)

from .base_node import BaseNode
from .widgets import DraggableHeader, ResizeHandle


class NixiTube(QWidget):
    W = 72
    H = 102
    GAP = 6

    def __init__(self, char=" ", parent=None):
        super().__init__(parent)
        self.setFixedSize(self.W, self.H)
        self._char = char
        self._lit = bool(char and char.strip())

    def set_char(self, char):
        self._char = char
        self._lit = bool(char and char.strip())
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        w, h = self.width(), self.height()
        tube_rect = QRectF(1.5, 1.5, w - 3, h - 3)

        bg = QRadialGradient(w * 0.5, h * 0.52, min(w, h) * 0.52)
        if self._lit:
            bg.setColorAt(0.00, QColor(22, 8, 0))
            bg.setColorAt(0.65, QColor(9, 3, 0))
            bg.setColorAt(1.00, QColor(2, 0, 0))
        else:
            bg.setColorAt(0.00, QColor(8, 3, 0))
            bg.setColorAt(1.00, QColor(2, 0, 0))

        painter.setBrush(QBrush(bg))
        border_col = QColor(110, 55, 8) if self._lit else QColor(38, 16, 0)
        painter.setPen(QPen(border_col, 2))
        painter.drawRoundedRect(tube_rect, 13, 13)

        painter.setPen(QPen(QColor(55, 25, 0, 50), 1.2))
        for row in range(8, h - 5, 9):
            for col in range(7, w - 4, 9):
                painter.drawPoint(col, row)

        font = QFont("Arial", 44)
        font.setWeight(QFont.Weight.Bold)
        painter.setFont(font)

        char = self._char if self._char else " "
        text_rect = QRectF(0, 0, w, h)

        if self._lit:
            for alpha, spread in [(55, 4), (35, 3), (20, 2), (12, 1)]:
                painter.setPen(QPen(QColor(255, 130, 0, alpha)))
                for dx in [-spread, 0, spread]:
                    for dy in [-spread, 0, spread]:
                        painter.drawText(QRectF(dx, dy, w, h), Qt.AlignmentFlag.AlignCenter, char)
            painter.setPen(QColor(255, 165, 48))
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, char)
        else:
            painter.setPen(QColor(28, 10, 0))
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, char)

        painter.end()


class NixiNodeWidget(QWidget, BaseNode):

    def __init__(self, node_id, on_modified=None):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.init_base_node(node_id=node_id, on_modified=on_modified)

        self._current_value = ""
        self._grid_layout = None

        self.setMinimumSize(160, 160)
        self.resize(420, 230)
        self.setStyleSheet("""
            NixiNodeWidget {
                background-color: #030100;
                border: 2px solid #1c0c00;
                border-radius: 8px;
            }
        """)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.header = DraggableHeader(self)
        self.header.setFixedHeight(26)
        self.header.setStyleSheet("""
            DraggableHeader {
                background-color: #0c0500;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                border-bottom: 1px solid #250f00;
            }
        """)
        h_layout = QHBoxLayout(self.header)
        h_layout.setContentsMargins(10, 0, 10, 0)

        title = QLabel(f"NIXI #{self.node_id}")
        title.setStyleSheet(
            "color: #3a1800; font-family: 'Courier New', monospace;"
            " font-size: 8px; font-weight: bold; letter-spacing: 2px; border: none;"
        )
        h_layout.addWidget(title)
        h_layout.addStretch()
        layout.addWidget(self.header)

        body = QWidget()
        body.setStyleSheet("background: transparent;")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(10, 10, 10, 10)
        body_layout.setSpacing(NixiTube.GAP)
        body_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        self._tubes_container = QWidget()
        self._tubes_container.setStyleSheet("background: transparent;")
        self._grid_layout = QVBoxLayout(self._tubes_container)
        self._grid_layout.setContentsMargins(0, 0, 0, 0)
        self._grid_layout.setSpacing(NixiTube.GAP)
        self._grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        self._render_tubes("")
        body_layout.addWidget(self._tubes_container)
        layout.addWidget(body, 1)

        self.resize_handle = ResizeHandle(self)
        self.resize_handle.move(self.width() - 16, self.height() - 16)
        self.resize_handle.raise_()

    def _cols(self):
        available = self.width() - 20
        step = NixiTube.W + NixiTube.GAP
        return max(1, available // step)

    def _render_tubes(self, text):
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        display = text if text else " "
        cols = self._cols()

        for row_start in range(0, len(display), cols):
            chunk = display[row_start:row_start + cols]
            row_w = QWidget()
            row_w.setStyleSheet("background: transparent;")
            row_layout = QHBoxLayout(row_w)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(NixiTube.GAP)
            row_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
            for ch in chunk:
                row_layout.addWidget(NixiTube(ch))
            row_layout.addStretch()
            self._grid_layout.addWidget(row_w)

    def _update_display(self, value):
        self._current_value = value
        text = str(value) if value is not None else ""
        self._render_tubes(text)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'resize_handle') and self.resize_handle:
            self.resize_handle.move(self.width() - 16, self.height() - 16)

    def on_signal_input(self, input_data=None):
        self.sent = False
        value = input_data or self._collect_input_data() or ""
        self._update_display(value)
        self.notify_modified()

    def get_data(self):
        x, y = 0, 0
        if self.proxy is not None and hasattr(self.proxy, "pos"):
            pos = self.proxy.pos()
            x, y = pos.x(), pos.y()
        return {
            "type": "nixi_node",
            "node_id": self.node_id,
            "x": x,
            "y": y,
            "width": self.width(),
            "height": self.height(),
            "current_value": self._current_value,
        }

    @staticmethod
    def from_data(data, on_modified=None):
        widget = NixiNodeWidget(node_id=data.get("node_id"), on_modified=on_modified)
        w = data.get("width")
        h = data.get("height")
        if w and h:
            widget.resize(int(w), int(h))
        value = data.get("current_value", "")
        if value:
            widget._update_display(value)
        return widget
