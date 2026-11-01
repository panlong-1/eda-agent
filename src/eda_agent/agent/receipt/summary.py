"""Layer 2, summary: the one screen a person reads before `eda-agent approve` / `reject`.

Built by plain code from the receipt alone (no model, no files opened), so it can be
re-rendered at any time and never says more than the receipt does. Every issue carries a
level, Fatal / Error / Warning (agent/levels.py), which also decides what `approve` needs.

    summarize(receipt) -> Summary            the data, shared by both pages (markdown.py, page.py)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from ...core.envelope import EVIDENCE_LEVELS
from ...core.numbers import SEVERITIES, severity_rank
from ..gate import split_residual
from ..human_gate import AWAITING
from ..levels import FINDING_LEVEL, LEVELS, Issue, assess, counts, counts_line, reviewer_name, unique_reviews
from .data import receipt_id, receipt_kind

SHORT = 220  # characters of a quote shown in the summary; the full text is in the tables and details

# What each evidence level lets a reader conclude, and what it does not (envelope.py ladder).
EVIDENCE = {
    "E0": ("parses / structural facts", "the design parses and elaborates, or the constraints exist as stated",
           "that the design works, or meets timing"),
    "E1": ("someone's score", "a person or a model rated it", "anything measured by a tool"),
    "E2": ("these tests passed", "the listed test cases ran and passed by the testbench's own checks",
           "that the design is correct, or that the tests would catch a bug (no coverage)"),
    "E3": ("bit-exact / mutation evidence", "outputs match a reference bit for bit, or the tests kill injected bugs",
           "timing, power or anything after layout"),
    "E4": ("synthesis estimate", "area and timing as estimated before placement and routing, under this SDC",
           "function, or signoff timing after placement and routing"),
    "E5": ("signoff", "post-route multi-corner timing, DRC/LVS and gate-level checks passed",
           "anything outside the signed-off corners and modes"),
}
VERDICT = {"accept": "accept", "accept_with_findings": "accept with findings", "reject": "REJECT", "": "(no verdict)"}
STATUS = {"pass": "pass", "fail": "FAIL", "error": "ERROR", "timeout": "TIMEOUT"}
RUN_STATUS = {"awaiting_approval": "awaiting approval", "approved": "approved", "rejected": "rejected",
              "done": "done (no approval requested)", "pending": "pending (gate rejected)"}
RUN_CLASS = {"approved": "ok", "rejected": "bad", "awaiting_approval": "warning", "pending": "bad", "done": "muted"}
STATUS_CLASS = {"pass": "ok", "fail": "bad", "error": "bad", "timeout": "bad"}
GENERATED = ("Written by plain code from the receipt data (no model). The tables below are the record; "
             "the full receipt is at the end.")


@dataclass
class Summary:
    run_id: str
    task: str
    run_line: str
    status: str
    decision: str
    issues: list[Issue] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    needs: str = ""
    checks: list[dict] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    not_checked: list[str] = field(default_factory=list)
    gate: str = ""
    gate_reasons: list[str] = field(default_factory=list)
    reviews: list[dict] = field(default_factory=list)
    approval: str = ""
    notes: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    commands_note: str = ""
    headline: str = ""
    status_class: str = "muted"   # colour of the status badge
    gate_status: str = ""          # passed | rejected | not_run
    no_review: str = "no independent review was run"


def _review_rank(v: dict) -> tuple:
    worst = min((severity_rank(f["severity"]) for f in v.get("findings", [])), default=len(SEVERITIES))
    return (0 if v.get("verdict") == "reject" else 1, worst)


def approve_needs(c: dict) -> str:
    """What `approve` asks for, by the levels (human_gate.decide enforces the same)."""
    if c["fatal"]:
        return (f"{c['fatal']} Fatal item(s): approving needs --override and a --reason "
                "(the decision records the override)")
    if c["error"]:
        return f"{c['error']} Error item(s): approving needs a --reason that says why they are acceptable"
    return "Warnings only: a plain approve is enough"


def summarize(r: dict) -> Summary:
    kind = receipt_kind(r)
    if kind == "testgen":
        return _summarize_testgen(r)
    if kind == "import":
        return _summarize_import(r)
    if kind == "tune":
        from .tune import summarize_tune
        return summarize_tune(r)
    a = r.get("approval") or {}
    issues = assess(r)
    s = Summary(run_id=r["run_id"], task=r["task"],
                run_line=f"{r['project']} · {r['provider']} / {r['model']}"
                         + (f" · prompt {r['prompt']}" if r.get("prompt") else "")
                         + (f" · preset {r['preset']}" if r.get("preset") else ""),
                status=RUN_STATUS.get(r["status"], r["status"]), decision="", issues=issues,
                counts=counts(issues), status_class=RUN_CLASS.get(r["status"], "muted"),
                gate_status=r["gate"]["status"])
    s.needs = approve_needs(s.counts)
    _decision(s, r, a)
    _checks(s, r)
    _evidence(s)
    _not_checked(s, r)
    _gate(s, r)
    _reviews(s, r)
    s.notes = _notes(a)
    _headline(s, [f"{sum(c['status'] == 'pass' for c in s.checks)} of {len(s.checks)} check(s) passed",
                  f"gate {r['gate']['status']}"])
    return s


def _notes(a: dict) -> list[str]:
    return [f"{n.get('at')} {n.get('by')}: {n.get('note') or '(no note)'}" for n in a.get("notes", [])]


def _headline(s: Summary, parts: list[str]) -> None:
    if s.reviews:
        verdicts: dict[str, int] = {}
        for v in s.reviews:
            verdicts[v["verdict_text"]] = verdicts.get(v["verdict_text"], 0) + 1
        parts.append("reviewers: " + ", ".join(f"{n}× {k}" for k, n in verdicts.items()))
    s.headline = "; ".join(parts) + "."


def _decision(s: Summary, r: dict, a: dict) -> None:
    rid = receipt_id(r)
    status = a.get("status")
    if status in ("approved", "rejected"):
        reason = f" ({a['reason']})" if a.get("reason") else ""
        s.decision = (f"Already decided: {status} by {a['by']} at {a['at']}{reason}. Decisions are final; "
                      "this summary was re-rendered afterwards.")
        s.approval = f"{status} by {a['by']} at {a['at']}{reason}"
        if a.get("levels"):
            s.approval += f" · levels at the decision: {counts_line(a['levels'])}"
        s.commands_note = "already decided; decisions are final (a new run fixes a rejected one)"
        return
    if r["status"] == AWAITING:
        why = a.get("why") or ""
        s.decision = (f"Approve or reject step '{a.get('step') or 'final report'}': accept the agent's final "
                      f"report below as the result of this task? (requested because: {why})")
        s.approval = "pending" + (f" · {len(a.get('notes', []))} note(s)" if a.get("notes") else "")
    elif r["status"] == "pending":
        s.decision = ("The evidence gate rejected the agent's report (a Fatal item). Approving it overrides "
                      "the gate; rejecting it closes the run.")
        s.approval = "no approval record"
    else:
        s.decision = f"No human decision was requested for this run (status {r['status']}); nothing to decide."
        s.approval = "no approval record"
        return
    c = s.counts
    approve = (f'eda-agent approve {rid} --override --reason "..."' if c["fatal"]
               else f'eda-agent approve {rid} --reason "..."' if c["error"] else f"eda-agent approve {rid}")
    s.commands = [approve, f'eda-agent reject {rid} --reason "..."', f'eda-agent pending {rid} --note "..."']


def _check(tool: str, args, status: str, level: str, summary: str, duration=None) -> dict:
    return {
        "tool": tool, "args": json.dumps(args, ensure_ascii=False) if args else "",
        "status": status, "status_text": STATUS.get(status, status),
        "level": level, "level_text": EVIDENCE[level][0] if level in EVIDENCE else level,
        "summary": summary.splitlines()[0] if summary else "",
        "duration": duration,
    }


def _checks(s: Summary, r: dict) -> None:
    for c in r["checks_run"]:
        s.checks.append(_check(c["tool"], c["args"], c["status"], c["evidence_level"], c["summary"],
                               c.get("duration_s")))


def _evidence(s: Summary) -> None:
    passing = {c["level"] for c in s.checks if c["status"] == "pass"}
    levels = [lvl for lvl in reversed(EVIDENCE_LEVELS) if lvl in passing]
    if not levels:
        s.evidence.append("No passing evidence: nothing may be concluded.")
        return
    s.evidence.append(f"Highest evidence reached: {levels[0]} ({EVIDENCE[levels[0]][0]}).")
    functional = [lvl for lvl in levels if lvl in ("E2", "E3", "E5")]
    if levels[0] == "E4" and functional:
        s.evidence.append(f"On function the highest is {functional[0]} ({EVIDENCE[functional[0]][0]}); "
                          "E4 never speaks about function.")
    for lvl in levels:
        words, may, may_not = EVIDENCE[lvl]
        s.evidence.append(f"{lvl} ({words}): may say {may}; may NOT say {may_not}.")


def _not_checked(s: Summary, r: dict) -> None:
    nr = r["not_run"]
    tools = {c["tool"] for c in r["checks_run"]}
    if nr["cases_not_run"]:
        names = ", ".join(nr["cases_not_run"][:8]) + (" …" if len(nr["cases_not_run"]) > 8 else "")
        s.not_checked.append(f"{len(nr['cases_not_run'])} case(s) in the project's list were not run: {names}")
    if nr["described_but_not_run"]:
        s.not_checked.append(f"read but not run: {', '.join(nr['described_but_not_run'])}")
    if not nr["lint_run"]:
        s.not_checked.append("lint was not run")
    if "regress" not in tools:
        s.not_checked.append("no simulation (regress) was run")
    if not any(c.get("timing") for c in r["checks_run"]):
        s.not_checked.append("no synthesis or timing estimate was run")
    s.not_checked += list(r["residual_risk"]["from_tools"])


def _gate(s: Summary, r: dict) -> None:
    g = r["gate"]
    s.gate = f"{g['status']}; the agent's report was sent back for a rewrite {g['rewrites']} time(s)"
    s.gate_reasons = [reason for d in g["decisions"] if not d["passed"] for reason in d["reasons"]]


def _reviews(s: Summary, r: dict) -> None:
    for v, n in sorted(unique_reviews(r), key=lambda x: _review_rank(x[0])):
        findings = sorted(v.get("findings", []), key=lambda f: severity_rank(f["severity"]))
        by_level = {lvl: sum(FINDING_LEVEL.get(f["severity"], "warning") == lvl for f in findings) for lvl in LEVELS}
        s.reviews.append({
            "who": reviewer_name(v) + (f" (×{n}, identical)" if n > 1 else ""),
            "verdict": v.get("verdict") or "", "verdict_text": VERDICT.get(v.get("verdict") or "", v.get("verdict")),
            "counts": {k: c for k, c in by_level.items() if c},
            "worst": findings[0] if findings else None,
        })


def narrative_meta(r: dict, n: dict) -> tuple[str, str]:
    """(who/when line, gate line) of the model-written summary."""
    meta = (f"Written by {n.get('provider') or '?'} / {n.get('model') or '?'} (prompt {n.get('prompt') or '?'}) "
            f"at {n.get('at') or '?'}, from the receipt data only. Check it against the tables: they are the record.")
    a = r.get("approval") or {}
    if a.get("at") and n.get("at") and n["at"] > a["at"]:
        meta += " Written after the decision; not part of what was decided."
    gate = "passed the evidence gate" if n["gate"]["passed"] else "REJECTED by the evidence gate: do not rely on it"
    if n["gate"].get("rewrites"):
        gate += f" ({n['gate']['rewrites']} rewrite(s))"
    return meta, gate


# ---------------------------------------------------------------- write-tests receipts

def _summarize_testgen(r: dict) -> Summary:
    a = r.get("approval") or {}
    issues = assess(r)
    decision = (r.get("gate") or {}).get("decision") or {}
    gate_status = "passed" if decision.get("passed") else "rejected"
    targets = ", ".join(map(str, r.get("targets") or []))
    s = Summary(run_id=r["run_id"],
                task=f"Write new tests that kill surviving mutants {targets} of mutate run {r['mutate_run']}",
                run_line=f"{r['project']} · {r['provider']} / {r['model']} · prompt {r['prompt']} · "
                         f"review prompt {r['review_prompt']}",
                status=RUN_STATUS.get(r["status"], r["status"]), decision="", issues=issues,
                counts=counts(issues), status_class=RUN_CLASS.get(r["status"], "muted"), gate_status=gate_status)
    s.needs = approve_needs(s.counts)
    _decision(s, r, a)
    results = r.get("results", [])
    for t in results:
        for score in t.get("scores", []):
            s.checks.append(_check(score.get("tool") or "score_tests", {"mutant": t["mutant"]},
                                   score.get("status") or "", score.get("evidence_level") or "",
                                   score.get("summary", "")))
    _evidence(s)
    for t in results:
        if t["status"] != "closed":
            s.not_checked.append(f"mutant {t['mutant']}: gap not closed" + (f" ({t['note']})" if t.get("note") else ""))
        elif not (t.get("review") or {}).get("verdict"):
            s.not_checked.append(f"mutant {t['mutant']}: the new test was not reviewed")
    residual = split_residual(r.get("final_claims") or "")[1].strip(" :\n")
    if residual:
        s.not_checked.append(residual)
    s.gate = f"{gate_status}; the report is written by code from the score_tests results (no rewrites)"
    s.gate_reasons = [] if decision.get("passed") else list(decision.get("reasons", []))
    for t in results:
        v = t.get("review") or {}
        if t["status"] == "closed" and v:
            verdict = (v.get("verdict") or "").strip().lower()
            s.reviews.append({"who": f"test reviewer ({r['model']}), mutant {t['mutant']}", "verdict": verdict,
                              "verdict_text": VERDICT.get(verdict, verdict), "counts": {}, "worst": None})
    s.notes = _notes(a)
    closed = sum(t["status"] == "closed" for t in results)
    _headline(s, [f"{closed} of {len(results)} gap(s) closed", f"gate {gate_status}"])
    return s


# ---------------------------------------------------------------- import receipts

def _summarize_import(r: dict) -> Summary:
    e = r["envelope"]
    m = e.get("metrics") or {}
    issues = assess(r)
    versions = " ".join(f"{k} {v}" for k, v in (e.get("tool_versions") or {}).items())
    s = Summary(run_id=receipt_id(r),
                task=f"Import a {m.get('category')} report made by {m.get('tool_name')} ({m.get('source_dir')})",
                run_line=f"{r['design']} · {versions} · importer {m.get('importer')} {m.get('importer_version')} · "
                         f"imported {r.get('imported_at')}",
                status=f"imported: {STATUS.get(e['status'], e['status'])} by the core's rule", decision="",
                issues=issues, counts=counts(issues), status_class=STATUS_CLASS.get(e["status"], "muted"),
                gate_status="not_run", no_review="imports are not reviewed")
    s.decision = ("Nothing to decide: an import records a report made elsewhere, judged by the core's rule for "
                  "its category. A report that cites it still goes through the evidence gate (R9).")
    s.approval = "no approval record (imports are not decided)"
    s.checks.append(_check(e["tool"], {"category": m.get("category"), "importer": m.get("importer")}, e["status"],
                           e["evidence_level"], e.get("summary", "")))
    _evidence(s)
    snap = e.get("snapshot") or {}
    if snap.get("state") == "unknown":
        s.not_checked.append(f"which design the report describes: {snap.get('note')}")
    s.not_checked += list(e.get("residual_risk") or [])
    s.gate = "not run: an import has no written report to check"
    _headline(s, [f"{m.get('category')}: {e['status']} by the core's rule", f"evidence {e['evidence_level']}"])
    return s
