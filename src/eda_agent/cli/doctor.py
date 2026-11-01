"""`eda-agent doctor`: are a project's tools, files and keys all there?

`--deep [--stage sim|synth|pnr]` goes further, per stage: file checks of what the stage needs, then one
minimal real run (the smallest regression case; synthesis with timing; for pnr the views and decks
named by the baseline run's config, no flow run). What stops a stage is reported as setup issues
(core/setup_issues.py): what is missing, the evidence, the fix and who acts. The approved [setup]
steps run when a stage is not ready and one of its fixes is such a step (`--no-setup`: report only).
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from ..core.settings import PROJECT_FILE
from ..core.setup_issues import STAGES, blocking, dedupe
from .common import DEFAULT_MODELS, _print_checks, resolve_project

DOCTOR_DIR = Path("runs/doctor")


def add_parser(sub) -> argparse.ArgumentParser:
    doc = sub.add_parser("doctor", help="check that a project's tools, files and keys are all there")
    doc.add_argument("--project", help=f"project config (default: {PROJECT_FILE} here or in a parent)")
    doc.add_argument("--provider", choices=sorted(DEFAULT_MODELS), help="also require this provider's API key")
    doc.add_argument("--deep", action="store_true",
                     help="per stage: check its prerequisites, then one minimal real run; report setup issues")
    doc.add_argument("--stage", choices=STAGES, action="append",
                     help="with --deep: only this stage (repeatable; default: every configured stage)")
    doc.add_argument("--no-setup", action="store_true", help="with --deep: never run the [setup] steps")
    return doc


def _doctor(args: argparse.Namespace) -> int:
    from ..onboard.doctor import diagnose
    path = Path(resolve_project(args.project))
    checks = diagnose(path, provider=args.provider)
    _print_checks(checks)
    failed = sum(c.status == "fail" for c in checks)
    print(f"\n{failed} problem(s)" if failed else "\nall checks passed")
    if not getattr(args, "deep", False):
        return 1 if failed else 0
    if checks and checks[0].name == "config" and checks[0].status == "fail":
        return 1
    return deep(path, args.stage, run_steps=not args.no_setup)


def _probe(project, stage: str):
    """One minimal real run of the stage; its envelope (None for pnr: no flow run from doctor)."""
    from ..core.project import case_specs
    from ..tools.regress import run_regress
    from ..tools.synth import run_synth
    if stage == "sim":
        case = project.setup.probe_case or case_specs(project)[0].name
        return run_regress(project, [case])
    if stage == "synth":
        return run_synth(project)
    return None


def _pnr_views(project) -> list[dict]:
    """pnr: the PDK and macro views and signoff decks the baseline run's config names, still on disk?"""
    from ..tools.flow import backend_of, resolve_run
    pnr = project.pnr
    if not pnr or not pnr.baseline:
        return []
    run = resolve_run(project, pnr.baseline)
    if isinstance(run, str):
        return []
    be = backend_of(project)
    return be.setup_issues(be.config(run), "")


def _print_issues(stage: str, issues: list[dict]) -> None:
    for i in issues:
        mark = "FATAL" if i.get("blocking", True) else "warn "
        who = "approved [setup] step" if i.get("who") == "setup_step" else "a person"
        print(f"  [{mark}] {stage} {i['category']}: missing {i['missing']}")
        print(f"          evidence: {i['evidence']}")
        print(f"          fix ({who}): {i['suggestion']}")


def deep(path: Path, stages: list[str] | None, run_steps: bool = True, out_dir: Path = DOCTOR_DIR) -> int:
    from ..core.project import load_project
    from ..core.setup_issues import precheck
    from ..tools.setup import run_setup
    project = load_project(path)
    configured = {"sim": True, "synth": project.flow is not None, "pnr": project.pnr is not None}
    stages = stages or [s for s in STAGES if configured[s]]
    record: dict = {"project": project.name, "config": str(path.resolve()), "stages": {}, "setup_runs": [],
                    "at": datetime.now().astimezone().isoformat(timespec="seconds")}
    setup_ran = False

    def maybe_setup(stage: str, issues: list[dict]) -> bool:
        nonlocal setup_ran
        if setup_ran or not run_steps or not project.setup.steps \
                or not any(i.get("who") == "setup_step" for i in issues):
            return False
        setup_ran = True
        res = run_setup(project, stage)
        record["setup_runs"].append(res.to_dict())
        print(f"\n[setup] {res.status}: {res.summary}")
        return True

    not_ready = 0
    for stage in stages:
        print(f"\n== {stage} ==")
        if not configured[stage]:
            print(f"  not configured (no [{'flow' if stage == 'synth' else stage}] section): skipped")
            record["stages"][stage] = {"status": "not_configured"}
            continue
        issues = blocking(precheck(project, stage))
        if issues and maybe_setup(stage, issues):
            issues = blocking(precheck(project, stage))
        result = None
        if not issues:
            result = _probe(project, stage)
            if result is not None and result.error_class == "setup" and \
                    maybe_setup(stage, result.metrics.get("setup_issues", [])):
                result = _probe(project, stage)
        found = dedupe(issues + ((result.metrics.get("setup_issues") or []) if result is not None else [])
                       + (_pnr_views(project) if stage == "pnr" and not issues else []))
        if result is not None:
            print(f"  minimal run ({result.tool}): {result.status}"
                  + (f" [{result.error_class}]" if result.error_class else "") + f": {result.summary[:300]}")
        elif stage == "pnr" and not issues:
            print("  prerequisites present; doctor does not start a flow run (about an hour): "
                  "`eda-agent flow-run --dry-run` shows the command")
        ready = not blocking(found) and (result is None or result.error_class != "setup")
        if ready and (result is None or result.status in ("pass", "fail")):
            print("  environment ready" + (" (the run itself reported a design result, see above)"
                                           if result is not None and result.status == "fail" else ""))
        elif ready:
            print("  no setup issue recognised, but the run did not finish: see its summary above")
        else:
            not_ready += 1
            print("  ENVIRONMENT NOT READY")
        _print_issues(stage, found)
        record["stages"][stage] = {"ready": ready, "issues": found,
                                   "result": result.to_dict() if result is not None else None}
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{project.name}_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(record, indent=1, ensure_ascii=False))
    print(f"\n{'environment not ready for ' + str(not_ready) + ' stage(s)' if not_ready else 'all stages ready'}"
          f"; record: {out}")
    return 1 if not_ready else 0
