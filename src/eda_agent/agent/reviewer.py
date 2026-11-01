"""Independent review of a finished run, with configurable independence I0-I3.

The program, not the model, decides what the reviewer sees. Everything is read from the
author's trace (runs/<id>.jsonl), so a run can also be reviewed later:

  I0  the author's whole history: task, every turn, every tool call and result, final report
  I1  a fresh conversation that sees only the author's final report
  I2  a fresh conversation that sees the task and the tool results, but none of the author's
      text or conclusions; it may call the same tools to re-check (Agent-as-a-Judge)
  I3  I2 on a different model or provider

The reviewer writes its checklist first, in a turn of its own, before it may look for
findings; the findings then go item by item. A rule-based reviewer (no model, the fixed
rules of the rtl_rules tool plus plain checks of the author's results) can run alongside
any level, or alone.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .loop import Budget, Executor, run_agent
from .model_output import parse_checklist_review as parse_review
from .models import ModelAdapter, ToolSpec

LEVELS = ("I0", "I1", "I2", "I3")
CONTEXT = {
    "I0": "the author's whole history: task, every turn, tool call and result, and the final report",
    "I1": "a fresh conversation with the author's final report only",
    "I2": "a fresh conversation with the task and the tool results of the run, none of the author's "
          "text or conclusions; may re-run the project's tools",
    "I3": "as I2, on a different model than the author",
}
# Tools an I2/I3 reviewer may call: all of them only read the design and write under runs/.
REVIEW_TOOLS = ("lint", "list_regress_cases", "describe_case", "regress", "rtl_rules", "synth", "check_sdc",
                "flow_report", "flow_history")

CHECKLIST_STEP = (
    "Step 1 of 2. Before judging anything, write the checklist you will review against: the "
    "specific things that must hold for the task to be done and for any conclusion to be supported "
    "(which checks must have run, at which evidence level, under which constraints, what is left "
    "uncovered). Write only a section headed 'Checklist' with one '- ' item per line. Do not call "
    "tools and do not give findings yet."
)
FINDINGS_STEP = (
    "Step 2 of 2. Go through your checklist item by item.{tools} Then write a section headed "
    "'Findings' with one item per line in the form '- [high|medium|low|info] what is wrong or "
    "missing — evidence: <tool result, file:line, or none>', and a section headed 'Verdict' with "
    "exactly one of: accept, accept_with_findings, reject. State only what the evidence supports."
)
TOOLS_NOTE = (" You may call the tools to re-check facts yourself; prefer that over trusting a "
              "result you were shown.")

@dataclass
class Review:
    reviewer: str                       # "rules" or the level, e.g. "I2"
    context: str = ""                   # what the reviewer was given
    provider: str = ""
    model: str = ""
    note: str = ""                      # e.g. why an I3 is weaker than it sounds
    checklist: list[str] = field(default_factory=list)
    findings: list[dict] = field(default_factory=list)  # {severity, text}
    verdict: str = ""
    format_ok: bool = True              # checklist came first, findings and verdict parsed
    text: str = ""                      # the reviewer's full answer (checklist turn + findings turn)
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    wall_s: float = 0.0
    stop_reason: str = ""
    trace: str = ""
    prompt: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- reading the author's run

@dataclass
class AuthorRun:
    task: str
    turns: list[dict]          # {text, calls}
    tools: list[dict]          # {name, input, result (parsed dict or raw text)}
    final_text: str
    model: str


def load_trace(path: Path) -> AuthorRun:
    task, model, final = "", "", ""
    turns, tools = [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        ev = json.loads(line)
        if ev["kind"] == "start":
            task, model = ev.get("task", ""), ev.get("model", "")
        elif ev["kind"] == "turn":
            turns.append({"text": ev.get("text", ""), "calls": ev.get("calls", [])})
        elif ev["kind"] == "tool":
            try:
                result = json.loads(ev.get("result", ""))
            except ValueError:
                result = ev.get("result", "")
            tools.append({"name": ev["name"], "input": ev.get("input", {}), "result": result})
        elif ev["kind"] == "end":
            final = ev.get("final_text", "")
    return AuthorRun(task, turns, tools, final, model)


# Envelope fields an I2 reviewer sees: the facts, not the author's reading of them.
_FACT_FIELDS = ("tool", "status", "evidence_level", "claim_scope", "summary", "metrics", "diagnostics",
                "diagnostics_total", "artifacts", "tool_versions", "error_class", "residual_risk")


def _facts(result) -> dict | str:
    if not isinstance(result, dict):
        return result
    if "evidence_level" not in result:
        return result  # list_regress_cases / describe_case output: facts already
    return {k: result[k] for k in _FACT_FIELDS if k in result}


def build_context(level: str, run: AuthorRun) -> str:
    if level == "I1":
        return f"Final report under review:\n\n{run.final_text}"
    if level == "I0":
        lines = [f"Task given to the author:\n{run.task}", "", "The author's whole session, in order:"]
        unused = list(run.tools)
        for i, turn in enumerate(run.turns, 1):
            if turn["text"]:
                lines.append(f"\n[author turn {i}]\n{turn['text']}")
            for call in turn["calls"]:
                # Pair by name and arguments: a call refused by the budget has no result event.
                tool = next((t for t in unused if t["name"] == call["name"] and t["input"] == call["input"]), None)
                lines.append(f"\n[tool call] {call['name']} {json.dumps(call['input'])}")
                if tool is None:
                    lines.append("[tool result] (none: the call was not executed)")
                else:
                    unused.remove(tool)
                    lines.append(f"[tool result] {json.dumps(tool['result'], ensure_ascii=False)}")
        lines += ["", f"The author's final report:\n\n{run.final_text}"]
        return "\n".join(lines)
    lines = [f"Task given to the author:\n{run.task}", "",
             "The tool results of the author's run, in order. The author's own text and conclusions "
             "are withheld on purpose: judge the task against these facts and your own checks."]
    for t in run.tools:
        lines.append(f"\n[{t['name']} {json.dumps(t['input'])}]\n"
                     f"{json.dumps(_facts(t['result']), ensure_ascii=False)}")
    if not run.tools:
        lines.append("\n(the author ran no tools)")
    return "\n".join(lines)


# ---------------------------------------------------------------- the model reviewer

async def model_review(model: ModelAdapter, level: str, run: AuthorRun, tools: list[ToolSpec],
                       execute: Executor, system: str, *, provider: str, author_model: str = "",
                       trace: Path | None = None, budget: Budget | None = None, prompt: str = "") -> Review:
    if level not in LEVELS:
        raise ValueError(f"unknown review level {level!r}; use one of {LEVELS}")
    note = ""
    if level == "I3":
        if model.name == author_model:
            raise ValueError(f"I3 needs a different model than the author's ({author_model})")
        note = (f"I3 here is the author's provider with a different model ({author_model} -> {model.name}); "
                "a different vendor would be more independent (shared training data and blind spots).")
    elif author_model and model.name != author_model:
        note = f"reviewer model {model.name} differs from the author's {author_model}"
    may_call = level in ("I2", "I3")
    review_tools = [t for t in tools if t.name in REVIEW_TOOLS] if may_call else []
    budget = budget or Budget(max_turns=16, max_tool_calls=10, max_gate_rewrites=0)
    context = build_context(level, run)

    first = await run_agent(model, review_tools, execute, f"{context}\n\n{CHECKLIST_STEP}", system,
                            budget, trace=trace, gate=None)
    second = await run_agent(model, review_tools, execute,
                             FINDINGS_STEP.format(tools=TOOLS_NOTE if may_call else ""), system,
                             budget, trace=trace, gate=None, resume=True)
    checklist, findings, verdict, ok = parse_review(first.final_text, second.final_text)
    usage = {k: first.usage.get(k, 0) + second.usage.get(k, 0) for k in {*first.usage, *second.usage}}
    calls = [{"tool": c["tool"], "args": c["args"], "status": (c["result"] or {}).get("status", ""),
              "evidence_level": (c["result"] or {}).get("evidence_level", ""),
              "summary": (c["result"] or {}).get("summary", "")} for c in first.checks + second.checks]
    return Review(
        reviewer=level, context=CONTEXT[level], provider=provider, model=model.name, note=note,
        checklist=checklist, findings=findings, verdict=verdict,
        format_ok=ok and not first.checks,  # no tool calls before the checklist was written
        text=f"{first.final_text}\n\n{second.final_text}".strip(), tool_calls=calls, usage=usage,
        wall_s=round(first.wall_s + second.wall_s, 2), stop_reason=second.stop_reason,
        trace=str(trace or ""), prompt=prompt)


# ---------------------------------------------------------------- the rule-based reviewer

RULES_CHECKLIST = [
    "every tool the author ran finished (no error or timeout)",
    "lint has no errors; regress has no failing case",
    "rtl_rules: no blocking assignment in a clocked block, no non-blocking one in combinational logic",
    "rtl_rules: no width truncation, latch or multiple driver; reset tests match edge and name",
    "synth: no latches, timing met under the SDC on every corner",
    "check_sdc: every endpoint constrained, the clock on the configured port",
    "all timing results of the run were measured under one SDC (same sha256)",
    "flow_report / flow_run: every signoff rule passes; the run is of the current top, RTL and SDC",
]


def rules_review(run: AuthorRun, rtl_rules: dict | None) -> Review:
    """No model: fixed checks of the author's results plus the rtl_rules tool's findings."""
    findings: list[dict] = []

    def add(severity: str, text: str) -> None:
        findings.append({"severity": severity, "text": text})

    results = [t for t in run.tools if isinstance(t["result"], dict)]
    # Only the latest result of each check counts: a run may fix a failure and re-run it.
    # regress is keyed by its cases too, since different case sets are different checks.
    latest: dict[tuple, dict] = {}
    for t in results:
        key = (t["name"], json.dumps(t["input"], sort_keys=True) if t["name"] == "regress" else "")
        latest[key] = t
    current = [t for t in results if any(t is v for v in latest.values())]
    if superseded := len(results) - len(current):
        add("info", f"{superseded} earlier result(s) were superseded by a later run of the same check; "
                    "only the latest of each is judged")
    for t in current:
        r, name = t["result"], t["name"]
        m = r.get("metrics") or {}
        if r.get("status") in ("error", "timeout"):
            add("low", f"{name} {json.dumps(t['input'])} ended with {r['status']} "
                       f"({r.get('error_class')}): {r.get('summary', '')[:160]}")
        elif name == "lint" and m.get("errors"):
            add("high", f"lint reports {m['errors']} error(s)")
        elif name == "regress" and r.get("status") == "fail":
            add("high", f"regress failed: {r.get('summary', '')}")
        elif name == "synth":
            if m.get("latches"):
                add("medium", f"synth inferred {m['latches']} latch(es)")
            if m.get("timing_met") is False:
                add("high", f"synth: timing not met under SDC sha256 {str(m.get('sdc_sha256'))[:12]}: "
                            f"setup WNS {m.get('wns')} (corner {m.get('worst_corner')}), hold WNS {m.get('hold_wns')}")
        elif name == "check_sdc" and r.get("status") == "fail":
            add("medium", f"check_sdc: {r.get('summary', '')}")
        elif name == "perf":
            for case in m.get("slower") or []:
                row = (m.get("comparison") or {}).get(case, {})
                add("high", f"perf: {case} is slower than the approved baseline: {row.get(m.get('key'))} vs "
                            f"{row.get('reference')} {m.get('key')} ({row.get('delta_pct')}%)")
            if m.get("comparison") is None:
                add("info", "perf ran without an approved baseline: its numbers were not compared with anything")
        elif name == "mutate" and (m.get("survived") or m.get("perf_killed")):
            add("info", f"mutate {m.get('file')}: {m.get('survived', 0)} mutant(s) survive the chosen cases"
                        + (f", {m['perf_killed']} caught only by a cycle-count change" if m.get("perf_killed") else "")
                        + " (test gaps, not design bugs)")
        elif name in ("flow_report", "flow_run"):
            for rule, v in (m.get("signoff") or {}).items():
                if v.get("outcome") == "fail":
                    add("high", f"{name} {m.get('tag')}: signoff rule {rule} fails: {v.get('detail')}")
            for d in r.get("diagnostics") or []:
                if d.get("code") in ("OTHER_TOP", "SDC_UNKNOWN", "INPUTS_CHANGED", "CROSS_CHECK"):
                    add("medium", f"{name} {m.get('tag')}: {d.get('msg', '')[:200]}")
    hashes ={(t["result"].get("metrics") or {}).get("sdc_sha256") for t in results
              if (t["result"].get("metrics") or {}).get("sdc_sha256") not in (None, "missing")}
    if len(hashes) > 1:  # allowed (the gate voids the older ones), but a person should know
        add("info", f"timing results of this run were measured under {len(hashes)} different SDCs; "
                    "only those under the latest one count")
        latest_sha = next((m.get("sdc_sha256") for t in reversed(results)
                           if (m := t["result"].get("metrics") or {}).get("sdc_sha256")), None)
        if any(t["name"] == "synth" and (t["result"].get("metrics") or {}).get("sdc_sha256") != latest_sha
               for t in current):
            add("high", "the latest synth result was measured under an SDC that changed afterwards; "
                        "no valid timing result remains")
    if rtl_rules is None:
        add("info", "rtl_rules did not run")
    elif rtl_rules.get("status") in ("error", "timeout"):
        add("low", f"rtl_rules ended with {rtl_rules['status']}: {rtl_rules.get('summary', '')}")
    else:
        groups: dict[tuple[str, str], list[dict]] = {}
        for d in rtl_rules.get("diagnostics", []):
            groups.setdefault((d["code"], d["severity"]), []).append(d)
        counts = (rtl_rules.get("metrics") or {}).get("by_rule", {})
        for (code, severity), ds in groups.items():
            where = ", ".join(f"{d['file']}:{d['line']}" for d in ds[:3])
            n = counts.get(code, len(ds))
            add("high" if severity == "error" else "medium",
                f"{code.lower()} x{n}: {ds[0]['msg'][:140]} (at {where}{', ...' if n > 3 else ''})")
    verdict = ("reject" if any(f["severity"] == "high" for f in findings)
               else "accept_with_findings" if any(f["severity"] in ("medium", "low") for f in findings)
               else "accept")
    return Review(reviewer="rules", context="the author's tool results and the rtl_rules tool; no model",
                  checklist=list(RULES_CHECKLIST), findings=findings, verdict=verdict,
                  tool_calls=[] if rtl_rules is None else [{
                      "tool": "rtl_rules", "args": {}, "status": rtl_rules.get("status", ""),
                      "evidence_level": rtl_rules.get("evidence_level", ""),
                      "summary": rtl_rules.get("summary", "")}])
