"""서버 설정(config.toml) 로드 및 기본 생성.

데이터 경로 (Windows): ``%APPDATA%/Qonvo/server/``
  - config.toml          서버 설정
  - boards/<id>/         보드별 snapshot.json + oplog.jsonl
  - users.json           로컬 계정 (pbkdf2 해시) — config.toml [users] 보다 우선

TOML 파서는 stdlib tomllib(3.11+) 우선, 없으면 tomli 사용.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

try:  # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # pragma: no cover
    import tomli as _toml  # type: ignore


def get_server_dir() -> Path:
    """서버 데이터 루트 디렉토리를 반환(필요 시 생성)한다."""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path.home() / ".config"
    d = base / "Qonvo" / "server"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_boards_dir() -> Path:
    """보드 저장 디렉토리를 반환(필요 시 생성)한다."""
    d = get_server_dir() / "boards"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_config_path() -> Path:
    """config.toml 경로를 반환한다."""
    return get_server_dir() / "config.toml"


DEFAULT_CONFIG_TOML = """# Qonvo 서버 설정
# 변경 후 서버를 재시작하세요.

[server]
host = "0.0.0.0"      # 0.0.0.0 = 모든 인터페이스에서 접속 허용
port = 9700
name = "Qonvo Server"
motd = "Welcome to Qonvo"
# 신규 계정/게스트의 기본 권한 레벨: 0=Visitor(읽기전용) 1=Member(편집+AI) 2=Operator(관리)
default_level = 1
# true 이면 users.json/[users]에 없는 사용자도 default_level 로 자동 입장 허용
allow_guests = false

[access]
# 화이트리스트 모드: true 면 whitelist.json 에 있는 사용자만 접속 가능
# (Operator 는 우회). 밴(banned.json)은 모두에게 적용.
# 콘솔: whitelist add/remove/list, ban/pardon/banlist
whitelist = false

[network]
# UPnP 자동 포트개방 — 라우터가 허용하면 포트포워딩/터널 없이 외부 접속 가능
# (스팀 데디서버처럼 그냥 실행). 라우터가 UPnP 미지원이면 자동으로 건너뜀.
upnp = true
# 클라이언트에게 안내할 접속 주소(도메인/DDNS). 비우면 공인 IP 자동 감지.
public_host = ""
# UPnP 임대 시간(초). 서버가 주기적으로 갱신한다.
upnp_lease = 3600

[ai]
# 비우면 데스크톱 앱의 저장된 Gemini 키(%APPDATA%/Qonvo/settings.json)를 사용.
gemini_keys = []
# OpenAI(GPT/DALL-E/GPT Image) · Anthropic(Claude) 키. 값이 있으면 settings 무관하게
# 해당 플러그인을 강제 활성화한다(서버는 머신종속 암호화 settings를 못 쓰므로 여기에 평문 저장).
openai_keys = []
anthropic_keys = []
default_model = "gemini-2.5-flash"

[chat]
# 라이브 채팅(커서 말풍선)을 boards/<id>/chat.log (jsonl) 에 누적 기록할지 여부.
log = true

# 로컬 계정. 비밀번호는 평문 금지 — `python -m server adduser <name>` 로 추가하면
# users.json 에 pbkdf2 해시로 저장됩니다. 아래 [users] 는 레벨 오버라이드 용도.
# 예) operator 계정 레벨 지정:
# [users]
# admin = 2

[oauth.mattermost]
# merri(Mattermost) OAuth2 연동. enabled=false 면 비활성.
enabled = false
base_url = "https://merri.example.com"   # Mattermost 서버 URL
client_id = ""
client_secret = ""
# OAuth 로그인 성공 시 부여할 기본 레벨
default_level = 1
# 이 팀/도메인 사용자만 허용(비우면 전체 허용)
allowed_email_domain = ""
"""


_DEFAULTS: Dict[str, Any] = {
    "server": {
        "host": "0.0.0.0",
        "port": 9700,
        "name": "Qonvo Server",
        "motd": "Welcome to Qonvo",
        "default_level": 1,
        "allow_guests": False,
    },
    "access": {"whitelist": False},
    "network": {"upnp": True, "public_host": "", "upnp_lease": 3600},
    "ai": {
        "gemini_keys": [],
        "openai_keys": [],
        "anthropic_keys": [],
        "default_model": "gemini-2.5-flash",
    },
    "chat": {"log": True},
    "users": {},
    "oauth": {
        "mattermost": {
            "enabled": False,
            "base_url": "",
            "client_id": "",
            "client_secret": "",
            "default_level": 1,
            "allowed_email_domain": "",
        }
    },
}


def _deep_merge(base: dict, over: dict) -> dict:
    """base 위에 over 를 재귀 병합한 새 dict 를 반환한다."""
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def ensure_config() -> Path:
    """config.toml 이 없으면 기본 템플릿을 생성하고 경로를 반환한다."""
    path = get_config_path()
    if not path.exists():
        path.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
    return path


def load_config() -> Dict[str, Any]:
    """config.toml 을 로드하고 기본값과 병합해 반환한다."""
    path = ensure_config()
    try:
        with open(path, "rb") as f:
            data = _toml.load(f)
    except Exception as e:  # 파싱 실패 시 기본값으로 동작
        print(f"[config] config.toml 파싱 실패, 기본값 사용: {e}")
        data = {}
    return _deep_merge(_DEFAULTS, data)
