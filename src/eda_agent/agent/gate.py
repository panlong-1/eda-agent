"""Evidence gate: plain checks of the final report against this run's tool results.

No model is involved. Each rule is a short function returning reasons for rejection,
so a rejection can always be explained by pointing at a line of the report. The rules
are deliberately conservative: a false rejection costs one rewrite, a false pass
lets an overclaim through.

  R1 citations  every (status, evidence level) pair and "N passed/failed" count the
                report cites must come from a tool result of this run; a level named as
                a requirement ("would need E3") is not a citation
  R2 strength   functional-correctness words need E3+ from a passing result that the
                same sentence names (its tool or one of its cases); with E2 the report may
                only say which cases passed
  R3 risk       the report has a residual-risk section with some content
  R4 skipped    a case the agent described but did not run is named in that section
  R5 timing     a timing claim ("meets 100 MHz", "positive slack", "timing met") names a result
                of this run that carries an SDC sha256 and agrees with it (met/not met, the
                clock, every WNS/TNS/slack number); when the SDC hash changes during a run,
                timing results measured under the earlier SDC are void
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
    r"not", r"no", r"nothing", r"n't", r"never", r"cannot", r"without", r"unproven", r"unverified",
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
_SENTENCE = re.compile(r"[^.!?。！？\n]+")
# What may stand before the strong word in a later item of a list: "correct", "or bug-free",
# "fully correct", "或无 bug". Anything more ("but the design is correct") starts a new claim.
_LIST_ITEM = re.compile(r"\s*(?:(?:or|and|nor)\s+|或者?|和|以及|及)?\s*(?:[A-Za-z]+ly\s+)?", re.I)


# Positive timing claims. A negation earlier in the clause makes one a limit ("not timed
# against 100 MHz"); "no setup violations" is a claim, its "no" is part of the pattern.
TIMING_CLAIMS = [
    r"meets?\s+(?:the\s+|its\s+)?(?:\d+(?:\.\d+)?\s*MHz|timing|(?:target\s+)?clock|\d+(?:\.\d+)?\s*ns)",
    r"meeting\s+(?:timing|\d+(?:\.\d+)?\s*MHz)", r"met\s+(?:timing|\d+(?:\.\d+)?\s*MHz)",
    r"timing\s+(?:is\s+|was\s+|has\s+been\s+)?(?:met|closed|clean|satisfied)",
    r"(?:positive|non-?negative)\s+(?:setup\s+|hold\s+|worst\s+)?slack", r"slack\s+(?:is\s+|stays\s+|remains\s+)?positive",
    r"no\s+(?:setup|hold|timing)\s+violations?", r"timing\s+closure", r"runs?\s+at\s+\d+(?:\.\d+)?\s*MHz",
    "满足时序", "时序(?:满足|达标|收敛|通过)", r"满足\s*\d+(?:\.\d+)?\s*MHz", r"正\s*slack", r"slack\s*为正",
]
_TIMING = re.compile("|".join(f"(?:{p})" for p in TIMING_CLAIMS), re.I)
_MHZ = re.compile(r"(\d+(?:\.\d+)?)\s*MHz", re.I)
# A number given as WNS / TNS / slack: "WNS +0.698 ns", "slack of 0.70", "TNS = -3.1"
_TIMING_NUM = re.compile(r"\b(WNS|TNS|slack)\b[^\d+\-.]{0,20}([+-]?\d+(?:\.\d+)?)", re.I)
# Sentences and clauses for R5: a decimal point or comma between digits does not end them.
_T_SENTENCE = re.compile(r"(?:[^.!?。！？\n]|(?<=\d)\.(?=\d))+")
_T_CLAUSE = re.compile(r"(?:[^.!?;,。！？；，\n]|(?<=\d)[.,](?=\d))+")
# What names a timing result in a sentence: the tool, its STA, its level, or the SDC hash
_TIMING_CITE = re.compile(r"\b(?:synth|STA|OpenSTA|E4)\b|\b[0-9a-f]{8,64}\b", re.I)


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


def _names(text: str, subject: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(subject)}(?![\w])", text, re.I) is not None


def check_strength(claims: str, envelopes: list[dict]) -> list[str]:
    """A strong word needs a passing E3+ result that its sentence names, by tool or case,
    so E3 on one check cannot vouch for a claim about something else."""
    strong = [r for r in envelopes if r.get("status") == "pass"
              and _rank(r.get("evidence_level", "")) >= _rank(STRONG_CLAIM_MIN_LEVEL)]
    subjects = sorted({s for r in strong
                       for s in [r.get("tool", ""), *(r.get("metrics") or {}).get("passed_cases", [])] if s})
    best = max((r["evidence_level"] for r in envelopes if r.get("status") == "pass"), key=_rank, default="")
    reasons = []
    for sentence in _SENTENCE.findall(claims):
        if any(_names(sentence, s) for s in subjects):
            continue
        limit = False  # the previous clause stated a limit ("does not show ... handled")
        for clause in _CLAUSE.findall(sentence):
            m = _STRONG.search(clause)
            if not m:
                limit = False
                continue
            # The items of a list after a stated limit stay limits: "does not establish that
            # hazards are handled, correct, or bug-free" claims none of the three.
            if _NEG.search(clause[:m.start()]) or (limit and _LIST_ITEM.fullmatch(clause[:m.start()])):
                limit = True
                continue
            limit = False
            if subjects:
                reasons.append(
                    f"'{m.group(0)}' claims functional correctness, but its sentence does not name the "
                    f"check that reached {STRONG_CLAIM_MIN_LEVEL}+ ({', '.join(subjects)}); evidence on one "
                    f"check does not support a claim about another: {clause.strip()!r}")
            else:
                reasons.append(
                    f"'{m.group(0)}' claims functional correctness, which needs {STRONG_CLAIM_MIN_LEVEL}+; "
                    f"the best passing evidence here is {best or 'none'}, which supports only "
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


def timing_results(results: list[dict]) -> tuple[list[dict], list[dict]]:
    """(valid, void) timing results of this run, in run order. A timing result carries an SDC
    sha256 and a WNS; those measured under an SDC other than the run's latest one are void."""
    hashes = [(r.get("metrics") or {}).get("sdc_sha256") for r in results]
    latest = next((h for h in reversed(hashes) if h and h != "missing"), None)
    timed = [r for r in results if (r.get("metrics") or {}).get("sdc_sha256")
             and isinstance((r.get("metrics") or {}).get("wns"), (int, float))]
    valid = [r for r in timed if r["metrics"]["sdc_sha256"] == latest]
    return valid, [r for r in timed if r not in valid]


def _timing_numbers(r: dict) -> set[float]:
    m = r.get("metrics") or {}
    nums = [m.get("wns"), m.get("tns"), m.get("hold_wns"), (m.get("worst_path") or {}).get("slack")]
    for c in (m.get("corners") or {}).values():
        nums += [c.get(k) for k in ("setup_wns", "setup_tns", "hold_wns", "hold_tns")]
    return {float(x) for x in nums if isinstance(x, (int, float))}


def _matches(value: float, decimals: int, known: set[float]) -> bool:
    return any(abs(value - k) <= 0.5 * 10 ** -decimals + 1e-9 or abs(value - round(k, decimals)) < 1e-9
               for k in known)


def check_timing(claims: str, results: list[dict]) -> list[str]:
    valid, void = timing_results(results)
    void_note = (f" ({len(void)} earlier timing result(s) are void: the SDC changed during this run)"
                 if void else "")
    reasons = []
    for sentence in _T_SENTENCE.findall(claims):
        claimed = []
        for clause in _T_CLAUSE.findall(sentence):
            for m in _TIMING.finditer(clause):
                if not _NEG.search(clause[:m.start()]):
                    claimed.append(m.group(0))
        numbers = [(m.group(1), m.group(2)) for m in _TIMING_NUM.finditer(sentence)]
        if not claimed and not numbers:
            continue
        text = sentence.strip()
        if not valid:
            reasons.append(f"timing statement '{(claimed or [numbers[0][0]])[0]}' but no result of this run "
                           f"carries an SDC sha256 and a timing estimate{void_note}; run synth: {text!r}")
            continue
        if claimed and not _TIMING_CITE.search(sentence):
            reasons.append(f"timing claim '{claimed[0]}' must name the result it rests on (synth / STA, E4 "
                           f"or the SDC sha256) in the same sentence: {text!r}")
        if claimed and not any(r["metrics"].get("timing_met") for r in valid):
            reasons.append(f"timing claim '{claimed[0]}', but the synth result of this run says timing is "
                           f"NOT met under its SDC (WNS {valid[-1]['metrics'].get('wns')}): {text!r}")
        periods = {r["metrics"].get("clock_period") for r in valid}
        for mhz in _MHZ.findall(sentence):
            if not any(p and abs(1000.0 / p - float(mhz)) < 0.5 for p in periods):
                reasons.append(f"says {mhz} MHz but the timing results of this run used a clock period of "
                               f"{', '.join(f'{p:g} ns' for p in periods if p)}: {text!r}")
        known = set().union(*(_timing_numbers(r) for r in valid))
        stale = set().union(*(_timing_numbers(r) for r in void)) if void else set()
        for name, num in numbers:
            decimals = len(num.split(".")[1]) if "." in num else 0
            if _matches(float(num), decimals, known):
                continue
            why = "it comes from a timing result made void by an SDC change" if _matches(
                float(num), decimals, stale) else "no valid timing result of this run reports it"
            reasons.append(f"cites {name} {num} but {why}: {text!r}")
    return reasons


def check_report(report: str, results: list[dict]) -> GateDecision:
    """results: the parsed tool results of this run (envelopes, describe_case output, ...)."""
    envelopes = [r for r in results if r.get("evidence_level") in EVIDENCE_LEVELS]
    passing = [r["evidence_level"] for r in envelopes if r.get("status") == "pass"]
    max_level = max(passing, key=_rank, default="")
    claims, residual = split_residual(report)
    reasons = (check_citations(claims, envelopes) + check_strength(claims, envelopes)
               + check_timing(claims, envelopes) + check_residual(residual))
    if not reasons:  # R4 needs the residual-risk section, so it is checked once R1-R3 pass
        reasons = check_skipped(residual, results)
    return GateDecision(passed=not reasons, reasons=reasons, max_level=max_level)


def rejection_message(decision: GateDecision, attempt: int, limit: int) -> str:
    lines = [f"Evidence gate rejected the report (rewrite {attempt} of {limit}). Fix these and "
             "send the full report again; you may run more checks if you need evidence:"]
    lines += [f"- {r}" for r in decision.reasons]
    return "\n".join(lines)
