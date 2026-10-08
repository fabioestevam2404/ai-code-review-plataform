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


def test_empty_diff_is_approved():
    context = PullRequestContext("acme/demo", 1, "a", "b", "x", "", "")
    result = ReviewEngine(10).review(context)
    assert result.verdict == "APPROVE"
