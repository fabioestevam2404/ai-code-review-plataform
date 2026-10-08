from dataclasses import dataclass
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class GitHubError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class PullRequestContext:
    repository: str
    number: int
    base_sha: str
    head_sha: str
    title: str
    description: str
    diff: str


class GitHubClient:
    def __init__(self, api_url: str, token: str | None, max_diff_bytes: int):
        self.api_url = api_url.rstrip("/")
        self.token = token
        self.max_diff_bytes = max_diff_bytes

    def _request(self, path: str, accept: str = "application/vnd.github+json"):
        headers = {"Accept": accept, "User-Agent": "ai-code-review-platform/0.1"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(self.api_url + path, headers=headers)
        try:
            with urlopen(request, timeout=30) as response:
                return response.status, response.read()
        except HTTPError as exc:
            raise GitHubError(str(exc), exc.code) from exc
        except URLError as exc:
            raise GitHubError(str(exc)) from exc

    def get_pull_request(self, repository: str, number: int) -> PullRequestContext:
        status, raw = self._request(f"/repos/{repository}/pulls/{number}")
        if status != 200:
            raise GitHubError(f"GitHub PR request returned {status}")
        data = json.loads(raw)
        _, diff_raw = self._request(
            f"/repos/{repository}/pulls/{number}",
            accept="application/vnd.github.v3.diff",
        )
        if len(diff_raw) > self.max_diff_bytes:
            raise GitHubError("pull request diff exceeds MAX_DIFF_BYTES")
        return PullRequestContext(
            repository=repository,
            number=number,
            base_sha=data["base"]["sha"],
            head_sha=data["head"]["sha"],
            title=data.get("title", ""),
            description=data.get("body") or "",
            diff=diff_raw.decode("utf-8", errors="replace"),
        )

    def _post(self, path: str, payload: dict) -> None:
        if not self.token:
            raise GitHubError("GITHUB_TOKEN is required for publishing")
        headers = {
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "ai-code-review-platform/0.1",
            "Authorization": f"Bearer {self.token}",
        }
        request = Request(self.api_url + path, data=json.dumps(payload).encode(), headers=headers, method="POST")
        try:
            with urlopen(request, timeout=30) as response:
                if response.status not in {200, 201}:
                    raise GitHubError(f"POST {path} returned {response.status}", response.status)
        except HTTPError as exc:
            raise GitHubError(str(exc), exc.code) from exc
        except URLError as exc:
            raise GitHubError(str(exc)) from exc

    def post_issue_comment(self, repository: str, number: int, body: str) -> None:
        self._post(f"/repos/{repository}/issues/{number}/comments", {"body": body})

    def post_review(self, repository: str, number: int, commit_id: str, body: str, comments: list[dict]) -> None:
        """Create a PR review with inline comments anchored to lines of the diff (event COMMENT)."""
        # COMMENT, not REQUEST_CHANGES: GitHub rejects REQUEST_CHANGES on the token owner's own PR,
        # and the verdict is already stated in the review body.
        self._post(f"/repos/{repository}/pulls/{number}/reviews", {
            "commit_id": commit_id, "body": body, "event": "COMMENT", "comments": comments,
        })
