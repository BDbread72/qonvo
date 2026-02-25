"""
자료함 노드 — 폴더 경로를 지정하여 파일을 Input/Output으로 사용
"""
import base64
import os
import shutil
import time
import uuid
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QLineEdit, QScrollArea, QGridLayout, QFrame, QMessageBox,
    QFileDialog,
)
from PyQt6.QtCore import Qt, QTimer, QFileSystemWatcher
from PyQt6.QtGui import QPixmap, QColor, QDesktopServices
from PyQt6.QtCore import QUrl

from v.theme import Theme
from .base_node import BaseNode
from .widgets import DraggableHeader, ResizeHandle

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
THUMB_SIZE = 80
GRID_COLS = 4


def _unique_path(folder: Path, name: str) -> Path:
    """폴더 내 중복 파일명 방지 — name_1, name_2 ... 접미사 추가"""
    dst = folder / name
    if not dst.exists():
        return dst
    stem = dst.stem
    suffix = dst.suffix
    n = 1
    while True:
        dst = folder / f"{stem}_{n}{suffix}"
        if not dst.exists():
            return dst
        n += 1


class RepositoryNodeWidget(QWidget, BaseNode):
    """자료함 노드 — 폴더 연결 및 파일 관리"""

    def __init__(self, node_id, on_modified=None):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.init_base_node(node_id=node_id, on_modified=on_modified)

        self.folder_path: str = ""
        self.file_paths: list[str] = []
        self.ai_response: str = ""
        self.sent = False

        self._file_watcher: QFileSystemWatcher | None = None
        self._thumbnail_cache: dict = {}
        self._rescan_timer: QTimer | None = None

        self.setMinimumSize(300, 250)
        self.resize(320, 350)
        self.setStyleSheet(f"""
            RepositoryNodeWidget {{
                background-color: {Theme.BG_TERTIARY};
                border: 3px solid #e67e22;
                border-radius: 12px;
            }}
        """)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(0)

        # ── Header ──
        self.header = DraggableHeader(self)
        self.header.setFixedHeight(36)
        self.header.setStyleSheet(f"""
            DraggableHeader {{
                background-color: {Theme.NODE_HEADER};
                border-top-left-radius: 9px;
                border-top-right-radius: 9px;
                border-bottom: 1px solid {Theme.BG_HOVER};
            }}
        """)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(12, 0, 8, 0)

        title = QLabel(f"#{self.node_id} 자료함")
        title.setStyleSheet(
            f"color: #e67e22; font-weight: bold; font-size: 12px;"
        )
        header_layout.addWidget(title)
        header_layout.addStretch()
        layout.addWidget(self.header)

        # ── Path bar ──
        path_frame = QFrame()
        path_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {Theme.BG_SECONDARY};
                border: none;
                padding: 4px;
            }}
        """)
        path_layout = QHBoxLayout(path_frame)
        path_layout.setContentsMargins(8, 4, 8, 4)
        path_layout.setSpacing(4)

        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText("폴더 경로를 선택하세요...")
        self.path_edit.setStyleSheet(f"""
            QLineEdit {{
                background-color: {Theme.BG_INPUT};
                color: {Theme.TEXT_PRIMARY};
                border: 1px solid {Theme.GRID_LINE};
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 11px;
            }}
        """)
        path_layout.addWidget(self.path_edit, 1)

        btn_browse = QPushButton("찾아보기")
        btn_browse.setFixedHeight(26)
        btn_browse.setStyleSheet(f"""
            QPushButton {{
                background-color: #e67e22;
                color: white; border: none; border-radius: 4px;
                padding: 2px 10px; font-size: 11px; font-weight: bold;
            }}
            QPushButton:hover {{ background-color: #d35400; }}
        """)
        btn_browse.clicked.connect(self._browse_folder)
        path_layout.addWidget(btn_browse)

        btn_open = QPushButton("열기")
        btn_open.setFixedHeight(26)
        btn_open.setStyleSheet(f"""
            QPushButton {{
                background-color: {Theme.BG_SECONDARY};
                color: {Theme.TEXT_SECONDARY}; border: 1px solid {Theme.GRID_LINE};
                border-radius: 4px; padding: 2px 8px; font-size: 11px;
            }}
            QPushButton:hover {{ background-color: {Theme.BG_HOVER}; }}
        """)
        btn_open.clicked.connect(self._open_folder)
        path_layout.addWidget(btn_open)

        layout.addWidget(path_frame)

        # ── Status ──
        self.status_label = QLabel("폴더를 선택하세요")
        self.status_label.setStyleSheet(
            f"color: {Theme.TEXT_TERTIARY}; font-size: 10px; padding: 4px 10px;"
        )
        layout.addWidget(self.status_label)

        # ── File grid (scrollable) ──
        self.file_scroll = QScrollArea()
        self.file_scroll.setWidgetResizable(True)
        self.file_scroll.setStyleSheet(f"""
            QScrollArea {{
                background-color: {Theme.BG_TERTIARY};
                border: none;
            }}
        """)
        self.file_widget = QWidget()
        self.file_widget.setStyleSheet("background: transparent;")
        self.file_layout = QGridLayout(self.file_widget)
        self.file_layout.setSpacing(6)
        self.file_layout.setContentsMargins(6, 6, 6, 6)
        self.file_scroll.setWidget(self.file_widget)
        layout.addWidget(self.file_scroll, 1)

        # ── Bottom bar ──
        bottom = QHBoxLayout()
        bottom.setContentsMargins(8, 4, 8, 4)

        bottom.addStretch()
        btn_refresh = QPushButton("새로고침")
        btn_refresh.setFixedHeight(24)
        btn_refresh.setStyleSheet(f"""
            QPushButton {{
                background-color: {Theme.BG_SECONDARY};
                color: {Theme.TEXT_SECONDARY}; border: 1px solid {Theme.GRID_LINE};
                border-radius: 4px; padding: 2px 10px; font-size: 10px;
            }}
            QPushButton:hover {{ background-color: {Theme.BG_HOVER}; }}
        """)
        btn_refresh.clicked.connect(self._on_refresh)
        bottom.addWidget(btn_refresh)
        layout.addLayout(bottom)

        # ── Resize handle ──
        self.resize_handle = ResizeHandle(self)

    # ── Path selection ──

    def _get_top_window(self):
        """프록시 위젯 내부에서 안전한 다이얼로그 부모 창 반환"""
        if self.proxy and self.proxy.scene() and self.proxy.scene().views():
            view = self.proxy.scene().views()[0]
            return view.window()
        return None

    def _browse_folder(self):
        parent = self._get_top_window()
        folder = QFileDialog.getExistingDirectory(
            parent, "폴더 선택", self.folder_path or ""
        )
        if not folder:
            return
        self._validate_and_set_path(folder)

    def _validate_and_set_path(self, folder_path: str):
        path = Path(folder_path)
        parent = self._get_top_window()

        if not path.exists():
            reply = QMessageBox.question(
                parent, "자료함",
                f"폴더 경로를 찾을 수 없습니다.\n{folder_path}에 새 폴더를 만들까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                path.mkdir(parents=True, exist_ok=True)
                self._set_folder_path(folder_path)
            return

        if any(path.iterdir()):
            file_count = sum(1 for f in path.iterdir() if f.is_file())
            dir_count = sum(1 for f in path.iterdir() if f.is_dir())
            reply = QMessageBox.question(
                parent, "자료함",
                f"{file_count}개의 파일과 {dir_count}개의 폴더가 존재합니다.\n경로를 지정할까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._set_folder_path(folder_path)
            return

        # Empty folder — just set it
        self._set_folder_path(folder_path)

    def _set_folder_path(self, folder_path: str, silent: bool = False):
        """Set the bound folder path. If silent=True, skip validation dialogs (for restore)."""
        self.folder_path = folder_path
        self.path_edit.setText(folder_path)
        self._setup_file_watcher(folder_path)
        self._scan_folder()
        self.notify_modified()

    def _open_folder(self):
        if not self.folder_path or not Path(self.folder_path).exists():
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.folder_path))

    # ── File watcher ──

    def _setup_file_watcher(self, folder_path: str):
        if self._file_watcher:
            dirs = self._file_watcher.directories()
            if dirs:
                self._file_watcher.removePaths(dirs)
            files = self._file_watcher.files()
            if files:
                self._file_watcher.removePaths(files)
            self._file_watcher.deleteLater()
            self._file_watcher = None

        if not folder_path or not Path(folder_path).exists():
            return

        self._file_watcher = QFileSystemWatcher([folder_path])
        self._file_watcher.directoryChanged.connect(self._on_folder_changed)

    def _on_folder_changed(self, _path: str):
        if self._rescan_timer and self._rescan_timer.isActive():
            return
        self._rescan_timer = QTimer()
        self._rescan_timer.setSingleShot(True)
        self._rescan_timer.timeout.connect(self._scan_folder)
        self._rescan_timer.start(500)

    # ── File scanning & display ──

    def _scan_folder(self):
        if not self.folder_path or not Path(self.folder_path).exists():
            self.status_label.setText("유효하지 않은 경로")
            self.file_paths = []
            self.ai_response = ""
            self._clear_file_grid()
            return

        folder = Path(self.folder_path)
        files = sorted([f for f in folder.iterdir() if f.is_file()])
        self.file_paths = [str(f) for f in files]

        file_names = [f.name for f in files]
        self.ai_response = "\n".join(file_names) if file_names else ""

        image_count = sum(1 for f in files if f.suffix.lower() in IMAGE_EXTENSIONS)
        self.status_label.setText(f"{len(files)}개 파일, {image_count}개 이미지")

        self._clear_file_grid()
        for i, fpath in enumerate(files):
            row_idx = i // GRID_COLS
            col_idx = i % GRID_COLS
            tile = self._create_file_tile(fpath)
            self.file_layout.addWidget(tile, row_idx, col_idx)

    def _clear_file_grid(self):
        while self.file_layout.count():
            item = self.file_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _create_file_tile(self, file_path: Path) -> QWidget:
        tile = QWidget()
        tile.setStyleSheet("background: transparent;")
        tile_layout = QVBoxLayout(tile)
        tile_layout.setContentsMargins(2, 2, 2, 2)
        tile_layout.setSpacing(2)

        if file_path.suffix.lower() in IMAGE_EXTENSIONS:
            pixmap = self._get_thumbnail(file_path)
            img_label = QLabel()
            img_label.setPixmap(pixmap)
            img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            img_label.setFixedSize(THUMB_SIZE, THUMB_SIZE)
            img_label.setStyleSheet(
                f"border: 1px solid {Theme.GRID_LINE}; border-radius: 4px;"
            )
            tile_layout.addWidget(img_label, alignment=Qt.AlignmentFlag.AlignCenter)
        else:
            icon_label = QLabel(file_path.suffix.upper() or "FILE")
            icon_label.setFixedSize(THUMB_SIZE, THUMB_SIZE)
            icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon_label.setStyleSheet(f"""
                background-color: {Theme.BG_SECONDARY};
                border: 1px solid {Theme.GRID_LINE};
                border-radius: 4px;
                color: {Theme.TEXT_TERTIARY};
                font-size: 10px; font-weight: bold;
            """)
            tile_layout.addWidget(icon_label, alignment=Qt.AlignmentFlag.AlignCenter)

        name_label = QLabel(file_path.name)
        name_label.setWordWrap(True)
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_label.setMaximumWidth(THUMB_SIZE + 10)
        name_label.setStyleSheet(
            f"color: {Theme.TEXT_SECONDARY}; font-size: 9px; border: none;"
        )
        tile_layout.addWidget(name_label, alignment=Qt.AlignmentFlag.AlignCenter)

        return tile

    def _get_thumbnail(self, file_path: Path) -> QPixmap:
        path_str = str(file_path)
        try:
            current_mtime = file_path.stat().st_mtime
        except OSError:
            return self._placeholder_pixmap()

        if path_str in self._thumbnail_cache:
            cached_mtime, cached_pixmap = self._thumbnail_cache[path_str]
            if cached_mtime == current_mtime:
                return cached_pixmap

        pixmap = QPixmap(path_str)
        if pixmap.isNull():
            return self._placeholder_pixmap()

        scaled = pixmap.scaled(
            THUMB_SIZE, THUMB_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._thumbnail_cache[path_str] = (current_mtime, scaled)
        return scaled

    @staticmethod
    def _placeholder_pixmap() -> QPixmap:
        pm = QPixmap(THUMB_SIZE, THUMB_SIZE)
        pm.fill(QColor("#2d2d2d"))
        return pm

    # ── Data sink: receive and save ──

    def _on_refresh(self):
        """새로고침 버튼 — 연결된 입력에서 수집 + 폴더 스캔"""
        self._collect_from_inputs()
        self._scan_folder()

    def _collect_input_data(self):
        """Override: ImageCardItem의 image_path도 처리"""
        result = super()._collect_input_data()
        if result:
            return result

        port = getattr(self, 'input_port', None)
        if port is None or not port.edges:
            return None
        source_proxy = port.edges[0].source_port.parent_proxy
        if not source_proxy:
            return None
        source_node = source_proxy.widget() if hasattr(source_proxy, 'widget') else source_proxy
        if hasattr(source_node, 'image_path') and source_node.image_path:
            return source_node.image_path
        return None

    def _collect_from_inputs(self):
        """입력 포트에 연결된 모든 소스에서 데이터 수집하여 폴더에 저장"""
        if not self.folder_path:
            return
        port = getattr(self, 'input_port', None)
        if port is None or not port.edges:
            return

        folder = Path(self.folder_path)
        if not folder.exists():
            folder.mkdir(parents=True, exist_ok=True)

        for edge in port.edges:
            source_proxy = edge.source_port.parent_proxy
            if not source_proxy:
                continue
            source_node = source_proxy.widget() if hasattr(source_proxy, 'widget') else source_proxy

            # ImageCardItem: 이미지 파일 복사
            if hasattr(source_node, 'image_path') and source_node.image_path:
                src_path = Path(source_node.image_path)
                if src_path.exists() and src_path.is_file():
                    dst_path = _unique_path(folder, src_path.name)
                    shutil.copy2(str(src_path), str(dst_path))
                continue

            # 텍스트 기반 노드
            text = None
            if hasattr(source_node, 'ai_response') and source_node.ai_response:
                text = source_node.ai_response
            elif hasattr(source_node, 'text_content') and source_node.text_content:
                text = source_node.text_content
            elif hasattr(source_node, 'body_edit') and hasattr(source_node.body_edit, 'toPlainText'):
                text = source_node.body_edit.toPlainText()
            if text:
                self._save_incoming_data(text)

    def on_signal_input(self, input_data=None):
        """Signal port receives trigger — save incoming data to folder."""
        self.sent = False
        data = input_data or self._collect_input_data()
        if data and self.folder_path:
            self._save_incoming_data(data)
            self._scan_folder()

    def _save_incoming_data(self, data):
        if not self.folder_path:
            return
        folder = Path(self.folder_path)
        if not folder.exists():
            folder.mkdir(parents=True, exist_ok=True)

        unique_id = uuid.uuid4().hex[:12]

        if isinstance(data, list):
            for item in data:
                self._save_incoming_data(item)
            return

        if isinstance(data, bytes):
            ext = self._detect_image_ext(data)
            save_path = folder / f"image_{unique_id}.{ext}"
            save_path.write_bytes(data)
            return

        if isinstance(data, str):
            # 이미지 파일 경로 → 복사
            src = Path(data)
            if src.exists() and src.is_file() and src.suffix.lower() in IMAGE_EXTENSIONS:
                dst = _unique_path(folder, src.name)
                shutil.copy2(str(src), str(dst))
                return

            # data:image/... URI
            if data.startswith("data:image"):
                header, encoded = data.split(",", 1)
                img_bytes = base64.b64decode(encoded)
                ext = "png"
                if "jpeg" in header or "jpg" in header:
                    ext = "jpg"
                elif "gif" in header:
                    ext = "gif"
                elif "webp" in header:
                    ext = "webp"
                save_path = folder / f"image_{unique_id}.{ext}"
                save_path.write_bytes(img_bytes)
                return

            # Try base64 image
            try:
                raw = base64.b64decode(data)
                if raw[:4] == b"\x89PNG" or raw[:2] == b"\xff\xd8":
                    ext = self._detect_image_ext(raw)
                    save_path = folder / f"image_{unique_id}.{ext}"
                    save_path.write_bytes(raw)
                    return
            except Exception:
                pass

            # Plain text
            save_path = folder / f"text_{unique_id}.txt"
            save_path.write_text(data, encoding="utf-8")

    @staticmethod
    def _detect_image_ext(data: bytes) -> str:
        if data[:4] == b"\x89PNG":
            return "png"
        if data[:2] == b"\xff\xd8":
            return "jpg"
        if data[:4] == b"GIF8":
            return "gif"
        if data[:4] == b"RIFF":
            return "webp"
        return "png"

    # ── Serialization ──

    def get_data(self) -> dict:
        base = self.serialize_common()
        base.update({
            "type": "repository_node",
            "folder_path": self.folder_path,
        })
        return base
