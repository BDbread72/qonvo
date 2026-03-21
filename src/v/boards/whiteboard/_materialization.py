from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QPointF, QRectF, QTimer
from PyQt6.QtWidgets import QGraphicsProxyWidget

from q import t
from .items import PortItem, ImageCardItem
from .dimension_item import DimensionItem
from v.logger import get_logger

logger = get_logger("qonvo.plugin")


class MaterializationMixin:

    def _process_batch(self):
        from v.constants import LAZY_LOAD_BATCH_SIZE

        if not self._batch_queue:
            self._finalize_batch_loading()
            return

        batch = self._batch_queue[:LAZY_LOAD_BATCH_SIZE]
        self._batch_queue = self._batch_queue[LAZY_LOAD_BATCH_SIZE:]

        for category, node_id, row in batch:
            if node_id in self._lazy_mgr._materialized_ids:
                continue
            try:
                self._materialize_single(category, node_id, row)
                self._lazy_mgr.mark_materialized(node_id, category)
            except Exception as e:
                logger.error(f"[LAZY] Failed to materialize {category} id={node_id}: {e}")
                try:
                    from .toast_notification import ToastManager
                    main_window = self.view.window() if self.view else None
                    ToastManager.instance().show_toast(f"로드 오류: {category} #{node_id}", main_window)
                except Exception:
                    pass

        resolvable = self._lazy_mgr.get_resolvable_edges()
        if resolvable:
            self._reposition_all_ports()
            self._invalidate_all_port_caches()
            for edge_data in resolvable:
                self._restore_edge(edge_data)
            self._manual_update_all_edges()

        if self._batch_queue:
            QTimer.singleShot(0, self._process_batch)
        else:
            self._finalize_batch_loading()

    def _finalize_batch_loading(self):
        try:
            self._reposition_all_ports()
            self._invalidate_all_port_caches()

            resolvable = self._lazy_mgr.get_resolvable_edges()
            for edge_data in resolvable:
                self._restore_edge(edge_data)

            self._manual_update_all_edges()
        except Exception as e:
            logger.error(f"[LAZY] _finalize_batch_loading error: {e}")
        finally:
            self._batch_loading = False

        pending_count = sum(len(d) for d in self._lazy_mgr._pending.values())
        logger.info(f"[LAZY] Initial load complete: {len(self._lazy_mgr._materialized_ids)} materialized, "
                     f"{pending_count} pending")

        self._verify_load()

        self._resume_pending_batches()

    def _verify_load(self):
        missing = []
        all_dicts = {
            'proxies': self.proxies,
            'function': self.function_proxies,
            'round_table': self.round_table_proxies,
            'sticky': self.sticky_proxies,
            'prompt': self.prompt_proxies,
            'markdown': self.markdown_proxies,
            'button': self.button_proxies,
            'switch': self.switch_proxies,
            'latch': self.latch_proxies,
            'and': self.and_gate_proxies,
            'or': self.or_gate_proxies,
            'not': self.not_gate_proxies,
            'xor': self.xor_gate_proxies,
            'checklist': self.checklist_proxies,
            'repository': self.repository_proxies,
            'nixi': self.nixi_proxies,
            'ups': self.ups_proxies,
            'rmv': self.rmv_proxies,
            'image_card': self.image_card_items,
            'dimension': self.dimension_items,
            'text': self.text_items,
            'group_frame': self.group_frame_items,
        }
        created_ids = set()
        for d in all_dicts.values():
            created_ids.update(d.keys())

        for nid in self._lazy_mgr._materialized_ids:
            if nid not in created_ids:
                missing.append(nid)

        if missing:
            msg = f"로드 검증: {len(missing)}개 아이템 누락 (ID: {missing[:10]})"
            logger.error(f"[VERIFY] {msg}")
            try:
                from .toast_notification import ToastManager
                mw = self.view.window() if self.view else None
                ToastManager.instance().show_toast(msg, mw)
            except Exception:
                pass
        else:
            logger.info(f"[VERIFY] 로드 검증 통과: {len(created_ids)}개 아이템 정상")

    def _materialize_single(self, category: str, node_id: int, row: dict):
        dispatch = {
            "nodes": self._materialize_chat_node,
            "function_nodes": self._materialize_function_node,
            "sticky_notes": self._materialize_sticky_note,
            "prompt_nodes": self._materialize_prompt_node,
            "markdown_nodes": self._materialize_markdown,
            "buttons": self._materialize_button,
            "switch_nodes": self._materialize_switch,
            "latch_nodes": self._materialize_latch,
            "and_gates": self._materialize_and_gate,
            "or_gates": self._materialize_or_gate,
            "not_gates": self._materialize_not_gate,
            "xor_gates": self._materialize_xor_gate,
            "round_tables": self._materialize_round_table,
            "checklists": self._materialize_checklist,
            "repository_nodes": self._materialize_repository_node,
            "texts": self._materialize_text,
            "group_frames": self._materialize_group_frame,
            "image_cards": self._materialize_image_card,
            "dimensions": self._materialize_dimension,
            "nixi_nodes": self._materialize_nixi_node,
            "ups_nodes": self._materialize_ups_node,
            "rmv_nodes": self._materialize_rmv_node,
        }
        handler = dispatch.get(category)
        if handler:
            handler(row)

    def _materialize_chat_node(self, row):
        proxy = self.add_node(QPointF(row.get("x", 0), row.get("y", 0)), node_id=row.get("id"))
        if proxy is None:
            return
        node = proxy.widget()
        if row.get("width") and row.get("height"):
            node.resize(int(row["width"]), int(row["height"]))
        node.user_message = row.get("user_message")
        node.user_files = row.get("user_files", [])
        node.ai_response = row.get("ai_response")
        saved_model = row.get("model", "")
        if saved_model:
            idx = node.model_combo.findData(saved_model)
            if idx >= 0:
                node.model_combo.setCurrentIndex(idx)
        saved_opts = row.get("node_options", {})
        if saved_opts:
            node.node_options = saved_opts
            if "aspect_ratio" in saved_opts:
                idx = node.ratio_combo.findText(saved_opts["aspect_ratio"])
                if idx >= 0:
                    node.ratio_combo.setCurrentIndex(idx)
            if "image_size" in saved_opts:
                idx = node.size_combo.findText(saved_opts["image_size"])
                if idx >= 0:
                    node.size_combo.setCurrentIndex(idx)
            if "temperature" in saved_opts:
                node.temp_spin.setValue(saved_opts["temperature"])
            if "top_p" in saved_opts:
                node.top_p_spin.setValue(saved_opts["top_p"])
            if "max_output_tokens" in saved_opts:
                node.max_tokens_spin.setValue(saved_opts["max_output_tokens"])
        node.pinned = row.get("pinned", False)
        node.btn_pin.setChecked(node.pinned)
        node.ai_image_paths = row.get("ai_image_paths", [])
        node.thought_signatures = row.get("thought_signatures", [])
        node.tokens_in = row.get("tokens_in", 0)
        node.tokens_out = row.get("tokens_out", 0)
        node.notify_on_complete = row.get("notify_on_complete", False)
        node.btn_notify.setChecked(node.notify_on_complete)
        node.preferred_options_enabled = row.get("preferred_options_enabled", False)
        node.preferred_options_count = row.get("preferred_options_count", 3)
        node.btn_pref_toggle.setChecked(node.preferred_options_enabled)
        node.pref_count_spin.setValue(node.preferred_options_count)
        node.pref_count_spin.setEnabled(node.preferred_options_enabled)
        if row.get("opts_panel_visible", False):
            node.btn_opts_toggle.setChecked(True)
        node.on_toggle_meta = self._toggle_meta_ports
        if row.get("meta_ports_enabled", False):
            node.meta_ports_enabled = True
            node.btn_meta_toggle.setChecked(True)
            self._enable_meta_ports(node)
        for port_def in row.get("extra_input_defs", []):
            self._add_chat_input_port(node, port_def["type"], port_def.get("name"))

        node._history = row.get("history", [])
        if not node._history and row.get("ai_response"):
            node._history = [{
                "user": row.get("user_message", ""),
                "files": row.get("user_files", []),
                "response": row.get("ai_response", ""),
                "images": row.get("ai_image_paths", []),
                "tokens_in": row.get("tokens_in", 0),
                "tokens_out": row.get("tokens_out", 0),
                "model": row.get("model", ""),
            }]
        node._archive_path = row.get("archive_path")
        node._archived_count = row.get("archived_count", 0)
        if node._history:
            last = node._history[-1]
            candidates = last.get("preferred_candidates", [])
            if candidates:
                node.pending_results = [(c["text"], c.get("images", [])) for c in candidates]
                node._btn_pref_view.setText(t("chat.preferred_candidates", count=len(candidates)))
                node._btn_pref_view.show()
                node._on_preferred_selected = self._on_chat_preferred_option_selected
        node._running = False
        node._update_status("done" if node._history else "idle")

    def _materialize_function_node(self, row):
        proxy = self.add_function(QPointF(row.get("x", 0), row.get("y", 0)), node_id=row.get("id"))
        if proxy is None:
            return
        node = proxy.widget()
        if row.get("width") and row.get("height"):
            node.resize(int(row["width"]), int(row["height"]))
        if row.get("function_id") and row.get("function_name"):
            function_id = row.get("function_id")
            function_name = row.get("function_name")
            node.set_function(function_id, function_name)
            function_def = self.functions_library.get(function_id)
            if function_def:
                self._update_function_ports(node, function_def)
                logger.debug(f"[LOAD] Function Node {node.node_id}: restored function '{function_name}'")
            else:
                logger.warning(f"[LOAD] Function Node {node.node_id}: function '{function_name}' "
                             f"(ID: {function_id}) not found in library")
                node.set_response(f"\u26a0\ufe0f 함수 '{function_name}'을(를) 찾을 수 없습니다.\n"
                                f"함수가 라이브러리에서 삭제되었거나 ID가 변경되었습니다.",
                                done=True)
        elif row.get("function_id") is None and row.get("function_name") is None:
            logger.info(f"[LOAD] Function Node {node.node_id}: no function set (migrated from old version)")
        node.sent = row.get("sent", False)
        node.pinned = row.get("pinned", False)
        node.btn_pin.setChecked(node.pinned)
        node.tokens_in = row.get("tokens_in", 0)
        node.tokens_out = row.get("tokens_out", 0)
        saved_opts = row.get("node_options", {})
        if saved_opts:
            node.node_options = saved_opts
            if "aspect_ratio" in saved_opts:
                idx = node.ratio_combo.findText(saved_opts["aspect_ratio"])
                if idx >= 0:
                    node.ratio_combo.setCurrentIndex(idx)
            if "temperature" in saved_opts:
                node.temp_spin.setValue(saved_opts["temperature"])
            if "top_p" in saved_opts:
                node.top_p_spin.setValue(saved_opts["top_p"])
            if "max_output_tokens" in saved_opts:
                node.max_tokens_spin.setValue(saved_opts["max_output_tokens"])
        node.notify_on_complete = row.get("notify_on_complete", False)
        node.btn_notify.setChecked(node.notify_on_complete)
        node.preferred_options_enabled = row.get("preferred_options_enabled", False)
        node.preferred_options_count = row.get("preferred_options_count", 3)
        node.btn_pref_toggle.setChecked(node.preferred_options_enabled)
        node.pref_count_spin.setValue(node.preferred_options_count)
        node.pref_count_spin.setEnabled(node.preferred_options_enabled)
        if row.get("opts_panel_visible", False):
            node.btn_opts_toggle.setChecked(True)
        if row.get("ai_response") and not str(node.ai_response or "").startswith("\u26a0\ufe0f"):
            node.set_response(row.get("ai_response"), done=False)

    def _materialize_sticky_note(self, row):
        proxy = self.add_sticky(QPointF(row.get("x", 0), row.get("y", 0)), node_id=row.get("node_id"))
        if proxy is None:
            return
        node = proxy.widget()
        if row.get("width") and row.get("height"):
            node.resize(int(row["width"]), int(row["height"]))
        node.title_edit.setText(row.get("title", ""))
        node.body_edit.setPlainText(row.get("body", ""))
        if row.get("color"):
            node._set_color(row["color"])

    def _materialize_prompt_node(self, row):
        proxy = self.add_prompt_node(QPointF(row.get("x", 0), row.get("y", 0)), node_id=row.get("node_id"))
        if proxy is None:
            return
        node = proxy.widget()
        if row.get("width") and row.get("height"):
            node.resize(int(row["width"]), int(row["height"]))
        node.title_edit.setText(row.get("title", ""))
        node.body_edit.setPlainText(row.get("body", ""))

        role = row.get("role", "system")
        idx = node.role_combo.findData(role)
        if idx >= 0:
            node.role_combo.setCurrentIndex(idx)

        enabled = row.get("enabled", True)
        node.btn_enable.setChecked(enabled)
        node._prompt_enabled = enabled
        node._apply_enabled_visual()

        node.priority_spin.setValue(row.get("priority", 0))

    def _materialize_markdown(self, row):
        proxy = self.add_markdown(QPointF(row.get("x", 0), row.get("y", 0)), node_id=row.get("node_id"))
        if proxy is None:
            return
        node = proxy.widget()
        if row.get("width") and row.get("height"):
            node.resize(int(row["width"]), int(row["height"]))
        node._raw_md = row.get("markdown", "")
        preview = row.get("preview_mode", True)
        node._preview_mode = preview
        if preview:
            node.text_area.setMarkdown(node._raw_md)
        else:
            node.text_area.setReadOnly(False)
            node.text_area.setPlainText(node._raw_md)
            node.toggle_btn.setText("\U0001f441")

    def _materialize_button(self, row):
        proxy = self.add_button(QPointF(row.get("x", 0), row.get("y", 0)), node_id=row.get("node_id"))
        if proxy is None:
            return
        node = proxy.widget()
        if row.get("width") and row.get("height"):
            node.setFixedSize(int(row["width"]), int(row["height"]))
        node.click_count = row.get("click_count", 0)
        node.input_data = row.get("input_data")
        label = row.get("label", "Button")
        node._label = label
        node.title_label.setText(label)

    def _materialize_switch(self, row):
        proxy = self.add_switch(QPointF(row.get("x", 0), row.get("y", 0)), node_id=row.get("node_id"))
        if proxy is None:
            return
        node = proxy.widget()
        if row.get("width") and row.get("height"):
            node.setFixedSize(int(row["width"]), int(row["height"]))
        node.is_on = row.get("is_on", True)
        node.toggle_btn.setChecked(node.is_on)
        node.toggle_btn.setText("ON" if node.is_on else "OFF")
        node._apply_style()
        node._apply_toggle_style()

    def _materialize_latch(self, row):
        proxy = self.add_latch(QPointF(row.get("x", 0), row.get("y", 0)), node_id=row.get("node_id"))
        if proxy is None:
            return
        node = proxy.widget()
        if row.get("width") and row.get("height"):
            node.setFixedSize(int(row["width"]), int(row["height"]))
        node._latched = row.get("latched", False)
        node._data = row.get("data")
        node._output_on = node._latched
        node._pass_powered = node._latched
        node.signal_output_port.set_powered(node._latched)
        node._update_visual()

    def _materialize_and_gate(self, row):
        proxy = self.add_and_gate(QPointF(row.get("x", 0), row.get("y", 0)), node_id=row.get("node_id"))
        if proxy is None:
            return
        node = proxy.widget()
        if row.get("width") and row.get("height"):
            node.setFixedSize(int(row["width"]), int(row["height"]))
        node._a_on = row.get("a_on", False)
        node._b_on = row.get("b_on", False)
        node._a_data = row.get("a_data")
        node._b_data = row.get("b_data")
        out = node._a_on and node._b_on
        node._output_on = out
        node._pass_powered = out
        node.signal_output_port.set_powered(out)
        a = "A" if node._a_on else "-"
        b = "B" if node._b_on else "-"
        node.status_label.setText(f"{a} & {b}")

    def _materialize_or_gate(self, row):
        proxy = self.add_or_gate(QPointF(row.get("x", 0), row.get("y", 0)), node_id=row.get("node_id"))
        if proxy is None:
            return
        node = proxy.widget()
        if row.get("width") and row.get("height"):
            node.setFixedSize(int(row["width"]), int(row["height"]))
        node._a_on = row.get("a_on", False)
        node._b_on = row.get("b_on", False)
        node._a_data = row.get("a_data")
        node._b_data = row.get("b_data")
        out = node._a_on or node._b_on
        node._output_on = out
        node._pass_powered = out
        node.signal_output_port.set_powered(out)
        a = "A" if node._a_on else "-"
        b = "B" if node._b_on else "-"
        node.status_label.setText(f"{a} | {b}")

    def _materialize_not_gate(self, row):
        proxy = self.add_not_gate(QPointF(row.get("x", 0), row.get("y", 0)), node_id=row.get("node_id"))
        if proxy is None:
            return
        node = proxy.widget()
        if row.get("width") and row.get("height"):
            node.setFixedSize(int(row["width"]), int(row["height"]))
        node._in_on = row.get("in_on", False)
        node._output_on = not node._in_on
        node._pass_powered = node._output_on
        node.signal_output_port.set_powered(node._output_on)
        node.status_label.setText("ON" if node._output_on else "OFF")

    def _materialize_xor_gate(self, row):
        proxy = self.add_xor_gate(QPointF(row.get("x", 0), row.get("y", 0)), node_id=row.get("node_id"))
        if proxy is None:
            return
        node = proxy.widget()
        if row.get("width") and row.get("height"):
            node.setFixedSize(int(row["width"]), int(row["height"]))
        node._a_on = row.get("a_on", False)
        node._b_on = row.get("b_on", False)
        node._a_data = row.get("a_data")
        node._b_data = row.get("b_data")
        out = node._a_on != node._b_on
        node._output_on = out
        node._pass_powered = out
        node.signal_output_port.set_powered(out)
        a = "A" if node._a_on else "-"
        b = "B" if node._b_on else "-"
        node.status_label.setText(f"{a} ^ {b}")

    def _materialize_round_table(self, row):
        proxy = self.add_round_table(QPointF(row.get("x", 0), row.get("y", 0)), node_id=row.get("id"))
        if proxy is None:
            return
        node = proxy.widget()
        if row.get("width") and row.get("height"):
            node.resize(int(row["width"]), int(row["height"]))
        node.participants = row.get("participants", [])
        node.conversation_log = row.get("conversation_log", [])
        node.user_message = row.get("user_message")
        node.ai_response = row.get("ai_response")
        node.sent = row.get("sent", False)
        node.pinned = row.get("pinned", False)
        node.tokens_in = row.get("tokens_in", 0)
        node.tokens_out = row.get("tokens_out", 0)
        node._update_participants_bar()
        if node.ai_response:
            node.set_response(node.ai_response, done=True)

    def _materialize_checklist(self, row):
        proxy = self.add_checklist(
            QPointF(row.get("x", 0), row.get("y", 0)),
            node_id=row.get("node_id"),
            title=row.get("title", ""),
            items=row.get("items", [])
        )
        if proxy is None:
            return
        node = proxy.widget()
        if row.get("width") and row.get("height"):
            node.resize(int(row["width"]), int(row["height"]))

    def _materialize_repository_node(self, row):
        proxy = self.add_repository(
            QPointF(row.get("x", 0), row.get("y", 0)),
            node_id=row.get("id"),
        )
        if proxy is None:
            return
        node = proxy.widget()
        if row.get("width") and row.get("height"):
            node.resize(int(row["width"]), int(row["height"]))
        if row.get("folder_path"):
            node._set_folder_path(row["folder_path"], silent=True)

    def _materialize_text(self, row):
        item = self.add_text_item(QPointF(row.get("x", 0), row.get("y", 0)), node_id=row.get("id"))
        item.setPlainText(row.get("text", ""))
        if row.get("font_size"):
            item._font_size = row["font_size"]
            font = item.font()
            font.setPointSize(item._font_size)
            item.setFont(font)
        if row.get("rotation"):
            item.setRotation(row["rotation"])

    def _materialize_group_frame(self, row):
        item = self.add_group_frame(QPointF(row.get("x", 0), row.get("y", 0)), node_id=row.get("id"))
        if row.get("width") and row.get("height"):
            item.prepareGeometryChange()
            item.setRect(0, 0, row["width"], row["height"])
            item._lock_btn.reposition()
        if row.get("label"):
            item._label.setPlainText(row["label"])
        if row.get("color"):
            item.color_name = row["color"]
            item._apply_style()
        if row.get("locked"):
            item.set_locked(True)

    def _materialize_image_card(self, row):
        item = self.add_image_card(
            row.get("image_path", ""),
            QPointF(row.get("x", 0), row.get("y", 0)),
            node_id=row.get("node_id"),
            width=row.get("width"),
            height=row.get("height"),
        )
        if item and row.get("hidden", False):
            item._hidden = True
            logger.info(f"[IMAGE_CARD] Restored hidden state: node_id={item.node_id} path={row.get('image_path', '')}")
        if item and row.get("vision_results"):
            item._vision_results = row["vision_results"]

    def _materialize_dimension(self, row):
        item = DimensionItem.from_data(row)
        node_id = self._next_id(row.get("node_id"))
        item.node_id = node_id
        item.on_double_click = self._open_dimension_board
        self.scene.addItem(item)
        self.dimension_items[node_id] = item
        self.app.nodes[node_id] = item
        self._attach_item_ports(item, PortItem.TYPE_STRING, PortItem.TYPE_STRING)

    def _materialize_nixi_node(self, row):
        proxy = self.add_nixi(QPointF(row.get("x", 0), row.get("y", 0)), node_id=row.get("node_id"))
        if proxy is None:
            return
        node = proxy.widget()
        if row.get("width") and row.get("height"):
            node.resize(int(row["width"]), int(row["height"]))
        value = row.get("current_value", "")
        if value:
            node._update_display(value)

    def _materialize_ups_node(self, row):
        proxy = self.add_ups(QPointF(row.get("x", 0), row.get("y", 0)), node_id=row.get("node_id"))
        if proxy is None:
            return
        node = proxy.widget()
        if row.get("width") and row.get("height"):
            node.resize(int(row["width"]), int(row["height"]))
        scale = row.get("scale", 2)
        node._scale = scale
        idx = node.scale_combo.findData(scale)
        if idx >= 0:
            node.scale_combo.setCurrentIndex(idx)
        node._last_result_path = row.get("last_result_path")
        node.ai_response = node._last_result_path
        if node._last_result_path:
            node.status_label.setText("Done")
            node.status_label.setStyleSheet("color: #27ae60; font-size: 10px; border: none; background: transparent;")
            node._update_preview()

    def _materialize_rmv_node(self, row):
        proxy = self.add_rmv(QPointF(row.get("x", 0), row.get("y", 0)), node_id=row.get("node_id"))
        if proxy is None:
            return
        node = proxy.widget()
        if row.get("width") and row.get("height"):
            node.resize(int(row["width"]), int(row["height"]))
        node._last_result_path = row.get("last_result_path")
        node.ai_response = node._last_result_path
        if node._last_result_path:
            node.status_label.setText("Done")
            node._update_preview(node._last_result_path)

    def _materialize_visible_items(self):
        if not self._lazy_mgr.has_pending():
            self._lazy_mgr._active = False
            return

        visible = self._lazy_mgr.query_visible(self._get_scene_viewport_rect())
        if not visible:
            return

        if self._batch_loading:
            queued_ids = {nid for _, nid, _ in self._batch_queue}
            visible = [(c, nid, r) for c, nid, r in visible if nid not in queued_ids]
            if not visible:
                return
            self._batch_queue.extend(visible)
        else:
            self._batch_queue = list(visible)
            self._batch_loading = True
            self._process_batch()

    def _force_materialize_by_id(self, node_id: int) -> bool:
        result = self._lazy_mgr.get_pending_item_by_id(node_id)
        if result is None:
            return False
        category, row = result
        self._materialize_single(category, node_id, row)
        self._lazy_mgr.mark_materialized(node_id, category)

        owner = self._owner_by_id(node_id)
        if owner:
            widget = owner.widget() if isinstance(owner, QGraphicsProxyWidget) else owner
            if hasattr(widget, 'reposition_ports'):
                widget.reposition_ports()
            self._invalidate_node_port_caches(widget)

        resolvable = self._lazy_mgr.get_resolvable_edges()
        for edge_data in resolvable:
            self._restore_edge(edge_data)
        if resolvable:
            self._manual_update_all_edges()

        return True

    def _resolve_port(self, owner, name: str, output: bool):
        if owner is None:
            return None
        if isinstance(owner, QGraphicsProxyWidget):
            owner = owner.widget()

        if output:
            if hasattr(owner, "output_ports") and isinstance(owner.output_ports, dict):
                if name in owner.output_ports:
                    return owner.output_ports[name]
            port = getattr(owner, "output_port", None)
            if port is not None:
                if name == "_default" or (hasattr(port, "port_name") and port.port_name == name):
                    return port
            port = getattr(owner, "signal_output_port", None)
            if port is not None and hasattr(port, "port_name") and port.port_name == name:
                return port
            meta_ports = getattr(owner, "meta_output_ports", None)
            if isinstance(meta_ports, dict) and name in meta_ports:
                return meta_ports[name]
            return None

        if hasattr(owner, "input_ports") and isinstance(owner.input_ports, dict):
            if name in owner.input_ports:
                return owner.input_ports[name]
        port = getattr(owner, "input_port", None)
        if port is not None:
            if name == "_default" or (hasattr(port, "port_name") and port.port_name == name):
                return port
        port = getattr(owner, "signal_input_port", None)
        if port is not None and hasattr(port, "port_name") and port.port_name == name:
            return port
        port = getattr(owner, "signal_input_port_b", None)
        if port is not None and hasattr(port, "port_name") and port.port_name == name:
            return port
        if hasattr(owner, 'iter_ports'):
            for p in owner.iter_ports():
                if p.port_type == PortItem.INPUT and getattr(p, 'port_name', None) == name:
                    return p
        return None

    def _owner_by_id(self, node_id: int):
        for d in (self.proxies, self.function_proxies, self.round_table_proxies,
                  self.sticky_proxies, self.prompt_proxies, self.markdown_proxies,
                  self.button_proxies, self.switch_proxies, self.latch_proxies,
                  self.and_gate_proxies, self.or_gate_proxies, self.not_gate_proxies,
                  self.xor_gate_proxies, self.checklist_proxies, self.repository_proxies,
                  self.image_card_items, self.dimension_items, self.text_items,
                  self.group_frame_items, self.nixi_proxies, self.ups_proxies,
                  self.rmv_proxies):
            if node_id in d:
                return d[node_id]
        return None

    def _restore_edge(self, row):
        s_id = row.get("source_node_id", row.get("start_node_id"))
        t_id = row.get("target_node_id", row.get("end_node_id"))
        s_name = row.get("source_port_name", row.get("start_key", "_default"))
        t_name = row.get("target_port_name", row.get("end_key", "_default"))
        if s_id is None or t_id is None:
            return

        src = self._owner_by_id(s_id)
        dst = self._owner_by_id(t_id)
        src_port = self._resolve_port(src, s_name, output=True)
        dst_port = self._resolve_port(dst, t_name, output=False)

        if src_port is None:
            logger.warning(f"[EDGE FAIL] Cannot resolve source port: node={s_id}, name={s_name}")
        if dst_port is None:
            logger.warning(f"[EDGE FAIL] Cannot resolve target port: node={t_id}, name={t_name}")

        if src_port is not None and dst_port is not None:
            self.create_edge(src_port, dst_port)
        else:
            logger.error(f"[EDGE FAILED] Could not create edge: src_port={src_port}, dst_port={dst_port}")

    def _invalidate_all_port_caches(self):
        invalidated_count = 0

        for d in (self.proxies, self.function_proxies, self.round_table_proxies,
                  self.sticky_proxies, self.prompt_proxies, self.markdown_proxies,
                  self.button_proxies, self.switch_proxies, self.latch_proxies,
                  self.and_gate_proxies, self.or_gate_proxies, self.not_gate_proxies,
                  self.xor_gate_proxies, self.checklist_proxies, self.repository_proxies,
                  self.nixi_proxies, self.ups_proxies, self.rmv_proxies):
            for proxy in d.values():
                node = proxy.widget()
                if node:
                    invalidated_count += self._invalidate_node_port_caches(node)

        logger.info(f"[CACHE INVALIDATE] Invalidated {invalidated_count} port caches during board load")

    def _invalidate_node_port_caches(self, node) -> int:
        count = 0
        if hasattr(node, 'iter_ports'):
            try:
                for port in node.iter_ports():
                    if hasattr(port, '_invalidate_cache'):
                        port._invalidate_cache()
                        count += 1
            except Exception:
                pass
        return count

    def _reposition_all_ports(self):
        reposition_count = 0

        for d in (self.proxies, self.function_proxies, self.round_table_proxies,
                  self.sticky_proxies, self.prompt_proxies, self.markdown_proxies,
                  self.button_proxies, self.switch_proxies, self.latch_proxies,
                  self.and_gate_proxies, self.or_gate_proxies, self.not_gate_proxies,
                  self.xor_gate_proxies, self.checklist_proxies, self.repository_proxies,
                  self.nixi_proxies, self.ups_proxies, self.rmv_proxies):
            for proxy in d.values():
                node = proxy.widget()
                if node and hasattr(node, 'reposition_ports'):
                    try:
                        node.reposition_ports()
                        reposition_count += 1
                    except Exception:
                        pass

        for item in self.image_card_items.values():
            if hasattr(item, '_reposition_own_ports'):
                try:
                    item._reposition_own_ports()
                    reposition_count += 1
                except Exception:
                    pass

        for item in self.dimension_items.values():
            if hasattr(item, '_reposition_own_ports'):
                try:
                    item._reposition_own_ports()
                    reposition_count += 1
                except Exception:
                    pass

        logger.info(f"[REPOSITION] Repositioned ports for {reposition_count} nodes")
