"""Optional model-written summary of a receipt, for the person who approves (a "narrative").

The model sees the receipt data only (no trace, no files, no tools). Its text goes through the
same evidence gate as the agent's report (agent/gate: citations, strength, timing, residual
risk, skipped cases), against results rebuilt from the receipt, with one rewrite allowed. The
receipt keeps the text with the provider, model, prompt name, gate decisions and cost under
`narrative`; the rendered receipt marks it as model-written, and the plain-code summary and
tables above and below it stay the authority. The narrative is left out of the sha256 a
decision records, so one can be added to a decided receipt without changing it.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from ..gate import check_report
from ..loop import Budget, run_agent
from ..model_output import results_from_receipt

PROMPT = "receipt_summary.v1"
# Fields the model does not get: the decision (the summary is not about it), the trace path,
# an earlier narrative, the derived levels, the reviewers' raw text (their findings and
# checklists are kept), and display-only data added from the trace.
_DROP = ("approval", "status", "agent_status", "trace", "narrative", "narratives", "levels", "_trace_used",
         "_source")


def model_view(r: dict) -> dict:
    view = {k: v for k, v in r.items() if k not in _DROP}
    view["reviews"] = [{k: v for k, v in rv.items() if k not in ("text", "trace", "usage")}
                       for rv in r.get("reviews", [])]
    view["checks_run"] = [{k: v for k, v in c.items() if k != "from_trace"} for c in r["checks_run"]]
    return view


async def _no_tools(name: str, args: dict) -> tuple[str, bool]:
    return "no tools are available here", True


async def write_narrative(r: dict, model, system: str, *, provider: str, prompt: str = PROMPT,
                          max_rewrites: int = 1) -> dict:
    results = results_from_receipt(r)
    task = ("Write the summary.\n\nReceipt data (JSON):\n```json\n"
            f"{json.dumps(model_view(r), ensure_ascii=False, indent=1)}\n```")
    report = await run_agent(model, [], _no_tools, task, system,
                             Budget(max_turns=1 + max_rewrites, max_tool_calls=0, max_gate_rewrites=max_rewrites),
                             gate=lambda text, _: check_report(text, results))
    passed = bool(report.gate) and report.gate[-1]["passed"]
    return {
        "text": report.final_text.strip(), "provider": provider, "model": model.name,
        "prompt": prompt, "input": "the receipt data only (no trace, no files, no tools)",
        "gate": {"passed": passed, "rewrites": max(0, len(report.gate) - 1), "decisions": report.gate},
        "at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "usage": report.usage, "turns": report.turns, "wall_s": report.wall_s, "stop_reason": report.stop_reason,
    }
