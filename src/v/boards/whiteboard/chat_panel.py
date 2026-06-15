"""서버 모드 채팅 패널 — 보드 옆 실시간 채팅.

서버가 chat 메시지를 브로드캐스트하므로 UI 만 담당한다.
입력 → send_message 시그널, 수신 → add_message.
"""
from __future__ import annotations

import html

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QTextEdit, QLineEdit

from v.theme import Theme


class ChatPanel(QWidget):
    send_message = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {Theme.BG_SECONDARY};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setStyleSheet(
            "QTextEdit { background-color: #1e1e22; color: #ddd; border: 1px solid #333;"
            " border-radius: 6px; padding: 6px; font-size: 12px; }")
        lay.addWidget(self._log, 1)

        self._input = QLineEdit()
        self._input.setPlaceholderText("메시지 입력 후 Enter…")
        self._input.setStyleSheet(
            "QLineEdit { background-color: #2d2d2d; color: #ddd; border: 1px solid #444;"
            " border-radius: 6px; padding: 8px; font-size: 13px; }"
            "QLineEdit:focus { border-color: #0d6efd; }")
        self._input.returnPressed.connect(self._on_send)
        lay.addWidget(self._input)

    def _on_send(self):
        text = self._input.text().strip()
        if text:
            self.send_message.emit(text)
            self._input.clear()

    def add_message(self, user: str, color: str, text: str, ts: int = 0):
        u = html.escape(user or "?")
        t = html.escape(text or "")
        col = color or "#888"
        self._log.append(
            f'<div style="margin:2px 0;"><b style="color:{col}">{u}</b> '
            f'<span style="color:#ddd">{t}</span></div>')
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def system_message(self, text: str):
        self._log.append(f'<div style="color:#888;font-style:italic;margin:2px 0;">{html.escape(text)}</div>')
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def focus_input(self):
        self._input.setFocus()
