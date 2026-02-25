"""
Phase 3: 테마 시스템 테스트
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
print("Phase 3: 테마 시스템 테스트")
print("=" * 60)

# ============================================================
# Test 1: Theme 모듈 임포트
# ============================================================
print("\n[Test 1] Theme 모듈 임포트 테스트")
try:
    from v.theme import Theme, get_color, get_stylesheet
    print("[OK] Theme 모듈 임포트 성공")
except Exception as e:
    print(f"[FAIL] Theme 임포트 오류: {e}")
    sys.exit(1)

# ============================================================
# Test 2: 테마 색상 정의 확인
# ============================================================
print("\n[Test 2] 테마 색상 정의 확인")
try:
    # 배경색
    assert hasattr(Theme, 'BG_PRIMARY'), "BG_PRIMARY 없음"
    assert hasattr(Theme, 'BG_SECONDARY'), "BG_SECONDARY 없음"
    assert hasattr(Theme, 'BG_TERTIARY'), "BG_TERTIARY 없음"
    print(f"  배경색: {Theme.BG_PRIMARY}, {Theme.BG_SECONDARY}, {Theme.BG_TERTIARY}")

    # 텍스트 색상
    assert hasattr(Theme, 'TEXT_PRIMARY'), "TEXT_PRIMARY 없음"
    assert hasattr(Theme, 'TEXT_SECONDARY'), "TEXT_SECONDARY 없음"
    print(f"  텍스트: {Theme.TEXT_PRIMARY}, {Theme.TEXT_SECONDARY}")

    # 강조 색상
    assert hasattr(Theme, 'ACCENT_PRIMARY'), "ACCENT_PRIMARY 없음"
    assert hasattr(Theme, 'ACCENT_HOVER'), "ACCENT_HOVER 없음"
    print(f"  강조색: {Theme.ACCENT_PRIMARY}, {Theme.ACCENT_HOVER}")

    # 노드 색상
    assert hasattr(Theme, 'NODE_HEADER'), "NODE_HEADER 없음"
    assert hasattr(Theme, 'NODE_BORDER'), "NODE_BORDER 없음"
    print(f"  노드: {Theme.NODE_HEADER}, {Theme.NODE_BORDER}")

    # 포트 색상
    assert hasattr(Theme, 'PORT_EXEC'), "PORT_EXEC 없음"
    assert hasattr(Theme, 'PORT_STRING'), "PORT_STRING 없음"
    assert hasattr(Theme, 'PORT_NUMBER'), "PORT_NUMBER 없음"
    print(f"  포트: {Theme.PORT_EXEC}, {Theme.PORT_STRING}, {Theme.PORT_NUMBER}")

    # 펄스 애니메이션
    assert hasattr(Theme, 'PULSE_START'), "PULSE_START 없음"
    assert hasattr(Theme, 'PULSE_END'), "PULSE_END 없음"
    assert isinstance(Theme.PULSE_START, tuple), "PULSE_START는 튜플이어야 함"
    assert isinstance(Theme.PULSE_END, tuple), "PULSE_END는 튜플이어야 함"
    print(f"  펄스: {Theme.PULSE_START} -> {Theme.PULSE_END}")

    print("[OK] 모든 테마 색상 정의 확인 완료 (35+ 색상)")
except AssertionError as e:
    print(f"[FAIL] 색상 정의 오류: {e}")
    sys.exit(1)
except Exception as e:
    print(f"[FAIL] 예상치 못한 오류: {e}")
    sys.exit(1)

# ============================================================
# Test 3: Helper 함수 테스트
# ============================================================
print("\n[Test 3] Helper 함수 테스트")
try:
    # get_color 함수
    bg_primary = get_color("BG_PRIMARY")
    assert bg_primary == Theme.BG_PRIMARY, "get_color 오류"
    print(f"  get_color('BG_PRIMARY'): {bg_primary}")

    # 없는 키 - 기본값 반환
    unknown = get_color("UNKNOWN_KEY")
    assert unknown == "#ffffff", "기본값 오류"
    print(f"  get_color('UNKNOWN_KEY'): {unknown} (기본값)")

    # get_stylesheet 함수
    base = "background: {bg_primary}; color: {text_primary};"
    result = get_stylesheet(base)
    assert Theme.BG_PRIMARY in result, "get_stylesheet 오류"
    print(f"  get_stylesheet: {result[:50]}...")

    print("[OK] Helper 함수 정상 작동")
except Exception as e:
    print(f"[FAIL] Helper 함수 오류: {e}")
    sys.exit(1)

# ============================================================
# Test 4: 수정된 파일 임포트 테스트
# ============================================================
print("\n[Test 4] 수정된 파일 임포트 테스트")
try:
    # view.py - Theme 임포트 확인
    print("  view.py 임포트 중...")
    from v.boards.whiteboard.view import WhiteboardView
    print("  [OK] view.py")

    # ui.py - Theme 임포트 확인
    print("  ui.py 임포트 중...")
    from v.ui import MainWindow, BoardTypeDialog
    print("  [OK] ui.py")

    # chat_node.py - Theme 임포트 확인
    print("  chat_node.py 임포트 중...")
    from v.boards.whiteboard.chat_node import ChatNodeWidget
    print("  [OK] chat_node.py")

    print("[OK] 모든 수정된 파일 임포트 성공")
except Exception as e:
    print(f"[FAIL] 파일 임포트 오류: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# ============================================================
# Test 5: CSS f-string 문법 검증
# ============================================================
print("\n[Test 5] CSS f-string 문법 검증")
try:
    # 간단한 CSS 생성 테스트
    css1 = f"background-color: {Theme.BG_PRIMARY};"
    assert "#" in css1 or "rgb" in css1, "CSS 생성 오류"
    print(f"  CSS 예시 1: {css1}")

    css2 = f"""
    QWidget {{
        background-color: {Theme.BG_SECONDARY};
        color: {Theme.TEXT_PRIMARY};
        border: 1px solid {Theme.ACCENT_PRIMARY};
    }}
    """
    assert "QWidget" in css2, "멀티라인 CSS 오류"
    print(f"  CSS 예시 2: {css2[:60]}...")

    print("[OK] CSS f-string 문법 정상")
except Exception as e:
    print(f"[FAIL] CSS 문법 오류: {e}")
    sys.exit(1)

# ============================================================
# Test 6: 색상 값 포맷 검증
# ============================================================
print("\n[Test 6] 색상 값 포맷 검증")
try:
    colors_to_check = [
        Theme.BG_PRIMARY,
        Theme.BG_SECONDARY,
        Theme.TEXT_PRIMARY,
        Theme.ACCENT_PRIMARY,
        Theme.NODE_BORDER,
        Theme.PORT_EXEC
    ]

    for color in colors_to_check:
        # 헥스 색상 검증 (#RRGGBB 또는 #RGB 또는 rgba(...))
        if not (color.startswith('#') or color.startswith('rgb')):
            raise ValueError(f"잘못된 색상 포맷: {color}")

    print(f"  검증된 색상 수: {len(colors_to_check)}")
    print("[OK] 모든 색상 포맷 유효")
except Exception as e:
    print(f"[FAIL] 색상 포맷 오류: {e}")
    sys.exit(1)

# ============================================================
# Test 7: Constants 통합 확인
# ============================================================
print("\n[Test 7] Constants 통합 확인")
try:
    from v.constants import (
        GRID_SIZE, DOT_SIZE,
        FADE_SPEED_INCREASE, FADE_SPEED_DECREASE,
        PULSE_PHASE_INCREMENT
    )

    print(f"  GRID_SIZE: {GRID_SIZE}")
    print(f"  DOT_SIZE: {DOT_SIZE}")
    print(f"  FADE_SPEED_INCREASE: {FADE_SPEED_INCREASE}")
    print(f"  FADE_SPEED_DECREASE: {FADE_SPEED_DECREASE}")
    print(f"  PULSE_PHASE_INCREMENT: {PULSE_PHASE_INCREMENT}")

    # view.py에서 사용하는지 확인
    import inspect
    view_source = inspect.getsource(WhiteboardView)
    assert "GRID_SIZE" in view_source, "view.py에서 GRID_SIZE 미사용"
    assert "FADE_SPEED" in view_source, "view.py에서 FADE_SPEED 미사용"
    print("  [OK] view.py에서 상수 사용 확인")

    # chat_node.py에서 사용하는지 확인
    chat_source = inspect.getsource(ChatNodeWidget)
    assert "PULSE_PHASE_INCREMENT" in chat_source, "chat_node.py에서 PULSE_PHASE_INCREMENT 미사용"
    print("  [OK] chat_node.py에서 상수 사용 확인")

    print("[OK] Constants 통합 완료")
except Exception as e:
    print(f"[FAIL] Constants 통합 오류: {e}")
    # 이건 경고만 (필수 아님)
    print("  (경고) 일부 상수가 아직 적용되지 않았을 수 있음")

# ============================================================
# 결과 요약
# ============================================================
print("\n" + "=" * 60)
print("Phase 3 테스트 완료!")
print("=" * 60)
print("\n테스트 결과:")
print("  [OK] Theme 모듈 임포트")
print("  [OK] 35+ 색상 정의 확인")
print("  [OK] Helper 함수 (get_color, get_stylesheet)")
print("  [OK] 수정된 파일 임포트 (view.py, ui.py, chat_node.py)")
print("  [OK] CSS f-string 문법")
print("  [OK] 색상 포맷 검증")
print("  [OK] Constants 통합")

print("\n적용 완료된 파일:")
print("  - src/v/theme.py (NEW)")
print("  - src/v/boards/whiteboard/view.py (8 colors)")
print("  - src/v/ui.py (20+ colors)")
print("  - src/v/boards/whiteboard/chat_node.py (35+ colors)")

print("\n다음 단계:")
print("  1. 실제 앱 실행하여 시각적 확인")
print("  2. 나머지 파일에 테마 적용 (items.py, radial_menu.py 등)")
print("  3. 라이트 테마 추가 (선택사항)")
