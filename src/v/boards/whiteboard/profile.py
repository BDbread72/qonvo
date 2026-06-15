"""Qonvo merri 프로필 — Steam 프로필 같은 1회 로그인 영구 신원.

merri(Mattermost) OAuth 로 **한 번** 로그인하면 발급된 access token 을
머신 종속 암호화로 settings.json `qonvo_profile` 에 저장한다. 이후 merri 인증
서버에 접속할 때마다 이 토큰을 비밀번호 대신 제시하고, 서버가 chat.idal.cc 에
토큰을 검증해 username 을 확인한다(서버측 `oauth.validate_token`).

로그인 흐름(루프백 OAuth, 붙여넣기 없음):
  1) 로컬 127.0.0.1:<port> 에 1회용 HTTP 서버를 띄운다
  2) 브라우저로 ``{server}/oauth/mattermost/login?redirect=http://127.0.0.1:<port>/cb`` 열기
  3) 서버가 OAuth 완료 후 ``/cb?token=<merri>&user=<name>`` 으로 리다이렉트
  4) 토큰/이름을 받아 프로필로 저장
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional, Tuple
from urllib.parse import urlparse, parse_qs, quote

from PyQt6.QtCore import QThread, pyqtSignal

from v.settings import get_setting, set_setting

try:
    from v.crypto_utils import encrypt_api_key as _enc, decrypt_api_key as _dec
except Exception:  # pragma: no cover
    def _enc(s): return s
    def _dec(s): return s


# ---- 프로필 저장/조회 ---------------------------------------------------
def get_profile() -> Optional[dict]:
    """저장된 프로필 ``{"username", "token", "merri_url"}`` 반환. 없으면 None.

    merri_url 은 토큰을 발급한 Mattermost 주소(People 패널이 직접 API 호출에 사용).
    """
    p = get_setting("qonvo_profile", None)
    if not isinstance(p, dict) or not p.get("token_enc"):
        return None
    try:
        token = _dec(p["token_enc"])
    except Exception:
        return None
    if not token:
        return None
    return {"username": p.get("username", ""), "token": token,
            "merri_url": p.get("merri_url", "")}


def save_profile(username: str, token: str, merri_url: str = "") -> None:
    set_setting("qonvo_profile", {
        "username": username, "token_enc": _enc(token), "merri_url": merri_url})


def clear_profile() -> None:
    set_setting("qonvo_profile", None)


# ---- 루프백 OAuth 로그인 ------------------------------------------------
class _CbHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        q = parse_qs(urlparse(self.path).query)
        self.server.qonvo_result = (  # type: ignore[attr-defined]
            q.get("user", [""])[0], q.get("token", [""])[0], q.get("merri", [""])[0])
        body = (
            "<!doctype html><meta charset=utf-8>"
            "<body style='font-family:sans-serif;background:#1e1e1e;color:#ddd;"
            "display:flex;align-items:center;justify-content:center;height:100vh'>"
            "<div><h2>Qonvo 로그인 완료</h2><p>이 창을 닫고 Qonvo 로 돌아가세요.</p></div>"
            "<script>setTimeout(()=>window.close(),800)</script>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # 콘솔 스팸 방지
        pass


class MerriLoginThread(QThread):
    """루프백 OAuth 로 merri 토큰을 받아오는 백그라운드 스레드.

    ``done(username, token)`` 또는 ``failed(reason)`` 시그널을 emit 한다.
    UI 는 QProgressDialog 등으로 대기하면 된다.
    """
    done = pyqtSignal(str, str, str)   # username, token, merri_url
    failed = pyqtSignal(str)

    def __init__(self, oauth_base: str, timeout: float = 180.0, parent=None):
        super().__init__(parent)
        self._base = oauth_base.rstrip("/")
        self._timeout = timeout
        self._httpd: Optional[HTTPServer] = None

    def cancel(self):
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
            except Exception:
                pass

    def run(self):
        import webbrowser
        try:
            httpd = HTTPServer(("127.0.0.1", 0), _CbHandler)
        except Exception as e:
            self.failed.emit(f"로컬 서버 시작 실패: {e}")
            return
        httpd.qonvo_result = None  # type: ignore[attr-defined]
        httpd.timeout = self._timeout
        self._httpd = httpd
        port = httpd.server_address[1]
        redirect = f"http://127.0.0.1:{port}/cb"
        url = f"{self._base}/oauth/mattermost/login?redirect={quote(redirect)}"
        try:
            webbrowser.open(url)
        except Exception:
            pass
        # 콜백 1건만 처리(타임아웃 시 handle_request 가 그냥 반환)
        try:
            httpd.handle_request()
        except Exception:
            pass
        result = getattr(httpd, "qonvo_result", None)
        try:
            httpd.server_close()
        except Exception:
            pass
        self._httpd = None
        if not result or not result[1]:
            self.failed.emit("로그인이 취소되었거나 시간 초과되었습니다.")
            return
        username, token, merri_url = (list(result) + ["", "", ""])[:3]
        self.done.emit(username, token, merri_url)


def login_blocking(oauth_base: str, parent=None) -> Optional[Tuple[str, str, str]]:
    """모달 진행 대화상자와 함께 merri 로그인. 성공 시 (username, token, merri_url)."""
    from PyQt6.QtWidgets import QProgressDialog
    from PyQt6.QtCore import Qt

    th = MerriLoginThread(oauth_base, parent=parent)
    dlg = QProgressDialog("브라우저에서 merri 로그인을 완료하세요…", "취소", 0, 0, parent)
    dlg.setWindowTitle("merri 로그인")
    dlg.setWindowModality(Qt.WindowModality.WindowModal)
    dlg.setMinimumDuration(0)
    dlg.setAutoClose(False)
    dlg.setAutoReset(False)

    box = {"result": None, "cancelled": False}

    def _done(u, t, murl):
        # 토큰 수신 → 대화상자를 직접 닫는다(accept). reset()은 autoClose=False라 안 닫힘.
        box["result"] = (u, t, murl)
        dlg.accept()

    def _failed(_r):
        dlg.reject()

    def _on_cancel():
        box["cancelled"] = True
        th.cancel()

    th.done.connect(_done)
    th.failed.connect(_failed)
    dlg.canceled.connect(_on_cancel)
    th.start()
    dlg.exec()
    th.wait(2000)
    # 사용자가 취소했으면 늦게 도착한 결과는 무시
    return None if box["cancelled"] else box["result"]
