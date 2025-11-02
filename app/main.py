from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api import router, downloader_service
from .web import router as web_router
from .config import settings
from .logging_setup import setup_logging
from .db import init_db, import_legacy_downloaded_list, close_db


def create_app() -> FastAPI:
    setup_logging()

    app = FastAPI(title="Telegram TXT Downloader & Search API", version="1.0.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount directory for downloaded files (absolute path)
    resolved_dir = Path(settings.download_dir).resolve()
    resolved_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/downloaded", StaticFiles(directory=str(resolved_dir)), name="downloaded")

    # Mount static for API server assets (may be unused when Vision UI is active)
    static_dir = Path(__file__).resolve().parent / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Mount results directory for search outputs
    results_dir = Path(settings.results_dir).resolve()
    results_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/results", StaticFiles(directory=str(results_dir)), name="results")

    # Initialize database and import legacy downloaded list if present
    try:
        init_db(Path(settings.db_file))
        project_root = Path(__file__).resolve().parent.parent
        # Legacy locations
        legacy1 = project_root / "downloaded_files.txt"
        legacy2 = project_root / "output" / "downloaded_files.txt"
        imported = 0
        if legacy1.exists():
            imported += import_legacy_downloaded_list(legacy1)
        if legacy2.exists():
            imported += import_legacy_downloaded_list(legacy2)
        if imported:
            # Optionally keep the files as backups; no further use in app
            pass
    except Exception:
        # Non-fatal; app can still run without DB init but features may be limited
        pass

    # Include REST API routes
    app.include_router(router)

    # Server-rendered Jinja UI (modern minimal web UI)
    app.include_router(web_router)

    @app.on_event("shutdown")
    async def _shutdown():
        # Ensure background downloader stops on app shutdown
        try:
            await downloader_service.stop()
            await downloader_service.wait_until_stopped(timeout=10.0)
        except Exception:
            # Best-effort shutdown; uvicorn will proceed
            pass
        try:
            close_db()
        except Exception:
            pass

    @app.get("/")
    async def index():
        return {
            "message": "Telegram TXT Downloader API",
            "docs": "/docs",
            "ui": "/ui",
            "health": "/health",
            "download_dir": str(resolved_dir),
            "downloader": {
                "start": {"method": "POST", "url": "/api/downloader/start"},
                "stop": {"method": "POST", "url": "/api/downloader/stop"},
                "status": {"method": "GET", "url": "/api/downloader/status"}
            },
            "files": {
                "list": {"method": "GET", "url": "/api/files"},
                "download": {"method": "GET", "url": "/api/files/download/{path}"},
                "inline_mount": "/downloaded/{path}"
            },
            "results": {
                "list": {"method": "GET", "url": "/api/results/files"},
                "download": {"method": "GET", "url": "/api/results/download/{name}"},
                "inline_mount": "/results/{name}"
            },
            "search": {"method": "POST", "url": "/api/search?keyword=example"},
            "logs": {"method": "GET", "url": "/api/logs"},
        }

    return app


app = create_app()
