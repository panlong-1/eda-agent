"""`eda-agent write-tests`: new tests that kill surviving mutants, then the human gate."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from ..agent import human_gate
from ..agent.mcp_client import mcp_tools
from ..agent.receipt import testgen_receipt, write_receipt
from ..core.project import load_project
from ..core.settings import PROJECT_FILE
from .common import DEFAULT_MODELS, _server_args, load_prompt, make_model


def add_parser(sub) -> None:
    wt = sub.add_parser("write-tests", help="write new tests that kill surviving mutants of a mutate run "
                                           "(model writes, tools judge, a person approves the patch)")
    wt.add_argument("--project", help=f"project config (default: {PROJECT_FILE} here or in a parent)")
    wt.add_argument("--mutate-run", required=True, help="work directory name of a mutate run (runs/work/<name>)")
    wt.add_argument("--mutants", type=int, nargs="+", required=True, help="mutant ids to close")
    wt.add_argument("--provider", default="anthropic", choices=sorted(DEFAULT_MODELS))
    wt.add_argument("--model")
    # Thinking models can spend a whole reply reasoning; medium effort and a larger cap leave room
    # for the test file itself.
    wt.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    wt.add_argument("--max-tokens", type=int, default=32000, help="output limit per reply (default 32000)")
    wt.add_argument("--review-model", help="reviewer model (default: the author's)")
    wt.add_argument("--no-review", action="store_true")
    wt.add_argument("--attempts", type=int, default=3)
    wt.add_argument("--run-id")


async def _write_tests(args: argparse.Namespace) -> int:
    """The test-generation loop (agent/testgen.py) for mutants of one mutate run, then the human gate."""
    import time

    from ..agent import testgen
    t0 = time.monotonic()
    project = load_project(args.project)
    run_id = args.run_id or f"tg_{datetime.now():%Y%m%d_%H%M%S}"
    model_name = args.model or DEFAULT_MODELS[args.provider]
    system, review_system = load_prompt(testgen.PROMPT), load_prompt(testgen.REVIEW_PROMPT)
    usage: dict = {}
    results = []
    async with mcp_tools(sys.executable, _server_args(args.project)) as (_, execute, _instructions):
        for mid in args.mutants:
            author = make_model(args.provider, model_name, args.effort, max_tokens=args.max_tokens)
            r = await testgen.close_gap(author, execute, system, run_id=run_id, mutate_run=args.mutate_run,
                                        mutant=mid, usage=usage, attempts=args.attempts)
            if r.status == "closed" and not args.no_review:
                reviewer = make_model(args.provider, args.review_model or model_name, args.effort,
                                      max_tokens=args.max_tokens)
                r.review = await testgen.review_test(reviewer, review_system, Path(r.test).read_text(), r.diff,
                                                     r.scores[-1], usage)
            print(f"mutant {mid}: {r.status}" + (f" — {r.test}" if r.test else f" — {r.note}"))
            results.append(r)
    gate = testgen.gate_results(results)
    closed = [r for r in results if r.status == "closed"]
    receipt = testgen_receipt(run_id=run_id, project=project.name, provider=args.provider, model=model_name,
                              prompt=testgen.PROMPT, review_prompt=testgen.REVIEW_PROMPT, mutate_run=args.mutate_run,
                              targets=args.mutants, results=[testgen.to_dict(r) for r in results], gate=gate,
                              usage=usage, wall_s=round(time.monotonic() - t0, 1), closed=bool(closed))
    ok = receipt["status"] == "done"
    if ok:
        human_gate.request_approval(receipt, "new tests",
                                    "a person decides whether the new test(s) and patch enter the regression")
    js, md = write_receipt(receipt, Path("runs"))
    print(gate["report"])
    print(f"\nreceipt: {md.with_suffix('.html')} (also {md.name}, json: {js.name})")
    return 0 if ok else 1
