from pathlib import Path
from typing import Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Telegram Bot Configuration
    BOT_TOKEN: str = Field(
        default="",
        description="Telegram Bot API Token from @BotFather",
    )
    BOT_API_SERVER: Optional[str] = Field(
        default=None,
        description="Custom/Local Telegram Bot API server URL (e.g. http://telegram-bot-api:8081). If None, uses official server.",
    )

    # Storage & Temporary Files
    DOWNLOAD_DIR: Path = Field(
        default=Path("/tmp/downloads"),
        description="Directory for temporary download subdirectories",
    )
    DATA_DIR: Path = Field(
        default=Path("/app/data"),
        description="Directory for persistent application data (stats SQLite database)",
    )
    MIN_FREE_DISK_GB: float = Field(
        default=3.0,
        description="Minimum free disk space in GB required before starting a download",
    )
    JANITOR_INTERVAL_MINUTES: int = Field(
        default=15,
        description="Interval in minutes for running the disk janitor cleanup loop",
    )
    JANITOR_MAX_AGE_MINUTES: int = Field(
        default=30,
        description="Max age in minutes for temporary download folders before removal by janitor",
    )

    # Concurrency Limits
    MAX_GLOBAL_CONCURRENT: int = Field(
        default=3,
        ge=1,
        description="Global maximum concurrent download subprocesses",
    )
    MAX_USER_CONCURRENT: int = Field(
        default=1,
        ge=1,
        description="Maximum concurrent downloads allowed per individual user",
    )
    MAX_GROUP_CONCURRENT: int = Field(
        default=2,
        ge=1,
        description="Maximum concurrent downloads allowed per group/supergroup",
    )

    # Rate Limiting (Token Bucket)
    USER_RATE_LIMIT_PER_MINUTE: int = Field(
        default=5,
        ge=1,
        description="Max download requests per minute for a single user in DM",
    )
    GROUP_RATE_LIMIT_PER_MINUTE: int = Field(
        default=10,
        ge=1,
        description="Max download requests per minute for a group chat",
    )

    # Execution Limits & Subprocess Guardrails
    DOWNLOAD_TIMEOUT_SECONDS: int = Field(
        default=600,
        ge=30,
        le=1800,
        description="Hard execution timeout in seconds for download subprocesses",
    )
    FFMPEG_THREADS: int = Field(
        default=2,
        ge=1,
        le=8,
        description="Maximum threads allocated to FFmpeg remuxing",
    )
    MAX_FILE_SIZE_MB: int = Field(
        default=50,
        ge=1,
        description="Maximum upload file size in MB for standard Bot API (50 MB) or local server (up to 2000 MB)",
    )

    # Binaries & CLI Paths
    OMNIGET_BIN: Optional[str] = Field(
        default=None,
        description="Path to omniget-cli binary if installed (falls back to yt-dlp if not found)",
    )
    YTDLP_BIN: str = Field(
        default="yt-dlp",
        description="yt-dlp executable name or path",
    )
    FFMPEG_BIN: str = Field(
        default="ffmpeg",
        description="ffmpeg executable name or path",
    )

    # UI / UX Throttling
    PROGRESS_UPDATE_INTERVAL_SECONDS: float = Field(
        default=4.0,
        ge=1.0,
        description="Minimum interval in seconds between Telegram message edits for download progress",
    )
    MAX_LINKS_PER_MESSAGE: int = Field(
        default=2,
        ge=1,
        le=5,
        description="Maximum number of links parsed and downloaded per message",
    )

    # Redis (Optional backend for distributed state / queuing)
    REDIS_URL: Optional[str] = Field(
        default=None,
        description="Redis connection URL (e.g. redis://redis:6379/0). If not set, in-memory queue/locks are used.",
    )

    # Admins (Telegram user IDs with superadmin access)
    ADMIN_IDS: list[int] = Field(
        default_factory=list,
        description="List of global bot administrator Telegram user IDs",
    )

    @field_validator("ADMIN_IDS", mode="before")
    @classmethod
    def parse_admin_ids(cls, v):
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            # In case it's a JSON array string e.g. "[123, 456]"
            if v.startswith("[") and v.endswith("]"):
                import json
                try:
                    return [int(x) for x in json.loads(v)]
                except Exception:
                    pass
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        if isinstance(v, (int, float)):
            return [int(v)]
        return v or []

    @field_validator("DOWNLOAD_DIR", "DATA_DIR", mode="before")
    @classmethod
    def parse_path_fields(cls, v):
        if isinstance(v, str):
            return Path(v)
        return v


settings = Settings()
