"""서버 모드 채팅 패널 — 보드 옆 실시간 채팅 + 마인크래프트식 `/명령어`.

평문 입력 → 채팅 브로드캐스트(send_message 시그널).
`/` 로 시작 → 명령 실행(ChatCommandController, vendored mccmd 기반).

입력창은 마크처럼 동작한다:
  - `/` 입력 시 **위로 자동완성 드롭다운**(Tab/↑↓ 탐색, Enter/클릭 적용)
  - 입력창 아래 **사용법 고스트 힌트**(다음에 올 토큰)
"""
from __future__ import annotations

import html
from collections import deque

from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QTimer, QPropertyAnimation
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTextEdit, QLineEdit, QLabel, QFrame,
    QListWidget, QListWidgetItem, QGraphicsOpacityEffect,
)

from v.theme import Theme
from v.logger import get_logger

logger = get_logger("qonvo.chat_panel")


# 마인크래프트식 §색코드 → HTML. §r=리셋(기본색).
_CODE_COLORS = {
    '6': '#e6a23c', '7': '#9aa0aa', '9': '#5b8cff',
    'a': '#7fd88f', 'b': '#5fb6ff', 'c': '#ff6b6b',
    'd': '#e0a0ff', 'e': '#e6c200', 'f': '#ffffff',
}


def format_codes(text: str, base: str = "#cfd3dc") -> str:
    """`§b`/`§e`/`§r` 같은 색코드가 섞인 문자열을 HTML span 으로 변환."""
    parts = (text or "").split("§")
    out = []
    if parts and parts[0]:
        out.append(f"<span style='color:{base}'>{html.escape(parts[0])}</span>")
    cur = base
    for seg in parts[1:]:
        if not seg:
            continue
        code = seg[0].lower()
        rest = seg[1:]
        if code == 'r':
            cur = base
        elif code in _CODE_COLORS:
            cur = _CODE_COLORS[code]
        else:
            rest = seg   # 알 수 없는 코드는 글자 그대로
        if rest:
            out.append(f"<span style='color:{cur}'>{html.escape(rest)}</span>")
    return "".join(out)


class ChatLog(QWidget):
    """채팅/명령 히스토리 — 마인크래프트 채팅 로그 방식(좌하단).

    - **닫힘**: 새 메시지가 오면 잠깐 떴다가 페이드(마우스 투과).
    - **열림(pin)**: Enter로 채팅 입력을 열면 **전체 스크롤백**을 불투명·스크롤 가능하게 표시.

    채팅(사람 말)·명령 결과·시스템 메시지가 모두 한 줄씩 쌓인다(말풍선과 별개의 기록).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._pinned = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._view = QTextEdit(self)
        self._view.setReadOnly(True)
        self._view.setFrameShape(QFrame.Shape.NoFrame)
        # 마인크래프트식: 반투명 검정 배경 + 큰 흰 글씨
        self._view.setStyleSheet(
            "QTextEdit { background: rgba(0,0,0,0.42); color:#f2f3f5;"
            " border:none; border-radius:4px; padding:10px 14px; font-size:15px; }"
            "QScrollBar:vertical { width:9px; background:transparent; }"
            "QScrollBar::handle:vertical { background:rgba(255,255,255,0.28);"
            " border-radius:4px; min-height:24px; }"
            "QScrollBar::add-line, QScrollBar::sub-line { height:0; }")
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        lay.addWidget(self._view)

        self._entries: deque = deque(maxlen=200)   # 전체 스크롤백(열면 다 보임)
        self._transient: list = []                  # 닫힌 뒤 새로 온 것만(페이드 대상)
        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._effect.setOpacity(1.0)
        self._anim = QPropertyAnimation(self._effect, b"opacity", self)
        self._anim.setDuration(700)
        self._anim.finished.connect(self._on_faded)
        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.timeout.connect(self._start_fade)
        self.hide()

    # --- 기록 추가 ---
    def add_chat(self, who: str, text: str, color: str = "#8fd1ff"):
        w = html.escape(who or "?")
        t = html.escape(text or "")
        self._add(f"<b style='color:{color or '#8fd1ff'}'>{w}</b>"
                  f"&nbsp;<span style='color:#dfe3ea'>{t}</span>")

    def add_system(self, text: str, color: str = "#cfd3dc"):
        """§색코드를 해석해 기록(명령 결과/시스템). color = 기본색(§r 리셋 대상)."""
        self._add(format_codes(text, color))

    def _render(self, entries):
        self._view.setHtml(
            "<div style='font-family:Segoe UI,Malgun Gothic,sans-serif;"
            " line-height:150%'>" + "<br>".join(entries) + "</div>")
        self._relayout()          # 박스를 내용 크기에 맞춤(마크처럼)
        self._scroll_bottom()

    def _add(self, line_html: str):
        self._entries.append(line_html)
        if self._pinned:
            # 열려있을 때 = 전체 스크롤백, 불투명 고정
            self._render(list(self._entries))
            self._anim.stop()
            self._hold.stop()
            self._effect.setOpacity(1.0)
            self.show()
            self.raise_()
        else:
            # 닫혀있을 때 = 새로 온 것만 모아서 잠깐 떴다 페이드
            self._transient.append(line_html)
            if len(self._transient) > 6:
                self._transient = self._transient[-6:]
            self._render(self._transient)
            self._flash(6000)

    # --- 표시 모드 ---
    def pin(self):
        """채팅 입력 열림 — 전체 스크롤백 고정 표시(불투명, 스크롤 가능)."""
        self._pinned = True
        self._transient = []
        self._anim.stop()
        self._hold.stop()
        self._effect.setOpacity(1.0)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self._render(list(self._entries))
        self.show()
        self.raise_()

    def unpin(self):
        """채팅 입력 닫힘 — **기존 스크롤백은 즉시 사라짐**(페이드 없음).
        이후 새 메시지만 _add 에서 페이드 처리된다."""
        self._pinned = False
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._transient = []
        self._anim.stop()
        self._hold.stop()
        self.hide()

    def clear(self):
        self._hold.stop()
        self._anim.stop()
        self._entries.clear()
        self._transient = []
        self._view.clear()
        self.hide()

    def _flash(self, linger_ms: int):
        self._anim.stop()
        self._effect.setOpacity(1.0)
        self.show()
        self.raise_()
        self._hold.start(linger_ms)

    def _start_fade(self):
        if self._pinned:
            return
        self._anim.stop()
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.start()

    def _on_faded(self):
        if not self._pinned and self._effect.opacity() <= 0.02:
            self.hide()

    def _scroll_bottom(self):
        sb = self._view.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())

    def _relayout(self):
        """박스를 **내용 크기에 맞춰** 잡고 좌하단(아래에서 위로)에 앵커.

        마인크래프트처럼 — 글자 줄 수/길이만큼만 반투명 박스가 뜬다(빈 큰 사각형 X).
        """
        p = self.parentWidget()
        if p is None:
            return
        pad_h = 18
        # 폭은 **가로 전체(꽉 차게)** — 입력창과 동일. 높이만 내용에 맞춰(빈 사각형 방지).
        cw = max(360, p.width() - 24)
        max_h = int(p.height() * (0.6 if self._pinned else 0.4))

        # 폭부터 적용 → QTextEdit 가 그 폭으로 줄바꿈 레이아웃 → 정확한 높이 측정
        self.resize(cw, max(40, self.height()))
        inner_h = self._view.document().size().height()
        ch = max(44, min(max_h, int(inner_h + pad_h)))
        self.resize(cw, ch)
        # 좌하단 — 입력창(하단, height-52) 바로 위, 박스 바닥 고정 = 위로 자람
        self.move(12, max(8, p.height() - ch - 60))


# ---------------------------------------------------------------------------
# 자동완성 드롭다운 (입력창 위에 뜨는 팝업)
# ---------------------------------------------------------------------------
class _SuggestPopup(QListWidget):
    """포커스를 뺏지 않는 프레임리스 자동완성 목록."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setUniformItemSizes(True)
        # 마인크래프트식: 반투명 검정 + 큰 글씨
        self.setStyleSheet(
            "QListWidget { background-color: rgba(0,0,0,0.62); color: #f0f0f0;"
            " border: 1px solid rgba(255,255,255,0.16); border-radius: 4px; padding: 3px;"
            " font-family: Consolas, monospace; font-size: 14px; outline: none; }"
            "QListWidget::item { padding: 6px 10px; border-radius: 3px; }"
            "QListWidget::item:selected { background-color: rgba(120,170,255,0.55);"
            " color: white; }")


# ---------------------------------------------------------------------------
# 채팅 + 명령 입력창
# ---------------------------------------------------------------------------
class CommandInput(QLineEdit):
    """평문 채팅 / `/`명령 겸용 입력창.

    submit_chat(str)     — 평문 메시지
    submit_command(str)  — '/' 제거된 명령 문자열
    """

    submit_chat = pyqtSignal(str)
    submit_command = pyqtSignal(str)
    escaped = pyqtSignal()       # Esc(팝업 안 떠있을 때) — 입력창 닫기용
    focus_out = pyqtSignal()     # 포커스 상실 — 떠있는 입력창 닫기 판단용

    def __init__(self, parent=None):
        super().__init__(parent)
        self._controller = None
        self._popup = _SuggestPopup(self)
        self._popup.itemClicked.connect(self._on_item_clicked)
        self._completions: list = []
        self._hint_cb = None   # ghost 힌트를 받을 콜백(라벨 갱신)
        # 명령/채팅 히스토리 — ↑/↓ 로 이전 입력 재호출(마크/셸 식).
        self._history: list = []      # 제출한 입력들(가장 오래된 → 최신)
        self._hist_idx = None         # None = 탐색 안 함, int = 탐색 중 위치
        self._hist_draft = ""         # 탐색 시작 전 입력 중이던 텍스트 보관
        self.textChanged.connect(self._refresh_suggest)

    # --- 외부 배선 ---
    def set_controller(self, controller):
        self._controller = controller

    def set_hint_callback(self, cb):
        self._hint_cb = cb

    # --- 명령/채팅 판별 ---
    def _is_command(self) -> bool:
        return self.text().startswith("/")

    def _cmd_text_and_cursor(self):
        """'/' 제거한 명령 문자열과 그 안에서의 커서 위치."""
        t = self.text()
        cmd = t[1:] if t.startswith("/") else t
        cur = max(0, self.cursorPosition() - 1)
        return cmd, min(cur, len(cmd))

    # --- 입력 히스토리 (↑/↓) ---
    def _push_history(self, text: str):
        if not self._history or self._history[-1] != text:
            self._history.append(text)
            if len(self._history) > 100:
                self._history.pop(0)
        self._hist_idx = None
        self._hist_draft = ""

    def _recall(self, text: str):
        """히스토리 항목을 입력창에 넣되 자동완성/힌트는 건드리지 않는다."""
        self.blockSignals(True)
        self.setText(text)
        self.blockSignals(False)
        self.setCursorPosition(len(text))
        self._hide_popup()
        self._set_hint("")

    def _history_prev(self):
        """↑ — 더 오래된 입력으로."""
        if not self._history:
            return
        if self._hist_idx is None:          # 탐색 시작 → 현재 입력 보관
            self._hist_draft = self.text()
            self._hist_idx = len(self._history)
        if self._hist_idx > 0:
            self._hist_idx -= 1
            self._recall(self._history[self._hist_idx])

    def _history_next(self):
        """↓ — 더 최근 입력으로, 끝에 닿으면 보관해둔 입력 복원."""
        if self._hist_idx is None:
            return
        if self._hist_idx < len(self._history) - 1:
            self._hist_idx += 1
            self._recall(self._history[self._hist_idx])
        else:
            self._hist_idx = None
            self._recall(self._hist_draft)
            self._hist_draft = ""

    # --- 자동완성 갱신 ---
    def _refresh_suggest(self):
        # 사용자가 직접 타이핑하면 히스토리 탐색 상태를 벗어난다(_recall 은 blockSignals).
        self._hist_idx = None
        if self._controller is None or not self._is_command():
            self._hide_popup()
            self._set_hint("")
            return
        cmd, cur = self._cmd_text_and_cursor()
        self._completions = list(self._controller.suggest(cmd, cur))
        if self._completions:
            self._popup.clear()
            for c in self._completions:
                label = c.text + (f"   {c.tooltip}" if c.tooltip else "")
                item = QListWidgetItem(label)
                self._popup.addItem(item)
            self._popup.setCurrentRow(0)
            self._show_popup()
            self._set_hint("")   # 팝업이 떠 있으면 고스트 힌트는 숨김(겹침 방지)
        else:
            self._hide_popup()
            ghost = self._controller.ghost(cmd, cur)
            self._set_hint(ghost, cmd)

    def _set_hint(self, ghost: str, typed: str = ""):
        if self._hint_cb is None:
            return
        self._hint_cb(ghost, typed)

    # --- 팝업 표시/숨김 ---
    def _show_popup(self):
        rows = min(9, self._popup.count())
        row_h = 32
        h = rows * row_h + 8
        w = max(self.width(), 260)
        self._popup.resize(w, h)
        gp = self.mapToGlobal(QPoint(0, 0))
        self._popup.move(gp.x(), gp.y() - h - 4)   # 입력창 '위'
        if not self._popup.isVisible():
            self._popup.show()

    def _hide_popup(self):
        if self._popup.isVisible():
            self._popup.hide()

    def _move_sel(self, delta: int):
        if not self._popup.isVisible():
            return
        n = self._popup.count()
        if n == 0:
            return
        row = (self._popup.currentRow() + delta) % n
        self._popup.setCurrentRow(row)

    def _accept_current(self):
        row = self._popup.currentRow()
        if 0 <= row < len(self._completions):
            self._apply_completion(self._completions[row])

    def _on_item_clicked(self, _item):
        self._accept_current()

    def _apply_completion(self, comp):
        # Completion.apply() 는 '/' 제거된 입력 기준 → 다시 '/' 를 붙인다
        new_cmd = comp.apply()
        self.blockSignals(True)
        self.setText("/" + new_cmd)
        self.blockSignals(False)
        self.setCursorPosition(len(self.text()))
        self._refresh_suggest()
        self.setFocus()

    # --- 키 처리 ---
    def keyPressEvent(self, e: QKeyEvent):
        key = e.key()
        popup_on = self._popup.isVisible()

        if key == Qt.Key.Key_Escape:
            if popup_on:
                self._hide_popup()
                e.accept()
                return
            self.escaped.emit()
            e.accept()
            return
        elif key == Qt.Key.Key_Tab:
            if popup_on:
                self._accept_current()
                e.accept()
                return
            if self._is_command():
                e.accept()
                return
        elif key == Qt.Key.Key_Down:
            if popup_on:
                self._move_sel(1)
            else:
                self._history_next()   # 팝업 없으면 ↓ = 최근 입력으로
            e.accept()
            return
        elif key == Qt.Key.Key_Up:
            if popup_on:
                self._move_sel(-1)
            else:
                self._history_prev()   # 팝업 없으면 ↑ = 이전 입력 재호출
            e.accept()
            return
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._submit()
            e.accept()
            return

        super().keyPressEvent(e)

    def _submit(self):
        text = self.text().strip()
        self._hide_popup()
        self._set_hint("")
        if not text:
            return
        self._push_history(text)   # ↑/↓ 재호출용 기록
        self.clear()
        if text.startswith("/"):
            cmd = text[1:].strip()
            if cmd:
                self.submit_command.emit(cmd)
        else:
            self.submit_chat.emit(text)

    def focusOutEvent(self, e):
        self._hide_popup()
        self.focus_out.emit()
        super().focusOutEvent(e)


# ---------------------------------------------------------------------------
# 패널
# ---------------------------------------------------------------------------
class ChatPanel(QWidget):
    send_message = pyqtSignal(str)

    def __init__(self, ui=None, parent=None):
        super().__init__(parent)
        self._ui = ui
        self._controller = None

        self.setStyleSheet(f"background-color: {Theme.BG_SECONDARY};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        # 헤더
        header = QLabel("채팅  ·  /help 로 명령어")
        header.setStyleSheet("color:#888; font-size:11px; padding:2px 2px;")
        lay.addWidget(header)

        # 로그
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setStyleSheet(
            "QTextEdit { background-color: #1e1e22; color: #ddd; border: 1px solid #333;"
            " border-radius: 6px; padding: 6px; font-size: 12px; }")
        lay.addWidget(self._log, 1)

        # 고스트 힌트 (입력창 위)
        self._hint = QLabel("")
        self._hint.setStyleSheet(
            "color:#666; font-family: Consolas, monospace; font-size: 11px;"
            " padding: 0 4px; min-height: 14px;")
        self._hint.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self._hint)

        # 입력
        self._input = CommandInput()
        self._input.setPlaceholderText("메시지 입력 (또는 /명령어)…")
        self._input.setStyleSheet(
            "QLineEdit { background-color: #2d2d2d; color: #ddd; border: 1px solid #444;"
            " border-radius: 6px; padding: 8px; font-size: 13px; }"
            "QLineEdit:focus { border-color: #0d6efd; }")
        self._input.set_hint_callback(self._update_hint)
        self._input.submit_chat.connect(self._on_submit_chat)
        self._input.submit_command.connect(self._on_submit_command)
        lay.addWidget(self._input)

        # ui 가 있으면 명령 컨트롤러 구성
        if ui is not None:
            self._build_controller(ui)

    # --- 컨트롤러 ---
    def _build_controller(self, ui):
        try:
            from v.boards.whiteboard.chat_commands import ChatCommandController
            self._controller = ChatCommandController(ui, panel=self)
            self._input.set_controller(self._controller)
        except Exception as e:
            logger.warning("채팅 명령 컨트롤러 초기화 실패: %s", e)
            self._controller = None

    def set_client(self, client):
        if self._controller is not None:
            self._controller.set_client(client)

    def set_presence(self, users: list):
        if self._controller is not None:
            self._controller.set_presence(users)

    # --- 입력 처리 ---
    def _on_submit_chat(self, text: str):
        self.send_message.emit(text)

    def _on_submit_command(self, cmd: str):
        if self._controller is None:
            self.system_message("명령 시스템이 준비되지 않았습니다.")
            return
        self._append_echo("/" + cmd)
        try:
            result = self._controller.execute(cmd)
        except Exception as e:
            logger.debug("command execute error: %s", e)
            self._append_error(f"명령 오류: {e}")
            return
        if result.failed:
            self._append_error(result.error or "알 수 없는 오류")
        else:
            for line in result.messages:
                self._append_command_output(line)

    def _update_hint(self, ghost: str, typed: str = ""):
        if not ghost:
            self._hint.setText("")
            return
        t = html.escape(typed)
        g = html.escape(ghost)
        self._hint.setText(
            f"<span style='color:#9aa'>/{t}</span>"
            f"<span style='color:#5a5a5a'>{g}</span>")

    # --- 렌더링 헬퍼 ---
    @staticmethod
    def _strip_code(text: str):
        """초간단 §코드: '§e' = 경고(노랑), '§' = 명령출력(연두). 반환 (color, text)."""
        if text.startswith("§e"):
            return "#e6c200", text[2:]
        if text.startswith("§"):
            return "#7fd88f", text[1:]
        return "#cfd3dc", text

    def _append(self, body_html: str):
        self._log.append(body_html)
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _append_echo(self, cmd: str):
        self._append(
            f'<div style="margin:2px 0;color:#7f8aa0;font-family:Consolas,monospace;">'
            f'&gt; {html.escape(cmd)}</div>')

    def _append_command_output(self, text: str):
        col, body = self._strip_code(text)
        self._append(
            f'<div style="margin:1px 0;color:{col};font-family:Consolas,monospace;'
            f'white-space:pre;">{html.escape(body)}</div>')

    def _append_error(self, text: str):
        self._append(
            f'<div style="margin:2px 0;color:#ff6b6b;font-family:Consolas,monospace;">'
            f'⚠ {html.escape(text)}</div>')

    # --- 기존 공개 API (ui.py 호환) ---
    def add_message(self, user: str, color: str, text: str, ts: int = 0):
        u = html.escape(user or "?")
        t = html.escape(text or "")
        col = color or "#888"
        self._append(
            f'<div style="margin:2px 0;"><b style="color:{col}">{u}</b> '
            f'<span style="color:#ddd">{t}</span></div>')

    def system_message(self, text: str):
        self._append(
            f'<div style="color:#888;font-style:italic;margin:2px 0;">'
            f'{html.escape(text)}</div>')

    def clear_log(self):
        self._log.clear()

    def focus_input(self):
        self._input.setFocus()
