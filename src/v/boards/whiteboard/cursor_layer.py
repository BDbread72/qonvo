"""라이브 커서 — 다른 사용자의 마우스를 캔버스 위에 이름표/말풍선으로 표시.

뷰포트 위에 **투명 오버레이 위젯**으로 그린다(QGraphicsScene 과 분리) →
- 캔버스(이미지카드 수백 개) 리페인트를 유발하지 않아 가볍고 드래그가 안 끊긴다
- 위젯이 자기 픽셀만 다시 그리므로 잔상이 없다

서버 presence 의 cursor{x,y}(보드 좌표)를 목표로 두고 60fps 로 보간해 부드럽게 움직인다.
채팅은 해당 사용자 커서 위 말풍선으로 떠서 서서히 사라진다. 글리프는 자작.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import Qt, QTimer, QPointF, QRectF, QEvent
from PyQt6.QtGui import (QColor, QPolygonF, QPainterPath, QFont, QFontMetrics, QPainter, QPen)
from PyQt6.QtWidgets import QWidget

_ARROW = QPolygonF([
    QPointF(0, 0), QPointF(0, 18), QPointF(4.5, 13.5), QPointF(8, 21),
    QPointF(11, 19.5), QPointF(7.5, 12.5), QPointF(13, 12),
])
_EASE = 0.35
_SETTLE = 0.4
_BUBBLE_TTL = 6.0
_BUBBLE_FADE = 1.8


class CursorLayer(QWidget):
    """뷰포트 위 투명 오버레이. 원격 커서/말풍선을 그린다."""

    def __init__(self, view):
        super().__init__(view.viewport())
        self._view = view
        self._cursors: dict = {}          # user -> {name,color,cur,tgt}
        self._bubbles: dict = {}          # user -> {text,t0,color,pos}
        self._self_pos = None
        self._font = QFont(); self._font.setPointSize(8); self._font.setBold(True)
        self._bfont = QFont(); self._bfont.setPointSize(9)

        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setGeometry(view.viewport().rect())
        view.viewport().installEventFilter(self)   # 리사이즈 추적
        # 팬/줌 시 갱신
        view.horizontalScrollBar().valueChanged.connect(self.update)
        view.verticalScrollBar().valueChanged.connect(self.update)
        self.show()
        self.raise_()

        self._timer = QTimer(self)
        self._timer.setInterval(16)  # ~60fps (오버레이만 갱신 — 가벼움)
        self._timer.timeout.connect(self._tick)

    # ---- 외부 API -------------------------------------------------------
    def set_self(self, x: float, y: float, name: str = "", color: str = ""):
        self._self_pos = [x, y]

    def add_bubble(self, user: str, color: str, text: str, is_self: bool = False):
        if not text:
            return
        if is_self:
            pos = list(self._self_pos) if self._self_pos else [0.0, 0.0]
        else:
            c = self._cursors.get(user)
            pos = list(c["cur"]) if c else (list(self._self_pos) if self._self_pos else [0.0, 0.0])
        self._bubbles[user] = {"text": text[:200], "t0": time.monotonic(),
                               "color": QColor(color or "#888"), "pos": pos}
        if not self._timer.isActive():
            self._timer.start()
        self.update()

    def update_from_presence(self, users: list, exclude_user: str = ""):
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
            c = self._cursors.get(name)
            if c is None:
                self._cursors[name] = {"name": name, "color": QColor(u.get("color", "#888")),
                                       "cur": [tx, ty], "tgt": [tx, ty], "select": sel}
            else:
                c["tgt"] = [tx, ty]
                c["color"] = QColor(u.get("color", "#888"))
                c["select"] = sel
        for name in list(self._cursors.keys()):
            if name not in seen:
                del self._cursors[name]
        if self._cursors and not self._timer.isActive():
            self._timer.start()
        self.update()

    def clear(self):
        self._timer.stop()
        self._cursors.clear()
        self._bubbles.clear()
        self.update()

    # ---- 내부 ----------------------------------------------------------
    def eventFilter(self, obj, event):
        if obj is self._view.viewport() and event.type() == QEvent.Type.Resize:
            self.setGeometry(self._view.viewport().rect())
        return super().eventFilter(obj, event)

    def _tick(self):
        moving = False
        for c in self._cursors.values():
            cur, tgt = c["cur"], c["tgt"]
            dx, dy = tgt[0] - cur[0], tgt[1] - cur[1]
            if abs(dx) < _SETTLE and abs(dy) < _SETTLE:
                continue
            cur[0] += dx * _EASE
            cur[1] += dy * _EASE
            moving = True
        now = time.monotonic()
        fading = bool(self._bubbles)
        for user in list(self._bubbles.keys()):
            if now - self._bubbles[user]["t0"] >= _BUBBLE_TTL:
                del self._bubbles[user]
        if moving or fading or self._bubbles:
            self.update()
        if not moving and not self._bubbles:
            self._timer.stop()

    def paintEvent(self, event):
        if not self._cursors and not self._bubbles:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        view = self._view
        # 영역 선택 사각형(다른 사용자)
        for c in self._cursors.values():
            sel = c.get("select")
            if not sel:
                continue
            tl = view.mapFromScene(QPointF(sel["x"], sel["y"]))
            br = view.mapFromScene(QPointF(sel["x"] + sel["w"], sel["y"] + sel["h"]))
            color = c["color"]
            p.setPen(QPen(color, 1.5, Qt.PenStyle.DashLine))
            fill = QColor(color); fill.setAlphaF(0.12)
            p.setBrush(fill)
            p.drawRect(QRectF(float(tl.x()), float(tl.y()),
                              float(br.x() - tl.x()), float(br.y() - tl.y())))
        # 말풍선
        self._paint_bubbles(p, view)
        # 커서 글리프
        p.setFont(self._font)
        fm = QFontMetrics(self._font)
        for c in self._cursors.values():
            vp = view.mapFromScene(QPointF(c["cur"][0], c["cur"][1]))
            p.save()
            p.translate(vp.x(), vp.y())
            color = c["color"]
            p.setPen(QColor(255, 255, 255, 230))
            p.setBrush(color)
            p.drawPolygon(_ARROW)
            name = c["name"]
            lw = fm.horizontalAdvance(name) + 12
            lh = fm.height() + 4
            path = QPainterPath()
            path.addRoundedRect(QRectF(14, 14, lw, lh), 5, 5)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawPath(path)
            p.setPen(QColor("#ffffff"))
            p.drawText(QRectF(20, 14, lw - 12, lh),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, name)
            p.restore()
        p.end()

    def _paint_bubbles(self, p, view):
        now = time.monotonic()
        p.setFont(self._bfont)
        fm = QFontMetrics(self._bfont)
        for info in self._bubbles.values():
            age = now - info["t0"]
            alpha = 1.0 if age < _BUBBLE_TTL - _BUBBLE_FADE else max(0.0, (_BUBBLE_TTL - age) / _BUBBLE_FADE)
            vp = view.mapFromScene(QPointF(info["pos"][0], info["pos"][1]))
            text = info["text"]
            bw = min(240, fm.horizontalAdvance(text)) + 18
            bh = fm.height() + 10
            bx, by = float(vp.x()), float(vp.y()) - bh - 8
            color = QColor(info["color"]); color.setAlphaF(0.92 * alpha)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            path = QPainterPath()
            path.addRoundedRect(QRectF(bx, by, bw, bh), 8, 8)
            p.drawPath(path)
            p.drawPolygon(QPolygonF([
                QPointF(bx + 10, by + bh), QPointF(bx + 22, by + bh), QPointF(bx + 13, by + bh + 7)]))
            tc = QColor("#ffffff"); tc.setAlphaF(alpha)
            p.setPen(tc)
            p.drawText(QRectF(bx + 9, by, bw - 18, bh),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
