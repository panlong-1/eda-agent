"""R1 citations: every (status, evidence level) pair and "N passed/failed" count the report cites must
come from a tool result of this run; a level named as a requirement ("would need E3") is not a citation."""
from __future__ import annotations

import re

from .common import _NEG


# "E5-partial" is a flow result's signoff_level, not a citation of evidence level E5
_LEVEL = re.compile(r"\b(E[0-5])\b(?!-partial)")
_STATUS = re.compile(r"\b(pass|fail|error|timeout)(?:ed|es|ing)?\b", re.I)
_COUNT = re.compile(r"\b(\d+)\s*(?:/\s*\d+\s*)?(?:cases?\s+|tests?\s+)?(passed|failed)\b", re.I)


def check_citations(claims: str, results: list[dict]) -> list[str]:
    pairs = {(r.get("status"), r.get("evidence_level")) for r in results}
    levels = {lvl for _, lvl in pairs}
    reasons = []
    for line in claims.splitlines():
        found = list(_LEVEL.finditer(line))
        for i, m in enumerate(found):
            lvl = m.group(1)
            clause_start = max(line.rfind(c, 0, m.start()) for c in ".!?;,。！？；，") + 1
            if _NEG.search(line[clause_start:m.start()]):
                continue  # "that would require E3", "no E3 evidence": a requirement, not a citation
            if lvl not in levels:
                reasons.append(f"cites {lvl} but no tool result of this run has evidence_level {lvl}: "
                               f"{line.strip()!r}")
                continue
            # The status a level belongs to: the status words just before it ("lint: pass (E0)"),
            # else just after it ("E2 regress: fail"), never across another cited level.
            before = line[found[i - 1].end() if i else 0:m.start()]
            after = line[m.end():found[i + 1].start() if i + 1 < len(found) else len(line)]
            statuses = {s.lower() for s in _STATUS.findall(before) or _STATUS.findall(after)}
            if statuses and not any((s, lvl) in pairs for s in statuses):
                reasons.append(f"cites {'/'.join(sorted(statuses))} at {lvl} but no tool result of this "
                               f"run has that status at {lvl}: {line.strip()!r}")
    counts: dict[str, list[int]] = {"passed": [], "failed": []}
    sub_counts: dict[str, list[int]] = {"passed": [], "failed": []}
    for r in results:
        m = r.get("metrics") or {}
        for k in counts:
            if isinstance(m.get(k), int):
                counts[k].append(m[k])
            if isinstance(m.get(f"subtests_{k}"), int):  # the testbench's own counts (summary_regex)
                sub_counts[k].append(m[f"subtests_{k}"])
    # several regress calls may be summed; case counts and testbench sub-test counts are each a source
    allowed = {k: {*v, sum(v), *sub_counts[k], sum(sub_counts[k])} for k, v in counts.items()}
    for m in _COUNT.finditer(claims):
        n, kind = int(m.group(1)), m.group(2).lower()
        if n not in allowed[kind]:
            reasons.append(f"says '{m.group(0)}' but no tool result of this run reports {n} {kind}")
    return reasons
