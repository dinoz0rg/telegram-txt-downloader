import logging
import logging.handlers
from pathlib import Path
from .config import settings


def setup_logging() -> logging.Logger:
    Path(settings.logs_dir).mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("tg_downloader")
    logger.setLevel(logging.INFO)

    # Avoid adding handlers twice if reloaded
    if logger.handlers:
        return logger

    file_handler = logging.handlers.TimedRotatingFileHandler(
        str(settings.log_file), when="D", interval=7, backupCount=10, encoding="utf-8"
    )
    file_handler.suffix = "_%Y-%m-%d"
    console_handler = logging.StreamHandler()

    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(fmt)
    console_handler.setFormatter(fmt)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.info(f"Logging to {settings.log_file} with 7-day rotation")
    return logger
