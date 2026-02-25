"""
테마 시스템
하드코딩된 색상 값들을 중앙에서 관리
"""

class Theme:
    """다크 테마 색상 정의"""

    # ============================================================
    # 기본 배경색
    # ============================================================
    BG_PRIMARY = "#1a1a1a"      # 메인 배경
    BG_SECONDARY = "#2d2d2d"    # 보조 배경 (카드, 패널)
    BG_TERTIARY = "#1e1e1e"     # 3차 배경 (노드)
    BG_HOVER = "#3a3a3a"        # 호버 상태
    BG_INPUT = "#252525"        # 입력 필드

    # ============================================================
    # 텍스트 색상
    # ============================================================
    TEXT_PRIMARY = "#ddd"       # 주요 텍스트
    TEXT_SECONDARY = "#999"     # 보조 텍스트
    TEXT_TERTIARY = "#666"      # 3차 텍스트 (힌트)
    TEXT_DISABLED = "#555"      # 비활성 텍스트

    # ============================================================
    # 강조 색상
    # ============================================================
    ACCENT_PRIMARY = "#0d6efd"  # 주요 강조 (파란색)
    ACCENT_HOVER = "#0b5ed7"    # 강조 호버
    ACCENT_SUCCESS = "#28a745"  # 성공 (녹색)
    ACCENT_WARNING = "#ffc107"  # 경고 (노란색)
    ACCENT_DANGER = "#dc3545"   # 위험 (빨간색)

    # ============================================================
    # 노드 색상
    # ============================================================
    NODE_HEADER = "#2a2a2a"     # 노드 헤더
    NODE_BORDER = "#5a6a7a"     # 노드 테두리
    NODE_SHADOW = "rgba(0, 0, 0, 0.3)"  # 그림자

    # ============================================================
    # 그리드 및 도트
    # ============================================================
    GRID_DOT = "#252525"        # 그리드 도트
    GRID_LINE = "#333"          # 그리드 라인

    # ============================================================
    # 포트 색상 (타입별)
    # ============================================================
    PORT_EXEC = "#ffffff"       # 실행 포트
    PORT_STRING = "#ff6ec7"     # 문자열 포트
    PORT_NUMBER = "#a0d911"     # 숫자 포트
    PORT_BOOLEAN = "#ff4d4f"    # 불린 포트
    PORT_OBJECT = "#1890ff"     # 객체 포트
    PORT_ANY = "#8c8c8c"        # Any 포트

    # ============================================================
    # 상태 색상
    # ============================================================
    STATE_PINNED = "#ffa500"    # 고정된 항목
    STATE_SELECTED = "#0d6efd"  # 선택된 항목
    STATE_ACTIVE = "#28a745"    # 활성 상태

    # ============================================================
    # 펄스 애니메이션 색상 (챗 노드 로딩)
    # ============================================================
    PULSE_START = (45, 45, 45)   # RGB 튜플
    PULSE_END = (30, 144, 255)   # RGB 튜플 (dodgerblue)


# 하위 호환을 위한 전역 함수
def get_color(key: str) -> str:
    """
    색상 키로 색상 값 가져오기
    예: get_color("BG_PRIMARY") -> "#1a1a1a"
    """
    return getattr(Theme, key, "#ffffff")


# CSS 스타일 생성 헬퍼
def get_stylesheet(base_style: str, **color_overrides) -> str:
    """
    테마 색상을 적용한 CSS 스타일시트 생성

    Args:
        base_style: 기본 CSS 템플릿 (색상 플레이스홀더 포함)
        **color_overrides: 기본 테마 색상 오버라이드

    Returns:
        색상이 적용된 CSS 스타일시트
    """
    colors = {
        "bg_primary": Theme.BG_PRIMARY,
        "bg_secondary": Theme.BG_SECONDARY,
        "bg_tertiary": Theme.BG_TERTIARY,
        "bg_hover": Theme.BG_HOVER,
        "text_primary": Theme.TEXT_PRIMARY,
        "text_secondary": Theme.TEXT_SECONDARY,
        "accent_primary": Theme.ACCENT_PRIMARY,
        **color_overrides
    }

    return base_style.format(**colors)
