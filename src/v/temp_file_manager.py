"""
임시 파일 관리자
- 앱 시작/종료 시 정리
- 노드별 임시 파일 추적
"""
import os
import tempfile
import time
from pathlib import Path
from typing import Set


class TempFileManager:
    """임시 파일 관리 싱글톤"""

    _instance = None
    _temp_files: Set[str] = set()  # 현재 세션에서 생성한 파일들

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def register(self, filepath: str):
        """임시 파일 등록 (추적 시작)"""
        self._temp_files.add(filepath)

    def unregister(self, filepath: str):
        """임시 파일 등록 해제 (이미 정리됨)"""
        self._temp_files.discard(filepath)

    def cleanup_file(self, filepath: str):
        """특정 파일 정리"""
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
            self.unregister(filepath)
        except Exception as e:
            from v.logger import get_logger
            logger = get_logger("qonvo.temp_file_manager")
            logger.warning(f"Failed to cleanup temp file {filepath}: {e}")

    def cleanup_session(self):
        """현재 세션의 모든 임시 파일 정리"""
        for filepath in list(self._temp_files):
            self.cleanup_file(filepath)
        self._temp_files.clear()

    @staticmethod
    def cleanup_old_files(days: int = 7):
        """
        오래된 Qonvo 임시 파일 정리
        - 파일명 패턴: qonvo_img_*
        - 수정 시간 기준
        """
        temp_dir = Path(tempfile.gettempdir())
        now = time.time()
        cutoff = now - (days * 86400)  # 7일 = 604800초

        pattern = "qonvo_img_*.png"
        try:
            for filepath in temp_dir.glob(pattern):
                try:
                    if filepath.stat().st_mtime < cutoff:
                        filepath.unlink()
                except Exception as e:
                    from v.logger import get_logger
                    logger = get_logger("qonvo.temp_file_manager")
                    logger.warning(f"Failed to cleanup old file {filepath}: {e}")
        except Exception as e:
            from v.logger import get_logger
            logger = get_logger("qonvo.temp_file_manager")
            logger.warning(f"Failed to cleanup old files: {e}")
