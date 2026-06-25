"""서버↔보드 브라우저 UI (마인크래프트 멀티플레이 스타일).

- ServerBrowserWidget: 저장된 서버 목록(상태 핑·접속자수) + 추가/수정/삭제/접속 (메인 임베드 화면)
- ServerEntryDialog: 서버 한 개 추가/수정 (이름/주소/포트/wss/인증방식 ID·PW|merri)
- ServerBoardListDialog: 접속한 서버의 보드 목록(노드수) + 새 보드/열기

서버 목록은 settings.json `server_list` 에 저장. 비밀번호는 crypto_utils 로 암호화
(머신 종속). merri 인증은 서버가 지원할 때만 노출되며, 브라우저 OAuth 로 받은
1회용 토큰을 비밀번호 대신 사용한다.
"""
from __future__ import annotations

import base64
import json
import urllib.request
from typing import Optional
from urllib.parse import quote

from PyQt6.QtCore import QThread, Qt, QSize, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QPushButton, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QCheckBox,
    QInputDialog, QMessageBox,
)

from v.theme import Theme
from v.settings import get_setting, set_setting
from .connect_dialog import _INPUT_STYLE, _LABEL_STYLE

try:
    from v.crypto_utils import encrypt_api_key as _enc, decrypt_api_key as _dec
except Exception:  # pragma: no cover
    def _enc(s): return s
    def _dec(s): return s


# ---- 설정 저장 ----------------------------------------------------------
def _load_servers() -> list:
    v = get_setting("server_list", [])
    servers = list(v) if isinstance(v, list) else []
    # 구버전 단일 접속정보(server_connection) → 서버목록으로 1회 이관
    if not servers:
        old = get_setting("server_connection", {})
        if isinstance(old, dict) and old.get("host"):
            servers = [{
                "name": old.get("host", "서버"),
                "host": old.get("host", ""),
                "port": int(old.get("port", 9700)),
                "secure": bool(old.get("secure", False)),
                "username": old.get("username", ""),
                "auth_method": "password",
            }]
            _save_servers(servers)
    return servers


def _save_servers(servers: list) -> None:
    set_setting("server_list", servers)


def _http_base(host: str, port: int, secure: bool) -> str:
    scheme = "https" if (secure or str(host).startswith(("https://", "wss://"))) else "http"
    h = host.split("://", 1)[-1].rstrip("/")
    if ":" in h or scheme == "https":
        return f"{scheme}://{h}" if ":" in h else f"{scheme}://{h}:{port}"
    return f"{scheme}://{h}:{port}"


# ---- 상태 핑 스레드 -----------------------------------------------------
# 정리 중인(종료 대기) 핑 스레드를 위젯 수명과 무관하게 살려두는 키프얼라이브.
# (메인 스레드를 wait() 로 막지 않고도 GC 로 인한 "QThread destroyed while running" 방지)
_LIVE_PINGS: set = set()


def _icon_from_b64(b64: str) -> Optional[QIcon]:
    """data-URI/base64 PNG → QIcon. 실패 시 None."""
    try:
        if not b64:
            return None
        if "," in b64:
            b64 = b64.split(",", 1)[1]
        raw = base64.b64decode(b64)
        pm = QPixmap()
        if not pm.loadFromData(raw) or pm.isNull():
            return None
        return QIcon(pm)
    except Exception:
        return None


class _PingThread(QThread):
    """각 서버의 /health 를 조회해 상태를 보고한다(취소 가능)."""
    # idx, online, name, online_count, merri, icon_b64(마크식 서버 아이콘)
    result = pyqtSignal(int, bool, str, int, bool, str)

    def __init__(self, servers: list):
        super().__init__()
        self._servers = servers
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        for i, s in enumerate(self._servers):
            if self._cancelled:
                return
            online, name, count, merri, icon = False, s.get("name", ""), 0, False, ""
            try:
                base = _http_base(s.get("host", ""), int(s.get("port", 9700)), s.get("secure", False))
                with urllib.request.urlopen(base + "/", timeout=4) as r:
                    data = json.loads(r.read().decode("utf-8"))
                online = True
                name = data.get("name", name)
                count = data.get("online", 0)
                merri = bool(data.get("auth", {}).get("merri", False))
                icon = data.get("icon", "") or ""
            except Exception:
                pass
            if self._cancelled:
                return
            self.result.emit(i, online, name, count, merri, icon)


class ServerBrowserWidget(QWidget):
    """마인크래프트 멀티플레이식 서버 목록 화면 (메인 중앙에 임베드).

    저장된 서버 목록(상태 핑·접속자수) + 추가/수정/삭제 + 접속을 한 화면에서 제공한다.

    시그널:
      connect_requested(dict): 접속할 서버 정보 — host/port/username/password/secure/name/merri
      back_requested():        뒤로가기(welcome 으로 복귀)
    """
    connect_requested = pyqtSignal(dict)
    back_requested = pyqtSignal()

    def __init__(self, parent=None, show_back: bool = True):
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {Theme.BG_PRIMARY};")
        self._servers = _load_servers()
        self._ping: Optional[_PingThread] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(10)

        # 헤더: 뒤로 + 타이틀
        header = QHBoxLayout()
        if show_back:
            back = QPushButton("←  뒤로")
            back.setCursor(Qt.CursorShape.PointingHandCursor)
            back.setStyleSheet(
                "QPushButton { background: transparent; color: #aaa; border: none;"
                " font-size: 14px; padding: 4px 0; text-align: left; }"
                "QPushButton:hover { color: #fff; }")
            back.clicked.connect(self._on_back)
            header.addWidget(back)
            header.addSpacing(16)
        title = QLabel("서버 목록")
        title.setStyleSheet(f"color: {Theme.TEXT_PRIMARY}; font-size: 18px; font-weight: bold;")
        header.addWidget(title)
        header.addStretch()
        layout.addLayout(header)

        # merri 프로필 바 (Steam 프로필 같은 1회 로그인 신원)
        prow = QHBoxLayout()
        self._prof_label = QLabel()
        self._prof_label.setStyleSheet("color:#bbb;font-size:12px;")
        prow.addWidget(self._prof_label)
        prow.addStretch()
        self._prof_btn = QPushButton()
        self._prof_btn.setStyleSheet("padding:4px 12px;font-size:12px;")
        self._prof_btn.clicked.connect(self._on_profile_btn)
        prow.addWidget(self._prof_btn)
        layout.addLayout(prow)
        self._refresh_profile_bar()

        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget {{ background-color: #2d2d2d; color: #ddd; border: 1px solid #444;"
            f" border-radius: 8px; padding: 4px; }}"
            f"QListWidget::item {{ padding: 10px; border-radius: 6px; }}"
            f"QListWidget::item:selected {{ background-color: #0d6efd; }}"
        )
        self._list.setIconSize(QSize(36, 36))   # 마크식 서버 아이콘 썸네일
        self._list.itemDoubleClicked.connect(lambda _i: self._on_connect())
        layout.addWidget(self._list, 1)

        row = QHBoxLayout()
        for text, fn in (("+ 추가", self._on_add), ("수정", self._on_edit),
                         ("삭제", self._on_remove), ("\U0001F504 새로고침", self._on_refresh)):
            b = QPushButton(text)
            b.setStyleSheet("padding: 7px 14px;")
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        self._btn_connect = QPushButton("접속")
        self._btn_connect.setStyleSheet(
            "padding: 7px 22px; background-color: #0d6efd; color: white;"
            " font-weight: bold; border-radius: 6px;")
        self._btn_connect.clicked.connect(self._on_connect)
        row.addWidget(self._btn_connect)
        layout.addLayout(row)

        self._refresh_list()
        self._start_ping()

    # ---- merri 프로필 ---------------------------------------------------
    def _refresh_profile_bar(self):
        from .profile import get_profile
        prof = get_profile()
        if prof:
            self._prof_label.setText(f"merri 프로필:  {prof['username']}")
            self._prof_btn.setText("로그아웃")
        else:
            self._prof_label.setText("merri 프로필:  (로그인 안 됨)")
            self._prof_btn.setText("merri 로그인")

    def _merri_oauth_base(self) -> Optional[str]:
        """merri 인증 서버 중 하나의 HTTP base (OAuth 진입점). 없으면 None.

        merri 여부는 ping(/health)으로 받아온 ``_merri`` 플래그로 판단한다.
        선택 서버 우선, 없으면 _merri 인 첫 서버, 그것도 없으면 선택 서버 그대로 시도.
        """
        cur = self._cur()
        if cur and cur.get("_merri"):
            return _http_base(cur.get("host", ""), int(cur.get("port", 9700)), cur.get("secure", False))
        for s in self._servers:
            if s.get("_merri"):
                return _http_base(s.get("host", ""), int(s.get("port", 9700)), s.get("secure", False))
        if cur:
            return _http_base(cur.get("host", ""), int(cur.get("port", 9700)), cur.get("secure", False))
        return None

    def _on_profile_btn(self):
        from .profile import get_profile, save_profile, clear_profile, login_blocking
        if get_profile():
            if QMessageBox.question(self, "로그아웃", "merri 프로필에서 로그아웃할까요?") == QMessageBox.StandardButton.Yes:
                clear_profile()
                self._refresh_profile_bar()
            return
        base = self._merri_oauth_base()
        if not base:
            QMessageBox.information(self, "merri 로그인",
                                    "merri 인증을 쓰는 서버를 먼저 추가하세요.")
            return
        res = login_blocking(base, self)
        if not res:
            return
        save_profile(res[0], res[1], res[2] if len(res) > 2 else "")
        self._refresh_profile_bar()

    def _refresh_list(self):
        self._list.clear()
        for s in self._servers:
            label = f"⚪  {s.get('name','(이름없음)')}\n      {s.get('host','')}:{s.get('port',9700)}"
            it = QListWidgetItem(label)
            qicon = _icon_from_b64(s.get("_icon", ""))   # 직전 핑에서 받은 아이콘 유지
            if qicon is not None:
                it.setIcon(qicon)
            self._list.addItem(it)
        if self._servers:
            self._list.setCurrentRow(0)

    def _on_refresh(self):
        """서버 목록을 다시 읽고 상태(온라인/접속자수)를 재조회한다."""
        self._servers = _load_servers()
        self._refresh_list()
        self._start_ping()

    def _start_ping(self):
        self._stop_ping()
        if not self._servers:
            return
        self._ping = _PingThread(self._servers)
        self._ping.result.connect(self._on_ping)
        self._ping.start()

    def _on_ping(self, idx, online, name, count, merri, icon):
        if idx >= self._list.count():
            return
        s = self._servers[idx]
        s["_merri"] = merri  # 접속 시 참고
        s["_icon"] = icon    # 마크식 서버 아이콘(다음 새로고침까지 유지)
        item = self._list.item(idx)
        dot = "🟢" if online else "🔴"
        extra = f"  ·  {count}명 접속" if online else "  ·  오프라인"
        item.setText(
            f"{dot}  {s.get('name','')}{extra}\n      {s.get('host','')}:{s.get('port',9700)}")
        qicon = _icon_from_b64(icon) if online else None
        if qicon is not None:
            item.setIcon(qicon)

    def _cur(self) -> Optional[dict]:
        i = self._list.currentRow()
        return self._servers[i] if 0 <= i < len(self._servers) else None

    def _on_add(self):
        dlg = ServerEntryDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_entry:
            self._servers.append(dlg.result_entry)
            _save_servers(self._servers)
            self._refresh_list()
            self._start_ping()

    def _on_edit(self):
        cur = self._cur()
        if not cur:
            return
        dlg = ServerEntryDialog(entry=cur, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_entry:
            self._servers[self._list.currentRow()] = dlg.result_entry
            _save_servers(self._servers)
            self._refresh_list()
            self._start_ping()

    def _on_remove(self):
        cur = self._cur()
        if not cur:
            return
        if QMessageBox.question(self, "삭제", f"'{cur.get('name')}' 삭제할까요?") == QMessageBox.StandardButton.Yes:
            del self._servers[self._list.currentRow()]
            _save_servers(self._servers)
            self._refresh_list()

    def _server_health(self, srv: dict) -> dict:
        """서버 /health 를 동기 조회. 실패 시 빈 dict."""
        try:
            base = _http_base(srv.get("host", ""), int(srv.get("port", 9700)), srv.get("secure", False))
            with urllib.request.urlopen(base + "/", timeout=6) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            return {}

    def _on_connect(self):
        cur = self._cur()
        if not cur:
            return
        # 인증 방식은 서버에서 받아온다(/health). merri 광고 시 merri, 아니면 ID/PW.
        health = self._server_health(cur)
        if not health:
            QMessageBox.warning(self, "접속 실패", "서버에 연결할 수 없습니다.\n주소/포트를 확인하세요.")
            return
        merri = bool(health.get("auth", {}).get("merri", False))
        password = _dec(cur.get("password_enc", "")) if cur.get("password_enc") else ""
        username = cur.get("username", "")

        if merri:
            # 저장된 qonvo 프로필(merri 토큰) 사용. 없으면 1회 로그인 후 저장.
            from .profile import get_profile, save_profile, login_blocking
            prof = get_profile()
            if not prof:
                base = _http_base(cur.get("host", ""), int(cur.get("port", 9700)), cur.get("secure", False))
                res = login_blocking(base, self)
                if not res:
                    return
                save_profile(res[0], res[1], res[2] if len(res) > 2 else "")
                prof = {"username": res[0], "token": res[1]}
                self._refresh_profile_bar()
            username = prof["username"]
            password = prof["token"]
        else:
            # ID/PW 서버: 저장된 자격이 없으면 입력 요청 후 항목에 기억(다음부턴 안 물음).
            newly = False
            if not username:
                username, ok = QInputDialog.getText(self, "로그인", "사용자 이름:")
                if not ok or not username.strip():
                    return
                username = username.strip()
                newly = True
            if not password:
                password, ok = QInputDialog.getText(
                    self, "로그인", "비밀번호:", QLineEdit.EchoMode.Password)
                if not ok:
                    return
                newly = True
            if newly:
                cur["username"] = username
                if password:
                    cur["password_enc"] = _enc(password)
                _save_servers(self._servers)

        info = {
            "host": cur.get("host", ""),
            "port": int(cur.get("port", 9700)),
            "username": username,
            "password": password,
            "secure": bool(cur.get("secure", False)),
            "name": cur.get("name", ""),
            "merri": merri,
        }
        self._stop_ping()
        self.connect_requested.emit(info)

    # ---- 네비게이션 / 정리 ---------------------------------------------
    def _on_back(self):
        self._stop_ping()
        self.back_requested.emit()

    def _stop_ping(self):
        """진행 중 핑을 비차단으로 중단한다(메인 스레드를 wait 로 막지 않음).

        취소 플래그를 세우고 결과 시그널을 끊은 뒤, 스레드가 스스로 끝나면 정리한다.
        끝날 때까지 _LIVE_PINGS 가 참조를 들고 있어 GC 크래시를 막는다."""
        p = self._ping
        self._ping = None
        if p is None:
            return
        try:
            p.cancel()
        except Exception:
            pass
        try:
            p.result.disconnect(self._on_ping)
        except Exception:
            pass
        _LIVE_PINGS.add(p)
        p.finished.connect(lambda: (_LIVE_PINGS.discard(p), p.deleteLater()))
        if p.isFinished():
            _LIVE_PINGS.discard(p)
            p.deleteLater()

    def hideEvent(self, a0):
        # 화면 전환(setCentralWidget)으로 가려질 때 핑 스레드 정리
        self._stop_ping()
        super().hideEvent(a0)


class ServerEntryDialog(QDialog):
    """서버 한 개 추가/수정."""

    def __init__(self, entry: Optional[dict] = None, parent=None):
        super().__init__(parent)
        self._editing = entry is not None
        e = entry or {}
        self._entry = e   # 기존 항목(기억된 username/password_enc 등) 보존용
        self.setWindowTitle("서버 수정" if self._editing else "서버 추가")
        self.setMinimumWidth(420)
        self.setModal(True)
        self.setStyleSheet(f"QDialog {{ background-color: {Theme.BG_PRIMARY}; }}")
        self.result_entry: Optional[dict] = None

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(8)

        self._name = QLineEdit(e.get("name", "")); self._name.setStyleSheet(_INPUT_STYLE)
        self._name.setPlaceholderText("우리집 서버")
        self._host = QLineEdit(e.get("host", "")); self._host.setStyleSheet(_INPUT_STYLE)
        self._host.setPlaceholderText("home.example.com 또는 wss://...")
        self._port = QLineEdit(str(e.get("port", 9700))); self._port.setStyleSheet(_INPUT_STYLE)
        self._secure = QCheckBox("Secure (wss / TLS)"); self._secure.setChecked(bool(e.get("secure", False)))
        self._secure.setStyleSheet("color:#bbb;font-size:12px;")

        for lbl, w in (("이름", self._name), ("주소", self._host), ("포트", self._port),
                       ("", self._secure)):
            l = QLabel(lbl); l.setStyleSheet(_LABEL_STYLE)
            form.addRow(l, w)
        layout.addLayout(form)

        hint = QLabel("로그인(비밀번호 또는 merri)은 접속할 때 서버 방식에 맞춰 물어봅니다.")
        hint.setStyleSheet("color:#888;font-size:11px;"); hint.setWordWrap(True)
        layout.addWidget(hint)

        self._status = QLabel(""); self._status.setStyleSheet("color:#ff6b6b;font-size:11px;")
        layout.addWidget(self._status)

        btns = QHBoxLayout()
        c = QPushButton("취소"); c.clicked.connect(self.reject); btns.addWidget(c)
        c.setAutoDefault(False); c.setDefault(False)   # Enter 가 취소로 가지 않게
        btns.addStretch()
        ok = QPushButton("저장"); ok.setStyleSheet(
            "padding:7px 22px;background-color:#0d6efd;color:white;font-weight:bold;border-radius:6px;")
        ok.clicked.connect(self._on_save); btns.addWidget(ok)
        ok.setDefault(True); ok.setAutoDefault(True)   # Enter = 저장
        layout.addLayout(btns)

    def _on_save(self):
        host = self._host.text().strip()
        if not host:
            self._status.setText("주소를 입력하세요"); return
        try:
            port = int(self._port.text().strip() or "9700")
        except ValueError:
            self._status.setText("포트 번호가 올바르지 않습니다"); return
        entry = {
            "name": self._name.text().strip() or host,
            "host": host,
            "port": port,
            "secure": self._secure.isChecked() or host.lower().startswith(("wss://", "https://")),
        }
        # 이전에 접속하며 기억해 둔 로그인 정보(username/password_enc)는 보존한다.
        for k in ("username", "password_enc"):
            if self._entry.get(k):
                entry[k] = self._entry[k]
        self.result_entry = entry
        self.accept()


class ServerBoardListDialog(QDialog):
    """접속한 서버의 보드 목록 + 새 보드/열기."""

    def __init__(self, client, parent=None, icon_b64: str = ""):
        super().__init__(parent)
        self.setWindowTitle("보드 선택")
        self.setMinimumSize(420, 380)
        self.setModal(True)
        self.setStyleSheet(f"QDialog {{ background-color: {Theme.BG_PRIMARY}; }}")
        self._client = client
        self._icon_b64 = icon_b64 or ""   # 핑에서 이미 받은 아이콘(있으면 네트워크 재조회 안 함)
        self._board_id: Optional[str] = None

        layout = QVBoxLayout(self)
        # 헤더: 서버 아이콘(마크식) + 타이틀
        head = QHBoxLayout()
        icon_pm = self._fetch_server_icon()
        if icon_pm is not None:
            lbl = QLabel()
            lbl.setPixmap(icon_pm.scaled(40, 40, Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation))
            lbl.setFixedSize(40, 40)
            head.addWidget(lbl)
            head.addSpacing(8)
        title = QLabel("보드 선택")
        title.setStyleSheet(f"color: {Theme.TEXT_PRIMARY}; font-size: 16px; font-weight: bold;")
        head.addWidget(title)
        head.addStretch()
        layout.addLayout(head)

        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget {{ background-color:#2d2d2d;color:#ddd;border:1px solid #444;"
            f"border-radius:8px;padding:4px;}}"
            f"QListWidget::item {{ padding:10px;border-radius:6px;}}"
            f"QListWidget::item:selected {{ background-color:#0d6efd;}}")
        self._list.itemDoubleClicked.connect(lambda _i: self._on_open())
        layout.addWidget(self._list, 1)

        row = QHBoxLayout()
        nb = QPushButton("+ 새 보드"); nb.setStyleSheet("padding:7px 14px;")
        nb.clicked.connect(self._on_new); row.addWidget(nb)
        nb.setAutoDefault(False); nb.setDefault(False)
        row.addStretch()
        ob = QPushButton("열기"); ob.setStyleSheet(
            "padding:7px 22px;background-color:#0d6efd;color:white;font-weight:bold;border-radius:6px;")
        ob.clicked.connect(self._on_open); row.addWidget(ob)
        ob.setDefault(True); ob.setAutoDefault(True)   # Enter = 열기
        layout.addLayout(row)

        self._load_boards()

    def _fetch_server_icon(self) -> Optional[QPixmap]:
        """서버 아이콘 QPixmap. 핑에서 받은 b64 가 있으면 그대로 쓰고(네트워크 X),
        없을 때만 /health 를 1회 조회한다. 실패 시 None."""
        try:
            icon = self._icon_b64
            if not icon:   # 폴백: 핑 아이콘이 없을 때만 동기 조회(드묾)
                base = getattr(self._client, "http_base", "")
                if not base:
                    return None
                with urllib.request.urlopen(base + "/", timeout=4) as r:
                    icon = json.loads(r.read().decode("utf-8")).get("icon", "") or ""
            qicon = _icon_from_b64(icon)
            if qicon is None:
                return None
            pm = qicon.pixmap(64, 64)
            return pm if not pm.isNull() else None
        except Exception:
            return None

    def _load_boards(self):
        """서버 /boards 메타로 노드수까지 표시. 실패하면 이름만."""
        boards = []
        try:
            url = f"{self._client.http_base}/boards?t={quote(self._client.http_token)}"
            with urllib.request.urlopen(url, timeout=10) as r:
                boards = json.loads(r.read().decode("utf-8")).get("boards", [])
        except Exception:
            boards = []
        self._list.clear()
        for b in boards:
            nodes = b.get("nodes", 0)
            online = b.get("online", 0)
            dot = "🟢" if online else "⚪"
            self._list.addItem(QListWidgetItem(
                f"📋  {b.get('id')}\n      {nodes} 노드  ·  {dot} {online}명 접속"))
        if self._list.count():
            self._list.setCurrentRow(0)
        self._board_ids = [b.get("id") for b in boards]

    def _on_open(self):
        i = self._list.currentRow()
        if 0 <= i < len(getattr(self, "_board_ids", [])):
            self._board_id = self._board_ids[i]
            self.accept()

    def _on_new(self):
        name, ok = QInputDialog.getText(self, "새 보드", "보드 이름:")
        if ok and name.strip():
            self._board_id = name.strip()
            self.accept()

    def get_board_id(self) -> Optional[str]:
        return self._board_id
