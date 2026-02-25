"""
Iconify 기반 아이콘 매니저

icons/icons.toml에서 아이콘 키 → Iconify ID 매핑을 읽고,
Iconify API에서 SVG를 페치하여 로컬 캐시 + 메모리 캐시.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional, Tuple

from v.logger import get_logger

logger = get_logger("qonvo.icon_manager")

# 프로젝트 루트 (src/v/icon_manager.py → src/v → src → project root)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ICONS_TOML = _PROJECT_ROOT / "icons" / "icons.toml"

# TOML 미존재 시 기본 매핑
_DEFAULT_ICONS = {
    "cat_nodes": "tabler:brain",
    "cat_notes": "tabler:notes",
    "cat_media": "tabler:photo-circle",
    "cat_ui": "tabler:layout-grid",
    "node": "tabler:message-dots",
    "function": "tabler:math-function",
    "round_table": "tabler:users-group",

    "repository": "tabler:database",
    "sticky": "tabler:note",
    "text": "tabler:letter-t",
    "checklist": "tabler:list-check",
    "image": "tabler:photo",
    "dimension": "tabler:dimensions",
    "button": "tabler:click",
    "group": "tabler:layout-board-split",
    "pin": "tabler:map-pin",
}


def _get_cache_dir() -> Path:
    """아이콘 캐시 디렉토리 (APPDATA/Qonvo/icon_cache)"""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path.home() / ".config"
    cache_dir = base / "Qonvo" / "icon_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


class IconManager:
    """Iconify SVG 아이콘 로더 (싱글턴)"""

    def __init__(self):
        self._icon_map: Dict[str, str] = {}  # icon_key → iconify_id
        self._default_color: str = "white"
        self._default_size: int = 28
        self._pixmap_cache: Dict[Tuple[str, int, str], object] = {}  # (key, size, color) → QPixmap
        self._cache_dir: Optional[Path] = None
        self._loaded = False

    def _ensure_loaded(self):
        """설정 파일을 한 번만 로드"""
        if self._loaded:
            return
        self._loaded = True
        self._cache_dir = _get_cache_dir()
        self._load_config()

    def _load_config(self):
        """icons/icons.toml 파싱"""
        if not _ICONS_TOML.exists():
            logger.warning(f"[ICON] icons.toml not found at {_ICONS_TOML}, using defaults")
            self._icon_map = dict(_DEFAULT_ICONS)
            return

        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib
            except ImportError:
                logger.warning("[ICON] No TOML parser available, using defaults")
                self._icon_map = dict(_DEFAULT_ICONS)
                return

        try:
            with open(_ICONS_TOML, "rb") as f:
                data = tomllib.load(f)

            settings = data.get("settings", {})
            self._default_color = settings.get("default_color", "white")
            self._default_size = settings.get("size", 28)

            icons = data.get("icons", {})
            if icons:
                self._icon_map = dict(icons)
            else:
                self._icon_map = dict(_DEFAULT_ICONS)

            logger.info(f"[ICON] Loaded {len(self._icon_map)} icons from {_ICONS_TOML}")
        except Exception as e:
            logger.error(f"[ICON] Failed to load icons.toml: {e}")
            self._icon_map = dict(_DEFAULT_ICONS)

    def get_pixmap(self, icon_key: str, size: int = 0, color: str = ""):
        """아이콘 키로 QPixmap 반환. 캐시 → 디스크 → API 순서.

        Returns:
            QPixmap or None (로드 실패 시)
        """
        self._ensure_loaded()

        if not size:
            size = self._default_size
        if not color:
            color = self._default_color

        cache_key = (icon_key, size, color)
        if cache_key in self._pixmap_cache:
            return self._pixmap_cache[cache_key]

        iconify_id = self._icon_map.get(icon_key)
        if not iconify_id:
            return None

        # SVG 데이터 가져오기 (디스크 캐시 → API)
        svg_data = self._get_svg(iconify_id, color)
        if not svg_data:
            return None

        # QPixmap 렌더링
        pixmap = self._render_svg(svg_data, size)
        if pixmap is not None:
            self._pixmap_cache[cache_key] = pixmap
        return pixmap

    def _get_svg(self, iconify_id: str, color: str) -> Optional[str]:
        """SVG 데이터 가져오기 (디스크 캐시 우선, 없으면 API 페치)"""
        # 디스크 캐시 파일명: tabler--brain--white.svg
        safe_name = iconify_id.replace(":", "--") + f"--{color}.svg"
        cache_path = self._cache_dir / safe_name

        # 디스크 캐시 히트
        if cache_path.exists():
            try:
                return cache_path.read_text(encoding="utf-8")
            except Exception:
                pass

        # API 페치
        svg_data = self._fetch_svg(iconify_id, color)
        if svg_data:
            try:
                cache_path.write_text(svg_data, encoding="utf-8")
            except Exception as e:
                logger.warning(f"[ICON] Failed to cache SVG: {e}")
        return svg_data

    def _fetch_svg(self, iconify_id: str, color: str) -> Optional[str]:
        """Iconify API에서 SVG 다운로드"""
        import urllib.request
        import urllib.error

        # "tabler:brain" → prefix="tabler", name="brain"
        if ":" not in iconify_id:
            return None
        prefix, name = iconify_id.split(":", 1)

        # URL 색상 인코딩 ('#' → '%23')
        encoded_color = color.replace("#", "%23")
        url = f"https://api.iconify.design/{prefix}/{name}.svg?color={encoded_color}"

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Qonvo"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                svg = resp.read().decode("utf-8")
                if "<svg" in svg:
                    logger.info(f"[ICON] Fetched {iconify_id}")
                    return svg
                else:
                    logger.warning(f"[ICON] Invalid SVG from {url}")
                    return None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            logger.warning(f"[ICON] Fetch failed for {iconify_id}: {e}")
            return None

    def _render_svg(self, svg_data: str, size: int):
        """SVG 문자열 → QPixmap 렌더링"""
        try:
            from PyQt6.QtSvg import QSvgRenderer
            from PyQt6.QtGui import QPixmap, QPainter, QImage
            from PyQt6.QtCore import QByteArray, Qt

            renderer = QSvgRenderer(QByteArray(svg_data.encode("utf-8")))
            if not renderer.isValid():
                return None

            image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            renderer.render(painter)
            painter.end()

            return QPixmap.fromImage(image)
        except Exception as e:
            logger.error(f"[ICON] SVG render failed: {e}")
            return None


# 싱글턴 인스턴스
icon_manager = IconManager()
