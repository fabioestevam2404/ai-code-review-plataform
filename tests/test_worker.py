from dataclasses import replace
import sqlite3

import pytest

from app.config import Settings
from app.db import Database
from app.github import GitHubError, PullRequestContext
from app.models import Finding, ReviewJob
from app.worker import ReviewWorker


RISKY_DIFF = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,1 +1,2 @@
+subprocess.run(command, shell=True)
+# TODO remove
"""


class FakeGitHub:
    def __init__(self, head_sha="head", fail_fetch=False, fail_post=False, diff="", review_error=None):
        self.head_sha = head_sha
        self.fail_fetch = fail_fetch
        self.fail_post = fail_post
        self.diff = diff
        self.review_error = review_error
        self.comments: list[str] = []
        self.reviews: list[dict] = []

    def get_pull_request(self, repository, number):
        if self.fail_fetch:
            raise GitHubError("boom")
        return PullRequestContext(repository, number, "base", self.head_sha, "t", "", self.diff)

    def post_issue_comment(self, repository, number, body):
        self.comments.append(body)
        if self.fail_post:
            raise GitHubError("timeout after create")

    def post_review(self, repository, number, commit_id, body, comments):
        if self.review_error:
            raise self.review_error
        self.reviews.append({"commit_id": commit_id, "body": body, "comments": comments})


def audit_events(db, review_id):
    with db.connection() as conn:
        rows = conn.execute("SELECT event_type, payload_json FROM audit_events WHERE review_id=? ORDER BY id", (review_id,))
        return [(row["event_type"], row["payload_json"]) for row in rows]


@pytest.fixture
def setup(tmp_path):
    settings = replace(
        Settings.from_env(), database_path=tmp_path / "db.sqlite3", github_write_enabled=True,
        worker_max_attempts=3, worker_retry_base_seconds=60, github_comment_mode="review",
    )
    db = Database(settings.database_path)
    db.init()
    db.create_job(ReviewJob(review_id="rev_1", event_id="d1", repository="acme/demo",
                            pull_request=1, base_sha="base", head_sha="head"))
    worker = ReviewWorker(db, settings)
    return db, worker, settings


def run_once(db, worker, settings):
    job = db.claim_next(settings.worker_max_attempts)
    if job:
        worker.process(job)
    return job


def test_transient_failure_is_scheduled_with_backoff_then_terminal(setup):
    db, worker, settings = setup
    worker.github = FakeGitHub(fail_fetch=True)
    run_once(db, worker, settings)
    job = db.get_job("rev_1")
    assert job.status == "RETRY_SCHEDULED" and job.next_attempt_at
    # Backoff: not claimable before next_attempt_at.
    assert db.claim_next(settings.worker_max_attempts) is None

    for attempt in (2, 3):
        with db.connection() as conn:
            conn.execute("UPDATE review_jobs SET next_attempt_at='2000-01-01T00:00:00+00:00'")
        assert run_once(db, worker, settings).attempts == attempt
    assert db.get_job("rev_1").status == "FAILED"
    assert db.claim_next(settings.worker_max_attempts) is None


def test_stale_commit_is_terminal(setup):
    db, worker, settings = setup
    worker.github = FakeGitHub(head_sha="other")
    run_once(db, worker, settings)
    assert db.get_job("rev_1").status == "STALE"
    assert db.claim_next(settings.worker_max_attempts) is None


def test_publish_failure_keeps_completed_and_does_not_repost(setup):
    db, worker, settings = setup
    worker.github = FakeGitHub(fail_post=True)
    run_once(db, worker, settings)
    job = db.get_job("rev_1")
    assert job.status == "COMPLETED"
    assert job.error.startswith("comment publish failed")
    assert run_once(db, worker, settings) is None
    assert len(worker.github.comments) == 1


def test_review_mode_posts_inline_comments_on_head_commit(setup):
    db, worker, settings = setup
    worker.github = FakeGitHub(diff=RISKY_DIFF)
    run_once(db, worker, settings)
    assert worker.github.comments == []
    [review] = worker.github.reviews
    assert review["commit_id"] == "head"
    assert "REQUEST_CHANGES" in review["body"]
    assert [(c["path"], c["line"], c["side"]) for c in review["comments"]] == [("app.py", 1, "RIGHT"), ("app.py", 2, "RIGHT")]
    assert "[HIGH] Command execution with shell=True" in review["comments"][0]["body"]
    assert ("COMMENT_PUBLISHED", '{"mode": "review"}') in audit_events(db, "rev_1")


def test_review_rejected_with_422_falls_back_to_issue_comment(setup):
    db, worker, settings = setup
    worker.github = FakeGitHub(diff=RISKY_DIFF, review_error=GitHubError("Unprocessable", 422))
    run_once(db, worker, settings)
    assert len(worker.github.comments) == 1
    events = [e for e, _ in audit_events(db, "rev_1")]
    assert "INLINE_REVIEW_REJECTED" in events
    assert ("COMMENT_PUBLISHED", '{"mode": "issue_comment"}') in audit_events(db, "rev_1")
    assert db.get_job("rev_1").error is None


def test_review_server_error_does_not_fall_back(setup):
    db, worker, settings = setup
    # A 5xx may have created the review anyway, so a fallback could duplicate output.
    worker.github = FakeGitHub(diff=RISKY_DIFF, review_error=GitHubError("Bad gateway", 502))
    run_once(db, worker, settings)
    assert worker.github.comments == []
    job = db.get_job("rev_1")
    assert job.status == "COMPLETED" and job.error.startswith("comment publish failed")


def test_issue_mode_posts_single_comment(setup):
    db, worker, settings = setup
    worker.settings = replace(settings, github_comment_mode="issue")
    worker.github = FakeGitHub(diff=RISKY_DIFF)
    run_once(db, worker, settings)
    assert worker.github.reviews == [] and len(worker.github.comments) == 1


def test_same_finding_in_two_reviews_does_not_collide(setup):
    db, worker, settings = setup
    finding = Finding("quality-abc", "quality", "LOW", 0.9, "t", "a.py", 1, 1, "p", "i", "e", "r")
    db.create_job(ReviewJob(review_id="rev_2", event_id="d2", repository="acme/demo",
                            pull_request=2, base_sha="base", head_sha="head"))
    result = {"risk_level": "LOW"}
    db.save_result(db.get_job("rev_1"), [finding], result)
    db.save_result(db.get_job("rev_2"), [finding], result)
    assert len(db.list_findings("rev_1")) == len(db.list_findings("rev_2")) == 1


def test_migrates_legacy_findings_primary_key(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(
        """CREATE TABLE findings (finding_id TEXT PRIMARY KEY, review_id TEXT NOT NULL, payload_json TEXT NOT NULL);
           INSERT INTO findings VALUES ('f1', 'rev_old', '{"x": 1}');"""
    )
    conn.close()
    db = Database(path)
    db.init()
    db.init()  # idempotent
    with db.connection() as c:
        pk = [row["name"] for row in c.execute("PRAGMA table_info(findings)") if row["pk"]]
    assert set(pk) == {"finding_id", "review_id"}
    assert db.list_findings("rev_old") == [{"x": 1}]


def test_requeue_only_terminal_jobs(setup):
    db, worker, settings = setup
    assert db.requeue_job("rev_1") is None  # still RECEIVED
    worker.github = FakeGitHub()
    run_once(db, worker, settings)
    job = db.requeue_job("rev_1")
    assert job.status == "RECEIVED" and job.attempts == 0
    run_once(db, worker, settings)
    assert db.get_job("rev_1").status == "COMPLETED"
    assert len(worker.github.comments) == 2
