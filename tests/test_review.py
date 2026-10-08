from app.github import PullRequestContext
from app.review import ReviewEngine


def test_security_finding_and_quality_gate():
    context = PullRequestContext(
        repository="acme/demo", number=1, base_sha="a", head_sha="b", title="x", description="",
        diff="""diff --git a/app.py b/app.py
index 1..2 100644
--- a/app.py
+++ b/app.py
@@ -1,1 +1,2 @@
+subprocess.run(command, shell=True)
+# TODO remove this
""",
    )
    result = ReviewEngine(10).review(context)
    assert result.verdict == "REQUEST_CHANGES"
    assert any(f.category == "security" for f in result.findings)
    assert any(f.category == "quality" for f in result.findings)


MIXED_DIFF = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1,1 +1,3 @@
+# TODO later
+requests.get(url)
+subprocess.run(cmd, shell=True)
"""


def test_finding_ids_are_stable_across_runs():
    context = PullRequestContext("acme/demo", 1, "a", "b", "x", "", MIXED_DIFF)
    first = [f.finding_id for f in ReviewEngine(10).review(context).findings]
    second = [f.finding_id for f in ReviewEngine(10).review(context).findings]
    assert first == second and len(set(first)) == len(first)


def test_findings_are_ordered_by_severity_rank():
    context = PullRequestContext("acme/demo", 1, "a", "b", "x", "", MIXED_DIFF)
    severities = [f.severity for f in ReviewEngine(10).review(context).findings]
    assert severities == ["HIGH", "MEDIUM", "LOW"]


def test_empty_diff_is_approved():
    context = PullRequestContext("acme/demo", 1, "a", "b", "x", "", "")
    result = ReviewEngine(10).review(context)
    assert result.verdict == "APPROVE"
