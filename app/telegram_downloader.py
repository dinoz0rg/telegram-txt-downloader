import asyncio
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Set

from pyrogram import Client
from pyrogram.errors import FloodWait, RPCError
from pyrogram.types import Message

from .config import settings
from .logging_setup import setup_logging
from .db import get_downloaded_file_ids, mark_file_downloaded, start_job, finish_job


@dataclass
class DownloadStats:
    downloaded: int = 0
    failed: int = 0
    skipped: int = 0
    total_candidates: int = 0  # all .txt messages found
    total_to_download: int = 0  # new files to actually download (excludes already downloaded)
    processed: int = 0  # downloaded + failed + skipped (only those considered during this run)
    percent: int = 0    # processed / max(1, total_to_download) * 100
    already_present: int = 0  # pre-scan: files that were already on disk
    current_index: int = 0    # 0-based index within new-files queue
    in_progress: bool = False
    current_file: Optional[str] = None
    last_update: float = 0.0


class TelegramDownloaderService:
    def __init__(self):
        self.logger = setup_logging()
        self.client: Optional[Client] = None
        self.running = False
        self._task: Optional[asyncio.Task] = None
        self.downloaded_files: Set[str] = set()
        self.stats = DownloadStats()
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self.current_job_id: Optional[int] = None
        # Explicit state machine for reliable UI status
        self.state: str = "stopped"  # stopped | starting | running | stopping
        self.last_state_change: float = time.time()
        Path(settings.download_dir).mkdir(parents=True, exist_ok=True)
        self.logger.info(f"Download directory: {Path(settings.download_dir).resolve()}")
        self._load_downloaded_files()

    def _update_progress(self):
        self.stats.processed = self.stats.downloaded + self.stats.failed + self.stats.skipped
        total = self.stats.total_to_download
        if total and total > 0:
            processed_capped = min(self.stats.processed, total)
            self.stats.percent = int(100 * processed_capped / total)
        else:
            # If nothing to download, consider progress done
            self.stats.percent = 100
        self.stats.last_update = time.time()

    def _load_downloaded_files(self):
        # Load previously downloaded file IDs from the database
        try:
            self.downloaded_files = set(get_downloaded_file_ids())
            self.logger.info(f"Loaded {len(self.downloaded_files)} already downloaded files from DB")
        except Exception as e:
            self.logger.error(f"Failed to load downloaded files from DB: {e}")
            self.downloaded_files = set()

    def _save_downloaded_file(self, file_id: str, path: Optional[Path] = None, size: Optional[int] = None):
        # Persist downloaded file to the database and local cache set
        try:
            mark_file_downloaded(file_id, path=path, size=size)
            self.downloaded_files.add(file_id)
        except Exception as e:
            self.logger.error(f"Failed to mark file as downloaded in DB: {e}")

    def _format_size(self, size_bytes: int) -> str:
        if size_bytes == 0:
            return "0 B"
        size_names = ["B", "KB", "MB", "GB", "TB"]
        i = 0
        val = float(size_bytes)
        while val >= 1024 and i < len(size_names) - 1:
            val /= 1024.0
            i += 1
        return f"{val:.1f} {size_names[i]}"

    def _sanitize_filename(self, filename: str) -> str:
        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
        filename = re.sub(r'[^\x00-\x7F]+', '_', filename)
        filename = re.sub(r'_+', '_', filename).strip('_')
        return filename or "unnamed_file"

    def _get_filename(self, message: Message) -> str:
        if message.document and message.document.file_name:
            return self._sanitize_filename(message.document.file_name)
        # Use GMT+8 for deterministic filename timestamp (no microseconds)
        ts = (datetime.utcfromtimestamp(message.date.timestamp()) + timedelta(hours=8)).strftime('%Y%m%d_%H%M%S')
        return f"file_{ts}_{message.id}.txt"

    def _is_txt(self, message: Message) -> bool:
        if not getattr(message, 'document', None):
            return False
        doc = message.document
        if doc.mime_type and 'text/plain' in doc.mime_type:
            return True
        if doc.file_name and doc.file_name.lower().endswith('.txt'):
            return True
        return False

    def _is_complete(self, message: Message, path: Path) -> bool:
        try:
            expected = message.document.file_size
            actual = path.stat().st_size
            if actual == expected:
                return True
            self.logger.warning(
                f"Incomplete file {path.name} ({self._format_size(actual)}/{self._format_size(expected)})"
            )
        except Exception as e:
            self.logger.error(f"Error checking completeness for {path.name}: {e}")
        return False


    async def _sleep_or_stop(self, seconds: float):
        if seconds <= 0:
            return
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            return

    async def _download_with_retries(self, message: Message, file_path: Path, filename: str) -> Optional[Path]:
        max_retries = 5
        for attempt in range(max_retries):
            if self._stop_event.is_set() or not self.running:
                return None
            try:
                downloaded = await self.client.download_media(message, file_name=str(file_path))
                if downloaded and Path(downloaded).exists():
                    p = Path(downloaded)
                    if p.stat().st_size == 0:
                        p.unlink(missing_ok=True)
                        self.logger.warning(f"Empty file (attempt {attempt+1}/{max_retries}): {filename}")
                    elif self._is_complete(message, p):
                        return p
                    else:
                        p.unlink(missing_ok=True)
                        self.logger.warning(f"Incomplete file (attempt {attempt+1}/{max_retries}): {filename}")
                else:
                    self.logger.warning(f"No file created (attempt {attempt+1}/{max_retries}): {filename}")
            except FloodWait as e:
                wait_time = int(getattr(e, 'value', 30))
                self.logger.warning(f"FloodWait {wait_time}s on attempt {attempt+1}")
                await self._sleep_or_stop(wait_time)
                continue
            except RPCError as e:
                self.logger.error(f"RPC error on attempt {attempt+1}: {e}")
            except Exception as e:
                self.logger.error(f"Unexpected error on attempt {attempt+1}: {e}")

            if attempt < max_retries - 1:
                wait_time = 30 + attempt * 30
                self.logger.info(f"Retrying in {wait_time}s...")
                await self._sleep_or_stop(wait_time)
        return None

    async def _get_all_messages(self) -> List[Message]:
        msgs: List[Message] = []
        async for m in self.client.get_chat_history(settings.group_id):
            if self._is_txt(m):
                msgs.append(m)
        return msgs

    async def _worker(self):
        async with Client(
            settings.session_name,
            api_id=settings.api_id,
            api_hash=settings.api_hash,
            phone_number=settings.phone_number,
        ) as client:
            self.client = client
            # Enter running state once the client session is active
            self.state = "running"
            self.last_state_change = time.time()
            self.logger.info("Connected to Telegram")
            # Start a job record for this run
            try:
                self.current_job_id = start_job("downloader", details={"group_id": str(settings.group_id)})
            except Exception:
                self.current_job_id = None
            try:
                chat = await client.get_chat(settings.group_id)
                self.logger.info(f"Connected to: {getattr(chat, 'title', str(settings.group_id))}")
            except Exception as e:
                self.logger.error(f"Could not access group/channel: {e}")
                try:
                    if self.current_job_id is not None:
                        finish_job(self.current_job_id, status="failed", details={"error": str(e)})
                        self.current_job_id = None
                except Exception:
                    pass
                return

            messages = await self._get_all_messages()
            self.stats.total_candidates = len(messages)
            if not messages:
                self.logger.info("No .txt files found")
                return

            # Prepare queue filtering already downloaded
            queue: list[tuple[Message, str]] = []
            for m in messages:
                fid = f"{m.id}_{m.date.timestamp()}"
                if fid in self.downloaded_files:
                    # Count as already present (do not include in this-run skipped)
                    self.stats.already_present += 1
                else:
                    queue.append((m, fid))

            self.logger.info(
                f"Found {len(messages)} .txt files total. Skipping {self.stats.skipped}. Downloading {len(queue)} new files"
            )

            # Initialize progress totals
            self.stats.total_candidates = len(messages)
            self.stats.total_to_download = len(queue)
            # Reset run-time counters
            self.stats.downloaded = 0
            self.stats.failed = 0
            self.stats.skipped = 0
            self.stats.processed = 0
            self.stats.percent = 0 if self.stats.total_to_download > 0 else 100
            self.stats.current_index = 0

            i = 0
            self.stats.in_progress = True
            last_refresh = time.time()
            refresh_interval = 30 * 60

            while self.running and i < len(queue):
                message, fid = queue[i]
                filename = self._get_filename(message)
                dest = Path(settings.download_dir) / filename
                self.stats.current_file = filename
                self.stats.last_update = time.time()

                # skip too old or too large
                if settings.max_file_age_days > 0:
                    age_days = (datetime.now() - message.date.replace(tzinfo=None)).days
                    if age_days > settings.max_file_age_days:
                        self.logger.warning(f"Skipping old file ({age_days}d): {filename}")
                        self.stats.skipped += 1
                        self._update_progress()
                        i += 1
                        self.stats.current_index = i
                        continue
                if message.document.file_size > settings.max_file_size:
                    self.logger.warning(
                        f"Too large {self._format_size(message.document.file_size)}: {filename}"
                    )
                    self.stats.skipped += 1
                    self._update_progress()
                    i += 1
                    self.stats.current_index = i
                    continue

                if dest.exists() and self._is_complete(message, dest):
                    self.logger.info(f"Already complete: {filename}")
                    self._save_downloaded_file(fid, path=dest, size=message.document.file_size)
                    self.stats.downloaded += 1
                    self._update_progress()
                    i += 1
                    self.stats.current_index = i
                    continue
                elif dest.exists():
                    dest.unlink(missing_ok=True)

                self.logger.info(
                    f"Downloading: {filename} ({self._format_size(message.document.file_size)})"
                )
                start = time.time()
                path = await self._download_with_retries(message, dest, filename)
                if path is not None:
                    dt = max(0.001, time.time() - start)
                    speed = (message.document.file_size / (1024*1024)) / dt
                    self.logger.info(f"Downloaded {filename} in {dt:.1f}s ({speed:.1f} MB/s)")
                    self.logger.info(f"Saved to: {Path(path).resolve()}")
                    self._save_downloaded_file(fid, path=dest, size=message.document.file_size)
                    self.stats.downloaded += 1
                    self._update_progress()
                else:
                    self.logger.error(f"Failed to download: {filename}")
                    self.stats.failed += 1
                    self._update_progress()

                i += 1
                self.stats.current_index = i

                # Refresh references periodically
                if settings.auto_refresh_on_failure and (time.time() - last_refresh > refresh_interval):
                    try:
                        fresh = await self._get_all_messages()
                        fmap = {m.id: m for m in fresh}
                        refreshed = []
                        for m, fid in queue[i:]:
                            if m.id in fmap:
                                refreshed.append((fmap[m.id], fid))
                            else:
                                self.logger.warning(f"Message {m.id} no longer exists, skipping")
                        queue = queue[:i] + refreshed
                        last_refresh = time.time()
                        self.logger.info(f"Refreshed message references: {len(refreshed)}")
                    except Exception as e:
                        self.logger.error(f"Error refreshing messages: {e}")

            self.stats.in_progress = False
            self.stats.current_file = None
            self._update_progress()
            # Mark service no longer running once worker finishes naturally
            self.running = False
            self.state = "stopped"
            self.last_state_change = time.time()
            # Finish job if started
            try:
                if self.current_job_id is not None:
                    finish_job(
                        self.current_job_id,
                        status="finished",
                        details={
                            "downloaded": self.stats.downloaded,
                            "failed": self.stats.failed,
                            "skipped": self.stats.skipped,
                            "total_candidates": self.stats.total_candidates,
                            "total_to_download": self.stats.total_to_download,
                        },
                    )
                    self.current_job_id = None
            except Exception:
                pass

    async def start(self) -> dict:
        async with self._lock:
            if self.running:
                return {"status": "already_running", "stats": asdict(self.stats)}
            self.running = True
            self._stop_event.clear()
            # Immediately reflect Running for deterministic UI, worker will reaffirm
            self.state = "running"
            self.last_state_change = time.time()
            self.stats = DownloadStats(in_progress=True, last_update=time.time())
            self._task = asyncio.create_task(self._worker())
            return {"status": "started"}

    async def stop(self) -> dict:
        async with self._lock:
            if not self.running:
                # Ensure state reflects stopped for deterministic UI
                self.state = "stopped"
                self.last_state_change = time.time()
                return {"status": "not_running"}
            # Deterministically flip to stopped for UI immediately
            self.running = False
            self.state = "stopped"
            self.last_state_change = time.time()
            self._stop_event.set()
            # Try to allow worker to finish; will be awaited via wait_until_stopped
            return {"status": "stopping"}

    async def wait_until_stopped(self, timeout: float = 15.0) -> bool:
        task = self._task
        if not task:
            return True
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            self.logger.warning("Downloader did not stop in time; cancelling task...")
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return False
        except asyncio.CancelledError:
            # Task was already cancelled elsewhere; treat as not gracefully stopped
            return False

    def status(self) -> dict:
        return asdict(self.stats)
