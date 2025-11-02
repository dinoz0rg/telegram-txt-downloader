from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from .config import settings

router = APIRouter()

# Templates env
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/ui", response_class=HTMLResponse)
async def ui_index(request: Request):
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "page": "dashboard",
            "download_dir": str(Path(settings.download_dir).resolve()),
        },
    )


@router.get("/ui/downloader", response_class=HTMLResponse)
async def ui_downloader(request: Request):
    return templates.TemplateResponse(
        "downloader.html",
        {"request": request, "page": "downloader"},
    )


@router.get("/ui/files", response_class=HTMLResponse)
async def ui_files(request: Request):
    return templates.TemplateResponse(
        "files.html",
        {"request": request, "page": "files"},
    )


@router.get("/ui/search", response_class=HTMLResponse)
async def ui_search(request: Request):
    return templates.TemplateResponse(
        "search.html",
        {"request": request, "page": "search"},
    )


@router.get("/ui/logs", response_class=HTMLResponse)
async def ui_logs(request: Request):
    return templates.TemplateResponse(
        "logs.html",
        {"request": request, "page": "logs"},
    )


@router.get("/ui/settings", response_class=HTMLResponse)
async def ui_settings(request: Request):
    effective = {
        "download_dir": str(Path(settings.download_dir).resolve()),
        "db_file": str(Path(settings.db_file).resolve()),
        "logs_dir": str(Path(settings.logs_dir).resolve()),
        "log_file": str(Path(settings.log_file).resolve()),
        "max_file_size": settings.max_file_size,
        "max_file_age_days": settings.max_file_age_days,
        "group_id": settings.group_id,
        "session_name": settings.session_name,
    }
    return templates.TemplateResponse(
        "settings.html",
        {"request": request, "page": "settings", "settings": effective},
    )
