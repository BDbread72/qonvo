"""
DimensionItem - 보드에 배치되는 차원 오브젝트 (포탈)
내부에 완전한 화이트보드 데이터를 저장
"""
import copy
import math
from typing import Dict, Any

from PyQt6.QtWidgets import QGraphicsItem
from PyQt6.QtCore import Qt, QPointF, QRectF, QTimer
from PyQt6.QtGui import (
    QPen, QBrush, QColor, QPainterPath, QFont,
    QPainter, QLinearGradient,
)

from v.theme import Theme
from v.boards.whiteboard.items import SceneItemMixin


def _get_empty_board_data() -> Dict[str, Any]:
    """빈 보드 데이터 생성"""
    return {
        "type": "WhiteBoard",
        "nodes": [],

        "function_nodes": [],
        "round_tables": [],
        "functions_library": [],
        "edges": [],
        "pins": [],
        "texts": [],
        "sticky_notes": [],
        "image_cards": [],
        "checklists": [],
        "group_frames": [],
        "dimensions": [],  # 중첩 Dimension 지원
        "next_id": 1,
        "system_prompt": "",
        "system_files": [],
    }


class DimensionItem(SceneItemMixin, QGraphicsItem):
    """보드에 배치되는 Dimension 오브젝트 (포탈)

    내부에 완전한 화이트보드 데이터(_board_data)를 저장.
    더블클릭하면 DimensionBoardWindow가 열려서 내부 보드를 편집할 수 있음.
    """

    def __init__(self, x: float, y: float, width: float = 200, height: float = 150):
        super().__init__()
        self.setPos(x, y)

        # ID 및 포트
        self.node_id = None
        self.input_port = None
        self.output_port = None

        # 내부 보드 데이터 (collect_data 형식)
        self._board_data: Dict[str, Any] = _get_empty_board_data()
        self._title = "Dimension"

        # 크기
        self._width = width
        self._height = height

        # 울렁임 애니메이션
        self._wave_phase = 0.0
        self._cached_wave_path = None
        self._wave_timer = QTimer()
        self._wave_timer.timeout.connect(self._wave_tick)
        self._wave_timer.setInterval(66)  # ~15fps (충분한 울렁임 효과)
        self._wave_timer.start()

        # 캐싱: gradient, node count
        self._cached_gradient = None
        self._cached_gradient_height = -1.0
        self._cached_node_count = 0
        self._node_count_dirty = True

        # 플래그
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsFocusable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setZValue(55)

        # 리사이즈 상태
        self._resizing = False
        self._resize_start = None
        self._initial_size = None

        # 콜백
        self.on_double_click = None  # fn(item)

    def boundingRect(self) -> QRectF:
        hs = self.HANDLE_SIZE
        margin = 5  # 울렁임을 위한 여유 공간
        return QRectF(-hs - margin, -hs - margin,
                      self._width + 2 * hs + 2 * margin,
                      self._height + 2 * hs + 2 * margin)

    def shape(self) -> QPainterPath:
        """클릭 영역"""
        path = QPainterPath()
        path.addRect(0, 0, self._width, self._height)
        return path

    def _wave_tick(self):
        """애니메이션 틱"""
        self._wave_phase += 0.03
        if self._wave_phase > 2 * math.pi * 100:
            self._wave_phase = 0
        self._cached_wave_path = None
        self.update()

    def _get_wave_path(self) -> QPainterPath:
        """sin파 기반 울렁이는 외곽선 생성 (캐싱)"""
        if self._cached_wave_path is not None:
            return self._cached_wave_path

        path = QPainterPath()
        amplitude = 2.5
        frequency = 0.08

        w, h = self._width, self._height
        phase = self._wave_phase

        # 상단 (좌→우)
        steps = max(int(w / 4), 10)
        for i in range(steps + 1):
            x = w * i / steps
            y = amplitude * math.sin(phase + x * frequency)
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)

        # 우측 (상→하)
        steps = max(int(h / 4), 10)
        for i in range(1, steps + 1):
            y = h * i / steps
            x = w + amplitude * math.sin(phase + y * frequency + 1.5)
            path.lineTo(x, y)

        # 하단 (우→좌)
        steps = max(int(w / 4), 10)
        for i in range(1, steps + 1):
            x = w - w * i / steps
            y = h + amplitude * math.sin(phase + x * frequency + 3.0)
            path.lineTo(x, y)

        # 좌측 (하→상)
        steps = max(int(h / 4), 10)
        for i in range(1, steps + 1):
            y = h - h * i / steps
            x = amplitude * math.sin(phase + y * frequency + 4.5)
            path.lineTo(x, y)

        path.closeSubpath()
        self._cached_wave_path = path
        return path

    def _get_node_count(self) -> int:
        """내부 보드의 노드 개수 (캐싱)"""
        if self._node_count_dirty:
            count = 0
            count += len(self._board_data.get("nodes", []))
            count += len(self._board_data.get("function_nodes", []))
            count += len(self._board_data.get("sticky_notes", []))
            count += len(self._board_data.get("image_cards", []))
            count += len(self._board_data.get("dimensions", []))
            self._cached_node_count = count
            self._node_count_dirty = False
        return self._cached_node_count

    def _get_gradient(self) -> QLinearGradient:
        """캐싱된 그라디언트 반환 (_height 변경 시만 재생성)"""
        if self._cached_gradient is None or self._cached_gradient_height != self._height:
            self._cached_gradient = QLinearGradient(0, 0, 0, self._height)
            self._cached_gradient.setColorAt(0, QColor("#2d1f3d"))
            self._cached_gradient.setColorAt(1, QColor("#1a1a2e"))
            self._cached_gradient_height = self._height
        return self._cached_gradient

    def paint(self, painter: QPainter, option, widget):
        option.state &= ~option.state.State_Selected

        # 울렁이는 외곽선
        path = self._get_wave_path()

        # 그라디언트 배경 (보라색 계열, 캐싱됨)
        painter.fillPath(path, self._get_gradient())

        # 울렁이는 테두리
        pen = QPen(QColor("#8e44ad"), 2)
        painter.setPen(pen)
        painter.drawPath(path)

        # 내부 글로우 효과
        inner_pen = QPen(QColor(142, 68, 173, 80), 4)
        painter.setPen(inner_pen)
        painter.drawPath(path)

        # 제목
        painter.setPen(QColor("#e0e0e0"))
        font = QFont("Segoe UI", 11)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QRectF(12, 10, self._width - 24, 24),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         self._title)

        # 내부 노드 개수 표시
        count = self._get_node_count()
        count_text = f"{count}개 오브젝트" if count > 0 else "비어있음"
        painter.setFont(QFont("Segoe UI", 9))
        painter.setPen(QColor("#888"))
        painter.drawText(QRectF(12, 38, self._width - 24, 20),
                         Qt.AlignmentFlag.AlignLeft,
                         count_text)

        # 중앙 포탈 아이콘 (동심원)
        cx, cy = self._width / 2, self._height / 2 + 15
        painter.setPen(QPen(QColor("#9b59b6"), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)

        for i in range(3):
            r = 12 + i * 7
            offset = 2 * math.sin(self._wave_phase * 2 + i)
            painter.drawEllipse(QPointF(cx, cy + offset), r, r * 0.6)

        # 선택 시 핸들
        if self.isSelected():
            painter.setPen(QPen(QColor("#0d6efd"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            rect = QRectF(0, 0, self._width, self._height)
            painter.drawRect(rect)
            self._paint_resize_handle(painter, rect)

    def mouseDoubleClickEvent(self, event):
        """더블클릭 → 내부 보드 창 열기"""
        if self.on_double_click:
            self.on_double_click(self)
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
            new_w = max(150, self._initial_size[0] + delta.x())
            new_h = max(120, self._initial_size[1] + delta.y())
            self._begin_resize()
            self._width = new_w
            self._height = new_h
            self.update()
            self._reposition_own_ports()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._end_resize()
        self._initial_size = None
        super().mouseReleaseEvent(event)

    # 데이터 관리
    def set_title(self, title: str):
        """제목 설정"""
        self._title = title
        self.update()

    def get_board_data(self) -> Dict[str, Any]:
        """내부 보드 데이터 반환"""
        return self._board_data

    def set_board_data(self, data: Dict[str, Any]):
        """내부 보드 데이터 설정"""
        self._board_data = data
        self._node_count_dirty = True
        self.update()

    # 직렬화
    def get_data(self) -> Dict[str, Any]:
        d = self._base_data()
        # deep copy: 저장 시 _save_impl이 경로를 상대 경로로 변환하는데,
        # 참조를 공유하면 in-memory _board_data가 오염됨
        d.update(type="dimension", node_id=self.node_id,
                 width=self._width, height=self._height,
                 title=self._title, board_data=copy.deepcopy(self._board_data))
        return d

    @staticmethod
    def from_data(data: Dict[str, Any]) -> "DimensionItem":
        """역직렬화"""
        item = DimensionItem(
            x=data.get("x", 0),
            y=data.get("y", 0),
            width=data.get("width", 200),
            height=data.get("height", 150),
        )
        item._title = data.get("title", "Dimension")
        item._board_data = data.get("board_data", _get_empty_board_data())
        if data.get("node_id") is not None:
            item.node_id = data["node_id"]
        return item

    def stop_animation(self):
        """애니메이션 정지 (정리용)"""
        if self._wave_timer.isActive():
            self._wave_timer.stop()
