"""인증 및 권한 관리.

- 로컬 계정: users.json (pbkdf2-hmac-sha256 해시). `adduser` CLI 로 추가.
- 권한 레벨: 0=Visitor(읽기전용) 1=Member(편집+AI) 2=Operator(관리)
- OAuth(merri) 발급 토큰: 메모리상 토큰→(user, level) 매핑. WS auth 의 pass 로 사용.

레벨 상수는 마인크래프트 BE 권한 모델을 따른다.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

from .config import get_server_dir, load_config

VISITOR = 0
MEMBER = 1
OPERATOR = 2

LEVEL_NAMES = {VISITOR: "Visitor", MEMBER: "Member", OPERATOR: "Operator"}

_PBKDF2_ITER = 240_000
_lock = threading.Lock()


def _users_path() -> Path:
    return get_server_dir() / "users.json"


def hash_password(password: str) -> str:
    """비밀번호를 ``pbkdf2$iter$salt$hash`` 형식 문자열로 해시한다."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITER)
    return f"pbkdf2${_PBKDF2_ITER}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """저장된 해시와 비밀번호 일치 여부를 상수시간 비교로 확인한다."""
    try:
        algo, iter_s, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iter_s)
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


def _load_users() -> Dict[str, dict]:
    path = _users_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_users(users: Dict[str, dict]) -> None:
    path = _users_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def add_user(username: str, password: str, level: int = MEMBER) -> None:
    """로컬 계정을 추가/갱신한다 (users.json)."""
    with _lock:
        users = _load_users()
        users[username] = {"password": hash_password(password), "level": int(level)}
        _save_users(users)


def remove_user(username: str) -> bool:
    """로컬 계정을 삭제한다. 존재 여부를 반환한다."""
    with _lock:
        users = _load_users()
        if username in users:
            del users[username]
            _save_users(users)
            return True
        return False


def list_users() -> Dict[str, int]:
    """username -> level 맵을 반환한다."""
    return {u: d.get("level", MEMBER) for u, d in _load_users().items()}


# ---- 화이트리스트 / 밴 (접근 제어) -------------------------------------
def _wl_path() -> Path:
    return get_server_dir() / "whitelist.json"


def _bl_path() -> Path:
    return get_server_dir() / "banned.json"


def _load_names(path: Path) -> list:
    if not path.exists():
        return []
    try:
        v = json.loads(path.read_text(encoding="utf-8"))
        return list(v) if isinstance(v, list) else []
    except Exception:
        return []


def _save_names(path: Path, names: list) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(sorted(set(names)), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def list_whitelist() -> list:
    return _load_names(_wl_path())


def list_banned() -> list:
    return _load_names(_bl_path())


def add_whitelist(username: str) -> None:
    with _lock:
        _save_names(_wl_path(), _load_names(_wl_path()) + [username])


def remove_whitelist(username: str) -> None:
    with _lock:
        _save_names(_wl_path(), [u for u in _load_names(_wl_path()) if u != username])


def ban_user(username: str) -> None:
    with _lock:
        _save_names(_bl_path(), _load_names(_bl_path()) + [username])


def pardon_user(username: str) -> None:
    with _lock:
        _save_names(_bl_path(), [u for u in _load_names(_bl_path()) if u != username])


def is_banned(username: str) -> bool:
    return username in _load_names(_bl_path())


def is_whitelisted(username: str) -> bool:
    return username in _load_names(_wl_path())


# ---- 역할(레벨) 오버라이드 — username 기준, 핫 적용(merri 포함 모든 인증방식) ----
def _roles_path() -> Path:
    return get_server_dir() / "roles.json"


def list_roles() -> Dict[str, int]:
    p = _roles_path()
    if not p.exists():
        return {}
    try:
        v = json.loads(p.read_text(encoding="utf-8"))
        return {str(k): int(x) for k, x in v.items()} if isinstance(v, dict) else {}
    except Exception:
        return {}


def get_role(username: str) -> Optional[int]:
    return list_roles().get(username)


def set_role(username: str, level: int) -> None:
    with _lock:
        roles = list_roles()
        roles[username] = int(level)
        tmp = _roles_path().with_suffix(".json.tmp")
        tmp.write_text(json.dumps(roles, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, _roles_path())


def clear_role(username: str) -> None:
    with _lock:
        roles = list_roles()
        if username in roles:
            del roles[username]
            tmp = _roles_path().with_suffix(".json.tmp")
            tmp.write_text(json.dumps(roles, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, _roles_path())


class TokenStore:
    """OAuth 등으로 발급한 1회성 로그인 토큰 저장소 (메모리, TTL)."""

    def __init__(self, ttl_seconds: int = 600):
        self._ttl = ttl_seconds
        self._tokens: Dict[str, Tuple[str, int, float]] = {}
        self._lock = threading.Lock()

    def issue(self, username: str, level: int, now: float) -> str:
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._tokens[token] = (username, level, now + self._ttl)
        return token

    def consume(self, token: str, now: float) -> Optional[Tuple[str, int]]:
        """토큰을 검증하고 (username, level) 을 반환. 만료/부재 시 None."""
        with self._lock:
            entry = self._tokens.pop(token, None)
        if not entry:
            return None
        username, level, expiry = entry
        if now > expiry:
            return None
        return username, level


class Authenticator:
    """config + users.json + 토큰을 종합해 인증을 수행한다."""

    def __init__(self, config: Optional[dict] = None):
        self._config = config or load_config()
        self.tokens = TokenStore()

    @property
    def default_level(self) -> int:
        return int(self._config["server"].get("default_level", MEMBER))

    @property
    def allow_guests(self) -> bool:
        return bool(self._config["server"].get("allow_guests", False))

    def _level_override(self, username: str) -> Optional[int]:
        """레벨 오버라이드: roles.json(관리자 페이지에서 부여, 즉시·영속) 우선 → config.toml [users]."""
        r = get_role(username)
        if r is not None:
            return r
        ov = self._config.get("users", {}).get(username)
        if isinstance(ov, int):
            return ov
        if isinstance(ov, dict) and "level" in ov:
            return int(ov["level"])
        return None

    @property
    def whitelist_enabled(self) -> bool:
        return bool(self._config.get("access", {}).get("whitelist", False))

    def _credentials_ok(self, username: str, password: str, now: float) -> Tuple[bool, int, str]:
        """자격 증명만 검사(접근제어 제외). (성공, 레벨, 사유)."""
        # 1) OAuth/발급 토큰 (password 자리에 토큰)
        tok = self.tokens.consume(password, now)
        if tok and tok[0] == username:
            ov = self._level_override(username)
            return True, ov if ov is not None else tok[1], "token"

        # 2) 로컬 계정
        rec = _load_users().get(username)
        if rec:
            if verify_password(password, rec.get("password", "")):
                ov = self._level_override(username)
                return True, ov if ov is not None else rec.get("level", MEMBER), "local"
            return False, -1, "bad password"

        # 3) 게스트
        if self.allow_guests:
            ov = self._level_override(username)
            return True, ov if ov is not None else self.default_level, "guest"

        return False, -1, "unknown user"

    def access_for(self, username: str) -> Tuple[bool, int, str]:
        """비밀번호 검증 없이(이미 외부 인증됨, 예: merri OAuth) 접근제어/레벨만 적용."""
        if not username:
            return False, -1, "no username"
        if is_banned(username):
            return False, -1, "banned"
        ov = self._level_override(username)
        level = ov if ov is not None else self.default_level
        if self.whitelist_enabled and level < OPERATOR and not is_whitelisted(username):
            return False, -1, "not whitelisted"
        return True, level, "merri"

    def authenticate(self, username: str, password: str, now: float) -> Tuple[bool, int, str]:
        """(성공여부, 레벨, 사유) 를 반환한다.

        자격 증명(토큰/로컬/게스트) 확인 후 접근제어(밴/화이트리스트)를 적용한다.
        Operator 는 화이트리스트를 우회하지만 밴은 모두에게 적용된다.
        """
        if not username:
            return False, -1, "username required"

        ok, level, reason = self._credentials_ok(username, password, now)
        if not ok:
            return ok, level, reason

        # 접근 제어
        if is_banned(username):
            return False, -1, "banned"
        if self.whitelist_enabled and level < OPERATOR and not is_whitelisted(username):
            return False, -1, "not whitelisted"

        return True, level, reason
