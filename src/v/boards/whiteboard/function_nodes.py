"""
Blueprint-style function editor internal nodes, ports, and edges
- FuncPortItem: Dual-shape port (exec=triangle, data=circle)
- FuncTempEdgeItem: Temporary edge during drag
- FuncEdgeItem: Permanent edge (exec=white/thick, data=colored)
- FuncNodeBase: Base class with IS_PURE, exec/data pin definitions
- 22 node widget classes across 4 categories
"""
import math

from PyQt6.QtWidgets import (
    QGraphicsItem, QGraphicsPathItem, QGraphicsProxyWidget,
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QFrame, QLineEdit, QSpinBox,
)
from PyQt6.QtCore import Qt, QPointF, QRectF
from PyQt6.QtGui import (
    QPen, QBrush, QColor, QPainterPath, QPainterPathStroker, QPainter,
    QMouseEvent, QCursor, QPolygonF,
)

from v.theme import Theme
from v.model_plugin import get_all_models
from .function_types import (
    DataType, NodeType, DATA_TYPE_COLORS, CATEGORY_COLORS, NODE_CATEGORIES,
    can_convert, PortDef,
)


# ──────────────────────────────────────────
# Port Item (dual shapes)
# ──────────────────────────────────────────

class FuncPortItem(QGraphicsItem):
    """Blueprint-style port: exec=triangle, data=circle"""
    INPUT = 0
    OUTPUT = 1

    PORT_SIZE = 10  # bounding size
    LABEL_OFFSET = 12
    LABEL_MAX_WIDTH = 60

    def __init__(self, port_id: str, port_type: int, data_type: DataType,
                 parent_proxy: QGraphicsProxyWidget, label: str = ""):
        super().__init__()
        self.port_id = port_id
        self.port_type = port_type
        self.data_type = data_type
        self.is_exec = (data_type == DataType.EXEC)
        self.parent_proxy = parent_proxy
        self.label = label
        self.edges: list = []
        self._last_scene_pos = None

        self._color = QColor(DATA_TYPE_COLORS.get(data_type, "#95a5a6"))
        self._hover = False

        self.setZValue(10)
        self.setAcceptHoverEvents(True)

    def boundingRect(self) -> QRectF:
        s = self.PORT_SIZE
        if self.label:
            if self.port_type == self.INPUT:
                return QRectF(-s / 2 - 2, -s / 2 - 2, s + 4 + self.LABEL_OFFSET + self.LABEL_MAX_WIDTH, s + 4)
            else:
                return QRectF(-s / 2 - 2 - self.LABEL_OFFSET - self.LABEL_MAX_WIDTH, -s / 2 - 2, s + 4 + self.LABEL_OFFSET + self.LABEL_MAX_WIDTH, s + 4)
        return QRectF(-s / 2 - 2, -s / 2 - 2, s + 4, s + 4)

    def paint(self, painter: QPainter, option, widget):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self.PORT_SIZE
        hs = s / 2

        pen_color = QColor("#ffcc00") if self._hover else QColor("#ffffff")
        pen_width = 2 if self._hover else 1
        painter.setPen(QPen(pen_color, pen_width))

        if self.is_exec:
            # Triangle shape for exec pins
            tri = QPolygonF([
                QPointF(-hs, -hs),
                QPointF(hs, 0),
                QPointF(-hs, hs),
            ])
            if self.edges:
                painter.setBrush(QBrush(self._color))
            else:
                painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolygon(tri)
        else:
            # Circle shape for data pins
            if self.edges:
                painter.setBrush(QBrush(self._color))
            else:
                painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(0, 0), hs - 1, hs - 1)

        # Draw label text
        if self.label:
            from PyQt6.QtGui import QFont
            font = QFont("Segoe UI", 8)
            painter.setFont(font)
            painter.setPen(QPen(QColor("#aaaaaa")))
            if self.port_type == self.INPUT:
                painter.drawText(
                    QRectF(hs + self.LABEL_OFFSET - 4, -7, self.LABEL_MAX_WIDTH, 14),
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                    self.label,
                )
            else:
                painter.drawText(
                    QRectF(-hs - self.LABEL_OFFSET - self.LABEL_MAX_WIDTH + 4, -7, self.LABEL_MAX_WIDTH, 14),
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                    self.label,
                )

    def reposition(self):
        """Reposition port relative to parent proxy"""
        widget = self.parent_proxy.widget()
        if not widget:
            return
        h = widget.height()
        w = widget.width()
        proxy_pos = self.parent_proxy.pos()

        if self.port_type == self.INPUT:
            input_ports = [p for p in _get_ports(self.parent_proxy) if p.port_type == self.INPUT]
            n = len(input_ports)
            if n <= 1:
                new_pos = (proxy_pos.x(), proxy_pos.y() + h / 2)
            else:
                idx = input_ports.index(self) if self in input_ports else 0
                spacing = h / (n + 1)
                new_pos = (proxy_pos.x(), proxy_pos.y() + spacing * (idx + 1))
        else:
            output_ports = [p for p in _get_ports(self.parent_proxy) if p.port_type == self.OUTPUT]
            n = len(output_ports)
            if n <= 1:
                new_pos = (proxy_pos.x() + w, proxy_pos.y() + h / 2)
            else:
                idx = output_ports.index(self) if self in output_ports else 0
                spacing = h / (n + 1)
                new_pos = (proxy_pos.x() + w, proxy_pos.y() + spacing * (idx + 1))

        if self._last_scene_pos == new_pos:
            return
        self._last_scene_pos = new_pos
        self.setPos(new_pos[0], new_pos[1])

    def hoverEnterEvent(self, event):
        self._hover = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._hover = False
        self.update()
        super().hoverLeaveEvent(event)


def _get_ports(proxy: QGraphicsProxyWidget) -> list:
    """Get all FuncPortItem attached to a proxy"""
    if not proxy.scene():
        return []
    return [
        item for item in proxy.scene().items()
        if isinstance(item, FuncPortItem) and item.parent_proxy is proxy
    ]


def can_connect(source_port: FuncPortItem, target_port: FuncPortItem) -> bool:
    """Validate if two ports can be connected"""
    # Same node: no
    if source_port.parent_proxy == target_port.parent_proxy:
        return False
    # Same direction: no
    if source_port.port_type == target_port.port_type:
        return False
    # Exec-to-data mismatch: no
    if source_port.is_exec != target_port.is_exec:
        return False
    # Both exec: ok
    if source_port.is_exec and target_port.is_exec:
        return True
    # Data type compatibility
    src_type = source_port.data_type
    tgt_type = target_port.data_type
    return can_convert(src_type, tgt_type)


# ──────────────────────────────────────────
# Edges
# ──────────────────────────────────────────

class FuncTempEdgeItem(QGraphicsPathItem):
    """Temporary edge during drag"""

    def __init__(self, start_pos: QPointF):
        super().__init__()
        self._start = start_pos
        self._end = start_pos
        pen = QPen(QColor("#4a9eff"), 1.5, Qt.PenStyle.DashLine)
        pen.setDashPattern([6, 4])
        self.setPen(pen)
        self.setOpacity(0.7)
        self.setZValue(-1)

    def set_end(self, end_pos: QPointF):
        self._end = end_pos
        self._rebuild()

    def set_start(self, start_pos: QPointF):
        self._start = start_pos
        self._rebuild()

    def _rebuild(self):
        offset = max(20, abs(self._end.x() - self._start.x()) / 2)
        path = QPainterPath()
        path.moveTo(self._start)
        path.cubicTo(
            self._start.x() + offset, self._start.y(),
            self._end.x() - offset, self._end.y(),
            self._end.x(), self._end.y(),
        )
        self.setPath(path)


class FuncEdgeItem(QGraphicsPathItem):
    """Blueprint-style edge: exec=white/thick, data=colored"""

    def __init__(self, source_port: FuncPortItem, target_port: FuncPortItem):
        super().__init__()
        self.source_port = source_port
        self.target_port = target_port
        self.is_exec = source_port.is_exec

        if self.is_exec:
            color = QColor("#cccccc")
            width = 3.0
        else:
            color = QColor(DATA_TYPE_COLORS.get(source_port.data_type, "#555555"))
            width = 2.0

        self._normal_pen = QPen(color, width)
        self._hover_pen = QPen(color.lighter(140), width + 0.5)
        self._selected_pen = QPen(QColor("#4a9eff"), width + 1)
        self._hover = False
        self.setPen(self._normal_pen)
        self.setZValue(-1)

        self.setFlag(QGraphicsPathItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)

        source_port.edges.append(self)
        target_port.edges.append(self)

        self._last_pos = None
        self.update_path()

    def update_path(self):
        s = self.source_port.scenePos()
        t = self.target_port.scenePos()
        key = (s.x(), s.y(), t.x(), t.y())
        if self._last_pos == key:
            return
        self._last_pos = key
        offset = max(20, abs(t.x() - s.x()) / 2)
        path = QPainterPath()
        path.moveTo(s)
        path.cubicTo(s.x() + offset, s.y(), t.x() - offset, t.y(), t.x(), t.y())
        self.setPath(path)

    def shape(self):
        stroker = QPainterPathStroker()
        stroker.setWidth(10)
        return stroker.createStroke(self.path())

    def paint(self, painter, option, widget):
        option.state &= ~option.state.State_Selected
        if self.isSelected():
            self.setPen(self._selected_pen)
        elif not self._hover:
            self.setPen(self._normal_pen)
        super().paint(painter, option, widget)

    def hoverEnterEvent(self, event):
        self._hover = True
        if not self.isSelected():
            self.setPen(self._hover_pen)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._hover = False
        if not self.isSelected():
            self.setPen(self._normal_pen)
        super().hoverLeaveEvent(event)

    def disconnect(self):
        if self in self.source_port.edges:
            self.source_port.edges.remove(self)
        if self in self.target_port.edges:
            self.target_port.edges.remove(self)


# ──────────────────────────────────────────
# Node Base Class
# ──────────────────────────────────────────

class FuncNodeBase(QWidget):
    """Blueprint-style internal node base class"""

    NODE_TYPE = "base"
    DISPLAY_NAME = "Node"
    IS_PURE = False  # Pure nodes have no exec pins, lazy evaluated

    # Subclass defines these for port creation
    EXEC_IN = True        # Has exec input pin?
    EXEC_OUT = ["exec_out"]  # List of exec output port IDs with labels
    DATA_IN: list = []    # [(port_id, DataType, label), ...]
    DATA_OUT: list = []   # [(port_id, DataType, label), ...]

    def __init__(self, node_id: str, config: dict = None):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.node_id = node_id
        self.config = config or {}
        self.proxy = None

        # Drag state
        self._dragging = False
        self._drag_start_global = None
        self._drag_start_proxy_pos = None

        # Category color for title bar
        category = NODE_CATEGORIES.get(self.NODE_TYPE, "control_flow")
        self._category_color = CATEGORY_COLORS.get(category, "#555555")

        self._auto_size()
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._apply_style()
        self._setup_ui()

    def _auto_size(self):
        """Calculate node size based on port count"""
        # Count total ports on each side
        left_count = (1 if self.EXEC_IN and not self.IS_PURE else 0) + len(self.DATA_IN)
        right_count = len(self.EXEC_OUT if not self.IS_PURE else []) + len(self.DATA_OUT)
        port_rows = max(left_count, right_count, 1)
        height = 28 + port_rows * 22 + 8  # title + ports + padding
        self.setFixedSize(180, max(height, 60))

    def _apply_style(self):
        # Collect all subclass names for stylesheet selector
        self.setStyleSheet(f"""
            QWidget {{
                background-color: #252525;
                border: 2px solid {self._category_color};
                border-radius: 8px;
            }}
        """)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 4)
        layout.setSpacing(2)

        # Title bar with category color
        title = QLabel(self.DISPLAY_NAME)
        title.setStyleSheet(
            f"color: {self._category_color}; font-weight: bold; font-size: 11px; "
            f"background: transparent; border: none;"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        self._content_layout = layout

    def get_port_defs(self) -> dict:
        """Return port definitions for this node.
        Returns dict with keys: exec_in, exec_out, data_in, data_out
        Each value is list of (port_id, DataType, label)
        """
        result = {"exec_in": [], "exec_out": [], "data_in": [], "data_out": []}

        if not self.IS_PURE and self.EXEC_IN:
            result["exec_in"].append(("exec_in", DataType.EXEC, ""))

        if not self.IS_PURE:
            for port_id in self.EXEC_OUT:
                label = port_id.replace("exec_out", "").replace("_", " ").strip()
                result["exec_out"].append((port_id, DataType.EXEC, label))

        result["data_in"] = list(self.DATA_IN)
        result["data_out"] = list(self.DATA_OUT)
        return result

    # ── Drag/selection mouse handling ──

    def _cursor_to_scene(self) -> QPointF:
        if not self.proxy or not self.proxy.scene():
            return QPointF(0, 0)
        views = self.proxy.scene().views()
        if not views:
            return QPointF(0, 0)
        view = views[0]
        vp = view.mapFromGlobal(QCursor.pos())
        return view.mapToScene(vp)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self.proxy:
            self._dragging = False
            self._drag_start_global = QCursor.pos()
            self._drag_start_proxy_pos = self.proxy.pos()
            if not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                scene = self.proxy.scene()
                if scene:
                    scene.clearSelection()
            self.proxy.setSelected(True)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._drag_start_global is not None and self.proxy:
            current = QCursor.pos()
            dx = current.x() - self._drag_start_global.x()
            dy = current.y() - self._drag_start_global.y()
            if not self._dragging and (abs(dx) + abs(dy) > 4):
                self._dragging = True
            if self._dragging:
                view = self.proxy.scene().views()[0] if self.proxy.scene() and self.proxy.scene().views() else None
                if view:
                    zoom = view.transform().m11()
                    new_x = self._drag_start_proxy_pos.x() + dx / zoom
                    new_y = self._drag_start_proxy_pos.y() + dy / zoom
                    self.proxy.setPos(new_x, new_y)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._drag_start_global = None
            self._drag_start_proxy_pos = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def get_config(self) -> dict:
        return dict(self.config)

    def set_config(self, config: dict):
        self.config = dict(config)


# ══════════════════════════════════════════
# Control Flow Nodes
# ══════════════════════════════════════════

class StartNode(FuncNodeBase):
    NODE_TYPE = NodeType.START
    DISPLAY_NAME = "Start"
    EXEC_IN = False
    EXEC_OUT = ["exec_out"]
    DATA_IN = []
    DATA_OUT = []  # Dynamic based on parameters

    on_params_changed = None

    def __init__(self, node_id: str, config: dict = None):
        self._param_widgets = []
        super().__init__(node_id, config)
        if "parameters" not in self.config:
            self.config["parameters"] = []
        self._update_size()

    def _auto_size(self):
        self.setMinimumWidth(220)
        self.setMaximumWidth(220)
        self.setMinimumHeight(60)

    def _update_size(self):
        self.setMinimumWidth(220)
        self.setMaximumWidth(220)
        self.adjustSize()
        self.updateGeometry()
        if self.proxy:
            self.proxy.resize(self.width(), self.height())

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)

        header = QHBoxLayout()
        header.setSpacing(4)
        title = QLabel("START")
        title.setStyleSheet(
            f"color: {self._category_color}; font-weight: bold; font-size: 11px; "
            f"background: transparent; border: none;"
        )
        header.addWidget(title)
        header.addStretch()

        btn_add = QPushButton("+")
        btn_add.setFixedSize(20, 20)
        btn_add.setStyleSheet("""
            QPushButton {
                background: #27ae60; border: none; border-radius: 4px;
                color: white; font-weight: bold; font-size: 14px;
            }
            QPushButton:hover { background: #2ecc71; }
        """)
        btn_add.clicked.connect(self._add_parameter)
        header.addWidget(btn_add)
        layout.addLayout(header)

        self._params_layout = QVBoxLayout()
        self._params_layout.setContentsMargins(0, 0, 0, 0)
        self._params_layout.setSpacing(2)
        layout.addLayout(self._params_layout)

        layout.addStretch()
        self._content_layout = layout
        self._rebuild_param_list()

    def _rebuild_param_list(self):
        for w in self._param_widgets:
            w.setParent(None)
            w.deleteLater()
        self._param_widgets.clear()

        for i, p in enumerate(self.config.get("parameters", [])):
            row = QWidget()
            row.setStyleSheet("background: transparent; border: none;")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(2)

            param_type = p.get("param_type", "string")
            type_icons = {
                "string": "S", "number": "#", "boolean": "?",
                "image": "I", "array": "[]", "object": "{}",
                "any": "*",
            }
            icon = type_icons.get(param_type, "S")

            label = QLabel(f"[{icon}] {p['name']}")
            label.setStyleSheet(
                "color: #aaa; font-size: 10px; background: transparent; border: none;"
            )
            label.setToolTip(f"{p['name']} ({param_type})")
            row_layout.addWidget(label)
            row_layout.addStretch()

            btn_del = QPushButton("x")
            btn_del.setFixedSize(16, 16)
            btn_del.setStyleSheet("""
                QPushButton {
                    background: transparent; border: none;
                    color: #c0392b; font-weight: bold; font-size: 12px;
                }
                QPushButton:hover { color: #e74c3c; }
            """)
            btn_del.clicked.connect(lambda _, idx=i: self._remove_parameter(idx))
            row_layout.addWidget(btn_del)

            self._params_layout.addWidget(row)
            self._param_widgets.append(row)

        self._update_size()

    def _add_parameter(self):
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox

        existing = [p["name"] for p in self.config.get("parameters", [])]

        dialog = QDialog(self)
        dialog.setWindowTitle("Add Parameter")
        dialog.setStyleSheet(f"background-color: {Theme.BG_PRIMARY}; color: {Theme.TEXT_PRIMARY};")
        layout = QVBoxLayout(dialog)
        layout.setSpacing(8)

        layout.addWidget(QLabel("Name:"))
        name_edit = QLineEdit()
        name_edit.setStyleSheet(
            f"background: {Theme.BG_SECONDARY}; border: 1px solid {Theme.NODE_BORDER}; "
            f"padding: 4px; color: {Theme.TEXT_PRIMARY};"
        )
        layout.addWidget(name_edit)

        layout.addWidget(QLabel("Type:"))
        type_combo = QComboBox()
        type_combo.setStyleSheet(
            f"background: {Theme.BG_SECONDARY}; border: 1px solid {Theme.NODE_BORDER}; "
            f"padding: 4px; color: {Theme.TEXT_PRIMARY};"
        )
        type_combo.addItems([
            "string", "number", "boolean", "image", "array", "object", "any",
        ])
        layout.addWidget(type_combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.setStyleSheet(f"color: {Theme.TEXT_PRIMARY};")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            name = name_edit.text().strip()
            if not name or name in existing:
                return
            param_type = type_combo.currentText()
            self.config["parameters"].append({"name": name, "param_type": param_type})
            self._rebuild_param_list()
            self._notify_params_changed()

    def _remove_parameter(self, index: int):
        params = self.config.get("parameters", [])
        if 0 <= index < len(params):
            params.pop(index)
            self._rebuild_param_list()
            self._notify_params_changed()

    def _notify_params_changed(self):
        if self.on_params_changed:
            self.on_params_changed(self.node_id, self.get_data_out_defs())

    def get_data_out_defs(self) -> list:
        """Generate DATA_OUT port definitions from parameters"""
        ports = []
        type_map = {
            "string": DataType.STRING, "number": DataType.NUMBER,
            "boolean": DataType.BOOLEAN, "image": DataType.IMAGE,
            "array": DataType.ARRAY, "object": DataType.OBJECT,
            "any": DataType.ANY,
        }
        for param in self.config.get("parameters", []):
            dt = type_map.get(param.get("param_type", "string"), DataType.STRING)
            ports.append((param["name"], dt, param["name"]))
        return ports

    def get_port_defs(self) -> dict:
        result = {"exec_in": [], "exec_out": [], "data_in": [], "data_out": []}
        result["exec_out"].append(("exec_out", DataType.EXEC, ""))
        result["data_out"] = self.get_data_out_defs()
        return result

    def get_config(self) -> dict:
        return dict(self.config)

    def set_config(self, config: dict):
        self.config = dict(config)
        if "parameters" not in self.config:
            self.config["parameters"] = []
        self._rebuild_param_list()


class EndNode(FuncNodeBase):
    NODE_TYPE = NodeType.END
    DISPLAY_NAME = "End"
    EXEC_IN = True
    EXEC_OUT = []
    DATA_IN = [("result", DataType.ANY, "Result")]
    DATA_OUT = []

    PORT_COLORS = [
        "#4a9eff", "#e67e22", "#2ecc71", "#e74c3c",
        "#9b59b6", "#1abc9c", "#f1c40f", "#c0392b",
    ]

    def __init__(self, node_id: str, config: dict = None):
        super().__init__(node_id, config)
        if "output_name" not in self.config:
            self.config["output_name"] = "output"
        if "port_color" not in self.config:
            self.config["port_color"] = "#4a9eff"

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)

        header = QHBoxLayout()
        header.setSpacing(4)
        title = QLabel("END")
        title.setStyleSheet(
            f"color: {self._category_color}; font-weight: bold; font-size: 11px; "
            f"background: transparent; border: none;"
        )
        header.addWidget(title)
        header.addStretch()

        self._color_btn = QPushButton("*")
        self._color_btn.setFixedSize(18, 18)
        color = self.config.get("port_color", "#4a9eff") if self.config else "#4a9eff"
        self._color_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: none;
                color: {color}; font-size: 14px;
            }}
            QPushButton:hover {{ color: white; }}
        """)
        self._color_btn.clicked.connect(self._cycle_color)
        header.addWidget(self._color_btn)
        layout.addLayout(header)

        output_name = self.config.get("output_name", "output") if self.config else "output"
        self._name_edit = QLineEdit(output_name)
        self._name_edit.setStyleSheet("""
            QLineEdit {
                background: #2a2a2a; border: 1px solid #444;
                border-radius: 3px; padding: 2px 4px; color: #ddd;
                font-size: 10px; text-align: center;
            }
            QLineEdit:focus { border-color: #c0392b; }
        """)
        self._name_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name_edit.textChanged.connect(self._on_name_changed)
        layout.addWidget(self._name_edit)

        self._content_layout = layout

    def _cycle_color(self):
        current = self.config.get("port_color", "#4a9eff")
        try:
            idx = self.PORT_COLORS.index(current)
            next_idx = (idx + 1) % len(self.PORT_COLORS)
        except ValueError:
            next_idx = 0
        new_color = self.PORT_COLORS[next_idx]
        self.config["port_color"] = new_color
        self._color_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: none;
                color: {new_color}; font-size: 14px;
            }}
            QPushButton:hover {{ color: white; }}
        """)

    def _on_name_changed(self, text: str):
        self.config["output_name"] = text.strip() or "output"

    def set_config(self, config: dict):
        self.config = dict(config)
        if "output_name" not in self.config:
            self.config["output_name"] = "output"
        if "port_color" not in self.config:
            self.config["port_color"] = "#4a9eff"
        if hasattr(self, '_name_edit'):
            self._name_edit.setText(self.config["output_name"])
        if hasattr(self, '_color_btn'):
            color = self.config["port_color"]
            self._color_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; border: none;
                    color: {color}; font-size: 14px;
                }}
                QPushButton:hover {{ color: white; }}
            """)


class BranchNode(FuncNodeBase):
    NODE_TYPE = NodeType.BRANCH
    DISPLAY_NAME = "Branch"
    EXEC_IN = True
    EXEC_OUT = ["true", "false"]
    DATA_IN = [("condition", DataType.BOOLEAN, "Condition")]
    DATA_OUT = []


class SwitchNode(FuncNodeBase):
    NODE_TYPE = NodeType.SWITCH
    DISPLAY_NAME = "Switch"
    EXEC_IN = True
    EXEC_OUT = ["default"]  # Dynamic: case_0..case_N + default
    DATA_IN = [("value", DataType.STRING, "Value")]
    DATA_OUT = []

    def __init__(self, node_id: str, config: dict = None):
        config = config or {}
        if "cases" not in config:
            config["cases"] = ["case_A", "case_B"]
        super().__init__(node_id, config)
        self._update_exec_out()

    def _update_exec_out(self):
        cases = self.config.get("cases", [])
        self.EXEC_OUT = [f"case_{i}" for i in range(len(cases))] + ["default"]

    def get_port_defs(self) -> dict:
        self._update_exec_out()
        result = {"exec_in": [], "exec_out": [], "data_in": [], "data_out": []}
        result["exec_in"].append(("exec_in", DataType.EXEC, ""))
        for i, case_val in enumerate(self.config.get("cases", [])):
            result["exec_out"].append((f"case_{i}", DataType.EXEC, case_val))
        result["exec_out"].append(("default", DataType.EXEC, "Default"))
        result["data_in"] = list(self.DATA_IN)
        return result

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 4)
        layout.setSpacing(2)

        title = QLabel("Switch")
        title.setStyleSheet(
            f"color: {self._category_color}; font-weight: bold; font-size: 11px; "
            f"background: transparent; border: none;"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        cases = self.config.get("cases", []) if self.config else []
        info = QLabel(f"Cases: {len(cases)}")
        info.setStyleSheet("color: #aaa; font-size: 9px; background: transparent; border: none;")
        layout.addWidget(info)
        self._info_label = info

        self._content_layout = layout

    def _auto_size(self):
        cases = self.config.get("cases", []) if self.config else []
        right_count = len(cases) + 1  # cases + default
        left_count = 1 + 1  # exec_in + value
        port_rows = max(left_count, right_count, 1)
        height = 28 + port_rows * 22 + 8
        self.setFixedSize(180, max(height, 70))

    def set_config(self, config: dict):
        self.config = dict(config)
        if "cases" not in self.config:
            self.config["cases"] = ["case_A", "case_B"]
        self._update_exec_out()
        if hasattr(self, '_info_label'):
            self._info_label.setText(f"Cases: {len(self.config.get('cases', []))}")


class ForEachNode(FuncNodeBase):
    NODE_TYPE = NodeType.FOR_EACH
    DISPLAY_NAME = "ForEach"
    EXEC_IN = True
    EXEC_OUT = ["loop_body", "completed"]
    DATA_IN = [("array", DataType.ARRAY, "Array")]
    DATA_OUT = [("element", DataType.ANY, "Element"), ("index", DataType.NUMBER, "Index")]

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 4)
        layout.setSpacing(2)
        title = QLabel("ForEach")
        title.setStyleSheet(
            f"color: {self._category_color}; font-weight: bold; font-size: 11px; "
            f"background: transparent; border: none;"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        max_iter = self.config.get("max_iter", 100) if self.config else 100
        self._info_label = QLabel(f"Max: {max_iter}")
        self._info_label.setStyleSheet(
            "color: #aaa; font-size: 9px; background: transparent; border: none;"
        )
        layout.addWidget(self._info_label)
        self._content_layout = layout

    def set_config(self, config: dict):
        self.config = dict(config)
        if hasattr(self, '_info_label'):
            self._info_label.setText(f"Max: {self.config.get('max_iter', 100)}")


class WhileLoopNode(FuncNodeBase):
    NODE_TYPE = NodeType.WHILE_LOOP
    DISPLAY_NAME = "While"
    EXEC_IN = True
    EXEC_OUT = ["loop_body", "completed"]
    DATA_IN = [("condition", DataType.BOOLEAN, "Condition")]
    DATA_OUT = [("index", DataType.NUMBER, "Index")]

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 4)
        layout.setSpacing(2)
        title = QLabel("While Loop")
        title.setStyleSheet(
            f"color: {self._category_color}; font-weight: bold; font-size: 11px; "
            f"background: transparent; border: none;"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        max_iter = self.config.get("max_iter", 100) if self.config else 100
        self._info_label = QLabel(f"Max: {max_iter}")
        self._info_label.setStyleSheet(
            "color: #aaa; font-size: 9px; background: transparent; border: none;"
        )
        layout.addWidget(self._info_label)
        self._content_layout = layout

    def set_config(self, config: dict):
        self.config = dict(config)
        if hasattr(self, '_info_label'):
            self._info_label.setText(f"Max: {self.config.get('max_iter', 100)}")


class SequenceNode(FuncNodeBase):
    NODE_TYPE = NodeType.SEQUENCE
    DISPLAY_NAME = "Sequence"
    EXEC_IN = True
    EXEC_OUT = ["then_0", "then_1"]
    DATA_IN = []
    DATA_OUT = []

    def __init__(self, node_id: str, config: dict = None):
        super().__init__(node_id, config)
        if "output_count" not in self.config:
            self.config["output_count"] = 2
        self._update_exec_out()

    def _update_exec_out(self):
        count = self.config.get("output_count", 2)
        self.EXEC_OUT = [f"then_{i}" for i in range(count)]

    def get_port_defs(self) -> dict:
        self._update_exec_out()
        result = {"exec_in": [], "exec_out": [], "data_in": [], "data_out": []}
        result["exec_in"].append(("exec_in", DataType.EXEC, ""))
        for i in range(self.config.get("output_count", 2)):
            result["exec_out"].append((f"then_{i}", DataType.EXEC, f"Then {i}"))
        return result

    def _auto_size(self):
        count = self.config.get("output_count", 2) if self.config else 2
        port_rows = max(1, count)
        height = 28 + port_rows * 22 + 8
        self.setFixedSize(160, max(height, 60))

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 4)
        layout.setSpacing(2)
        title = QLabel("Sequence")
        title.setStyleSheet(
            f"color: {self._category_color}; font-weight: bold; font-size: 11px; "
            f"background: transparent; border: none;"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        count = self.config.get("output_count", 2) if self.config else 2
        self._info_label = QLabel(f"Outputs: {count}")
        self._info_label.setStyleSheet(
            "color: #aaa; font-size: 9px; background: transparent; border: none;"
        )
        layout.addWidget(self._info_label)
        self._content_layout = layout

    def set_config(self, config: dict):
        self.config = dict(config)
        if "output_count" not in self.config:
            self.config["output_count"] = 2
        self._update_exec_out()
        if hasattr(self, '_info_label'):
            self._info_label.setText(f"Outputs: {self.config.get('output_count', 2)}")


# ══════════════════════════════════════════
# AI Nodes
# ══════════════════════════════════════════

class LLMCallNode(FuncNodeBase):
    NODE_TYPE = NodeType.LLM_CALL
    DISPLAY_NAME = "LLM Call"
    IS_PURE = False
    EXEC_IN = True
    EXEC_OUT = ["exec_out"]
    DATA_IN = [("in_0", DataType.STRING, "Arg 0")]  # Dynamic
    DATA_OUT = [("response", DataType.STRING, "Response")]

    on_inputs_changed = None

    def __init__(self, node_id: str, config: dict = None):
        super().__init__(node_id, config)
        self._num_arg_ports = self.config.get("_num_arg_ports", 1)

    def _auto_size(self):
        self.setFixedSize(240, 100)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 4)
        layout.setSpacing(2)

        title = QLabel("LLM Call")
        title.setStyleSheet(
            f"color: {self._category_color}; font-weight: bold; font-size: 11px; "
            f"background: transparent; border: none;"
        )
        layout.addWidget(title)

        model = self.config.get("model", "")
        model_name = get_all_models().get(model, model) if model else "--"
        self._model_label = QLabel(f"Model: {model_name}")
        self._model_label.setStyleSheet(
            "color: #888; font-size: 9px; background: transparent; border: none;"
        )
        layout.addWidget(self._model_label)

        prompt = self.config.get("prompt_template", "")
        preview = prompt[:50] + "..." if len(prompt) > 50 else prompt
        self._prompt_label = QLabel(preview or "--")
        self._prompt_label.setStyleSheet(
            "color: #aaa; font-size: 9px; background: transparent; border: none;"
        )
        self._prompt_label.setWordWrap(True)
        layout.addWidget(self._prompt_label)

        layout.addStretch()
        self._content_layout = layout

    def get_port_defs(self) -> dict:
        result = {"exec_in": [], "exec_out": [], "data_in": [], "data_out": []}
        result["exec_in"].append(("exec_in", DataType.EXEC, ""))
        result["exec_out"].append(("exec_out", DataType.EXEC, ""))
        for i in range(self._num_arg_ports):
            result["data_in"].append((f"in_{i}", DataType.STRING, f"Arg {i}"))
        result["data_out"].append(("response", DataType.STRING, "Response"))
        return result

    def sync_arg_ports(self, connected_port_ids: set):
        connected_arg_count = sum(1 for pid in connected_port_ids if pid.startswith("in_"))
        new_count = connected_arg_count + 1
        if self._num_arg_ports != new_count:
            self._num_arg_ports = new_count
            if self.on_inputs_changed:
                self.on_inputs_changed(self.node_id, self.get_port_defs())

    def set_config(self, config: dict):
        self.config = dict(config)
        self._num_arg_ports = config.get("_num_arg_ports", 1)
        model = config.get("model", "")
        model_name = get_all_models().get(model, model) if model else "--"
        if hasattr(self, '_model_label'):
            self._model_label.setText(f"Model: {model_name}")
        prompt = config.get("prompt_template", "")
        preview = prompt[:50] + "..." if len(prompt) > 50 else prompt
        if hasattr(self, '_prompt_label'):
            self._prompt_label.setText(preview or "--")

    def get_config(self) -> dict:
        self.config["_num_arg_ports"] = self._num_arg_ports
        return dict(self.config)


class PromptBuilderNode(FuncNodeBase):
    NODE_TYPE = NodeType.PROMPT_BUILDER
    DISPLAY_NAME = "Prompt Builder"
    IS_PURE = True
    EXEC_IN = False
    EXEC_OUT = []
    DATA_IN = [
        ("system", DataType.STRING, "System"),
        ("user", DataType.STRING, "User"),
        ("context", DataType.STRING, "Context"),
    ]
    DATA_OUT = [("prompt", DataType.STRING, "Prompt")]

    def _auto_size(self):
        self.setFixedSize(180, 90)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 4)
        layout.setSpacing(2)
        title = QLabel("Prompt Builder")
        title.setStyleSheet(
            f"color: {self._category_color}; font-weight: bold; font-size: 11px; "
            f"background: transparent; border: none;"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        tmpl = self.config.get("template", "") if self.config else ""
        preview = tmpl[:40] + "..." if len(tmpl) > 40 else (tmpl or "--")
        self._tmpl_label = QLabel(preview)
        self._tmpl_label.setStyleSheet(
            "color: #aaa; font-size: 9px; background: transparent; border: none;"
        )
        self._tmpl_label.setWordWrap(True)
        layout.addWidget(self._tmpl_label)
        self._content_layout = layout

    def set_config(self, config: dict):
        self.config = dict(config)
        if hasattr(self, '_tmpl_label'):
            tmpl = config.get("template", "")
            preview = tmpl[:40] + "..." if len(tmpl) > 40 else (tmpl or "--")
            self._tmpl_label.setText(preview)


class ResponseParserNode(FuncNodeBase):
    NODE_TYPE = NodeType.RESPONSE_PARSER
    DISPLAY_NAME = "Response Parser"
    IS_PURE = False
    EXEC_IN = True
    EXEC_OUT = ["exec_out"]
    DATA_IN = [
        ("text", DataType.STRING, "Text"),
        ("pattern", DataType.STRING, "Pattern"),
    ]
    DATA_OUT = [
        ("parsed", DataType.ANY, "Parsed"),
        ("items", DataType.ARRAY, "Items"),
    ]

    def _auto_size(self):
        self.setFixedSize(180, 90)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 4)
        layout.setSpacing(2)
        title = QLabel("Response Parser")
        title.setStyleSheet(
            f"color: {self._category_color}; font-weight: bold; font-size: 11px; "
            f"background: transparent; border: none;"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        mode = self.config.get("mode", "json") if self.config else "json"
        self._mode_label = QLabel(f"Mode: {mode}")
        self._mode_label.setStyleSheet(
            "color: #aaa; font-size: 9px; background: transparent; border: none;"
        )
        layout.addWidget(self._mode_label)
        self._content_layout = layout

    def set_config(self, config: dict):
        self.config = dict(config)
        if hasattr(self, '_mode_label'):
            self._mode_label.setText(f"Mode: {config.get('mode', 'json')}")


class ImageGeneratorNode(FuncNodeBase):
    NODE_TYPE = NodeType.IMAGE_GENERATOR
    DISPLAY_NAME = "Image Gen"
    IS_PURE = False
    EXEC_IN = True
    EXEC_OUT = ["exec_out"]
    DATA_IN = [("prompt", DataType.STRING, "Prompt")]
    DATA_OUT = [("image", DataType.IMAGE, "Image")]

    def _auto_size(self):
        self.setFixedSize(180, 70)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 4)
        layout.setSpacing(2)
        title = QLabel("Image Generator")
        title.setStyleSheet(
            f"color: {self._category_color}; font-weight: bold; font-size: 11px; "
            f"background: transparent; border: none;"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        model = self.config.get("model", "") if self.config else ""
        self._model_label = QLabel(f"Model: {model or '--'}")
        self._model_label.setStyleSheet(
            "color: #aaa; font-size: 9px; background: transparent; border: none;"
        )
        layout.addWidget(self._model_label)
        self._content_layout = layout

    def set_config(self, config: dict):
        self.config = dict(config)
        if hasattr(self, '_model_label'):
            self._model_label.setText(f"Model: {config.get('model', '') or '--'}")


# ══════════════════════════════════════════
# Data Processing Nodes (all pure)
# ══════════════════════════════════════════

class MathNode(FuncNodeBase):
    NODE_TYPE = NodeType.MATH
    DISPLAY_NAME = "Math"
    IS_PURE = True
    EXEC_IN = False
    EXEC_OUT = []
    DATA_IN = [("a", DataType.NUMBER, "A"), ("b", DataType.NUMBER, "B")]
    DATA_OUT = [("result", DataType.NUMBER, "Result")]

    def _auto_size(self):
        self.setFixedSize(140, 70)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 4)
        layout.setSpacing(2)
        title = QLabel("Math")
        title.setStyleSheet(
            f"color: {self._category_color}; font-weight: bold; font-size: 11px; "
            f"background: transparent; border: none;"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        op = self.config.get("op", "+") if self.config else "+"
        self._op_label = QLabel(f"Op: {op}")
        self._op_label.setStyleSheet(
            "color: #aaa; font-size: 10px; background: transparent; border: none;"
        )
        self._op_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._op_label)
        self._content_layout = layout

    def set_config(self, config: dict):
        self.config = dict(config)
        if hasattr(self, '_op_label'):
            self._op_label.setText(f"Op: {config.get('op', '+')}")


class CompareNode(FuncNodeBase):
    NODE_TYPE = NodeType.COMPARE
    DISPLAY_NAME = "Compare"
    IS_PURE = True
    EXEC_IN = False
    EXEC_OUT = []
    DATA_IN = [("a", DataType.ANY, "A"), ("b", DataType.ANY, "B")]
    DATA_OUT = [("result", DataType.BOOLEAN, "Result")]

    def _auto_size(self):
        self.setFixedSize(140, 70)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 4)
        layout.setSpacing(2)
        title = QLabel("Compare")
        title.setStyleSheet(
            f"color: {self._category_color}; font-weight: bold; font-size: 11px; "
            f"background: transparent; border: none;"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        op = self.config.get("op", "==") if self.config else "=="
        self._op_label = QLabel(f"Op: {op}")
        self._op_label.setStyleSheet(
            "color: #aaa; font-size: 10px; background: transparent; border: none;"
        )
        self._op_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._op_label)
        self._content_layout = layout

    def set_config(self, config: dict):
        self.config = dict(config)
        if hasattr(self, '_op_label'):
            self._op_label.setText(f"Op: {config.get('op', '==')}")


class StringOpNode(FuncNodeBase):
    NODE_TYPE = NodeType.STRING_OP
    DISPLAY_NAME = "String"
    IS_PURE = True
    EXEC_IN = False
    EXEC_OUT = []
    DATA_IN = [("text", DataType.STRING, "Text"), ("param", DataType.STRING, "Param")]
    DATA_OUT = [("result", DataType.STRING, "Result")]

    def _auto_size(self):
        self.setFixedSize(140, 70)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 4)
        layout.setSpacing(2)
        title = QLabel("String Op")
        title.setStyleSheet(
            f"color: {self._category_color}; font-weight: bold; font-size: 11px; "
            f"background: transparent; border: none;"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        op = self.config.get("op", "replace") if self.config else "replace"
        self._op_label = QLabel(f"Op: {op}")
        self._op_label.setStyleSheet(
            "color: #aaa; font-size: 10px; background: transparent; border: none;"
        )
        self._op_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._op_label)
        self._content_layout = layout

    def set_config(self, config: dict):
        self.config = dict(config)
        if hasattr(self, '_op_label'):
            self._op_label.setText(f"Op: {config.get('op', 'replace')}")


class ArrayOpNode(FuncNodeBase):
    NODE_TYPE = NodeType.ARRAY_OP
    DISPLAY_NAME = "Array"
    IS_PURE = True
    EXEC_IN = False
    EXEC_OUT = []
    DATA_IN = [("array", DataType.ARRAY, "Array"), ("item", DataType.ANY, "Item")]
    DATA_OUT = [("result", DataType.ARRAY, "Result"), ("element", DataType.ANY, "Element")]

    def _auto_size(self):
        self.setFixedSize(140, 70)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 4)
        layout.setSpacing(2)
        title = QLabel("Array Op")
        title.setStyleSheet(
            f"color: {self._category_color}; font-weight: bold; font-size: 11px; "
            f"background: transparent; border: none;"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        op = self.config.get("op", "push") if self.config else "push"
        self._op_label = QLabel(f"Op: {op}")
        self._op_label.setStyleSheet(
            "color: #aaa; font-size: 10px; background: transparent; border: none;"
        )
        self._op_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._op_label)
        self._content_layout = layout

    def set_config(self, config: dict):
        self.config = dict(config)
        if hasattr(self, '_op_label'):
            self._op_label.setText(f"Op: {config.get('op', 'push')}")


class JsonParseNode(FuncNodeBase):
    NODE_TYPE = NodeType.JSON_PARSE
    DISPLAY_NAME = "JSON Parse"
    IS_PURE = True
    EXEC_IN = False
    EXEC_OUT = []
    DATA_IN = [("text", DataType.STRING, "Text")]
    DATA_OUT = [("object", DataType.OBJECT, "Object")]

    def _auto_size(self):
        self.setFixedSize(140, 60)


class JsonPathNode(FuncNodeBase):
    NODE_TYPE = NodeType.JSON_PATH
    DISPLAY_NAME = "JSON Path"
    IS_PURE = True
    EXEC_IN = False
    EXEC_OUT = []
    DATA_IN = [("object", DataType.OBJECT, "Object"), ("path", DataType.STRING, "Path")]
    DATA_OUT = [("value", DataType.ANY, "Value")]

    def _auto_size(self):
        self.setFixedSize(140, 70)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 4)
        layout.setSpacing(2)
        title = QLabel("JSON Path")
        title.setStyleSheet(
            f"color: {self._category_color}; font-weight: bold; font-size: 11px; "
            f"background: transparent; border: none;"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        path = self.config.get("default_path", "") if self.config else ""
        self._path_label = QLabel(path or "--")
        self._path_label.setStyleSheet(
            "color: #aaa; font-size: 9px; background: transparent; border: none;"
        )
        layout.addWidget(self._path_label)
        self._content_layout = layout

    def set_config(self, config: dict):
        self.config = dict(config)
        if hasattr(self, '_path_label'):
            self._path_label.setText(config.get("default_path", "") or "--")


class TypeConvertNode(FuncNodeBase):
    NODE_TYPE = NodeType.TYPE_CONVERT
    DISPLAY_NAME = "Type Convert"
    IS_PURE = True
    EXEC_IN = False
    EXEC_OUT = []
    DATA_IN = [("input", DataType.ANY, "Input")]
    DATA_OUT = [("output", DataType.ANY, "Output")]  # Type set by config

    def _auto_size(self):
        self.setFixedSize(140, 60)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 4)
        layout.setSpacing(2)
        title = QLabel("Type Convert")
        title.setStyleSheet(
            f"color: {self._category_color}; font-weight: bold; font-size: 11px; "
            f"background: transparent; border: none;"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        target = self.config.get("target_type", "string") if self.config else "string"
        self._type_label = QLabel(f"-> {target}")
        self._type_label.setStyleSheet(
            "color: #aaa; font-size: 10px; background: transparent; border: none;"
        )
        self._type_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._type_label)
        self._content_layout = layout

    def set_config(self, config: dict):
        self.config = dict(config)
        if hasattr(self, '_type_label'):
            self._type_label.setText(f"-> {config.get('target_type', 'string')}")

    def get_port_defs(self) -> dict:
        result = super().get_port_defs()
        target = self.config.get("target_type", "string")
        try:
            dt = DataType(target)
        except ValueError:
            dt = DataType.STRING
        result["data_out"] = [("output", dt, "Output")]
        return result


# ══════════════════════════════════════════
# Variable Nodes
# ══════════════════════════════════════════

class GetVariableNode(FuncNodeBase):
    NODE_TYPE = NodeType.GET_VARIABLE
    DISPLAY_NAME = "Get Variable"
    IS_PURE = True
    EXEC_IN = False
    EXEC_OUT = []
    DATA_IN = []
    DATA_OUT = [("value", DataType.ANY, "Value")]

    def _auto_size(self):
        self.setFixedSize(140, 60)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 4)
        layout.setSpacing(2)
        title = QLabel("Get Variable")
        title.setStyleSheet(
            f"color: {self._category_color}; font-weight: bold; font-size: 11px; "
            f"background: transparent; border: none;"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        var = self.config.get("var_name", "") if self.config else ""
        self._var_label = QLabel(var or "--")
        self._var_label.setStyleSheet(
            "color: #aaa; font-size: 10px; background: transparent; border: none;"
        )
        self._var_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._var_label)
        self._content_layout = layout

    def set_config(self, config: dict):
        self.config = dict(config)
        if hasattr(self, '_var_label'):
            self._var_label.setText(config.get("var_name", "") or "--")


class SetVariableNode(FuncNodeBase):
    NODE_TYPE = NodeType.SET_VARIABLE
    DISPLAY_NAME = "Set Variable"
    IS_PURE = False
    EXEC_IN = True
    EXEC_OUT = ["exec_out"]
    DATA_IN = [("value", DataType.ANY, "Value")]
    DATA_OUT = [("value", DataType.ANY, "Value")]

    def _auto_size(self):
        self.setFixedSize(140, 70)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 4)
        layout.setSpacing(2)
        title = QLabel("Set Variable")
        title.setStyleSheet(
            f"color: {self._category_color}; font-weight: bold; font-size: 11px; "
            f"background: transparent; border: none;"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        var = self.config.get("var_name", "") if self.config else ""
        self._var_label = QLabel(var or "--")
        self._var_label.setStyleSheet(
            "color: #aaa; font-size: 10px; background: transparent; border: none;"
        )
        self._var_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._var_label)
        self._content_layout = layout

    def set_config(self, config: dict):
        self.config = dict(config)
        if hasattr(self, '_var_label'):
            self._var_label.setText(config.get("var_name", "") or "--")


class MakeLiteralNode(FuncNodeBase):
    NODE_TYPE = NodeType.MAKE_LITERAL
    DISPLAY_NAME = "Literal"
    IS_PURE = True
    EXEC_IN = False
    EXEC_OUT = []
    DATA_IN = []
    DATA_OUT = [("value", DataType.ANY, "Value")]  # Type from config

    def _auto_size(self):
        self.setFixedSize(140, 60)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 4)
        layout.setSpacing(2)
        title = QLabel("Literal")
        title.setStyleSheet(
            f"color: {self._category_color}; font-weight: bold; font-size: 11px; "
            f"background: transparent; border: none;"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        val = self.config.get("value", "") if self.config else ""
        preview = str(val)[:30] if val else "--"
        self._val_label = QLabel(preview)
        self._val_label.setStyleSheet(
            "color: #aaa; font-size: 9px; background: transparent; border: none;"
        )
        self._val_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._val_label)
        self._content_layout = layout

    def set_config(self, config: dict):
        self.config = dict(config)
        if hasattr(self, '_val_label'):
            val = config.get("value", "")
            preview = str(val)[:30] if val else "--"
            self._val_label.setText(preview)

    def get_port_defs(self) -> dict:
        result = super().get_port_defs()
        target = self.config.get("type", "string")
        try:
            dt = DataType(target)
        except ValueError:
            dt = DataType.STRING
        result["data_out"] = [("value", dt, "Value")]
        return result


# ──────────────────────────────────────────
# Node Type Registry
# ──────────────────────────────────────────

NODE_CLASSES = {
    NodeType.START: StartNode,
    NodeType.END: EndNode,
    NodeType.BRANCH: BranchNode,
    NodeType.SWITCH: SwitchNode,
    NodeType.FOR_EACH: ForEachNode,
    NodeType.WHILE_LOOP: WhileLoopNode,
    NodeType.SEQUENCE: SequenceNode,
    NodeType.LLM_CALL: LLMCallNode,
    NodeType.PROMPT_BUILDER: PromptBuilderNode,
    NodeType.RESPONSE_PARSER: ResponseParserNode,
    NodeType.IMAGE_GENERATOR: ImageGeneratorNode,
    NodeType.MATH: MathNode,
    NodeType.COMPARE: CompareNode,
    NodeType.STRING_OP: StringOpNode,
    NodeType.ARRAY_OP: ArrayOpNode,
    NodeType.JSON_PARSE: JsonParseNode,
    NodeType.JSON_PATH: JsonPathNode,
    NodeType.TYPE_CONVERT: TypeConvertNode,
    NodeType.GET_VARIABLE: GetVariableNode,
    NodeType.SET_VARIABLE: SetVariableNode,
    NodeType.MAKE_LITERAL: MakeLiteralNode,
}

# Category -> node types for palette
NODE_PALETTE = {
    "control_flow": [
        NodeType.START, NodeType.END, NodeType.BRANCH, NodeType.SWITCH,
        NodeType.FOR_EACH, NodeType.WHILE_LOOP, NodeType.SEQUENCE,
    ],
    "ai": [
        NodeType.LLM_CALL, NodeType.PROMPT_BUILDER,
        NodeType.RESPONSE_PARSER, NodeType.IMAGE_GENERATOR,
    ],
    "data": [
        NodeType.MATH, NodeType.COMPARE, NodeType.STRING_OP,
        NodeType.ARRAY_OP, NodeType.JSON_PARSE, NodeType.JSON_PATH,
        NodeType.TYPE_CONVERT,
    ],
    "variables": [
        NodeType.GET_VARIABLE, NodeType.SET_VARIABLE, NodeType.MAKE_LITERAL,
    ],
}
