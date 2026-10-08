from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ChangedLine:
    file: str
    line: int
    text: str


_HUNK = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def changed_lines(diff: str) -> list[ChangedLine]:
    result: list[ChangedLine] = []
    current_file: str | None = None
    new_line = 0
    for raw in diff.splitlines():
        if raw.startswith("+++ b/"):
            current_file = raw[6:]
            continue
        match = _HUNK.search(raw)
        if match:
            new_line = int(match.group(1))
            continue
        if current_file is None or raw.startswith(("---", "diff ", "index ")):
            continue
        if raw.startswith("+"):
            result.append(ChangedLine(current_file, new_line, raw[1:]))
            new_line += 1
        elif raw.startswith("-"):
            continue
        else:
            new_line += 1
    return result
