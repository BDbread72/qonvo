"""
화이트보드 뷰
- WhiteboardView: 줌/팬/선택/방사형 메뉴를 지원하는 QGraphicsView
"""
import math

from PyQt6.QtWidgets import (
    QGraphicsView, QGraphicsProxyWidget, QLabel, QMessageBox,
)
from PyQt6.QtCore import Qt, QPointF, QRectF, QTimer
from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QColor, QPixmap,
    QWheelEvent, QMouseEvent, QKeyEvent, QCursor
)

from .items import PinItem, TextItem, PortItem, TempEdgeItem, EdgeItem, ImageCardItem, GroupFrameItem, FileNodeItem
from .dimension_item import DimensionItem
# from .minimap import BranchGraphWidget  # 미니맵 제거(미사용)
from .radial_menu import RadialMenu
from .search_bar import SearchBarWidget
from v.theme import Theme


def _strip_chat_code(text: str, error: bool = False):
    """초간단 §색코드 해석. 반환 (color, text). error 면 빨강 우선."""
    text = text or ""
    if text.startswith("§e"):
        text = text[2:]
        return ("#ff8888" if error else "#e6c200"), text
    if text.startswith("§"):
        text = text[1:]
    return ("#ff8888" if error else "#7fd88f"), text


class WhiteboardView(QGraphicsView):
    """줌/팬 가능한 화이트보드 뷰"""

    def __init__(self, scene, plugin=None):
        super().__init__(scene)
        self.plugin = plugin
        self.radial_menu = None
        self._menu_center = None  # 메뉴 중심 (뷰포트 좌표)
        self._menu_scene_pos = None  # 메뉴 중심 (씬 좌표)
        self._original_cursor_pos = None  # 원래 마우스 위치 (복원용)
        self._tab_held = False
        self._space_held = False
        from v.settings import get_setting
        self._toggle_mode = get_setting("toggle_mode", False)
        self._wire_opacity = 0.0  # 포트/엣지 페이드 현재 값
        self._wire_fade_target = 0.0
        self._current_category = None  # 현재 선택된 카테고리 (계층형 메뉴용)
        self._original_scene_pos = None  # 노드 추가 위치 저장
        self._submenu_opened = False  # 서브메뉴가 방금 열렸는지 플래그

        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)
        # ⚠️ DontAdjustForAntialiasing 는 켜지 말 것 — 켜면 Qt 가 dirty 영역을 AA(2px) 만큼
        # 넓히지 않아서, 안티앨리어싱된 아이템(라이브 커서·노드·엣지·점선 선택박스)이
        # 움직일 때 옛 위치의 가장자리 픽셀이 안 지워져 '잔상'이 남는다. 페인터 상태 절약만 유지.
        self.setOptimizationFlag(QGraphicsView.OptimizationFlag.DontSavePainterState)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._panning = False
        self._pan_start = QPointF()
        self._pan_scroll_start_h = 0
        self._pan_scroll_start_v = 0
        self._right_click_origin = None
        self._zoom = 1.0

        # 다중 선택 (러버밴드)
        self._selecting = False
        self._selection_start = None  # 씬 좌표
        self._selection_rect = None   # 씬 좌표 QRectF
        self._selection_add_mode = False  # Ctrl 누른 상태로 시작했는지

        # 포트 드래그 연결
        self._port_dragging = False
        self._drag_source_port = None  # 드래그 시작 PortItem
        self._temp_edge = None         # TempEdgeItem
        self._drag_reverse = False     # 입력 포트에서 역방향 드래그

        # 포커스 받을 수 있게
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)  # 마우스 이동 항상 추적
        self.setAcceptDrops(True)  # 외부 파일 드래그 앤 드롭
        self._last_report_pos = None   # 라이브 커서 마지막 보고 위치(상태만 바뀔 때 재사용)
        self._last_poll_xy = None
        # 라이브 커서 폴링: mouseMoveEvent 는 빈 캔버스에서만 오므로(노드 위에선 위젯이 먹음)
        # 전역 커서를 주기적으로 읽어 보고 → 노드 위에 있어도 상대 화면에서 커서가 따라옴
        from PyQt6.QtCore import QTimer
        self._cursor_poll_timer = QTimer(self)
        self._cursor_poll_timer.setInterval(120)
        self._cursor_poll_timer.timeout.connect(self._poll_report_cursor)
        self._cursor_poll_timer.start()
        # 앱 비활성(다른 창으로 alt-tab) → 커서 'away' 표시
        try:
            from PyQt6.QtWidgets import QApplication
            _qa = QApplication.instance()
            if _qa is not None:
                _qa.applicationStateChanged.connect(lambda *_: self._report_cursor_state())
        except Exception:
            pass

        # 브랜치 그래프(미니맵) — 제거됨(미사용). 참조부는 hasattr 가드로 안전 무시
        # self._branch_graph = BranchGraphWidget(self)

        # 검색 바
        self._search_bar = SearchBarWidget(self)

        # 애니메이션 타이머
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._animate_menu)
        from v.constants import ANIMATION_INTERVAL_MS
        self._anim_timer.setInterval(ANIMATION_INTERVAL_MS)  # ~60fps

        # 와이어 페이드 타이머
        self._wire_fade_timer = QTimer(self)
        self._wire_fade_timer.timeout.connect(self._wire_fade_tick)
        from v.constants import WIRE_FADE_INTERVAL_MS
        self._wire_fade_timer.setInterval(WIRE_FADE_INTERVAL_MS)

        # 포트 아이템 컬렉션 (전체 씬 순회 대신 사용)
        self._all_port_items: set = set()

        # 텍스트 선택 추적 (O(N) 씬 스캔 방지)
        self._items_with_text_sel: set = set()
        self._may_have_text_sel = False

        # 스냅 엔진 초기화
        from .snap_engine import SnapEngine
        scene._snap_engine = SnapEngine(scene)

        # 도트 그리드 타일 캐시
        self._dot_tile = None
        self._dot_tile_theme = None

        # 초기화 후 포트 opacity 초기화 (안 보이는 상태)
        QTimer.singleShot(100, self._init_port_visibility)

    # ── 외부 파일 드래그 앤 드롭 ──

    _IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'}

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if not self.plugin or not event.mimeData().hasUrls():
            event.ignore()
            return
        from pathlib import Path
        base_pos = self.mapToScene(event.position().toPoint())
        offset = 0
        other_files = []
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.suffix.lower() in self._IMAGE_EXTENSIONS:
                drop_pos = QPointF(base_pos.x() + offset, base_pos.y() + offset)
                self.plugin.add_image_card(str(path), drop_pos)
                offset += 30
            elif path.is_file():
                other_files.append(str(path))
        # 이미지가 아닌 파일들은 하나의 파일 노드로 묶어 배치
        if other_files:
            drop_pos = QPointF(base_pos.x() + offset, base_pos.y() + offset)
            self.plugin.add_file_node(other_files, drop_pos)
        event.acceptProposedAction()

    def focusNextPrevChild(self, next):
        """TAB 키로 포커스 이동 방지"""
        # TAB 키 이벤트를 여기서 가로챔
        return False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_branch_graph'):
            self._branch_graph.reposition()
        if hasattr(self, '_search_bar') and self._search_bar.isVisible():
            self._search_bar.reposition()

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self._notify_viewport_changed()

    def _notify_viewport_changed(self):
        """뷰포트 변경 시 lazy loader에 알림."""
        if self.plugin and hasattr(self.plugin, '_lazy_mgr'):
            self.plugin._lazy_mgr.schedule_check()

    def wheelEvent(self, event: QWheelEvent):
        if self.radial_menu:
            event.accept()
            return

        mods = event.modifiers()
        if mods & Qt.KeyboardModifier.ControlModifier:
            from v.constants import ZOOM_FACTOR, ZOOM_MIN, ZOOM_MAX
            factor = ZOOM_FACTOR if event.angleDelta().y() > 0 else 1 / ZOOM_FACTOR

            new_zoom = self._zoom * factor
            if ZOOM_MIN <= new_zoom <= ZOOM_MAX:
                self._zoom = new_zoom
                self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
                self.scale(factor, factor)
                self._notify_viewport_changed()
        else:
            pd = event.pixelDelta()
            if not pd.isNull():
                dx, dy = pd.x(), pd.y()
            else:
                dx, dy = event.angleDelta().x(), event.angleDelta().y()
            if mods & Qt.KeyboardModifier.ShiftModifier:
                dx, dy = dy, 0
            hs = self.horizontalScrollBar()
            vs = self.verticalScrollBar()
            hs.setValue(hs.value() - dx)
            vs.setValue(vs.value() - dy)
            self._notify_viewport_changed()

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        """더블클릭: 원점 → 시스템 프롬프트 다이얼로그"""
        if event.button() == Qt.MouseButton.LeftButton and self.plugin:
            scene_pos = self.mapToScene(event.pos())
            items_at = self.scene().items(scene_pos)
            origin = getattr(self.plugin, '_origin_item', None)
            if origin and origin in items_at:
                self.plugin.open_system_prompt_dialog()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def _get_dot_tile(self) -> QPixmap:
        """도트 그리드 타일 생성/캐싱 (테마 변경 시만 재생성)"""
        theme_key = (Theme.BG_PRIMARY, Theme.GRID_DOT)
        if self._dot_tile is not None and self._dot_tile_theme == theme_key:
            return self._dot_tile
        from v.constants import GRID_SIZE, DOT_SIZE
        tile = QPixmap(GRID_SIZE, GRID_SIZE)
        tile.fill(QColor(Theme.BG_PRIMARY))
        p = QPainter(tile)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(Theme.GRID_DOT)))
        p.drawEllipse(QPointF(0, 0), DOT_SIZE, DOT_SIZE)
        p.end()
        self._dot_tile = tile
        self._dot_tile_theme = theme_key
        return tile

    def drawBackground(self, painter: QPainter, rect: QRectF):
        """배경에 도트 그리드 그리기 (타일 기반)"""
        painter.fillRect(rect, QColor(Theme.BG_PRIMARY))

        from v.constants import GRID_SIZE
        tile = self._get_dot_tile()

        # 타일 원점을 그리드에 맞춤
        left = int(rect.left()) - (int(rect.left()) % GRID_SIZE)
        top = int(rect.top()) - (int(rect.top()) % GRID_SIZE)
        tile_rect = QRectF(left, top, rect.right() - left, rect.bottom() - top)
        painter.drawTiledPixmap(tile_rect.toAlignedRect(), tile)

    def track_text_selection(self, item):
        """텍스트 선택 시 추적 등록 (QLabel 또는 TextItem)"""
        self._items_with_text_sel.add(item)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            self._panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            if event.button() == Qt.MouseButton.RightButton and self._right_click_origin is not None:
                delta = event.position() - self._right_click_origin
                if (delta.x() ** 2 + delta.y() ** 2) < 25:
                    self._show_context_menu(event.position())
            self._right_click_origin = None
        elif event.button() == Qt.MouseButton.LeftButton and self._port_dragging:
            # 포트 드래그 완료
            scene_pos = self.mapToScene(event.pos())
            self._complete_port_drag(scene_pos)
        elif event.button() == Qt.MouseButton.LeftButton and self._selecting:
            # 드래그 선택 완료
            self._selecting = False
            self._selection_start = None
            self._selection_rect = None
            self._selection_add_mode = False
            self.viewport().update()
            self._report_selection(None)  # 서버모드: 영역 표시 해제
        else:
            super().mouseReleaseEvent(event)

        if event.button() == Qt.MouseButton.LeftButton and self.plugin:
            release_pos = event.position()
            press_pos = getattr(self, '_left_press_pos', None)
            self._left_press_pos = None
            if press_pos is not None:
                delta = release_pos - press_pos
                if delta.x() ** 2 + delta.y() ** 2 > 100:
                    self._check_dimension_drop(self.mapToScene(event.pos()))

        # 스냅 가이드 라인 제거 (모든 마우스 릴리즈 시)
        scene = self.scene()
        if scene and hasattr(scene, '_snap_engine'):
            scene._snap_engine.clear_guides()

    def _deselect_proxy_text_inputs(self, press_item=None):
        """다른 곳을 클릭하면 프록시 노드 내부 QLineEdit/QTextEdit 의
        잔류 캐럿/선택을 제거한다. (scene().clearFocus() 만으론 임베드 위젯의
        깜빡이는 커서·하이라이트가 안 사라지는 문제 보정.)

        ⚠️ 포커스 소스는 scene.focusItem() 이 아니라 QApplication.focusWidget()
        을 쓴다. QGraphicsProxyWidget 안의 위젯이 실제로 포커스를 잡아도
        scene.focusItem() 은 None/엉뚱한 값을 돌려줘서(이 때문에 캐럿이 안 지워짐)
        — 앱 전역 포커스 위젯이 임베드 위젯을 정확히 가리키는 단일 진실원이다.

        같은 노드를 다시 클릭하는 경우(press_item 이 그 위젯을 품은 프록시)는
        건드리지 않아 더블클릭 단어선택/커서 재배치가 깨지지 않는다.
        """
        from PyQt6.QtWidgets import (
            QApplication, QGraphicsProxyWidget, QLineEdit, QTextEdit, QPlainTextEdit,
        )
        fw = QApplication.focusWidget()
        if not isinstance(fw, (QLineEdit, QTextEdit, QPlainTextEdit)):
            return
        # 방금 클릭한 아이템이 이 위젯을 품은 프록시면(같은 노드 재클릭) 그대로 둔다.
        if isinstance(press_item, QGraphicsProxyWidget):
            pw = press_item.widget()
            if pw is not None and (pw is fw or pw.isAncestorOf(fw)):
                return
        if isinstance(fw, QLineEdit):
            if fw.hasSelectedText():
                fw.deselect()
        elif isinstance(fw, (QTextEdit, QPlainTextEdit)):
            cur = fw.textCursor()
            if cur.hasSelection():
                cur.clearSelection()
                fw.setTextCursor(cur)
        fw.clearFocus()

    def _clear_label_selections(self):
        """모든 텍스트 선택 해제 (QLabel + TextItem) — 추적 기반 최적화"""
        if not self._may_have_text_sel and not self._items_with_text_sel:
            return

        # 이전 추적된 항목 클리어
        for item in list(self._items_with_text_sel):
            if isinstance(item, TextItem):
                cursor = item.textCursor()
                if cursor.hasSelection():
                    cursor.clearSelection()
                    item.setTextCursor(cursor)
            elif isinstance(item, QLabel):
                if item.hasSelectedText():
                    item.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
                    item.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._items_with_text_sel.clear()

        # 위젯 상호작용 후 → 한 번만 전체 스캔하여 선택된 텍스트 수집
        if self._may_have_text_sel:
            self._may_have_text_sel = False
            for item in self.scene().items():
                if isinstance(item, QGraphicsProxyWidget):
                    widget = item.widget()
                    if widget:
                        for label in widget.findChildren(QLabel):
                            if label.hasSelectedText():
                                label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
                                label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                elif isinstance(item, TextItem):
                    cursor = item.textCursor()
                    if cursor.hasSelection():
                        cursor.clearSelection()
                        item.setTextCursor(cursor)

    def _show_context_menu(self, view_pos):
        from PyQt6.QtWidgets import QMenu
        scene_pos = self.mapToScene(view_pos.toPoint())
        item = self.scene().itemAt(scene_pos, self.transform())

        from .items import ImageCardItem
        img_card = None
        check = item
        while check is not None:
            if isinstance(check, ImageCardItem):
                img_card = check
                break
            check = check.parentItem()

        if img_card is None:
            return

        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {Theme.BG_SECONDARY};
                color: {Theme.TEXT_PRIMARY};
                border: 1px solid {Theme.GRID_LINE};
                border-radius: 4px;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 6px 20px;
                border-radius: 3px;
            }}
            QMenu::item:selected {{
                background-color: {Theme.ACCENT_PRIMARY};
                color: white;
            }}
        """)

        from v.services.vision import has_vision_api_key
        vision_action = menu.addAction("Use Vision")
        vision_action.setEnabled(bool(img_card.image_path))
        vision_action.triggered.connect(lambda: self._open_vision_dialog(img_card))

        # 보드 → DM: 이미지를 동료에게 보내기
        send_action = menu.addAction("동료에게 보내기 (DM)")
        send_action.setEnabled(bool(img_card.image_path))
        send_action.triggered.connect(lambda: self._share_image_to_contact(img_card))

        global_pos = self.mapToGlobal(view_pos.toPoint())
        menu.exec(global_pos)

    def _share_image_to_contact(self, img_card):
        from .share import share_pixmap_to_contact
        pm = getattr(img_card, "_pixmap", None)
        if (pm is None or pm.isNull()) and img_card.image_path:
            from PyQt6.QtGui import QPixmap
            pm = QPixmap(img_card.image_path)
        share_pixmap_to_contact(pm, self)

    def _open_vision_dialog(self, img_card):
        from .vision_dialog import VisionDialog
        import os

        image_path = img_card.image_path
        if not image_path or not os.path.exists(image_path):
            if ImageCardItem._board_temp_dir and image_path:
                candidate = os.path.join(
                    ImageCardItem._board_temp_dir,
                    os.path.basename(image_path),
                )
                if os.path.exists(candidate):
                    image_path = candidate
            if not image_path or not os.path.exists(image_path):
                return

        existing = getattr(img_card, '_vision_results', None)
        dialog = VisionDialog(image_path, existing_results=existing, parent=self)
        dialog.results_ready.connect(
            lambda raw, card=img_card: self._on_vision_results(card, raw)
        )
        dialog.show()

    def _on_vision_results(self, img_card, raw: dict):
        img_card._vision_results = raw

    def _check_dimension_drop(self, cursor_scene_pos):
        if not self.plugin:
            return
        if getattr(self, '_in_dimension_drop', False):
            return
        self._in_dimension_drop = True
        try:
            selected = [item for item in self.scene().selectedItems()
                        if not isinstance(item, (EdgeItem, PortItem))]
            if not selected:
                return

            for dim in self.plugin.dimension_items.values():
                if dim in selected:
                    continue
                dim_rect = dim.mapToScene(dim.shape()).boundingRect()
                if dim_rect.contains(cursor_scene_pos):
                    targets = [s for s in selected if not isinstance(s, DimensionItem)]
                    if targets:
                        self.plugin.move_items_to_dimension(targets, dim)
                    return
        finally:
            self._in_dimension_drop = False

        # 상위 차원 탈출: 뷰포트 가장자리에 닿으면 부모로 이동
        if self.plugin._parent_plugin:
            vp = self.viewport().rect()
            margin = 30  # 가장자리 감지 영역 (px)
            hits = []
            for s in selected:
                center = self.mapFromScene(s.sceneBoundingRect().center())
                if (center.x() < margin or center.x() > vp.width() - margin or
                        center.y() < margin or center.y() > vp.height() - margin):
                    hits.append(s)
            if hits:
                self.plugin.move_items_to_parent(hits)

    def _find_port_at(self, pos):
        """클릭 위치에서 PortItem 찾기 (부모 탐색 포함)"""
        item = self.itemAt(pos)
        check = item
        while check:
            if isinstance(check, PortItem):
                return check
            check = check.parentItem()
        return None

    def _start_port_drag(self, port, scene_pos):
        """포트 드래그 시작"""
        self._port_dragging = True
        self._drag_source_port = port
        start = port.scenePos()

        if port.port_type == PortItem.OUTPUT:
            self._drag_reverse = False
            self._temp_edge = TempEdgeItem(start)
            self._temp_edge.set_end(scene_pos)
        else:
            # 입력 포트 — 역방향 드래그
            self._drag_reverse = True
            self._temp_edge = TempEdgeItem(scene_pos)
            self._temp_edge.set_end(start)

        self.scene().addItem(self._temp_edge)

        # 드래그 시작 시 와이어링 오버레이 표시
        if self._wire_opacity < 0.5:
            self._start_wire_fade(True)

    def _complete_port_drag(self, scene_pos):
        """포트 드래그 완료 — 호환 포트 위에서 놓으면 연결"""
        # 임시 엣지 제거
        if self._temp_edge:
            self.scene().removeItem(self._temp_edge)
            self._temp_edge = None

        # 드롭 위치에서 포트 검색
        target_port = self._find_port_at(self.mapFromScene(scene_pos))

        if target_port and target_port != self._drag_source_port and self.plugin:
            if self._drag_reverse:
                self.plugin.create_edge(target_port, self._drag_source_port)
            else:
                self.plugin.create_edge(self._drag_source_port, target_port)
        else:
            pass  # no valid target port

        self._port_dragging = False
        self._drag_source_port = None
        self._drag_reverse = False

        # Space 안 누른 상태면 와이어링 페이드 아웃
        if not self._space_held:
            self._start_wire_fade(False)

    def mousePressEvent(self, event: QMouseEvent):
        self._clear_label_selections()
        self._deselect_proxy_text_inputs(self.itemAt(event.pos()))
        self.scene().clearFocus()

        # 메뉴 열려있으면 클릭한 아이템 실행
        if self.radial_menu and event.button() == Qt.MouseButton.LeftButton:
            # 현재 선택된 아이템 실행 (각도 기반으로 이미 선택됨)
            if self.radial_menu.selected_index >= 0:
                selected_item = self.radial_menu.menu_items[self.radial_menu.selected_index]
                if selected_item and selected_item.callback:
                    selected_item.callback()
                event.accept()
                return

            # 아이템 선택 없음 → 메뉴만 닫기
            self._close_radial_menu(execute=False)
            event.accept()
            return

        if event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            self._panning = True
            self._pan_start = event.position()
            self._pan_scroll_start_h = self.horizontalScrollBar().value()
            self._pan_scroll_start_v = self.verticalScrollBar().value()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self._right_click_origin = event.position() if event.button() == Qt.MouseButton.RightButton else None
        elif event.button() == Qt.MouseButton.LeftButton:
            self._left_press_pos = event.position()
            item = self.itemAt(event.pos())
            scene_pos = self.mapToScene(event.pos())

            # 1) PortItem 클릭 → 드래그 연결 시작
            port = self._find_port_at(event.pos())
            if port:
                if port.port_type == PortItem.INPUT and port.edges:
                    # 기존 연결 있는 입력 포트 → reroute (기존 엣지 제거 후 source에서 재드래그)
                    old_edge = port.edges[0]
                    reroute_port = old_edge.source_port
                    self.plugin.remove_edge(old_edge)
                    self._start_port_drag(reroute_port, scene_pos)
                else:
                    self._start_port_drag(port, scene_pos)
                event.accept()
                return

            # 2) EdgeItem 클릭 → 선택
            if isinstance(item, EdgeItem):
                if not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                    self.scene().clearSelection()
                item.setSelected(not item.isSelected() if event.modifiers() & Qt.KeyboardModifier.ControlModifier else True)
                event.accept()
                return

            # 3) 프록시 위젯 클릭 처리 (노드/매크로/메모/체크리스트)
            if isinstance(item, QGraphicsProxyWidget) and item.flags() & QGraphicsProxyWidget.GraphicsItemFlag.ItemIsSelectable:
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    # Ctrl+클릭: 선택 토글
                    item.setSelected(not item.isSelected())
                    event.accept()
                    return
                else:
                    # 다중 선택 상태에서 이미 선택된 아이템 클릭 → 그룹 드래그
                    selected_items = self.scene().selectedItems()
                    if item.isSelected() and len(selected_items) > 1:
                        super().mousePressEvent(event)
                        return
                    # 일반 클릭: 위젯 상호작용 (선택 해제 후 상호작용)
                    if not item.isSelected():
                        self.scene().clearSelection()
                    item.setFlag(QGraphicsProxyWidget.GraphicsItemFlag.ItemIsSelectable, False)
                    super().mousePressEvent(event)
                    item.setFlag(QGraphicsProxyWidget.GraphicsItemFlag.ItemIsSelectable, True)
                    self._may_have_text_sel = True
                    return

            # 4) 선택 가능한 아이템인지 확인 (PinItem, TextItem)
            selectable_item = None
            check_item = item
            while check_item:
                if isinstance(check_item, (PinItem, TextItem)):
                    selectable_item = check_item
                    break
                check_item = check_item.parentItem()

            if selectable_item:
                # Ctrl 없이 클릭하면 다른 선택 해제 후 이 아이템만 선택
                if not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                    if not selectable_item.isSelected():
                        self.scene().clearSelection()
                        selectable_item.setSelected(True)
                # Ctrl 클릭은 토글
                else:
                    selectable_item.setSelected(not selectable_item.isSelected())
                super().mousePressEvent(event)
            elif item is not None:
                # 다른 아이템 (노드 등) 클릭 - 기본 처리
                super().mousePressEvent(event)
            else:
                # 완전히 빈 영역 - 드래그 선택 시작
                self._selection_add_mode = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                if not self._selection_add_mode:
                    self.scene().clearSelection()
                self._selecting = True
                self._selection_start = self.mapToScene(event.pos())
                self._selection_rect = QRectF(self._selection_start, self._selection_start)
                event.accept()
        else:
            super().mousePressEvent(event)

    def _cursor_state(self) -> str:
        """라이브 커서 상태표시용: menu(방사형메뉴)/typing(입력중)/point(노드 위)/away(앱 비활성)."""
        from PyQt6.QtWidgets import QApplication
        if self.radial_menu:
            return "menu"
        ci = getattr(self, "_chat_input", None)
        if self._has_focused_input() or (ci is not None and ci.isVisible()):
            return "typing"
        try:
            if QApplication.applicationState() != Qt.ApplicationState.ApplicationActive:
                return "away"
        except Exception:
            pass
        # 클릭 가능한 노드/아이템 위면 포인터(손) — 커서 스킨의 point 역할에 매핑
        if self._over_clickable_item():
            return "point"
        return ""

    def _over_clickable_item(self) -> bool:
        """현재 마우스가 클릭 가능한 씬 아이템(노드/포트/엣지 등) 위에 있는지."""
        try:
            from PyQt6.QtGui import QCursor
            vp = self.viewport()
            pos = vp.mapFromGlobal(QCursor.pos())
            if not vp.rect().contains(pos):
                return False
            it = self.itemAt(pos)
            # 배경(grid 는 drawBackground 로 그려 아이템 아님) 위면 None → 화살표,
            # 노드/포트/엣지/프록시 등 실제 아이템 위면 손모양.
            return it is not None
        except Exception:
            return False

    def _report_cursor_state(self):
        """위치 변화 없이 상태만 바뀐 경우(메뉴 열림/입력/포커스) 마지막 위치로 재보고."""
        if (self.plugin is not None and getattr(self.plugin, 'server_mode', False)
                and hasattr(self.plugin, 'report_cursor')):
            p = getattr(self, "_last_report_pos", None)
            if p is not None:
                try:
                    self.plugin.report_cursor(p[0], p[1], self._cursor_state())
                except Exception:
                    pass

    def _track_self_cursor(self) -> bool:
        """내 커서 위치를 보고할지 — 서버모드거나, 솔로라도 말풍선 레이어가 있으면(채팅용)."""
        plg = self.plugin
        if plg is None or not hasattr(plg, 'report_cursor'):
            return False
        return (getattr(plg, 'server_mode', False)
                or getattr(plg, '_cursor_layer', None) is not None)

    def _seed_self_cursor(self):
        """채팅 열 때 현재 마우스 위치를 내 말풍선 기준점으로 시드(솔로 첫 말풍선이 중앙에
        뜨던 문제 방지). 레이어가 없으면 만들어 이후 마우스 이동도 추적되게 한다."""
        plg = self.plugin
        if plg is None:
            return
        cl = plg.ensure_cursor_layer() if hasattr(plg, 'ensure_cursor_layer') \
            else getattr(plg, '_cursor_layer', None)
        if cl is None:
            return
        from PyQt6.QtGui import QCursor
        vp = self.viewport()
        pos = vp.mapFromGlobal(QCursor.pos())
        if vp.rect().contains(pos):
            sp = self.mapToScene(pos)
            cl.set_self(sp.x(), sp.y())

    def _poll_report_cursor(self):
        """전역 커서를 주기적으로 보고(노드 위에서도 동작 — mouseMoveEvent 사각지대 보완)."""
        if not self._track_self_cursor():
            return
        # 자리비움(다른 앱으로 alt-tab)이면 위치를 갱신하지 않음 — 다른 앱 위의 마우스가
        # 보드 좌표로 잘못 전송돼 커서가 엉뚱하게 움직이는 것을 방지(마지막 위치 유지)
        if self._cursor_state() == "away":
            return
        # 내 커서 스킨 자가복구 — 팬 종료/방사형 메뉴 닫기 등이 viewport 커서를 화살표로
        # 되돌려 스킨이 풀리므로 매 폴 틱(120ms) 다시 입힌다. 위치 변화 없이 정지 상태에서
        # 풀리는 경우(메뉴 닫힘=커서 워프, 위치 안 변함)도 아래 위치-스킵 전에 복구한다.
        # 방사형 메뉴 중엔 뷰가 일부러 커서를 숨기므로 건드리지 않는다.
        if not self.radial_menu:
            cl = getattr(self.plugin, '_cursor_layer', None) if self.plugin is not None else None
            if cl is not None:
                try:
                    cl.reassert_self_pointer()
                except Exception:
                    pass
        try:
            if self.radial_menu and getattr(self, '_original_scene_pos', None) is not None:
                # 방사형 메뉴 중엔 워프된 실제 커서 말고 원래 위치를 보고
                sx, sy = self._original_scene_pos.x(), self._original_scene_pos.y()
            else:
                from PyQt6.QtGui import QCursor
                vp = self.viewport()
                pos = vp.mapFromGlobal(QCursor.pos())
                if not vp.rect().contains(pos):
                    return  # 뷰 밖이면 위치 보고 안 함(마지막 위치 유지)
                sp = self.mapToScene(pos)
                sx, sy = sp.x(), sp.y()
                self._last_report_pos = (sx, sy)
            last = self._last_poll_xy
            if last is not None and abs(last[0] - sx) < 0.5 and abs(last[1] - sy) < 0.5:
                return  # 안 움직였으면 스킵(주기 ping 이 어차피 재전송)
            self._last_poll_xy = (sx, sy)
            self.plugin.report_cursor(sx, sy, self._cursor_state())
        except Exception:
            pass

    def mouseMoveEvent(self, event: QMouseEvent):
        # 라이브 커서: 서버모드면 서버 보고 + 내 말풍선 위치. 솔로라도 말풍선 레이어가
        # 있으면 내 위치만 갱신(채팅 말풍선이 커서를 따라가게).
        if self._track_self_cursor():
            try:
                # 방사형 메뉴 중엔 워프된 중앙이 아니라 원래 위치를 보낸다(남들 눈에 안 튀게)
                if self.radial_menu and getattr(self, '_original_scene_pos', None) is not None:
                    sx, sy = self._original_scene_pos.x(), self._original_scene_pos.y()
                else:
                    sp = self.mapToScene(event.pos())
                    sx, sy = sp.x(), sp.y()
                    self._last_report_pos = (sx, sy)
                self.plugin.report_cursor(sx, sy, self._cursor_state())
            except Exception:
                pass

        # 메뉴 열려있으면 각도 계산 + 커서 업데이트
        if self.radial_menu and self._menu_center:
            pos = event.position()
            dx = pos.x() - self._menu_center.x()
            dy = pos.y() - self._menu_center.y()
            distance = math.sqrt(dx*dx + dy*dy)
            angle = math.degrees(math.atan2(dy, dx))
            self.radial_menu.update_selection_by_angle(angle, distance)
            # 커서 위치 (메뉴 로컬 좌표)
            self.radial_menu.update_cursor_pos(QPointF(dx, dy))
            event.accept()
            return

        if self._port_dragging and self._temp_edge:
            # 포트 드래그 중 — 임시 엣지 끝점 업데이트
            scene_pos = self.mapToScene(event.pos())
            if self._drag_reverse:
                self._temp_edge.set_start(scene_pos)
            else:
                self._temp_edge.set_end(scene_pos)
            event.accept()
            return

        if self._panning:
            # 절대 방식: 시작점 대비 총 델타
            total_delta = event.position() - self._pan_start
            self.horizontalScrollBar().setValue(int(self._pan_scroll_start_h - total_delta.x()))
            self.verticalScrollBar().setValue(int(self._pan_scroll_start_v - total_delta.y()))
        elif self._selecting and self._selection_start:
            # 드래그 선택 중
            current = self.mapToScene(event.pos())
            self._selection_rect = QRectF(self._selection_start, current).normalized()
            self._update_rubber_band_selection(self._selection_add_mode)
            self.viewport().update()
            self._report_selection(self._selection_rect)  # 서버모드: 상대에게 영역 표시
        else:
            super().mouseMoveEvent(event)

    def _update_rubber_band_selection(self, add_mode: bool = False):
        """러버밴드 영역 내 아이템 선택 (공간 인덱스 활용)"""
        if not self._selection_rect:
            return
        # 공간 쿼리로 영역 내 아이템만 검사 (O(N) → O(영역 내 아이템))
        candidates = self.scene().items(self._selection_rect, Qt.ItemSelectionMode.IntersectsItemBoundingRect)
        # add_mode가 아닐 때: 영역 밖 아이템 선택 해제
        if not add_mode:
            for item in self.scene().selectedItems():
                if item not in candidates:
                    item.setSelected(False)
        for item in candidates:
            if isinstance(item, (PinItem, TextItem, ImageCardItem, FileNodeItem, GroupFrameItem, DimensionItem)):
                in_rect = self._selection_rect.contains(item.pos())
                if add_mode:
                    if in_rect:
                        item.setSelected(True)
                else:
                    item.setSelected(in_rect)
            elif isinstance(item, QGraphicsProxyWidget) and item.flags() & QGraphicsProxyWidget.GraphicsItemFlag.ItemIsSelectable:
                if add_mode:
                    item.setSelected(True)
                else:
                    item.setSelected(True)

    def _report_selection(self, rect):
        """서버모드: 영역 선택 사각형을 다른 사용자에게 보이도록 보고."""
        if self.plugin is None or not getattr(self.plugin, 'server_mode', False):
            return
        if not hasattr(self.plugin, 'report_selection'):
            return
        if rect is None:
            self.plugin.report_selection(None)
        else:
            self.plugin.report_selection(rect.x(), rect.y(), rect.width(), rect.height())

    # ── 게임식 채팅/명령 입력창 (하단 중앙) — 솔로/서버 공통 ──
    def _ensure_cmd_controller(self):
        """채팅 명령 컨트롤러(1개) 지연 생성. 채팅=말풍선, /clear=말풍선비우기로 배선."""
        if getattr(self, '_cmd_controller', None) is None:
            try:
                from .chat_commands import ChatCommandController
                ctrl = ChatCommandController(self.window())
                ctrl.set_chat_sink(self._chat_sink)
                ctrl.set_clear_sink(self._clear_bubbles)
                self._cmd_controller = ctrl
            except Exception:
                self._cmd_controller = None
        return getattr(self, '_cmd_controller', None)

    def _sync_cmd_controller(self):
        """입력창 열 때마다 현재 서버/솔로 상태를 컨트롤러에 반영."""
        ctrl = self._ensure_cmd_controller()
        if ctrl is None:
            return
        plg = self.plugin
        client = getattr(plg, '_server_client', None) if plg is not None else None
        if client is not None and getattr(client, 'is_connected', False):
            ctrl.set_client(client)
            ctrl.set_presence(getattr(plg, '_last_presence', []) or [])
        else:
            ctrl.set_client(None)

    def _chat_sink(self, text: str):
        """평문 채팅(및 /say·/me) → 말풍선/서버 전송."""
        if self.plugin is not None and hasattr(self.plugin, 'send_chat_message'):
            self.plugin.send_chat_message(text)

    def _clear_bubbles(self):
        cl = getattr(self.plugin, '_cursor_layer', None) if self.plugin is not None else None
        if cl is not None:
            cl.clear()
        log = getattr(self, '_chat_log', None)
        if log is not None:
            log.clear()

    def _open_chat_input(self):
        """게임식 채팅/명령 입력창(하단 중앙)을 띄운다."""
        from .chat_panel import CommandInput
        from PyQt6.QtWidgets import QLabel
        if getattr(self, '_chat_input', None) is None:
            ci = CommandInput(self)
            ci.setPlaceholderText("메시지 또는 /명령어   ·   Enter 전송, Esc 취소")
            # 마인크래프트식: 반투명 검정 바, 큰 흰 글씨, 하단 가로 전체
            ci.setStyleSheet(
                "QLineEdit { background:rgba(0,0,0,0.55); color:#ffffff;"
                " border:1px solid rgba(255,255,255,0.18); border-radius:4px;"
                " padding:11px 15px; font-size:15px; }"
                "QLineEdit:focus { border-color:rgba(120,170,255,0.6); }")
            ci.set_controller(self._ensure_cmd_controller())
            ci.set_hint_callback(self._update_chat_hint)
            ci.submit_chat.connect(self._on_chat_submit)
            ci.submit_command.connect(self._on_command_submit)
            ci.escaped.connect(self._close_chat_input)
            ci.focus_out.connect(self._on_chat_focus_out)
            self._chat_input = ci
            self._chat_hint = QLabel(self)
            self._chat_hint.setTextFormat(Qt.TextFormat.RichText)
            self._chat_hint.setStyleSheet(
                "color:#c8ccd2; background:rgba(0,0,0,0.4);"
                " border-radius:3px; padding:2px 8px;"
                " font-family:Consolas,monospace; font-size:13px;")
            self._chat_hint.hide()

        self._sync_cmd_controller()
        self._seed_self_cursor()   # 내 말풍선 기준점 = 현재 마우스(중앙 튐 방지)
        self._ensure_chat_log().pin()   # Enter = 이전 대화 스크롤백 펼치기
        w = self.width() - 24          # 하단 가로 거의 전체(마크식)
        x = 12
        self._chat_input.setFixedWidth(w)
        self._chat_input.move(x, self.height() - 52)
        self._chat_hint.move(x + 6, self.height() - 52 - 24)
        if not self._chat_input.isVisible():
            self._chat_input.clear()
        self._chat_input.show()
        self._chat_input.raise_()
        self._chat_input.setFocus()
        self._report_cursor_state()   # 입력 중 표시

    def _on_chat_submit(self, text: str):
        # 먼저 입력창 닫기/유지 결정 → 메시지는 그 상태로 로그에 (닫혔으면 새 메시지 페이드)
        self._after_chat_submit()
        self._chat_sink(text)

    def _on_command_submit(self, cmd: str):
        # 먼저 입력 닫기/유지 → 결과는 닫힌 상태면 '새 메시지'로 잠깐 떴다 페이드
        self._after_chat_submit()
        ctrl = self._ensure_cmd_controller()
        if ctrl is None:
            self._show_self_lines(["명령 시스템을 쓸 수 없습니다"], error=True)
            return
        try:
            res = ctrl.execute(cmd)
        except Exception as ex:
            self._show_self_lines([f"명령 오류: {ex}"], error=True)
            return
        if res.failed:
            known = ctrl.command_names()
            self._show_self_lines([self._friendly_cmd_error(cmd, res.error, known)], error=True)
        elif res.messages:
            self._show_self_lines(list(res.messages))

    @staticmethod
    def _friendly_cmd_error(cmd: str, err, known: set) -> str:
        """mccmd 영문 오류를 친절한 한글 안내로. 아는 명령이면 사용법, 아니면 오타 안내."""
        name = cmd.split()[0].lstrip("/") if cmd.split() else ""
        if name and name in known:
            return f"§e'/{name}' 사용법이 안 맞아요.§r  /help {name} 로 확인하세요"
        return f"§e'/{name}' 는 모르는 명령이에요.§r  /help 로 목록을 보세요"

    def _ensure_chat_log(self):
        if getattr(self, '_chat_log', None) is None:
            from .chat_panel import ChatLog
            self._chat_log = ChatLog(self)
        return self._chat_log

    def add_chat_log(self, user: str, text: str, color: str = "#8fd1ff"):
        """채팅 한 줄을 히스토리 로그에 기록(말풍선과 별개의 스크롤백)."""
        self._ensure_chat_log().add_chat(user, text, color)

    def _show_self_lines(self, lines, error: bool = False):
        """명령 결과/오류 = **시스템 피드백** → 채팅 로그(말풍선 아님 — 사람 말과 구분).
        §색코드는 로그가 해석. error 면 기본색(코드 없는 부분)을 빨강으로."""
        log = self._ensure_chat_log()
        base = "#ff8888" if error else "#cfd3dc"
        for ln in lines:
            if ln and ln.strip():
                log.add_system(ln, base)

    def _after_chat_submit(self):
        """전송 후 — stay-open 이면 유지, 아니면 닫기."""
        ci = getattr(self, '_chat_input', None)
        if ci is None:
            return
        from v.settings import get_setting
        if get_setting("chat_stay_open", False):
            ci.clear()
            ci.setFocus()
        else:
            self._close_chat_input()

    def _close_chat_input(self):
        ci = getattr(self, '_chat_input', None)
        if ci is not None:
            ci.hide()
        if getattr(self, '_chat_hint', None) is not None:
            self._chat_hint.hide()
        if getattr(self, '_chat_log', None) is not None:
            self._chat_log.unpin()   # 스크롤백 접고 잠깐 보이다 페이드
        self.setFocus()
        self._report_cursor_state()

    def _on_chat_focus_out(self):
        from v.settings import get_setting
        if get_setting("chat_stay_open", False):
            return
        # 완성 클릭 등으로 잠깐 포커스가 튀는 경우를 흡수(되돌아오면 유지)
        def _check():
            ci = getattr(self, '_chat_input', None)
            if ci is not None and ci.isVisible() and not ci.hasFocus():
                self._close_chat_input()
        QTimer.singleShot(120, _check)

    def _update_chat_hint(self, ghost: str, typed: str = ""):
        lbl = getattr(self, '_chat_hint', None)
        if lbl is None:
            return
        if not ghost:
            lbl.hide()
            return
        import html as _html
        lbl.setText(
            f"<span style='color:#9aa'>/{_html.escape(typed)}</span>"
            f"<span style='color:#5a5a5a'>{_html.escape(ghost)}</span>")
        lbl.adjustSize()
        lbl.raise_()
        lbl.show()

    def drawForeground(self, painter: QPainter, rect: QRectF):
        """선택 영역 + 프록시 선택 표시 그리기 (라이브 커서는 별도 오버레이 위젯)"""
        super().drawForeground(painter, rect)
        if self._selecting and self._selection_rect:
            painter.setPen(QPen(QColor(Theme.ACCENT_PRIMARY), 1, Qt.PenStyle.DashLine))
            painter.setBrush(QBrush(QColor(13, 110, 253, 30)))  # 반투명 파란색
            painter.drawRect(self._selection_rect)

        # 선택된 프록시 위젯 테두리 표시
        selected = self.scene().selectedItems()
        if not selected:
            return
        pen = QPen(QColor(Theme.ACCENT_PRIMARY), 2)
        pen.setCosmetic(True)  # 줌 무관 픽셀 단위
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for item in selected:
            if isinstance(item, QGraphicsProxyWidget):
                painter.drawRect(item.sceneBoundingRect())

    def _animate_menu(self):
        """메뉴 애니메이션"""
        if self.radial_menu:
            self.radial_menu.animate()

    def keyPressEvent(self, event: QKeyEvent):
        """키 이벤트 처리"""
        # Enter → 채팅/명령 입력창 (게임처럼). 솔로·서버 공통, 텍스트 입력 중이 아닐 때만.
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.plugin is not None and not self._has_focused_input():
                self._open_chat_input()
                event.accept()
                return
        if event.key() == Qt.Key.Key_Delete:
            if not self._has_focused_input():
                self._delete_selected_items()
                event.accept()
                return
        elif event.key() == Qt.Key.Key_H and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            if not self._has_focused_input():
                self._toggle_hide_selected_images()
                event.accept()
                return
        elif event.key() == Qt.Key.Key_A and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self._select_all_items()
            event.accept()
            return
        elif event.key() == Qt.Key.Key_H and event.modifiers() == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
            if self.plugin:
                self.plugin.open_history_search()
            event.accept()
            return
        elif event.key() == Qt.Key.Key_F and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self._toggle_search()
            event.accept()
            return
        elif event.key() in (Qt.Key.Key_C, Qt.Key.Key_X, Qt.Key.Key_V) and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            if not self._has_focused_input() and self.plugin:
                if event.key() == Qt.Key.Key_C:
                    self.plugin.copy_selected()
                elif event.key() == Qt.Key.Key_X:
                    self.plugin.cut_selected()
                elif event.key() == Qt.Key.Key_V:
                    self.plugin.paste_clipboard()
                event.accept()
                return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent):
        """키 릴리즈 이벤트"""
        super().keyReleaseEvent(event)

    def _init_port_visibility(self):
        """포트 초기 표시 상태 초기화 (숨김)"""
        self._wire_opacity = 0.0
        self._wire_fade_target = 0.0
        self._apply_wire_opacity()

    def _toggle_search(self):
        """Ctrl+F 검색 바 토글"""
        if self._search_bar.isVisible():
            self._search_bar.close()
        else:
            self._search_bar.open()

    def _select_all_items(self):
        """모든 선택 가능한 아이템 선택"""
        for item in self.scene().items():
            if isinstance(item, (PinItem, TextItem, ImageCardItem, FileNodeItem, GroupFrameItem, DimensionItem)):
                item.setSelected(True)
            elif isinstance(item, QGraphicsProxyWidget) and item.flags() & QGraphicsProxyWidget.GraphicsItemFlag.ItemIsSelectable:
                item.setSelected(True)

    def _toggle_hide_selected_images(self):
        """선택된 ImageCardItem의 이미지 가리기/보이기 토글"""
        for item in list(self.scene().selectedItems()):
            if isinstance(item, ImageCardItem):
                item.toggle_hidden()

    def _delete_selected_items(self):
        """선택된 아이템 삭제"""
        if not self.plugin:
            return
        for item in list(self.scene().selectedItems()):
            if isinstance(item, EdgeItem):
                self.plugin.remove_edge(item)
            elif isinstance(item, DimensionItem):
                # Dimension 삭제 시 확인 다이얼로그
                reply = QMessageBox.question(
                    self, "차원 삭제",
                    "이 차원과 내부의 모든 내용이 삭제됩니다.\n계속하시겠습니까?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self.plugin.delete_dimension_item(item)
            elif isinstance(item, FileNodeItem):
                self.plugin.delete_file_node(item)
            elif isinstance(item, ImageCardItem):
                self.plugin.delete_scene_item(item)
            elif isinstance(item, GroupFrameItem):
                self.plugin.delete_group_frame(item)
            elif isinstance(item, TextItem):
                self.plugin.delete_text_item(item)
            elif isinstance(item, PinItem):
                self.scene().removeItem(item)
            elif isinstance(item, QGraphicsProxyWidget):
                node = item.widget()
                if node and getattr(node, '_running', False):
                    reply = QMessageBox.question(
                        self, "작업 중인 노드",
                        "이 노드는 현재 작업 중입니다.\n정말 삭제하시겠습니까?",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No
                    )
                    if reply != QMessageBox.StandardButton.Yes:
                        continue
                self.plugin.delete_proxy_item(item)

    def event(self, event):
        if event.type() == event.Type.KeyPress:
            key_event = event
            if key_event.key() == Qt.Key.Key_Tab:
                if self._has_focused_input():
                    return False
                if not key_event.isAutoRepeat():
                    if self._toggle_mode:
                        if self.radial_menu:
                            self._close_radial_menu(execute=True)
                        else:
                            self._open_radial_menu()
                    elif not self._tab_held:
                        self._tab_held = True
                        self._open_radial_menu()
                return True
            elif key_event.key() == Qt.Key.Key_Escape and self.radial_menu:
                if self._current_category:
                    self._current_category = None
                    self._open_radial_menu()
                else:
                    self._close_radial_menu(execute=False)
                return True
            elif key_event.key() == Qt.Key.Key_Space:
                if not self._has_focused_input():
                    if not key_event.isAutoRepeat():
                        if self._toggle_mode:
                            self._space_held = not self._space_held
                            self._start_wire_fade(self._space_held)
                        else:
                            self._space_held = True
                            self._start_wire_fade(True)
                    return True

        elif event.type() == event.Type.KeyRelease:
            key_event = event
            if not self._toggle_mode and not key_event.isAutoRepeat():
                if key_event.key() == Qt.Key.Key_Tab and self._tab_held:
                    self._tab_held = False
                    if self.radial_menu:
                        self._close_radial_menu(execute=False)
                    return True
                elif key_event.key() == Qt.Key.Key_Space and self._space_held:
                    self._space_held = False
                    if not self._port_dragging:
                        self._start_wire_fade(False)
                    return True

        return super().event(event)

    # ── 와이어링 모드 (Space 키 포트/엣지 페이드) ──

    def _has_focused_input(self):
        from PyQt6.QtWidgets import (
            QApplication, QLineEdit, QTextEdit, QPlainTextEdit,
            QGraphicsTextItem, QGraphicsProxyWidget,
        )
        _TEXT_TYPES = (QLineEdit, QTextEdit, QPlainTextEdit)
        focus_item = self.scene().focusItem()
        if isinstance(focus_item, QGraphicsTextItem):
            if focus_item.textInteractionFlags() & Qt.TextInteractionFlag.TextEditable:
                return True
        if isinstance(focus_item, QGraphicsProxyWidget):
            w = focus_item.widget()
            if w:
                fw = w.focusWidget()
                if isinstance(fw, _TEXT_TYPES):
                    return True
        focus_widget = QApplication.focusWidget()
        if focus_widget is not None and focus_widget is not self and focus_widget is not self.viewport():
            if isinstance(focus_widget, _TEXT_TYPES):
                return True
            parent = focus_widget.parent()
            if parent and isinstance(parent, _TEXT_TYPES):
                return True
        return False

    def _start_wire_fade(self, show: bool):
        """포트/엣지 페이드 시작"""
        self._wire_fade_target = 1.0 if show else 0.0
        if not self._wire_fade_timer.isActive():
            self._wire_fade_timer.start()

    def _wire_fade_tick(self):
        """포트/엣지 opacity 보간"""
        target = self._wire_fade_target
        diff = target - self._wire_opacity
        if abs(diff) < 0.01:
            self._wire_opacity = target
            self._wire_fade_timer.stop()
        else:
            from v.constants import FADE_SPEED_INCREASE, FADE_SPEED_DECREASE
            speed = FADE_SPEED_INCREASE if diff > 0 else FADE_SPEED_DECREASE
            self._wire_opacity += diff * speed
        self._apply_wire_opacity()

    def _apply_wire_opacity(self):
        """현재 _wire_opacity를 모든 포트/엣지에 적용 (캐싱된 컬렉션 사용)"""
        op = self._wire_opacity
        for port in self._all_port_items:
            port.setOpacity(op)
            if port._label_bg:
                port._label_bg.setOpacity(op * 0.85)
            if port._label:
                port._label.setOpacity(op * 0.95)
        if self.plugin:
            for edge in self.plugin._edges:
                if edge.isSelected():
                    edge.setOpacity(1.0)
                else:
                    edge.setOpacity(0.15 + op * 0.85)
        self.viewport().update()

    def _open_radial_menu(self, category: str = None):
        """방사형 메뉴 열기 (화면 중앙)"""
        if not self.plugin:
            return

        # 첫 번째 호출 시에만 원래 마우스 위치 저장
        if category is None:
            self._original_cursor_pos = QCursor.pos()
            original_viewport_pos = self.viewport().mapFromGlobal(self._original_cursor_pos)
            self._original_scene_pos = self.mapToScene(original_viewport_pos)
            self._current_category = None
        else:
            # 서브메뉴는 원래 위치 유지
            original_viewport_pos = self.viewport().mapFromGlobal(self._original_cursor_pos) if self._original_cursor_pos else QPointF(0, 0)

        # 화면 중앙 계산 (메뉴 표시용)
        viewport_center = QPointF(self.viewport().width() / 2, self.viewport().height() / 2)
        menu_scene_pos = self.mapToScene(viewport_center.toPoint())

        # 기존 메뉴 닫기 (서브메뉴로 전환)
        if self.radial_menu:
            self.scene().removeItem(self.radial_menu)
            self.radial_menu = None

        # 메뉴 아이템 정의
        items = self.plugin.get_radial_menu_items(self._original_scene_pos, category)
        if not items:
            return

        # callback 변환
        menu_items = []
        if category is None:
            # 1단계: 카테고리 → 서브메뉴 열기
            for icon, label, cat_name in items:
                menu_items.append((icon, label, lambda c=cat_name: self._open_submenu(c)))
        else:
            # 2단계: 노드 → 생성 + 메뉴 닫기
            for icon, label, callback in items:
                menu_items.append((icon, label, lambda cb=callback: self._execute_and_close(cb)))
        items = menu_items

        # 원래 마우스 위치의 오프셋 (뷰포트 픽셀 좌표 — ItemIgnoresTransformations)
        origin_offset = QPointF(original_viewport_pos) - viewport_center

        # 메뉴 생성 (화면 중앙에 표시)
        self.radial_menu = RadialMenu(items, on_close=self._on_menu_closed, origin_offset=origin_offset)
        self.radial_menu.setPos(menu_scene_pos)
        self.scene().addItem(self.radial_menu)

        # 메뉴 중심 저장 + 마우스 커서 이동 (첫 번째만)
        if category is None:
            self._menu_center = viewport_center
            self._menu_scene_pos = menu_scene_pos
            global_center = self.viewport().mapToGlobal(viewport_center.toPoint())
            QCursor.setPos(global_center)

            # 커서 숨기기 (viewport에 적용)
            self.viewport().setCursor(Qt.CursorShape.BlankCursor)

            # 애니메이션 시작
            self._anim_timer.start()
        else:
            # 서브메뉴는 커서를 중앙으로 다시 이동
            global_center = self.viewport().mapToGlobal(viewport_center.toPoint())
            QCursor.setPos(global_center)

    def _open_submenu(self, category: str):
        """서브메뉴 열기"""
        self._current_category = category
        self._open_radial_menu(category)

    def _execute_and_close(self, callback):
        """노드 생성 + 메뉴 닫기"""
        callback()
        self._close_radial_menu(execute=False)

    def _close_radial_menu(self, execute: bool = False):
        """방사형 메뉴 닫기"""
        if self.radial_menu:
            if execute:
                self.radial_menu.execute_selected()
            self.radial_menu.close()

    def _on_menu_closed(self):
        """메뉴 닫힘 콜백"""
        # 애니메이션 중지
        self._anim_timer.stop()

        # 커서 복원 (viewport에 적용) — 스킨이 있으면 화살표 대신 스킨으로 즉시 복원
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        cl = getattr(self.plugin, '_cursor_layer', None) if self.plugin is not None else None
        if cl is not None:
            try:
                cl.reassert_self_pointer()
            except Exception:
                pass

        # 원래 위치로 마우스 이동
        if self._original_cursor_pos is not None:
            QCursor.setPos(self._original_cursor_pos)

        # 상태 리셋
        self.radial_menu = None
        self._menu_center = None
        self._menu_scene_pos = None
        self._original_cursor_pos = None
        self._current_category = None
        self._submenu_opened = False
