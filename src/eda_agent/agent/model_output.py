"""Model reply -> structured fields -> evidence gate: the steps every model-written text goes through.

Used by the reviewer (checklist, findings, verdict), write-tests (a test file in a code block, the
test reviewer's fields) and the receipt's model-written summary. The formats are the ones the
prompts ask for; nothing here guesses at a format a prompt did not ask for.

    parse_checklist_review(checklist_turn, findings_turn)   reviewer.py, prompts/reviewer.*
    parse_test_review(text)                                  testgen.py, prompts/testgen-review.*
    last_code_block(text)                                    testgen.py, prompts/testgen.*
    gate_decision(text, results)                             any text checked against tool results
    results_from_receipt(receipt)                            the tool results a receipt still holds
"""
from __future__ import annotations

import re

from .gate import check_report

# ---------------------------------------------------------------- parsing

_BLOCK = re.compile(r"```[^\n]*\n(.*?)```", re.S)
_ITEM = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+(?:\[[ xX]\]\s*)?(.+?)\s*$", re.M)
_FINDING = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+\[(high|medium|low|info)\]\s*(.+?)\s*$", re.I | re.M)
REVIEW_SECTIONS = ("checklist", "findings", "verdict")
VERDICTS = ("accept_with_findings", "accept", "reject")  # longest first, for matching
TEST_REVIEW_FIELDS = ("architectural_only", "expected_values_checked", "verdict", "notes")


def last_code_block(text: str) -> str | None:
    """The last fenced code block of a reply."""
    blocks = _BLOCK.findall(text or "")
    return blocks[-1] if blocks else None


def _heading(names: tuple[str, ...]) -> re.Pattern:
    return re.compile(r"^\s*(?:#+\s*|\*\*)?(" + "|".join(names) + r")\b", re.I | re.M)


def section(text: str, name: str, names: tuple[str, ...] = REVIEW_SECTIONS) -> str:
    """The text under heading `name` ('## Findings', '**Findings**', 'Findings'), up to the next
    heading of `names`; empty when there is no such heading."""
    heads = list(_heading(names).finditer(text))
    for i, h in enumerate(heads):
        if h.group(1).lower() == name:
            end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
            return text[h.end():end]
    return ""


def key_values(text: str, keys: tuple[str, ...]) -> dict[str, str]:
    """'key: value' lines for the given keys (keys lower-cased, values stripped)."""
    pattern = re.compile(r"^(" + "|".join(keys) + r"):\s*(.*)$", re.M | re.I)
    return {k.lower(): v.strip() for k, v in pattern.findall(text or "")}


def parse_checklist_review(checklist_text: str, findings_text: str) -> tuple[list[str], list[dict], str, bool]:
    """(checklist items, findings {severity, text}, verdict, format followed) of a two-turn review."""
    checklist = [m.group(1) for m in _ITEM.finditer(section(checklist_text, "checklist") or checklist_text)]
    findings_part = section(findings_text, "findings")
    findings = [{"severity": m.group(1).lower(), "text": m.group(2)} for m in _FINDING.finditer(findings_part)]
    verdict_part = section(findings_text, "verdict").lower()
    verdict = next((v for v in VERDICTS if v in verdict_part), "")
    # Checklist first: the first turn must have a checklist and no findings section of its own.
    ok = bool(checklist) and not section(checklist_text, "findings") and bool(verdict) and bool(findings_part)
    return checklist, findings, verdict, ok


def parse_test_review(text: str) -> dict:
    """The test reviewer's fields; format_ok when at least architectural_only and verdict are there."""
    got = key_values(text, TEST_REVIEW_FIELDS)
    got["format_ok"] = {"architectural_only", "verdict"} <= set(got)
    return got


def add_usage(total: dict, turn_usage: dict) -> dict:
    """Add a turn's token counts into total (numbers only)."""
    for k, v in (turn_usage or {}).items():
        if isinstance(v, (int, float)):
            total[k] = total.get(k, 0) + v
    return total


# ---------------------------------------------------------------- the evidence gate

def gate_decision(text: str, results: list[dict]) -> dict:
    """The evidence gate's decision on a text written from these tool results."""
    return check_report(text, results).to_dict()


def results_from_receipt(r: dict) -> list[dict]:
    """Tool results as the gate expects them, rebuilt from a run receipt's checks."""
    results = []
    for c in r["checks_run"]:
        metrics = dict(c.get("metrics") or {})
        if c.get("timing"):
            metrics = {**{k: v for k, v in c["timing"].items() if k != "void"},
                       "sdc_sha256": c.get("sdc_sha256"), **metrics}
        results.append({"tool": c["tool"], "status": c["status"], "evidence_level": c["evidence_level"],
                        "summary": c["summary"], "claim_scope": c["claim_scope"], "metrics": metrics})
    described = r["not_run"].get("described_but_not_run") or []
    if described:  # so that R4 asks for them under residual risk, as it did for the author
        results.append({"tool": "describe_case", "status": "ok", "cases": [{"name": n} for n in described]})
    return results
