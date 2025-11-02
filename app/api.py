from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pathlib import Path
from typing import Optional
import time
import os
from starlette.concurrency import run_in_threadpool

from .telegram_downloader import TelegramDownloaderService
from .searcher import run_search, SearchResult
from .config import settings
from .db import record_search_result

router = APIRouter()

# Detect multi-process instances (Uvicorn workers>1) to avoid split-brain state
INSTANCE_PID = os.getpid()
LOCK_PATH = Path(settings.results_dir).resolve().parent / "runtime.lock"
MULTI_INSTANCE_DETECTED = False
try:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    if LOCK_PATH.exists():
        try:
            existing = int(LOCK_PATH.read_text(encoding="utf-8").strip() or "0")
        except Exception:
            existing = 0
        if existing and existing != INSTANCE_PID:
            MULTI_INSTANCE_DETECTED = True
        else:
            LOCK_PATH.write_text(str(INSTANCE_PID), encoding="utf-8")
    else:
        LOCK_PATH.write_text(str(INSTANCE_PID), encoding="utf-8")
except Exception:
    # Non-fatal; if we can't write the lock, we just won't detect multi-instance
    pass

downloader_service = TelegramDownloaderService()

# Simple explicit state for Searcher
SEARCH_STATE = "stopped"  # stopped | starting | running | stopping
SEARCH_LAST_CHANGE = time.time()


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.post("/api/downloader/start")
async def start_downloader():
    result = await downloader_service.start()
    return result


@router.post("/api/downloader/stop")
async def stop_downloader(force: bool = False, timeout: float = 10.0):
    await downloader_service.stop()
    try:
        stopped_gracefully = await downloader_service.wait_until_stopped(timeout=0.0 if force else timeout)
        return {"status": "stopped" if stopped_gracefully else "cancelled"}
    except Exception:
        # Never propagate cancellation errors to the client
        return {"status": "cancelled"}


@router.get("/api/downloader/status")
async def downloader_status():
    # Trust explicit service state, but also fall back to safe runtime signals
    st = downloader_service.status()
    state = getattr(downloader_service, "state", "stopped")
    last_change = getattr(downloader_service, "last_state_change", 0.0)

    # Robust running detection
    service_running_flag = bool(getattr(downloader_service, "running", False))
    task = getattr(downloader_service, "_task", None)
    task_exists = task is not None
    task_done = (task.done() if task_exists else False)
    # Consider recent activity on stats as a signal too (e.g., logs/progress updates)
    stats_last_update = float(st.get("last_update", 0.0) or 0.0)
    activity_recent = (time.time() - stats_last_update) < 15.0 if stats_last_update else False

    running_flag = (
        state in ("starting", "running")
        or service_running_flag
        or (task_exists and not task_done)
        or activity_recent
    )

    st["state"] = state
    st["last_state_change"] = last_change
    st["running"] = bool(running_flag)

    # Diagnostics to help verify why running is true/false and detect multi-instance
    st["_diag"] = {
        "service_running_flag": service_running_flag,
        "task_exists": task_exists,
        "task_done": task_done,
        "stats_last_update": stats_last_update,
        "activity_recent": activity_recent,
        "pid": INSTANCE_PID,
        "multi_instance": MULTI_INSTANCE_DETECTED,
    }
    st["multi_instance"] = MULTI_INSTANCE_DETECTED

    # Compute overall progress based on downloaded list or directory scan
    try:
        root = Path(settings.download_dir).resolve()
        dir_count = sum(1 for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ".txt")
        list_count = len(getattr(downloader_service, "downloaded_files", []))
        overall_downloaded = max(dir_count, list_count)
    except Exception:
        overall_downloaded = len(getattr(downloader_service, "downloaded_files", [])) or 0

    overall_total = st.get("total_candidates") or 0
    overall_percent = 100 if (overall_total == 0 and overall_downloaded > 0) else (
        int(100 * overall_downloaded / overall_total) if overall_total else 0
    )

    st["overall_downloaded"] = overall_downloaded
    st["overall_total"] = overall_total
    st["overall_percent"] = overall_percent
    return st


@router.get("/api/files")
async def list_files(page: int = 1, per_page: int = 10):
    root = Path(settings.download_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    items = []
    # Recursive and case-insensitive .txt matching
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() == ".txt":
            rel = p.resolve().relative_to(root)
            rel_url = str(rel).replace("\\", "/")
            st = p.stat()
            items.append({
                "name": rel.name,
                "size": st.st_size,
                "modified": st.st_mtime,
                "path": f"/downloaded/{rel_url}",  # inline view via StaticFiles (supports nested)
                "download_url": f"/api/files/download/{rel_url}",  # force download (API)
            })
    # Sort by modified desc for consistency
    items.sort(key=lambda x: x["modified"], reverse=True)
    total = len(items)
    # normalize params
    per_page = 1 if per_page <= 0 else per_page
    page = 1 if page <= 0 else page
    total_pages = (total + per_page - 1) // per_page if total > 0 else 1
    if page > total_pages:
        page = total_pages
    start = (page - 1) * per_page
    end = start + per_page
    page_items = items[start:end]
    return {"count": total, "page": page, "per_page": per_page, "total_pages": total_pages, "files": page_items}


@router.get("/api/results/files")
async def list_result_files(page: int = 1, per_page: int = 10):
    root = Path(settings.results_dir)
    root.mkdir(parents=True, exist_ok=True)
    items = []
    for p in root.glob("*.txt"):
        st = p.stat()
        items.append({
            "name": p.name,
            "size": st.st_size,
            "modified": st.st_mtime,
            "path": f"/results/{p.name}",  # inline view via StaticFiles
            "download_url": f"/api/results/download/{p.name}",  # force download (API)
        })
    items.sort(key=lambda x: x["modified"], reverse=True)
    total = len(items)
    per_page = 1 if per_page <= 0 else per_page
    page = 1 if page <= 0 else page
    total_pages = (total + per_page - 1) // per_page if total > 0 else 1
    if page > total_pages:
        page = total_pages
    start = (page - 1) * per_page
    end = start + per_page
    page_items = items[start:end]
    return {"count": total, "page": page, "per_page": per_page, "total_pages": total_pages, "files": page_items}


@router.delete("/api/results/{name}")
async def delete_result_file(name: str):
    # Securely delete a result file from results_dir by name only
    safe_name = Path(name).name
    if not safe_name or safe_name != name:
        raise HTTPException(status_code=400, detail="Invalid filename")
    file_path = Path(settings.results_dir) / safe_name
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        file_path.unlink()
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Could not delete: {e}")
    return {"deleted": True, "name": safe_name}


@router.get("/api/files/download/{path:path}")
async def download_file(path: str):
    # Securely resolve within download_dir (allow nested paths)
    root = Path(settings.download_dir).resolve()
    requested = (root / path).resolve()
    try:
        requested.relative_to(root)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not requested.exists() or not requested.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(requested), media_type="text/plain; charset=utf-8", filename=requested.name)


@router.get("/api/results/download/{name}")
async def download_result_file(name: str):
    # Prevent path traversal; only allow filenames
    safe_name = Path(name).name
    file_path = Path(settings.results_dir) / safe_name
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(file_path), media_type="text/plain; charset=utf-8", filename=safe_name)


@router.get("/api/logs", response_class=PlainTextResponse)
async def get_logs(tail: int = 2000):
    log_path = Path(settings.log_file)
    if not log_path.exists():
        return "No logs yet."
    text = log_path.read_text(encoding="utf-8", errors="ignore")
    if tail > 0 and len(text) > tail:
        return text[-tail:]
    return text


@router.get("/api/search/status")
async def search_status():
    global SEARCH_STATE, SEARCH_LAST_CHANGE
    return {
        "state": SEARCH_STATE,
        "running": SEARCH_STATE in ("starting", "running"),
        "last_state_change": SEARCH_LAST_CHANGE,
    }


@router.post("/api/search")
async def search(keyword: str, max_workers: Optional[int] = None):
    global SEARCH_STATE, SEARCH_LAST_CHANGE
    try:
        SEARCH_STATE = "starting"
        SEARCH_LAST_CHANGE = time.time()
        # Immediately mark running for UI responsiveness
        SEARCH_STATE = "running"
        SEARCH_LAST_CHANGE = time.time()
        # Run the synchronous searcher off the event loop so other endpoints (like downloader/status) remain responsive
        result: SearchResult = await run_in_threadpool(
            run_search,
            keyword,
            settings.download_dir,
            None,
            max_workers,
        )
        # If the result is inside the results_dir, provide a public URL under /results
        output_path = Path(result.output_path)
        output_url = None
        try:
            if output_path.is_relative_to(settings.results_dir):
                output_url = f"/results/{output_path.name}"
            else:
                # Fallback: if same name exists in results_dir, link there
                candidate = Path(settings.results_dir) / output_path.name
                if candidate.exists():
                    output_url = f"/results/{candidate.name}"
        except AttributeError:
            # Python <3.9 compatibility for is_relative_to
            try:
                output_path.relative_to(settings.results_dir)
                output_url = f"/results/{output_path.name}"
            except Exception:
                candidate = Path(settings.results_dir) / output_path.name
                if candidate.exists():
                    output_url = f"/results/{candidate.name}"
        # Record the search result file in DB (origin='search')
        try:
            record_search_result(Path(result.output_path))
        except Exception:
            # non-fatal if DB write fails
            pass
        return {
            "scanned_files": result.scanned_files,
            "lines_found": result.lines_found,
            "output_path": result.output_path,
            "output_url": output_url,
        }
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        SEARCH_STATE = "stopped"
        SEARCH_LAST_CHANGE = time.time()
