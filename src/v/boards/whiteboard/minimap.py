"""
브랜치 그래프 위젯
노드 트리를 git branch 스타일로 시각화
고정 크기 위젯 안에서 내용 자동 축소 + 우클릭 드래그로 내부 패닝
"""
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QPointF, QTimer
from PyQt6.QtGui import QPainter, QPen, QBrush, QColor, QPolygonF

from v.theme import Theme
_TYPE_UNSENT = 0
_TYPE_TEXT = 1
_TYPE_IMAGE = 2

WIDTH = 220
HEIGHT = 160


class BranchGraphWidget(QWidget):

    MARGIN = 12
    PADDING = 8
    DOT_R = 6
    STEP_X = 24
    STEP_Y = 20

    def __init__(self, view):
        super().__init__(view)
        self._view = view
        # 논리 좌표 (스케일 전)
        self._raw_nodes = []   # [(lx, ly, nid, ntype), ...]
        self._raw_lines = []   # [(x1,y1,x2,y2), ...]
        self._content_w = 0
        self._content_h = 0
        # 표시용 (스케일 후)
        self._scale = 1.0
        self._offset_x = 0.0  # 패닝 오프셋
        self._offset_y = 0.0
        self._dragging = False
        self._drag_start = QPointF()

        self.setFixedSize(WIDTH, HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._dirty = True
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(300)

        self.show()

    def mark_dirty(self):
        """변경 발생 시 호출 — 다음 타이머 틱에서 새로고침."""
        self._dirty = True

    def reposition(self):
        x = self._view.width() - WIDTH - self.MARGIN
        y = self._view.height() - HEIGHT - self.MARGIN
        self.move(max(0, x), max(0, y))
        self.raise_()

    # ── 트리 빌드 ──

    def _refresh(self):
        if not self._dirty:
            return
        self._dirty = False

        plugin = self._view.plugin
        if not plugin or not hasattr(plugin, 'proxies'):
            return

        proxies = plugin.proxies
        edges_list = getattr(plugin.app, 'edges', [])
        nodes_map = getattr(plugin.app, 'nodes', {})

        if not proxies:
            self._raw_nodes = []
            self._raw_lines = []
            self.reposition()
            self.update()
            return

        # proxy → nid 역 매핑
        proxy_to_nid = {id(proxy): nid for nid, proxy in proxies.items()}

        children = {}
        parents_of = {}
        for edge in edges_list:
            src_id = proxy_to_nid.get(id(edge.source))
            tgt_id = proxy_to_nid.get(id(edge.target))
            if src_id is not None and tgt_id is not None:
                children.setdefault(src_id, []).append(tgt_id)
                parents_of.setdefault(tgt_id, set()).add(src_id)

        roots = sorted(nid for nid in proxies if nid not in parents_of)
        if not roots:
            roots = sorted(proxies)[:1]

        raw_nodes = []
        raw_lines = []
        row = [0]
        pad = self.PADDING
        r = self.DOT_R
        visited = {}   # nid → row
        positions = {}  # nid → (lx, ly)

        def dfs(nid, depth):
            if nid in visited:
                return visited[nid]

            kids = sorted(children.get(nid, []))
            if not kids:
                my_row = row[0]
                row[0] += 1
            else:
                child_rows = []
                for cid in kids:
                    cr = dfs(cid, depth + 1)
                    child_rows.append(cr)
                my_row = child_rows[0]

            lx = pad + depth * self.STEP_X + r
            ly = pad + my_row * self.STEP_Y + r

            w = nodes_map.get(nid)
            sent = bool(getattr(w, "sent", False))
            model = str(getattr(w, "model", "") or "").lower()

            if not sent:
                ntype = _TYPE_UNSENT
            elif "image" in model:
                ntype = _TYPE_IMAGE
            else:
                ntype = _TYPE_TEXT

            raw_nodes.append((lx, ly, nid, ntype))
            visited[nid] = my_row
            positions[nid] = (lx, ly)

            return my_row

        for rid in roots:
            dfs(rid, 0)

        # 엣지 기반 라인 생성 (DFS 트리 대신 실제 엣지)
        for edge in edges_list:
            src_id = proxy_to_nid.get(id(edge.source))
            tgt_id = proxy_to_nid.get(id(edge.target))
            if src_id in positions and tgt_id in positions:
                x1, y1 = positions[src_id]
                x2, y2 = positions[tgt_id]
                if y2 == y1:
                    raw_lines.append((x1, y1, x2, y2))
                else:
                    raw_lines.append((x1, y1, x1, y2))
                    raw_lines.append((x1, y2, x2, y2))

        self._raw_nodes = raw_nodes
        self._raw_lines = raw_lines

        if raw_nodes:
            self._content_w = max(n[0] for n in raw_nodes) + r + pad
            self._content_h = max(n[1] for n in raw_nodes) + r + pad
        else:
            self._content_w = self._content_h = 1

        # 자동 스케일: 내용이 위젯에 맞도록
        sx = (WIDTH - 4) / max(self._content_w, 1)
        sy = (HEIGHT - 4) / max(self._content_h, 1)
        self._scale = min(sx, sy, 1.0)  # 최대 1.0 (확대 안 함)

        # 스케일 변경 시 오프셋 리셋
        scaled_w = self._content_w * self._scale
        scaled_h = self._content_h * self._scale
        if scaled_w <= WIDTH and scaled_h <= HEIGHT:
            self._offset_x = 0.0
            self._offset_y = 0.0

        self._clamp_offset()
        self.reposition()
        self.update()

    def _clamp_offset(self):
        """오프셋을 유효 범위 내로 제한"""
        scaled_w = self._content_w * self._scale
        scaled_h = self._content_h * self._scale
        max_ox = max(0, scaled_w - WIDTH + 4)
        max_oy = max(0, scaled_h - HEIGHT + 4)
        self._offset_x = max(0, min(self._offset_x, max_ox))
        self._offset_y = max(0, min(self._offset_y, max_oy))

    def _to_screen(self, lx, ly):
        """논리 좌표 → 스크린 좌표"""
        return (
            lx * self._scale - self._offset_x + 2,
            ly * self._scale - self._offset_y + 2,
        )

    # ── 페인팅 ──

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 배경
        p.setPen(QPen(QColor(58, 58, 58, 200), 1))
        p.setBrush(QBrush(QColor(26, 26, 26, 200)))
        p.drawRoundedRect(0, 0, WIDTH - 1, HEIGHT - 1, 6, 6)

        p.setClipRect(2, 2, WIDTH - 4, HEIGHT - 4)
        s = self._scale
        r = max(self.DOT_R * s, 3)

        # 연결선
        p.setPen(QPen(QColor(100, 100, 100), max(2 * s, 1)))
        for x1, y1, x2, y2 in self._raw_lines:
            sx1, sy1 = self._to_screen(x1, y1)
            sx2, sy2 = self._to_screen(x2, y2)
            p.drawLine(int(sx1), int(sy1), int(sx2), int(sy2))

        # 노드
        for lx, ly, nid, ntype in self._raw_nodes:
            sx, sy = self._to_screen(lx, ly)
            if ntype == _TYPE_UNSENT:
                p.setPen(QPen(QColor("#333"), 1))
                p.setBrush(QBrush(QColor("#555")))
                p.drawEllipse(QPointF(sx, sy), r, r)
            elif ntype == _TYPE_IMAGE:
                p.setPen(QPen(QColor("#1a5c2a"), 1))
                p.setBrush(QBrush(QColor("#28a745")))
                p.drawEllipse(QPointF(sx, sy), r, r)
                if s > 0.5:
                    self._draw_mountain(p, sx, sy, s)
            else:
                p.setPen(QPen(QColor("#0a4dad"), 1))
                p.setBrush(QBrush(QColor("#0d6efd")))
                p.drawEllipse(QPointF(sx, sy), r, r)
                if s > 0.5:
                    self._draw_plane(p, sx, sy, s)

        p.end()

    def _draw_plane(self, p, cx, cy, s):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(255, 255, 255, 200)))
        d = 3.0 * s
        p.drawPolygon(QPolygonF([
            QPointF(cx + d, cy),
            QPointF(cx - d, cy - d * 0.7),
            QPointF(cx - d * 0.3, cy),
            QPointF(cx - d, cy + d * 0.7),
        ]))

    def _draw_mountain(self, p, cx, cy, s):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(255, 255, 255, 200)))
        d = 3.0 * s
        p.drawPolygon(QPolygonF([
            QPointF(cx - d, cy + d * 0.6),
            QPointF(cx - d * 0.3, cy - d * 0.6),
            QPointF(cx + d * 0.2, cy + d * 0.1),
            QPointF(cx + d * 0.5, cy - d * 0.3),
            QPointF(cx + d, cy + d * 0.6),
        ]))

    # ── 마우스 ──

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position()
            s = self._scale
            hit_r = max(self.DOT_R * s, 3) + 4
            for lx, ly, nid, _ in self._raw_nodes:
                sx, sy = self._to_screen(lx, ly)
                if (pos.x() - sx) ** 2 + (pos.y() - sy) ** 2 <= hit_r ** 2:
                    proxy = self._view.plugin.proxies.get(nid)
                    if proxy:
                        self._view.centerOn(proxy)
                    break
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            self._dragging = True
            self._drag_start = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event):
        if self._dragging:
            delta = event.position() - self._drag_start
            self._offset_x -= delta.x()
            self._offset_y -= delta.y()
            self._clamp_offset()
            self._drag_start = event.position()
            self.update()
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton and self._dragging:
            self._dragging = False
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            event.accept()
