from contextlib import asynccontextmanager
import asyncio
import hashlib
import hmac
import json
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request

from .config import Settings
from .db import Database
from .models import ReviewJob
from .worker import ReviewWorker


settings = Settings.from_env()
db = Database(settings.database_path)
worker = ReviewWorker(db, settings)


def verify_signature(body: bytes, signature: str | None) -> bool:
    if not settings.github_webhook_secret:
        return settings.app_env != "production"
    if not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(settings.github_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def require_admin(request: Request) -> None:
    if settings.api_admin_token and not hmac.compare_digest(
        request.headers.get("X-API-Key", ""), settings.api_admin_token
    ):
        raise HTTPException(status_code=401, detail="invalid API key")


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    task = asyncio.create_task(worker.run())
    yield
    worker.stop_event.set()
    await task


app = FastAPI(title="AI Code Review Platform", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "ai-code-review-platform"}


@app.post("/webhooks/github", status_code=202)
async def github_webhook(
    request: Request,
    x_github_event: str | None = Header(default=None),
    x_github_delivery: str | None = Header(default=None),
    x_hub_signature_256: str | None = Header(default=None),
) -> dict:
    body = await request.body()
    if not verify_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="invalid webhook signature")
    if not x_github_delivery:
        raise HTTPException(status_code=400, detail="missing X-GitHub-Delivery")
    if x_github_event != "pull_request":
        return {"accepted": False, "reason": "event_not_supported"}
    try:
        payload = json.loads(body)
        action = payload["action"]
        repository = payload["repository"]["full_name"]
        number = int(payload["number"])
        base_sha = payload["pull_request"]["base"]["sha"]
        head_sha = payload["pull_request"]["head"]["sha"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="invalid pull_request payload") from exc
    if action not in {"opened", "synchronize", "reopened"}:
        return {"accepted": False, "reason": "action_not_supported"}
    job = ReviewJob(
        review_id=f"rev_{uuid4().hex}", event_id=x_github_delivery,
        repository=repository, pull_request=number, base_sha=base_sha, head_sha=head_sha,
    )
    stored, created = db.create_job(job)
    return {"accepted": True, "created": created, "review_id": stored.review_id, "status": stored.status}


@app.get("/reviews/{review_id}")
def get_review(review_id: str, request: Request) -> dict:
    require_admin(request)
    job = db.get_job(review_id)
    if not job:
        raise HTTPException(status_code=404, detail="review not found")
    return {**job.to_dict(), "findings": db.list_findings(review_id)}


@app.get("/reviews/{review_id}/findings")
def get_findings(review_id: str, request: Request) -> dict:
    require_admin(request)
    if not db.get_job(review_id):
        raise HTTPException(status_code=404, detail="review not found")
    return {"review_id": review_id, "findings": db.list_findings(review_id)}


@app.post("/reviews/{review_id}/rerun", status_code=202)
def rerun_review(review_id: str, request: Request) -> dict:
    require_admin(request)
    if not db.get_job(review_id):
        raise HTTPException(status_code=404, detail="review not found")
    # The unique key (repository, PR, head_sha, profile) allows one job per commit, so a rerun
    # requeues that job in place instead of creating a duplicate.
    job = db.requeue_job(review_id)
    if not job:
        raise HTTPException(status_code=409, detail="review is still queued or running")
    return {"requeued": True, "review_id": job.review_id, "status": job.status}
