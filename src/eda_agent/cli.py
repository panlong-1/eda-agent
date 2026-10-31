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

DEFAULT_MODELS = {"anthropic": "claude-opus-5-5", "deepseek": "deepseek-v4-pro"}

SYSTEM = """You are an engineering agent for digital chip design. You act only through the tools.

Rules:
- A conclusion is valid only if a tool result supports it. Quote the tool's status and
  evidence_level, and do not claim more than its claim_scope.
- "Compiles" or "lint clean" (E0) never means "works". Functional claims need regress (E2) or higher.
- If a tool errors twice in a row, stop and report instead of trying a third time.
- End with a short report: what you ran, what each result proves, and the residual risk."""


async def _run(args: argparse.Namespace) -> int:
    trace = Path(args.trace or f"runs/{datetime.now():%Y%m%d_%H%M%S}.jsonl")
    trace.parent.mkdir(parents=True, exist_ok=True)
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
    print(report.final_text)
    print("\n--- run report ---")
    print(json.dumps({k: v for k, v in asdict(report).items() if k != "final_text"}, indent=2))
    print(f"trace: {trace}")
    return 0 if report.stop_reason == "end" else 1


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
