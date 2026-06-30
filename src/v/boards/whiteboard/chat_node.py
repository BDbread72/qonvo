"""
Chat node widget.
- Collects user input and displays AI responses
- Supports attachments and image responses
- Multi-run: compose button remains active after completion
- Status display: shows idle/running/done indicator instead of inline response
- Log window: click to view full execution history
"""
import base64
import copy
import gzip
import json
import math
import os
import tempfile
import time
import uuid

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QScrollArea, QFrame, QApplication, QSpinBox, QMenu, QLineEdit,
    QSizePolicy, QGraphicsDropShadowEffect,
)
from PyQt6.QtCore import Qt, QTimer, QPointF, QRectF, pyqtSignal
from PyQt6.QtGui import QPixmap, QPainter, QColor, QPen, QPainterPath, QLinearGradient

from q import t
from v.model_plugin import get_all_models, get_all_model_ids, get_all_model_options
from v.theme import Theme
from .base_node import BaseNode
from .model_picker import ModelSelectorButton
from .options_panel import OptionsPanel
from .widgets import DraggableHeader, ResizeHandle, InputDialog


# 계측 메타 포트 — 단일 진실원. (key, 표시라벨, is_bool).
# M 메뉴가 이 목록으로 체크리스트를 만들고, plugin 이 같은 key 로 포트를 생성한다.
# chat_node._set_meta_port_values 가 같은 key 로 값을 채운다.
META_METRICS = [
    ("success", "Success (bool)", True),
    ("error", "Error 메시지", False),
    ("elapsed", "Elapsed (초)", False),
    ("model_name", "Model", False),
    ("tokens_in", "Tokens in", False),
    ("tokens_out", "Tokens out", False),
    ("tokens_total", "Tokens total", False),
    ("cost", "Cost ($)", False),
    ("runs", "Run count", False),
]
META_METRIC_KEYS = [k for k, _l, _b in META_METRICS]


class _StayOpenMenu(QMenu):
    """체크 항목을 토글해도 닫히지 않는 메뉴(여러 개 연속 선택용)."""

    def mouseReleaseEvent(self, e):
        act = self.activeAction()
        if act is not None and act.isCheckable() and act.isEnabled():
            act.trigger()   # 체크 토글만, 메뉴는 유지
            return
        super().mouseReleaseEvent(e)


class ChatLogWindow(QWidget):

    _PAGE_SIZE = 10

    def __init__(self, node_id, history, parent=None,
                 archived_count=0, on_pack=None, on_unpack=None, on_view_archive=None):
        super().__init__(parent, Qt.WindowType.Window)
        self._node_id = node_id
        self._history = history
        self._archived_count = archived_count
        self._on_pack = on_pack
        self._on_unpack = on_unpack
        self._on_view_archive = on_view_archive
        self._total_pages = max(1, (len(history) + self._PAGE_SIZE - 1) // self._PAGE_SIZE)
        self._current_page = self._total_pages - 1
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle(t("chat.log_title", node_id=self._node_id))
        self.setMinimumSize(600, 400)
        self.resize(700, 500)
        self.setStyleSheet(f"background-color: {Theme.BG_PRIMARY};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        action_btn_style = f"""
            QPushButton {{
                background-color: {Theme.BG_HOVER}; color: {Theme.TEXT_PRIMARY};
                border: 1px solid {Theme.NODE_BORDER}; border-radius: 4px;
                font-size: 10px; padding: 3px 10px;
            }}
            QPushButton:hover {{ background-color: {Theme.ACCENT_PRIMARY}; color: white; }}
        """

        if self._archived_count > 0:
            archive_bar = QHBoxLayout()
            archive_bar.setSpacing(6)
            archive_lbl = QLabel(f"{self._archived_count}entries packed")
            archive_lbl.setStyleSheet(f"color: {Theme.TEXT_SECONDARY}; font-size: 11px;")
            archive_bar.addWidget(archive_lbl)
            archive_bar.addStretch()
            if self._on_view_archive:
                btn_view = QPushButton("View Archive")
                btn_view.setStyleSheet(action_btn_style)
                btn_view.clicked.connect(self._on_view_archive)
                archive_bar.addWidget(btn_view)
            if self._on_unpack:
                btn_unpack = QPushButton("Unpack")
                btn_unpack.setStyleSheet(action_btn_style)
                btn_unpack.clicked.connect(self._on_unpack)
                archive_bar.addWidget(btn_unpack)
            layout.addLayout(archive_bar)

        if len(self._history) > 20 and self._on_pack:
            pack_bar = QHBoxLayout()
            pack_bar.addStretch()
            btn_pack = QPushButton(f"Pack ({len(self._history) - 20} entries)")
            btn_pack.setStyleSheet(action_btn_style)
            btn_pack.clicked.connect(self._on_pack)
            pack_bar.addWidget(btn_pack)
            layout.addLayout(pack_bar)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet(f"""
            QScrollArea {{
                background-color: {Theme.BG_PRIMARY};
                border: none;
            }}
        """)

        self._content = QWidget()
        self._content.setStyleSheet("background: transparent;")
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(16)
        self._content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._scroll.setWidget(self._content)
        layout.addWidget(self._scroll)

        if len(self._history) > self._PAGE_SIZE:
            nav_bar = QHBoxLayout()
            nav_bar.setSpacing(8)

            nav_btn_style = f"""
                QPushButton {{
                    background-color: {Theme.BG_HOVER}; color: {Theme.TEXT_PRIMARY};
                    border: 1px solid {Theme.NODE_BORDER}; border-radius: 6px;
                    font-size: 11px; padding: 4px 12px;
                }}
                QPushButton:hover {{ background-color: {Theme.BG_INPUT}; }}
                QPushButton:disabled {{ color: {Theme.TEXT_DISABLED}; }}
            """

            self._btn_prev = QPushButton("< Prev")
            self._btn_prev.setStyleSheet(nav_btn_style)
            self._btn_prev.clicked.connect(self._prev_page)
            nav_bar.addWidget(self._btn_prev)

            nav_bar.addStretch()

            self._page_label = QLabel()
            self._page_label.setStyleSheet(f"color: {Theme.TEXT_SECONDARY}; font-size: 11px;")
            nav_bar.addWidget(self._page_label)

            nav_bar.addStretch()

            self._btn_next = QPushButton("Next >")
            self._btn_next.setStyleSheet(nav_btn_style)
            self._btn_next.clicked.connect(self._next_page)
            nav_bar.addWidget(self._btn_next)

            layout.addLayout(nav_bar)

        btn_close = QPushButton(t("chat.log_close"))
        btn_close.setFixedHeight(36)
        btn_close.setStyleSheet(f"""
            QPushButton {{
                background-color: {Theme.BG_HOVER}; color: {Theme.TEXT_PRIMARY};
                border: 1px solid {Theme.NODE_BORDER}; border-radius: 8px;
                font-weight: bold; font-size: 12px;
            }}
            QPushButton:hover {{ background-color: {Theme.BG_INPUT}; }}
        """)
        btn_close.clicked.connect(self.close)
        layout.addWidget(btn_close)

        self._render_page(self._current_page)

    def _prev_page(self):
        if self._current_page > 0:
            self._current_page -= 1
            self._render_page(self._current_page)

    def _next_page(self):
        if self._current_page < self._total_pages - 1:
            self._current_page += 1
            self._render_page(self._current_page)

    def _render_page(self, page):
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        if not self._history:
            empty_label = QLabel(t("chat.log_no_history"))
            empty_label.setStyleSheet(
                f"color: {Theme.TEXT_DISABLED}; font-size: 13px; padding: 20px;"
            )
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._content_layout.addWidget(empty_label)
        else:
            start = page * self._PAGE_SIZE
            end = min(start + self._PAGE_SIZE, len(self._history))
            for i in range(start, end):
                entry_frame = self._create_entry_widget(i, self._history[i])
                self._content_layout.addWidget(entry_frame)

        if hasattr(self, '_page_label'):
            self._page_label.setText(f"{page + 1} / {self._total_pages}")
            self._btn_prev.setEnabled(page > 0)
            self._btn_next.setEnabled(page < self._total_pages - 1)

        self._scroll.verticalScrollBar().setValue(
            self._scroll.verticalScrollBar().maximum()
        )

    def _create_entry_widget(self, index, entry):
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
        fl.setSpacing(8)

        # header: run number + model + tokens
        header = QHBoxLayout()
        run_label = QLabel(f"#{index + 1}")
        run_label.setStyleSheet(
            f"color: {Theme.ACCENT_PRIMARY}; font-weight: bold; font-size: 12px; border: none;"
        )
        header.addWidget(run_label)

        model_label = QLabel(entry.get("model", ""))
        model_label.setStyleSheet(
            f"color: {Theme.TEXT_DISABLED}; font-size: 10px; border: none;"
        )
        header.addWidget(model_label)
        header.addStretch()

        tokens_in = entry.get("tokens_in", 0)
        tokens_out = entry.get("tokens_out", 0)
        if tokens_in or tokens_out:
            tok_label = QLabel(f"{tokens_in:,} / {tokens_out:,}")
            tok_label.setStyleSheet(
                f"color: {Theme.TEXT_DISABLED}; font-size: 10px; border: none;"
            )
            header.addWidget(tok_label)

        fl.addLayout(header)

        extra_texts = entry.get("extra_texts", [])
        if extra_texts:
            et_header = QLabel("Extra Inputs")
            et_header.setStyleSheet(
                f"color: {Theme.TEXT_TERTIARY}; font-size: 10px; font-weight: bold; border: none;"
            )
            fl.addWidget(et_header)
            for et in extra_texts:
                et_label = QLabel(et[:200] + ("..." if len(et) > 200 else ""))
                et_label.setWordWrap(True)
                et_label.setStyleSheet(
                    f"background-color: #2a3a2a; border: 1px solid #3a4a3a; border-radius: 6px; "
                    f"padding: 6px 8px; color: {Theme.TEXT_SECONDARY}; font-size: 11px;"
                )
                fl.addWidget(et_label)

        extra_files = entry.get("extra_files", [])
        if extra_files:
            ef_header = QLabel("Extra Files")
            ef_header.setStyleSheet(
                f"color: {Theme.TEXT_TERTIARY}; font-size: 10px; font-weight: bold; border: none;"
            )
            fl.addWidget(ef_header)
            for ef in extra_files:
                ef_label = QLabel(os.path.basename(ef))
                ef_label.setStyleSheet(
                    f"color: {Theme.ACCENT_PRIMARY}; font-size: 10px; border: none; padding: 2px 4px;"
                )
                fl.addWidget(ef_label)

        prompt_entries_list = entry.get("prompt_entries", [])
        if prompt_entries_list:
            pe_header = QLabel("Prompt Nodes")
            pe_header.setStyleSheet(
                f"color: {Theme.TEXT_TERTIARY}; font-size: 10px; font-weight: bold; border: none;"
            )
            fl.addWidget(pe_header)
            for pe in prompt_entries_list:
                pe_text = pe.get("text", "")[:150]
                pe_role = pe.get("role", "system")
                pe_label = QLabel(f"[{pe_role}] {pe_text}")
                pe_label.setWordWrap(True)
                pe_label.setStyleSheet(
                    f"background-color: #2a2a3a; border: 1px solid #3a3a4a; border-radius: 6px; "
                    f"padding: 6px 8px; color: {Theme.TEXT_SECONDARY}; font-size: 11px;"
                )
                fl.addWidget(pe_label)

        user_msg = entry.get("user", "")
        if user_msg:
            user_header = QLabel(t("chat.log_user"))
            user_header.setStyleSheet(
                f"color: {Theme.TEXT_TERTIARY}; font-size: 10px; font-weight: bold; border: none;"
            )
            fl.addWidget(user_header)

            user_label = QLabel(user_msg)
            user_label.setWordWrap(True)
            user_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            user_label.setStyleSheet(
                f"background-color: {Theme.ACCENT_PRIMARY}; border-radius: 8px; "
                f"padding: 8px 10px; color: white; font-size: 12px;"
            )
            fl.addWidget(user_label)

        # AI response
        response = entry.get("response", "")
        if response:
            ai_header = QLabel(t("chat.log_ai"))
            ai_header.setStyleSheet(
                f"color: {Theme.TEXT_TERTIARY}; font-size: 10px; font-weight: bold; border: none;"
            )
            fl.addWidget(ai_header)

            resp_label = QLabel(response)
            resp_label.setWordWrap(True)
            resp_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            resp_label.setStyleSheet(
                f"background-color: {Theme.BG_TERTIARY}; border: 1px solid {Theme.BG_HOVER}; "
                f"border-radius: 8px; padding: 8px 10px; color: {Theme.TEXT_PRIMARY}; font-size: 12px;"
            )
            fl.addWidget(resp_label)

            # copy button
            copy_btn = QPushButton(t("button.copy"))
            copy_btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {Theme.TEXT_TERTIARY}; "
                f"border: none; font-size: 10px; padding: 2px 4px; }}"
                f"QPushButton:hover {{ color: #aaa; }}"
            )
            copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            resp_text = response
            copy_btn.clicked.connect(lambda checked, txt=resp_text, btn=copy_btn: self._copy_text(txt, btn))
            fl.addWidget(copy_btn, alignment=Qt.AlignmentFlag.AlignRight)

        # images
        images = entry.get("images", [])
        if images:
            for img_path in images:
                resolved = img_path
                if not os.path.exists(img_path) and ChatNodeWidget._board_temp_dir:
                    _td = ChatNodeWidget._board_temp_dir
                    for _sub in ['attachments', '']:
                        _c = os.path.join(_td, _sub, os.path.basename(img_path)) if _sub else os.path.join(_td, os.path.basename(img_path))
                        if os.path.exists(_c):
                            resolved = _c
                            break
                if os.path.exists(resolved):
                    pixmap = QPixmap(resolved)
                    if not pixmap.isNull():
                        display = pixmap.scaled(
                            200, 200,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                        img_label = QLabel()
                        img_label.setPixmap(display)
                        img_label.setStyleSheet("border: none;")
                        img_label.setCursor(Qt.CursorShape.PointingHandCursor)
                        img_label.mousePressEvent = lambda e, p=img_path: self._open_file(p)
                        fl.addWidget(img_label)

        candidates = entry.get("preferred_candidates", [])
        if not candidates:
            old_texts = entry.get("preferred_texts", [])
            if old_texts:
                candidates = [{"text": t_, "images": []} for t_ in old_texts]
        if candidates:
            pref_header = QLabel(t("chat.preferred_candidates", count=len(candidates)))
            pref_header.setStyleSheet(
                f"color: {Theme.ACCENT_PRIMARY}; font-size: 10px; font-weight: bold; border: none;"
            )
            fl.addWidget(pref_header)

            for i, cand in enumerate(candidates):
                ptext = cand.get("text", "") if isinstance(cand, dict) else str(cand)
                cand_images = cand.get("images", []) if isinstance(cand, dict) else []
                if not ptext and not cand_images:
                    continue
                num_label = QLabel(f"#{i + 1}")
                num_label.setStyleSheet(
                    f"color: {Theme.TEXT_DISABLED}; font-size: 10px; font-weight: bold; "
                    f"border: none; margin-top: 4px;"
                )
                fl.addWidget(num_label)
                if ptext:
                    cand_label = QLabel(ptext)
                    cand_label.setWordWrap(True)
                    cand_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                    cand_label.setStyleSheet(
                        f"background-color: {Theme.BG_TERTIARY}; border: 1px solid {Theme.BG_HOVER}; "
                        f"border-radius: 6px; padding: 6px 8px; color: {Theme.TEXT_PRIMARY}; font-size: 11px;"
                    )
                    fl.addWidget(cand_label)
                    cp_btn = QPushButton(t("button.copy"))
                    cp_btn.setStyleSheet(
                        f"QPushButton {{ background: transparent; color: {Theme.TEXT_TERTIARY}; "
                        f"border: none; font-size: 10px; padding: 2px 4px; }}"
                        f"QPushButton:hover {{ color: #aaa; }}"
                    )
                    cp_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                    cp_btn.clicked.connect(lambda checked, txt=ptext, btn=cp_btn: self._copy_text(txt, btn))
                    fl.addWidget(cp_btn, alignment=Qt.AlignmentFlag.AlignRight)
                for img_path in cand_images:
                    resolved = img_path
                    if img_path and not os.path.exists(img_path) and ChatNodeWidget._board_temp_dir:
                        _td = ChatNodeWidget._board_temp_dir
                        for _sub in ['attachments', '']:
                            _c = os.path.join(_td, _sub, os.path.basename(img_path)) if _sub else os.path.join(_td, os.path.basename(img_path))
                            if os.path.exists(_c):
                                resolved = _c
                                break
                    if resolved and os.path.exists(resolved):
                        pix = QPixmap(resolved)
                        if not pix.isNull():
                            scaled = pix.scaled(200, 200, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                            img_label = QLabel()
                            img_label.setPixmap(scaled)
                            img_label.setStyleSheet("border: none;")
                            img_label.setCursor(Qt.CursorShape.PointingHandCursor)
                            img_label.mousePressEvent = lambda e, p=img_path: self._open_file(p)
                            fl.addWidget(img_label)

        return frame

    def _copy_text(self, text, btn):
        QApplication.clipboard().setText(text)
        btn.setText(t("button.copied"))
        QTimer.singleShot(1500, lambda: btn.setText(t("button.copy")) if btn else None)

    def _open_file(self, fpath):
        try:
            os.startfile(fpath)
        except Exception:
            pass


class _TypingDots(QWidget):
    """LLM 응답 생성 중 인디케이터 — 3점 파동(자체 QTimer, 위젯 단위 repaint 라 깜빡임 없음)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(16)
        self.setFixedWidth(40)
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._tick)

    def start(self):
        if not self._timer.isActive():
            self._timer.start()
        self.show()

    def stop(self):
        self._timer.stop()
        self.hide()

    def _tick(self):
        self._phase += 0.30
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        base = QColor(Theme.ACCENT_PRIMARY)
        cy = self.height() / 2.0
        for i in range(3):
            w = math.sin(self._phase - i * 0.7) * 0.5 + 0.5  # 0..1
            c = QColor(base)
            c.setAlphaF(0.30 + 0.70 * w)
            p.setBrush(c)
            r = 2.4 + 1.4 * w
            p.drawEllipse(QPointF(6.0 + i * 11.0, cy), r, r)
        p.end()


class _ShimmerBar(QWidget):
    """헤더 아래 진행 셰이머 — 실행 중 좌→우로 흐르는 하이라이트(3px). 자체 타이머."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(3)
        self._offset = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def start(self):
        self._offset = 0.0
        if not self._timer.isActive():
            self._timer.start()
        self.show()

    def stop(self):
        self._timer.stop()
        self.hide()

    def _tick(self):
        self._offset = (self._offset + 0.022) % 1.0
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        w = float(self.width())
        h = self.height()
        p.fillRect(0, 0, int(w), h, QColor(Theme.BG_INPUT))
        band = max(40.0, w * 0.35)
        x = self._offset * (w + band) - band
        grad = QLinearGradient(x, 0, x + band, 0)
        c0 = QColor(Theme.ACCENT_PRIMARY); c0.setAlpha(0)
        c1 = QColor(Theme.ACCENT_PRIMARY); c1.setAlpha(235)
        grad.setColorAt(0.0, c0)
        grad.setColorAt(0.5, c1)
        grad.setColorAt(1.0, c0)
        p.fillRect(QRectF(x, 0, band, h), grad)
        p.end()


class ChatNodeWidget(QWidget, BaseNode):
    """Chat node with model selection and streaming response."""

    TITLE_NAME = "LLM"

    # 보드별 이미지 임시 폴더 (plugin이 설정)
    _board_temp_dir: str | None = None

    def __init__(self, node_id, on_send=None, on_branch=None, on_modified=None):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        # Initialize BaseNode
        self.init_base_node(node_id=node_id, on_modified=on_modified)

        # Chat node specific attributes
        self.on_send = on_send
        self.on_cancel = None
        self.on_add_port = None       # plugin sets: (node, type_str) -> None
        self.on_remove_port = None    # plugin sets: (node, port_name) -> None
        self._running = False
        self._send_queue = []  # 실행 중 들어온 요청 대기 큐
        self.user_message = None
        self.user_files = []
        self.ai_response = None
        self.ai_image_paths = []
        self.thought_signatures = []
        self.model = None
        self.node_options = {}
        self.extra_input_defs = []    # [{"name": "text_1", "type": "text"}, ...]
        self.pinned = False
        self.tokens_in = 0
        self.tokens_out = 0
        self.notify_on_complete = False
        self.preferred_options_enabled = False
        self.preferred_options_count = 3
        self.pending_results = []
        self._on_preferred_selected = None
        self._on_rework = None
        self._pref_input_images = []
        self._pref_window = None
        self._log_window = None
        self.meta_output_ports = {}
        self.meta_selected = set()      # 노출할 메트릭 키 집합(사용자 선택). M 메뉴로 토글.
        self.on_toggle_meta_port = None  # plugin 콜백(node, key, enabled) — 포트 추가/제거
        self._start_time = None

        # 계측(분석용) — 마지막 실행 메트릭. meta 포트 + 분석 노드(④)가 읽는다.
        self._run_count = 0
        self._last_success = False
        self._last_error = ""
        self._last_elapsed = 0.0
        self._last_cost = None

        # Multi-run history
        self._history = []            # [{"user": str, "files": [], "response": str, "images": [], "tokens_in": int, "tokens_out": int, "model": str}, ...]
        self._current_streaming = ""
        self._archive_path = None
        self._archived_count = 0

        # 결과를 면에 안 보여주는 호출 노드 → 세로로 넉넉할 필요 없음. 컨트롤 + 상태만 담을
        # 만큼 컴팩트하게. (예전 트랜스크립트용 400 높이는 하단이 텅 비는 '민머리'가 됐음)
        self.setMinimumSize(260, 210)
        self.resize(300, 250)
        self.setStyleSheet(
            f"""
            ChatNodeWidget {{
                background-color: {Theme.BG_TERTIARY};
                border: 3px solid {Theme.NODE_BORDER};
                border-radius: 12px;
            }}
            """
        )

        self._setup_ui()

    # Backward compatibility property
    @property
    def sent(self):
        return self._running

    @sent.setter
    def sent(self, value):
        self._running = value

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(0)

        # header
        self.header = DraggableHeader(self)
        self.header.setFixedHeight(36)
        self.header.setStyleSheet(
            f"""
            DraggableHeader {{
                background-color: {Theme.NODE_HEADER};
                border-top-left-radius: 9px;
                border-top-right-radius: 9px;
                border-bottom: 1px solid {Theme.BG_HOVER};
            }}
            """
        )
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(12, 0, 8, 0)

        # 노드 이름은 공용 편집형 이름표(node_title.NodeTitleItem)가 노드 위에 표시한다.
        header_layout.addStretch()

        self.btn_pin = QPushButton("P")
        self.btn_pin.setFixedSize(28, 28)
        self.btn_pin.setCheckable(True)
        self.btn_pin.setStyleSheet(
            f"""
            QPushButton {{ background: transparent; border: none; font-size: 14px; border-radius: 4px; opacity: 0.4; }}
            QPushButton:checked {{ background-color: #3a3a1e; }}
            QPushButton:hover {{ background-color: {Theme.BG_HOVER}; }}
            """
        )
        self.btn_pin.setToolTip(t("tooltip.pin"))
        self.btn_pin.toggled.connect(self._toggle_pin)
        header_layout.addWidget(self.btn_pin)

        self.btn_notify = QPushButton("N")
        self.btn_notify.setFixedSize(28, 28)
        self.btn_notify.setCheckable(True)
        self.btn_notify.setStyleSheet(
            f"""
            QPushButton {{ background: transparent; border: none; font-size: 14px; border-radius: 4px; }}
            QPushButton:checked {{ background-color: #1e3a1e; color: {Theme.ACCENT_SUCCESS}; }}
            QPushButton:hover {{ background-color: {Theme.BG_HOVER}; }}
            """
        )
        self.btn_notify.setToolTip(t("tooltip.notify_on_complete"))
        self.btn_notify.toggled.connect(lambda c: setattr(self, 'notify_on_complete', c))
        header_layout.addWidget(self.btn_notify)

        layout.addWidget(self.header)

        # 진행 셰이머 — 실행 중 헤더 아래로 좌→우 하이라이트가 흐른다(작동 중 시각 효과).
        self._shimmer = _ShimmerBar()
        layout.addWidget(self._shimmer)

        # model bar
        model_bar = QFrame()
        model_bar.setStyleSheet(f"background-color: {Theme.BG_INPUT}; border: none;")
        model_layout = QHBoxLayout(model_bar)
        model_layout.setContentsMargins(12, 7, 12, 7)

        model_label = QLabel(t("label.model"))
        model_label.setStyleSheet(f"color: {Theme.TEXT_TERTIARY}; font-size: 11px;")
        model_layout.addWidget(model_label)

        # 모델 선택 — 명령 팔레트(검색 중심) 피커. 헤더엔 현재 모델만, 클릭하면 검색 팝업.
        self.model_combo = ModelSelectorButton()
        _all_models = get_all_models()
        _all_model_ids = get_all_model_ids()
        for model_id in _all_model_ids:
            self.model_combo.addItem(_all_models[model_id], model_id)
        try:
            from v.settings import get_default_model
            default_model = get_default_model()
            if default_model and default_model in _all_model_ids:
                self.model_combo.setCurrentIndex(_all_model_ids.index(default_model))
        except Exception:
            pass
        model_layout.addWidget(self.model_combo)

        # 생성 옵션(G 값)은 항상 인라인으로 보인다(아래 opts_panel). 토글/팝업 안 씀 —
        # 저장된 값을 노드 면에서 바로 확인/수정할 수 있어야 하기 때문.

        self.btn_meta_toggle = QPushButton("M")
        self.btn_meta_toggle.setFixedSize(24, 24)
        self.btn_meta_toggle.setCheckable(True)
        self.btn_meta_toggle.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: none; font-size: 11px;
                border-radius: 4px; color: {Theme.TEXT_TERTIARY};
            }}
            QPushButton:checked {{ background-color: #2a6; color: white; }}
            QPushButton:hover {{ background-color: {Theme.BG_HOVER}; }}
        """)
        self.btn_meta_toggle.setToolTip("계측 출력 포트 선택 (success/elapsed/tokens/cost ...)")
        self.btn_meta_toggle.clicked.connect(self._open_meta_menu)
        model_layout.addWidget(self.btn_meta_toggle)

        model_layout.addStretch()
        layout.addWidget(model_bar)

        # 생성 옵션 패널 — 스키마 기반(OptionsPanel). 현재 모델의 MODEL_OPTIONS 를 그대로
        # 렌더하므로 thinking_level/budget 같은 옵션도 자동 노출. **항상 인라인 표시**(토글 없음):
        # 저장된 G 값이 노드 면에서 늘 보이고, 옵션 없는 모델이면 숨긴다. 옵션 수가 바뀌면
        # 노드 높이를 콘텐츠에 맞춰 정리(_fit_height) — 빈 공간(민머리)도, 잘림도 없게.
        self.opts_panel = OptionsPanel()
        self.opts_panel.changed.connect(self._on_opts_changed)
        layout.addWidget(self.opts_panel)   # 인라인 — grid sizeHint 가 정확해 _fit_height 가 맞음

        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        self._on_model_changed()

        # input port management bar
        input_bar = QFrame()
        input_bar.setStyleSheet(f"QFrame {{ background-color: {Theme.BG_INPUT}; border: none; }}")
        input_bar_layout = QHBoxLayout(input_bar)
        input_bar_layout.setContentsMargins(12, 7, 12, 7)
        input_bar_layout.setSpacing(6)

        in_label = QLabel("IN")
        in_label.setStyleSheet(f"color: {Theme.TEXT_TERTIARY}; font-size: 10px; font-weight: bold;")
        input_bar_layout.addWidget(in_label)

        self.input_count_label = QLabel("0")
        self.input_count_label.setStyleSheet(f"color: {Theme.TEXT_SECONDARY}; font-size: 10px;")
        input_bar_layout.addWidget(self.input_count_label)

        btn_style = f"""
            QPushButton {{
                background-color: #333; color: {Theme.TEXT_PRIMARY}; border: 1px solid #444;
                border-radius: 4px; font-size: 10px; font-weight: bold;
            }}
            QPushButton:hover {{ border-color: {Theme.ACCENT_PRIMARY}; background-color: {Theme.BG_HOVER}; }}
        """

        btn_add_text = QPushButton("+T")
        btn_add_text.setFixedSize(30, 22)
        btn_add_text.setToolTip("Add text input port")
        btn_add_text.setStyleSheet(btn_style)
        btn_add_text.clicked.connect(lambda: self._request_add_port("text"))
        input_bar_layout.addWidget(btn_add_text)

        btn_add_image = QPushButton("+I")
        btn_add_image.setFixedSize(30, 22)
        btn_add_image.setToolTip("Add image input port")
        btn_add_image.setStyleSheet(btn_style)
        btn_add_image.clicked.connect(lambda: self._request_add_port("image"))
        input_bar_layout.addWidget(btn_add_image)

        self.btn_remove_port = QPushButton("-")
        self.btn_remove_port.setFixedSize(24, 22)
        self.btn_remove_port.setToolTip("Remove last input port")
        self.btn_remove_port.setStyleSheet(btn_style)
        self.btn_remove_port.setEnabled(False)
        self.btn_remove_port.clicked.connect(self._request_remove_port)
        input_bar_layout.addWidget(self.btn_remove_port)

        input_bar_layout.addStretch()
        layout.addWidget(input_bar)

        # Preferred Options bar
        pref_bar = QFrame()
        pref_bar.setStyleSheet(f"QFrame {{ background-color: {Theme.BG_INPUT}; border: none; }}")
        pref_layout = QHBoxLayout(pref_bar)
        pref_layout.setContentsMargins(12, 7, 12, 7)
        pref_layout.setSpacing(6)

        self.btn_pref_toggle = QPushButton("Preferred")
        self.btn_pref_toggle.setFixedHeight(24)
        self.btn_pref_toggle.setCheckable(True)
        self.btn_pref_toggle.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {Theme.TEXT_TERTIARY}; border: 1px solid {Theme.TEXT_DISABLED};
                border-radius: 4px; font-size: 10px; padding: 2px 8px;
            }}
            QPushButton:checked {{
                background-color: {Theme.ACCENT_PRIMARY}; color: white; border: 1px solid {Theme.ACCENT_PRIMARY};
            }}
        """)
        self.btn_pref_toggle.setToolTip(t("tooltip.preferred_options"))
        self.btn_pref_toggle.toggled.connect(self._toggle_preferred)
        pref_layout.addWidget(self.btn_pref_toggle)

        x_label = QLabel("x")
        x_label.setStyleSheet(f"color: {Theme.TEXT_TERTIARY}; font-size: 10px;")
        pref_layout.addWidget(x_label)

        self.pref_count_spin = QSpinBox()
        self.pref_count_spin.setRange(2, 32)
        self.pref_count_spin.setValue(3)
        self.pref_count_spin.setFixedWidth(50)
        self.pref_count_spin.setStyleSheet(f"""
            QSpinBox {{
                background-color: #333; color: {Theme.TEXT_PRIMARY}; border: 1px solid #444;
                border-radius: 4px; padding: 2px; font-size: 10px;
            }}
        """)
        self.pref_count_spin.setEnabled(False)
        self.pref_count_spin.valueChanged.connect(lambda v: setattr(self, 'preferred_options_count', v))
        pref_layout.addWidget(self.pref_count_spin)

        pref_layout.addStretch()
        layout.addWidget(pref_bar)

        # 상태 영역 — 이 노드는 LLM '호출' 노드다. 결과(output)는 노드 면에 표시하지 않고
        # 출력 포트(신호/데이터)로만 내보낸다. 면에는 호출 상태 + 작동 효과만 둔다.
        self._status_area = QFrame()
        self._status_area.setStyleSheet(f"QFrame {{ background-color: {Theme.BG_TERTIARY}; border: none; }}")
        status_layout = QVBoxLayout(self._status_area)
        status_layout.setContentsMargins(10, 8, 10, 8)
        status_layout.setSpacing(6)
        status_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # 상태 콘텐츠를 하나의 중앙 클러스터로 묶는다(위/아래 빈공간 2개 → 균형 1쌍).
        status_layout.addStretch(1)

        self._status_label = QLabel(t("chat.status_idle"))
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setStyleSheet(
            f"color: {Theme.TEXT_DISABLED}; font-size: 15px; font-weight: bold;"
        )
        status_layout.addWidget(self._status_label, alignment=Qt.AlignmentFlag.AlignCenter)

        # 생성 중 인디케이터(3점 파동) — 실행 중에만 표시. 출력 텍스트는 안 보여준다.
        self._gen_dots = _TypingDots()
        self._gen_dots.hide()
        status_layout.addWidget(self._gen_dots, alignment=Qt.AlignmentFlag.AlignCenter)

        self._run_count_label = QLabel("")
        self._run_count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._run_count_label.setStyleSheet(
            f"color: {Theme.TEXT_TERTIARY}; font-size: 11px;"
        )
        self._run_count_label.hide()
        status_layout.addWidget(self._run_count_label)

        self._btn_log = QPushButton(t("chat.view_log"))
        self._btn_log.setFixedHeight(28)
        self._btn_log.setStyleSheet(f"""
            QPushButton {{
                background-color: {Theme.BG_HOVER}; color: {Theme.TEXT_PRIMARY};
                border: 1px solid {Theme.NODE_BORDER}; border-radius: 6px;
                font-size: 11px; padding: 4px 12px;
            }}
            QPushButton:hover {{ background-color: {Theme.BG_INPUT}; border-color: {Theme.ACCENT_PRIMARY}; }}
        """)
        self._btn_log.clicked.connect(self._open_log_window)
        self._btn_log.hide()
        status_layout.addWidget(self._btn_log, alignment=Qt.AlignmentFlag.AlignCenter)

        # Preferred results view button (shown when preferred results are ready)
        self._btn_pref_view = QPushButton("")
        self._btn_pref_view.setFixedHeight(32)
        self._btn_pref_view.setStyleSheet(f"""
            QPushButton {{
                background-color: {Theme.ACCENT_PRIMARY}; color: white;
                border: 2px solid {Theme.ACCENT_HOVER}; border-radius: 7px;
                font-weight: bold; font-size: 12px; padding: 4px 16px;
            }}
            QPushButton:hover {{
                background-color: {Theme.ACCENT_HOVER};
                border-color: white;
            }}
        """)
        self._btn_pref_view.clicked.connect(self._open_preferred_window)
        self._btn_pref_view.hide()
        status_layout.addWidget(self._btn_pref_view, alignment=Qt.AlignmentFlag.AlignCenter)

        status_layout.addStretch(1)

        layout.addWidget(self._status_area, stretch=1)

        # 직접 입력(작성줄) 제거 — 이 노드는 입력 포트/신호로만 트리거되는 LLM 호출 노드다.
        # btn_input/composer_input 은 기존 상태-갱신 코드(setText/setEnabled, text/clear)와의
        # 호환을 위해 숨김 객체로만 유지한다(레이아웃에 추가하지 않으므로 화면엔 안 보임).
        self.composer_input = QLineEdit(self)
        self.composer_input.hide()
        self.btn_input = QPushButton(self)
        self.btn_input.hide()

        self.tokens_label = QLabel("")
        self.tokens_label.setStyleSheet(f"color: {Theme.TEXT_DISABLED}; font-size: 10px; padding: 2px 10px;")
        self.tokens_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.tokens_label.hide()
        layout.addWidget(self.tokens_label)

        self.resize_handle = ResizeHandle(self)
        self.resize_handle.move(self.width() - 16, self.height() - 16)
        self.resize_handle.raise_()

        # 작동 중 글로우(노드 둘레 호흡) — proxy 그림자라 위젯 내부 리페인트 없음 = 깜빡임 없음.
        self._pulse_timer = QTimer()
        self._pulse_timer.setInterval(50)    # ~20fps 부드러운 호흡
        self._pulse_timer.timeout.connect(self._pulse_tick)
        self._pulse_active = False
        self._glow_phase = 0.0
        self._glow_effect = None

    def _update_status(self, state):
        """호출 상태 표시(idle/running/done/error). 결과 텍스트는 표시하지 않음 — 출력 포트로만 나감."""
        count = len(self._history)
        base = "font-size: 15px; font-weight: bold;"
        running = (state == "running")
        if state == "idle":
            self._status_label.setText(t("chat.status_idle"))
            self._status_label.setStyleSheet(f"color: {Theme.TEXT_DISABLED}; {base}")
        elif state == "running":
            self._status_label.setText(t("chat.status_running"))
            self._status_label.setStyleSheet(f"color: {Theme.ACCENT_PRIMARY}; {base}")
        elif state == "done":
            self._status_label.setText(t("chat.status_done"))
            self._status_label.setStyleSheet(f"color: {Theme.ACCENT_SUCCESS}; {base}")
        elif state == "error":
            # 실패를 성공('완료')과 똑같이 보이지 않게 빨간 '오류' 상태로 표시.
            self._status_label.setText("⚠ " + t("chat.status_done"))
            self._status_label.setStyleSheet(f"color: {Theme.ACCENT_DANGER}; {base}")

        # 생성 중 점은 실행 중에만 표시.
        if running:
            self._gen_dots.start()
        else:
            self._gen_dots.stop()

        if count > 0:
            self._run_count_label.setText(t("chat.run_count", count=count))
            self._run_count_label.show()
            self._btn_log.show()
        else:
            self._run_count_label.hide()
            self._btn_log.hide()

        # 실행 기록(회수·로그버튼)이 생기면 상태 영역이 더 필요해진다 → 최소 높이 갱신해
        # 짜부/잘림 방지(현재 높이가 모자라면 자동으로 그만큼 키운다).
        self._apply_content_min()

    def _open_log_window(self):
        if self._log_window is not None:
            self._log_window.raise_()
            self._log_window.activateWindow()
            return
        import weakref
        weak_self = weakref.ref(self)

        def _do_pack():
            s = weak_self()
            if s and s.pack_history():
                if s._log_window:
                    s._log_window.close()
                s._open_log_window()

        def _do_unpack():
            s = weak_self()
            if s and s.unpack_history():
                if s._log_window:
                    s._log_window.close()
                s._open_log_window()

        def _do_view_archive():
            s = weak_self()
            if not s:
                return
            entries = s.load_archive_entries()
            if entries:
                win = ChatLogWindow(s.node_id, entries, parent=None)
                win.setWindowTitle(f"Chat #{s.node_id} - Archive ({len(entries)} entries)")
                win.show()

        self._log_window = ChatLogWindow(
            self.node_id, list(self._history),
            archived_count=self._archived_count,
            on_pack=_do_pack,
            on_unpack=_do_unpack if self._archive_path else None,
            on_view_archive=_do_view_archive if self._archive_path else None,
        )
        self._log_window.destroyed.connect(
            lambda: (lambda ws=weak_self: setattr(ws(), '_log_window', None) if ws() is not None else None)()
        )
        self._log_window.show()

    def _apply_content_min(self):
        """노드 최소 높이를 '콘텐츠가 안 짜부되는 높이'로 고정한다.

        옵션이 항상 인라인이라 막대+옵션+상태가 차지하는 최소 높이가 있다. 이걸 노드의
        minimumHeight 로 박아두면, 리사이즈 핸들로 드래그하든 작은 기본값이든 그 아래로
        못 줄어든다 = 내용이 짜부되지 않는다. 폭은 별도(가로는 위젯이 알아서 줄어듦).
        """
        lay = self.layout()
        if lay is None:
            return
        lay.activate()
        need = lay.minimumSize().height()
        if need > 0 and self.minimumHeight() != need:
            self.setMinimumHeight(need)
            if self.height() < need:
                self.resize(self.width(), need)
                if self.proxy is not None:
                    try:
                        self.proxy.resize(self.width(), need)
                    except Exception:
                        pass

    def _fit_height(self):
        """최소 높이(짜부 방지)를 잡고, 현재 높이가 부족하면 콘텐츠 선호 높이로 키운다.

        옵션 수가 모델마다 달라 필요 높이가 바뀐다 → 모델 변경/최초 표시 때만 한 번 정리
        (빈 공간/잘림 방지). 폭은 유지. 토글로 깜빡 늘었다 줄지 않음.
        """
        lay = self.layout()
        if lay is None:
            return
        self._apply_content_min()
        target = max(self.minimumHeight(), lay.sizeHint().height())
        if self.height() != target:
            self.resize(self.width(), target)
            if self.proxy is not None:
                try:
                    self.proxy.resize(self.width(), target)
                except Exception:
                    pass

    def showEvent(self, e):
        super().showEvent(e)
        # 구성 시점엔 레이아웃이 안 잡혀 sizeHint 가 작게 나온다 → 표시 직후 한 번 정리.
        if not getattr(self, "_did_initial_fit", False):
            self._did_initial_fit = True
            if getattr(self, "_restored_geometry", False):
                # 저장된 크기는 존중하되, 짜부 방지 최소 높이는 항상 건다
                # (옛 보드의 너무 작은 저장 높이는 최소까지 자동으로 올라감).
                QTimer.singleShot(0, self._apply_content_min)
            else:
                QTimer.singleShot(0, self._fit_height)

    def _open_meta_menu(self):
        """M 클릭 → 어떤 계측 포트를 노출할지 고르는 체크리스트 메뉴(여러 개 연속 선택 가능)."""
        menu = _StayOpenMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{ background-color: {Theme.BG_SECONDARY}; color: {Theme.TEXT_PRIMARY};
                     border: 1px solid #444; border-radius: 6px; padding: 4px; }}
            QMenu::item {{ padding: 5px 24px 5px 8px; border-radius: 4px; }}
            QMenu::item:selected {{ background-color: {Theme.ACCENT_PRIMARY}; }}
        """)
        for key, label, _is_bool in META_METRICS:
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(key in self.meta_selected)
            act.toggled.connect(lambda checked, k=key: self._toggle_meta_metric(k, checked))
        from PyQt6.QtGui import QCursor
        menu.exec(QCursor.pos())
        # 버튼 활성 표시는 선택이 하나라도 있으면 on
        self.btn_meta_toggle.setChecked(bool(self.meta_selected))

    def _toggle_meta_metric(self, key, enabled):
        """단일 계측 포트 on/off → plugin 콜백으로 포트 추가/제거 + dirty."""
        if enabled:
            self.meta_selected.add(key)
        else:
            self.meta_selected.discard(key)
        self.btn_meta_toggle.setChecked(bool(self.meta_selected))
        if callable(self.on_toggle_meta_port):
            self.on_toggle_meta_port(self, key, enabled)
        if callable(self.on_modified):
            self.on_modified(self.node_id)

    def _toggle_preferred(self, checked):
        self.preferred_options_enabled = checked
        self.pref_count_spin.setEnabled(checked)

    def show_preferred_results(self, results):
        """Preferred results ready -- update status and enable viewing."""
        if self._pref_window is not None:
            self._pref_window.close()
            self._pref_window = None
        self.pending_results = results
        self._running = False
        self._stop_pulse(ok=True)
        self.btn_input.setText("▶")
        self.btn_input.setEnabled(True)
        self.model_combo.setEnabled(True)
        self._update_status("done")
        if self._send_queue:  # preferred 완료 후 대기 큐 처리
            QTimer.singleShot(0, self._process_queue)
        count = len(results)
        self._btn_pref_view.setText(t("chat.preferred_candidates", count=count))
        self._btn_pref_view.show()

        summary = t("chat.preferred_done", count=count)
        self.ai_response = summary
        if self._history:
            preferred_candidates = []
            for text, images in results:
                preferred_candidates.append({
                    "text": text or "",
                    "images": list(images or []),
                })
            self._history[-1]["preferred_candidates"] = preferred_candidates
            self._history[-1]["preferred_texts"] = [c["text"] for c in preferred_candidates]
            self._history[-1]["response"] = summary
        self._update_inline_response(summary)   # 출력 블록을 요약으로 마감(점 멈춤)
        if self.on_modified:
            self.on_modified()

    def _open_preferred_window(self):
        """Open preferred results selection window."""
        if not self.pending_results:
            return
        if self._pref_window is not None:
            self._pref_window.raise_()
            self._pref_window.activateWindow()
            return

        from .preferred_dialog import PreferredResultsWindow

        self._pref_window = PreferredResultsWindow(
            self.pending_results,
            input_images=self._pref_input_images,
        )
        self._pref_window.selection_confirmed.connect(self._on_pref_confirmed)
        self._pref_window.selection_cancelled.connect(self._on_pref_cancelled)
        if self._on_rework:
            cb = self._on_rework
            self._pref_window.rework_requested.connect(
                lambda _cb=cb: _cb(self)
            )
        self._pref_window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._pref_window.show()

    def _on_pref_confirmed(self, selected_indices):
        """Selection window confirmed signal received."""
        self._pref_window = None
        if not selected_indices:
            self.set_response("", done=True)
            return
        selections = [self.pending_results[i] for i in selected_indices]
        self._apply_preferred_selections(selections)

    def _on_pref_cancelled(self):
        """Selection window closed without selection."""
        self._pref_window = None

    def _apply_preferred_selections(self, selections):
        """Apply selected preferred results."""
        if not selections:
            return

        first_text, first_images = selections[0]
        self.ai_response = first_text

        if first_images:
            self.set_image_response(first_text, first_images, [])
        else:
            self.set_response(first_text, done=True)

        if self._on_preferred_selected:
            self._on_preferred_selected(self, selections)

    def _request_add_port(self, port_type):
        if self.on_add_port:
            self.on_add_port(self, port_type)

    def _request_remove_port(self):
        if self.on_remove_port and self.extra_input_defs:
            last = self.extra_input_defs[-1]
            self.on_remove_port(self, last["name"])

    def _update_input_count(self):
        count = len(self.extra_input_defs)
        self.input_count_label.setText(str(count))
        self.btn_remove_port.setEnabled(count > 0)

    def _collect_all_inputs(self):
        texts = []
        files = []
        prompt_entries = []
        # 메인 입력 포트("이전 대화" = self.input_port)도 함께 스캔한다.
        # 예전엔 self.input_ports(추가 포트 dict)만 봐서, 메인 포트에 물린
        # 프롬프트 노드가 prompt_entries 로 분류되지 못하고 시스템 프롬프트가
        # 통째로 누락되거나 메시지로 새던 버그가 있었음.
        scan_ports = []
        main_port = getattr(self, 'input_port', None)
        if main_port is not None:
            scan_ports.append((main_port, True))
        scan_ports.extend((p, False) for p in self.input_ports.values())

        for port, is_main in scan_ports:
            if not port.edges:
                continue
            is_image = port.port_data_type == port.TYPE_FILE
            for edge in port.edges:
                source_port = edge.source_port
                source_proxy = source_port.parent_proxy
                if not source_proxy:
                    continue
                source_node = source_proxy.widget() if hasattr(source_proxy, 'widget') else source_proxy

                # 실행 중인 소스(채팅/함수 등)의 출력은 직전 결과(stale)이거나 미완성이다.
                # 그대로 읽으면 '다른 주제의 이전 응답'이 새 입력에 섞여 들어간다.
                # 프롬프트/텍스트/이미지 같은 정적 노드는 _running 이 없어 영향 없음.
                if getattr(source_node, '_running', False):
                    continue

                if is_image:
                    # 파일 노드(여러 파일을 담는 노드)는 전체 목록을 첨부로 확장
                    get_files = getattr(source_node, 'get_resolved_paths', None)
                    if callable(get_files):
                        multi = get_files()
                        if multi:
                            files.extend(multi)
                            continue
                    path = None
                    if hasattr(source_port, 'port_value') and source_port.port_value is not None:
                        path = str(source_port.port_value)
                    elif hasattr(source_node, 'image_path') and source_node.image_path:
                        path = source_node.image_path
                    elif hasattr(source_node, 'ai_response') and source_node.ai_response:
                        path = source_node.ai_response
                    if path:
                        files.append(path)
                else:
                    text = None
                    if hasattr(source_port, 'port_value') and source_port.port_value is not None:
                        text = str(source_port.port_value)
                    elif hasattr(source_node, 'ai_response') and source_node.ai_response:
                        text = source_node.ai_response
                    elif hasattr(source_node, 'text_content') and source_node.text_content:
                        text = source_node.text_content
                    elif hasattr(source_node, 'body_edit') and hasattr(source_node.body_edit, 'toPlainText'):
                        text = source_node.body_edit.toPlainText()
                    if text:
                        if getattr(source_node, 'is_prompt_node', False):
                            if not getattr(source_node, 'prompt_enabled', True):
                                continue
                            prompt_entries.append({
                                "text": text,
                                "role": getattr(source_node, 'prompt_role', 'system'),
                                "priority": getattr(source_node, 'prompt_priority_value', 0),
                            })
                        elif is_main:
                            # 메인 포트의 비프롬프트 텍스트(이전 대화 컨텍스트)는
                            # 메시지 경로(_collect_input_data → on_signal_input)가
                            # 이미 처리하므로 중복 방지로 여기선 건너뛴다.
                            continue
                        else:
                            texts.append(text)
        return texts, files, prompt_entries

    def _collect_input_data(self):
        """메인 포트 소스가 프롬프트 노드면 '메시지'로 잡지 않는다.

        메인 포트("이전 대화")에 프롬프트 노드를 물리면 그것은 채팅 메시지가
        아니라 시스템/유저 프롬프트다. _collect_all_inputs 가 prompt_entries 로
        처리하므로, 여기서 그 텍스트를 메시지로 끌어오면 시스템 프롬프트가
        user 메시지로 새어든다(=#1 의 'Response with MarkDown' 누수 버그).
        """
        port = getattr(self, 'input_port', None)
        if port is not None and port.edges:
            src_proxy = port.edges[0].source_port.parent_proxy
            if src_proxy is not None:
                src = src_proxy.widget() if hasattr(src_proxy, 'widget') else src_proxy
                if getattr(src, 'is_prompt_node', False):
                    return None
        return super()._collect_input_data()

    def on_signal_input(self, input_data=None):
        context = input_data or self._collect_input_data() or ""
        self._send(context, [])  # 큐잉 여부는 _send 내부에서 판단

    def _on_model_changed(self):
        """모델 변경 시 옵션 패널을 그 모델의 스키마로 재구성한다(기본값 적용)."""
        model_id = self.model_combo.currentData()
        opts = get_all_model_options().get(model_id, {})
        self.opts_panel.set_schema(opts)
        # 모델이 바뀌면 옵션 기본값으로 node_options 갱신(저장 대상 항상 최신 유지).
        self.node_options = self.opts_panel.values()
        # 옵션 있는 모델만 인라인 패널을 보인다(없으면 숨겨 빈 영역 제거) + 높이 정리.
        self.opts_panel.setVisible(self.opts_panel.has_options())
        self._fit_height()
        if callable(self.on_modified):
            self.on_modified(self.node_id)

    def _on_opts_changed(self):
        """옵션 값 변경 → node_options 즉시 갱신(전송 안 해도 저장됨) + 보드 dirty."""
        self.node_options = self.opts_panel.values()
        if callable(self.on_modified):
            self.on_modified(self.node_id)

    def _open_input(self):
        if self._running:
            self._request_cancel()
            return

        def on_submit(result):
            if result:
                text, files = result
                self._send(text, files)

        dialog = InputDialog(self.window(), t("dialog.node_input_title", node_id=self.node_id), on_submit)
        dialog.raise_()
        dialog.activateWindow()
        dialog.exec()

    def _on_composer_submit(self):
        """인라인 작성줄에서 Enter — 텍스트 전송."""
        text = self.composer_input.text().strip()
        if not text:
            return
        self.composer_input.clear()
        self._send(text, [])

    def _on_send_button(self):
        """보내기 버튼 — 실행 중이면 중지, 입력칸에 글이 있으면 전송, 없으면 모달."""
        if self._running:
            self._request_cancel()
            return
        if self.composer_input.text().strip():
            self._on_composer_submit()
        else:
            self._open_input()

    def _request_cancel(self):
        self._send_queue.clear()
        if self.on_cancel:
            self.on_cancel(self.node_id)

    def send(self, message, files=None):
        self._send(message, files or [])

    def _collect_node_options(self, model=None):
        """현재 옵션 패널의 값(스키마 기반)을 그대로 반환. provider 로 흐를 node_options."""
        return self.opts_panel.values()

    def _clear_pending_output(self):
        """새 실행을 시작할 때 직전 결과를 비운다.

        ai_response 는 완료 시점에만 갱신되므로, 비우지 않으면 재실행이 도는 동안에도
        이 노드는 '직전 응답(=다른 주제일 수 있음)'을 출력으로 계속 들고 있게 된다.
        그 사이 다운스트림이 _collect_all_inputs 로 이 노드를 읽으면 stale 출력이
        새 입력과 섞여 들어간다(='이전 내용이 보이는' 버그). 시작 시 비워서 차단.
        """
        self.ai_response = None
        op = getattr(self, 'output_port', None)
        if op is not None and getattr(op, 'port_value', None) is not None:
            op.port_value = None

    def _send(self, msg, files):
        if self._running:
            model = self.model_combo.currentData()
            node_options = self._collect_node_options(model)
            extra_texts, extra_files, prompt_entries = self._collect_all_inputs()
            send_msg = msg
            if extra_texts:
                context = "\n\n---\n\n".join(extra_texts)
                send_msg = f"{context}\n\n---\n\n{msg}" if msg else context
            send_files = list(files) + extra_files
            # 빈 입력은 큐에도 넣지 않는다(빈 채팅 실행/체인 방지).
            if not (send_msg and send_msg.strip()) and not send_files and not prompt_entries:
                return
            self._send_queue.append({
                "msg": msg, "files": list(files),
                "model": model, "node_options": node_options,
                "send_msg": send_msg, "send_files": send_files,
                "prompt_entries": prompt_entries,
                "extra_texts": extra_texts,
                "extra_files": extra_files,
            })
            return

        self._running = True
        self._start_time = time.time()
        self.user_message = msg
        self.user_files = files
        self.model = self.model_combo.currentData()

        self.node_options = self._collect_node_options(self.model)

        extra_texts, extra_files, prompt_entries = self._collect_all_inputs()
        send_msg = msg
        if extra_texts:
            context = "\n\n---\n\n".join(extra_texts)
            send_msg = f"{context}\n\n---\n\n{msg}" if msg else context
        send_files = list(files) + extra_files

        # 빈 입력(메시지·파일·프롬프트 전부 없음)이면 실행하지 않는다.
        # 빈 채팅이 자동 다음노드 생성/체인을 유발해 무한 실행되는 것을 막는 가드.
        if not (send_msg and send_msg.strip()) and not send_files and not prompt_entries:
            self._running = False
            return

        # 입력 수집(_collect_all_inputs)을 끝낸 뒤 비운다 — 이 노드의 출력만 비우고
        # 입력으로 끌어온 값에는 영향을 주지 않기 위함.
        self._clear_pending_output()

        self._history.append({
            "user": msg,
            "files": list(files),
            "response": "",
            "images": [],
            "tokens_in": 0,
            "tokens_out": 0,
            "model": self.model,
            "extra_texts": list(extra_texts),
            "extra_files": list(extra_files),
            "prompt_entries": list(prompt_entries),
        })
        self._current_streaming = ""

        self._update_status("running")
        self.btn_input.setText("■")
        self.model_combo.setEnabled(False)
        self._btn_pref_view.hide()
        self.pending_results = []
        self._start_pulse()

        if self.on_send:
            self.on_send(self.node_id, self.model, send_msg, send_files, prompt_entries)

    def _process_queue(self):
        if not self._send_queue:
            return
        entry = self._send_queue.pop(0)
        self._running = True
        self._start_time = time.time()
        self.user_message = entry["msg"]
        self.user_files = entry["files"]
        self.model = entry["model"]
        self.node_options = entry["node_options"]

        self._clear_pending_output()

        self._history.append({
            "user": entry["msg"],
            "files": entry["files"],
            "response": "",
            "images": [],
            "tokens_in": 0,
            "tokens_out": 0,
            "model": entry["model"],
            "extra_texts": entry.get("extra_texts", []),
            "extra_files": entry.get("extra_files", []),
            "prompt_entries": entry.get("prompt_entries", []),
        })
        self._current_streaming = ""

        self._update_status("running")
        self.btn_input.setText("■")
        self.model_combo.setEnabled(False)
        self._btn_pref_view.hide()
        self.pending_results = []
        self._start_pulse()

        if self.on_send:
            self.on_send(self.node_id, entry["model"], entry["send_msg"], entry["send_files"], entry["prompt_entries"])

    def set_response(self, response, done=False, is_error=None):
        """Update response -- only updates history, no inline display.

        is_error: 명시 시 성공/에러 판정. None 이면 응답 접두("Error:"/"⚠️")로 추론.
        """
        self.ai_response = response
        self._current_streaming = response

        # Update last history entry
        if self._history:
            self._history[-1]["response"] = response

        # 인라인 응답 미리보기 갱신 — 스트리밍 중에도 즉시 보이게(진행상황 표시).
        streaming_error = is_error if is_error is not None else \
            bool(str(response or "").lstrip().startswith(("Error:", "⚠️")))
        self._update_inline_response(response, is_error=streaming_error)

        if done:
            self._running = False
            self._stop_pulse(ok=not streaming_error)
            self.btn_input.setText("▶")
            self.btn_input.setEnabled(True)
            self.model_combo.setEnabled(True)
            if self._history and (self.tokens_in or self.tokens_out):
                self._history[-1]["tokens_in"] = self.tokens_in
                self._history[-1]["tokens_out"] = self.tokens_out
            elapsed = time.time() - self._start_time if self._start_time else 0
            if is_error is None:
                is_error = bool(str(response or "").lstrip().startswith(("Error:", "⚠️")))
            self._record_run(elapsed, is_error, str(response) if is_error else "")
            self._set_meta_port_values(elapsed)
            self._update_status("error" if is_error else "done")
            if self._send_queue:
                QTimer.singleShot(0, self._process_queue)

    def _update_inline_response(self, text, is_error=False):
        """노드 면에는 결과를 표시하지 않는다(LLM 호출 노드 — 출력 포트로만 내보냄).

        과거엔 여기서 대화창 말풍선을 갱신했으나, 이 노드는 결과를 보여주는 게 아니라
        신호/데이터로 흘려보내는 역할이라 표시를 제거. 스트리밍 중 작동 표시는
        상태 라벨 + 글로우/셰이머/생성점이 담당한다. 지난 결과는 '로그 보기'에서만.
        """
        try:
            if hasattr(self, "_btn_log") and self._history:
                self._btn_log.show()
        except Exception:
            pass

    def _record_run(self, elapsed, is_error, error_msg=""):
        """실행 메트릭을 노드 상태 + 마지막 history 항목에 기록(분석용 단일 진실원)."""
        from v.provider import estimate_cost
        self._run_count += 1
        self._last_elapsed = float(elapsed or 0.0)
        self._last_success = not is_error
        self._last_error = error_msg or ""
        model = self.model_combo.currentData() or ""
        self._last_cost = estimate_cost(model, self.tokens_in, self.tokens_out)
        if self._history:
            h = self._history[-1]
            h["elapsed"] = self._last_elapsed
            h["success"] = self._last_success
            h["error"] = self._last_error
            h["cost"] = self._last_cost
            h["model"] = model

    def _decode_image_data(self, img_data):
        if isinstance(img_data, bytes):
            if (
                img_data[:4] == b"\x89PNG" or
                img_data[:2] == b"\xff\xd8" or
                img_data[:4] == b"GIF8" or
                img_data[:4] == b"RIFF"
            ):
                return img_data
            try:
                decoded = base64.b64decode(img_data)
                if (
                    decoded[:4] == b"\x89PNG" or
                    decoded[:2] == b"\xff\xd8" or
                    decoded[:4] == b"GIF8" or
                    decoded[:4] == b"RIFF"
                ):
                    return decoded
            except Exception:
                pass
            return img_data
        if isinstance(img_data, str):
            try:
                return base64.b64decode(img_data)
            except Exception:
                return None
        return None

    def set_image_response(self, text, images, thought_signatures=None):
        self._stop_pulse(ok=True)
        self.ai_response = text or t("status.images_created", count=len(images))
        self.ai_image_paths = []
        self.thought_signatures = thought_signatures or []

        from v.temp_file_manager import TempFileManager
        temp_manager = TempFileManager()

        saved_paths = []
        for i, img_data in enumerate(images):
            raw_bytes = self._decode_image_data(img_data)
            if not raw_bytes:
                continue

            # 보드별 temp 폴더에 저장 (save 시 자동 아카이빙)
            if ChatNodeWidget._board_temp_dir:
                _temp_img_dir = ChatNodeWidget._board_temp_dir
            else:
                _temp_img_dir = tempfile.gettempdir()
            temp_path = os.path.join(
                _temp_img_dir, f"{uuid.uuid4().hex}.png"
            )
            try:
                with open(temp_path, "wb") as f:
                    f.write(raw_bytes)
                temp_manager.register(temp_path)
            except Exception:
                continue

            pixmap = QPixmap(temp_path)
            if pixmap.isNull():
                continue

            self.ai_image_paths.append(temp_path)
            saved_paths.append(temp_path)

        # Update last history entry
        if self._history:
            self._history[-1]["response"] = self.ai_response
            self._history[-1]["images"] = list(saved_paths)
            if self.tokens_in or self.tokens_out:
                self._history[-1]["tokens_in"] = self.tokens_in
                self._history[-1]["tokens_out"] = self.tokens_out

        self._running = False
        elapsed = time.time() - self._start_time if self._start_time else 0
        self._record_run(elapsed, is_error=False)
        self._set_meta_port_values(elapsed)
        self.btn_input.setText("▶")
        self.btn_input.setEnabled(True)
        self.model_combo.setEnabled(True)
        # 출력 블록을 요약 텍스트로 마감(이미지 생성은 텍스트 스트림이 없으므로 명시 갱신).
        self._update_inline_response(self.ai_response or t("status.images_created", count=len(saved_paths)))
        self._update_status("done")
        if self._send_queue:
            QTimer.singleShot(0, self._process_queue)

    def set_tokens(self, tokens_in: int, tokens_out: int):
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        # Update current history entry tokens
        if self._history:
            self._history[-1]["tokens_in"] = tokens_in
            self._history[-1]["tokens_out"] = tokens_out

    def _set_meta_port_values(self, elapsed):
        meta = self.meta_output_ports
        tin, tout = self.tokens_in or 0, self.tokens_out or 0
        # 분석 친화 typed 포트들. 없는 키는 조용히 건너뜀(포트 셋이 바뀌어도 안전).
        values = {
            "success": self._last_success,                       # BOOLEAN
            "error": self._last_error,                           # STRING
            "elapsed": f"{elapsed:.2f}",                          # 초(숫자 문자열)
            "model_name": self.model_combo.currentData() or "",
            "tokens_in": str(tin),
            "tokens_out": str(tout),
            "tokens_total": str(tin + tout),
            "cost": ("" if self._last_cost is None else f"{self._last_cost:.6f}"),
            "runs": str(self._run_count),
        }
        for key, val in values.items():
            if key in meta:
                meta[key].port_value = val

    def _show_tokens(self):
        self.tokens_label.setText(f"{self.tokens_in:,}  {self.tokens_out:,}")
        self.tokens_label.show()

    def _copy_response(self):
        if self.ai_response:
            QApplication.clipboard().setText(self.ai_response)

    def _add_file_button(self, fname, fpath):
        pass  # No longer used for inline display

    def _open_file(self, fpath):
        try:
            os.startfile(fpath)
        except Exception:
            pass

    def _toggle_pin(self, checked):
        self.pinned = checked
        if checked:
            self.header.setStyleSheet(
                f"""
                DraggableHeader {{
                    background-color: #3a3a1e;
                    border-top-left-radius: 9px;
                    border-top-right-radius: 9px;
                    border-bottom: 1px solid #5a5a2e;
                }}
                """
            )
        else:
            self.header.setStyleSheet(
                f"""
                DraggableHeader {{
                    background-color: {Theme.NODE_HEADER};
                    border-top-left-radius: 9px;
                    border-top-right-radius: 9px;
                    border-bottom: 1px solid {Theme.BG_HOVER};
                }}
                """
            )
        if self.on_modified:
            self.on_modified()

    def _ensure_glow(self):
        """proxy(캔버스 아이템)에 드롭섀도 글로우 효과를 단다 — 위젯 내부와 무관해 깜빡임 없음."""
        if self._glow_effect is not None:
            return self._glow_effect
        if self.proxy is None:
            return None
        try:
            eff = QGraphicsDropShadowEffect()
            eff.setOffset(0, 0)
            eff.setBlurRadius(18)
            eff.setColor(QColor(Theme.ACCENT_PRIMARY))
            self.proxy.setGraphicsEffect(eff)
            self._glow_effect = eff
        except Exception:
            self._glow_effect = None
        return self._glow_effect

    def _clear_glow(self):
        self._glow_effect = None
        if self.proxy is None:
            return
        try:
            self.proxy.setGraphicsEffect(None)
        except Exception:
            pass

    def _start_pulse(self):
        # 작동 중 시각 효과 = (1) 노드 둘레 글로우 호흡 (2) 헤더 진행 셰이머
        # (3) 상태영역 생성 점(_gen_dots, _update_status 가 토글) (4) 내부 accent 테두리(paintEvent).
        self._pulse_active = True
        self._glow_phase = 0.0
        self._ensure_glow()
        self._pulse_timer.start()
        sh = getattr(self, "_shimmer", None)
        if sh is not None:
            sh.start()
        self.update()   # 내부 실행 테두리 즉시 표시

    def _stop_pulse(self, ok=True):
        self._pulse_timer.stop()
        self._pulse_active = False
        sh = getattr(self, "_shimmer", None)
        if sh is not None:
            sh.stop()
        gd = getattr(self, "_gen_dots", None)
        if gd is not None:
            gd.stop()   # 생성 중 점 정지(자체 타이머)
        # 완료 플래시(성공=녹색 / 오류=빨강) 후 글로우 제거.
        eff = self._glow_effect
        if eff is not None:
            try:
                eff.setColor(QColor(Theme.ACCENT_SUCCESS if ok else Theme.ACCENT_DANGER))
                eff.setBlurRadius(26)
            except Exception:
                pass
            QTimer.singleShot(650, self._clear_glow)
        self.update()   # 내부 실행 테두리 제거

    def _pulse_tick(self):
        if not self._pulse_active:
            return
        eff = self._glow_effect
        if eff is None:
            return
        self._glow_phase += 0.16
        w = math.sin(self._glow_phase) * 0.5 + 0.5   # 0..1 호흡
        # 파랑(ACCENT) ↔ 밝은 시안 사이 보간 + blur 호흡.
        a = QColor(Theme.ACCENT_PRIMARY)
        r = int(a.red() + (90 - a.red()) * w)
        g = int(a.green() + (200 - a.green()) * w)
        b = int(a.blue() + (255 - a.blue()) * w)
        try:
            eff.setColor(QColor(r, g, b))
            eff.setBlurRadius(16 + 22 * w)
        except Exception:
            pass

    def paintEvent(self, event):
        super().paintEvent(event)
        # 실행 중엔 노드 내부에 accent 둥근 테두리(이미 리페인트되는 순간에만 그려져 깜빡임 없음).
        if getattr(self, "_running", False):
            try:
                p = QPainter(self)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                pen = QPen(QColor(Theme.ACCENT_PRIMARY))
                pen.setWidth(2)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                r = self.rect().adjusted(2, 2, -2, -2)
                p.drawRoundedRect(QRectF(r), 11.0, 11.0)
                p.end()
            except Exception:
                pass

    def pack_history(self, keep_recent=20):
        if len(self._history) <= keep_recent:
            return False
        to_archive = self._history[:-keep_recent]
        self._history = self._history[-keep_recent:]

        temp_dir = self._board_temp_dir
        if not temp_dir:
            return False
        archive_dir = os.path.join(temp_dir, "archives")
        os.makedirs(archive_dir, exist_ok=True)
        archive_file = os.path.join(archive_dir, f"chat_{self.node_id}.json.gz")

        existing = []
        if os.path.exists(archive_file):
            try:
                with gzip.open(archive_file, "rt", encoding="utf-8") as f:
                    data = json.loads(f.read())
                    existing = data.get("entries", [])
            except Exception:
                pass

        all_entries = existing + to_archive
        payload = {
            "node_id": self.node_id,
            "packed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "entries": all_entries,
        }
        with gzip.open(archive_file, "wt", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False))

        self._archive_path = f"archives/chat_{self.node_id}.json.gz"
        self._archived_count = len(all_entries)
        self._update_status("done")
        if callable(self.on_modified):
            self.on_modified()
        return True

    def unpack_history(self):
        if not self._archive_path:
            return False
        temp_dir = self._board_temp_dir
        if not temp_dir:
            return False
        archive_file = os.path.join(temp_dir, "archives", f"chat_{self.node_id}.json.gz")
        if not os.path.exists(archive_file):
            return False
        try:
            with gzip.open(archive_file, "rt", encoding="utf-8") as f:
                data = json.loads(f.read())
            archived = data.get("entries", [])
        except Exception:
            return False
        self._history = archived + self._history
        try:
            os.remove(archive_file)
        except OSError:
            pass
        self._archive_path = None
        self._archived_count = 0
        self._update_status("done")
        if callable(self.on_modified):
            self.on_modified()
        return True

    def load_archive_entries(self):
        if not self._archive_path:
            return []
        temp_dir = self._board_temp_dir
        if not temp_dir:
            return []
        archive_file = os.path.join(temp_dir, "archives", f"chat_{self.node_id}.json.gz")
        if not os.path.exists(archive_file):
            return []
        try:
            with gzip.open(archive_file, "rt", encoding="utf-8") as f:
                data = json.loads(f.read())
            return data.get("entries", [])
        except Exception:
            return []

    def get_data(self, _light: bool = False):
        # _light=True: 무거운 deepcopy(history/extra_input_defs) 생략 — sync_props 처럼
        # 내용을 어차피 버리는 경우의 주기 스캔 비용 절감. 저장/직렬화는 기본(False)로 전체.
        d = {
            "type": "chat_node",
            "id": self.node_id,
            "x": self.proxy.pos().x() if self.proxy else 0,
            "y": self.proxy.pos().y() if self.proxy else 0,
            "width": self.width(),
            "height": self.height(),
            "user_message": self.user_message,
            "user_files": list(self.user_files or []),
            "ai_response": self.ai_response,
            "ai_image_paths": list(self.ai_image_paths or []),
            "thought_signatures": list(self.thought_signatures or []),
            "sent": self._running,
            "pinned": self.pinned,
            "model": self.model_combo.currentData(),
            "node_options": self.node_options,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "run_count": self._run_count,
            "notify_on_complete": self.notify_on_complete,
            "extra_input_defs": [] if _light else copy.deepcopy(self.extra_input_defs),
            "preferred_options_enabled": self.preferred_options_enabled,
            "preferred_options_count": self.preferred_options_count,
            "meta_selected": sorted(self.meta_selected),
            "history": [] if _light else copy.deepcopy(self._history),
        }
        if self._archive_path:
            d["archive_path"] = self._archive_path
            d["archived_count"] = self._archived_count
        return d

    # 동기화에서 제외할 키: 내용/히스토리(=chat_append·AI 경로) + 포트 토폴로지(구조)
    _SYNC_EXCLUDE_KEYS = {
        "user_message", "user_files", "ai_response", "ai_image_paths",
        "thought_signatures", "history", "sent", "tokens_in", "tokens_out",
        "archive_path", "archived_count", "extra_input_defs", "meta_selected",
    }

    def sync_props(self) -> dict:
        """서버모드 동기화용 데이터 — get_data 에서 내용/포트 키만 제외(=설정/크기 전부).

        get_data 에 새 설정 필드가 추가되면 자동으로 포함된다(따로 손볼 필요 없음).
        extra_input_defs(추가 입력 포트 구조)는 명시적으로 포함한다 — 서버 doc 에
        영속돼야 재접속 시 포트가 복원되고 그 포트로 연결된 엣지가 안 끊긴다.
        """
        d = {k: v for k, v in self.get_data(_light=True).items()
             if k not in self._SYNC_EXCLUDE_KEYS}
        d["extra_input_defs"] = [dict(x) for x in self.extra_input_defs]
        return d

    def apply_sync_data(self, data: dict):
        """원격 변경을 제자리 반영. load 와 동일한 restore_state 를 재사용(단일 진실원).

        data 에 있는 키만 적용 → sync_props 는 내용 키를 안 보내므로 내용은 안 건드림.
        op 에코는 _applying_remote_op 가 차단.
        """
        self.restore_state(data)

    def restore_state(self, row: dict):
        """row 에 존재하는 위젯 필드만 복원(부분 dict 안전). load·sync 공용.

        포트(meta/extra_input)·preferred 후보 콜백은 플러그인 레벨이라 여기서 다루지 않고
        _materialize_chat_node 가 이 호출 뒤에 처리한다.
        """
        if row.get("width") and row.get("height"):
            self.resize(int(row["width"]), int(row["height"]))
            self._restored_geometry = True   # 저장된 크기 존중 — showEvent 자동 fit 안 함
        if "user_message" in row:
            self.user_message = row["user_message"]
        if "user_files" in row:
            self.user_files = row.get("user_files") or []
        if "ai_response" in row:
            self.ai_response = row["ai_response"]
        if row.get("model"):
            idx = self.model_combo.findData(row["model"])
            if idx >= 0:
                self.model_combo.setCurrentIndex(idx)
        if "node_options" in row and isinstance(row["node_options"], dict):
            opts = row["node_options"]
            self.node_options = opts
            # 모델 복원(위)으로 패널 스키마가 이미 세팅됨 → 저장된 값을 패널에 반영.
            self.opts_panel.set_values(opts)
        if "pinned" in row:
            self.pinned = bool(row["pinned"])
            self.btn_pin.setChecked(self.pinned)
        if "ai_image_paths" in row:
            self.ai_image_paths = row.get("ai_image_paths") or []
        if "thought_signatures" in row:
            self.thought_signatures = row.get("thought_signatures") or []
        if "tokens_in" in row:
            self.tokens_in = row.get("tokens_in", 0)
        if "tokens_out" in row:
            self.tokens_out = row.get("tokens_out", 0)
        if "run_count" in row:
            self._run_count = int(row.get("run_count", 0) or 0)
        if "meta_selected" in row:
            self.meta_selected = set(row.get("meta_selected") or [])
        if "notify_on_complete" in row:
            self.notify_on_complete = bool(row["notify_on_complete"])
            self.btn_notify.setChecked(self.notify_on_complete)
        if "preferred_options_enabled" in row:
            self.preferred_options_enabled = bool(row["preferred_options_enabled"])
            self.btn_pref_toggle.setChecked(self.preferred_options_enabled)
        if "preferred_options_count" in row:
            self.preferred_options_count = int(row["preferred_options_count"])
            self.pref_count_spin.setValue(self.preferred_options_count)
        self.pref_count_spin.setEnabled(self.preferred_options_enabled)
        # 옵션 패널은 항상 인라인이라 별도 가시성 복원 불필요(set_schema 가 모델 따라 채움).
        if "history" in row:
            self._history = row.get("history") or []
            if not self._history and row.get("ai_response"):
                self._history = [{
                    "user": row.get("user_message", ""),
                    "files": row.get("user_files", []),
                    "response": row.get("ai_response", ""),
                    "images": row.get("ai_image_paths", []),
                    "tokens_in": row.get("tokens_in", 0),
                    "tokens_out": row.get("tokens_out", 0),
                    "model": row.get("model", ""),
                }]
            # 결과는 노드 면에 표시하지 않는다(출력 포트로만). 로드 시 상태만 갱신.
            self._update_status("done" if self._history else "idle")
        if "archive_path" in row:
            self._archive_path = row.get("archive_path")
            self._archived_count = row.get("archived_count", 0)

    def cleanup_temp_files(self):
        """Clean up temp files created by this node."""
        from v.temp_file_manager import TempFileManager
        temp_manager = TempFileManager()

        for filepath in self.ai_image_paths:
            temp_manager.cleanup_file(filepath)
        self.ai_image_paths.clear()
