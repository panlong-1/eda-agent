"""Run the project's own regression and read its summary (evidence level E2)."""
from __future__ import annotations

import re
from pathlib import Path

from ..envelope import Diagnostic, ToolResult, artifact
from ..projects import Project, project_env, regress_cases
from .proc import run, tool_version

CLAIM_SCOPE = (
    "The listed directed cases ran to completion and each reported pass through the "
    "testbench's own self-check. It does not show that the tests would catch a bug "
    "(no coverage or mutation score), and says nothing about timing or power."
)
_RESULT_DIR = re.compile(r"^Result directory:\s*(\S+)", re.M)
_CASE_NAME = re.compile(r"^[A-Za-z0-9_]+$")


def parse_summary(text: str) -> list[tuple[str, str, str]]:
    """Rows of name,STATUS,detail; header and comment lines are skipped."""
    rows = []
    for line in text.splitlines():
        parts = line.strip().split(",", 2)
        if len(parts) >= 2 and parts[1] in ("PASS", "FAIL", "SKIP"):
            rows.append((parts[0], parts[1], parts[2] if len(parts) > 2 else ""))
    return rows


def _log_tail(case_dir: str, name: str, lines: int = 8) -> str:
    log = Path(case_dir) / f"{name}.sim.log"
    if not log.is_file():
        return ""
    return "\n".join(log.read_text(errors="replace").splitlines()[-lines:])


def run_regress(project: Project, cases: list[str] | None = None, max_diagnostics: int = 10) -> ToolResult:
    known = regress_cases(project)
    cases = list(cases or [])
    bad = [c for c in cases if not _CASE_NAME.match(c) or c not in known]
    if bad:
        return ToolResult("regress", "error", "E2", CLAIM_SCOPE,
                          summary=f"unknown case(s) {bad}; call list_regress_cases first",
                          error_class="bad_request")

    cfg = project.regress
    cmd = [*cfg.cmd]
    for c in cases:
        cmd += [cfg.case_flag, c]
    env = project_env(project)
    proc = run(cmd, project.root, env, timeout_s=cfg.timeout_s)
    version = tool_version(["verilator", "--version"], env)
    if proc.returncode is None:
        return ToolResult("regress", "timeout", "E2", CLAIM_SCOPE,
                          summary=f"regression timed out after {cfg.timeout_s} s",
                          tool_version=version, error_class="timeout")

    m = _RESULT_DIR.search(proc.output)
    summary_path = Path(m.group(1)) / cfg.summary_file if m else None
    if not summary_path or not summary_path.is_file():
        return ToolResult("regress", "error", "E2", CLAIM_SCOPE,
                          summary="no summary produced; tail of output:\n" + proc.output[-800:],
                          tool_version=version, error_class="infra")

    rows = parse_summary(summary_path.read_text())
    failed = [r for r in rows if r[1] == "FAIL"]
    passed = [r for r in rows if r[1] == "PASS"]
    diags = [Diagnostic("error", "CASE_FAIL", f"{name}: {_log_tail(detail, name)}".strip(), file=detail)
             for name, _, detail in failed[:max_diagnostics]]
    ok = bool(passed) and not failed
    return ToolResult(
        tool="regress",
        status="pass" if ok else "fail",
        evidence_level="E2",
        claim_scope=CLAIM_SCOPE,
        summary=f"{len(passed)} passed, {len(failed)} failed, {len(rows) - len(passed) - len(failed)} skipped"
                + ("" if cases else " (full list)"),
        metrics={"passed": len(passed), "failed": len(failed), "cases": len(rows),
                 "failed_cases": [r[0] for r in failed]},
        diagnostics=diags,
        diagnostics_total=len(failed),
        artifacts=[artifact(summary_path)],
        tool_version=version,
        residual_risk=["test adequacy unknown: no coverage or mutation score yet",
                       "pass/fail is reported by the test program itself"],
    )
