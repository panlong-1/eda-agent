"""Run the project's own regression and read its results (evidence level E2).

Projects report results in different ways; `[regress] format` picks the reader:
torv (summary.rpt in a result directory), junit (a JUnit XML file) or exitcode
(the exit status per case, optionally confirmed by a pass/fail regex). Each format
says in its claim_scope what it can and cannot tell.
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..envelope import Diagnostic, ToolResult, artifact
from ..projects import CaseSpec, Project, RegressConfig, case_specs, project_env
from .proc import run, tool_version

_NOT_ADEQUACY = ("It does not show that the tests would catch a bug (no coverage or mutation "
                 "score), and says nothing about timing or power.")
CLAIM_SCOPES = {
    "torv": "The listed directed cases ran to completion and each reported pass through the "
            "testbench's own self-check. " + _NOT_ADEQUACY,
    "junit": "Each testcase in this run's JUnit report passed or failed by the harness's own "
             "assertions; a case missing from the report did not run. " + _NOT_ADEQUACY,
    "exitcode": "Each case's command exited 0{pattern}. There is no per-check detail: one exit "
                "status may cover many tests, and a test that checks nothing looks the same. "
                + _NOT_ADEQUACY,
}
_ADEQUACY_RISK = "test adequacy unknown: no coverage or mutation score yet"
RESIDUAL_RISKS = {
    "torv": [_ADEQUACY_RISK, "pass/fail is reported by the test program itself"],
    "junit": [_ADEQUACY_RISK, "pass/fail is decided by the harness's assertions; the JUnit file is "
                              "the only record of what was checked"],
    "exitcode": [_ADEQUACY_RISK, "only an exit status (and output pattern) was observed: no per-check "
                                 "detail, so which sub-tests ran is not known"],
}

LOG_DIR = Path("runs/logs")
_RESULT_DIR = re.compile(r"^Result directory:\s*(\S+)", re.M)
_CASE_NAME = re.compile(r"^[A-Za-z0-9_.\-]+$")

Row = tuple[str, str, str]  # name, PASS/FAIL/SKIP, detail


@dataclass
class _Outcome:
    rows: list[Row]
    artifacts: list[dict] = field(default_factory=list)
    diags: dict[str, str] = field(default_factory=dict)  # failing case -> detail text


def claim_scope(cfg: RegressConfig) -> str:
    pattern = f" and printed /{cfg.pass_regex}/" if cfg.pass_regex else ""
    return CLAIM_SCOPES[cfg.format].format(pattern=pattern)


def parse_summary(text: str) -> list[Row]:
    """Rows of name,STATUS,detail; header and comment lines are skipped."""
    rows = []
    for line in text.splitlines():
        parts = line.strip().split(",", 2)
        if len(parts) >= 2 and parts[1] in ("PASS", "FAIL", "SKIP"):
            rows.append((parts[0], parts[1], parts[2] if len(parts) > 2 else ""))
    return rows


def parse_junit(text: str) -> list[Row]:
    """One row per <testcase>; failure/error -> FAIL, skipped -> SKIP, with the message as detail."""
    rows = []
    for tc in ET.fromstring(text).iter("testcase"):
        bad = tc.find("failure")
        if bad is None:
            bad = tc.find("error")
        if bad is not None:
            rows.append((tc.get("name", "?"), "FAIL", (bad.get("message") or bad.text or "").strip()[:300]))
        elif tc.find("skipped") is not None:
            rows.append((tc.get("name", "?"), "SKIP", ""))
        else:
            rows.append((tc.get("name", "?"), "PASS", ""))
    return rows


def judge_exitcode(returncode: int | None, output: str, cfg: RegressConfig) -> tuple[str, str]:
    """PASS only on exit 0, the pass regex found (if set) and the fail regex absent (if set)."""
    if returncode is None:
        return "FAIL", f"timed out after {cfg.timeout_s} s"
    if returncode != 0:
        return "FAIL", f"exit status {returncode}"
    if cfg.fail_regex and re.search(cfg.fail_regex, output, re.M):
        return "FAIL", f"output matches fail pattern /{cfg.fail_regex}/"
    if cfg.pass_regex and not re.search(cfg.pass_regex, output, re.M):
        return "FAIL", f"exit 0 but pass pattern /{cfg.pass_regex}/ not found"
    return "PASS", "exit 0" + (f", /{cfg.pass_regex}/ found" if cfg.pass_regex else "")


def _case_args(cfg: RegressConfig, spec: CaseSpec) -> list[str]:
    if spec.args:
        return list(spec.args)
    return [cfg.case_flag, spec.name] if cfg.case_flag else []


def _log_tail(case_dir: str, name: str, lines: int = 8) -> str:
    log = Path(case_dir) / f"{name}.sim.log"
    if not log.is_file():
        return ""
    return "\n".join(log.read_text(errors="replace").splitlines()[-lines:])


def _tail(text: str, lines: int = 8) -> str:
    return "\n".join(text.splitlines()[-lines:])


def _run_torv(project: Project, specs: list[CaseSpec], env: dict) -> _Outcome | str:
    cfg = project.regress
    cmd = [*cfg.cmd]
    for s in specs:
        cmd += _case_args(cfg, s)
    proc = run(cmd, project.root, env, timeout_s=cfg.timeout_s)
    if proc.returncode is None:
        return "timeout"
    m = _RESULT_DIR.search(proc.output)
    summary_path = Path(m.group(1)) / cfg.summary_file if m else None
    if not summary_path or not summary_path.is_file():
        return "no summary produced; tail of output:\n" + proc.output[-800:]
    rows = parse_summary(summary_path.read_text())
    diags = {name: _log_tail(detail, name) for name, status, detail in rows if status == "FAIL"}
    return _Outcome(rows, [artifact(summary_path)], diags)


def _run_junit(project: Project, specs: list[CaseSpec], env: dict) -> _Outcome | str:
    cfg = project.regress
    cmd = [*cfg.cmd]
    for s in specs:
        cmd += _case_args(cfg, s)
    started = time.time()
    proc = run(cmd, project.root, env, timeout_s=cfg.timeout_s)
    if proc.returncode is None:
        return "timeout"
    report = project.root / cfg.junit_file
    if not report.is_file():
        return f"no JUnit file at {cfg.junit_file}; tail of output:\n" + proc.output[-800:]
    if report.stat().st_mtime < started - 1:
        return f"{cfg.junit_file} was not rewritten by this run (stale report)"
    try:
        rows = parse_junit(report.read_text(errors="replace"))
    except ET.ParseError as e:
        return f"unreadable JUnit file {cfg.junit_file}: {e}"
    return _Outcome(rows, [artifact(report)], {n: d for n, s, d in rows if s == "FAIL"})


def _run_exitcode(project: Project, specs: list[CaseSpec], env: dict, log_dir: Path) -> _Outcome | str:
    cfg = project.regress
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = _Outcome(rows=[])
    for s in specs:
        proc = run([*cfg.cmd, *_case_args(cfg, s)], project.root, env, timeout_s=cfg.timeout_s)
        status, detail = judge_exitcode(proc.returncode, proc.output, cfg)
        log = log_dir / f"{project.name}_{stamp}_{s.name}.log"
        log.write_text(proc.output)
        out.rows.append((s.name, status, detail))
        out.artifacts.append(artifact(log.resolve()))
        if status == "FAIL":
            out.diags[s.name] = f"{detail}\n{_tail(proc.output)}"
    return out


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

    env = project_env(project)
    version = tool_version(["verilator", "--version"], env)
    if cfg.format == "exitcode":
        # No "run everything" switch to rely on: run each case on its own.
        outcome = _run_exitcode(project, [specs[c] for c in cases or specs], env, log_dir)
    elif cfg.format == "junit":
        outcome = _run_junit(project, [specs[c] for c in cases], env)
    else:
        outcome = _run_torv(project, [specs[c] for c in cases], env)
    if outcome == "timeout":
        return ToolResult("regress", "timeout", "E2", scope,
                          summary=f"regression timed out after {cfg.timeout_s} s",
                          tool_version=version, error_class="timeout")
    if isinstance(outcome, str):
        return ToolResult("regress", "error", "E2", scope, summary=outcome,
                          tool_version=version, error_class="infra")

    rows = outcome.rows
    failed = [r for r in rows if r[1] == "FAIL"]
    passed = [r for r in rows if r[1] == "PASS"]
    missing = [c for c in cases if c not in {r[0] for r in rows}] if cfg.format != "junit" else []
    diags = [Diagnostic("error", "CASE_FAIL", f"{name}: {outcome.diags.get(name, '')}".strip(), file=detail)
             for name, _, detail in failed[:max_diagnostics]]
    ok = bool(passed) and not failed and not missing
    return ToolResult(
        tool="regress",
        status="pass" if ok else "fail",
        evidence_level="E2",
        claim_scope=scope,
        summary=f"{len(passed)} passed, {len(failed)} failed, {len(rows) - len(passed) - len(failed)} skipped"
                + ("" if cases else " (full list)")
                + (f"; requested but not reported: {missing}" if missing else ""),
        metrics={"passed": len(passed), "failed": len(failed), "cases": len(rows),
                 "passed_cases": [r[0] for r in passed], "failed_cases": [r[0] for r in failed],
                 "format": cfg.format},
        diagnostics=diags,
        diagnostics_total=len(failed),
        artifacts=outcome.artifacts,
        tool_version=version,
        residual_risk=list(RESIDUAL_RISKS[cfg.format]),
    )
