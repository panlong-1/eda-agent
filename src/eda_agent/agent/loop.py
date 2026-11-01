"""The agent loop: model -> tool calls -> results, under hard budgets.

Budgets follow the methodology: the same tool failing twice in a row stops the
run and reports instead of trying a third time, and every run records tokens,
tool calls and wall time (Liu2026 reporting item 6). When the model ends, the
evidence gate (gate.py) checks its report against the tool results of this run;
a rejected report goes back to the model for at most `max_gate_rewrites` rewrites.

Memory and compaction (plan §0.16, agent/memory.py): every check gets an id (r1, r2, ...). With
`compaction`, when a turn's input tokens pass the threshold (or every N turns) the adapter replaces
older turns by a run-state table built by code (runstate.py); the loop only asks, the adapter knows
how its provider's history stays valid. get_result(id) and search_memory(query) are answered here,
logged, and never counted as checks. The gate always reads every check of the run, not the context.
"""
from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .gate import GateDecision, check_report, rejection_message
from .memory import GET_RESULT, SEARCH_MEMORY, Compaction, RunMemory
from .models import ModelAdapter, ToolOutcome, ToolSpec
from .runstate import run_state

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
    checks: list[dict] = field(default_factory=list)  # every tool call: id, tool, args, parsed result
    compactions: list[dict] = field(default_factory=list)  # when, which turns, why (plan §0.16)
    fetches: list[dict] = field(default_factory=list)      # get_result / search_memory calls


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
                    gate: Gate | None = check_report, resume: bool = False,
                    memory: RunMemory | None = None, compaction: Compaction | None = None) -> RunReport:
    budget = budget or Budget()
    raw: dict[str, str] = {}  # result id -> the text the tool returned (for get_result)
    local = {GET_RESULT.name: GET_RESULT} if compaction else {}
    if memory and memory.search:
        local[SEARCH_MEMORY.name] = SEARCH_MEMORY
    tools = list(tools) + [t for t in local.values() if all(t.name != x.name for x in tools)]
    since_compaction = 0
    report = RunReport(stop_reason="", model=model.name)
    usage: dict[str, int] = {}
    consecutive: dict[str, int] = {}
    t0 = time.monotonic()
    trace_f = trace.open("a", encoding="utf-8") if trace else None

    def log(kind: str, **data) -> None:
        if trace_f:
            trace_f.write(json.dumps({"t": round(time.monotonic() - t0, 3), "kind": kind, **data}) + "\n")

    if resume:  # continue the model's conversation with one more user message
        model.add_user_message(task)
        log("resume", model=model.name, task=task)
    else:
        model.start(system, tools, task)
        log("start", trace_version=TRACE_VERSION, model=model.name, task=task, tools=[t.name for t in tools])
    def compact_if_due(turn_usage: dict) -> None:
        """Ask the adapter to replace older turns by the run-state table (built by code)."""
        nonlocal since_compaction
        why = compaction.due(turn_usage.get("input_tokens", 0) + turn_usage.get("output_tokens", 0),
                             since_compaction)
        if not why:
            return
        n = len(report.compactions) + 1

        def state(first: int, last: int) -> str:
            return run_state(report.checks, turns=report.turns, max_turns=budget.max_turns,
                             tool_calls=report.tool_calls, max_tool_calls=budget.max_tool_calls, usage=usage,
                             replaced=(first, last), number=n, gate=report.gate,
                             retrieved=memory.retrieved if memory else None)
        done = model.compact(state, compaction.keep_turns)
        if done:
            since_compaction = 0
            rec = {"number": n, "at_turn": report.turns, "reason": why, **done}
            report.compactions.append(rec)
            log("compact", **rec)

    if memory:
        log("memory", hints=[{k: h.get(k) for k in ("kind", "id", "status", "source", "score", "note")}
                             for h in memory.hints])

    def answer_locally(name: str, args: dict) -> tuple[str, bool]:
        if name == GET_RESULT.name:
            rid = str(args.get("id", "")).strip()
            text = raw.get(rid)
            report.fetches.append({"tool": name, "id": rid, "turn": report.turns, "found": text is not None})
            log("fetch", id=rid, turn=report.turns, found=text is not None)
            if text is None:
                return f"no result {rid!r} in this run (ids: {', '.join(raw) or 'none yet'})", True
            return text, False
        query = str(args.get("query", ""))
        hits = memory.search(query) if memory and memory.search else []
        for h in hits:
            memory.note(h["id"])
        report.fetches.append({"tool": name, "query": query, "turn": report.turns, "ids": [h["id"] for h in hits]})
        log("recall", query=query, turn=report.turns, ids=[h["id"] for h in hits])
        return json.dumps({"note": "hints, NOT evidence; cite ids exactly, as hints", "hits": hits}), False
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
                decision = gate(turn.text, [c["result"] for c in report.checks if c["result"]]
                                + ([memory.gate_record()] if memory else []))
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
                if call.name in local:
                    text, is_error = answer_locally(call.name, call.input)
                    results.append(ToolOutcome(call.id, text, is_error))
                    continue
                if report.tool_calls >= budget.max_tool_calls:
                    results.append(ToolOutcome(call.id, "tool-call budget exhausted", True))
                    continue
                report.tool_calls += 1
                started = time.monotonic()
                text, is_error = await execute(call.name, call.input)
                duration = round(time.monotonic() - started, 2)
                failed = _is_tool_error(text, is_error)
                report.tool_errors += failed
                consecutive[call.name] = consecutive.get(call.name, 0) + 1 if failed else 0
                results.append(ToolOutcome(call.id, text, is_error))
                rid = f"r{len(report.checks) + 1}"
                raw[rid] = text
                report.checks.append({"id": rid, "tool": call.name, "args": call.input, "result": _parse(text),
                                      "duration_s": duration})
                # The whole result, so a trace can be replayed through the gate later.
                log("tool", id=rid, name=call.name, input=call.input, is_error=failed, result=text)
            model.add_tool_results(results)
            since_compaction += 1
            if compaction and hasattr(model, "compact"):
                compact_if_due(turn.usage)

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
