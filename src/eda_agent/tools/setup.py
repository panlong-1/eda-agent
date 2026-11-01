"""run_setup: the project's approved preparation steps ([setup] steps), and nothing else.

A person writes the steps into the config (e.g. `make vendor`, `make regs`); the agent may run only
those, in their order, from the project root, through the command allowlist. Each step's command,
exit status, time and log (with sha256) are recorded, so the receipt shows what prepared the checkout.
A step that fails is diagnosed by the setup rule table like any other setup problem. Evidence level E0:
this prepares the environment and proves nothing about the design.
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from ..core.envelope import Diagnostic, ToolResult, artifact
from ..core.proc import check_allowed, run
from ..core.project import Project, project_env
from ..core.setup_issues import blocking, classify, context, summary

LOG_DIR = Path("runs/logs")
SCOPE = ("The project's approved [setup] steps ran in the project root; exit status and log of each are "
         "recorded. This prepares the checkout; it says nothing about the design.")
RISK = ["setup steps may fetch or generate files; what they produced is not checked beyond their exit status"]


def run_setup(project: Project, stage: str = "sim", log_dir: Path = LOG_DIR) -> ToolResult:
    steps = project.setup.steps
    if not steps:
        return ToolResult("run_setup", "error", "E0", "Nothing was run.", error_class="not_configured",
                          summary="the project config has no [setup] steps: a person adds the steps the agent may run, "
                                  'e.g. [setup] steps = [["make", "vendor"], ["make", "regs"]]')
    for s in steps:
        if refused := check_allowed(list(s), project.root, project.allowed_commands):
            return ToolResult("run_setup", "error", "E0", "Nothing was run.", summary=refused,
                              error_class="not_allowed")
    env = project_env(project)
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    done, arts, issues = [], [], []
    for n, s in enumerate(steps, 1):
        t0 = time.monotonic()
        proc = run(list(s), project.root, env, project.setup.timeout_s)
        log = log_dir / f"{project.name}_{stamp}_setup{n}.log"
        log.write_text(proc.output)
        arts.append(artifact(log.resolve()))
        done.append({"step": list(s), "returncode": proc.returncode, "duration_s": round(time.monotonic() - t0, 1),
                     "log": str(log.resolve())})
        if proc.returncode != 0:
            issues = classify(proc.output, stage, context(project))
            break
    failed = done[-1]["returncode"] != 0
    text = " && ".join(" ".join(d["step"]) for d in done)
    if not failed:
        return ToolResult("run_setup", "pass", "E0", SCOPE, summary=f"{len(done)} setup step(s) ran: {text}",
                          metrics={"steps": done}, artifacts=arts, residual_risk=RISK)
    last = done[-1]
    what = "timed out" if last["returncode"] is None else f"exit {last['returncode']}"
    return ToolResult(
        "run_setup", "timeout" if last["returncode"] is None else "error", "E0", SCOPE,
        summary=(summary(blocking(issues)) + "; " if blocking(issues) else "")
        + f"setup step `{' '.join(last['step'])}` failed ({what})",
        metrics={"steps": done, "setup_issues": issues}, artifacts=arts, residual_risk=RISK,
        error_class="setup",
        diagnostics=[Diagnostic("error", "SETUP", f"{i['missing']}: {i['suggestion']}") for i in issues[:10]],
        diagnostics_total=len(issues))
