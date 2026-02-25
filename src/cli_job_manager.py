"""CLI REPL용 작업 관리 모듈. Job 데이터클래스와 ThreadPoolExecutor 기반 JobManager."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class Job:
    """개별 실행 작업의 상태/결과를 담는 데이터클래스."""
    id: int
    prompt: str
    model: str
    status: str = "QUEUED"
    created_at: float = 0.0
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    preview: str = ""
    full_text: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    result_file: Optional[Path] = None
    attachments: list = field(default_factory=list)


class JobManager:
    """ThreadPoolExecutor 기반으로 여러 프롬프트를 동시 실행하는 관리자."""

    def __init__(
        self,
        router,
        default_model: str = "gemini-2.5-flash",
        max_workers: int = 4,
        results_dir: Optional[Path] = None,
    ):
        """라우터, 기본 모델, 워커 수, 결과 디렉터리를 초기화한다."""
        self._router = router
        self._default_model = default_model
        self._max_workers = max_workers
        self._results_dir = results_dir or self._default_results_dir()
        self._results_dir.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()  # 작업 상태 보호용 락
        self._jobs: Dict[int, Job] = {}
        self._next_id = 1
        self._shutting_down = False
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._system_prompt: Optional[str] = None

    @staticmethod
    def _default_results_dir() -> Path:
        """기본 결과 저장 디렉터리를 반환한다."""
        from v.settings import get_app_data_path
        return get_app_data_path() / "cli_results"

    @property
    def default_model(self) -> str:
        """현재 기본 모델 ID를 반환한다."""
        return self._default_model

    @default_model.setter
    def default_model(self, value: str):
        """기본 모델 ID를 변경한다."""
        self._default_model = value

    @property
    def system_prompt(self) -> Optional[str]:
        """현재 시스템 프롬프트를 반환한다."""
        return self._system_prompt

    @system_prompt.setter
    def system_prompt(self, value: Optional[str]):
        """시스템 프롬프트를 변경한다."""
        self._system_prompt = value

    @property
    def max_workers(self) -> int:
        """현재 최대 워커 수를 반환한다."""
        return self._max_workers

    def submit(
        self,
        prompt: str,
        model: Optional[str] = None,
        system: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
        attachments: list = None,
    ) -> int:
        """새 작업을 등록하고 워커 스레드에서 실행되도록 제출한다."""
        with self._lock:
            # 종료 중이면 신규 작업을 거부
            if self._shutting_down:
                raise RuntimeError("JobManager is shutting down")
            # 작업 ID 발급 및 등록
            job_id = self._next_id
            self._next_id += 1
            job = Job(
                id=job_id,
                prompt=prompt,
                model=model or self._default_model,
                created_at=time.time(),
                attachments=list(attachments) if attachments else [],
            )
            self._jobs[job_id] = job

        # 스레드풀에 작업 실행을 위임
        self._executor.submit(self._execute_job, job_id, system, options)
        return job_id

    def _execute_job(
        self,
        job_id: int,
        system: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
    ):
        """워커 스레드에서 개별 작업을 실행한다."""
        from cli_runner import BatchRunner

        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            # 실행 시작 시각 및 상태 갱신
            job.status = "RUNNING"
            job.started_at = time.time()

        runner = BatchRunner(self._router, default_model=job.model)
        line_buf = []

        def on_chunk(chunk: str):
            """스트리밍 청크를 받아 미리보기/전체 텍스트를 갱신한다."""
            # 스트리밍 콜백에서도 상태를 락으로 보호
            with self._lock:
                j = self._jobs.get(job_id)
                if not j:
                    return
                j.full_text += chunk
                line_buf.append(chunk)
                merged = "".join(line_buf)
                lines = merged.split("\n")
                # 미리보기는 첫 3줄만 유지
                if len(lines) > 3:
                    j.preview = "\n".join(lines[:3])
                else:
                    j.preview = merged

        effective_system = system or self._system_prompt
        try:
            text, meta = runner.run_single(
                prompt=job.prompt,
                model=job.model,
                system=effective_system,
                options=options or {},
                on_chunk=on_chunk,
                attachments=job.attachments or None,
            )
            with self._lock:
                j = self._jobs.get(job_id)
                if j:
                    # 완료 후 전체 텍스트/메타 및 상태 갱신
                    j.full_text = text
                    lines = text.split("\n")
                    j.preview = "\n".join(lines[:3])
                    j.meta = meta
                    j.status = "DONE"
                    j.finished_at = time.time()
                    if meta.get("error"):
                        j.error = meta["error"]
                        j.status = "ERROR"
        except Exception as e:
            with self._lock:
                j = self._jobs.get(job_id)
                if j:
                    # 예외 발생 시 에러 상태로 전환
                    j.error = str(e)
                    j.status = "ERROR"
                    j.finished_at = time.time()

        # 완료/에러 결과 저장
        self._save_result(job_id)

    def _save_result(self, job_id: int):
        """완료된 작업 결과를 .md와 .meta.json으로 저장한다."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            # 락 안에서는 필요한 값만 복사
            text = job.full_text
            meta = dict(job.meta)
            model = job.model
            status = job.status
            error = job.error
            ts = time.strftime("%Y%m%d_%H%M%S")

        stem = f"{job_id:04d}_{model}_{ts}"
        result_path = self._results_dir / f"{stem}.md"
        meta_path = self._results_dir / f"{stem}.meta.json"

        try:
            # 파일 I/O는 락 밖에서 수행
            result_path.write_text(text, encoding="utf-8")
            save_meta = {**meta, "status": status, "error": error}
            meta_path.write_text(
                json.dumps(save_meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            with self._lock:
                j = self._jobs.get(job_id)
                if j:
                    j.result_file = result_path
        except OSError:
            # 파일 저장 실패는 무시 (상태는 유지)
            pass

    def get_status_snapshot(self) -> list:
        """스레드 안전한 상태 스냅샷을 반환한다 (full_text 제외)."""
        # 스레드 보호된 상태를 복사하여 외부에 전달
        with self._lock:
            return [
                Job(
                    id=j.id,
                    prompt=j.prompt,
                    model=j.model,
                    status=j.status,
                    created_at=j.created_at,
                    started_at=j.started_at,
                    finished_at=j.finished_at,
                    preview=j.preview,
                    full_text="",
                    meta=dict(j.meta),
                    error=j.error,
                    result_file=j.result_file,
                )
                for j in self._jobs.values()
            ]

    def get_job(self, job_id: int) -> Optional[Job]:
        """특정 작업의 전체 상태를 반환한다."""
        with self._lock:
            j = self._jobs.get(job_id)
            if not j:
                return None
            return Job(
                id=j.id,
                prompt=j.prompt,
                model=j.model,
                status=j.status,
                created_at=j.created_at,
                started_at=j.started_at,
                finished_at=j.finished_at,
                preview=j.preview,
                full_text=j.full_text,
                meta=dict(j.meta),
                error=j.error,
                result_file=j.result_file,
            )

    def clear_done(self):
        """완료/에러 작업을 제거하고 제거된 개수를 반환한다."""
        with self._lock:
            # DONE/ERROR 상태만 필터링하여 삭제
            to_remove = [
                jid for jid, j in self._jobs.items()
                if j.status in ("DONE", "ERROR")
            ]
            for jid in to_remove:
                del self._jobs[jid]
            return len(to_remove)

    def shutdown(self, timeout: float = 5.0):
        """신규 작업을 차단하고 스레드풀을 종료한다."""
        with self._lock:
            self._shutting_down = True

        self._executor.shutdown(wait=True, cancel_futures=True)
