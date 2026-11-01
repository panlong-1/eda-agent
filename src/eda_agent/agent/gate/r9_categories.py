"""Gate R9: a claim about a check category must cite a result of that category from this run, with
its key numbers; categories never stand in for each other (plan §0.19).

  R9 category  "lint clean", "CDC clean", "RDC clean", "DFT rules pass", "test/fault coverage N%",
               "formally equivalent" (and Chinese forms) need a result of that category in this run
               (an imported result judged by the core, or any result whose metrics.category is it),
               named in the same sentence (the category, the tool's name, or "imported"), with status
               pass for a positive claim, and at least one of its numbers in the sentence (waivers,
               crossings, coverage and target, points ...); every number given must be one it reports.
               Verification/code/functional coverage is never backed by ATPG coverage, and the other
               way round. Reasons start with "[error]" or "[fatal]" (one category standing in for another).
"""
from __future__ import annotations

import re

from .common import _T_CLAUSE, _T_SENTENCE, _matches, _negated

CATEGORY_CLAIMS = {
    "lint_rules": [r"lint[- ]clean", r"no\s+lint\s+(?:violations?|errors?)", r"rule[- ]checks?\s+(?:is\s+)?(?:clean|passed)",
                   "规则检查(?:通过|干净)"],
    "cdc": [r"CDC[- ](?:clean|passed|safe)", r"CDC\s+(?:is\s+|was\s+)?(?:clean|passed)",
            r"no\s+unsynchronized\s+(?:clock[- ]domain\s+)?crossings?", "跨时钟域(?:检查)?(?:通过|干净)"],
    "rdc": [r"RDC[- ](?:clean|passed|safe)", r"RDC\s+(?:is\s+|was\s+)?(?:clean|passed)",
            r"no\s+unsafe\s+reset[- ]domain\s+crossings?", "跨复位域(?:检查)?(?:通过|干净)"],
    "dft": [r"DFT\s+(?:rules?\s+)?(?:clean|passed?|met)", r"scan\s+(?:insertion\s+)?(?:passed|is\s+clean)",
            r"(?:is|are)\s+(?:fully\s+)?testable", "可测性(?:满足|通过)"],
    "atpg": [r"(?:test|fault)\s+coverage", r"ATPG\s+(?:coverage\s+)?(?:met|passed|reached|clean)", "(?:故障|测试)覆盖率"],
    "equivalence": [r"(?:formally|logically)\s+equivalent", r"equivalence\s+(?:check(?:ing)?\s+)?(?:passed|is\s+clean|proven|holds)",
                    r"netlists?\s+(?:are|is)\s+equivalent", r"LEC\s+(?:clean|passed)", "等价(?:检查)?通过"],
}
_CLAIM = {k: re.compile("|".join(f"(?:{p})" for p in v), re.I) for k, v in CATEGORY_CLAIMS.items()}
_VERIF_COVERAGE = re.compile(r"(?:verification|code|functional|line|branch|toggle)\s+coverage|验证覆盖率|代码覆盖率", re.I)
_NUM = re.compile(r"(?<![\w.])([+-]?\d+(?:\.\d+)?)(?![\w])")
# Numbers that are not the result's: list numbering, years.
_POSITIVE = re.compile(r"clean|passed?|met|safe|no\s+|testable|equivalent|holds|proven|reached|通过|干净|满足", re.I)


def _category_results(results: list[dict], cat: str) -> list[dict]:
    return [r for r in results if (r.get("metrics") or {}).get("category") == cat]


def _numbers(r: dict) -> set[float]:
    out: set[float] = set()

    def walk(x, key=""):
        if key in ("files", "artifacts", "source_dir", "experience", "notes"):
            return
        if isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            out.add(float(x))
        elif isinstance(x, dict):
            for k, v in x.items():
                walk(v, k)
        elif isinstance(x, list):
            out.add(float(len(x)))
            for v in x:
                walk(v, key)
    walk(r.get("metrics") or {})
    return out


def check_categories(claims: str, results: list[dict]) -> list[str]:
    reasons = []
    for sentence in _T_SENTENCE.findall(claims):
        text = sentence.strip()
        found = []
        for clause in _T_CLAUSE.findall(sentence):
            for cat, rx in _CLAIM.items():
                found += [(cat, m.group(0)) for m in rx.finditer(clause) if not _negated(clause, m.start())]
        if not found:
            continue
        if _VERIF_COVERAGE.search(sentence) and any(c == "atpg" for c, _ in found) and \
                not any((r.get("metrics") or {}).get("category") == "simulation" for r in results):
            reasons.append(f"[fatal] ATPG (manufacturing-fault) coverage is not verification coverage; the sentence "
                           f"mixes them: {text!r}")
        for cat, words in found:
            mine = _category_results(results, cat)
            if not mine:
                reasons.append(f"[error] '{words}' is a {cat} claim but no {cat} result is part of this run "
                               f"(imported_results with category {cat}): {text!r}")
                continue
            names = {cat, cat.replace("_", " "), "imported", "import"} | \
                {str((r.get("metrics") or {}).get("tool_name")) for r in mine}
            if not any(re.search(rf"(?<![\w-]){re.escape(n)}(?![\w-])", sentence, re.I) for n in names if n):
                reasons.append(f"[error] '{words}' must name the {cat} result it rests on (the category, the tool's name "
                               f"or 'imported') in the same sentence: {text!r}")
            if _POSITIVE.search(words) and not any(r.get("status") == "pass" for r in mine):
                reasons.append(f"[error] '{words}', but the {cat} result of this run is "
                               f"{mine[-1].get('status')} by the core's rule ({mine[-1].get('summary', '')[:120]}): {text!r}")
            known = set().union(*(_numbers(r) for r in mine))
            nums = [n for n in _NUM.findall(sentence) if not re.match(r"^(?:19|20)\d\d$", n)]
            if not nums:
                reasons.append(f"[error] '{words}' must give the {cat} result's key numbers (waivers, crossings, "
                               f"coverage and target, points ...): {text!r}")
            for n in nums:
                dec = len(n.split(".")[1]) if "." in n else 0
                if not _matches(abs(float(n)), dec, known):
                    reasons.append(f"[error] cites {n} but no {cat} result of this run reports it: {text!r}")
    return reasons
