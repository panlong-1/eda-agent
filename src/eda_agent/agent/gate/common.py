"""What the gate rules share: the decision record, sentence and clause splitting, negations
(a negated claim is a limit, not a claim), number matching, and which cases a run described and ran."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field


# One of these earlier in the same clause makes it a limit, not a claim ("does not show ... correct").
NEGATIONS = [
    r"not", r"no", r"nothing", r"n't", r"never", r"cannot", r"without", r"unproven", r"unverified",
    r"would", r"need(?:s|ed)?", r"requires?", r"until", r"beyond", r"only if",
    "不", "未", "没有", "无法", "需要",
]
RESIDUAL_HEADING = re.compile(r"residual[\s_-]*risks?|剩余风险|残余风险", re.I)

_NEG = re.compile(r"(?<![A-Za-z])(?:" + "|".join(NEGATIONS) + r")(?![A-Za-z])", re.I)

_CLAUSE = re.compile(r"[^.!?;,。！？；，\n]+")
_SENTENCE = re.compile(r"[^.!?。！？\n]+")

_TIMING_NEG = re.compile(r"(?<![A-Za-z])(?:fail(?:s|ed)?\s+to|unable\s+to|miss(?:es|ed)?)(?![A-Za-z])", re.I)
# Words that start a new part of a clause: a negation before one does not reach past it.
_CONJ = re.compile(r"(?<![A-Za-z])(?:and|but|so|while|whereas|although|though|yet|then)(?![A-Za-z])|而|但", re.I)
NEG_WINDOW_WORDS = 4

# Sentences and clauses for R5: a decimal point or comma between digits does not end them.
_T_SENTENCE = re.compile(r"(?:[^.!?。！？\n]|(?<=\d)\.(?=\d))+")
_T_CLAUSE = re.compile(r"(?:[^.!?;,。！？；，\n]|(?<=\d)[.,](?=\d))+")


@dataclass
class GateDecision:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    max_level: str = ""  # highest evidence level among passing results of this run

    def to_dict(self) -> dict:
        return asdict(self)


def split_residual(report: str) -> tuple[str, str]:
    """(claims part, residual-risk part); the second is empty when there is no such heading."""
    m = RESIDUAL_HEADING.search(report)
    return (report, "") if not m else (report[:m.start()], report[m.end():])


def _names(text: str, subject: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(subject)}(?![\w])", text, re.I) is not None


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


def _matches(value: float, decimals: int, known: set[float]) -> bool:
    """Equal to a known value as printed: rounding allowed after the decimal point, not before it."""
    tol = 0.5 * 10 ** -decimals + 1e-9 if decimals else 1e-9
    return any(abs(value - k) <= tol for k in known)


def _negated(clause: str, start: int) -> bool:
    """A negation in the few words just before position start, not past a conjunction."""
    before = clause[:start]
    if conj := list(_CONJ.finditer(before)):
        before = before[conj[-1].end():]
    tail = " ".join(before.split()[-NEG_WINDOW_WORDS:])
    return bool(_NEG.search(tail) or _TIMING_NEG.search(tail))


def _perf_limit(clause: str, start: int) -> bool:
    """A negation anywhere earlier in the clause, not past a conjunction: "says nothing about whether
    it is faster" states a limit (as for R2's strong words)."""
    before = clause[:start]
    if conj := list(_CONJ.finditer(before)):
        before = before[conj[-1].end():]
    return bool(_NEG.search(before) or _TIMING_NEG.search(before))
