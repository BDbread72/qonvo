"""
그래픽 씬 아이템
- EdgeItem: 노드 간 연결선 (베지어 곡선)
- PinItem: 핀 마커
- TextItem: 편집 가능한 텍스트
- ImageCardItem: 이미지 카드
- GroupFrameItem: 그룹 프레임
"""
import math
import os
from pathlib import Path
from typing import Dict, Any

from PyQt6.QtWidgets import (
    QGraphicsPathItem, QGraphicsEllipseItem, QGraphicsTextItem,
    QGraphicsItem, QGraphicsRectItem, QGraphicsProxyWidget, QToolTip,
)
from PyQt6.QtCore import Qt, QPointF, QRectF, QTimer
from PyQt6.QtGui import (
    QPen, QBrush, QColor, QPainterPath, QPainterPathStroker, QFont,
    QPixmap, QPainter,
)

from q import t
from v.theme import Theme
from v.logger import get_logger

_logger = get_logger("qonvo.items")

_group_moving = False  # 다중 선택 이동 시 재귀 방지


class SceneItemMixin:
    """모든 화이트보드 씬 아이템의 공통 기능 믹스인.

    TextItem, ImageCardItem, GroupFrameItem, DimensionItem 등
    QGraphicsItem 기반 아이템에 적용하여 아래 기능을 통합 제공:
      - itemChange: 이동 시 포트 자동 재배치
      - 리사이즈: prepareGeometryChange() 보장 + 상태 관리
      - 포트 열거 (삭제/정리용)
      - 리사이즈 핸들 페인팅
      - 직렬화 베이스 (x, y)
    """

    HANDLE_SIZE = 10

    # ── itemChange (MRO로 상속) ───────────────────────────

    def itemChange(self, change, value):
        global _group_moving
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            self._pre_move_pos = self.pos()
            if not _group_moving:
                try:
                    scene = self.scene()
                    if scene and hasattr(scene, '_snap_engine'):
                        value = scene._snap_engine.snap(self, value)
                except Exception:
                    pass
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self._reposition_own_ports()
            if not _group_moving and self.isSelected():
                scene = self.scene()
                pre = getattr(self, '_pre_move_pos', None)
                if scene and pre is not None:
                    delta = self.pos() - pre
                    if delta.x() != 0 or delta.y() != 0:
                        _group_moving = True
                        try:
                            for item in scene.selectedItems():
                                if item is not self:
                                    item.moveBy(delta.x(), delta.y())
                        finally:
                            _group_moving = False
        return super().itemChange(change, value)

    # ── 포트 관리 ─────────────────────────────────────────

    def _reposition_own_ports(self):
        """연결된 모든 포트를 재배치한다."""
        for attr in ("input_port", "output_port",
                      "signal_input_port", "signal_output_port"):
            port = getattr(self, attr, None)
            if port:
                port.reposition()

    def iter_ports(self):
        """아이템에 속한 모든 포트를 반환한다 (삭제/정리용)."""
        ports = []
        for attr in ("input_port", "output_port",
                      "signal_input_port", "signal_output_port"):
            port = getattr(self, attr, None)
            if port:
                ports.append(port)
        return ports

    # ── 리사이즈 공통 ────────────────────────────────────

    def _begin_resize(self):
        """리사이즈 전 반드시 호출 — prepareGeometryChange() 보장."""
        self.prepareGeometryChange()

    def _end_resize(self):
        """리사이즈 종료 — 공통 상태 초기화."""
        self._resizing = False
        self._resize_start = None

    def _is_near_bottom_right(self, pos, w, h):
        """마우스가 우하단 리사이즈 핸들 근처인지 판정."""
        hs = self.HANDLE_SIZE + 5
        return abs(pos.x() - w) < hs and abs(pos.y() - h) < hs

    # ── 페인팅 공통 ──────────────────────────────────────

    def _paint_resize_handle(self, painter, rect):
        """선택 시 우하단 리사이즈 핸들을 그린다."""
        hs = self.HANDLE_SIZE
        painter.setPen(QPen(QColor("#0d6efd"), 1))
        painter.setBrush(QBrush(QColor("#ffffff")))
        br = rect.bottomRight()
        painter.drawRect(QRectF(br.x() - hs / 2, br.y() - hs / 2, hs, hs))

    # ── 직렬화 공통 ──────────────────────────────────────

    def _base_data(self) -> Dict[str, Any]:
        """모든 씬 아이템의 공통 직렬화 필드."""
        return {"x": self.pos().x(), "y": self.pos().y()}


class PortItem(QGraphicsEllipseItem):
    """노드의 입출력 포트 (단자)"""
    INPUT = 0
    OUTPUT = 1

    # 포트 데이터 타입
    TYPE_BOOLEAN = "boolean"  # 신호 (on/off, 레드스톤)
    TYPE_STRING = "str"       # 문자열
    TYPE_FILE = "file"        # 파일/이미지

    # 타입별 색상 (Theme 사용)
    TYPE_COLORS = {
        TYPE_BOOLEAN: Theme.PORT_BOOLEAN,
        TYPE_STRING: Theme.PORT_STRING,
        TYPE_FILE: Theme.ACCENT_SUCCESS,
    }

    # 기본 색상 팔레트 (다중 포트 구분용 - 레거시)
    DEFAULT_COLORS = [
        Theme.PORT_STRING,   # 파랑
        "#e67e22",           # 주황
        Theme.ACCENT_SUCCESS,  # 초록
        "#e74c3c",           # 빨강
        "#9b59b6",           # 보라
        "#1abc9c",           # 청록
        Theme.PORT_BOOLEAN,  # 노랑
        "#34495e",           # 회색
    ]

    def __init__(self, port_type, parent_proxy, name: str = None, index: int = 0, total: int = 1, color: str = None, data_type: str = None):
        size = 12
        super().__init__(-size / 2, -size / 2, size, size)  # 독립 아이템
        self.port_type = port_type
        self.parent_proxy = parent_proxy
        self.edges = []  # 이 포트에 연결된 EdgeItem 리스트
        self._last_scene_pos = None

        # Phase 4: 포트 위치 캐싱
        self._cached_scene_pos: QPointF | None = None
        self._cache_valid = False

        # 다중 포트 지원
        self.port_name = name      # 포트 이름 (파라미터명)
        self.port_index = index    # 순서 (0-based)
        self.port_total = total    # 전체 포트 개수

        # 포트 데이터 타입 (기본값: str)
        self.port_data_type = data_type if data_type else self.TYPE_STRING

        # 다중 연결 허용 (True면 INPUT 포트에도 여러 엣지 가능)
        self.multi_connect = False

        # 색상: 지정값 → 타입별 색상 → 인덱스 기반 자동 할당
        if color:
            self._color = QColor(color)
        elif self.port_data_type in self.TYPE_COLORS:
            self._color = QColor(self.TYPE_COLORS[self.port_data_type])
        else:
            color_idx = index % len(self.DEFAULT_COLORS)
            self._color = QColor(self.DEFAULT_COLORS[color_idx])
        self.setBrush(QBrush(self._color))
        self.setPen(QPen(QColor(Theme.PORT_EXEC), 1.5))
        self.setZValue(100)
        self.setAcceptHoverEvents(True)
        self.setOpacity(0.15)  # 살짝 보이게 (툴팁 작동 위해)

        # 툴팁: 타입 정보 표시
        type_name_kr = {
            self.TYPE_BOOLEAN: "신호",
            self.TYPE_STRING: "문자열",
            self.TYPE_FILE: "파일"
        }.get(self.port_data_type, self.port_data_type)
        port_dir = "입력" if port_type == self.INPUT else "출력"
        tooltip = f"{port_dir} 포트\n타입: {type_name_kr} ({self.port_data_type})"
        if name and name != "_default":
            tooltip = f"{name}\n{tooltip}"
        elif name == "_default":
            display_name = "이전 대화" if port_type == self.INPUT else "응답"
            tooltip = f"{display_name}\n{tooltip}"
        self.setToolTip(tooltip)

        # 이름 있는 포트는 라벨 표시 (_default일 때는 이전 대화/응답으로 표시)
        self._label = None
        self._label_bg = None  # 라벨 배경
        if name:
            display_name = name
            if name == "_default":
                display_name = "이전 대화" if port_type == self.INPUT else "응답"

            # 라벨 텍스트
            self._label = QGraphicsTextItem(display_name)
            self._label.setDefaultTextColor(self._color)  # 포트 색상과 동일
            font = QFont("Segoe UI", 8)
            font.setBold(True)
            self._label.setFont(font)
            self._label.setZValue(201)  # 최상위
            self._label.setOpacity(0.0)  # Space 키로 토글

            # 라벨 배경 (반투명 검정)
            self._label_bg = QGraphicsRectItem()
            self._label_bg.setBrush(QBrush(QColor(0, 0, 0, 180)))
            self._label_bg.setPen(QPen(Qt.PenStyle.NoPen))
            self._label_bg.setZValue(200)
            self._label_bg.setOpacity(0.0)

    def scenePos(self) -> QPointF:
        """장면상 위치 반환 (캐싱됨)"""
        # Phase 4: 포트 위치 캐싱으로 성능 개선
        if self._cache_valid and self._cached_scene_pos is not None:
            return self._cached_scene_pos

        self._cached_scene_pos = super().scenePos()
        self._cache_valid = True
        return self._cached_scene_pos

    def _invalidate_cache(self):
        """포트 위치 캐시 무효화"""
        self._cache_valid = False

    def _notify_edges(self):
        """연결된 엣지들에 위치 변경 알림"""
        for edge in self.edges:
            edge.schedule_update()

    def set_color(self, color: str):
        """단자 색상 설정"""
        self._color = QColor(color)
        self.setBrush(QBrush(self._color))
        if self._label:
            self._label.setDefaultTextColor(self._color)

    def setPos(self, *args):
        """위치 설정 및 캐시 무효화"""
        super().setPos(*args)
        self._invalidate_cache()
        self._notify_edges()

    def reposition(self):
        """포트를 노드 좌/우에 배치 (다중 포트는 수직 분배)"""
        parent = self.parent_proxy

        if isinstance(parent, QGraphicsProxyWidget):
            widget = parent.widget()
            if not widget:
                return
            h = widget.height()
            w = widget.width()
        elif hasattr(parent, '_width') and hasattr(parent, '_height'):
            w = parent._width
            h = parent._height
        else:
            rect = parent.boundingRect()
            w = rect.width()
            h = rect.height()
        proxy_pos = parent.pos()

        if self.port_type == self.INPUT:
            if self.port_total <= 1:
                # 단일 포트: 중앙
                new_pos = (proxy_pos.x(), proxy_pos.y() + h / 2)
            else:
                # 다중 포트: 수직 분배
                spacing = h / (self.port_total + 1)
                y_offset = spacing * (self.port_index + 1)
                new_pos = (proxy_pos.x(), proxy_pos.y() + y_offset)
        else:
            # 출력 포트: 다중일 때도 수직 분배
            if self.port_total <= 1:
                new_pos = (proxy_pos.x() + w, proxy_pos.y() + h / 2)
            else:
                spacing = h / (self.port_total + 1)
                y_offset = spacing * (self.port_index + 1)
                new_pos = (proxy_pos.x() + w, proxy_pos.y() + y_offset)

        if self._last_scene_pos == new_pos:
            return
        self._last_scene_pos = new_pos
        self.setPos(new_pos[0], new_pos[1])

        # 라벨 위치 업데이트 (입력:우측, 출력:좌측)
        if self._label:
            label_rect = self._label.boundingRect()
            label_w = label_rect.width()
            label_h = label_rect.height()
            padding = 3

            if self.port_type == self.INPUT:
                # 입력 포트: 라벨을 우측에
                label_x = new_pos[0] + 10
                label_y = new_pos[1] - 8
            else:
                # 출력 포트: 라벨을 좌측에
                label_x = new_pos[0] - label_w - 10
                label_y = new_pos[1] - 8

            self._label.setPos(label_x, label_y)

            # 배경 위치/크기 업데이트
            if self._label_bg:
                self._label_bg.setRect(
                    label_x - padding,
                    label_y - padding,
                    label_w + padding * 2,
                    label_h + padding * 2
                )

    def setParentItem(self, parent):
        """씬에 추가될 때 라벨도 함께 추가"""
        super().setParentItem(parent)
        if self.scene():
            if self._label_bg and not self._label_bg.scene():
                self.scene().addItem(self._label_bg)
            if self._label and not self._label.scene():
                self.scene().addItem(self._label)

    def scene_add_label(self, scene):
        """씬에 라벨 추가 (plugin에서 호출)"""
        if scene:
            if self._label_bg and not self._label_bg.scene():
                scene.addItem(self._label_bg)
            if self._label and not self._label.scene():
                scene.addItem(self._label)

    def scene_remove_label(self):
        """씬에서 라벨 제거"""
        if self._label_bg and self._label_bg.scene():
            self._label_bg.scene().removeItem(self._label_bg)
        if self._label and self._label.scene():
            self._label.scene().removeItem(self._label)

    def show_port(self, show: bool = True):
        """단자와 라벨 표시/숨김 (Space 키 토글용)"""
        self.setOpacity(1.0 if show else 0.0)
        if self._label_bg:
            self._label_bg.setOpacity(0.85 if show else 0.0)
        if self._label:
            self._label.setOpacity(0.95 if show else 0.0)

    def hoverEnterEvent(self, event):
        self.setPen(QPen(QColor(Theme.ACCENT_WARNING), 2.5))
        self.setScale(1.4)
        # 툴팁 직접 표시
        if self.toolTip():
            view = self.scene().views()[0] if self.scene() and self.scene().views() else None
            if view:
                pos = view.mapToGlobal(view.mapFromScene(self.scenePos()))
                QToolTip.showText(pos, self.toolTip())
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self.setPen(QPen(QColor(Theme.PORT_EXEC), 1.5))
        self.setScale(1.0)
        # 툴팁 숨기기
        QToolTip.hideText()
        super().hoverLeaveEvent(event)


class TempEdgeItem(QGraphicsPathItem):
    """드래그 중 표시되는 임시 엣지"""

    def __init__(self, start_pos):
        super().__init__()
        self._start = start_pos
        self._end = start_pos
        pen = QPen(QColor(Theme.PORT_STRING), 2, Qt.PenStyle.DashLine)
        pen.setDashPattern([6, 4])
        self.setPen(pen)
        self.setOpacity(0.7)
        self.setZValue(-1)

    def set_end(self, end_pos):
        self._end = end_pos
        self._rebuild_path()

    def set_start(self, start_pos):
        self._start = start_pos
        self._rebuild_path()

    def _rebuild_path(self):
        offset = max(30, abs(self._end.x() - self._start.x()) / 2)
        path = QPainterPath()
        path.moveTo(self._start)
        path.cubicTo(
            self._start.x() + offset, self._start.y(),
            self._end.x() - offset, self._end.y(),
            self._end.x(), self._end.y()
        )
        self.setPath(path)


class EdgeItem(QGraphicsPathItem):
    """두 포트를 연결하는 베지어 곡선"""

    def __init__(self, source_port, target_port):
        super().__init__()
        self.source_port = source_port  # PortItem (OUTPUT)
        self.target_port = target_port  # PortItem (INPUT)

        # 타입 검증: 같은 타입끼리만 연결 가능
        self.is_type_valid = (source_port.port_data_type == target_port.port_data_type)

        # 타입에 따라 색상 결정
        if self.is_type_valid:
            # 포트 색상 사용
            port_color = source_port._color
            self._normal_pen = QPen(port_color, 2)
            self._hover_pen = QPen(port_color.lighter(120), 2.5)
            self._selected_pen = QPen(port_color.lighter(140), 3.5)
        else:
            # 타입 불일치 시 빨간색
            self._normal_pen = QPen(QColor(Theme.ACCENT_DANGER), 2)
            self._hover_pen = QPen(QColor("#c0392b"), 2.5)
            self._selected_pen = QPen(QColor(Theme.ACCENT_DANGER), 3.5)

        self.setPen(self._normal_pen)
        self.setZValue(-1)
        self.setOpacity(0.15)

        # 선택 가능
        self.setFlag(QGraphicsPathItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)

        # 포트에 등록
        source_port.edges.append(self)
        target_port.edges.append(self)

        self._last_pos = None
        self._cached_shape = None
        # Phase 4: 이벤트 기반 엣지 업데이트
        self._update_scheduled = False
        self.update_path()

    @property
    def source(self):
        """하위호환: source proxy 반환"""
        return self.source_port.parent_proxy

    @property
    def target(self):
        """하위호환: target proxy 반환"""
        return self.target_port.parent_proxy

    def schedule_update(self):
        """업데이트를 다음 이벤트 루프에서 수행하도록 예약 (이벤트 기반)"""
        # Phase 4: 이벤트 기반 엣지 업데이트로 성능 개선
        if not self._update_scheduled:
            self._update_scheduled = True
            QTimer.singleShot(0, self._do_update)

    def _do_update(self):
        """예약된 업데이트 수행"""
        self._update_scheduled = False
        self.update_path()

    def update_path(self):
        s = self.source_port.scenePos()
        t = self.target_port.scenePos()
        key = (s.x(), s.y(), t.x(), t.y())
        if self._last_pos == key:
            return
        self._last_pos = key
        self._cached_shape = None
        offset = max(30, abs(t.x() - s.x()) / 2)
        path = QPainterPath()
        path.moveTo(s)
        path.cubicTo(s.x() + offset, s.y(), t.x() - offset, t.y(), t.x(), t.y())
        self.setPath(path)

    def shape(self):
        """넓은 클릭 영역 (캐싱)"""
        if self._cached_shape is None:
            stroker = QPainterPathStroker()
            stroker.setWidth(12)
            self._cached_shape = stroker.createStroke(self.path())
        return self._cached_shape

    def paint(self, painter, option, widget):
        option.state &= ~option.state.State_Selected
        if self.isSelected():
            self.setPen(self._selected_pen)
        super().paint(painter, option, widget)

    def hoverEnterEvent(self, event):
        if not self.isSelected():
            self.setPen(self._hover_pen)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        if not self.isSelected():
            self.setPen(self._normal_pen)
        super().hoverLeaveEvent(event)

    def disconnect(self):
        """포트의 edges 리스트에서 자기 자신 제거"""
        if self in self.source_port.edges:
            self.source_port.edges.remove(self)
        if self in self.target_port.edges:
            self.target_port.edges.remove(self)


class PinItem(QGraphicsEllipseItem):
    """핀 마커 아이템"""

    def __init__(self, x: float, y: float, color: str = "#ff6b6b"):
        size = 20
        super().__init__(-size/2, -size/2, size, size)
        self.setPos(x, y)
        self.color = color

        self.setBrush(QBrush(QColor(color)))
        self.setPen(QPen(QColor(Theme.PORT_EXEC), 2))
        self.setZValue(50)

        # 드래그 가능
        self.setFlag(QGraphicsEllipseItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsEllipseItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsEllipseItem.GraphicsItemFlag.ItemIsFocusable, True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def paint(self, painter, option, widget):
        # 선택 하이라이트 제거
        option.state &= ~option.state.State_Selected

        # 선택 시 테두리 강조
        if self.isSelected():
            self.setPen(QPen(QColor(Theme.ACCENT_PRIMARY), 3))
        else:
            self.setPen(QPen(QColor(Theme.PORT_EXEC), 2))
        super().paint(painter, option, widget)

        # 중앙에 작은 원
        painter.setBrush(QBrush(QColor(Theme.PORT_EXEC)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(0, 0), 4, 4)


class TextItem(SceneItemMixin, QGraphicsTextItem):
    """편집 가능한 텍스트 아이템 (그림판 스타일)"""

    ROTATE_HANDLE_DIST = 25

    def __init__(self, x: float, y: float, text: str = ""):
        super().__init__(text or t("label.text_default"))
        self.setPos(x, y)
        self.setZValue(60)

        # 스타일
        self.setDefaultTextColor(QColor(Theme.PORT_EXEC))
        font = QFont("맑은 고딕", 16)
        self.setFont(font)
        self._font_size = 16

        # 플래그
        self.setFlag(QGraphicsTextItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsTextItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsTextItem.GraphicsItemFlag.ItemIsFocusable, True)
        self.setFlag(QGraphicsTextItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

        # 리사이즈/회전 상태
        self._resizing = False
        self._rotating = False
        self._resize_start = None
        self._rotate_start = None
        self._initial_scale = 1.0

    def _text_rect(self) -> QRectF:
        """텍스트만의 사각형"""
        return QGraphicsTextItem.boundingRect(self)

    def boundingRect(self) -> QRectF:
        """바운딩 박스 (회전 핸들까지 포함)"""
        rect = self._text_rect()
        # 항상 회전 핸들 영역 포함 (선택 시 보이지만 이벤트는 항상 받아야 함)
        hs = self.HANDLE_SIZE
        return rect.adjusted(-hs, -self.ROTATE_HANDLE_DIST - hs, hs, hs)

    def shape(self):
        """충돌/클릭 영역"""
        path = QPainterPath()
        if self.isSelected():
            path.addRect(self.boundingRect())
        else:
            path.addRect(self._text_rect())
        return path

    def mouseDoubleClickEvent(self, event):
        """더블클릭으로 텍스트 편집 모드"""
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.setFocus()
        super().mouseDoubleClickEvent(event)

    def focusOutEvent(self, event):
        """포커스 잃으면 편집 모드 해제"""
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        cursor = self.textCursor()
        cursor.clearSelection()
        self.setTextCursor(cursor)
        super().focusOutEvent(event)

    def paint(self, painter, option, widget):
        """텍스트 + 선택 시 핸들 그리기"""
        # 선택 하이라이트 제거
        option.state &= ~option.state.State_Selected

        # 텍스트 그리기
        super().paint(painter, option, widget)

        # 선택 시 바운딩 박스 + 핸들
        if self.isSelected():
            rect = self._text_rect()
            hs = self.HANDLE_SIZE

            # 외곽선
            painter.setPen(QPen(QColor(Theme.ACCENT_PRIMARY), 1.5, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)

            # 코너 핸들 (리사이즈)
            painter.setPen(QPen(QColor(Theme.ACCENT_PRIMARY), 1))
            painter.setBrush(QBrush(QColor(Theme.PORT_EXEC)))

            corners = [
                rect.topLeft(),
                rect.topRight(),
                rect.bottomLeft(),
                rect.bottomRight(),
            ]
            for corner in corners:
                painter.drawRect(QRectF(corner.x() - hs/2, corner.y() - hs/2, hs, hs))

            # 회전 핸들 (상단 중앙 위)
            rotate_pos = QPointF(rect.center().x(), rect.top() - self.ROTATE_HANDLE_DIST)
            painter.setBrush(QBrush(QColor(Theme.ACCENT_WARNING)))
            painter.drawEllipse(rotate_pos, hs/2 + 2, hs/2 + 2)
            # 연결선
            painter.setPen(QPen(QColor(Theme.ACCENT_PRIMARY), 1))
            painter.drawLine(QPointF(rect.center().x(), rect.top()), rotate_pos)

    def _get_handle_at(self, pos: QPointF) -> str:
        """위치에 있는 핸들 타입 반환"""
        if not self.isSelected():
            return None

        rect = self._text_rect()
        hs = self.HANDLE_SIZE + 5  # 여유 있게

        # 회전 핸들 (먼저 체크)
        rotate_pos = QPointF(rect.center().x(), rect.top() - self.ROTATE_HANDLE_DIST)
        dist = math.sqrt((pos.x() - rotate_pos.x())**2 + (pos.y() - rotate_pos.y())**2)
        if dist < hs:
            return "rotate"

        # 코너 핸들 (우하단만 리사이즈)
        br = rect.bottomRight()
        if abs(pos.x() - br.x()) < hs and abs(pos.y() - br.y()) < hs:
            return "resize"

        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isSelected():
            handle = self._get_handle_at(event.pos())
            if handle == "resize":
                self._resizing = True
                self._resize_start = event.pos()
                self._initial_scale = self._font_size
                event.accept()
                return
            elif handle == "rotate":
                self._rotating = True
                self._rotate_start = event.scenePos()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resizing and self._resize_start:
            # 드래그 거리로 폰트 크기 조절
            delta = event.pos().y() - self._resize_start.y()
            new_size = max(8, min(72, self._initial_scale + delta / 3))
            self._font_size = int(new_size)
            font = self.font()
            font.setPointSize(self._font_size)
            self._begin_resize()
            self.setFont(font)
            self.update()
            event.accept()
            return
        elif self._rotating and self._rotate_start:
            # 중심 기준 각도 계산
            center = self.mapToScene(self._text_rect().center())
            current = event.scenePos()
            start_angle = math.atan2(self._rotate_start.y() - center.y(),
                                     self._rotate_start.x() - center.x())
            current_angle = math.atan2(current.y() - center.y(),
                                       current.x() - center.x())
            delta_angle = math.degrees(current_angle - start_angle)
            self.setRotation(self.rotation() + delta_angle)
            self._rotate_start = current
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._end_resize()
        self._rotating = False
        self._rotate_start = None
        super().mouseReleaseEvent(event)

    def get_data(self) -> Dict[str, Any]:
        d = self._base_data()
        d.update(type="text", text=self.toPlainText(),
                 font_size=self._font_size, rotation=self.rotation())
        return d


class ImageCardItem(SceneItemMixin, QGraphicsItem):
    """이미지 카드 아이템 - 보드에 이미지를 배치"""

    # 보드별 이미지 임시 폴더 (plugin이 설정)
    _board_temp_dir: str | None = None

    def __init__(self, x: float, y: float, image_path: str = "", width: float = 0, height: float = 0):
        super().__init__()
        self.setPos(x, y)
        self.image_path = image_path
        self.node_id = None
        self.input_port = None
        self.output_port = None
        self.on_image_changed = None  # callback: fn(card)
        self.setZValue(55)

        self._hidden = False  # 개별 이미지 가리기
        self._pixmap = QPixmap(image_path) if image_path and os.path.exists(image_path) else QPixmap()

        # 기본 크기 결정
        if width > 0 and height > 0:
            self._width = width
            self._height = height
        elif not self._pixmap.isNull():
            pw, ph = self._pixmap.width(), self._pixmap.height()
            max_side = 300
            if pw > ph:
                self._width = min(pw, max_side)
                self._height = self._width * ph / pw
            else:
                self._height = min(ph, max_side)
                self._width = self._height * pw / ph
        else:
            self._width = 200
            self._height = 150

        self._aspect = self._pixmap.width() / self._pixmap.height() if not self._pixmap.isNull() and self._pixmap.height() > 0 else 1.0

        # 스케일드 픽스맵 캐시
        self._scaled_pixmap = None
        self._scaled_key = None  # (width, height, pixmap_cacheKey)

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsFocusable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

        self._resizing = False
        self._resize_start = None
        self._initial_size = None

    def boundingRect(self) -> QRectF:
        hs = self.HANDLE_SIZE
        return QRectF(-hs, -hs, self._width + 2 * hs, self._height + 2 * hs)

    def paint(self, painter: QPainter, option, widget):
        option.state &= ~option.state.State_Selected

        rect = QRectF(0, 0, self._width, self._height)

        if self._hidden and self.image_path:
            # 개별 이미지 가리기: 플레이스홀더 + 크기 텍스트
            painter.setBrush(QBrush(QColor(Theme.BG_INPUT)))
            painter.setPen(QPen(QColor(Theme.TEXT_DISABLED), 1.5, Qt.PenStyle.DashLine))
            painter.drawRoundedRect(rect, 8, 8)
            size_text = f"{int(self._width)} x {int(self._height)}"
            painter.setPen(QPen(QColor(Theme.TEXT_TERTIARY), 1))
            font = painter.font()
            font.setPointSize(10)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, size_text)
        elif not self._pixmap.isNull():
            key = (int(self._width), int(self._height), self._pixmap.cacheKey())
            if self._scaled_pixmap is None or self._scaled_key != key:
                self._scaled_pixmap = self._pixmap.scaled(
                    int(self._width), int(self._height),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                self._scaled_key = key
            painter.drawPixmap(QPointF(0, 0), self._scaled_pixmap)
        else:
            # 빈 이미지 카드 플레이스홀더
            painter.setBrush(QBrush(QColor(Theme.BG_INPUT)))
            painter.setPen(QPen(QColor(Theme.TEXT_DISABLED), 1.5, Qt.PenStyle.DashLine))
            painter.drawRoundedRect(rect, 8, 8)
            # + 아이콘
            cx, cy = self._width / 2, self._height / 2
            s = min(self._width, self._height) * 0.15
            painter.setPen(QPen(QColor(Theme.TEXT_TERTIARY), 2.5, Qt.PenStyle.SolidLine))
            painter.drawLine(QPointF(cx - s, cy), QPointF(cx + s, cy))
            painter.drawLine(QPointF(cx, cy - s), QPointF(cx, cy + s))

        # 선택 시 테두리 + 리사이즈 핸들
        if self.isSelected():
            painter.setPen(QPen(QColor(Theme.ACCENT_PRIMARY), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)
            self._paint_resize_handle(painter, rect)

    def set_image(self, path: str):
        """이미지 설정 — 원본을 관리 폴더에 복사하여 원본 삭제 시에도 유지"""
        if path and os.path.exists(path):
            path = self._copy_to_managed(path)
        self.image_path = path
        self._pixmap = QPixmap(path) if path and os.path.exists(path) else QPixmap()
        if not self._pixmap.isNull():
            pw, ph = self._pixmap.width(), self._pixmap.height()
            self._aspect = pw / ph if ph > 0 else 1.0
            max_side = 300
            if pw > ph:
                new_w = min(pw, max_side)
                new_h = new_w * ph / pw
            else:
                new_h = min(ph, max_side)
                new_w = new_h * pw / ph
            self.prepareGeometryChange()
            self._width = new_w
            self._height = new_h
        self.update()
        if self.output_port:
            self.output_port.reposition()
        if self.input_port:
            self.input_port.reposition()
        if self.on_image_changed:
            self.on_image_changed(self)

    def toggle_hidden(self):
        """이미지 가리기/보이기 토글"""
        self._hidden = not self._hidden
        _logger.info(f"[IMAGE_CARD] node_id={self.node_id} hidden={self._hidden} path={self.image_path}")
        if not self._hidden and self._pixmap.isNull() and self.image_path:
            # 가리기 해제 시 pixmap 리로드
            resolved = self.image_path
            if not os.path.isabs(resolved) or not os.path.exists(resolved):
                if ImageCardItem._board_temp_dir:
                    candidate = os.path.join(ImageCardItem._board_temp_dir, os.path.basename(resolved))
                    if os.path.exists(candidate):
                        resolved = candidate
            if os.path.exists(resolved):
                self._pixmap = QPixmap(resolved)
                if not self._pixmap.isNull():
                    self._aspect = self._pixmap.width() / self._pixmap.height() if self._pixmap.height() > 0 else 1.0
                    self._scaled_pixmap = None
                    self._scaled_key = None
        self.update()

    def _copy_to_managed(self, src_path: str) -> str:
        """이미지를 보드별 temp 폴더에 복사 — 이미 관리 폴더 안이면 스킵"""
        import shutil
        import uuid as _uuid

        # 보드별 temp 사용 (plugin이 설정). 없으면 폴백.
        if ImageCardItem._board_temp_dir:
            managed_dir = Path(ImageCardItem._board_temp_dir)
        else:
            from v.settings import get_app_data_path
            managed_dir = get_app_data_path() / "temp" / "board_images"
        managed_dir.mkdir(parents=True, exist_ok=True)

        src = Path(src_path)
        # 이미 관리 폴더 안에 있으면 복사 불필요
        try:
            src.resolve().relative_to(managed_dir.resolve())
            return src_path
        except ValueError:
            pass

        # 고유 파일명 생성: UUID + 확장자
        dest = managed_dir / f"{_uuid.uuid4().hex}{src.suffix}"
        try:
            shutil.copy2(str(src), str(dest))
            return str(dest)
        except Exception:
            return src_path  # 복사 실패 시 원본 경로 유지

    def _upload_image(self):
        """파일 다이얼로그로 이미지 업로드"""
        scene = self.scene()
        if not scene or not scene.views():
            return
        from PyQt6.QtWidgets import QFileDialog
        fpath, _ = QFileDialog.getOpenFileName(
            scene.views()[0], "Select Image", "",
            "Images (*.png *.jpg *.jpeg *.gif *.bmp *.webp);;All Files (*.*)"
        )
        if fpath:
            self.set_image(fpath)

    def mouseDoubleClickEvent(self, event):
        """더블클릭: 빈 카드 → 업로드 / 이미지 있으면 → 원본 열기"""
        if self._pixmap.isNull():
            self._upload_image()
        else:
            if self.image_path and os.path.exists(self.image_path):
                from PyQt6.QtGui import QDesktopServices
                from PyQt6.QtCore import QUrl
                QDesktopServices.openUrl(QUrl.fromLocalFile(self.image_path))
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isSelected():
            if self._is_near_bottom_right(event.pos(), self._width, self._height):
                self._resizing = True
                self._resize_start = event.scenePos()
                self._initial_size = (self._width, self._height)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resizing and self._resize_start and self._initial_size:
            delta = event.scenePos() - self._resize_start
            new_w = max(60, self._initial_size[0] + delta.x())
            new_h = new_w / self._aspect if self._aspect > 0 else new_w
            self._begin_resize()
            self._width = new_w
            self._height = max(40, new_h)
            self.update()
            self._reposition_own_ports()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._end_resize()
        self._initial_size = None
        super().mouseReleaseEvent(event)

    def get_data(self) -> Dict[str, Any]:
        d = self._base_data()
        d.update(type="image_card", node_id=self.node_id,
                 width=self._width, height=self._height,
                 image_path=self.image_path,
                 hidden=self._hidden)
        return d


class _LockButtonItem(QGraphicsTextItem):
    """GroupFrameItem용 잠금 토글 버튼 (클릭 가능)."""

    def __init__(self, parent_frame: 'GroupFrameItem'):
        super().__init__(parent_frame)
        self._frame = parent_frame
        self.setDefaultTextColor(QColor("#999999"))
        font = QFont("맑은 고딕", 9)
        self.setFont(font)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.update_text()

    def update_text(self):
        self.setPlainText("해제" if self._frame._locked else "잠금")
        self.reposition()

    def reposition(self):
        """프레임 우상단에 위치."""
        r = self._frame.rect()
        btn_w = self.boundingRect().width()
        self.setPos(r.right() - btn_w - 8, 2)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._frame.set_locked(not self._frame._locked)
            event.accept()


GROUP_COLORS = {
    "blue":  ("#3a7bd5", 40),
    "green": ("#2d8659", 40),
    "gray":  ("#666666", 30),
    "red":   ("#c0392b", 35),
}


class GroupFrameItem(SceneItemMixin, QGraphicsRectItem):
    """그룹 프레임 - 아이템을 묶어서 함께 이동"""

    LABEL_HEIGHT = 24

    def __init__(self, x: float, y: float, width: float = 400, height: float = 300,
                 label: str = "", color: str = "blue"):
        super().__init__(0, 0, width, height)
        self.setPos(x, y)
        self.color_name = color if color in GROUP_COLORS else "blue"
        self._locked = False
        self.setZValue(-50)

        self._apply_style()

        self.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsFocusable, True)
        self.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

        # 라벨
        self._label = QGraphicsTextItem(label or "Group", self)
        self._label.setDefaultTextColor(QColor("#cccccc"))
        font = QFont("맑은 고딕", 11)
        font.setBold(True)
        self._label.setFont(font)
        self._label.setPos(8, 2)
        self._label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

        # 잠금 버튼
        self._lock_btn = _LockButtonItem(self)

        # 그룹 드래그 상태
        self._grouped_items = []
        self._group_offsets = []
        self._last_pos = self.pos()

        # 리사이즈 상태
        self._resizing = False
        self._resize_corner = None
        self._resize_start = None
        self._initial_rect = None

    def set_locked(self, locked: bool):
        """잠금 상태 설정. 잠금 시 마우스 이벤트가 뒤의 아이템으로 투과."""
        self._locked = locked
        self.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsMovable, not locked)
        self.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable, not locked)
        self.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsFocusable, not locked)
        if locked and self.hasFocus():
            self.clearFocus()
        if locked:
            self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self._label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            all_btns = (Qt.MouseButton.LeftButton
                        | Qt.MouseButton.RightButton
                        | Qt.MouseButton.MiddleButton)
            self.setAcceptedMouseButtons(all_btns)
            self._label.setAcceptedMouseButtons(all_btns)
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._apply_style()
        self._lock_btn.update_text()

    def _apply_style(self):
        accent, alpha = GROUP_COLORS[self.color_name]
        bg = QColor(accent)
        bg.setAlpha(alpha)
        self.setBrush(QBrush(bg))
        border = QColor(accent)
        border.setAlpha(120)
        style = Qt.PenStyle.SolidLine if self._locked else Qt.PenStyle.DashLine
        self.setPen(QPen(border, 2, style))

    def mouseDoubleClickEvent(self, event):
        """더블클릭: 라벨 편집"""
        self._label.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self._label.setFocus()
        event.accept()

    def _collect_grouped_items(self):
        """프레임 안의 아이템 수집 (공간 인덱스 활용)"""
        self._grouped_items = []
        self._group_offsets = []
        frame_rect = self.sceneBoundingRect()
        my_pos = self.pos()

        # 공간 쿼리로 프레임 영역 내 아이템만 검사
        for item in self.scene().items(frame_rect, Qt.ItemSelectionMode.IntersectsItemBoundingRect):
            if item is self or item is self._label or item is self._lock_btn:
                continue
            if isinstance(item, (PortItem, TempEdgeItem, EdgeItem)):
                continue
            if isinstance(item, QGraphicsRectItem) and item.parentItem() is not None:
                continue
            if isinstance(item, QGraphicsEllipseItem) and item.zValue() == -100:
                continue
            if isinstance(item, GroupFrameItem):
                continue

            item_pos = item.pos()
            if frame_rect.contains(item_pos):
                self._grouped_items.append(item)
                self._group_offsets.append(item_pos - my_pos)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # 리사이즈 핸들 체크
            if self.isSelected():
                corner = self._get_resize_corner(event.pos())
                if corner:
                    self._resizing = True
                    self._resize_corner = corner
                    self._resize_start = event.scenePos()
                    self._initial_rect = self.rect()
                    event.accept()
                    return

            self._collect_grouped_items()
            self._last_pos = self.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resizing and self._resize_start:
            delta = event.scenePos() - self._resize_start
            r = QRectF(self._initial_rect)
            if "right" in self._resize_corner:
                r.setWidth(max(100, r.width() + delta.x()))
            if "bottom" in self._resize_corner:
                r.setHeight(max(80, r.height() + delta.y()))
            self._begin_resize()
            self.setRect(r)
            self._lock_btn.reposition()
            self.update()
            event.accept()
            return

        super().mouseMoveEvent(event)

        # U8: 그룹 이동 시 _group_moving 설정으로 재귀 방지 + 포트 갱신 보장
        if self._grouped_items:
            global _group_moving
            _group_moving = True
            try:
                new_pos = self.pos()
                for item, offset in zip(self._grouped_items, self._group_offsets):
                    item.setPos(new_pos + offset)
            finally:
                _group_moving = False

    def mouseReleaseEvent(self, event):
        self._grouped_items = []
        self._group_offsets = []
        self._end_resize()
        self._resize_corner = None
        self._initial_rect = None
        if not self._label.hasFocus():
            self._label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        super().mouseReleaseEvent(event)

    def _get_resize_corner(self, pos: QPointF) -> str:
        """리사이즈 코너 감지"""
        r = self.rect()
        hs = self.HANDLE_SIZE + 5
        result = ""
        if abs(pos.x() - r.right()) < hs:
            result += "right"
        if abs(pos.y() - r.bottom()) < hs:
            result += "bottom"
        return result or None

    def paint(self, painter, option, widget):
        option.state &= ~option.state.State_Selected
        super().paint(painter, option, widget)

        if self.isSelected():
            self._paint_resize_handle(painter, self.rect())

    def get_data(self) -> Dict[str, Any]:
        d = self._base_data()
        d.update(type="group_frame", width=self.rect().width(),
                 height=self.rect().height(),
                 label=self._label.toPlainText(), color=self.color_name,
                 locked=self._locked)
        return d
