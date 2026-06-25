"""보드 스크린샷 — 캔버스를 PNG로 저장/복사. 영역 지정·커서 이름 익명화 지원.

- 캡처 모드: **보이는 화면**(view.grab) / **보드 전체**(scene.render, 모든 노드 바운딩).
- **범위 지정**: 미리보기 위에서 드래그하면 그 영역만 잘라 저장/복사(전체 해상도 기준).
- 커서 이름 가리기 ON/OFF: 타인 커서 이름을 'Guest 1·2…'로(CursorLayer.set_anonymized).

view.grab() 은 점배경+노드+씬아이템 커서를 잡고, F1/F12 오버레이·사이드패널은 MainWindow
자식이라 자동 제외된다. 저장 폴더는 settings `screenshot_dir`(없으면 %APPDATA%/Qonvo/screenshots).
"""
from __future__ import annotations

import os
import re
import time

from PyQt6.QtCore import Qt, QPoint, QRect, QRectF, QUrl
from PyQt6.QtGui import QColor, QDesktopServices, QGuiApplication, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout, QWidget,
)


def default_screenshot_dir() -> str:
    """저장 기본 폴더(설정값 우선, 없으면 %APPDATA%/Qonvo/screenshots)."""
    from v.settings import get_setting, get_app_data_path
    d = (get_setting("screenshot_dir", "") or "").strip()
    if d:
        try:
            os.makedirs(d, exist_ok=True)
            return d
        except Exception:
            pass
    base = str(get_app_data_path() / "screenshots")
    os.makedirs(base, exist_ok=True)
    return base


def _safe_name(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]+', "_", (name or "board").strip()) or "board"
    return name[:60]


def _with_anon(view, anonymize: bool, fn):
    """anonymize 면 캡처 동안만 타인 커서 이름을 Guest N 으로 바꾸고 복원."""
    layer = getattr(view, "_cursor_layer", None)
    relabeled = False
    try:
        if anonymize and layer is not None:
            layer.set_anonymized(True)
            relabeled = True
        return fn()
    finally:
        if relabeled and layer is not None:
            try:
                layer.set_anonymized(False)
            except Exception:
                pass


def capture_pixmap(view, anonymize: bool = False, whole_board: bool = False) -> QPixmap:
    """캔버스를 QPixmap 으로 캡처. whole_board 면 보드 전체(모든 노드 바운딩) 렌더."""
    def grab_visible():
        return view.grab()

    def render_whole():
        scene = view.scene()
        if scene is None:
            return view.grab()
        rect = scene.itemsBoundingRect().adjusted(-48, -48, 48, 48)
        if rect.isEmpty() or rect.width() < 1 or rect.height() < 1:
            return view.grab()
        # 너무 큰 보드는 최대 변 5000px 로 다운스케일.
        w, h = rect.width(), rect.height()
        scale = min(1.0, 5000.0 / max(w, h))
        pm = QPixmap(max(1, int(w * scale)), max(1, int(h * scale)))
        try:
            from v.theme import Theme
            pm.fill(QColor(Theme.BG_PRIMARY))
        except Exception:
            pm.fill(QColor("#1a1a1a"))
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        scene.render(painter, QRectF(pm.rect()), rect)
        painter.end()
        return pm

    return _with_anon(view, anonymize, render_whole if whole_board else grab_visible)


def _others_count(view) -> int:
    layer = getattr(view, "_cursor_layer", None)
    if layer is None:
        return 0
    try:
        return layer.others_count()
    except Exception:
        return 0


_INPUT = ("background:#1e1e1e;color:#ddd;border:1px solid #444;"
          "border-radius:6px;padding:6px 8px;")
_BTN = "padding:7px 14px;background:#3a3d42;color:#fff;border-radius:6px;"


class _PreviewLabel(QWidget):
    """미리보기 + 드래그로 영역 선택(범위 지정). 선택 좌표는 원본 픽스맵 기준으로 환산."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._src: QPixmap = QPixmap()
        self._scaled: QPixmap = QPixmap()
        self._offset = QPoint(0, 0)   # 라벨 내 이미지 좌상단
        self._scale = 1.0             # scaled → src 배율
        self._sel: QRect | None = None
        self._drag_origin: QPoint | None = None
        self.setMinimumHeight(300)
        self.setMouseTracking(True)
        self.setStyleSheet("background:#141414;border:1px solid #3a3a3a;border-radius:8px;")

    def set_source(self, pm: QPixmap):
        self._src = pm if pm is not None else QPixmap()
        self._sel = None
        self._drag_origin = None
        self._rescale()
        self.update()

    def has_selection(self) -> bool:
        return self._sel is not None and self._sel.width() > 3 and self._sel.height() > 3

    def selected_source_rect(self) -> QRect | None:
        """선택 영역을 원본 픽스맵 좌표로 환산(없으면 None)."""
        if not self.has_selection() or self._src.isNull():
            return None
        s, off, k = self._sel, self._offset, self._scale
        r = QRect(int((s.x() - off.x()) * k), int((s.y() - off.y()) * k),
                  int(s.width() * k), int(s.height() * k))
        return r.intersected(self._src.rect())

    def clear_selection(self):
        self._sel = None
        self.update()

    # ---- 내부 ----
    def _rescale(self):
        if self._src.isNull():
            self._scaled = QPixmap()
            return
        target = self.rect().adjusted(6, 6, -6, -6).size()
        self._scaled = self._src.scaled(
            target, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        sw = max(1, self._scaled.width())
        self._scale = self._src.width() / sw
        ox = (self.width() - self._scaled.width()) // 2
        oy = (self.height() - self._scaled.height()) // 2
        self._offset = QPoint(ox, oy)

    def _image_rect(self) -> QRect:
        return QRect(self._offset, self._scaled.size())

    def resizeEvent(self, e):
        self._rescale()
        # 리사이즈 시 선택 무효화(좌표 어긋남 방지)
        self._sel = None
        super().resizeEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#141414"))
        if self._scaled.isNull():
            p.setPen(QColor("#888"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "미리보기")
            p.end()
            return
        p.drawPixmap(self._offset, self._scaled)
        sel = self._sel
        if sel is not None and sel.width() > 3 and sel.height() > 3:
            # 선택 밖을 어둡게
            dark = QColor(0, 0, 0, 120)
            img = self._image_rect()
            p.fillRect(QRect(img.left(), img.top(), img.width(), sel.top() - img.top()), dark)
            p.fillRect(QRect(img.left(), sel.bottom(), img.width(), img.bottom() - sel.bottom()), dark)
            p.fillRect(QRect(img.left(), sel.top(), sel.left() - img.left(), sel.height()), dark)
            p.fillRect(QRect(sel.right(), sel.top(), img.right() - sel.right(), sel.height()), dark)
            p.setPen(QColor("#43b581"))
            p.drawRect(sel)
        p.end()

    def mousePressEvent(self, e):
        if self._scaled.isNull() or e.button() != Qt.MouseButton.LeftButton:
            return
        img = self._image_rect()
        pt = self._clamp(e.pos(), img)
        self._drag_origin = pt
        self._sel = QRect(pt, pt)
        self.update()

    def mouseMoveEvent(self, e):
        if self._drag_origin is None:
            return
        img = self._image_rect()
        pt = self._clamp(e.pos(), img)
        self._sel = QRect(self._drag_origin, pt).normalized()
        self.update()

    def mouseReleaseEvent(self, e):
        self._drag_origin = None
        if self._sel is not None and (self._sel.width() <= 3 or self._sel.height() <= 3):
            self._sel = None   # 점 클릭 = 선택 해제
        self.update()

    @staticmethod
    def _clamp(pt: QPoint, rect: QRect) -> QPoint:
        x = min(max(pt.x(), rect.left()), rect.right())
        y = min(max(pt.y(), rect.top()), rect.bottom())
        return QPoint(x, y)


class ScreenshotDialog(QDialog):
    """미리보기 + 모드/익명화/범위지정 + 저장/복사/폴더열기."""

    def __init__(self, view, board_name: str = "", parent=None):
        super().__init__(parent)
        self._view = view
        self._board_name = board_name or "board"
        self._pm: QPixmap = QPixmap()
        self._first_shown = False

        self.setWindowTitle("보드 스크린샷")
        self.setModal(True)
        self.resize(600, 580)
        self.setStyleSheet("QDialog{background:#252525;} QLabel{color:#ddd;background:transparent;}"
                           "QComboBox{" + _INPUT + "} QCheckBox{color:#ddd;}")
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(10)

        self._preview = _PreviewLabel()
        v.addWidget(self._preview, 1)

        hint = QLabel("미리보기에서 드래그하면 그 영역만 저장됩니다 (빈 곳 클릭 = 전체).")
        hint.setStyleSheet("color:#888;font-size:11px;background:transparent;")
        v.addWidget(hint)

        opt = QHBoxLayout()
        opt.addWidget(QLabel("범위"))
        self._mode = QComboBox()
        self._mode.addItem("보이는 화면", False)
        self._mode.addItem("보드 전체", True)
        self._mode.currentIndexChanged.connect(self._refresh_preview)
        opt.addWidget(self._mode)
        opt.addSpacing(12)
        self._anon = QCheckBox("타인 커서 이름 가리기 (Guest)")
        if _others_count(view) <= 0:
            self._anon.setEnabled(False)
            self._anon.setToolTip("접속 중인 다른 사용자가 없습니다.")
        self._anon.toggled.connect(self._refresh_preview)
        opt.addWidget(self._anon)
        opt.addStretch()
        clear = QPushButton("범위 해제")
        clear.setStyleSheet(_BTN)
        clear.clicked.connect(self._preview.clear_selection)
        opt.addWidget(clear)
        v.addLayout(opt)

        row = QHBoxLayout()
        row.addWidget(QLabel("폴더"))
        self._folder = QLineEdit(default_screenshot_dir())
        self._folder.setStyleSheet(f"QLineEdit{{{_INPUT}}}")
        row.addWidget(self._folder, 1)
        browse = QPushButton("…")
        browse.setFixedWidth(34)
        browse.setStyleSheet(_BTN)
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        openbtn = QPushButton("폴더 열기")
        openbtn.setStyleSheet(_BTN)
        openbtn.clicked.connect(self._open_folder)
        row.addWidget(openbtn)
        v.addLayout(row)

        self._status = QLabel("")
        self._status.setStyleSheet("color:#8ab4ff;font-size:11px;background:transparent;")
        v.addWidget(self._status)

        btns = QHBoxLayout()
        cancel = QPushButton("닫기")
        cancel.setStyleSheet(_BTN)
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        btns.addStretch()
        copy = QPushButton("클립보드 복사")
        copy.setStyleSheet(_BTN)
        copy.clicked.connect(self._copy)
        btns.addWidget(copy)
        save = QPushButton("저장")
        save.setStyleSheet("padding:7px 18px;background:#43b581;color:#fff;font-weight:bold;border-radius:6px;")
        save.clicked.connect(self._save)
        btns.addWidget(save)
        v.addLayout(btns)

    def showEvent(self, e):
        super().showEvent(e)
        if not self._first_shown:
            self._first_shown = True
            self._refresh_preview()

    # ---- 동작 ----------------------------------------------------------
    def _refresh_preview(self, *_):
        whole = bool(self._mode.currentData())
        self._pm = capture_pixmap(self._view, self._anon.isChecked(), whole)
        self._preview.set_source(self._pm)

    def _output(self) -> QPixmap:
        """선택 영역이 있으면 잘라서, 없으면 전체."""
        if self._pm.isNull():
            return self._pm
        crop = self._preview.selected_source_rect()
        if crop is not None and crop.width() > 0 and crop.height() > 0:
            return self._pm.copy(crop)
        return self._pm

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "저장 폴더 선택", self._folder.text().strip())
        if d:
            self._folder.setText(d)

    def _open_folder(self):
        folder = self._folder.text().strip() or default_screenshot_dir()
        try:
            os.makedirs(folder, exist_ok=True)
        except Exception:
            pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _copy(self):
        out = self._output()
        if out.isNull():
            return
        QGuiApplication.clipboard().setPixmap(out)
        self._status.setText("클립보드에 복사됨" + (" (선택 영역)" if self._preview.has_selection() else ""))

    def _save(self):
        out = self._output()
        if out.isNull():
            self._status.setText("캡처 실패 — 다시 시도하세요")
            return
        folder = self._folder.text().strip() or default_screenshot_dir()
        try:
            os.makedirs(folder, exist_ok=True)
        except Exception as ex:
            self._status.setText(f"폴더 오류: {ex}")
            return
        fname = f"{_safe_name(self._board_name)}_{time.strftime('%Y%m%d_%H%M%S')}.png"
        path = os.path.join(folder, fname)
        if not out.save(path, "PNG"):
            self._status.setText("저장 실패")
            return
        try:
            from v.settings import set_setting
            set_setting("screenshot_dir", folder)
        except Exception:
            pass
        self._status.setText(f"저장됨: {path}")


def open_screenshot_dialog(view, board_name: str = "", parent=None) -> None:
    if view is None:
        return
    ScreenshotDialog(view, board_name, parent).exec()
