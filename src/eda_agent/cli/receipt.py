"""`eda-agent receipt`: re-render a receipt's .md and .html (optionally with a model summary)."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from ..agent import human_gate
from ..agent.receipt import render_files, with_trace_data, write_receipt
from ..agent.receipt.markdown import render_summary_md
from ..agent.receipt.summary import summarize
from .common import DEFAULT_MODELS, narrate


def add_parser(sub) -> None:
    rec = sub.add_parser("receipt", help="re-render a receipt's .md and .html (review summary on top)")
    rec.add_argument("run_id")
    rec.add_argument("--runs-dir", default="runs")
    rec.add_argument("--out-dir", help="write the rendered files here instead of next to the .json")
    rec.add_argument("--print", action="store_true", help="also print the review summary")
    rec.add_argument("--narrate", action="store_true",
                     help="add a model-written summary (checked by the evidence gate); needs the provider's "
                          "API key")
    rec.add_argument("--provider", choices=sorted(DEFAULT_MODELS), help="for --narrate (default: the run's)")
    rec.add_argument("--model", help="for --narrate (default: the run's model)")
    rec.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])


def _receipt(args: argparse.Namespace) -> int:
    """Re-render the .md and .html of a receipt from its .json and bring its `levels` up to
    date. With --narrate, first add a model-written summary (outside the decided sha256)."""
    runs_dir = Path(args.runs_dir)
    try:
        receipt, _ = human_gate.load_receipt(args.run_id, runs_dir)
    except human_gate.ApprovalError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.narrate:
        provider = args.provider or receipt["provider"]
        model_name = args.model or (receipt["model"] if provider == receipt["provider"] else DEFAULT_MODELS[provider])
        asyncio.run(narrate(receipt, provider, model_name, args.effort, runs_dir))
    if args.out_dir:
        md, page = render_files(receipt, Path(args.out_dir), base=runs_dir)
    else:
        _, md = write_receipt(receipt, runs_dir)
        page = md.with_suffix(".html")
    if args.print:
        print(render_summary_md(summarize(with_trace_data(receipt, runs_dir))))
    print(f"receipt: {page} (also {md.name})")
    return 0
