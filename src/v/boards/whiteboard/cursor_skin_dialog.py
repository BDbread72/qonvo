"""커서 스킨 디자인 다이얼로그 — Dynamic Cursor (클라 UI, 3단계).

마인크래프트 스킨 모델: 내 커서를 **만들어**(PNG 업로드 또는 간단 에디터) 서버에 올리면
계정(merri username)에 귀속돼 어디서 접속하든 따라오고, 같은 기능을 가진 다른 사람에게
내 커서로 보인다(상대는 presence 해시로 받아 캐시).

**역할 세트(진짜 커서처럼)**: 기본(화살표)/포인터(손, 노드 위)/텍스트(I-beam, 입력 중) 3종을
각각 디자인한다. 적용 시 가로 3칸 스프라이트 시트(192×64) 한 장으로 합쳐 업로드 →
서버는 PNG 1장만 알면 되고(phase 1 무변경), 클라가 칸을 잘라 상태별로 쓴다.
포인터/텍스트를 안 만지면 기본과 동일(=단일 스킨).

- 업로드: 임의 PNG → 현재 역할 칸에(큰 건 64px 축소).
- 에디터: 모양(화살표/원/별/하트/다이아) + 색 → 현재 역할 칸.
- 적용: 로컬에 시트 저장(재업로드 소스) + 접속 중이면 즉시 서버 업로드 + 내 포인터 반영.
- 기본으로: 서버 스킨 삭제 + 로컬 삭제 + 포인터 복귀.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QBuffer, QByteArray, QRectF, QPointF
from PyQt6.QtGui import (QPixmap, QPainter, QColor, QPolygonF, QPainterPath, QPen)
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QFileDialog, QColorDialog, QFrame, QButtonGroup,
)

from v.logger import get_logger

logger = get_logger("qonvo.cursor_skin")

_CELL = 64   # 시트 한 칸 크기(px)
_ROLES = [("default", "기본"), ("point", "포인터"), ("text", "텍스트")]
_ROLE_KEYS = [k for k, _ in _ROLES]

_SHAPES = [
    ("화살표", "arrow"), ("원", "circle"), ("별", "star"),
    ("하트", "heart"), ("다이아", "diamond"),
]

_ARROW = QPolygonF([
    QPointF(3, 3), QPointF(3, 50), QPointF(15, 39), QPointF(23, 57),
    QPointF(31, 53), QPointF(22, 36), QPointF(37, 35),
])


def local_skin_path():
    """내 커서 스킨 시트(재업로드 소스) 로컬 경로."""
    from v.settings import get_app_data_path
    return get_app_data_path() / "my_cursor_skin.png"


def feature_icon_path() -> str:
    """번들된 Dynamic Cursor 기능 아이콘(icons/qonvo_cursor.png) 경로. 없으면 ""."""
    import sys
    from pathlib import Path
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(getattr(sys, "_MEIPASS", ".")) / "icons" / "qonvo_cursor.png")
    candidates.append(Path(__file__).resolve().parents[4] / "icons" / "qonvo_cursor.png")
    for p in candidates:
        try:
            if p.exists():
                return str(p)
        except Exception:
            continue
    return ""


def pixmap_to_png(pm: QPixmap) -> bytes:
    """QPixmap → PNG 바이트."""
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    pm.save(buf, "PNG")
    return ba.data()


def render_shape(shape: str, color: QColor, size: int = _CELL) -> QPixmap:
    """베이스 모양을 색칠해 투명 배경 PNG 픽스맵으로 렌더(좌상단 기준)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(QPen(QColor(255, 255, 255, 230), 1.5))
    p.setBrush(color)
    s = size
    if shape == "arrow":
        p.drawPolygon(_ARROW)
    elif shape == "circle":
        p.drawEllipse(QRectF(s * 0.12, s * 0.12, s * 0.76, s * 0.76))
    elif shape == "diamond":
        p.drawPolygon(QPolygonF([
            QPointF(s * 0.5, s * 0.08), QPointF(s * 0.92, s * 0.5),
            QPointF(s * 0.5, s * 0.92), QPointF(s * 0.08, s * 0.5)]))
    elif shape == "star":
        import math
        path = QPainterPath()
        cx = cy = s * 0.5
        rO, rI = s * 0.44, s * 0.18
        for i in range(10):
            r = rO if i % 2 == 0 else rI
            ang = -math.pi / 2 + i * math.pi / 5
            pt = QPointF(cx + r * math.cos(ang), cy + r * math.sin(ang))
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        path.closeSubpath()
        p.drawPath(path)
    elif shape == "heart":
        path = QPainterPath()
        path.moveTo(s * 0.5, s * 0.86)
        path.cubicTo(s * -0.05, s * 0.45, s * 0.28, s * 0.05, s * 0.5, s * 0.30)
        path.cubicTo(s * 0.72, s * 0.05, s * 1.05, s * 0.45, s * 0.5, s * 0.86)
        p.drawPath(path)
    p.end()
    return pm


def _fit_cell(pm: QPixmap, cap: int = _CELL) -> QPixmap:
    """업로드 이미지를 cap 칸 안으로 맞춘다(중앙 배치, 투명 패딩)."""
    if pm.isNull():
        return pm
    src = pm
    if pm.width() > cap or pm.height() > cap:
        src = pm.scaled(cap, cap, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)
    cell = QPixmap(cap, cap)
    cell.fill(Qt.GlobalColor.transparent)
    p = QPainter(cell)
    p.drawPixmap((cap - src.width()) // 2, (cap - src.height()) // 2, src)
    p.end()
    return cell


def slice_sheet(pm: QPixmap) -> dict:
    """저장된 시트를 역할별 칸으로 자른다(클라 캐시와 동일 규칙)."""
    from PyQt6.QtCore import QRect
    out = {}
    if pm is None or pm.isNull() or pm.height() <= 0:
        return out
    w, h = pm.width(), pm.height()
    cells = max(1, round(w / h))
    cw = w / cells
    for i, k in enumerate(_ROLE_KEYS):
        idx = i if i < cells else 0
        out[k] = pm.copy(QRect(int(round(idx * cw)), 0, int(round(cw)), h))
    return out


def compose_sheet(roles: dict) -> QPixmap:
    """역할별 칸을 가로 시트(_CELL*3 × _CELL)로 합친다. 빈 역할은 기본칸 복제."""
    default = roles.get("default")
    if default is None or default.isNull():
        default = render_shape("arrow", QColor("#0d6efd"))
    sheet = QPixmap(_CELL * len(_ROLE_KEYS), _CELL)
    sheet.fill(Qt.GlobalColor.transparent)
    p = QPainter(sheet)
    for i, k in enumerate(_ROLE_KEYS):
        cell = roles.get(k)
        if cell is None or cell.isNull():
            cell = default
        if cell.width() != _CELL or cell.height() != _CELL:
            cell = _fit_cell(cell)
        p.drawPixmap(i * _CELL, 0, cell)
    p.end()
    return sheet


class CursorSkinDialog(QDialog):
    """커서 스킨(역할 세트) 만들기/적용 다이얼로그."""

    def __init__(self, parent=None, client=None, cursor_layer=None):
        super().__init__(parent)
        self._client = client
        self._cursor_layer = cursor_layer
        self._roles: dict = {}      # role -> QPixmap
        self._active = "default"
        self._color = QColor("#0d6efd")

        self.setWindowTitle("커서 스킨 디자인")
        self.setModal(True)
        self.setMinimumWidth(380)
        self.setStyleSheet("""
            QDialog { background-color: #1e1e1e; }
            QLabel { color: #ddd; }
            QComboBox, QPushButton {
                background-color: #2d2d2d; color: #eee; border: 1px solid #444;
                border-radius: 6px; padding: 7px 12px; font-size: 13px;
            }
            QPushButton:hover { border-color: #0d6efd; }
            QComboBox::drop-down { border: none; }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(11)

        # 제목 + 아이콘
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        _icon = feature_icon_path()
        if _icon:
            from PyQt6.QtGui import QIcon
            self.setWindowIcon(QIcon(_icon))
            ic = QLabel()
            ic.setPixmap(QPixmap(_icon).scaled(
                26, 26, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
            title_row.addWidget(ic)
        title = QLabel("내 커서 스킨")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #fff;")
        title_row.addWidget(title)
        title_row.addStretch()
        root.addLayout(title_row)
        root.addWidget(self._hint(
            "기본/포인터(손)/텍스트(I-beam) 3가지를 디자인하면, 진짜 커서처럼 상황에 따라 바뀝니다.\n"
            "포인터·텍스트를 안 만지면 기본과 같아요. merri 계정에 저장 — 어디서 접속하든 따라옴."))

        # ── 프리셋(기본 제공 커서) ──
        root.addWidget(self._caption("프리셋 — 클릭하면 불러와요"))
        preset_row = QHBoxLayout()
        preset_row.setSpacing(6)
        try:
            from . import cursor_presets
            for pid, pname, ppath in cursor_presets.list_presets():
                b = QPushButton()
                b.setToolTip(pname)
                b.setFixedSize(58, 30)
                b.setStyleSheet(
                    "QPushButton { background-color: #232323; border: 1px solid #444;"
                    " border-radius: 6px; } QPushButton:hover { border-color: #0d6efd; }")
                pm = cursor_presets.sheet_pixmap(pid)
                if not pm.isNull():
                    from PyQt6.QtGui import QIcon
                    from PyQt6.QtCore import QSize
                    b.setIcon(QIcon(pm))
                    b.setIconSize(QSize(50, 18))
                b.clicked.connect(lambda _=False, k=pid: self._load_preset(k))
                preset_row.addWidget(b)
        except Exception as e:
            logger.debug("preset row build failed: %s", e)
        preset_row.addStretch()
        root.addLayout(preset_row)

        # ── 역할 선택(세그먼트) ──
        role_row = QHBoxLayout()
        role_row.setSpacing(6)
        self._role_group = QButtonGroup(self)
        self._role_group.setExclusive(True)
        for key, label in _ROLES:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setStyleSheet("""
                QPushButton { background-color: #262626; padding: 6px 10px; }
                QPushButton:checked { background-color: #0d6efd; border-color: #0d6efd;
                    color: #fff; font-weight: bold; }
            """)
            b.setProperty("role_key", key)
            b.clicked.connect(lambda _=False, k=key: self._switch_role(k))
            self._role_group.addButton(b)
            role_row.addWidget(b)
            if key == "default":
                b.setChecked(True)
        role_row.addStretch()
        root.addLayout(role_row)

        # ── 미리보기(현재 역할 큰 칸 + 전체 3칸 스트립) ──
        prev_row = QHBoxLayout()
        self._preview = QLabel()
        self._preview.setFixedSize(72, 72)
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setStyleSheet(
            "background-color: #2a2a2a; border: 1px solid #444; border-radius: 8px;")
        prev_row.addWidget(self._preview)
        prev_row.addSpacing(10)
        strip_box = QVBoxLayout()
        strip_box.addWidget(self._caption("전체 (기본·포인터·텍스트)"))
        self._strip = QLabel()
        self._strip.setFixedHeight(40)
        self._strip.setStyleSheet("background-color: #232323; border-radius: 6px;")
        strip_box.addWidget(self._strip)
        prev_row.addLayout(strip_box, 1)
        root.addLayout(prev_row)

        root.addWidget(self._sep())

        # ── 에디터(모양 + 색) — 현재 역할에 적용 ──
        root.addWidget(self._caption("간단 에디터 (선택한 역할에 적용)"))
        ed_row = QHBoxLayout()
        self._shape_combo = QComboBox()
        for name, key in _SHAPES:
            self._shape_combo.addItem(name, key)
        self._shape_combo.currentIndexChanged.connect(self._regen_shape)
        ed_row.addWidget(self._shape_combo, 1)
        self._color_btn = QPushButton("색 선택")
        self._color_btn.clicked.connect(self._pick_color)
        ed_row.addWidget(self._color_btn)
        root.addLayout(ed_row)

        up_btn = QPushButton("이미지 업로드(PNG)…")
        up_btn.clicked.connect(self._upload_image)
        root.addWidget(up_btn)

        root.addWidget(self._sep())

        # ── 동작 ──
        btn_row = QHBoxLayout()
        reset_btn = QPushButton("기본으로")
        reset_btn.clicked.connect(self._reset_default)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        cancel_btn = QPushButton("닫기")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        self._apply_btn = QPushButton("적용")
        self._apply_btn.setStyleSheet(
            "background-color: #0d6efd; border: none; font-weight: bold;")
        self._apply_btn.clicked.connect(self._apply)
        btn_row.addWidget(self._apply_btn)
        root.addLayout(btn_row)

        self._status = QLabel("")
        self._status.setStyleSheet("color: #888; font-size: 12px;")
        root.addWidget(self._status)

        self._load_initial()

    # ---- 위젯 헬퍼 ----
    def _caption(self, text):
        l = QLabel(text)
        l.setStyleSheet("color: #888; font-size: 11px; font-weight: bold;")
        return l

    def _hint(self, text):
        l = QLabel(text)
        l.setWordWrap(True)
        l.setStyleSheet("color: #888; font-size: 12px;")
        return l

    def _sep(self):
        f = QFrame()
        f.setFrameShape(QFrame.Shape.HLine)
        f.setStyleSheet("color: #333; background-color: #333; max-height: 1px;")
        return f

    # ---- 상태/렌더 ----
    def _load_initial(self):
        """로컬 저장 시트가 있으면 역할별로 잘라 불러온다. 없으면 기본 모양으로 시작."""
        try:
            p = local_skin_path()
            if p.exists():
                pm = QPixmap(str(p))
                if not pm.isNull():
                    self._roles = slice_sheet(pm)
                    self._refresh_previews()
                    return
        except Exception:
            pass
        # 신규: 기본 화살표 → 모든 역할에 동일 시드
        base = render_shape("arrow", self._color)
        self._roles = {k: base for k in _ROLE_KEYS}
        self._refresh_previews()

    def _load_preset(self, preset_id: str):
        """프리셋 시트를 역할별로 잘라 에디터에 불러온다('적용'으로 저장/업로드)."""
        try:
            from . import cursor_presets
            pm = cursor_presets.sheet_pixmap(preset_id)
            if pm.isNull():
                return
            self._roles = slice_sheet(pm)
            self._refresh_previews()
            self._status.setText("프리셋 불러옴 — '적용'을 눌러 저장하세요.")
        except Exception as e:
            logger.debug("load preset failed: %s", e)

    def _switch_role(self, key: str):
        self._active = key
        self._refresh_previews()

    def _set_active_pixmap(self, pm: QPixmap):
        self._roles[self._active] = pm
        self._refresh_previews()

    def _refresh_previews(self):
        # 현재 역할 큰 미리보기
        cur = self._roles.get(self._active)
        if cur is not None and not cur.isNull():
            self._preview.setPixmap(cur.scaled(
                56, 56, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        else:
            self._preview.clear()
        # 전체 3칸 스트립
        try:
            sheet = compose_sheet(self._roles)
            self._strip.setPixmap(sheet.scaledToHeight(
                36, Qt.TransformationMode.SmoothTransformation))
        except Exception:
            pass

    def _regen_shape(self, *_):
        try:
            key = self._shape_combo.currentData()
            self._set_active_pixmap(render_shape(key, self._color))
        except Exception as e:
            logger.debug("regen shape failed: %s", e)

    def _pick_color(self):
        try:
            col = QColorDialog.getColor(self._color, self, "커서 색")
            if col.isValid():
                self._color = col
                self._regen_shape()
        except Exception as e:
            logger.debug("pick color failed: %s", e)

    def _upload_image(self):
        try:
            path, _ = QFileDialog.getOpenFileName(
                self, "커서 이미지 선택", "", "이미지 (*.png *.jpg *.jpeg *.bmp)")
            if not path:
                return
            pm = QPixmap(path)
            if pm.isNull():
                self._status.setText("⚠ 이미지를 읽지 못했습니다.")
                return
            self._set_active_pixmap(_fit_cell(pm))
            self._status.setText(f"'{dict(_ROLES)[self._active]}' 역할에 이미지 적용 — '적용'으로 저장.")
        except Exception as e:
            logger.debug("upload image failed: %s", e)

    def _apply(self):
        sheet = compose_sheet(self._roles)
        if sheet.isNull():
            self._status.setText("⚠ 먼저 모양을 만들거나 이미지를 올리세요.")
            return
        data = pixmap_to_png(sheet)
        # 1) 로컬 시트 저장(재업로드 소스 + 미접속 시 다음 접속에 업로드)
        try:
            p = local_skin_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".png.tmp")
            tmp.write_bytes(data)
            import os
            os.replace(tmp, p)
        except Exception as e:
            logger.debug("save local skin failed: %s", e)

        # 2) 접속 중이면 즉시 업로드 + 내 포인터 반영
        client = self._client
        if client is not None and getattr(client, "is_connected", False):
            h = client.upload_skin(data)
            if h:
                try:
                    from . import cursor_skin_cache as cache
                    cache.put_pixmap(h, sheet)
                    if self._cursor_layer is not None:
                        self._cursor_layer.set_self_skin(h, client.username)
                except Exception:
                    pass
                self._status.setText("✓ 적용됨 — 서버에 올라갔습니다.")
                self.accept()
                return
            self._status.setText("⚠ 서버 업로드 실패(권한/연결 확인). 로컬엔 저장됨.")
            return
        self._status.setText("저장됨 — 서버 접속 시 자동으로 적용됩니다.")
        self.accept()

    def _reset_default(self):
        client = self._client
        if client is not None and getattr(client, "is_connected", False):
            try:
                client.delete_skin()
            except Exception:
                pass
        try:
            p = local_skin_path()
            if p.exists():
                p.unlink()
        except Exception:
            pass
        if self._cursor_layer is not None:
            try:
                self._cursor_layer.set_self_skin("", getattr(client, "username", ""))
            except Exception:
                pass
        self._status.setText("기본 커서로 되돌렸습니다.")
        self._roles = {}
        self._preview.clear()
        self._strip.clear()
