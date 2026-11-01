"""The run-state table: what replaces older turns when the context is compacted (plan §0.16).

Built by code from this run's tool envelopes, never by a model: every status, evidence level and
number is copied as the tool returned it (numbers through json.dumps, so 0.725448 stays 0.725448).
The raw results stay in the trace with their ids; the agent fetches one with get_result(id). The
gate and the reviewers read the full trace, not this table.
"""
from __future__ import annotations

import json

from .gate import described_cases, executed_cases

MAX_NUMBERS = 8      # numeric metrics per check, in the order the tool returned them
MAX_ARGS = 80        # characters of a call's arguments
MAX_SUMMARY = 240    # characters of a summary's first line


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def key_numbers(result: dict) -> str:
    """The summary's first line, then the top-level numeric metrics, verbatim."""
    m = result.get("metrics") or {}
    nums = [f"{k}={json.dumps(v)}" for k, v in m.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)][:MAX_NUMBERS]
    first = (result.get("summary") or "").splitlines()[0][:MAX_SUMMARY] if result.get("summary") else ""
    lists = [f"{k}={','.join(map(str, v))}" for k, v in m.items()
             if k in ("failed_cases", "passed_cases") and isinstance(v, list) and v and len(v) <= 40]
    return "; ".join(x for x in [first, *nums, *lists] if x)


def run_state(checks: list[dict], *, turns: int, max_turns: int, tool_calls: int, max_tool_calls: int,
              usage: dict, replaced: tuple[int, int], number: int, gate: list[dict] | None = None,
              retrieved: list[str] | None = None) -> str:
    """Markdown: checks done (id, tool, args, status, level, key numbers), open items, budget used."""
    a, b = replaced
    out = [f"## Run state (compaction {number}; built by code from this run's tool results)",
           f"Turns {a}-{b} were replaced by this table. Every number below is copied from the tool "
           "results; the full result of any check is available with get_result(id). Quote numbers from "
           "here or from get_result, never from memory.", "",
           "| id | tool | args | status | evidence | key numbers |", "|---|---|---|---|---|---|"]
    for c in checks:
        r = c.get("result")
        args = json.dumps(c.get("args") or {}, ensure_ascii=False)
        args = args if len(args) <= MAX_ARGS else args[:MAX_ARGS - 1] + "…"
        if not isinstance(r, dict):
            out.append(f"| {c['id']} | {c['tool']} | {_cell(args)} | (not JSON) | | |")
            continue
        out.append(f"| {c['id']} | {c['tool']} | {_cell(args)} | {r.get('status', '')} | "
                   f"{r.get('evidence_level', '')} | {_cell(key_numbers(r))} |")
    results = [c["result"] for c in checks if isinstance(c.get("result"), dict)]
    open_items = []
    errors = [c["id"] for c in checks if isinstance(c.get("result"), dict)
              and c["result"].get("status") in ("error", "timeout")]
    if errors:
        open_items.append(f"checks that ended in error/timeout: {', '.join(errors)}")
    unrun = sorted(described_cases(results) - executed_cases(results))
    if unrun:
        open_items.append(f"cases read with describe_case but not run: {', '.join(unrun)}")
    if gate and not gate[-1].get("passed"):
        open_items.append("the evidence gate rejected the last report: "
                          + "; ".join(gate[-1].get("reasons") or [])[:600])
    out += ["", "Open items: " + (" | ".join(open_items) if open_items else "none recorded by code")]
    if retrieved:
        out.append("Memory ids retrieved so far (hints, citable): " + ", ".join(retrieved))
    tok = f"{usage.get('input_tokens', 0)} in / {usage.get('output_tokens', 0)} out"
    out.append(f"Budget used: turns {turns}/{max_turns}, tool calls {tool_calls}/{max_tool_calls}, tokens {tok}")
    return "\n".join(out)
