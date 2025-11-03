from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

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


# App-wide singleton repository
repository = Repository()
