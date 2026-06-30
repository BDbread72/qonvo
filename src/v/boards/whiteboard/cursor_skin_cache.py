"""커서 스킨 디스크/메모리 캐시 — Dynamic Cursor (클라 측).

서버에서 받은 스킨 PNG 를 **해시 기준**으로 ``%APPDATA%/Qonvo/cursor_skins/<hash>.png`` 에
캐시한다(마크가 받은 스킨을 로컬에 캐시하는 것과 동일). presence 가 해시만 알려주므로,
이미 캐시된 해시는 즉시 표시되고 처음 보는 해시만 다운로드한다.

**스킨 = 가로 스프라이트 시트**(서버는 PNG 한 장만 알면 됨 — phase 1 무변경). 한 장을
가로 N칸으로 잘라 역할별 커서로 쓴다: 0=기본, 1=포인터(손), 2=텍스트(I-beam).
정사각(1칸)이면 단일 스킨 → 모든 역할이 같은 그림(하위호환). 칸이 모자라면 0번(기본)으로 폴백.

QPixmap(Qt) 의존 → GUI 전용.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from PyQt6.QtCore import QRect
from PyQt6.QtGui import QPixmap

# 역할 순서(=시트 칸 순서)
ROLE_ORDER = ("default", "point", "text")

_mem: dict = {}     # hash -> QPixmap (시트 원본)
_roles: dict = {}   # hash -> {role: QPixmap} (슬라이스 캐시)


def cache_dir() -> Path:
    from v.settings import get_app_data_path
    d = get_app_data_path() / "cursor_skins"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cached_file(h: str) -> Path:
    return cache_dir() / f"{h}.png"


def slice_roles(pm: Optional[QPixmap]) -> Dict[str, QPixmap]:
    """스프라이트 시트를 역할별 QPixmap 으로 자른다. 칸 부족 시 0번(기본)으로 폴백."""
    if pm is None or pm.isNull():
        return {}
    w, h = pm.width(), pm.height()
    if h <= 0 or w <= 0:
        return {}
    cells = max(1, round(w / h))
    cw = w / cells
    out: Dict[str, QPixmap] = {}
    for i, role in enumerate(ROLE_ORDER):
        idx = i if i < cells else 0
        out[role] = pm.copy(QRect(int(round(idx * cw)), 0, int(round(cw)), h))
    return out


def get_pixmap(h: str) -> Optional[QPixmap]:
    """해시의 시트 QPixmap(메모리→디스크 캐시). 미존재/손상 시 None."""
    if not h:
        return None
    pm = _mem.get(h)
    if pm is not None:
        return pm if not pm.isNull() else None
    p = cached_file(h)
    if p.exists():
        pm = QPixmap(str(p))
        _mem[h] = pm
        return pm if not pm.isNull() else None
    return None


def get_roles(h: str) -> Dict[str, QPixmap]:
    """해시의 역할별 QPixmap dict(슬라이스 캐시). 미캐시면 {}."""
    if not h:
        return {}
    r = _roles.get(h)
    if r is not None:
        return r
    pm = get_pixmap(h)
    if pm is None:
        return {}
    r = slice_roles(pm)
    _roles[h] = r
    return r


def store_file(h: str, path: str) -> Optional[QPixmap]:
    """다운로드 완료 파일을 메모리 캐시에 로드해 시트 QPixmap 반환(손상 시 None)."""
    if not h or not path:
        return None
    pm = QPixmap(path)
    _mem[h] = pm
    _roles.pop(h, None)   # 슬라이스 캐시 무효화 → 다음 get_roles 에서 재계산
    return pm if not pm.isNull() else None


def put_pixmap(h: str, pm: QPixmap) -> None:
    """이미 만든 시트 QPixmap 을 캐시에 직접 넣는다(내 스킨 즉시 적용 등)."""
    if h and pm is not None and not pm.isNull():
        _mem[h] = pm
        _roles.pop(h, None)
