from dataclasses import dataclass
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class GitHubError(RuntimeError):
    pass


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
        except (HTTPError, URLError) as exc:
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

    def post_issue_comment(self, repository: str, number: int, body: str) -> None:
        if not self.token:
            raise GitHubError("GITHUB_TOKEN is required for publishing")
        payload = json.dumps({"body": body}).encode()
        headers = {
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "ai-code-review-platform/0.1",
            "Authorization": f"Bearer {self.token}",
        }
        request = Request(
            self.api_url + f"/repos/{repository}/issues/{number}/comments",
            data=payload,
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=30) as response:
                if response.status not in {200, 201}:
                    raise GitHubError(f"comment request returned {response.status}")
        except (HTTPError, URLError) as exc:
            raise GitHubError(str(exc)) from exc
