"""
함수 라이브러리 다이얼로그
- FunctionLibraryDialog: 함수 목록 관리 (생성/편집/삭제/선택)
"""
import base64
import json
import uuid
import time

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QWidget, QScrollArea, QFrame, QMessageBox, QInputDialog,
    QApplication,
)
from PyQt6.QtCore import Qt

from q import t
from .function_types import FunctionDefinition
from .function_editor import FunctionEditorDialog

# ── 스타일 상수 ─────────────────────────────────────────
_S_BTN = """QPushButton { background:#333; color:#ddd; border:1px solid #555;
    border-radius:3px; padding:4px 10px; font-size:11px; }
    QPushButton:hover { background:#444; }"""

_S_BTN_ACCENT = """QPushButton { background:#e67e22; color:white; border:none;
    border-radius:3px; padding:4px 12px; font-size:11px; }
    QPushButton:hover { background:#d35400; }"""

_S_BTN_DANGER = """QPushButton { background:#333; color:#c0392b; border:1px solid #555;
    border-radius:3px; padding:4px 10px; font-size:11px; }
    QPushButton:hover { background:#442020; }"""

_S_BTN_EXPORT = """QPushButton { background:#333; color:#2ecc71; border:1px solid #555;
    border-radius:3px; padding:4px 10px; font-size:11px; }
    QPushButton:hover { background:#2a4a35; }"""

_S_BTN_ARROW = """QPushButton { background:#333; color:#aaa; border:1px solid #555;
    border-radius:3px; padding:4px 8px; font-size:11px; }
    QPushButton:hover { background:#444; color:#fff; }
    QPushButton:disabled { color:#444; border-color:#333; }"""

_S_BTN_TOP = """QPushButton { background:#e67e22; color:white; border:none;
    border-radius:4px; padding:8px 16px; font-weight:bold; }
    QPushButton:hover { background:#d35400; }"""

_S_BTN_TOP_SECONDARY = """QPushButton { background:#333; color:#ddd; border:1px solid #555;
    border-radius:4px; padding:8px 12px; font-weight:bold; }
    QPushButton:hover { background:#444; }"""


def _btn(text, style, callback) -> QPushButton:
    b = QPushButton(text)
    b.setStyleSheet(style)
    b.clicked.connect(callback)
    return b


class FunctionLibraryDialog(QDialog):
    """함수 라이브러리 (가방) — 함수 목록 관리 + 선택"""

    def __init__(self, functions: dict, on_update=None, on_select=None, parent=None):
        super().__init__(parent)
        self.functions = functions
        self.on_update = on_update
        self.on_select = on_select
        self._select_mode = on_select is not None

        self.setWindowTitle(t("function.library_title"))
        self.setMinimumSize(500, 400)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowCloseButtonHint)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setStyleSheet("QDialog { background-color: #1e1e1e; }")

        self._setup_ui()
        self._refresh_list()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # 상단: 타이틀 + 버튼
        top = QHBoxLayout()
        title = QLabel(t("function.library_title"))
        title.setStyleSheet("color: #ddd; font-size: 16px; font-weight: bold;")
        top.addWidget(title)
        top.addStretch()
        top.addWidget(_btn(t("function.new_function"), _S_BTN_TOP, self._create_new))
        top.addWidget(_btn("Import", _S_BTN_TOP_SECONDARY, self._import_function))
        layout.addLayout(top)

        # 스크롤 영역
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical { width: 6px; background: transparent; }
            QScrollBar::handle:vertical { background: #444; border-radius: 3px; }
        """)
        self._list_widget = QWidget()
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(6)
        self._list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._scroll.setWidget(self._list_widget)
        layout.addWidget(self._scroll)

    # ── 리스트 ──────────────────────────────────────────

    def _refresh_list(self):
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self.functions:
            empty = QLabel(t("function.no_functions"))
            empty.setStyleSheet("color: #666; font-size: 12px; padding: 20px;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setWordWrap(True)
            self._list_layout.addWidget(empty)
            return

        func_ids = list(self.functions.keys())
        for i, fid in enumerate(func_ids):
            self._list_layout.addWidget(
                self._create_card(self.functions[fid], i, len(func_ids))
            )

    def _create_card(self, fd: FunctionDefinition, index: int, total: int) -> QFrame:
        card = QFrame()
        card.setStyleSheet("""
            QFrame { background:#252525; border:1px solid #333; border-radius:6px; }
            QFrame:hover { border-color:#e67e22; }
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        # 헤더: 이름 + 카운트
        header = QHBoxLayout()
        name = QLabel(fd.name)
        name.setStyleSheet("color: #e67e22; font-weight: bold; font-size: 13px;")
        header.addWidget(name)
        header.addStretch()
        count = QLabel(f"{len(fd.nodes)} nodes, {len(fd.edges)} edges")
        count.setStyleSheet("color: #666; font-size: 10px;")
        header.addWidget(count)
        layout.addLayout(header)

        # 설명
        if fd.description:
            desc = QLabel(fd.description)
            desc.setStyleSheet("color: #888; font-size: 11px;")
            desc.setWordWrap(True)
            layout.addWidget(desc)

        # 버튼 행
        row = QHBoxLayout()
        row.setSpacing(6)

        if self._select_mode:
            row.addWidget(_btn(t("function.select_function"), _S_BTN_ACCENT,
                               lambda _, f=fd: self._on_select(f)))
        row.addWidget(_btn(t("function.edit_function"), _S_BTN,
                           lambda _, f=fd: self._edit(f)))
        row.addWidget(_btn(t("function.duplicate_function"), _S_BTN,
                           lambda _, f=fd: self._duplicate(f)))
        row.addWidget(_btn(t("function.delete_function"), _S_BTN_DANGER,
                           lambda _, f=fd: self._delete(f)))
        row.addWidget(_btn("Export", _S_BTN_EXPORT,
                           lambda _, f=fd: self._export_function(f)))

        row.addStretch()

        # ▲▼ 순서 버튼
        btn_up = _btn("▲", _S_BTN_ARROW,
                       lambda _, fid=fd.function_id: self._move(fid, -1))
        btn_up.setEnabled(index > 0)
        row.addWidget(btn_up)

        btn_down = _btn("▼", _S_BTN_ARROW,
                        lambda _, fid=fd.function_id: self._move(fid, 1))
        btn_down.setEnabled(index < total - 1)
        row.addWidget(btn_down)

        layout.addLayout(row)
        return card

    # ── 액션 ────────────────────────────────────────────

    def _create_new(self):
        fd = FunctionDefinition.create_default()
        FunctionEditorDialog(fd, on_save=self._on_saved, parent=self).exec()

    def _edit(self, fd: FunctionDefinition):
        FunctionEditorDialog(fd, on_save=self._on_saved, parent=self).exec()

    def _duplicate(self, fd: FunctionDefinition):
        new = FunctionDefinition.from_dict(fd.to_dict())
        new.function_id = str(uuid.uuid4())
        new.name = fd.name + " (copy)"
        new.created_at = new.updated_at = time.time()
        self.functions[new.function_id] = new
        self._refresh_list()
        self._notify_update()

    def _delete(self, fd: FunctionDefinition):
        reply = QMessageBox.question(
            self, t("function.delete_function"),
            t("function.confirm_delete", name=fd.name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.functions.pop(fd.function_id, None)
            self._refresh_list()
            self._notify_update()

    def _move(self, func_id: str, direction: int):
        keys = list(self.functions.keys())
        idx = keys.index(func_id)
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(keys):
            return
        keys[idx], keys[new_idx] = keys[new_idx], keys[idx]
        self.functions = {k: self.functions[k] for k in keys}
        self._refresh_list()
        self._notify_update()

    def _export_function(self, fd: FunctionDefinition):
        data = json.dumps(fd.to_dict(), ensure_ascii=False)
        encoded = base64.b64encode(data.encode("utf-8")).decode("ascii")
        QApplication.clipboard().setText(encoded)
        QMessageBox.information(self, "Export", f"'{fd.name}' copied to clipboard.")

    def _import_function(self):
        clipboard = QApplication.clipboard().text().strip()
        text, ok = QInputDialog.getMultiLineText(
            self, "Import", "Base64 code:", clipboard,
        )
        if not ok or not text.strip():
            return
        try:
            decoded = base64.b64decode(text.strip()).decode("utf-8")
        except Exception:
            QMessageBox.warning(self, "Import", "Invalid base64 encoding.")
            return
        try:
            data = json.loads(decoded)
        except json.JSONDecodeError as e:
            QMessageBox.warning(self, "Import", f"Invalid JSON:\n{e}")
            return
        try:
            fd = FunctionDefinition.from_dict(data)
            fd.function_id = str(uuid.uuid4())
            fd.created_at = fd.updated_at = time.time()
            self.functions[fd.function_id] = fd
            self._refresh_list()
            self._notify_update()
            QMessageBox.information(self, "Import", f"'{fd.name}' imported.")
        except (KeyError, TypeError, ValueError) as e:
            QMessageBox.warning(self, "Import", f"Invalid function data:\n{e}")

    # ── 내부 ────────────────────────────────────────────

    def _on_saved(self, fd: FunctionDefinition):
        self.functions[fd.function_id] = fd
        self._refresh_list()
        self._notify_update()

    def _on_select(self, fd: FunctionDefinition):
        if self.on_select:
            self.on_select(fd)
        self.accept()

    def _notify_update(self):
        if self.on_update:
            self.on_update(self.functions)

    def showEvent(self, event):
        super().showEvent(event)
        if self.parent():
            geo = self.parent().geometry()
            self.move(
                max(0, geo.center().x() - self.width() // 2),
                max(0, geo.center().y() - self.height() // 2),
            )
