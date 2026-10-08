from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
import json


SEVERITIES = ("BLOCKER", "HIGH", "MEDIUM", "LOW", "INFO")
TERMINAL_STATUSES = ("COMPLETED", "FAILED", "STALE", "CANCELLED")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Finding:
    finding_id: str
    category: str
    severity: str
    confidence: float
    title: str
    file: str
    start_line: int
    end_line: int
    problem: str
    impact: str
    evidence: str
    recommendation: str
    blocking: bool = False
    status: str = "CONFIRMED"
    agent: str = ""

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"invalid severity: {self.severity}")
        self.confidence = min(1.0, max(0.0, float(self.confidence)))
        self.blocking = self.severity in {"BLOCKER", "HIGH"}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReviewJob:
    review_id: str
    event_id: str
    repository: str
    pull_request: int
    base_sha: str
    head_sha: str
    profile: str = "standard"
    status: str = "RECEIVED"
    risk_level: str = "LOW"
    attempts: int = 0
    error: str | None = None
    next_attempt_at: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
