"""R6 tests: a claim that a test catches / detects / kills a bug or mutant, or that the kill rate rose,
names a score_tests result of this run in the same sentence (mutate also counts for plain mutant
facts, never for a rise); its percentages are ones those results report, and every mutant it
names was killed there."""
from __future__ import annotations

import re

from .common import _T_CLAUSE, _T_SENTENCE, _perf_limit


# R6: claims that tests catch bugs, and kill-rate rises (only score_tests can back a rise).
TEST_CLAIMS = [
    r"(?:catch(?:es|ing)?|caught|detect(?:s|ed|ing)?|kill(?:s|ed|ing)?|expos(?:es|ed|ing))\s+(?:\w+\s+){0,3}?"
    r"(?:bugs?|mutants?|mutations?|faults?|defects?)",
    r"(?:bugs?|mutants?|mutations?|faults?)\s+(?:\d+(?:\s*(?:,|and)\s*\d+)*\s+)?(?:is|are|was|were|gets?|got)\s+"
    r"(?:now\s+|also\s+)?(?:caught|killed|detected)",
    "(?:抓到|检测到|杀死|发现)[^。，,.；;]{0,12}(?:bug|变异体|缺陷)",
]
RISE_CLAIMS = [
    r"kill\s+rate\s+(?:rises?|rose|increase[sd]?|improve[sd]?|goes\s+up|went\s+up|from\s+\d)",
    r"(?:raise[sd]?|improve[sd]?|increase[sd]?)\s+(?:the\s+)?kill\s+rate", r"kill\s+rate[^.;]{0,30}->",
    "捕获率(?:提高|上升|提升|从)",
]
_TEST_CLAIM = re.compile("|".join(f"(?:{p})" for p in TEST_CLAIMS), re.I)
_RISE = re.compile("|".join(f"(?:{p})" for p in RISE_CLAIMS), re.I)
_SCORE_CITE = re.compile(r"(?<![\w-])score_tests(?![\w-])", re.I)
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
# A mutant caught only by its cycle count is a slower-but-correct change: never a functional bug.
_FUNC_WORD = re.compile(r"(?<![\w-])(?:bugs?|functional(?:ly)?|wrong|incorrect|errors?)(?![\w-])|功能错误|错误", re.I)
_TIMING_WORD = re.compile(r"cycle|timing|perf|slower|stall|周期|性能|时序", re.I)

_MUTANT_ID = re.compile(r"(?:mutants?|#)\s*((?:\d+)(?:\s*(?:,|and|、)\s*\d+)*)", re.I)


def _killed_ids(results: list[dict]) -> tuple[set[int], set[int]]:
    """(mutants a score_tests result of this run killed, mutants a mutate result reports killed)."""
    # a kind=perf test kills by cycle count: its perf_killed mutants count as killed by it
    scored = {int(i) for r in results if r.get("tool") == "score_tests" and r.get("status") in ("pass", "fail")
              for i in [*(r.get("metrics") or {}).get("killed", []),
                        *((r.get("metrics") or {}).get("perf_killed", [])
                          if (r.get("metrics") or {}).get("kind") == "perf" else [])]}
    mutated = {int(x["id"]) for r in results if r.get("tool") == "mutate"
               for x in (r.get("metrics") or {}).get("mutant_list", []) if x.get("status") in ("killed", "perf_killed")}
    return scored, mutated


def _rates(results: list[dict]) -> set[float]:
    """Kill rates (in %) that score_tests and mutate results of this run report."""
    keys = ("kill_rate", "kill_rate_with_perf", "kill_rate_before", "kill_rate_after",
            "kill_rate_with_perf_before", "kill_rate_with_perf_after")
    return {100.0 * v for r in results if r.get("tool") in ("score_tests", "mutate")
            for k in keys if isinstance(v := (r.get("metrics") or {}).get(k), (int, float))}


def check_tests(claims: str, results: list[dict]) -> list[str]:
    scores = [r for r in results if r.get("tool") == "score_tests" and r.get("status") in ("pass", "fail")]
    mutates = [r for r in results if r.get("tool") == "mutate" and r.get("status") in ("pass", "fail")]
    by_score, by_mutate = _killed_ids(results)
    rates = _rates(results)
    perf_only = {int(i) for r in scores if (r.get("metrics") or {}).get("kind") == "perf"
                 for i in (r.get("metrics") or {}).get("perf_killed", [])} | \
                {int(x["id"]) for r in mutates for x in (r.get("metrics") or {}).get("mutant_list", [])
                 if x.get("status") == "perf_killed"}
    reasons = []
    for sentence in _T_SENTENCE.findall(claims):
        text = sentence.strip()
        rise = [m.group(0) for c in _T_CLAUSE.findall(sentence) for m in _RISE.finditer(c) if not _perf_limit(c, m.start())]
        catch = [m.group(0) for c in _T_CLAUSE.findall(sentence) for m in _TEST_CLAIM.finditer(c)
                 if not _perf_limit(c, m.start())]
        if not rise and not catch:
            continue
        first = (rise or catch)[0]
        cites_score = bool(_SCORE_CITE.search(sentence))
        cites_mutate = bool(re.search(r"(?<![\w-])mutate(?![\w-])", sentence, re.I))
        if rise and not scores:
            reasons.append(f"'{first}' claims a higher kill rate, which needs a score_tests result of this run; "
                           f"there is none: {text!r}")
            continue
        if not scores and not mutates:
            reasons.append(f"'{first}' claims that tests catch bugs, but no score_tests (or mutate) result of this "
                           f"run backs it: {text!r}")
            continue
        if rise and not cites_score:
            reasons.append(f"'{first}' must name the score_tests result it rests on in the same sentence: {text!r}")
        elif catch and not (cites_score or cites_mutate):
            reasons.append(f"'{first}' must name the score_tests (or mutate) result it rests on in the same "
                           f"sentence: {text!r}")
        killed = (by_score if cites_score else set()) | (by_mutate if cites_mutate else set())
        for m in _MUTANT_ID.finditer(sentence):
            for n in re.findall(r"\d+", m.group(1)):
                if int(n) not in killed and not _perf_limit(sentence, m.start()):
                    reasons.append(f"names mutant {n}, but no cited result of this run reports it killed: {text!r}")
                elif int(n) in perf_only and _FUNC_WORD.search(sentence) and not _TIMING_WORD.search(sentence):
                    reasons.append(f"mutant {n} was caught only by its cycle count (slower, results right): do not "
                                   f"call it a functional bug; say it is a timing change: {text!r}")
        for p in _PCT.findall(sentence):
            decimals = len(p.split(".")[1]) if "." in p else 0
            # a percentage is a rounded ratio: 15/16 may be written 94% (unlike WNS, where 0 decimals
            # means the value itself has none)
            if not any(abs(float(p) - r) <= 0.5 * 10 ** -decimals + 1e-9 for r in rates):
                reasons.append(f"cites {p}% but no score_tests or mutate result of this run reports that kill rate: "
                               f"{text!r}")
    return reasons
