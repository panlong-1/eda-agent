"""What each regression case covers, read from the test's own leading comment.

Choosing cases by name alone misses tests whose name does not say everything they
exercise (a test named for a stall may also check forwarding). The header comment is the
author's statement of intent; it is not evidence that the test checks it.
"""
from __future__ import annotations

import re

from ..projects import CaseSpec, Project, case_specs

MAX_LINES = 20
MAX_CHARS = 1200

_DECORATION = re.compile(r"^[\s=\-*#/_~]*$")
# '#' directly followed by a word is a preprocessor directive (#include, #define), not a comment.
_DIRECTIVE = re.compile(r"^#[A-Za-z]")


def leading_comment(text: str) -> tuple[str, bool]:
    """The comment block at the top of a source file, markers stripped; (text, truncated)."""
    lines: list[str] = []
    in_block = False
    for raw in text.splitlines():
        line = raw.strip()
        if in_block:
            in_block = "*/" not in line
            line = line.split("*/")[0].lstrip("*").strip()
        elif not line or line.startswith("#!"):
            if lines:
                lines.append("")
            continue
        elif line.startswith("/*"):
            in_block = "*/" not in line
            line = line[2:].split("*/")[0].lstrip("*").strip()
        elif line.startswith("//"):
            line = line[2:].strip()
        elif line.startswith("#") and not _DIRECTIVE.match(line):
            line = line[1:].strip()
        else:
            break  # first line of code
        if not _DECORATION.match(line):
            lines.append(line)
    text = "\n".join(lines).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    kept = "\n".join(text.splitlines()[:MAX_LINES])[:MAX_CHARS]
    return kept, kept != text


def describe(project: Project, spec: CaseSpec) -> dict:
    out = {"name": spec.name, "source": spec.source, "description": "", "truncated": False}
    if spec.list_entry:
        out["list_entry"] = spec.list_entry
    path = project.root / spec.source if spec.source else None
    if spec.description:  # written in the project config for this purpose; preferred
        out["description"] = spec.description
    elif path and path.is_file():
        out["description"], out["truncated"] = leading_comment(path.read_text(errors="replace"))
    if not out["description"]:
        out["description"] = "(no description found; read the source)"
    return out


_BOILERPLATE = re.compile(r"licen[cs]e|copyright|public domain|SPDX", re.I)
SUMMARY_CHARS = 160


def list_cases(project: Project) -> dict:
    """Every case with a one-line summary, so that no case can be chosen or skipped by name alone."""
    cases = []
    for spec in case_specs(project):
        lines = describe(project, spec)["description"].splitlines()
        first = next((ln for ln in lines if ln.strip() and not _BOILERPLATE.search(ln)), "")
        cases.append({"name": spec.name, "summary": first[:SUMMARY_CHARS]})
    return {"cases": cases,
            "note": "Summaries are the first line of each case's description; call describe_case "
                    "for the full text before choosing."}


def describe_cases(project: Project, names: list[str]) -> dict:
    specs = {s.name: s for s in case_specs(project)}
    unknown = [n for n in names if n not in specs]
    if unknown or not names:
        return {"status": "error", "error_class": "bad_request",
                "summary": f"unknown case(s) {unknown}; call list_regress_cases first" if unknown
                else "give at least one case name"}
    return {"tool": "describe_case", "status": "ok",
            "note": "Descriptions come from the test's own header comment or the project config: "
                    "what a test is meant to cover, not evidence that it checks it.",
            "cases": [describe(project, specs[n]) for n in names]}
