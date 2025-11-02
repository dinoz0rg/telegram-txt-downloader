from __future__ import annotations

import time
from typing import Optional, Tuple

from starlette.concurrency import run_in_threadpool

from .telegram_downloader import TelegramDownloaderService
from .searcher import run_search, SearchResult
from .config import settings
from .jobs import job_manager, JobStatus


class SearchService:
    """
    Lightweight service wrapper around the synchronous search job to provide
    reusable, stateful, and concurrency-friendly usage from multiple endpoints.
    """

    def __init__(self) -> None:
        self.state: str = "stopped"  # stopped | starting | running | stopping
        self.last_state_change: float = time.time()

    def _set_state(self, new_state: str) -> None:
        self.state = new_state
        self.last_state_change = time.time()

    def status(self) -> dict:
        return {
            "state": self.state,
            "running": self.state in ("starting", "running"),
            "last_state_change": self.last_state_change,
        }

    async def run(self, keyword: str, max_workers: Optional[int] = None) -> Tuple[SearchResult, str]:
        """Run a search and register it as a job; returns (result, job_id)."""
        # Validate and clamp max_workers to a safe range
        if max_workers is not None:
            try:
                max_workers = int(max_workers)
            except Exception:
                max_workers = None
        if max_workers is not None:
            if max_workers <= 0:
                max_workers = 1
            if max_workers > 128:
                max_workers = 128
        # Create job and set service state while executing the CPU-bound synchronous
        # search in a threadpool so the event loop remains responsive.
        job = job_manager.create("search", {"keyword": keyword})

        def on_progress(progress: dict) -> None:
            # Directly reflect progress into the job manager
            try:
                job_manager.update_progress(job.id, progress)
            except Exception:
                pass

        def is_cancelled() -> bool:
            try:
                return job_manager.is_cancelled(job.id)
            except Exception:
                return False

        try:
            self._set_state("starting")
            job_manager.mark(job.id, JobStatus.starting)
            # Immediately mark running to keep UI responsive
            self._set_state("running")
            job_manager.mark(job.id, JobStatus.running)

            result: SearchResult = await run_in_threadpool(
                run_search,
                keyword,
                settings.download_dir,
                None,  # output_path -> let the searcher resolve into results_dir
                max_workers,
                on_progress=on_progress,
                is_cancelled=is_cancelled,
            )
            # Determine final state based on cancellation flag
            if job_manager.is_cancelled(job.id):
                job_manager.mark(
                    job.id,
                    JobStatus.cancelled,
                    progress={
                        "scanned_files": result.scanned_files,
                        "lines_found": result.lines_found,
                        "output_path": result.output_path,
                    },
                )
            else:
                job_manager.mark(
                    job.id,
                    JobStatus.completed,
                    progress={
                        "scanned_files": result.scanned_files,
                        "lines_found": result.lines_found,
                        "output_path": result.output_path,
                    },
                )
            return result, job.id
        except Exception as e:
            job_manager.mark(job.id, JobStatus.failed, error=str(e))
            raise
        finally:
            # Always reset the state
            self._set_state("stopped")


# Exposed singleton services for application-wide reuse
search_service = SearchService()
downloader_service = TelegramDownloaderService()
