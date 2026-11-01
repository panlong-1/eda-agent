"""read_source: numbered lines of one text file of the project (an RTL file, a test program).

For localising a failure the agent needs what a person sees in a debugger's source pane: the test
program (to know what it checks and which values it expects) and the RTL around a traced line.
Only files under the project root; never under .git, never a dotfile, a binary, or a file with
something shaped like an API key. Output is capped. Plain facts, no envelope (no evidence level).
"""
from __future__ import annotations

import re
from pathlib import Path

from ..core.project import Project

MAX_LINES = 200
MAX_BYTES = 4 * 1024 * 1024
SECRET = re.compile(r"sk-[A-Za-z0-9_-]{20,}")


def run_read_source(project: Project, file: str, line: int | None = None, context: int = 40,
                    start: int | None = None, end: int | None = None) -> dict:
    out = {"tool": "read_source", "file": file}
    p = Path(file).expanduser()
    if not p.is_absolute():
        p = project.root / p
    try:
        real = p.resolve(strict=True)
    except (OSError, RuntimeError):
        return out | {"error": "file not found"}
    root = project.root.resolve()
    if not real.is_relative_to(root) or not real.is_file():
        return out | {"error": "only files under the project root can be read"}
    if ".git" in real.parts or any(x.startswith(".") for x in real.relative_to(root).parts):
        return out | {"error": "this kind of file is not shown"}
    if real.stat().st_size > MAX_BYTES:
        return out | {"error": "file too large"}
    data = real.read_bytes()
    if b"\0" in data[:8192]:
        return out | {"error": "binary file"}
    text = data.decode("utf-8", "replace")
    if SECRET.search(text):
        return out | {"error": "the file contains something that looks like an API key; not shown"}
    lines = text.splitlines()
    total = len(lines)
    if start is None:
        if line:
            c = max(0, min(int(context), MAX_LINES // 2))
            start, end = int(line) - c, int(line) + c
        else:
            start, end = 1, MAX_LINES
    start = max(1, int(start))
    end = min(total, int(end) if end is not None else start + MAX_LINES - 1, start + MAX_LINES - 1)
    return out | {"path": str(real.relative_to(root)), "total_lines": total, "first_line": start, "last_line": end,
                  "lines": [f"{n:5d}  {lines[n - 1]}" for n in range(start, end + 1)],
                  "truncated": end < total or start > 1}
