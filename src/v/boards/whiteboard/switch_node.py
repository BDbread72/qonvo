from PyQt6.QtWidgets import QWidget, QPushButton, QVBoxLayout, QLabel, QHBoxLayout
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from .base_node import BaseNode


class SwitchNodeWidget(QWidget, BaseNode):
    """ON/OFF 토글이 있는 신호 게이트 노드 위젯.

    - ON 상태에서만 입력 신호를 수집해 출력으로 전달
    - OFF 상태에서는 신호를 차단
    - 버튼 노드와 유사한 패턴(드래그, 직렬화) 적용
    """

    def __init__(self, node_id, on_signal=None, on_modified=None):
        """스위치 노드 위젯을 초기화한다."""
        super().__init__()
        # 배경 스타일 적용 및 마우스 이벤트 수신 설정
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

        # BaseNode 초기화(노드 ID, 수정 콜백 등)
        self.init_base_node(node_id=node_id, on_modified=on_modified)

        self.on_signal = on_signal  # 신호 출력 콜백
        self.is_on = True  # 기본 상태는 ON
        self._drag_start_pos = None  # 드래그 시작 좌표

        self.setMinimumSize(120, 70)
        self.resize(120, 70)
        self._apply_style()
        self._init_ui()

    def _init_ui(self):
        """UI 요소를 구성하고 레이아웃을 초기화한다."""
        layout = QVBoxLayout()
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        title = QLabel("Switch")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        title.setStyleSheet("color: #2ecc71; border: none; background: transparent;")
        layout.addWidget(title)

        # ON/OFF 토글 버튼
        self.toggle_btn = QPushButton("ON")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setChecked(True)
        self.toggle_btn.setFixedHeight(28)
        self._apply_toggle_style()
        self.toggle_btn.clicked.connect(self._on_toggle)
        layout.addWidget(self.toggle_btn)

        self.setLayout(layout)

    def _apply_style(self):
        """현재 ON/OFF 상태에 맞춰 외곽선 색상을 적용한다."""
        color = "#2ecc71" if self.is_on else "#555"
        self.setStyleSheet(f"""
            SwitchNodeWidget {{
                background-color: #2c2c2c;
                border: 2px solid {color};
                border-radius: 8px;
            }}
        """)

    def _apply_toggle_style(self):
        """현재 ON/OFF 상태에 맞춰 버튼 스타일을 적용한다."""
        if self.is_on:
            self.toggle_btn.setStyleSheet("""
                QPushButton {
                    background-color: #2ecc71; color: #000;
                    border: none; border-radius: 4px;
                    font-weight: bold; font-size: 12px;
                }
                QPushButton:hover { background-color: #27ae60; }
            """)
        else:
            self.toggle_btn.setStyleSheet("""
                QPushButton {
                    background-color: #555; color: #999;
                    border: none; border-radius: 4px;
                    font-weight: bold; font-size: 12px;
                }
                QPushButton:hover { background-color: #666; }
            """)

    def _on_toggle(self):
        """토글 상태 변경 시 UI를 갱신하고 수정 콜백을 호출한다."""
        self.is_on = self.toggle_btn.isChecked()
        self.toggle_btn.setText("ON" if self.is_on else "OFF")
        self._apply_style()
        self._apply_toggle_style()
        if self.on_modified:
            self.on_modified()

    def on_signal_input(self, input_data=None):
        """ON일 때만 신호를 통과시킨다."""
        if not self.is_on:
            return
        if self.on_signal:
            self._pass_data = input_data
            self.on_signal(self.node_id)

    def mousePressEvent(self, event):
        """마우스 클릭 처리: 버튼 클릭과 드래그 시작을 구분한다."""
        if self.toggle_btn.geometry().contains(event.pos()):
            super().mousePressEvent(event)
        else:
            # 노드 드래그 시작
            self._drag_start_pos = event.pos()
            event.accept()

    def mouseMoveEvent(self, event):
        """마우스 드래그로 노드를 이동한다."""
        if self._drag_start_pos and self.proxy:
            delta = event.pos() - self._drag_start_pos
            self.proxy.moveBy(delta.x(), delta.y())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """드래그 종료 후 상태를 초기화한다."""
        self._drag_start_pos = None
        super().mouseReleaseEvent(event)

    def to_dict(self):
        """노드 상태를 직렬화(dict)한다."""
        return {
            "type": "switch",
            "node_id": self.node_id,
            "x": self.proxy.pos().x() if self.proxy else 0,
            "y": self.proxy.pos().y() if self.proxy else 0,
            "width": self.width(),
            "height": self.height(),
            "is_on": self.is_on,
        }

    @staticmethod
    def from_dict(data, on_signal=None, on_modified=None):
        """직렬화된 데이터로부터 노드 위젯을 복원한다."""
        widget = SwitchNodeWidget(
            data["node_id"],
            on_signal=on_signal,
            on_modified=on_modified,
        )
        widget.is_on = data.get("is_on", True)
        widget.toggle_btn.setChecked(widget.is_on)
        widget.toggle_btn.setText("ON" if widget.is_on else "OFF")
        widget._apply_style()
        widget._apply_toggle_style()
        return widget
