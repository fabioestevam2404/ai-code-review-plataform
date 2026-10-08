import hashlib
import re
from typing import Iterable

from .diff import ChangedLine
from .models import SEVERITIES, Finding


def _id(agent: str, file: str, line: int, title: str) -> str:
    """Stable id: the same finding gets the same id across reruns of the same diff."""
    digest = hashlib.sha256(f"{agent}|{file}|{line}|{title}".encode()).hexdigest()
    return f"{agent}-{digest[:12]}"


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
            # (?<![\w.]) skips method calls such as model.eval().
            (re.compile(r"(?<![\w.])(?:eval|exec)\s*\("),
             "Dynamic code execution with eval/exec", "HIGH", 0.85,
             "Untrusted input reaching eval/exec allows arbitrary code execution.",
             "Remove eval/exec; use ast.literal_eval for literals or an explicit dispatch table."),
            (re.compile(r"\b(?:pickle|cPickle|dill)\.loads?\("),
             "Unsafe deserialization with pickle", "HIGH", 0.85,
             "Unpickling attacker-controlled data executes arbitrary code.",
             "Use a data-only format such as JSON, or sign and verify payloads before loading."),
            (re.compile(r"\byaml\.unsafe_load\(|\byaml\.load\((?![^\n]*Loader\s*=\s*(?:yaml\.)?C?SafeLoader)"),
             "Unsafe YAML loading", "HIGH", 0.85,
             "yaml.load without SafeLoader can construct arbitrary Python objects.",
             "Use yaml.safe_load or Loader=yaml.SafeLoader."),
            (re.compile(r"\bverify\s*=\s*False\b"),
             "TLS certificate verification disabled", "HIGH", 0.93,
             "Disabling certificate verification allows man-in-the-middle attacks.",
             "Keep verification enabled; configure a custom CA bundle if needed."),
            (re.compile(r"\bhashlib\.(?:md5|sha1)\((?![^\n]*usedforsecurity\s*=\s*False)"),
             "Weak hash algorithm", "MEDIUM", 0.60,
             "MD5/SHA-1 are broken for security purposes such as signatures or password storage.",
             "Use SHA-256+ (or bcrypt/argon2 for passwords); pass usedforsecurity=False for non-security checksums."),
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


_LOOP_HEADER = re.compile(r"^(\s*)(?:async\s+)?(?:for|while)\b[^\n]*:(.*)$")
_QUERY_CALL = re.compile(
    r"\.(?:execute|executemany|query|raw|filter|find|find_one)\(|\.objects\.get\(|\bsession\.get\("
)


def _indent(text: str) -> int:
    return len(text) - len(text.lstrip())


class PerformanceAgent(Agent):
    name = "performance"

    def _query_in_loop(self, lines: list[ChangedLine]) -> list[ChangedLine]:
        """Added lines that call a query API inside a loop body, including one-line `for x in y: q(x)` loops.

        Only contiguous added lines are tracked: a gap means unseen context, so loop state is reset.
        """
        hits: list[ChangedLine] = []
        loops: list[int] = []  # indentation of enclosing loop headers
        previous: ChangedLine | None = None
        for line in lines:
            if previous is None or line.file != previous.file or line.line != previous.line + 1:
                loops = []
            previous = line
            if not line.text.strip():
                continue
            indent = _indent(line.text)
            while loops and indent <= loops[-1]:
                loops.pop()
            header = _LOOP_HEADER.match(line.text)
            if header:
                inline_body = header.group(2).split("#", 1)[0].strip()
                if inline_body:
                    if _QUERY_CALL.search(inline_body):
                        hits.append(line)
                else:
                    loops.append(indent)
            elif loops and _QUERY_CALL.search(line.text):
                hits.append(line)
        return hits

    def analyze(self, lines: list[ChangedLine]) -> list[Finding]:
        findings: list[Finding] = []
        for line in self._query_in_loop(lines):
            findings.append(Finding(
                _id(self.name, line.file, line.line, "Possible query inside loop"), self.name, "MEDIUM", 0.75,
                "Possible query inside loop", line.file, line.line, line.line,
                "A query call is executed once per loop iteration.",
                "This can create N+1 queries and increase latency with input size.", line.text.strip(),
                "Batch the operation or fetch the required data in one query; confirm with profiling.", agent=self.name,
            ))
        for line in lines:
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
    # Most severe first (SEVERITIES is ordered BLOCKER..INFO), so per-category caps keep the worst findings.
    return sorted(unique.values(), key=lambda f: (SEVERITIES.index(f.severity), f.file, f.start_line, f.title))
