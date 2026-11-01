"""`eda-agent perf`: cycle counts per case, no model; compared with the approved baseline."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from ..core.project import load_project
from ..core.settings import PROJECT_FILE
from .common import resolve_project


def add_parser(sub) -> None:
    prf = sub.add_parser("perf", help="cycle counts per case, no model; compared with the approved baseline")
    prf.add_argument("--project", help=f"project config (default: {PROJECT_FILE} here or in a parent)")
    prf.add_argument("--cases", nargs="*", help="regression cases (default: [perf] cases)")


def _perf(args: argparse.Namespace) -> int:
    """No model: run the perf tool, print the table and keep the result for perf-baseline."""
    from ..tools.perf import run_perf
    project = load_project(resolve_project(args.project))
    res = run_perf(project, args.cases)
    print(f"perf: {res.status} — {res.summary}")
    m = res.metrics
    for case, v in (m.get("cases") or {}).items():
        row = (m.get("comparison") or {}).get(case)
        vs = f"  vs baseline {row['reference']} ({row['delta']:+g}, {row['delta_pct']:+g}%) {row['verdict']}" \
            if row and row.get("delta") is not None else ""
        print(f"  {case:24} " + " ".join(f"{k}={x}" for k, x in v.items()) + vs)
    for r in res.residual_risk:
        print(f"  risk: {r}")
    out = Path("runs/perf") / f"{project.name}_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res.to_dict(), indent=2) + "\n")
    print(f"result: {out}  (a person may record it: eda-agent perf-baseline {out})")
    return {"pass": 0, "fail": 1}.get(res.status, 2)
