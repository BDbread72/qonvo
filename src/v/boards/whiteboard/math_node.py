"""연산(Math) 노드 — 두 숫자 입력 A,B 를 받아 사칙연산/비교 결과를 낸다.

연산자 드롭다운으로 Math(+ − × ÷ % ^ min max)와 Compare(> < = ≥ ≤ ≠)를 한 노드에서 처리.

포트(팩토리에서 생성):
  입력  A(NUMBER), B(NUMBER)
  출력  결과(NUMBER)   ─ 산술 결과(비교 연산이면 1/0)
        ⚡ 참(BOOLEAN) ─ 비교 결과(산술이면 result != 0)
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from .base_node import BaseNode
from .widgets import DraggableHeader, ResizeHandle
from .number_node import coerce_number


# (key, 표시기호, 비교연산?)
_OPERATORS = [
    ("add", "+", False),
    ("sub", "−", False),
    ("mul", "×", False),
    ("div", "÷", False),
    ("mod", "%", False),
    ("pow", "^", False),
    ("min", "min", False),
    ("max", "max", False),
    ("gt", ">", True),
    ("lt", "<", True),
    ("ge", "≥", True),
    ("le", "≤", True),
    ("eq", "=", True),
    ("ne", "≠", True),
]
_OP_SYMBOL = {k: s for k, s, _ in _OPERATORS}
_OP_IS_CMP = {k: c for k, _, c in _OPERATORS}


def _apply_op(op, a, b):
    """(result_number, bool_flag) 반환."""
    try:
        if op == "add":
            r = a + b
        elif op == "sub":
            r = a - b
        elif op == "mul":
            r = a * b
        elif op == "div":
            r = a / b if b != 0 else 0
        elif op == "mod":
            r = a % b if b != 0 else 0
        elif op == "pow":
            r = a ** b
        elif op == "min":
            r = min(a, b)
        elif op == "max":
            r = max(a, b)
        elif op == "gt":
            return (1 if a > b else 0), a > b
        elif op == "lt":
            return (1 if a < b else 0), a < b
        elif op == "ge":
            return (1 if a >= b else 0), a >= b
        elif op == "le":
            return (1 if a <= b else 0), a <= b
        elif op == "eq":
            return (1 if a == b else 0), a == b
        elif op == "ne":
            return (1 if a != b else 0), a != b
        else:
            r = 0
    except (ValueError, OverflowError, ZeroDivisionError, TypeError):
        return 0, False
    r = coerce_number(r)
    return r, (r != 0)


class MathNodeWidget(QWidget, BaseNode):

    _COLOR = "#a0d911"

    def __init__(self, node_id, on_value_changed=None, on_modified=None):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.init_base_node(node_id=node_id, on_modified=on_modified)

        self.on_value_changed = on_value_changed
        self._a = 0
        self._b = 0
        self._op = "add"
        self._result = 0
        self._bool = False

        self.setMinimumSize(150, 120)
        self.resize(190, 150)
        self.setStyleSheet(f"""
            MathNodeWidget {{
                background-color: #161a0c;
                border: 2px solid #6f9c1c;
                border-radius: 8px;
            }}
        """)
        self._setup_ui()
        self._evaluate(propagate=False)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.header = DraggableHeader(self)
        self.header.setFixedHeight(26)
        self.header.setStyleSheet("""
            DraggableHeader {
                background-color: #0d1005;
                border-top-left-radius: 6px; border-top-right-radius: 6px;
                border-bottom: 1px solid #6f9c1c44;
            }
        """)
        h_layout = QHBoxLayout(self.header)
        h_layout.setContentsMargins(10, 0, 10, 0)
        title = QLabel(f"MATH #{self.node_id}")
        title.setStyleSheet(
            "color: #8fbf2c; font-family: 'Consolas', monospace;"
            " font-size: 9px; font-weight: bold; letter-spacing: 1px; border: none; background: transparent;")
        h_layout.addWidget(title)
        h_layout.addStretch()
        layout.addWidget(self.header)

        body = QWidget()
        body.setStyleSheet("background: transparent;")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(10, 8, 10, 8)
        body_layout.setSpacing(6)

        # A op B
        ab_row = QHBoxLayout()
        ab_row.setSpacing(4)
        self.lbl_a = QLabel("0")
        self.lbl_b = QLabel("0")
        for lbl in (self.lbl_a, self.lbl_b):
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setFont(QFont("Consolas", 13, QFont.Weight.Bold))
            lbl.setStyleSheet("color: #c0d860; border: none; background: transparent;")
        self.op_combo = QComboBox()
        for key, sym, _ in _OPERATORS:
            self.op_combo.addItem(sym, key)
        self.op_combo.setFixedWidth(52)
        self.op_combo.setStyleSheet(f"""
            QComboBox {{
                background: #0a0d03; color: {self._COLOR};
                border: 1px solid {self._COLOR}55; border-radius: 4px;
                font-size: 14px; font-weight: bold; padding: 1px 4px;
            }}
            QComboBox::drop-down {{ border: none; width: 12px; }}
            QComboBox::down-arrow {{ image: none; }}
            QComboBox QAbstractItemView {{
                background: #0a0d03; color: {self._COLOR};
                selection-background-color: {self._COLOR}44;
                border: 1px solid {self._COLOR};
            }}
        """)
        self.op_combo.currentIndexChanged.connect(self._on_op_changed)
        ab_row.addWidget(self.lbl_a, 1)
        ab_row.addWidget(self.op_combo)
        ab_row.addWidget(self.lbl_b, 1)
        body_layout.addLayout(ab_row)

        # = result
        res_row = QHBoxLayout()
        eq = QLabel("=")
        eq.setStyleSheet(f"color: {self._COLOR}; font-size: 16px; font-weight: bold; border: none; background: transparent;")
        res_row.addWidget(eq)
        self.lbl_result = QLabel("0")
        self.lbl_result.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_result.setFont(QFont("Consolas", 20, QFont.Weight.Bold))
        self.lbl_result.setStyleSheet(f"color: {self._COLOR}; border: none; background: transparent;")
        res_row.addWidget(self.lbl_result, 1)
        body_layout.addLayout(res_row, 1)

        layout.addWidget(body, 1)

        self.resize_handle = ResizeHandle(self)
        self.resize_handle.move(self.width() - 16, self.height() - 16)
        self.resize_handle.raise_()

    def _on_op_changed(self):
        self._op = self.op_combo.currentData()
        self._evaluate()

    def _evaluate(self, propagate=True):
        a = coerce_number(self._a)
        b = coerce_number(self._b)
        self._result, self._bool = _apply_op(self._op, a, b)
        self.lbl_a.setText(str(a))
        self.lbl_b.setText(str(b))
        self.lbl_result.setText(str(self._result))
        if _OP_IS_CMP.get(self._op):
            self.lbl_result.setText("참" if self._bool else "거짓")
        if propagate:
            self.notify_modified()
            if callable(self.on_value_changed):
                self.on_value_changed(self.node_id)

    def on_number_input(self, port_name, value):
        if port_name == "B":
            self._b = coerce_number(value)
        else:
            self._a = coerce_number(value)
        self._evaluate()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'resize_handle') and self.resize_handle:
            self.resize_handle.move(self.width() - 16, self.height() - 16)

    def get_data(self):
        x, y = 0, 0
        if self.proxy is not None and hasattr(self.proxy, "pos"):
            pos = self.proxy.pos()
            x, y = pos.x(), pos.y()
        return {
            "type": "math_node",
            "node_id": self.node_id,
            "x": x, "y": y,
            "width": self.width(), "height": self.height(),
            "op": self._op,
            "a": self._a, "b": self._b,
        }

    @staticmethod
    def from_data(data, on_value_changed=None, on_modified=None):
        widget = MathNodeWidget(
            node_id=data.get("node_id"),
            on_value_changed=on_value_changed, on_modified=on_modified)
        w, h = data.get("width"), data.get("height")
        if w and h:
            widget.resize(int(w), int(h))
        widget._a = coerce_number(data.get("a", 0))
        widget._b = coerce_number(data.get("b", 0))
        op = data.get("op", "add")
        idx = widget.op_combo.findData(op)
        if idx >= 0:
            widget.op_combo.blockSignals(True)
            widget.op_combo.setCurrentIndex(idx)
            widget.op_combo.blockSignals(False)
            widget._op = op
        widget._evaluate(propagate=False)
        return widget
