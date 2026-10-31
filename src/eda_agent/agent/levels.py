"""Fatal / Error / Warning: one severity scheme for everything an approver must weigh.

- **Fatal**: the result cannot be trusted or used as it stands. `approve` refuses unless the
  person overrides it (`--override`) and gives a reason.
- **Error**: a real problem the approver must accept knowingly. `approve` needs a reason.
- **Warning**: worth knowing, does not block. A plain `approve` is enough.

Every rule is in RULES below (the README shows the same table). `assess(receipt)` applies them
to a receipt's own data, by plain code; the summary, the tables, the JSON (`levels`),
`human_gate.decide` and the review page all use it, so they never disagree.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass

LEVELS = ("fatal", "error", "warning")
TITLE = {"fatal": "Fatal", "error": "Error", "warning": "Warning"}

# Thresholds for "small slack": below these a pre-PnR estimate can easily turn negative after
# placement and routing.
SETUP_MARGIN = 0.10   # fraction of the clock period
HOLD_MARGIN_NS = 0.10

# (what, level) in the order the checks below apply them. Mapped from: tool status, tool
# diagnostics (error/warning), reviewer verdicts and findings (high/medium/low/info), the gate,
# the human gate and the receipt's own facts.
RULES = [
    ("a check ended in error or timeout, or failed", "fatal"),
    ("a tool call of the run ended in an error", "fatal"),
    ("the run stopped early (budget, retries)", "fatal"),
    ("the evidence gate rejected the report after its rewrites", "fatal"),
    ("a regression case failed; testbench sub-tests failed", "fatal"),
    ("lint (Verilator) reported errors", "fatal"),
    ("synthesis inferred latches", "fatal"),
    ("timing not met under the SDC", "fatal"),
    ("a reviewer's verdict is reject", "fatal"),
    ("mutation: the original checkout changed during the run", "fatal"),
    ("the receipt changed after its decision", "fatal"),
    ("a reviewer finding of severity high or medium (incl. rule-based errors: multiple drivers, "
     "width truncation, latches, blocking assignment in a clocked block)", "error"),
    ("the gate needed rewrites before it passed", "error"),
    ("a case was read (describe_case) but not run", "error"),
    ("a regression case was skipped", "error"),
    ("a check has no tool versions recorded", "error"),
    ("small timing slack: setup under 10 % of the clock period, or hold under 0.1 ns", "error"),
    ("a timing result is void (the SDC changed later in the run)", "error"),
    ("the SDC is not the design's own (sdc_origin, e.g. a hand translation)", "error"),
    ("the run was approved over the gate (--override)", "error"),
    ("a review failed to run", "error"),
    ("mutation: mutants that ended in an error", "error"),
    ("lint warnings", "warning"),
    ("a reviewer finding of severity low or info; a reviewer note; a review format problem", "warning"),
    ("only part of the case list ran; lint was not run; no independent review", "warning"),
    ("pass/fail comes from the testbench's own self-check or exit status", "warning"),
    ("timing is a pre-PnR estimate only", "warning"),
    ("mutation: surviving or likely-equivalent mutants", "warning"),
    ("the model-written summary was rejected by the gate", "warning"),
]

FINDING_LEVEL = {"high": "error", "medium": "error", "low": "warning", "info": "warning"}
DIAG_LEVEL = {"error": "fatal", "warning": "warning"}


@dataclass
class Issue:
    level: str        # fatal | error | warning
    text: str         # plain words, written here
    quote: str = ""   # verbatim text from the receipt (a finding, a reason), if any
    source: str = ""  # which check, reviewer or part of the receipt

    @property
    def id(self) -> str:
        """Stable within one receipt; the review page uses it for its tick boxes."""
        return hashlib.sha1(f"{self.level}|{self.text}|{self.quote}".encode()).hexdigest()[:10]

    def label(self) -> str:
        return f"{self.text}: {self.quote}" if self.quote else self.text

    def to_dict(self) -> dict:
        return {**asdict(self), "id": self.id}


def counts(issues: list[Issue]) -> dict:
    return {lvl: sum(i.level == lvl for i in issues) for lvl in LEVELS}


def counts_line(c: dict) -> str:
    return " · ".join(f"{TITLE[lvl]} {c[lvl]}" for lvl in LEVELS)


def _short(text: str, n: int = 220) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def regress_counts(summary: str, metrics: dict | None = None) -> dict:
    """Case and sub-test counts of a regress result: its metrics, else its summary line."""
    out = {}
    m = re.search(r"(\d+) passed, (\d+) failed(?:, (\d+) skipped)?", summary or "")
    if m:
        out |= {"passed": int(m[1]), "failed": int(m[2]), "skipped": int(m[3] or 0)}
    m = re.search(r"sub-tests: (\d+) passed, (\d+) failed of (\d+)", summary or "")
    if m:
        out |= {"subtests_passed": int(m[1]), "subtests_failed": int(m[2]), "subtests_total": int(m[3])}
    return {**out, **{k: v for k, v in (metrics or {}).items() if isinstance(v, (int, float))}}


def reviewer_name(v: dict) -> str:
    return "rule-based reviewer" if v["reviewer"] == "rules" else f"{v['reviewer']} ({v.get('model') or '?'})"


def unique_reviews(r: dict) -> list[tuple[dict, int]]:
    """Reviews with identical content (e.g. the rule-based review written twice), once, counted."""
    seen: dict[str, list] = {}
    for v in r.get("reviews", []):
        key = json.dumps([v["reviewer"], v.get("model"), v.get("verdict"),
                          [(f["severity"], f["text"]) for f in v.get("findings", [])]], ensure_ascii=False)
        seen.setdefault(key, [v, 0])[1] += 1
    return [(v, n) for v, n in seen.values()]


def assess(r: dict) -> list[Issue]:
    """All issues of a receipt, Fatal first, then Error, then Warning."""
    from .human_gate import content_sha256  # human_gate uses assess() for its policy
    out: list[Issue] = []
    def add(level: str, text: str, quote: str = "", source: str = "") -> None:
        out.append(Issue(level, text, _short(quote, 400), source))
    a = r.get("approval") or {}

    # the run and the gate
    saved = r.get("_source", r)  # the receipt as saved, not a display copy with trace data
    if a.get("receipt_sha256") and a["receipt_sha256"] != content_sha256(saved):
        add("fatal", f"The receipt changed after its decision (sha256 then {a['receipt_sha256'][:12]}…, "
                     f"now {content_sha256(saved)[:12]}…)", source="receipt")
    if r["gate"]["status"] == "rejected":
        add("fatal", "The evidence gate rejected the final report", source="gate")
    if r["stop_reason"] not in ("end", "gate_rejected"):
        add("fatal", f"The run did not end normally (stop reason {r['stop_reason']})", source="run")
    if r.get("tool_errors"):
        add("fatal", f"{r['tool_errors']} tool call(s) of the run ended in an error", source="run")
    if a.get("overrode_gate") or a.get("override"):
        add("error", "This run was approved over a Fatal item or the gate (--override)",
            quote=a.get("reason") or "", source="approval")
    if r["gate"]["rewrites"]:
        first = next((d["reasons"][0] for d in r["gate"]["decisions"] if not d["passed"] and d["reasons"]), "")
        add("error", f"The gate sent the agent's report back {r['gate']['rewrites']} time(s) before it passed",
            quote=first, source="gate")

    # the checks
    for c in r["checks_run"]:
        tool = c["tool"]
        m = c.get("metrics") or {}
        if c["status"] != "pass":
            add("fatal", f"{tool} ended with status {c['status']}",
                quote=c["summary"].splitlines()[0] if c["summary"] else "", source=tool)
        if tool == "lint":
            errors = m.get("errors")
            if errors is None:
                hit = re.search(r"(\d+) error", c["summary"])
                errors = int(hit[1]) if hit else 0
            hit = re.search(r"(\d+) warning", c["summary"])
            warnings = m.get("warnings", int(hit[1]) if hit else 0)
            if errors:
                add("fatal", f"lint: {errors} error(s)", source=tool)
            if warnings:
                codes = ", ".join(f"{k} {v}" for k, v in sorted((m.get("by_code") or {}).items(), key=lambda x: -x[1]))
                add("warning", f"lint: {warnings} warning(s)", quote=codes, source=tool)
        if tool == "regress":
            n = regress_counts(c["summary"], m)
            if n.get("failed"):
                add("fatal", f"regress: {n['failed']} case(s) failed",
                    quote=", ".join(m.get("failed_cases", [])), source=tool)
            if n.get("subtests_failed"):
                add("fatal", f"regress: {n['subtests_failed']} testbench sub-test(s) failed", source=tool)
            if n.get("skipped"):
                add("error", f"regress: {n['skipped']} case(s) skipped", source=tool)
            if "subtests_total" in n:
                add("warning", f"regress: {n.get('passed', '?')} case(s) passed, which the testbench counts as "
                               f"{n['subtests_passed']} of {n['subtests_total']} sub-tests; these counts are the "
                               "testbench's own summary line", source=tool)
            elif n.get("passed"):
                add("warning", "regress: pass/fail comes from the testbench's own self-check or exit status, "
                               "not from separate results", source=tool)
        tm = c.get("timing")
        if tm:
            if tm.get("void"):
                add("error", f"{tool}: this timing result is void, the SDC changed later in the run", source=tool)
            if tm.get("latches"):
                add("fatal", f"{tool}: {tm['latches']} latch(es) inferred", source=tool)
            if tm.get("timing_met") is False:
                add("fatal", f"{tool}: timing NOT met (setup WNS {tm.get('wns')} ns, hold WNS {tm.get('hold_wns')} ns)",
                    source=tool)
            else:
                period, wns, hold = tm.get("clock_period"), tm.get("wns"), tm.get("hold_wns")
                if period and isinstance(wns, (int, float)) and wns < SETUP_MARGIN * period:
                    add("error", f"{tool}: small setup slack, {wns:+.3f} ns = {100 * wns / period:.0f}% of the "
                                 f"{period:g} ns clock (pre-PnR; usually worse after routing)", source=tool)
                if isinstance(hold, (int, float)) and hold < HOLD_MARGIN_NS:
                    add("error", f"{tool}: small hold slack, {hold:+.3f} ns; real clock skew after layout can "
                                 "eat it", source=tool)
            add("warning", f"{tool}: timing is a pre-PnR estimate (no wire parasitics, ideal clocks), not signoff",
                source=tool)
        if tool == "mutate":
            if m.get("root_unchanged") is False:
                add("fatal", "mutate: the original checkout changed during the run", source=tool)
            if m.get("error"):
                add("error", f"mutate: {m['error']} mutant(s) ended in an error", source=tool)
            if m.get("survived"):
                add("warning", f"mutate: {m['survived']} mutant(s) survived (no test caught them)", source=tool)
            if m.get("likely_equivalent"):
                add("warning", f"mutate: {m['likely_equivalent']} mutant(s) likely equivalent (not a proof)",
                    source=tool)
    missing = [c["tool"] for c in r["checks_run"] if not c.get("tool_versions")]
    if missing:
        add("error", f"No tool versions recorded for: {', '.join(missing)}; the result cannot be tied to a "
                     "tool release", source="run")
    if r.get("sdc_origin"):
        add("error", "The SDC is not the design's own", quote=r["sdc_origin"], source="sdc")

    # what was not run
    nr = r["not_run"]
    if nr["described_but_not_run"]:
        add("error", f"Read but not run: {', '.join(nr['described_but_not_run'])}", source="run")
    if nr["cases_not_run"]:
        add("warning", f"Only part of the regression ran: {len(nr['cases_not_run'])} listed case(s) not run",
            source="run")
    if not nr["lint_run"]:
        add("warning", "lint was not run", source="run")

    # reviewers: verdicts, then findings (each text once, at its most severe level)
    reviews = unique_reviews(r)
    for v, _ in reviews:
        if v.get("verdict") == "reject":
            add("fatal", f"{reviewer_name(v)[0].upper()}{reviewer_name(v)[1:]} said reject", source=reviewer_name(v))
    seen: dict[str, Issue] = {}
    for v, _ in reviews:
        for f in v.get("findings", []):
            key = " ".join(f["text"].split())
            level = FINDING_LEVEL.get(f["severity"], "warning")
            prev = seen.get(key)
            if prev and LEVELS.index(prev.level) <= LEVELS.index(level):
                continue
            seen[key] = Issue(level, f"[{f['severity']}] from {reviewer_name(v)}", _short(f["text"], 400),
                              reviewer_name(v))
    out += list(seen.values())
    for v, _ in reviews:
        if not v.get("format_ok", True):
            add("warning", f"Reviewer {reviewer_name(v)} did not follow the required format", source=reviewer_name(v))
        if v.get("note"):
            add("warning", f"Note on reviewer {reviewer_name(v)}", quote=v["note"], source=reviewer_name(v))
    if r.get("review_error"):
        add("error", "A review failed to run", quote=r["review_error"], source="review")
    if not r.get("reviews") and not r.get("review_error"):
        add("warning", "No independent review was run: only the gate checked the report", source="review")
    n = r.get("narrative")
    if n and not n["gate"]["passed"]:
        add("warning", "The model-written summary was rejected by the evidence gate; rely on the tables",
            source="summary")
    return sorted(out, key=lambda i: LEVELS.index(i.level))


def levels_record(r: dict) -> dict:
    """What goes into the receipt's JSON as `levels` (derived; left out of the decision sha256)."""
    issues = assess(r)
    return {"counts": counts(issues), "items": [i.to_dict() for i in issues], "rules": "eda_agent/agent/levels.py"}
