"""
Button node widget.
- Emits boolean-style signal on click
- Behaves like a simple trigger node
"""
from PyQt6.QtWidgets import QWidget, QPushButton, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont

from v.theme import Theme
from .base_node import BaseNode


class ButtonNodeWidget(QWidget, BaseNode):
    """Button node that emits a signal when clicked."""

    signal_triggered = pyqtSignal()

    def __init__(self, node_id, on_signal=None, on_modified=None):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

        # Initialize BaseNode
        self.init_base_node(node_id=node_id, on_modified=on_modified)

        # Button node specific attributes
        self.on_signal = on_signal
        self.click_count = 0
        self._drag_start_pos = None
        self.input_data = None  # 입력 포트로부터 수집된 데이터

        self.setMinimumSize(120, 80)
        self.resize(120, 80)
        self.setStyleSheet(
            """
            ButtonNodeWidget {
                background-color: #2c2c2c;
                border: 2px solid #555555;
                border-radius: 8px;
            }
            """
        )

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        title = QLabel("Signal Button")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont("Segoe UI", 9, QFont.Weight.Bold)
        title.setFont(font)
        title.setStyleSheet("color: #f1c40f;")
        layout.addWidget(title)

        self.button = QPushButton("click")
        self.button.setStyleSheet(
            """
            QPushButton {
                background-color: #f1c40f;
                color: #000000;
                border: none;
                border-radius: 4px;
                padding: 8px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #f39c12;
            }
            QPushButton:pressed {
                background-color: #d68910;
            }
            """
        )
        self.button.clicked.connect(self._on_click)
        layout.addWidget(self.button)

        self.counter_label = QLabel("click: 0")
        self.counter_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.counter_label.setStyleSheet("color: #aaaaaa; font-size: 8pt;")
        layout.addWidget(self.counter_label)

        self.setLayout(layout)

    def on_signal_input(self, input_data=None):
        """입력 포트로 신호를 받았을 때 - 데이터를 수집하여 저장만 함"""
        self.input_data = self._collect_input_data()

    def _on_click(self):
        self.click_count += 1
        self.counter_label.setText(f"click: {self.click_count}")

        self.button.setText("signal")

        # 클릭 시 입력 포트로부터 데이터 수집
        self.input_data = self._collect_input_data()

        self.signal_triggered.emit()
        if self.on_signal:
            self.on_signal(self.node_id)

        from PyQt6.QtCore import QTimer

        QTimer.singleShot(200, lambda: self.button.setText("click"))

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
        widget.counter_label.setText(f"click: {widget.click_count}")
        return widget
