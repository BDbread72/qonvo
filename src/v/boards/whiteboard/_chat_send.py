from __future__ import annotations

import os
from typing import TYPE_CHECKING

from v.provider import GeminiProvider, ChatMessage
from v.settings import get_api_keys, get_model_options
from v.logger import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger("qonvo.plugin")


class ChatSendMixin:
    """plugin.py에서 분리된 채팅 전송 mixin.

    self._provider로 AI 모델에 접근하고, self._preferred_results/self._rework_params로
    preferred options 상태를 추적한다. 워커 관리는 self._start_worker()에 위임한다.
    """

    def _ensure_provider(self):
        if self._provider is not None:
            return self._provider

        from v.model_plugin import ProviderRouter

        keys = get_api_keys()
        gemini = GeminiProvider(api_keys=keys) if keys else None

        self._provider = ProviderRouter(gemini_provider=gemini)
        return self._provider

    def _invalidate_provider(self):
        self._provider = None

    def _handle_chat_send(self, node_id, model, message, files, prompt_entries=None):
        logger.info(f"[CHAT_SEND] node={node_id}, model={model}, msg_len={len(message or '')}, files={len(files or [])}, prompts={len(prompt_entries or [])}, active_workers={self._active_workers}")
        node = self.app.nodes.get(node_id)
        if node is None:
            return

        if self.server_mode:
            self._handle_chat_send_server(node_id, node, model, message, files, prompt_entries)
            return

        provider = self._ensure_provider()
        if not model:
            node.set_response("No model selected", done=True)
            return

        options = get_model_options(model)
        node_opts = getattr(node, 'node_options', {})
        if node_opts:
            options.update(node_opts)

        effective_system_prompt = self.system_prompt
        prefix_messages = []

        if prompt_entries:
            sorted_entries = sorted(prompt_entries, key=lambda e: e.get("priority", 0))
            system_parts = []
            for entry in sorted_entries:
                role = entry.get("role", "system")
                text = entry.get("text", "")
                if not text:
                    continue
                if role == "system":
                    system_parts.append(text)
                else:
                    prefix_messages.append(ChatMessage(role=role, content=text))

            if system_parts:
                node_prompt = "\n\n".join(system_parts)
                effective_system_prompt = f"{effective_system_prompt}\n\n{node_prompt}".strip()
            logger.info(f"[PROMPT_NODE] node={node_id}, entries={len(sorted_entries)}, system={len(system_parts)}, prefix_msgs={len(prefix_messages)}")

        pref_enabled = getattr(node, 'preferred_options_enabled', False)
        pref_count = getattr(node, 'preferred_options_count', 3) if pref_enabled else 1

        if pref_enabled:
            from .items import ImageCardItem
            from .widgets import StreamWorker

            dim_images = self._extract_dimension_images(node)
            non_dim_files = list(files or [])

            if dim_images:
                actual_count = len(dim_images)
                files_per_index = [non_dim_files + [img] for img in dim_images]
            else:
                actual_count = pref_count
                files_per_index = [list(files or []) for _ in range(actual_count)]

            self._preferred_results[node_id] = []
            self._preferred_expected[node_id] = actual_count
            node._on_preferred_selected = self._on_chat_preferred_option_selected
            node._on_rework = self._on_chat_rework_requested
            input_imgs = [f for f in (files or []) if isinstance(f, str) and os.path.exists(f)]
            if dim_images:
                seen = set(input_imgs)
                for di in dim_images:
                    if di not in seen:
                        input_imgs.append(di)
                        seen.add(di)
            node._pref_input_images = input_imgs
            node.set_response(f"생성 중... (0/{actual_count})", done=False)

            self._rework_params[node_id] = {
                'model': model,
                'message': message,
                'system_prompt': effective_system_prompt,
                'system_files': list(self.system_files),
                'options': dict(options),
                'prefix_messages': list(prefix_messages),
                'files_per_index': files_per_index,
            }

            for i in range(actual_count):
                wk_files = files_per_index[i]
                messages = list(prefix_messages) + [ChatMessage(role="user", content=message or "", attachments=wk_files or None)]
                worker = StreamWorker(
                    provider,
                    model,
                    messages,
                    system_prompt=effective_system_prompt,
                    system_files=self.system_files,
                    **options,
                )
                worker._node_id = node_id

                worker.tokens_received.connect(lambda i, o, n=node: n.set_tokens(i, o))
                worker.error_signal.connect(
                    lambda err, n=node, w=worker: self._finish_worker(
                        w, lambda: self._on_chat_pref_error(n, err)
                    )
                )
                worker.finished_signal.connect(
                    lambda text, n=node, w=worker: self._finish_worker(
                        w, lambda: self._on_chat_pref_finished(n, text, [])
                    )
                )
                worker.image_received.connect(
                    lambda payload, n=node, w=worker: self._finish_worker(
                        w, lambda: self._on_chat_pref_image(n, payload)
                    )
                )

                if self._active_workers < self._max_concurrent_workers:
                    self._start_worker(worker)
                else:
                    self._pending_workers.append((node, worker))
        else:
            from .widgets import StreamWorker

            messages = list(prefix_messages) + [ChatMessage(role="user", content=message or "", attachments=files or None)]
            # ── 진단: 모델로 나가는 실제 페이로드 덤프 (이전대화/형제노드 누수 추적용) ──
            try:
                _sys_prev = (effective_system_prompt or "")[:200].replace("\n", " ")
                logger.info(f"[CHAT_PAYLOAD] node={node.node_id} sys_len={len(effective_system_prompt or '')} "
                            f"sys='{_sys_prev}' msg_count={len(messages)}")
                for _i, _m in enumerate(messages):
                    _c = (_m.content or "")[:200].replace("\n", " ")
                    logger.info(f"[CHAT_PAYLOAD] node={node.node_id} #{_i} role={_m.role} "
                                f"len={len(_m.content or '')} content='{_c}'")
            except Exception:
                pass
            worker = StreamWorker(
                provider,
                model,
                messages,
                system_prompt=effective_system_prompt,
                system_files=self.system_files,
                **options,
            )
            worker._node_id = node.node_id

            worker.chunk_received.connect(lambda text, n=node: n.set_response(text, done=False))
            worker.tokens_received.connect(lambda i, o, n=node: n.set_tokens(i, o))
            worker.signatures_received.connect(lambda sigs, n=node: setattr(n, "thought_signatures", sigs))
            worker.error_signal.connect(lambda err, n=node, w=worker: self._finish_worker(w, lambda: (n.set_response(f"Error: {err}", done=True), self._emit_complete_signal(n))))
            worker.finished_signal.connect(lambda text, n=node, w=worker: self._finish_worker(w, lambda: (n.set_response(text, done=True), self._emit_complete_signal(n))))
            worker.image_received.connect(lambda payload, n=node, w=worker: self._on_image_payload(n, w, payload))

            if self._active_workers < self._max_concurrent_workers:
                self._start_worker(worker)
            else:
                self._pending_workers.append((node, worker))

    def _on_chat_pref_finished(self, node, text, images):
        nid = node.node_id
        if nid not in self._preferred_results:
            return
        self._preferred_results[nid].append((text, images))
        expected = self._preferred_expected.get(nid, 1)
        done_count = len(self._preferred_results[nid])
        if done_count < expected:
            node.set_response(f"생성 중... ({done_count}/{expected})", done=False)
        else:
            node.show_preferred_results(self._preferred_results[nid])
            notify = getattr(node, 'notify_on_complete', False)
            if notify:
                from .toast_notification import ToastManager
                main_window = self.view.window() if self.view else None
                ToastManager.instance().show_toast(
                    f"Chat #{node.node_id} - {expected}개 결과 준비됨", main_window
                )

    def _on_chat_pref_image(self, node, payload):
        images = payload.get("images", [])
        text = payload.get("text", "")
        if not images:
            text = text if text else "이미지 생성 실패"
            self._on_chat_pref_finished(node, text, [])
            return
        import uuid, os
        from v.temp_file_manager import TempFileManager
        from .chat_node import ChatNodeWidget
        temp_manager = TempFileManager()
        saved_paths = []
        for img_data in images:
            raw_bytes = node._decode_image_data(img_data)
            if not raw_bytes:
                continue
            temp_dir = ChatNodeWidget._board_temp_dir or __import__('tempfile').gettempdir()
            temp_path = os.path.join(temp_dir, f"{uuid.uuid4().hex}.png")
            try:
                with open(temp_path, "wb") as f:
                    f.write(raw_bytes)
                temp_manager.register(temp_path)
                saved_paths.append(temp_path)
            except Exception:
                continue
        self._on_chat_pref_finished(node, text, saved_paths)

    def _on_chat_pref_error(self, node, error):
        nid = node.node_id
        if nid not in self._preferred_results:
            return
        self._preferred_results[nid].append((f"Error: {error}", []))
        expected = self._preferred_expected.get(nid, 1)
        if len(self._preferred_results[nid]) >= expected:
            node.show_preferred_results(self._preferred_results[nid])

    def _extract_dimension_images(self, node):
        from .items import PortItem, ImageCardItem
        from .dimension_item import DimensionItem

        dim_images = []
        if not hasattr(node, 'input_ports'):
            return dim_images
        for port_name, port in node.input_ports.items():
            if port.port_data_type != PortItem.TYPE_FILE:
                continue
            if not port.edges:
                continue
            source_proxy = port.edges[0].source_port.parent_proxy
            if not isinstance(source_proxy, DimensionItem):
                continue
            board_data = source_proxy.get_board_data()
            for card in board_data.get("image_cards", []):
                img_path = card.get("image_path", "")
                if not img_path:
                    continue
                if not os.path.isabs(img_path) or not os.path.exists(img_path):
                    if ImageCardItem._board_temp_dir:
                        candidate = os.path.join(
                            ImageCardItem._board_temp_dir,
                            os.path.basename(img_path),
                        )
                        if os.path.exists(candidate):
                            img_path = candidate
                if os.path.exists(img_path):
                    dim_images.append(img_path)
        return dim_images

    def _on_chat_rework_requested(self, node):
        from .widgets import StreamWorker

        nid = node.node_id
        params = self._rework_params.get(nid)
        if not params:
            return
        provider = self._ensure_provider()
        for index in range(len(params['files_per_index'])):
            wk_files = params['files_per_index'][index]
            messages = list(params['prefix_messages']) + [
                ChatMessage(
                    role="user",
                    content=params['message'] or "",
                    attachments=wk_files or None,
                )
            ]
            worker = StreamWorker(
                provider,
                params['model'],
                messages,
                system_prompt=params['system_prompt'],
                system_files=params['system_files'],
                **params['options'],
            )
            worker._node_id = nid

            worker.tokens_received.connect(lambda i, o, n=node: n.set_tokens(i, o))
            worker.error_signal.connect(
                lambda err, n=node, w=worker, idx=index: self._finish_worker(
                    w, lambda: self._on_chat_rework_done(n, idx, f"Error: {err}", [])
                )
            )
            worker.finished_signal.connect(
                lambda text, n=node, w=worker, idx=index: self._finish_worker(
                    w, lambda: self._on_chat_rework_done(n, idx, text, [])
                )
            )
            worker.image_received.connect(
                lambda payload, n=node, w=worker, idx=index: self._finish_worker(
                    w, lambda: self._on_chat_rework_image(n, idx, payload)
                )
            )

            if self._active_workers < self._max_concurrent_workers:
                self._start_worker(worker)
            else:
                self._pending_workers.append((node, worker))

    def _on_chat_rework_image(self, node, index, payload):
        images = payload.get("images", [])
        text = payload.get("text", "")
        if not images:
            text = text if text else "이미지 생성 실패"
        self._on_chat_rework_done(node, index, text, images)

    def _on_chat_rework_done(self, node, index, text, images):
        nid = node.node_id
        results = self._preferred_results.get(nid)
        if results is not None and index < len(results):
            results[index] = (text, images)
        pref_window = getattr(node, '_pref_window', None)
        if pref_window:
            pref_window.update_card(index, text, images)

    def _on_chat_preferred_option_selected(self, node, selections):
        from .items import ImageCardItem
        from .dimension_item import DimensionItem
        from .repository_node import RepositoryNodeWidget

        nid = node.node_id

        if not selections:
            self._preferred_results.pop(nid, None)
            self._preferred_expected.pop(nid, None)
            return

        first_text, first_images = selections[0]
        node.ai_response = first_text

        all_images = []
        for _, imgs in selections:
            all_images.extend(imgs)

        output_port_to_check = getattr(node, 'output_port', None)
        if output_port_to_check and hasattr(output_port_to_check, 'edges'):
            for edge in list(output_port_to_check.edges):
                target = edge.target_port
                if isinstance(target.parent_proxy, DimensionItem):
                    for text, images in selections:
                        self._add_to_dimension(target.parent_proxy, text, images)
                elif isinstance(target.parent_proxy, ImageCardItem):
                    if all_images:
                        self._update_image_card(target.parent_proxy, node, all_images)
                elif hasattr(target.parent_proxy, 'widget'):
                    _tw = target.parent_proxy.widget()
                    if isinstance(_tw, RepositoryNodeWidget):
                        self._save_to_repository(_tw, node, all_images or None)

        self._emit_complete_signal(node, all_images if all_images else None)

        notify = getattr(node, 'notify_on_complete', False)
        if notify:
            from .toast_notification import ToastManager
            main_window = self.view.window() if self.view else None
            ToastManager.instance().show_toast(
                f"Chat #{node.node_id} - {len(selections)}개 선택 완료", main_window
            )

        self._preferred_results.pop(nid, None)
        self._preferred_expected.pop(nid, None)
        self._rework_params.pop(nid, None)

    # ──────────────────────────────────────────── 체크리스트 완료 신호 / AI

    def _on_checklist_complete(self, node_id):
        """체크리스트 모든 항목 체크 → ⚡ 완료 신호 발화."""
        node = self.app.nodes.get(node_id)
        if node is None:
            return
        sig_port = getattr(node, 'signal_output_port', None)
        if sig_port is not None:
            try:
                self.emit_signal(sig_port, data=node.get_markdown())
            except Exception:
                logger.exception("[CHECKLIST] complete signal failed")

    @staticmethod
    def _parse_checklist_json(text):
        """AI 응답(JSON)에서 항목 리스트 추출. 코드펜스/잡음 허용."""
        import json
        import re
        if not text:
            return []
        s = text.strip()
        # ```json ... ``` 펜스 제거
        if s.startswith("```"):
            s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
            s = re.sub(r"\n?```$", "", s).strip()
        try:
            data = json.loads(s)
        except Exception:
            # 본문에서 첫 JSON 객체/배열만 뽑아 재시도
            m = re.search(r"(\{.*\}|\[.*\])", s, re.DOTALL)
            if not m:
                return []
            try:
                data = json.loads(m.group(1))
            except Exception:
                return []
        if isinstance(data, dict):
            items = data.get("items") or data.get("tasks") or []
        elif isinstance(data, list):
            items = data
        else:
            items = []
        return items if isinstance(items, list) else []

    def _checklist_ai_prompt(self, kind, goal, current_items):
        if kind == "cleanup":
            joined = "\n".join(f"- {it}" for it in current_items) or "(없음)"
            return (
                "다음 할 일 목록을 정리해줘: 중복 제거, 비슷한 항목 통합, 논리적 순서로 재정렬, "
                "모호한 표현은 명확하게. 항목 텍스트는 한국어로 간결하게.\n\n"
                f"현재 목록:\n{joined}\n\n"
                'JSON 으로만 답해: {"items": [{"text": "..."}]}'
            )
        return (
            f'"{goal}" 라는 목표를 달성하기 위한 구체적이고 실행 가능한 체크리스트 항목을 '
            "3~8개로 분해해줘. 각 항목은 한국어로 간결한 행동 단위.\n\n"
            'JSON 으로만 답해: {"items": [{"text": "..."}]}'
        )

    def _checklist_ai(self, node_id, kind):
        """✨ AI: 목표→항목 분해(breakdown) / 항목 정리·요약(cleanup)."""
        from PyQt6.QtWidgets import QInputDialog

        node = self.app.nodes.get(node_id)
        if node is None:
            return

        current_items = [it.get("text", "") for it in node.get_items() if it.get("text")]
        goal = node.title_edit.text().strip()
        if kind == "breakdown":
            goal, ok = QInputDialog.getText(
                self.view, "AI 분해", "목표를 입력하세요", text=goal)
            if not ok or not goal.strip():
                return
            goal = goal.strip()
        elif kind == "cleanup" and not current_items:
            return

        from v.settings import get_default_model
        model = get_default_model()
        if not model:
            node.set_ai_busy(False)
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self.view, "AI", "기본 모델이 설정되지 않았습니다.")
            return

        prompt = self._checklist_ai_prompt(kind, goal, current_items)
        replace = (kind == "cleanup")
        node.set_ai_busy(True)

        # 서버 모드: 서버가 AI 대행 → _on_ai_complete 에서 체크리스트로 분기
        if getattr(self, 'server_mode', False):
            if not hasattr(self, '_checklist_ai_pending'):
                self._checklist_ai_pending = {}
            self._checklist_ai_pending[str(node_id)] = replace
            try:
                self._server_client.send_ai_request(
                    node_id=str(node_id), model=model, message=prompt,
                    files=[], system_prompt="", options={"json_mode": True})
            except Exception:
                node.set_ai_busy(False)
                self._checklist_ai_pending.pop(str(node_id), None)
            return

        # 로컬 모드
        from .widgets import StreamWorker
        provider = self._ensure_provider()
        messages = [ChatMessage(role="user", content=prompt)]
        worker = StreamWorker(provider, model, messages, json_mode=True)
        worker._node_id = node_id

        def _done(text, n=node, w=worker, rep=replace):
            self._finish_worker(w, lambda: self._apply_checklist_ai(n, text, rep))

        def _err(err, n=node, w=worker):
            self._finish_worker(w, lambda: (n.set_ai_busy(False), logger.warning(f"[CHECKLIST_AI] {err}")))

        worker.finished_signal.connect(_done)
        worker.error_signal.connect(_err)

        if self._active_workers < self._max_concurrent_workers:
            self._start_worker(worker)
        else:
            self._pending_workers.append((node, worker))

    def _apply_checklist_ai(self, node, text, replace):
        items = self._parse_checklist_json(text)
        node.apply_ai_items(items, replace=replace)
