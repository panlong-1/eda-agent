"""R7 perf: a performance claim (faster/slower, speed-up, "N cycles", IPC/CPI, throughput, "no
performance regression") names a perf (or mutate) result of this run in the same sentence, and its
numbers are ones those results report; "no regression / not slower" needs a perf result compared
with the approved baseline and slower nowhere."""
from __future__ import annotations

import re

from .common import _T_CLAUSE, _T_SENTENCE, _matches, _perf_limit


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

# R7: an accepted slowdown names the perf result and the approved baseline change behind it.
_ACCEPTED_SLOW = re.compile(
    r"(?:accept(?:ed|s)?|intentional(?:ly)?|deliberate(?:ly)?|approved)\s+(?:\w+\s+){0,3}?"
    r"(?:slow-?downs?|slower|performance\s+(?:regressions?|costs?|loss)|cycle\s+(?:costs?|increases?))"
    r"|(?:接受|有意|批准)[^。，,.]{0,8}(?:变慢|性能(?:下降|退化|损失))", re.I)


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
        if accepted := _ACCEPTED_SLOW.search(sentence):
            loosened = [r for r in perf if (r.get("metrics") or {}).get("baseline_loosened")]
            if not loosened:
                reasons.append(f"'{accepted.group(0)}': an accepted slowdown needs a perf result compared with a "
                               "baseline that a person loosened for it (eda-agent perf-baseline --allow-loosen "
                               f"--reason); this run has none: {text!r}")
            elif not re.search(r"baseline|基线", sentence, re.I):
                reasons.append(f"'{accepted.group(0)}' must name the approved baseline change in the same sentence "
                               f"(loosened on {loosened[-1]['metrics'].get('baseline_recorded_at')} by "
                               f"{loosened[-1]['metrics'].get('baseline_by')}): {text!r}")
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
