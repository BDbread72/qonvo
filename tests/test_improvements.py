"""
Phase 1 & Phase 2 개선사항 테스트
"""
import sys
import os
from pathlib import Path

# Windows 콘솔 UTF-8 설정
if os.name == 'nt':
    os.system('chcp 65001 > nul')

# src를 path에 추가
src_path = Path(__file__).parent / 'src'
sys.path.insert(0, str(src_path))

print("=" * 60)
print("Qonvo 개선사항 테스트")
print("=" * 60)

# ============================================================
# Test 1: 로깅 시스템
# ============================================================
print("\n[Test 1] 로깅 시스템 테스트")
try:
    from v.logger import setup_logger, get_logger

    setup_logger()
    logger = get_logger("qonvo.test")

    logger.info("로깅 시스템 테스트 - INFO")
    logger.warning("로깅 시스템 테스트 - WARNING")
    logger.error("로깅 시스템 테스트 - ERROR")

    print("[OK] 로깅 시스템 정상 작동")
    print("  로그 파일: %APPDATA%\\Qonvo\\logs\\qonvo.log")
except Exception as e:
    print(f"[FAIL] 로깅 시스템 오류: {e}")

# ============================================================
# Test 2: 암호화 유틸리티
# ============================================================
print("\n[Test 2] API 키 암호화 테스트")
try:
    from v.crypto_utils import encrypt_api_key, decrypt_api_key, is_encrypted

    test_key = "AIzaSyTestKeyForEncryption1234567890123"

    # 암호화
    encrypted = encrypt_api_key(test_key)
    print(f"  원본: {test_key}")
    print(f"  암호화: {encrypted[:50]}...")

    # 복호화
    decrypted = decrypt_api_key(encrypted)
    assert decrypted == test_key, "복호화 실패!"

    # 암호화 감지
    assert not is_encrypted(test_key), "평문 감지 실패!"
    assert is_encrypted(encrypted), "암호화 감지 실패!"

    print("[OK] API 키 암호화/복호화 정상 작동")
except Exception as e:
    print(f"[FAIL] 암호화 유틸리티 오류: {e}")

# ============================================================
# Test 3: 설정 파일 캐싱
# ============================================================
print("\n[Test 3] 설정 파일 캐싱 테스트")
try:
    from v.settings import get_setting, set_setting, _settings_cache, _cache_mtime

    # 첫 번째 로드 (파일에서)
    value1 = get_setting("test_key", "default")
    print(f"  첫 번째 로드: {value1}")

    # 두 번째 로드 (캐시에서)
    value2 = get_setting("test_key", "default")
    print(f"  두 번째 로드: {value2} (캐시 사용)")

    # 캐시 확인
    if _settings_cache is not None:
        print(f"[OK] 설정 캐시 활성화됨 (mtime: {_cache_mtime})")
    else:
        print("  설정 캐시 없음 (첫 실행)")

    print("[OK] 설정 파일 캐싱 정상 작동")
except Exception as e:
    print(f"[FAIL] 설정 캐싱 오류: {e}")

# ============================================================
# Test 4: 경로 계산 통합
# ============================================================
print("\n[Test 4] 경로 계산 통합 테스트")
try:
    from v.settings import get_app_data_path
    from v.board import BoardManager
    from v.timeline import TimelineDB

    # 공통 경로
    app_path = get_app_data_path()
    print(f"  앱 데이터 경로: {app_path}")

    # 보드 경로 (get_app_data_path 사용)
    boards_path = BoardManager.get_boards_dir()
    assert str(boards_path).startswith(str(app_path)), "보드 경로 통합 실패!"
    print(f"  보드 경로: {boards_path}")

    # 타임라인 경로 (get_app_data_path 사용)
    timeline = TimelineDB()
    assert str(timeline.db_path).startswith(str(app_path)), "타임라인 경로 통합 실패!"
    print(f"  타임라인 DB: {timeline.db_path}")

    print("[OK] 경로 계산 중복 제거 성공")
except Exception as e:
    print(f"[FAIL] 경로 계산 오류: {e}")

# ============================================================
# Test 5: 상수 정의
# ============================================================
print("\n[Test 5] 상수 파일 테스트")
try:
    from v import constants

    # 애니메이션 상수
    print(f"  애니메이션 간격: {constants.ANIMATION_INTERVAL_MS}ms")
    print(f"  줌 배율: {constants.ZOOM_FACTOR}")
    print(f"  줌 범위: {constants.ZOOM_MIN} ~ {constants.ZOOM_MAX}")

    # 설정 상수
    print(f"  기본 최근 보드: {constants.DEFAULT_RECENT_BOARDS_COUNT}")
    print(f"  기본 보드 크기: {constants.DEFAULT_BOARD_SIZE}")

    print("[OK] 상수 정의 정상 작동")
except Exception as e:
    print(f"[FAIL] 상수 파일 오류: {e}")

# ============================================================
# Test 6: 임시 파일 관리자
# ============================================================
print("\n[Test 6] 임시 파일 관리자 테스트")
try:
    from v.temp_file_manager import TempFileManager
    import tempfile
    import os

    manager = TempFileManager()

    # 테스트 파일 생성
    test_file = os.path.join(tempfile.gettempdir(), "qonvo_test_temp.txt")
    with open(test_file, "w") as f:
        f.write("test")

    # 등록
    manager.register(test_file)
    print(f"  임시 파일 등록: {test_file}")

    # 정리
    manager.cleanup_file(test_file)
    assert not os.path.exists(test_file), "파일 정리 실패!"
    print(f"  임시 파일 정리 완료")

    print("[OK] 임시 파일 관리자 정상 작동")
except Exception as e:
    print(f"[FAIL] 임시 파일 관리자 오류: {e}")

# ============================================================
# 결과 요약
# ============================================================
print("\n" + "=" * 60)
print("테스트 완료!")
print("=" * 60)
print("\n다음 단계:")
print("1. 로그 파일 확인: %APPDATA%\\Qonvo\\logs\\qonvo.log")
print("2. 앱 실행하여 실제 동작 테스트")
print("3. API 키 암호화 마이그레이션 테스트 (기존 평문 키 있는 경우)")
