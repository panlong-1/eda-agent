"""`eda-agent doctor`: is everything a project config names actually there?

Each check says ok, warn or fail and why, so a missing tool or path shows up before a
run, not halfway through it. Values of secrets are never printed, only whether they are set.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ..core.proc import check_allowed, tool_version
from ..core.project import Project, expand_vars, load_project, project_env, regress_cases

API_KEYS = {"anthropic": "ANTHROPIC_API_KEY", "deepseek": "DEEPSEEK_API_KEY"}


@dataclass
class Check:
    status: str  # ok | warn | fail
    name: str
    detail: str


def _exists(project: Project, rel: str, env: dict) -> bool:
    p = Path(expand_vars(rel, env))
    return (p if p.is_absolute() else project.root / p).exists()


def _paths(project: Project, env: dict, what: str, items) -> list[Check]:
    missing = [i for i in items if not _exists(project, i, env)]
    if not items:
        return []
    if missing:
        return [Check("fail", what, f"missing: {', '.join(missing)}")]
    return [Check("ok", what, f"{len(items)} present")]


def _on_path(cmd0: str, project: Project, env: dict) -> bool:
    if "/" in cmd0:
        return (project.root / cmd0).is_file()
    return shutil.which(cmd0, path=env.get("PATH")) is not None


def diagnose(config: Path, provider: str | None = None) -> list[Check]:
    checks: list[Check] = []
    try:
        project = load_project(config)
    except Exception as e:  # a config that does not load is the first thing to fix
        return [Check("fail", "config", f"{config}: {type(e).__name__}: {e}")]
    checks.append(Check("ok", "config", f"{config} loads (project {project.name})"))
    if not project.root.is_dir():
        return checks + [Check("fail", "root", f"{project.root} is not a directory")]
    checks.append(Check("ok", "root", str(project.root)))
    try:
        env = project_env(project)
        if project.env_script:
            checks.append(Check("ok", "env_script", f"{project.env_script} sources cleanly"))
    except subprocess.CalledProcessError as e:
        return checks + [Check("fail", "env_script", f"{project.env_script} failed (exit {e.returncode})")]

    # tools named in [versions]
    for name, cmd in project.versions.items():
        v = tool_version(list(cmd), env)
        checks.append(Check("ok" if v != "unknown" else "fail", f"tool {name}", v if v != "unknown" else
                            f"`{' '.join(cmd)}` did not run: is {cmd[0]} installed and on PATH?"))

    lint = project.lint
    checks += _paths(project, env, "lint files", list(lint.filelists) + list(lint.files))
    for cf in lint.command_files:
        ok = (project.root / cf).is_file()
        checks.append(Check("ok" if ok else "fail", "lint command file", cf if ok else
                            f"{cf} missing: regenerate it (`eda-agent init` again, or the project's lint setup)"))
    if lint.top.startswith("TODO"):
        checks.append(Check("fail", "lint top", "still TODO: write the top module name"))

    reg = project.regress
    if reg.cmd and reg.cmd[0] == "TODO":
        checks.append(Check("fail", "regress cmd", "still TODO: write the regression command"))
    else:
        refused = check_allowed(list(reg.cmd), project.root, project.allowed_commands)
        checks.append(Check("fail" if refused else "ok", "regress allowlist", refused or " ".join(reg.cmd)))
        if not _on_path(reg.cmd[0], project, env):
            checks.append(Check("fail", "regress cmd", f"{reg.cmd[0]} not found"))
    if reg.case_list:
        checks += _paths(project, env, "case list", [reg.case_list])
    if reg.format == "exitcode" and not reg.pass_regex:
        checks.append(Check("warn", "regress pass_regex", "not set: exit status alone decides pass, and a "
                                                          "testbench that always exits 0 would always pass"))

    flow = project.flow
    if flow is None:
        checks.append(Check("warn", "flow", "no [flow]: synth and check_sdc will refuse"))
    else:
        checks += _paths(project, env, "flow sdc", [flow.sdc])
        checks += _paths(project, env, "flow liberty", [flow.liberty, *flow.corners.values()])
        checks += _paths(project, env, "flow files", list(flow.files) + list(flow.filelists) + list(flow.command_files))
        if shutil.which("docker") is None:
            checks.append(Check("fail", "docker", "not installed; synth runs in a container"))
        else:
            r = subprocess.run(["docker", "image", "inspect", flow.image], capture_output=True)
            checks.append(Check("ok" if r.returncode == 0 else "fail", "flow image", flow.image if r.returncode == 0
                                else f"{flow.image} not present: docker pull {flow.image}"))

    mut = project.mutation
    if mut is not None:
        checks += _paths(project, env, "mutation files", list(mut.files))
        known = set(regress_cases(project))
        unknown = [c for c in mut.cases if c not in known]
        checks.append(Check("fail" if unknown else "ok", "mutation cases",
                            f"not in the case list: {unknown}" if unknown else f"{len(mut.cases) or len(known)} cases"))
        avl = (Path(sys.executable).parent / "avl-mutation-testing").is_file() or shutil.which("avl-mutation-testing")
        checks.append(Check("ok" if avl else "fail", "avl-core", "avl-mutation-testing found" if avl else
                            "not installed: pip install -e '.[mutation]' (mutate generates mutants with it)"))

    for prov, var in API_KEYS.items():
        if provider and prov != provider:
            continue
        state = "set" if os.environ.get(var) else "not set"
        status = "ok" if state == "set" else ("fail" if provider else "warn")
        checks.append(Check(status, f"api key {prov}", f"{var} {state}"))
    return checks
