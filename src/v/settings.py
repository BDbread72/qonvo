"""
앱 설정 저장/로드
JSON 기반, AppData/Qonvo 폴더에 저장
메모리 캐싱으로 파일 I/O 최소화
"""
import json
import os
import threading
from pathlib import Path
from typing import Dict, Any

from v.provider import MODEL_OPTIONS, get_default_options

# 설정 캐시 (메모리) — D6: 스레드 안전성 보장
_settings_cache: Dict[str, Any] | None = None
_cache_mtime: float | None = None
_cache_generation: int = 0  # D7: mtime 정밀도 보완용 세대 카운터
_settings_lock = threading.Lock()


def _get_settings_path() -> Path:
    """설정 파일 경로"""
    if os.name == 'nt':  # Windows
        base = Path(os.environ.get('APPDATA', Path.home()))
    else:  # Linux/Mac
        base = Path.home() / '.config'

    settings_dir = base / 'Qonvo'
    settings_dir.mkdir(parents=True, exist_ok=True)
    return settings_dir / 'settings.json'


def _load_all() -> dict:
    """전체 설정 로드 (캐시 사용, 스레드 안전)"""
    global _settings_cache, _cache_mtime

    with _settings_lock:
        path = _get_settings_path()
        if not path.exists():
            _settings_cache = {}
            _cache_mtime = None
            return {}

        try:
            # 파일 수정 시간 확인
            current_mtime = path.stat().st_mtime

            # 캐시가 유효한 경우 캐시 반환
            if _settings_cache is not None and _cache_mtime == current_mtime:
                return _settings_cache.copy()

            # 파일에서 로드
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # 캐시 업데이트
            _settings_cache = data.copy()
            _cache_mtime = current_mtime
            return data

        except (json.JSONDecodeError, IOError) as e:
            from v.logger import get_logger
            logger = get_logger("qonvo.settings")
            logger.warning(f"Failed to load settings from {path}: {e}")
            return {}


def _save_all(data: dict):
    """전체 설정 저장 (원자적 쓰기 + 캐시 업데이트, 스레드 안전)"""
    global _settings_cache, _cache_mtime, _cache_generation

    with _settings_lock:
        path = _get_settings_path()
        tmp_path = path.with_suffix('.json.tmp')
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(str(tmp_path), str(path))
        except OSError:
            # os.replace 실패 시 (Windows 호환) shutil.move 시도
            import shutil
            shutil.move(str(tmp_path), str(path))

        # 캐시 업데이트 (D7: 세대 카운터로 mtime 정밀도 보완)
        _settings_cache = data.copy()
        _cache_mtime = path.stat().st_mtime
        _cache_generation += 1


# ============================================================
# 모델 옵션 관련
# ============================================================

def get_model_options(model: str) -> Dict[str, Any]:
    """
    모델별 저장된 옵션 반환.
    저장된 값이 없으면 기본값 사용.
    """
    data = _load_all()
    saved = data.get("model_options", {}).get(model, {})

    # 기본값에 저장된 값 덮어쓰기
    defaults = get_default_options(model)
    return {**defaults, **saved}


def save_model_options(model: str, options: Dict[str, Any]):
    """모델별 옵션 저장"""
    data = _load_all()

    if "model_options" not in data:
        data["model_options"] = {}

    data["model_options"][model] = options
    _save_all(data)


def reset_model_options(model: str):
    """모델 옵션 초기화 (기본값으로)"""
    data = _load_all()

    if "model_options" in data and model in data["model_options"]:
        del data["model_options"][model]
        _save_all(data)


# ============================================================
# API 키 관련
# ============================================================

def get_api_key() -> str | None:
    """
    API 키 반환 (우선순위: 환경변수 → 설정파일)
    설정파일에서 읽을 때 자동 복호화
    """
    env_key = os.environ.get("GEMINI_API_KEY")
    if env_key:
        return env_key

    # 암호화된 키 우선 확인
    encrypted = get_setting("api_key_encrypted")
    if encrypted:
        try:
            from v.crypto_utils import decrypt_api_key
            return decrypt_api_key(encrypted)
        except Exception as e:
            from v.logger import get_logger
            logger = get_logger("qonvo.settings")
            logger.warning(f"Failed to decrypt API key: {e}")
            return None

    # 하위 호환: 평문 키 확인 (마이그레이션 전)
    return get_setting("api_key")


def save_api_key(key: str):
    """API 키 암호화하여 저장"""
    from v.crypto_utils import encrypt_api_key
    encrypted = encrypt_api_key(key)

    data = _load_all()
    # 평문 키 삭제
    if "api_key" in data:
        del data["api_key"]
    data["api_key_encrypted"] = encrypted
    _save_all(data)


def has_api_key() -> bool:
    """API 키가 있는지 확인"""
    return bool(get_api_key())


def get_api_keys() -> list[str]:
    """모든 API 키 반환 (환경변수 + 설정파일). 중복 제거."""
    from v.crypto_utils import decrypt_api_key, is_encrypted

    keys = []

    # 1. 환경변수
    env_key = os.environ.get("GEMINI_API_KEY")
    if env_key:
        keys.append(env_key)

    # 2. 다중 키 리스트
    data = _load_all()
    encrypted_list = data.get("api_keys_encrypted", [])
    decrypt_failures = 0
    if isinstance(encrypted_list, list):
        for enc in encrypted_list:
            try:
                plaintext = decrypt_api_key(enc)
                if plaintext and plaintext not in keys:
                    keys.append(plaintext)
            except Exception:
                decrypt_failures += 1
                continue
    # D8: 모든 키 복호화 실패 시 경고 로그
    if decrypt_failures > 0 and not keys:
        from v.logger import get_logger
        logger = get_logger("qonvo.settings")
        logger.warning(
            f"All {decrypt_failures} encrypted API keys failed to decrypt. "
            "Keys may be corrupted or machine-specific encryption changed."
        )

    # 3. fallback: 단일 키
    if not keys:
        single = get_api_key()
        if single:
            keys.append(single)

    return keys


def save_api_keys(key_list: list[str]):
    """다중 API 키 암호화하여 저장"""
    from v.crypto_utils import encrypt_api_key

    data = _load_all()
    data["api_keys_encrypted"] = [encrypt_api_key(k) for k in key_list]
    # 하위 호환: 첫 번째 키를 단일 키 필드에도 저장
    if key_list:
        data["api_key_encrypted"] = encrypt_api_key(key_list[0])
    if "api_key" in data:
        del data["api_key"]
    _save_all(data)


# ============================================================
# 일반 설정
# ============================================================

def get_setting(key: str, default=None):
    """일반 설정값 가져오기"""
    data = _load_all()
    return data.get(key, default)


def set_setting(key: str, value):
    """일반 설정값 저장"""
    data = _load_all()
    data[key] = value
    _save_all(data)


# ============================================================
# 기본 모델
# ============================================================

def get_default_model() -> str | None:
    """기본 모델 ID 반환 (없으면 None → 첫 번째 모델 사용)"""
    return get_setting("default_model")


def set_default_model(model_id: str):
    """기본 모델 설정"""
    set_setting("default_model", model_id)


# ============================================================
# 최근 보드 개수
# ============================================================

def get_recent_boards_count() -> int:
    """최근 보드 표시 개수 (기본 5)"""
    from v.constants import DEFAULT_RECENT_BOARDS_COUNT
    return get_setting("recent_boards_count", DEFAULT_RECENT_BOARDS_COUNT)


def set_recent_boards_count(count: int):
    """최근 보드 표시 개수 설정"""
    from v.constants import MIN_RECENT_BOARDS_COUNT, MAX_RECENT_BOARDS_COUNT
    set_setting("recent_boards_count", max(MIN_RECENT_BOARDS_COUNT, min(MAX_RECENT_BOARDS_COUNT, count)))


# ============================================================
# 개발자 모드
# ============================================================

def is_developer_mode() -> bool:
    """개발자 모드 여부"""
    return bool(get_setting("developer_mode", False))


def set_developer_mode(enabled: bool):
    """개발자 모드 설정"""
    set_setting("developer_mode", enabled)


# ============================================================
# 보드 크기
# ============================================================

def get_board_size() -> int:
    """보드 크기 반환 (기본 10000)"""
    from v.constants import DEFAULT_BOARD_SIZE
    return get_setting("board_size", DEFAULT_BOARD_SIZE)


def set_board_size(size: int):
    """보드 크기 설정"""
    from v.constants import MIN_BOARD_SIZE, MAX_BOARD_SIZE
    set_setting("board_size", max(MIN_BOARD_SIZE, min(MAX_BOARD_SIZE, size)))


# ============================================================
# 언어
# ============================================================

def get_language() -> str:
    """언어 코드 반환 (기본 KR)"""
    return get_setting("language", "KR")


def set_language(lang: str):
    """언어 설정"""
    set_setting("language", lang)


# ============================================================
# 실험적 기능
# ============================================================

def is_experimental_mode() -> bool:
    """실험적 기능 활성화 여부"""
    return bool(get_setting("experimental_mode", False))


def set_experimental_mode(enabled: bool):
    """실험적 기능 설정"""
    set_setting("experimental_mode", enabled)


# ============================================================
# 모델 플러그인
# ============================================================

def get_enabled_plugins() -> list:
    """활성화된 플러그인 ID 목록"""
    return get_setting("enabled_plugins", [])


def set_enabled_plugins(plugin_ids: list):
    """활성화 플러그인 목록 저장"""
    set_setting("enabled_plugins", plugin_ids)


def get_plugin_api_keys(plugin_id: str) -> list[str]:
    """플러그인별 API 키 목록 반환 (복호화)"""
    from v.crypto_utils import decrypt_api_key
    data = _load_all()
    encrypted_list = data.get("plugin_api_keys", {}).get(plugin_id, [])
    keys = []
    for enc in encrypted_list:
        try:
            keys.append(decrypt_api_key(enc))
        except Exception:
            continue
    return keys


def save_plugin_api_keys(plugin_id: str, keys: list[str]):
    """플러그인별 API 키 저장 (암호화)"""
    from v.crypto_utils import encrypt_api_key
    data = _load_all()
    if "plugin_api_keys" not in data:
        data["plugin_api_keys"] = {}
    data["plugin_api_keys"][plugin_id] = [encrypt_api_key(k) for k in keys]
    _save_all(data)


# ============================================================
# 앱 데이터 경로
# ============================================================

def get_app_data_path() -> Path:
    """AppData/Qonvo 경로 반환"""
    if os.name == 'nt':  # Windows
        base = Path(os.environ.get('APPDATA', Path.home()))
    else:  # Linux/Mac
        base = Path.home() / '.config'

    app_dir = base / 'Qonvo'
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


# ============================================================
# API 키 마이그레이션
# ============================================================

def migrate_plaintext_api_key():
    """
    평문 API 키를 암호화된 키로 마이그레이션
    앱 시작 시 1회 실행
    """
    data = _load_all()
    plaintext_key = data.get("api_key")

    if plaintext_key and isinstance(plaintext_key, str):
        # 평문 키가 존재하면 암호화하여 저장
        try:
            from v.crypto_utils import encrypt_api_key, decrypt_api_key, is_encrypted
            if not is_encrypted(plaintext_key):
                # 백업 생성
                backup_path = _get_settings_path().with_suffix('.json.backup')
                import shutil
                shutil.copy(_get_settings_path(), backup_path)

                # D4: 암호화 → 복호화 검증 → 성공 후에만 평문 삭제
                encrypted = encrypt_api_key(plaintext_key)
                # 복호화 검증 (라운드트립 확인)
                decrypted = decrypt_api_key(encrypted)
                if decrypted != plaintext_key:
                    raise ValueError("Encryption round-trip verification failed")

                data["api_key_encrypted"] = encrypted
                del data["api_key"]
                _save_all(data)

                from v.logger import get_logger
                logger = get_logger("qonvo.settings")
                logger.info("API key migrated to encrypted storage")
        except Exception as e:
            from v.logger import get_logger
            logger = get_logger("qonvo.settings")
            logger.error(f"Failed to migrate API key: {e}")
            # D4: 마이그레이션 실패 시 평문 키 보존 (삭제하지 않음)
