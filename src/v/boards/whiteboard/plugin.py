from __future__ import annotations

import os
import base64
import uuid
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, QPointF, QRectF, QTimer
from PyQt6.QtWidgets import QGraphicsProxyWidget, QGraphicsScene

from q import t
from v.boards.base import BoardPlugin
from v.settings import get_board_size
from v.logger import get_logger

from .view import WhiteboardView
from .items import PortItem, EdgeItem, ImageCardItem, TextItem, GroupFrameItem
from .dimension_item import DimensionItem
from .chat_node import ChatNodeWidget
from .repository_node import RepositoryNodeWidget
from .lazy_loader import LazyLoadManager

from ._server_mixin import ServerMixin
from ._node_factory import NodeFactoryMixin
from ._chat_send import ChatSendMixin
from ._chat_workers import ChatWorkersMixin
from ._serialization import SerializationMixin
from ._materialization import MaterializationMixin

logger = get_logger("qonvo.plugin")


class NodeProxyWidget(QGraphicsProxyWidget):
    def itemChange(self, change, value):
        import v.boards.whiteboard.items as _items_mod

        if change == self.GraphicsItemChange.ItemPositionChange:
            self._pre_move_pos = self.pos()
            if not _items_mod._group_moving:
                try:
                    scene = self.scene()
                    if scene and hasattr(scene, '_snap_engine'):
                        value = scene._snap_engine.snap(self, value)
                except Exception:
                    pass

        result = super().itemChange(change, value)

        if change == self.GraphicsItemChange.ItemPositionHasChanged:
            widget = self.widget()
            if widget and hasattr(widget, 'reposition_ports'):
                widget.reposition_ports()

            if not _items_mod._group_moving and self.isSelected():
                scene = self.scene()
                pre = getattr(self, '_pre_move_pos', None)
                if scene and pre is not None:
                    delta = self.pos() - pre
                    if delta.x() != 0 or delta.y() != 0:
                        _items_mod._group_moving = True
                        try:
                            for item in scene.selectedItems():
                                if item is not self:
                                    item.moveBy(delta.x(), delta.y())
                        finally:
                            _items_mod._group_moving = False

        return result

    def wheelEvent(self, event):
        event.ignore()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        pre = getattr(self, '_pre_move_pos', None)
        if pre is not None and self.pos() != pre:
            scene = self.scene()
            if scene and hasattr(scene, '_plugin'):
                scene._plugin._notify_modified()
                widget = self.widget()
                node_id = getattr(widget, 'node_id', None) if widget else None
                if node_id is not None and hasattr(scene._plugin, '_send_node_move_op'):
                    pos = self.pos()
                    scene._plugin._send_node_move_op(node_id, pos.x(), pos.y())


class WhiteBoardPlugin(
    ServerMixin,
    NodeFactoryMixin,
    ChatSendMixin,
    ChatWorkersMixin,
    SerializationMixin,
    MaterializationMixin,
    BoardPlugin,
):
    NAME = "WhiteBoard"
    DESCRIPTION = "Whiteboard"
    VERSION = "1.0"
    ICON = "WB"

    def __init__(self, app):
        super().__init__(app)
        self.view: Optional[WhiteboardView] = None

        self.proxies: Dict[int, QGraphicsProxyWidget] = {}
        self.function_proxies: Dict[int, QGraphicsProxyWidget] = {}
        self.round_table_proxies: Dict[int, QGraphicsProxyWidget] = {}
        self.sticky_proxies: Dict[int, QGraphicsProxyWidget] = {}
        self.prompt_proxies: Dict[int, QGraphicsProxyWidget] = {}
        self.markdown_proxies: Dict[int, QGraphicsProxyWidget] = {}
        self.button_proxies: Dict[int, QGraphicsProxyWidget] = {}
        self.checklist_proxies: Dict[int, QGraphicsProxyWidget] = {}

        self.repository_proxies: Dict[int, QGraphicsProxyWidget] = {}
        self.nixi_proxies: Dict[int, QGraphicsProxyWidget] = {}
        self.ups_proxies: Dict[int, QGraphicsProxyWidget] = {}
        self.rmv_proxies: Dict[int, QGraphicsProxyWidget] = {}
        self.switch_proxies: Dict[int, QGraphicsProxyWidget] = {}
        self.latch_proxies: Dict[int, QGraphicsProxyWidget] = {}
        self.and_gate_proxies: Dict[int, QGraphicsProxyWidget] = {}
        self.or_gate_proxies: Dict[int, QGraphicsProxyWidget] = {}
        self.not_gate_proxies: Dict[int, QGraphicsProxyWidget] = {}
        self.xor_gate_proxies: Dict[int, QGraphicsProxyWidget] = {}
        self.bulb_proxies: Dict[int, QGraphicsProxyWidget] = {}
        self.text_items: Dict[int, TextItem] = {}
        self.group_frame_items: Dict[int, GroupFrameItem] = {}
        self.image_card_items: Dict[int, ImageCardItem] = {}
        self.dimension_items: Dict[int, DimensionItem] = {}

        self._edges: List[EdgeItem] = []
        self._workers: List[Any] = []
        self._provider = None
        self._dimension_windows: List[Any] = []

        self._active_workers = 0
        self._max_concurrent_workers = 4
        self._pending_workers: List[tuple] = []

        self._preferred_results: Dict[int, list] = {}
        self._preferred_expected: Dict[int, int] = {}
        self._rework_params: Dict[int, dict] = {}

        self.system_prompt = ""
        self.system_files: List[str] = []
        self.functions_library: Dict[str, Any] = {}
        self._origin_item = None
        self._lazy_mgr = LazyLoadManager()
        self._batch_queue: List = []
        self._batch_loading = False
        self._board_name: Optional[str] = None

        self._parent_plugin: Optional['WhiteBoardPlugin'] = None
        self._parent_dimension_item: Optional[DimensionItem] = None
        self._history_search_window = None

        self._node_data_cache: Dict[int, Any] = {}
        self._dirty_node_ids: set = set()
        self._save_generation: int = 0

        self._server_client = None
        self._applying_remote_op = False

        self._migrate_dimension_images()

    def _migrate_dimension_images(self):
        import shutil
        from pathlib import Path
        from v.settings import get_app_data_path
        from v.board import BoardManager

        old_dir = Path(get_app_data_path()) / "dimension_images"
        if not old_dir.exists():
            return

        boards_dir = BoardManager.get_boards_dir()
        new_dir = boards_dir / '.temp' / 'dimension_images'

        try:
            logger.info(f"[MIGRATE] Moving dimension_images: {old_dir} → {new_dir}")

            new_dir.mkdir(parents=True, exist_ok=True)

            moved_count = 0
            for img_file in old_dir.glob("*.png"):
                try:
                    shutil.move(str(img_file), str(new_dir / img_file.name))
                    moved_count += 1
                except Exception as e:
                    logger.warning(f"[MIGRATE] Failed to move {img_file.name}: {e}")

            if not any(old_dir.iterdir()):
                old_dir.rmdir()
                logger.info(f"[MIGRATE] Removed empty old directory: {old_dir}")
            else:
                logger.warning(f"[MIGRATE] Old directory not empty, keeping it: {old_dir}")

            logger.info(f"[MIGRATE] dimension_images migration complete: {moved_count} files moved")

        except Exception as e:
            logger.error(f"[MIGRATE] dimension_images migration failed: {e}", exc_info=True)

    def create_view(self):
        from PyQt6.QtWidgets import QGraphicsEllipseItem
        from PyQt6.QtGui import QBrush, QPen, QColor

        size = get_board_size()
        half = size / 2
        self.scene = QGraphicsScene()
        self.scene.setItemIndexMethod(QGraphicsScene.ItemIndexMethod.BspTreeIndex)
        self.scene.setBspTreeDepth(14)
        self.scene.setSceneRect(-half, -half, size, size)
        self.scene._plugin = self
        self.app.bind(self.scene)

        self._origin_item = QGraphicsEllipseItem(-6, -6, 12, 12)
        self._origin_item.setBrush(QBrush(QColor("#444444")))
        self._origin_item.setPen(QPen(QColor("#666666"), 1))
        self._origin_item.setZValue(-100)
        self._origin_item.setToolTip("Origin (0, 0)")
        self.scene.addItem(self._origin_item)

        self.view = WhiteboardView(self.scene, plugin=self)
        self._manual_update_all_edges()
        self._lazy_mgr.setup_timer(self.view, self._materialize_visible_items)
        return self.view

    def center_on_origin(self):
        if self.view:
            self.view.centerOn(0, 0)

    def reset_zoom(self):
        if self.view:
            self.view.resetTransform()

    def _next_id(self, forced: Optional[int] = None) -> int:
        if forced is not None:
            self.app._next_id = max(self.app._next_id, forced + 1)
            return forced
        node_id = self.app._next_id
        self.app._next_id += 1
        return node_id

    def _cursor_scene_pos(self) -> QPointF:
        if self.view is None:
            return QPointF(0, 0)
        return self.view.mapToScene(self.view.viewport().rect().center())

    def _add_proxy(self, widget, node_id: int, pos: Optional[QPointF], type_dict: Dict[int, QGraphicsProxyWidget]):
        proxy = NodeProxyWidget()
        proxy.setWidget(widget)
        self.scene.addItem(proxy)
        proxy.setFlag(NodeProxyWidget.GraphicsItemFlag.ItemIsMovable, True)
        proxy.setFlag(NodeProxyWidget.GraphicsItemFlag.ItemIsSelectable, True)
        proxy.setFlag(NodeProxyWidget.GraphicsItemFlag.ItemIsFocusable, True)
        proxy.setFlag(NodeProxyWidget.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        proxy.node = widget
        widget.proxy = proxy
        widget.node_id = node_id
        if pos is None:
            pos = self._cursor_scene_pos()
        proxy.setPos(pos)
        self.proxies[node_id] = proxy
        type_dict[node_id] = proxy
        self.app.nodes[node_id] = widget
        self._send_node_add_op(node_id, widget, pos)
        self._notify_modified()
        return proxy

    def _add_port(self, port_type, parent, **kwargs) -> PortItem:
        port = PortItem(port_type, parent, **kwargs)
        self.scene.addItem(port)
        port.scene_add_label(self.scene)
        port.reposition()
        if self.view:
            self.view._all_port_items.add(port)
        return port

    def _create_ports(self, proxy, widget):
        widget.input_port = self._add_port(
            PortItem.INPUT, proxy, name="이전 대화",
            index=0, total=2, data_type=PortItem.TYPE_STRING)
        widget.output_port = self._add_port(
            PortItem.OUTPUT, proxy, name="응답",
            index=0, total=2, data_type=PortItem.TYPE_STRING)
        widget.signal_input_port = self._add_port(
            PortItem.INPUT, proxy, name="⚡ 실행",
            index=1, total=2, data_type=PortItem.TYPE_BOOLEAN)
        widget.signal_output_port = self._add_port(
            PortItem.OUTPUT, proxy, name="⚡ 완료",
            index=1, total=2, data_type=PortItem.TYPE_BOOLEAN)

    def _toggle_meta_ports(self, node):
        if node.meta_ports_enabled:
            self._enable_meta_ports(node)
        else:
            self._disable_meta_ports(node)

    def _enable_meta_ports(self, node):
        proxy = node.proxy
        if proxy is None or node.meta_output_ports:
            return
        node.meta_output_ports["elapsed_time"] = self._add_port(
            PortItem.OUTPUT, proxy, name="elapsed_time",
            index=0, total=1, data_type=PortItem.TYPE_STRING)
        node.meta_output_ports["model_name"] = self._add_port(
            PortItem.OUTPUT, proxy, name="model_name",
            index=0, total=1, data_type=PortItem.TYPE_STRING)
        node.meta_output_ports["tokens"] = self._add_port(
            PortItem.OUTPUT, proxy, name="tokens",
            index=0, total=1, data_type=PortItem.TYPE_STRING)
        self._reindex_chat_input_ports(node)

    def _disable_meta_ports(self, node):
        if not node.meta_output_ports:
            return
        for port in list(node.meta_output_ports.values()):
            for edge in list(port.edges):
                self.remove_edge(edge)
            if port.scene():
                self.scene.removeItem(port)
            port.scene_remove_label()
            if self.view and port in self.view._all_port_items:
                self.view._all_port_items.discard(port)
        node.meta_output_ports.clear()
        self._reindex_chat_input_ports(node)

    def _add_chat_input_port(self, node, port_type_str, port_name=None):
        proxy = node.proxy
        if not proxy:
            return

        if port_name is None:
            existing = {d["name"] for d in node.extra_input_defs}
            counter = 1
            while True:
                name = f"{port_type_str}_{counter}"
                if name not in existing:
                    break
                counter += 1
            port_name = name

        data_type = PortItem.TYPE_FILE if port_type_str == "image" else PortItem.TYPE_STRING
        port = PortItem(PortItem.INPUT, proxy, name=port_name,
                        index=0, total=1, data_type=data_type)
        self.scene.addItem(port)
        port.scene_add_label(self.scene)
        node.input_ports[port_name] = port
        if self.view:
            self.view._all_port_items.add(port)

        node.extra_input_defs.append({"name": port_name, "type": port_type_str})
        self._reindex_chat_input_ports(node)
        node._update_input_count()
        self._notify_modified()

    def _remove_chat_input_port(self, node, port_name):
        if port_name not in node.input_ports:
            return
        port = node.input_ports[port_name]
        for edge in list(port.edges):
            self.remove_edge(edge)
        if port.scene():
            self.scene.removeItem(port)
        port.scene_remove_label()
        if self.view and port in self.view._all_port_items:
            self.view._all_port_items.discard(port)
        del node.input_ports[port_name]

        node.extra_input_defs = [d for d in node.extra_input_defs if d["name"] != port_name]
        self._reindex_chat_input_ports(node)
        node._update_input_count()
        self._notify_modified()

    def _reindex_chat_input_ports(self, node):
        all_input = []
        if node.input_port:
            all_input.append(node.input_port)
        for port_name in sorted(node.input_ports.keys()):
            all_input.append(node.input_ports[port_name])
        if node.signal_input_port:
            all_input.append(node.signal_input_port)

        total = len(all_input)
        for i, port in enumerate(all_input):
            port.port_index = i
            port.port_total = total
            port.reposition()

        all_output = []
        if node.output_port:
            all_output.append(node.output_port)
        if node.signal_output_port:
            all_output.append(node.signal_output_port)
        meta = getattr(node, 'meta_output_ports', {})
        for pname in sorted(meta.keys()):
            all_output.append(meta[pname])
        out_total = len(all_output)
        for i, port in enumerate(all_output):
            port.port_index = i
            port.port_total = out_total
            port.reposition()

    def _mark_node_dirty(self, node_id):
        self._dirty_node_ids.add(node_id)
        self._notify_modified()

    def _notify_modified(self):
        if self._batch_loading:
            return
        if callable(self.on_modified):
            self.on_modified()
        if self.view and hasattr(self.view, '_branch_graph'):
            self.view._branch_graph.mark_dirty()

    def _manual_update_all_edges(self):
        for edge in list(self._edges):
            edge.update_path()
        for d in (self.proxies, self.function_proxies, self.round_table_proxies,
                  self.sticky_proxies, self.prompt_proxies, self.markdown_proxies, self.button_proxies, self.switch_proxies, self.latch_proxies, self.and_gate_proxies, self.or_gate_proxies, self.not_gate_proxies, self.xor_gate_proxies, self.bulb_proxies,
                  self.checklist_proxies, self.nixi_proxies, self.ups_proxies, self.rmv_proxies):
            for proxy in d.values():
                node = proxy.widget()
                if node and hasattr(node, "reposition_ports"):
                    node.reposition_ports()

    def create_edge(self, start_port: PortItem, end_port: PortItem):
        if start_port is None or end_port is None:
            return None
        if start_port is end_port:
            return None
        if start_port.port_type == end_port.port_type:
            return None

        if start_port.port_type == PortItem.INPUT:
            start_port, end_port = end_port, start_port

        if end_port.port_type == PortItem.INPUT and end_port.edges and not end_port.multi_connect:
            self.remove_edge(end_port.edges[0])

        for edge in self._edges:
            if edge.source_port is start_port and edge.target_port is end_port:
                return None

        edge = EdgeItem(start_port, end_port)
        self.scene.addItem(edge)
        self._edges.append(edge)
        self.app.edges.append(edge)
        self._send_edge_add_op(edge)
        self._notify_modified()

        if start_port.port_data_type == PortItem.TYPE_BOOLEAN and start_port.powered:
            edge.set_powered(True)
            end_port.set_powered(True)
            target_proxy = end_port.parent_proxy
            if target_proxy:
                target_node = target_proxy.widget() if hasattr(target_proxy, 'widget') else None
                if target_node:
                    tp_name = end_port.port_name or ""
                    src_node = start_port.parent_proxy.widget() if hasattr(start_port.parent_proxy, 'widget') else None
                    sig_data = getattr(src_node, '_pass_data', None) if src_node else None
                    if tp_name == "⚡ A" and hasattr(target_node, 'on_signal_a'):
                        target_node.on_signal_a(input_data=sig_data, powered=True)
                    elif tp_name == "⚡ B" and hasattr(target_node, 'on_signal_b'):
                        target_node.on_signal_b(input_data=sig_data, powered=True)

        if isinstance(start_port.parent_proxy, ImageCardItem) and start_port.parent_proxy.image_path:
            card = start_port.parent_proxy
            target = end_port.parent_proxy
            if isinstance(target, ImageCardItem):
                target.set_image(card.image_path)
            elif isinstance(target, DimensionItem):
                self._add_to_dimension(target, card.image_path, [card.image_path])

        return edge

    def remove_edge(self, edge: EdgeItem):
        self._send_edge_remove_op(edge)
        if edge in self._edges:
            self._edges.remove(edge)
        if edge in self.app.edges:
            self.app.edges.remove(edge)
        edge.disconnect()
        if self.scene:
            self.scene.removeItem(edge)
        self._notify_modified()

    def _collect_ports(self, item_or_proxy) -> list:
        obj = item_or_proxy
        if hasattr(item_or_proxy, "widget"):
            obj = item_or_proxy.widget() or item_or_proxy
        if hasattr(obj, "iter_ports"):
            return list(obj.iter_ports())
        return []

    def _remove_ports_and_edges(self, ports: list):
        port_set = set(id(p) for p in ports)

        for edge in list(self._edges):
            if id(edge.source_port) in port_set or id(edge.target_port) in port_set:
                self.remove_edge(edge)

        for port in ports:
            if port and self.scene:
                port.scene_remove_label()
                self.scene.removeItem(port)
                if self.view:
                    self.view._all_port_items.discard(port)

    def delete_proxy_item(self, proxy):
        node = proxy.widget()
        if node is None:
            if self.scene:
                self.scene.removeItem(proxy)
            return
        node_id = getattr(node, "node_id", None)
        self._send_node_remove_op(node_id)

        if hasattr(node, 'cleanup_temp_files'):
            node.cleanup_temp_files()

        self._remove_ports_and_edges(self._collect_ports(proxy))

        for d in (self.proxies, self.function_proxies, self.round_table_proxies,
                  self.sticky_proxies, self.prompt_proxies, self.markdown_proxies, self.button_proxies, self.switch_proxies, self.latch_proxies, self.and_gate_proxies, self.or_gate_proxies, self.not_gate_proxies, self.xor_gate_proxies, self.bulb_proxies,
                  self.checklist_proxies, self.repository_proxies,
                  self.nixi_proxies, self.ups_proxies, self.rmv_proxies):
            d.pop(node_id, None)
        self.app.nodes.pop(node_id, None)
        self._node_data_cache.pop(node_id, None)
        self._dirty_node_ids.discard(node_id)
        if self.scene:
            self.scene.removeItem(proxy)
        self._notify_modified()

    def _delete_scene_item(self, item, registry: dict):
        node_id = getattr(item, "node_id", None)
        self._send_node_remove_op(node_id)
        self._remove_ports_and_edges(self._collect_ports(item))
        registry.pop(node_id, None)
        self.app.nodes.pop(node_id, None)
        self._node_data_cache.pop(node_id, None)
        self._dirty_node_ids.discard(node_id)
        if hasattr(item, 'stop_animation'):
            item.stop_animation()
        if self.scene:
            self.scene.removeItem(item)
        self._notify_modified()

    def delete_scene_item(self, item):
        self._delete_scene_item(item, self.image_card_items)

    def delete_dimension_item(self, item):
        self._delete_scene_item(item, self.dimension_items)

    def delete_group_frame(self, item):
        self._delete_scene_item(item, self.group_frame_items)

    def delete_text_item(self, item):
        self._delete_scene_item(item, self.text_items)

    def set_port_state(self, source_port, powered: bool, data=None):
        if source_port is None or source_port.port_data_type != PortItem.TYPE_BOOLEAN:
            return
        if source_port.powered == powered:
            return
        if not hasattr(self, '_prop_depth'):
            self._prop_depth = 0
        if self._prop_depth > 32:
            return
        self._prop_depth += 1
        try:
            self._set_port_state_inner(source_port, powered, data)
        finally:
            self._prop_depth -= 1

    def _set_port_state_inner(self, source_port, powered, data):
        source_port.set_powered(powered)

        for edge in list(self._edges):
            if edge.source_port != source_port:
                continue
            edge.set_powered(powered)
            tgt = edge.target_port
            prev = tgt.powered
            tgt.set_powered(powered)

            target_proxy = tgt.parent_proxy
            if not target_proxy:
                continue
            target_node = target_proxy.widget() if hasattr(target_proxy, 'widget') else None
            if target_node is None:
                continue
            tp_name = tgt.port_name or ""
            if tp_name == "⚡ A" and hasattr(target_node, 'on_signal_a'):
                target_node.on_signal_a(input_data=data, powered=powered)
            elif tp_name == "⚡ B" and hasattr(target_node, 'on_signal_b'):
                target_node.on_signal_b(input_data=data, powered=powered)
            elif powered and not prev and hasattr(target_node, 'on_signal_input'):
                target_node.on_signal_input(input_data=data)

    def emit_signal(self, source_port, data=None):
        self.set_port_state(source_port, True, data)
        QTimer.singleShot(500, lambda: self.set_port_state(source_port, False))

    def _emit_complete_signal(self, node, images=None):
        nid = getattr(node, 'node_id', '?')
        img_count = len(images) if images else 0
        logger.info(f"[EMIT_COMPLETE] node={nid}, images={img_count}")

        notify = getattr(node, 'notify_on_complete', False)
        if notify:
            from .toast_notification import ToastManager
            node_name = getattr(node, 'function_name', None) or f"Node #{node.node_id}"
            main_window = self.view.window() if self.view else None
            ToastManager.instance().show_toast(f"{node_name} - 완료", main_window)

        _node_ai_response = getattr(node, 'ai_response', None)
        if hasattr(node, 'signal_output_port') and node.signal_output_port is not None:
            self.emit_signal(node.signal_output_port, data=_node_ai_response)

        output_port_to_check = None

        if hasattr(node, 'output_ports') and node.output_ports:
            first_output_name = next(iter(node.output_ports.keys()))
            output_port_to_check = node.output_ports[first_output_name]
        elif hasattr(node, 'output_port') and node.output_port is not None:
            output_port_to_check = node.output_port

        if output_port_to_check and hasattr(output_port_to_check, 'edges'):
            for edge in list(output_port_to_check.edges):
                target_port = edge.target_port
                if target_port is None or target_port.parent_proxy is None:
                    continue

                if isinstance(target_port.parent_proxy, DimensionItem):
                    result_text = getattr(node, 'ai_response', None) or getattr(node, 'text_content', '') or ''
                    self._add_to_dimension(target_port.parent_proxy, result_text, images)
                    continue

                if isinstance(target_port.parent_proxy, ImageCardItem):
                    self._update_image_card(target_port.parent_proxy, node, images)
                    continue

                if hasattr(target_port.parent_proxy, 'widget'):
                    _tw = target_port.parent_proxy.widget()
                    if isinstance(_tw, RepositoryNodeWidget):
                        self._save_to_repository(_tw, node, images)
                        continue

                if hasattr(target_port.parent_proxy, 'widget'):
                    target_node = target_port.parent_proxy.widget()
                    if target_node is None:
                        continue
                    if hasattr(target_node, 'on_signal_input'):
                        target_node.on_signal_input(input_data=_node_ai_response)

    def _update_image_card(self, card: ImageCardItem, source_node, images=None):
        from v.settings import get_app_data_path

        img_bytes = None

        if images:
            raw = images[0]
            if isinstance(raw, bytes):
                img_bytes = raw
            elif isinstance(raw, str):
                if os.path.isfile(raw):
                    card.set_image(raw)
                    return
                elif raw.startswith("data:image"):
                    _, encoded = raw.split(",", 1)
                    img_bytes = base64.b64decode(encoded)
                else:
                    try:
                        img_bytes = base64.b64decode(raw)
                    except Exception:
                        pass

        if not img_bytes:
            src_path = getattr(source_node, 'image_path', None)
            if src_path and os.path.exists(src_path):
                card.set_image(src_path)
                return

        if not img_bytes:
            logger.warning("[IMAGE_CARD] No image data available for card update")
            return

        from v.board import BoardManager
        board_name = self._board_name or "untitled"
        temp_dir = BoardManager.get_boards_dir() / '.temp' / board_name / 'attachments'
        temp_dir.mkdir(parents=True, exist_ok=True)
        img_path = str(temp_dir / f"{uuid.uuid4().hex}.png")
        try:
            with open(img_path, "wb") as f:
                f.write(img_bytes)
            logger.info(f"[IMAGE_CARD] Saved: {img_path} ({len(img_bytes)} bytes)")
            card.set_image(img_path)
        except Exception as e:
            logger.error(f"[IMAGE_CARD] Failed to save: {e}")

    def _on_image_card_changed(self, card: ImageCardItem):
        if not card.output_port or not card.image_path:
            return
        for edge in list(self._edges):
            if edge.source_port != card.output_port:
                continue
            target = edge.target_port.parent_proxy
            if isinstance(target, ImageCardItem):
                target.set_image(card.image_path)
            elif isinstance(target, DimensionItem):
                self._add_to_dimension(target, card.image_path, [card.image_path])

    def _save_to_repository(self, repo_node: RepositoryNodeWidget, source_node, images=None):
        if not repo_node.folder_path:
            return
        if images:
            for img_data in images:
                repo_node._save_incoming_data(img_data)
        else:
            text = getattr(source_node, 'ai_response', None)
            if text:
                repo_node._save_incoming_data(text)
        repo_node._scan_folder()
        self._emit_complete_signal(repo_node)

    def _add_to_dimension(self, dimension_item: DimensionItem, content: str, images=None):
        from PyQt6.QtGui import QPixmap
        from v.constants import DIMENSION_RESULTS_PER_ROW, DIMENSION_ROW_HEIGHT

        board_data = dimension_item.get_board_data()
        next_id = board_data.get("next_id", 1)

        group_count = len(board_data.get("group_frames", [])) + 1
        group_label = f"Result #{group_count}"

        results_per_row = DIMENSION_RESULTS_PER_ROW
        row = (group_count - 1) // results_per_row
        group_y = 100 + row * DIMENSION_ROW_HEIGHT

        existing_groups_in_row = [
            g for g in board_data.get("group_frames", [])
            if abs(g.get("y", 0) - group_y) < 50
        ]

        if existing_groups_in_row:
            rightmost = max(existing_groups_in_row, key=lambda g: g.get("x", 0) + g.get("width", 0))
            group_x = rightmost.get("x", 0) + rightmost.get("width", 0) + 50
        else:
            group_x = 100

        items_in_group = []
        max_item_width = 400
        current_y = group_y + 40

        if content:
            text_len = len(content)
            if text_len < 100:
                sticky_width = 400
                sticky_height = 120
            elif text_len < 300:
                sticky_width = 600
                sticky_height = 150
            elif text_len < 800:
                sticky_width = 700
                sticky_height = 200
            else:
                sticky_width = 800
                sticky_height = 250

            max_item_width = max(max_item_width, sticky_width)

            sticky_data = {
                "type": "sticky_note",
                "node_id": next_id,
                "x": group_x + 20,
                "y": current_y,
                "width": sticky_width,
                "height": sticky_height,
                "title": "Output",
                "body": content,
                "color": "yellow",
            }
            if "sticky_notes" not in board_data:
                board_data["sticky_notes"] = []
            board_data["sticky_notes"].append(sticky_data)
            items_in_group.append(("sticky_note", next_id))
            next_id += 1
            current_y += sticky_height + 20

        if images:
            from v.board import BoardManager
            boards_dir = BoardManager.get_boards_dir()
            board_name = self._board_name or "untitled"
            img_dir = boards_dir / '.temp' / board_name / 'attachments'
            img_dir.mkdir(parents=True, exist_ok=True)

            for idx, img_data in enumerate(images):
                img_path = None
                if isinstance(img_data, str) and os.path.isfile(img_data):
                    img_path = img_data
                else:
                    raw_bytes = None
                    if isinstance(img_data, bytes):
                        raw_bytes = img_data
                    elif isinstance(img_data, str):
                        if img_data.startswith("data:image"):
                            header, encoded = img_data.split(",", 1)
                            raw_bytes = base64.b64decode(encoded)
                        else:
                            try:
                                raw_bytes = base64.b64decode(img_data)
                            except Exception:
                                continue

                    if not raw_bytes:
                        continue

                    img_filename = f"{uuid.uuid4().hex}.png"
                    img_path = os.path.join(img_dir, img_filename)
                    try:
                        with open(img_path, "wb") as f:
                            f.write(raw_bytes)
                        logger.info(f"[DIM] Saved image: {img_path} ({len(raw_bytes)} bytes)")
                    except Exception as e:
                        logger.error(f"[DIM] Failed to save image: {e}")
                        continue

                if not img_path:
                    continue

                pixmap = QPixmap(img_path)
                if not pixmap.isNull():
                    img_width = pixmap.width()
                    img_height = pixmap.height()

                    max_width = 800
                    if img_width > max_width:
                        scale = max_width / img_width
                        card_width = max_width
                        card_height = int(img_height * scale)
                    else:
                        card_width = img_width
                        card_height = img_height

                    max_item_width = max(max_item_width, card_width)
                else:
                    card_width = 400
                    card_height = 300

                image_card_data = {
                    "type": "image_card",
                    "node_id": next_id,
                    "x": group_x + 20,
                    "y": current_y,
                    "width": card_width,
                    "height": card_height,
                    "image_path": img_path,
                }
                if "image_cards" not in board_data:
                    board_data["image_cards"] = []
                board_data["image_cards"].append(image_card_data)
                items_in_group.append(("image_card", next_id))
                next_id += 1
                current_y += card_height + 20

        group_width = max_item_width + 40
        group_height = max(200, current_y - group_y + 20)

        group_frame_data = {
            "type": "group_frame",
            "id": next_id,
            "x": group_x,
            "y": group_y,
            "width": group_width,
            "height": group_height,
            "label": group_label,
            "color": "blue",
        }
        if "group_frames" not in board_data:
            board_data["group_frames"] = []
        board_data["group_frames"].append(group_frame_data)
        next_id += 1

        board_data["next_id"] = next_id

        dimension_item.set_board_data(board_data)

        self._notify_modified()
