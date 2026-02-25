"""
PPT 스타일 스냅 가이드 엔진
아이템 드래그 시 다른 오브젝트의 가장자리에 자동 정렬
CapsLock ON = 자유 배치 모드 (스냅 비활성화)
"""
from __future__ import annotations

import ctypes
from typing import List, Optional, Tuple

from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QPen, QColor
from PyQt6.QtWidgets import (
    QGraphicsItem, QGraphicsLineItem, QGraphicsProxyWidget,
)

from v.constants import SNAP_THRESHOLD

# 스냅 대상 아이템 타입 (순환 import 방지를 위해 런타임 체크)
_SNAP_TYPES: tuple = ()


def _ensure_snap_types():
    """지연 import로 스냅 대상 타입 로드."""
    global _SNAP_TYPES
    if _SNAP_TYPES:
        return
    from .items import TextItem, ImageCardItem, GroupFrameItem
    from .dimension_item import DimensionItem
    _SNAP_TYPES = (TextItem, ImageCardItem, GroupFrameItem,
                   DimensionItem, QGraphicsProxyWidget)


def _is_caps_lock() -> bool:
    """CapsLock 활성 여부 (Windows)."""
    try:
        return bool(ctypes.windll.user32.GetKeyState(0x14) & 1)
    except Exception:
        return False


def _item_rect(item: QGraphicsItem) -> Optional[QRectF]:
    """아이템의 씬 좌표 바운딩 렉트 반환."""
    try:
        return item.sceneBoundingRect()
    except Exception:
        return None


def _edges(rect: QRectF) -> Tuple[List[float], List[float]]:
    """rect의 X 엣지 3개, Y 엣지 3개를 반환.

    Returns: ([left, cx, right], [top, cy, bottom])
    """
    xs = [rect.left(), rect.center().x(), rect.right()]
    ys = [rect.top(), rect.center().y(), rect.bottom()]
    return xs, ys


class SnapEngine:
    """PPT 스타일 스냅 가이드 엔진.

    scene._snap_engine 으로 접근.
    itemChange(ItemPositionChange)에서 snap()을 호출해
    proposed position을 스냅된 위치로 보정한다.
    """

    def __init__(self, scene):
        self.scene = scene
        self._guides: List[QGraphicsLineItem] = []
        self._guide_pen = QPen(QColor("#ff4444"), 1)
        self._guide_pen.setDashPattern([4, 4])
        self._snapping = False

    # ── public API ───────────────────────────────────────

    def snap(self, moving_item: QGraphicsItem, proposed_pos: QPointF) -> QPointF:
        """이동 아이템의 제안 위치를 스냅 보정하여 반환."""
        if self._snapping:
            return proposed_pos

        if _is_caps_lock():
            self.clear_guides()
            return proposed_pos

        _ensure_snap_types()

        self._snapping = True
        try:
            # 이동 아이템의 제안 위치 기준 렉트 계산
            moving_rect = self._rect_at(moving_item, proposed_pos)
            if moving_rect is None or moving_rect.isEmpty():
                self.clear_guides()
                return proposed_pos

            # 후보 아이템 수집 (뷰포트 + 여유 영역)
            candidates = self._get_candidates(moving_item)
            if not candidates:
                self.clear_guides()
                return proposed_pos

            # 스냅 계산
            mx, my = _edges(moving_rect)
            best_dx, best_snap_x = self._find_best_snap(mx, candidates, axis="x")
            best_dy, best_snap_y = self._find_best_snap(my, candidates, axis="y")

            # 위치 보정
            result = QPointF(proposed_pos)
            if best_dx is not None:
                result.setX(proposed_pos.x() + best_dx)
            if best_dy is not None:
                result.setY(proposed_pos.y() + best_dy)

            # 가이드 라인 업데이트
            self._update_guides(moving_item, best_snap_x, best_snap_y)

            return result
        finally:
            self._snapping = False

    def clear_guides(self):
        """모든 가이드 라인 제거."""
        for line in self._guides:
            try:
                self.scene.removeItem(line)
            except Exception:
                pass
        self._guides.clear()

    # ── internal ─────────────────────────────────────────

    def _rect_at(self, item: QGraphicsItem, pos: QPointF) -> Optional[QRectF]:
        """item이 pos에 있을 때의 씬 좌표 바운딩 렉트."""
        br = item.boundingRect()
        if br.isEmpty():
            return None
        return QRectF(
            pos.x() + br.x(),
            pos.y() + br.y(),
            br.width(),
            br.height(),
        )

    def _get_candidates(self, moving_item: QGraphicsItem) -> List[QRectF]:
        """뷰포트 주변의 스냅 후보 렉트 목록."""
        views = self.scene.views()
        if not views:
            return []

        view = views[0]
        # 뷰포트 영역 (씬 좌표) + 여유 500px
        vp = view.mapToScene(view.viewport().rect()).boundingRect()
        search = vp.adjusted(-500, -500, 500, 500)

        rects: List[QRectF] = []
        for item in self.scene.items(search):
            if item is moving_item:
                continue
            # 이동 아이템의 자식도 제외
            if item.parentItem() is moving_item:
                continue
            # 가이드 라인 제외
            if item in self._guides:
                continue
            # 스냅 대상 타입만
            if not isinstance(item, _SNAP_TYPES):
                continue
            r = _item_rect(item)
            if r and not r.isEmpty():
                rects.append(r)

        return rects

    def _find_best_snap(
        self,
        moving_edges: List[float],
        candidates: List[QRectF],
        axis: str,
    ) -> Tuple[Optional[float], Optional[float]]:
        """최적의 스냅 오프셋과 스냅 좌표를 찾는다.

        Returns: (offset_to_apply, snap_coordinate) or (None, None)
        """
        best_dist = SNAP_THRESHOLD + 1
        best_offset: Optional[float] = None
        best_coord: Optional[float] = None

        for cr in candidates:
            if axis == "x":
                cand_edges = [cr.left(), cr.center().x(), cr.right()]
            else:
                cand_edges = [cr.top(), cr.center().y(), cr.bottom()]

            for me in moving_edges:
                for ce in cand_edges:
                    dist = abs(me - ce)
                    if dist < best_dist:
                        best_dist = dist
                        best_offset = ce - me
                        best_coord = ce

        if best_dist <= SNAP_THRESHOLD:
            return best_offset, best_coord
        return None, None

    def _update_guides(
        self,
        moving_item: QGraphicsItem,
        snap_x: Optional[float],
        snap_y: Optional[float],
    ):
        """가이드 라인을 그리거나 제거한다."""
        self.clear_guides()

        views = self.scene.views()
        if not views:
            return
        vp = views[0].mapToScene(views[0].viewport().rect()).boundingRect()

        if snap_x is not None:
            line = QGraphicsLineItem(snap_x, vp.top(), snap_x, vp.bottom())
            line.setPen(self._guide_pen)
            line.setZValue(9999)
            self.scene.addItem(line)
            self._guides.append(line)

        if snap_y is not None:
            line = QGraphicsLineItem(vp.left(), snap_y, vp.right(), snap_y)
            line.setPen(self._guide_pen)
            line.setZValue(9999)
            self.scene.addItem(line)
            self._guides.append(line)
