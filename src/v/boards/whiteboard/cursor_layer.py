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
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, QPointF, QRectF, QObject
from PyQt6.QtGui import (QColor, QPolygonF, QPainterPath, QFont, QFontMetrics, QPen,
                         QPixmap, QCursor)
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsRectItem

_ARROW = QPolygonF([
    QPointF(0, 0), QPointF(0, 18), QPointF(4.5, 13.5), QPointF(8, 21),
    QPointF(11, 19.5), QPointF(7.5, 12.5), QPointF(13, 12),
])
# I-beam(텍스트 커서) 글리프 — state == "typing" 일 때 화살표 대신 그린다.
_IBEAM = QPolygonF([
    QPointF(0, 0), QPointF(9, 0), QPointF(9, 2), QPointF(6, 2),
    QPointF(6, 18), QPointF(9, 18), QPointF(9, 20), QPointF(0, 20),
    QPointF(0, 18), QPointF(3, 18), QPointF(3, 2), QPointF(0, 2),
])
_EASE = 0.35
_SETTLE = 0.4
_SKIN_DISP = 26.0   # 스킨 글리프 표시 한도(px, 화살표 대체) — 큰 PNG 도 이 안에 맞춤

# presence state → 스킨 역할(시트 칸). 나머지(""/menu/away)는 default.
_ROLE_FOR_STATE = {"typing": "text", "point": "point"}


def _role_for_state(state: str) -> str:
    return _ROLE_FOR_STATE.get(state or "", "default")
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
        self._skins: dict = {}   # 역할별 스킨 {role: QPixmap} (비면 기본 화살표/I-beam)
        self._recalc()

    def set_skins(self, skins: dict):
        """역할별 스킨 dict 교체({}=기본 글리프). 동일하면 무시."""
        if skins is self._skins or (not skins and not self._skins):
            return
        self.prepareGeometryChange()
        self._skins = skins or {}
        self.update()

    def _cur_skin(self) -> Optional[QPixmap]:
        """현재 상태에 맞는 스킨 픽스맵(없으면 default, 그것도 없으면 None)."""
        if not self._skins:
            return None
        pm = self._skins.get(_role_for_state(self._state)) or self._skins.get("default")
        return pm if (pm is not None and not pm.isNull()) else None

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
        if self._cur_skin() is not None:
            # 스킨은 화살표보다 클 수 있어 글리프 영역을 _SKIN_DISP 까지 확보
            w = max(_SKIN_DISP, 14.0 + self._lw)
            h = max(_SKIN_DISP, 14.0 + self._lh)
        else:
            w = 18.0 + self._lw
            h = 20.0 + self._lh
        return QRectF(-2 * s - m, -2 * s - m, w * s + 2 * m, h * s + 2 * m)

    def paint(self, p, opt, widget=None):
        p.setRenderHint(p.RenderHint.Antialiasing, True)
        p.setOpacity(self._opacity)
        if self._scale != 1.0:
            p.scale(self._scale, self._scale)
        # 글리프: 현재 상태의 스킨(있으면, 화살표/I-beam 대체) 또는 내장 글리프
        skin = self._cur_skin()
        if skin is not None:
            p.setRenderHint(p.RenderHint.SmoothPixmapTransform, True)
            bw, bh = skin.width(), skin.height()
            if bw > 0 and bh > 0:
                f = min(_SKIN_DISP / bw, _SKIN_DISP / bh, 1.0)  # 축소만(작은 건 원본 크기)
                p.drawPixmap(QRectF(0, 0, bw * f, bh * f), skin, QRectF(0, 0, bw, bh))
        else:
            p.setPen(QColor(255, 255, 255, 230))
            p.setBrush(self._color)
            # 입력 중이면 텍스트 커서(I-beam), 그 외엔 화살표
            p.drawPolygon(_IBEAM if self._state == "typing" else _ARROW)
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
        self._cursors: dict = {}   # user -> {name,color,cur,tgt,select,state,skin,item,selitem}
        self._bubbles: dict = {}   # user -> {t0, item}
        self._self_pos = None

        # ── 커서 스킨(Dynamic Cursor) ──
        self._client = None              # ServerClient (스킨 URL/다운로드용)
        self._skin_downloading: set = set()   # 진행 중 다운로드 해시(중복 방지)
        self._skin_threads: list = []    # SkinDownloadThread 참조 보관(GC 방지)
        self._self_skin_hash = None      # 내 포인터에 적용된 스킨 해시
        self._self_skin_want = None      # 내 포인터가 원하는 스킨 해시(다운로드 대기)
        self._self_roles: dict = {}      # 내 스킨 역할별 {role: QPixmap}
        self._self_state = ""            # 내 커서 상태(역할 선택용)

        self._timer = QTimer(self)
        self._timer.setInterval(16)  # ~60fps 보간
        self._timer.timeout.connect(self._tick)

    def set_client(self, client):
        """스킨 다운로드/URL 구성을 위한 ServerClient 참조 설정."""
        self._client = client

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
            # 내 채팅: 캔버스에서 마우스를 안 움직였으면 _self_pos 가 없어 안 보였음
            # (채팅칸에 타이핑만 한 경우) → 현재 뷰 중앙에 띄워 항상 보이게.
            if self._self_pos:
                pos = list(self._self_pos)
            else:
                try:
                    c = self._view.mapToScene(self._view.viewport().rect().center())
                    pos = [c.x(), c.y()]
                except Exception:
                    pos = [0.0, 0.0]
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
                     "select": None, "state": st, "skin": "", "item": item, "selitem": None}
                self._cursors[name] = c
            else:
                c["tgt"] = [tx, ty]
                c["color"] = color
                c["state"] = st
                c["item"].set_label(name, st, color)
                c["item"].set_style(scale, opacity)
            self._apply_user_skin(c, name, u.get("skin") or "")
            self._apply_select(c, sel)
        for name in list(self._cursors.keys()):
            if name not in seen:
                self._remove_cursor(name)
        if self._cursors and not self._timer.isActive():
            self._timer.start()

    # ── 커서 스킨(Dynamic Cursor, 역할 세트) ───────────────────────────
    def _apply_user_skin(self, c: dict, name: str, h: str):
        """원격 커서에 역할 스킨 적용: 캐시 히트면 즉시, 없으면 다운로드(그동안 기본 글리프)."""
        c["skin"] = h
        item = c["item"]
        if not h:
            item.set_skins({})
            return
        from . import cursor_skin_cache as cache
        roles = cache.get_roles(h)
        if roles:
            item.set_skins(roles)
        else:
            item.set_skins({})
            self._request_skin(name, h)

    def _request_skin(self, name: str, h: str):
        """username 의 스킨 시트를 백그라운드로 받는다(해시당 1회)."""
        if not h or h in self._skin_downloading or self._client is None:
            return
        base = getattr(self._client, "http_base", "")
        token = getattr(self._client, "http_token", "")
        if not base or not token:
            return
        from . import cursor_skin_cache as cache
        try:
            th = self._client.start_skin_download(name, h, str(cache.cache_dir()))
        except Exception:
            return
        self._skin_downloading.add(h)
        th.done.connect(self._on_skin_downloaded)
        th.finished.connect(lambda t=th: self._drop_thread(t))
        self._skin_threads.append(th)
        th.start()

    def _drop_thread(self, t):
        try:
            self._skin_threads.remove(t)
        except ValueError:
            pass

    def _on_skin_downloaded(self, user: str, expected: str, actual: str, path: str):
        """다운로드 완료 → 시트 캐시 로드 후 해당 유저/해시 커서 + 내 포인터에 역할 적용."""
        self._skin_downloading.discard(expected)
        if not actual or not path:
            return
        from . import cursor_skin_cache as cache
        if cache.store_file(actual, path) is None:
            return
        roles = cache.get_roles(actual)
        if not roles:
            return
        for nm, c in self._cursors.items():
            if nm == user or c.get("skin") == actual:
                c["item"].set_skins(roles)
                c["skin"] = actual
        # 내 포인터가 이 스킨을 기다렸으면 적용
        if self._self_skin_want in (actual, expected) and self._self_skin_hash != actual:
            self._self_roles = roles
            self._self_skin_hash = actual
            self._apply_self_pointer()
        self._invalidate()

    def set_self_skin(self, h: str, username: str):
        """내 커서 스킨 세트를 내 마우스 포인터에 적용한다(없으면 기본 포인터로 복귀)."""
        self._self_skin_want = h or None
        if not h:
            self._reset_self_pointer()
            return
        if self._self_skin_hash == h:
            return
        from . import cursor_skin_cache as cache
        roles = cache.get_roles(h)
        if roles:
            self._self_roles = roles
            self._self_skin_hash = h
            self._apply_self_pointer()
        else:
            self._request_skin(username, h)

    def set_self_state(self, state: str):
        """내 커서 상태(typing/point/…) 변경 → 내 포인터를 해당 역할 스킨으로 즉시 전환.

        (서버 round-trip 없이 view 가 로컬에서 호출 → 즉각 반응)
        menu/away 는 뷰가 커서를 직접 제어(방사형 메뉴=숨김 등)하므로 스킨을 입히지 않는다.
        """
        if state == self._self_state:
            return
        self._self_state = state
        if self._self_roles and state not in ("menu", "away"):
            self._apply_self_pointer()

    def reassert_self_pointer(self):
        """내 포인터 스킨을 다시 강제 적용(자가복구).

        뷰의 다른 코드(팬 종료/방사형 메뉴 닫기 등)가 viewport 커서를 ArrowCursor 로
        되돌려 스킨이 풀리는 일이 잦다. 폴 타이머가 매 틱 호출해 120ms 내 복구한다.
        menu/away 상태에선 뷰가 커서를 직접 제어하므로 건드리지 않는다.
        """
        if self._self_roles and self._self_state not in ("menu", "away"):
            self._apply_self_pointer()

    def _apply_self_pointer(self):
        """현재 상태(_self_state)의 역할 스킨을 내 마우스 포인터에 적용."""
        if self._view is None or not self._self_roles:
            return
        pm = (self._self_roles.get(_role_for_state(self._self_state))
              or self._self_roles.get("default"))
        if pm is None or pm.isNull():
            return
        try:
            disp = pm
            if pm.width() > _SKIN_DISP or pm.height() > _SKIN_DISP:
                disp = pm.scaled(int(_SKIN_DISP), int(_SKIN_DISP),
                                 Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
            self._view.viewport().setCursor(QCursor(disp, 0, 0))
        except Exception:
            pass

    def _reset_self_pointer(self):
        self._self_roles = {}
        if self._self_skin_hash is None:
            return
        self._self_skin_hash = None
        try:
            if self._view is not None:
                self._view.viewport().unsetCursor()
        except Exception:
            pass

    def set_anonymized(self, on: bool):
        """스크린샷용 — 타인 커서 이름표를 'Guest 1·2…'로 바꾸거나(on) 실제 이름으로 복원(off).

        _cursors 는 본인 제외 타 사용자만 담는다. 정렬 순서로 번호를 매겨 캡처마다 동일하게.
        다음 presence 갱신이 오면 실제 이름으로 덮어쓰지만, 캡처는 동기(grab)라 그 전에 끝난다.
        """
        names = sorted(self._cursors.keys(), key=str.lower)
        for i, nm in enumerate(names, 1):
            c = self._cursors.get(nm)
            if not c:
                continue
            label = f"Guest {i}" if on else c["name"]
            try:
                c["item"].set_label(label, c["state"], c["color"])
            except Exception:
                continue

    def others_count(self) -> int:
        """현재 보이는 타 사용자 커서 수(익명화 옵션 활성 판단용)."""
        return len(self._cursors)

    def clear(self):
        self._timer.stop()
        for name in list(self._cursors.keys()):
            self._remove_cursor(name)
        for user in list(self._bubbles.keys()):
            self._remove_bubble(user)
        self._cursors.clear()
        self._bubbles.clear()
        # 내 포인터 스킨도 기본으로 복귀(보드 전환/연결 해제 시 유령 방지)
        self._reset_self_pointer()
        self._self_skin_want = None

    def remove_user(self, name: str):
        """특정 사용자의 커서/말풍선을 즉시 제거(user_leave 수신 시 호출).

        기존엔 다음 presence 브로드캐스트의 diff 로만 정리돼, 브로드캐스트가
        늦거나 누락되면 나간 사용자의 유령 커서가 남았다.
        """
        self._remove_cursor(name)
        self._remove_bubble(name)

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
            self._invalidate()

    def _invalidate(self):
        """뷰포트 전체를 강제 리페인트한다.

        view 가 BoundingRectViewportUpdate + ItemIgnoresTransformations 라서
        커서/말풍선 아이템을 removeItem/이동해도 Qt 가 더티 사각형을 정확히 못 잡아
        픽셀 잔상(유령)이 남는다. 제거·이동 시 뷰포트를 통째로 갱신해 무조건 지운다.
        """
        try:
            if self._view is not None:
                self._view.viewport().update()
        except Exception:
            pass

    def _remove_cursor(self, name):
        c = self._cursors.pop(name, None)
        if not c or self._scene is None:
            return
        if c.get("item") is not None:
            self._scene.removeItem(c["item"])
        if c.get("selitem") is not None:
            self._scene.removeItem(c["selitem"])
        self._invalidate()

    def _remove_bubble(self, user):
        lst = self._bubbles.pop(user, None)
        if lst and self._scene is not None:
            for entry in lst:
                self._scene.removeItem(entry["item"])
            self._invalidate()

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
        bubble_removed = False
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
            if removed:
                bubble_removed = True
        # 커서가 움직였거나(보간) 말풍선이 제거됐으면 뷰포트를 강제 갱신해 잔상 제거.
        if moving or bubble_removed:
            self._invalidate()
        if not moving and not self._bubbles:
            self._timer.stop()
