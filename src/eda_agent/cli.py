"""Command line entry point.

    eda-agent run --project projects/torv.toml "Lint the design and run the smoke case"
    eda-agent run --provider deepseek --project projects/torv.toml "..."
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .agent.loop import Budget, run_agent
from .agent.mcp_client import mcp_tools
from .agent.model import AnthropicAdapter, OpenAICompatAdapter
from .agent.receipt import build_receipt, write_receipt
from .projects import load_project, regress_cases

DEFAULT_MODELS = {"anthropic": "claude-opus-5-5", "deepseek": "deepseek-v4-pro"}

SYSTEM = """You are an engineering agent for digital chip design. You act only through the tools.

Rules:
- A conclusion is valid only if a tool result of this run supports it. Quote the tool's status
  and evidence_level exactly as returned, and do not claim more than its claim_scope.
- Choosing regression cases: call list_regress_cases and read every case's summary, then
  describe_case on each case whose summary touches the task, and pick by what each test
  covers, not by its name. A test named for one feature often exercises others.
- "Compiles" or "lint clean" (E0) never means "works". A regress pass (E2) supports only
  "these cases passed"; do not write that a feature is handled, correct, verified or works
  unless a passing result reaches E3 or higher.
- If a tool errors twice in a row, stop and report instead of trying a third time.
- End with a short report: what you ran, what each result proves, and a section headed
  "Residual risk" that says what the checks did not cover. An evidence gate checks the
  report against the tool results and sends it back if it overclaims."""


async def _run(args: argparse.Namespace) -> int:
    run_id = Path(args.trace).stem if args.trace else f"{datetime.now():%Y%m%d_%H%M%S}"
    trace = Path(args.trace or f"runs/{run_id}.jsonl")
    trace.parent.mkdir(parents=True, exist_ok=True)
    project = load_project(args.project)
    server_args = ["-m", "eda_agent.mcp_server", "--project", str(Path(args.project).resolve())]
    model_name = args.model or DEFAULT_MODELS[args.provider]
    if args.provider == "deepseek":
        model = OpenAICompatAdapter(model=model_name, effort=args.effort)
    else:
        model = AnthropicAdapter(model=model_name, effort=args.effort)
    async with mcp_tools(sys.executable, server_args) as (tools, execute):
        report = await run_agent(
            model,
            tools, execute, args.task, SYSTEM,
            Budget(max_turns=args.max_turns, max_tool_calls=args.max_tool_calls),
            trace=trace,
        )
    receipt = build_receipt(report, run_id=run_id, task=args.task, provider=args.provider,
                            project=project.name, all_cases=regress_cases(project), trace=str(trace))
    js, md = write_receipt(receipt, trace.parent)
    print(report.final_text)
    print("\n--- run report ---")
    print(json.dumps({k: v for k, v in asdict(report).items() if k not in ("final_text", "checks")},
                     indent=2, ensure_ascii=False))
    print(f"trace: {trace}")
    print(f"receipt: {md} (json: {js})")
    return 0 if report.status == "done" else 1


def main() -> None:
    ap = argparse.ArgumentParser(prog="eda-agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run one task against a project")
    run.add_argument("task")
    run.add_argument("--project", required=True)
    run.add_argument("--provider", default="anthropic", choices=sorted(DEFAULT_MODELS))
    run.add_argument("--model", help="defaults per provider: " + ", ".join(
        f"{p}={m}" for p, m in DEFAULT_MODELS.items()))
    run.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    run.add_argument("--max-turns", type=int, default=30)
    run.add_argument("--max-tool-calls", type=int, default=60)
    run.add_argument("--trace")
    args = ap.parse_args()
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
