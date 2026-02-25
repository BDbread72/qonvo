"""
방사형 메뉴 시스템
- RadialMenuItem: 메뉴 아이템
- RadialCursor: 커스텀 커서
- OriginMarker: 원래 마우스 위치 마커
- RadialMenu: 메뉴 본체
"""
import math

from PyQt6.QtWidgets import QGraphicsEllipseItem, QGraphicsTextItem
from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QPen, QBrush, QColor, QFont
from v.theme import Theme


def _paint_icon(painter, icon_key, rect):
    """아이콘 그리기 — IconManager에서 SVG pixmap 로드"""
    from v.icon_manager import icon_manager
    pixmap = icon_manager.get_pixmap(icon_key)
    if pixmap and not pixmap.isNull():
        c = rect.center()
        px = int(c.x() - pixmap.width() / 2)
        py = int(c.y() - pixmap.height() / 2)
        painter.drawPixmap(px, py, pixmap)
    else:
        # 폴백: 텍스트
        painter.setFont(QFont("Segoe UI Emoji", 14))
        painter.setPen(QColor("white"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, icon_key[:2])


class RadialMenuItem(QGraphicsEllipseItem):
    """방사형 메뉴 아이템"""

    def __init__(self, icon: str, label: str, callback, angle: float, index: int, radius: float = 110):
        from v.constants import RADIAL_MENU_ITEM_SIZE
        size = RADIAL_MENU_ITEM_SIZE
        super().__init__(-size/2, -size/2, size, size)
        self.callback = callback
        self.label = label
        self.icon = icon
        self.angle = angle  # 이 아이템의 각도
        self.index = index
        self._selected = False

        # 위치 계산 (각도 → 좌표)
        rad = math.radians(angle - 90)  # 12시 방향이 0도
        x = radius * math.cos(rad)
        y = radius * math.sin(rad)
        self.setPos(x, y)

        self._update_style()
        self.setZValue(1000)

        # 클릭 이벤트 활성화
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_selected(self, selected: bool):
        self._selected = selected
        self._update_style()

    def _update_style(self):
        if self._selected:
            self.setBrush(QBrush(QColor(Theme.ACCENT_PRIMARY)))
            self.setPen(QPen(QColor("#3d8bfd"), 3))
        else:
            self.setBrush(QBrush(QColor(Theme.BG_SECONDARY)))
            self.setPen(QPen(QColor(Theme.TEXT_DISABLED), 2))

    def paint(self, painter, option, widget):
        super().paint(painter, option, widget)
        _paint_icon(painter, self.icon, self.boundingRect())

    def mousePressEvent(self, event):
        """클릭 이벤트 — view.py에서 처리하므로 여기서는 무시"""
        # view.py의 mousePressEvent에서 아이템을 찾아 callback을 실행함
        event.accept()


class RadialCursor(QGraphicsEllipseItem):
    """마우스를 따라다니는 커스텀 커서"""

    def __init__(self):
        size = 12
        super().__init__(-size/2, -size/2, size, size)
        self._base_size = size
        self._target_size = size
        self._current_size = float(size)
        self._target_pos = QPointF(0, 0)
        self._current_pos = QPointF(0, 0)
        self._target_opacity = 1.0
        self._current_opacity = 1.0

        # 흰색 커서 + 테두리
        self.setBrush(QBrush(QColor(Theme.PORT_EXEC)))
        self.setPen(QPen(QColor("#333333"), 2))
        self.setZValue(2000)

    def set_target(self, pos: QPointF, selected: bool):
        """목표 위치와 선택 상태 설정"""
        self._target_pos = pos
        if selected:
            self._target_size = 20
            self._target_opacity = 0.5
        else:
            self._target_size = self._base_size
            self._target_opacity = 1.0

    def animate_step(self):
        """한 프레임 애니메이션"""
        ease = 0.2

        # ease 위치
        self._current_pos = QPointF(
            self._current_pos.x() + (self._target_pos.x() - self._current_pos.x()) * ease,
            self._current_pos.y() + (self._target_pos.y() - self._current_pos.y()) * ease
        )
        self.setPos(self._current_pos)

        # ease 크기
        self._current_size += (self._target_size - self._current_size) * ease
        half = self._current_size / 2
        self.setRect(-half, -half, self._current_size, self._current_size)

        # ease 투명도
        self._current_opacity += (self._target_opacity - self._current_opacity) * ease
        self.setOpacity(self._current_opacity)


class OriginMarker(QGraphicsEllipseItem):
    """원래 마우스 위치를 표시하는 마커"""

    def __init__(self, offset: QPointF):
        size = 10
        super().__init__(-size/2, -size/2, size, size)
        self.setPos(offset)
        self.setBrush(QBrush(QColor(Theme.ACCENT_WARNING)))
        self.setPen(QPen(QColor("#ff9900"), 2))
        self.setZValue(998)
        self.setOpacity(0.8)


class RadialMenu(QGraphicsEllipseItem):
    """게임 스타일 방사형 메뉴 (TAB 홀드 + 마우스 방향)"""

    def __init__(self, items, on_close=None, origin_offset: QPointF = None):
        from v.constants import RADIAL_MENU_CENTER_SIZE, RADIAL_MENU_RADIUS, RADIAL_MENU_OPEN_DURATION_MS

        # 중앙 원
        center_size = RADIAL_MENU_CENTER_SIZE
        super().__init__(-center_size/2, -center_size/2, center_size, center_size)
        self.setBrush(QBrush(QColor(Theme.BG_PRIMARY)))
        self.setPen(QPen(QColor(Theme.TEXT_DISABLED), 2))
        self.setZValue(999)
        self.setFlag(self.GraphicsItemFlag.ItemIgnoresTransformations)
        self.on_close = on_close
        self.items_data = items
        self.menu_items: list[RadialMenuItem] = []
        self.selected_index = -1

        # 오픈 애니메이션 상태
        self._open_progress = 0.0  # 0.0 ~ 1.0
        self._open_duration_ms = RADIAL_MENU_OPEN_DURATION_MS
        self._is_opening = True

        # 원래 마우스 위치 마커
        if origin_offset:
            self.origin_marker = OriginMarker(origin_offset)
            self.origin_marker.setParentItem(self)
        else:
            self.origin_marker = None

        # 커스텀 커서
        self.cursor_item = RadialCursor()
        self.cursor_item.setParentItem(self)

        # 라벨 텍스트 (중앙 아래)
        self.label_item = QGraphicsTextItem("", self)
        self.label_item.setDefaultTextColor(QColor(Theme.PORT_EXEC))
        self.label_item.setFont(QFont("맑은 고딕", 12, QFont.Weight.Bold))
        self.label_item.setZValue(1001)

        # 아이템 배치 (단일 링)
        n = len(items)
        if n > 0:
            angle_step = 360 / n
            for i, (icon, label, callback) in enumerate(items):
                angle = i * angle_step
                item = RadialMenuItem(icon, label, callback, angle, i, radius=RADIAL_MENU_RADIUS)
                item.setParentItem(self)
                self.menu_items.append(item)
                # 초기 스케일 0 (애니메이션)
                item.setScale(0.0)
                item.setOpacity(0.0)

    def update_cursor_pos(self, local_pos: QPointF):
        """커서 위치 업데이트 (메뉴 로컬 좌표)"""
        self.cursor_item.set_target(local_pos, self.selected_index >= 0)

    def animate(self):
        """애니메이션 프레임"""
        # 오픈 애니메이션
        if self._is_opening:
            from v.constants import ANIMATION_INTERVAL_MS
            self._open_progress += ANIMATION_INTERVAL_MS / self._open_duration_ms
            if self._open_progress >= 1.0:
                self._open_progress = 1.0
                self._is_opening = False

            # 메뉴 아이템들 스케일 + 투명도 애니메이션
            for item in self.menu_items:
                # 부드러운 easing (ease-out)
                eased = self._ease_out_cubic(self._open_progress)
                item.setScale(eased)
                item.setOpacity(eased)

        # 커서 애니메이션
        self.cursor_item.animate_step()

    def _ease_out_cubic(self, t: float) -> float:
        """Cubic ease-out animation curve"""
        t = min(1.0, max(0.0, t))
        return 1.0 - ((1.0 - t) ** 3)

    def update_selection_by_angle(self, angle_deg: float, distance: float):
        """마우스 각도로 선택 아이템 업데이트"""
        # 중앙에서 너무 가까우면 선택 없음
        if distance < 30:
            self._set_selected(-1)
            return

        n = len(self.menu_items)
        if n == 0:
            return

        # 각도를 0~360으로 정규화 (12시 방향이 0도)
        angle_deg = (angle_deg + 90) % 360

        # 어떤 아이템에 해당하는지 계산
        angle_step = 360 / n
        index = int((angle_deg + angle_step / 2) % 360 / angle_step)
        self._set_selected(index)

    def _set_selected(self, index: int):
        if self.selected_index == index:
            return

        # 이전 선택 해제
        if 0 <= self.selected_index < len(self.menu_items):
            self.menu_items[self.selected_index].set_selected(False)

        self.selected_index = index

        # 새 선택
        if 0 <= index < len(self.menu_items):
            self.menu_items[index].set_selected(True)
            label = self.menu_items[index].label
            self.label_item.setPlainText(label)
            rect = self.label_item.boundingRect()
            self.label_item.setPos(-rect.width()/2, 130)
            self.label_item.show()
        else:
            self.label_item.hide()

    def execute_selected(self):
        """선택된 아이템 실행"""
        if 0 <= self.selected_index < len(self.menu_items):
            item = self.menu_items[self.selected_index]
            if item.callback:
                item.callback()

    def close(self):
        if self.scene():
            self.scene().removeItem(self)
        if self.on_close:
            self.on_close()
