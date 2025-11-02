from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class JobStatus(str, Enum):
    pending = "pending"
    starting = "starting"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


@dataclass
class Job:
    id: str
    type: str
    status: JobStatus = JobStatus.pending
    progress: Dict[str, Any] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    error: Optional[str] = None


class JobManager:
    """Thread-safe in-memory job registry and lifecycle manager.

    Minimal functionality required by current slice:
    - create jobs
    - transition through statuses (starting, running, completed, failed, cancelled)
    - update progress
    - query job by id / list jobs
    - cooperative cancellation flags
    """

    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.RLock()
        self._cancel_flags: set[str] = set()

    # Lifecycle helpers
    def create(self, job_type: str, initial_progress: Optional[Dict[str, Any]] = None) -> Job:
        with self._lock:
            job_id = uuid.uuid4().hex
            job = Job(id=job_id, type=job_type, progress=initial_progress or {})
            self._jobs[job_id] = job
            return job

    def mark(self, job_id: str, status: JobStatus, *, progress: Optional[Dict[str, Any]] = None, error: Optional[str] = None) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.status = status
            if progress:
                job.progress.update(progress)
            if status in (JobStatus.completed, JobStatus.failed, JobStatus.cancelled):
                job.finished_at = time.time()
                # Clear cancellation flag on terminal state
                if job_id in self._cancel_flags:
                    self._cancel_flags.discard(job_id)
            if error:
                job.error = error

    def update_progress(self, job_id: str, progress: Dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.progress.update(progress)

    # Cancellation
    def request_cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            if job.status in (JobStatus.completed, JobStatus.failed, JobStatus.cancelled):
                return False
            self._cancel_flags.add(job_id)
            return True

    def is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._cancel_flags

    # Queries
    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self, limit: int = 50) -> List[Job]:
        with self._lock:
            # Most recent first by started_at
            jobs = sorted(self._jobs.values(), key=lambda j: j.started_at, reverse=True)
            return jobs[: max(1, limit)]


# Singleton manager for app-wide usage
job_manager = JobManager()
