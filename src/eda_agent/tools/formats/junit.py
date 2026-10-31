"""Format junit: a JUnit XML file written by the run (rejected if this run did not rewrite it)."""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET

from ...core.envelope import artifact
from ...core.proc import run
from ...core.project import Project
from . import NOT_ADEQUACY, ADEQUACY_RISK, Outcome, Row

CLAIM_SCOPE = ("Each testcase in this run's JUnit report passed or failed by the harness's own "
               "assertions; a case missing from the report did not run. " + NOT_ADEQUACY)
RESIDUAL_RISK = [ADEQUACY_RISK, "pass/fail is decided by the harness's assertions; the JUnit file is "
                                "the only record of what was checked"]


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


def run_cases(project: Project, cmd: list[str], env: dict) -> Outcome | str:
    cfg = project.regress
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
    return Outcome(rows, [artifact(report)], {n: d for n, s, d in rows if s == "FAIL"})
