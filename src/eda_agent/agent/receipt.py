"""Run receipt: what was asked, what was checked, what was not, and what was claimed.

Written at the end of every run as runs/<id>.receipt.json (for tools) and, for a human
reviewer, runs/<id>.receipt.md and runs/<id>.receipt.html: a review summary on top
(receipt_view.py), the data tables, the full receipt below. Everything in it is taken from the
run report and the tool envelopes; nothing is re-derived by a model. The .json also carries
`levels`: the Fatal / Error / Warning issues (levels.py), derived from the rest.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..core.envelope import EVIDENCE_LEVELS
from .gate import described_cases, executed_cases, split_residual, timing_results
from .loop import RunReport
from . import receipt_lineage
from .levels import levels_record
from .receipt_tables import with_trace_data
from .receipt_view import (render_data_md, render_html, render_narrative_md, render_summary_md, static_linker,
                           summarize)
from .sources import Sources, project_roots

MAX_DIAGNOSTICS = 50  # per check; the count of all of them is kept in diagnostics_total


def build_receipt(report: RunReport, *, run_id: str, task: str, provider: str, project: str,
                  all_cases: list[str], trace: str = "", prompt: str = "", sdc_origin: str = "",
                  project_root: str = "") -> dict:
    envelopes = [c for c in report.checks if c["result"] and c["result"].get("evidence_level") in EVIDENCE_LEVELS]
    others = [c for c in report.checks if all(c is not e for e in envelopes)]
    # Same definitions as gate rule R4, so the receipt and the gate never disagree.
    results = [c["result"] for c in report.checks if c["result"]]
    described = described_cases(results)
    executed = executed_cases(results)
    claims, residual = split_residual(report.final_text)
    tool_risks = sorted({r for c in envelopes for r in c["result"].get("residual_risk", [])})
    # Same definition as gate rule R5: timing under an SDC other than the latest one is void.
    _, void = timing_results(results)
    if not report.gate:
        gate_status = "not_run"
    else:
        gate_status = "passed" if report.gate[-1]["passed"] else "rejected"
    return {
        "run_id": run_id,
        "task": task,
        "provider": provider,
        "model": report.model,
        "prompt": prompt,
        "project": project,
        **({"project_root": project_root} if project_root else {}),
        "stop_reason": report.stop_reason,
        "status": report.status,
        "gate": {"status": gate_status, "rewrites": max(0, len(report.gate) - 1), "decisions": report.gate},
        "checks_run": [{
            "tool": c["tool"],
            "args": c["args"],
            "status": c["result"].get("status"),
            "evidence_level": c["result"].get("evidence_level"),
            "summary": c["result"].get("summary", ""),
            "claim_scope": c["result"].get("claim_scope", ""),
            "tool_versions": c["result"].get("tool_versions", {}),
            "artifacts": [{"path": a.get("path"), "sha256": a.get("sha256")}
                          for a in c["result"].get("artifacts", [])],
            "duration_s": c.get("duration_s"),
            "metrics": c["result"].get("metrics") or {},
            "diagnostics": (c["result"].get("diagnostics") or [])[:MAX_DIAGNOSTICS],
            "diagnostics_total": c["result"].get("diagnostics_total", 0),
            **_timing(c["result"], void),
            **_perf(c["result"]),
            **_flow(c["result"]),
            **receipt_lineage.snapshot_of(c["result"]), **receipt_lineage.lineage_of(c["result"]),
        } for c in envelopes],
        "consistency": receipt_lineage.receipt_consistency([c["result"] for c in envelopes]),
        "other_tool_calls": [{"tool": c["tool"], "args": c["args"],
                              "status": (c["result"] or {}).get("status", "")} for c in others],
        "not_run": {
            "described_but_not_run": sorted(described - executed),
            "cases_not_run": [n for n in all_cases if n not in executed],
            "lint_run": any(c["tool"] == "lint" for c in envelopes),
        },
        "final_claims": claims.rstrip().rstrip("#*").strip(),  # without the residual heading's "###"
        "residual_risk": {"report": residual.strip(), "from_tools": tool_risks},
        "usage": report.usage,
        "turns": report.turns,
        "tool_calls": report.tool_calls,
        "tool_errors": report.tool_errors,
        "wall_s": report.wall_s,
        "trace": trace,
        **({"sdc_origin": sdc_origin} if sdc_origin else {}),
        "case_sources": {k["name"]: k["source"] for x in results if x.get("tool") == "describe_case"
                         for k in x.get("cases", []) if isinstance(k, dict) and k.get("name") and k.get("source")},
    }


def _timing(result: dict, void: list[dict]) -> dict:
    """The SDC hash and timing numbers of a synth/check_sdc result; nothing for other tools."""
    m = result.get("metrics") or {}
    if not m.get("sdc_sha256"):
        return {}
    out = {"sdc": m.get("sdc"), "sdc_sha256": m["sdc_sha256"]}
    if "wns" in m:
        out["timing"] = {k: m.get(k) for k in ("clock_period", "wns", "tns", "hold_wns", "worst_corner",
                                               "timing_met", "cells", "area", "latches")}
        out["timing"]["void"] = any(result is v for v in void)
    return out


def _perf(result: dict) -> dict:
    """The per-case table of a perf result (and the baseline it was compared with); nothing else."""
    if result.get("tool") != "perf":
        return {}
    m = result.get("metrics") or {}
    return {"perf": {k: m.get(k) for k in ("key", "cases", "comparison", "slower", "faster",
                                           "baseline_sha256", "baseline_by", "baseline_recorded_at",
                                           "baseline_reason", "baseline_loosened")}}


def _flow(result: dict) -> dict:
    """Signoff rules of a flow_report/flow_run result, or the run table summary, knob effects and
    lesson verdicts of a flow_history result; nothing for other tools."""
    m = result.get("metrics") or {}
    if result.get("tool") in ("flow_report", "flow_run"):
        cmp = (m.get("comparison") or {}).get("baseline") or {}
        return {"flow": {"run": m.get("tag"), "backend": m.get("backend"), "experience_table": m.get("experience_table"),
                         "signoff_level": m.get("signoff_level"), "e5_parts": m.get("e5_parts"),
                         "signoff": {k: v.get("outcome") for k, v in (m.get("signoff") or {}).items()},
                         "baseline": cmp.get("baseline"), "baseline_table": cmp.get("baseline_table"),
                         "better": cmp.get("better"), "worse": cmp.get("worse")}}
    if result.get("tool") == "flow_history":
        return {"flow_history": {
            "design": m.get("design"), "backend": m.get("backend"),
            "tables": {name: {
                "provenance": t.get("provenance"), "baseline": t.get("baseline"), "stages": t.get("stages"),
                "runs": [{k: r.get(k) for k in ("run", "table", "stage", "started", "clock_period")}
                         for r in t.get("runs") or []],
                "knobs": {k: {"verdict": v["verdict"], "pairs": [p.get("summary") or p for p in v["pairs"]]}
                          for k, v in (t.get("knob_effects") or {}).items()}}
                for name, t in (m.get("tables") or {}).items()},
            "lessons": [{k: les.get(k) for k in ("key", "verdict", "why", "source_table", "status")}
                        for les in m.get("lessons") or []],
            "hypotheses_from_other_designs": m.get("hypotheses_from_other_designs") or []}}
    return {}


def _cell(text: str) -> str:
    """A Markdown table cell: '|' escaped (GFM honours it inside code spans too), one line."""
    return " ".join(str(text).split()).replace("|", "\\|")


def render_markdown(r: dict) -> str:
    """The review summary, the model-written summary if there is one, the data tables; then the
    full receipt (details) and the long lists."""
    data, more = render_data_md(r)
    narrative = render_narrative_md(r)
    return (render_summary_md(summarize(r)) + ("\n" + narrative if narrative else "") + "\n" + data
            + "\n---\n\n" + render_details(r) + ("\n" + more if more else ""))


def render_details(r: dict) -> str:
    gate = r["gate"]
    out = [
        f"# Run receipt {r['run_id']}",
        "",
        f"- **Task:** {r['task']}",
        f"- **Project:** {r['project']}  ·  **Model:** {r['provider']} / {r['model']}"
        f"  ·  **Prompt:** {r.get('prompt') or 'n/a'}",
        f"- **Stop:** {r['stop_reason']}  ·  **Status:** {r['status']}"
        + (f" (agent: {r['agent_status']})" if r.get("agent_status") else "") + "  ·  "
        f"**Gate:** {gate['status']} ({gate['rewrites']} rewrite(s))",
        f"- **Cost:** {r['turns']} turns, {r['tool_calls']} tool calls ({r['tool_errors']} errors), "
        f"{r['usage'].get('input_tokens', 0)} in / {r['usage'].get('output_tokens', 0)} out tokens, "
        f"{r['wall_s']} s",
        *([f"- **Preset:** {r['preset']}"] if r.get("preset") else []),
        *([f"- **SDC origin:** {r['sdc_origin']}"] if r.get("sdc_origin") else []),
        "",
        "## Checks run",
        "",
        "| tool | args | status | evidence | summary |",
        "|---|---|---|---|---|",
    ]
    for c in r["checks_run"]:
        summary = c["summary"].splitlines()[0] if c["summary"] else ""
        out.append(f"| {_cell(c['tool'])} | `{_cell(json.dumps(c['args']))}` | {_cell(c['status'])} "
                   f"| {_cell(c['evidence_level'])} | {_cell(summary)} |")
    for c in r["checks_run"]:
        out += ["", f"**{c['tool']} {json.dumps(c['args'])}** claim scope: {c['claim_scope']}"]
        out += [f"- artifact `{a['path']}` sha256 `{a['sha256']}`" for a in c["artifacts"]]
        out += [f"- {name}: {version}" for name, version in c.get("tool_versions", {}).items()]
    timed = [c for c in r["checks_run"] if c.get("timing")]
    if timed:
        out += ["", "## Timing estimates (E4, pre-PnR)", "",
                "| tool | clock | setup WNS | TNS | hold WNS | worst corner | met | SDC sha256 | void |",
                "|---|---|---|---|---|---|---|---|---|"]
        for c in timed:
            t = c["timing"]
            out.append(f"| {c['tool']} | {t['clock_period']} ns | {t['wns']} | {t['tns']} | {t['hold_wns']} "
                       f"| {t['worst_corner']} | {'yes' if t['timing_met'] else 'no'} | `{c['sdc_sha256'][:16]}…` "
                       f"| {'VOID (SDC changed later in the run)' if t['void'] else 'no'} |")
    out += receipt_lineage.render(r)
    nr = r["not_run"]
    out += ["", "## Not run", "",
            f"- lint run: {'yes' if nr['lint_run'] else 'no'}",
            f"- described but not run: {', '.join(nr['described_but_not_run']) or 'none'}",
            f"- cases in the list not run ({len(nr['cases_not_run'])}): "
            f"{', '.join(nr['cases_not_run']) or 'none'}"]
    if gate["decisions"]:
        out += ["", "## Gate decisions", ""]
        for i, d in enumerate(gate["decisions"], 1):
            verdict = "pass" if d["passed"] else "reject"
            out.append(f"{i}. {verdict} (best passing evidence {d['max_level'] or 'none'})")
            out += [f"   - {reason}" for reason in d["reasons"]]
    out += ["", "## Final claims", "", r["final_claims"] or "(none)",
            "", "## Residual risk", "", r["residual_risk"]["report"] or "(none stated in the report)",
            "", "From the tools:"]
    out += [f"- {x}" for x in r["residual_risk"]["from_tools"]] or ["- (none)"]
    for review in r.get("reviews", []):
        out += _render_review(review)
    if r.get("approval"):
        a = r["approval"]
        out += ["", "## Human approval", "",
                f"- **Step:** {a.get('step')}  ·  **Status:** {a['status']}",
                f"- requested: {a.get('requested_at')} ({a.get('why')})"]
        if a["status"] != "pending":
            out += [f"- decided by **{a['by']}** at {a['at']}" + (f": {a['reason']}" if a.get("reason") else ""),
                    f"- receipt content sha256 at decision: `{a['receipt_sha256']}`"]
            if a.get("overrode_gate"):
                out.append("- this approval overrode the evidence gate's rejection")
        else:
            out.append(f"- decide with `eda-agent approve {r['run_id']}` or "
                       f"`eda-agent reject {r['run_id']} --reason \"...\"`")
    if r["trace"]:
        out += ["", f"Trace: `{r['trace']}`"]
    return "\n".join(out) + "\n"


def _render_review(v: dict) -> list[str]:
    who = "rule-based (no model)" if v["reviewer"] == "rules" else f"{v['reviewer']}, {v['provider']} / {v['model']}"
    out = ["", f"## Review: {who}", "", f"- **Saw:** {v['context']}",
           f"- **Verdict:** {v['verdict'] or '(none given)'}"
           + ("" if v.get("format_ok", True) else "  ·  format not followed (checklist first, findings, verdict)")]
    if v.get("note"):
        out.append(f"- **Note:** {v['note']}")
    if v.get("tool_calls"):
        out.append("- **Re-ran:** " + "; ".join(f"{c['tool']} {json.dumps(c['args'])} -> {c['status']} "
                                                f"{c['evidence_level']}" for c in v["tool_calls"]))
    out += ["", "Checklist (written before the findings):", ""]
    out += [f"- {x}" for x in v["checklist"]] or ["- (none)"]
    out += ["", "Findings:", ""]
    out += [f"- [{f['severity']}] {f['text']}" for f in v["findings"]] or ["- (none)"]
    return out


def write_receipt(receipt: dict, out_dir: Path, base: Path | None = None) -> tuple[Path, Path]:
    """runs/<id>.receipt.json (with its `levels` brought up to date), plus the rendered .md and
    .html next to it. `base`: where the receipt's trace may be found (default out_dir)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    js = out_dir / f"{receipt['run_id']}.receipt.json"
    if receipt.get("kind") == "testgen":
        # eda-agent write-tests keeps its own Markdown layout for now (no levels, no HTML page yet).
        from .testgen import render_markdown as render_testgen
        js.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        md = out_dir / f"{receipt['run_id']}.receipt.md"
        md.write_text(render_testgen(receipt), encoding="utf-8")
        return js, md
    receipt["levels"] = levels_record(with_trace_data(receipt, base or out_dir))
    js.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md, _ = render_files(receipt, out_dir, base=base)
    return js, md


def render_files(receipt: dict, out_dir: Path, *, base: Path | None = None) -> tuple[Path, Path]:
    """(Re-)write runs/<id>.receipt.md and .html from the receipt data; the .json is not
    touched. `base`: where the receipt's trace may be found (default out_dir)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    view = with_trace_data(receipt, base or out_dir)
    name = f"{receipt['run_id']}.receipt"
    md, page = out_dir / f"{name}.md", out_dir / f"{name}.html"
    md.write_text(render_markdown(view), encoding="utf-8")
    runs_dir = base or out_dir
    sources = Sources(runs_dir, project_roots([runs_dir.parent / "projects"]))
    page.write_text(render_html(view, link=static_linker(view, sources)), encoding="utf-8")
    return md, page
