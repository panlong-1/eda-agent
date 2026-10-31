"""The three regression result formats, driven by small shell commands instead of a simulator."""
import os
import time
from pathlib import Path

import pytest

from eda_agent.projects import RegressConfig, load_project
from eda_agent.tools.regress import judge_exitcode, parse_junit, run_regress

JUNIT = """\
<testsuites><testsuite name="tb">
  <testcase classname="tb" name="test_reset"/>
  <testcase classname="tb" name="test_overflow"><failure message="count 17 != 16">trace</failure></testcase>
  <testcase classname="tb" name="test_slow"><skipped/></testcase>
  <testcase classname="tb" name="test_x"><error message="sim crashed"/></testcase>
</testsuite></testsuites>
"""


def _project(tmp_path: Path, regress: str, cases: str = ""):
    toml = tmp_path / "p.toml"
    toml.write_text(f'name = "p"\nroot = "{tmp_path}"\n[lint]\ntop = "t"\n[regress]\n{regress}\n{cases}')
    return load_project(toml)


def test_parse_junit():
    assert parse_junit(JUNIT) == [("test_reset", "PASS", ""), ("test_overflow", "FAIL", "count 17 != 16"),
                                  ("test_slow", "SKIP", ""), ("test_x", "FAIL", "sim crashed")]


def test_judge_exitcode():
    cfg = RegressConfig(cmd=("x",), case_list="l", format="exitcode",
                        pass_regex="^ALL TESTS PASSED", fail_regex="^ERROR")
    assert judge_exitcode(0, "ALL TESTS PASSED.\n", cfg)[0] == "PASS"
    assert judge_exitcode(0, "TRAP\nERROR!\n", cfg) == ("FAIL", "output matches fail pattern /^ERROR/")
    assert "not found" in judge_exitcode(0, "done\n", cfg)[1]
    assert judge_exitcode(2, "ALL TESTS PASSED\n", cfg) == ("FAIL", "exit status 2")
    assert judge_exitcode(None, "", cfg)[1].startswith("timed out")
    plain = RegressConfig(cmd=("x",), case_list="l", format="exitcode")
    assert judge_exitcode(0, "", plain) == ("PASS", "exit 0")


def test_config_validation():
    with pytest.raises(ValueError, match="unknown regress format"):
        RegressConfig(cmd=("x",), case_list="l", format="vcs")
    with pytest.raises(ValueError, match="junit_file"):
        RegressConfig(cmd=("x",), case_list="l", format="junit")
    with pytest.raises(ValueError, match="case_list or inline"):
        RegressConfig(cmd=("x",))


EXITCODE_CASES = """
[[regress.cases]]
name = "good"
args = ["echo ALL TESTS PASSED"]
[[regress.cases]]
name = "silent"
args = ["true"]
[[regress.cases]]
name = "crash"
args = ["echo boom; exit 3"]
"""


def test_exitcode_format(tmp_path):
    project = _project(tmp_path, 'format = "exitcode"\ncmd = ["sh", "-c"]\ncase_flag = ""\n'
                                 'pass_regex = "^ALL TESTS PASSED"', EXITCODE_CASES)
    r = run_regress(project, ["good"], log_dir=tmp_path / "logs")
    assert (r.status, r.evidence_level, r.metrics["passed_cases"]) == ("pass", "E2", ["good"])
    assert "no per-check detail" in r.claim_scope and "/^ALL TESTS PASSED/" in r.claim_scope
    assert any("exit status" in x for x in r.residual_risk)
    assert Path(r.artifacts[0]["path"]).read_text() == "ALL TESTS PASSED\n" and r.artifacts[0]["sha256"]

    full = run_regress(project, log_dir=tmp_path / "logs")  # no cases: each case runs on its own
    assert full.status == "fail" and full.metrics["failed_cases"] == ["silent", "crash"]
    assert "pass pattern" in full.diagnostics[0].msg and "exit status 3" in full.diagnostics[1].msg
    assert "boom" in full.diagnostics[1].msg and full.summary.endswith("(full list)")


def test_junit_format(tmp_path):
    (tmp_path / "template.xml").write_text(JUNIT)
    project = _project(tmp_path, 'format = "junit"\ncmd = ["cp", "template.xml"]\ncase_flag = ""\n'
                                 'junit_file = "results.xml"',
                       '[[regress.cases]]\nname = "results"\nargs = ["results.xml"]\n')
    r = run_regress(project, ["results"])
    assert r.status == "fail" and (r.metrics["passed"], r.metrics["failed"]) == (1, 2)
    assert r.diagnostics[0].msg == "test_overflow: count 17 != 16"
    assert "JUnit report" in r.claim_scope and r.artifacts[0]["path"].endswith("results.xml")


def test_junit_stale_report_is_an_error(tmp_path):
    (tmp_path / "results.xml").write_text(JUNIT)
    old = time.time() - 3600
    os.utime(tmp_path / "results.xml", (old, old))
    project = _project(tmp_path, 'format = "junit"\ncmd = ["true"]\ncase_flag = ""\njunit_file = "results.xml"',
                       '[[regress.cases]]\nname = "all"\n')
    r = run_regress(project, ["all"])
    assert r.status == "error" and "stale" in r.summary


def test_torv_format(tmp_path):
    script = tmp_path / "regress.sh"
    script.write_text('#!/bin/sh\nd="$(pwd)/out"; mkdir -p "$d"\n'
                      'printf "case,status,detail\\n%s,PASS,%s\\n" "$2" "$d" > "$d/summary.rpt"\n'
                      'echo "Result directory: $d"\n')
    script.chmod(0o755)
    (tmp_path / "cases.list").write_text("smoke asm x.S\nalu asm y.S\n")
    project = _project(tmp_path, 'cmd = ["./regress.sh"]\ncase_list = "cases.list"')
    r = run_regress(project, ["alu"])
    assert r.status == "pass" and r.metrics["passed_cases"] == ["alu"]
    assert "testbench's own self-check" in r.claim_scope and r.artifacts[0]["path"].endswith("summary.rpt")
    bad = run_regress(project, ["nope"])
    assert bad.status == "error" and bad.error_class == "bad_request"
