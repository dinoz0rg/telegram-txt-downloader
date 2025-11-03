from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pathlib import Path
from typing import Optional, Any, Dict

import time
import os

from .services import downloader_service, search_service
from .config import settings
from .repository import repository
from .jobs import job_manager

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
    pass


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/api/stats")
async def stats(days: int = 30):
    try:
        daily = repository.get_daily_downloads(days)
        origin = repository.get_origin_breakdown()
        summary = repository.get_stats_summary()
        return {
            "daily": daily,
            "origin": origin,
            "summary": summary,
            "days": days,
        }
    except Exception as e:
        return {
            "daily": [],
            "origin": [],
            "summary": {"count": 0, "mb": 0.0, "first_download": None, "last_download": None},
            "days": days,
            "error": str(e),
        }


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

    # Attach current JobManager job if present
    jm_id = getattr(downloader_service, "current_job_manager_id", None)
    st["job_id"] = jm_id
    if jm_id:
        job = job_manager.get(jm_id)
        if job:
            st["job"] = _serialize_job(job)

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
async def list_files(page: int = 1, per_page: int = 5, q: Optional[str] = None):
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
    # Optional server-side filter by filename (case-insensitive) BEFORE sorting/pagination
    if q:
        q_low = q.strip().lower()
        if q_low:
            items = [it for it in items if q_low in (it.get("name") or "").lower()]
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
async def list_result_files(page: int = 1, per_page: int = 5, q: Optional[str] = None):
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
    # Optional server-side filter by filename (case-insensitive) BEFORE sorting/pagination
    if q:
        q_low = q.strip().lower()
        if q_low:
            items = [it for it in items if q_low in (it.get("name") or "").lower()]
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


@router.delete("/api/files/{path:path}")
async def delete_file(path: str):
    # Delete a file under download_dir, supporting nested relative paths
    root = Path(settings.download_dir).resolve()
    target = (root / path).resolve()
    try:
        target.relative_to(root)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        target.unlink()
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Could not delete: {e}")
    return {"deleted": True, "path": path}


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
    return search_service.status()


@router.post("/api/search")
async def search(keyword: str, max_workers: Optional[int] = None):
    try:
        result, job_id = await search_service.run(keyword=keyword, max_workers=max_workers)
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
            repository.record_search_result(Path(result.output_path))
        except Exception:
            # non-fatal if DB write fails
            pass
        return {
            "job_id": job_id,
            "scanned_files": result.scanned_files,
            "lines_found": result.lines_found,
            "output_path": result.output_path,
            "output_url": output_url,
        }
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- Jobs API (additive, non-breaking) ---


def _serialize_job(job) -> Dict[str, Any]:
    return {
        "id": job.id,
        "type": job.type,
        "status": job.status.value if hasattr(job.status, "value") else str(job.status),
        "progress": job.progress,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "error": job.error,
    }


@router.get("/api/jobs")
async def list_jobs(limit: int = 50):
    limit = 1 if limit <= 0 else min(500, limit)
    jobs = job_manager.list(limit=limit)
    return {"jobs": [_serialize_job(j) for j in jobs], "limit": limit}


@router.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _serialize_job(job)


@router.delete("/api/jobs/{job_id}")
async def cancel_job(job_id: str):
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    # Request cooperative cancellation; do not mark terminal here to avoid clearing the flag prematurely.
    requested = job_manager.request_cancel(job_id)
    # Return current job status; SearchService/SearchJob will observe the flag and mark `cancelled` soon after.
    job = job_manager.get(job_id) or job
    return {"requested": requested, "status": job.status}


@router.post("/api/jobs/search")
async def start_search_job(keyword: str, max_workers: Optional[int] = None):
    # Alias of /api/search for now (synchronous execution), returns same fields plus job_id.
    try:
        result, job_id = await search_service.run(keyword=keyword, max_workers=max_workers)
        output_path = Path(result.output_path)
        output_url = None
        try:
            if output_path.is_relative_to(settings.results_dir):
                output_url = f"/results/{output_path.name}"
            else:
                candidate = Path(settings.results_dir) / output_path.name
                if candidate.exists():
                    output_url = f"/results/{candidate.name}"
        except AttributeError:
            try:
                output_path.relative_to(settings.results_dir)
                output_url = f"/results/{output_path.name}"
            except Exception:
                candidate = Path(settings.results_dir) / output_path.name
                if candidate.exists():
                    output_url = f"/results/{candidate.name}"
        try:
            repository.record_search_result(Path(result.output_path))
        except Exception:
            pass
        return {
            "job_id": job_id,
            "scanned_files": result.scanned_files,
            "lines_found": result.lines_found,
            "output_path": result.output_path,
            "output_url": output_url,
        }
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
