import pytest

from app.analyzers import PerformanceAgent, SecurityAgent
from app.diff import ChangedLine


def added(*texts: str, file: str = "app.py", start: int = 1) -> list[ChangedLine]:
    return [ChangedLine(file, start + i, text) for i, text in enumerate(texts)]


def titles(findings) -> list[str]:
    return [f.title for f in findings]


@pytest.mark.parametrize("code, title", [
    ("result = eval(user_input)", "Dynamic code execution with eval/exec"),
    ("exec(source)", "Dynamic code execution with eval/exec"),
    ("obj = pickle.loads(payload)", "Unsafe deserialization with pickle"),
    ("data = yaml.load(stream)", "Unsafe YAML loading"),
    ("data = yaml.unsafe_load(stream)", "Unsafe YAML loading"),
    ("requests.get(url, timeout=5, verify=False)", "TLS certificate verification disabled"),
    ("digest = hashlib.md5(password).hexdigest()", "Weak hash algorithm"),
])
def test_security_rules_detect(code, title):
    assert title in titles(SecurityAgent().analyze(added(code)))


@pytest.mark.parametrize("code", [
    "model.eval()",
    "data = yaml.load(stream, Loader=yaml.SafeLoader)",
    "data = yaml.safe_load(stream)",
    "requests.get(url, timeout=5, verify=True)",
    "etag = hashlib.md5(body, usedforsecurity=False).hexdigest()",
    "digest = hashlib.sha256(body).hexdigest()",
])
def test_security_rules_ignore_safe_code(code):
    assert SecurityAgent().analyze(added(code)) == []


def test_query_inside_loop_body():
    lines = added(
        "for user in users:",
        "    name = user.name",
        "    orders = cursor.execute('SELECT * FROM orders WHERE user_id = ?', (user.id,))",
    )
    findings = PerformanceAgent().analyze(lines)
    assert [(f.title, f.start_line) for f in findings] == [("Possible query inside loop", 3)]


def test_query_inside_single_line_loop():
    findings = PerformanceAgent().analyze(added("for item in ids: db.query(item)  # TODO batch"))
    assert titles(findings) == ["Possible query inside loop"]


def test_query_after_loop_ends_is_not_flagged():
    lines = added(
        "for user in users:",
        "    total += 1",
        "rows = db.query(all_ids)",
    )
    assert PerformanceAgent().analyze(lines) == []


def test_gap_in_added_lines_resets_loop_context():
    lines = added("for user in users:") + added("    rows = db.query(all_ids)", start=10)
    assert PerformanceAgent().analyze(lines) == []
