import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable
from .config import settings


def iter_text_files(root_dir: Path) -> Iterable[Path]:
    for path in root_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() == ".txt":
            yield path


def search_lines_in_file(file_path: Path, keyword: str):
    try:
        with file_path.open("r", encoding="utf-8", errors="ignore") as f:
            needle = keyword.lower()
            for line_num, line in enumerate(f, start=1):
                if needle in line.lower():
                    yield line_num, line.rstrip("\n")
    except (OSError, UnicodeError):
        return


@dataclass
class SearchResult:
    scanned_files: int
    lines_found: int
    output_path: str


class SearchJob:
    def __init__(self, keyword: str, root_dir: str | Path = settings.download_dir, output_path: str | Path | None = None, max_workers: int | None = None, *, on_progress=None, is_cancelled=None):
        self.keyword = keyword
        self.root = Path(root_dir)
        self.output_path = Path(output_path) if output_path else None
        self.max_workers = max_workers
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.total_lines_found = 0
        # Optional callbacks for progress and cooperative cancellation
        self.on_progress = on_progress  # callable(dict) -> None
        self.is_cancelled = is_cancelled  # callable() -> bool

    def _resolve_output_path(self) -> Path:
        if self.output_path:
            return self.output_path
        # Use GMT+8 timestamp without microseconds
        stamp = (datetime.utcnow() + timedelta(hours=8)).strftime("%Y%m%d_%H%M%S")
        clean_kw = "".join(ch for ch in self.keyword if ch.isalnum() or ch in ("-", "_")) or "keyword"
        # Ensure results directory exists and write results there
        out_dir = Path(settings.results_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir / f"search_results_{clean_kw}_{stamp}.txt"

    def _process_file(self, file_path: Path, out_path: Path) -> int:
        local_count = 0
        local_lines: list[str] = []
        for _, content in search_lines_in_file(file_path, self.keyword):
            if self.stop_event.is_set() or (self.is_cancelled and self.is_cancelled()):
                break
            local_lines.append(content)
            local_count += 1
        if local_lines:
            with self.lock:
                with out_path.open("a", encoding="utf-8") as out:
                    for content in local_lines:
                        out.write(f"{content}\n")
                    out.flush()
        return local_count

    def run(self) -> SearchResult:
        if not self.root.exists() or not self.root.is_dir():
            raise RuntimeError(f"Directory not found: {self.root}")
        if not self.keyword:
            raise RuntimeError("keyword cannot be empty")

        out_path = self._resolve_output_path()
        out_path.touch(exist_ok=True)
        files = list(iter_text_files(self.root))
        file_count = len(files)

        # Initial progress notification
        if self.on_progress:
            try:
                self.on_progress({
                    "total_files": file_count,
                    "files_scanned": 0,
                    "matches_found": 0,
                    "percent_complete": 0,
                })
            except Exception:
                pass

        max_workers = self.max_workers
        if max_workers is None:
            max_workers = min(32, (os.cpu_count() or 4) * 4)

        executor = ThreadPoolExecutor(max_workers=max_workers)
        futures = {}
        files_scanned = 0
        try:
            futures = {executor.submit(self._process_file, f, out_path): f for f in files}
            for future in as_completed(futures):
                if self.stop_event.is_set() or (self.is_cancelled and self.is_cancelled()):
                    # Cancel remaining futures and stop early
                    self.stop_event.set()
                    for f in list(futures.keys()):
                        f.cancel()
                    break
                cnt = future.result()
                self.total_lines_found += cnt
                files_scanned += 1
                if self.on_progress:
                    try:
                        percent = int(100 * files_scanned / file_count) if file_count else 100
                        current_file = futures.get(future)
                        self.on_progress({
                            "total_files": file_count,
                            "files_scanned": files_scanned,
                            "matches_found": self.total_lines_found,
                            "percent_complete": percent,
                            "current_file": str(current_file) if current_file else None,
                        })
                    except Exception:
                        pass
        except KeyboardInterrupt:
            self.stop_event.set()
            for f in list(futures.keys()):
                f.cancel()
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        return SearchResult(
            scanned_files=files_scanned if files_scanned else file_count,
            lines_found=self.total_lines_found,
            output_path=str(out_path.resolve()),
        )


def run_search(keyword: str, root_dir: str | Path = settings.download_dir, output_path: str | Path | None = None, max_workers: int | None = None, *, on_progress=None, is_cancelled=None) -> SearchResult:
    job = SearchJob(
        keyword=keyword,
        root_dir=root_dir,
        output_path=output_path,
        max_workers=max_workers,
        on_progress=on_progress,
        is_cancelled=is_cancelled,
    )
    return job.run()
