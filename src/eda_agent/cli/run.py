"""`eda-agent run` and `eda-agent check`: one task against a project, then receipt and human gate."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from ..agent import human_gate
from ..agent.loop import Budget, run_agent
from ..agent.mcp_client import mcp_tools
from ..agent.receipt import build_receipt, write_receipt
from ..core.project import load_project, regress_cases
from .common import (DEFAULT_MODELS, PRESETS, _add_run_args, _levels, _print_reviews, _server_args, load_prompt,
                     make_model, narrate, review_model_choice, run_reviews)


def add_parsers(sub) -> tuple:
    run = sub.add_parser("run", help="run one task against a project")
    run.add_argument("task")
    _add_run_args(run)

    check = sub.add_parser("check", help="run a fixed task: lint + one regression case (--full: whole "
                                         "regression + synth)")
    check.add_argument("--full", action="store_true")
    _add_run_args(check)

    return run, check


async def _run(args: argparse.Namespace) -> int:
    run_id = Path(args.trace).stem if args.trace else f"{datetime.now():%Y%m%d_%H%M%S}"
    trace = Path(args.trace or f"runs/{run_id}.jsonl")
    trace.parent.mkdir(parents=True, exist_ok=True)
    project = load_project(args.project)
    system = load_prompt(args.prompt)
    model_name = args.model or DEFAULT_MODELS[args.provider]
    if args.review == "I3":  # refuse before the author runs, not after
        _, review_model = review_model_choice("I3", args.provider, model_name, args.review_provider,
                                              args.review_model)
        if review_model == model_name:
            print(f"error: --review I3 needs a reviewer model other than the author's ({model_name})",
                  file=sys.stderr)
            return 2
    model = make_model(args.provider, model_name, args.effort)
    async with mcp_tools(sys.executable, _server_args(args.project)) as (tools, execute, instructions):
        if instructions:  # the tool server's own rules travel with the system prompt
            system = f"{system}\n\n## Tool server instructions\n{instructions}"
        report = await run_agent(
            model,
            tools, execute, args.task, system,
            Budget(max_turns=args.max_turns, max_tool_calls=args.max_tool_calls),
            trace=trace,
        )
        receipt = build_receipt(report, run_id=run_id, task=args.task, provider=args.provider,
                                project=project.name, all_cases=regress_cases(project), trace=str(trace),
                                prompt=args.prompt, sdc_origin=project.flow.sdc_origin if project.flow else "",
                                project_root=str(project.root))
        if getattr(args, "preset", None):
            receipt["preset"] = f"{args.preset}@{PRESETS}"
        if args.review != "none":
            try:
                receipt["reviews"] = await run_reviews(args, receipt, trace, tools, execute)
            except Exception as e:  # a failed review must not lose the author's receipt
                receipt["review_error"] = f"{type(e).__name__}: {e}"
                print(f"review failed: {receipt['review_error']}", file=sys.stderr)
    if args.narrate:
        await narrate(receipt, args.provider, model_name, args.effort, trace.parent)
    if report.status == "done" and not args.no_approval:
        human_gate.request_approval(receipt, "final report",
                                    "the evidence gate passed; a person accepts the result")
    js, md = write_receipt(receipt, trace.parent)
    print(report.final_text)
    print("\n--- run report ---")
    print(json.dumps({k: v for k, v in asdict(report).items() if k not in ("final_text", "checks")},
                     indent=2, ensure_ascii=False))
    _print_reviews(receipt.get("reviews", []))
    print(f"\nstatus: {receipt['status']}")
    print(f"trace: {trace}")
    print(f"receipt: {md.with_suffix('.html')} (also {md.name}, json: {js.name})")
    if receipt["status"] == human_gate.AWAITING:
        print(f"waiting for a person ({_levels(receipt)}): eda-agent review-ui, or eda-agent approve / "
              f"reject {run_id} (the receipt's commands say what approve needs)")
    return 0 if report.status == "done" else 1
