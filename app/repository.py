from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

# Thin repository wrapper over db.py to centralize persistence access.
# This keeps API/service layers decoupled from the concrete persistence module
# and simplifies testing/mocking later.

from . import db as _db


class Repository:
    def record_search_result(self, path: Path) -> None:
        _db.record_search_result(path)

    def get_daily_downloads(self, days: int = 30) -> List[Dict[str, Any]]:
        return _db.get_daily_downloads(days)

    def get_origin_breakdown(self) -> List[Dict[str, Any]]:
        return _db.get_origin_breakdown()

    def get_stats_summary(self) -> Dict[str, Any]:
        return _db.get_stats_summary()

    # Downloader persistence passthroughs kept accessible if needed elsewhere
    def get_downloaded_file_ids(self) -> List[str]:
        return _db.get_downloaded_file_ids()

    def start_job(self, job_type: str, details: Optional[dict[str, Any]] = None) -> int:
        return _db.start_job(job_type, details=details)

    def finish_job(self, job_id: int, status: str = "finished", details: Optional[dict[str, Any]] = None) -> None:
        return _db.finish_job(job_id, status=status, details=details)


# App-wide singleton repository
repository = Repository()
