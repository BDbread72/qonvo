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
        # 주기적 자동 동기화 — 이벤트가 안 잡힌 변경도 주기마다 자동 감지해 전송(견고)
        from PyQt6.QtCore import QTimer
        if getattr(self, '_periodic_sync_timer', None) is None:
            self._periodic_sync_timer = QTimer(self.view if self.view is not None else None)
            self._periodic_sync_timer.setInterval(700)
            self._periodic_sync_timer.timeout.connect(self._periodic_prop_sync)
        self._periodic_sync_timer.start()

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
        if getattr(self, '_periodic_sync_timer', None) is not None:
            self._periodic_sync_timer.stop()
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

    def ensure_cursor_layer(self):
        """커서 레이어 보장(솔로 포함) — 내 말풍선/명령 출력 표시용."""
        if getattr(self, '_cursor_layer', None) is None:
            try:
                from .cursor_layer import CursorLayer
                self._cursor_layer = CursorLayer(self.view)
                if self.view is not None:
                    self.view._cursor_layer = self._cursor_layer
            except Exception:
                return None
        return self._cursor_layer

    def show_self_bubble(self, text: str, color: str = "#7fd88f"):
        """내 커서 위에 말풍선 표시(명령 출력/솔로 채팅). 자동 페이드."""
        cl = self.ensure_cursor_layer()
        if cl is not None and text:
            cl.add_bubble("나", color, text, is_self=True)

    def _log_chat(self, user: str, text: str, color: str):
        """채팅 한 줄을 뷰의 히스토리 로그에 기록(말풍선과 별개)."""
        v = getattr(self, 'view', None)
        if v is not None and hasattr(v, 'add_chat_log'):
            try:
                v.add_chat_log(user, text, color)
            except Exception:
                pass

    def send_chat_message(self, text: str):
        """뷰의 채팅 입력 → 서버 전송(에코로 말풍선+로그), 솔로면 직접 말풍선+로그."""
        if self._server_client and self._server_client.is_connected:
            self._server_client.send_chat(text)   # 에코가 _on_chat_bubble 로 돌아옴
        else:
            self.show_self_bubble(text, "#7fd1ff")
            self._log_chat("나", text, "#7fd1ff")

    def _on_chat_bubble(self, msg: dict):
        """채팅 수신 → 해당 사용자 커서 위 말풍선 + 히스토리 로그."""
        me = self._server_client.username if self._server_client else ""
        user = msg.get("user", "")
        color = msg.get("color", "#888")
        text = msg.get("text", "")
        if getattr(self, '_cursor_layer', None) is not None:
            self._cursor_layer.add_bubble(user, color, text, is_self=(user == me))
        self._log_chat(user, text, color)

    def _on_presence_cursors(self, users: list):
        # 체크리스트 담당자 메뉴 등에서 쓰도록 최신 접속자 목록 캐시
        try:
            self._last_presence = list(users or [])
        except Exception:
            self._last_presence = []
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
        from .checklist import ChecklistWidget
        node_id = int(node_id_str) if node_id_str.isdigit() else None
        if node_id is None:
            return
        node = self.app.nodes.get(node_id)
        # 체크리스트 AI(분해/정리) 결과 분기
        pending = getattr(self, '_checklist_ai_pending', None)
        if node and isinstance(node, ChecklistWidget) and pending is not None \
                and node_id_str in pending:
            replace = pending.pop(node_id_str, False)
            err = result.get("error")
            if err and not result.get("text"):
                node.set_ai_busy(False)
            else:
                self._apply_checklist_ai(node, result.get("text", ""), replace)
            return
        if node and isinstance(node, ChatNodeWidget):
            # preferred(N개 후보) 결과
            candidates = result.get("candidates")
            if candidates is not None:
                self._show_server_preferred(node, candidates)
                return
            text = result.get("text", "")
            images = result.get("images", [])
            err = result.get("error")
            # 결과가 비어있고 에러가 있으면 빈 '완료' 대신 에러를 표시(원인 보이게).
            if err and not text and not images:
                node.set_response(f"⚠️ 오류: {err}", done=True)
                return
            tokens_in = result.get("tokens_in", 0)
            tokens_out = result.get("tokens_out", 0)
            if tokens_in or tokens_out:
                node.set_tokens(tokens_in, tokens_out)
            if images:
                node.set_image_response(text, images)
            else:
                node.set_response(text, done=True)
            self._emit_complete_signal(node)

    def _prepare_server_input_files(self, files):
        """입력 이미지(로컬 경로)를 서버 첨부로 업로드하고 'attachments/<name>' 참조로 변환.

        서버는 클라 로컬 경로를 못 읽으므로, 각 파일을 서버에 PUT 업로드한 뒤
        서버가 board attachments 에서 해석할 수 있는 상대참조를 보낸다.
        """
        import os
        from .items import ImageCardItem
        refs = []
        client = self._server_client
        board_id = self._board_name
        for f in files or []:
            if not isinstance(f, str) or not f:
                continue
            local = f if os.path.exists(f) else None
            if local is None and ImageCardItem._board_temp_dir:
                for sub in ("attachments", ""):
                    cand = (os.path.join(ImageCardItem._board_temp_dir, sub, os.path.basename(f))
                            if sub else os.path.join(ImageCardItem._board_temp_dir, os.path.basename(f)))
                    if os.path.exists(cand):
                        local = cand
                        break
            if local is None:
                # 이미 상대참조(attachments/...)거나 못 찾음 → 그대로 보냄(서버가 해석/무시)
                refs.append(f)
                continue
            name = os.path.basename(local)
            try:
                if client is not None:
                    client.upload_attachment(board_id, name, local)
            except Exception:
                pass
            refs.append(f"attachments/{name}")
        return refs

    def _show_server_preferred(self, node, candidates):
        """서버 preferred 후보(candidates: [{text,images(base64),error}])를 노드의
        preferred 결과 UI 로 표시한다. base64 이미지는 temp 에 저장해 경로로 넘긴다."""
        import os, uuid as _uuid
        from .chat_node import ChatNodeWidget
        temp_dir = ChatNodeWidget._board_temp_dir or __import__('tempfile').gettempdir()
        results = []
        for c in candidates or []:
            text = c.get("text", "")
            if not text and c.get("error"):
                text = f"⚠️ {c['error']}"
            img_paths = []
            for img_data in c.get("images", []) or []:
                try:
                    raw = node._decode_image_data(img_data)
                except Exception:
                    raw = None
                if not raw:
                    continue
                p = os.path.join(temp_dir, f"{_uuid.uuid4().hex}.png")
                try:
                    with open(p, "wb") as fh:
                        fh.write(raw)
                    img_paths.append(p)
                except Exception:
                    continue
            results.append((text, img_paths))
        node._on_preferred_selected = self._on_chat_preferred_option_selected
        node.show_preferred_results(results)
        notify = getattr(node, 'notify_on_complete', False)
        if notify:
            try:
                from .toast_notification import ToastManager
                mw = self.view.window() if self.view else None
                ToastManager.instance().show_toast(
                    f"Chat #{node.node_id} - {len(results)}개 결과 준비됨", mw)
            except Exception:
                pass

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
                effective_system_prompt = f"{effective_system_prompt}\n\n{chr(10).join(system_parts)}".strip()

        # 입력 이미지 업로드 + 상대참조로 변환(서버가 읽을 수 있게).
        ref_files = self._prepare_server_input_files(files)

        # 노드 옵션(temperature 등) 병합.
        options = get_model_options(model)
        node_opts = getattr(node, 'node_options', {})
        if node_opts:
            options.update(node_opts)

        # preferred(N개 후보) 모드 지원.
        pref_enabled = getattr(node, 'preferred_options_enabled', False)
        count = getattr(node, 'preferred_options_count', 3) if pref_enabled else 1
        if pref_enabled:
            node._on_preferred_selected = self._on_chat_preferred_option_selected
            node.set_response(f"생성 중... (0/{count})", done=False)
        else:
            node.set_response("서버 처리 중...", done=False)

        # ── 진단: 서버로 나가는 실제 페이로드 덤프 (이전대화/형제노드 누수 추적용) ──
        try:
            from v.logger import get_logger as _gl
            _lg = _gl("qonvo.plugin")
            _sysp = (effective_system_prompt or "")[:200].replace("\n", " ")
            _msgp = (message or "")[:300].replace("\n", " ")
            _lg.info(f"[CHAT_PAYLOAD/server] node={node_id} sys_len={len(effective_system_prompt or '')} "
                     f"sys='{_sysp}' msg_len={len(message or '')} msg='{_msgp}'")
        except Exception:
            pass

        self._server_client.send_ai_request(
            node_id=node_id,
            model=model,
            message=message or "",
            files=ref_files,
            system_prompt=effective_system_prompt,
            options=options,
            count=count,
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
        elif op_type == "node_rename":
            try:
                # 부수효과 없이 적용(서버로 재전송 안 함 → 에코 방지).
                self._set_node_name(int(target), data.get("name", ""))
            except Exception:
                pass

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
            "texts": self.add_text_item,
            "group_frames": self.add_group_frame,
            "image_cards": self.add_image_card,
            "file_nodes": self.add_file_node,
        }
        add_fn = add_map.get(category)
        if add_fn:
            add_fn(pos=pos, node_id=node_id)
        # 원격이 새 이미지/파일 노드를 추가 → 참조 첨부를 백그라운드로 받아온다.
        if category in ("image_cards", "file_nodes"):
            self._schedule_attach_resync()

    def _remote_remove_node(self, target: str):
        node_id = int(target) if target.isdigit() else None
        if node_id is None:
            return
        for d in (self.proxies, self.function_proxies, self.round_table_proxies,
                  self.sticky_proxies, self.prompt_proxies, self.markdown_proxies, self.button_proxies, self.switch_proxies, self.latch_proxies, self.and_gate_proxies, self.or_gate_proxies, self.not_gate_proxies, self.xor_gate_proxies, self.bulb_proxies,
                  self.checklist_proxies, self.repository_proxies,
                  self.nixi_proxies):
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
                  self.nixi_proxies):
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
        # 원격이 이미지 카드 데이터를 보냄(드래그 추가/교체) → 새 첨부 증분 다운로드.
        if node_id in getattr(self, 'image_card_items', {}) or node_id in getattr(self, 'file_node_items', {}):
            self._schedule_attach_resync()
        # 신규: 전체 노드 데이터를 제자리 적용(텍스트/제목/색상 등 위젯 갱신).
        # apply_sync_data 가 있으면 제자리 갱신(부드러움), 없으면 데이터로 재생성(범용).
        full = data.get("data")
        if isinstance(full, dict):
            if hasattr(node, "apply_sync_data"):
                try:
                    node.apply_sync_data(full)
                except Exception:
                    pass
                # 채팅 노드 추가 입력 포트(extra_input_defs) 라이브 복원(없는 것만 추가)
                # → restore_state 는 포트를 안 만들므로 플러그인 레벨에서 보강.
                try:
                    from .chat_node import ChatNodeWidget
                    if isinstance(node, ChatNodeWidget) and "extra_input_defs" in full:
                        existing = set(node.input_ports.keys())
                        for d in (full.get("extra_input_defs") or []):
                            nm = d.get("name")
                            if nm and nm not in existing:
                                self._add_chat_input_port(node, d.get("type", "text"), nm)
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
                   "file_nodes": self.file_node_items,
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

    def _upload_and_sync_image(self, card):
        """서버모드에서 새로 넣은 이미지카드를 서버에 영속한다.

        이미지카드는 자동 prop 동기화에서 제외돼 있어, 드래그/붙여넣기로 넣은
        이미지는 (1) 첨부 파일 업로드 (2) 상대경로(attachments/<name>) 기록이
        둘 다 안 돼서 저장이 안 됐다. 여기서 둘 다 처리한다:
          - node_prop 으로 image_path=attachments/<name> + preview_b64 를 즉시
            전송 → 서버 doc 머지(영속) + 타 멤버 미리보기 표시.
          - 파일 PUT 은 백그라운드 스레드(큰 이미지로 UI 안 멈춤).
        """
        if not getattr(self, 'server_mode', False) or self._applying_remote_op:
            return
        client = getattr(self, '_server_client', None)
        if client is None or not client.is_connected:
            return
        import os
        path = getattr(card, 'image_path', None)
        if not path or not os.path.exists(path):
            return
        name = os.path.basename(path)
        board_id = self._board_name
        if not board_id:
            return
        # (1) doc + 타 멤버에 즉시 반영(상대경로). 이미지카드는 _send_op 직접 호출.
        try:
            data = card.get_data()
            data["image_path"] = f"attachments/{name}"
            self._send_op("node_prop", card.node_id, {"data": data})
        except Exception:
            pass
        # (2) 파일 업로드는 백그라운드(동기 PUT 을 UI 스레드 밖에서)
        import threading

        def _do_upload(b=board_id, n=name, p=path):
            try:
                client.upload_attachment(b, n, p)
            except Exception:
                pass
        threading.Thread(target=_do_upload, daemon=True).start()

    def _schedule_attach_resync(self):
        """원격 op 가 새 이미지 첨부를 참조 → 백그라운드 증분 다운로드(디바운스)."""
        if not getattr(self, 'server_mode', False):
            return
        from PyQt6.QtCore import QTimer
        if getattr(self, '_attach_resync_timer', None) is None:
            self._attach_resync_timer = QTimer(self.view if self.view is not None else None)
            self._attach_resync_timer.setSingleShot(True)
            self._attach_resync_timer.setInterval(500)
            self._attach_resync_timer.timeout.connect(
                lambda: self._start_attachment_sync(self._board_name))
        if not self._attach_resync_timer.isActive():
            self._attach_resync_timer.start()

    def _send_node_rename_op(self, node_id: int, name: str):
        """노드 이름 변경을 다른 멤버에게 전파(plugin.rename_node 에서 호출)."""
        self._send_op("node_rename", node_id, {"name": name})

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
