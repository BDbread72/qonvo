from __future__ import annotations

import copy
import os
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QPointF, QRectF, QTimer, QThread, pyqtSignal
from PyQt6.QtWidgets import QGraphicsProxyWidget, QGraphicsScene

from v.boards.base import BoardPlugin
from v.settings import get_board_size, get_api_key, get_api_keys, get_model_options
from v.provider import GeminiProvider, ChatMessage
from v.logger import get_logger

logger = get_logger("qonvo.plugin")

# 전역 클립보드 (차원 간 복사/붙여넣기 공유)
_global_clipboard: Optional[Dict[str, Any]] = None
_global_paste_offset = 0

from .view import WhiteboardView
from .items import PortItem, EdgeItem, ImageCardItem, TextItem, GroupFrameItem
from .dimension_item import DimensionItem
from .widgets import StreamWorker, ApiKeyDialog
from .chat_node import ChatNodeWidget
from .function_node import FunctionNodeWidget
from .sticky_note import StickyNoteWidget
from .prompt_node import PromptNodeWidget
from .button_node import ButtonNodeWidget
from .round_table import RoundTableWidget
from .checklist import ChecklistWidget

from .repository_node import RepositoryNodeWidget
from .function_library import FunctionLibraryDialog
from .function_editor import FunctionEditorDialog
from .function_types import FunctionDefinition
from .lazy_loader import LazyLoadManager


class _BatchWorker(QThread):
    """chat_candidates 기반 배치 워커 (단순 함수 전용)

    단순 함수 구조 (Start -> LLM -> End)일 때 Batch API로 처리.
    복잡한 함수면 batch_failed 시그널로 fallback.
    """
    batch_finished = pyqtSignal(list)   # [(text, images), ...]
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
        self.job_name = None  # 생성된 batch job 이름 (큐 제거용)

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
    """앱 재시작 후 persisted batch job 폴링 재개"""
    batch_finished = pyqtSignal(list)   # [(text, images), ...]
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
                # 결과를 (text, images) 튜플로 정규화
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


class NodeProxyWidget(QGraphicsProxyWidget):
    """노드 위젯을 감싸는 커스텀 프록시 (Phase 4: 이벤트 기반 업데이트)

    노드 이동 시 포트 재배치를 자동으로 호출하여 엣지가 실시간으로 따라오도록 함.
    스냅 엔진을 통해 PPT 스타일 정렬을 지원.
    """
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


class WhiteBoardPlugin(BoardPlugin):
    NAME = "WhiteBoard"
    DESCRIPTION = "Whiteboard"
    VERSION = "1.0"
    ICON = "WB"

    def __init__(self, app):
        super().__init__(app)
        self.view: Optional[WhiteboardView] = None

        # dictionaries expected by existing modules
        self.proxies: Dict[int, QGraphicsProxyWidget] = {}
        self.function_proxies: Dict[int, QGraphicsProxyWidget] = {}
        self.round_table_proxies: Dict[int, QGraphicsProxyWidget] = {}
        self.sticky_proxies: Dict[int, QGraphicsProxyWidget] = {}
        self.prompt_proxies: Dict[int, QGraphicsProxyWidget] = {}
        self.button_proxies: Dict[int, QGraphicsProxyWidget] = {}
        self.checklist_proxies: Dict[int, QGraphicsProxyWidget] = {}

        self.repository_proxies: Dict[int, QGraphicsProxyWidget] = {}
        self.text_items: Dict[int, TextItem] = {}
        self.group_frame_items: Dict[int, GroupFrameItem] = {}
        self.image_card_items: Dict[int, ImageCardItem] = {}
        self.dimension_items: Dict[int, DimensionItem] = {}

        self._edges: List[EdgeItem] = []
        self._workers: List[Any] = []
        self._provider: Optional[GeminiProvider] = None
        self._dimension_windows: List[Any] = []  # U7: weakref로 누수 방지 — _open_dimension에서 정리

        # Phase 4: 동시 실행 워커 수 제한
        self._active_workers = 0
        self._max_concurrent_workers = 4
        self._pending_workers: List[tuple] = []  # (node_id, model, message, files)

        # Preferred Options: 다중 실행 결과 누적
        self._preferred_results: Dict[int, list] = {}   # node_id → [(text, images)]
        self._preferred_expected: Dict[int, int] = {}    # node_id → expected count

        self.system_prompt = ""
        self.system_files: List[str] = []
        self.functions_library: Dict[str, Any] = {}
        self._origin_item = None
        self._lazy_mgr = LazyLoadManager()
        self._batch_queue: List = []
        self._batch_loading = False
        self._board_name: Optional[str] = None

        # 차원 간 부모 참조
        self._parent_plugin: Optional['WhiteBoardPlugin'] = None
        self._parent_dimension_item: Optional[DimensionItem] = None

        # Phase 4: 30 FPS 타이머 제거, 이벤트 기반 업데이트로 전환
        # self._edge_timer = QTimer()  # [REMOVED]
        # self._edge_timer.setInterval(33)  # [REMOVED]
        # self._edge_timer.timeout.connect(self._update_all_edges)  # [REMOVED]

        # Phase 5.1: dimension_images 폴더 마이그레이션 (.temp/로 이동)
        self._migrate_dimension_images()

    def _migrate_dimension_images(self):
        """기존 dimension_images 폴더를 .temp/로 이동 (1회성 마이그레이션)"""
        import shutil
        from pathlib import Path
        from v.settings import get_app_data_path
        from v.board import BoardManager

        old_dir = Path(get_app_data_path()) / "dimension_images"
        if not old_dir.exists():
            return  # 마이그레이션 불필요

        boards_dir = BoardManager.get_boards_dir()
        new_dir = boards_dir / '.temp' / 'dimension_images'

        try:
            logger.info(f"[MIGRATE] Moving dimension_images: {old_dir} → {new_dir}")

            # 새 디렉토리 생성
            new_dir.mkdir(parents=True, exist_ok=True)

            # 파일 이동
            moved_count = 0
            for img_file in old_dir.glob("*.png"):
                try:
                    shutil.move(str(img_file), str(new_dir / img_file.name))
                    moved_count += 1
                except Exception as e:
                    logger.warning(f"[MIGRATE] Failed to move {img_file.name}: {e}")

            # 빈 폴더 삭제
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
        self.scene.setSceneRect(-half, -half, size, size)
        self.app.bind(self.scene)

        # Create origin marker
        self._origin_item = QGraphicsEllipseItem(-6, -6, 12, 12)
        self._origin_item.setBrush(QBrush(QColor("#444444")))
        self._origin_item.setPen(QPen(QColor("#666666"), 1))
        self._origin_item.setZValue(-100)
        self._origin_item.setToolTip("Origin (0, 0)")
        self.scene.addItem(self._origin_item)

        self.view = WhiteboardView(self.scene, plugin=self)
        # Phase 4: 타이머 제거 - 이벤트 기반으로 동작
        # self._edge_timer.start()  # [REMOVED]
        # 초기 엣지 업데이트 (한 번만)
        self._manual_update_all_edges()
        # Lazy loading 디바운스 타이머 설정
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
        # Phase 4: NodeProxyWidget 사용하여 노드 이동 시 자동으로 포트 재배치
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
        self._notify_modified()
        return proxy

    def _add_port(self, port_type, parent, **kwargs) -> PortItem:
        """포트 생성 + 씬 등록 + 라벨 + 재배치를 한 번에 수행."""
        port = PortItem(port_type, parent, **kwargs)
        self.scene.addItem(port)
        port.scene_add_label(self.scene)
        port.reposition()
        if self.view:
            self.view._all_port_items.add(port)
        return port

    def _create_ports(self, proxy, widget):
        """노드에 입출력 포트 생성 (데이터 + 신호)"""
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

    # ── Chat 노드 동적 입력 포트 관리 ──

    def _add_chat_input_port(self, node, port_type_str, port_name=None):
        """Chat 노드에 입력 포트 동적 추가 (text 또는 image)"""
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
        """Chat 노드에서 마지막 추가 입력 포트 제거"""
        if port_name not in node.input_ports:
            return
        port = node.input_ports[port_name]
        for edge in list(port.edges):
            self.remove_edge(edge)
        if port.scene():
            self.scene.removeItem(port)
        if hasattr(port, 'label') and port.label and port.label.scene():
            self.scene.removeItem(port.label)
        if self.view and port in self.view._all_port_items:
            self.view._all_port_items.discard(port)
        del node.input_ports[port_name]

        node.extra_input_defs = [d for d in node.extra_input_defs if d["name"] != port_name]
        self._reindex_chat_input_ports(node)
        node._update_input_count()
        self._notify_modified()

    def _reindex_chat_input_ports(self, node):
        """Chat 노드의 입력 포트 인덱스 재조정"""
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

    def _notify_modified(self):
        if callable(self.on_modified):
            self.on_modified()
        if self.view and hasattr(self.view, '_branch_graph'):
            self.view._branch_graph.mark_dirty()

    def _manual_update_all_edges(self):
        """수동 엣지 업데이트 (초기화/디버그용)

        Phase 4: 이전에는 30 FPS 타이머로 지속적으로 호출되었으나,
        이제는 이벤트 기반으로 동작하므로 필요할 때만 호출
        """
        for edge in list(self._edges):
            edge.update_path()
        for d in (self.proxies, self.function_proxies, self.round_table_proxies,
                  self.sticky_proxies, self.prompt_proxies, self.button_proxies, self.checklist_proxies):
            for proxy in d.values():
                node = proxy.widget()
                if node and hasattr(node, "reposition_ports"):
                    node.reposition_ports()

    def add_node(self, pos: Optional[QPointF] = None, node_id: Optional[int] = None):
        node_id = self._next_id(node_id)
        node = ChatNodeWidget(node_id, on_send=self._handle_chat_send, on_modified=self._notify_modified)
        node.on_add_port = self._add_chat_input_port
        node.on_remove_port = self._remove_chat_input_port
        proxy = self._add_proxy(node, node_id, pos, self.proxies)
        self._create_ports(proxy, node)
        QTimer.singleShot(0, node.reposition_ports)
        return proxy

    def _open_function_library(self, function_node):
        """함수 라이브러리 다이얼로그 열기"""
        def on_select(function_def: FunctionDefinition):
            function_node.set_function(function_def.function_id, function_def.name)
            # Update ports based on function parameters
            self._update_function_ports(function_node, function_def)
            self._notify_modified()

        def on_update(updated_library: dict):
            """함수 라이브러리 변경 시 저장"""
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
        """함수 파라미터 기반으로 입력/출력 포트 재생성"""
        proxy = function_node.proxy
        if not proxy:
            return

        # 1. 기존 데이터 포트 제거 (signal 포트는 유지)
        for port_name in list(function_node.input_ports.keys()):
            port = function_node.input_ports[port_name]
            # 연결된 엣지 제거
            for edge in list(port.edges):
                self.remove_edge(edge)  # 언더스코어 없음!
            # 씬에서 포트 제거
            if port.scene():
                self.scene.removeItem(port)
            if hasattr(port, 'label') and port.label and port.label.scene():
                self.scene.removeItem(port.label)
            del function_node.input_ports[port_name]

        for port_name in list(function_node.output_ports.keys()):
            port = function_node.output_ports[port_name]
            for edge in list(port.edges):
                self.remove_edge(edge)  # 언더스코어 없음!
            if port.scene():
                self.scene.removeItem(port)
            if hasattr(port, 'label') and port.label and port.label.scene():
                self.scene.removeItem(port.label)
            del function_node.output_ports[port_name]

        # 2. 파라미터 기반 입력 포트 생성
        param_count = len(function_def.parameters)
        for i, param in enumerate(function_def.parameters):
            port_name = param.name
            port_type = PortItem.TYPE_FILE if param.param_type == "image" else PortItem.TYPE_STRING

            port = PortItem(
                PortItem.INPUT, proxy,
                name=port_name,
                index=i, total=param_count + 1,  # +1 for signal port
                data_type=port_type
            )
            self.scene.addItem(port)
            port.scene_add_label(self.scene)
            function_node.input_ports[port_name] = port
            port.reposition()
            if self.view:
                self.view._all_port_items.add(port)

        # 3. 출력 포트 생성 (기본 1개)
        outputs = function_def.get_outputs()
        output_count = len(outputs) if outputs else 1

        if outputs:
            for i, output in enumerate(outputs):
                port = PortItem(
                    PortItem.OUTPUT, proxy,
                    name=output.name,
                    index=i, total=output_count + 1,  # +1 for signal port
                    data_type=PortItem.TYPE_STRING
                )
                self.scene.addItem(port)
                port.scene_add_label(self.scene)
                function_node.output_ports[output.name] = port
                port.reposition()
                if self.view:
                    self.view._all_port_items.add(port)
        else:
            # 출력이 없으면 기본 출력 포트 1개
            port = PortItem(
                PortItem.OUTPUT, proxy,
                name="_default",
                index=0, total=2,  # 1 data + 1 signal
                data_type=PortItem.TYPE_STRING
            )
            self.scene.addItem(port)
            port.scene_add_label(self.scene)
            function_node.output_ports["_default"] = port
            port.reposition()
            if self.view:
                self.view._all_port_items.add(port)

        # Signal 포트는 그대로 유지 (이미 생성되어 있음)
        if hasattr(function_node, 'signal_input_port') and function_node.signal_input_port:
            function_node.signal_input_port.port_total = param_count + 1
            function_node.signal_input_port.reposition()
        if hasattr(function_node, 'signal_output_port') and function_node.signal_output_port:
            function_node.signal_output_port.port_total = output_count + 1
            function_node.signal_output_port.reposition()

    def _edit_function(self, function_node):
        """함수 편집 다이얼로그 열기"""
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
            on_modified=self._notify_modified,
            on_open_library=self._open_function_library
        )
        proxy = self._add_proxy(node, node_id, pos, self.function_proxies)

        # Create default ports (will be recreated when function is selected)
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
        node = StickyNoteWidget(on_modified=self._notify_modified)
        node.node_id = node_id
        proxy = self._add_proxy(node, node_id, pos, self.sticky_proxies)
        node.output_port = self._add_port(
            PortItem.OUTPUT, proxy, name="텍스트",
            index=0, total=1, data_type=PortItem.TYPE_STRING)
        QTimer.singleShot(0, node.reposition_ports)
        return proxy

    def add_prompt_node(self, pos: Optional[QPointF] = None, node_id: Optional[int] = None):
        node_id = self._next_id(node_id)
        node = PromptNodeWidget(on_modified=self._notify_modified)
        node.node_id = node_id
        proxy = self._add_proxy(node, node_id, pos, self.prompt_proxies)
        node.output_port = self._add_port(
            PortItem.OUTPUT, proxy, name="prompt",
            index=0, total=1, data_type=PortItem.TYPE_STRING)
        QTimer.singleShot(0, node.reposition_ports)
        return proxy

    def add_button(self, pos: Optional[QPointF] = None, node_id: Optional[int] = None):
        node_id = self._next_id(node_id)
        node = ButtonNodeWidget(node_id, on_signal=self._on_button_signal, on_modified=self._notify_modified)
        proxy = self._add_proxy(node, node_id, pos, self.button_proxies)

        node.input_port = self._add_port(
            PortItem.INPUT, proxy, name="입력",
            index=0, total=2, data_type=PortItem.TYPE_STRING)
        node.signal_output_port = self._add_port(
            PortItem.OUTPUT, proxy, name="⚡ 신호",
            index=1, total=2, data_type=PortItem.TYPE_BOOLEAN)
        QTimer.singleShot(0, node.reposition_ports)
        return proxy

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
        item = ImageCardItem(pos.x(), pos.y(), image_path)
        item.node_id = node_id

        # width/height를 포트 생성 전에 먼저 설정 (reposition이 올바른 크기를 사용하도록)
        if width is not None and height is not None:
            item.prepareGeometryChange()
            item._width = width
            item._height = height
            item.update()

        self.scene.addItem(item)
        self.image_card_items[node_id] = item
        self.app.nodes[node_id] = item

        self._attach_item_ports(item, PortItem.TYPE_FILE, PortItem.TYPE_FILE)
        self._notify_modified()
        return item

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
        self._notify_modified()
        return item

    def _open_dimension_board(self, dimension_item: DimensionItem):
        """차원 더블클릭 시 내부 보드 창 열기"""
        from .dimension_board import DimensionBoardWindow

        # U7: 닫힌 창 참조 정리 (메모리 누수 방지)
        self._dimension_windows = [w for w in self._dimension_windows if w.isVisible()]
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
            on_modified=self._notify_modified
        )
        proxy = self._add_proxy(node, node_id, pos, self.round_table_proxies)
        self._create_ports(proxy, node)
        QTimer.singleShot(0, node.reposition_ports)
        return proxy

    def add_checklist(self, pos: Optional[QPointF] = None, node_id: Optional[int] = None,
                      title: str = "", items: list = None):
        node_id = self._next_id(node_id)
        node = ChecklistWidget(title=title, items=items, on_modified=self._notify_modified)
        node.node_id = node_id
        proxy = self._add_proxy(node, node_id, pos, self.checklist_proxies)
        node.output_port = self._add_port(
            PortItem.OUTPUT, proxy, name="체크리스트",
            index=0, total=1, data_type=PortItem.TYPE_STRING)
        QTimer.singleShot(0, node.reposition_ports)
        return proxy

    def add_repository(self, pos: Optional[QPointF] = None, node_id: Optional[int] = None):
        node_id = self._next_id(node_id)
        node = RepositoryNodeWidget(node_id, on_modified=self._notify_modified)
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

    def add_text_item(self, pos: Optional[QPointF] = None, node_id: Optional[int] = None):
        node_id = self._next_id(node_id)
        if pos is None:
            pos = self._cursor_scene_pos()
        item = TextItem(pos.x(), pos.y())
        item.node_id = node_id
        self.scene.addItem(item)
        self.text_items[node_id] = item
        self.app.nodes[node_id] = item
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
        self._notify_modified()
        return item

    def get_radial_menu_items(self, scene_pos: Optional[QPointF] = None, category: Optional[str] = None):
        """계층형 메뉴: 카테고리 → 노드"""
        if category is None:
            # 1단계: 카테고리 선택
            return [
                ("cat_nodes", "Nodes", "nodes"),
                ("cat_notes", "Notes", "notes"),
                ("cat_media", "Media", "media"),
                ("cat_ui", "UI", "ui"),
            ]

        # 2단계: 카테고리별 노드
        if category == "nodes":
            return [
                ("node", "Chat", lambda: self.add_node(scene_pos)),
                ("function", "Function", lambda: self.add_function(scene_pos)),
                ("round_table", "Round Table", lambda: self.add_round_table(scene_pos)),
                ("repository", "자료함", lambda: self.add_repository(scene_pos)),
            ]
        elif category == "notes":
            return [
                ("sticky", "Sticky", lambda: self.add_sticky(scene_pos)),
                ("prompt", "Prompt", lambda: self.add_prompt_node(scene_pos)),
                ("text", "Text", lambda: self.add_text_item(scene_pos)),
                ("checklist", "Checklist", lambda: self.add_checklist(scene_pos)),
            ]
        elif category == "media":
            return [
                ("image", "Image", lambda: self.add_image_card("", scene_pos)),
                ("dimension", "Dimension", lambda: self.add_dimension_item(scene_pos)),
            ]
        elif category == "ui":
            return [
                ("button", "Button", lambda: self.add_button(scene_pos)),
                ("group", "Group", lambda: self.add_group_frame(scene_pos)),
            ]

        return []

    def create_edge(self, start_port: PortItem, end_port: PortItem):
        from v.logger import get_logger
        logger = get_logger("qonvo.plugin")

        # 디버그: 모든 create_edge 호출 로깅
        if start_port and end_port:
            logger.debug(f"[EDGE CREATE] Attempting: {start_port.port_name}({start_port.port_data_type}) -> {end_port.port_name}({end_port.port_data_type})")

        if start_port is None or end_port is None:
            logger.warning(f"[EDGE CREATE] Rejected: None port (start={start_port}, end={end_port})")
            return None
        if start_port is end_port:
            logger.warning(f"[EDGE CREATE] Rejected: Same port")
            return None
        if start_port.port_type == end_port.port_type:
            logger.warning(f"[EDGE CREATE] Rejected: Same direction (type={start_port.port_type})")
            return None

        if start_port.port_type == PortItem.INPUT:
            start_port, end_port = end_port, start_port

        if end_port.port_type == PortItem.INPUT and end_port.edges and not end_port.multi_connect:
            logger.debug(f"[EDGE CREATE] Removing existing edge from INPUT port {end_port.port_name}")
            self.remove_edge(end_port.edges[0])

        for edge in self._edges:
            if edge.source_port is start_port and edge.target_port is end_port:
                logger.warning(f"[EDGE CREATE] Rejected: Duplicate edge (already exists)")
                return None

        edge = EdgeItem(start_port, end_port)
        self.scene.addItem(edge)
        self._edges.append(edge)
        self.app.edges.append(edge)
        logger.debug(f"[EDGE CREATE] Success: {start_port.port_name} -> {end_port.port_name} (is_type_valid={edge.is_type_valid})")
        self._notify_modified()
        return edge

    def remove_edge(self, edge: EdgeItem):
        if edge in self._edges:
            self._edges.remove(edge)
        if edge in self.app.edges:
            self.app.edges.remove(edge)
        edge.disconnect()
        if self.scene:
            self.scene.removeItem(edge)
        self._notify_modified()

    # ── 공용 포트/엣지 정리 ──────────────────────────────────

    def _collect_ports(self, item_or_proxy) -> list:
        """아이템 종류에 관계없이 모든 포트를 수집한다.

        BaseNode.iter_ports() 또는 SceneItemMixin.iter_ports()에 위임.
        """
        # proxy 기반 노드 → widget의 iter_ports()
        obj = item_or_proxy
        if hasattr(item_or_proxy, "widget"):
            obj = item_or_proxy.widget() or item_or_proxy
        if hasattr(obj, "iter_ports"):
            return list(obj.iter_ports())
        return []

    def _remove_ports_and_edges(self, ports: list):
        """포트에 연결된 엣지를 모두 제거한 뒤, 포트 자체도 씬에서 제거한다."""
        port_set = set(id(p) for p in ports)

        # 1. 엣지 제거
        for edge in list(self._edges):
            if id(edge.source_port) in port_set or id(edge.target_port) in port_set:
                self.remove_edge(edge)

        # 2. 포트 + 라벨을 씬에서 제거
        for port in ports:
            if port and self.scene:
                if hasattr(port, '_label_bg') and port._label_bg:
                    self.scene.removeItem(port._label_bg)
                if hasattr(port, '_label') and port._label:
                    self.scene.removeItem(port._label)
                self.scene.removeItem(port)
                if self.view:
                    self.view._all_port_items.discard(port)

    # ── 삭제 메서드 ──────────────────────────────────────────

    def delete_proxy_item(self, proxy):
        node = proxy.widget()
        # U3: proxy.widget()이 None일 수 있음 (이미 삭제된 경우)
        if node is None:
            if self.scene:
                self.scene.removeItem(proxy)
            return
        node_id = getattr(node, "node_id", None)

        if hasattr(node, 'cleanup_temp_files'):
            node.cleanup_temp_files()

        self._remove_ports_and_edges(self._collect_ports(proxy))

        for d in (self.proxies, self.function_proxies, self.round_table_proxies,
                  self.sticky_proxies, self.prompt_proxies, self.button_proxies,
                  self.checklist_proxies, self.repository_proxies):
            d.pop(node_id, None)
        self.app.nodes.pop(node_id, None)
        if self.scene:
            self.scene.removeItem(proxy)
        self._notify_modified()

    # ── 통합 씬 아이템 삭제 ─────────────────────────────────

    def _delete_scene_item(self, item, registry: dict):
        """공통 씬 아이템 삭제 (ImageCard, Dimension, GroupFrame, Text).

        포트/엣지 정리 → 레지스트리 제거 → 씬 제거 → 수정 알림.
        """
        node_id = getattr(item, "node_id", None)
        self._remove_ports_and_edges(self._collect_ports(item))
        registry.pop(node_id, None)
        self.app.nodes.pop(node_id, None)
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

    def emit_signal(self, source_port, data=None):
        """신호 포트(⚡)로부터 신호를 발송하여 연결된 모든 노드의 on_signal_input() 호출"""
        if source_port is None:
            return
        if source_port.port_data_type != PortItem.TYPE_BOOLEAN:
            return

        for edge in list(self._edges):
            if edge.source_port == source_port:
                target_proxy = edge.target_port.parent_proxy
                if target_proxy and hasattr(target_proxy, 'widget'):
                    target_node = target_proxy.widget()
                    if target_node is None:
                        continue
                    if hasattr(target_node, 'on_signal_input'):
                        target_node.on_signal_input(input_data=data)

    def _emit_complete_signal(self, node, images=None):
        """노드의 완료 신호(⚡ 완료) 발송 및 _default 출력 포트 연결 노드 자동 실행"""
        from v.logger import get_logger
        logger = get_logger("qonvo.plugin")
        nid = getattr(node, 'node_id', '?')
        img_count = len(images) if images else 0
        logger.info(f"[EMIT_COMPLETE] node={nid}, images={img_count}")

        # 0. 완료 알림 (토스트)
        notify = getattr(node, 'notify_on_complete', False)
        if notify:
            from .toast_notification import ToastManager
            node_name = getattr(node, 'function_name', None) or f"Node #{node.node_id}"
            main_window = self.view.window() if self.view else None
            ToastManager.instance().show_toast(f"{node_name} - 완료", main_window)

        # 1. signal_output_port(⚡ 완료)로 신호 전달
        if hasattr(node, 'signal_output_port') and node.signal_output_port is not None:
            self.emit_signal(node.signal_output_port)

        # 2. 출력 포트에 연결된 노드들 자동 실행
        output_port_to_check = None

        if hasattr(node, 'output_ports') and node.output_ports:
            first_output_name = next(iter(node.output_ports.keys()))
            output_port_to_check = node.output_ports[first_output_name]
        elif hasattr(node, 'output_port') and node.output_port is not None:
            output_port_to_check = node.output_port

        if output_port_to_check and hasattr(output_port_to_check, 'edges'):
            edge_count = len(output_port_to_check.edges)
            logger.info(f"[EMIT_COMPLETE] node={nid} output_port edges={edge_count}")
            for edge in list(output_port_to_check.edges):
                target_port = edge.target_port
                # U4: 삭제된 노드에 대한 안전 체크
                if target_port is None or target_port.parent_proxy is None:
                    logger.warning(f"[EMIT_COMPLETE] node={nid} -> target port/proxy is None, skipping")
                    continue
                target_type = type(target_port.parent_proxy).__name__
                logger.info(f"[EMIT_COMPLETE] node={nid} -> target={target_type}")

                if isinstance(target_port.parent_proxy, DimensionItem):
                    result_text = getattr(node, 'ai_response', None) or getattr(node, 'text_content', '') or ''
                    self._add_to_dimension(target_port.parent_proxy, result_text, images)
                    continue

                if isinstance(target_port.parent_proxy, ImageCardItem):
                    logger.info(f"[EMIT_COMPLETE] -> ImageCard update (images={img_count})")
                    self._update_image_card(target_port.parent_proxy, node, images)
                    continue

                # 자료함 노드: 결과를 폴더에 자동 저장
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
                        target_node.on_signal_input()

    def _update_image_card(self, card: ImageCardItem, source_node, images=None):
        """ImageCardItem에 이미지 데이터 설정 (소스 노드 또는 images에서 추출)"""
        import os, base64
        from v.settings import get_app_data_path

        img_bytes = None

        # 1. images 파라미터에서 첫 번째 이미지 추출
        if images:
            raw = images[0]
            if isinstance(raw, bytes):
                img_bytes = raw
            elif isinstance(raw, str):
                if raw.startswith("data:image"):
                    _, encoded = raw.split(",", 1)
                    img_bytes = base64.b64decode(encoded)
                else:
                    img_bytes = base64.b64decode(raw)

        # 2. 소스 노드의 image_path가 있으면 직접 사용
        if not img_bytes:
            src_path = getattr(source_node, 'image_path', None)
            if src_path and os.path.exists(src_path):
                card.set_image(src_path)
                return

        if not img_bytes:
            logger.warning("[IMAGE_CARD] No image data available for card update")
            return

        # 3. 바이트 → 보드별 temp 폴더에 저장 후 set_image
        from v.board import BoardManager
        board_name = self._board_name or "untitled"
        temp_dir = BoardManager.get_boards_dir() / '.temp' / board_name / 'attachments'
        temp_dir.mkdir(parents=True, exist_ok=True)
        import uuid
        img_path = str(temp_dir / f"{uuid.uuid4().hex}.png")
        try:
            with open(img_path, "wb") as f:
                f.write(img_bytes)
            logger.info(f"[IMAGE_CARD] Saved: {img_path} ({len(img_bytes)} bytes)")
            card.set_image(img_path)
        except Exception as e:
            logger.error(f"[IMAGE_CARD] Failed to save: {e}")

    def _save_to_repository(self, repo_node: RepositoryNodeWidget, source_node, images=None):
        """자료함 노드의 폴더에 데이터 저장"""
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
        """Dimension 내부 보드에 결과 그룹 추가 (텍스트 + 이미지)"""
        import os
        import base64
        import uuid
        from PyQt6.QtGui import QPixmap
        from v.settings import get_app_data_path
        from v.constants import DIMENSION_RESULTS_PER_ROW, DIMENSION_ROW_HEIGHT

        board_data = dimension_item.get_board_data()
        next_id = board_data.get("next_id", 1)

        # 그룹 번호 계산 (기존 그룹 개수 + 1)
        group_count = len(board_data.get("group_frames", [])) + 1
        group_label = f"Result #{group_count}"

        # Row 계산 (5개마다 아래로)
        results_per_row = DIMENSION_RESULTS_PER_ROW
        row = (group_count - 1) // results_per_row
        group_y = 100 + row * DIMENSION_ROW_HEIGHT

        # 같은 row에 있는 기존 그룹들 찾기 (실제 크기에 맞춰 배치)
        existing_groups_in_row = [
            g for g in board_data.get("group_frames", [])
            if abs(g.get("y", 0) - group_y) < 50  # 같은 row (오차 허용)
        ]

        if existing_groups_in_row:
            # 가장 오른쪽 그룹의 끝 위치 찾기
            rightmost = max(existing_groups_in_row, key=lambda g: g.get("x", 0) + g.get("width", 0))
            group_x = rightmost.get("x", 0) + rightmost.get("width", 0) + 50  # 50px 간격
        else:
            # 이 row의 첫 그룹
            group_x = 100

        # 내부 아이템들의 위치 및 너비 추적
        items_in_group = []
        max_item_width = 400  # 최소 너비
        current_y = group_y + 40  # 그룹 라벨 아래

        # 1. 텍스트가 있으면 Sticky Note 추가 (너비 자동 조정)
        if content:
            # 텍스트 길이에 따라 너비 결정
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

        # 2. 이미지가 있으면 Image Card 추가 (크기 자동 조정)
        if images:
            # 이미지 저장 디렉토리 생성 (보드별 .temp/{board}/attachments/)
            from v.board import BoardManager
            boards_dir = BoardManager.get_boards_dir()
            board_name = self._board_name or "untitled"
            img_dir = boards_dir / '.temp' / board_name / 'attachments'
            img_dir.mkdir(parents=True, exist_ok=True)

            for idx, img_data in enumerate(images):
                # 이미지 데이터 디코딩
                raw_bytes = None
                if isinstance(img_data, bytes):
                    raw_bytes = img_data
                elif isinstance(img_data, str):
                    if img_data.startswith("data:image"):
                        header, encoded = img_data.split(",", 1)
                        raw_bytes = base64.b64decode(encoded)
                    else:
                        raw_bytes = base64.b64decode(img_data)

                if not raw_bytes:
                    continue

                # 보드별 temp에 UUID 파일로 저장
                img_filename = f"{uuid.uuid4().hex}.png"
                img_path = os.path.join(img_dir, img_filename)
                try:
                    with open(img_path, "wb") as f:
                        f.write(raw_bytes)
                    logger.info(f"[DIM] Saved image: {img_path} ({len(raw_bytes)} bytes)")
                except Exception as e:
                    logger.error(f"[DIM] Failed to save image: {e}")
                    continue

                # 이미지 실제 크기 확인
                pixmap = QPixmap(img_path)
                if not pixmap.isNull():
                    img_width = pixmap.width()
                    img_height = pixmap.height()

                    # 최대 너비 800px로 제한하며 비율 유지
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
                    # 이미지 로드 실패 시 기본값
                    card_width = 400
                    card_height = 300

                # Image Card 데이터 생성
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

        # 그룹 크기 자동 조정 (내부 아이템들에 맞춰)
        group_width = max_item_width + 40  # 좌우 여유 20px씩
        group_height = max(200, current_y - group_y + 20)

        # 그룹 프레임 데이터 생성
        group_frame_data = {
            "type": "group_frame",
            "id": next_id,  # Group Frame에도 id 필요
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
        next_id += 1  # Group Frame도 ID 소비

        # next_id 업데이트
        board_data["next_id"] = next_id

        # Dimension에 업데이트된 데이터 설정
        dimension_item.set_board_data(board_data)

        self._notify_modified()
    def _ensure_provider(self):
        if self._provider is not None:
            return self._provider

        from v.model_plugin import ProviderRouter

        # Gemini 키 (없어도 OK -- 플러그인 모델만 쓸 수도 있음)
        keys = get_api_keys()
        gemini = GeminiProvider(api_keys=keys) if keys else None

        self._provider = ProviderRouter(gemini_provider=gemini)
        return self._provider

    def _invalidate_provider(self):
        """API 키 변경 시 provider 재생성 강제"""
        self._provider = None

    def _handle_chat_send(self, node_id, model, message, files, prompt_entries=None):
        from v.logger import get_logger
        logger = get_logger("qonvo.plugin")
        logger.info(f"[CHAT_SEND] node={node_id}, model={model}, msg_len={len(message or '')}, files={len(files or [])}, prompts={len(prompt_entries or [])}, active_workers={self._active_workers}")
        node = self.app.nodes.get(node_id)
        if node is None:
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
            self._preferred_results[node_id] = []
            self._preferred_expected[node_id] = pref_count
            node._on_preferred_selected = self._on_chat_preferred_option_selected
            node.set_response(f"생성 중... (0/{pref_count})", done=False)

            for _ in range(pref_count):
                messages = list(prefix_messages) + [ChatMessage(role="user", content=message or "", attachments=files or None)]
                worker = StreamWorker(
                    provider,
                    model,
                    messages,
                    system_prompt=effective_system_prompt,
                    system_files=self.system_files,
                    **options,
                )

                # Preferred 모드: 스트리밍 청크는 노드에 표시하지 않음
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
            messages = list(prefix_messages) + [ChatMessage(role="user", content=message or "", attachments=files or None)]
            worker = StreamWorker(
                provider,
                model,
                messages,
                system_prompt=effective_system_prompt,
                system_files=self.system_files,
                **options,
            )

            worker.chunk_received.connect(lambda text, n=node: n.set_response(text, done=False))
            worker.tokens_received.connect(lambda i, o, n=node: n.set_tokens(i, o))
            worker.signatures_received.connect(lambda sigs, n=node: setattr(n, "thought_signatures", sigs))
            worker.error_signal.connect(lambda err, n=node, w=worker: self._finish_worker(w, lambda: (n.set_response(f"Error: {err}", done=True), self._emit_complete_signal(n))))
            worker.finished_signal.connect(lambda text, n=node, w=worker: self._finish_worker(w, lambda: (n.set_response(text, done=True), self._emit_complete_signal(n))))
            worker.image_received.connect(lambda payload, n=node, w=worker: self._on_image_payload(n, w, payload))

            # Phase 4: 동시 워커 수 제한 (최대 4개)
            if self._active_workers < self._max_concurrent_workers:
                self._start_worker(worker)
            else:
                # 대기열에 추가
                self._pending_workers.append((node, worker))

    def _on_chat_pref_finished(self, node, text, images):
        """Chat Preferred: 단일 워커 완료 (텍스트)"""
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
        """Chat Preferred: 이미지 모델 워커 완료"""
        images = payload.get("images", [])
        text = payload.get("text", "")
        if not images:
            text = text if text else "이미지 생성 실패"
        self._on_chat_pref_finished(node, text, images)

    def _on_chat_pref_error(self, node, error):
        """Chat Preferred: 워커 에러"""
        nid = node.node_id
        if nid not in self._preferred_results:
            return
        self._preferred_results[nid].append((f"Error: {error}", []))
        expected = self._preferred_expected.get(nid, 1)
        if len(self._preferred_results[nid]) >= expected:
            node.show_preferred_results(self._preferred_results[nid])

    def _on_chat_preferred_option_selected(self, node, selections):
        """Chat Preferred: 사용자가 결과를 선택한 후 호출"""
        nid = node.node_id

        if not selections:
            self._preferred_results.pop(nid, None)
            self._preferred_expected.pop(nid, None)
            return

        first_text, first_images = selections[0]
        node.ai_response = first_text

        # 연결된 타겟에 각 선택 결과 전달
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

        # 완료 신호 발송
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

    def _handle_round_table_send(self, node_id, model, message, files):
        """라운드 테이블 전송 핸들러"""
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

        # Phase 4: 동시 워커 수 제한 (최대 4개)
        if self._active_workers < self._max_concurrent_workers:
            self._start_worker(worker)
        else:
            # 대기열에 추가
            self._pending_workers.append((node, worker))

    def _try_candidate_count(self, func_def, input_data, count, parameters,
                             node_options=None, on_job_created=None):
        """
        chat_candidates를 사용하여 Batch API로 다중 결과 생성.

        단순 함수 (Start -> LLM -> End, GetParam만 허용)만 지원.
        복잡한 그래프 (Condition, Loop, TextTransform 등)는 병렬 Worker로 fallback.

        Returns:
            list[(str, list)] — (텍스트, 이미지 리스트) 튜플 리스트
            None — 미지원/실패 시 (복잡한 함수, API 에러 등)
        """
        from v.provider import get_default_options

        # 1. LLM 노드가 정확히 1개인지 확인
        llm_nodes = [n for n in func_def.nodes if n.node_type == "llm_call"]
        if len(llm_nodes) != 1:
            return None

        # 2. 단순 구조 확인 (Start, LLM, End, GetParam만 존재)
        allowed_types = {"start", "end", "llm_call", "get_param"}
        if any(n.node_type not in allowed_types for n in func_def.nodes):
            return None

        llm_node = llm_nodes[0]
        model = llm_node.config.get("model", "")
        is_nanobanana = model in ("gemini-3-pro-image-preview", "gemini-2.5-flash-image")

        # 3. Imagen 제외 (generate_images API 사용, chat 미지원)
        if model.startswith("imagen"):
            return None

        # 4. 프롬프트 빌드 (template 변수 치환)
        template = llm_node.config.get("prompt_template", "{input}")
        prompt = template.replace("{input}", input_data or "")
        for param_name, param_info in parameters.items():
            if param_info.get("type") != "image":
                prompt = prompt.replace(
                    f"{{param:{param_name}}}", str(param_info.get("value", ""))
                )

        # 5. Provider로 병렬 배치 요청
        provider = self._ensure_provider()
        if not provider:
            return None

        image_attachments = []
        for param_name, param_info in parameters.items():
            if param_info.get("type") == "image":
                path = param_info.get("value", "")
                if path and os.path.exists(path):
                    image_attachments.append(path)

        # 모델 옵션: 기본값 + 노드 위젯 설정 (function_engine.py와 동일 패턴)
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
            # 결과를 (text, images) 튜플로 정규화
            normalized = []
            for r in results:
                if isinstance(r, dict):
                    normalized.append((r.get("text", ""), r.get("images", [])))
                else:
                    normalized.append((str(r), []))

            # Nanobanana: 이미지 없는 결과는 실패 처리 → fallback
            if is_nanobanana:
                has_images = any(imgs for _, imgs in normalized)
                if not has_images:
                    return None

            return normalized if normalized else None
        except Exception:
            return None

    def _spawn_pref_workers(self, node, function_id, input_data, count,
                            parameters, node_options, provider):
        """Preferred Options용 병렬 Worker N개 생성 (fallback 방식)"""
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
        """
        함수 노드의 FunctionDefinition 실행.

        Preferred Options 모드 배치 처리 전략:
        1단계: candidate_count (단일 API 호출) — 단순 함수만 적용
        2단계: 부분 결과 + 병렬 Worker fallback — deficit분만 추가 실행
        3단계: 전체 병렬 Worker — 복잡한 함수 또는 candidate_count 완전 실패
        """
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

        # Preferred Options 모드
        pref_enabled = getattr(node, 'preferred_options_enabled', False)
        pref_count = getattr(node, 'preferred_options_count', 3) if pref_enabled else 1

        if pref_enabled:
            self._preferred_results[node_id] = []
            self._preferred_expected[node_id] = pref_count
            node._on_preferred_selected = self._on_preferred_option_selected
            node.set_response(f"실행 중... (0/{pref_count})", done=False)

            # 1단계: 단순 함수면 chat_candidates 배치 시도
            # 2단계: 복잡한 함수거나 실패 시 _spawn_pref_workers fallback
            func_def = copy.deepcopy(self.functions_library[function_id])

            # func_def에서 모델 정보 미리 추출
            llm_nodes_for_cb = [n for n in func_def.nodes if n.node_type == "llm_call"]
            batch_model = llm_nodes_for_cb[0].config.get("model", "") if llm_nodes_for_cb else ""
            batch_is_nano = batch_model in ("gemini-3-pro-image-preview", "gemini-2.5-flash-image")

            batch_worker = _BatchWorker(
                self._try_candidate_count, func_def, input_data, pref_count,
                parameters, node_options=node_options,
            )

            # Batch API 생성 즉시 큐에 저장하는 콜백 (worker 참조 필요)
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
            # 일반 단일 실행 (변경 없음)
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
        """함수 실행 완료"""
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
        """chat_candidates 배치 완료 — 결과를 preferred options로 표시"""
        # 완료된 job을 persistent queue에서 제거
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
        """Preferred Options: 단일 실행 완료"""
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
            # 결과 준비 완료 알림
            notify = getattr(node, 'notify_on_complete', False)
            if notify:
                from .toast_notification import ToastManager
                node_name = getattr(node, 'function_name', None) or f"Node #{node.node_id}"
                main_window = self.view.window() if self.view else None
                ToastManager.instance().show_toast(
                    f"{node_name} - {expected}개 결과 준비됨", main_window
                )

    def _on_pref_run_error(self, node, error):
        """Preferred Options: 실행 에러"""
        nid = node.node_id
        if nid not in self._preferred_results:
            return
        self._preferred_results[nid].append((f"Error: {error}", []))
        expected = self._preferred_expected.get(nid, 1)
        if len(self._preferred_results[nid]) >= expected:
            node.show_preferred_results(self._preferred_results[nid])

    def _on_preferred_option_selected(self, node, selections):
        """사용자가 preferred option(들)을 선택한 후 호출.

        Args:
            node: FunctionNodeWidget
            selections: list of (text, images) tuples — 다중 선택
        """
        nid = node.node_id

        if not selections:
            self._preferred_results.pop(nid, None)
            self._preferred_expected.pop(nid, None)
            return

        # 첫 번째 결과를 노드 응답으로 설정
        first_text, first_images = selections[0]
        node.ai_response = first_text

        all_images = []
        for _, imgs in selections:
            all_images.extend(imgs)

        # 연결된 타겟에 각 선택 결과 전달
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

        # ⚡ 완료 신호 발송 (타겟 전달은 위에서 이미 처리)
        if hasattr(node, 'signal_output_port') and node.signal_output_port is not None:
            self.emit_signal(node.signal_output_port)

        # 완료 알림 (토스트)
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

    def _resume_pending_batches(self):
        """앱 재시작 후 persistent batch queue에서 pending job 폴링 재개"""
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
                # lazy loading 중 미생성 노드 → 강제 생성
                if not self._force_materialize_by_id(node_id):
                    logger.warning(f"[BATCH_RESUME] Node {node_id} not found, removing job {job['job_name']}")
                    mgr.remove_job(job["job_name"])
                    continue
                node = self.app.nodes.get(node_id)
                if node is None:
                    mgr.remove_job(job["job_name"])
                    continue

            # preferred results 추적 초기화
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
        """Batch resume 실패 — 큐에서 제거하고 노드에 에러 표시"""
        from v.batch_queue import BatchQueueManager
        BatchQueueManager().remove_job(job_name)
        node.set_response("배치 만료/실패 — 다시 실행해주세요", done=True)
        nid = node.node_id
        self._preferred_results.pop(nid, None)
        self._preferred_expected.pop(nid, None)
        logger.warning(f"[BATCH_RESUME] Failed: {job_name} (node {nid})")

    def _on_image_payload(self, node, worker, payload):
        """이미지 생성 응답(payload)을 처리해 노드에 결과를 반영한다."""
        from v.logger import get_logger
        logger = get_logger("qonvo.plugin")
        # payload에서 이미지/텍스트/노드 ID를 추출
        images = payload.get("images", [])
        text = payload.get("text", "")
        nid = getattr(node, 'node_id', '?')
        logger.info(f"[IMAGE_PAYLOAD] node={nid}, images={len(images)}, text_len={len(text)}")
        try:
            if not images:
                # 이미지가 없으면 오류 메시지로 응답 처리
                error_msg = text if text else "이미지 생성 실패"
                node.set_response(error_msg, done=True)
                self._finish_worker(worker)
                self._emit_complete_signal(node)
                return
            # 토큰 사용량을 노드에 기록
            node.set_tokens(payload.get("prompt_tokens") or 0, payload.get("candidates_tokens") or 0)
            # 이미지 결과와 부가 정보(사고 서명)를 노드에 반영
            node.set_image_response(text, images, payload.get("thought_signatures", []))
            self._finish_worker(worker)
            self._emit_complete_signal(node, images)
        except Exception as e:
            # 처리 중 오류 발생 시 로깅하고 가능한 범위에서 노드에 전달
            logger.error(f"[IMAGE_PAYLOAD] node={nid} crashed: {e}", exc_info=True)
            try:
                node.set_response(f"Error: {e}", done=True)
            except Exception:
                pass
            self._finish_worker(worker)

    def _start_worker(self, worker):
        """워커 시작 (동시 실행 수 추적)"""
        self._active_workers += 1
        self._workers.append(worker)
        try:
            worker.start()
        except Exception:
            self._active_workers = max(0, self._active_workers - 1)
            if worker in self._workers:
                self._workers.remove(worker)

    def _finish_worker(self, worker, post=None):
        """워커 종료 및 대기 중인 워커 시작

        Phase 4: 워커 완료 시 대기 중인 다음 워커 시작
        W1: _finished 플래그로 중복 호출 방지 (error_signal + all_finished 동시 fire 대응)
        post() 예외가 발생해도 워커 정리는 반드시 수행.
        """
        from v.logger import get_logger
        logger = get_logger("qonvo.plugin")

        # W1: 중복 호출 방지
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

        # 활성 워커 카운트 감소
        if self._active_workers > 0:
            self._active_workers -= 1

        # 대기 중인 워커 시작
        if self._pending_workers and self._active_workers < self._max_concurrent_workers:
            next_node, next_worker = self._pending_workers.pop(0)
            self._start_worker(next_worker)

    def _on_button_signal(self, node_id):
        """버튼 클릭 시 신호 전달 - signal_output_port를 통해 emit_signal 호출"""
        button_node = self.app.nodes.get(node_id)
        if button_node is None:
            return
        if not hasattr(button_node, 'signal_output_port') or button_node.signal_output_port is None:
            return
        button_data = getattr(button_node, 'input_data', None)
        self.emit_signal(button_node.signal_output_port, data=button_data)

    def open_system_prompt_dialog(self):
        # Keep compatibility with view double-click path.
        pass

    def collect_data(self):
        data = {
            "type": "WhiteBoard",
            "nodes": [],
            "function_nodes": [],
            "round_tables": [],

            "buttons": [],
            "repository_nodes": [],
            "functions_library": [],
            "edges": [],
            "pins": [],
            "texts": [],
            "sticky_notes": [],
            "prompt_nodes": [],
            "image_cards": [],
            "checklists": [],
            "group_frames": [],
            "dimensions": [],
            "next_id": self.app._next_id,
            "system_prompt": self.system_prompt,
            "system_files": list(self.system_files),
        }

        for node in self.app.nodes.values():
            if isinstance(node, ChatNodeWidget):
                data["nodes"].append(node.get_data())
            elif isinstance(node, FunctionNodeWidget):
                data["function_nodes"].append(node.get_data())
            elif isinstance(node, RoundTableWidget):
                data["round_tables"].append(node.get_data())
            elif isinstance(node, PromptNodeWidget):
                data["prompt_nodes"].append(node.get_data())
            elif isinstance(node, StickyNoteWidget):
                data["sticky_notes"].append(node.get_data())
            elif isinstance(node, ButtonNodeWidget):
                data["buttons"].append(node.to_dict())
            elif isinstance(node, ChecklistWidget):
                data["checklists"].append(node.get_data())
            elif isinstance(node, RepositoryNodeWidget):
                data["repository_nodes"].append(node.get_data())
            elif isinstance(node, TextItem):
                data["texts"].append({
                    "id": node.node_id,
                    "x": node.pos().x(),
                    "y": node.pos().y(),
                    "text": node.toPlainText(),
                    "font_size": getattr(node, "_font_size", 16),
                    "rotation": node.rotation(),
                })
            elif isinstance(node, GroupFrameItem):
                data["group_frames"].append({
                    "id": node.node_id,
                    "x": node.pos().x(),
                    "y": node.pos().y(),
                    "width": node.rect().width(),
                    "height": node.rect().height(),
                    "label": node._label.toPlainText() if hasattr(node, "_label") else "",
                    "color": getattr(node, "color_name", "blue"),
                    "locked": getattr(node, "_locked", False),
                })

        for card in self.image_card_items.values():
            data["image_cards"].append(card.get_data())
        for dim in self.dimension_items.values():
            data["dimensions"].append(dim.get_data())

        from v.logger import get_logger
        logger = get_logger("qonvo.plugin")

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

        # 함수 라이브러리 저장
        for func_id, func_def in self.functions_library.items():
            data["functions_library"].append(func_def.to_dict())

        # Lazy loading: 미생성 pending 아이템 + 엣지 포함
        # deepcopy 필수: board.py가 image_path를 archive 이름으로 덮어쓰면
        # 원본 pending dict까지 변형되어 다음 save에서 경로 유실됨
        if self._lazy_mgr.has_pending():
            pending = self._lazy_mgr.get_all_pending_data()
            for category, rows in pending.items():
                if category not in data:
                    data[category] = []
                data[category].extend(copy.deepcopy(rows))
            data["edges"].extend(self._lazy_mgr.get_pending_edges())

        # 사용된 플러그인 모델 수집
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

    # ── Copy / Paste / Cut ──

    _ID_KEY_MAP = {
        "nodes": "id", "function_nodes": "id", "round_tables": "id",
        "repository_nodes": "id", "texts": "id",
        "group_frames": "id", "sticky_notes": "node_id", "prompt_nodes": "node_id",
        "buttons": "node_id", "checklists": "node_id", "image_cards": "node_id",
        "dimensions": "node_id",
    }

    def _categorize_selected_item(self, item):
        """선택된 씬 아이템 → (category, node_id, data_dict) 또는 None."""
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
            elif isinstance(widget, ButtonNodeWidget):
                return ("buttons", node_id, widget.to_dict())
            elif isinstance(widget, RoundTableWidget):
                return ("round_tables", node_id, widget.get_data())
            elif isinstance(widget, ChecklistWidget):
                return ("checklists", node_id, widget.get_data())
            elif isinstance(widget, RepositoryNodeWidget):
                return ("repository_nodes", node_id, widget.get_data())
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
        """선택된 아이템 + 내부 엣지를 클립보드에 복사."""
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

        # 양쪽 모두 선택된 엣지만 포함
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

        global _global_clipboard, _global_paste_offset
        _global_clipboard = {"items": items_data, "edges": edges_data}
        _global_paste_offset = 0
        logger.info(f"[COPY] {len(items_data)} items, {len(edges_data)} edges")

    def paste_clipboard(self):
        """클립보드 아이템을 새 ID/오프셋으로 붙여넣기."""
        global _global_clipboard, _global_paste_offset
        if not _global_clipboard or not self.scene:
            return

        _global_paste_offset += 1
        offset = 50 * _global_paste_offset

        items = copy.deepcopy(_global_clipboard["items"])
        edges = copy.deepcopy(_global_clipboard["edges"])

        # old_id → new_id 매핑
        id_remap = {}
        for category, old_id, row in items:
            new_id = self._next_id()
            id_remap[old_id] = new_id
            id_key = self._ID_KEY_MAP.get(category, "id")
            row[id_key] = new_id
            row["x"] = row.get("x", 0) + offset
            row["y"] = row.get("y", 0) + offset

        self.scene.clearSelection()

        # 아이템 생성
        for category, old_id, row in items:
            new_id = id_remap[old_id]
            self._materialize_single(category, new_id, row)

        # 포트 재배치
        self._reposition_all_ports()
        self._invalidate_all_port_caches()

        # 엣지 ID 리매핑 + 복원
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

        # 새 아이템 선택
        for old_id in id_remap:
            new_id = id_remap[old_id]
            owner = self._owner_by_id(new_id)
            if owner is not None:
                owner.setSelected(True)

        self._manual_update_all_edges()
        self._notify_modified()
        logger.info(f"[PASTE] {len(items)} items, {len(edges)} edges at offset +{offset}")

    def cut_selected(self):
        """잘라내기 = 복사 + 삭제."""
        self.copy_selected()
        if _global_clipboard and self.view:
            self.view._delete_selected_items()

    # ── 차원 간 이동 ──────────────────────────────────────────

    def _delete_item_by_scene_item(self, item):
        """단일 씬 아이템 삭제 (차원 이동 시 원본 제거용)."""
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
        """선택 아이템을 대상 DimensionItem 내부로 이동."""
        board_data = target_dimension.get_board_data()
        for item in list(scene_items):
            result = self._categorize_selected_item(item)
            if not result:
                continue
            category, node_id, data = result
            data = copy.deepcopy(data)
            id_key = self._ID_KEY_MAP.get(category, "id")
            new_id = board_data.get("next_id", 1)
            data[id_key] = new_id
            board_data["next_id"] = new_id + 1
            # 차원 내부 중앙에 배치
            data["x"] = target_dimension._width / 2 - 50
            data["y"] = target_dimension._height / 2
            board_data.setdefault(category, []).append(data)
        target_dimension.set_board_data(board_data)
        # 현재 씬에서 삭제
        for item in list(scene_items):
            self._delete_item_by_scene_item(item)
        self._notify_modified()
        logger.info(f"[DIM-MOVE] {len(scene_items)} items -> dimension #{getattr(target_dimension, 'node_id', '?')}")

    def move_items_to_parent(self, scene_items):
        """선택 아이템을 상위 차원(부모 플러그인)으로 이동."""
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
            # 부모의 DimensionItem 옆에 배치
            dim = self._parent_dimension_item
            if dim:
                data["x"] = dim.pos().x() + dim._width + 30
                data["y"] = dim.pos().y()
            self._parent_plugin._materialize_single(category, new_id, data)
        # 현재 씬에서 삭제
        for item in list(scene_items):
            self._delete_item_by_scene_item(item)
        self._parent_plugin._reposition_all_ports()
        self._parent_plugin._invalidate_all_port_caches()
        self._parent_plugin._manual_update_all_edges()
        self._parent_plugin._notify_modified()
        self._notify_modified()
        logger.info(f"[DIM-MOVE] {len(scene_items)} items -> parent dimension")

    def _get_scene_viewport_rect(self) -> QRectF:
        """현재 뷰포트의 씬 좌표 영역 반환."""
        if self.view is None:
            return QRectF(-500, -500, 1000, 1000)
        vp = self.view.viewport().rect()
        tl = self.view.mapToScene(vp.topLeft())
        br = self.view.mapToScene(vp.bottomRight())
        return QRectF(tl, br).normalized()

    def restore_data(self, data):
        # 플러그인 의존성 확인
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

        # 보드별 temp 경로를 ChatNodeWidget에 전달
        if self._board_name:
            from v.board import BoardManager
            _temp = BoardManager.get_boards_dir() / '.temp' / self._board_name / 'attachments'
            _temp.mkdir(parents=True, exist_ok=True)
            from .chat_node import ChatNodeWidget
            from .items import ImageCardItem
            ChatNodeWidget._board_temp_dir = str(_temp)
            ImageCardItem._board_temp_dir = str(_temp)

        # === clear 로직 (기존 그대로) ===
        for edge in list(self._edges):
            self.remove_edge(edge)
        if self.scene:
            for d in (self.proxies, self.function_proxies, self.round_table_proxies,
                      self.sticky_proxies, self.prompt_proxies, self.button_proxies,
                      self.checklist_proxies, self.repository_proxies):
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
            for proxy in list(self.button_proxies.values()):
                self.scene.removeItem(proxy)
            for proxy in list(self.checklist_proxies.values()):
                self.scene.removeItem(proxy)
            for proxy in list(self.repository_proxies.values()):
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
        self.button_proxies.clear()
        self.checklist_proxies.clear()

        self.repository_proxies.clear()
        self.text_items.clear()
        self.group_frame_items.clear()
        self.image_card_items.clear()
        self.dimension_items.clear()
        self.app.nodes.clear()

        self.system_prompt = data.get("system_prompt", "")
        self.system_files = data.get("system_files", [])

        # 함수 라이브러리 로드
        self.functions_library = {}
        for func_data in data.get("functions_library", []):
            func_def = FunctionDefinition.from_dict(func_data)
            self.functions_library[func_def.function_id] = func_def

        # === Lazy Loading (배치 분할) ===
        self._lazy_mgr.reset()
        self._lazy_mgr.ingest_data(data)
        self.app._next_id = max(self.app._next_id, data.get("next_id", self.app._next_id))

        viewport_rect = self._get_scene_viewport_rect()
        visible = self._lazy_mgr.query_visible(viewport_rect)

        # 배치 큐에 넣고 점진적 로딩 시작
        self._batch_queue = list(visible)
        self._batch_loading = True
        self._process_batch()

    # ── Lazy Loading: 배치 분할 materialize ──

    def _process_batch(self):
        """배치 큐에서 N개씩 꺼내 생성. 남으면 QTimer로 다음 배치 예약."""
        from v.constants import LAZY_LOAD_BATCH_SIZE

        if not self._batch_queue:
            self._finalize_batch_loading()
            return

        batch = self._batch_queue[:LAZY_LOAD_BATCH_SIZE]
        self._batch_queue = self._batch_queue[LAZY_LOAD_BATCH_SIZE:]

        for category, node_id, row in batch:
            if node_id in self._lazy_mgr._materialized_ids:
                continue  # 이미 생성된 아이템 스킵 (중복 방지)
            self._materialize_single(category, node_id, row)
            self._lazy_mgr.mark_materialized(node_id, category)

        # 배치마다 엣지 해결 시도
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
        """배치 로딩 완료 — 최종 포트/엣지 정리."""
        self._batch_loading = False
        self._reposition_all_ports()
        self._invalidate_all_port_caches()

        resolvable = self._lazy_mgr.get_resolvable_edges()
        for edge_data in resolvable:
            self._restore_edge(edge_data)
        self._manual_update_all_edges()

        logger.info(f"[LAZY] Initial load complete: {len(self._lazy_mgr._materialized_ids)} materialized, "
                     f"{sum(len(d) for d in self._lazy_mgr._pending.values())} pending")

        # Persistent batch queue에서 pending job 폴링 재개
        self._resume_pending_batches()

    def _materialize_single(self, category: str, node_id: int, row: dict):
        """단일 아이템 카테고리별 생성."""
        dispatch = {
            "nodes": self._materialize_chat_node,
            "function_nodes": self._materialize_function_node,
            "sticky_notes": self._materialize_sticky_note,
            "prompt_nodes": self._materialize_prompt_node,
            "buttons": self._materialize_button,
            "round_tables": self._materialize_round_table,

            "checklists": self._materialize_checklist,
            "repository_nodes": self._materialize_repository_node,
            "texts": self._materialize_text,
            "group_frames": self._materialize_group_frame,
            "image_cards": self._materialize_image_card,
            "dimensions": self._materialize_dimension,
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
        # 모델 먼저 설정 (_on_model_changed가 스피너를 기본값으로 리셋하므로)
        saved_model = row.get("model", "")
        if saved_model:
            idx = node.model_combo.findData(saved_model)
            if idx >= 0:
                node.model_combo.setCurrentIndex(idx)
        # 옵션 복원 (모델 변경 후 기본값 위에 덮어씀)
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
        # extra input ports 복원
        for port_def in row.get("extra_input_defs", []):
            self._add_chat_input_port(node, port_def["type"], port_def.get("name"))

        # History restore
        node._history = row.get("history", [])
        # Backward compat: no history but has ai_response -> create single entry
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
        node._running = False  # Always idle on load
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
            node.set_response(row.get("ai_response"), done=True)

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

    def _materialize_button(self, row):
        proxy = self.add_button(QPointF(row.get("x", 0), row.get("y", 0)), node_id=row.get("node_id"))
        if proxy is None:
            return
        node = proxy.widget()
        # U5: 버튼 크기 복원
        if row.get("width") and row.get("height"):
            node.setFixedSize(int(row["width"]), int(row["height"]))
        node.click_count = row.get("click_count", 0)
        node.counter_label.setText(f"click: {node.click_count}")
        node.input_data = row.get("input_data")

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

    def _materialize_dimension(self, row):
        item = DimensionItem.from_data(row)
        node_id = self._next_id(row.get("node_id"))
        item.node_id = node_id
        item.on_double_click = self._open_dimension_board
        self.scene.addItem(item)
        self.dimension_items[node_id] = item
        self.app.nodes[node_id] = item
        self._attach_item_ports(item, PortItem.TYPE_STRING, PortItem.TYPE_STRING)

    def _materialize_visible_items(self):
        """뷰포트 변경 시 호출 — 새로 보이는 pending 아이템을 배치 큐에 추가."""
        if not self._lazy_mgr.has_pending():
            self._lazy_mgr._active = False
            return

        visible = self._lazy_mgr.query_visible(self._get_scene_viewport_rect())
        if not visible:
            return

        # 이미 배치 로딩 중이면 큐에 추가, 아니면 새 배치 시작
        if self._batch_loading:
            # 큐에 이미 있는 ID 제외 (중복 방지)
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
        """시그널/검색용 강제 생성."""
        result = self._lazy_mgr.get_pending_item_by_id(node_id)
        if result is None:
            return False
        category, row = result
        self._materialize_single(category, node_id, row)
        self._lazy_mgr.mark_materialized(node_id, category)

        # 관련 포트 재배치 + 캐시
        owner = self._owner_by_id(node_id)
        if owner:
            widget = owner.widget() if isinstance(owner, QGraphicsProxyWidget) else owner
            if hasattr(widget, 'reposition_ports'):
                widget.reposition_ports()
            self._invalidate_node_port_caches(widget)

        # 엣지 해결 시도
        resolvable = self._lazy_mgr.get_resolvable_edges()
        for edge_data in resolvable:
            self._restore_edge(edge_data)
        if resolvable:
            self._manual_update_all_edges()

        return True
    def _resolve_port(self, owner, name: str, output: bool):
        from v.logger import get_logger
        logger = get_logger("qonvo.plugin")

        if owner is None:
            return None
        if isinstance(owner, QGraphicsProxyWidget):
            owner = owner.widget()

        direction = "OUTPUT" if output else "INPUT"
        logger.debug(f"[RESOLVE PORT] Looking for {direction} port named '{name}'")

        if output:
            # 먼저 일반 출력 포트에서 찾기 (다중 포트)
            if hasattr(owner, "output_ports") and isinstance(owner.output_ports, dict):
                if name in owner.output_ports:
                    logger.debug(f"[RESOLVE PORT] Found in output_ports dict: {name}")
                    return owner.output_ports[name]
            # 단일 출력 포트 - 이름이 일치할 때만 반환 (_default 포트는 항상 반환)
            port = getattr(owner, "output_port", None)
            if port is not None:
                port_name = getattr(port, "port_name", "?")
                if name == "_default" or (hasattr(port, "port_name") and port.port_name == name):
                    logger.debug(f"[RESOLVE PORT] Found output_port: {port_name}")
                    return port
                else:
                    logger.debug(f"[RESOLVE PORT] output_port name mismatch: requested={name}, actual={port_name}")
            # Boolean 신호 출력 포트 (⚡ 완료, ⚡ 신호 등)
            port = getattr(owner, "signal_output_port", None)
            if port is not None and hasattr(port, "port_name") and port.port_name == name:
                logger.debug(f"[RESOLVE PORT] Found signal_output_port: {port.port_name}")
                return port
            logger.debug(f"[RESOLVE PORT] OUTPUT port '{name}' NOT FOUND")
            return None
        # 입력 포트 검색
        # 먼저 일반 입력 포트에서 찾기 (다중 포트)
        if hasattr(owner, "input_ports") and isinstance(owner.input_ports, dict):
            if name in owner.input_ports:
                logger.debug(f"[RESOLVE PORT] Found in input_ports dict: {name}")
                return owner.input_ports[name]
        # 단일 입력 포트 - 이름이 일치할 때만 반환 (_default 포트는 항상 반환)
        port = getattr(owner, "input_port", None)
        if port is not None:
            port_name = getattr(port, "port_name", "?")
            if name == "_default" or (hasattr(port, "port_name") and port.port_name == name):
                logger.debug(f"[RESOLVE PORT] Found input_port: {port_name}")
                return port
            else:
                logger.debug(f"[RESOLVE PORT] input_port name mismatch: requested={name}, actual={port_name}")
        # Boolean 신호 입력 포트 (⚡ 실행 등)
        port = getattr(owner, "signal_input_port", None)
        if port is not None and hasattr(port, "port_name") and port.port_name == name:
            logger.debug(f"[RESOLVE PORT] Found signal_input_port: {port.port_name}")
            return port
        logger.debug(f"[RESOLVE PORT] INPUT port '{name}' NOT FOUND")
        return None

    def _owner_by_id(self, node_id: int):
        if node_id in self.proxies:
            return self.proxies[node_id]
        if node_id in self.function_proxies:
            return self.function_proxies[node_id]
        if node_id in self.round_table_proxies:
            return self.round_table_proxies[node_id]
        if node_id in self.sticky_proxies:
            return self.sticky_proxies[node_id]
        if node_id in self.prompt_proxies:
            return self.prompt_proxies[node_id]
        if node_id in self.button_proxies:
            return self.button_proxies[node_id]
        if node_id in self.checklist_proxies:
            return self.checklist_proxies[node_id]
        if node_id in self.repository_proxies:
            return self.repository_proxies[node_id]
        if node_id in self.image_card_items:
            return self.image_card_items[node_id]
        if node_id in self.dimension_items:
            return self.dimension_items[node_id]
        return None

    def _restore_edge(self, row):
        from v.logger import get_logger
        logger = get_logger("qonvo.plugin")

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
        """Phase 4: 모든 포트의 위치 캐시 초기화

        보드 로드 시 포트 위치 캐시를 무효화하여 엣지 복원 시
        올바른 포트 위치를 다시 계산하도록 함
        """
        from v.logger import get_logger
        logger = get_logger("qonvo.plugin")

        invalidated_count = 0

        # 모든 프록시 위젯의 포트 캐시 무효화
        for proxy in self.proxies.values():
            node = proxy.widget()
            if node:
                invalidated_count += self._invalidate_node_port_caches(node)

        # 함수 노드
        for proxy in self.function_proxies.values():
            node = proxy.widget()
            if node:
                invalidated_count += self._invalidate_node_port_caches(node)

        # 라운드 테이블
        for proxy in self.round_table_proxies.values():
            node = proxy.widget()
            if node:
                invalidated_count += self._invalidate_node_port_caches(node)

        # 스티키 노트
        for proxy in self.sticky_proxies.values():
            node = proxy.widget()
            if node:
                invalidated_count += self._invalidate_node_port_caches(node)

        # 프롬프트 노드
        for proxy in self.prompt_proxies.values():
            node = proxy.widget()
            if node:
                invalidated_count += self._invalidate_node_port_caches(node)

        # 버튼 노드
        for proxy in self.button_proxies.values():
            node = proxy.widget()
            if node:
                invalidated_count += self._invalidate_node_port_caches(node)

        # 체크리스트
        for proxy in self.checklist_proxies.values():
            node = proxy.widget()
            if node:
                invalidated_count += self._invalidate_node_port_caches(node)

        # 자료함 노드
        for proxy in self.repository_proxies.values():
            node = proxy.widget()
            if node:
                invalidated_count += self._invalidate_node_port_caches(node)

        logger.info(f"[CACHE INVALIDATE] Invalidated {invalidated_count} port caches during board load")

    def _invalidate_node_port_caches(self, node) -> int:
        """노드의 모든 포트 캐시 무효화, 무효화된 포트 개수 반환"""
        count = 0
        if hasattr(node, 'iter_ports'):
            try:
                for port in node.iter_ports():
                    if hasattr(port, '_invalidate_cache'):
                        port._invalidate_cache()
                        count += 1
            except Exception:
                pass  # 포트 순회 실패 무시
        return count

    def _reposition_all_ports(self):
        """Phase 4: 모든 노드의 포트 위치 재설정

        보드 로드 시 모든 노드의 포트를 재배치하여
        포트의 scenePos()가 올바른 값을 반환하도록 함
        """
        from v.logger import get_logger
        logger = get_logger("qonvo.plugin")

        reposition_count = 0

        # 모든 프록시 위젯의 포트 재배치
        for proxy in self.proxies.values():
            node = proxy.widget()
            if node and hasattr(node, 'reposition_ports'):
                try:
                    node.reposition_ports()
                    reposition_count += 1
                except Exception:
                    pass

        # 함수 노드
        for proxy in self.function_proxies.values():
            node = proxy.widget()
            if node and hasattr(node, 'reposition_ports'):
                try:
                    node.reposition_ports()
                    reposition_count += 1
                except Exception:
                    pass

        # 라운드 테이블
        for proxy in self.round_table_proxies.values():
            node = proxy.widget()
            if node and hasattr(node, 'reposition_ports'):
                try:
                    node.reposition_ports()
                    reposition_count += 1
                except Exception:
                    pass

        # 스티키 노트
        for proxy in self.sticky_proxies.values():
            node = proxy.widget()
            if node and hasattr(node, 'reposition_ports'):
                try:
                    node.reposition_ports()
                    reposition_count += 1
                except Exception:
                    pass

        # 프롬프트 노드
        for proxy in self.prompt_proxies.values():
            node = proxy.widget()
            if node and hasattr(node, 'reposition_ports'):
                try:
                    node.reposition_ports()
                    reposition_count += 1
                except Exception:
                    pass

        # 버튼 노드
        for proxy in self.button_proxies.values():
            node = proxy.widget()
            if node and hasattr(node, 'reposition_ports'):
                try:
                    node.reposition_ports()
                    reposition_count += 1
                except Exception:
                    pass

        # 체크리스트
        for proxy in self.checklist_proxies.values():
            node = proxy.widget()
            if node and hasattr(node, 'reposition_ports'):
                try:
                    node.reposition_ports()
                    reposition_count += 1
                except Exception:
                    pass

        # 자료함 노드
        for proxy in self.repository_proxies.values():
            node = proxy.widget()
            if node and hasattr(node, 'reposition_ports'):
                try:
                    node.reposition_ports()
                    reposition_count += 1
                except Exception:
                    pass

        logger.info(f"[PORT REPOSITION] Repositioned {reposition_count} nodes' ports during board load")
