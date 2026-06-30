"""함수 파일 에디터 — CodeMirror(QWebEngine) + 명령 시스템 브리지.

`/function edit [이름]` 또는 설정에서 연다. CodeMirror 5(웹) 를 QWebEngineView 로
띄우고, QWebChannel 로 Python 의 ChatCommandController(자동완성/검증) + cmd_functions
(저장/불러오기) 와 연동한다.

자산: icons/funceditor/{editor.html, editor.js, vendor/*}  (crack.bat 의 --add-data icons 로 번들).
의존성: PyQt6-WebEngine (requirements.txt). 미설치면 open_function_editor 가 None 반환.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Optional

from v.logger import get_logger

from . import cmd_functions as cf

logger = get_logger("qonvo.func_editor")

_RESERVED = {"list", "show", "set", "add", "remove", "edit"}


def assets_dir() -> str:
    """editor.html 이 있는 디렉토리. frozen 은 _MEIPASS/icons, dev 는 repo/icons."""
    if getattr(sys, "frozen", False):
        base = os.path.join(getattr(sys, "_MEIPASS", ""), "icons", "funceditor")
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        base = os.path.join(here, "..", "..", "..", "..", "icons", "funceditor")
    return os.path.abspath(base)


def webengine_available() -> bool:
    try:
        import PyQt6.QtWebEngineWidgets  # noqa: F401
        import PyQt6.QtWebChannel        # noqa: F401
        return True
    except Exception:
        return False


import re


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def build_inline_html(open_name: str = "") -> str:
    """editor.html 의 vendor <link>/<script> 를 파일 내용으로 인라인한 자급자족 HTML.

    QWebEngine 의 file:// 하위자원 로딩이 간헐적으로 실패해 CSS 가 안 붙고(흰 화면)
    CodeMirror 가 크기측정을 못 해 무한확장하던 문제를 근절한다. 모든 자산을 한 문서에
    넣어 외부 요청을 0 으로 만든다(qwebchannel.js 만 qrc — 항상 가능).
    """
    d = assets_dir()
    html = _read(os.path.join(d, "editor.html"))

    def css_repl(m):
        href = m.group(1)
        if href.startswith("http") or "://" in href:
            return m.group(0)
        try:
            return "<style>\n" + _read(os.path.join(d, href)) + "\n</style>"
        except Exception:
            return m.group(0)

    def js_repl(m):
        src = m.group(1)
        if src.startswith("qrc:") or src.startswith("http") or "://" in src:
            return m.group(0)
        try:
            body = _read(os.path.join(d, src))
            body = body.replace("</script>", "<\\/script>")  # 종료태그 오인 방지
            return "<script>\n" + body + "\n</script>"
        except Exception:
            return m.group(0)

    html = re.sub(r'<link[^>]*href="([^"]+\.css)"[^>]*>', css_repl, html)
    html = re.sub(r'<script src="([^"]+)"></script>', js_repl, html)

    inject = "<script>window.__OPEN__=%s;</script>" % json.dumps(open_name or "")
    html = html.replace("</head>", inject + "\n</head>")
    return html


def _build_bridge(controller):
    """QObject 브리지 클래스를 런타임에 만든다(WebChannel import 를 지연시키기 위해)."""
    from PyQt6.QtCore import QObject, pyqtSlot

    class _Bridge(QObject):
        def __init__(self, ctrl):
            super().__init__()
            self._ctrl = ctrl

        @pyqtSlot(result=str)
        def functionNames(self):
            try:
                return json.dumps(cf.list_functions())
            except Exception:
                return "[]"

        @pyqtSlot(str, result=str)
        def loadFunction(self, name):
            return cf.get_function(name) or ""

        @pyqtSlot(str, str, result=str)
        def saveFunction(self, name, body):
            name = (name or "").strip()
            if not name:
                return "이름을 입력하세요"
            if name in _RESERVED:
                return f"'{name}' 은 예약어라 함수명으로 못 씁니다"
            try:
                cf.set_function(name, body or "")
                return ""
            except Exception as e:
                return f"저장 실패: {e}"

        @pyqtSlot(str, result=str)
        def deleteFunction(self, name):
            return "" if cf.delete_function((name or "").strip()) else "함수가 없습니다"

        @pyqtSlot(str, int, result=str)
        def suggest(self, line, col):
            try:
                comps = self._ctrl.suggest(line, col) or []
            except Exception:
                comps = []
            if not comps:
                return json.dumps({"from": col, "to": col, "items": []})
            frm = max(0, min(int(getattr(comps[0], "start", col)), len(line)))
            to = max(frm, min(int(getattr(comps[0], "end", col)), len(line)))
            items = [{"text": c.text, "tooltip": getattr(c, "tooltip", "") or ""}
                     for c in comps]
            return json.dumps({"from": frm, "to": to, "items": items})

        @pyqtSlot(str, result=str)
        def validateLine(self, line):
            try:
                return self._ctrl.validate(line)
            except Exception:
                return ""

        @pyqtSlot(result=str)
        def commandList(self):
            try:
                return json.dumps(sorted(self._ctrl.command_names()))
            except Exception:
                return "[]"

    return _Bridge(controller)


def open_function_editor(parent_window, controller, name: str = ""):
    """함수 에디터 다이얼로그를 띄운다. WebEngine 없으면 None."""
    if not webengine_available():
        logger.warning("PyQt6-WebEngine 미설치 — 함수 에디터를 열 수 없음")
        return None
    from PyQt6.QtCore import QUrl, Qt
    from PyQt6.QtWidgets import QDialog, QVBoxLayout
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebChannel import QWebChannel

    html = os.path.join(assets_dir(), "editor.html")
    if not os.path.exists(html):
        logger.error("editor.html 없음: %s", html)
        return None

    dlg = QDialog(parent_window)
    dlg.setWindowTitle("함수 에디터 — /function")
    logger.debug("[funceditor] open editor (name=%r)", name)
    dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    dlg.resize(880, 600)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(0, 0, 0, 0)

    view = QWebEngineView(dlg)
    lay.addWidget(view)

    # 웹 페이지의 console.*/JS오류를 터미널 로그로 끌어낸다(헤드리스 디버깅 대체).
    from PyQt6.QtWebEngineCore import QWebEnginePage

    class _DebugPage(QWebEnginePage):
        def javaScriptConsoleMessage(self, level, message, line, source):
            # 에디터 페이지의 JS 콘솔/오류를 디버그 로그로만 캡처(평상시 파일 로그 안 더럽힘).
            try:
                logger.debug("[funceditor JS] %s", message)
            except Exception:
                pass

    page = _DebugPage(view)
    # 로드 직전 흰 깜빡임 제거 — 페이지/뷰/다이얼로그 배경을 에디터 다크색으로.
    from PyQt6.QtGui import QColor
    page.setBackgroundColor(QColor("#1e2228"))
    view.setStyleSheet("background:#1e2228;")
    dlg.setStyleSheet("QDialog{background:#1e2228;}")
    view.setPage(page)
    dlg._qonvo_page = page

    bridge = _build_bridge(controller)
    channel = QWebChannel(dlg)
    channel.registerObject("bridge", bridge)
    view.page().setWebChannel(channel)
    # GC 방지: 다이얼로그에 강참조 보관
    dlg._qonvo_bridge = bridge
    dlg._qonvo_channel = channel

    # 자산을 전부 인라인한 HTML 을 setHtml — file:// 하위자원 로딩 실패(흰 화면) 근절.
    # baseUrl 은 funceditor 디렉토리(남은 상대참조가 있다면 그 기준).
    try:
        page_html = build_inline_html(name)
        base = QUrl.fromLocalFile(assets_dir() + os.sep)
        view.setHtml(page_html, base)
    except Exception as e:
        logger.error("인라인 HTML 빌드 실패, 파일 로드로 폴백: %s", e)
        url = QUrl.fromLocalFile(html)
        if name:
            from PyQt6.QtCore import QUrlQuery
            q = QUrlQuery()
            q.addQueryItem("open", name)
            url.setQuery(q)
        view.load(url)

    dlg.show()
    dlg.raise_()
    return dlg
