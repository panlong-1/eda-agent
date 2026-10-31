"""Evidence gate: plain checks of the final report against this run's tool results.

No model is involved. Each rule is a short function returning reasons for rejection,
so a rejection can always be explained by pointing at a line of the report. The rules
are deliberately conservative: a false rejection costs one rewrite, a false pass
lets an overclaim through.

  R1 citations  every (status, evidence level) pair and "N passed/failed" count the
                report cites must come from a tool result of this run; a level named as
                a requirement ("would need E3") is not a citation
  R2 strength   functional-correctness words need a passing result at a functional level
                (E3 or E5; E4 synthesis says nothing about function) that the same sentence
                names (its tool or one of its cases); with E2 the report may only say which
                cases passed. "Does not prove: A, B, or C" lists limits, not claims
  R3 risk       the report has a residual-risk section with some content
  R4 skipped    a case the agent described but did not run is named in that section
  R5 timing     a timing claim ("meets 100 MHz", "positive slack", "timing met") names a result
                of this run that carries an SDC sha256 and agrees with it (met/not met, the
                clock, every WNS/TNS/slack number); when the SDC hash changes during a run,
                timing results measured under the earlier SDC are void
  R7 perf       a performance claim (faster/slower, speed-up, "N cycles", IPC/CPI, throughput, "no
                performance regression") names a perf (or mutate) result of this run in the same
                sentence, and its numbers are ones those results report; "no regression / not
                slower" needs a perf result compared with the approved baseline and slower nowhere
  R8 signoff    "DRC/LVS/antenna clean", "post-route timing met", "signoff passed", "tapeout-ready"
                name a flow result of this run that shows exactly that; see gate_signoff.py
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from ..core.envelope import EVIDENCE_LEVELS

# Level a passing result must reach before the report may use strong claim words.
STRONG_CLAIM_MIN_LEVEL = "E3"
# Levels that speak about function. The ladder is not one line: E4 (synthesis, PPA) is above
# E3 but says nothing about whether the design computes the right thing.
FUNCTIONAL_LEVELS = ("E3", "E5")

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
# A heading-style limit: "Does not prove: A, B, or gate-level functional correctness." Everything
# after the colon, to the end of the sentence, is a list of what was not shown.
_LIMIT_LIST = re.compile(
    r"(?<![A-Za-z])(?:not|never|cannot|no)\s+(?:\w+\s+){0,2}?(?:prove|show|establish|cover|demonstrate|"
    r"verify|guarantee|imply|tell|say)\w*\s*(?:about\s*)?[:：]|(?:不能|无法|未能?)(?:证明|说明|覆盖|保证)\s*[:：]",
    re.I)


# Positive timing claims. A negation in the few words just before one (within its part of
# the clause) makes it a limit or an admitted failure ("does not meet 100 MHz", "fails to meet",
# "nothing is claimed about meeting 100 MHz"); "no setup violations" is a claim, its "no" is
# part of the pattern.
TIMING_CLAIMS = [
    r"meets?\s+(?:the\s+|its\s+)?(?:\d+(?:\.\d+)?\s*[MG]Hz|timing|(?:target\s+)?clock|\d+(?:\.\d+)?\s*ns)",
    r"meeting\s+(?:timing|\d+(?:\.\d+)?\s*[MG]Hz)", r"met\s+(?:timing|\d+(?:\.\d+)?\s*[MG]Hz)",
    r"timing\s+(?:is\s+|was\s+|has\s+been\s+)?(?:met|closed|clean|satisfied)",
    r"clos(?:es|ed|ing)\s+timing", r"timing\s+closes",
    r"(?:positive|non-?negative)\s+(?:setup\s+|hold\s+|worst\s+)?slack", r"slack\s+(?:is\s+|stays\s+|remains\s+)?positive",
    r"no\s+(?:setup|hold|timing)\s+violations?", r"timing\s+closure", r"runs?\s+at\s+\d+(?:\.\d+)?\s*[MG]Hz",
    "满足时序", "时序(?:满足|达标|收敛|通过)", r"满足\s*\d+(?:\.\d+)?\s*[MG]Hz", r"正\s*slack", r"slack\s*为正",
]
_TIMING = re.compile("|".join(f"(?:{p})" for p in TIMING_CLAIMS), re.I)
_TIMING_NEG = re.compile(r"(?<![A-Za-z])(?:fail(?:s|ed)?\s+to|unable\s+to|miss(?:es|ed)?)(?![A-Za-z])", re.I)
# Words that start a new part of a clause: a negation before one does not reach past it.
_CONJ = re.compile(r"(?<![A-Za-z])(?:and|but|so|while|whereas|although|though|yet|then)(?![A-Za-z])|而|但", re.I)
NEG_WINDOW_WORDS = 4
# A frequency or a clock period anywhere in the claims: "200 MHz", "0.2 GHz", "meets 5 ns"
_FREQ = re.compile(r"(\d+(?:\.\d+)?)\s*([MG])Hz", re.I)
_PERIOD_NS = re.compile(r"(\d+(?:\.\d+)?)\s*ns\b", re.I)
_TIMING_KEY = re.compile(r"\b(WNS|TNS|slack)\b", re.I)
# A number standing on its own (not part of a name like tt_025C_1v80)
_STANDALONE_NUM = re.compile(r"(?<![\w.])([+-]?\d+(?:\.\d+)?)(?![\w])")
# Sentences and clauses for R5: a decimal point or comma between digits does not end them.
_T_SENTENCE = re.compile(r"(?:[^.!?。！？\n]|(?<=\d)\.(?=\d))+")
_T_CLAUSE = re.compile(r"(?:[^.!?;,。！？；，\n]|(?<=\d)[.,](?=\d))+")
# What names a timing result in a sentence: the tool, its STA, its level, or the SDC hash
_TIMING_CITE = re.compile(r"\b(?:synth|STA|OpenSTA|E4|flow(?:_report|_run)?)\b|\b[0-9a-f]{8,64}\b", re.I)


# Performance claims (R7). "no ... regression" style claims say nothing got slower; they need a
# baseline comparison. A negation just before any other claim makes it a limit ("does not show
# it is faster"), as for R5.
PERF_SAME_CLAIMS = [
    r"no\s+(?:performance|perf|speed|cycle(?:[- ]count)?)\s+regressions?", r"not\s+slower",
    r"no\s+slower", r"(?:same|unchanged|identical)\s+(?:performance|speed|cycle\s+counts?)",
    r"performance\s+(?:is\s+|was\s+|stays\s+)?(?:unchanged|the\s+same|preserved)",
    "性能(?:没有|未)(?:退化|下降|回退)", "性能不变", "没有性能(?:退化|下降|回退)",
]
PERF_CLAIMS = [
    r"faster", r"slower", r"speed-?ups?", r"slow-?downs?", r"(?:performance|perf)\s+regressions?",
    r"(?:more|fewer|less|extra)\s+(?:clock\s+)?cycles", r"\d[\d,]*(?:\.\d+)?\s*(?:clock\s+)?cycles?",
    r"\bIPC\b", r"\bCPI\b", r"throughput", r"stall\s+cycles",
    "更快", "更慢", "变慢", "变快", "加速", "性能(?:退化|下降|回退|提升|提高)", r"\d+\s*个?(?:时钟)?周期", "周期数", "吞吐",
]
_PERF_SAME = re.compile("|".join(f"(?:{p})" for p in PERF_SAME_CLAIMS), re.I)
_PERF = re.compile("|".join(f"(?:{p})" for p in PERF_CLAIMS), re.I)
_PERF_CITE = re.compile(r"(?<![\w-])(?:perf|mutate)(?![\w-])", re.I)
# Numbers a perf claim gives: "1234 cycles", "+12 cycles", "IPC 0.81", "CPI: 4.82", "3.5% slower"
_PERF_NUMS = [
    re.compile(r"([+-]?\d[\d,]*(?:\.\d+)?)\s*(?:clock\s+)?(?:cycles?|个?(?:时钟)?周期)", re.I),
    re.compile(r"\b(?:IPC|CPI)\b\s*(?:of|is|was|=|:|：)?\s*([+-]?\d+(?:\.\d+)?)", re.I),
    re.compile(r"([+-]?\d+(?:\.\d+)?)\s*%\s*(?:faster|slower|more|fewer|less|speed-?up|slow-?down|更快|更慢)", re.I),
]


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


def _names(text: str, subject: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(subject)}(?![\w])", text, re.I) is not None


def check_strength(claims: str, envelopes: list[dict]) -> list[str]:
    """A strong word needs a passing result at a functional level (E3, E5) that its sentence
    names, by tool or case, so evidence on one check cannot vouch for a claim about something
    else, and synthesis (E4) never vouches for function."""
    strong = [r for r in envelopes if r.get("status") == "pass"
              and r.get("evidence_level") in FUNCTIONAL_LEVELS]
    subjects = sorted({s for r in strong
                       for s in [r.get("tool", ""), *(r.get("metrics") or {}).get("passed_cases", [])] if s})
    best = max((r["evidence_level"] for r in envelopes if r.get("status") == "pass"), key=_rank, default="")
    reasons = []
    for sentence in _SENTENCE.findall(claims):
        if any(_names(sentence, s) for s in subjects):
            continue
        if m := _LIMIT_LIST.search(sentence):
            sentence = sentence[:m.start()]  # what follows "does not prove:" is a list of limits
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
                    f"check that reached {'/'.join(FUNCTIONAL_LEVELS)} ({', '.join(subjects)}); evidence on "
                    f"one check does not support a claim about another: {clause.strip()!r}")
            else:
                reasons.append(
                    f"'{m.group(0)}' claims functional correctness, which needs {STRONG_CLAIM_MIN_LEVEL} "
                    f"(bit-exact comparison or mutants) or E5; the best passing evidence here is "
                    f"{best or 'none'}" + (" (synthesis: it says nothing about function)" if best == "E4" else "")
                    + f", which supports only 'these checks/cases passed': {clause.strip()!r}")
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


def _timing_numbers(r: dict, key: str, sentence: str) -> set[float]:
    """Numbers a sentence may give as `key` (WNS / TNS / slack) from result r: the run-level
    worst values, plus a corner's own values only when the sentence names that corner."""
    m = r.get("metrics") or {}
    key = key.lower()
    path_slack = (m.get("worst_path") or {}).get("slack")
    nums = {"wns": [m.get("wns"), m.get("hold_wns"), path_slack],
            "tns": [m.get("tns"), m.get("hold_tns")],
            "slack": [m.get("wns"), m.get("hold_wns"), path_slack]}[key]
    for name, c in (m.get("corners") or {}).items():
        if re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", sentence, re.I):
            nums += ([c.get("setup_tns"), c.get("hold_tns")] if key == "tns"
                     else [c.get("setup_wns"), c.get("hold_wns")])
    return {float(x) for x in nums if isinstance(x, (int, float))}


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


def _periods_ns(text: str) -> list[float]:
    """Clock periods a text states, as frequencies (MHz/GHz) converted to ns."""
    return [1000.0 / (float(v) * (1000 if unit.upper() == "G" else 1)) for v, unit in _FREQ.findall(text)
            if float(v) > 0]


def _met(r: dict, kind: str) -> bool:
    m = r["metrics"]
    if kind == "setup":
        return isinstance(m.get("wns"), (int, float)) and m["wns"] >= 0
    if kind == "hold":
        return isinstance(m.get("hold_wns"), (int, float)) and m["hold_wns"] >= 0
    return bool(m.get("timing_met"))


def check_timing(claims: str, results: list[dict]) -> list[str]:
    valid, void = timing_results(results)
    void_note = (f" ({len(void)} earlier timing result(s) are void: the SDC changed during this run)"
                 if void else "")
    periods = sorted({r["metrics"].get("clock_period") for r in valid if r["metrics"].get("clock_period")})
    reasons = []
    for sentence in _T_SENTENCE.findall(claims):
        text = sentence.strip()
        claimed: list[tuple[str, str, list[float]]] = []  # (words, setup|hold|all, stated periods)
        free_periods: list[float] = []                     # frequencies outside negated claims
        for clause in _T_CLAUSE.findall(sentence):
            negated_here = False
            for m in _TIMING.finditer(clause):
                if _negated(clause, m.start()):
                    negated_here = True
                    continue
                window = (clause[:m.start()].split()[-NEG_WINDOW_WORDS:] + [m.group(0)])
                words = " ".join(window).lower()
                kind = "setup" if "setup" in words else "hold" if "hold" in words else "all"
                stated = _periods_ns(clause) + [float(v) for v in _PERIOD_NS.findall(m.group(0))]
                claimed.append((m.group(0), kind, stated))
            if not negated_here:
                free_periods += _periods_ns(clause)
        numbers = []
        for k in _TIMING_KEY.finditer(sentence):
            rest = sentence[k.end():k.end() + 40]
            if nxt := _TIMING_KEY.search(rest):
                rest = rest[:nxt.start()]
            n = next((x for x in _STANDALONE_NUM.finditer(rest)
                      if not re.match(r"\s*(?:[kMG]Hz|%)", rest[x.end():], re.I)), None)
            if n:
                numbers.append((k.group(1), n.group(1)))
        if not claimed and not numbers and not (valid and free_periods):
            continue
        if not valid:
            if claimed or numbers:
                first = claimed[0][0] if claimed else numbers[0][0]
                reasons.append(f"timing statement '{first}' but no result of this run carries an SDC sha256 "
                               f"and a timing estimate{void_note}; run synth: {text!r}")
            continue
        if claimed and not _TIMING_CITE.search(sentence):
            reasons.append(f"timing claim '{claimed[0][0]}' must name the result it rests on (synth / STA, "
                           f"E4 or the SDC sha256) in the same sentence: {text!r}")
        for words, kind, stated in claimed:
            backing = [r for r in valid if _met(r, kind)]
            if stated:
                backing = [r for r in backing
                           if any(abs(r["metrics"].get("clock_period", 0) - p) < 0.01 for p in stated)]
            if not backing:
                last = valid[-1]["metrics"]
                at = f" at {', '.join(f'{p:g} ns' for p in stated)}" if stated else ""
                reasons.append(f"timing claim '{words}', but no valid synth result of this run shows "
                               f"{'setup' if kind == 'setup' else 'hold' if kind == 'hold' else 'timing'} met{at} "
                               f"(latest: setup WNS {last.get('wns')}, hold WNS {last.get('hold_wns')} at "
                               f"{last.get('clock_period')} ns): {text!r}")
        for p in free_periods:
            if not any(abs(p - q) < 0.01 for q in periods):
                reasons.append(f"states a clock of {1000 / p:g} MHz ({p:g} ns) but the timing results of this "
                               f"run used {', '.join(f'{q:g} ns' for q in periods)}; only those can be "
                               f"cited: {text!r}")
        for name, num in numbers:
            decimals = len(num.split(".")[1]) if "." in num else 0
            known = set().union(*(_timing_numbers(r, name, sentence) for r in valid))
            if _matches(float(num), decimals, known):
                continue
            stale = set().union(*(_timing_numbers(r, name, sentence) for r in void)) if void else set()
            why = "it comes from a timing result made void by an SDC change" if _matches(
                float(num), decimals, stale) else "no valid timing result of this run reports it as that"
            reasons.append(f"cites {name} {num} but {why}: {text!r}")
    return reasons


def _collect_numbers(obj, out: set[float]) -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out |= {float(obj), abs(float(obj))}  # "+12 cycles" and "12 more cycles" both name a delta of +12
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_numbers(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_numbers(v, out)


def perf_numbers(results: list[dict]) -> set[float]:
    """Every number a perf result of this run reports (per-case counters, baseline values, deltas,
    percentages), plus the cycle numbers of mutate results (perf comparisons and the reference)."""
    nums: set[float] = set()
    for r in results:
        m = r.get("metrics") or {}
        if r.get("tool") == "perf" and r.get("status") in ("pass", "fail"):
            _collect_numbers({k: m.get(k) for k in ("cases", "comparison")}, nums)
        elif r.get("tool") == "mutate":
            _collect_numbers(m.get("perf_reference"), nums)
            for x in m.get("mutant_list") or []:
                _collect_numbers(x.get("perf"), nums)
    return nums


def _perf_limit(clause: str, start: int) -> bool:
    """A negation anywhere earlier in the clause, not past a conjunction: "says nothing about whether
    it is faster" states a limit (as for R2's strong words)."""
    before = clause[:start]
    if conj := list(_CONJ.finditer(before)):
        before = before[conj[-1].end():]
    return bool(_NEG.search(before) or _TIMING_NEG.search(before))


def check_perf(claims: str, results: list[dict]) -> list[str]:
    perf = [r for r in results if r.get("tool") == "perf" and r.get("status") in ("pass", "fail")]
    sources = perf + [r for r in results if r.get("tool") == "mutate" and (r.get("metrics") or {}).get("perf_key")]
    known = perf_numbers(results)
    reasons = []
    for sentence in _T_SENTENCE.findall(claims):
        text = sentence.strip()
        same, other = [], []
        for clause in _T_CLAUSE.findall(sentence):
            for m in _PERF_SAME.finditer(clause):
                same.append(m.group(0))
            rest = _PERF_SAME.sub(" ", clause)
            other += [m.group(0) for m in _PERF.finditer(rest) if not _perf_limit(rest, m.start())]
        if not same and not other:
            continue
        first = (same or other)[0]
        if not sources:
            reasons.append(f"performance statement '{first}' but no perf result of this run backs it; run perf: {text!r}")
            continue
        if not _PERF_CITE.search(sentence):
            reasons.append(f"performance statement '{first}' must name the result it rests on (perf, or mutate "
                           f"for a mutant) in the same sentence: {text!r}")
        if same:
            compared = [r for r in perf if (r.get("metrics") or {}).get("comparison")]
            slower = sorted({c for r in compared for c in (r.get("metrics") or {}).get("slower", [])})
            if not compared:
                reasons.append(f"'{same[0]}' needs a perf result compared with the approved baseline; this run "
                               f"has none (no baseline recorded, or perf not run): {text!r}")
            elif slower:
                reasons.append(f"'{same[0]}', but perf reports {', '.join(slower)} slower than the baseline: {text!r}")
        for pat in _PERF_NUMS:
            for m in pat.finditer(sentence):
                num = m.group(1).replace(",", "")
                decimals = len(num.split(".")[1]) if "." in num else 0
                if not _matches(abs(float(num)), decimals, known):
                    reasons.append(f"cites '{m.group(0).strip()}' but no perf or mutate result of this run reports "
                                   f"{num.lstrip('+')}: {text!r}")
    return reasons


def check_report(report: str, results: list[dict]) -> GateDecision:
    """results: the parsed tool results of this run (envelopes, describe_case output, ...)."""
    envelopes = [r for r in results if r.get("evidence_level") in EVIDENCE_LEVELS]
    passing = [r["evidence_level"] for r in envelopes if r.get("status") == "pass"]
    max_level = max(passing, key=_rank, default="")
    claims, residual = split_residual(report)
    reasons = (check_citations(claims, envelopes) + check_strength(claims, envelopes)
               + check_timing(claims, envelopes) + check_perf(claims, envelopes)
               + check_signoff(claims, envelopes) + check_residual(residual))
    if not reasons:  # R4 needs the residual-risk section, so it is checked once R1-R3 pass
        reasons = check_skipped(residual, results)
    return GateDecision(passed=not reasons, reasons=reasons, max_level=max_level)


def rejection_message(decision: GateDecision, attempt: int, limit: int) -> str:
    lines = [f"Evidence gate rejected the report (rewrite {attempt} of {limit}). Fix these and "
             "send the full report again; you may run more checks if you need evidence:"]
    lines += [f"- {r}" for r in decision.reasons]
    return "\n".join(lines)


from .gate_signoff import check_signoff  # noqa: E402  (R8 lives in its own module and uses helpers above)
