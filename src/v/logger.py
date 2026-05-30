"""
로깅 설정
- SQLite DB 로깅 (AppData/Qonvo/logs/qonvo.db)
- 파일 로깅 백업 (AppData/Qonvo/logs/qonvo.log)
- 30일 이상 된 로그 자동 정리
"""
import logging
import sqlite3
import threading
import time
from logging.handlers import RotatingFileHandler
import sys
from pathlib import Path

_logger_initialized = False
_db_handler = None


class SafeRotatingFileHandler(RotatingFileHandler):
    """Windows에서 다른 프로세스/인스턴스가 로그 파일을 잡고 있어 rename이
    실패(WinError 32)해도 앱이 죽거나 콘솔에 트레이스백을 쏟지 않도록 방어한다.

    rollover 실패 시: 회전을 건너뛰고 기존 파일에 계속 append (로그 유실보다 안전).
    """

    def doRollover(self):
        try:
            super().doRollover()
        except (OSError, PermissionError):
            # rename 잠김 등 — 회전 포기하고 스트림만 다시 연다.
            try:
                if self.stream:
                    self.stream.close()
            except Exception:
                pass
            self.stream = self._open()


class SQLiteLogHandler(logging.Handler):
    """SQLite DB에 로그를 저장하는 핸들러."""

    def __init__(self, db_path: str):
        super().__init__()
        self._db_path = db_path
        self._local = threading.local()
        # 메인 스레드에서 테이블 생성
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                level TEXT NOT NULL,
                logger TEXT NOT NULL,
                message TEXT NOT NULL,
                func TEXT,
                lineno INTEGER
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_logs_level ON logs(level)
        """)
        conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        """스레드별 DB 연결 반환."""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(self._db_path, timeout=5)
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
        return self._local.conn

    def emit(self, record):
        try:
            conn = self._get_conn()
            conn.execute(
                "INSERT INTO logs (timestamp, level, logger, message, func, lineno) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(record.created))
                    + f".{int(record.msecs):03d}",
                    record.levelname,
                    record.name,
                    self.format(record) if self.formatter else record.getMessage(),
                    record.funcName,
                    record.lineno,
                ),
            )
            conn.commit()
        except Exception:
            pass  # DB 오류로 앱 크래시 방지

    def cleanup_old(self, days: int = 30):
        """오래된 로그 삭제."""
        try:
            conn = self._get_conn()
            cutoff = time.strftime(
                '%Y-%m-%d', time.localtime(time.time() - days * 86400)
            )
            conn.execute("DELETE FROM logs WHERE timestamp < ?", (cutoff,))
            conn.commit()
        except Exception:
            pass

    def close(self):
        try:
            if hasattr(self._local, 'conn') and self._local.conn:
                self._local.conn.close()
                self._local.conn = None
        except Exception:
            pass
        super().close()


def setup_logger():
    """로거 초기화 (앱 시작 시 1회 호출)"""
    global _logger_initialized, _db_handler
    if _logger_initialized:
        return

    from v.settings import get_app_data_path
    log_dir = get_app_data_path() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("qonvo")
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return

    # 1. SQLite DB 핸들러 (주 저장소)
    db_file = log_dir / "qonvo.db"
    try:
        _db_handler = SQLiteLogHandler(str(db_file))
        _db_handler.setLevel(logging.INFO)
        _db_handler.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(_db_handler)
        # 30일 이상 된 로그 정리
        _db_handler.cleanup_old(30)
    except Exception:
        pass

    # 2. 파일 핸들러 (백업, 로테이션)
    log_file = log_dir / "qonvo.log"
    try:
        file_handler = SafeRotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding='utf-8',
            delay=True,  # 첫 emit 전까지 파일 안 열어 다중 인스턴스 잠금 충돌 완화
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))
        logger.addHandler(file_handler)
    except Exception:
        pass

    # 3. 콘솔 핸들러 (개발자 모드)
    from v.settings import is_developer_mode
    if is_developer_mode():
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.DEBUG)
        console_handler.setFormatter(logging.Formatter(
            '%(levelname)s - %(name)s - %(message)s'
        ))
        logger.addHandler(console_handler)

    _logger_initialized = True


def get_logger(name: str = "qonvo"):
    """로거 인스턴스 반환"""
    return logging.getLogger(name)
