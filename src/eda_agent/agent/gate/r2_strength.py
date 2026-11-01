"""R2 strength: functional-correctness words need a passing result at a functional level (E3 or E5;
E4 synthesis says nothing about function) that the same sentence names (its tool or one of its
cases); with E2 the report may only say which cases passed. "Does not prove: A, B, or C" lists
limits, not claims."""
from __future__ import annotations

import re

from ...core.numbers import level_rank
from .common import _CLAUSE, _NEG, _SENTENCE, _names


# Level a passing result must reach before the report may use strong claim words.
STRONG_CLAIM_MIN_LEVEL = "E3"
# Levels that speak about function. The ladder is not one line: E4 (synthesis, PPA) is above
# E3 but says nothing about whether the design computes the right thing.
FUNCTIONAL_LEVELS = ("E3", "E5")

STRONG_CLAIM_WORDS = [
    r"handles?", r"handled", r"correct(?:ly|ness)?", r"verified", r"verifies", r"proven",
    # "work" alone is usually the noun ("synthesis/timing work"); only the verb claims function
    r"works", r"working", r"(?:will|does|do|did|can|should|must) work", r"functional(?:ly)? correct", r"bug[- ]free", r"no bugs?", r"guarantee[sd]?",
    r"robust", r"fully (?:tested|functional)",
    "正确", "已验证", "验证通过", "没有\\s*bug", "无\\s*bug", "正常工作", "保证", "可靠",
]

# Not inside a path or identifier: runs/work/x, case_working, verified.log are not claims.
_STRONG = re.compile(r"(?<![A-Za-z/_.])(?:" + "|".join(STRONG_CLAIM_WORDS) + r")(?![A-Za-z/_])", re.I)

# What may stand before the strong word in a later item of a list: "correct", "or bug-free",
# "fully correct", "或无 bug". Anything more ("but the design is correct") starts a new claim.
_LIST_ITEM = re.compile(r"\s*(?:(?:or|and|nor)\s+|或者?|和|以及|及)?\s*(?:[A-Za-z]+ly\s+)?", re.I)
# A heading-style limit: "Does not prove: A, B, or gate-level functional correctness." Everything
# after the colon, to the end of the sentence, is a list of what was not shown.
_LIMIT_LIST = re.compile(
    r"(?<![A-Za-z])(?:not|never|cannot|no)\s+(?:\w+\s+){0,2}?(?:prove|show|establish|cover|demonstrate|"
    r"verify|guarantee|imply|tell|say)\w*\s*(?:about\s*)?[:：]|(?:不能|无法|未能?)(?:证明|说明|覆盖|保证)\s*[:：]",
    re.I)


def check_strength(claims: str, envelopes: list[dict]) -> list[str]:
    """A strong word needs a passing result at a functional level (E3, E5) that its sentence
    names, by tool or case, so evidence on one check cannot vouch for a claim about something
    else, and synthesis (E4) never vouches for function."""
    strong = [r for r in envelopes if r.get("status") == "pass"
              and r.get("evidence_level") in FUNCTIONAL_LEVELS]
    subjects = sorted({s for r in strong
                       for s in [r.get("tool", ""), *(r.get("metrics") or {}).get("passed_cases", [])] if s})
    best = max((r["evidence_level"] for r in envelopes if r.get("status") == "pass"), key=level_rank, default="")
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
