"""Layer 1, data: what a receipt records, built from the run report and the tool envelopes.

Three kinds of receipt share the tables and pages built on top of this layer:

    run       an agent run (eda-agent run / check): runs/<id>.receipt.json, built by build_receipt
    testgen   new tests against surviving mutants (eda-agent write-tests): testgen_receipt
    import    a report made elsewhere (eda-agent import): the record tools/importing.py writes

Nothing here is re-derived by a model. Receipts written before the per-check numbers were kept
get them, for display only, from the run's own trace (`with_trace_data`); the .json is never
changed by that.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from ...core.envelope import EVIDENCE_LEVELS
from ...core.version import code_version
from ...core.snapshot import consistency
from ..gate import described_cases, executed_cases, split_residual, timing_results
from ..loop import RunReport

MAX_DIAGNOSTICS = 50  # per check; the count of all of them is kept in diagnostics_total


def receipt_kind(r: dict) -> str:
    """run | testgen | import. Import records carry no `kind` field (the imported table keeps them
    as they are), so they are told by their envelope."""
    return r.get("kind") or ("import" if "envelope" in r and "imported_at" in r else "run")


def receipt_id(r: dict) -> str:
    """The name the receipt's files have: runs/<id>.receipt.json (imports: <design>_<import id>)."""
    return f"{r['design']}_{r['id']}" if receipt_kind(r) == "import" else r["run_id"]


# ---------------------------------------------------------------- run receipts

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
        "code_version": code_version(),
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
            **({"diagnostic": True} if c["result"].get("diagnostic") else {}),
            "diagnostics": (c["result"].get("diagnostics") or [])[:MAX_DIAGNOSTICS],
            "diagnostics_total": c["result"].get("diagnostics_total", 0),
            **_timing(c["result"], void),
            **_perf(c["result"]),
            **_flow(c["result"]),
            **snapshot_of(c["result"]), **lineage_of(c["result"]),
        } for c in envelopes],
        "consistency": receipt_consistency([c["result"] for c in envelopes]),
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


# Plan §0.18: which design snapshot each check describes, whether the checks used together are one
# design, and each flow run's lineage (stage, inputs, outputs, sha256, breaks).

def snapshot_of(result: dict) -> dict:
    s = result.get("snapshot") or {}
    return {"snapshot": {k: s.get(k) for k in ("id", "design_id", "state", "note")}} if s else {}


def lineage_of(result: dict) -> dict:
    lin = (result.get("metrics") or {}).get("lineage")
    return {"lineage": lin} if lin else {}


def receipt_consistency(envelopes: list[dict]) -> dict:
    """Facts about all checks of the run taken together (same design, SDC, PDK, corners, lineage)."""
    return consistency([e for e in envelopes if e.get("snapshot") or (e.get("metrics") or {}).get("lineage")])


# ---------------------------------------------------------------- write-tests receipts

def testgen_receipt(*, run_id: str, project: str, provider: str, model: str, prompt: str, review_prompt: str,
                    mutate_run: str, targets: list[int], results: list[dict], gate: dict, usage: dict,
                    wall_s: float, closed: bool) -> dict:
    """The receipt of one write-tests run: per mutant what was tried, the score_tests results, the
    test review, and the report the code wrote from them with the gate's decision on it."""
    return {
        "kind": "testgen", "run_id": run_id, "project": project, "provider": provider,
        "model": model, "prompt": prompt, "review_prompt": review_prompt, "code_version": code_version(),
        "mutate_run": mutate_run, "targets": targets,
        "results": results, "gate": gate, "final_claims": gate["report"],
        "usage": usage, "wall_s": wall_s,
        "status": "done" if closed and gate["decision"]["passed"] else "failed",
    }


# ---------------------------------------------------------------- trace data for older run receipts

def resolve_trace(r: dict, base: Path | None) -> Path | None:
    trace = r.get("trace")
    if not trace:
        return None
    for p in (Path(trace), *(([base / Path(trace).name, base.parent / trace]) if base else [])):
        if p.is_file():
            return p
    return None


def with_trace_data(r: dict, base: Path | None = None) -> dict:
    """A copy of the receipt where checks without numbers get metrics, diagnostics and duration
    from the run's trace (matched in order by tool and args), and the cases the run read get
    their source files (from its describe_case results). The receipt itself is not changed.
    Write-tests and import receipts have no trace: they are returned as they are."""
    if receipt_kind(r) != "run":
        return r
    if all("metrics" in c for c in r["checks_run"]) and "case_sources" in r:
        return r
    path = resolve_trace(r, base)
    if not path:
        return r
    calls, last_t = [], 0.0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        t = float(rec.get("t", last_t) or 0)
        if rec.get("kind") == "tool":
            try:
                result = json.loads(rec["result"])
            except (ValueError, TypeError):
                result = None
            if isinstance(result, dict):
                calls.append((rec["name"], rec.get("input"), result, round(t - last_t, 2)))
        last_t = t
    out = copy.deepcopy(r)
    if "case_sources" not in out:
        sources = {k["name"]: k["source"] for name, _, result, _ in calls if name == "describe_case"
                   for k in result.get("cases", []) if isinstance(k, dict) and k.get("name") and k.get("source")}
        if sources:
            out["_case_sources"] = sources
    i = 0
    for c in out["checks_run"]:
        if "metrics" in c:
            continue
        for j in range(i, len(calls)):
            name, args, result, duration = calls[j]
            if name == c["tool"] and args == c["args"] and result.get("summary") == c["summary"]:
                c.update(metrics=result.get("metrics") or {}, diagnostics=(result.get("diagnostics") or [])[:50],
                         diagnostics_total=result.get("diagnostics_total", 0), duration_s=duration, from_trace=True)
                i = j + 1
                break
    if any(c.get("from_trace") for c in out["checks_run"]) or out.get("_case_sources"):
        out["_trace_used"] = r["trace"]
    out["_source"] = r  # the receipt as saved, e.g. for its sha256
    return out
