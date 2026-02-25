"""
검색 바 위젯
화이트보드 뷰 위에 오버레이되는 검색 UI
Ctrl+F로 열고, 텍스트 검색 + 필터 + 순차 이동
"""
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QLineEdit, QPushButton, QCheckBox, QLabel
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPainter, QPen, QBrush, QColor, QKeyEvent

from v.theme import Theme
from q import t


class SearchBarWidget(QWidget):
    """화이트보드 검색 바 오버레이"""

    def __init__(self, view):
        super().__init__(view)
        self._view = view
        self._matches = []        # [node_id, ...]
        self._current_index = -1

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedHeight(40)
        self._build_ui()

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(250)
        self._debounce.timeout.connect(self._do_search)

        self.hide()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        self._input = QLineEdit()
        self._input.setPlaceholderText(t("search.placeholder"))
        self._input.setFixedHeight(28)
        self._input.setStyleSheet("""
            QLineEdit {
                background-color: #2d2d2d;
                color: #ddd;
                border: 1px solid #444;
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 12px;
            }
            QLineEdit:focus { border-color: #0d6efd; }
        """)
        self._input.textChanged.connect(self._on_text_changed)
        layout.addWidget(self._input)

        self._chk_pinned = QCheckBox(t("search.pinned_only"))
        self._chk_pinned.setStyleSheet("QCheckBox { color: #aaa; font-size: 11px; }")
        self._chk_pinned.toggled.connect(self._on_filter_changed)
        layout.addWidget(self._chk_pinned)

        self._chk_images = QCheckBox(t("search.images_only"))
        self._chk_images.setStyleSheet("QCheckBox { color: #aaa; font-size: 11px; }")
        self._chk_images.toggled.connect(self._on_filter_changed)
        layout.addWidget(self._chk_images)

        self._btn_prev = QPushButton("<")
        self._btn_prev.setFixedSize(24, 24)
        self._btn_prev.setStyleSheet(self._nav_btn_style())
        self._btn_prev.clicked.connect(lambda: self._navigate(-1))
        layout.addWidget(self._btn_prev)

        self._counter = QLabel("0/0")
        self._counter.setStyleSheet("color: #888; font-size: 11px; min-width: 40px;")
        self._counter.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._counter)

        self._btn_next = QPushButton(">")
        self._btn_next.setFixedSize(24, 24)
        self._btn_next.setStyleSheet(self._nav_btn_style())
        self._btn_next.clicked.connect(lambda: self._navigate(1))
        layout.addWidget(self._btn_next)

        self._btn_close = QPushButton("X")
        self._btn_close.setFixedSize(24, 24)
        self._btn_close.setStyleSheet(self._nav_btn_style())
        self._btn_close.clicked.connect(self.close)
        layout.addWidget(self._btn_close)

    @staticmethod
    def _nav_btn_style():
        return """
            QPushButton {
                background-color: #333; color: #aaa;
                border: 1px solid #444; border-radius: 4px;
                font-size: 12px; font-weight: bold;
            }
            QPushButton:hover { background-color: #444; color: #ddd; }
        """

    # ── 페인팅 ──

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(58, 58, 58, 200), 1))
        p.setBrush(QBrush(QColor(26, 26, 26, 220)))
        p.drawRoundedRect(0, 0, self.width() - 1, self.height() - 1, 6, 6)
        p.end()

    # ── 위치 ──

    def reposition(self):
        w = min(560, self._view.width() - 24)
        self.setFixedWidth(w)
        x = (self._view.width() - w) // 2
        self.move(max(0, x), 12)
        self.raise_()

    # ── 열기/닫기 ──

    def open(self):
        self.reposition()
        self.show()
        self.raise_()
        self._input.setFocus()
        self._input.selectAll()

    def close(self):
        self._restore_all_opacity()
        self._matches = []
        self._current_index = -1
        self._input.clear()
        self._chk_pinned.setChecked(False)
        self._chk_images.setChecked(False)
        self._counter.setText("0/0")
        self.hide()

    # ── 검색 ──

    def _on_text_changed(self, text):
        self._debounce.start()

    def _on_filter_changed(self):
        self._do_search()

    def _do_search(self):
        plugin = self._view.plugin
        if not plugin:
            return

        query = self._input.text().strip().lower()
        pinned_only = self._chk_pinned.isChecked()
        images_only = self._chk_images.isChecked()

        self._matches = []

        if not query and not pinned_only and not images_only:
            self._restore_all_opacity()
            self._current_index = -1
            self._update_counter()
            return

        nodes = getattr(plugin.app, 'nodes', {})
        for node_id, widget in nodes.items():
            if pinned_only and not getattr(widget, 'pinned', False):
                continue
            if images_only and not getattr(widget, 'ai_image_paths', []):
                continue
            if query:
                haystack = ""
                if getattr(widget, 'user_message', None):
                    haystack += widget.user_message.lower()
                if getattr(widget, 'ai_response', None):
                    haystack += " " + widget.ai_response.lower()
                if query not in haystack:
                    continue
            self._matches.append(node_id)

        self._matches.sort()
        self._current_index = 0 if self._matches else -1

        self._apply_highlighting()
        self._update_counter()

    # ── 하이라이팅 ──

    def _apply_highlighting(self):
        plugin = self._view.plugin
        if not plugin:
            return
        match_set = set(self._matches)
        for node_id, proxy in plugin.proxies.items():
            proxy.setOpacity(1.0 if node_id in match_set else 0.3)

    def _restore_all_opacity(self):
        plugin = self._view.plugin
        if not plugin:
            return
        for proxy in plugin.proxies.values():
            proxy.setOpacity(1.0)

    # ── 네비게이션 ──

    def _navigate(self, direction=1):
        if not self._matches:
            return
        self._current_index = (self._current_index + direction) % len(self._matches)
        node_id = self._matches[self._current_index]

        plugin = self._view.plugin
        if plugin:
            proxy = plugin.proxies.get(node_id)
            if proxy:
                self._view.centerOn(proxy)
        self._update_counter()

    def _update_counter(self):
        if self._matches:
            self._counter.setText(f"{self._current_index + 1}/{len(self._matches)}")
        else:
            self._counter.setText("0/0")

    # ── 키 이벤트 ──

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self._navigate(-1)
            else:
                self._navigate(1)
            event.accept()
        else:
            super().keyPressEvent(event)
