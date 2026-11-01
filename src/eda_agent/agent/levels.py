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
from dataclasses import asdict, dataclass, field

from ..core.numbers import shorten

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
    ("environment not ready: a stage could not run for a setup problem (core/setup_issues.py), shown with "
     "the rule-based fix; not a test failure", "fatal"),
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
    ("import: the report fails its category's rule, or could not be judged", "fatal"),
    ("the report cites a lesson or document that does not exist or was not retrieved in this run, or uses "
     "one as evidence (gate R11), and the gate rejected it", "fatal"),
    ("a reviewer finding of severity high or medium (incl. rule-based errors: multiple drivers, "
     "width truncation, latches, blocking assignment in a clocked block)", "error"),
    ("the gate needed rewrites before it passed", "error"),
    ("a case was read (describe_case) but not run", "error"),
    ("a regression case was skipped", "error"),
    ("a check has no tool versions recorded", "error"),
    ("small timing slack: setup under 10 % of the clock period, or hold under 0.1 ns", "error"),
    ("a timing result is void (the SDC changed later in the run)", "error"),
    ("a setup issue in a check that still ran (e.g. a flow run with a signoff deck missing)", "error"),
    ("the SDC is not the design's own (sdc_origin, e.g. a hand translation)", "error"),
    ("the run was approved over the gate (--override)", "error"),
    ("a review failed to run", "error"),
    ("mutation: mutants that ended in an error", "error"),
    ("import: the report does not identify the design it describes (no RTL hashes)", "error"),
    ("lint warnings", "warning"),
    ("a reviewer finding of severity low or info; a reviewer note; a review format problem", "warning"),
    ("only part of the case list ran; lint was not run; no independent review", "warning"),
    ("pass/fail comes from the testbench's own self-check or exit status", "warning"),
    ("timing is a pre-PnR estimate only", "warning"),
    ("a non-blocking setup finding, e.g. a behavioural memory synthesized to flip-flops", "warning"),
    ("mutation: surviving or likely-equivalent mutants", "warning"),
    ("a failure localisation (trace_driver / trace_load / waveform_query): a diagnosis, a hypothesis about "
     "the cause; not a proof and not a fix", "warning"),
    ("the model-written summary was rejected by the gate", "warning"),
    ("lessons or documents were given to the agent as hints (never evidence); the context was compacted "
     "(the gate and reviewers read the full trace)", "warning"),
    ("import: input files the report names were not found here; waived items; the tool was not run by "
     "eda-agent", "warning"),
    ("a decision made with eda-agent code older than the receipt's own, or that does not meet the approval "
     "policy of the current code (e.g. Fatal approved without --override and a reason)", "warning"),
]

FINDING_LEVEL = {"high": "error", "medium": "error", "low": "warning", "info": "warning"}
DIAG_LEVEL = {"error": "fatal", "warning": "warning"}


@dataclass
class Issue:
    level: str        # fatal | error | warning
    text: str         # one line in plain words (for a finding: its first sentence)
    quote: str = ""   # verbatim text from the receipt (a reason, a note), if any
    source: str = ""  # which check, reviewer(s) or part of the receipt
    location: str = ""                                  # file:line, when the receipt names one
    details: list[str] = field(default_factory=list)    # the full texts behind a grouped finding

    @property
    def id(self) -> str:
        """Stable within one receipt; the review page uses it for its tick boxes."""
        return hashlib.sha1(f"{self.level}|{self.text}|{self.quote}|{self.location}".encode()).hexdigest()[:10]

    def label(self) -> str:
        where = f" ({self.location})" if self.location else ""
        return (f"{self.text}: {self.quote}" if self.quote else self.text) + where

    def to_dict(self) -> dict:
        return {**asdict(self), "id": self.id}


_LOCATION = re.compile(r"[\w./-]+\.(?:sv|svh|v|vh|vhd|sdc|tcl|S|s|c|h|py|yaml|toml):\d+(?![\w])")
_AT = re.compile(r"\s*\(at [^)]*\)")
_EVIDENCE = re.compile(r"\s*(?:—|--|-)?\s*\b[Ee]vidence:.*$", re.S)


def locations(text: str) -> list[str]:
    """file:line mentions, without a bare name that a longer path already covers."""
    found = list(dict.fromkeys(_LOCATION.findall(text)))
    return [x for x in found if not any(y != x and y.endswith("/" + x) for y in found)]


def first_sentence(text: str, n: int = 170) -> str:
    """A finding's first sentence, without its location and evidence tail, as one line."""
    t = _EVIDENCE.sub("", _AT.sub("", " ".join(text.replace("**", "").split())))
    m = re.search(r"(?<!e\.g)(?<!i\.e)[.;](\s|$)", t)
    t = t[: m.start()] if m and m.start() > 30 else t
    return t if len(t) <= n else t[: t.rfind(" ", 0, n - 1)].rstrip(",;:") + " …"


def _group_findings(reviews: list[tuple[dict, int]]) -> list[Issue]:
    """One issue per finding place: findings that name the same first file:line (or, naming
    none, have the same text) are one row, at the highest level any reviewer gave it, with
    every reviewer listed and each full text kept for the details."""
    groups: dict[str, list[tuple[dict, dict]]] = {}
    for v, _ in reviews:
        for f in v.get("findings", []):
            locs = locations(f["text"])
            key = locs[0].rsplit("/", 1)[-1] if locs else " ".join(f["text"].split())
            groups.setdefault(key, []).append((v, f))
    out = []
    for members in groups.values():
        level = min((FINDING_LEVEL.get(f["severity"], "warning") for _, f in members), key=LEVELS.index)
        # the one-line text: the rule-based reviewer's (terse) if it is there, else the most severe
        lead = min(members, key=lambda m: (m[0]["reviewer"] != "rules", LEVELS.index(
            FINDING_LEVEL.get(m[1]["severity"], "warning")), len(m[1]["text"])))[1]
        who = list(dict.fromkeys(reviewer_name(v) for v, _ in members))
        locs = next((locations(f["text"])[:1] for _, f in members if locations(f["text"])), [])
        texts = list(dict.fromkeys(f"[{f['severity']}] {reviewer_name(v)}: {f['text']}" for v, f in members))
        out.append(Issue(level, first_sentence(lead["text"]), source=", ".join(who),
                         location=", ".join(locs), details=texts if len(texts) > 1 or
                         len(" ".join(lead["text"].split())) > len(first_sentence(lead["text"])) + 2 else []))
    return out


def counts(issues: list[Issue]) -> dict:
    return {lvl: sum(i.level == lvl for i in issues) for lvl in LEVELS}


def counts_line(c: dict) -> str:
    return " · ".join(f"{TITLE[lvl]} {c[lvl]}" for lvl in LEVELS)


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


def _assess_testgen(r: dict, add) -> None:
    """Issues of a write-tests receipt (new tests against surviving mutants)."""
    decision = (r.get("gate") or {}).get("decision") or {}
    if not decision.get("passed", False):
        add("fatal", "The evidence gate rejected the report", quote="; ".join(decision.get("reasons", [])),
            source="gate")
    for t in r.get("results", []):
        verdict = (t.get("review") or {}).get("verdict", "")
        if t.get("status") == "closed" and verdict == "reject":
            add("fatal", f"Reviewer rejected the new test for mutant {t.get('mutant')}", source="review")
        elif t.get("status") != "closed":
            add("error", f"Mutant {t.get('mutant')}: gap not closed", quote=t.get("note") or "", source="testgen")


def _assess_import(r: dict, add) -> None:
    """Issues of an import receipt (a report made elsewhere, judged by its category's rule)."""
    e = r["envelope"]
    m = e.get("metrics") or {}
    cat = m.get("category") or "?"
    if e.get("status") == "fail":
        add("fatal", f"The imported {cat} report fails its category's rule", quote=e.get("summary") or "",
            source="import")
    elif e.get("status") != "pass":
        add("fatal", f"The imported {cat} report could not be judged (status {e.get('status')})",
            quote=e.get("summary") or "", source="import")
    snap = e.get("snapshot") or {}
    if snap.get("state") == "unknown":
        add("error", "The report does not identify the design it describes, so no check can be tied to it "
                     "(eda-agent import --rtl-manifest)", quote=snap.get("note") or "", source="snapshot")
    missing = [f["path"] for f in (m.get("files") or {}).get("inputs", []) if not f.get("sha256")]
    if missing:
        add("warning", f"{len(missing)} input file(s) the report names were not found here; their sha256 is "
                       "unknown", quote=", ".join(missing), source="import")
    if m.get("approved_waivers"):
        add("warning", f"{m['approved_waivers']} item(s) waived by a person (core/standards.py)", source="import")
    add("warning", f"eda-agent did not run {m.get('tool_name') or 'the tool'}: the result is only as complete as "
                   "the report", source="import")


def _assess_decision(r: dict, issues: list[Issue], add) -> None:
    """A decision made with older code, or one the current approval policy would not have allowed.
    Decisions are final and stay as they are; this only tells the reader."""
    from .human_gate import POLICY
    from ..core.version import label, older
    a = r.get("approval") or {}
    if a.get("status") not in ("approved", "rejected"):
        return
    if older(a.get("code_version"), r.get("code_version")):
        add("warning", "The decision was made with eda-agent code older than the code that wrote this receipt",
            quote=f"decided with {label(a.get('code_version'))}; receipt written by {label(r.get('code_version'))}",
            source="approval")
    if a.get("status") != "approved" or a.get("policy") == POLICY:
        return
    # Without a recorded policy: judge by the levels at the decision if recorded, else by today's levels
    # (not counting what the decision itself or a later change of the receipt caused).
    c = a.get("levels") or counts([i for i in issues if i.source not in ("approval", "receipt")])
    missing = [x for x, needed in (("an override", c["fatal"] and not (a.get("override") or a.get("overrode_gate"))),
                                   ("a reason", (c["fatal"] or c["error"]) and not a.get("reason"))) if needed]
    if missing:
        needs = "--override and a reason" if c["fatal"] else "a reason"
        add("warning", f"The approval does not meet the current approval policy: with {counts_line(c)} it needs "
                       f"{needs}, but the decision records no {' and no '.join(m.split(' ', 1)[1] for m in missing)} "
                       "(it was made under older rules)",
            quote=f"decided by {a.get('by')} at {a.get('at')}; eda-agent code version: {label(a.get('code_version'))}",
            source="approval")


def assess(r: dict) -> list[Issue]:
    """All issues of a receipt, Fatal first, then Error, then Warning."""
    from .human_gate import content_sha256  # human_gate uses assess() for its policy
    from .receipt.data import receipt_kind  # the receipt package uses this module
    out: list[Issue] = []
    def add(level: str, text: str, quote: str = "", source: str = "") -> None:
        out.append(Issue(level, text, shorten(quote, 400), source))
    a = r.get("approval") or {}
    kind = receipt_kind(r)
    if kind in ("testgen", "import"):
        (_assess_testgen if kind == "testgen" else _assess_import)(r, add)
        if kind == "testgen":
            _assess_decision(r, list(out), add)
        return sorted(out, key=lambda i: LEVELS.index(i.level))

    # the run and the gate
    saved = r.get("_source", r)  # the receipt as saved, not a display copy with trace data
    if a.get("receipt_sha256") and a["receipt_sha256"] != content_sha256(saved):
        add("fatal", f"The receipt changed after its decision (sha256 then {a['receipt_sha256'][:12]}…, "
                     f"now {content_sha256(saved)[:12]}…)", source="receipt")
    if r["gate"]["status"] == "rejected":
        add("fatal", "The evidence gate rejected the final report", source="gate")
        last = (r["gate"]["decisions"] or [{}])[-1]
        r10 = [x for x in last.get("reasons", []) if x.startswith("(R11)")]
        if r10:
            add("fatal", "The report cites memory (a lesson or document) that does not exist, was not "
                         "retrieved in this run, or is used as evidence (R11)", quote=r10[0], source="gate")
    mem = r.get("memory") or {}
    if mem.get("used_as_hints"):
        n = len(mem["used_as_hints"])
        c = sum(1 for u in mem["used_as_hints"] if u.get("cited"))
        add("warning", f"{n} lesson(s)/document section(s) were given to the agent as hints ({c} cited in the "
                       "report); hints are not evidence", source="memory")
    if mem.get("compactions"):
        add("warning", f"The context was compacted {len(mem['compactions'])} time(s) (turns replaced by a "
                       "code-built run-state table); the gate and reviewers read the full trace",
            source="memory")
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
        # A perf result that only reports slower cases is not a broken result: a slowdown may be the
        # price of a functional fix. It is an Error (approve with a reason), never Fatal.
        slow_only = tool == "perf" and c["status"] == "fail" and not m.get("failed_cases") and m.get("slower")
        setup = [i for i in m.get("setup_issues") or [] if isinstance(i, dict)]
        not_ready = c["status"] in ("error", "timeout") and any(i.get("blocking", True) for i in setup)
        for i in setup:
            # A setup problem is not a test failure: the stage could not run. The fix comes from rules.
            fix = (f"fix ({'approved [setup] step' if i.get('who') == 'setup_step' else 'a person'}): "
                   f"{i.get('suggestion', '')}; evidence: {i.get('evidence', '')}")
            level = ("warning" if not i.get("blocking", True) else "fatal" if not_ready else "error")
            add(level, f"Environment not ready ({i.get('stage', '?')}): missing {i.get('missing', '?')}",
                quote=fix, source=tool)
        if c["status"] != "pass" and not slow_only and not not_ready:
            add("fatal", f"{tool} ended with status {c['status']}",
                quote=c["summary"].splitlines()[0] if c["summary"] else "", source=tool)
        if tool == "perf":
            comp = m.get("comparison") or {}
            for case in m.get("slower") or []:
                row = comp.get(case, {})
                add("error", f"perf: {case} is slower than the approved baseline ({row.get('delta'):+g} "
                             f"{m.get('key')}, {row.get('delta_pct'):+g}%)",
                    quote="if this is intentional (e.g. the price of a functional fix), approve with --reason and "
                          "record the new baseline: eda-agent perf-baseline <result> --allow-loosen --reason ...",
                    source=tool)
            if m.get("baseline_loosened"):
                add("warning", f"perf: the baseline was loosened on {m.get('baseline_recorded_at')} by "
                               f"{m.get('baseline_by')}: {m.get('baseline_reason')}",
                    quote="; ".join(m["baseline_loosened"]), source=tool)
            if m.get("comparison") is None:
                add("warning", "perf: no approved baseline, nothing was compared", source=tool)
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
    diag = [c for c in r["checks_run"] if c.get("diagnostic") or c["tool"] in ("trace_driver", "trace_load",
                                                                              "waveform_query", "hierarchy")]
    traced = [c for c in diag if c["tool"] in ("trace_driver", "trace_load")]
    if traced:
        locs = list(dict.fromkeys(x for c in traced for x in (c.get("metrics") or {}).get("locations", [])))
        add("warning", "Failure localisation is a diagnosis: a hypothesis about the cause from trace and waveform "
                       "facts, not a proof and not a fix", quote=", ".join(locs[:8]), source="localisation")
    # A check that stopped on a setup problem ran no tool, so it has no version to record.
    missing = [c["tool"] for c in r["checks_run"] if not c.get("tool_versions")
               and not (c["status"] in ("error", "timeout") and (c.get("metrics") or {}).get("setup_issues"))]
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
    out += _group_findings(reviews)
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
    _assess_decision(r, list(out), add)
    return sorted(out, key=lambda i: LEVELS.index(i.level))


def levels_record(r: dict) -> dict:
    """What goes into the receipt's JSON as `levels` (derived; left out of the decision sha256)."""
    issues = assess(r)
    return {"counts": counts(issues), "items": [i.to_dict() for i in issues], "rules": "eda_agent/agent/levels.py"}
