"""People 창 — merri(Mattermost) 기반 동료 관리 + DM 채팅 + 보드 초대.

별도 창(QDialog, 비모달). 프로필(merri 토큰/주소)로 merri API 를 호출하며
실제 아바타·온라인 상태를 렌더한다. 채팅은 말풍선 위젯으로 그리고, 주고받은
이미지 첨부는 인라인 표시 + 클릭하면 원본 뷰어로 크게 본다.

왼쪽: 동료 목록(아바타+이름+상태, 검색 추가, 우클릭 삭제) — 폴링 시 깜빡이지
않도록 상태만 제자리 갱신한다.
오른쪽: 1:1 DM 말풍선 채팅(폴링) + 보드 초대.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QThread, QTimer, QBuffer, QByteArray, pyqtSignal
from PyQt6.QtGui import (QPixmap, QImage, QPainter, QPainterPath, QColor, QFont,
                         QKeySequence)
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QLineEdit, QMessageBox, QMenu, QSplitter, QScrollArea, QFrame,
    QApplication, QFileDialog,
)

from v.settings import get_setting
from v.theme import Theme
from .merri_client import MerriClient, _UA, get_contacts, add_contact, remove_contact

try:
    import websocket as _ws  # websocket-client
    _HAS_WS = True
except Exception:  # pragma: no cover
    _HAS_WS = False

_STATUS_COLOR = {"online": "#43b581", "working": "#9b59b6", "away": "#faa61a",
                 "dnd": "#f04747", "offline": "#747f8d"}
_STATUS_TEXT = {"online": "온라인", "working": "작업 중", "away": "자리비움",
                "dnd": "방해금지", "offline": "오프라인"}
_AVATAR_TINTS = ["#5865f2", "#43b581", "#faa61a", "#eb459e", "#00b0f4", "#f04747", "#9b59b6"]
_IMG_MAX = 260


# ---- 이미지 헬퍼 --------------------------------------------------------
def _circular(data: bytes, size: int) -> Optional[QPixmap]:
    src = QPixmap()
    if not data or not src.loadFromData(data) or src.isNull():
        return None
    src = src.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                     Qt.TransformationMode.SmoothTransformation)
    out = QPixmap(size, size); out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath(); path.addEllipse(0, 0, size, size); p.setClipPath(path)
    p.drawPixmap(-(src.width() - size) // 2, -(src.height() - size) // 2, src)
    p.end()
    return out


def _initials_avatar(name: str, size: int) -> QPixmap:
    out = QPixmap(size, size); out.fill(Qt.GlobalColor.transparent)
    tint = _AVATAR_TINTS[(sum(map(ord, name)) if name else 0) % len(_AVATAR_TINTS)]
    p = QPainter(out); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(tint)); p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(0, 0, size, size)
    p.setPen(QColor("#ffffff"))
    f = QFont(); f.setPointSize(int(size * 0.36)); f.setBold(True); p.setFont(f)
    p.drawText(out.rect(), Qt.AlignmentFlag.AlignCenter, (name[:1] or "?").upper())
    p.end()
    return out


class _Call(QThread):
    done = pyqtSignal(object)
    fail = pyqtSignal(str)

    def __init__(self, fn: Callable, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            self.done.emit(self._fn())
        except Exception as e:
            self.fail.emit(str(e))


class MerriSocket(QThread):
    """merri(Mattermost) WebSocket — 실시간 이벤트(새 메시지 등)를 받는다.

    {base}/api/v4/websocket 에 접속해 토큰으로 authentication_challenge 인증 후
    이벤트 JSON 을 ``ws_event(dict)`` 시그널로 흘린다. 끊기면 폴링이 백업한다.
    """
    ws_event = pyqtSignal(dict)

    def __init__(self, base_url: str, token: str, parent=None):
        super().__init__(parent)
        self._base = (base_url or "").rstrip("/")
        self._token = token
        self._app = None
        self._running = False

    def run(self):
        if not _HAS_WS or not self._base or not self._token:
            return
        self._running = True
        url = (self._base.replace("https://", "wss://").replace("http://", "ws://")
               + "/api/v4/websocket")

        def on_open(app):
            app.send(json.dumps({"seq": 1, "action": "authentication_challenge",
                                 "data": {"token": self._token}}))

        def on_message(app, raw):
            try:
                self.ws_event.emit(json.loads(raw))
            except Exception:
                pass

        try:
            self._app = _ws.WebSocketApp(
                url, header=[f"User-Agent: {_UA}"],
                on_open=on_open, on_message=on_message)
            self._app.run_forever(ping_interval=30, ping_timeout=10)
        except Exception:
            pass

    def stop(self):
        self._running = False
        if self._app is not None:
            try:
                self._app.close()
            except Exception:
                pass


def _find_merri_base(servers: list) -> str:
    for s in servers if isinstance(servers, list) else []:
        host = s.get("host", ""); port = int(s.get("port", 9700)); secure = s.get("secure", False)
        scheme = "https" if (secure or str(host).startswith(("https://", "wss://"))) else "http"
        h = host.split("://", 1)[-1].rstrip("/")
        base = f"{scheme}://{h}" if ":" in h else f"{scheme}://{h}:{port}"
        try:
            with urllib.request.urlopen(base + "/", timeout=5) as r:
                d = json.loads(r.read().decode("utf-8"))
            if d.get("auth", {}).get("merri"):
                return base
        except Exception:
            continue
    return ""


def _qimage_png_bytes(img: QImage) -> bytes:
    if img is None or img.isNull():
        return b""
    ba = QByteArray(); buf = QBuffer(ba); buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return ba.data()


# ---- 채팅 입력(붙여넣기·드래그로 이미지/파일 첨부) ----------------------
class _ChatInput(QLineEdit):
    """Ctrl+V 이미지 붙여넣기 + 파일 드래그&드롭 → attach(bytes, filename)."""
    attach = pyqtSignal(object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def keyPressEvent(self, e):
        if e.matches(QKeySequence.StandardKey.Paste):
            md = QApplication.clipboard().mimeData()
            if md is not None and md.hasImage():
                data = _qimage_png_bytes(QImage(md.imageData()))
                if data:
                    self.attach.emit(data, "pasted.png")
                    return
        super().keyPressEvent(e)

    def dragEnterEvent(self, e):
        md = e.mimeData()
        if md.hasImage() or md.hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        md = e.mimeData()
        if md.hasImage():
            data = _qimage_png_bytes(QImage(md.imageData()))
            if data:
                self.attach.emit(data, "image.png")
        elif md.hasUrls():
            for u in md.urls():
                p = u.toLocalFile()
                if p and os.path.isfile(p):
                    try:
                        with open(p, "rb") as f:
                            self.attach.emit(f.read(), os.path.basename(p))
                    except Exception:
                        pass
        e.acceptProposedAction()


# ---- 클릭 가능한 이미지 라벨 --------------------------------------------
class _ClickImage(QLabel):
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("background:transparent;")
        self._full: Optional[QImage] = None

    def mousePressEvent(self, e):
        self.clicked.emit()


# ---- 원본 이미지 뷰어 ---------------------------------------------------
class _ImageViewer(QDialog):
    def __init__(self, image: QImage, parent=None):
        super().__init__(parent)
        self.setWindowTitle("이미지")
        self.setStyleSheet("QDialog{background:#0d0d0d;}")
        from PyQt6.QtWidgets import QApplication
        scr = QApplication.primaryScreen().availableGeometry()
        maxw, maxh = int(scr.width() * 0.85), int(scr.height() * 0.85)
        pm = QPixmap.fromImage(image)
        if pm.width() > maxw or pm.height() > maxh:
            pm = pm.scaled(maxw, maxh, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(); lbl.setPixmap(pm); lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(lbl)
        self.resize(pm.width(), pm.height())


# ---- 동료 한 줄 위젯 ----------------------------------------------------
class _ContactRow(QWidget):
    def __init__(self, user: dict, status: str):
        super().__init__()
        self.user = user
        name = user.get("username", "?")
        lay = QHBoxLayout(self); lay.setContentsMargins(6, 5, 6, 5); lay.setSpacing(10)
        self._av = QLabel(); self._av.setFixedSize(40, 40)
        self._av.setPixmap(_initials_avatar(name, 40))
        self._av.setStyleSheet("background:transparent;")
        lay.addWidget(self._av)
        col = QVBoxLayout(); col.setSpacing(1)
        self._name = QLabel(name)
        self._name.setStyleSheet("color:#fff;font-size:13px;font-weight:bold;background:transparent;")
        col.addWidget(self._name)
        self._sub = QLabel(); self._sub.setStyleSheet("font-size:11px;background:transparent;")
        col.addWidget(self._sub)
        lay.addLayout(col); lay.addStretch()
        self._badge = QLabel(); self._badge.setFixedSize(10, 10)
        self._badge.setStyleSheet("background:#f04747;border-radius:5px;")
        self._badge.setVisible(False)
        lay.addWidget(self._badge)
        self.set_status(status)

    def set_unread(self, on: bool):
        self._badge.setVisible(on)

    def set_status(self, status: str, board: str = ""):
        c = _STATUS_COLOR.get(status, "#747f8d")
        label = _STATUS_TEXT.get(status, status)
        if status == "working" and board:
            label = f"{label} · {board}"
        self._sub.setText(f"<span style='color:{c}'>●</span> "
                          f"<span style='color:#aaa'>{label}</span>")

    def set_avatar(self, pix: Optional[QPixmap]):
        if pix is not None and not pix.isNull():
            self._av.setPixmap(pix)


# ---- 반응(이모지) ------------------------------------------------------
_EMOJI_CHAR = {
    "thumbsup": "👍", "+1": "👍", "heart": "❤️", "joy": "😂", "laughing": "😂",
    "tada": "🎉", "eyes": "👀", "fire": "🔥", "smile": "😄", "cry": "😢",
    "open_mouth": "😮", "100": "💯", "pray": "🙏", "ok_hand": "👌", "rocket": "🚀",
}
_PICKER = [("👍", "thumbsup"), ("❤️", "heart"), ("😂", "joy"),
           ("🎉", "tada"), ("👀", "eyes"), ("🔥", "fire")]


def _emoji_char(name: str) -> str:
    return _EMOJI_CHAR.get(name, f":{name}:")


def _fmt_time(create_at_ms) -> str:
    """Mattermost create_at(ms) → 'HH:MM'. 실패 시 빈 문자열."""
    try:
        return time.strftime("%H:%M", time.localtime(float(create_at_ms) / 1000.0))
    except Exception:
        return ""


class _ReactionChip(QPushButton):
    def __init__(self, emoji_name: str, count: int, mine: bool, on_click):
        super().__init__(f"{_emoji_char(emoji_name)} {count}")
        bg = "#3a4a6a" if mine else "#2f3136"
        bd = "#5865f2" if mine else "#4a4a4a"
        self.setStyleSheet(f"QPushButton{{background:{bg};border:1px solid {bd};border-radius:10px;"
                           f"padding:1px 8px;color:#ddd;font-size:11px;}}"
                           f"QPushButton:hover{{background:#40444b;}}")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(lambda: on_click(emoji_name))


# ---- 말풍선 ------------------------------------------------------------
class _Bubble(QWidget):
    """메시지 한 개 — 답글 미리보기 + 텍스트/첨부 + 반응 + 호버 액션(반응/답글)."""

    def __init__(self, post: dict, who: str, mine: bool, me_id: str,
                 root_preview: str, cb: dict):
        super().__init__()
        self._post = post; self._cb = cb
        pid = post.get("id", "")
        outer = QHBoxLayout(self); outer.setContentsMargins(2, 1, 2, 1); outer.setSpacing(4)

        colw = QWidget(); col = QVBoxLayout(colw); col.setContentsMargins(0, 0, 0, 0); col.setSpacing(3)
        bubble = QFrame()
        bg = "#2b5278" if mine else "#36393f"
        bubble.setStyleSheet(f"QFrame{{background:{bg};border-radius:12px;}}")
        bubble.setMaximumWidth(460)
        bv = QVBoxLayout(bubble); bv.setContentsMargins(12, 8, 12, 8); bv.setSpacing(5)
        if root_preview:
            rp = QLabel("↳ " + root_preview)
            rp.setStyleSheet("color:#9aa4b2;font-size:11px;border-left:2px solid #5865f2;"
                             "padding-left:6px;background:transparent;")
            rp.setWordWrap(True); bv.addWidget(rp)
        tstr = _fmt_time(post.get("create_at", 0))
        if not mine and (who or tstr):
            nm = QLabel(f"{who}  <span style='color:#7a818c;font-weight:normal'>{tstr}</span>")
            nm.setStyleSheet("color:#9ecbff;font-size:11px;font-weight:bold;background:transparent;")
            bv.addWidget(nm)
        text = post.get("message", "")
        if text:
            t = QLabel(text)
            t.setWordWrap(True); t.setTextFormat(Qt.TextFormat.PlainText)
            t.setStyleSheet("color:#fff;font-size:13px;background:transparent;")
            t.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            bv.addWidget(t)
        self._img_slots = {}
        self._bv = bv
        if mine and tstr:   # 내 메시지: 시간만 작게 우측
            tl = QLabel(tstr); tl.setStyleSheet("color:#9fb4d8;font-size:10px;background:transparent;")
            tl.setAlignment(Qt.AlignmentFlag.AlignRight); bv.addWidget(tl)
        col.addWidget(bubble, 0, Qt.AlignmentFlag.AlignRight if mine else Qt.AlignmentFlag.AlignLeft)

        # 반응 줄
        rwrap = QWidget(); self._react_row = QHBoxLayout(rwrap)
        self._react_row.setContentsMargins(2, 0, 2, 0); self._react_row.setSpacing(3)
        reactions = (post.get("metadata") or {}).get("reactions") or []
        groups: dict = {}
        for r in reactions:
            n = r.get("emoji_name", ""); groups.setdefault(n, [0, False])
            groups[n][0] += 1
            if r.get("user_id") == me_id:
                groups[n][1] = True
        if mine:
            self._react_row.addStretch()
        for n, (cnt, ismine) in groups.items():
            self._react_row.addWidget(_ReactionChip(
                n, cnt, ismine, lambda emoji, p=pid: cb["on_react"](p, emoji)))
        if not mine:
            self._react_row.addStretch()
        rwrap.setVisible(bool(groups))
        col.addWidget(rwrap)

        # 호버 액션(반응 추가 / 답글)
        self._actions = QWidget()
        ah = QHBoxLayout(self._actions); ah.setContentsMargins(0, 0, 0, 0); ah.setSpacing(2)
        for label, tip, fn in (("🙂", "반응", lambda: cb["on_pick"](post, self._actions)),
                               ("↩", "답글", lambda: cb["on_reply"](post))):
            b = QPushButton(label); b.setToolTip(tip); b.setFixedSize(26, 26)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet("QPushButton{background:#2f3136;border:1px solid #444;border-radius:13px;"
                            "font-size:12px;}QPushButton:hover{background:#40444b;}")
            b.clicked.connect(fn); ah.addWidget(b)
        self._actions.hide()

        if mine:
            outer.addStretch(); outer.addWidget(self._actions, 0, Qt.AlignmentFlag.AlignVCenter)
            outer.addWidget(colw)
        else:
            outer.addWidget(colw)
            outer.addWidget(self._actions, 0, Qt.AlignmentFlag.AlignVCenter); outer.addStretch()

    def enterEvent(self, e):
        self._actions.show()

    def leaveEvent(self, e):
        self._actions.hide()

    def add_image_slot(self, file_id: str, on_click: Callable[[str], None]) -> _ClickImage:
        lbl = _ClickImage()
        lbl.setText("이미지 불러오는 중…")
        lbl.setStyleSheet("color:#aaa;background:transparent;font-size:11px;")
        lbl.clicked.connect(lambda fid=file_id: on_click(fid))
        self._bv.addWidget(lbl)
        self._img_slots[file_id] = lbl
        return lbl

    def add_file(self, name: str):
        lbl = QLabel(f"📎 {name}")
        lbl.setStyleSheet("color:#cfe3ff;background:transparent;font-size:12px;")
        self._bv.addWidget(lbl)

    def add_custom(self, w: QWidget):
        self._bv.addWidget(w)

    def add_thread_footer(self, count: int, on_click):
        btn = QPushButton(f"💬 {count}개의 답글")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet("QPushButton{background:transparent;color:#8ab4ff;border:none;"
                          "font-size:11px;text-align:left;padding:3px 0 0 0;}"
                          "QPushButton:hover{text-decoration:underline;}")
        btn.clicked.connect(on_click)
        self._bv.addWidget(btn)


# ---- 채팅 뷰(스크롤 영역) ----------------------------------------------
class _ChatView(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setStyleSheet("QScrollArea{background:#1e1e1e;border:1px solid #333;border-radius:12px;}"
                           "QWidget#inner{background:#1e1e1e;}")
        self._inner = QWidget(); self._inner.setObjectName("inner")
        self._v = QVBoxLayout(self._inner)
        self._v.setContentsMargins(10, 10, 10, 10); self._v.setSpacing(2)
        self._v.addStretch()
        self.setWidget(self._inner)
        self.img_slots = {}   # file_id -> _ClickImage (현재 화면)

    def clear(self):
        self.img_slots = {}
        while self._v.count() > 1:
            it = self._v.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()

    def set_placeholder(self, text: str):
        self.clear()
        lbl = QLabel(text); lbl.setStyleSheet("color:#777;font-style:italic;background:transparent;")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._v.insertWidget(0, lbl)

    def add_bubble(self, bubble: _Bubble):
        self._v.insertWidget(self._v.count() - 1, bubble)
        for fid, lbl in bubble._img_slots.items():
            self.img_slots[fid] = lbl

    def add_divider(self, text: str):
        w = QLabel(f"───  {text}  ───")
        w.setStyleSheet("color:#777;font-size:11px;background:transparent;")
        w.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._v.insertWidget(self._v.count() - 1, w)

    def scroll_bottom(self):
        QTimer.singleShot(0, lambda: self.verticalScrollBar().setValue(
            self.verticalScrollBar().maximum()))


class _AddPeopleDialog(QDialog):
    """사람 검색 → 연락처 추가/제거 (메인 목록과 분리된 별도 창)."""

    def __init__(self, client, me_id: str, parent=None):
        super().__init__(parent)
        self._client = client; self._me_id = me_id
        self._results: list = []; self._jobs: set = set(); self._avatars: dict = {}
        self._rows: dict = {}
        self.setWindowTitle("사람 추가")
        self.setModal(True)
        self.resize(420, 540)
        self.setStyleSheet("QDialog{background:#252525;}")

        v = QVBoxLayout(self); v.setContentsMargins(14, 14, 14, 14); v.setSpacing(8)
        title = QLabel("동료 추가")
        title.setStyleSheet("color:#fff;font-weight:bold;font-size:15px;background:transparent;")
        v.addWidget(title)
        self._search = QLineEdit()
        self._search.setPlaceholderText("🔍 merri 사용자 이름 검색")
        self._search.setStyleSheet(
            "background:#1e1e1e;color:#fff;border:1px solid #444;border-radius:8px;padding:9px;")
        self._search.textChanged.connect(self._debounce)
        self._search.returnPressed.connect(self._do_search)
        v.addWidget(self._search)
        self._list = QListWidget()
        self._list.setStyleSheet(
            "QListWidget{background:transparent;border:none;}"
            "QListWidget::item{border-radius:8px;margin:1px 0;}"
            "QListWidget::item:hover{background:#33363c;}")
        self._list.itemClicked.connect(self._toggle)
        v.addWidget(self._list, 1)
        hint = QLabel("이름을 검색하고, 결과를 클릭하면 추가/제거됩니다.")
        hint.setStyleSheet("color:#888;font-size:11px;background:transparent;")
        v.addWidget(hint)
        close = QPushButton("닫기")
        close.setStyleSheet("padding:9px 16px;background:#3a3f47;color:#fff;border-radius:8px;")
        close.clicked.connect(self.accept)
        v.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)

        self._timer = QTimer(self); self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._do_search)

    def _run(self, fn, on_done):
        job = _Call(fn, self); self._jobs.add(job)
        job.done.connect(on_done)
        job.done.connect(lambda *_: self._jobs.discard(job))
        job.fail.connect(lambda *_: self._jobs.discard(job))
        job.start()

    def _debounce(self):
        self._timer.start(300)

    def _do_search(self):
        term = self._search.text().strip()
        if not term:
            self._list.clear(); self._rows.clear(); return
        self._run(lambda: self._client.search_users(term), self._on_results)

    def _on_results(self, users: list):
        self._results = [u for u in users if u.get("id") != self._me_id]
        self._render()

    def _render(self):
        have = set(get_contacts())
        self._list.clear(); self._rows.clear()
        if not self._results:
            it = QListWidgetItem("검색 결과가 없습니다."); it.setFlags(Qt.ItemFlag.NoItemFlags)
            it.setForeground(QColor("#888")); self._list.addItem(it); return
        for u in self._results:
            uid = u.get("id", "")
            row = _ContactRow(u, "offline")
            row._sub.setText("<span style='color:#43b581'>✓ 추가됨 (클릭해 제거)</span>" if uid in have
                             else "<span style='color:#5865f2'>＋ 클릭해 추가</span>")
            self._rows[uid] = row
            it = QListWidgetItem(); it.setData(Qt.ItemDataRole.UserRole, u)
            it.setSizeHint(row.sizeHint()); self._list.addItem(it)
            self._list.setItemWidget(it, row)
            self._fetch_avatar(uid, u.get("username", "?"), row)

    def _fetch_avatar(self, uid, name, row):
        if uid in self._avatars:
            row.set_avatar(self._avatars[uid]); return
        cli = self._client

        def _store(data):
            pix = _circular(data, 40) or _initials_avatar(name, 40)
            self._avatars[uid] = pix; row.set_avatar(pix)
        self._run(lambda: cli.get_image(uid), _store)

    def _toggle(self, item: QListWidgetItem):
        u = item.data(Qt.ItemDataRole.UserRole)
        if not u:
            return
        uid = u.get("id", "")
        if uid in set(get_contacts()):
            remove_contact(uid)
        else:
            add_contact(uid)
        self._render()

    def closeEvent(self, a0):
        for job in list(self._jobs):
            job.wait(1000)
        super().closeEvent(a0)


class PeopleWindow(QDialog):
    """동료 관리 + 말풍선 DM 채팅 별도 창."""

    def __init__(self, invite_requester=None, invite_joiner=None,
                 host_status=None, invite_stopper=None, board_image_adder=None,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("People")
        self.setModal(False)
        self.resize(960, 660)
        self.setMinimumSize(680, 460)
        self.setStyleSheet(f"QDialog{{background:{Theme.BG_PRIMARY};}}")
        # invite_requester(cb): 호스팅 보장 후 cb(link|None). invite_joiner(link): 참여
        # host_status(): 현재 내가 호스팅 중인 링크(or None). invite_stopper(): 호스팅 중지
        # board_image_adder(QImage): DM 이미지를 현재 보드에 카드로 추가
        self._invite_requester = invite_requester
        self._invite_joiner = invite_joiner
        self._host_status = host_status
        self._invite_stopper = invite_stopper
        self._board_image_adder = board_image_adder
        self._client: Optional[MerriClient] = None
        self._me_id = ""; self._me_name = ""
        self._names: dict = {}
        self._avatars: dict = {}
        self._rows: dict = {}
        self._displayed_ids: list = []
        self._file_cache: dict = {}
        self._fetching: set = set()
        self._cur_posts: list = []
        self._rendered_ids: list = []
        self._mode = "contacts"
        self._cur_user: Optional[dict] = None
        self._cur_channel = ""
        self._thread_root = ""        # 스레드 뷰 중이면 루트 post id
        self._unread: set = set()     # 안 읽은 DM 의 상대 user_id
        self._jobs: set = set()
        self._socket: Optional[MerriSocket] = None
        self._build_ui()
        self._poll = QTimer(self); self._poll.setInterval(6000); self._poll.timeout.connect(self._tick)
        self.reload()

    # ---- UI -------------------------------------------------------------
    def _build_ui(self):
        outer = QHBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        split = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget(); left.setStyleSheet("background:#252525;")
        lv = QVBoxLayout(left); lv.setContentsMargins(12, 12, 12, 12); lv.setSpacing(8)
        self._me_label = QLabel("People")
        self._me_label.setStyleSheet("color:#fff;font-weight:bold;font-size:16px;background:transparent;")
        lv.addWidget(self._me_label)

        self._login_box = QWidget()
        lb = QVBoxLayout(self._login_box); lb.setContentsMargins(0, 24, 0, 0)
        hint = QLabel("동료와 채팅하고 보드에 초대하려면\nmerri 계정으로 로그인하세요.")
        hint.setStyleSheet("color:#aaa;font-size:12px;background:transparent;")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter); lb.addWidget(hint)
        self._login_btn = QPushButton("merri 로그인")
        self._login_btn.setStyleSheet(
            "padding:10px 18px;background:#5865f2;color:#fff;font-weight:bold;border-radius:8px;")
        self._login_btn.clicked.connect(self._login_merri)
        lb.addWidget(self._login_btn, 0, Qt.AlignmentFlag.AlignCenter); lb.addStretch()
        lv.addWidget(self._login_box)

        self._add_btn = QPushButton("＋ 사람 추가")
        self._add_btn.setStyleSheet(
            "QPushButton{background:#33363c;color:#ddd;border:1px solid #444;border-radius:8px;"
            "padding:9px;font-weight:bold;}QPushButton:hover{background:#3a3f47;color:#fff;}")
        self._add_btn.clicked.connect(self._open_add_dialog)
        lv.addWidget(self._add_btn)

        self._list = QListWidget()
        self._list.setStyleSheet(
            "QListWidget{background:transparent;border:none;}"
            "QListWidget::item{border-radius:8px;margin:1px 0;}"
            "QListWidget::item:hover{background:#33363c;}"
            "QListWidget::item:selected{background:#3a3f47;}")
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context)
        self._list.itemClicked.connect(self._on_item)
        lv.addWidget(self._list, 1)
        split.addWidget(left)

        right = QWidget(); right.setStyleSheet("background:#1b1b1b;")
        rv = QVBoxLayout(right); rv.setContentsMargins(14, 12, 14, 12); rv.setSpacing(8)
        hrow = QHBoxLayout()
        self._back_btn = QPushButton("←"); self._back_btn.setFixedSize(30, 30)
        self._back_btn.setToolTip("채팅으로 돌아가기")
        self._back_btn.setStyleSheet(
            "QPushButton{background:#2f3136;color:#fff;border:1px solid #444;border-radius:15px;"
            "font-size:15px;}QPushButton:hover{background:#40444b;}")
        self._back_btn.clicked.connect(self._close_thread)
        self._back_btn.hide()
        hrow.addWidget(self._back_btn)
        self._dm_avatar = QLabel(); self._dm_avatar.setFixedSize(34, 34)
        self._dm_avatar.setStyleSheet("background:transparent;")
        hrow.addWidget(self._dm_avatar)
        self._dm_title = QLabel("대화 상대를 고르세요")
        self._dm_title.setStyleSheet("color:#fff;font-size:15px;font-weight:bold;background:transparent;")
        hrow.addWidget(self._dm_title); hrow.addStretch()
        self._invite_btn = QPushButton("＋ 보드 초대"); self._invite_btn.setEnabled(False)
        self._invite_btn.setStyleSheet(
            "QPushButton{padding:7px 14px;background:#43b581;color:#fff;font-weight:bold;border-radius:8px;}"
            "QPushButton:disabled{background:#33363c;color:#777;}")
        self._invite_btn.clicked.connect(self._on_invite)
        hrow.addWidget(self._invite_btn)
        rv.addLayout(hrow)

        self._chat = _ChatView()
        rv.addWidget(self._chat, 1)

        irow = QHBoxLayout()
        self._attach_btn = QPushButton("📎"); self._attach_btn.setEnabled(False)
        self._attach_btn.setToolTip("이미지/파일 보내기 (붙여넣기·드래그도 됨)")
        self._attach_btn.setStyleSheet(
            "QPushButton{padding:10px 12px;background:#2d2d2d;color:#ddd;border:1px solid #444;"
            "border-radius:18px;font-size:15px;}QPushButton:hover{background:#3a3f47;}"
            "QPushButton:disabled{color:#666;}")
        self._attach_btn.clicked.connect(self._pick_file)
        irow.addWidget(self._attach_btn)
        self._input = _ChatInput(); self._input.setEnabled(False)
        self._input.setPlaceholderText("메시지 입력 (이미지는 붙여넣기/드래그)…")
        self._input.setStyleSheet(
            "background:#2d2d2d;color:#fff;border:1px solid #444;border-radius:18px;padding:11px 16px;")
        self._input.returnPressed.connect(self._on_send)
        self._input.attach.connect(self._send_attachment)
        irow.addWidget(self._input, 1)
        send = QPushButton("보내기"); send.setStyleSheet(
            "padding:11px 18px;background:#5865f2;color:#fff;font-weight:bold;border-radius:18px;")
        send.clicked.connect(self._on_send)
        irow.addWidget(send)
        rv.addLayout(irow)
        split.addWidget(right)

        split.setSizes([300, 660])
        split.setStretchFactor(1, 1)
        outer.addWidget(split)

    def _set_logged_in_ui(self, on: bool):
        self._login_box.setVisible(not on)
        for w in (self._add_btn, self._list):
            w.setVisible(on)

    def _open_add_dialog(self):
        if self._client is None:
            return
        dlg = _AddPeopleDialog(self._client, self._me_id, self)
        dlg.finished.connect(lambda *_: self._show_contacts())
        dlg.exec()

    # ---- 워커 ----------------------------------------------------------
    def _run(self, fn, on_done, on_fail=None):
        job = _Call(fn, self); self._jobs.add(job)
        job.done.connect(on_done)
        job.fail.connect(on_fail or (lambda m: None))
        job.done.connect(lambda *_: self._jobs.discard(job))
        job.fail.connect(lambda *_: self._jobs.discard(job))
        job.start()

    # ---- 로그인 --------------------------------------------------------
    def _login_merri(self):
        servers = get_setting("server_list", [])
        self._login_btn.setText("merri 서버 확인 중…"); self._login_btn.setEnabled(False)
        self._run(lambda: _find_merri_base(servers), self._do_login)

    def _do_login(self, base: str):
        from .profile import login_blocking, save_profile
        self._login_btn.setText("merri 로그인"); self._login_btn.setEnabled(True)
        if not base:
            QMessageBox.information(self, "merri 로그인",
                                    "merri 인증을 쓰는 서버를 먼저 추가하세요(서버 목록).")
            return
        res = login_blocking(base, self)
        if not res:
            return
        save_profile(res[0], res[1], res[2] if len(res) > 2 else "")
        self.reload()

    # ---- 로드/폴링 -----------------------------------------------------
    def reload(self):
        self._client = MerriClient.from_profile()
        if self._client is None:
            self._me_label.setText("People"); self._set_logged_in_ui(False)
            return
        self._set_logged_in_ui(True)
        self._run(lambda: self._client.me(), self._on_me)

    def _on_me(self, me: dict):
        self._me_id = me.get("id", ""); self._me_name = me.get("username", "")
        self._names[self._me_id] = self._me_name
        self._me_label.setText(f"👥  {self._me_name}")
        self._fetch_avatar(self._me_id, self._me_name)
        self._show_contacts()
        if not self._poll.isActive():
            self._poll.start()
        self._start_socket()

    def _start_socket(self):
        """merri WebSocket 으로 실시간 이벤트 구독(폴링은 백업으로 유지)."""
        if self._socket is not None or self._client is None or not _HAS_WS:
            return
        self._socket = MerriSocket(self._client.base, self._client.token, self)
        self._socket.ws_event.connect(self._on_ws_event)
        self._socket.start()

    def _on_ws_event(self, msg: dict):
        ev = msg.get("event")
        bcast_chan = (msg.get("broadcast") or {}).get("channel_id", "")
        if ev == "posted":
            data = msg.get("data", {})
            try:
                post = json.loads(data.get("post", "{}"))
            except Exception:
                return
            if post.get("channel_id") == self._cur_channel:
                self._rendered_ids = None
                self._load_posts()
            elif (data.get("channel_type") == "D" and post.get("user_id") != self._me_id
                  and "__" in data.get("channel_name", "")):
                # 안 열린 DM 의 새 메시지 → 그 상대를 안읽음 표시
                a, b = data["channel_name"].split("__", 1)
                other = a if b == self._me_id else b
                self._unread.add(other)
                self._set_row_unread(other, True)
        elif ev in ("reaction_added", "reaction_removed"):
            # 반응 변화는 메시지 id 가 그대로라 강제 재렌더
            if bcast_chan == self._cur_channel or not bcast_chan:
                self._rendered_ids = None
                self._load_posts()

    def _show_contacts(self):
        """전체 목록 재구성(연락처 추가/삭제·최초 로드 때만)."""
        self._mode = "contacts"
        ids = get_contacts()
        self._list.clear(); self._rows.clear()
        self._displayed_ids = list(ids)
        if not ids:
            it = QListWidgetItem("아직 동료가 없어요.\n위에서 검색해 추가하세요.")
            it.setFlags(Qt.ItemFlag.NoItemFlags); it.setForeground(QColor("#888"))
            self._list.addItem(it); return
        from .qonvo_presence import fetch_presence
        tok = self._client.token
        self._run(lambda: (self._client.users_by_ids(ids), fetch_presence(tok)),
                  self._on_contacts)

    def _on_contacts(self, result):
        users, presence = result   # presence: {username: {status, board}}
        self._list.clear(); self._rows.clear()
        for u in users:
            uid = u.get("id", ""); uname = u.get("username", "")
            self._names[uid] = uname
            pres = presence.get(uname) or {}
            row = _ContactRow(u, pres.get("status", "offline"))
            row.set_status(pres.get("status", "offline"), pres.get("board", ""))
            row.set_unread(uid in self._unread)
            self._rows[uid] = row
            it = QListWidgetItem(); it.setData(Qt.ItemDataRole.UserRole, u)
            it.setSizeHint(row.sizeHint()); self._list.addItem(it)
            self._list.setItemWidget(it, row)
            self._apply_avatar(uid, row)
            self._fetch_avatar(uid, uname)

    def _set_row_unread(self, uid: str, on: bool):
        row = self._rows.get(uid)
        if row is not None:
            row.set_unread(on)

    def _refresh_statuses(self):
        """폴링: 목록을 다시 그리지 않고 qonvo 상태만 제자리 갱신(깜빡임 방지)."""
        if not self._rows:
            return
        from .qonvo_presence import fetch_presence
        tok = self._client.token
        self._run(lambda: fetch_presence(tok), self._apply_statuses)

    def _apply_statuses(self, presence: dict):
        for uid, row in self._rows.items():
            uname = self._names.get(uid, "")
            pres = presence.get(uname) or {}
            row.set_status(pres.get("status", "offline"), pres.get("board", ""))

    def _tick(self):
        if self._client is None:
            return
        if self._mode == "contacts":
            if set(get_contacts()) != set(self._displayed_ids):
                self._show_contacts()      # 구성 변경 시에만 재구성
            else:
                self._refresh_statuses()   # 평소엔 상태만 갱신
        if self._cur_channel:
            self._load_posts()

    # ---- 아바타 --------------------------------------------------------
    def _fetch_avatar(self, uid: str, name: str):
        if uid in self._avatars or not uid:
            self._apply_avatar(uid, self._rows.get(uid))
            return
        cli = self._client

        def _store(data):
            pix = _circular(data, 40) or _initials_avatar(name, 40)
            self._avatars[uid] = pix
            self._apply_avatar(uid, self._rows.get(uid))
            if self._cur_user and self._cur_user.get("id") == uid:
                self._dm_avatar.setPixmap(_circular(data, 34) or _initials_avatar(name, 34))
        self._run(lambda: cli.get_image(uid), _store)

    def _apply_avatar(self, uid: str, row):
        if row is not None and uid in self._avatars:
            row.set_avatar(self._avatars[uid])

    # ---- 목록 클릭/우클릭 ----------------------------------------------
    def _on_item(self, item: QListWidgetItem):
        user = item.data(Qt.ItemDataRole.UserRole)
        if user:
            self._open_dm(user)

    def _on_context(self, pos):
        item = self._list.itemAt(pos)
        if not item or self._mode != "contacts":
            return
        user = item.data(Qt.ItemDataRole.UserRole)
        if not user:
            return
        menu = QMenu(self)
        act = menu.addAction(f"'{user.get('username','?')}' 연락처 삭제")
        if menu.exec(self._list.mapToGlobal(pos)) == act:
            remove_contact(user.get("id", "")); self._show_contacts()

    # ---- DM ------------------------------------------------------------
    def _open_dm(self, user: dict):
        self._cur_user = user
        uid = user.get("id", ""); name = user.get("username", "?")
        self._unread.discard(uid); self._set_row_unread(uid, False)
        self._dm_title.setText(name)
        pix = self._avatars.get(uid)
        self._dm_avatar.setPixmap(pix.scaled(34, 34, Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation)
                                  if pix else _initials_avatar(name, 34))
        self._input.setEnabled(True); self._invite_btn.setEnabled(True)
        self._attach_btn.setEnabled(True)
        self._close_thread()        # DM 전환 시 스레드 뷰 해제
        self._rendered_ids = None   # 첫 응답은 빈([])이어도 반드시 렌더
        self._chat.set_placeholder("불러오는 중…")
        self._run(lambda: self._client.direct_channel(self._me_id, uid), self._on_channel)

    def _on_channel(self, channel: dict):
        self._cur_channel = channel.get("id", ""); self._load_posts()

    def _load_posts(self):
        ch = self._cur_channel
        if ch:
            self._run(lambda: self._client.get_posts(ch), self._on_posts)

    def _on_posts(self, posts: list):
        ids = [p.get("id") for p in posts]
        if ids == self._rendered_ids:
            return                      # 변화 없으면 다시 그리지 않음(깜빡임 방지)
        self._rendered_ids = ids
        self._cur_posts = posts
        self._render()

    def _cb(self) -> dict:
        return {"on_react": self._toggle_reaction, "on_pick": self._open_reaction_picker,
                "on_reply": self._reply_to_post, "on_image": self._open_image}

    def _make_bubble(self, p: dict) -> "_Bubble":
        who = self._names.get(p.get("user_id", ""), "?")
        mine = p.get("user_id") == self._me_id
        bubble = _Bubble(p, who, mine, self._me_id, "", self._cb())
        for f in (p.get("metadata") or {}).get("files", []) or []:
            fid = f.get("id", "")
            if not fid:
                continue
            if str(f.get("mime_type", "")).startswith("image/"):
                slot = bubble.add_image_slot(fid, self._open_image)
                slot.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                slot.customContextMenuRequested.connect(
                    lambda pos, ff=fid, ss=slot: self._image_menu(ff, ss, pos))
                if fid in self._file_cache:
                    self._set_slot_image(slot, self._file_cache[fid])
                else:
                    self._fetch_file(fid)
            else:
                bubble.add_file(f.get("name", "파일"))
        # 첨부 카드(초대 카드 등) — props.attachments
        atts = (p.get("props") or {}).get("attachments") or []
        if atts:
            inv = self._find_invite(p)
            mine = p.get("user_id") == self._me_id
            for att in atts:
                bubble.add_custom(self._make_attachment_card(att, inv, mine))
        return bubble

    def _find_invite(self, post: dict) -> str:
        """초대 링크를 숨은 prop(qonvo_invite)에서, 없으면 본문에서 찾는다."""
        link = (post.get("props") or {}).get("qonvo_invite", "")
        if link:
            return link
        from .invite import parse_invite
        for tok in (post.get("message", "") or "").replace("👉", " ").split():
            if "@" in tok:
                inv = parse_invite(tok)
                if inv and inv.board_id:
                    return tok
        return ""

    def _make_attachment_card(self, att: dict, invite_link: str, mine: bool) -> QWidget:
        color = att.get("color", "#5865f2")
        card = QFrame()
        card.setStyleSheet(f"QFrame{{background:#2f3136;border-left:3px solid {color};border-radius:6px;}}")
        card.setMaximumWidth(420)
        cv = QVBoxLayout(card); cv.setContentsMargins(10, 8, 10, 8); cv.setSpacing(6)
        top = QHBoxLayout(); top.setSpacing(10)
        thumb = QLabel(); thumb.setFixedSize(56, 56)
        thumb.setStyleSheet("background:#1e1e1e;border-radius:6px;")
        top.addWidget(thumb)
        col = QVBoxLayout(); col.setSpacing(2)
        if att.get("author_name"):
            a = QLabel(att["author_name"]); a.setStyleSheet("color:#8ab4ff;font-size:10px;background:transparent;")
            col.addWidget(a)
        if att.get("title"):
            t = QLabel(att["title"]); t.setStyleSheet("color:#fff;font-size:13px;font-weight:bold;background:transparent;")
            col.addWidget(t)
        if att.get("text"):
            body = att["text"].replace("**", "")
            x = QLabel(body); x.setWordWrap(True)
            x.setStyleSheet("color:#cfd3dc;font-size:12px;background:transparent;")
            col.addWidget(x)
        top.addLayout(col, 1)
        cv.addLayout(top)

        # 액션: 내 초대면 호스팅중지/종료, 받은 초대면 참여하기
        active_link = self._host_status() if self._host_status else None
        if mine:
            if invite_link and active_link == invite_link:
                stop = QPushButton("🟢 호스팅 중 · 중지")
                stop.setStyleSheet("QPushButton{background:#3a3f47;color:#fff;border-radius:6px;padding:6px;}"
                                   "QPushButton:hover{background:#f04747;}")
                stop.clicked.connect(self._on_stop_hosting)
                cv.addWidget(stop)
            else:
                gone = QLabel("⚫ 종료된 초대"); gone.setStyleSheet("color:#888;font-size:11px;background:transparent;")
                cv.addWidget(gone)
        elif invite_link and self._invite_joiner:
            join = QPushButton("참여하기")
            join.setStyleSheet("QPushButton{background:#43b581;color:#fff;font-weight:bold;"
                               "border-radius:6px;padding:6px;}QPushButton:hover{background:#3aa372;}")
            join.clicked.connect(lambda _=None, lk=invite_link: self._invite_joiner(lk))
            cv.addWidget(join)

        thumb_url = att.get("thumb_url") or att.get("image_url")
        if thumb_url:
            self._fetch_url_image(thumb_url, thumb)
        return card

    def _on_stop_hosting(self):
        if self._invite_stopper:
            self._invite_stopper()
        # 카드 상태 갱신(종료됨 표시)
        self._rendered_ids = None
        self._render()

    def _fetch_url_image(self, url: str, label: QLabel):
        def work():
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.read()

        def done(data):
            img = QImage()
            if data and img.loadFromData(data):
                pm = QPixmap.fromImage(img).scaled(56, 56, Qt.AspectRatioMode.KeepAspectRatio,
                                                   Qt.TransformationMode.SmoothTransformation)
                label.setPixmap(pm)
        self._run(work, done)

    def _render(self):
        if not self._cur_posts:
            self._chat.set_placeholder("아직 메시지가 없어요. 먼저 인사해보세요!")
            return
        if self._thread_root:
            self._render_thread()
        else:
            self._render_channel()

    def _render_channel(self):
        """메인 타임라인 — 루트 메시지만. 답글 있으면 'N개의 답글' 푸터."""
        self._chat.clear()
        replies_by_root: dict = {}
        for p in self._cur_posts:
            rid = p.get("root_id", "")
            if rid:
                replies_by_root.setdefault(rid, []).append(p)
        for p in self._cur_posts:
            if p.get("root_id"):       # 답글은 메인에 안 보임(스레드 안에서만)
                continue
            bubble = self._make_bubble(p)
            kids = replies_by_root.get(p.get("id", ""), [])
            if kids:
                rid = p.get("id", "")
                bubble.add_thread_footer(len(kids), lambda _=None, r=rid: self._open_thread(r))
            self._chat.add_bubble(bubble)
        self._chat.scroll_bottom()

    def _render_thread(self):
        """스레드 뷰 — 루트 + 그 답글들(채팅 속 채팅)."""
        by_id = {p.get("id", ""): p for p in self._cur_posts}
        root = by_id.get(self._thread_root)
        if root is None:
            self._close_thread(); return
        self._chat.clear()
        self._chat.add_bubble(self._make_bubble(root))
        replies = [p for p in self._cur_posts if p.get("root_id") == self._thread_root]
        self._chat.add_divider(f"{len(replies)}개의 답글")
        for p in replies:
            self._chat.add_bubble(self._make_bubble(p))
        self._chat.scroll_bottom()

    # ---- 스레드 전환 ----------------------------------------------------
    def _open_thread(self, root_id: str):
        self._thread_root = root_id
        self._back_btn.show()
        self._dm_title.setText("← 스레드")
        self._input.setPlaceholderText("스레드에 답글…")
        self._rendered_ids = None
        self._render()
        self._input.setFocus()

    def _close_thread(self):
        self._thread_root = ""
        self._back_btn.hide()
        if self._cur_user:
            self._dm_title.setText(self._cur_user.get("username", "?"))
        self._input.setPlaceholderText("메시지 입력 (이미지는 붙여넣기/드래그)…")
        self._rendered_ids = None
        self._render()

    def _reply_to_post(self, post: dict):
        """↩ : 그 메시지의 스레드를 연다(루트면 자기, 답글이면 그 루트)."""
        self._open_thread(post.get("root_id") or post.get("id", ""))

    def _set_slot_image(self, slot: "_ClickImage", img: QImage):
        slot._full = img
        pm = QPixmap.fromImage(img)
        if pm.width() > _IMG_MAX:
            pm = pm.scaledToWidth(_IMG_MAX, Qt.TransformationMode.SmoothTransformation)
        slot.setPixmap(pm)

    def _fetch_file(self, fid: str):
        if fid in self._file_cache or fid in self._fetching:
            return
        self._fetching.add(fid)
        cli = self._client

        def _store(data):
            self._fetching.discard(fid)
            img = QImage()
            if data and img.loadFromData(data):
                self._file_cache[fid] = img
                slot = self._chat.img_slots.get(fid)
                if slot is not None:
                    self._set_slot_image(slot, img)   # 전체 재렌더 없이 그 이미지만 갱신
        self._run(lambda: cli.get_file(fid), _store)

    def _open_image(self, fid: str):
        img = self._file_cache.get(fid)
        if img is not None:
            _ImageViewer(img, self).show()

    def _image_menu(self, fid: str, slot, pos):
        menu = QMenu(self)
        menu.setStyleSheet("QMenu{background:#2f3136;border:1px solid #444;color:#ddd;}"
                           "QMenu::item{padding:6px 14px;}QMenu::item:selected{background:#40444b;}")
        a_view = menu.addAction("원본 보기")
        a_board = menu.addAction("보드에 추가")
        chosen = menu.exec(slot.mapToGlobal(pos))
        if chosen == a_view:
            self._open_image(fid)
        elif chosen == a_board:
            self._add_to_board(fid)

    def _add_to_board(self, fid: str):
        img = self._file_cache.get(fid)
        if img is None:
            return
        if not self._board_image_adder:
            QMessageBox.information(self, "보드에 추가", "qonvo에서 보드를 먼저 여세요.")
            return
        self._board_image_adder(img)

    # ---- 파일/이미지 전송 ----------------------------------------------
    def _pick_file(self):
        if not self._cur_channel:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "보낼 파일 선택", "",
            "이미지/파일 (*.png *.jpg *.jpeg *.gif *.webp *.pdf *.txt *.zip);;모든 파일 (*.*)")
        if not path:
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
        except Exception as e:
            QMessageBox.warning(self, "전송 실패", str(e)); return
        self._send_attachment(data, os.path.basename(path))

    def _send_attachment(self, data, filename: str):
        if not self._cur_channel or not data:
            return
        caption = self._input.text().strip(); self._input.clear()
        ch = self._cur_channel; cli = self._client

        def work():
            fid = cli.upload_file(ch, filename, data)
            if not fid:
                return False
            cli.create_post(ch, caption, file_ids=[fid])
            return True

        def done(ok):
            if ok:
                self._rendered_ids = None
                self._load_posts()
            else:
                self._dm_title_flash("전송 실패")
        self._run(work, done)

    def _dm_title_flash(self, msg: str):
        QMessageBox.information(self, "파일 전송", msg)

    def _on_send(self):
        msg = self._input.text().strip()
        if not msg or not self._cur_channel:
            return
        self._input.clear(); ch = self._cur_channel
        root = self._thread_root   # 스레드 뷰면 그 루트에 답글, 아니면 일반 메시지
        self._run(lambda: self._client.create_post(ch, msg, root_id=root),
                  lambda _p: (self.__setattr__("_rendered_ids", None), self._load_posts()))

    # ---- 반응 ----------------------------------------------------------
    def _toggle_reaction(self, post_id: str, emoji_name: str):
        """내가 그 이모지 반응을 이미 했으면 제거, 아니면 추가."""
        post = next((p for p in self._cur_posts if p.get("id") == post_id), None)
        mine = False
        if post:
            for r in (post.get("metadata") or {}).get("reactions") or []:
                if r.get("user_id") == self._me_id and r.get("emoji_name") == emoji_name:
                    mine = True; break
        cli = self._client; uid = self._me_id

        def work():
            if mine:
                cli.remove_reaction(uid, post_id, emoji_name)
            else:
                cli.add_reaction(uid, post_id, emoji_name)
            return True
        self._run(work, lambda _r: (self.__setattr__("_rendered_ids", None), self._load_posts()))

    def _open_reaction_picker(self, post: dict, anchor):
        menu = QMenu(self)
        menu.setStyleSheet("QMenu{background:#2f3136;border:1px solid #444;}"
                           "QMenu::item{padding:6px 10px;font-size:16px;}"
                           "QMenu::item:selected{background:#40444b;}")
        acts = {}
        for ch, name in _PICKER:
            acts[menu.addAction(ch)] = name
        chosen = menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))
        if chosen in acts:
            self._toggle_reaction(post.get("id", ""), acts[chosen])

    def _on_invite(self):
        if not self._cur_channel:
            return
        if not self._invite_requester:
            QMessageBox.information(self, "보드 초대", "보드 호스팅을 사용할 수 없습니다.")
            return
        self._invite_btn.setEnabled(False); self._invite_btn.setText("준비 중…")
        # 호스팅 보장(없으면 자동 시작) → 링크 받으면 카드 전송
        self._invite_requester(self._send_invite_card)

    def _send_invite_card(self, link):
        self._invite_btn.setEnabled(True); self._invite_btn.setText("＋ 보드 초대")
        if not link or not self._cur_channel:
            QMessageBox.information(
                self, "보드 초대",
                "보드를 호스팅할 수 없습니다.\n보드를 먼저 저장(Ctrl+S)했는지 확인하세요.")
            return
        ch = self._cur_channel
        board_id = link.split("@", 1)[0]
        # 링크는 숨은 prop(qonvo_invite)에만 — 본문 문구 없이 카드만 보이게.
        props = {
            "qonvo_invite": link,
            "attachments": [{
                "color": "#5865f2",
                "author_name": "qonvo",
                "title": "qonvo 보드 초대",
                "text": f"**{board_id}** 보드에 같이 작업해요!\nqonvo에서 '참여하기'를 누르세요.",
                "thumb_url": "https://placehold.co/120x120/5865f2/ffffff/png?text=qonvo",
            }],
        }
        self._run(lambda: self._client.create_post(ch, "", props=props),
                  lambda _p: (self.__setattr__("_rendered_ids", None), self._load_posts()))

    def closeEvent(self, a0):
        self._poll.stop()
        if self._socket is not None:
            self._socket.stop()
            self._socket.wait(1500)
            self._socket = None
        for job in list(self._jobs):
            job.wait(1200)
        super().closeEvent(a0)
