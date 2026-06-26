"""애플리케이션 전역 예외 및 스레드 예외를 로깅하는 진입점 모듈."""

import faulthandler
import sys
import os
os.environ["QT_LOGGING_RULES"] = "qt.qpa.screen=false"

# Windows + Python 3.14에서 aiohttp 등이 platform.uname()/win32_ver()를 호출하면
# 내부적으로 WMI 쿼리(_wmi_query)가 일어나는데, WMI 서비스가 hang 상태이면 앱이 통째로 멈춤.
# _wmi_query를 OSError로 강제 실패시키면 platform 모듈이 레지스트리/환경변수 fallback을 탐.
if sys.platform == "win32":
    import platform as _platform
    def _no_wmi(*_a, **_k):
        raise OSError("WMI disabled (qonvo bypass)")
    _platform._wmi_query = _no_wmi
    # 일부 캐시도 미리 채워서 fallback 자체도 빠르게
    _wv = sys.getwindowsversion()
    _build = f"{_wv.major}.{_wv.minor}.{_wv.build}"
    _release = "11" if _wv.major == 10 and _wv.build >= 22000 else str(_wv.major)
    _platform.win32_ver = lambda: (_release, _build, "", "")
import threading
import traceback
from datetime import datetime

# 로그 디렉터리를 사용자 데이터 폴더 하위에 생성 (Windows: %APPDATA%, Linux/Mac: ~/.config)
if os.name == "nt":
    _data_base = os.environ.get("APPDATA", os.path.expanduser("~"))
else:
    _data_base = os.path.join(os.path.expanduser("~"), ".config")
log_dir = os.path.join(_data_base, "Qonvo", "logs")
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
    try:
        # 크래시를 로컬 큐에 적어 다음 서버 접속 때 업로드(crash_reporter)
        from v import crash_reporter
        crash_reporter.report("crash", f"{exc_type.__name__}: {exc_value}",
                              "".join(tb_lines))
        crash_reporter.note_fatal()  # 세션 마커에 표시 → 다음 실행 중복 보고 방지
    except Exception:
        pass


def _thread_exception(args):
    """스레드 예외 훅에서 호출되어 스레드별 예외를 기록한다."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    tb_lines = traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
    thread_name = args.thread.name if args.thread else "unknown"  # 스레드가 없으면 이름 대체
    msg = f"\n[{ts}] THREAD EXCEPTION (thread={thread_name})\n{''.join(tb_lines)}"
    _fault_log.write(msg)
    _fault_log.flush()
    try:
        # 스레드 예외는 앱을 즉사시키진 않으므로 보고만 하고 note_fatal 은 하지 않는다
        # (이후 하드 종료가 나면 그건 별도의 unexpected_exit 로 잡혀야 함).
        from v import crash_reporter
        crash_reporter.report("thread", f"{args.exc_type.__name__}: {args.exc_value}",
                              "".join(tb_lines))
    except Exception:
        pass


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
    try:
        from v import crash_reporter
        crash_reporter.report("unraisable",
                              f"{type(hook_args.exc_value).__name__}: {hook_args.exc_value}",
                              "".join(tb_lines))
    except Exception:
        pass


# 전역 예외 훅 등록
sys.excepthook = _log_exception
# 스레드 예외 훅 등록 (Python 3.8+)
threading.excepthook = _thread_exception
# unraisable 예외 훅 등록
sys.unraisablehook = _unraisable_exception

from v import ui
from v import app

if __name__ == "__main__":
    # 크래시 리포터: 컨텍스트 설정 + 세션 센티넬 시작.
    # begin_session 은 '지난 세션이 정상 종료됐는지'를 마커로 확인해, 정상 종료가
    # 아니었으면(=segfault/강제종료/전원차단 등 예외 훅 미경유 비정상 종료) 보고를
    # 큐에 넣는다. 이후 서버 접속 시 업로드된다.
    try:
        from v import crash_reporter
        from v.board import _get_display_version
        from v.settings import get_setting
        _ver = _get_display_version() or ""
        crash_reporter.set_context(
            version=_ver, enabled=get_setting("crash_report_enabled", True))
        crash_reporter.begin_session(_ver)
    except Exception:
        pass

    try:
        mapp = app.App()
        ui.run_app(mapp)
    finally:
        # 여기까지 정상 도달 = Qt 이벤트 루프가 정상 종료됨 → 세션을 clean 으로 표시.
        # (segfault/강제종료면 이 finally 가 실행 안 돼 마커가 unclean 으로 남아
        #  다음 실행에서 비정상 종료로 감지된다.)
        try:
            from v import crash_reporter
            crash_reporter.end_session()
        except Exception:
            pass
