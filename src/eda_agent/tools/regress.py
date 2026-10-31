"""Run the project's own regression and read its results (evidence level E2).

Projects report results in different ways; `[regress] format` picks the reader in
formats/: torv (summary.rpt in a result directory), junit (a JUnit XML file) or
exitcode (the exit status per case, optionally confirmed by a pass/fail regex).
Each format says in its claim_scope what it can and cannot tell.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..core.envelope import Diagnostic, ToolResult
from ..core.proc import check_allowed
from ..core.project import CaseSpec, Project, RegressConfig, case_specs, project_env, project_versions
from .formats import exitcode, junit, torv

FORMATS = {"torv": torv, "junit": junit, "exitcode": exitcode}

LOG_DIR = Path("runs/logs")
_CASE_NAME = re.compile(r"^[A-Za-z0-9_.\-]+$")


def claim_scope(cfg: RegressConfig) -> str:
    pattern = f" and printed /{cfg.pass_regex}/" if cfg.pass_regex else ""
    scope = FORMATS[cfg.format].CLAIM_SCOPE.format(pattern=pattern)
    if cfg.summary_regex:
        scope += exitcode.SUBTEST_SCOPE.format(regex=cfg.summary_regex)
    return scope


def _case_args(cfg: RegressConfig, spec: CaseSpec) -> list[str]:
    if spec.args:
        return list(spec.args)
    return [cfg.case_flag, spec.name] if cfg.case_flag else []


def run_regress(project: Project, cases: list[str] | None = None, max_diagnostics: int = 10,
                log_dir: Path = LOG_DIR) -> ToolResult:
    cfg = project.regress
    scope = claim_scope(cfg)
    specs = {s.name: s for s in case_specs(project)}
    cases = list(cases or [])
    bad = [c for c in cases if not _CASE_NAME.match(c) or c not in specs]
    if bad:
        return ToolResult("regress", "error", "E2", scope,
                          summary=f"unknown case(s) {bad}; call list_regress_cases first",
                          error_class="bad_request")

    if refused := check_allowed(list(cfg.cmd), project.root, project.allowed_commands):
        return ToolResult("regress", "error", "E2", scope, summary=refused, error_class="not_allowed")
    env = project_env(project)
    versions = project_versions(project)
    if cfg.format == "exitcode":
        # No "run everything" switch to rely on: run each case on its own.
        cmds = {c: [*cfg.cmd, *_case_args(cfg, specs[c])] for c in cases or specs}
        outcome = exitcode.run_cases(project, cmds, env, log_dir)
    else:
        cmd = [*cfg.cmd]
        for c in cases:
            cmd += _case_args(cfg, specs[c])
        outcome = FORMATS[cfg.format].run_cases(project, cmd, env)
    if outcome == "timeout":
        return ToolResult("regress", "timeout", "E2", scope,
                          summary=f"regression timed out after {cfg.timeout_s} s",
                          tool_versions=versions, error_class="timeout")
    if isinstance(outcome, str):
        return ToolResult("regress", "error", "E2", scope, summary=outcome,
                          tool_versions=versions, error_class="infra")

    rows = outcome.rows
    failed = [r for r in rows if r[1] == "FAIL"]
    passed = [r for r in rows if r[1] == "PASS"]
    missing = [c for c in cases if c not in {r[0] for r in rows}] if cfg.format != "junit" else []
    diags = [Diagnostic("error", "CASE_FAIL", f"{name}: {outcome.diags.get(name, '')}".strip(), file=detail)
             for name, _, detail in failed[:max_diagnostics]]
    ok = bool(passed) and not failed and not missing
    metrics = {"passed": len(passed), "failed": len(failed), "cases": len(rows),
               "passed_cases": [r[0] for r in passed], "failed_cases": [r[0] for r in failed],
               "format": cfg.format}
    sub_text = ""
    if outcome.subtests:
        totals = {k: sum(s.get(k, 0) for s in outcome.subtests.values()) for k in ("passed", "failed", "total")}
        metrics |= {"subtests": outcome.subtests, "subtests_passed": totals["passed"],
                    "subtests_failed": totals["failed"], "subtests_total": totals["total"]}
        sub_text = (f"; testbench sub-tests: {totals['passed']} passed, {totals['failed']} failed "
                    f"of {totals['total']}")
    risks = list(FORMATS[cfg.format].RESIDUAL_RISK)
    if outcome.subtests:
        risks = [exitcode.SUBTEST_RISK if "which sub-tests ran" in r else r for r in risks]
    return ToolResult(
        tool="regress",
        status="pass" if ok else "fail",
        evidence_level="E2",
        claim_scope=scope,
        summary=f"{len(passed)} passed, {len(failed)} failed, {len(rows) - len(passed) - len(failed)} skipped"
                + ("" if cases else " (full list)")
                + (f"; requested but not reported: {missing}" if missing else "") + sub_text,
        metrics=metrics,
        diagnostics=diags,
        diagnostics_total=len(failed),
        artifacts=outcome.artifacts,
        tool_versions=versions,
        residual_risk=risks
        + ([] if versions else ["tool versions not recorded: the project config has no [versions] table"]),
    )
