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

from .items import PinItem, TextItem, PortItem, TempEdgeItem, EdgeItem, ImageCardItem, GroupFrameItem
from .dimension_item import DimensionItem
from .minimap import BranchGraphWidget
from .radial_menu import RadialMenu
from .search_bar import SearchBarWidget
from v.theme import Theme


class WhiteboardView(QGraphicsView):
    """줌/팬 가능한 화이트보드 뷰"""

    def __init__(self, scene, plugin=None):
        super().__init__(scene)
        self.plugin = plugin
        self.radial_menu = None
        self._menu_center = None  # 메뉴 중심 (뷰포트 좌표)
        self._menu_scene_pos = None  # 메뉴 중심 (씬 좌표)
        self._original_cursor_pos = None  # 원래 마우스 위치 (복원용)
        self._tab_held = False  # TAB 키 홀드 상태
        self._space_held = False  # Space 키 홀드 상태 (와이어링 모드)
        self._wire_opacity = 0.0  # 포트/엣지 페이드 현재 값
        self._wire_fade_target = 0.0
        self._current_category = None  # 현재 선택된 카테고리 (계층형 메뉴용)
        self._original_scene_pos = None  # 노드 추가 위치 저장
        self._submenu_opened = False  # 서브메뉴가 방금 열렸는지 플래그

        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)
        self.setOptimizationFlag(QGraphicsView.OptimizationFlag.DontAdjustForAntialiasing)
        self.setOptimizationFlag(QGraphicsView.OptimizationFlag.DontSavePainterState)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._panning = False
        self._pan_start = QPointF()
        self._pan_scroll_start_h = 0
        self._pan_scroll_start_v = 0
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

        # 브랜치 그래프
        self._branch_graph = BranchGraphWidget(self)

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
                    from pathlib import Path
                    ext = Path(url.toLocalFile()).suffix.lower()
                    if ext in self._IMAGE_EXTENSIONS:
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
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.suffix.lower() not in self._IMAGE_EXTENSIONS:
                continue
            drop_pos = QPointF(base_pos.x() + offset, base_pos.y() + offset)
            self.plugin.add_image_card(str(path), drop_pos)
            offset += 30
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
        # 방사형 메뉴 열려있으면 줌 차단
        if self.radial_menu:
            event.accept()
            return

        from v.constants import ZOOM_FACTOR, ZOOM_MIN, ZOOM_MAX
        factor = ZOOM_FACTOR if event.angleDelta().y() > 0 else 1 / ZOOM_FACTOR

        new_zoom = self._zoom * factor
        if ZOOM_MIN <= new_zoom <= ZOOM_MAX:
            self._zoom = new_zoom
            self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
            self.scale(factor, factor)
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
        else:
            super().mouseReleaseEvent(event)

        # 드래그 완료 후 차원 겹침 감지
        if event.button() == Qt.MouseButton.LeftButton and self.plugin:
            self._check_dimension_drop()

        # 스냅 가이드 라인 제거 (모든 마우스 릴리즈 시)
        scene = self.scene()
        if scene and hasattr(scene, '_snap_engine'):
            scene._snap_engine.clear_guides()

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

    def _check_dimension_drop(self):
        """드래그 완료 후 차원 아이템과 겹침 감지 → 차원 이동."""
        if not self.plugin:
            return
        selected = [item for item in self.scene().selectedItems()
                    if not isinstance(item, (EdgeItem, PortItem))]
        if not selected:
            return

        # 차원 아이템 위에 놓으면 해당 차원으로 이동
        for dim in self.plugin.dimension_items.values():
            if dim in selected:
                continue
            dim_rect = dim.mapToScene(dim.shape()).boundingRect()
            hits = [s for s in selected
                    if not isinstance(s, GroupFrameItem)
                    and dim_rect.contains(s.sceneBoundingRect().center())]
            if hits:
                self.plugin.move_items_to_dimension(hits, dim)
                return

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
        elif event.button() == Qt.MouseButton.LeftButton:
            # 클릭한 위치의 아이템 확인
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

    def mouseMoveEvent(self, event: QMouseEvent):
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
            if isinstance(item, (PinItem, TextItem, ImageCardItem, GroupFrameItem, DimensionItem)):
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

    def drawForeground(self, painter: QPainter, rect: QRectF):
        """선택 영역 + 프록시 선택 표시 그리기"""
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
        elif event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            if not self._has_focused_input():
                self._show_all_ports(True)
                event.accept()
                return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent):
        """키 릴리즈 이벤트"""
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            if not self._has_focused_input():
                self._show_all_ports(False)
                event.accept()
                return
        super().keyReleaseEvent(event)

    def _init_port_visibility(self):
        """포트 초기 표시 상태 초기화 (숨김)"""
        self._wire_opacity = 0.0
        self._wire_fade_target = 0.0
        self._apply_wire_opacity()

    def _show_all_ports(self, show: bool):
        """모든 단자와 라벨 표시/숨김 (캐싱된 컬렉션 사용)"""
        for port in self._all_port_items:
            port.show_port(show)

    def _toggle_search(self):
        """Ctrl+F 검색 바 토글"""
        if self._search_bar.isVisible():
            self._search_bar.close()
        else:
            self._search_bar.open()

    def _select_all_items(self):
        """모든 선택 가능한 아이템 선택"""
        for item in self.scene().items():
            if isinstance(item, (PinItem, TextItem, ImageCardItem, GroupFrameItem, DimensionItem)):
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
        """모든 이벤트 처리 (TAB/Space 키 가로채기)"""
        if event.type() == event.Type.KeyPress:
            key_event = event
            if key_event.key() == Qt.Key.Key_Tab:
                # Tab 키는 메뉴 열기의 핵심이므로, 포커스 체크 무시
                # (포커스된 텍스트 입력 필드를 명시적으로 제외해야 함)
                from PyQt6.QtWidgets import QApplication, QLineEdit, QTextEdit, QPlainTextEdit
                focus_widget = QApplication.focusWidget()

                # 텍스트 입력 필드 중: 실제 텍스트 편집 중인 경우만 무시
                if isinstance(focus_widget, (QLineEdit, QTextEdit, QPlainTextEdit)):
                    # 텍스트 입력 중이면 Tab 키 통과 (포커스 이동)
                    return False

                if not key_event.isAutoRepeat() and not self._tab_held:
                    self._tab_held = True
                    self._open_radial_menu()
                return True
            elif key_event.key() == Qt.Key.Key_Escape and self.radial_menu:
                # 서브메뉴면 1단계로 돌아가기
                if self._current_category:
                    self._current_category = None
                    self._open_radial_menu()
                else:
                    # 1단계면 메뉴 닫기
                    self._close_radial_menu(execute=False)
                return True
            elif key_event.key() == Qt.Key.Key_Space:
                if not self._has_focused_input():
                    if not key_event.isAutoRepeat():
                        self._space_held = True
                        self._start_wire_fade(True)
                    return True

        elif event.type() == event.Type.KeyRelease:
            key_event = event
            if key_event.key() == Qt.Key.Key_Tab and not key_event.isAutoRepeat():
                if self._tab_held:
                    self._tab_held = False
                    # TAB 떼면 메뉴 닫기 (취소)
                    if self.radial_menu:
                        self._close_radial_menu(execute=False)
                    return True
            elif key_event.key() == Qt.Key.Key_Space and not key_event.isAutoRepeat():
                if self._space_held:
                    self._space_held = False
                    if not self._port_dragging:
                        self._start_wire_fade(False)
                    return True

        return super().event(event)

    # ── 와이어링 모드 (Space 키 포트/엣지 페이드) ──

    def _has_focused_input(self):
        """씬에 포커스된 아이템이 있는지 확인"""
        # 씬에 포커스된 아이템이 있으면 키 캡처하지 않음
        focus_item = self.scene().focusItem()
        if focus_item is not None:
            # 잠긴 그룹은 포커스 차단 대상에서 제외
            if isinstance(focus_item, GroupFrameItem) and getattr(focus_item, '_locked', False):
                pass
            else:
                return True

        # 앱 전체에서 포커스 위젯 확인
        from PyQt6.QtWidgets import QApplication
        focus_widget = QApplication.focusWidget()

        # 뷰/뷰포트가 아닌 곳에 포커스가 있으면 키 캡처하지 않음
        if focus_widget is not None:
            if focus_widget is not self and focus_widget is not self.viewport():
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

        # 커서 복원 (viewport에 적용)
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)

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
