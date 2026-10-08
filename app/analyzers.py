from collections import defaultdict
import re
from typing import Iterable
from uuid import uuid4

from .diff import ChangedLine
from .models import Finding


def _id(agent: str, file: str, line: int, title: str) -> str:
    return f"{agent}-{uuid4().hex[:12]}"


class Agent:
    name = "base"

    def analyze(self, lines: list[ChangedLine]) -> list[Finding]:
        raise NotImplementedError


class SecurityAgent(Agent):
    name = "security"

    def analyze(self, lines: list[ChangedLine]) -> list[Finding]:
        findings: list[Finding] = []
        rules = [
            (re.compile(r"\bsubprocess\.(run|Popen|call)\([^\n]*shell\s*=\s*True"),
             "Command execution with shell=True", "HIGH", 0.96,
             "Input may reach a shell command.", "Use argv execution with shell=False and strict allowlisting."),
            (re.compile(r"(?:password|api[_-]?key|secret|token)\s*=\s*['\"][^'\"]+['\"]", re.I),
             "Possible hard-coded secret", "HIGH", 0.90,
             "A credential-like value is assigned directly in changed code.", "Move the secret to a managed secret store and rotate exposed credentials."),
            (re.compile(r"(?:execute|executemany)\([^\n]*(?:f['\"]|%\s|\.format\()", re.I),
             "Possible SQL string interpolation", "HIGH", 0.88,
             "SQL execution appears to combine code and values dynamically.", "Use parameterized queries and validate the data flow."),
        ]
        for line in lines:
            for pattern, title, severity, confidence, impact, recommendation in rules:
                if pattern.search(line.text):
                    findings.append(Finding(
                        _id(self.name, line.file, line.line, title), self.name, severity, confidence,
                        title, line.file, line.line, line.line,
                        "A security-sensitive pattern was introduced in the diff.", impact,
                        line.text.strip(), recommendation, agent=self.name,
                    ))
        return findings


class PerformanceAgent(Agent):
    name = "performance"

    def analyze(self, lines: list[ChangedLine]) -> list[Finding]:
        findings: list[Finding] = []
        for line in lines:
            if re.search(r"for\s+\w+\s+in\s+\w+.*:\s*$", line.text) and "query" in line.text.lower():
                findings.append(Finding(
                    _id(self.name, line.file, line.line, "Possible query inside loop"), self.name, "MEDIUM", 0.72,
                    "Possible query inside loop", line.file, line.line, line.line,
                    "A loop line appears related to query execution.",
                    "This can create N+1 queries and increase latency with input size.", line.text.strip(),
                    "Batch the operation or fetch the required data in one query; confirm with profiling.", agent=self.name,
                ))
            if re.search(r"requests\.(get|post|put|delete)\(", line.text) and "timeout=" not in line.text:
                findings.append(Finding(
                    _id(self.name, line.file, line.line, "Network call without timeout"), self.name, "MEDIUM", 0.94,
                    "Network call without timeout", line.file, line.line, line.line,
                    "The HTTP call has no explicit timeout.",
                    "A stalled upstream can exhaust worker capacity.", line.text.strip(),
                    "Set a finite connect/read timeout and define retry/backoff policy.", agent=self.name,
                ))
        return findings


class QualityAgent(Agent):
    name = "quality"

    def analyze(self, lines: list[ChangedLine]) -> list[Finding]:
        findings: list[Finding] = []
        for line in lines:
            if "TODO" in line.text or "FIXME" in line.text:
                findings.append(Finding(
                    _id(self.name, line.file, line.line, "Unresolved marker"), self.name, "LOW", 0.99,
                    "Unresolved marker in changed code", line.file, line.line, line.line,
                    "The diff introduces a TODO/FIXME marker.",
                    "The intended behavior may remain incomplete.", line.text.strip(),
                    "Resolve the marker or create a tracked issue with acceptance criteria.", agent=self.name,
                ))
        return findings


def deduplicate(findings: Iterable[Finding]) -> list[Finding]:
    unique: dict[tuple[str, int, str], Finding] = {}
    for finding in findings:
        key = (finding.file, finding.start_line, finding.title.lower())
        current = unique.get(key)
        if current is None or finding.confidence > current.confidence:
            unique[key] = finding
    return sorted(unique.values(), key=lambda f: (f.file, f.start_line, f.severity, f.title))
