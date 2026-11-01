"""R5 timing: a timing claim ("meets 100 MHz", "positive slack", "timing met") names a result of this
run that carries an SDC sha256 and agrees with it (met/not met, the clock, every WNS/TNS/slack
number); when the SDC hash changes during a run, timing results measured under the earlier SDC
are void."""
from __future__ import annotations

import re

from .common import _T_CLAUSE, _T_SENTENCE, NEG_WINDOW_WORDS, _matches, _negated


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

# A frequency or a clock period anywhere in the claims: "200 MHz", "0.2 GHz", "meets 5 ns"
_FREQ = re.compile(r"(\d+(?:\.\d+)?)\s*([MG])Hz", re.I)
_PERIOD_NS = re.compile(r"(\d+(?:\.\d+)?)\s*ns\b", re.I)
_TIMING_KEY = re.compile(r"\b(WNS|TNS|slack)\b", re.I)
# A number standing on its own (not part of a name like tt_025C_1v80)
_STANDALONE_NUM = re.compile(r"(?<![\w.])([+-]?\d+(?:\.\d+)?)(?![\w])")

# What names a timing result in a sentence: the tool, its STA, its level, or the SDC hash
_TIMING_CITE = re.compile(r"\b(?:synth|STA|OpenSTA|E4|flow(?:_report|_run)?)\b|\b[0-9a-f]{8,64}\b", re.I)


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
