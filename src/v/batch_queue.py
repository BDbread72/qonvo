"""
Persistent Batch Queue Manager

Gemini Batch API job을 디스크에 저장하여 앱 재시작 후 폴링 재개 가능.
%APPDATA%/Qonvo/batch_queue.json에 저장.
"""
import json
import os
import threading
from datetime import datetime, timezone

from v.settings import get_app_data_path
from v.logger import get_logger

logger = get_logger("qonvo.batch_queue")


class BatchQueueManager:
    """Batch job 큐 영속 레이어 (thread-safe)"""

    # W2: 클래스 레벨 lock (인스턴스 간 동시 접근 방지)
    _lock = threading.Lock()

    def __init__(self):
        self._path = get_app_data_path() / "batch_queue.json"

    def add_job(
        self,
        job_name: str,
        board_name: str,
        node_id: int,
        model: str,
        is_nanobanana: bool,
        key_index: int,
        pref_count: int,
    ) -> None:
        """Batch job 항목 저장"""
        with self._lock:
            data = self._load_unlocked()
            data["jobs"].append({
                "job_name": job_name,
                "board_name": board_name,
                "node_id": node_id,
                "model": model,
                "is_nanobanana": is_nanobanana,
                "key_index": key_index,
                "pref_count": pref_count,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            self._save_unlocked(data)
            logger.info(f"[BATCH_QUEUE] Added: {job_name} (board={board_name}, node={node_id})")

    def remove_job(self, job_name: str) -> None:
        """완료/실패한 job 제거"""
        with self._lock:
            data = self._load_unlocked()
            before = len(data["jobs"])
            data["jobs"] = [j for j in data["jobs"] if j["job_name"] != job_name]
            if len(data["jobs"]) < before:
                self._save_unlocked(data)
                logger.info(f"[BATCH_QUEUE] Removed: {job_name}")

    def get_jobs_for_board(self, board_name: str) -> list:
        """특정 보드의 pending job 목록"""
        with self._lock:
            data = self._load_unlocked()
            return [j for j in data["jobs"] if j["board_name"] == board_name]

    def cleanup_stale(self, max_age_hours: int = 48) -> None:
        """만료된 job 정리 (Batch API 48시간 제한)"""
        with self._lock:
            data = self._load_unlocked()
            now = datetime.now(timezone.utc)
            before = len(data["jobs"])
            remaining = []
            for job in data["jobs"]:
                try:
                    created = datetime.fromisoformat(job["created_at"])
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                    age_hours = (now - created).total_seconds() / 3600
                    if age_hours <= max_age_hours:
                        remaining.append(job)
                    else:
                        logger.info(f"[BATCH_QUEUE] Stale removed: {job['job_name']} ({age_hours:.1f}h)")
                except (ValueError, KeyError):
                    pass  # 파싱 불가 항목 제거
            data["jobs"] = remaining
            if len(remaining) < before:
                try:
                    self._save_unlocked(data)
                except Exception as e:
                    logger.error(f"[BATCH_QUEUE] Failed to save after stale cleanup: {e}")

    def _load_unlocked(self) -> dict:
        """JSON 로드 (lock 없이 — caller가 lock 보유 전제)"""
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "jobs" in data:
                    return data
        except FileNotFoundError:
            pass
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[BATCH_QUEUE] Failed to load: {e}")
        return {"version": 1, "jobs": []}

    def _save_unlocked(self, data: dict) -> None:
        """Atomic write (tmp -> rename)"""
        tmp_path = str(self._path) + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._path)
        except OSError as e:
            logger.warning(f"[BATCH_QUEUE] Failed to save: {e}")
            try:
                os.remove(tmp_path)
            except OSError:
                pass
