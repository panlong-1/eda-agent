"""`eda-agent approve`, `reject`, `pending`: the human gate (agent/human_gate.py)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..agent import human_gate
from ..agent.receipt import write_receipt
from .common import _levels


def add_parsers(sub) -> None:
    for name, help_text in (("approve", "approve a run awaiting approval (Error items need --reason, Fatal "
                                        "items --override and --reason)"),
                            ("reject", "reject a run"),
                            ("pending", "decide later: record a note, the run stays awaiting approval")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("run_id")
        p.add_argument("--by", help="who decides (default: git user.name, else the login name)")
        if name == "pending":
            p.add_argument("--note", help="what is missing, or why later")
        else:
            p.add_argument("--reason", required=name == "reject")
        p.add_argument("--runs-dir", default="runs")
        if name == "approve":
            p.add_argument("--override", "--override-gate", dest="override", action="store_true",
                           help="approve despite Fatal items or the evidence gate's rejection (needs --reason; "
                                "recorded in the receipt)")


def _decide(args: argparse.Namespace, decision: str) -> int:
    runs_dir = Path(args.runs_dir)
    by = args.by or human_gate.default_approver()
    try:
        receipt, _ = human_gate.load_receipt(args.run_id, runs_dir)
        if decision == "pending":
            human_gate.defer(receipt, by=by, note=args.note or "")
        else:
            human_gate.decide(receipt, decision, by=by, reason=args.reason or "",
                              override=getattr(args, "override", False))
    except human_gate.ApprovalError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    js, md = write_receipt(receipt, runs_dir)
    a = receipt["approval"]
    if decision == "pending":
        print(f"run {args.run_id}: still open; note {len(a['notes'])} recorded by {by}")
    else:
        print(f"run {args.run_id}: {a['status']} by {a['by']} at {a['at']} ({_levels(receipt)})")
    print(f"receipt: {md.with_suffix('.html')} (also {md.name}, json: {js.name})")
    return 0
