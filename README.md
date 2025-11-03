### Telegram TXT Downloader & Search — FastAPI Service

This project exposes your Telegram `.txt` file downloader and a high‑performance text search as a web service using FastAPI.

You can use the built-in server-rendered web UI or call REST endpoints directly. The UI is available at `/ui`.

#### Features
- Async Telegram downloader built on Pyrogram
- FloodWait handling, size/age filters, sanitized filenames
- Resumable downloads tracked in a lightweight SQLite database (`/db/app.db`) with per-file path, size in MB, origin (telegram|search), and timestamps stored in GMT+8 without microseconds
- Rotating logs (7‑day rotation)
- REST API: start/stop/status/files/logs/search
- Serves downloaded files from `/downloaded/*`

![App UI](https://i.imgur.com/Be8BjvX.png)

### Legal disclaimer
- This project is provided for educational and lawful use only.
- Do not use it to download, store, search, process, or distribute content that you do not have the legal right to access.
- You are solely responsible for complying with all applicable laws and regulations, as well as the terms of service of the platforms you use.
- The authors and contributors are not responsible or liable for any misuse or damages resulting from the use of this software.

---

#### Requirements
- Python 3.10+
- A Telegram app `API_ID` and `API_HASH` from https://my.telegram.org
- Your Telegram `GROUP_ID` (chat/channel ID or username like `@channel`)
- Optionally a `PHONE_NUMBER` for first authorization (Pyrogram session is saved locally)

Install dependencies:
```bash
pip install -r requirements.txt
```

Create a `.env` file in the project root (see `.env.example`):
```ini
API_ID=123456
API_HASH=your_api_hash_here
PHONE_NUMBER=+10001112233
GROUP_ID=@your_channel_or_group
DOWNLOAD_DIR=output\downloaded_dir
RESULTS_DIR=output\searched_dir
DB_FILE=db\app.db
LOGS_DIR=logs
LOG_FILE=logs\app.log
SESSION_NAME=session
HOST=127.0.0.1
PORT=8000
MAX_FILE_SIZE_MB=500
MAX_FILE_AGE_DAYS=0
AUTO_REFRESH_ON_FAILURE=true
```

Run the API server (development):
```bash
uvicorn app.main:app --host %HOST% --port %PORT% --reload
```
If `%HOST%`/`%PORT%` are not set in the environment, you can just run:
```bash
uvicorn app.main:app --reload
```
Then open Swagger UI:
- http://127.0.0.1:8000/docs

---

#### Usage Overview
- GET `/health` — lightweight healthcheck
- POST `/api/downloader/start` — starts the background download task
- POST `/api/downloader/stop` — requests a graceful stop (supports `?force=true`)
- GET `/api/downloader/status` — detailed status, progress, and diagnostics
- GET `/api/files` — lists downloaded `.txt` files (recursive)
- GET `/api/results/files` — lists search results files
- DELETE `/api/results/{name}` — delete a specific search results file
- GET `/api/logs` — tails app log (last 2000 chars by default)
- POST `/api/search?keyword=your+term` — searches all `.txt` files and writes matches into a timestamped results file; returns counters and result file path
- GET `/downloaded/{path}` — serves a downloaded file (inline)
- GET `/results/{name}` — serves a search results file (inline)
- Server-rendered UI: `/ui` (dash), `/ui/files`, `/ui/search`, `/ui/logs`, `/ui/settings`

Notes:
- First run may prompt for Telegram login in the console to create a local session (Pyrogram). After that, it reuses the session file.
- For private channels/groups ensure the account has access.

---

#### Common Tips
- Windows paths are used with backslashes. Configure `DOWNLOAD_DIR` and `LOG_FILE` accordingly.
- The service tracks downloaded files in the SQLite database at `db/app.db` by default.
- To reset downloads state, stop the service and either delete `db/app.db` or `DELETE FROM files;` inside it (you can use any SQLite browser). To re‑authenticate, remove the local `session*` files created by Pyrogram.

---

#### Production
Use a proper process manager and a reverse proxy. Example:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
```

### cURL examples (Jobs)

- Start a search and get `job_id`:
```
curl -X POST "http://127.0.0.1:8000/api/jobs/search?keyword=test"
```
- List jobs:
```
curl "http://127.0.0.1:8000/api/jobs"
```
- Job status:
```
curl "http://127.0.0.1:8000/api/jobs/<job_id>"
```
- Cancel a job:
```
curl -X DELETE "http://127.0.0.1:8000/api/jobs/<job_id>"
```

