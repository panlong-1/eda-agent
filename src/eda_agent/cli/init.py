"""`eda-agent init`: draft a project config from a design repository."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from .common import _print_checks


def add_parser(sub) -> None:
    init = sub.add_parser("init", help="draft a project config from a design repository")
    init.add_argument("design_dir")
    init.add_argument("--out", help="where to write the draft (default projects/<dir name>.toml)")
    init.add_argument("--env-script", help="script to source before running the design's tools")
    init.add_argument("--no-fusesoc", action="store_true", help="do not run fusesoc --setup")
    init.add_argument("--force", action="store_true", help="overwrite an existing draft")


def _init(args: argparse.Namespace) -> int:
    from ..onboard.doctor import diagnose
    from ..onboard.init import write_draft
    design = Path(args.design_dir).expanduser()
    out = Path(args.out or f"projects/{design.resolve().name}.toml")
    try:
        path, det = write_draft(design, out, f"{datetime.now():%Y-%m-%d}", env_script=args.env_script,
                                run_fusesoc=not args.no_fusesoc, force=args.force)
    except FileExistsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"draft written: {path}")
    confirm = [f"[{s}] {k}" for s, d in (("lint", det.lint), ("regress", det.regress), ("flow", det.flow))
               for k, g in d.items() if g.confirm]
    if confirm:
        print("check these first: " + ", ".join(confirm))
    for n in det.notes:
        print(f"note: {n}")
    print("\nprobe (doctor on the draft):")
    checks = diagnose(path)
    _print_checks(checks)
    print(f"\nnext: read {path}, fix what is marked, then: eda-agent doctor --project {path}")
    return 0
