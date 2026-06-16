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
        'BulbNodeWidget': 'bulb_nodes',
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
        client.presence_received.connect(self._on_presence_cursors)
        client.chat_received.connect(self._on_chat_bubble)
        # 라이브 커서 레이어 (뷰 drawForeground 에서 그림)
        from .cursor_layer import CursorLayer
        self._cursor_layer = CursorLayer(self.view)
        if self.view is not None:
            self.view._cursor_layer = self._cursor_layer

    def detach_server_client(self):
        if self._server_client:
            try:
                self._server_client.sync_received.disconnect(self._on_server_sync)
                self._server_client.remote_ops.disconnect(self._on_remote_ops)
                self._server_client.ai_progress.disconnect(self._on_ai_progress)
                self._server_client.ai_complete.disconnect(self._on_ai_complete)
                self._server_client.presence_received.disconnect(self._on_presence_cursors)
                self._server_client.chat_received.disconnect(self._on_chat_bubble)
            except Exception:
                pass
        if getattr(self, '_cursor_layer', None) is not None:
            self._cursor_layer.clear()
            self._cursor_layer.deleteLater()  # 오버레이 위젯 정리
            self._cursor_layer = None
        if getattr(self, 'view', None) is not None:
            self.view._cursor_layer = None
        self._server_client = None

    def report_cursor(self, scene_x: float, scene_y: float, state: str = ""):
        """뷰의 마우스 이동을 서버에 커서 위치+상태로 보고한다(서버모드일 때).

        state: ""(보통) / "menu"(방사형메뉴) / "typing"(입력중) / "away"(앱 비활성).
        """
        if self._server_client and self._server_client.is_connected:
            self._server_client.update_cursor(scene_x, scene_y, state)
        if getattr(self, '_cursor_layer', None) is not None:
            self._cursor_layer.set_self(scene_x, scene_y)  # 내 말풍선 위치용

    def report_selection(self, x=None, y=None, w=None, h=None):
        """영역 선택 사각형을 서버에 보고(None 이면 해제)."""
        if not (self._server_client and self._server_client.is_connected):
            return
        sel = None if x is None else {"x": round(x, 1), "y": round(y, 1),
                                      "w": round(w, 1), "h": round(h, 1)}
        self._server_client.update_selection(sel)

    def send_chat_message(self, text: str):
        """뷰의 채팅 입력 → 서버로 전송."""
        if self._server_client and self._server_client.is_connected:
            self._server_client.send_chat(text)

    def _on_chat_bubble(self, msg: dict):
        """채팅 수신 → 해당 사용자 커서 위 말풍선."""
        if getattr(self, '_cursor_layer', None) is None:
            return
        me = self._server_client.username if self._server_client else ""
        user = msg.get("user", "")
        self._cursor_layer.add_bubble(user, msg.get("color", "#888"),
                                      msg.get("text", ""), is_self=(user == me))

    def _on_presence_cursors(self, users: list):
        if getattr(self, '_cursor_layer', None) is None:
            return
        me = self._server_client.username if self._server_client else ""
        try:
            self._cursor_layer.update_from_presence(users, exclude_user=me)
        except Exception:
            pass

    def _send_op(self, op_type: str, target, data: dict | None = None):
        if self._applying_remote_op:
            return
        if self._server_client and self._server_client.is_connected:
            self._server_client.send_op(op_type, str(target), data)

    def _on_server_sync(self, snapshot: dict):
        # 서버모드: board_name 을 board_id 로 맞춘 뒤 **즉시 복원**(구조/텍스트 바로 표시).
        # 첨부(이미지)는 백그라운드로 내려받아 끝나면 이미지 노드를 새로고침한다.
        board_id = self._server_client.board_id if self._server_client else ""
        if board_id:
            self._board_name = board_id

        # 스냅샷 prime 캐시는 비활성(일부 노드 비는 문제) — 항상 서버 full sync 로 복원.
        # 첨부(이미지)만 백그라운드로 증분 다운로드한다.
        self._applying_remote_op = True
        try:
            self.restore_data(snapshot)
        finally:
            self._applying_remote_op = False

        self._start_attachment_sync(board_id)

    def _start_attachment_sync(self, board_id):
        """서버 첨부를 백그라운드로 증분 다운로드(이미 받은 건 건너뜀)."""
        client = self._server_client
        dest = self._server_attachments_dir(board_id) if board_id else None
        if dest and client and client.http_base and client.http_token:
            try:
                th = client.start_attachment_download(board_id, dest)
                self._attach_dl_thread = th  # GC 방지
                th.progress.connect(self._on_attachment_progress)
                th.finished_dl.connect(self._on_attachments_ready)
                self._notify_server_status("보드 첨부 동기화 중...")
                th.start()
            except Exception:
                pass

    # ---- 스냅샷 캐시 (재접속 가속) --------------------------------------
    def _server_cache_dir(self):
        from v.board import BoardManager
        d = BoardManager.get_boards_dir().parent / 'server_cache'
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _server_cache_path(self, board_id):
        import re
        base = (self._server_client.http_base if self._server_client else '') or ''
        key = re.sub(r'[^A-Za-z0-9._-]', '_', f"{base}_{board_id}")[:120]
        return self._server_cache_dir() / (key + '.json')

    def _save_server_cache(self, board_id, seq, doc):
        import json, os
        try:
            p = self._server_cache_path(board_id)
            tmp = p.with_suffix('.json.tmp')
            tmp.write_text(json.dumps({'seq': int(seq), 'doc': doc}, ensure_ascii=False),
                           encoding='utf-8')
            os.replace(tmp, p)
        except Exception:
            pass

    def prime_from_cache(self, board_id) -> int:
        """캐시된 스냅샷이 있으면 즉시 복원하고 그 seq 를 반환(없으면 0).

        join 전에 호출 → 보드가 즉시 뜨고, join 은 이 seq 로 delta 만 받는다.
        """
        import json
        try:
            p = self._server_cache_path(board_id)
            if not p.exists():
                return 0
            data = json.loads(p.read_text(encoding='utf-8'))
            self._board_name = board_id
            self._applying_remote_op = True
            try:
                self.restore_data(data.get('doc', {}))
            finally:
                self._applying_remote_op = False
            self._notify_server_status("캐시에서 불러옴 — 최신 변경 동기화 중…")
            self._start_attachment_sync(board_id)  # 증분(이미 받은 첨부는 스킵)
            return int(data.get('seq', 0))
        except Exception:
            return 0

    def _server_attachments_dir(self, board_id):
        if not board_id:
            return None
        from v.board import BoardManager
        return str(BoardManager.get_boards_dir() / '.temp' / board_id / 'attachments')

    def _server_window(self):
        try:
            return self.view.window() if getattr(self, 'view', None) else None
        except Exception:
            return None

    def _on_attachment_progress(self, done, total):
        if total:
            pct = int(done * 100 / total)
            self._notify_server_status(f"보드 첨부 동기화 {done}/{total} ({pct}%)")
            win = self._server_window()
            if win is not None and hasattr(win, 'set_server_loading_progress'):
                win.set_server_loading_progress(done, total)
            # 주기적으로 이미지 새로고침(받는 중에도 점점 채워지게). 너무 잦으면
            # chat 재렌더 churn 이 크므로 드물게.
            if done % 200 == 0:
                self._refresh_image_nodes()

    def _on_attachments_ready(self, ok, total):
        if total:
            self._notify_server_status(f"첨부 {ok}/{total} 동기화 완료")
        self._refresh_image_nodes()
        win = self._server_window()
        if win is not None and hasattr(win, 'set_server_loaded'):
            win.set_server_loaded()

    def _refresh_image_nodes(self):
        """첨부 다운로드 후 이미 생성된 이미지 노드를 다시 그린다."""
        from .chat_node import ChatNodeWidget
        # 채팅 노드: 히스토리 이미지 재렌더
        app = getattr(self, 'app', None)
        if app is not None:
            for node in list(getattr(app, 'nodes', {}).values()):
                try:
                    if (isinstance(node, ChatNodeWidget) and hasattr(node, '_render_page')
                            and hasattr(node, '_current_page')):
                        node._render_page(node._current_page)
                except Exception:
                    pass
        # 이미지 카드: 아직 못 불러온 것만 로드 시도.
        # ⚠️ 로딩 중인 항목에 _start_load 를 다시 호출하면 _load_signal(QObject)이
        #    교체→옛 객체 GC→워커 스레드가 죽은 객체에 emit → 세그폴트. 절대 금지.
        for item in list(getattr(self, 'image_card_items', {}).values()):
            try:
                if not hasattr(item, '_start_load'):
                    continue
                if getattr(item, '_loading', False):
                    continue  # 로딩 중 → 건드리지 않음
                if getattr(item, '_pixmap', None) is None:  # 미로드/실패한 것만 재시도
                    item._load_failed = False
                    item._start_load()
            except Exception:
                pass
        try:
            if getattr(self, 'view', None):
                self.view.viewport().update()
        except Exception:
            pass

    def _notify_server_status(self, text: str):
        try:
            win = self.view.window() if getattr(self, 'view', None) else None
            if win is not None:
                win.statusBar().showMessage(text, 0)
        except Exception:
            pass

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
            "bulb_nodes": self.add_bulb,
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
                  self.sticky_proxies, self.prompt_proxies, self.markdown_proxies, self.button_proxies, self.switch_proxies, self.latch_proxies, self.and_gate_proxies, self.or_gate_proxies, self.not_gate_proxies, self.xor_gate_proxies, self.bulb_proxies,
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
                  self.sticky_proxies, self.prompt_proxies, self.markdown_proxies, self.button_proxies, self.switch_proxies, self.latch_proxies, self.and_gate_proxies, self.or_gate_proxies, self.not_gate_proxies, self.xor_gate_proxies, self.bulb_proxies,
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
        # 신규: 전체 노드 데이터를 제자리 적용(텍스트/제목/색상 등 위젯 갱신).
        # apply_sync_data 가 있으면 제자리 갱신(부드러움), 없으면 데이터로 재생성(범용).
        full = data.get("data")
        if isinstance(full, dict):
            if hasattr(node, "apply_sync_data"):
                try:
                    node.apply_sync_data(full)
                except Exception:
                    pass
            else:
                self._recreate_node_from_data(node_id, full)
            return
        # 레거시: 단일 key/value
        key = data.get("key", "")
        value = data.get("value")
        if key and hasattr(node, key):
            try:
                setattr(node, key, value)
            except Exception:
                pass

    def _recreate_node_from_data(self, node_id, data: dict):
        """범용 폴백: apply_sync_data 가 없는 노드를 데이터로 제자리 재생성 + 엣지 재연결.

        원격 op 적용 중(_applying_remote_op=True)이라 내부 op 전송은 _send_op 가 차단한다.
        """
        from PyQt6.QtWidgets import QGraphicsProxyWidget
        owner = self._owner_by_id(node_id)
        if owner is None:
            return
        target_widget = owner.widget() if isinstance(owner, QGraphicsProxyWidget) else owner
        category = self._node_category(target_widget)
        # 이 노드에 연결된 엣지 메타 저장(포트 이름 기준)
        saved_edges = []
        for edge in list(self._edges):
            try:
                s = self._owner_node_id(edge.source_port.parent_proxy)
                t = self._owner_node_id(edge.target_port.parent_proxy)
            except Exception:
                continue
            if s == node_id or t == node_id:
                saved_edges.append({
                    "source_node_id": s, "target_node_id": t,
                    "source_port_name": edge.source_port.port_name,
                    "target_port_name": edge.target_port.port_name,
                })
        # 옛 노드 삭제
        if isinstance(owner, QGraphicsProxyWidget):
            self.delete_proxy_item(owner)
        else:
            reg = {"texts": self.text_items, "group_frames": self.group_frame_items,
                   "image_cards": self.image_card_items,
                   "dimensions": self.dimension_items}.get(category)
            if reg is None:
                return
            self._delete_scene_item(owner, reg)
        # 데이터로 재생성
        try:
            self._materialize_single(category, int(node_id), data)
        except Exception:
            return
        # 엣지 재연결
        for e in saved_edges:
            try:
                self._restore_edge(e)
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

    def _send_node_move_throttled(self, node_id: int, x: float, y: float):
        """드래그 중 위치를 throttle(약 25/s)로 전송 → 상대가 점프 없이 부드럽게 본다."""
        if not self.server_mode or self._applying_remote_op:
            return
        import time
        if not hasattr(self, '_move_throttle'):
            self._move_throttle = {}
        now = time.monotonic()
        if now - self._move_throttle.get(node_id, 0.0) >= 0.04:
            self._move_throttle[node_id] = now
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
