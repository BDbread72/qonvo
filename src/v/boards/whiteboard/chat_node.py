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
import math
import os
import tempfile
import time
import uuid

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QComboBox, QScrollArea, QFrame, QApplication, QDoubleSpinBox, QSpinBox,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPixmap, QPainter, QColor, QPen, QPainterPath

from q import t
from v.model_plugin import get_all_models, get_all_model_ids, get_all_model_options
from v.theme import Theme
from .base_node import BaseNode
from .widgets import DraggableHeader, ResizeHandle, InputDialog


class ChatLogWindow(QWidget):

    _PAGE_SIZE = 10

    def __init__(self, node_id, history, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self._node_id = node_id
        self._history = history
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
                if os.path.exists(img_path):
                    pixmap = QPixmap(img_path)
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

        # preferred candidates
        preferred_texts = entry.get("preferred_texts", [])
        if preferred_texts:
            pref_header = QLabel(t("chat.preferred_candidates", count=len(preferred_texts)))
            pref_header.setStyleSheet(
                f"color: {Theme.ACCENT_PRIMARY}; font-size: 10px; font-weight: bold; border: none;"
            )
            fl.addWidget(pref_header)

            for i, ptext in enumerate(preferred_texts):
                if not ptext:
                    continue
                # candidate number
                num_label = QLabel(f"#{i + 1}")
                num_label.setStyleSheet(
                    f"color: {Theme.TEXT_DISABLED}; font-size: 10px; font-weight: bold; "
                    f"border: none; margin-top: 4px;"
                )
                fl.addWidget(num_label)
                # candidate text
                cand_label = QLabel(ptext)
                cand_label.setWordWrap(True)
                cand_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                cand_label.setStyleSheet(
                    f"background-color: {Theme.BG_TERTIARY}; border: 1px solid {Theme.BG_HOVER}; "
                    f"border-radius: 6px; padding: 6px 8px; color: {Theme.TEXT_PRIMARY}; font-size: 11px;"
                )
                fl.addWidget(cand_label)
                # copy button
                cp_btn = QPushButton(t("button.copy"))
                cp_btn.setStyleSheet(
                    f"QPushButton {{ background: transparent; color: {Theme.TEXT_TERTIARY}; "
                    f"border: none; font-size: 10px; padding: 2px 4px; }}"
                    f"QPushButton:hover {{ color: #aaa; }}"
                )
                cp_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                cp_btn.clicked.connect(lambda checked, txt=ptext, btn=cp_btn: self._copy_text(txt, btn))
                fl.addWidget(cp_btn, alignment=Qt.AlignmentFlag.AlignRight)

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


class ChatNodeWidget(QWidget, BaseNode):
    """Chat node with model selection and streaming response."""

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
        self._pref_window = None
        self._log_window = None
        self.meta_output_ports = {}
        self.meta_ports_enabled = False
        self.on_toggle_meta = None
        self._start_time = None

        # Multi-run history
        self._history = []            # [{"user": str, "files": [], "response": str, "images": [], "tokens_in": int, "tokens_out": int, "model": str}, ...]
        self._current_streaming = ""

        self.setMinimumSize(280, 200)
        self.resize(280, 200)
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

        title = QLabel(f"#{self.node_id}")
        title.setStyleSheet(f"color: {Theme.TEXT_SECONDARY}; font-weight: bold; font-size: 12px;")
        header_layout.addWidget(title)
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

        # model bar
        model_bar = QFrame()
        model_bar.setStyleSheet(f"background-color: {Theme.BG_INPUT}; border: none;")
        model_layout = QHBoxLayout(model_bar)
        model_layout.setContentsMargins(12, 6, 12, 6)

        model_label = QLabel(t("label.model"))
        model_label.setStyleSheet(f"color: {Theme.TEXT_TERTIARY}; font-size: 11px;")
        model_layout.addWidget(model_label)

        self.model_combo = QComboBox()
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
        self.model_combo.setMaxVisibleItems(15)
        self.model_combo.setStyleSheet(
            f"""
            QComboBox {{
                background-color: #333; color: {Theme.TEXT_PRIMARY}; border: 1px solid #444;
                border-radius: 6px; padding: 6px 12px; padding-right: 28px;
                min-width: 150px; font-size: 12px;
            }}
            QComboBox:hover {{ border-color: {Theme.ACCENT_PRIMARY}; background-color: {Theme.BG_HOVER}; }}
            QComboBox:focus {{ border-color: {Theme.ACCENT_PRIMARY}; }}
            QComboBox::drop-down {{ border: none; width: 24px; }}
            QComboBox::down-arrow {{
                image: none; border-left: 5px solid transparent;
                border-right: 5px solid transparent; border-top: 6px solid {Theme.TEXT_SECONDARY};
                margin-right: 8px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {Theme.BG_SECONDARY}; color: {Theme.TEXT_PRIMARY}; border: 1px solid #444;
                selection-background-color: {Theme.ACCENT_PRIMARY};
            }}
            """
        )
        model_layout.addWidget(self.model_combo)

        # aspect ratio combo (shown only for image models)
        self.ratio_combo = QComboBox()
        self.ratio_combo.setFixedWidth(70)
        self.ratio_combo.setStyleSheet(
            f"""
            QComboBox {{
                background-color: #333; color: {Theme.TEXT_PRIMARY}; border: 1px solid #444;
                border-radius: 4px; padding: 4px 6px; font-size: 11px;
            }}
            QComboBox:hover {{ border-color: {Theme.ACCENT_PRIMARY}; }}
            QComboBox::drop-down {{ border: none; width: 18px; }}
            QComboBox::down-arrow {{
                image: none; border-left: 4px solid transparent;
                border-right: 4px solid transparent; border-top: 5px solid {Theme.TEXT_SECONDARY};
                margin-right: 4px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {Theme.BG_SECONDARY}; color: {Theme.TEXT_PRIMARY};
                border: 1px solid #444; selection-background-color: {Theme.ACCENT_PRIMARY};
            }}
            """
        )
        self.ratio_combo.hide()
        model_layout.addWidget(self.ratio_combo)

        self.size_combo = QComboBox()
        self.size_combo.setFixedWidth(60)
        self.size_combo.setStyleSheet(
            f"""
            QComboBox {{
                background-color: #333; color: {Theme.TEXT_PRIMARY}; border: 1px solid #444;
                border-radius: 4px; padding: 4px 6px; font-size: 11px;
            }}
            QComboBox:hover {{ border-color: {Theme.ACCENT_PRIMARY}; }}
            QComboBox::drop-down {{ border: none; width: 18px; }}
            QComboBox::down-arrow {{
                image: none; border-left: 4px solid transparent;
                border-right: 4px solid transparent; border-top: 5px solid {Theme.TEXT_SECONDARY};
                margin-right: 4px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {Theme.BG_SECONDARY}; color: {Theme.TEXT_PRIMARY};
                border: 1px solid #444; selection-background-color: {Theme.ACCENT_PRIMARY};
            }}
            """
        )
        self.size_combo.hide()
        model_layout.addWidget(self.size_combo)

        # Generation options toggle button
        self.btn_opts_toggle = QPushButton("G")
        self.btn_opts_toggle.setFixedSize(24, 24)
        self.btn_opts_toggle.setCheckable(True)
        self.btn_opts_toggle.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: none; font-size: 12px;
                border-radius: 4px; color: {Theme.TEXT_TERTIARY};
            }}
            QPushButton:checked {{ background-color: {Theme.ACCENT_PRIMARY}; color: white; }}
            QPushButton:hover {{ background-color: {Theme.BG_HOVER}; }}
        """)
        self.btn_opts_toggle.setToolTip("Generation Options")
        self.btn_opts_toggle.clicked.connect(self._toggle_opts_panel)
        model_layout.addWidget(self.btn_opts_toggle)

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
        self.btn_meta_toggle.setToolTip("Meta Output Ports (elapsed_time / model_name / tokens)")
        self.btn_meta_toggle.clicked.connect(self._on_meta_toggle_clicked)
        model_layout.addWidget(self.btn_meta_toggle)

        model_layout.addStretch()
        layout.addWidget(model_bar)

        # generation options panel (hidden by default)
        self.opts_panel = QFrame()
        self.opts_panel.setStyleSheet(f"QFrame {{ background-color: {Theme.BG_INPUT}; border: none; }}")
        opts_layout = QHBoxLayout(self.opts_panel)
        opts_layout.setContentsMargins(12, 4, 12, 4)
        opts_layout.setSpacing(6)

        t_label = QLabel("T:")
        t_label.setStyleSheet(f"color: {Theme.TEXT_TERTIARY}; font-size: 10px;")
        opts_layout.addWidget(t_label)

        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(0.0, 2.0)
        self.temp_spin.setSingleStep(0.05)
        self.temp_spin.setDecimals(2)
        self.temp_spin.setValue(1.0)
        self.temp_spin.setFixedWidth(58)
        self.temp_spin.setStyleSheet(f"""
            QDoubleSpinBox {{
                background-color: #333; color: {Theme.TEXT_PRIMARY}; border: 1px solid #444;
                border-radius: 4px; padding: 2px; font-size: 10px;
            }}
        """)
        opts_layout.addWidget(self.temp_spin)

        p_label = QLabel("P:")
        p_label.setStyleSheet(f"color: {Theme.TEXT_TERTIARY}; font-size: 10px;")
        opts_layout.addWidget(p_label)

        self.top_p_spin = QDoubleSpinBox()
        self.top_p_spin.setRange(0.0, 1.0)
        self.top_p_spin.setSingleStep(0.05)
        self.top_p_spin.setDecimals(2)
        self.top_p_spin.setValue(0.95)
        self.top_p_spin.setFixedWidth(58)
        self.top_p_spin.setStyleSheet(f"""
            QDoubleSpinBox {{
                background-color: #333; color: {Theme.TEXT_PRIMARY}; border: 1px solid #444;
                border-radius: 4px; padding: 2px; font-size: 10px;
            }}
        """)
        opts_layout.addWidget(self.top_p_spin)

        max_label = QLabel("Max:")
        max_label.setStyleSheet(f"color: {Theme.TEXT_TERTIARY}; font-size: 10px;")
        opts_layout.addWidget(max_label)

        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setRange(1, 65536)
        self.max_tokens_spin.setValue(8192)
        self.max_tokens_spin.setFixedWidth(68)
        self.max_tokens_spin.setStyleSheet(f"""
            QSpinBox {{
                background-color: #333; color: {Theme.TEXT_PRIMARY}; border: 1px solid #444;
                border-radius: 4px; padding: 2px; font-size: 10px;
            }}
        """)
        opts_layout.addWidget(self.max_tokens_spin)
        opts_layout.addStretch()

        self.opts_panel.hide()
        layout.addWidget(self.opts_panel)

        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        self._on_model_changed()

        # input port management bar
        input_bar = QFrame()
        input_bar.setStyleSheet(f"QFrame {{ background-color: {Theme.BG_INPUT}; border: none; }}")
        input_bar_layout = QHBoxLayout(input_bar)
        input_bar_layout.setContentsMargins(12, 4, 12, 4)
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
        pref_layout.setContentsMargins(12, 4, 12, 4)
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

        # Status area (replaces old content_area with inline responses)
        self._status_area = QFrame()
        self._status_area.setStyleSheet(f"QFrame {{ background-color: {Theme.BG_TERTIARY}; border: none; }}")
        status_layout = QVBoxLayout(self._status_area)
        status_layout.setContentsMargins(10, 12, 10, 12)
        status_layout.setSpacing(6)
        status_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._status_label = QLabel(t("chat.status_idle"))
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setStyleSheet(
            f"color: {Theme.TEXT_DISABLED}; font-size: 13px; font-weight: bold;"
        )
        status_layout.addWidget(self._status_label)

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

        layout.addWidget(self._status_area)

        # compose button (always visible, disabled only while running)
        self.btn_input = QPushButton(t("button.compose"))
        self.btn_input.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {Theme.ACCENT_PRIMARY}; color: white; border: none; padding: 10px;
                font-weight: bold; border-bottom-left-radius: 9px; border-bottom-right-radius: 9px;
            }}
            QPushButton:hover {{ background-color: {Theme.ACCENT_HOVER}; }}
            QPushButton:disabled {{ background-color: {Theme.BG_HOVER}; color: {Theme.TEXT_DISABLED}; }}
            """
        )
        self.btn_input.clicked.connect(self._open_input)
        layout.addWidget(self.btn_input)

        self.tokens_label = QLabel("")
        self.tokens_label.setStyleSheet(f"color: {Theme.TEXT_DISABLED}; font-size: 10px; padding: 2px 10px;")
        self.tokens_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.tokens_label.hide()
        layout.addWidget(self.tokens_label)

        self.resize_handle = ResizeHandle(self)
        self.resize_handle.move(self.width() - 16, self.height() - 16)
        self.resize_handle.raise_()

        self._pulse_timer = QTimer()
        self._pulse_timer.setInterval(30)
        self._pulse_timer.timeout.connect(self._pulse_tick)
        self._pulse_phase = 0.0
        self._pulse_active = False
        self._pulse_bg_color = None
        self._pulse_border_color = None

    def _update_status(self, state):
        """Update the status indicator: 'idle', 'running', or 'done'."""
        count = len(self._history)
        if state == "idle":
            self._status_label.setText(t("chat.status_idle"))
            self._status_label.setStyleSheet(
                f"color: {Theme.TEXT_DISABLED}; font-size: 13px; font-weight: bold;"
            )
        elif state == "running":
            self._status_label.setText(t("chat.status_running"))
            self._status_label.setStyleSheet(
                f"color: {Theme.ACCENT_PRIMARY}; font-size: 13px; font-weight: bold;"
            )
        elif state == "done":
            self._status_label.setText(t("chat.status_done"))
            self._status_label.setStyleSheet(
                f"color: {Theme.ACCENT_SUCCESS}; font-size: 13px; font-weight: bold;"
            )

        if count > 0:
            self._run_count_label.setText(t("chat.run_count", count=count))
            self._run_count_label.show()
            self._btn_log.show()
        else:
            self._run_count_label.hide()
            self._btn_log.hide()

    def _open_log_window(self):
        """Open the log window to view execution history."""
        if self._log_window is not None:
            self._log_window.raise_()
            self._log_window.activateWindow()
            return
        self._log_window = ChatLogWindow(self.node_id, list(self._history))
        # U2: weak ref 방식으로 안전한 콜백 (부모 노드 삭제 후 접근 방지)
        import weakref
        weak_self = weakref.ref(self)
        self._log_window.destroyed.connect(
            lambda: (lambda ws=weak_self: setattr(ws(), '_log_window', None) if ws() is not None else None)()
        )
        self._log_window.show()

    def _toggle_opts_panel(self):
        self.opts_panel.setVisible(self.btn_opts_toggle.isChecked())

    def _on_meta_toggle_clicked(self):
        """메타 포트 토글 버튼 클릭 시 meta_ports_enabled를 갱신하고 on_toggle_meta 콜백을 호출한다."""
        self.meta_ports_enabled = self.btn_meta_toggle.isChecked()
        if self.on_toggle_meta:
            self.on_toggle_meta(self)

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
        self._stop_pulse()
        self.btn_input.setText(t("button.compose"))
        self.btn_input.setEnabled(True)
        self.model_combo.setEnabled(True)
        self._update_status("done")
        if self._send_queue:  # preferred 완료 후 대기 큐 처리
            QTimer.singleShot(0, self._process_queue)
        count = len(results)
        self._btn_pref_view.setText(t("chat.preferred_candidates", count=count))
        self._btn_pref_view.show()
        QTimer.singleShot(300, self._open_preferred_window)

        # 히스토리에 전체 후보 텍스트 저장 + 응답 텍스트 갱신
        summary = t("chat.preferred_done", count=count)
        self.ai_response = summary
        if self._history:
            preferred_texts = []
            for text, _images in results:
                preferred_texts.append(text or "")
            self._history[-1]["preferred_texts"] = preferred_texts
            self._history[-1]["response"] = summary

    def _open_preferred_window(self):
        """Open preferred results selection window."""
        if not self.pending_results:
            return
        if self._pref_window is not None:
            self._pref_window.raise_()
            self._pref_window.activateWindow()
            return

        from .preferred_dialog import PreferredResultsWindow

        self._pref_window = PreferredResultsWindow(self.pending_results)
        self._pref_window.selection_confirmed.connect(self._on_pref_confirmed)
        self._pref_window.selection_cancelled.connect(self._on_pref_cancelled)
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
        for port_name, port in self.input_ports.items():
            if not port.edges:
                continue
            source_port = port.edges[0].source_port
            source_proxy = source_port.parent_proxy
            if not source_proxy:
                continue
            source_node = source_proxy.widget() if hasattr(source_proxy, 'widget') else source_proxy

            is_image = port.port_data_type == port.TYPE_FILE
            if is_image:
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
                    else:
                        texts.append(text)
        return texts, files, prompt_entries

    def on_signal_input(self, input_data=None):
        context = input_data or self._collect_input_data() or ""
        self._send(context, [])  # 큐잉 여부는 _send 내부에서 판단

    def _on_model_changed(self):
        model_id = self.model_combo.currentData()
        opts = get_all_model_options().get(model_id, {})
        ar_spec = opts.get("aspect_ratio")
        if ar_spec and "values" in ar_spec:
            self.ratio_combo.blockSignals(True)
            self.ratio_combo.clear()
            self.ratio_combo.addItems(ar_spec["values"])
            default_val = ar_spec.get("default", "")
            idx = self.ratio_combo.findText(default_val)
            if idx >= 0:
                self.ratio_combo.setCurrentIndex(idx)
            self.ratio_combo.blockSignals(False)
            self.ratio_combo.show()
        else:
            self.ratio_combo.hide()
        size_spec = opts.get("image_size")
        if size_spec and "values" in size_spec:
            self.size_combo.blockSignals(True)
            self.size_combo.clear()
            self.size_combo.addItems(size_spec["values"])
            default_sz = size_spec.get("default", "")
            idx = self.size_combo.findText(default_sz)
            if idx >= 0:
                self.size_combo.setCurrentIndex(idx)
            self.size_combo.blockSignals(False)
            self.size_combo.show()
        else:
            self.size_combo.hide()
        if "temperature" in opts:
            self.temp_spin.setValue(opts["temperature"]["default"])
        if "top_p" in opts:
            self.top_p_spin.setValue(opts["top_p"]["default"])
        if "max_output_tokens" in opts:
            self.max_tokens_spin.setValue(opts["max_output_tokens"]["default"])
            self.max_tokens_spin.show()
        else:
            self.max_tokens_spin.hide()

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

    def _request_cancel(self):
        self._send_queue.clear()
        if self.on_cancel:
            self.on_cancel(self.node_id)

    def send(self, message, files=None):
        self._send(message, files or [])

    def _collect_node_options(self, model=None):
        model = model or self.model_combo.currentData()
        node_options = {}
        opts = get_all_model_options().get(model, {})
        if "aspect_ratio" in opts:
            node_options["aspect_ratio"] = self.ratio_combo.currentText()
        if "image_size" in opts:
            node_options["image_size"] = self.size_combo.currentText()
        if "temperature" in opts:
            node_options["temperature"] = self.temp_spin.value()
        if "top_p" in opts:
            node_options["top_p"] = self.top_p_spin.value()
        if "max_output_tokens" in opts:
            node_options["max_output_tokens"] = self.max_tokens_spin.value()
        return node_options

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

    def set_response(self, response, done=False):
        """Update response -- only updates history, no inline display."""
        self.ai_response = response
        self._current_streaming = response

        # Update last history entry
        if self._history:
            self._history[-1]["response"] = response

        if done:
            self._running = False
            self._stop_pulse()
            self.btn_input.setText(t("button.compose"))
            self.btn_input.setEnabled(True)
            self.model_combo.setEnabled(True)
            if self._history and (self.tokens_in or self.tokens_out):
                self._history[-1]["tokens_in"] = self.tokens_in
                self._history[-1]["tokens_out"] = self.tokens_out
            elapsed = time.time() - self._start_time if self._start_time else 0
            self._set_meta_port_values(elapsed)
            self._update_status("done")
            if self._send_queue:
                QTimer.singleShot(0, self._process_queue)

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
        self._stop_pulse()
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
        self._set_meta_port_values(elapsed)
        self.btn_input.setText(t("button.compose"))
        self.btn_input.setEnabled(True)
        self.model_combo.setEnabled(True)
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
        if "elapsed_time" in meta:
            meta["elapsed_time"].port_value = f"{elapsed:.1f}s"
        if "model_name" in meta:
            meta["model_name"].port_value = self.model_combo.currentData() or ""
        if "tokens" in meta:
            meta["tokens"].port_value = f"{self.tokens_in:,} / {self.tokens_out:,}"

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

    def _start_pulse(self):
        self._pulse_phase = 0.0
        self._pulse_active = True
        self.setStyleSheet("")
        self._pulse_timer.start()

    def _stop_pulse(self):
        self._pulse_timer.stop()
        self._pulse_active = False
        self._pulse_bg_color = None
        self._pulse_border_color = None
        self.setStyleSheet(
            f"""
            ChatNodeWidget {{
                background-color: {Theme.BG_TERTIARY};
                border: 3px solid {Theme.NODE_BORDER};
                border-radius: 12px;
            }}
            """
        )

    def _pulse_tick(self):
        from v.constants import PULSE_PHASE_INCREMENT
        self._pulse_phase += PULSE_PHASE_INCREMENT
        v = (math.sin(self._pulse_phase) + 1) / 2

        bg_start_r, bg_start_g, bg_start_b = Theme.PULSE_START
        border_end_r, border_end_g, border_end_b = Theme.PULSE_END

        br = int(90 + v * (border_end_r - 90))
        bg = int(106 + v * (border_end_g - 106))
        bb = int(122 + v * (border_end_b - 122))
        bgb = int(bg_start_b + v * (46 - bg_start_b))
        self._pulse_bg_color = QColor(bg_start_r, bg_start_g, bgb)
        self._pulse_border_color = QColor(br, bg, bb)
        self.update()

    def paintEvent(self, event):
        if self._pulse_active and self._pulse_bg_color:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            path = QPainterPath()
            path.addRoundedRect(1.5, 1.5, self.width() - 3, self.height() - 3, 12, 12)
            p.fillPath(path, self._pulse_bg_color)
            p.setPen(QPen(self._pulse_border_color, 3))
            p.drawPath(path)
            p.end()
        else:
            super().paintEvent(event)

    def get_data(self):
        return {
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
            "notify_on_complete": self.notify_on_complete,
            "extra_input_defs": copy.deepcopy(self.extra_input_defs),
            "preferred_options_enabled": self.preferred_options_enabled,
            "preferred_options_count": self.preferred_options_count,
            "opts_panel_visible": self.btn_opts_toggle.isChecked(),
            "meta_ports_enabled": self.meta_ports_enabled,
            "history": copy.deepcopy(self._history),
        }

    def cleanup_temp_files(self):
        """Clean up temp files created by this node."""
        from v.temp_file_manager import TempFileManager
        temp_manager = TempFileManager()

        for filepath in self.ai_image_paths:
            temp_manager.cleanup_file(filepath)
        self.ai_image_paths.clear()
