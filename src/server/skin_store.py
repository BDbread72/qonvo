"""커서 스킨 저장소 — Dynamic Cursor (마인크래프트 스킨 모델).

사용자가 자기 커서 스킨(PNG)을 올려두면 서버가 **계정(username) 기준**으로 호스팅한다.
presence 엔 이미지 바이트가 아니라 **스킨 해시만** 싣고, 다른 클라이언트는 그 해시로
``GET /skin/{user}`` 를 한 번 받아 캐시한다(마크가 위치 패킷엔 스킨을 안 넣고 세션서버에서
UUID 로 받아오는 것과 동일). 계정당 스킨 1개 → UUID 파일명이 아니라 username 파일명을 써서
덮어쓰기가 정상 동작한다.

저장 규칙(CLAUDE.md): 원자적 저장(.tmp→fsync→replace), PNG 매직 검증, 크기 상한.
merri 프로필 전용이지만 키는 그냥 username 이라 로컬 계정도 동일하게 동작한다.
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
from pathlib import Path
from typing import Optional

from .config import get_server_dir

_SAFE = re.compile(r"[^A-Za-z0-9._-]")
_MAX_SKIN = 512 * 1024              # 커서 스킨 상한 512KB(작은 PNG면 충분)
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

_lock = threading.Lock()
# username -> (hash, mtime) — 파일 mtime 으로 무효화하는 해시 캐시
_hash_cache: dict = {}


def get_skins_dir() -> Path:
    """스킨 저장 디렉토리(필요 시 생성)."""
    d = get_server_dir() / "skins"
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_username(username: str) -> str:
    """username 을 파일시스템 안전 문자열로 정규화한다(merri username 은 보통 영숫자.-_)."""
    return _SAFE.sub("_", (username or "").strip())[:64]


def skin_path(username: str) -> Optional[Path]:
    """해당 계정의 스킨 파일 경로(미존재해도 경로는 반환). username 무효 시 None."""
    u = safe_username(username)
    if not u:
        return None
    return get_skins_dir() / f"{u}.png"


def is_png(data: bytes) -> bool:
    return len(data) >= 8 and data[:8] == _PNG_MAGIC


def save_skin(username: str, data: bytes) -> Optional[str]:
    """스킨 PNG 를 계정 밑에 저장하고 해시(16hex)를 반환. 부적합/실패 시 None.

    검증: PNG 매직 + 크기 상한. 원자적(.tmp→fsync→replace)."""
    p = skin_path(username)
    if p is None:
        return None
    if not data or len(data) > _MAX_SKIN or not is_png(data):
        return None
    with _lock:
        try:
            tmp = p.with_suffix(".png.tmp")
            with open(tmp, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, p)
            mtime = p.stat().st_mtime
        except Exception:
            return None
        h = hashlib.sha256(data).hexdigest()[:16]
        _hash_cache[safe_username(username)] = (h, mtime)
        return h


def skin_hash(username: str) -> str:
    """현재 스킨의 해시(16hex). 없으면 "". (mtime 캐시)"""
    p = skin_path(username)
    if p is None or not p.exists():
        return ""
    u = safe_username(username)
    try:
        mtime = p.stat().st_mtime
    except Exception:
        return ""
    c = _hash_cache.get(u)
    if c and c[1] == mtime:
        return c[0]
    try:
        h = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    except Exception:
        return ""
    _hash_cache[u] = (h, mtime)
    return h


def delete_skin(username: str) -> bool:
    """스킨을 삭제(기본 화살표로 복귀). 존재했으면 True."""
    p = skin_path(username)
    if p is None or not p.exists():
        return False
    with _lock:
        try:
            p.unlink()
        except Exception:
            return False
        _hash_cache.pop(safe_username(username), None)
        return True
