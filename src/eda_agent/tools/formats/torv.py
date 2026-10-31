"""Format torv: "Result directory: <dir>" in the output, <dir>/<summary_file> rows name,STATUS,detail."""
from __future__ import annotations

import re
from pathlib import Path

from ...core.envelope import artifact
from ...core.proc import run
from ...core.project import Project
from . import NOT_ADEQUACY, ADEQUACY_RISK, Outcome, Row

CLAIM_SCOPE = ("The listed directed cases ran to completion and each reported pass through the "
               "testbench's own self-check. " + NOT_ADEQUACY)
RESIDUAL_RISK = [ADEQUACY_RISK, "pass/fail is reported by the test program itself"]

_RESULT_DIR = re.compile(r"^Result directory:\s*(\S+)", re.M)


def parse_summary(text: str) -> list[Row]:
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


def run_cases(project: Project, cmd: list[str], env: dict) -> Outcome | str:
    cfg = project.regress
    proc = run(cmd, project.root, env, timeout_s=cfg.timeout_s)
    if proc.returncode is None:
        return "timeout"
    m = _RESULT_DIR.search(proc.output)
    summary_path = Path(m.group(1)) / cfg.summary_file if m else None
    if not summary_path or not summary_path.is_file():
        return "no summary produced; tail of output:\n" + proc.output[-800:]
    rows = parse_summary(summary_path.read_text())
    diags = {name: _log_tail(detail, name) for name, status, detail in rows if status == "FAIL"}
    return Outcome(rows, [artifact(summary_path)], diags)
