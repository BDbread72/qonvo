from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Optional

from PyQt6.QtCore import QPointF

if TYPE_CHECKING:
    from .items import PortItem, EdgeItem


class ServerMixin:
    """plugin.py에서 분리된 서버 동기화 mixin.

    self._server_client로 서버와 통신하고, self._applying_remote_op으로 원격 적용 중 전송을 차단한다.
    노드 조회/갱신은 self.app.nodes를 통해 수행한다.
    """

    _NODE_CATEGORY_MAP = {
        'ChatNodeWidget': 'nodes',
        'FunctionNodeWidget': 'function_nodes',
        'RoundTableWidget': 'round_tables',
        'StickyNoteWidget': 'sticky_notes',
        'PromptNodeWidget': 'prompt_nodes',
        'MarkdownNodeWidget': 'markdown_nodes',
        'ButtonNodeWidget': 'buttons',
        'ChecklistWidget': 'checklists',
        'RepositoryNodeWidget': 'repository_nodes',
        'NixiNodeWidget': 'nixi_nodes',
        'UpsNodeWidget': 'ups_nodes',
        'RmvNodeWidget': 'rmv_nodes',
        'SwitchNodeWidget': 'switch_nodes',
        'LatchNodeWidget': 'latch_nodes',
        'AndGateWidget': 'and_gates',
        'OrGateWidget': 'or_gates',
        'NotGateWidget': 'not_gates',
        'XorGateWidget': 'xor_gates',
        'TextItem': 'texts',
        'GroupFrameItem': 'group_frames',
        'ImageCardItem': 'image_cards',
        'DimensionItem': 'dimensions',
    }

    def _node_category(self, node) -> str:
        return self._NODE_CATEGORY_MAP.get(type(node).__name__, 'nodes')

    @property
    def server_mode(self) -> bool:
        return self._server_client is not None and self._server_client.is_connected

    def set_server_client(self, client):
        from .server_client import ServerClient
        self._server_client = client
        client.sync_received.connect(self._on_server_sync)
        client.remote_ops.connect(self._on_remote_ops)
        client.ai_progress.connect(self._on_ai_progress)
        client.ai_complete.connect(self._on_ai_complete)

    def detach_server_client(self):
        if self._server_client:
            try:
                self._server_client.sync_received.disconnect(self._on_server_sync)
                self._server_client.remote_ops.disconnect(self._on_remote_ops)
                self._server_client.ai_progress.disconnect(self._on_ai_progress)
                self._server_client.ai_complete.disconnect(self._on_ai_complete)
            except Exception:
                pass
        self._server_client = None

    def _send_op(self, op_type: str, target, data: dict | None = None):
        if self._applying_remote_op:
            return
        if self._server_client and self._server_client.is_connected:
            self._server_client.send_op(op_type, str(target), data)

    def _on_server_sync(self, snapshot: dict):
        self._applying_remote_op = True
        try:
            self.restore_data(snapshot)
        finally:
            self._applying_remote_op = False

    def _on_remote_ops(self, ops: list, author: str):
        self._applying_remote_op = True
        try:
            for op in ops:
                self._apply_remote_op(op)
        finally:
            self._applying_remote_op = False

    def _on_ai_progress(self, node_id_str: str, chunk: str):
        from .chat_node import ChatNodeWidget
        node_id = int(node_id_str) if node_id_str.isdigit() else None
        if node_id is None:
            return
        node = self.app.nodes.get(node_id)
        if node and isinstance(node, ChatNodeWidget):
            node.set_response(chunk, done=False)

    def _on_ai_complete(self, node_id_str: str, result: dict):
        from .chat_node import ChatNodeWidget
        node_id = int(node_id_str) if node_id_str.isdigit() else None
        if node_id is None:
            return
        node = self.app.nodes.get(node_id)
        if node and isinstance(node, ChatNodeWidget):
            text = result.get("text", "")
            images = result.get("images", [])
            tokens_in = result.get("tokens_in", 0)
            tokens_out = result.get("tokens_out", 0)
            if tokens_in or tokens_out:
                node.set_tokens(tokens_in, tokens_out)
            if images:
                node.set_image_response(text, images)
            else:
                node.set_response(text, done=True)
            self._emit_complete_signal(node)

    def _handle_chat_send_server(self, node_id, node, model, message, files, prompt_entries):
        from v.settings import get_model_options

        if not model:
            node.set_response("No model selected", done=True)
            return

        effective_system_prompt = self.system_prompt
        if prompt_entries:
            sorted_entries = sorted(prompt_entries, key=lambda e: e.get("priority", 0))
            system_parts = [e.get("text", "") for e in sorted_entries
                           if e.get("role") == "system" and e.get("text")]
            if system_parts:
                effective_system_prompt = f"{effective_system_prompt}\n\n{''.join(system_parts)}".strip()

        node.set_response("서버 처리 중...", done=False)

        self._server_client.send_ai_request(
            node_id=node_id,
            model=model,
            message=message or "",
            files=[f for f in (files or []) if isinstance(f, str)],
            system_prompt=effective_system_prompt,
            options=get_model_options(model),
        )

    def _apply_remote_op(self, op: dict):
        op_type = op.get("op_type", "")
        target = op.get("target", "")
        data = op.get("data", {})

        if op_type == "node_add":
            self._remote_add_node(target, data)
        elif op_type == "node_remove":
            self._remote_remove_node(target)
        elif op_type == "node_move":
            self._remote_move_node(target, data)
        elif op_type == "node_prop":
            self._remote_node_prop(target, data)
        elif op_type == "edge_add":
            self._remote_add_edge(data)
        elif op_type == "edge_remove":
            self._remote_remove_edge(data)
        elif op_type == "chat_append":
            self._remote_chat_append(target, data)

    def _remote_add_node(self, target: str, data: dict):
        category = data.get("_category", "nodes")
        node_id = int(target) if target.isdigit() else self._next_id()
        self.app._next_id = max(self.app._next_id, node_id + 1)
        pos = QPointF(data.get("x", 0), data.get("y", 0))

        add_map = {
            "nodes": self.add_node,
            "function_nodes": self.add_function,
            "round_tables": self.add_round_table,
            "sticky_notes": self.add_sticky,
            "prompt_nodes": self.add_prompt_node,
            "markdown_nodes": self.add_markdown,
            "buttons": self.add_button,
            "switch_nodes": self.add_switch,
            "latch_nodes": self.add_latch,
            "and_gates": self.add_and_gate,
            "or_gates": self.add_or_gate,
            "not_gates": self.add_not_gate,
            "xor_gates": self.add_xor_gate,
            "checklists": self.add_checklist,
            "repository_nodes": self.add_repository,
            "nixi_nodes": self.add_nixi,
            "ups_nodes": self.add_ups,
            "rmv_nodes": self.add_rmv,
            "texts": self.add_text_item,
            "group_frames": self.add_group_frame,
            "image_cards": self.add_image_card,
        }
        add_fn = add_map.get(category)
        if add_fn:
            add_fn(pos=pos, node_id=node_id)

    def _remote_remove_node(self, target: str):
        node_id = int(target) if target.isdigit() else None
        if node_id is None:
            return
        for d in (self.proxies, self.function_proxies, self.round_table_proxies,
                  self.sticky_proxies, self.prompt_proxies, self.markdown_proxies, self.button_proxies, self.switch_proxies, self.latch_proxies, self.and_gate_proxies, self.or_gate_proxies, self.not_gate_proxies, self.xor_gate_proxies,
                  self.checklist_proxies, self.repository_proxies,
                  self.nixi_proxies, self.ups_proxies, self.rmv_proxies):
            if node_id in d:
                self.delete_proxy_item(d[node_id])
                return
        for d in (self.image_card_items, self.dimension_items,
                  self.text_items, self.group_frame_items):
            if node_id in d:
                self._delete_scene_item(d[node_id], d)
                return

    def _remote_move_node(self, target: str, data: dict):
        node_id = int(target) if target.isdigit() else None
        if node_id is None:
            return
        x, y = data.get("x", 0), data.get("y", 0)
        for d in (self.proxies, self.function_proxies, self.round_table_proxies,
                  self.sticky_proxies, self.prompt_proxies, self.markdown_proxies, self.button_proxies, self.switch_proxies, self.latch_proxies, self.and_gate_proxies, self.or_gate_proxies, self.not_gate_proxies, self.xor_gate_proxies,
                  self.checklist_proxies, self.repository_proxies,
                  self.nixi_proxies, self.ups_proxies, self.rmv_proxies):
            if node_id in d:
                d[node_id].setPos(QPointF(x, y))
                return
        for d in (self.image_card_items, self.dimension_items,
                  self.text_items, self.group_frame_items):
            if node_id in d:
                d[node_id].setPos(QPointF(x, y))
                return

    def _remote_node_prop(self, target: str, data: dict):
        node_id = int(target) if target.isdigit() else None
        if node_id is None:
            return
        node = self.app.nodes.get(node_id)
        if node is None:
            return
        key = data.get("key", "")
        value = data.get("value")
        if key and hasattr(node, key):
            try:
                setattr(node, key, value)
            except Exception:
                pass

    def _remote_add_edge(self, data: dict):
        from .items import PortItem

        src_id = data.get("source_node_id")
        tgt_id = data.get("target_node_id")
        src_port_name = data.get("source_port_name", "_default")
        tgt_port_name = data.get("target_port_name", "_default")
        if src_id is None or tgt_id is None:
            return

        src_id = int(src_id) if isinstance(src_id, str) and src_id.isdigit() else src_id
        tgt_id = int(tgt_id) if isinstance(tgt_id, str) and tgt_id.isdigit() else tgt_id

        src_port = self._find_port(src_id, src_port_name, PortItem.OUTPUT)
        tgt_port = self._find_port(tgt_id, tgt_port_name, PortItem.INPUT)
        if src_port and tgt_port:
            self.create_edge(src_port, tgt_port)

    def _remote_remove_edge(self, data: dict):
        src_id = data.get("source_node_id")
        tgt_id = data.get("target_node_id")
        src_port_name = data.get("source_port_name", "_default")
        tgt_port_name = data.get("target_port_name", "_default")

        src_id = int(src_id) if isinstance(src_id, str) and src_id.isdigit() else src_id
        tgt_id = int(tgt_id) if isinstance(tgt_id, str) and tgt_id.isdigit() else tgt_id

        for edge in list(self._edges):
            s_nid = self._owner_node_id(edge.source_port.parent_proxy)
            t_nid = self._owner_node_id(edge.target_port.parent_proxy)
            if (s_nid == src_id and t_nid == tgt_id
                    and edge.source_port.port_name == src_port_name
                    and edge.target_port.port_name == tgt_port_name):
                self.remove_edge(edge)
                return

    def _remote_chat_append(self, target: str, data: dict):
        from .chat_node import ChatNodeWidget
        node_id = int(target) if target.isdigit() else None
        if node_id is None:
            return
        node = self.app.nodes.get(node_id)
        if node and isinstance(node, ChatNodeWidget) and hasattr(node, '_history'):
            message = data.get("message", {})
            if message:
                node._history.append(message)
                node._redraw_chat_area()

    def _find_port(self, node_id, port_name: str, port_type: int):
        node = self.app.nodes.get(node_id)
        if node is None:
            return None
        if hasattr(node, 'iter_ports'):
            for p in node.iter_ports():
                if p.port_name == port_name and p.port_type == port_type:
                    return p
        proxy = getattr(node, 'proxy', None)
        if proxy and hasattr(proxy, 'widget'):
            w = proxy.widget()
            if w and hasattr(w, 'iter_ports'):
                for p in w.iter_ports():
                    if p.port_name == port_name and p.port_type == port_type:
                        return p
        return None

    def _send_node_add_op(self, node_id: int, node, pos: QPointF):
        if not self.server_mode or self._applying_remote_op:
            return
        category = self._node_category(node)
        self._send_op("node_add", node_id, {
            "_category": category,
            "x": pos.x(),
            "y": pos.y(),
        })

    def _send_node_remove_op(self, node_id):
        if not self.server_mode or self._applying_remote_op:
            return
        self._send_op("node_remove", node_id, {})

    def _send_node_move_op(self, node_id: int, x: float, y: float):
        if not self.server_mode or self._applying_remote_op:
            return
        self._send_op("node_move", node_id, {"x": x, "y": y})

    def _send_edge_add_op(self, edge):
        if not self.server_mode or self._applying_remote_op:
            return
        s_id = self._owner_node_id(edge.source_port.parent_proxy)
        t_id = self._owner_node_id(edge.target_port.parent_proxy)
        if s_id is None or t_id is None:
            return
        self._send_op("edge_add", "", {
            "source_node_id": s_id,
            "target_node_id": t_id,
            "source_port_name": edge.source_port.port_name or "_default",
            "target_port_name": edge.target_port.port_name or "_default",
        })

    def _send_edge_remove_op(self, edge):
        if not self.server_mode or self._applying_remote_op:
            return
        s_id = self._owner_node_id(edge.source_port.parent_proxy)
        t_id = self._owner_node_id(edge.target_port.parent_proxy)
        if s_id is None or t_id is None:
            return
        self._send_op("edge_remove", "", {
            "source_node_id": s_id,
            "target_node_id": t_id,
            "source_port_name": edge.source_port.port_name or "_default",
            "target_port_name": edge.target_port.port_name or "_default",
        })
