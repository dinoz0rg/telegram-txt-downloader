from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api import router, downloader_service
from .web import router as web_router
from .config import settings
from .logging_setup import setup_logging
from .db import init_db, close_db


def create_app() -> FastAPI:
    setup_logging()

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        try:
            init_db(Path(settings.db_file))
        except Exception:
            pass

        try:
            yield
        finally:
            try:
                await downloader_service.stop()
                await downloader_service.wait_until_stopped(timeout=10.0)
            except Exception:
                pass
            try:
                close_db()
            except Exception:
                pass

    app = FastAPI(title="Telegram TXT Downloader & Search API", version="1.0.0", lifespan=_lifespan)

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

    # Include REST API routes
    app.include_router(router)

    # Server-rendered Jinja UI (modern minimal web UI)
    app.include_router(web_router)

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
