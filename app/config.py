from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PLACEHOLDER_SECRETS = {"change-me", "changeme", "secret"}
COMMENT_MODES = {"review", "issue"}


class ConfigError(RuntimeError):
    pass


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
    github_comment_mode: str
    api_admin_token: str | None
    worker_poll_seconds: float
    worker_max_attempts: int
    worker_retry_base_seconds: float
    max_diff_bytes: int
    max_findings_per_category: int

    def validate(self) -> None:
        """Refuse to start with settings that would expose the service or fail every publish."""
        errors: list[str] = []
        if self.app_env == "production":
            if not self.github_webhook_secret or self.github_webhook_secret in PLACEHOLDER_SECRETS:
                errors.append("GITHUB_WEBHOOK_SECRET must be set to a real secret in production")
            if not self.api_admin_token or self.api_admin_token in PLACEHOLDER_SECRETS:
                errors.append("API_ADMIN_TOKEN must be set in production")
        if self.github_write_enabled and not self.github_token:
            errors.append("GITHUB_WRITE_ENABLED=true requires GITHUB_TOKEN")
        if self.github_comment_mode not in COMMENT_MODES:
            errors.append(f"GITHUB_COMMENT_MODE must be one of {sorted(COMMENT_MODES)}")
        if errors:
            raise ConfigError("; ".join(errors))

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            app_env=os.getenv("APP_ENV", "development"),
            database_path=Path(os.getenv("DATABASE_PATH", "./data/reviews.sqlite3")),
            github_webhook_secret=os.getenv("GITHUB_WEBHOOK_SECRET", ""),
            github_token=os.getenv("GITHUB_TOKEN") or None,
            github_api_url=os.getenv("GITHUB_API_URL", "https://api.github.com").rstrip("/"),
            github_write_enabled=_bool("GITHUB_WRITE_ENABLED"),
            github_comment_mode=os.getenv("GITHUB_COMMENT_MODE", "review").strip().lower(),
            api_admin_token=os.getenv("API_ADMIN_TOKEN") or None,
            worker_poll_seconds=max(0.2, float(os.getenv("WORKER_POLL_SECONDS", "1"))),
            worker_max_attempts=max(1, int(os.getenv("WORKER_MAX_ATTEMPTS", "3"))),
            worker_retry_base_seconds=max(0.0, float(os.getenv("WORKER_RETRY_BASE_SECONDS", "30"))),
            max_diff_bytes=max(1024, int(os.getenv("MAX_DIFF_BYTES", "1000000"))),
            max_findings_per_category=max(1, int(os.getenv("MAX_FINDINGS_PER_CATEGORY", "10"))),
        )
