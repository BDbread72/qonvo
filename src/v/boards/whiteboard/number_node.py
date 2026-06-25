"""숫자(Number) 노드 — int/float 값을 보관/표시/전파하는 데이터 노드.

마인크래프트 nixie 카운터처럼 값 하나를 들고 있다가, 입력으로 받거나
⚡ 증가 신호 펄스마다 step 만큼 올리고(=카운터), ⚡ 리셋으로 초기값으로 돌린다.

포트(팩토리에서 생성):
  입력  설정(NUMBER)   ─ 값을 직접 세팅(on_number_input)
        ⚡ 증가         ─ 펄스마다 +step
        ⚡ 리셋         ─ 초기값으로
  출력  값(NUMBER)
        ⚡ 변경         ─ 값이 바뀔 때 펄스(다음 노드 구동)
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from .base_node import BaseNode
from .widgets import DraggableHeader, ResizeHandle


def coerce_number(v, default=0):
    """문자열/숫자 → int(정수면) 또는 float. 실패 시 default."""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        f = float(v)
        return int(f) if f.is_integer() else f
    if v is None:
        return default
    s = str(v).strip()
    if not s:
        return default
    try:
        f = float(s)
    except (ValueError, TypeError):
        return default
    return int(f) if f.is_integer() else f


class NumberNodeWidget(QWidget, BaseNode):

    TITLE_NAME = "Number"
    _COLOR = "#a0d911"  # Theme.PORT_NUMBER 와 동일

    def __init__(self, node_id, on_value_changed=None, on_modified=None):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.init_base_node(node_id=node_id, on_modified=on_modified)

        self.on_value_changed = on_value_changed  # 플러그인 콜백(node_id)
        self._value = 0
        self._initial = 0
        self._step = 1

        self.setMinimumSize(160, 120)
        self.resize(200, 150)
        self.setStyleSheet(f"""
            NumberNodeWidget {{
                background-color: #1a1f0a;
                border: 2px solid {self._COLOR};
                border-radius: 8px;
            }}
        """)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.header = DraggableHeader(self)
        self.header.setFixedHeight(26)
        self.header.setStyleSheet(f"""
            DraggableHeader {{
                background-color: #0f1305;
                border-top-left-radius: 6px; border-top-right-radius: 6px;
                border-bottom: 1px solid {self._COLOR}44;
            }}
        """)
        h_layout = QHBoxLayout(self.header)
        h_layout.setContentsMargins(10, 0, 10, 0)
        # 이름은 공용 편집형 이름표(node_title)가 헤더에 표시 — 타입+#id 배지 제거.
        h_layout.addStretch()
        layout.addWidget(self.header)

        body = QWidget()
        body.setStyleSheet("background: transparent;")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(10, 8, 10, 8)
        body_layout.setSpacing(6)

        # 큰 값 입력칸(직접 편집 가능)
        self.value_edit = QLineEdit("0")
        self.value_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = QFont("Consolas", 26)
        f.setBold(True)
        self.value_edit.setFont(f)
        self.value_edit.setStyleSheet(f"""
            QLineEdit {{
                background: #0a0d03; color: {self._COLOR};
                border: 1px solid {self._COLOR}55; border-radius: 5px;
                padding: 4px;
            }}
        """)
        self.value_edit.editingFinished.connect(self._commit_value)
        body_layout.addWidget(self.value_edit, 1)

        # step + 리셋
        ctrl = QHBoxLayout()
        ctrl.setSpacing(4)
        step_lbl = QLabel("step")
        step_lbl.setStyleSheet("color: #7a8a4a; font-size: 10px; border: none; background: transparent;")
        ctrl.addWidget(step_lbl)
        self.step_edit = QLineEdit("1")
        self.step_edit.setFixedWidth(46)
        self.step_edit.setStyleSheet(f"""
            QLineEdit {{
                background: #0a0d03; color: #c0d860;
                border: 1px solid {self._COLOR}33; border-radius: 3px;
                font-size: 11px; padding: 1px 3px;
            }}
        """)
        self.step_edit.editingFinished.connect(self._commit_step)
        ctrl.addWidget(self.step_edit)
        ctrl.addStretch()
        self.btn_reset = QPushButton("리셋")
        self.btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reset.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: #7a8a4a;
                border: 1px solid {self._COLOR}33; border-radius: 3px;
                font-size: 10px; padding: 2px 8px;
            }}
            QPushButton:hover {{ color: {self._COLOR}; border-color: {self._COLOR}; }}
        """)
        self.btn_reset.clicked.connect(lambda: self.reset_value())
        ctrl.addWidget(self.btn_reset)
        body_layout.addLayout(ctrl)

        layout.addWidget(body, 1)

        self.resize_handle = ResizeHandle(self)
        self.resize_handle.move(self.width() - 16, self.height() - 16)
        self.resize_handle.raise_()

    # ── 값 표시/세팅 ────────────────────────────────────────────────
    def _fmt(self, v):
        v = coerce_number(v)
        return str(v)

    def _refresh_display(self):
        if not self.value_edit.hasFocus():
            self.value_edit.blockSignals(True)
            self.value_edit.setText(self._fmt(self._value))
            self.value_edit.blockSignals(False)

    def set_value(self, v, propagate=True, mark=True):
        """값을 세팅하고(정수/실수 정규화) 표시 갱신 + 전파."""
        new_val = coerce_number(v, self._value)
        self._value = new_val
        self._refresh_display()
        if mark:
            self.notify_modified()
        if propagate and callable(self.on_value_changed):
            self.on_value_changed(self.node_id)

    def reset_value(self):
        self.set_value(self._initial)

    def _commit_value(self):
        # 사용자가 직접 입력한 값을 초기값으로도 채택(리셋 기준).
        self._initial = coerce_number(self.value_edit.text(), self._value)
        self.set_value(self.value_edit.text())

    def _commit_step(self):
        self._step = coerce_number(self.step_edit.text(), 1)
        self.step_edit.blockSignals(True)
        self.step_edit.setText(self._fmt(self._step))
        self.step_edit.blockSignals(False)
        self.notify_modified()

    # ── 입력 핸들러 ────────────────────────────────────────────────
    def on_number_input(self, port_name, value):
        """NUMBER 입력 포트('설정')로 값이 들어옴."""
        self.set_value(value)

    def on_named_signal(self, name, input_data=None, powered=True):
        """⚡ 증가 / ⚡ 리셋 펄스 처리(상승엣지에서만 동작)."""
        if not powered:
            return
        if "리셋" in name:
            self.reset_value()
        elif "증가" in name:
            self.set_value(self._value + self._step)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'resize_handle') and self.resize_handle:
            self.resize_handle.move(self.width() - 16, self.height() - 16)

    # ── 직렬화 ─────────────────────────────────────────────────────
    def get_data(self):
        x, y = 0, 0
        if self.proxy is not None and hasattr(self.proxy, "pos"):
            pos = self.proxy.pos()
            x, y = pos.x(), pos.y()
        return {
            "type": "number_node",
            "node_id": self.node_id,
            "x": x, "y": y,
            "width": self.width(), "height": self.height(),
            "value": self._value,
            "initial": self._initial,
            "step": self._step,
        }

    @staticmethod
    def from_data(data, on_value_changed=None, on_modified=None):
        widget = NumberNodeWidget(
            node_id=data.get("node_id"),
            on_value_changed=on_value_changed, on_modified=on_modified)
        w, h = data.get("width"), data.get("height")
        if w and h:
            widget.resize(int(w), int(h))
        widget._initial = coerce_number(data.get("initial", 0))
        widget._step = coerce_number(data.get("step", 1), 1)
        widget.step_edit.blockSignals(True)
        widget.step_edit.setText(widget._fmt(widget._step))
        widget.step_edit.blockSignals(False)
        # 값 복원은 전파 없이(로드 중 다른 노드가 아직 없을 수 있음)
        widget.set_value(data.get("value", 0), propagate=False, mark=False)
        return widget
