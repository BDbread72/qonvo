from PyQt6.QtWidgets import QWidget, QPushButton, QVBoxLayout, QLabel, QInputDialog
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont

from .base_node import BaseNode


class ButtonNodeWidget(QWidget, BaseNode):

    signal_triggered = pyqtSignal()

    def __init__(self, node_id, on_signal=None, on_modified=None):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.init_base_node(node_id=node_id, on_modified=on_modified)

        self.on_signal = on_signal
        self.click_count = 0
        self._drag_start_pos = None
        self.input_data = None

        self.setMinimumSize(100, 60)
        self.resize(100, 60)
        self.setStyleSheet("""
            ButtonNodeWidget {
                background-color: #2c2c2c;
                border: 2px solid #f1c40f;
                border-radius: 8px;
            }
        """)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)

        self._label = "Button"
        self.title_label = QLabel(self._label)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self.title_label.setStyleSheet("color: #f1c40f; border: none; background: transparent;")
        layout.addWidget(self.title_label)

        self.button = QPushButton("Push")
        self.button.setFixedHeight(24)
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.setStyleSheet("""
            QPushButton {
                background-color: #f1c40f; color: #000;
                border: none; border-radius: 4px;
                font-weight: bold; font-size: 11px;
            }
            QPushButton:hover { background-color: #f39c12; }
            QPushButton:pressed { background-color: #d68910; }
        """)
        self.button.clicked.connect(self._on_click)
        layout.addWidget(self.button)

        self.setLayout(layout)

    def on_signal_input(self, input_data=None):
        self.input_data = self._collect_input_data()

    def _on_click(self):
        self.click_count += 1
        self.input_data = self._collect_input_data()

        self.signal_triggered.emit()
        if self.on_signal:
            self.on_signal(self.node_id)

        if self.on_modified:
            self.on_modified()

    def mouseDoubleClickEvent(self, event):
        parent_window = self.window() if self.window() != self else None
        text, ok = QInputDialog.getText(parent_window, "Label", "", text=self._label)
        if ok and text:
            self._label = text
            self.title_label.setText(text)
            if self.on_modified:
                self.on_modified()

    def mousePressEvent(self, event):
        if self.button.geometry().contains(event.pos()):
            super().mousePressEvent(event)
        else:
            self._drag_start_pos = event.pos()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_start_pos and self.proxy:
            delta = event.pos() - self._drag_start_pos
            self.proxy.moveBy(delta.x(), delta.y())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_start_pos = None
        super().mouseReleaseEvent(event)

    def to_dict(self):
        return {
            "type": "button",
            "node_id": self.node_id,
            "x": self.proxy.pos().x() if self.proxy else 0,
            "y": self.proxy.pos().y() if self.proxy else 0,
            "width": self.width(),
            "height": self.height(),
            "click_count": self.click_count,
            "input_data": self.input_data,
            "label": self._label,
        }

    @staticmethod
    def from_dict(data, on_signal=None, on_modified=None):
        widget = ButtonNodeWidget(
            data["node_id"],
            on_signal=on_signal,
            on_modified=on_modified,
        )
        widget.click_count = data.get("click_count", 0)
        widget.input_data = data.get("input_data")
        label = data.get("label", "Button")
        widget._label = label
        widget.title_label.setText(label)
        return widget
