from __future__ import annotations

import copy
import os
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal

from v.provider import ChatMessage
from v.logger import get_logger

logger = get_logger("qonvo.plugin")


class _BatchWorker(QThread):
    batch_finished = pyqtSignal(list)
    batch_failed = pyqtSignal()

    def __init__(self, try_fn, func_def, input_data, count, parameters,
                 node_options=None, on_job_created=None):
        super().__init__()
        self._try_fn = try_fn
        self._func_def = func_def
        self._input_data = input_data
        self._count = count
        self._parameters = parameters
        self._node_options = node_options or {}
        self._on_job_created = on_job_created
        self.job_name = None

    def run(self):
        try:
            results = self._try_fn(
                self._func_def, self._input_data, self._count,
                self._parameters, self._node_options, self._on_job_created,
            )
            if results is not None:
                self.batch_finished.emit(results)
            else:
                self.batch_failed.emit()
        except Exception:
            self.batch_failed.emit()


class _BatchResumeWorker(QThread):
    batch_finished = pyqtSignal(list)
    batch_failed = pyqtSignal()

    def __init__(self, provider, job_name, key_index, is_nanobanana):
        super().__init__()
        self._provider = provider
        self.job_name = job_name
        self._key_index = key_index
        self._is_nanobanana = is_nanobanana

    def run(self):
        try:
            results = self._provider.poll_batch_job(
                self.job_name, self._key_index, self._is_nanobanana,
            )
            if results is not None:
                normalized = []
                for r in results:
                    if isinstance(r, dict):
                        normalized.append((r.get("text", ""), r.get("images", [])))
                    else:
                        normalized.append((str(r), []))
                self.batch_finished.emit(normalized)
            else:
                self.batch_failed.emit()
        except Exception:
            self.batch_failed.emit()


class ChatWorkersMixin:

    def _handle_round_table_send(self, node_id, model, message, files):
        from .round_table import RoundTableWorker

        node = self.app.nodes.get(node_id)
        if node is None:
            return
        provider = self._ensure_provider()
        if not node.participants:
            node.set_response("No participants configured", done=True)
            return

        context_messages = []
        if self.system_prompt:
            context_messages.append(ChatMessage(role="system", content=self.system_prompt))

        worker = RoundTableWorker(
            provider,
            node.participants,
            message,
            context_messages
        )

        worker.participant_started.connect(lambda idx, name, n=node: n.set_participant_progress(idx, name))
        worker.chunk_received.connect(lambda idx, chunk, n=node: n.add_response_chunk(idx, chunk))
        worker.participant_finished.connect(lambda idx, response, n=node: n.finalize_participant(idx, response))
        worker.tokens_received.connect(lambda i, o, n=node: n.set_tokens(i, o))
        worker.error_signal.connect(lambda err, n=node, w=worker: self._finish_worker(w, lambda: (n.set_response(f"Error: {err}", done=True), self._emit_complete_signal(n))))
        worker.all_finished.connect(lambda text, n=node, w=worker: self._finish_worker(w, lambda: (n.set_response(text, done=True), self._emit_complete_signal(n))))

        if self._active_workers < self._max_concurrent_workers:
            self._start_worker(worker)
        else:
            self._pending_workers.append((node, worker))

    def _try_candidate_count(self, func_def, input_data, count, parameters,
                             node_options=None, on_job_created=None):
        from v.provider import get_default_options

        llm_nodes = [n for n in func_def.nodes if n.node_type == "llm_call"]
        if len(llm_nodes) != 1:
            return None

        allowed_types = {"start", "end", "llm_call", "get_param"}
        if any(n.node_type not in allowed_types for n in func_def.nodes):
            return None

        llm_node = llm_nodes[0]
        model = llm_node.config.get("model", "")
        is_nanobanana = model in ("gemini-3-pro-image-preview", "gemini-2.5-flash-image")

        if model.startswith("imagen"):
            return None

        template = llm_node.config.get("prompt_template", "{input}")
        prompt = template.replace("{input}", input_data or "")
        for param_name, param_info in parameters.items():
            if param_info.get("type") != "image":
                prompt = prompt.replace(
                    f"{{param:{param_name}}}", str(param_info.get("value", ""))
                )

        provider = self._ensure_provider()
        if not provider:
            return None

        image_attachments = []
        for param_name, param_info in parameters.items():
            if param_info.get("type") == "image":
                path = param_info.get("value", "")
                if path and os.path.exists(path):
                    image_attachments.append(path)

        model_opts = get_default_options(model)
        model_opts.update(node_options or {})

        try:
            results = provider.chat_candidates(
                model,
                [ChatMessage(role="user", content=prompt,
                             attachments=image_attachments or None)],
                count=count,
                on_job_created=on_job_created,
                system_prompt=self.system_prompt,
                system_files=self.system_files,
                **model_opts,
            )
            if results is None:
                return None
            normalized = []
            for r in results:
                if isinstance(r, dict):
                    normalized.append((r.get("text", ""), r.get("images", [])))
                else:
                    normalized.append((str(r), []))

            if is_nanobanana:
                has_images = any(imgs for _, imgs in normalized)
                if not has_images:
                    return None

            return normalized if normalized else None
        except Exception:
            return None

    def _spawn_pref_workers(self, node, function_id, input_data, count,
                            parameters, node_options, provider):
        from .function_engine import FunctionExecutionWorker

        for run_idx in range(count):
            function_def = copy.deepcopy(self.functions_library[function_id])

            worker = FunctionExecutionWorker(
                provider,
                function_def,
                input_data or "",
                context_messages=[],
                system_prompt=self.system_prompt,
                system_files=self.system_files,
                parameters=parameters,
                node_options=node_options,
            )

            worker.step_started.connect(
                lambda name, step, total, n=node: n.set_response(
                    f"{name} ({step}/{total})", done=False
                )
            )
            worker.tokens_received.connect(
                lambda i, o, n=node: n.set_tokens(i, o)
            )
            worker.error_signal.connect(
                lambda err, n=node, w=worker: self._finish_worker(
                    w, lambda: self._on_pref_run_error(n, err)
                )
            )
            worker.all_finished.connect(
                lambda outputs, images, n=node, w=worker: self._finish_worker(
                    w, lambda: self._on_pref_run_finished(n, outputs, images)
                )
            )

            if self._active_workers < self._max_concurrent_workers:
                self._start_worker(worker)
            else:
                self._pending_workers.append((node, worker))

    def _execute_function_graph(self, node_id, function_id, input_data, _):
        from .function_engine import FunctionExecutionWorker

        node = self.app.nodes.get(node_id)
        if node is None:
            return

        if not function_id:
            node.set_response("함수를 선택하세요", done=True)
            return

        if function_id not in self.functions_library:
            node.set_response(f"함수를 찾을 수 없습니다: {function_id}", done=True)
            return

        provider = self._ensure_provider()

        parameters = getattr(node, 'parameters', {})
        node_options = getattr(node, 'node_options', {})

        pref_enabled = getattr(node, 'preferred_options_enabled', False)
        pref_count = getattr(node, 'preferred_options_count', 3) if pref_enabled else 1

        if pref_enabled:
            self._preferred_results[node_id] = []
            self._preferred_expected[node_id] = pref_count
            node._on_preferred_selected = self._on_preferred_option_selected
            node.set_response(f"실행 중... (0/{pref_count})", done=False)

            func_def = copy.deepcopy(self.functions_library[function_id])

            llm_nodes_for_cb = [n for n in func_def.nodes if n.node_type == "llm_call"]
            batch_model = llm_nodes_for_cb[0].config.get("model", "") if llm_nodes_for_cb else ""
            batch_is_nano = batch_model in ("gemini-3-pro-image-preview", "gemini-2.5-flash-image")

            batch_worker = _BatchWorker(
                self._try_candidate_count, func_def, input_data, pref_count,
                parameters, node_options=node_options,
            )

            def _on_job_created(job_name, key_index, _nid=node_id,
                                _model=batch_model, _nano=batch_is_nano,
                                _pc=pref_count, _w=batch_worker):
                from v.batch_queue import BatchQueueManager
                _w.job_name = job_name
                BatchQueueManager().add_job(
                    job_name, self._board_name or "", _nid, _model,
                    _nano, key_index, _pc,
                )

            batch_worker._on_job_created = _on_job_created
            batch_worker.batch_finished.connect(
                lambda results, n=node, w=batch_worker: self._finish_worker(
                    w, lambda: self._on_batch_finished(n, results, getattr(w, 'job_name', None))
                )
            )
            batch_worker.batch_failed.connect(
                lambda n=node, fid=function_id, idata=input_data, pc=pref_count,
                       p=parameters, no=node_options, prov=provider, w=batch_worker:
                    self._finish_worker(
                        w, lambda: self._spawn_pref_workers(n, fid, idata, pc, p, no, prov)
                    )
            )

            if self._active_workers < self._max_concurrent_workers:
                self._start_worker(batch_worker)
            else:
                self._pending_workers.append((node, batch_worker))
        else:
            function_def = copy.deepcopy(self.functions_library[function_id])

            worker = FunctionExecutionWorker(
                provider,
                function_def,
                input_data or "",
                context_messages=[],
                system_prompt=self.system_prompt,
                system_files=self.system_files,
                parameters=parameters,
                node_options=node_options,
            )

            worker.step_started.connect(lambda name, step, total, n=node: n.set_response(f"{name} ({step}/{total})", done=False))
            worker.tokens_received.connect(lambda i, o, n=node: n.set_tokens(i, o))
            worker.error_signal.connect(lambda err, n=node, w=worker: self._finish_worker(w, lambda: (n.set_response(f"Error: {err}", done=True))))
            worker.all_finished.connect(lambda outputs, images, n=node, w=worker: self._finish_worker(w, lambda: self._on_function_finished(n, outputs, images)))

            if self._active_workers < self._max_concurrent_workers:
                self._start_worker(worker)
            else:
                self._pending_workers.append((node, worker))

    def _on_function_finished(self, node, outputs, images):
        result = outputs.get("output", "")
        if not result and outputs:
            result = next(iter(outputs.values()))

        node.ai_response = result

        if images:
            node.set_image_response(result, images, [])
        else:
            node.set_response(result, done=True)

        self._emit_complete_signal(node, images)

    def _on_batch_finished(self, node, results, job_name=None):
        if job_name:
            from v.batch_queue import BatchQueueManager
            BatchQueueManager().remove_job(job_name)

        nid = node.node_id
        if nid not in self._preferred_results:
            return
        for text, images in results:
            self._preferred_results[nid].append((text, images))
        node.show_preferred_results(self._preferred_results[nid])
        notify = getattr(node, 'notify_on_complete', False)
        if notify:
            from .toast_notification import ToastManager
            node_name = getattr(node, 'function_name', None) or f"Node #{node.node_id}"
            main_window = self.view.window() if self.view else None
            ToastManager.instance().show_toast(
                f"{node_name} - {len(results)}개 결과 준비됨", main_window
            )

    def _on_pref_run_finished(self, node, outputs, images):
        result = outputs.get("output", "")
        if not result and outputs:
            result = next(iter(outputs.values()))

        nid = node.node_id
        if nid not in self._preferred_results:
            return

        self._preferred_results[nid].append((result, images or []))

        expected = self._preferred_expected.get(nid, 1)
        done_count = len(self._preferred_results[nid])
        if done_count < expected:
            node.set_response(f"실행 중... ({done_count}/{expected})", done=False)
        else:
            node.show_preferred_results(self._preferred_results[nid])
            notify = getattr(node, 'notify_on_complete', False)
            if notify:
                from .toast_notification import ToastManager
                node_name = getattr(node, 'function_name', None) or f"Node #{node.node_id}"
                main_window = self.view.window() if self.view else None
                ToastManager.instance().show_toast(
                    f"{node_name} - {expected}개 결과 준비됨", main_window
                )

    def _on_pref_run_error(self, node, error):
        nid = node.node_id
        if nid not in self._preferred_results:
            return
        self._preferred_results[nid].append((f"Error: {error}", []))
        expected = self._preferred_expected.get(nid, 1)
        if len(self._preferred_results[nid]) >= expected:
            node.show_preferred_results(self._preferred_results[nid])

    def _on_preferred_option_selected(self, node, selections):
        from .items import ImageCardItem, DimensionItem
        from .repository_node import RepositoryNodeWidget

        nid = node.node_id

        if not selections:
            self._preferred_results.pop(nid, None)
            self._preferred_expected.pop(nid, None)
            self._rework_params.pop(nid, None)
            return

        first_text, first_images = selections[0]
        node.ai_response = first_text

        all_images = []
        for _, imgs in selections:
            all_images.extend(imgs)

        output_port_to_check = None
        if hasattr(node, 'output_ports') and node.output_ports:
            output_port_to_check = next(iter(node.output_ports.values()))
        elif hasattr(node, 'output_port') and node.output_port is not None:
            output_port_to_check = node.output_port

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

        if hasattr(node, 'signal_output_port') and node.signal_output_port is not None:
            self.emit_signal(node.signal_output_port, data=getattr(node, 'ai_response', None))

        notify = getattr(node, 'notify_on_complete', False)
        if notify:
            from .toast_notification import ToastManager
            node_name = getattr(node, 'function_name', None) or f"Node #{node.node_id}"
            main_window = self.view.window() if self.view else None
            ToastManager.instance().show_toast(
                f"{node_name} - {len(selections)}개 선택 완료", main_window
            )

        self._preferred_results.pop(nid, None)
        self._preferred_expected.pop(nid, None)
        self._rework_params.pop(nid, None)

    def _resume_pending_batches(self):
        if not self._board_name:
            return
        from v.batch_queue import BatchQueueManager
        mgr = BatchQueueManager()
        jobs = mgr.get_jobs_for_board(self._board_name)
        if not jobs:
            return

        logger.info(f"[BATCH_RESUME] {len(jobs)}개 pending batch job 발견")
        provider = self._ensure_provider()
        if not provider.gemini:
            logger.warning("[BATCH_RESUME] Gemini provider not available (no API key)")
            return

        for job in jobs:
            node_id = job["node_id"]
            node = self.app.nodes.get(node_id)
            if node is None:
                if not self._force_materialize_by_id(node_id):
                    logger.warning(f"[BATCH_RESUME] Node {node_id} not found, removing job {job['job_name']}")
                    mgr.remove_job(job["job_name"])
                    continue
                node = self.app.nodes.get(node_id)
                if node is None:
                    mgr.remove_job(job["job_name"])
                    continue

            pref_count = job.get("pref_count", 3)
            self._preferred_results[node_id] = []
            self._preferred_expected[node_id] = pref_count
            node._on_preferred_selected = self._on_preferred_option_selected
            node.set_response("배치 결과 대기 중...", done=False)

            worker = _BatchResumeWorker(
                provider.gemini, job["job_name"], job["key_index"], job["is_nanobanana"]
            )
            job_name = job["job_name"]
            worker.batch_finished.connect(
                lambda results, n=node, jn=job_name, w=worker:
                    self._finish_worker(w, lambda: self._on_batch_finished(n, results, jn))
            )
            worker.batch_failed.connect(
                lambda jn=job_name, n=node, w=worker:
                    self._finish_worker(w, lambda: self._on_batch_resume_failed(n, jn))
            )

            if self._active_workers < self._max_concurrent_workers:
                self._start_worker(worker)
            else:
                self._pending_workers.append((node, worker))

        logger.info(f"[BATCH_RESUME] {len(jobs)}개 job 폴링 재개 완료")

    def _on_batch_resume_failed(self, node, job_name):
        from v.batch_queue import BatchQueueManager
        BatchQueueManager().remove_job(job_name)
        node.set_response("배치 만료/실패 — 다시 실행해주세요", done=True)
        nid = node.node_id
        self._preferred_results.pop(nid, None)
        self._preferred_expected.pop(nid, None)
        self._rework_params.pop(nid, None)
        logger.warning(f"[BATCH_RESUME] Failed: {job_name} (node {nid})")

    def _on_image_payload(self, node, worker, payload):
        images = payload.get("images", [])
        text = payload.get("text", "")
        nid = getattr(node, 'node_id', '?')
        logger.info(f"[IMAGE_PAYLOAD] node={nid}, images={len(images)}, text_len={len(text)}")
        try:
            if not images:
                error_msg = text if text else "[이미지 생성 실패: API 응답에 이미지 없음]"
                logger.warning(f"[IMAGE_PAYLOAD] node={nid} no images: {error_msg!r}")
                node.set_response(error_msg, done=True)
                self._finish_worker(worker)
                self._emit_complete_signal(node)
                return
            node.set_tokens(payload.get("prompt_tokens") or 0, payload.get("candidates_tokens") or 0)
            node.set_image_response(text, images, payload.get("thought_signatures", []))
            self._finish_worker(worker)
            self._emit_complete_signal(node, images)
        except Exception as e:
            logger.error(f"[IMAGE_PAYLOAD] node={nid} crashed: {e}", exc_info=True)
            try:
                node.set_response(f"Error: {e}", done=True)
            except Exception:
                pass
            self._finish_worker(worker)

    def _cancel_node_workers(self, node_id):
        cancelled = 0

        for w in list(self._workers):
            if getattr(w, '_node_id', None) == node_id:
                w.cancel()
                self._finish_worker(w)
                cancelled += 1

        self._pending_workers = [
            (n, w) for n, w in self._pending_workers
            if getattr(n, 'node_id', None) != node_id
        ]

        self._preferred_results.pop(node_id, None)
        self._preferred_expected.pop(node_id, None)
        self._rework_params.pop(node_id, None)

        node = self.app.nodes.get(node_id)
        if node and hasattr(node, 'set_response'):
            node.set_response("Cancelled", done=True)

        logger.info(f"[CANCEL] node={node_id}, cancelled_workers={cancelled}")

    def _start_worker(self, worker):
        self._active_workers += 1
        self._workers.append(worker)
        try:
            worker.start()
        except Exception:
            self._active_workers = max(0, self._active_workers - 1)
            if worker in self._workers:
                self._workers.remove(worker)

    def _finish_worker(self, worker, post=None):
        if getattr(worker, '_finished', False):
            logger.debug("[FINISH_WORKER] Already finished, skipping duplicate call")
            return
        worker._finished = True

        if post:
            try:
                post()
            except Exception as e:
                logger.error(f"[FINISH_WORKER] post() exception: {e}", exc_info=True)
        if worker in self._workers:
            self._workers.remove(worker)
        try:
            worker.quit()
        except Exception as e:
            logger.warning(f"[FINISH_WORKER] Failed to quit worker: {e}")

        if self._active_workers > 0:
            self._active_workers -= 1

        if self._pending_workers and self._active_workers < self._max_concurrent_workers:
            next_node, next_worker = self._pending_workers.pop(0)
            self._start_worker(next_worker)

    def _on_button_signal(self, node_id):
        button_node = self.app.nodes.get(node_id)
        if button_node is None:
            return
        if not hasattr(button_node, 'signal_output_port') or button_node.signal_output_port is None:
            return
        button_data = getattr(button_node, 'input_data', None)
        self.emit_signal(button_node.signal_output_port, data=button_data)

    def open_system_prompt_dialog(self):
        pass

    def open_history_search(self):
        from PyQt6.QtCore import Qt
        from .history_search import HistorySearchDialog
        if self._history_search_window is not None:
            self._history_search_window.raise_()
            self._history_search_window.activateWindow()
            return
        win = HistorySearchDialog(self)
        win.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        win.destroyed.connect(lambda: setattr(self, '_history_search_window', None))
        self._history_search_window = win
        win.show()
