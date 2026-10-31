"""The agent loop: model -> tool calls -> results, under hard budgets.

Budgets follow the methodology: the same tool failing twice in a row stops the
run and reports instead of trying a third time, and every run records tokens,
tool calls and wall time (Liu2026 reporting item 6).
"""
from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .model import ModelAdapter, ToolOutcome, ToolSpec

# (tool name, arguments) -> (result text, is_error)
Executor = Callable[[str, dict], Awaitable[tuple[str, bool]]]


@dataclass
class Budget:
    max_turns: int = 30
    max_tool_calls: int = 60
    max_consecutive_errors: int = 2  # per tool; then stop and report


@dataclass
class RunReport:
    stop_reason: str
    final_text: str = ""
    turns: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    usage: dict = field(default_factory=dict)
    wall_s: float = 0.0
    model: str = ""
    halted_on: str = ""


def _is_tool_error(text: str, is_error: bool) -> bool:
    """Transport errors and envelopes with status error/timeout count; 'fail' is a real result."""
    if is_error:
        return True
    try:
        return json.loads(text).get("status") in ("error", "timeout")
    except (ValueError, AttributeError):
        return False


async def run_agent(model: ModelAdapter, tools: list[ToolSpec], execute: Executor, task: str,
                    system: str, budget: Budget | None = None, trace: Path | None = None) -> RunReport:
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
    log("start", model=model.name, task=task, tools=[t.name for t in tools])
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
            if turn.stop != "tool_use":
                report.stop_reason = turn.stop
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
                log("tool", name=call.name, input=call.input, is_error=failed, result=text[:4000])
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
        log("end", **asdict(report))
        if trace_f:
            trace_f.close()
    return report
