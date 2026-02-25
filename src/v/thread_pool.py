"""
Phase 4: QThreadPool 싱글톤

여러 API 요청을 병렬로 처리하기 위한 스레드 풀 관리
Gemini SDK는 동기 전용이므로, QThreadPool + QRunnable로 병렬화
최대 4개까지 동시 요청 가능
"""
from PyQt6.QtCore import QThreadPool

_thread_pool: QThreadPool | None = None


def get_thread_pool() -> QThreadPool:
    """글로벌 스레드 풀 인스턴스 반환 (싱글톤)

    최대 4개의 워커를 동시에 실행할 수 있음
    """
    global _thread_pool
    if _thread_pool is None:
        _thread_pool = QThreadPool.globalInstance()
        # 최대 동시 실행 워커 수 제한
        # 너무 많으면 API 레이트 제한, 너무 적으면 성능 저하
        _thread_pool.setMaxThreadCount(4)
    return _thread_pool


def reset_thread_pool():
    """스레드 풀 리셋 (테스트 용도)"""
    global _thread_pool
    _thread_pool = None
