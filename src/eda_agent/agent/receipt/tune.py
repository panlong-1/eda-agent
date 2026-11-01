"""Tune receipts (eda-agent tune, plan §0.15 step 3): the proposals, the knob-policy check, the runs,
the comparison with the best run, the lesson updates and the config patch that waits for a person.

All four layers of one receipt kind in one module: data (tune_receipt), issues (assess_tune, used by
levels.assess), the summary, the tables and the Markdown details. Nothing here is written by a model.
"""
from __future__ import annotations

import json

from ...core.numbers import fmt_num
from ...core.version import code_version
from ..levels import counts
from .tables import Table, _Cost, _gate_rows, cell, fmt_ns, level_cell

RESIDUAL = [
    "single run per setting: a knob's effect cannot be told apart from run-to-run noise (placement and "
    "routing react chaotically to any change); an identical config was reproduced exactly once "
    "(m4_e5_1 = full_p10_fwd), which says nothing about the noise a changed knob brings",
    "the verdict reads one goal metric (slow-corner setup WNS) and the signoff counts; area, power and runtime "
    "are listed but not judged",
    "post-route STA, DRC, LVS and antenna only (E5-partial): no gate-level simulation of the routed netlist, "
    "vectorless power",
    "the patch is for this design, this PDK, this clock period and this flow version; it is not a general rule",
]


def tune_receipt(*, run_id: str, project: str, provider: str, model: str, prompt: str, body: dict, report: str,
                 gate: dict, patch_files: dict, usage: dict, wall_s: float) -> dict:
    ran = [a for a in body["attempts"] if a["decision"] == "ran"]
    return {
        "kind": "tune", "run_id": run_id, "project": project, "provider": provider, "model": model,
        "prompt": prompt, "code_version": code_version(), "goal": body["goal"], "metric": body["metric"],
        "budget_runs": body["budget_runs"], "runs_used": body["runs_used"], "start_best": body["start_best"],
        "best": body["best"], "best_patch": body["best_patch"], "attempts": body["attempts"],
        "patch_files": patch_files, "final_claims": report, "gate": {"decision": gate},
        "residual_risk": RESIDUAL, "usage": usage, "wall_s": wall_s,
        "applied_to_design": False,
        "status": "done" if gate.get("passed") and (ran or body["attempts"]) else "failed",
    }


def assess_tune(r: dict, add) -> None:
    """Issues of a tune receipt."""
    decision = (r.get("gate") or {}).get("decision") or {}
    if not decision.get("passed", False):
        add("fatal", "The evidence gate rejected the tuning report", quote="; ".join(decision.get("reasons", [])),
            source="gate")
    for a in r.get("attempts", []):
        fat = [f for f in a.get("policy", []) if f["level"] == "fatal"]
        if fat:
            add("fatal", f"Attempt {a['n']} proposed a forbidden change (rejected by core/knobs.py, not run)",
                quote="; ".join(f"{f['key']}={f['value']!r}: {f['reason']}" for f in fat), source="knobs")
        elif a["decision"] == "rejected":
            add("warning", f"Attempt {a['n']} was rejected before running",
                quote="; ".join([f"{f['key']}: {f['reason']}" for f in a.get("policy", [])] + a.get("reasons", [])),
                source="tune")
        if a["decision"] == "ran":
            if a.get("verdict") == "no_result":
                add("error", f"Attempt {a['n']}: run {a['run'].get('tag')} gave no post-route result",
                    quote=a["run"].get("summary") or "", source="flow_run")
            elif a.get("verdict") == "signoff_worse":
                add("error", f"Attempt {a['n']}: a signoff check got worse", quote=a.get("verdict_detail", ""),
                    source="flow_run")
            lu = a.get("lesson_update") or {}
            if lu.get("kind") in ("verify", "refute"):
                add("warning", f"Lesson {lu['lesson']} was {'verified' if lu['kind'] == 'verify' else 'refuted'} by "
                               f"one run ({a['run'].get('tag')}); a person may void the entry ({lu['entry']})",
                    source="lessons")
    if r.get("runs_used"):
        add("warning", "Single run per setting: the effect cannot be told apart from run-to-run noise",
            source="tune")
    if (r.get("patch_files") or {}).get("diff"):
        add("warning", "The config patch is NOT applied to the design: a person applies it after approval",
            quote=(r.get("patch_files") or {}).get("diff", ""), source="tune")


def tune_tables(r: dict) -> list[Table]:
    att = Table("Proposals", ["#", "source", "patch", "tests lesson", "knob policy", "other checks", "decision"])
    runs = Table("Runs against the best run (ns; negative = violation)",
                 ["run", "patch (all knobs)", "slow setup WNS", "slow TNS", "slow viol.", "hold WNS", "DRC", "LVS",
                  "antenna", "die um2", "verdict", "lesson update"])
    bw = Table("Better / worse (flow_report comparison with the best run at the time)", ["run", "better", "worse",
                                                                                        "not comparable"])
    s = r.get("start_best") or {}
    sn = s.get("numbers") or {}
    runs.rows.append([f"{s.get('run')} (start)", "—", fmt_ns(sn.get("setup_wns_slow")), fmt_ns(sn.get("setup_tns_slow")),
                      fmt_num(sn.get("setup_violations_slow")), fmt_ns(sn.get("hold_wns")), fmt_num(sn.get("drc")),
                      fmt_num(sn.get("lvs_errors")), fmt_num(sn.get("antenna_nets")), fmt_num(sn.get("die_area_um2")),
                      "—", "—"])
    for a in r.get("attempts", []):
        p = a.get("proposal") or {}
        pol = "; ".join(f"{f['level'].upper()} {f['key']}: {f['reason']}" for f in a.get("policy", [])) or "ok"
        att.rows.append([str(a["n"]), a["source"], json.dumps(p.get("patch"), ensure_ascii=False),
                         p.get("tests_lesson") or a.get("hypothesis_lesson") or "—",
                         level_cell("fatal") if any(f["level"] == "fatal" for f in a.get("policy", []))
                         else cell(pol, "bad" if a.get("policy") else "ok"),
                         "; ".join(a.get("reasons", [])) or "ok",
                         cell(a["decision"], "ok" if a["decision"] == "ran" else "bad")])
        if a["decision"] == "ran":
            n = a["run"].get("numbers") or {}
            lu = a.get("lesson_update") or {}
            runs.rows.append([a["run"].get("tag"), json.dumps(a["run"].get("patch")), fmt_ns(n.get("setup_wns_slow")),
                              fmt_ns(n.get("setup_tns_slow")), fmt_num(n.get("setup_violations_slow")),
                              fmt_ns(n.get("hold_wns")), fmt_num(n.get("drc")), fmt_num(n.get("lvs_errors")),
                              fmt_num(n.get("antenna_nets")), fmt_num(n.get("die_area_um2")),
                              cell(a.get("verdict"), "ok" if a.get("verdict") == "improved" else "bad"),
                              f"{lu.get('kind')} {lu.get('lesson')} ({lu.get('entry', '-')})"])
            c = a.get("comparison") or {}
            bw.rows.append([a["run"].get("tag"), "; ".join(c.get("better") or []) or "—",
                            "; ".join(c.get("worse") or []) or "—", "; ".join(c.get("not_comparable") or []) or "—"])
    patch = Table("Config patch (awaiting approval; not applied)", ["item", "value"])
    pf = r.get("patch_files") or {}
    patch.rows = [["knobs (-c KEY=VALUE)", json.dumps(r.get("best_patch") or {})],
                  ["best run", (r.get("best") or {}).get("run") or "—"],
                  ["diff of the design's config", cell(pf.get("diff") or "none (no run improved on the start)", "ref")],
                  ["overrides", cell(pf.get("overrides") or "—", "ref")]]
    cost = _Cost()
    cost.add("proposer", f"{r['provider']} / {r['model']}", "", "", r.get("usage"), r.get("wall_s"))
    decision = (r.get("gate") or {}).get("decision") or {}
    return [att, runs, bw, patch, _gate_rows([decision] if decision else []), cost.table()]


def tune_details_md(r: dict) -> str:
    a = r.get("approval") or {}
    lines = [f"# Receipt {r['run_id']}: backend tuning", "",
             f"- project: {r['project']} · goal: {r['goal']} (metric {r['metric']}) · budget {r['budget_runs']} "
             f"flow run(s), used {r['runs_used']} · proposer: {r['provider']}/{r['model']} (prompt {r['prompt']})",
             f"- status: **{r['status']}**" + (f" · approval: {a.get('status')}"
                                              + (f" by {a['by']} at {a['at']}" if a.get("by") else "") if a else ""),
             f"- applied to the design: **{'yes' if r.get('applied_to_design') else 'no'}** (a person applies the patch)",
             f"- evidence gate: {'passed' if r['gate']['decision'].get('passed') else 'REJECTED'}"
             + "".join(f"\n  - {x}" for x in r["gate"]["decision"].get("reasons", [])),
             f"- tokens: {r['usage']} · wall time: {r['wall_s']} s", "", "## Attempts", ""]
    for x in r["attempts"]:
        p = x.get("proposal") or {}
        lines += [f"### Attempt {x['n']} (round {x['round']}, {x['source']}): {x['decision']}", "",
                  f"- patch: `{json.dumps(p.get('patch'))}`",
                  f"- tests lesson: {p.get('tests_lesson') or x.get('hypothesis_lesson') or '—'}",
                  f"- hypothesis: {p.get('hypothesis') or '—'}", f"- reasoning: {p.get('reasoning') or '—'}"]
        for f in x.get("policy", []):
            lines.append(f"- knob policy **{f['level'].upper()}**: `{f['key']}={f['value']!r}`: {f['reason']}")
        lines += [f"- rejected: {y}" for y in x.get("reasons", [])]
        if x["decision"] == "ran":
            lines += [f"- run: `{x['run'].get('tag')}` ({x['run'].get('status')}, {x['run'].get('evidence_level')}) "
                      f"at `{x['run'].get('run_dir')}`: {x['run'].get('summary')}",
                      f"- verdict (code): **{x['verdict']}**: {x['verdict_detail']}",
                      f"- better: {'; '.join(x['comparison'].get('better') or []) or '—'}",
                      f"- worse: {'; '.join(x['comparison'].get('worse') or []) or '—'}",
                      f"- lesson update: {json.dumps(x.get('lesson_update'))}"]
        lines.append("")
    pf = r.get("patch_files") or {}
    lines += ["## Config patch", "", f"- knobs: `{json.dumps(r.get('best_patch') or {})}`"]
    if pf.get("diff_text"):
        lines += ["", "```diff", pf["diff_text"].rstrip(), "```", "",
                  f"Apply after approval, in the design repository: `git apply {pf.get('diff')}`; eda-agent never writes "
                  "there."]
    else:
        lines.append("- no run improved on the start: nothing to apply")
    lines += ["", "## Report (checked by the evidence gate)", "", r["final_claims"], "",
              "## Residual risk", ""] + [f"- {x}" for x in r["residual_risk"]] + [""]
    return "\n".join(lines)


def summarize_tune(r: dict):
    from ..levels import assess
    from .summary import RUN_CLASS, RUN_STATUS, Summary, _check, _decision, _evidence, _headline, _notes, approve_needs
    a = r.get("approval") or {}
    issues = assess(r)
    decision = (r.get("gate") or {}).get("decision") or {}
    gate_status = "passed" if decision.get("passed") else "rejected"
    s = Summary(run_id=r["run_id"], task=f"Tune the back-end flow: {r['goal']} (budget {r['budget_runs']} run(s))",
                run_line=f"{r['project']} · {r['provider']} / {r['model']} · prompt {r['prompt']}",
                status=RUN_STATUS.get(r["status"], r["status"]), decision="", issues=issues,
                counts=counts(issues), status_class=RUN_CLASS.get(r["status"], "muted"), gate_status=gate_status)
    s.needs = approve_needs(s.counts)
    _decision(s, r, a)
    for x in r.get("attempts", []):
        if x["decision"] == "ran":
            s.checks.append(_check("flow_run", {"tag": x["run"].get("tag")}, x["run"].get("status") or "",
                                   x["run"].get("evidence_level") or "", x["run"].get("summary") or ""))
    _evidence(s)
    s.not_checked += list(r.get("residual_risk") or [])
    s.gate = f"{gate_status}; the report is written by code from the flow results"
    s.gate_reasons = [] if decision.get("passed") else list(decision.get("reasons", []))
    s.no_review = "no independent review: the verdicts are computed by code from the flow results"
    s.notes = _notes(a)
    ran = [x for x in r.get("attempts", []) if x["decision"] == "ran"]
    rej = len(r.get("attempts", [])) - len(ran)
    _headline(s, [f"{len(ran)} run(s), {rej} proposal(s) rejected before running",
                  "verdicts: " + (", ".join(x.get("verdict", "") for x in ran) or "none"), f"gate {gate_status}"])
    return s
