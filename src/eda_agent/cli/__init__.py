"""Command line entry point.

    eda-agent init ~/project/mydesign --out projects/mydesign.toml       (draft a project config)
    eda-agent doctor --project projects/mydesign.toml                   (is everything there?)
    eda-agent check  --project projects/torv.toml [--full]              (fixed lint/regress[/synth] task)
    eda-agent mutate --project projects/torv.toml --file rtl/x.sv       (mutation run, no model)
    eda-agent perf --project projects/torv.toml                         (cycle counts, no model)
    eda-agent perf-baseline runs/perf/<result>.json                     (a person approves a baseline)
    eda-agent write-tests --mutate-run <dir> --mutants 3 --provider deepseek  (new tests vs mutants)
    eda-agent flow-report full_p10 [--baseline X] [--with-synth]        (read a LibreLane run, no model)
    eda-agent flow-run [--tag T] [--dry-run]                            (LibreLane on a copy, ~1 h)
    eda-agent import <dir> [--as plugin]                                (a report made elsewhere)
    eda-agent run --project projects/torv.toml "Lint the design and run the smoke case"
    eda-agent run --provider deepseek --review I2 --project projects/torv.toml "..."
    eda-agent review <run_id> --review I3 --project projects/torv.toml   (review a finished run)
    eda-agent approve <run_id> [--reason "..."] [--override]           (human gate; see human_gate.py)
    eda-agent reject <run_id> --reason "..."
    eda-agent pending <run_id> --note "..."                             (decide later; the run stays open)
    eda-agent review-ui [--port 8799]                                   (decide by clicking, on localhost)
    eda-agent receipt <run_id> [--narrate]                              (re-render .md/.html [+ model summary])

--project may be left out when the current directory (or a parent) has eda-agent.toml.
Personal defaults (provider, model, effort, review, narrate) live in ~/.config/eda-agent/config.toml.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from ..core.settings import user_defaults
from . import (decide, doctor, flow, init, mutate, perf, perf_baseline, plugins, receipt, review, review_ui, run,
               write_tests)
from .common import (DEFAULT_MODELS, DEFAULT_PROMPT, I3_ALTERNATIVE, PRESETS, REVIEW_CHOICES,  # noqa: F401
                     REVIEW_PROMPT, load_preset, load_prompt, make_model, resolve_project,
                     review_model_choice)


def main() -> None:
    ap = argparse.ArgumentParser(prog="eda-agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run_p, check = run.add_parsers(sub)
    init.add_parser(sub)
    doc = doctor.add_parser(sub)
    mutate.add_parser(sub)
    perf.add_parser(sub)
    perf_baseline.add_parser(sub)
    write_tests.add_parser(sub)
    flow.add_parsers(sub)
    plugins.add_parsers(sub)
    review.add_parser(sub)
    receipt.add_parser(sub)
    review_ui.add_parser(sub)

    # Personal defaults apply to run and check; anything on the command line wins.
    try:
        defaults = user_defaults()
    except ValueError as e:
        raise SystemExit(f"error: {e}")
    run_p.set_defaults(**defaults)
    check.set_defaults(**defaults)
    if "provider" in defaults:
        doc.set_defaults(provider=defaults["provider"])
    # provider/model of `receipt --narrate` default to the run's

    decide.add_parsers(sub)

    args = ap.parse_args()
    if args.cmd == "init":
        sys.exit(init._init(args))
    if args.cmd == "doctor":
        sys.exit(doctor._doctor(args))
    if args.cmd == "receipt":
        sys.exit(receipt._receipt(args))
    if args.cmd == "review-ui":
        sys.exit(review_ui._review_ui(args))
    if args.cmd == "mutate":
        sys.exit(mutate._mutate(args))
    if args.cmd == "perf":
        sys.exit(perf._perf(args))
    if args.cmd == "perf-baseline":
        sys.exit(perf_baseline._perf_baseline(args))
    if args.cmd == "write-tests":
        args.project = resolve_project(args.project)
        sys.exit(asyncio.run(write_tests._write_tests(args)))
    if (code := flow.dispatch(args, resolve_project)) is not None:
        sys.exit(code)
    if (code := plugins.dispatch(args, resolve_project)) is not None:
        sys.exit(code)
    if args.cmd == "check":
        args.preset = "full" if args.full else "check"
        args.task = load_preset(args.preset)
    if args.cmd in ("run", "check", "review"):
        args.project = resolve_project(args.project)
    if args.cmd in ("run", "check"):
        sys.exit(asyncio.run(run._run(args)))
    if args.cmd == "review":
        sys.exit(asyncio.run(review._review(args)))
    sys.exit(decide._decide(args, args.cmd))
