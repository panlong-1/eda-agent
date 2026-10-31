"""Run receipt: what was asked, what was checked, what was not, and what was claimed.

Written at the end of every run as runs/<id>.receipt.json (for tools) and
runs/<id>.receipt.md (for a human reviewer). Everything in it is taken from the
run report and the tool envelopes; nothing is re-derived by a model.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..core.envelope import EVIDENCE_LEVELS
from .gate import described_cases, executed_cases, split_residual
from .loop import RunReport


def build_receipt(report: RunReport, *, run_id: str, task: str, provider: str, project: str,
                  all_cases: list[str], trace: str = "", prompt: str = "") -> dict:
    envelopes = [c for c in report.checks if c["result"] and c["result"].get("evidence_level") in EVIDENCE_LEVELS]
    others = [c for c in report.checks if all(c is not e for e in envelopes)]
    # Same definitions as gate rule R4, so the receipt and the gate never disagree.
    results = [c["result"] for c in report.checks if c["result"]]
    described = described_cases(results)
    executed = executed_cases(results)
    claims, residual = split_residual(report.final_text)
    tool_risks = sorted({r for c in envelopes for r in c["result"].get("residual_risk", [])})
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
        } for c in envelopes],
        "other_tool_calls": [{"tool": c["tool"], "args": c["args"],
                              "status": (c["result"] or {}).get("status", "")} for c in others],
        "not_run": {
            "described_but_not_run": sorted(described - executed),
            "cases_not_run": [n for n in all_cases if n not in executed],
            "lint_run": any(c["tool"] == "lint" for c in envelopes),
        },
        "final_claims": claims.strip(),
        "residual_risk": {"report": residual.strip(), "from_tools": tool_risks},
        "usage": report.usage,
        "turns": report.turns,
        "tool_calls": report.tool_calls,
        "tool_errors": report.tool_errors,
        "wall_s": report.wall_s,
        "trace": trace,
    }


def _cell(text: str) -> str:
    """A Markdown table cell: '|' escaped (GFM honours it inside code spans too), one line."""
    return " ".join(str(text).split()).replace("|", "\\|")


def render_markdown(r: dict) -> str:
    gate = r["gate"]
    out = [
        f"# Run receipt {r['run_id']}",
        "",
        f"- **Task:** {r['task']}",
        f"- **Project:** {r['project']}  ·  **Model:** {r['provider']} / {r['model']}"
        f"  ·  **Prompt:** {r.get('prompt') or 'n/a'}",
        f"- **Stop:** {r['stop_reason']}  ·  **Status:** {r['status']}  ·  "
        f"**Gate:** {gate['status']} ({gate['rewrites']} rewrite(s))",
        f"- **Cost:** {r['turns']} turns, {r['tool_calls']} tool calls ({r['tool_errors']} errors), "
        f"{r['usage'].get('input_tokens', 0)} in / {r['usage'].get('output_tokens', 0)} out tokens, "
        f"{r['wall_s']} s",
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
    if r["trace"]:
        out += ["", f"Trace: `{r['trace']}`"]
    return "\n".join(out) + "\n"


def write_receipt(receipt: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    js, md = (out_dir / f"{receipt['run_id']}.receipt.{ext}" for ext in ("json", "md"))
    js.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md.write_text(render_markdown(receipt), encoding="utf-8")
    return js, md
