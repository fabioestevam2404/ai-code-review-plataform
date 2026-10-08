import asyncio
from datetime import datetime, timedelta, timezone
import logging

from .config import Settings
from .db import Database
from .github import GitHubClient
from .review import ReviewEngine, render_comment

log = logging.getLogger(__name__)

MAX_RETRY_DELAY_SECONDS = 900.0


class ReviewWorker:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings
        self.github = GitHubClient(settings.github_api_url, settings.github_token, settings.max_diff_bytes)
        self.engine = ReviewEngine(settings.max_findings_per_category)
        self.stop_event = asyncio.Event()

    async def run(self) -> None:
        while not self.stop_event.is_set():
            job = await asyncio.to_thread(self.db.claim_next, self.settings.worker_max_attempts)
            if job:
                await asyncio.to_thread(self.process, job)
                continue
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=self.settings.worker_poll_seconds)
            except asyncio.TimeoutError:
                pass

    def retry_delay(self, attempts: int) -> float:
        return min(MAX_RETRY_DELAY_SECONDS, self.settings.worker_retry_base_seconds * 2 ** max(0, attempts - 1))

    def process(self, job) -> None:
        try:
            context = self.github.get_pull_request(job.repository, job.pull_request)
            if context.head_sha != job.head_sha:
                # Terminal: retrying cannot help; the new head arrives through its own webhook.
                self.db.audit(job.review_id, "STALE_COMMIT", {"expected": job.head_sha, "actual": context.head_sha})
                self.db.save_failure(job, "PR head changed before analysis; submit a new webhook", "STALE")
                return
            result = self.engine.review(context)
            self.db.save_result(job, result.findings, result.to_dict())
        except Exception as exc:  # noqa: BLE001 - boundary must persist failure
            log.exception("review %s failed", job.review_id)
            if job.attempts >= self.settings.worker_max_attempts:
                self.db.save_failure(job, str(exc), "FAILED")
            else:
                next_at = datetime.now(timezone.utc) + timedelta(seconds=self.retry_delay(job.attempts))
                self.db.save_failure(job, str(exc), "RETRY_SCHEDULED", next_at.isoformat())
            return
        if self.settings.github_write_enabled:
            # Publishing happens once per analysis; failures are recorded, never retried automatically,
            # because the comment may already exist on GitHub (e.g. response timeout after creation).
            try:
                self.github.post_issue_comment(job.repository, job.pull_request, render_comment(result))
                self.db.audit(job.review_id, "COMMENT_PUBLISHED", {"mode": "issue_comment"})
            except Exception as exc:  # noqa: BLE001
                log.exception("publishing comment for review %s failed", job.review_id)
                self.db.save_publish_failure(job, str(exc))
