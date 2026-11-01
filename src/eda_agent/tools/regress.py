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
from ..core.setup_issues import blocking, classify, context, dedupe, precheck, summary
from .formats import Outcome, exitcode, junit, torv

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


def _case_output(outcome: Outcome, name: str) -> str:
    log = outcome.logs.get(name)
    if log and Path(log).is_file():
        return Path(log).read_text(errors="replace")
    return outcome.diags.get(name, "")


def _setup_blocked(project: Project, outcome: Outcome) -> tuple[list[str], list[dict]] | None:
    """(cases, issues) when failed cases did not get to test anything: their output matches the setup
    rule table, not the fail pattern, and the testbench printed no summary of its own."""
    cfg = project.regress
    ctx = context(project)
    cases, issues = [], []
    for name, status, _ in outcome.rows:
        if status != "FAIL" or name in outcome.subtests:
            continue
        text = _case_output(outcome, name)
        if cfg.fail_regex and re.search(cfg.fail_regex, text, re.M):
            continue
        if found := blocking(classify(text, "sim", ctx)):
            cases.append(name)
            issues += found
    return (cases, issues) if cases else None


def _not_ready(project: Project, scope: str, versions: dict, issues: list[dict], cases: list[str],
               outcome: Outcome | str) -> ToolResult:
    """A regression that could not run its tests: Fatal "environment not ready", never a test failure."""
    issues = dedupe(issues + blocking(precheck(project, "sim")))
    rows = outcome.rows if isinstance(outcome, Outcome) else []
    passed = [r[0] for r in rows if r[1] == "PASS"]
    failed = [r[0] for r in rows if r[1] == "FAIL" and r[0] not in cases]
    return ToolResult(
        "regress", "error", "E2", scope,
        summary=f"{summary(issues)}. {len(passed)} passed, {len(failed)} failed; "
                f"{len(cases) or 'all'} case(s) could not run: this is not a test result",
        metrics={"setup_issues": issues, "setup_blocked_cases": cases, "passed_cases": passed,
                 "failed_cases": failed, "format": project.regress.format,
                 "case_logs": outcome.logs if isinstance(outcome, Outcome) else {}},
        diagnostics=[Diagnostic("error", "SETUP", f"{i['missing']}: {i['suggestion']}") for i in issues[:10]],
        diagnostics_total=len(issues),
        artifacts=outcome.artifacts if isinstance(outcome, Outcome) else [],
        tool_versions=versions, error_class="setup",
        residual_risk=["the environment was not ready: no conclusion about the design follows from this run"])


MAX_WAVE_CASES = 2  # waveforms are large: dump them for the failing case(s) only


def waves_files(cfg: RegressConfig, logs: dict[str, str], names: list[str]) -> dict[str, str]:
    """Case -> waveform file the regression wrote for it (only files that exist)."""
    out = {}
    for name in names:
        case_dir = str(Path(logs[name]).parent) if name in logs else ""
        p = Path(cfg.waves_file.format(name=name, case_dir=case_dir))
        if case_dir or p.is_absolute():
            if p.is_file():
                out[name] = str(p)
    return out


def run_regress(project: Project, cases: list[str] | None = None, max_diagnostics: int = 10,
                log_dir: Path = LOG_DIR, waves: bool = False) -> ToolResult:
    cfg = project.regress
    scope = claim_scope(cfg)
    specs = {s.name: s for s in case_specs(project)}
    cases = list(cases or [])
    bad = [c for c in cases if not _CASE_NAME.match(c) or c not in specs]
    if bad:
        return ToolResult("regress", "error", "E2", scope,
                          summary=f"unknown case(s) {bad}; call list_regress_cases first",
                          error_class="bad_request")
    if waves and not (cfg.waves_flag and cfg.waves_file):
        return ToolResult("regress", "error", "E2", scope, error_class="not_configured",
                          summary="this project's [regress] has no waves_flag/waves_file: waveforms cannot be dumped")
    if waves and not 1 <= len(cases) <= MAX_WAVE_CASES:
        return ToolResult("regress", "error", "E2", scope, error_class="bad_request",
                          summary=f"waves=true needs the failing case(s) by name, at most {MAX_WAVE_CASES}")

    if refused := check_allowed(list(cfg.cmd), project.root, project.allowed_commands):
        return ToolResult("regress", "error", "E2", scope, summary=refused, error_class="not_allowed")
    env = project_env(project)
    versions = project_versions(project)
    if cfg.format == "exitcode":
        # No "run everything" switch to rely on: run each case on its own.
        cmds = {c: [*cfg.cmd, *([cfg.waves_flag] if waves else []), *_case_args(cfg, specs[c])]
                for c in cases or specs}
        outcome = exitcode.run_cases(project, cmds, env, log_dir)
    else:
        cmd = [*cfg.cmd, *([cfg.waves_flag] if waves else [])]
        for c in cases:
            cmd += _case_args(cfg, specs[c])
        outcome = FORMATS[cfg.format].run_cases(project, cmd, env)
    if outcome == "timeout":
        return ToolResult("regress", "timeout", "E2", scope,
                          summary=f"regression timed out after {cfg.timeout_s} s",
                          tool_versions=versions, error_class="timeout")
    if isinstance(outcome, str):
        if issues := blocking(classify(outcome, "sim", context(project))):
            return _not_ready(project, scope, versions, issues, [], outcome)
        return ToolResult("regress", "error", "E2", scope, summary=outcome,
                          tool_versions=versions, error_class="infra")
    if blocked := _setup_blocked(project, outcome):
        return _not_ready(project, scope, versions, blocked[1], blocked[0], outcome)

    rows = outcome.rows
    failed = [r for r in rows if r[1] == "FAIL"]
    passed = [r for r in rows if r[1] == "PASS"]
    missing = [c for c in cases if c not in {r[0] for r in rows}] if cfg.format != "junit" else []
    diags = [Diagnostic("error", "CASE_FAIL", f"{name}: {outcome.diags.get(name, '')}".strip(), file=detail)
             for name, _, detail in failed[:max_diagnostics]]
    ok = bool(passed) and not failed and not missing
    metrics = {"passed": len(passed), "failed": len(failed), "cases": len(rows),
               "passed_cases": [r[0] for r in passed], "failed_cases": [r[0] for r in failed],
               "format": cfg.format, "case_logs": outcome.logs}
    if waves:
        metrics["waves"] = waves_files(cfg, outcome.logs, [r[0] for r in rows])
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
