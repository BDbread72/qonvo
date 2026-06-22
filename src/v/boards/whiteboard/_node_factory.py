from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QPointF, QTimer

from .items import PortItem
from .chat_node import ChatNodeWidget
from .function_node import FunctionNodeWidget
from .sticky_note import StickyNoteWidget
from .prompt_node import PromptNodeWidget
from .markdown_node import MarkdownNodeWidget
from .button_node import ButtonNodeWidget
from .round_table import RoundTableWidget
from .checklist import ChecklistWidget
from .repository_node import RepositoryNodeWidget
from .nixi_node import NixiNodeWidget
from .ups_node import UpsNodeWidget
from .rmv_node import RmvNodeWidget
from .switch_node import SwitchNodeWidget
from .logic_nodes import LatchNodeWidget, AndGateWidget, OrGateWidget, NotGateWidget, XorGateWidget, BulbNodeWidget
from .number_node import NumberNodeWidget
from .math_node import MathNodeWidget
from .items import ImageCardItem, TextItem, GroupFrameItem, FileNodeItem
from .dimension_item import DimensionItem
from .function_types import FunctionDefinition


class NodeFactoryMixin:
    """plugin.py에서 분리된 노드 생성 팩토리 mixin.

    self.scene, self._add_proxy(), self._add_port()를 사용해 노드/포트를 생성하고,
    self.proxies 등 카테고리별 딕셔너리에 등록한다.
    """

    def add_node(self, pos: Optional[QPointF] = None, node_id: Optional[int] = None):
        node_id = self._next_id(node_id)
        node = ChatNodeWidget(node_id, on_send=self._handle_chat_send, on_modified=lambda nid=node_id: self._mark_node_dirty(nid))
        node.on_cancel = self._cancel_node_workers
        node.on_add_port = self._add_chat_input_port
        node.on_remove_port = self._remove_chat_input_port
        node.on_toggle_meta_port = self._toggle_meta_port
        proxy = self._add_proxy(node, node_id, pos, self.proxies)
        self._create_ports(proxy, node)
        QTimer.singleShot(0, node.reposition_ports)
        return proxy

    def _open_function_library(self, function_node):
        from .function_library import FunctionLibraryDialog

        def on_select(function_def: FunctionDefinition):
            function_node.set_function(function_def.function_id, function_def.name)
            self._update_function_ports(function_node, function_def)
            self._notify_modified()

        def on_update(updated_library: dict):
            self.functions_library = updated_library
            self._notify_modified()

        dialog = FunctionLibraryDialog(
            self.functions_library,
            on_update=on_update,
            on_select=on_select,
            parent=self.view
        )
        dialog.exec()

    def _update_function_ports(self, function_node, function_def: FunctionDefinition):
        proxy = function_node.proxy
        if not proxy:
            return

        for port_name in list(function_node.input_ports.keys()):
            port = function_node.input_ports[port_name]
            for edge in list(port.edges):
                self.remove_edge(edge)
            if port.scene():
                self.scene.removeItem(port)
            port.scene_remove_label()
            del function_node.input_ports[port_name]

        for port_name in list(function_node.output_ports.keys()):
            port = function_node.output_ports[port_name]
            for edge in list(port.edges):
                self.remove_edge(edge)
            if port.scene():
                self.scene.removeItem(port)
            port.scene_remove_label()
            del function_node.output_ports[port_name]

        param_count = len(function_def.parameters)
        for i, param in enumerate(function_def.parameters):
            port_name = param.name
            port_type = PortItem.TYPE_FILE if param.param_type == "image" else PortItem.TYPE_STRING

            port = PortItem(
                PortItem.INPUT, proxy,
                name=port_name,
                index=i, total=param_count + 1,
                data_type=port_type
            )
            self.scene.addItem(port)
            port.scene_add_label(self.scene)
            function_node.input_ports[port_name] = port
            port.reposition()
            if self.view:
                self.view._all_port_items.add(port)

        outputs = function_def.get_outputs()
        output_count = len(outputs) if outputs else 1

        if outputs:
            for i, output in enumerate(outputs):
                port = PortItem(
                    PortItem.OUTPUT, proxy,
                    name=output.name,
                    index=i, total=output_count + 1,
                    data_type=PortItem.TYPE_STRING
                )
                self.scene.addItem(port)
                port.scene_add_label(self.scene)
                function_node.output_ports[output.name] = port
                port.reposition()
                if self.view:
                    self.view._all_port_items.add(port)
        else:
            port = PortItem(
                PortItem.OUTPUT, proxy,
                name="_default",
                index=0, total=2,
                data_type=PortItem.TYPE_STRING
            )
            self.scene.addItem(port)
            port.scene_add_label(self.scene)
            function_node.output_ports["_default"] = port
            port.reposition()
            if self.view:
                self.view._all_port_items.add(port)

        if hasattr(function_node, 'signal_input_port') and function_node.signal_input_port:
            function_node.signal_input_port.port_total = param_count + 1
            function_node.signal_input_port.reposition()
        if hasattr(function_node, 'signal_output_port') and function_node.signal_output_port:
            function_node.signal_output_port.port_total = output_count + 1
            function_node.signal_output_port.reposition()

    def _edit_function(self, function_node):
        from .function_editor import FunctionEditorDialog

        if not function_node.function_id:
            return

        function_def = self.functions_library.get(function_node.function_id)
        if not function_def:
            return

        dialog = FunctionEditorDialog(function_def, parent=self.view)

        def on_save(updated_def: FunctionDefinition):
            self.functions_library[updated_def.function_id] = updated_def
            function_node.set_function(updated_def.function_id, updated_def.name)
            self._notify_modified()

        dialog.on_save = on_save
        dialog.exec()

    def add_function(self, pos: Optional[QPointF] = None, node_id: Optional[int] = None):
        node_id = self._next_id(node_id)
        node = FunctionNodeWidget(
            node_id,
            on_send=self._execute_function_graph,
            on_modified=lambda nid=node_id: self._mark_node_dirty(nid),
            on_open_library=self._open_function_library
        )
        proxy = self._add_proxy(node, node_id, pos, self.function_proxies)

        node.input_ports["_default"] = self._add_port(
            PortItem.INPUT, proxy, name="_default",
            index=0, total=2, data_type=PortItem.TYPE_STRING)
        node.output_ports["_default"] = self._add_port(
            PortItem.OUTPUT, proxy, name="_default",
            index=0, total=2, data_type=PortItem.TYPE_STRING)
        node.signal_input_port = self._add_port(
            PortItem.INPUT, proxy, name="⚡ 실행",
            index=1, total=2, data_type=PortItem.TYPE_BOOLEAN)
        node.signal_output_port = self._add_port(
            PortItem.OUTPUT, proxy, name="⚡ 완료",
            index=1, total=2, data_type=PortItem.TYPE_BOOLEAN)

        QTimer.singleShot(0, node.reposition_ports)
        return proxy

    def add_sticky(self, pos: Optional[QPointF] = None, node_id: Optional[int] = None):
        node_id = self._next_id(node_id)
        node = StickyNoteWidget(on_modified=lambda nid=node_id: self._mark_node_dirty(nid))
        node.node_id = node_id
        proxy = self._add_proxy(node, node_id, pos, self.sticky_proxies)
        node.output_port = self._add_port(
            PortItem.OUTPUT, proxy, name="텍스트",
            index=0, total=1, data_type=PortItem.TYPE_STRING)
        QTimer.singleShot(0, node.reposition_ports)
        return proxy

    def add_prompt_node(self, pos: Optional[QPointF] = None, node_id: Optional[int] = None):
        node_id = self._next_id(node_id)
        node = PromptNodeWidget(on_modified=lambda nid=node_id: self._mark_node_dirty(nid))
        node.node_id = node_id
        proxy = self._add_proxy(node, node_id, pos, self.prompt_proxies)
        node.output_port = self._add_port(
            PortItem.OUTPUT, proxy, name="prompt",
            index=0, total=1, data_type=PortItem.TYPE_STRING)
        # 입력 포트 추가 — 상위 텍스트를 받아 본문에 채움(양방향). 기존 보드는
        # 엣지를 포트 '이름'으로 복원하므로 출력 "prompt" 유지 → 기존 연결 안전.
        node.input_port = self._add_port(
            PortItem.INPUT, proxy, name="입력",
            index=0, total=1, data_type=PortItem.TYPE_STRING)
        QTimer.singleShot(0, node.reposition_ports)
        return proxy

    def add_markdown(self, pos: Optional[QPointF] = None, node_id: Optional[int] = None):
        node_id = self._next_id(node_id)
        node = MarkdownNodeWidget(on_modified=lambda nid=node_id: self._mark_node_dirty(nid))
        node.node_id = node_id
        proxy = self._add_proxy(node, node_id, pos, self.markdown_proxies)
        node.input_port = self._add_port(
            PortItem.INPUT, proxy, name="text",
            index=0, total=1, data_type=PortItem.TYPE_STRING)
        # 출력 포트 추가 — 마크다운 텍스트를 하위로 내보냄(양방향). 입력 "text" 유지.
        node.output_port = self._add_port(
            PortItem.OUTPUT, proxy, name="출력",
            index=0, total=1, data_type=PortItem.TYPE_STRING)
        QTimer.singleShot(0, node.reposition_ports)
        return proxy

    def add_button(self, pos: Optional[QPointF] = None, node_id: Optional[int] = None):
        node_id = self._next_id(node_id)
        node = ButtonNodeWidget(node_id, on_signal=self._on_button_signal, on_modified=lambda nid=node_id: self._mark_node_dirty(nid))
        proxy = self._add_proxy(node, node_id, pos, self.button_proxies)

        node.input_port = self._add_port(
            PortItem.INPUT, proxy, name="입력",
            index=0, total=2, data_type=PortItem.TYPE_STRING)
        node.signal_output_port = self._add_port(
            PortItem.OUTPUT, proxy, name="⚡ 신호",
            index=1, total=2, data_type=PortItem.TYPE_BOOLEAN)
        QTimer.singleShot(0, node.reposition_ports)
        return proxy

    def add_switch(self, pos: Optional[QPointF] = None, node_id: Optional[int] = None):
        node_id = self._next_id(node_id)
        node = SwitchNodeWidget(node_id, on_signal=self._on_switch_signal, on_modified=lambda nid=node_id: self._mark_node_dirty(nid))
        proxy = self._add_proxy(node, node_id, pos, self.switch_proxies)

        node.signal_input_port = self._add_port(
            PortItem.INPUT, proxy, name="⚡ 입력",
            index=0, total=1, data_type=PortItem.TYPE_BOOLEAN)
        node.signal_output_port = self._add_port(
            PortItem.OUTPUT, proxy, name="⚡ 출력",
            index=0, total=1, data_type=PortItem.TYPE_BOOLEAN)
        QTimer.singleShot(0, node.reposition_ports)
        return proxy

    def _on_switch_signal(self, node_id):
        node = self.app.nodes.get(node_id)
        if node is None:
            return
        if not hasattr(node, 'signal_output_port') or node.signal_output_port is None:
            return
        data = getattr(node, '_pass_data', None)
        self.emit_signal(node.signal_output_port, data=data)

    def _on_logic_signal(self, node_id):
        node = self.app.nodes.get(node_id)
        if node is None or not hasattr(node, 'signal_output_port') or node.signal_output_port is None:
            return
        data = getattr(node, '_pass_data', None)
        if getattr(node, '_use_pulse', False):
            self.emit_signal(node.signal_output_port, data)
        else:
            powered = getattr(node, '_pass_powered', False)
            self.set_port_state(node.signal_output_port, powered, data)

    def add_latch(self, pos: Optional[QPointF] = None, node_id: Optional[int] = None):
        node_id = self._next_id(node_id)
        node = LatchNodeWidget(node_id, on_signal=self._on_logic_signal, on_modified=lambda nid=node_id: self._mark_node_dirty(nid))
        proxy = self._add_proxy(node, node_id, pos, self.latch_proxies)
        node.signal_input_port = self._add_port(
            PortItem.INPUT, proxy, name="⚡ A",
            index=0, total=2, data_type=PortItem.TYPE_BOOLEAN)
        node.signal_input_port_b = self._add_port(
            PortItem.INPUT, proxy, name="⚡ B",
            index=1, total=2, data_type=PortItem.TYPE_BOOLEAN)
        node.signal_output_port = self._add_port(
            PortItem.OUTPUT, proxy, name="⚡ 출력",
            index=0, total=1, data_type=PortItem.TYPE_BOOLEAN)
        QTimer.singleShot(0, node.reposition_ports)
        return proxy

    def add_and_gate(self, pos: Optional[QPointF] = None, node_id: Optional[int] = None):
        node_id = self._next_id(node_id)
        node = AndGateWidget(node_id, on_signal=self._on_logic_signal, on_modified=lambda nid=node_id: self._mark_node_dirty(nid))
        proxy = self._add_proxy(node, node_id, pos, self.and_gate_proxies)
        node.signal_input_port = self._add_port(
            PortItem.INPUT, proxy, name="⚡ A",
            index=0, total=2, data_type=PortItem.TYPE_BOOLEAN)
        node.signal_input_port_b = self._add_port(
            PortItem.INPUT, proxy, name="⚡ B",
            index=1, total=2, data_type=PortItem.TYPE_BOOLEAN)
        node.signal_output_port = self._add_port(
            PortItem.OUTPUT, proxy, name="⚡ 출력",
            index=0, total=1, data_type=PortItem.TYPE_BOOLEAN)
        QTimer.singleShot(0, node.reposition_ports)
        return proxy

    def add_or_gate(self, pos: Optional[QPointF] = None, node_id: Optional[int] = None):
        node_id = self._next_id(node_id)
        node = OrGateWidget(node_id, on_signal=self._on_logic_signal, on_modified=lambda nid=node_id: self._mark_node_dirty(nid))
        proxy = self._add_proxy(node, node_id, pos, self.or_gate_proxies)
        node.signal_input_port = self._add_port(
            PortItem.INPUT, proxy, name="⚡ A",
            index=0, total=2, data_type=PortItem.TYPE_BOOLEAN)
        node.signal_input_port_b = self._add_port(
            PortItem.INPUT, proxy, name="⚡ B",
            index=1, total=2, data_type=PortItem.TYPE_BOOLEAN)
        node.signal_output_port = self._add_port(
            PortItem.OUTPUT, proxy, name="⚡ 출력",
            index=0, total=1, data_type=PortItem.TYPE_BOOLEAN)
        QTimer.singleShot(0, node.reposition_ports)
        return proxy

    def add_not_gate(self, pos: Optional[QPointF] = None, node_id: Optional[int] = None):
        node_id = self._next_id(node_id)
        node = NotGateWidget(node_id, on_signal=self._on_logic_signal, on_modified=lambda nid=node_id: self._mark_node_dirty(nid))
        proxy = self._add_proxy(node, node_id, pos, self.not_gate_proxies)
        node.signal_input_port = self._add_port(
            PortItem.INPUT, proxy, name="⚡ A",
            index=0, total=1, data_type=PortItem.TYPE_BOOLEAN)
        node.signal_output_port = self._add_port(
            PortItem.OUTPUT, proxy, name="⚡ 출력",
            index=0, total=1, data_type=PortItem.TYPE_BOOLEAN)
        node.signal_output_port.set_powered(True)
        QTimer.singleShot(0, node.reposition_ports)
        return proxy

    def add_xor_gate(self, pos: Optional[QPointF] = None, node_id: Optional[int] = None):
        node_id = self._next_id(node_id)
        node = XorGateWidget(node_id, on_signal=self._on_logic_signal, on_modified=lambda nid=node_id: self._mark_node_dirty(nid))
        proxy = self._add_proxy(node, node_id, pos, self.xor_gate_proxies)
        node.signal_input_port = self._add_port(
            PortItem.INPUT, proxy, name="⚡ A",
            index=0, total=2, data_type=PortItem.TYPE_BOOLEAN)
        node.signal_input_port_b = self._add_port(
            PortItem.INPUT, proxy, name="⚡ B",
            index=1, total=2, data_type=PortItem.TYPE_BOOLEAN)
        node.signal_output_port = self._add_port(
            PortItem.OUTPUT, proxy, name="⚡ 출력",
            index=0, total=1, data_type=PortItem.TYPE_BOOLEAN)
        QTimer.singleShot(0, node.reposition_ports)
        return proxy

    def add_bulb(self, pos: Optional[QPointF] = None, node_id: Optional[int] = None):
        node_id = self._next_id(node_id)
        node = BulbNodeWidget(node_id, on_signal=self._on_logic_signal, on_modified=lambda nid=node_id: self._mark_node_dirty(nid))
        proxy = self._add_proxy(node, node_id, pos, self.bulb_proxies)
        node.signal_input_port = self._add_port(
            PortItem.INPUT, proxy, name="⚡ A",
            index=0, total=1, data_type=PortItem.TYPE_BOOLEAN)
        node.signal_output_port = self._add_port(
            PortItem.OUTPUT, proxy, name="⚡ 출력",
            index=0, total=1, data_type=PortItem.TYPE_BOOLEAN)
        QTimer.singleShot(0, node.reposition_ports)
        return proxy

    def add_number(self, pos: Optional[QPointF] = None, node_id: Optional[int] = None):
        node_id = self._next_id(node_id)
        node = NumberNodeWidget(
            node_id,
            on_value_changed=self._on_number_changed,
            on_modified=lambda nid=node_id: self._mark_node_dirty(nid))
        proxy = self._add_proxy(node, node_id, pos, self.number_proxies)
        node.input_port = self._add_port(
            PortItem.INPUT, proxy, name="설정",
            index=0, total=3, data_type=PortItem.TYPE_NUMBER)
        node.signal_input_port = self._add_port(
            PortItem.INPUT, proxy, name="⚡ 증가",
            index=1, total=3, data_type=PortItem.TYPE_BOOLEAN)
        node.signal_input_port_b = self._add_port(
            PortItem.INPUT, proxy, name="⚡ 리셋",
            index=2, total=3, data_type=PortItem.TYPE_BOOLEAN)
        node.output_port = self._add_port(
            PortItem.OUTPUT, proxy, name="값",
            index=0, total=2, data_type=PortItem.TYPE_NUMBER)
        node.signal_output_port = self._add_port(
            PortItem.OUTPUT, proxy, name="⚡ 변경",
            index=1, total=2, data_type=PortItem.TYPE_BOOLEAN)
        node.output_port.port_value = node._value
        QTimer.singleShot(0, node.reposition_ports)
        return proxy

    def add_math(self, pos: Optional[QPointF] = None, node_id: Optional[int] = None):
        node_id = self._next_id(node_id)
        node = MathNodeWidget(
            node_id,
            on_value_changed=self._on_math_changed,
            on_modified=lambda nid=node_id: self._mark_node_dirty(nid))
        proxy = self._add_proxy(node, node_id, pos, self.math_proxies)
        node.input_port = self._add_port(
            PortItem.INPUT, proxy, name="A",
            index=0, total=2, data_type=PortItem.TYPE_NUMBER)
        node.input_port_b = self._add_port(
            PortItem.INPUT, proxy, name="B",
            index=1, total=2, data_type=PortItem.TYPE_NUMBER)
        node.output_port = self._add_port(
            PortItem.OUTPUT, proxy, name="결과",
            index=0, total=2, data_type=PortItem.TYPE_NUMBER)
        node.signal_output_port = self._add_port(
            PortItem.OUTPUT, proxy, name="⚡ 참",
            index=1, total=2, data_type=PortItem.TYPE_BOOLEAN)
        node.output_port.port_value = node._result
        QTimer.singleShot(0, node.reposition_ports)
        return proxy

    def _on_number_changed(self, node_id):
        node = self.app.nodes.get(node_id)
        if node is None:
            return
        val = getattr(node, '_value', 0)
        self._propagate_number(getattr(node, 'output_port', None), val)
        sp = getattr(node, 'signal_output_port', None)
        if sp is not None:
            self.emit_signal(sp, data=str(val))

    def _on_math_changed(self, node_id):
        node = self.app.nodes.get(node_id)
        if node is None:
            return
        self._propagate_number(getattr(node, 'output_port', None), getattr(node, '_result', 0))
        bsp = getattr(node, 'signal_output_port', None)
        if bsp is not None:
            self.set_port_state(bsp, bool(getattr(node, '_bool', False)), data=str(getattr(node, '_result', 0)))

    def _attach_item_ports(self, item, in_type: str, out_type: str):
        item.input_port = self._add_port(
            PortItem.INPUT, item, name="_default",
            index=0, total=1, data_type=in_type)
        item.output_port = self._add_port(
            PortItem.OUTPUT, item, name="_default",
            index=0, total=1, data_type=out_type)
        QTimer.singleShot(0, item._reposition_own_ports)

    def add_image_card(
        self,
        image_path: str = "",
        pos: Optional[QPointF] = None,
        node_id: Optional[int] = None,
        width: Optional[float] = None,
        height: Optional[float] = None,
    ):
        node_id = self._next_id(node_id)
        if pos is None:
            pos = self._cursor_scene_pos()
        item = ImageCardItem(pos.x(), pos.y())
        item.node_id = node_id

        if image_path:
            item.set_image(image_path)

        if width is not None and height is not None:
            item.prepareGeometryChange()
            item._width = width
            item._height = height
            item.update()

        self.scene.addItem(item)
        self.image_card_items[node_id] = item
        self.app.nodes[node_id] = item

        self._attach_item_ports(item, PortItem.TYPE_FILE, PortItem.TYPE_FILE)
        item.on_image_changed = self._on_image_card_changed
        item.setToolTip(f"Image #{node_id}")
        self._send_node_add_op(node_id, item, pos)
        # 서버모드: 드래그/붙여넣기로 넣은 이미지를 서버에 업로드+영속(자동 prop 동기화 제외 대상).
        if image_path and getattr(self, 'server_mode', False) and not getattr(self, '_applying_remote_op', False):
            self._upload_and_sync_image(item)
        self._notify_modified()
        return item

    def add_file_node(
        self,
        file_paths=None,
        pos: Optional[QPointF] = None,
        node_id: Optional[int] = None,
        width: Optional[float] = None,
        height: Optional[float] = None,
    ):
        node_id = self._next_id(node_id)
        if pos is None:
            pos = self._cursor_scene_pos()
        item = FileNodeItem(pos.x(), pos.y())
        item.node_id = node_id

        if file_paths:
            item.set_files(file_paths)

        if width is not None and height is not None:
            item.prepareGeometryChange()
            item._width = width
            item._height = height
            item.update()

        self.scene.addItem(item)
        self.file_node_items[node_id] = item
        self.app.nodes[node_id] = item

        self._attach_item_ports(item, PortItem.TYPE_FILE, PortItem.TYPE_FILE)
        item.on_files_changed = self._on_file_node_changed
        item.setToolTip(f"Files #{node_id}")
        self._send_node_add_op(node_id, item, pos)
        self._notify_modified()
        return item

    def _on_file_node_changed(self, item):
        self._notify_modified()

    def add_dimension_item(self, pos: Optional[QPointF] = None):
        node_id = self._next_id()
        if pos is None:
            pos = self._cursor_scene_pos()
        item = DimensionItem(pos.x(), pos.y())
        item.node_id = node_id
        item.on_double_click = self._open_dimension_board
        self.scene.addItem(item)
        self.dimension_items[node_id] = item
        self.app.nodes[node_id] = item

        self._attach_item_ports(item, PortItem.TYPE_STRING, PortItem.TYPE_STRING)
        self._send_node_add_op(node_id, item, pos)
        self._notify_modified()
        return item

    def _open_dimension_board(self, dimension_item: DimensionItem):
        from .dimension_board import DimensionBoardWindow

        self._dimension_windows = [w for w in self._dimension_windows if w.isVisible()]
        for w in self._dimension_windows:
            if w.dimension_item is dimension_item:
                w.raise_()
                w.activateWindow()
                return
        window = DimensionBoardWindow(dimension_item, self, parent=self.view)
        self._dimension_windows.append(window)
        window.show()
        window.raise_()
        window.activateWindow()

    def add_round_table(self, pos: Optional[QPointF] = None, node_id: Optional[int] = None):
        node_id = self._next_id(node_id)
        node = RoundTableWidget(
            node_id,
            on_send=self._handle_round_table_send,
            on_modified=lambda nid=node_id: self._mark_node_dirty(nid)
        )
        proxy = self._add_proxy(node, node_id, pos, self.round_table_proxies)
        self._create_ports(proxy, node)
        QTimer.singleShot(0, node.reposition_ports)
        return proxy

    def add_checklist(self, pos: Optional[QPointF] = None, node_id: Optional[int] = None,
                      title: str = "", items: list = None):
        node_id = self._next_id(node_id)
        node = ChecklistWidget(
            title=title, items=items,
            on_modified=lambda nid=node_id: self._mark_node_dirty(nid),
            on_complete=lambda nid=node_id: self._on_checklist_complete(nid),
            get_current_user=self._checklist_current_user,
            on_ai=lambda kind, nid=node_id: self._checklist_ai(nid, kind),
            get_users=self._checklist_online_users,
        )
        node.node_id = node_id
        proxy = self._add_proxy(node, node_id, pos, self.checklist_proxies)
        node.output_port = self._add_port(
            PortItem.OUTPUT, proxy, name="체크리스트",
            index=0, total=2, data_type=PortItem.TYPE_STRING)
        node.signal_output_port = self._add_port(
            PortItem.OUTPUT, proxy, name="⚡ 완료",
            index=1, total=2, data_type=PortItem.TYPE_BOOLEAN)
        QTimer.singleShot(0, node.reposition_ports)
        return proxy

    # ── 체크리스트 협업/AI 콜백 헬퍼 ──
    def _checklist_current_user(self) -> str:
        try:
            if getattr(self, 'server_mode', False) and getattr(self, '_server_client', None):
                return self._server_client.username or ""
        except Exception:
            pass
        return ""

    def _checklist_online_users(self) -> list:
        try:
            return [u.get("user", "") for u in getattr(self, '_last_presence', []) if u.get("user")]
        except Exception:
            return []

    def add_repository(self, pos: Optional[QPointF] = None, node_id: Optional[int] = None):
        node_id = self._next_id(node_id)
        node = RepositoryNodeWidget(node_id, on_modified=lambda nid=node_id: self._mark_node_dirty(nid))
        proxy = self._add_proxy(node, node_id, pos, self.repository_proxies)
        node.input_port = self._add_port(
            PortItem.INPUT, proxy, name="입력",
            index=0, total=2, data_type=PortItem.TYPE_STRING)
        node.input_port.multi_connect = True
        node.output_port = self._add_port(
            PortItem.OUTPUT, proxy, name="출력",
            index=0, total=2, data_type=PortItem.TYPE_STRING)
        node.signal_input_port = self._add_port(
            PortItem.INPUT, proxy, name="⚡ 실행",
            index=1, total=2, data_type=PortItem.TYPE_BOOLEAN)
        node.signal_output_port = self._add_port(
            PortItem.OUTPUT, proxy, name="⚡ 완료",
            index=1, total=2, data_type=PortItem.TYPE_BOOLEAN)
        QTimer.singleShot(0, node.reposition_ports)
        return proxy

    def add_nixi(self, pos: Optional[QPointF] = None, node_id: Optional[int] = None):
        node_id = self._next_id(node_id)
        node = NixiNodeWidget(node_id, on_modified=lambda nid=node_id: self._mark_node_dirty(nid))
        proxy = self._add_proxy(node, node_id, pos, self.nixi_proxies)
        node.input_port = self._add_port(
            PortItem.INPUT, proxy, name="입력",
            index=0, total=2, data_type=PortItem.TYPE_STRING)
        node.signal_input_port = self._add_port(
            PortItem.INPUT, proxy, name="⚡ 실행",
            index=1, total=2, data_type=PortItem.TYPE_BOOLEAN)
        QTimer.singleShot(0, node.reposition_ports)
        return proxy

    def _create_signal_ports(self, proxy, widget):
        widget.input_port = self._add_port(
            PortItem.INPUT, proxy, name="입력",
            index=0, total=2, data_type=PortItem.TYPE_FILE)
        widget.output_port = self._add_port(
            PortItem.OUTPUT, proxy, name="출력",
            index=0, total=2, data_type=PortItem.TYPE_FILE)
        widget.signal_input_port = self._add_port(
            PortItem.INPUT, proxy, name="⚡ 실행",
            index=1, total=2, data_type=PortItem.TYPE_BOOLEAN)
        widget.signal_output_port = self._add_port(
            PortItem.OUTPUT, proxy, name="⚡ 완료",
            index=1, total=2, data_type=PortItem.TYPE_BOOLEAN)

    def add_ups(self, pos: Optional[QPointF] = None, node_id: Optional[int] = None):
        node_id = self._next_id(node_id)
        node = UpsNodeWidget(node_id, on_modified=lambda nid=node_id: self._mark_node_dirty(nid))
        proxy = self._add_proxy(node, node_id, pos, self.ups_proxies)
        self._create_signal_ports(proxy, node)
        _orig_on_done = node._on_done
        def _ups_done_hook():
            _orig_on_done()
            if node.output_port:
                node.output_port.port_value = node.ai_response
            self._emit_complete_signal(node)
        node._on_done = _ups_done_hook
        QTimer.singleShot(0, node.reposition_ports)
        return proxy

    def add_rmv(self, pos: Optional[QPointF] = None, node_id: Optional[int] = None):
        node_id = self._next_id(node_id)
        node = RmvNodeWidget(node_id, on_modified=lambda nid=node_id: self._mark_node_dirty(nid))
        proxy = self._add_proxy(node, node_id, pos, self.rmv_proxies)
        self._create_signal_ports(proxy, node)
        _orig_on_finished = node._on_finished
        def _rmv_done_hook(result_path):
            _orig_on_finished(result_path)
            if node.output_port:
                node.output_port.port_value = node.ai_response
            self._emit_complete_signal(node)
        node._on_finished = _rmv_done_hook
        QTimer.singleShot(0, node.reposition_ports)
        return proxy

    def add_text_item(self, pos: Optional[QPointF] = None, node_id: Optional[int] = None):
        node_id = self._next_id(node_id)
        if pos is None:
            pos = self._cursor_scene_pos()
        item = TextItem(pos.x(), pos.y())
        item.node_id = node_id
        self.scene.addItem(item)
        self.text_items[node_id] = item
        self.app.nodes[node_id] = item
        self._send_node_add_op(node_id, item, pos)
        self._notify_modified()
        return item

    def add_group_frame(self, pos: Optional[QPointF] = None, node_id: Optional[int] = None):
        node_id = self._next_id(node_id)
        if pos is None:
            pos = self._cursor_scene_pos()
        item = GroupFrameItem(pos.x(), pos.y())
        item.node_id = node_id
        self.scene.addItem(item)
        self.group_frame_items[node_id] = item
        self.app.nodes[node_id] = item
        self._send_node_add_op(node_id, item, pos)
        self._notify_modified()
        return item

    def get_radial_menu_items(self, scene_pos: Optional[QPointF] = None, category: Optional[str] = None):
        if category is None:
            return [
                ("cat_nodes", "Nodes", "nodes"),
                ("cat_notes", "Notes", "notes"),
                ("cat_media", "Media", "media"),
                ("cat_signal", "Signal", "signal"),
                ("cat_ui", "UI", "ui"),
            ]

        if category == "nodes":
            return [
                ("node", "Chat", lambda: self.add_node(scene_pos)),
                ("function", "Function", lambda: self.add_function(scene_pos)),
                # Round Table / 자료함(repository) 은 폐기 — 생성 메뉴에서 숨김
                # (기존 보드 로드는 _materialization 으로 계속 지원)
                ("nixi", "Nixi", lambda: self.add_nixi(scene_pos)),
            ]
        elif category == "notes":
            return [
                # Sticky 는 Prompt 로 통합 — 생성 메뉴에서 숨김(기존 보드는 계속 로드).
                ("prompt", "Prompt", lambda: self.add_prompt_node(scene_pos)),
                ("text", "Text", lambda: self.add_text_item(scene_pos)),
                ("markdown", "Markdown", lambda: self.add_markdown(scene_pos)),
                ("checklist", "Checklist", lambda: self.add_checklist(scene_pos)),
            ]
        elif category == "media":
            return [
                ("image", "Image", lambda: self.add_image_card("", scene_pos)),
                ("file", "Files", lambda: self.add_file_node(None, scene_pos)),
                ("dimension", "Dimension", lambda: self.add_dimension_item(scene_pos)),
                ("ups", "Upscale", lambda: self.add_ups(scene_pos)),
                ("rmv", "Remove BG", lambda: self.add_rmv(scene_pos)),
            ]
        elif category == "signal":
            return [
                ("button", "Button", lambda: self.add_button(scene_pos)),
                ("switch", "Switch", lambda: self.add_switch(scene_pos)),
                ("latch", "Latch", lambda: self.add_latch(scene_pos)),
                ("not", "NOT", lambda: self.add_not_gate(scene_pos)),
                ("and", "AND", lambda: self.add_and_gate(scene_pos)),
                ("or", "OR", lambda: self.add_or_gate(scene_pos)),
                ("xor", "XOR", lambda: self.add_xor_gate(scene_pos)),
                ("bulb", "Bulb", lambda: self.add_bulb(scene_pos)),
                ("number", "Number", lambda: self.add_number(scene_pos)),
                ("math", "Math", lambda: self.add_math(scene_pos)),
            ]
        elif category == "ui":
            return [
                ("group", "Group", lambda: self.add_group_frame(scene_pos)),
            ]

        return []
