"""`eda-agent mutate`: a mutation run on one RTL file, no model."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..core.project import load_project
from ..core.settings import PROJECT_FILE
from .common import resolve_project


def add_parser(sub) -> None:
    mut = sub.add_parser("mutate", help="mutation run on one RTL file, no model: which mutants the "
                                        "regression cases miss")
    mut.add_argument("--project", help=f"project config (default: {PROJECT_FILE} here or in a parent)")
    mut.add_argument("--file", required=True, help="RTL file relative to the project root")
    mut.add_argument("--count", type=int, default=20)
    mut.add_argument("--types", nargs="*", help="avl-core mutation classes (default: [mutation] types, else all)")
    mut.add_argument("--cases", nargs="*", help="regression cases (default: [mutation] cases)")
    mut.add_argument("--seed", type=int, default=1)
    mut.add_argument("--jobs", type=int, help="copies run in parallel (default: [mutation] jobs)")
    mut.add_argument("--confirm-full", action="store_true", help="re-run survivors on the whole case list")
    mut.add_argument("--no-equiv", action="store_true", help="skip the equivalence check of survivors")


def _mutate(args: argparse.Namespace) -> int:
    """No model: run the mutate tool directly and print where its report is."""
    from ..tools.mutate import run_mutate
    project = load_project(resolve_project(args.project))
    res = run_mutate(project, args.file, count=args.count, types=args.types, cases=args.cases, seed=args.seed,
                     confirm_full=args.confirm_full, equivalence=not args.no_equiv, jobs=args.jobs)
    print(f"mutate {args.file}: {res.status} — {res.summary}")
    for d in res.diagnostics:
        label = {"SURVIVED": "survived", "PERF_KILLED": "perf-killed"}.get(d.code, d.code.lower())
        print(f"  {label} {d.file}:{d.line}  {d.msg}")
    for r in res.residual_risk:
        print(f"  risk: {r}")
    if work := res.metrics.get("work_dir"):
        print(f"report: {work}/report.md  matrix: {work}/kill_matrix.csv")
        Path(work, "envelope.json").write_text(json.dumps(res.to_dict(), indent=2) + "\n")
    return {"pass": 0, "fail": 1}.get(res.status, 2)
