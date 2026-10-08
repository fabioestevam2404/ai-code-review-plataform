from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_env: str
    database_path: Path
    github_webhook_secret: str
    github_token: str | None
    github_api_url: str
    github_write_enabled: bool
    api_admin_token: str | None
    worker_poll_seconds: float
    worker_max_attempts: int
    worker_retry_base_seconds: float
    max_diff_bytes: int
    max_findings_per_category: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            app_env=os.getenv("APP_ENV", "development"),
            database_path=Path(os.getenv("DATABASE_PATH", "./data/reviews.sqlite3")),
            github_webhook_secret=os.getenv("GITHUB_WEBHOOK_SECRET", ""),
            github_token=os.getenv("GITHUB_TOKEN") or None,
            github_api_url=os.getenv("GITHUB_API_URL", "https://api.github.com").rstrip("/"),
            github_write_enabled=_bool("GITHUB_WRITE_ENABLED"),
            api_admin_token=os.getenv("API_ADMIN_TOKEN") or None,
            worker_poll_seconds=max(0.2, float(os.getenv("WORKER_POLL_SECONDS", "1"))),
            worker_max_attempts=max(1, int(os.getenv("WORKER_MAX_ATTEMPTS", "3"))),
            worker_retry_base_seconds=max(0.0, float(os.getenv("WORKER_RETRY_BASE_SECONDS", "30"))),
            max_diff_bytes=max(1024, int(os.getenv("MAX_DIFF_BYTES", "1000000"))),
            max_findings_per_category=max(1, int(os.getenv("MAX_FINDINGS_PER_CATEGORY", "10"))),
        )
