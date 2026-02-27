"""
Blueprint-style function editor dialog
- NodePalette: categorized node palette with collapsible sections
- NodeConfigDialog: configuration dialog for all node types
- FunctionEditorView: graph editor view with connection validation
- FunctionEditorDialog: main dialog
"""
import uuid

from PyQt6.QtWidgets import (
    QDialog, QGraphicsView, QGraphicsScene, QGraphicsProxyWidget,
    QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QWidget, QFrame,
    QLineEdit, QTextEdit, QComboBox, QMessageBox, QScrollArea, QSpinBox,
)
from PyQt6.QtCore import Qt, QPointF, QRectF
from PyQt6.QtGui import QPainter, QPen, QBrush, QColor, QWheelEvent, QMouseEvent, QKeyEvent

from v.theme import Theme
from q import t
from v.model_plugin import get_all_models, get_all_model_ids, get_all_model_options
from .function_types import (
    FunctionDefinition, FunctionNode, FunctionEdge, FunctionParameter,
    DataType, NodeType, CATEGORY_COLORS,
)
from .function_nodes import (
    FuncPortItem, FuncTempEdgeItem, FuncEdgeItem, FuncNodeBase,
    NODE_CLASSES, NODE_PALETTE, StartNode, LLMCallNode,
    can_connect as validate_connection,
)


# ──────────────────────────────────────────
# Node Config Dialog
# ──────────────────────────────────────────

class NodeConfigDialog(QDialog):
    """Node configuration editor for all node types"""

    def __init__(self, node_widget: FuncNodeBase, parent=None):
        super().__init__(parent)
        self.node_widget = node_widget
        self.setWindowTitle(f"{node_widget.DISPLAY_NAME} Settings")
        self.setMinimumSize(450, 300)
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setStyleSheet("QDialog { background-color: #1e1e1e; }")
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        node_type = self.node_widget.NODE_TYPE
        config = self.node_widget.get_config()

        setup_map = {
            NodeType.LLM_CALL: self._setup_llm,
            NodeType.BRANCH: self._setup_branch,
            NodeType.SWITCH: self._setup_switch,
            NodeType.FOR_EACH: self._setup_loop,
            NodeType.WHILE_LOOP: self._setup_loop,
            NodeType.SEQUENCE: self._setup_sequence,
            NodeType.PROMPT_BUILDER: self._setup_prompt_builder,
            NodeType.RESPONSE_PARSER: self._setup_response_parser,
            NodeType.IMAGE_GENERATOR: self._setup_image_gen,
            NodeType.MATH: self._setup_math,
            NodeType.COMPARE: self._setup_compare,
            NodeType.STRING_OP: self._setup_string_op,
            NodeType.ARRAY_OP: self._setup_array_op,
            NodeType.JSON_PATH: self._setup_json_path,
            NodeType.TYPE_CONVERT: self._setup_type_convert,
            NodeType.GET_VARIABLE: self._setup_variable_name,
            NodeType.SET_VARIABLE: self._setup_variable_name,
            NodeType.MAKE_LITERAL: self._setup_literal,
        }

        setup_fn = setup_map.get(node_type)
        if setup_fn:
            setup_fn(layout, config)
        else:
            lbl = QLabel("No settings for this node.")
            lbl.setStyleSheet("color: #888;")
            layout.addWidget(lbl)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.setStyleSheet(
            "QPushButton { background: #333; color: #ddd; border: 1px solid #555; "
            "border-radius: 4px; padding: 8px 20px; } QPushButton:hover { background: #444; }"
        )
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        btn_save = QPushButton("Save")
        btn_save.setStyleSheet(
            "QPushButton { background: #0d6efd; color: white; border: none; "
            "border-radius: 4px; padding: 8px 20px; font-weight: bold; } "
            "QPushButton:hover { background: #0b5ed7; }"
        )
        btn_save.clicked.connect(self._save)
        btn_layout.addWidget(btn_save)
        layout.addLayout(btn_layout)

    def _add_label(self, layout, text):
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #ccc; font-size: 12px; font-weight: bold; margin-top: 8px;")
        layout.addWidget(lbl)

    def _make_line_edit(self, text="", placeholder=""):
        edit = QLineEdit(text)
        edit.setPlaceholderText(placeholder)
        edit.setStyleSheet(
            "QLineEdit { background: #252525; color: #ddd; border: 1px solid #444; "
            "border-radius: 4px; padding: 8px; font-size: 12px; } "
            "QLineEdit:focus { border-color: #0d6efd; }"
        )
        return edit

    def _make_combo(self, items, current=""):
        combo = QComboBox()
        combo.setStyleSheet(
            "QComboBox { background: #333; color: #ddd; border: 1px solid #444; "
            "border-radius: 4px; padding: 6px; font-size: 12px; } "
            "QComboBox:hover { border-color: #0d6efd; } "
            "QComboBox::drop-down { border: none; width: 24px; } "
            "QComboBox::down-arrow { border-left: 5px solid transparent; "
            "border-right: 5px solid transparent; border-top: 6px solid #888; margin-right: 8px; } "
            "QComboBox QAbstractItemView { background: #2d2d2d; color: #ddd; "
            "border: 1px solid #444; selection-background-color: #0d6efd; }"
        )
        for item in items:
            if isinstance(item, tuple):
                combo.addItem(item[0], item[1])
            else:
                combo.addItem(item, item)
        if current:
            idx = combo.findData(current)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        combo.setMaxVisibleItems(15)
        return combo

    # ── Setup methods for each node type ──

    def _setup_llm(self, layout, config):
        self._add_label(layout, "Model")
        _all_models = get_all_models()
        model_items = [(name, mid) for mid, name in _all_models.items()]
        self._model_combo = self._make_combo(model_items, config.get("model", ""))
        layout.addWidget(self._model_combo)

        self._add_label(layout, "Prompt Template")
        desc = QLabel(
            "{input} = initial input\n"
            "{in_0}, {in_1} = connected arg port values\n"
            "{param:name} = function parameter\n"
            "{var:name} = stored variable"
        )
        desc.setStyleSheet("color: #666; font-size: 10px;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self._prompt_edit = QTextEdit()
        self._prompt_edit.setPlainText(config.get("prompt_template", ""))
        self._prompt_edit.setStyleSheet(
            "QTextEdit { background: #252525; color: #ddd; border: 1px solid #444; "
            "border-radius: 4px; padding: 8px; font-size: 12px; font-family: 'Consolas', monospace; } "
            "QTextEdit:focus { border-color: #0d6efd; }"
        )
        self._prompt_edit.setMinimumHeight(120)
        self._prompt_edit.setMaximumHeight(250)
        layout.addWidget(self._prompt_edit)

    def _setup_branch(self, layout, config):
        self._add_label(layout, "Branch Node")
        desc = QLabel("Connect a Boolean data pin to the Condition input.\nTrue/False exec outputs will fire accordingly.")
        desc.setStyleSheet("color: #888; font-size: 11px;")
        desc.setWordWrap(True)
        layout.addWidget(desc)
        layout.addStretch()

    def _setup_switch(self, layout, config):
        self._add_label(layout, "Cases (one per line)")
        self._cases_edit = QTextEdit()
        cases = config.get("cases", ["case_A", "case_B"])
        self._cases_edit.setPlainText("\n".join(cases))
        self._cases_edit.setStyleSheet(
            "QTextEdit { background: #252525; color: #ddd; border: 1px solid #444; "
            "border-radius: 4px; padding: 8px; font-size: 12px; } "
            "QTextEdit:focus { border-color: #0d6efd; }"
        )
        self._cases_edit.setMaximumHeight(150)
        layout.addWidget(self._cases_edit)
        layout.addStretch()

    def _setup_loop(self, layout, config):
        self._add_label(layout, "Max Iterations")
        self._max_iter_spin = QSpinBox()
        self._max_iter_spin.setRange(1, 1000)
        self._max_iter_spin.setValue(config.get("max_iter", 100))
        self._max_iter_spin.setStyleSheet(
            "QSpinBox { background: #252525; color: #ddd; border: 1px solid #444; "
            "border-radius: 4px; padding: 6px; }"
        )
        layout.addWidget(self._max_iter_spin)
        layout.addStretch()

    def _setup_sequence(self, layout, config):
        self._add_label(layout, "Number of Outputs")
        self._seq_count_spin = QSpinBox()
        self._seq_count_spin.setRange(2, 10)
        self._seq_count_spin.setValue(config.get("output_count", 2))
        self._seq_count_spin.setStyleSheet(
            "QSpinBox { background: #252525; color: #ddd; border: 1px solid #444; "
            "border-radius: 4px; padding: 6px; }"
        )
        layout.addWidget(self._seq_count_spin)
        layout.addStretch()

    def _setup_prompt_builder(self, layout, config):
        self._add_label(layout, "Template")
        desc = QLabel("{system}, {user}, {context} = connected inputs")
        desc.setStyleSheet("color: #666; font-size: 10px;")
        layout.addWidget(desc)

        self._template_edit = QTextEdit()
        self._template_edit.setPlainText(config.get("template", "{system}\n\n{user}\n\n{context}"))
        self._template_edit.setStyleSheet(
            "QTextEdit { background: #252525; color: #ddd; border: 1px solid #444; "
            "border-radius: 4px; padding: 8px; font-size: 12px; } "
            "QTextEdit:focus { border-color: #0d6efd; }"
        )
        self._template_edit.setMaximumHeight(200)
        layout.addWidget(self._template_edit)

    def _setup_response_parser(self, layout, config):
        self._add_label(layout, "Parse Mode")
        self._mode_combo = self._make_combo(
            [("JSON", "json"), ("Regex", "regex"), ("Split Lines", "split")],
            config.get("mode", "json"),
        )
        layout.addWidget(self._mode_combo)
        layout.addStretch()

    def _setup_image_gen(self, layout, config):
        self._add_label(layout, "Model")
        _all_models = get_all_models()
        model_items = [(name, mid) for mid, name in _all_models.items()]
        self._img_model_combo = self._make_combo(model_items, config.get("model", ""))
        layout.addWidget(self._img_model_combo)

        self._add_label(layout, "Aspect Ratio")
        model_id = config.get("model", "")
        all_opts = get_all_model_options()
        ar_spec = all_opts.get(model_id, {}).get("aspect_ratio", {})
        ratio_values = ar_spec.get("values", ["1:1", "16:9", "9:16", "4:3", "3:4"])
        self._ratio_combo = self._make_combo(
            ratio_values,
            config.get("aspect_ratio", "1:1"),
        )
        layout.addWidget(self._ratio_combo)
        layout.addStretch()

    def _setup_math(self, layout, config):
        self._add_label(layout, "Operation")
        self._op_combo = self._make_combo(
            ["+", "-", "*", "/", "%", "pow", "min", "max"],
            config.get("op", "+"),
        )
        layout.addWidget(self._op_combo)
        layout.addStretch()

    def _setup_compare(self, layout, config):
        self._add_label(layout, "Operator")
        self._op_combo = self._make_combo(
            ["==", "!=", "<", ">", "<=", ">=", "contains", "starts_with", "ends_with"],
            config.get("op", "=="),
        )
        layout.addWidget(self._op_combo)
        layout.addStretch()

    def _setup_string_op(self, layout, config):
        self._add_label(layout, "Operation")
        self._op_combo = self._make_combo(
            ["replace", "split", "join", "trim", "upper", "lower",
             "format", "regex", "substring", "length"],
            config.get("op", "trim"),
        )
        layout.addWidget(self._op_combo)
        layout.addStretch()

    def _setup_array_op(self, layout, config):
        self._add_label(layout, "Operation")
        self._op_combo = self._make_combo(
            ["push", "pop", "filter", "find", "length", "slice", "sort", "reverse", "flatten"],
            config.get("op", "push"),
        )
        layout.addWidget(self._op_combo)
        layout.addStretch()

    def _setup_json_path(self, layout, config):
        self._add_label(layout, "Default Path")
        self._path_edit = self._make_line_edit(config.get("default_path", ""), "key1.key2.0")
        layout.addWidget(self._path_edit)
        layout.addStretch()

    def _setup_type_convert(self, layout, config):
        self._add_label(layout, "Target Type")
        self._type_combo = self._make_combo(
            ["string", "number", "boolean", "array", "object"],
            config.get("target_type", "string"),
        )
        layout.addWidget(self._type_combo)
        layout.addStretch()

    def _setup_variable_name(self, layout, config):
        self._add_label(layout, "Variable Name")
        self._var_edit = self._make_line_edit(config.get("var_name", ""), "my_variable")
        layout.addWidget(self._var_edit)
        layout.addStretch()

    def _setup_literal(self, layout, config):
        self._add_label(layout, "Type")
        self._lit_type_combo = self._make_combo(
            ["string", "number", "boolean", "array", "object"],
            config.get("type", "string"),
        )
        layout.addWidget(self._lit_type_combo)

        self._add_label(layout, "Value")
        self._lit_value_edit = QTextEdit()
        self._lit_value_edit.setPlainText(str(config.get("value", "")))
        self._lit_value_edit.setStyleSheet(
            "QTextEdit { background: #252525; color: #ddd; border: 1px solid #444; "
            "border-radius: 4px; padding: 8px; font-size: 12px; } "
            "QTextEdit:focus { border-color: #0d6efd; }"
        )
        self._lit_value_edit.setMaximumHeight(120)
        layout.addWidget(self._lit_value_edit)
        layout.addStretch()

    # ── Save ──

    def _save(self):
        node_type = self.node_widget.NODE_TYPE
        config = dict(self.node_widget.get_config())  # Preserve existing config

        if node_type == NodeType.LLM_CALL:
            config["model"] = self._model_combo.currentData()
            config["prompt_template"] = self._prompt_edit.toPlainText()
        elif node_type == NodeType.SWITCH:
            text = self._cases_edit.toPlainText().strip()
            config["cases"] = [c.strip() for c in text.split("\n") if c.strip()]
        elif node_type in (NodeType.FOR_EACH, NodeType.WHILE_LOOP):
            config["max_iter"] = self._max_iter_spin.value()
        elif node_type == NodeType.SEQUENCE:
            config["output_count"] = self._seq_count_spin.value()
        elif node_type == NodeType.PROMPT_BUILDER:
            config["template"] = self._template_edit.toPlainText()
        elif node_type == NodeType.RESPONSE_PARSER:
            config["mode"] = self._mode_combo.currentData()
        elif node_type == NodeType.IMAGE_GENERATOR:
            config["model"] = self._img_model_combo.currentData()
            config["aspect_ratio"] = self._ratio_combo.currentData()
        elif node_type == NodeType.MATH:
            config["op"] = self._op_combo.currentData()
        elif node_type == NodeType.COMPARE:
            config["op"] = self._op_combo.currentData()
        elif node_type == NodeType.STRING_OP:
            config["op"] = self._op_combo.currentData()
        elif node_type == NodeType.ARRAY_OP:
            config["op"] = self._op_combo.currentData()
        elif node_type == NodeType.JSON_PATH:
            config["default_path"] = self._path_edit.text()
        elif node_type == NodeType.TYPE_CONVERT:
            config["target_type"] = self._type_combo.currentData()
        elif node_type in (NodeType.GET_VARIABLE, NodeType.SET_VARIABLE):
            config["var_name"] = self._var_edit.text()
        elif node_type == NodeType.MAKE_LITERAL:
            config["type"] = self._lit_type_combo.currentData()
            config["value"] = self._lit_value_edit.toPlainText()

        self.node_widget.set_config(config)
        self.accept()


# ──────────────────────────────────────────
# Node Palette
# ──────────────────────────────────────────

class NodePalette(QWidget):
    """Categorized node palette with collapsible sections"""

    CATEGORY_LABELS = {
        "control_flow": "Control Flow",
        "ai": "AI",
        "data": "Data",
        "variables": "Variables",
    }

    def __init__(self, on_add_node):
        super().__init__()
        self.on_add_node = on_add_node
        self.setFixedWidth(140)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.setSpacing(2)

        title = QLabel("Nodes")
        title.setStyleSheet("color: #888; font-weight: bold; font-size: 11px;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background: #333;")
        layout.addWidget(sep)

        for category, node_types in NODE_PALETTE.items():
            color = CATEGORY_COLORS.get(category, "#555555")
            cat_label = self.CATEGORY_LABELS.get(category, category)

            # Category header
            header = QLabel(f"  {cat_label}")
            header.setStyleSheet(
                f"color: {color}; font-weight: bold; font-size: 10px; "
                f"padding: 4px 0 2px 0;"
            )
            layout.addWidget(header)

            for nt in node_types:
                cls = NODE_CLASSES.get(nt)
                if not cls:
                    continue
                btn = QPushButton(cls.DISPLAY_NAME)
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {color}33;
                        color: {color};
                        border: 1px solid {color}88;
                        border-radius: 4px;
                        padding: 4px;
                        font-size: 10px;
                        font-weight: bold;
                        text-align: left;
                        padding-left: 8px;
                    }}
                    QPushButton:hover {{ background: {color}66; color: white; }}
                """)
                btn.clicked.connect(lambda _, n=nt: self.on_add_node(n))
                layout.addWidget(btn)

        layout.addStretch()


# ──────────────────────────────────────────
# Editor View
# ──────────────────────────────────────────

class FunctionEditorView(QGraphicsView):
    """Function graph editor mini-view"""

    def __init__(self, scene: QGraphicsScene, editor: "FunctionEditorDialog"):
        super().__init__(scene)
        self.editor = editor

        self._panning = False
        self._pan_start = QPointF()
        self._pan_scroll_start_h = 0
        self._pan_scroll_start_v = 0
        self._zoom = 1.0

        self._selecting = False
        self._selection_start = None
        self._selection_rect = None
        self._selection_add_mode = False

        self._port_dragging = False
        self._drag_source_port = None
        self._temp_edge = None
        self._drag_reverse = False

        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

    def drawBackground(self, painter: QPainter, rect: QRectF):
        painter.fillRect(rect, QColor("#1e1e1e"))
        grid_size = 40
        dot_size = 1.5
        dot_color = QColor("#282828")

        left = int(rect.left()) - (int(rect.left()) % grid_size)
        top = int(rect.top()) - (int(rect.top()) % grid_size)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(dot_color))

        x = left
        while x < rect.right():
            y = top
            while y < rect.bottom():
                painter.drawEllipse(QPointF(x, y), dot_size, dot_size)
                y += grid_size
            x += grid_size

    def drawForeground(self, painter: QPainter, rect: QRectF):
        super().drawForeground(painter, rect)

        if self._selecting and self._selection_rect:
            painter.setPen(QPen(QColor("#0d6efd"), 1, Qt.PenStyle.DashLine))
            painter.setBrush(QBrush(QColor(13, 110, 253, 30)))
            painter.drawRect(self._selection_rect)

        pen = QPen(QColor("#0d6efd"), 2)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for item in self.scene().selectedItems():
            if isinstance(item, QGraphicsProxyWidget):
                painter.drawRect(item.sceneBoundingRect())

    def wheelEvent(self, event: QWheelEvent):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        new_zoom = self._zoom * factor
        if 0.3 <= new_zoom <= 3.0:
            self._zoom = new_zoom
            self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
            self.scale(factor, factor)

    def mousePressEvent(self, event: QMouseEvent):
        self.scene().clearFocus()

        if event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            self._panning = True
            self._pan_start = event.position()
            self._pan_scroll_start_h = self.horizontalScrollBar().value()
            self._pan_scroll_start_v = self.verticalScrollBar().value()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        if event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.pos())

            # 1) Port click
            port = self._find_port_at(event.pos())
            if port:
                scene_pos = self.mapToScene(event.pos())
                if port.port_type == FuncPortItem.INPUT and port.edges:
                    old_edge = port.edges[0]
                    reroute_port = old_edge.source_port
                    self.editor._remove_edge(old_edge)
                    self._start_port_drag(reroute_port, scene_pos)
                else:
                    self._start_port_drag(port, scene_pos)
                event.accept()
                return

            # 2) Edge click
            if isinstance(item, FuncEdgeItem):
                if not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                    self.scene().clearSelection()
                item.setSelected(
                    not item.isSelected()
                    if event.modifiers() & Qt.KeyboardModifier.ControlModifier
                    else True
                )
                event.accept()
                return

            # 3) Proxy click
            if isinstance(item, QGraphicsProxyWidget) and item.flags() & QGraphicsProxyWidget.GraphicsItemFlag.ItemIsSelectable:
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    item.setSelected(not item.isSelected())
                    event.accept()
                    return
                else:
                    item.setFlag(QGraphicsProxyWidget.GraphicsItemFlag.ItemIsSelectable, False)
                    super().mousePressEvent(event)
                    item.setFlag(QGraphicsProxyWidget.GraphicsItemFlag.ItemIsSelectable, True)
                    return

            # 4) Empty area -> rubber band
            if item is None:
                self._selection_add_mode = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                if not self._selection_add_mode:
                    self.scene().clearSelection()
                self._selecting = True
                self._selection_start = self.mapToScene(event.pos())
                self._selection_rect = QRectF(self._selection_start, self._selection_start)
                event.accept()
            else:
                super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._port_dragging and self._temp_edge:
            scene_pos = self.mapToScene(event.pos())
            if self._drag_reverse:
                self._temp_edge.set_start(scene_pos)
            else:
                self._temp_edge.set_end(scene_pos)
            if self._drag_source_port:
                src_pos = self._drag_source_port.scenePos()
                if self._drag_reverse:
                    self._temp_edge.set_end(src_pos)
                else:
                    self._temp_edge.set_start(src_pos)
            return

        if self._panning:
            total_delta = event.position() - self._pan_start
            self.horizontalScrollBar().setValue(int(self._pan_scroll_start_h - total_delta.x()))
            self.verticalScrollBar().setValue(int(self._pan_scroll_start_v - total_delta.y()))
        elif self._selecting and self._selection_start:
            current = self.mapToScene(event.pos())
            self._selection_rect = QRectF(self._selection_start, current).normalized()
            self._update_rubber_band_selection()
            self.viewport().update()
        else:
            super().mouseMoveEvent(event)
            self._update_all_ports()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            self._panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return

        if event.button() == Qt.MouseButton.LeftButton and self._port_dragging:
            scene_pos = self.mapToScene(event.pos())
            self._complete_port_drag(scene_pos)
            return

        if event.button() == Qt.MouseButton.LeftButton and self._selecting:
            self._selecting = False
            self._selection_start = None
            self._selection_rect = None
            self._selection_add_mode = False
            self.viewport().update()
            return

        super().mouseReleaseEvent(event)
        self._update_all_ports()

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        item = self.itemAt(event.pos())
        if isinstance(item, QGraphicsProxyWidget):
            widget = item.widget()
            if isinstance(widget, FuncNodeBase) and widget.NODE_TYPE not in (NodeType.START, NodeType.END):
                dialog = NodeConfigDialog(widget, parent=self.editor)
                if dialog.exec() == QDialog.DialogCode.Accepted:
                    # Rebuild ports for nodes with dynamic ports
                    if widget.NODE_TYPE in (NodeType.SWITCH, NodeType.SEQUENCE):
                        self.editor._rebuild_node_ports(widget.node_id)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Delete:
            self._delete_selected()
            event.accept()
        elif event.key() == Qt.Key.Key_A and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self._select_all()
            event.accept()
        else:
            super().keyPressEvent(event)

    # ── Rubber band selection ──

    def _update_rubber_band_selection(self):
        if not self._selection_rect:
            return
        for item in self.scene().items():
            if isinstance(item, QGraphicsProxyWidget) and item.flags() & QGraphicsProxyWidget.GraphicsItemFlag.ItemIsSelectable:
                in_rect = self._selection_rect.intersects(item.sceneBoundingRect())
                if self._selection_add_mode:
                    if in_rect:
                        item.setSelected(True)
                else:
                    item.setSelected(in_rect)
            elif isinstance(item, FuncEdgeItem):
                s = item.source_port.scenePos()
                t_pos = item.target_port.scenePos()
                in_rect = self._selection_rect.contains(s) or self._selection_rect.contains(t_pos)
                if self._selection_add_mode:
                    if in_rect:
                        item.setSelected(True)
                else:
                    item.setSelected(in_rect)

    def _select_all(self):
        for item in self.scene().items():
            if isinstance(item, QGraphicsProxyWidget) and item.flags() & QGraphicsProxyWidget.GraphicsItemFlag.ItemIsSelectable:
                item.setSelected(True)
            elif isinstance(item, FuncEdgeItem):
                item.setSelected(True)

    # ── Port drag ──

    def _find_port_at(self, pos):
        item = self.itemAt(pos)
        check = item
        while check:
            if isinstance(check, FuncPortItem):
                return check
            check = check.parentItem()
        return None

    def _start_port_drag(self, port: FuncPortItem, scene_pos: QPointF):
        self._port_dragging = True
        self._drag_source_port = port
        start = port.scenePos()

        if port.port_type == FuncPortItem.OUTPUT:
            self._drag_reverse = False
            self._temp_edge = FuncTempEdgeItem(start)
            self._temp_edge.set_end(scene_pos)
        else:
            self._drag_reverse = True
            self._temp_edge = FuncTempEdgeItem(scene_pos)
            self._temp_edge.set_end(start)

        self.scene().addItem(self._temp_edge)

    def _complete_port_drag(self, scene_pos: QPointF):
        if self._temp_edge:
            self.scene().removeItem(self._temp_edge)
            self._temp_edge = None

        target_port = self._find_port_at(self.mapFromScene(scene_pos))

        if target_port and target_port != self._drag_source_port:
            if self._drag_reverse:
                self.editor.create_edge(target_port, self._drag_source_port)
            else:
                self.editor.create_edge(self._drag_source_port, target_port)

        self._port_dragging = False
        self._drag_source_port = None
        self._drag_reverse = False

    # ── Util ──

    def _update_all_ports(self):
        for item in self.scene().items():
            if isinstance(item, FuncPortItem):
                item.reposition()
        for item in self.scene().items():
            if isinstance(item, FuncEdgeItem):
                item.update_path()

    def _delete_selected(self):
        for item in list(self.scene().selectedItems()):
            if isinstance(item, FuncEdgeItem):
                self.editor._remove_edge(item)
            elif isinstance(item, QGraphicsProxyWidget):
                widget = item.widget()
                if isinstance(widget, FuncNodeBase):
                    self.editor.remove_internal_node(widget.node_id)


# ──────────────────────────────────────────
# Main Editor Dialog
# ──────────────────────────────────────────

class FunctionEditorDialog(QDialog):
    """Function graph editor dialog"""

    def __init__(self, function_def: FunctionDefinition = None, on_save=None, parent=None):
        super().__init__(parent)
        self.function_def = function_def or FunctionDefinition.create_default()
        self.on_save = on_save

        self.setWindowTitle(t("function.editor_title"))
        self.setMinimumSize(900, 600)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowCloseButtonHint)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setStyleSheet("QDialog { background-color: #1a1a1a; }")

        self.editor_scene = QGraphicsScene()
        self.editor_scene.setSceneRect(-2000, -2000, 4000, 4000)

        self.func_nodes: dict[str, FuncNodeBase] = {}
        self.func_proxies: dict[str, QGraphicsProxyWidget] = {}
        self.func_ports: dict[str, list[FuncPortItem]] = {}
        self.func_edges: list[FuncEdgeItem] = []

        self._setup_ui()
        self._restore_from_definition()

        self.editor_scene.changed.connect(self._on_scene_changed)

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        # Top bar: name + description
        top_bar = QHBoxLayout()

        name_label = QLabel("Name:")
        name_label.setStyleSheet("color: #888; font-size: 11px;")
        top_bar.addWidget(name_label)

        self._name_edit = QLineEdit(self.function_def.name)
        self._name_edit.setStyleSheet(
            "QLineEdit { background: #252525; color: #ddd; border: 1px solid #444; "
            "border-radius: 4px; padding: 6px; font-size: 12px; } "
            "QLineEdit:focus { border-color: #0d6efd; }"
        )
        self._name_edit.setMaximumWidth(250)
        top_bar.addWidget(self._name_edit)

        desc_label = QLabel("Desc:")
        desc_label.setStyleSheet("color: #888; font-size: 11px;")
        top_bar.addWidget(desc_label)

        self._desc_edit = QLineEdit(self.function_def.description)
        self._desc_edit.setStyleSheet(
            "QLineEdit { background: #252525; color: #ddd; border: 1px solid #444; "
            "border-radius: 4px; padding: 6px; font-size: 12px; } "
            "QLineEdit:focus { border-color: #0d6efd; }"
        )
        top_bar.addWidget(self._desc_edit)

        main_layout.addLayout(top_bar)

        # Center: palette + editor view
        center = QHBoxLayout()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(152)
        scroll.setStyleSheet(
            "QScrollArea { background: #1a1a1a; border-right: 1px solid #333; border: none; } "
            "QScrollBar:vertical { width: 4px; background: transparent; } "
            "QScrollBar::handle:vertical { background: #444; border-radius: 2px; }"
        )
        self._palette = NodePalette(self._add_node_from_palette)
        scroll.setWidget(self._palette)
        center.addWidget(scroll)

        self.editor_view = FunctionEditorView(self.editor_scene, editor=self)
        center.addWidget(self.editor_view, stretch=1)

        main_layout.addLayout(center, stretch=1)

        # Bottom buttons
        bottom = QHBoxLayout()

        btn_validate = QPushButton(t("function.validate"))
        btn_validate.setStyleSheet(
            "QPushButton { background: #333; color: #ddd; border: 1px solid #555; "
            "border-radius: 4px; padding: 8px 16px; } QPushButton:hover { background: #444; }"
        )
        btn_validate.clicked.connect(self._validate)
        bottom.addWidget(btn_validate)

        bottom.addStretch()

        btn_cancel = QPushButton("Cancel")
        btn_cancel.setStyleSheet(
            "QPushButton { background: #333; color: #ddd; border: 1px solid #555; "
            "border-radius: 4px; padding: 8px 20px; } QPushButton:hover { background: #444; }"
        )
        btn_cancel.clicked.connect(self.reject)
        bottom.addWidget(btn_cancel)

        btn_save = QPushButton("Save")
        btn_save.setStyleSheet(
            "QPushButton { background: #0d6efd; color: white; border: none; "
            "border-radius: 4px; padding: 8px 20px; font-weight: bold; } "
            "QPushButton:hover { background: #0b5ed7; }"
        )
        btn_save.clicked.connect(self._save)
        bottom.addWidget(btn_save)

        main_layout.addLayout(bottom)

    def _add_node_from_palette(self, node_type: str):
        if node_type == NodeType.START:
            existing = [w for w in self.func_nodes.values() if w.NODE_TYPE == NodeType.START]
            if existing:
                return
        center = self.editor_view.mapToScene(
            self.editor_view.viewport().rect().center()
        )
        self.add_internal_node(node_type, center.x(), center.y())

    def add_internal_node(self, node_type: str, x: float, y: float,
                          node_id: str = None, config: dict = None) -> str:
        node_id = node_id or str(uuid.uuid4())
        cls = NODE_CLASSES.get(node_type)
        if not cls:
            return node_id

        widget = cls(node_id, config)
        proxy = QGraphicsProxyWidget()
        proxy.setWidget(widget)
        proxy.setPos(x - widget.width() / 2, y - widget.height() / 2)
        proxy.setFlag(QGraphicsProxyWidget.GraphicsItemFlag.ItemIsMovable, True)
        proxy.setFlag(QGraphicsProxyWidget.GraphicsItemFlag.ItemIsSelectable, True)
        widget.proxy = proxy

        self.editor_scene.addItem(proxy)
        self.func_nodes[node_id] = widget
        self.func_proxies[node_id] = proxy

        # Callbacks
        if isinstance(widget, StartNode):
            widget.on_params_changed = self._on_start_params_changed
        if isinstance(widget, LLMCallNode):
            widget.on_inputs_changed = self._on_llm_inputs_changed

        # Create ports from port definitions
        self._create_ports_for_node(node_id, widget, proxy)

        return node_id

    def _create_ports_for_node(self, node_id: str, widget: FuncNodeBase,
                               proxy: QGraphicsProxyWidget):
        """Create FuncPortItems for a node based on its port definitions"""
        port_defs = widget.get_port_defs()
        ports = []

        # Exec in ports
        for port_id, data_type, label in port_defs["exec_in"]:
            port = FuncPortItem(port_id, FuncPortItem.INPUT, data_type, proxy, label)
            self.editor_scene.addItem(port)
            ports.append(port)

        # Data in ports
        for port_id, data_type, label in port_defs["data_in"]:
            port = FuncPortItem(port_id, FuncPortItem.INPUT, data_type, proxy, label)
            self.editor_scene.addItem(port)
            ports.append(port)

        # Exec out ports
        for port_id, data_type, label in port_defs["exec_out"]:
            port = FuncPortItem(port_id, FuncPortItem.OUTPUT, data_type, proxy, label)
            self.editor_scene.addItem(port)
            ports.append(port)

        # Data out ports
        for port_id, data_type, label in port_defs["data_out"]:
            port = FuncPortItem(port_id, FuncPortItem.OUTPUT, data_type, proxy, label)
            self.editor_scene.addItem(port)
            ports.append(port)

        self.func_ports[node_id] = ports

        for port in ports:
            port.reposition()

    def _rebuild_node_ports(self, node_id: str):
        """Rebuild all ports for a node (after config change like Switch cases)"""
        widget = self.func_nodes.get(node_id)
        proxy = self.func_proxies.get(node_id)
        if not widget or not proxy:
            return

        # Remove all existing ports and connected edges
        old_ports = self.func_ports.get(node_id, [])
        for port in list(old_ports):
            for edge in list(port.edges):
                self._remove_edge(edge)
            if port.scene():
                self.editor_scene.removeItem(port)

        self.func_ports[node_id] = []

        # Re-create ports
        self._create_ports_for_node(node_id, widget, proxy)

    def _on_start_params_changed(self, node_id: str, new_data_out_defs: list):
        """StartNode parameters changed -> rebuild output ports"""
        self._rebuild_node_ports(node_id)

    def _on_llm_inputs_changed(self, node_id: str, port_defs: dict):
        """LLMCallNode input ports changed -> rebuild ports preserving edges"""
        widget = self.func_nodes.get(node_id)
        proxy = self.func_proxies.get(node_id)
        if not widget or not proxy:
            return

        # Save existing edge connections
        edge_connections = []
        old_ports = self.func_ports.get(node_id, [])
        for port in list(old_ports):
            if port.port_type == FuncPortItem.INPUT:
                for edge in list(port.edges):
                    edge_connections.append((edge.source_port, port.port_id))
                    edge.disconnect()
                    if edge.scene():
                        self.editor_scene.removeItem(edge)
                    if edge in self.func_edges:
                        self.func_edges.remove(edge)
                if port.scene():
                    self.editor_scene.removeItem(port)
                old_ports.remove(port)

        # Create new input ports
        new_ports_dict = {}
        for port_id, data_type, label in port_defs.get("exec_in", []) + port_defs.get("data_in", []):
            port = FuncPortItem(port_id, FuncPortItem.INPUT, data_type, proxy, label)
            self.editor_scene.addItem(port)
            old_ports.insert(0, port)
            port.reposition()
            new_ports_dict[port_id] = port

        self.func_ports[node_id] = old_ports

        # Restore edges
        for source_port, target_port_id in edge_connections:
            target_port = new_ports_dict.get(target_port_id)
            if target_port:
                edge = FuncEdgeItem(source_port, target_port)
                self.editor_scene.addItem(edge)
                self.func_edges.append(edge)

    def remove_internal_node(self, node_id: str):
        ports = self.func_ports.get(node_id, [])
        for port in ports:
            for edge in list(port.edges):
                self._remove_edge(edge)

        for port in ports:
            if port.scene():
                self.editor_scene.removeItem(port)
        self.func_ports.pop(node_id, None)

        proxy = self.func_proxies.pop(node_id, None)
        if proxy and proxy.scene():
            self.editor_scene.removeItem(proxy)
        self.func_nodes.pop(node_id, None)

    def _remove_edge(self, edge: FuncEdgeItem):
        target_widget = edge.target_port.parent_proxy.widget() if edge.target_port.parent_proxy else None

        edge.disconnect()
        if edge.scene():
            self.editor_scene.removeItem(edge)
        if edge in self.func_edges:
            self.func_edges.remove(edge)

        if isinstance(target_widget, LLMCallNode):
            connected_port_ids = set()
            ports = self.func_ports.get(target_widget.node_id, [])
            for port in ports:
                if port.port_type == FuncPortItem.INPUT and port.edges:
                    connected_port_ids.add(port.port_id)
            target_widget.sync_arg_ports(connected_port_ids)

    def create_edge(self, source_port: FuncPortItem, target_port: FuncPortItem) -> bool:
        # Ensure OUTPUT -> INPUT
        if source_port.port_type == FuncPortItem.INPUT:
            source_port, target_port = target_port, source_port

        # Validate connection
        if not validate_connection(source_port, target_port):
            return False

        # No duplicate edges
        for edge in self.func_edges:
            if edge.source_port is source_port and edge.target_port is target_port:
                return False

        # Single connection per input port (for exec: also single)
        for edge in list(target_port.edges):
            self._remove_edge(edge)

        edge = FuncEdgeItem(source_port, target_port)
        self.editor_scene.addItem(edge)
        self.func_edges.append(edge)

        # LLMCallNode port sync
        target_widget = target_port.parent_proxy.widget() if target_port.parent_proxy else None
        if isinstance(target_widget, LLMCallNode):
            connected_port_ids = set()
            ports = self.func_ports.get(target_widget.node_id, [])
            for port in ports:
                if port.port_type == FuncPortItem.INPUT and port.edges:
                    connected_port_ids.add(port.port_id)
            target_widget.sync_arg_ports(connected_port_ids)

        return True

    def _on_scene_changed(self, regions):
        for item in self.editor_scene.items():
            if isinstance(item, FuncPortItem):
                item.reposition()
        for item in self.editor_scene.items():
            if isinstance(item, FuncEdgeItem):
                item.update_path()

    def _restore_from_definition(self):
        for node_data in self.function_def.nodes:
            self.add_internal_node(
                node_data.node_type, node_data.x, node_data.y,
                node_id=node_data.node_id, config=node_data.config,
            )

        for edge_data in self.function_def.edges:
            source_port = self._find_port(edge_data.source_node_id, edge_data.source_port_id)
            target_port = self._find_port(edge_data.target_node_id, edge_data.target_port_id)
            if source_port and target_port:
                edge = FuncEdgeItem(source_port, target_port)
                self.editor_scene.addItem(edge)
                self.func_edges.append(edge)

        # LLMCallNode port sync
        for node_id, widget in self.func_nodes.items():
            if isinstance(widget, LLMCallNode):
                connected_port_ids = set()
                ports = self.func_ports.get(node_id, [])
                for port in ports:
                    if port.port_type == FuncPortItem.INPUT and port.edges:
                        connected_port_ids.add(port.port_id)
                widget.sync_arg_ports(connected_port_ids)

    def _find_port(self, node_id: str, port_id: str) -> FuncPortItem | None:
        for port in self.func_ports.get(node_id, []):
            if port.port_id == port_id:
                return port
        return None

    def _collect_definition(self) -> FunctionDefinition:
        import time
        nodes = []
        parameters = []

        for node_id, widget in self.func_nodes.items():
            proxy = self.func_proxies.get(node_id)
            pos = proxy.pos() if proxy else QPointF(0, 0)
            nodes.append(FunctionNode(
                node_id=node_id,
                node_type=widget.NODE_TYPE,
                x=pos.x(),
                y=pos.y(),
                config=widget.get_config(),
            ))

            if isinstance(widget, StartNode):
                for p in widget.config.get("parameters", []):
                    parameters.append(FunctionParameter(
                        name=p["name"],
                        param_type=p.get("param_type", "string"),
                    ))

        edges = []
        for edge in self.func_edges:
            source_nid = self._port_node_id(edge.source_port)
            target_nid = self._port_node_id(edge.target_port)
            if source_nid and target_nid:
                # Determine edge type from port data type
                edge_type = "exec" if edge.source_port.is_exec else "data"
                edges.append(FunctionEdge(
                    edge_id=str(uuid.uuid4()),
                    source_node_id=source_nid,
                    source_port_id=edge.source_port.port_id,
                    target_node_id=target_nid,
                    target_port_id=edge.target_port.port_id,
                    edge_type=edge_type,
                ))

        return FunctionDefinition(
            function_id=self.function_def.function_id,
            name=self._name_edit.text() or "Unnamed",
            description=self._desc_edit.text(),
            version=2,
            color=self.function_def.color,
            nodes=nodes,
            edges=edges,
            parameters=parameters,
            variables=list(self.function_def.variables),
            created_at=self.function_def.created_at,
            updated_at=time.time(),
        )

    def _port_node_id(self, port: FuncPortItem) -> str | None:
        for node_id, ports in self.func_ports.items():
            if port in ports:
                return node_id
        return None

    def _validate(self):
        from .function_engine import validate_function_graph
        func_def = self._collect_definition()
        errors = validate_function_graph(func_def)
        if errors:
            QMessageBox.warning(self, t("function.validation_errors"), "\n".join(errors))
        else:
            QMessageBox.information(self, t("function.validate"), t("function.validation_ok"))

    def _save(self):
        from .function_engine import validate_function_graph
        func_def = self._collect_definition()
        errors = validate_function_graph(func_def)
        if errors:
            reply = QMessageBox.warning(
                self, t("function.validation_errors"),
                "\n".join(errors) + "\n\nSave anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        if self.on_save:
            self.on_save(func_def)
        self.accept()

    def showEvent(self, event):
        super().showEvent(event)
        if self.parent():
            parent_geo = self.parent().geometry()
            x = parent_geo.center().x() - self.width() // 2
            y = parent_geo.center().y() - self.height() // 2
            self.move(max(0, x), max(0, y))
