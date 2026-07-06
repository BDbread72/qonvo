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
    """스프라이트 시트를 역할별 QPixmap 으로 자른다. 칸 부족 시 0번(기본)으로 폴백.

    같은 칸을 쓰는 역할들은 **같은 QPixmap 객체**를 공유한다 — 단일칸(스킨 1장) 시트에서
    point/text 가 default 와 동일 객체가 되므로, 핫스팟 계산이 '이 그림은 화살표'임을
    identity 로 알 수 있다(화살표 그림에 손끝 휴리스틱을 적용하는 오류 방지).
    """
    if pm is None or pm.isNull():
        return {}
    w, h = pm.width(), pm.height()
    if h <= 0 or w <= 0:
        return {}
    cells = max(1, round(w / h))
    cw = w / cells
    out: Dict[str, QPixmap] = {}
    cell_pm: Dict[int, QPixmap] = {}
    for i, role in enumerate(ROLE_ORDER):
        idx = i if i < cells else 0
        if idx not in cell_pm:
            cell_pm[idx] = pm.copy(QRect(int(round(idx * cw)), 0, int(round(cw)), h))
        out[role] = cell_pm[idx]
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


# ── 핫스팟(가리키는 지점) 감지 ────────────────────────────────────
# QCursor 는 핫스팟 좌표가 실제 포인터 위치다. 지금까지 (0,0) 고정이라 손가락/I-beam
# 커서에서 '보이는 손끝'과 '실제 클릭점'이 십수 px 어긋났다(리사이즈 핸들이 안 잡히는 체감).
# 글리프 내용(알파)에서 역할별 지점을 찾는다: 화살표=최상단 행의 왼쪽 끝(팁),
# 손=최상단 행의 중앙(손끝), I-beam=불투명 영역 중앙. 글로우 후광은 alpha<128 로 무시.
_hotspots: dict = {}   # (pixmap cacheKey, role) -> (x, y) 원본 픽셀 좌표

_ALPHA_MIN = 128
_SCAN_MAX = 64   # 큰 스킨은 축소본에서 스캔(정밀도 충분, 속도 일정)


def hotspot(role: str, pm: Optional[QPixmap]) -> tuple:
    """역할 글리프의 '가리키는 지점'(원본 픽셀 좌표). 실패/빈 그림이면 (0,0)."""
    if pm is None or pm.isNull():
        return (0.0, 0.0)
    key = (pm.cacheKey(), role)
    got = _hotspots.get(key)
    if got is not None:
        return got
    try:
        from PyQt6.QtCore import Qt as _Qt
        from PyQt6.QtGui import QImage
        scan = pm
        if pm.width() > _SCAN_MAX or pm.height() > _SCAN_MAX:
            scan = pm.scaled(_SCAN_MAX, _SCAN_MAX, _Qt.AspectRatioMode.KeepAspectRatio,
                             _Qt.TransformationMode.FastTransformation)
        img = scan.toImage().convertToFormat(QImage.Format.Format_ARGB32)
        w, h = img.width(), img.height()
        minx, miny, maxx, maxy = w, h, -1, -1
        top_y = -1
        top_xs: list = []
        for y in range(h):
            for x in range(w):
                if ((img.pixel(x, y) >> 24) & 0xFF) >= _ALPHA_MIN:
                    if top_y < 0:
                        top_y = y
                    if y == top_y:
                        top_xs.append(x)
                    minx = min(minx, x); maxx = max(maxx, x)
                    miny = min(miny, y); maxy = max(maxy, y)
        if maxx < 0:                     # 전부 투명
            pt = (0.0, 0.0)
        elif role == "text":             # I-beam: 중앙이 캐럿 위치
            pt = ((minx + maxx) / 2.0, (miny + maxy) / 2.0)
        elif role == "point":            # 손가락: 최상단(손끝)의 중앙
            pt = ((top_xs[0] + top_xs[-1]) / 2.0, float(top_y))
        else:                            # 화살표: 최상단 행의 왼쪽 끝 = 팁
            pt = (float(top_xs[0]), float(top_y))
        f = pm.width() / float(w) if w else 1.0   # 축소 스캔 → 원본 좌표로 환산
        pt = (pt[0] * f, pt[1] * f)
    except Exception:
        pt = (0.0, 0.0)
    _hotspots[key] = pt
    return pt


def hotspot_for(roles: Dict[str, QPixmap], role: str) -> tuple:
    """역할 dict 에서 실제 쓰일 픽스맵의 핫스팟. 단일칸 폴백(다른 역할이 default 와
    같은 객체)이면 화살표(default) 휴리스틱을 적용한다."""
    pm = roles.get(role) or roles.get("default")
    if pm is None:
        return (0.0, 0.0)
    used_role = role
    dflt = roles.get("default")
    if role != "default" and dflt is not None and pm is dflt:
        used_role = "default"
    return hotspot(used_role, pm)
