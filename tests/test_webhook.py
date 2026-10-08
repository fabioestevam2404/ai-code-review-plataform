import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from app import main


def test_webhook_is_idempotent(monkeypatch, tmp_path):
    from dataclasses import replace

    monkeypatch.setattr(main, "settings", replace(main.settings, github_webhook_secret="secret", app_env="test"))
    monkeypatch.setattr(main.db, "path", tmp_path / "test.sqlite3")
    async def no_worker():
        return None
    monkeypatch.setattr(main.worker, "run", no_worker)
    main.db.init()
    payload = {
        "action": "opened", "number": 7,
        "repository": {"full_name": "acme/demo"},
        "pull_request": {"base": {"sha": "base"}, "head": {"sha": "head"}},
    }
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    with TestClient(main.app) as client:
        first = client.post("/webhooks/github", content=body, headers={
            "X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "delivery-1",
            "X-Hub-Signature-256": signature,
        })
        second = client.post("/webhooks/github", content=body, headers={
            "X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "delivery-2",
            "X-Hub-Signature-256": signature,
        })
    assert first.status_code == second.status_code == 202
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert first.json()["review_id"] == second.json()["review_id"]


def test_rerun_requeues_terminal_job_and_rejects_active(monkeypatch, tmp_path):
    from dataclasses import replace

    monkeypatch.setattr(main, "settings", replace(main.settings, api_admin_token=None))
    monkeypatch.setattr(main.db, "path", tmp_path / "test.sqlite3")
    async def no_worker():
        return None
    monkeypatch.setattr(main.worker, "run", no_worker)
    main.db.init()
    main.db.create_job(main.ReviewJob(review_id="rev_x", event_id="d", repository="acme/demo",
                                      pull_request=1, base_sha="a", head_sha="b"))
    with TestClient(main.app) as client:
        assert client.post("/reviews/rev_x/rerun").status_code == 409
        main.db.save_failure(main.db.get_job("rev_x"), "err", "FAILED")
        response = client.post("/reviews/rev_x/rerun")
        assert client.post("/reviews/missing/rerun").status_code == 404
    assert response.status_code == 202
    assert response.json() == {"requeued": True, "review_id": "rev_x", "status": "RECEIVED"}
