"""R10 memory citations (plan §0.16): lessons and documents are hints, never evidence.

- Every lesson id (lesson-<design>-NNN) the report names must exist in the lessons store and must
  have been retrieved in this run (a hint at the start, or returned by search_memory). Citing a
  voided or refuted lesson is rejected too.
- Every document citation must name a section (doc:<doc>#<section>), and that section must exist
  and have been retrieved in this run.
- A sentence that cites one must mark it as a hint or source (hint, source, 提示, 来源 ...), and must
  not use it to confirm, prove or verify anything unless the same sentence names a tool that has a
  result in this run: R1 still decides what the evidence shows.
- Lines under a "Lessons" heading (the report's proposed lessons for later runs) are new lessons, not
  citations: the existence checks apply, the hint marker does not.

The run's retrieved ids and the store's known ids reach the gate as a pseudo-result with tool
"memory" and no evidence level, so it is never counted as a check.
"""
from __future__ import annotations

import re

from ...core.memory import DOC_REF, LESSON_ID

TAG = "(R10)"
HINT_MARK = re.compile(r"\b(?:hints?|sources?|reference[sd]?|according\s+to|per)\b|提示|来源|参考|出处", re.I)
CONFIRM = re.compile(r"\b(?:confirm(?:s|ed)?|prove[sdn]?|verif(?:y|ies|ied)|demonstrat\w*|establish(?:es|ed)?)\b"
                     r"|证明|证实|确认|验证", re.I)
LESSONS_HEADING = re.compile(r"^\s*#{1,6}\s*(?:proposed\s+)?lessons?\b|^\s*#{1,6}\s*经验", re.I | re.M)
_SENT = re.compile(r"(?<=[.!?。！？])\s+|\n+")


def _sections(report: str) -> tuple[str, str]:
    """(the report without its Lessons section, the Lessons section); the section ends at the next heading."""
    m = LESSONS_HEADING.search(report)
    if not m:
        return report, ""
    nxt = re.search(r"^\s*#{1,6}\s", report[m.end():], re.M)
    end = m.end() + nxt.start() if nxt else len(report)
    return report[:m.start()] + report[end:], report[m.end():end]


def _tools_named(sentence: str, tools: set[str]) -> bool:
    return any(re.search(rf"(?<![\w]){re.escape(t)}(?![\w])", sentence, re.I) for t in tools)


def check_memory(report: str, results: list[dict]) -> list[str]:
    mem = next((r for r in results if r.get("tool") == "memory"), None) or {}
    retrieved = {x.lower() for x in mem.get("retrieved", [])}
    known = mem.get("known") or {}
    lessons = {k.lower(): v for k, v in (known.get("lessons") or {}).items()}
    docs = {d.lower() for d in known.get("docs", [])}
    tools = {r["tool"] for r in results if r.get("tool") and r.get("tool") != "memory"}
    body, proposed = _sections(report)
    reasons: list[str] = []

    def exists(sentence: str) -> bool:
        """Reasons for ids that do not exist or were not retrieved; True if the sentence cites any."""
        cited = False
        for m in LESSON_ID.finditer(sentence):
            cited = True
            lid = m.group(0).lower()
            if lid not in lessons:
                reasons.append(f"{TAG} cites {m.group(0)}, which is not in any lessons store: {sentence.strip()!r}")
            elif lid not in retrieved:
                reasons.append(f"{TAG} cites {m.group(0)}, which was not retrieved in this run (only the hints "
                               f"given at the start or returned by search_memory may be cited): {sentence.strip()!r}")
            elif lessons[lid] in ("void", "refuted"):
                reasons.append(f"{TAG} cites {m.group(0)}, which is {lessons[lid]}: {sentence.strip()!r}")
        for m in DOC_REF.finditer(sentence):
            cited = True
            ref = m.group(0).lower()
            if not m.group(2):
                reasons.append(f"{TAG} cites {m.group(0)} without a section; cite doc:<doc>#<section>: "
                               f"{sentence.strip()!r}")
            elif ref not in docs:
                reasons.append(f"{TAG} cites {m.group(0)}, which is not a section of any document of this "
                               f"project: {sentence.strip()!r}")
            elif ref not in retrieved:
                reasons.append(f"{TAG} cites {m.group(0)}, which was not retrieved in this run: {sentence.strip()!r}")
        return cited

    for sentence in _SENT.split(body):
        if not exists(sentence):
            continue
        if not HINT_MARK.search(LESSON_ID.sub("", DOC_REF.sub("", sentence))):
            reasons.append(f"{TAG} cites a lesson or document without marking it as a hint or source "
                           f"(e.g. '(hint: lesson-…)'): {sentence.strip()!r}")
        if CONFIRM.search(sentence) and not _tools_named(sentence, tools):
            reasons.append(f"{TAG} uses a lesson or document to confirm something; memory is a hint, not "
                           f"evidence: name the tool result of this run that shows it: {sentence.strip()!r}")
    for sentence in _SENT.split(proposed):
        exists(sentence)
    return reasons
