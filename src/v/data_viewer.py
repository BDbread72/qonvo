"""
데이터 뷰어 창
- 로그 뷰어 (레벨 필터, 검색, 자동 새로고침)
- 보드 탐색기 (메타데이터, 첨부파일 미리보기)
- 파일 시스템 뷰어 (디스크 사용량)
- 설정 JSON 뷰어
"""
import copy
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import (
    QMainWindow, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QListWidget, QListWidgetItem, QTreeWidget, QTreeWidgetItem,
    QPushButton, QLabel, QCheckBox, QLineEdit, QScrollArea,
    QFormLayout, QSplitter, QHeaderView, QFileDialog,
)
from PyQt6.QtCore import Qt, QTimer, QUrl, QSize
from PyQt6.QtGui import (
    QFont, QColor, QTextCharFormat, QPixmap, QTextCursor,
    QDesktopServices, QIcon,
)

from q import t
from v.theme import Theme

# 로그 파싱 정규식
_LOG_PATTERN = re.compile(
    r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) - ([\w.]+) - (\w+) - (.+)'
)

# 레벨별 색상
_LEVEL_COLORS = {
    'DEBUG': '#888888',
    'INFO': '#8bc34a',
    'WARNING': '#ffc107',
    'ERROR': '#ff6b6b',
    'CRITICAL': '#dc3545',
}

# 노드 카테고리 표시 이름
_NODE_TYPE_LABELS = {
    'nodes': 'Chat',
    'function_nodes': 'Function',
    'sticky_notes': 'Sticky Note',
    'buttons': 'Button',
    'round_tables': 'Round Table',
    'checklists': 'Checklist',
    'repository_nodes': 'Repository',
    'texts': 'Text',
    'group_frames': 'Group Frame',
    'image_cards': 'Image Card',
    'dimensions': 'Dimension',
}

# 최대 표시 줄 수
_MAX_DISPLAY_LINES = 5000

# 공통 스타일
_WINDOW_STYLE = f"""
    QMainWindow {{ background-color: {Theme.BG_PRIMARY}; }}
    QTabWidget::pane {{
        border: 1px solid #333;
        background-color: {Theme.BG_PRIMARY};
    }}
    QTabBar::tab {{
        background-color: {Theme.BG_SECONDARY};
        color: {Theme.TEXT_SECONDARY};
        padding: 8px 20px;
        border: 1px solid #333;
        border-bottom: none;
        margin-right: 2px;
    }}
    QTabBar::tab:selected {{
        background-color: {Theme.BG_PRIMARY};
        color: #fff;
        border-bottom: 2px solid {Theme.ACCENT_PRIMARY};
    }}
"""

_BTN_STYLE = (
    f"padding: 6px 16px; background-color: {Theme.BG_SECONDARY}; "
    f"color: {Theme.TEXT_PRIMARY}; border-radius: 4px; border: 1px solid #444;"
)

_TEXTEDIT_STYLE = f"""
    QTextEdit {{
        background-color: #0d0d0d;
        color: {Theme.TEXT_PRIMARY};
        border: 1px solid #333;
        border-radius: 4px;
        padding: 8px;
    }}
"""

_LIST_STYLE = f"""
    QListWidget {{
        background-color: #0d0d0d;
        color: {Theme.TEXT_PRIMARY};
        border: 1px solid #333;
        border-radius: 4px;
        padding: 4px;
    }}
    QListWidget::item {{
        padding: 4px 8px;
    }}
    QListWidget::item:selected {{
        background-color: {Theme.ACCENT_PRIMARY};
    }}
"""

_TREE_STYLE = f"""
    QTreeWidget {{
        background-color: #0d0d0d;
        color: {Theme.TEXT_PRIMARY};
        border: 1px solid #333;
        border-radius: 4px;
        padding: 4px;
    }}
    QTreeWidget::item {{
        padding: 3px 0;
    }}
    QTreeWidget::item:selected {{
        background-color: {Theme.ACCENT_PRIMARY};
    }}
    QHeaderView::section {{
        background-color: {Theme.BG_SECONDARY};
        color: {Theme.TEXT_PRIMARY};
        padding: 4px 8px;
        border: 1px solid #333;
    }}
"""

_CHECKBOX_STYLE = f"color: {Theme.TEXT_PRIMARY}; padding: 2px 6px;"

_SEARCH_STYLE = f"""
    QLineEdit {{
        background-color: {Theme.BG_INPUT};
        color: {Theme.TEXT_PRIMARY};
        border: 1px solid #444;
        border-radius: 4px;
        padding: 4px 8px;
    }}
"""

_LABEL_STYLE = f"color: {Theme.TEXT_PRIMARY};"
_LABEL_DIM_STYLE = f"color: {Theme.TEXT_SECONDARY};"
_SECTION_STYLE = f"color: {Theme.ACCENT_PRIMARY}; font-weight: bold; font-size: 13px; margin-top: 8px;"


class DataViewerWindow(QMainWindow):
    """프로젝트 데이터 탐색 창"""

    def __init__(self, main_window=None):
        super().__init__()
        self.main_window = main_window

        # 로그 상태
        self._log_entries = []
        self._auto_refresh_timer = None
        self._log_mtime = 0.0
        self._search_debounce = None

        # 페이지네이션
        self._page_size = 200
        self._current_page = 0
        self._filtered_entries = []
        self._total_pages = 1

        # 보드 상태
        self._current_board_path = None
        self._current_attachments = []
        self._selected_attachment = None

        self.setWindowTitle(t("data_viewer.title"))
        self.setMinimumSize(900, 600)
        self.resize(1100, 700)
        self.setStyleSheet(_WINDOW_STYLE)

        tabs = QTabWidget()
        self.setCentralWidget(tabs)

        tabs.addTab(self._build_logs_tab(), t("data_viewer.tab_logs"))
        tabs.addTab(self._build_boards_tab(), t("data_viewer.tab_boards"))
        tabs.addTab(self._build_files_tab(), t("data_viewer.tab_files"))
        tabs.addTab(self._build_settings_tab(), t("data_viewer.tab_settings"))

        # 초기 데이터 로드
        QTimer.singleShot(100, self._initial_load)

    def _initial_load(self):
        """창 표시 후 초기 데이터 로드"""
        self._refresh_logs()
        self._refresh_board_list()
        self._refresh_file_tree()
        self._refresh_settings()

    # ================================================================
    # Tab 1: 로그
    # ================================================================

    def _build_logs_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(8, 8, 8, 8)

        # 툴바
        toolbar = QHBoxLayout()

        self._cb_debug = QCheckBox("DEBUG")
        self._cb_debug.setStyleSheet(_CHECKBOX_STYLE)
        self._cb_debug.setChecked(False)
        self._cb_debug.toggled.connect(self._apply_filters)
        toolbar.addWidget(self._cb_debug)

        self._cb_info = QCheckBox("INFO")
        self._cb_info.setStyleSheet(_CHECKBOX_STYLE)
        self._cb_info.setChecked(True)
        self._cb_info.toggled.connect(self._apply_filters)
        toolbar.addWidget(self._cb_info)

        self._cb_warning = QCheckBox("WARNING")
        self._cb_warning.setStyleSheet(_CHECKBOX_STYLE)
        self._cb_warning.setChecked(True)
        self._cb_warning.toggled.connect(self._apply_filters)
        toolbar.addWidget(self._cb_warning)

        self._cb_error = QCheckBox("ERROR")
        self._cb_error.setStyleSheet(_CHECKBOX_STYLE)
        self._cb_error.setChecked(True)
        self._cb_error.toggled.connect(self._apply_filters)
        toolbar.addWidget(self._cb_error)

        self._log_search = QLineEdit()
        self._log_search.setPlaceholderText(t("data_viewer.search_placeholder"))
        self._log_search.setStyleSheet(_SEARCH_STYLE)
        self._log_search.setMaximumWidth(200)
        self._log_search.textChanged.connect(self._on_search_changed)
        toolbar.addWidget(self._log_search)

        btn_refresh = QPushButton(t("button.refresh"))
        btn_refresh.setStyleSheet(_BTN_STYLE)
        btn_refresh.clicked.connect(self._refresh_logs)
        toolbar.addWidget(btn_refresh)

        self._cb_auto = QCheckBox(t("data_viewer.auto_refresh"))
        self._cb_auto.setStyleSheet(_CHECKBOX_STYLE)
        self._cb_auto.toggled.connect(self._toggle_auto_refresh)
        toolbar.addWidget(self._cb_auto)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        # 로그 본문
        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setFont(QFont("Consolas", 10))
        self._log_view.setStyleSheet(_TEXTEDIT_STYLE)
        layout.addWidget(self._log_view)

        # 상태바 + 페이지네이션
        status = QHBoxLayout()
        self._log_line_count = QLabel()
        self._log_line_count.setStyleSheet(_LABEL_DIM_STYLE)
        status.addWidget(self._log_line_count)

        self._log_file_size = QLabel()
        self._log_file_size.setStyleSheet(_LABEL_DIM_STYLE)
        status.addWidget(self._log_file_size)

        status.addStretch()

        _page_btn_style = (
            f"padding: 4px 10px; background-color: {Theme.BG_SECONDARY}; "
            f"color: {Theme.TEXT_PRIMARY}; border-radius: 3px; border: 1px solid #444; "
            f"min-width: 28px;"
        )

        btn_first = QPushButton("«")
        btn_first.setStyleSheet(_page_btn_style)
        btn_first.setToolTip("첫 페이지")
        btn_first.clicked.connect(self._go_first_page)
        status.addWidget(btn_first)

        btn_prev = QPushButton("‹")
        btn_prev.setStyleSheet(_page_btn_style)
        btn_prev.setToolTip("이전 페이지")
        btn_prev.clicked.connect(self._go_prev_page)
        status.addWidget(btn_prev)

        self._page_label = QLabel("1 / 1")
        self._page_label.setStyleSheet(
            f"color: {Theme.TEXT_PRIMARY}; padding: 0 8px; min-width: 60px;"
        )
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status.addWidget(self._page_label)

        btn_next = QPushButton("›")
        btn_next.setStyleSheet(_page_btn_style)
        btn_next.setToolTip("다음 페이지")
        btn_next.clicked.connect(self._go_next_page)
        status.addWidget(btn_next)

        btn_last = QPushButton("»")
        btn_last.setStyleSheet(_page_btn_style)
        btn_last.setToolTip("마지막 페이지")
        btn_last.clicked.connect(self._go_last_page)
        status.addWidget(btn_last)

        layout.addLayout(status)

        return widget

    def _load_log_files(self):
        """모든 로그 파일 로드 (백업 포함, 오래된 순서)"""
        from v.settings import get_app_data_path
        log_dir = get_app_data_path() / "logs"

        self._log_entries = []
        total_size = 0

        # 백업 로그 (오래된 것부터)
        for i in range(3, 0, -1):
            path = log_dir / f"qonvo.log.{i}"
            if path.exists():
                total_size += path.stat().st_size
                self._parse_log_file(path)

        # 현재 로그
        current = log_dir / "qonvo.log"
        if current.exists():
            stat = current.stat()
            total_size += stat.st_size
            self._log_mtime = stat.st_mtime
            self._parse_log_file(current)

        self._log_file_size.setText(
            t("data_viewer.log_file_size", size=_format_size(total_size))
        )

    def _parse_log_file(self, path: Path):
        """로그 파일 파싱 → 구조화된 엔트리"""
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    line = line.rstrip('\n\r')
                    m = _LOG_PATTERN.match(line)
                    if m:
                        self._log_entries.append({
                            'timestamp': m.group(1),
                            'logger': m.group(2),
                            'level': m.group(3),
                            'message': m.group(4),
                        })
                    elif self._log_entries:
                        # 연속 줄 (traceback 등) → 마지막 엔트리에 추가
                        self._log_entries[-1]['message'] += '\n' + line
        except Exception:
            pass

    def _refresh_logs(self):
        """로그 재로드 + 필터 적용"""
        self._load_log_files()
        self._apply_filters()

    def _apply_filters(self):
        """레벨 + 검색 필터 적용 후 페이지네이션 렌더링"""
        # 활성 레벨 수집
        active_levels = set()
        if self._cb_debug.isChecked():
            active_levels.add('DEBUG')
        if self._cb_info.isChecked():
            active_levels.add('INFO')
        if self._cb_warning.isChecked():
            active_levels.add('WARNING')
        if self._cb_error.isChecked():
            active_levels.update(('ERROR', 'CRITICAL'))

        search_text = self._log_search.text().strip().lower()

        # 필터링
        filtered = []
        for entry in self._log_entries:
            if entry['level'] not in active_levels:
                continue
            if search_text:
                haystack = (
                    entry['timestamp'] + entry['logger'] +
                    entry['level'] + entry['message']
                ).lower()
                if search_text not in haystack:
                    continue
            filtered.append(entry)

        # 최대 줄 수 제한
        if len(filtered) > _MAX_DISPLAY_LINES:
            filtered = filtered[-_MAX_DISPLAY_LINES:]

        self._filtered_entries = filtered
        self._total_pages = max(1, (len(filtered) + self._page_size - 1) // self._page_size)

        # 마지막 페이지로 이동 (최신 로그 표시)
        self._current_page = self._total_pages - 1
        self._render_page()

    def _render_page(self):
        """현재 페이지의 로그만 렌더링"""
        start = self._current_page * self._page_size
        end = start + self._page_size
        page_entries = self._filtered_entries[start:end]

        self._log_view.clear()
        cursor = self._log_view.textCursor()

        fmt_time = QTextCharFormat()
        fmt_time.setForeground(QColor(Theme.TEXT_TERTIARY))

        fmt_logger = QTextCharFormat()
        fmt_logger.setForeground(QColor("#6a9fb5"))

        for entry in page_entries:
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertText(f"[{entry['timestamp']}] ", fmt_time)
            cursor.insertText(f"{entry['logger']} ", fmt_logger)

            fmt_msg = QTextCharFormat()
            color = _LEVEL_COLORS.get(entry['level'], Theme.TEXT_PRIMARY)
            fmt_msg.setForeground(QColor(color))
            cursor.insertText(
                f"{entry['level']} - {entry['message']}\n", fmt_msg
            )

        self._log_view.moveCursor(QTextCursor.MoveOperation.Start)

        total = len(self._filtered_entries)
        self._log_line_count.setText(
            t("data_viewer.log_lines", count=str(total))
        )
        self._page_label.setText(
            f"{self._current_page + 1} / {self._total_pages}"
        )

    def _go_first_page(self):
        if self._current_page != 0:
            self._current_page = 0
            self._render_page()

    def _go_prev_page(self):
        if self._current_page > 0:
            self._current_page -= 1
            self._render_page()

    def _go_next_page(self):
        if self._current_page < self._total_pages - 1:
            self._current_page += 1
            self._render_page()

    def _go_last_page(self):
        last = self._total_pages - 1
        if self._current_page != last:
            self._current_page = last
            self._render_page()

    def _on_search_changed(self, text):
        """검색 입력 디바운스 (300ms)"""
        if self._search_debounce:
            self._search_debounce.stop()
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.timeout.connect(self._apply_filters)
        self._search_debounce.start(300)

    def _toggle_auto_refresh(self, checked):
        """자동 새로고침 토글"""
        if checked:
            self._auto_refresh_timer = QTimer(self)
            self._auto_refresh_timer.timeout.connect(self._on_auto_refresh_tick)
            self._auto_refresh_timer.start(2000)
        else:
            if self._auto_refresh_timer:
                self._auto_refresh_timer.stop()
                self._auto_refresh_timer = None

    def _on_auto_refresh_tick(self):
        """자동 새로고침 — mtime 변경 시에만 재로드"""
        from v.settings import get_app_data_path
        log_path = get_app_data_path() / "logs" / "qonvo.log"
        if log_path.exists():
            mtime = log_path.stat().st_mtime
            if mtime != self._log_mtime:
                self._refresh_logs()

    # ================================================================
    # Tab 2: 보드
    # ================================================================

    def _build_boards_tab(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(8, 8, 8, 8)

        # 좌측: 보드 목록
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        lbl = QLabel(t("data_viewer.board_list"))
        lbl.setStyleSheet(_SECTION_STYLE)
        left_layout.addWidget(lbl)

        self._board_list = QListWidget()
        self._board_list.setStyleSheet(_LIST_STYLE)
        self._board_list.currentItemChanged.connect(self._on_board_selected)
        left_layout.addWidget(self._board_list)

        btn_refresh = QPushButton(t("button.refresh"))
        btn_refresh.setStyleSheet(_BTN_STYLE)
        btn_refresh.clicked.connect(self._refresh_board_list)
        left_layout.addWidget(btn_refresh)

        # 우측: 보드 상세
        right = QScrollArea()
        right.setWidgetResizable(True)
        right.setStyleSheet(
            f"QScrollArea {{ background-color: {Theme.BG_PRIMARY}; border: none; }}"
        )

        self._board_detail = QWidget()
        self._board_detail_layout = QVBoxLayout(self._board_detail)
        self._board_detail_layout.setContentsMargins(12, 8, 12, 8)
        self._board_detail_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # 플레이스홀더
        self._board_placeholder = QLabel(t("data_viewer.no_board_selected"))
        self._board_placeholder.setStyleSheet(_LABEL_DIM_STYLE)
        self._board_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._board_detail_layout.addWidget(self._board_placeholder)

        right.setWidget(self._board_detail)

        # 스플리터
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([280, 700])
        splitter.setStyleSheet(
            f"QSplitter::handle {{ background-color: #333; width: 2px; }}"
        )
        layout.addWidget(splitter)

        return widget

    def _refresh_board_list(self):
        """보드 목록 갱신"""
        from v.board import BoardManager
        self._board_list.clear()
        for name in BoardManager.list_boards():
            self._board_list.addItem(name)

    def _on_board_selected(self, current, previous):
        """보드 선택 시 상세 정보 표시"""
        if not current:
            return
        self._show_board_details(current.text())

    def _show_board_details(self, name: str):
        """보드 상세 정보 표시"""
        from v.board import BoardManager, _is_qonvo_binary, _parse_toc, _read_qonvo_entry

        filepath = BoardManager.get_boards_dir() / f"{name}.qonvo"
        if not filepath.exists():
            return

        self._current_board_path = filepath

        # 기존 위젯 제거
        _clear_layout(self._board_detail_layout)

        # 파일 메타
        stat = filepath.stat()
        file_size = stat.st_size
        modified = datetime.fromtimestamp(stat.st_mtime).strftime(
            '%Y-%m-%d %H:%M:%S'
        )

        # 섹션: 메타데이터
        self._add_section(t("data_viewer.board_metadata"))

        meta_form = QFormLayout()
        meta_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        meta_form.addRow(
            self._dim_label("Name:"),
            self._val_label(name),
        )
        meta_form.addRow(
            self._dim_label("File Size:"),
            self._val_label(_format_size(file_size)),
        )
        meta_form.addRow(
            self._dim_label("Modified:"),
            self._val_label(modified),
        )

        # board.json 파싱
        toc = []
        if _is_qonvo_binary(filepath):
            try:
                with open(filepath, 'rb') as f:
                    toc = _parse_toc(f)
            except Exception:
                pass

        board_json_bytes = _read_qonvo_entry(filepath, 'board.json')
        if board_json_bytes:
            try:
                data = json.loads(board_json_bytes)
            except json.JSONDecodeError:
                data = {}

            version = data.get('version', '?')
            saved_at = data.get('saved_at', '?')
            board_type = data.get('type', '?')

            meta_form.addRow(
                self._dim_label("Version:"), self._val_label(version)
            )
            meta_form.addRow(
                self._dim_label("Saved At:"), self._val_label(saved_at)
            )
            meta_form.addRow(
                self._dim_label("Type:"), self._val_label(board_type)
            )

            meta_widget = QWidget()
            meta_widget.setLayout(meta_form)
            self._board_detail_layout.addWidget(meta_widget)

            # 섹션: 노드 수
            self._add_section(t("data_viewer.node_counts"))
            for category, label in _NODE_TYPE_LABELS.items():
                items = data.get(category, [])
                if items:
                    row = QLabel(f"  {label}: {len(items)}")
                    row.setStyleSheet(_LABEL_STYLE)
                    self._board_detail_layout.addWidget(row)

            # 차원 내부 오브젝트 수
            dimensions = data.get('dimensions', [])
            for dim in dimensions:
                bd = dim.get('board_data', {})
                title = dim.get('title', 'Dimension')
                inner_count = sum(
                    len(bd.get(cat, []))
                    for cat in _NODE_TYPE_LABELS
                )
                if inner_count > 0:
                    row = QLabel(f"    └ {title}: {inner_count}개 내부 오브젝트")
                    row.setStyleSheet(f"color: #9b59b6;")
                    self._board_detail_layout.addWidget(row)

            # 엣지 수
            edges = data.get('edges', [])
            self._add_section(t("data_viewer.edge_count"))
            edge_lbl = QLabel(f"  {len(edges)}")
            edge_lbl.setStyleSheet(_LABEL_STYLE)
            self._board_detail_layout.addWidget(edge_lbl)
        else:
            meta_widget = QWidget()
            meta_widget.setLayout(meta_form)
            self._board_detail_layout.addWidget(meta_widget)

        # 섹션: 첨부파일
        attachments = [
            (entry_name, size)
            for entry_name, offset, size, flags in toc
            if entry_name != 'board.json'
        ]

        self._current_attachments = attachments

        # 이미지/비이미지 분류
        _img_exts = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'}
        image_attachments = [
            (n, s) for n, s in attachments
            if Path(n).suffix.lower() in _img_exts
        ]
        other_attachments = [
            (n, s) for n, s in attachments
            if Path(n).suffix.lower() not in _img_exts
        ]

        # ── 이미지 갤러리 ──
        if image_attachments:
            self._add_section(
                t("data_viewer.image_gallery") + f" ({len(image_attachments)})"
            )

            from PyQt6.QtWidgets import QListView

            gallery = QListWidget()
            gallery.setViewMode(QListView.ViewMode.IconMode)
            gallery.setIconSize(QSize(120, 120))
            gallery.setResizeMode(QListView.ResizeMode.Adjust)
            gallery.setWrapping(True)
            gallery.setSpacing(6)
            gallery.setMinimumHeight(160)
            gallery.setMaximumHeight(420)
            gallery.setStyleSheet(
                f"QListWidget {{ background-color: #111; border: 1px solid #333; "
                f"border-radius: 4px; padding: 4px; }}"
                f"QListWidget::item {{ color: {Theme.TEXT_SECONDARY}; "
                f"padding: 4px; border-radius: 4px; }}"
                f"QListWidget::item:selected {{ background-color: {Theme.ACCENT_PRIMARY}; }}"
            )

            _MAX_GALLERY = 60
            for entry_name, size in image_attachments[:_MAX_GALLERY]:
                raw = _read_qonvo_entry(self._current_board_path, entry_name)
                if not raw:
                    continue
                pixmap = QPixmap()
                pixmap.loadFromData(raw)
                if pixmap.isNull():
                    continue
                thumb = pixmap.scaled(
                    120, 120,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                stem = Path(entry_name).stem
                display_name = stem[:14] + "..." if len(stem) > 14 else stem
                item = QListWidgetItem(QIcon(thumb), display_name)
                item.setData(Qt.ItemDataRole.UserRole, entry_name)
                item.setSizeHint(QSize(136, 152))
                gallery.addItem(item)

            if len(image_attachments) > _MAX_GALLERY:
                overflow = QLabel(
                    f"  ... +{len(image_attachments) - _MAX_GALLERY}개 이미지"
                )
                overflow.setStyleSheet(_LABEL_DIM_STYLE)
                self._board_detail_layout.addWidget(overflow)

            gallery.itemClicked.connect(self._on_gallery_item_clicked)
            self._board_detail_layout.addWidget(gallery)

        # ── 미리보기 ──
        self._preview_label = QLabel()
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setMinimumHeight(80)
        self._preview_label.setStyleSheet(
            f"background-color: #111; border: 1px solid #333; "
            f"border-radius: 4px; padding: 8px;"
        )
        if image_attachments:
            self._board_detail_layout.addWidget(self._preview_label)

        self._preview_info = QLabel()
        self._preview_info.setStyleSheet(_LABEL_DIM_STYLE)
        self._preview_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if image_attachments:
            self._board_detail_layout.addWidget(self._preview_info)

        # ── 기타 첨부파일 목록 ──
        if other_attachments:
            self._add_section(
                t("data_viewer.attachments") + f" ({len(other_attachments)})"
            )
            self._attachment_list = QListWidget()
            self._attachment_list.setStyleSheet(_LIST_STYLE)
            self._attachment_list.setMaximumHeight(160)
            for entry_name, size in other_attachments:
                display = f"{entry_name}  ({_format_size(size)})"
                item = QListWidgetItem(display)
                item.setData(Qt.ItemDataRole.UserRole, entry_name)
                self._attachment_list.addItem(item)
            self._attachment_list.itemClicked.connect(
                self._on_attachment_clicked
            )
            self._board_detail_layout.addWidget(self._attachment_list)

        # ── 다운로드 ──
        if attachments:
            dl_row = QHBoxLayout()
            self._btn_download_one = QPushButton(t("data_viewer.download_file"))
            self._btn_download_one.setStyleSheet(_BTN_STYLE)
            self._btn_download_one.setEnabled(False)
            self._btn_download_one.clicked.connect(self._download_selected_attachment)
            dl_row.addWidget(self._btn_download_one)

            btn_download_all = QPushButton(t("data_viewer.download_all_zip"))
            btn_download_all.setStyleSheet(_BTN_STYLE)
            btn_download_all.clicked.connect(self._download_all_attachments)
            dl_row.addWidget(btn_download_all)

            dl_row.addStretch()
            self._board_detail_layout.addLayout(dl_row)

        self._board_detail_layout.addStretch()

    def _on_gallery_item_clicked(self, item):
        """갤러리 썸네일 클릭 → 큰 미리보기"""
        attachment_name = item.data(Qt.ItemDataRole.UserRole)
        if not attachment_name or not self._current_board_path:
            return

        self._selected_attachment = attachment_name
        if hasattr(self, '_btn_download_one'):
            self._btn_download_one.setEnabled(True)

        from v.board import _read_qonvo_entry
        raw = _read_qonvo_entry(self._current_board_path, attachment_name)
        if raw:
            pixmap = QPixmap()
            pixmap.loadFromData(raw)
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    480, 480,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._preview_label.setPixmap(scaled)
                self._preview_info.setText(
                    f"{pixmap.width()} x {pixmap.height()} | "
                    f"{_format_size(len(raw))} | {Path(attachment_name).name}"
                )

    def _on_attachment_clicked(self, item):
        """첨부파일 클릭 → 다운로드 버튼 활성화"""
        if not self._current_board_path:
            return

        attachment_name = item.data(Qt.ItemDataRole.UserRole)
        if not attachment_name:
            return

        self._selected_attachment = attachment_name
        if hasattr(self, '_btn_download_one'):
            self._btn_download_one.setEnabled(True)

    def _download_selected_attachment(self):
        """선택된 첨부파일 개별 다운로드"""
        if not self._current_board_path or not hasattr(self, '_selected_attachment'):
            return

        attachment_name = self._selected_attachment
        filename = Path(attachment_name).name

        save_path, _ = QFileDialog.getSaveFileName(
            self, t("data_viewer.download_file"), filename
        )
        if not save_path:
            return

        from v.board import _read_qonvo_entry
        raw = _read_qonvo_entry(self._current_board_path, attachment_name)
        if raw:
            Path(save_path).write_bytes(raw)

    def _download_all_attachments(self):
        """모든 첨부파일을 ZIP으로 다운로드"""
        if not self._current_board_path or not self._current_attachments:
            return

        board_name = self._current_board_path.stem
        default_name = f"{board_name}_attachments.zip"

        save_path, _ = QFileDialog.getSaveFileName(
            self, t("data_viewer.download_all_zip"),
            default_name, "ZIP (*.zip)"
        )
        if not save_path:
            return

        from v.board import _read_qonvo_entry
        with zipfile.ZipFile(save_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for entry_name, _ in self._current_attachments:
                raw = _read_qonvo_entry(self._current_board_path, entry_name)
                if raw:
                    # 아카이브 내 경로 그대로 유지
                    zf.writestr(entry_name, raw)

    def _add_section(self, title: str):
        """섹션 헤더 추가"""
        lbl = QLabel(title)
        lbl.setStyleSheet(_SECTION_STYLE)
        self._board_detail_layout.addWidget(lbl)

    @staticmethod
    def _dim_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(_LABEL_DIM_STYLE)
        return lbl

    @staticmethod
    def _val_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(_LABEL_STYLE)
        return lbl

    # ================================================================
    # Tab 3: 파일
    # ================================================================

    def _build_files_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(8, 8, 8, 8)

        # 툴바
        toolbar = QHBoxLayout()
        btn_refresh = QPushButton(t("button.refresh"))
        btn_refresh.setStyleSheet(_BTN_STYLE)
        btn_refresh.clicked.connect(self._refresh_file_tree)
        toolbar.addWidget(btn_refresh)

        self._disk_usage_label = QLabel()
        self._disk_usage_label.setStyleSheet(_LABEL_DIM_STYLE)
        toolbar.addWidget(self._disk_usage_label)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        # 트리
        self._file_tree = QTreeWidget()
        self._file_tree.setHeaderLabels(["Name", "Size", "Modified"])
        self._file_tree.setStyleSheet(_TREE_STYLE)
        self._file_tree.setColumnWidth(0, 400)
        self._file_tree.setColumnWidth(1, 100)
        header = self._file_tree.header()
        header.setStretchLastSection(True)
        self._file_tree.itemDoubleClicked.connect(self._on_file_double_clicked)
        layout.addWidget(self._file_tree)

        return widget

    def _refresh_file_tree(self):
        """파일 시스템 트리 갱신"""
        from v.settings import get_app_data_path
        from v.board import BoardManager

        self._file_tree.clear()
        app_dir = get_app_data_path()
        total_size = 0

        # boards/
        boards_dir = BoardManager.get_boards_dir()
        boards_item = QTreeWidgetItem([t("data_viewer.boards_dir"), "", ""])
        boards_item.setForeground(0, QColor(Theme.ACCENT_PRIMARY))

        for f in sorted(boards_dir.glob("*.qonvo")):
            try:
                stat = f.stat()
                size = stat.st_size
                total_size += size
                modified = datetime.fromtimestamp(stat.st_mtime).strftime(
                    '%Y-%m-%d %H:%M'
                )
                child = QTreeWidgetItem([f.name, _format_size(size), modified])
                child.setData(0, Qt.ItemDataRole.UserRole, str(f))
                boards_item.addChild(child)
            except OSError:
                pass

        self._file_tree.addTopLevelItem(boards_item)
        boards_item.setExpanded(True)

        # boards/.temp/
        temp_dir = boards_dir / ".temp"
        if temp_dir.exists():
            temp_item = QTreeWidgetItem([t("data_viewer.temp_dir"), "", ""])
            temp_item.setForeground(0, QColor(Theme.ACCENT_WARNING))

            try:
                for d in sorted(temp_dir.iterdir()):
                    if d.is_dir():
                        dir_size = 0
                        file_count = 0
                        for fp in d.rglob("*"):
                            if fp.is_file():
                                try:
                                    dir_size += fp.stat().st_size
                                    file_count += 1
                                except OSError:
                                    pass
                        total_size += dir_size
                        child = QTreeWidgetItem([
                            f"{d.name} ({file_count} files)",
                            _format_size(dir_size),
                            "",
                        ])
                        child.setData(0, Qt.ItemDataRole.UserRole, str(d))
                        temp_item.addChild(child)
            except OSError:
                pass

            self._file_tree.addTopLevelItem(temp_item)

        # logs/
        log_dir = app_dir / "logs"
        if log_dir.exists():
            logs_item = QTreeWidgetItem([t("data_viewer.logs_dir"), "", ""])
            logs_item.setForeground(0, QColor(Theme.ACCENT_SUCCESS))

            try:
                for f in sorted(log_dir.iterdir()):
                    if f.is_file():
                        stat = f.stat()
                        size = stat.st_size
                        total_size += size
                        modified = datetime.fromtimestamp(
                            stat.st_mtime
                        ).strftime('%Y-%m-%d %H:%M')
                        child = QTreeWidgetItem([
                            f.name, _format_size(size), modified
                        ])
                        child.setData(0, Qt.ItemDataRole.UserRole, str(f))
                        logs_item.addChild(child)
            except OSError:
                pass

            self._file_tree.addTopLevelItem(logs_item)
            logs_item.setExpanded(True)

        # settings.json
        settings_path = app_dir / "settings.json"
        if settings_path.exists():
            try:
                stat = settings_path.stat()
                size = stat.st_size
                total_size += size
                modified = datetime.fromtimestamp(stat.st_mtime).strftime(
                    '%Y-%m-%d %H:%M'
                )
                settings_item = QTreeWidgetItem([
                    "settings.json", _format_size(size), modified
                ])
                settings_item.setData(
                    0, Qt.ItemDataRole.UserRole, str(settings_path)
                )
                self._file_tree.addTopLevelItem(settings_item)
            except OSError:
                pass

        self._disk_usage_label.setText(
            t("data_viewer.total_disk_usage", size=_format_size(total_size))
        )

    def _on_file_double_clicked(self, item, column):
        """더블클릭 → OS 파일 탐색기에서 열기"""
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path:
            p = Path(path)
            target = p.parent if p.is_file() else p
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    # ================================================================
    # Tab 4: 설정
    # ================================================================

    def _build_settings_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(8, 8, 8, 8)

        toolbar = QHBoxLayout()
        btn_refresh = QPushButton(t("button.refresh"))
        btn_refresh.setStyleSheet(_BTN_STYLE)
        btn_refresh.clicked.connect(self._refresh_settings)
        toolbar.addWidget(btn_refresh)

        btn_copy = QPushButton(t("button.copy"))
        btn_copy.setStyleSheet(_BTN_STYLE)
        btn_copy.clicked.connect(self._copy_settings)
        toolbar.addWidget(btn_copy)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        self._settings_view = QTextEdit()
        self._settings_view.setReadOnly(True)
        self._settings_view.setFont(QFont("Consolas", 11))
        self._settings_view.setStyleSheet(f"""
            QTextEdit {{
                background-color: #0d0d0d;
                color: #8bc34a;
                border: 1px solid #333;
                border-radius: 4px;
                padding: 8px;
            }}
        """)
        layout.addWidget(self._settings_view)

        return widget

    def _refresh_settings(self):
        """설정 JSON 로드 + 마스킹 + 표시"""
        from v.settings import get_app_data_path

        settings_path = get_app_data_path() / "settings.json"
        if not settings_path.exists():
            self._settings_view.setPlainText(t("data_viewer.no_settings"))
            return

        try:
            with open(settings_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            data = self._mask_sensitive(data)
            text = json.dumps(data, indent=2, ensure_ascii=False)
            self._settings_view.setPlainText(text)
        except Exception as e:
            self._settings_view.setPlainText(f"Error: {e}")

    @staticmethod
    def _mask_sensitive(data: dict) -> dict:
        """API 키 마스킹"""
        masked = copy.deepcopy(data)

        if 'api_key_encrypted' in masked:
            masked['api_key_encrypted'] = '********'
        if 'api_key' in masked:
            masked['api_key'] = '********'
        if 'api_keys_encrypted' in masked:
            masked['api_keys_encrypted'] = [
                '********' for _ in masked['api_keys_encrypted']
            ]

        return masked

    def _copy_settings(self):
        """설정 JSON 클립보드 복사"""
        from PyQt6.QtWidgets import QApplication
        text = self._settings_view.toPlainText()
        if text:
            QApplication.clipboard().setText(text)

    # ================================================================
    # Lifecycle
    # ================================================================

    def closeEvent(self, event):
        """타이머 정지 + 숨기기"""
        if self._auto_refresh_timer:
            self._auto_refresh_timer.stop()
            self._auto_refresh_timer = None
        self.hide()
        event.ignore()


# ================================================================
# Helpers
# ================================================================

def _format_size(size_bytes: int) -> str:
    """바이트 → 사람이 읽기 쉬운 크기"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def _clear_layout(layout):
    """레이아웃의 모든 자식 위젯 제거"""
    while layout.count():
        child = layout.takeAt(0)
        if child.widget():
            child.widget().deleteLater()
        elif child.layout():
            _clear_layout(child.layout())
