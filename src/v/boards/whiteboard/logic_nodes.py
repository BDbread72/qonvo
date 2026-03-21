"""로직 게이트 노드 위젯 모음.
- _LogicNodeBase: 공통 베이스(스타일, 드래그, 직렬화)
- LatchNodeWidget: 신호 저장 노드 (펄스)
- AndGateWidget: AND 게이트 (상태)
- OrGateWidget: OR 게이트 (상태)
"""

from PyQt6.QtWidgets import QWidget, QPushButton, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from .base_node import BaseNode


_SMALL_SIZE = (100, 60)


class _LogicNodeBase(QWidget, BaseNode):
    """로직 게이트 노드의 공통 베이스 클래스."""

    _title = ""
    _color = "#888"

    def __init__(self, node_id, on_signal=None, on_modified=None):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.init_base_node(node_id=node_id, on_modified=on_modified)
        self.on_signal = on_signal
        self._drag_start_pos = None
        self._pass_data = None
        self._pass_powered = False
        self._output_on = False
        self.setMinimumSize(*_SMALL_SIZE)
        self.resize(*_SMALL_SIZE)
        self._apply_style()

    def _apply_style(self):
        self.setStyleSheet(f"""
            {type(self).__name__} {{
                background-color: #2c2c2c;
                border: 2px solid {self._color};
                border-radius: 8px;
            }}
        """)

    def _make_title_label(self):
        label = QLabel(self._title)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        label.setStyleSheet(f"color: {self._color}; border: none; background: transparent;")
        return label

    def _make_status_label(self):
        label = QLabel("--")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("color: #888; font-size: 9px; border: none; background: transparent;")
        return label

    def _set_output(self, powered: bool, data=None):
        """출력 상태가 실제로 바뀔 때만 콜백 호출."""
        if self._output_on == powered:
            return
        self._output_on = powered
        self._pass_powered = powered
        self._pass_data = data
        if self.on_signal:
            self.on_signal(self.node_id)

    def mousePressEvent(self, event):
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

    def _base_dict(self, node_type):
        return {
            "type": node_type,
            "node_id": self.node_id,
            "x": self.proxy.pos().x() if self.proxy else 0,
            "y": self.proxy.pos().y() if self.proxy else 0,
            "width": self.width(),
            "height": self.height(),
        }


class LatchNodeWidget(_LogicNodeBase):
    """SR 래치: Set 신호 → ON 유지, Reset 신호 → OFF. 상태 기반."""

    _title = "Latch"
    _color = "#e67e22"

    def __init__(self, node_id, on_signal=None, on_modified=None):
        super().__init__(node_id, on_signal, on_modified)
        self._latched = False
        self._data = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)
        layout.addWidget(self._make_title_label())
        self.status_label = self._make_status_label()
        self.status_label.setText("OFF")
        layout.addWidget(self.status_label)
        self.setLayout(layout)

    def on_signal_a(self, input_data=None, powered=True):
        """Set 입력. Reset(⚡ B)이 활성이면 무시."""
        self._a_powered = powered
        self._a_data = input_data
        self._evaluate()

    def on_signal_b(self, input_data=None, powered=True):
        """Reset 입력. Reset 우선."""
        self._b_powered = powered
        self._evaluate()

    def _evaluate(self):
        b = getattr(self, '_b_powered', False)
        a = getattr(self, '_a_powered', False)
        if b:
            if self._latched:
                self._latched = False
                self._data = None
                self._update_visual()
                self._set_output(False)
        elif a and not self._latched:
            self._latched = True
            self._data = getattr(self, '_a_data', None)
            self._update_visual()
            self._set_output(True, self._data)

    def _update_visual(self):
        if self._latched:
            self.status_label.setText("ON")
            self.status_label.setStyleSheet("color: #e67e22; font-size: 9px; border: none; background: transparent;")
        else:
            self.status_label.setText("OFF")
            self.status_label.setStyleSheet("color: #888; font-size: 9px; border: none; background: transparent;")

    def to_dict(self):
        d = self._base_dict("latch")
        d["latched"] = self._latched
        d["data"] = self._data
        return d

    @staticmethod
    def from_dict(data, on_signal=None, on_modified=None):
        w = LatchNodeWidget(data["node_id"], on_signal, on_modified)
        w._latched = data.get("latched", False)
        w._data = data.get("data")
        w._update_visual()
        w._output_on = w._latched
        return w


class AndGateWidget(_LogicNodeBase):
    """A와 B가 모두 ON일 때 출력 ON (상태 기반)."""

    _title = "AND"
    _color = "#3498db"

    def __init__(self, node_id, on_signal=None, on_modified=None):
        super().__init__(node_id, on_signal, on_modified)
        self._a_on = False
        self._b_on = False
        self._a_data = None
        self._b_data = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)
        layout.addWidget(self._make_title_label())
        self.status_label = self._make_status_label()
        layout.addWidget(self.status_label)
        self.setLayout(layout)

    def on_signal_a(self, input_data=None, powered=True):
        self._a_on = powered
        self._a_data = input_data if powered else None
        self._evaluate()

    def on_signal_b(self, input_data=None, powered=True):
        self._b_on = powered
        self._b_data = input_data if powered else None
        self._evaluate()

    def _evaluate(self):
        out = self._a_on and self._b_on
        a = "A" if self._a_on else "-"
        b = "B" if self._b_on else "-"
        self.status_label.setText(f"{a} & {b}")
        parts = [p for p in (self._a_data, self._b_data) if p]
        data = "\n".join(parts) if parts else ""
        self._set_output(out, data)

    def to_dict(self):
        d = self._base_dict("and_gate")
        d["a_on"] = self._a_on
        d["b_on"] = self._b_on
        d["a_data"] = self._a_data
        d["b_data"] = self._b_data
        return d

    @staticmethod
    def from_dict(data, on_signal=None, on_modified=None):
        w = AndGateWidget(data["node_id"], on_signal, on_modified)
        w._a_on = data.get("a_on", False)
        w._b_on = data.get("b_on", False)
        w._a_data = data.get("a_data")
        w._b_data = data.get("b_data")
        a = "A" if w._a_on else "-"
        b = "B" if w._b_on else "-"
        w.status_label.setText(f"{a} & {b}")
        return w


class OrGateWidget(_LogicNodeBase):
    """A 또는 B 중 하나라도 ON이면 출력 ON (상태 기반)."""

    _title = "OR"
    _color = "#2ecc71"

    def __init__(self, node_id, on_signal=None, on_modified=None):
        super().__init__(node_id, on_signal, on_modified)
        self._a_on = False
        self._b_on = False
        self._a_data = None
        self._b_data = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)
        layout.addWidget(self._make_title_label())
        self.status_label = self._make_status_label()
        layout.addWidget(self.status_label)
        self.setLayout(layout)

    def on_signal_a(self, input_data=None, powered=True):
        self._a_on = powered
        self._a_data = input_data if powered else None
        self._evaluate()

    def on_signal_b(self, input_data=None, powered=True):
        self._b_on = powered
        self._b_data = input_data if powered else None
        self._evaluate()

    def _evaluate(self):
        out = self._a_on or self._b_on
        a = "A" if self._a_on else "-"
        b = "B" if self._b_on else "-"
        self.status_label.setText(f"{a} | {b}")
        data = self._a_data or self._b_data or ""
        self._set_output(out, data)

    def to_dict(self):
        d = self._base_dict("or_gate")
        d["a_on"] = self._a_on
        d["b_on"] = self._b_on
        d["a_data"] = self._a_data
        d["b_data"] = self._b_data
        return d

    @staticmethod
    def from_dict(data, on_signal=None, on_modified=None):
        w = OrGateWidget(data["node_id"], on_signal, on_modified)
        w._a_on = data.get("a_on", False)
        w._b_on = data.get("b_on", False)
        w._a_data = data.get("a_data")
        w._b_data = data.get("b_data")
        a = "A" if w._a_on else "-"
        b = "B" if w._b_on else "-"
        w.status_label.setText(f"{a} | {b}")
        return w


class NotGateWidget(_LogicNodeBase):
    """입력 반전: ON → OFF, OFF → ON."""

    _title = "NOT"
    _color = "#e74c3c"

    def __init__(self, node_id, on_signal=None, on_modified=None):
        super().__init__(node_id, on_signal, on_modified)
        self._in_on = False
        self._init_ui()
        self._output_on = True
        self._pass_powered = True

    def _init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)
        layout.addWidget(self._make_title_label())
        self.status_label = self._make_status_label()
        self.status_label.setText("ON")
        layout.addWidget(self.status_label)
        self.setLayout(layout)

    def on_signal_input(self, input_data=None):
        pass

    def on_signal_a(self, input_data=None, powered=True):
        self._in_on = powered
        out = not self._in_on
        self.status_label.setText("ON" if out else "OFF")
        self._set_output(out, input_data)

    def to_dict(self):
        d = self._base_dict("not_gate")
        d["in_on"] = self._in_on
        return d

    @staticmethod
    def from_dict(data, on_signal=None, on_modified=None):
        w = NotGateWidget(data["node_id"], on_signal, on_modified)
        w._in_on = data.get("in_on", False)
        w._output_on = not w._in_on
        w.status_label.setText("ON" if w._output_on else "OFF")
        return w


class XorGateWidget(_LogicNodeBase):
    """A와 B 중 하나만 ON일 때 출력 ON."""

    _title = "XOR"
    _color = "#9b59b6"

    def __init__(self, node_id, on_signal=None, on_modified=None):
        super().__init__(node_id, on_signal, on_modified)
        self._a_on = False
        self._b_on = False
        self._a_data = None
        self._b_data = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)
        layout.addWidget(self._make_title_label())
        self.status_label = self._make_status_label()
        layout.addWidget(self.status_label)
        self.setLayout(layout)

    def on_signal_a(self, input_data=None, powered=True):
        self._a_on = powered
        self._a_data = input_data if powered else None
        self._evaluate()

    def on_signal_b(self, input_data=None, powered=True):
        self._b_on = powered
        self._b_data = input_data if powered else None
        self._evaluate()

    def _evaluate(self):
        out = self._a_on != self._b_on
        a = "A" if self._a_on else "-"
        b = "B" if self._b_on else "-"
        self.status_label.setText(f"{a} ^ {b}")
        data = self._a_data or self._b_data or ""
        self._set_output(out, data)

    def to_dict(self):
        d = self._base_dict("xor_gate")
        d["a_on"] = self._a_on
        d["b_on"] = self._b_on
        d["a_data"] = self._a_data
        d["b_data"] = self._b_data
        return d

    @staticmethod
    def from_dict(data, on_signal=None, on_modified=None):
        w = XorGateWidget(data["node_id"], on_signal, on_modified)
        w._a_on = data.get("a_on", False)
        w._b_on = data.get("b_on", False)
        w._a_data = data.get("a_data")
        w._b_data = data.get("b_data")
        a = "A" if w._a_on else "-"
        b = "B" if w._b_on else "-"
        w.status_label.setText(f"{a} ^ {b}")
        return w


class BulbNodeWidget(_LogicNodeBase):

    _title = "BULB"
    _color = "#f1c40f"

    def __init__(self, node_id, on_signal=None, on_modified=None):
        super().__init__(node_id, on_signal, on_modified)
        self._lit = False
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)
        layout.addWidget(self._make_title_label())
        self.bulb_label = QLabel("●")
        self.bulb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.bulb_label.setFont(QFont("Segoe UI", 18))
        self.bulb_label.setStyleSheet("color: #555; border: none; background: transparent;")
        layout.addWidget(self.bulb_label)
        self.setLayout(layout)

    def on_signal_a(self, input_data=None, powered=True):
        self._lit = powered
        self._update_visual()
        self._set_output(powered, input_data)

    def _update_visual(self):
        if self._lit:
            self.setStyleSheet("""
                BulbNodeWidget {
                    background-color: #4a3800;
                    border: 3px solid #f1c40f;
                    border-radius: 8px;
                }
            """)
            self.bulb_label.setStyleSheet("color: #f1c40f; border: none; background: transparent;")
        else:
            self._apply_style()
            self.bulb_label.setStyleSheet("color: #555; border: none; background: transparent;")

    def to_dict(self):
        d = self._base_dict("bulb")
        d["lit"] = self._lit
        return d

    @staticmethod
    def from_dict(data, on_signal=None, on_modified=None):
        w = BulbNodeWidget(data["node_id"], on_signal, on_modified)
        w._lit = data.get("lit", False)
        w._output_on = w._lit
        w._pass_powered = w._lit
        w._update_visual()
        return w
