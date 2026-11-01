"""`eda-agent review-ui`: a local page to decide by clicking."""
from __future__ import annotations

import argparse
from pathlib import Path


def add_parser(sub) -> None:
    ui = sub.add_parser("review-ui", help="a local page to approve / leave pending / reject runs by clicking")
    ui.add_argument("--runs-dir", default="runs")
    ui.add_argument("--port", type=int, default=8799)
    ui.add_argument("--by", help="who decides (default: git user.name, else the login name)")
    ui.add_argument("--projects-dir", help="where the project configs are, to find each run's project root for "
                                           "its file references (default: projects/ next to the runs folder)")


def _review_ui(args: argparse.Namespace) -> int:
    from ..review_ui import serve
    return serve(Path(args.runs_dir), port=args.port, by=args.by,
                 projects_dir=Path(args.projects_dir) if args.projects_dir else None)
