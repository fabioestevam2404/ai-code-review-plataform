from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from .analyzers import PerformanceAgent, QualityAgent, SecurityAgent, deduplicate
from .diff import changed_lines
from .github import PullRequestContext
from .models import Finding


@dataclass
class ReviewResult:
    findings: list[Finding]
    risk_level: str
    verdict: str
    limitations: list[str]
    agents_executed: list[str]
    tools_executed: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "risk_level": self.risk_level,
            "findings": [f.to_dict() for f in self.findings],
            "limitations": self.limitations,
            "agents_executed": self.agents_executed,
            "tools_executed": self.tools_executed,
        }


class ReviewEngine:
    def __init__(self, max_findings_per_category: int):
        self.max_findings_per_category = max_findings_per_category

    def review(self, context: PullRequestContext) -> ReviewResult:
        lines = changed_lines(context.diff)
        agents = [SecurityAgent(), QualityAgent()]
        if any(line.file.endswith((".py", ".js", ".ts", ".java", ".go")) for line in lines):
            agents.append(PerformanceAgent())
        with ThreadPoolExecutor(max_workers=len(agents), thread_name_prefix="review-agent") as pool:
            batches = list(pool.map(lambda agent: agent.analyze(lines), agents))
        findings = deduplicate(item for batch in batches for item in batch)
        counts: dict[str, int] = {}
        limited: list[Finding] = []
        for finding in findings:
            counts[finding.category] = counts.get(finding.category, 0)
            if counts[finding.category] < self.max_findings_per_category:
                limited.append(finding)
                counts[finding.category] += 1
        if any(f.severity == "BLOCKER" for f in limited):
            risk, verdict = "CRITICAL", "REQUEST_CHANGES"
        elif any(f.severity == "HIGH" for f in limited):
            risk, verdict = "HIGH", "REQUEST_CHANGES"
        elif any(f.severity == "MEDIUM" for f in limited):
            risk, verdict = "MEDIUM", "APPROVE_WITH_COMMENTS"
        elif limited:
            risk, verdict = "LOW", "APPROVE_WITH_COMMENTS"
        else:
            risk, verdict = "LOW", "APPROVE"
        return ReviewResult(
            findings=limited, risk_level=risk, verdict=verdict,
            limitations=["LLM, RAG, SAST externo e testes do PR não foram executados neste MVP."],
            agents_executed=[agent.name for agent in agents],
            tools_executed=["github.get_pull_request", "diff_parser"],
        )


def render_comment(result: ReviewResult) -> str:
    lines = ["## AI Code Review", "", f"**Veredito:** `{result.verdict}`  ", f"**Risco:** `{result.risk_level}`", ""]
    if not result.findings:
        lines.append("Não foram identificados problemas relevantes no escopo analisado.")
    else:
        lines.append("### Achados")
        for f in result.findings:
            lines.extend([
                f"- **[{f.severity}] {f.title}** — `{f.file}:{f.start_line}`",
                f"  - Problema: {f.problem}",
                f"  - Impacto: {f.impact}",
                f"  - Evidência: `{f.evidence}`",
                f"  - Recomendação: {f.recommendation}",
                f"  - Confiança: `{f.confidence:.2f}`",
            ])
    lines.extend(["", "### Limitações", *[f"- {item}" for item in result.limitations]])
    return "\n".join(lines)


def render_summary(result: ReviewResult) -> str:
    """Review body for inline mode: verdict and counts; details live in the inline comments."""
    lines = ["## AI Code Review", "", f"**Veredito:** `{result.verdict}`  ", f"**Risco:** `{result.risk_level}`", ""]
    if not result.findings:
        lines.append("Não foram identificados problemas relevantes no escopo analisado.")
    else:
        counts: dict[str, int] = {}
        for f in result.findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        summary = ", ".join(f"{n} {sev}" for sev, n in counts.items())
        lines.append(f"{len(result.findings)} achado(s) comentados nas linhas do diff: {summary}.")
    lines.extend(["", "### Limitações", *[f"- {item}" for item in result.limitations]])
    return "\n".join(lines)


def render_inline_comments(result: ReviewResult) -> list[dict]:
    """One GitHub review comment per finding, anchored to the added line (RIGHT side of the diff)."""
    return [
        {
            "path": f.file,
            "line": f.start_line,
            "side": "RIGHT",
            "body": "\n".join([
                f"**[{f.severity}] {f.title}** (confiança `{f.confidence:.2f}`)",
                "",
                f"- Problema: {f.problem}",
                f"- Impacto: {f.impact}",
                f"- Recomendação: {f.recommendation}",
            ]),
        }
        for f in result.findings
    ]
