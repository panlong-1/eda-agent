"""`eda-agent doctor`: are a project's tools, files and keys all there?"""
from __future__ import annotations

import argparse
from pathlib import Path

from ..core.settings import PROJECT_FILE
from .common import DEFAULT_MODELS, _print_checks, resolve_project


def add_parser(sub) -> argparse.ArgumentParser:
    doc = sub.add_parser("doctor", help="check that a project's tools, files and keys are all there")
    doc.add_argument("--project", help=f"project config (default: {PROJECT_FILE} here or in a parent)")
    doc.add_argument("--provider", choices=sorted(DEFAULT_MODELS), help="also require this provider's API key")

    return doc


def _doctor(args: argparse.Namespace) -> int:
    from ..onboard.doctor import diagnose
    checks = diagnose(Path(resolve_project(args.project)), provider=args.provider)
    _print_checks(checks)
    failed = sum(c.status == "fail" for c in checks)
    print(f"\n{failed} problem(s)" if failed else "\nall checks passed")
    return 1 if failed else 0
