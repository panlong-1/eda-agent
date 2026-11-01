"""`eda-agent perf-baseline`: a person records a perf result as the approved baseline."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..agent import human_gate
from ..core.project import load_project
from ..core.settings import PROJECT_FILE
from .common import resolve_project


def add_parser(sub) -> None:
    pb = sub.add_parser("perf-baseline", help="record a perf result as the approved baseline (a person decides)")
    pb.add_argument("result", help="a perf result file written by `eda-agent perf` (runs/perf/*.json)")
    pb.add_argument("--project", help=f"project config (default: {PROJECT_FILE} here or in a parent)")
    pb.add_argument("--by", help="who decides (default: git user.name, else the login name)")
    pb.add_argument("--reason", help="why; required with --allow-loosen")
    pb.add_argument("--tolerance-pct", type=float, help="allowed difference in percent (default: [perf] tolerance_pct)")
    pb.add_argument("--allow-loosen", action="store_true",
                    help="accept a baseline that is slower, has a larger tolerance or drops cases (needs --reason)")
    pb.add_argument("--justified-by", default="",
                    help="the run or receipt that justifies the change, e.g. the functional fix that costs cycles")


def _perf_baseline(args: argparse.Namespace) -> int:
    """A person records a perf result as the approved baseline (never done by the agent)."""
    from ..tools.perf import record_baseline
    project = load_project(resolve_project(args.project))
    try:
        new, loosened = record_baseline(project, Path(args.result), by=args.by or human_gate.default_approver(),
                                        reason=args.reason or "", tolerance_pct=args.tolerance_pct,
                                        allow_loosen=args.allow_loosen, justified_by=args.justified_by)
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"baseline for {project.name} recorded by {new['recorded_by']} at {new['recorded_at']}: "
          f"{len(new['cases'])} cases, {new['key']}, tolerance {new['tolerance_pct']}%"
          + (f"; loosened: {'; '.join(loosened)} (reason: {new['reason']})" if loosened else ""))
    print(f"file: {project.perf.baseline}")
    return 0
