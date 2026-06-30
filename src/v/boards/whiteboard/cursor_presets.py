"""기본 제공 커서 프리셋 — Dynamic Cursor.

역할별 모양(화살표/손/I-beam)은 퍼미시브 라이선스 아이콘(Phosphor=MIT, Lucide=ISC)의 SVG
실루엣을 쓰고(출처: icons/cursors/_src/CREDITS.txt), 거기에 색(단색|그라데이션)+외곽선+글로우를
입혀 프리셋 시트(192×64, 3칸)를 만든다. 빌드 시 `icons/cursors/<id>.png` 로 구워 번들
(icons 폴더는 crack.bat 가 --add-data 로 포함). 런타임(다이얼로그)은 PNG 만 로드하므로
QtSvg 는 생성 시(개발/리젠)에만 필요.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QRectF, QByteArray
from PyQt6.QtGui import (QPixmap, QPainter, QColor, QLinearGradient, QBrush)

CELL = 64
ROLES = ("default", "point", "text")

# 역할 → SVG 파일 + 셀 내 배치(렌더 크기, x오프셋, y오프셋).
# 화살표는 팁이 좌상단(0,0 핫스팟)에 오도록, 손/텍스트는 살짝 안쪽 배치.
_ROLE_SVG = {"default": "arrow.svg", "point": "hand.svg", "text": "text.svg"}
_ROLE_PLACE = {
    "default": (52.0, 2.0, 2.0),
    "point": (54.0, 5.0, 5.0),
    "text": (44.0, 10.0, 10.0),
}

# 프리셋(스타일만 — 모양은 위 SVG 공용). fill: "#rgb" 또는 (top,bottom) 그라데이션.
PRESETS = [
    {"id": "classic", "name": "클래식", "fill": "#ffffff", "outline": "#1c1c1c", "ow": 2},
    {"id": "graphite", "name": "그래파이트", "fill": ("#565656", "#222222"),
     "outline": "#f0f0f0", "ow": 2},
    {"id": "ocean", "name": "오션", "fill": ("#5fdcff", "#0a84ff"),
     "outline": "#ffffff", "ow": 2, "glow": "#0a84ff"},
    {"id": "sunset", "name": "선셋", "fill": ("#ffd36e", "#ff5e8a"),
     "outline": "#ffffff", "ow": 2, "glow": "#ff5e8a"},
    {"id": "mint", "name": "민트", "fill": ("#c9ffdd", "#2ecf72"),
     "outline": "#0c5a32", "ow": 2},
]

_svg_cache: dict = {}


def _src_dir():
    from pathlib import Path
    return Path(__file__).resolve().parents[4] / "icons" / "cursors" / "_src"


def _load_svg(role: str) -> str:
    if role in _svg_cache:
        return _svg_cache[role]
    text = ""
    try:
        p = _src_dir() / _ROLE_SVG[role]
        text = p.read_text(encoding="utf-8")
        # Lucide 스트로크 아이콘(text)은 얇아서 살짝 굵게
        if role == "text":
            text = text.replace('stroke-width="2"', 'stroke-width="2.6"')
    except Exception:
        text = ""
    _svg_cache[role] = text
    return text


def _brush(style: dict, h0: float, h1: float) -> QBrush:
    fill = style.get("fill", "#ffffff")
    if isinstance(fill, (tuple, list)):
        g = QLinearGradient(0, h0, 0, h1)
        g.setColorAt(0.0, QColor(fill[0]))
        g.setColorAt(1.0, QColor(fill[1]))
        return QBrush(g)
    return QBrush(QColor(fill))


def _silhouette(role: str) -> QPixmap:
    """역할 SVG 를 흰색으로 64칸에 렌더 → 알파=모양인 실루엣 픽스맵."""
    pm = QPixmap(CELL, CELL)
    pm.fill(Qt.GlobalColor.transparent)
    svg = _load_svg(role)
    if not svg:
        return pm
    try:
        from PyQt6.QtSvg import QSvgRenderer
        data = svg.replace("currentColor", "#ffffff")
        r = QSvgRenderer(QByteArray(data.encode("utf-8")))
        scale, ox, oy = _ROLE_PLACE[role]
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r.render(p, QRectF(ox, oy, scale, scale))
        p.end()
    except Exception:
        pass
    return pm


def _tint(sil: QPixmap, color) -> QPixmap:
    """실루엣의 RGB 를 color 로 치환(알파 유지)."""
    out = QPixmap(sil.size())
    out.fill(Qt.GlobalColor.transparent)
    q = QPainter(out)
    q.drawPixmap(0, 0, sil)
    q.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    q.fillRect(out.rect(), QColor(color))
    q.end()
    return out


def _fill_masked(sil: QPixmap, style: dict) -> QPixmap:
    """그라데이션/단색을 실루엣 알파로 마스킹."""
    fill = QPixmap(sil.size())
    fill.fill(Qt.GlobalColor.transparent)
    q = QPainter(fill)
    q.fillRect(QRectF(0, 0, CELL, CELL), _brush(style, 4, CELL - 4))
    q.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    q.drawPixmap(0, 0, sil)
    q.end()
    return fill


def _ring(r: float):
    return [(r, 0), (-r, 0), (0, r), (0, -r),
            (r, r), (r, -r), (-r, r), (-r, -r)]


def render_role(style: dict, role: str) -> QPixmap:
    """역할 셀: (글로우) + 외곽선 스탬프 + 그라데이션 채움."""
    sil = _silhouette(role)
    fill = _fill_masked(sil, style)
    outline = _tint(sil, style.get("outline", "#1c1c1c"))
    cell = QPixmap(CELL, CELL)
    cell.fill(Qt.GlobalColor.transparent)
    p = QPainter(cell)
    if style.get("glow"):
        glow = _tint(sil, style["glow"])
        for rad, a in ((5.0, 55), (3.0, 95)):
            p.setOpacity(a / 255.0)
            for dx, dy in _ring(rad):
                p.drawPixmap(int(dx), int(dy), glow)
        p.setOpacity(1.0)
    ow = float(style.get("ow", 2))
    for dx, dy in _ring(ow):
        p.drawPixmap(int(round(dx)), int(round(dy)), outline)
    p.drawPixmap(0, 0, fill)
    p.end()
    return cell


def build_sheet(style: dict) -> QPixmap:
    """프리셋의 3역할을 가로 시트(192×64)로 합친다."""
    sheet = QPixmap(CELL * len(ROLES), CELL)
    sheet.fill(Qt.GlobalColor.transparent)
    p = QPainter(sheet)
    for i, role in enumerate(ROLES):
        p.drawPixmap(i * CELL, 0, render_role(style, role))
    p.end()
    return sheet


def generate_all(out_dir) -> list:
    """모든 프리셋 시트를 out_dir/<id>.png 로 저장. (id, path) 목록 반환."""
    import os
    os.makedirs(out_dir, exist_ok=True)
    out = []
    for style in PRESETS:
        path = os.path.join(out_dir, f"{style['id']}.png")
        build_sheet(style).save(path, "PNG")
        out.append((style["id"], path))
    return out


def presets_dir() -> str:
    """번들된 프리셋 시트 디렉토리(icons/cursors). frozen=_MEIPASS, dev=repo root."""
    import sys
    from pathlib import Path
    if getattr(sys, "frozen", False):
        p = Path(getattr(sys, "_MEIPASS", ".")) / "icons" / "cursors"
        if p.exists():
            return str(p)
    return str(Path(__file__).resolve().parents[4] / "icons" / "cursors")


def list_presets() -> list:
    """[(id, name, sheet_path)] — 번들 PNG 경로(없으면 "")."""
    import os
    d = presets_dir()
    out = []
    for style in PRESETS:
        path = os.path.join(d, f"{style['id']}.png")
        out.append((style["id"], style["name"], path if os.path.exists(path) else ""))
    return out


def sheet_pixmap(preset_id: str) -> QPixmap:
    """프리셋 시트 QPixmap — 번들 PNG 우선, 없으면 즉석 렌더(폴백)."""
    import os
    path = os.path.join(presets_dir(), f"{preset_id}.png")
    if os.path.exists(path):
        pm = QPixmap(path)
        if not pm.isNull():
            return pm
    for style in PRESETS:
        if style["id"] == preset_id:
            return build_sheet(style)
    return QPixmap()
