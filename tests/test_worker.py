from dataclasses import replace

import pytest

from app.config import Settings
from app.db import Database
from app.github import GitHubError, PullRequestContext
from app.models import ReviewJob
from app.worker import ReviewWorker


class FakeGitHub:
    def __init__(self, head_sha="head", fail_fetch=False, fail_post=False):
        self.head_sha = head_sha
        self.fail_fetch = fail_fetch
        self.fail_post = fail_post
        self.comments: list[str] = []

    def get_pull_request(self, repository, number):
        if self.fail_fetch:
            raise GitHubError("boom")
        return PullRequestContext(repository, number, "base", self.head_sha, "t", "", "")

    def post_issue_comment(self, repository, number, body):
        self.comments.append(body)
        if self.fail_post:
            raise GitHubError("timeout after create")


@pytest.fixture
def setup(tmp_path):
    settings = replace(
        Settings.from_env(), database_path=tmp_path / "db.sqlite3", github_write_enabled=True,
        worker_max_attempts=3, worker_retry_base_seconds=60,
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
