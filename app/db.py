from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Iterable, Set, Optional, Any
import json
import time

_DB_CONN: sqlite3.Connection | None = None
_DB_PATH: Path | None = None


def _dict_factory(cursor, row):
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def get_conn(db_path: Path | None = None) -> sqlite3.Connection:
    global _DB_CONN, _DB_PATH
    if _DB_CONN is not None:
        return _DB_CONN
    if db_path is None:
        # Lazy fallback to configured DB file to avoid import-order issues
        try:
            from .config import settings  # local import to avoid circulars at module import time
            db_path = Path(settings.db_file)
        except Exception as e:
            raise RuntimeError("DB not initialized and no settings available. Call init_db(db_path) first.") from e
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = _dict_factory
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    _DB_CONN = conn
    _DB_PATH = db_path
    _apply_migrations(conn)
    return conn


def init_db(db_path: Path) -> None:
    """Initialize global connection and create tables if missing."""
    get_conn(db_path)


def close_db() -> None:
    global _DB_CONN
    if _DB_CONN is not None:
        try:
            _DB_CONN.close()
        finally:
            _DB_CONN = None


def _now_str_gmt8() -> str:
    from datetime import datetime, timedelta
    # UTC+8 without microseconds
    return (datetime.utcnow() + timedelta(hours=8)).replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def _apply_migrations(conn: sqlite3.Connection) -> None:
    # Meta table for schema versioning
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS _meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    # Determine current schema version (default 1 for legacy schema)
    cur = conn.execute("SELECT value FROM _meta WHERE key='schema_version'")
    row = cur.fetchone()
    try:
        schema_version = int((row or {}).get("value", 1))
    except Exception:
        schema_version = 1

    def _create_new_files():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id TEXT UNIQUE NOT NULL,
                path TEXT,
                size_mb REAL,
                origin TEXT NOT NULL DEFAULT 'telegram',
                status TEXT NOT NULL DEFAULT 'downloaded',
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','+8 hours')),
                downloaded_at TEXT
            );
            """
        )

    def _create_new_jobs():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                details TEXT
            );
            """
        )

    # If tables do not exist, create them with new schema
    # Check files table structure
    files_info = list(conn.execute("PRAGMA table_info(files)"))
    if not files_info:
        _create_new_files()
    else:
        cols = {c[1] if isinstance(c, tuple) else c.get('name') for c in files_info}
        # If legacy (no size_mb/origin or REAL timestamps), migrate
        needs_migration = ("size_mb" not in cols) or ("origin" not in cols)
        if not needs_migration:
            # verify created_at type TEXT
            try:
                created_at_type = None
                for c in files_info:
                    name = c[1] if isinstance(c, tuple) else c.get('name')
                    if name == 'created_at':
                        created_at_type = (c[2] if isinstance(c, tuple) else c.get('type'))
                        break
                if (created_at_type or '').upper() != 'TEXT':
                    needs_migration = True
            except Exception:
                pass
        if needs_migration:
            conn.execute("ALTER TABLE files RENAME TO files_old;")
            _create_new_files()
            # Copy & transform data
            conn.execute(
                """
                INSERT OR IGNORE INTO files (file_id, path, size_mb, origin, status, created_at, downloaded_at)
                SELECT
                    file_id,
                    path,
                    CASE WHEN size IS NOT NULL THEN (1.0*size)/1048576.0 ELSE NULL END,
                    'telegram',
                    COALESCE(status, 'downloaded'),
                    CASE
                        WHEN typeof(created_at)='text' THEN created_at
                        WHEN created_at IS NOT NULL THEN strftime('%Y-%m-%d %H:%M:%S', created_at, 'unixepoch', '+8 hours')
                        ELSE strftime('%Y-%m-%d %H:%M:%S','now','+8 hours')
                    END,
                    CASE
                        WHEN typeof(downloaded_at)='text' THEN downloaded_at
                        WHEN downloaded_at IS NOT NULL THEN strftime('%Y-%m-%d %H:%M:%S', downloaded_at, 'unixepoch', '+8 hours')
                        ELSE NULL
                    END
                FROM files_old;
                """
            )
            conn.execute("DROP TABLE IF EXISTS files_old;")

    # Jobs table
    jobs_info = list(conn.execute("PRAGMA table_info(jobs)"))
    if not jobs_info:
        _create_new_jobs()
    else:
        # If legacy REAL timestamps, migrate
        needs_migration = False
        try:
            for c in jobs_info:
                name = c[1] if isinstance(c, tuple) else c.get('name')
                ctype = (c[2] if isinstance(c, tuple) else c.get('type')) or ''
                if name in ('started_at', 'finished_at') and ctype.upper() != 'TEXT':
                    needs_migration = True
                    break
        except Exception:
            needs_migration = True
        if needs_migration:
            conn.execute("ALTER TABLE jobs RENAME TO jobs_old;")
            _create_new_jobs()
            conn.execute(
                """
                INSERT OR IGNORE INTO jobs (id, job_type, status, started_at, finished_at, details)
                SELECT
                    id,
                    job_type,
                    status,
                    CASE
                        WHEN typeof(started_at)='text' THEN started_at
                        ELSE strftime('%Y-%m-%d %H:%M:%S', started_at, 'unixepoch', '+8 hours')
                    END,
                    CASE
                        WHEN finished_at IS NULL THEN NULL
                        WHEN typeof(finished_at)='text' THEN finished_at
                        ELSE strftime('%Y-%m-%d %H:%M:%S', finished_at, 'unixepoch', '+8 hours')
                    END,
                    details
                FROM jobs_old;
                """
            )
            conn.execute("DROP TABLE IF EXISTS jobs_old;")

    # Update schema version
    conn.execute("INSERT OR REPLACE INTO _meta (key, value) VALUES ('schema_version', '2');")
    conn.commit()


# Files API

def get_downloaded_file_ids() -> Set[str]:
    conn = get_conn()
    cur = conn.execute("SELECT file_id FROM files WHERE status='downloaded'")
    return {row["file_id"] for row in cur.fetchall()}


def _to_windows_rel(path: Optional[Path]) -> Optional[str]:
    if path is None:
        return None
    try:
        base = Path(__file__).resolve().parent.parent  # project root
        rel = Path(path).resolve().relative_to(base)
        s = str(rel)
    except Exception:
        s = str(Path(path).resolve())
    # Force backslashes
    return s.replace('/', '\\')


def mark_file_downloaded(file_id: str, path: Optional[Path] = None, size: Optional[int] = None, origin: str = 'telegram') -> None:
    """Upsert a file entry.
    - size: bytes; stored as MB (float)
    - timestamps: GMT+8 string without microseconds
    - origin: 'telegram' or 'search'
    """
    conn = get_conn()
    now_txt = _now_str_gmt8()
    path_txt = _to_windows_rel(path)
    size_mb = (float(size) / 1048576.0) if (size is not None) else None
    conn.execute(
        """
        INSERT INTO files (file_id, path, size_mb, origin, status, downloaded_at)
        VALUES (?, ?, ?, ?, 'downloaded', ?)
        ON CONFLICT(file_id) DO UPDATE SET
            status=excluded.status,
            origin=excluded.origin,
            path=COALESCE(excluded.path, files.path),
            size_mb=COALESCE(excluded.size_mb, files.size_mb),
            downloaded_at=COALESCE(excluded.downloaded_at, files.downloaded_at)
        ;
        """,
        (file_id, path_txt, size_mb, origin, now_txt),
    )
    conn.commit()


# Jobs API (basic helpers)

def start_job(job_type: str, details: Optional[dict[str, Any]] = None) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO jobs (job_type, status, started_at, details) VALUES (?, 'running', ?, ?)",
        (job_type, _now_str_gmt8(), json.dumps(details or {})),
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_job(job_id: int, status: str = "finished", details: Optional[dict[str, Any]] = None) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE jobs SET status=?, finished_at=?, details=? WHERE id=?",
        (status, _now_str_gmt8(), json.dumps(details or {}), job_id),
    )
    conn.commit()


def record_search_result(path: Path) -> str:
    """Record a search results file into files table with origin='search'.
    Returns the file_id used.
    """
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError:
        size = None
    file_id = f"search:{p.name}"
    mark_file_downloaded(file_id=file_id, path=p, size=size, origin='search')
    return file_id

