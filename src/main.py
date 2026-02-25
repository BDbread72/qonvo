"""애플리케이션 전역 예외 및 스레드 예외를 로깅하는 진입점 모듈."""

import faulthandler
import sys
import os
import threading
import traceback
from datetime import datetime

# 로그 디렉터리를 사용자 APPDATA 하위에 생성
log_dir = os.path.join(os.environ.get("APPDATA", ""), "Qonvo", "logs")
os.makedirs(log_dir, exist_ok=True)
# 크래시 로그 파일 핸들러를 열어 faulthandler에 연결
_fault_log = open(os.path.join(log_dir, "crash.log"), "a")
faulthandler.enable(file=_fault_log)


def _log_exception(exc_type, exc_value, exc_tb):
    """처리되지 않은 예외를 포맷팅해 파일과 로거에 기록한다."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]  # 밀리초까지 포함한 타임스탬프
    tb_lines = traceback.format_exception(exc_type, exc_value, exc_tb)  # 스택트레이스 문자열 리스트
    msg = f"\n[{ts}] UNHANDLED EXCEPTION\n{''.join(tb_lines)}"
    _fault_log.write(msg)
    _fault_log.flush()
    try:
        # 로거가 준비되어 있으면 치명적 예외도 함께 기록
        from v.logger import get_logger
        logger = get_logger("qonvo.crash")
        logger.critical(f"Unhandled exception: {exc_type.__name__}: {exc_value}")
    except Exception:
        # 로거 초기화 실패 등은 무시
        pass


def _thread_exception(args):
    """스레드 예외 훅에서 호출되어 스레드별 예외를 기록한다."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    tb_lines = traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
    thread_name = args.thread.name if args.thread else "unknown"  # 스레드가 없으면 이름 대체
    msg = f"\n[{ts}] THREAD EXCEPTION (thread={thread_name})\n{''.join(tb_lines)}"
    _fault_log.write(msg)
    _fault_log.flush()


def _unraisable_exception(hook_args):
    """파이썬의 unraisable 예외를 기록한다(예: __del__ 내부 오류)."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    tb_lines = traceback.format_exception(
        type(hook_args.exc_value), hook_args.exc_value, hook_args.exc_traceback
    )
    obj_repr = repr(hook_args.object) if hook_args.object is not None else "None"
    msg = f"\n[{ts}] UNRAISABLE EXCEPTION (object={obj_repr})\n{''.join(tb_lines)}"
    _fault_log.write(msg)
    _fault_log.flush()


# 전역 예외 훅 등록
sys.excepthook = _log_exception
# 스레드 예외 훅 등록 (Python 3.8+)
threading.excepthook = _thread_exception
# unraisable 예외 훅 등록
sys.unraisablehook = _unraisable_exception

from v import ui
from v import app

if __name__ == "__main__":
    mapp = app.App()
    ui.run_app(mapp)
