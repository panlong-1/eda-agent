"""`eda-agent review`: review a finished run (writes into its receipt)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..agent import human_gate
from ..agent.mcp_client import mcp_tools
from ..agent.receipt import write_receipt
from ..core.settings import PROJECT_FILE
from .common import _add_review_args, _print_reviews, _server_args, run_reviews


def add_parser(sub) -> None:
    rev = sub.add_parser("review", help="review a finished run (writes into its receipt)")
    rev.add_argument("run_id")
    rev.add_argument("--project", help=f"project config (default: {PROJECT_FILE} here or in a parent)")
    rev.add_argument("--runs-dir", default="runs")
    _add_review_args(rev, "I2")


async def _review(args: argparse.Namespace) -> int:
    runs_dir = Path(args.runs_dir)
    receipt, _ = human_gate.load_receipt(args.run_id, runs_dir)
    if (receipt.get("approval") or {}).get("status") in ("approved", "rejected"):
        print(f"run {args.run_id} is already {receipt['approval']['status']}; a review now would not be "
              "seen by the decision", file=sys.stderr)
        return 2
    async with mcp_tools(sys.executable, _server_args(args.project)) as (tools, execute, _):
        reviews = await run_reviews(args, receipt, Path(receipt["trace"]), tools, execute)
    if any(v["reviewer"] == "rules" for v in receipt.get("reviews", [])):
        reviews = [v for v in reviews if v["reviewer"] != "rules"]  # deterministic: once is enough
    receipt["reviews"] = receipt.get("reviews", []) + reviews
    js, md = write_receipt(receipt, runs_dir)
    _print_reviews(reviews)
    print(f"receipt: {md.with_suffix('.html')} (also {md.name}, json: {js.name})")
    return 0
