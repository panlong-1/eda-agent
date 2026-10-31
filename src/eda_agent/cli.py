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
from .agent.models import AnthropicAdapter, OpenAICompatAdapter
from .agent.receipt import build_receipt, write_receipt
from .core.project import load_project, regress_cases

DEFAULT_MODELS = {"anthropic": "claude-opus-5-5", "deepseek": "deepseek-v4-pro"}
# System prompts live in <repo>/prompts/<name>.md; the name (with its version) goes into the receipt.
PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompts"
DEFAULT_PROMPT = "system.v1"


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


async def _run(args: argparse.Namespace) -> int:
    run_id = Path(args.trace).stem if args.trace else f"{datetime.now():%Y%m%d_%H%M%S}"
    trace = Path(args.trace or f"runs/{run_id}.jsonl")
    trace.parent.mkdir(parents=True, exist_ok=True)
    project = load_project(args.project)
    system = load_prompt(args.prompt)
    server_args = ["-m", "eda_agent.server.mcp_server", "--project", str(Path(args.project).resolve())]
    model_name = args.model or DEFAULT_MODELS[args.provider]
    if args.provider == "deepseek":
        model = OpenAICompatAdapter(model=model_name, effort=args.effort)
    else:
        model = AnthropicAdapter(model=model_name, effort=args.effort)
    async with mcp_tools(sys.executable, server_args) as (tools, execute):
        report = await run_agent(
            model,
            tools, execute, args.task, system,
            Budget(max_turns=args.max_turns, max_tool_calls=args.max_tool_calls),
            trace=trace,
        )
    receipt = build_receipt(report, run_id=run_id, task=args.task, provider=args.provider,
                            project=project.name, all_cases=regress_cases(project), trace=str(trace),
                            prompt=args.prompt)
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
    run.add_argument("--prompt", default=DEFAULT_PROMPT,
                     help=f"system prompt name in prompts/ (default {DEFAULT_PROMPT})")
    run.add_argument("--max-turns", type=int, default=30)
    run.add_argument("--max-tool-calls", type=int, default=60)
    run.add_argument("--trace")
    args = ap.parse_args()
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
