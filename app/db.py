from contextlib import contextmanager
import json
import sqlite3
from pathlib import Path
from typing import Iterator

from .models import Finding, ReviewJob, now_iso


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    def init(self) -> None:
        with self.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS review_jobs (
                    review_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    repository TEXT NOT NULL,
                    pull_request INTEGER NOT NULL,
                    base_sha TEXT NOT NULL,
                    head_sha TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    status TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(repository, pull_request, head_sha, profile)
                );
                CREATE INDEX IF NOT EXISTS idx_review_jobs_status
                    ON review_jobs(status, updated_at);
                CREATE TABLE IF NOT EXISTS findings (
                    finding_id TEXT NOT NULL,
                    review_id TEXT NOT NULL REFERENCES review_jobs(review_id),
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (review_id, finding_id)
                );
                CREATE INDEX IF NOT EXISTS idx_findings_review
                    ON findings(review_id);
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    review_id TEXT,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(review_jobs)")}
            if "next_attempt_at" not in columns:
                conn.execute("ALTER TABLE review_jobs ADD COLUMN next_attempt_at TEXT")
            self._migrate_findings_key(conn)

    @staticmethod
    def _migrate_findings_key(conn: sqlite3.Connection) -> None:
        """Finding ids are stable per diff, so the key must be (review_id, finding_id), not finding_id alone."""
        pk = [row["name"] for row in conn.execute("PRAGMA table_info(findings)") if row["pk"]]
        if pk != ["finding_id"]:
            return
        # Standard SQLite table rebuild: foreign keys off while the table is swapped.
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.executescript(
            """
            BEGIN;
            ALTER TABLE findings RENAME TO findings_old;
            DROP INDEX IF EXISTS idx_findings_review;
            CREATE TABLE findings (
                finding_id TEXT NOT NULL,
                review_id TEXT NOT NULL REFERENCES review_jobs(review_id),
                payload_json TEXT NOT NULL,
                PRIMARY KEY (review_id, finding_id)
            );
            INSERT INTO findings(finding_id, review_id, payload_json)
                SELECT finding_id, review_id, payload_json FROM findings_old;
            DROP TABLE findings_old;
            CREATE INDEX IF NOT EXISTS idx_findings_review ON findings(review_id);
            COMMIT;
            """
        )
        conn.execute("PRAGMA foreign_keys=ON")

    def create_job(self, job: ReviewJob) -> tuple[ReviewJob, bool]:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM review_jobs WHERE repository=? AND pull_request=? AND head_sha=? AND profile=?",
                (job.repository, job.pull_request, job.head_sha, job.profile),
            ).fetchone()
            if existing:
                conn.execute("COMMIT")
                return self._job(existing), False
            conn.execute(
                """INSERT INTO review_jobs
                (review_id,event_id,repository,pull_request,base_sha,head_sha,profile,status,risk_level,attempts,error,result_json,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (job.review_id, job.event_id, job.repository, job.pull_request, job.base_sha,
                 job.head_sha, job.profile, job.status, job.risk_level, job.attempts, job.error,
                 json.dumps(job.result), job.created_at, job.updated_at),
            )
            conn.execute("COMMIT")
        self.audit(job.review_id, "REVIEW_CREATED", job.to_dict())
        return job, True

    def get_job(self, review_id: str) -> ReviewJob | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM review_jobs WHERE review_id=?", (review_id,)).fetchone()
        return self._job(row) if row else None

    def claim_next(self, max_attempts: int) -> ReviewJob | None:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            updated = now_iso()
            row = conn.execute(
                """SELECT * FROM review_jobs
                   WHERE attempts < ? AND (
                       status = 'RECEIVED'
                       OR (status = 'RETRY_SCHEDULED' AND (next_attempt_at IS NULL OR next_attempt_at <= ?))
                   )
                   ORDER BY created_at LIMIT 1""",
                (max_attempts, updated),
            ).fetchone()
            if not row:
                conn.execute("COMMIT")
                return None
            conn.execute(
                """UPDATE review_jobs SET status='ANALYZING', attempts=attempts+1, updated_at=?,
                   error=NULL, next_attempt_at=NULL WHERE review_id=?""",
                (updated, row["review_id"]),
            )
            conn.execute("COMMIT")
            row = dict(row)
            row["status"] = "ANALYZING"
            row["attempts"] += 1
            row["updated_at"] = updated
            row["error"] = None
            row["next_attempt_at"] = None
        self.audit(row["review_id"], "JOB_CLAIMED", {"attempt": row["attempts"]})
        return self._job(row)

    def save_result(self, job: ReviewJob, findings: list[Finding], result: dict) -> None:
        with self.connection() as conn:
            conn.execute("BEGIN")
            conn.execute(
                "UPDATE review_jobs SET status='COMPLETED', risk_level=?, result_json=?, updated_at=? WHERE review_id=?",
                (result["risk_level"], json.dumps(result, ensure_ascii=False), now_iso(), job.review_id),
            )
            conn.execute("DELETE FROM findings WHERE review_id=?", (job.review_id,))
            conn.executemany(
                "INSERT INTO findings(finding_id,review_id,payload_json) VALUES(?,?,?)",
                [(f.finding_id, job.review_id, json.dumps(f.to_dict(), ensure_ascii=False)) for f in findings],
            )
            conn.execute("COMMIT")
        self.audit(job.review_id, "REVIEW_COMPLETED", result)

    def save_failure(self, job: ReviewJob, error: str, status: str, next_attempt_at: str | None = None) -> None:
        """Persist a failed attempt as RETRY_SCHEDULED (with next_attempt_at) or a terminal FAILED/STALE."""
        if status not in {"RETRY_SCHEDULED", "FAILED", "STALE"}:
            raise ValueError(f"invalid failure status: {status}")
        with self.connection() as conn:
            conn.execute(
                "UPDATE review_jobs SET status=?, error=?, next_attempt_at=?, updated_at=? WHERE review_id=?",
                (status, error[:4000], next_attempt_at, now_iso(), job.review_id),
            )
        self.audit(job.review_id, "REVIEW_FAILED", {
            "error": error, "attempt": job.attempts, "status": status, "next_attempt_at": next_attempt_at,
        })

    def save_publish_failure(self, job: ReviewJob, error: str) -> None:
        """Analysis is done; keep COMPLETED so a publish error never triggers re-analysis or a duplicate comment."""
        with self.connection() as conn:
            conn.execute(
                "UPDATE review_jobs SET error=?, updated_at=? WHERE review_id=?",
                (f"comment publish failed: {error}"[:4000], now_iso(), job.review_id),
            )
        self.audit(job.review_id, "COMMENT_FAILED", {"error": error})

    def requeue_job(self, review_id: str) -> ReviewJob | None:
        """Reset a terminal job for a manual rerun. Returns None if the job is still queued or running."""
        with self.connection() as conn:
            cursor = conn.execute(
                """UPDATE review_jobs SET status='RECEIVED', attempts=0, error=NULL, next_attempt_at=NULL, updated_at=?
                   WHERE review_id=? AND status IN ('COMPLETED','FAILED','STALE','CANCELLED')""",
                (now_iso(), review_id),
            )
            if cursor.rowcount == 0:
                return None
        self.audit(review_id, "RERUN_REQUESTED", {})
        return self.get_job(review_id)

    def list_findings(self, review_id: str) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute("SELECT payload_json FROM findings WHERE review_id=?", (review_id,)).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def audit(self, review_id: str | None, event_type: str, payload: dict) -> None:
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO audit_events(review_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                (review_id, event_type, json.dumps(payload, ensure_ascii=False), now_iso()),
            )

    @staticmethod
    def _job(row: sqlite3.Row | dict) -> ReviewJob:
        get = row.__getitem__
        return ReviewJob(
            review_id=get("review_id"), event_id=get("event_id"), repository=get("repository"),
            pull_request=get("pull_request"), base_sha=get("base_sha"), head_sha=get("head_sha"),
            profile=get("profile"), status=get("status"), risk_level=get("risk_level"),
            attempts=get("attempts"), error=get("error"), next_attempt_at=get("next_attempt_at"), result=json.loads(get("result_json") or "{}"),
            created_at=get("created_at"), updated_at=get("updated_at"),
        )
