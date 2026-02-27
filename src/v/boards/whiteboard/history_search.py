"""화이트보드 히스토리 검색 다이얼로그.

모든 노드(이미 생성 + 지연 로드 대기)의 대화를 수집해 AND 키워드 검색을 제공한다.
"""
from __future__ import annotations

import html
import os
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel,
    QScrollArea, QFrame, QPushButton, QApplication,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPixmap

from q import t
from v.theme import Theme

if TYPE_CHECKING:
    from .plugin import WhiteBoardPlugin


PREVIEW_LENGTH = 200
PAGE_SIZE = 100


class HistorySearchDialog(QWidget):
    """화이트보드 대화 히스토리를 검색하고 결과를 표시하는 다이얼로그."""

    def __init__(self, plugin: WhiteBoardPlugin, parent=None):
        """다이얼로그를 초기화한다.

        Args:
            plugin: 화이트보드 플러그인 인스턴스.
            parent: 상위 위젯.
        """
        super().__init__(parent, Qt.WindowType.Window)
        self._plugin = plugin
        self._all_entries: List[Dict[str, Any]] = []
        self._filtered: List[Dict[str, Any]] = []
        self._displayed_count = 0
        self._debounce_timer = QTimer()
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(300)
        self._debounce_timer.timeout.connect(self._do_search)

        self._collect_all_history()
        self._setup_ui()

    def _collect_all_history(self):
        entries: List[Dict[str, Any]] = []

        from .chat_node import ChatNodeWidget
        for node_id, proxy in self._plugin.proxies.items():
            widget = proxy.widget()
            if not isinstance(widget, ChatNodeWidget):
                continue
            for run_idx, entry in enumerate(widget._history):
                entries.append(self._flatten_entry(node_id, run_idx, entry, materialized=True))

        pending = self._plugin._lazy_mgr.get_all_pending_data()
        for row in pending.get("nodes", []):
            node_id = row.get("id")
            if node_id is None:
                continue
            for run_idx, entry in enumerate(row.get("_history", [])):
                entries.append(self._flatten_entry(node_id, run_idx, entry, materialized=False))

        self._all_entries = entries

    def _flatten_entry(self, node_id: int, run_index: int, entry: dict, materialized: bool) -> Dict[str, Any]:
        preferred = entry.get("preferred_texts", [])
        response = entry.get("response", "")
        # preferred_texts가 있으면 실제 후보 텍스트를 응답으로 사용
        if preferred:
            response = "\n---\n".join(f"[{i+1}] {pt}" for i, pt in enumerate(preferred) if pt)
        return {
            "node_id": node_id,
            "run_index": run_index,
            "user": entry.get("user", ""),
            "response": response,
            "images": entry.get("images", []),
            "model": entry.get("model", ""),
            "tokens_in": entry.get("tokens_in", 0),
            "tokens_out": entry.get("tokens_out", 0),
            "materialized": materialized,
        }

    def _setup_ui(self):
        self.setWindowTitle(t("history_search.title"))
        self.setMinimumSize(600, 400)
        self.resize(800, 600)
        self.setStyleSheet(f"background-color: {Theme.BG_PRIMARY};")

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        search_row = QHBoxLayout()
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText(t("history_search.placeholder"))
        self._search_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {Theme.BG_INPUT}; color: {Theme.TEXT_PRIMARY};
                border: 1px solid {Theme.NODE_BORDER}; border-radius: 8px;
                padding: 8px 12px; font-size: 13px;
            }}
            QLineEdit:focus {{ border-color: {Theme.ACCENT_PRIMARY}; }}
        """)
        self._search_input.textChanged.connect(self._on_text_changed)
        search_row.addWidget(self._search_input)

        self._count_label = QLabel("")
        self._count_label.setStyleSheet(
            f"color: {Theme.TEXT_TERTIARY}; font-size: 11px; min-width: 100px;"
        )
        self._count_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        search_row.addWidget(self._count_label)
        root.addLayout(search_row)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet(f"""
            QScrollArea {{ background-color: {Theme.BG_PRIMARY}; border: none; }}
        """)
        self._content = QWidget()
        self._content.setStyleSheet("background: transparent;")
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(14)
        self._content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._scroll.setWidget(self._content)
        root.addWidget(self._scroll)

        self._show_more_btn = QPushButton(t("history_search.show_more"))
        self._show_more_btn.setFixedHeight(32)
        self._show_more_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {Theme.BG_HOVER}; color: {Theme.TEXT_PRIMARY};
                border: 1px solid {Theme.NODE_BORDER}; border-radius: 6px;
                font-size: 12px;
            }}
            QPushButton:hover {{ background-color: {Theme.BG_INPUT}; }}
        """)
        self._show_more_btn.clicked.connect(self._show_more)
        self._show_more_btn.hide()
        root.addWidget(self._show_more_btn)

        self._search_input.setFocus()

    def _on_text_changed(self, _text: str):
        self._debounce_timer.start()

    def _do_search(self):
        query = self._search_input.text().strip().lower()
        if not query:
            self._filtered = []
            self._update_count_label()
            self._clear_results()
            return

        keywords = query.split()
        results = []
        for entry in self._all_entries:
            haystack = (entry["user"] + " " + entry["response"]).lower()
            if all(kw in haystack for kw in keywords):
                results.append(entry)

        self._filtered = results
        self._update_count_label()
        self._clear_results()
        self._displayed_count = 0
        self._render_page()

    def _update_count_label(self):
        total = len(self._filtered)
        if total == 0:
            query = self._search_input.text().strip()
            if query:
                self._count_label.setText(t("history_search.no_results"))
            else:
                self._count_label.setText("")
        elif total > PAGE_SIZE:
            self._count_label.setText(
                t("history_search.result_count_limited", count=total, limit=min(self._displayed_count + PAGE_SIZE, total))
            )
        else:
            self._count_label.setText(t("history_search.result_count", count=total))

    def _clear_results(self):
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self._show_more_btn.hide()

    def _render_page(self):
        start = self._displayed_count
        end = min(start + PAGE_SIZE, len(self._filtered))
        keywords = self._search_input.text().strip().lower().split()

        for i in range(start, end):
            card = self._create_card(self._filtered[i], keywords)
            self._content_layout.addWidget(card)

        self._displayed_count = end

        if end < len(self._filtered):
            self._show_more_btn.show()
            self._update_count_label()
        else:
            self._show_more_btn.hide()

    def _show_more(self):
        self._render_page()

    def _create_card(self, entry: Dict[str, Any], keywords: List[str]) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background-color: {Theme.BG_SECONDARY};
                border: 1px solid {Theme.NODE_BORDER};
                border-radius: 8px;
            }}
        """)
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(12, 10, 12, 10)
        fl.setSpacing(6)

        header = QHBoxLayout()

        node_label = QLabel(t("history_search.node_label", node_id=entry["node_id"]))
        node_label.setStyleSheet(
            f"color: {Theme.ACCENT_PRIMARY}; font-weight: bold; font-size: 11px; border: none;"
        )
        header.addWidget(node_label)

        run_label = QLabel(t("history_search.run_label", run=entry["run_index"] + 1))
        run_label.setStyleSheet(
            f"color: {Theme.TEXT_DISABLED}; font-size: 10px; border: none;"
        )
        header.addWidget(run_label)

        if entry["model"]:
            model_label = QLabel(entry["model"])
            model_label.setStyleSheet(
                f"color: {Theme.TEXT_DISABLED}; font-size: 10px; border: none;"
            )
            header.addWidget(model_label)

        header.addStretch()

        tok_in = entry.get("tokens_in", 0)
        tok_out = entry.get("tokens_out", 0)
        if tok_in or tok_out:
            tok_label = QLabel(f"{tok_in:,} / {tok_out:,}")
            tok_label.setStyleSheet(
                f"color: {Theme.TEXT_DISABLED}; font-size: 10px; border: none;"
            )
            header.addWidget(tok_label)

        fl.addLayout(header)

        user_text = entry["user"]
        if user_text:
            user_header = QLabel(t("history_search.user_label"))
            user_header.setStyleSheet(
                f"color: {Theme.TEXT_TERTIARY}; font-size: 10px; font-weight: bold; border: none;"
            )
            fl.addWidget(user_header)

            preview = self._make_preview(user_text, keywords)
            user_label = QLabel(preview)
            user_label.setWordWrap(True)
            user_label.setTextFormat(Qt.TextFormat.RichText)
            user_label.setStyleSheet(
                f"background: transparent; border-left: 3px solid {Theme.ACCENT_PRIMARY}; "
                f"padding: 6px 10px; color: {Theme.TEXT_PRIMARY}; font-size: 11px; "
                f"border-top: none; border-right: none; border-bottom: none; border-radius: 0px;"
            )
            fl.addWidget(user_label)

        response_text = entry["response"]
        if response_text:
            ai_header = QLabel(t("history_search.ai_label"))
            ai_header.setStyleSheet(
                f"color: {Theme.TEXT_TERTIARY}; font-size: 10px; font-weight: bold; border: none;"
            )
            fl.addWidget(ai_header)

            preview = self._make_preview(response_text, keywords)
            resp_label = QLabel(preview)
            resp_label.setWordWrap(True)
            resp_label.setTextFormat(Qt.TextFormat.RichText)
            resp_label.setStyleSheet(
                f"background: transparent; border-left: 3px solid {Theme.TEXT_DISABLED}; "
                f"padding: 6px 10px; color: {Theme.TEXT_SECONDARY}; font-size: 11px; "
                f"border-top: none; border-right: none; border-bottom: none; border-radius: 0px;"
            )
            fl.addWidget(resp_label)

        images = entry.get("images", [])
        if images:
            img_row = QHBoxLayout()
            img_row.setSpacing(6)
            for img_path in images:
                if not os.path.exists(img_path):
                    continue
                pixmap = QPixmap(img_path)
                if pixmap.isNull():
                    continue
                thumb = pixmap.scaled(
                    80, 80,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                img_label = QLabel()
                img_label.setPixmap(thumb)
                img_label.setStyleSheet(
                    f"border: 1px solid {Theme.GRID_LINE}; border-radius: 4px; padding: 2px;"
                )
                img_label.setCursor(Qt.CursorShape.PointingHandCursor)
                img_label.mousePressEvent = lambda e, p=img_path: os.startfile(p)
                img_row.addWidget(img_label)
            img_row.addStretch()
            fl.addLayout(img_row)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        if user_text:
            copy_user_btn = self._make_copy_btn(t("history_search.copy_user"), user_text)
            btn_row.addWidget(copy_user_btn)

        if response_text:
            copy_resp_btn = self._make_copy_btn(t("history_search.copy_response"), response_text)
            btn_row.addWidget(copy_resp_btn)

        nav_btn = QPushButton(t("history_search.navigate"))
        nav_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {Theme.ACCENT_PRIMARY}; "
            f"border: 1px solid {Theme.ACCENT_PRIMARY}; border-radius: 10px; "
            f"font-size: 10px; padding: 3px 10px; font-weight: bold; }}"
            f"QPushButton:hover {{ background-color: {Theme.ACCENT_PRIMARY}; color: white; }}"
        )
        nav_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        nid = entry["node_id"]
        nav_btn.clicked.connect(lambda checked, node_id=nid: self._navigate_to_node(node_id))
        btn_row.addWidget(nav_btn)

        fl.addLayout(btn_row)
        return frame

    def _make_preview(self, text: str, keywords: List[str]) -> str:
        truncated = text[:PREVIEW_LENGTH]
        if len(text) > PREVIEW_LENGTH:
            truncated += "..."
        safe = html.escape(truncated)
        # HTML 이스케이프된 텍스트에 키워드 하이라이트 마크업을 삽입한다.
        for kw in keywords:
            safe_kw = html.escape(kw)
            idx = safe.lower().find(safe_kw.lower())
            while idx != -1:
                original = safe[idx:idx + len(safe_kw)]
                replacement = f"<span style='color:#fff;text-decoration:underline;text-decoration-color:#ffc107;'>{original}</span>"
                safe = safe[:idx] + replacement + safe[idx + len(safe_kw):]
                # 삽입된 마크업을 고려해 다음 검색 위치를 갱신한다.
                idx = safe.find(safe_kw.lower(), idx + len(replacement))
                if idx == -1:
                    break
                actual = safe[idx:idx + len(safe_kw)]
                if actual.lower() == safe_kw.lower():
                    continue
                else:
                    break
        return safe

    def _make_copy_btn(self, label: str, text: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {Theme.TEXT_TERTIARY}; "
            f"border: 1px solid {Theme.GRID_LINE}; border-radius: 10px; font-size: 10px; padding: 3px 10px; }}"
            f"QPushButton:hover {{ background-color: {Theme.BG_HOVER}; color: {Theme.TEXT_PRIMARY}; }}"
        )
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda checked, txt=text, b=btn: self._copy_text(txt, b))
        return btn

    def _copy_text(self, text: str, btn: QPushButton):
        QApplication.clipboard().setText(text)
        original = btn.text()
        btn.setText(t("button.copied"))
        QTimer.singleShot(1500, lambda: btn.setText(original) if btn else None)

    def _navigate_to_node(self, node_id: int):
        proxy = self._plugin.proxies.get(node_id)
        if proxy is None:
            if self._plugin._force_materialize_by_id(node_id):
                proxy = self._plugin.proxies.get(node_id)
        if proxy is not None and self._plugin.view is not None:
            self._plugin.view.centerOn(proxy)
