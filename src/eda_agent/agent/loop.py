"""The agent loop: model -> tool calls -> results, under hard budgets.

Budgets follow the methodology: the same tool failing twice in a row stops the
run and reports instead of trying a third time, and every run records tokens,
tool calls and wall time (Liu2026 reporting item 6). When the model ends, the
evidence gate (gate.py) checks its report against the tool results of this run;
a rejected report goes back to the model for at most `max_gate_rewrites` rewrites.
"""
from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .gate import GateDecision, check_report, rejection_message
from .models import ModelAdapter, ToolOutcome, ToolSpec

# 2: tool results are logged whole (1 cut them at 4000 characters).
TRACE_VERSION = 2

# (tool name, arguments) -> (result text, is_error)
Executor = Callable[[str, dict], Awaitable[tuple[str, bool]]]
# (final report, parsed tool results of this run) -> decision
Gate = Callable[[str, list[dict]], GateDecision]


@dataclass
class Budget:
    max_turns: int = 30
    max_tool_calls: int = 60
    max_consecutive_errors: int = 2  # per tool; then stop and report
    max_gate_rewrites: int = 2


@dataclass
class RunReport:
    stop_reason: str
    # done: gate passed (or no gate); pending: gate rejected, a human must judge; incomplete: stopped early
    status: str = "incomplete"
    final_text: str = ""
    turns: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    usage: dict = field(default_factory=dict)
    wall_s: float = 0.0
    model: str = ""
    halted_on: str = ""
    gate: list[dict] = field(default_factory=list)    # one decision per report the model ended with
    checks: list[dict] = field(default_factory=list)  # every tool call: tool, args, parsed result


def _is_tool_error(text: str, is_error: bool) -> bool:
    """Transport errors and envelopes with status error/timeout count; 'fail' is a real result."""
    if is_error:
        return True
    try:
        return json.loads(text).get("status") in ("error", "timeout")
    except (ValueError, AttributeError):
        return False


def _parse(text: str) -> dict | None:
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


async def run_agent(model: ModelAdapter, tools: list[ToolSpec], execute: Executor, task: str,
                    system: str, budget: Budget | None = None, trace: Path | None = None,
                    gate: Gate | None = check_report) -> RunReport:
    budget = budget or Budget()
    report = RunReport(stop_reason="", model=model.name)
    usage: dict[str, int] = {}
    consecutive: dict[str, int] = {}
    t0 = time.monotonic()
    trace_f = trace.open("a", encoding="utf-8") if trace else None

    def log(kind: str, **data) -> None:
        if trace_f:
            trace_f.write(json.dumps({"t": round(time.monotonic() - t0, 3), "kind": kind, **data}) + "\n")

    model.start(system, tools, task)
    log("start", trace_version=TRACE_VERSION, model=model.name, task=task, tools=[t.name for t in tools])
    try:
        while True:
            if report.turns >= budget.max_turns:
                report.stop_reason = "budget_turns"
                break
            turn = await model.step()
            report.turns += 1
            for k, v in turn.usage.items():
                usage[k] = usage.get(k, 0) + v
            log("turn", stop=turn.stop, text=turn.text,
                calls=[{"name": c.name, "input": c.input} for c in turn.tool_calls], usage=turn.usage)
            report.final_text = turn.text or report.final_text

            if turn.stop == "pause":
                continue
            if turn.stop == "end" and gate:
                decision = gate(turn.text, [c["result"] for c in report.checks if c["result"]])
                rewrites = len(report.gate)
                report.gate.append(decision.to_dict())
                log("gate", attempt=rewrites + 1, **decision.to_dict())
                if decision.passed:
                    report.stop_reason, report.status = "end", "done"
                    break
                if rewrites >= budget.max_gate_rewrites:
                    report.stop_reason, report.status = "gate_rejected", "pending"
                    break
                model.add_user_message(rejection_message(decision, rewrites + 1, budget.max_gate_rewrites))
                continue
            if turn.stop != "tool_use":
                report.stop_reason = turn.stop
                report.status = "done" if turn.stop == "end" else "incomplete"
                break

            results = []
            for call in turn.tool_calls:
                if report.tool_calls >= budget.max_tool_calls:
                    results.append(ToolOutcome(call.id, "tool-call budget exhausted", True))
                    continue
                report.tool_calls += 1
                text, is_error = await execute(call.name, call.input)
                failed = _is_tool_error(text, is_error)
                report.tool_errors += failed
                consecutive[call.name] = consecutive.get(call.name, 0) + 1 if failed else 0
                results.append(ToolOutcome(call.id, text, is_error))
                report.checks.append({"tool": call.name, "args": call.input, "result": _parse(text)})
                # The whole result, so a trace can be replayed through the gate later.
                log("tool", name=call.name, input=call.input, is_error=failed, result=text)
            model.add_tool_results(results)

            exhausted = [n for n, k in consecutive.items() if k >= budget.max_consecutive_errors]
            if exhausted:
                report.stop_reason = "retry_budget"
                report.halted_on = exhausted[0]
                break
            if report.tool_calls >= budget.max_tool_calls:
                report.stop_reason = "budget_tool_calls"
                break
    finally:
        report.usage = usage
        report.wall_s = round(time.monotonic() - t0, 2)
        log("end", **{k: v for k, v in asdict(report).items() if k != "checks"})
        if trace_f:
            trace_f.close()
    return report
