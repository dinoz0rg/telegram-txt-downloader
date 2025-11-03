from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Telegram / Pyrogram
    api_id: int = Field(..., alias="API_ID")
    api_hash: str = Field(..., alias="API_HASH")
    phone_number: str | None = Field(None, alias="PHONE_NUMBER")
    session_name: str = Field("session", alias="SESSION_NAME")

    # Target chat
    group_id: str | int = Field(..., alias="GROUP_ID")

    # Downloading
    download_dir: Path = Field(Path("output\\downloaded_dir"), alias="DOWNLOAD_DIR")
    # Configure limit in megabytes (MB) only
    max_file_size_mb: int = Field(500, alias="MAX_FILE_SIZE_MB")  # preferred, whole MB
    # Canonical bytes value used by the app (computed in post-init)
    max_file_size: int = 500 * 1024 * 1024
    max_file_age_days: int = Field(0, alias="MAX_FILE_AGE_DAYS")

    # Behavior
    auto_refresh_on_failure: bool = Field(True, alias="AUTO_REFRESH_ON_FAILURE")

    # Logging
    logs_dir: Path = Field(Path("logs"), alias="LOGS_DIR")
    log_file: Path = Field(Path("logs\\app.log"), alias="LOG_FILE")

    # Results
    results_dir: Path = Field(Path("output\\searched_dir"), alias="RESULTS_DIR")

    # Database
    db_file: Path = Field(Path("db\\app.db"), alias="DB_FILE")

    # Web API
    host: str = Field("127.0.0.1", alias="HOST")
    port: int = Field(8000, alias="PORT")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False)

    def model_post_init(self, __context):
        base_dir = Path(__file__).resolve().parent.parent  # project root
        def _to_abs(p: Path) -> Path:
            return p if p.is_absolute() else (base_dir / p).resolve()
        # Normalize paths relative to project root when given as relative
        self.download_dir = _to_abs(self.download_dir)
        self.logs_dir = _to_abs(self.logs_dir)
        self.log_file = _to_abs(self.log_file)
        self.results_dir = _to_abs(self.results_dir)
        self.db_file = _to_abs(self.db_file)

        # Resolve canonical max_file_size in bytes from MB-only setting
        if self.max_file_size_mb is None or self.max_file_size_mb <= 0:
            raise ValueError("MAX_FILE_SIZE_MB must be set to a positive integer (MB)")
        self.max_file_size = int(self.max_file_size_mb) * 1024 * 1024

    @property
    def max_file_size_mb_display(self) -> str:
        mbytes = self.max_file_size / (1024 * 1024)
        return str(int(mbytes)) if float(mbytes).is_integer() else f"{mbytes:.1f}"


settings = Settings()