"""Evidence gate: plain checks of the final report against this run's tool results.

No model is involved. Each rule is a short function returning reasons for rejection,
so a rejection can always be explained by pointing at a line of the report. The rules
are deliberately conservative: a false rejection costs one rewrite, a false pass
lets an overclaim through.

  R1 citations  every (status, evidence level) pair and "N passed/failed" count the
                report cites must come from a tool result of this run; a level named as
                a requirement ("would need E3") is not a citation
  R2 strength   functional-correctness words need E3+ from a passing result; with E2
                the report may only say which cases passed
  R3 risk       the report has a residual-risk section with some content
  R4 skipped    a case the agent described but did not run is named in that section
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from ..core.envelope import EVIDENCE_LEVELS

# Level a passing result must reach before the report may use strong claim words.
STRONG_CLAIM_MIN_LEVEL = "E3"

STRONG_CLAIM_WORDS = [
    r"handles?", r"handled", r"correct(?:ly|ness)?", r"verified", r"verifies", r"proven",
    r"works?", r"working", r"functional(?:ly)? correct", r"bug[- ]free", r"no bugs?", r"guarantee[sd]?",
    r"robust", r"fully (?:tested|functional)",
    "正确", "已验证", "验证通过", "没有\\s*bug", "无\\s*bug", "正常工作", "保证", "可靠",
]
# One of these earlier in the same clause makes it a limit, not a claim ("does not show ... correct").
NEGATIONS = [
    r"not", r"no", r"n't", r"never", r"cannot", r"without", r"unproven", r"unverified",
    r"would", r"need(?:s|ed)?", r"requires?", r"until", r"beyond", r"only if",
    "不", "未", "没有", "无法", "需要",
]
RESIDUAL_HEADING = re.compile(r"residual[\s_-]*risks?|剩余风险|残余风险", re.I)
MIN_RESIDUAL_CHARS = 20

_STRONG = re.compile(r"(?<![A-Za-z])(?:" + "|".join(STRONG_CLAIM_WORDS) + r")(?![A-Za-z])", re.I)
_NEG = re.compile(r"(?<![A-Za-z])(?:" + "|".join(NEGATIONS) + r")(?![A-Za-z])", re.I)
_LEVEL = re.compile(r"\b(E[0-5])\b")
_STATUS = re.compile(r"\b(pass|fail|error|timeout)(?:ed|es|ing)?\b", re.I)
_COUNT = re.compile(r"\b(\d+)\s*(?:/\s*\d+\s*)?(?:cases?\s+|tests?\s+)?(passed|failed)\b", re.I)
_CLAUSE = re.compile(r"[^.!?;,。！？；，\n]+")


@dataclass
class GateDecision:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    max_level: str = ""  # highest evidence level among passing results of this run

    def to_dict(self) -> dict:
        return asdict(self)


def _rank(level: str) -> int:
    return EVIDENCE_LEVELS.index(level) if level in EVIDENCE_LEVELS else -1


def split_residual(report: str) -> tuple[str, str]:
    """(claims part, residual-risk part); the second is empty when there is no such heading."""
    m = RESIDUAL_HEADING.search(report)
    return (report, "") if not m else (report[:m.start()], report[m.end():])


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
    for r in results:
        m = r.get("metrics") or {}
        for k in counts:
            if isinstance(m.get(k), int):
                counts[k].append(m[k])
    allowed = {k: {*v, sum(v)} for k, v in counts.items()}  # several regress calls may be summed
    for m in _COUNT.finditer(claims):
        n, kind = int(m.group(1)), m.group(2).lower()
        if n not in allowed[kind]:
            reasons.append(f"says '{m.group(0)}' but no tool result of this run reports {n} {kind}")
    return reasons


def check_strength(claims: str, max_level: str) -> list[str]:
    if _rank(max_level) >= _rank(STRONG_CLAIM_MIN_LEVEL):
        return []
    reasons = []
    for clause in _CLAUSE.findall(claims):
        m = _STRONG.search(clause)
        if m and not _NEG.search(clause[:m.start()]):
            reasons.append(
                f"'{m.group(0)}' claims functional correctness, which needs {STRONG_CLAIM_MIN_LEVEL}+; "
                f"the best passing evidence here is {max_level or 'none'}, which supports only "
                f"'these checks/cases passed': {clause.strip()!r}")
    return reasons


def check_residual(residual: str) -> list[str]:
    if len(residual.strip(" \t\n:#*-")) < MIN_RESIDUAL_CHARS:
        return ["no residual-risk section: add a section headed 'Residual risk' that says what "
                "the checks did not cover"]
    return []


def described_cases(results: list[dict]) -> set[str]:
    """Cases a describe_case call of this run returned."""
    return {c["name"] for r in results if r.get("tool") == "describe_case"
            for c in r.get("cases", []) if isinstance(c, dict) and "name" in c}


def executed_cases(results: list[dict]) -> set[str]:
    """Cases a regress result of this run reported as passed or failed (skipped ones did not run)."""
    ran: set[str] = set()
    for r in results:
        if r.get("tool") == "regress" and r.get("status") in ("pass", "fail"):
            m = r.get("metrics") or {}
            ran |= set(m.get("passed_cases", [])) | set(m.get("failed_cases", []))
    return ran


def check_skipped(residual: str, results: list[dict]) -> list[str]:
    return [f"described case '{name}' but neither ran it nor named it under residual risk: run it, "
            "or say there why it was left out"
            for name in sorted(described_cases(results) - executed_cases(results))
            if not re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", residual)]


def check_report(report: str, results: list[dict]) -> GateDecision:
    """results: the parsed tool results of this run (envelopes, describe_case output, ...)."""
    envelopes = [r for r in results if r.get("evidence_level") in EVIDENCE_LEVELS]
    passing = [r["evidence_level"] for r in envelopes if r.get("status") == "pass"]
    max_level = max(passing, key=_rank, default="")
    claims, residual = split_residual(report)
    reasons = (check_citations(claims, envelopes) + check_strength(claims, max_level)
               + check_residual(residual))
    if not reasons:  # R4 needs the residual-risk section, so it is checked once R1-R3 pass
        reasons = check_skipped(residual, results)
    return GateDecision(passed=not reasons, reasons=reasons, max_level=max_level)


def rejection_message(decision: GateDecision, attempt: int, limit: int) -> str:
    lines = [f"Evidence gate rejected the report (rewrite {attempt} of {limit}). Fix these and "
             "send the full report again; you may run more checks if you need evidence:"]
    lines += [f"- {r}" for r in decision.reasons]
    return "\n".join(lines)
