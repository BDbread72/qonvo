"""서버 모드 오버레이 — F1(사용자 목록+핑), F12(디버그 정보).

MainWindow 위에 떠 있는 반투명 패널. 시그널로 갱신된다.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QLabel


class _UserRow(QLabel):
    """클릭하면 해당 사용자의 커서로 따라가기."""
    clicked = pyqtSignal(str)

    def __init__(self, username: str, html: str, parent=None):
        super().__init__(html, parent)
        self._username = username
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("QLabel{font-size:12px;padding:2px 4px;border-radius:4px;}"
                           "QLabel:hover{background:#3a3f47;}")
        self.setToolTip("클릭 → 이 사람 위치로 따라가기")

    def mousePressEvent(self, e):
        self.clicked.emit(self._username)


def _ping_color(ms: int) -> str:
    if ms <= 0:
        return "#888"
    if ms < 60:
        return "#2ecc71"
    if ms < 150:
        return "#f39c12"
    return "#e74c3c"


class _Panel(QFrame):
    """공통 반투명 패널."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "QFrame { background-color: rgba(20,20,24,0.86); border: 1px solid #444;"
            " border-radius: 8px; } QLabel { color: #ddd; border: none; }")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(12, 10, 12, 10)
        self._lay.setSpacing(3)
        self.hide()

    def toggle(self):
        vis = not self.isVisible()
        self.setVisible(vis)
        if vis:
            self.raise_()


class DebugOverlay(_Panel):
    """F12 — 핑·서버·보드·노드수 등 디버그 정보."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._title = QLabel("● Qonvo Debug (F12)")
        self._title.setStyleSheet("color:#6cf; font-weight:bold; font-size:11px;")
        self._lay.addWidget(self._title)
        self._lines = {}
        for key in ("server", "board", "ping", "nodes", "users", "fps"):
            lbl = QLabel("")
            lbl.setStyleSheet("font-size:12px; font-family:Consolas,monospace;")
            self._lay.addWidget(lbl)
            self._lines[key] = lbl
        self._data = {"server": "-", "board": "-", "ping": 0, "nodes": 0, "users": 0, "fps": 0}
        self._render()

    def set(self, **kw):
        self._data.update(kw)
        self._render()

    def _render(self):
        d = self._data
        self._lines["server"].setText(f"server : {d['server']}")
        self._lines["board"].setText(f"board  : {d['board']}")
        ping = d["ping"]
        self._lines["ping"].setText(
            f"ping   : <span style='color:{_ping_color(ping)}'>{ping} ms</span>")
        self._lines["ping"].setTextFormat(Qt.TextFormat.RichText)
        self._lines["nodes"].setText(f"nodes  : {d['nodes']}")
        self._lines["users"].setText(f"users  : {d['users']}")
        self._lines["fps"].setText(f"fps    : {d['fps']}")
        self.adjustSize()


class UserListOverlay(_Panel):
    """F1 — 현재 보드 사용자 목록(이름/레벨/핑/색). 행 클릭 → 따라가기."""

    user_clicked = pyqtSignal(str)
    _LEVEL = {0: "Visitor", 1: "Member", 2: "Operator"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._title = QLabel("● 접속자 (F1)")
        self._title.setStyleSheet("color:#9f9; font-weight:bold; font-size:11px;")
        self._lay.addWidget(self._title)
        self._rows_box = QVBoxLayout()
        self._rows_box.setSpacing(2)
        self._lay.addLayout(self._rows_box)
        self._count = QLabel("")
        self._count.setStyleSheet("color:#888; font-size:10px;")
        self._lay.addWidget(self._count)
        self.set_users([])

    def set_users(self, users: list):
        # 기존 행 제거
        while self._rows_box.count():
            it = self._rows_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for u in users:
            color = u.get("color", "#888")
            name = u.get("user", "?")
            lvl = self._LEVEL.get(u.get("level", 1), "")
            ping = u.get("ping", 0)
            row = _UserRow(
                name,
                f"<span style='color:{color}'>●</span> {name}  "
                f"<span style='color:#888'>{lvl}</span>  "
                f"<span style='color:{_ping_color(ping)}'>{ping}ms</span>")
            row.clicked.connect(self.user_clicked)
            self._rows_box.addWidget(row)
        self._count.setText(f"{len(users)}명 접속")
        self.adjustSize()
