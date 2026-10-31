"""Format exitcode: one command per case, judged by its exit status and optional pass/fail regexes."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from ...core.envelope import artifact
from ...core.proc import run
from ...core.project import Project, RegressConfig
from . import NOT_ADEQUACY, ADEQUACY_RISK, Outcome, tail

CLAIM_SCOPE = ("Each case's command exited 0{pattern}. There is no per-check detail: one exit "
               "status may cover many tests, and a test that checks nothing looks the same. "
               + NOT_ADEQUACY)
RESIDUAL_RISK = [ADEQUACY_RISK, "only an exit status (and output pattern) was observed: no per-check "
                                "detail, so which sub-tests ran is not known"]


SUBTEST_SCOPE = (" The sub-test counts are the testbench's own summary line (/{regex}/), read as "
                 "printed; what each sub-test checks is the testbench's business.")
SUBTEST_RISK = "sub-test counts come from the testbench's own summary line, not from separate results"


def read_subtests(output: str, cfg: RegressConfig) -> dict | None:
    """The testbench's own counts from its summary line (the last match), or None."""
    if not cfg.summary_regex:
        return None
    found = list(re.finditer(cfg.summary_regex, output, re.M))
    if not found:
        return None
    got = {k: int(v) for k, v in found[-1].groupdict().items() if v is not None}
    if "total" in got and "passed" in got and "failed" not in got:
        got["failed"] = got["total"] - got["passed"]
    if "total" not in got and {"passed", "failed"} <= set(got):
        got["total"] = got["passed"] + got["failed"]
    return got


def judge_exitcode(returncode: int | None, output: str, cfg: RegressConfig) -> tuple[str, str]:
    """PASS only on exit 0, the pass regex found (if set), the fail regex absent (if set) and,
    with a summary_regex, the testbench's own summary found and reporting no failure."""
    if returncode is None:
        return "FAIL", f"timed out after {cfg.timeout_s} s"
    if returncode != 0:
        return "FAIL", f"exit status {returncode}"
    if cfg.fail_regex and re.search(cfg.fail_regex, output, re.M):
        return "FAIL", f"output matches fail pattern /{cfg.fail_regex}/"
    if cfg.pass_regex and not re.search(cfg.pass_regex, output, re.M):
        return "FAIL", f"exit 0 but pass pattern /{cfg.pass_regex}/ not found"
    sub = read_subtests(output, cfg)
    if cfg.summary_regex and sub is None:
        return "FAIL", f"exit 0 but the testbench summary /{cfg.summary_regex}/ was not printed"
    if sub and (sub.get("failed", 0) > 0 or sub.get("passed", 0) < sub.get("total", 0)):
        return "FAIL", f"testbench reports {sub.get('failed', 0)} failed of {sub.get('total', '?')}"
    counts = f", testbench: {sub.get('passed', '?')}/{sub.get('total', '?')} passed" if sub else ""
    return "PASS", "exit 0" + (f", /{cfg.pass_regex}/ found" if cfg.pass_regex else "") + counts


def run_cases(project: Project, cmds: dict[str, list[str]], env: dict, log_dir: Path) -> Outcome:
    """cmds: case name -> its full command; each runs on its own and keeps its own log."""
    cfg = project.regress
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Outcome(rows=[])
    for name, cmd in cmds.items():
        proc = run(cmd, project.root, env, timeout_s=cfg.timeout_s)
        status, detail = judge_exitcode(proc.returncode, proc.output, cfg)
        log = log_dir / f"{project.name}_{stamp}_{name}.log"
        log.write_text(proc.output)
        out.rows.append((name, status, detail))
        out.artifacts.append(artifact(log.resolve()))
        if (sub := read_subtests(proc.output, cfg)) is not None:
            out.subtests[name] = sub
        if status == "FAIL":
            out.diags[name] = f"{detail}\n{tail(proc.output)}"
    return out
