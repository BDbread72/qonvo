"""
스키마 기반 생성-옵션 패널.

과거엔 chat_node 가 옵션마다 위젯을 하드코딩하고 _on_model_changed 에서 키 유무로
일일이 show/hide 했다. 그래서 스키마에만 있고 UI 가 없는 옵션(예: thinking_level)은
사용자가 못 바꿨고, 새 옵션을 추가하려면 UI·수집·복원 코드를 다 같이 손대야 했다.

여기서는 MODEL_OPTIONS 스키마 dict 를 받아 위젯을 동적으로 생성한다. 옵션 추가 =
스키마 한 줄. 값 수집(values)·복원(set_values)·변경 시그널(changed)을 일반화해 제공.

지원 타입:
    float        -> QDoubleSpinBox
    int          -> QSpinBox
    choice        -> QComboBox (values/default)
    bool         -> QCheckBox
    string       -> QLineEdit
    string_list  -> QLineEdit (쉼표 구분 ↔ list)
"""
from PyQt6.QtWidgets import (
    QFrame, QGridLayout, QLabel, QComboBox, QCheckBox,
    QDoubleSpinBox, QSpinBox, QLineEdit,
)
from PyQt6.QtCore import pyqtSignal

from v.theme import Theme


_SPIN_STYLE = f"""
    QDoubleSpinBox, QSpinBox {{
        background-color: #333; color: {Theme.TEXT_PRIMARY}; border: 1px solid #444;
        border-radius: 4px; padding: 2px 4px; font-size: 10px;
    }}
    QDoubleSpinBox:hover, QSpinBox:hover {{ border-color: {Theme.ACCENT_PRIMARY}; }}
"""
_COMBO_STYLE = f"""
    QComboBox {{
        background-color: #333; color: {Theme.TEXT_PRIMARY}; border: 1px solid #444;
        border-radius: 4px; padding: 3px 6px; font-size: 10px;
    }}
    QComboBox:hover {{ border-color: {Theme.ACCENT_PRIMARY}; }}
    QComboBox::drop-down {{ border: none; width: 16px; }}
    QComboBox::down-arrow {{
        image: none; border-left: 4px solid transparent;
        border-right: 4px solid transparent; border-top: 5px solid {Theme.TEXT_SECONDARY};
        margin-right: 4px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {Theme.BG_SECONDARY}; color: {Theme.TEXT_PRIMARY};
        border: 1px solid #444; selection-background-color: {Theme.ACCENT_PRIMARY};
    }}
"""
_LINE_STYLE = f"""
    QLineEdit {{
        background-color: #333; color: {Theme.TEXT_PRIMARY}; border: 1px solid #444;
        border-radius: 4px; padding: 3px 6px; font-size: 10px;
    }}
    QLineEdit:focus {{ border-color: {Theme.ACCENT_PRIMARY}; }}
"""
_CHK_STYLE = f"""
    QCheckBox {{ color: {Theme.TEXT_SECONDARY}; font-size: 10px; spacing: 3px; }}
    QCheckBox::indicator {{
        width: 13px; height: 13px; border-radius: 3px;
        border: 1px solid #555; background-color: #333;
    }}
    QCheckBox::indicator:checked {{
        background-color: {Theme.ACCENT_PRIMARY}; border-color: {Theme.ACCENT_PRIMARY};
    }}
"""
_LABEL_STYLE = f"color: {Theme.TEXT_TERTIARY}; font-size: 10px;"


class OptionsPanel(QFrame):
    """스키마 dict → 위젯. values()/set_values()/changed 제공."""

    changed = pyqtSignal()

    COLUMNS = 2  # 한 줄에 옵션 N개 (label+widget 쌍)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"QFrame {{ background-color: {Theme.BG_INPUT}; border: none; }}")
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(12, 6, 12, 6)
        self._grid.setHorizontalSpacing(8)
        self._grid.setVerticalSpacing(5)
        self._fields = {}   # key -> dict(type, widget, getter, setter)
        self._schema = {}

    def has_options(self) -> bool:
        return bool(self._fields)

    def set_schema(self, schema: dict):
        """현재 모델의 옵션 스키마로 위젯을 재구성한다(기본값 적용)."""
        self._schema = schema or {}
        self._clear()
        # 필드 열(1·3)만 늘어나게 → 위젯이 노드 폭에 맞춰 줄어듦(280px 노드에서도 폭 안 터짐).
        for c in range(self.COLUMNS):
            self._grid.setColumnStretch(c * 2, 0)      # 라벨 열
            self._grid.setColumnStretch(c * 2 + 1, 1)  # 위젯 열
        col_pairs = 0
        row = 0
        for key, spec in self._schema.items():
            widget, getter, setter = self._build_field(spec)
            if widget is None:
                continue
            self._fields[key] = {"spec": spec, "widget": widget, "get": getter, "set": setter}
            label = QLabel(spec.get("label", key))
            label.setStyleSheet(_LABEL_STYLE)
            base_col = (col_pairs % self.COLUMNS) * 2
            self._grid.addWidget(label, row, base_col)
            self._grid.addWidget(widget, row, base_col + 1)
            col_pairs += 1
            if col_pairs % self.COLUMNS == 0:
                row += 1

    def values(self) -> dict:
        return {key: f["get"]() for key, f in self._fields.items()}

    def set_values(self, vals: dict):
        if not vals:
            return
        for key, f in self._fields.items():
            if key in vals:
                try:
                    f["set"](vals[key])
                except Exception:
                    pass

    # ── 내부 ──
    def _clear(self):
        self._fields.clear()
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _emit_changed(self, *_):
        self.changed.emit()

    def _build_field(self, spec: dict):
        """타입별 위젯 + getter/setter 생성. (widget, getter, setter) 반환."""
        t = spec.get("type")
        default = spec.get("default")

        if t == "float":
            w = QDoubleSpinBox()
            w.setRange(float(spec.get("min", 0.0)), float(spec.get("max", 1.0)))
            step = float(spec.get("step", 0.05))
            w.setSingleStep(step)
            w.setDecimals(max(2, len(str(step).split(".")[-1]) if "." in str(step) else 2))
            w.setValue(float(default if default is not None else 0.0))
            w.setMinimumWidth(46)
            w.setMaximumWidth(80)
            w.setStyleSheet(_SPIN_STYLE)
            w.valueChanged.connect(self._emit_changed)
            return w, w.value, (lambda v, w=w: w.setValue(float(v)))

        if t == "int":
            w = QSpinBox()
            w.setRange(int(spec.get("min", 0)), int(spec.get("max", 2147483647)))
            w.setValue(int(default if default is not None else 0))
            w.setMinimumWidth(46)
            w.setMaximumWidth(88)
            w.setStyleSheet(_SPIN_STYLE)
            w.valueChanged.connect(self._emit_changed)
            return w, w.value, (lambda v, w=w: w.setValue(int(v)))

        if t == "choice":
            w = QComboBox()
            vals = [str(v) for v in spec.get("values", [])]
            w.addItems(vals)
            if default is not None and str(default) in vals:
                w.setCurrentIndex(vals.index(str(default)))
            w.setStyleSheet(_COMBO_STYLE)
            w.currentIndexChanged.connect(self._emit_changed)
            return w, w.currentText, (lambda v, w=w: self._set_combo(w, v))

        if t == "bool":
            w = QCheckBox()
            w.setChecked(bool(default))
            w.setStyleSheet(_CHK_STYLE)
            w.toggled.connect(self._emit_changed)
            return w, w.isChecked, (lambda v, w=w: w.setChecked(bool(v)))

        if t == "string":
            w = QLineEdit()
            if spec.get("placeholder"):
                w.setPlaceholderText(spec["placeholder"])
            if default:
                w.setText(str(default))
            w.setStyleSheet(_LINE_STYLE)
            w.setMinimumWidth(70)  # 고정폭 대신 최소폭 — 열 stretch 로 노드 폭에 맞춰 늘어/줄어듦
            w.textChanged.connect(self._emit_changed)
            return w, w.text, (lambda v, w=w: w.setText(str(v) if v is not None else ""))

        if t == "string_list":
            w = QLineEdit()
            if spec.get("placeholder"):
                w.setPlaceholderText(spec["placeholder"])
            if default:
                w.setText(", ".join(default) if isinstance(default, (list, tuple)) else str(default))
            w.setStyleSheet(_LINE_STYLE)
            w.setMinimumWidth(70)
            w.textChanged.connect(self._emit_changed)
            return (
                w,
                (lambda w=w: [s.strip() for s in w.text().split(",") if s.strip()]),
                (lambda v, w=w: w.setText(", ".join(v) if isinstance(v, (list, tuple)) else str(v or ""))),
            )

        return None, None, None

    @staticmethod
    def _set_combo(w: QComboBox, v):
        i = w.findText(str(v))
        if i >= 0:
            w.setCurrentIndex(i)
