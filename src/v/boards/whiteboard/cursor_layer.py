"""라이브 커서 — 다른 사용자의 마우스를 캔버스 위에 이름표/말풍선으로 표시.

**scene 아이템(QGraphicsItem)** 으로 그린다. 각 커서/말풍선은
`ItemIgnoresTransformations` 플래그라 줌과 무관하게 항상 화면 픽셀 크기로 보이고,
`setPos(scene_x, scene_y)` 로 보드 좌표에 놓이면 Qt 가 줌·팬·DPI(디스플레이 배율)를
**전부 알아서** 화면 위치로 변환한다 → 우리가 mapFromScene 같은 좌표 계산을 안 하므로
줌/배율이 달라도 절대 어긋나지 않는다(예전 오버레이 위젯 방식의 오프셋 문제 해결).

서버 presence 의 cursor{x,y}(보드 좌표)를 목표로 60fps 로 보간해 부드럽게 움직인다.
채팅은 해당 사용자 커서 위 말풍선으로 떠서 서서히 사라진다. 글리프는 자작.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import Qt, QTimer, QPointF, QRectF, QObject
from PyQt6.QtGui import (QColor, QPolygonF, QPainterPath, QFont, QFontMetrics, QPen)
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsRectItem

_ARROW = QPolygonF([
    QPointF(0, 0), QPointF(0, 18), QPointF(4.5, 13.5), QPointF(8, 21),
    QPointF(11, 19.5), QPointF(7.5, 12.5), QPointF(13, 12),
])
_EASE = 0.35
_SETTLE = 0.4
_BUBBLE_TTL = 6.0
_BUBBLE_FADE = 1.8
_STATE_LABEL = {"menu": "≡ 메뉴", "typing": "⌨ 입력 중", "away": "💤 자리비움"}

_Z_SEL = 1_000_000.0
_Z_CURSOR = 1_000_002.0
_Z_BUBBLE = 1_000_003.0

_FONT = QFont(); _FONT.setPointSize(8); _FONT.setBold(True)
_BFONT = QFont(); _BFONT.setPointSize(9)


class _CursorItem(QGraphicsItem):
    """화면 픽셀 고정 크기 커서 글리프 + 이름표. setPos = 보드 좌표(scene)."""

    def __init__(self, scale: float = 1.0, opacity: float = 1.0):
        super().__init__()
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.setZValue(_Z_CURSOR)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._color = QColor("#888")
        self._name = ""
        self._state = ""
        self._scale = max(0.1, float(scale))
        self._opacity = max(0.0, min(1.0, float(opacity)))
        self._fm = QFontMetrics(_FONT)
        self._lw = 0.0
        self._lh = float(self._fm.height() + 4)
        self._recalc()

    def set_label(self, name: str, state: str, color: QColor):
        if name == self._name and state == self._state and color == self._color:
            return
        self.prepareGeometryChange()
        self._name, self._state, self._color = name, state, color
        self._recalc()
        self.update()

    def set_style(self, scale: float, opacity: float):
        scale = max(0.1, float(scale))
        opacity = max(0.0, min(1.0, float(opacity)))
        if scale == self._scale and opacity == self._opacity:
            return
        self.prepareGeometryChange()
        self._scale, self._opacity = scale, opacity
        self.update()

    def _recalc(self):
        st = _STATE_LABEL.get(self._state, "")
        label = f"{self._name}  {st}" if st else self._name
        self._label = label
        self._lw = float(self._fm.horizontalAdvance(label) + 12)

    def shape(self):
        return QPainterPath()   # 클릭 안 잡힘(장식용)

    def boundingRect(self) -> QRectF:
        s = self._scale
        m = 4.0  # 안티앨리어싱/빠른 이동 여유 — 뷰가 DontAdjustForAntialiasing 라 잔상 방지용으로 직접 확보
        return QRectF(-2 * s - m, -2 * s - m,
                      (18 + self._lw) * s + 2 * m, (20 + self._lh) * s + 2 * m)

    def paint(self, p, opt, widget=None):
        p.setRenderHint(p.RenderHint.Antialiasing, True)
        p.setOpacity(self._opacity)
        if self._scale != 1.0:
            p.scale(self._scale, self._scale)
        p.setPen(QColor(255, 255, 255, 230))
        p.setBrush(self._color)
        p.drawPolygon(_ARROW)
        lw, lh = self._lw, self._lh
        path = QPainterPath()
        path.addRoundedRect(QRectF(14, 14, lw, lh), 5, 5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#555a64") if self._state == "away" else self._color)
        p.drawPath(path)
        p.setFont(_FONT)
        p.setPen(QColor("#ffffff"))
        p.drawText(QRectF(20, 14, lw - 12, lh),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._label)


class _BubbleItem(QGraphicsItem):
    """커서 위 말풍선(채팅). 화면 픽셀 고정. setPos = 커서 보드 좌표(scene)."""

    def __init__(self, text: str, color: QColor, scale: float = 1.0, opacity: float = 1.0):
        super().__init__()
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.setZValue(_Z_BUBBLE)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._fm = QFontMetrics(_BFONT)
        self._text = text[:200]
        self._color = QColor(color)
        self._scale = max(0.1, float(scale))
        self._opacity = max(0.0, min(1.0, float(opacity)))
        self.alpha = 1.0
        self._bw = float(min(240, self._fm.horizontalAdvance(self._text)) + 18)
        self._bh = float(self._fm.height() + 10)
        self._below = 0.0   # 이 말풍선 아래에 쌓인 말풍선들의 총 높이(스택용)

    def set_below(self, below: float):
        if below != self._below:
            self.prepareGeometryChange()
            self._below = below
            self.update()

    def stack_height(self) -> float:
        return self._bh + 5   # 한 칸(말풍선 + 간격)

    def shape(self):
        return QPainterPath()

    def boundingRect(self) -> QRectF:
        s = self._scale
        m = 4.0  # 안티앨리어싱/빠른 이동 여유 (잔상 방지)
        top = -(self._below + self._bh + 12)
        return QRectF(-2 * s - m, top * s - m,
                      (self._bw + 4) * s + 2 * m, (self._below + self._bh + 22) * s + 2 * m)

    def paint(self, p, opt, widget=None):
        p.setRenderHint(p.RenderHint.Antialiasing, True)
        if self._opacity != 1.0:
            p.setOpacity(self._opacity)
        if self._scale != 1.0:
            p.scale(self._scale, self._scale)
        bw, bh = self._bw, self._bh
        by = -self._below - bh - 8   # 아래에 쌓인 만큼 위로 올림
        color = QColor(self._color); color.setAlphaF(0.92 * self.alpha)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, by, bw, bh), 8, 8)
        p.drawPath(path)
        if self._below <= 0.01:   # 맨 아래(커서에 가장 가까운) 말풍선만 꼬리 표시
            p.drawPolygon(QPolygonF([
                QPointF(10, by + bh), QPointF(22, by + bh), QPointF(13, by + bh + 7)]))
        tc = QColor("#ffffff"); tc.setAlphaF(self.alpha)
        p.setFont(_BFONT)
        p.setPen(tc)
        p.drawText(QRectF(9, by, bw - 18, bh),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._text)


class CursorLayer(QObject):
    """원격 커서/말풍선을 scene 아이템으로 관리(줌·DPI 무관)."""

    def __init__(self, view):
        super().__init__(view)
        self._view = view
        self._scene = view.scene()
        self._cursors: dict = {}   # user -> {name,color,cur,tgt,select,state,item,selitem}
        self._bubbles: dict = {}   # user -> {t0, item}
        self._self_pos = None

        self._timer = QTimer(self)
        self._timer.setInterval(16)  # ~60fps 보간
        self._timer.timeout.connect(self._tick)

    # ---- 외부 API -------------------------------------------------------
    def update(self):
        """호환용 no-op(scene 아이템은 Qt 가 자동 리페인트)."""
        pass

    def set_self(self, x: float, y: float, name: str = "", color: str = ""):
        self._self_pos = [x, y]

    def add_bubble(self, user: str, color: str, text: str, is_self: bool = False):
        if self._scene is None:
            self._scene = self._view.scene()
        if not text or self._scene is None:
            return
        if is_self:
            pos = list(self._self_pos) if self._self_pos else [0.0, 0.0]
        else:
            c = self._cursors.get(user)
            pos = list(c["cur"]) if c else (list(self._self_pos) if self._self_pos else [0.0, 0.0])
        from v.settings import get_setting
        bscale = float(get_setting("cursor_scale", 1.0) or 1.0)
        bopacity = float(get_setting("cursor_opacity", 1.0) or 1.0)
        item = _BubbleItem(text, QColor(color or "#888"), bscale, bopacity)
        self._scene.addItem(item)
        # 연속 입력 시 이전 말풍선이 위로 쌓이도록(교체 X) — 유저별 스택 리스트
        lst = self._bubbles.setdefault(user, [])
        lst.append({"t0": time.monotonic(), "item": item, "pos": pos})
        while len(lst) > 5:   # 너무 많이 쌓이지 않게(오래된 것부터 제거)
            old = lst.pop(0)
            self._scene.removeItem(old["item"])
        self._restack(user)
        if not self._timer.isActive():
            self._timer.start()

    def _restack(self, user: str):
        """유저의 말풍선들을 커서 위로 세로 스택(최신=맨 아래, 이전=위로)."""
        lst = self._bubbles.get(user) or []
        if not lst:
            return
        base = lst[-1]["pos"]   # 최신 말풍선의 기준 위치에 정렬
        below = 0.0
        for entry in reversed(lst):   # 최신(맨 아래)부터
            it = entry["item"]
            it.setPos(base[0], base[1])
            it.set_below(below)
            below += it.stack_height()

    def update_from_presence(self, users: list, exclude_user: str = ""):
        if self._scene is None:
            self._scene = self._view.scene()
        if self._scene is None:
            return
        from v.settings import get_setting
        if not get_setting("cursor_show", True):
            # 설정: 다른 사용자 커서 숨김 — 기존 원격 커서 제거
            for nm in list(self._cursors.keys()):
                self._remove_cursor(nm)
            return
        scale = float(get_setting("cursor_scale", 1.0) or 1.0)
        opacity = float(get_setting("cursor_opacity", 1.0) or 1.0)
        seen = set()
        for u in users:
            name = u.get("user", "")
            cur = u.get("cursor")
            if not name or name == exclude_user or not cur:
                continue
            try:
                tx, ty = float(cur.get("x", 0)), float(cur.get("y", 0))
            except Exception:
                continue
            seen.add(name)
            sel = u.get("select")
            st = u.get("state", "")
            color = QColor(u.get("color", "#888"))
            c = self._cursors.get(name)
            if c is None:
                item = _CursorItem(scale, opacity)
                item.set_label(name, st, color)
                item.setPos(tx, ty)
                self._scene.addItem(item)
                c = {"name": name, "color": color, "cur": [tx, ty], "tgt": [tx, ty],
                     "select": None, "state": st, "item": item, "selitem": None}
                self._cursors[name] = c
            else:
                c["tgt"] = [tx, ty]
                c["color"] = color
                c["state"] = st
                c["item"].set_label(name, st, color)
                c["item"].set_style(scale, opacity)
            self._apply_select(c, sel)
        for name in list(self._cursors.keys()):
            if name not in seen:
                self._remove_cursor(name)
        if self._cursors and not self._timer.isActive():
            self._timer.start()

    def clear(self):
        self._timer.stop()
        for name in list(self._cursors.keys()):
            self._remove_cursor(name)
        for user in list(self._bubbles.keys()):
            self._remove_bubble(user)
        self._cursors.clear()
        self._bubbles.clear()

    # ---- 내부 ----------------------------------------------------------
    def _apply_select(self, c, sel):
        if sel:
            if c["selitem"] is None and self._scene is not None:
                r = QGraphicsRectItem()
                r.setZValue(_Z_SEL)
                r.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                pen = QPen(c["color"], 1.5, Qt.PenStyle.DashLine)
                pen.setCosmetic(True)   # 줌 무관 1.5px
                r.setPen(pen)
                fill = QColor(c["color"]); fill.setAlphaF(0.12)
                r.setBrush(fill)
                self._scene.addItem(r)
                c["selitem"] = r
            if c["selitem"] is not None:
                c["selitem"].setRect(QRectF(sel["x"], sel["y"], sel["w"], sel["h"]))
            c["select"] = sel
        elif c["selitem"] is not None:
            self._scene.removeItem(c["selitem"])
            c["selitem"] = None
            c["select"] = None

    def _remove_cursor(self, name):
        c = self._cursors.pop(name, None)
        if not c or self._scene is None:
            return
        if c.get("item") is not None:
            self._scene.removeItem(c["item"])
        if c.get("selitem") is not None:
            self._scene.removeItem(c["selitem"])

    def _remove_bubble(self, user):
        lst = self._bubbles.pop(user, None)
        if lst and self._scene is not None:
            for entry in lst:
                self._scene.removeItem(entry["item"])

    def _tick(self):
        moving = False
        for c in self._cursors.values():
            cur, tgt = c["cur"], c["tgt"]
            dx, dy = tgt[0] - cur[0], tgt[1] - cur[1]
            if abs(dx) < _SETTLE and abs(dy) < _SETTLE:
                continue
            cur[0] += dx * _EASE
            cur[1] += dy * _EASE
            c["item"].setPos(cur[0], cur[1])
            moving = True
        now = time.monotonic()
        for user in list(self._bubbles.keys()):
            lst = self._bubbles[user]
            kept = []
            removed = False
            for entry in lst:
                age = now - entry["t0"]
                if age >= _BUBBLE_TTL:
                    self._scene.removeItem(entry["item"])
                    removed = True
                    continue
                if age >= _BUBBLE_TTL - _BUBBLE_FADE:
                    entry["item"].alpha = max(0.0, (_BUBBLE_TTL - age) / _BUBBLE_FADE)
                    entry["item"].update()
                kept.append(entry)
            if removed:
                if kept:
                    self._bubbles[user] = kept
                    self._restack(user)
                else:
                    del self._bubbles[user]
        if not moving and not self._bubbles:
            self._timer.stop()
