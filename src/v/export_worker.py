"""
board export/import 등 무거운 파일 작업을 메인 스레드 밖에서 돌리는 범용 워커.

fn(progress) 형태의 콜러블 하나를 받아 백그라운드에서 실행한다.
  - progress(done, total, label) -> bool : board_export의 ProgressCb 규약. False=취소.
  - fn은 결과 dict를 반환 (또는 None).

시그널:
  progress(done, total, label) : 진행률 갱신 (메인 스레드로 큐잉)
  done(result_dict)            : 정상 완료
  failed(message)              : 예외 발생
  cancelled()                  : 사용자 취소 (ExportCancelled)
"""
from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from v.board_export import ExportCancelled
from v.logger import get_logger

logger = get_logger("qonvo.export")


class BoardTaskWorker(QThread):
    progress = pyqtSignal(int, int, str)
    done = pyqtSignal(dict)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def _progress(self, done: int, total: int, label: str) -> bool:
        # 워커 스레드에서 emit → 큐드 연결로 메인 스레드 슬롯 실행
        self.progress.emit(done, total, label)
        return not self._cancel

    def run(self):
        try:
            result = self._fn(self._progress)
            self.done.emit(result or {})
        except ExportCancelled:
            logger.info("[TASK] cancelled by user")
            self.cancelled.emit()
        except Exception as e:
            logger.error(f"[TASK] failed: {e}", exc_info=True)
            self.failed.emit(str(e))
