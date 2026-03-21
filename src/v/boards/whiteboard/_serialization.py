from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QPointF, QRectF, QTimer
from PyQt6.QtWidgets import QGraphicsProxyWidget

from v.logger import get_logger

logger = get_logger("qonvo.plugin")

_global_clipboard: Optional[Dict[str, Any]] = None
_global_paste_offset = 0


class SerializationMixin:

    @staticmethod
    def _copy_chat_data(nd):
        r = dict(nd)
        r['user_files'] = list(r.get('user_files', []))
        r['ai_image_paths'] = list(r.get('ai_image_paths', []))
        hist = r.get('history')
        if hist:
            new_hist = []
            for e in hist:
                entry = {**e, 'images': list(e.get('images', []))}
                prefs = e.get('preferred_candidates')
                if prefs:
                    entry['preferred_candidates'] = [
                        {**c, 'images': list(c.get('images', []))}
                        for c in prefs
                    ]
                new_hist.append(entry)
            r['history'] = new_hist
        return r

    def _get_cached_or_fresh(self, node):
        from .items import TextItem, GroupFrameItem

        nid = node.node_id
        cache = self._node_data_cache
        dirty = self._dirty_node_ids

        if nid in dirty or nid not in cache:
            if isinstance(node, TextItem):
                nd = {
                    "id": nid, "x": node.pos().x(), "y": node.pos().y(),
                    "text": node.toPlainText(),
                    "font_size": getattr(node, "_font_size", 16),
                    "rotation": node.rotation(),
                }
            elif isinstance(node, GroupFrameItem):
                nd = {
                    "id": nid, "x": node.pos().x(), "y": node.pos().y(),
                    "width": node.rect().width(), "height": node.rect().height(),
                    "label": node._label.toPlainText() if hasattr(node, "_label") else "",
                    "color": getattr(node, "color_name", "blue"),
                    "locked": getattr(node, "_locked", False),
                }
            elif hasattr(node, 'to_dict'):
                nd = node.to_dict()
            elif hasattr(node, 'get_data'):
                nd = node.get_data()
            else:
                return None
            cache[nid] = nd
        else:
            nd = cache[nid]
            if isinstance(node, (TextItem, GroupFrameItem)):
                nd['x'] = node.pos().x()
                nd['y'] = node.pos().y()
                if isinstance(node, GroupFrameItem):
                    nd['width'] = node.rect().width()
                    nd['height'] = node.rect().height()
            elif hasattr(node, 'proxy') and node.proxy:
                nd['x'] = node.proxy.pos().x()
                nd['y'] = node.proxy.pos().y()
                if hasattr(node, 'width'):
                    nd['width'] = node.width()
                    nd['height'] = node.height()
        return nd

    def collect_data(self):
        from .chat_node import ChatNodeWidget
        from .function_node import FunctionNodeWidget
        from .round_table import RoundTableWidget
        from .sticky_note import StickyNoteWidget
        from .prompt_node import PromptNodeWidget
        from .markdown_node import MarkdownNodeWidget
        from .button_node import ButtonNodeWidget
        from .switch_node import SwitchNodeWidget
        from .logic_nodes import LatchNodeWidget, AndGateWidget, OrGateWidget, NotGateWidget, XorGateWidget
        from .checklist import ChecklistWidget
        from .repository_node import RepositoryNodeWidget
        from .nixi_node import NixiNodeWidget
        from .ups_node import UpsNodeWidget
        from .rmv_node import RmvNodeWidget
        from .items import TextItem, GroupFrameItem

        data = {
            "type": "WhiteBoard",
            "nodes": [], "function_nodes": [], "round_tables": [],
            "buttons": [], "switch_nodes": [], "latch_nodes": [],
            "and_gates": [], "or_gates": [], "not_gates": [], "xor_gates": [],
            "repository_nodes": [], "functions_library": [],
            "edges": [], "pins": [], "texts": [], "sticky_notes": [],
            "prompt_nodes": [], "markdown_nodes": [], "image_cards": [], "checklists": [],
            "group_frames": [], "dimensions": [], "nixi_nodes": [],
            "ups_nodes": [], "rmv_nodes": [],
            "next_id": self.app._next_id,
            "system_prompt": self.system_prompt,
            "system_files": list(self.system_files),
        }

        _save_errors = []

        for node in self.app.nodes.values():
            try:
                nd = self._get_cached_or_fresh(node)
                if nd is None:
                    continue
                if isinstance(node, ChatNodeWidget):
                    data["nodes"].append(self._copy_chat_data(nd))
                elif isinstance(node, FunctionNodeWidget):
                    data["function_nodes"].append(dict(nd))
                elif isinstance(node, RoundTableWidget):
                    data["round_tables"].append(dict(nd))
                elif isinstance(node, PromptNodeWidget):
                    data["prompt_nodes"].append(dict(nd))
                elif isinstance(node, MarkdownNodeWidget):
                    data["markdown_nodes"].append(dict(nd))
                elif isinstance(node, StickyNoteWidget):
                    data["sticky_notes"].append(dict(nd))
                elif isinstance(node, ButtonNodeWidget):
                    data["buttons"].append(dict(nd))
                elif isinstance(node, SwitchNodeWidget):
                    data["switch_nodes"].append(dict(nd))
                elif isinstance(node, LatchNodeWidget):
                    data["latch_nodes"].append(dict(nd))
                elif isinstance(node, AndGateWidget):
                    data["and_gates"].append(dict(nd))
                elif isinstance(node, OrGateWidget):
                    data["or_gates"].append(dict(nd))
                elif isinstance(node, NotGateWidget):
                    data["not_gates"].append(dict(nd))
                elif isinstance(node, XorGateWidget):
                    data["xor_gates"].append(dict(nd))
                elif isinstance(node, ChecklistWidget):
                    data["checklists"].append(dict(nd))
                elif isinstance(node, RepositoryNodeWidget):
                    data["repository_nodes"].append(dict(nd))
                elif isinstance(node, NixiNodeWidget):
                    data["nixi_nodes"].append(dict(nd))
                elif isinstance(node, UpsNodeWidget):
                    data["ups_nodes"].append(dict(nd))
                elif isinstance(node, RmvNodeWidget):
                    data["rmv_nodes"].append(dict(nd))
                elif isinstance(node, TextItem):
                    data["texts"].append(dict(nd))
                elif isinstance(node, GroupFrameItem):
                    data["group_frames"].append(dict(nd))
            except Exception as e:
                _save_errors.append(f"node {getattr(node, 'node_id', '?')}: {e}")
                logger.error(f"[SAVE] Failed to collect node {getattr(node, 'node_id', '?')}: {e}")

        for card in self.image_card_items.values():
            try:
                nid = card.node_id
                if nid in self._dirty_node_ids or nid not in self._node_data_cache:
                    nd = card.get_data()
                    self._node_data_cache[nid] = nd
                else:
                    nd = self._node_data_cache[nid]
                    nd['x'] = card.pos().x()
                    nd['y'] = card.pos().y()
                data["image_cards"].append(dict(nd))
            except Exception as e:
                _save_errors.append(f"image_card {getattr(card, 'node_id', '?')}: {e}")
                logger.error(f"[SAVE] Failed to collect image_card {getattr(card, 'node_id', '?')}: {e}")
        for dim in self.dimension_items.values():
            try:
                data["dimensions"].append(dim.get_data())
            except Exception as e:
                _save_errors.append(f"dimension {getattr(dim, 'node_id', '?')}: {e}")
                logger.error(f"[SAVE] Failed to collect dimension {getattr(dim, 'node_id', '?')}: {e}")

        if _save_errors:
            msg = f"저장 중 {len(_save_errors)}개 오류:\n" + "\n".join(_save_errors[:5])
            try:
                from .toast_notification import ToastManager
                main_window = self.view.window() if self.view else None
                ToastManager.instance().show_toast(msg, main_window)
            except Exception:
                pass

        self._dirty_node_ids.clear()
        self._save_generation += 1

        logger.debug(f"[EDGE SAVE] Starting to save {len(self._edges)} edges")
        for i, edge in enumerate(self._edges):
            s_id = self._owner_node_id(edge.source_port.parent_proxy)
            t_id = self._owner_node_id(edge.target_port.parent_proxy)
            source_port_name = edge.source_port.port_name or "_default"
            target_port_name = edge.target_port.port_name or "_default"

            if s_id is None or t_id is None:
                logger.warning(f"[EDGE SKIP] Missing node ID: source={s_id}, target={t_id}, ports={source_port_name}->{target_port_name}")
                continue

            data["edges"].append(
                {
                    "source_node_id": s_id,
                    "target_node_id": t_id,
                    "source_port_name": source_port_name,
                    "target_port_name": target_port_name,
                    "start_node_id": s_id,
                    "end_node_id": t_id,
                    "start_key": source_port_name,
                    "end_key": target_port_name,
                }
            )

        logger.debug(f"[EDGE SAVE COMPLETE] Total edges saved: {len(data['edges'])}")

        for func_id, func_def in self.functions_library.items():
            data["functions_library"].append(func_def.to_dict())

        if self._lazy_mgr.has_pending():
            pending = self._lazy_mgr.get_all_pending_data()
            for category, rows in pending.items():
                if category not in data:
                    data[category] = []
                data[category].extend(copy.deepcopy(rows))
            saved_edge_keys = {
                (e.get("source_node_id", e.get("start_node_id")),
                 e.get("target_node_id", e.get("end_node_id")),
                 e.get("source_port_name", e.get("start_key", "_default")),
                 e.get("target_port_name", e.get("end_key", "_default")))
                for e in data["edges"]
            }
            for pe in self._lazy_mgr.get_pending_edges():
                key = (
                    pe.get("source_node_id", pe.get("start_node_id")),
                    pe.get("target_node_id", pe.get("end_node_id")),
                    pe.get("source_port_name", pe.get("start_key", "_default")),
                    pe.get("target_port_name", pe.get("end_key", "_default")),
                )
                if key not in saved_edge_keys:
                    data["edges"].append(pe)
                    saved_edge_keys.add(key)
                else:
                    logger.warning(f"[EDGE DEDUP] Skipped duplicate pending edge: {key}")

        used_models = set()
        for row in data.get("nodes", []):
            if row.get("model"):
                used_models.add(row["model"])
        for row in data.get("function_nodes", []):
            opts = row.get("node_options", {})
            if isinstance(opts, dict) and opts.get("model"):
                used_models.add(opts["model"])
        for row in data.get("round_tables", []):
            for p in row.get("participants", []):
                if p.get("model"):
                    used_models.add(p["model"])
        for func_data in data.get("functions_library", []):
            for n in func_data.get("nodes", []):
                if n.get("node_type") == "llm_call":
                    m = n.get("config", {}).get("model")
                    if m:
                        used_models.add(m)
        from v.model_plugin import PluginRegistry
        data["plugins_used"] = PluginRegistry.instance().get_used_plugin_ids(used_models)

        return data

    def _owner_node_id(self, owner):
        if isinstance(owner, QGraphicsProxyWidget):
            return getattr(owner.widget(), "node_id", None)
        return getattr(owner, "node_id", None)

    _ID_KEY_MAP = {
        "nodes": "id", "function_nodes": "id", "round_tables": "id",
        "repository_nodes": "id", "texts": "id",
        "group_frames": "id", "sticky_notes": "node_id", "prompt_nodes": "node_id", "markdown_nodes": "node_id",
        "buttons": "node_id", "switch_nodes": "node_id",
        "latch_nodes": "node_id", "and_gates": "node_id", "or_gates": "node_id",
        "not_gates": "node_id", "xor_gates": "node_id",
        "checklists": "node_id", "image_cards": "node_id",
        "dimensions": "node_id", "nixi_nodes": "node_id",
        "ups_nodes": "node_id", "rmv_nodes": "node_id",
    }

    def _categorize_selected_item(self, item):
        from .chat_node import ChatNodeWidget
        from .function_node import FunctionNodeWidget
        from .round_table import RoundTableWidget
        from .sticky_note import StickyNoteWidget
        from .prompt_node import PromptNodeWidget
        from .markdown_node import MarkdownNodeWidget
        from .button_node import ButtonNodeWidget
        from .switch_node import SwitchNodeWidget
        from .logic_nodes import LatchNodeWidget, AndGateWidget, OrGateWidget, NotGateWidget, XorGateWidget
        from .checklist import ChecklistWidget
        from .repository_node import RepositoryNodeWidget
        from .nixi_node import NixiNodeWidget
        from .ups_node import UpsNodeWidget
        from .rmv_node import RmvNodeWidget
        from .items import TextItem, GroupFrameItem, ImageCardItem
        from .dimension_item import DimensionItem

        if isinstance(item, QGraphicsProxyWidget):
            widget = item.widget()
            if widget is None:
                return None
            node_id = getattr(widget, 'node_id', None)
            if node_id is None:
                return None
            if isinstance(widget, ChatNodeWidget):
                return ("nodes", node_id, widget.get_data())
            elif isinstance(widget, FunctionNodeWidget):
                return ("function_nodes", node_id, widget.get_data())
            elif isinstance(widget, PromptNodeWidget):
                return ("prompt_nodes", node_id, widget.get_data())
            elif isinstance(widget, StickyNoteWidget):
                return ("sticky_notes", node_id, widget.get_data())
            elif isinstance(widget, MarkdownNodeWidget):
                return ("markdown_nodes", node_id, widget.get_data())
            elif isinstance(widget, ButtonNodeWidget):
                return ("buttons", node_id, widget.to_dict())
            elif isinstance(widget, SwitchNodeWidget):
                return ("switch_nodes", node_id, widget.to_dict())
            elif isinstance(widget, LatchNodeWidget):
                return ("latch_nodes", node_id, widget.to_dict())
            elif isinstance(widget, AndGateWidget):
                return ("and_gates", node_id, widget.to_dict())
            elif isinstance(widget, OrGateWidget):
                return ("or_gates", node_id, widget.to_dict())
            elif isinstance(widget, NotGateWidget):
                return ("not_gates", node_id, widget.to_dict())
            elif isinstance(widget, XorGateWidget):
                return ("xor_gates", node_id, widget.to_dict())
            elif isinstance(widget, RoundTableWidget):
                return ("round_tables", node_id, widget.get_data())
            elif isinstance(widget, ChecklistWidget):
                return ("checklists", node_id, widget.get_data())
            elif isinstance(widget, RepositoryNodeWidget):
                return ("repository_nodes", node_id, widget.get_data())
            elif isinstance(widget, NixiNodeWidget):
                return ("nixi_nodes", node_id, widget.get_data())
            elif isinstance(widget, UpsNodeWidget):
                return ("ups_nodes", node_id, widget.get_data())
            elif isinstance(widget, RmvNodeWidget):
                return ("rmv_nodes", node_id, widget.get_data())
        elif isinstance(item, TextItem):
            node_id = getattr(item, 'node_id', None)
            if node_id is None:
                return None
            return ("texts", node_id, {
                "id": node_id,
                "x": item.pos().x(), "y": item.pos().y(),
                "text": item.toPlainText(),
                "font_size": getattr(item, "_font_size", 16),
                "rotation": item.rotation(),
            })
        elif isinstance(item, GroupFrameItem):
            node_id = getattr(item, 'node_id', None)
            if node_id is None:
                return None
            return ("group_frames", node_id, {
                "id": node_id,
                "x": item.pos().x(), "y": item.pos().y(),
                "width": item.rect().width(), "height": item.rect().height(),
                "label": item._label.toPlainText() if hasattr(item, '_label') else "",
                "color": getattr(item, "color_name", "blue"),
                "locked": getattr(item, "_locked", False),
            })
        elif isinstance(item, ImageCardItem):
            node_id = getattr(item, 'node_id', None)
            if node_id is None:
                return None
            return ("image_cards", node_id, item.get_data())
        elif isinstance(item, DimensionItem):
            node_id = getattr(item, 'node_id', None)
            if node_id is None:
                return None
            return ("dimensions", node_id, item.get_data())
        return None

    def copy_selected(self):
        global _global_clipboard, _global_paste_offset
        if not self.scene:
            return
        selected = self.scene.selectedItems()
        if not selected:
            return

        items_data = []
        selected_ids = set()
        for item in selected:
            result = self._categorize_selected_item(item)
            if result:
                category, node_id, data = result
                items_data.append((category, node_id, copy.deepcopy(data)))
                selected_ids.add(node_id)

        if not items_data:
            return

        edges_data = []
        for edge in self._edges:
            s_id = self._owner_node_id(edge.source_port.parent_proxy)
            t_id = self._owner_node_id(edge.target_port.parent_proxy)
            if s_id in selected_ids and t_id in selected_ids:
                edges_data.append({
                    "source_node_id": s_id,
                    "target_node_id": t_id,
                    "source_port_name": edge.source_port.port_name or "_default",
                    "target_port_name": edge.target_port.port_name or "_default",
                })

        _global_clipboard = {"items": items_data, "edges": edges_data}
        _global_paste_offset = 0
        logger.info(f"[COPY] {len(items_data)} items, {len(edges_data)} edges")

    def paste_clipboard(self):
        global _global_clipboard, _global_paste_offset
        if not _global_clipboard or not self.scene:
            return

        _global_paste_offset += 1
        offset = 50 * _global_paste_offset

        items = copy.deepcopy(_global_clipboard["items"])
        edges = copy.deepcopy(_global_clipboard["edges"])

        id_remap = {}
        for category, old_id, row in items:
            new_id = self._next_id()
            id_remap[old_id] = new_id
            id_key = self._ID_KEY_MAP.get(category, "id")
            row[id_key] = new_id
            row["x"] = row.get("x", 0) + offset
            row["y"] = row.get("y", 0) + offset

        self.scene.clearSelection()

        for category, old_id, row in items:
            new_id = id_remap[old_id]
            self._materialize_single(category, new_id, row)

        self._reposition_all_ports()
        self._invalidate_all_port_caches()

        for edge_row in edges:
            new_s = id_remap.get(edge_row["source_node_id"])
            new_t = id_remap.get(edge_row["target_node_id"])
            if new_s is not None and new_t is not None:
                edge_row["source_node_id"] = new_s
                edge_row["target_node_id"] = new_t
                edge_row["start_node_id"] = new_s
                edge_row["end_node_id"] = new_t
                edge_row["start_key"] = edge_row.get("source_port_name", "_default")
                edge_row["end_key"] = edge_row.get("target_port_name", "_default")
                self._restore_edge(edge_row)

        for old_id in id_remap:
            new_id = id_remap[old_id]
            owner = self._owner_by_id(new_id)
            if owner is not None:
                owner.setSelected(True)

        self._manual_update_all_edges()
        self._notify_modified()
        logger.info(f"[PASTE] {len(items)} items, {len(edges)} edges at offset +{offset}")

    def cut_selected(self):
        self.copy_selected()
        if _global_clipboard and self.view:
            self.view._delete_selected_items()

    def _delete_item_by_scene_item(self, item):
        from .items import ImageCardItem, TextItem, GroupFrameItem
        from .dimension_item import DimensionItem

        if isinstance(item, DimensionItem):
            self.delete_dimension_item(item)
        elif isinstance(item, ImageCardItem):
            self.delete_scene_item(item)
        elif isinstance(item, GroupFrameItem):
            self.delete_group_frame(item)
        elif isinstance(item, TextItem):
            self.delete_text_item(item)
        elif isinstance(item, QGraphicsProxyWidget):
            self.delete_proxy_item(item)

    def move_items_to_dimension(self, scene_items, target_dimension):
        from .items import ImageCardItem, TextItem, GroupFrameItem
        from .dimension_item import DimensionItem

        board_data = target_dimension.get_board_data()
        seen_items = []
        seen_ids = set()
        for item in list(scene_items):
            result = self._categorize_selected_item(item)
            if not result:
                continue
            category, node_id, data = result
            if node_id in seen_ids:
                continue
            seen_ids.add(node_id)
            seen_items.append(item)
            data = copy.deepcopy(data)
            id_key = self._ID_KEY_MAP.get(category, "id")
            new_id = board_data.get("next_id", 1)
            data[id_key] = new_id
            board_data["next_id"] = new_id + 1
            col = (len(seen_items) - 1) % 3
            row = (len(seen_items) - 1) // 3
            data["x"] = 30 + col * 320
            data["y"] = 30 + row * 280
            board_data.setdefault(category, []).append(data)
        target_dimension.set_board_data(board_data)
        for item in seen_items:
            nid = getattr(item, 'node_id', None)
            self._remove_ports_and_edges(self._collect_ports(item))
            if isinstance(item, ImageCardItem):
                self.image_card_items.pop(nid, None)
            elif isinstance(item, DimensionItem):
                self.dimension_items.pop(nid, None)
            elif isinstance(item, GroupFrameItem):
                self.group_frame_items.pop(nid, None)
            elif isinstance(item, TextItem):
                self.text_items.pop(nid, None)
            self.app.nodes.pop(nid, None)
            self._node_data_cache.pop(nid, None)
            self._dirty_node_ids.discard(nid)
            if hasattr(item, 'stop_animation'):
                item.stop_animation()
            if self.scene:
                self.scene.removeItem(item)
        self._notify_modified()
        logger.info(f"[DIM-MOVE] {len(seen_items)} items -> dimension #{getattr(target_dimension, 'node_id', '?')}")

    def move_items_to_parent(self, scene_items):
        if not self._parent_plugin:
            return
        for item in list(scene_items):
            result = self._categorize_selected_item(item)
            if not result:
                continue
            category, node_id, data = result
            data = copy.deepcopy(data)
            id_key = self._ID_KEY_MAP.get(category, "id")
            new_id = self._parent_plugin._next_id()
            data[id_key] = new_id
            dim = self._parent_dimension_item
            if dim:
                data["x"] = dim.pos().x() + dim._width + 30
                data["y"] = dim.pos().y()
            self._parent_plugin._materialize_single(category, new_id, data)
        for item in list(scene_items):
            self._delete_item_by_scene_item(item)
        self._parent_plugin._reposition_all_ports()
        self._parent_plugin._invalidate_all_port_caches()
        self._parent_plugin._manual_update_all_edges()
        self._parent_plugin._notify_modified()
        self._notify_modified()
        logger.info(f"[DIM-MOVE] {len(scene_items)} items -> parent dimension")

    def _get_scene_viewport_rect(self) -> QRectF:
        if self.view is None:
            return QRectF(-500, -500, 1000, 1000)
        vp = self.view.viewport().rect()
        tl = self.view.mapToScene(vp.topLeft())
        br = self.view.mapToScene(vp.bottomRight())
        return QRectF(tl, br).normalized()

    def restore_data(self, data):
        from .function_types import FunctionDefinition
        from .chat_node import ChatNodeWidget
        from .items import ImageCardItem
        from .ups_node import UpsNodeWidget
        from .rmv_node import RmvNodeWidget

        plugins_used = data.get("plugins_used", [])
        if plugins_used:
            from v.model_plugin import PluginRegistry
            registry = PluginRegistry.instance()
            missing = [p for p in plugins_used if not registry.is_available(p)]
            if missing:
                from PyQt6.QtWidgets import QMessageBox
                reply = QMessageBox.warning(
                    self.view if hasattr(self, 'view') else None,
                    "Missing Plugins",
                    f"다음 플러그인이 설치되지 않았습니다:\n{', '.join(missing)}\n\n"
                    "해당 플러그인의 모델을 사용하는 노드는\n"
                    "초기화된 상태로 로드됩니다.\n\n"
                    "그래도 열겠습니까?",
                    QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Ok
                )
                if reply == QMessageBox.StandardButton.Cancel:
                    return

        if self._board_name:
            from v.board import BoardManager
            _temp = BoardManager.get_boards_dir() / '.temp' / self._board_name
            (_temp / 'attachments').mkdir(parents=True, exist_ok=True)
            ChatNodeWidget._board_temp_dir = str(_temp)
            ImageCardItem._board_temp_dir = str(_temp)
            UpsNodeWidget._board_temp_dir = str(_temp)
            RmvNodeWidget._board_temp_dir = str(_temp)

        for edge in list(self._edges):
            self.remove_edge(edge)
        if self.scene:
            for d in (self.proxies, self.function_proxies, self.round_table_proxies,
                      self.sticky_proxies, self.prompt_proxies, self.markdown_proxies, self.button_proxies, self.switch_proxies, self.latch_proxies, self.and_gate_proxies, self.or_gate_proxies, self.not_gate_proxies, self.xor_gate_proxies,
                      self.checklist_proxies, self.repository_proxies,
                      self.nixi_proxies, self.ups_proxies, self.rmv_proxies):
                for proxy in d.values():
                    self._remove_ports_and_edges(self._collect_ports(proxy))
            for d in (self.image_card_items, self.dimension_items):
                for item in d.values():
                    self._remove_ports_and_edges(self._collect_ports(item))

            for proxy in list(self.proxies.values()):
                self.scene.removeItem(proxy)
            for proxy in list(self.function_proxies.values()):
                self.scene.removeItem(proxy)
            for proxy in list(self.round_table_proxies.values()):
                self.scene.removeItem(proxy)
            for proxy in list(self.sticky_proxies.values()):
                self.scene.removeItem(proxy)
            for proxy in list(self.prompt_proxies.values()):
                self.scene.removeItem(proxy)
            for proxy in list(self.markdown_proxies.values()):
                self.scene.removeItem(proxy)
            for proxy in list(self.button_proxies.values()):
                self.scene.removeItem(proxy)
            for proxy in list(self.switch_proxies.values()):
                self.scene.removeItem(proxy)
            for proxy in list(self.latch_proxies.values()):
                self.scene.removeItem(proxy)
            for proxy in list(self.and_gate_proxies.values()):
                self.scene.removeItem(proxy)
            for proxy in list(self.or_gate_proxies.values()):
                self.scene.removeItem(proxy)
            for proxy in list(self.not_gate_proxies.values()):
                self.scene.removeItem(proxy)
            for proxy in list(self.xor_gate_proxies.values()):
                self.scene.removeItem(proxy)
            for proxy in list(self.checklist_proxies.values()):
                self.scene.removeItem(proxy)
            for proxy in list(self.repository_proxies.values()):
                self.scene.removeItem(proxy)
            for proxy in list(self.nixi_proxies.values()):
                self.scene.removeItem(proxy)
            for proxy in list(self.ups_proxies.values()):
                self.scene.removeItem(proxy)
            for proxy in list(self.rmv_proxies.values()):
                self.scene.removeItem(proxy)
            for item in list(self.text_items.values()):
                self.scene.removeItem(item)
            for item in list(self.group_frame_items.values()):
                self.scene.removeItem(item)
            for item in list(self.image_card_items.values()):
                self.scene.removeItem(item)
            for item in list(self.dimension_items.values()):
                self.scene.removeItem(item)

        self.proxies.clear()
        self.function_proxies.clear()
        self.round_table_proxies.clear()
        self.sticky_proxies.clear()
        self.prompt_proxies.clear()
        self.markdown_proxies.clear()
        self.button_proxies.clear()
        self.switch_proxies.clear()
        self.latch_proxies.clear()
        self.and_gate_proxies.clear()
        self.or_gate_proxies.clear()
        self.not_gate_proxies.clear()
        self.xor_gate_proxies.clear()
        self.checklist_proxies.clear()

        self.repository_proxies.clear()
        self.nixi_proxies.clear()
        self.ups_proxies.clear()
        self.rmv_proxies.clear()
        self.text_items.clear()
        self.group_frame_items.clear()
        self.image_card_items.clear()
        self.dimension_items.clear()
        self.app.nodes.clear()

        self.system_prompt = data.get("system_prompt", "")
        self.system_files = data.get("system_files", [])

        self.functions_library = {}
        for func_data in data.get("functions_library", []):
            func_def = FunctionDefinition.from_dict(func_data)
            self.functions_library[func_def.function_id] = func_def

        self._lazy_mgr.reset()
        self._lazy_mgr.ingest_data(data)
        self.app._next_id = max(self.app._next_id, data.get("next_id", self.app._next_id))

        viewport_rect = self._get_scene_viewport_rect()
        visible = self._lazy_mgr.query_visible(viewport_rect)

        self._batch_queue = list(visible)
        self._batch_loading = True
        self._process_batch()
