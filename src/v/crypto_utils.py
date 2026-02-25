"""
암호화 유틸리티
- Fernet 대칭 암호화 사용
- 키는 머신 고유 정보 기반 파생 (재현 가능)
"""
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64
import platform
import getpass


def _get_machine_key() -> bytes:
    """
    머신 고유 키 생성 (재현 가능)
    - 사용자명 + 호스트명 기반
    - PBKDF2로 키 파생
    """
    # 솔트: 고정값 (애플리케이션별 고유값)
    salt = b'Qonvo_v1_salt_2025'

    # 시드: 사용자명 + 호스트명
    seed = f"{getpass.getuser()}@{platform.node()}".encode('utf-8')

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    key = kdf.derive(seed)
    return base64.urlsafe_b64encode(key)


def encrypt_api_key(plaintext: str) -> str:
    """API 키 암호화 → base64 문자열 반환"""
    key = _get_machine_key()
    f = Fernet(key)
    encrypted = f.encrypt(plaintext.encode('utf-8'))
    return base64.b64encode(encrypted).decode('ascii')


def decrypt_api_key(encrypted_b64: str) -> str:
    """암호화된 API 키 복호화"""
    key = _get_machine_key()
    f = Fernet(key)
    encrypted = base64.b64decode(encrypted_b64.encode('ascii'))
    return f.decrypt(encrypted).decode('utf-8')


def is_encrypted(value: str) -> bool:
    """문자열이 암호화되었는지 판단 (휴리스틱)"""
    # Gemini API 키는 "AIza"로 시작
    if value.startswith("AIza"):
        return False
    # base64 형식이고 충분히 긴 경우 암호화된 것으로 간주
    try:
        base64.b64decode(value.encode('ascii'))
        return len(value) > 50  # 암호화된 키는 더 김
    except Exception:
        return False
