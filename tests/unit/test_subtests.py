"""summary_regex: the testbench's own sub-test counts, read from its summary line."""
from pathlib import Path

import pytest

from eda_agent.agent.gate import check_report
from eda_agent.core.project import RegressConfig, load_project
from eda_agent.tools.formats.exitcode import judge_exitcode, read_subtests
from eda_agent.tools.regress import run_regress

# The shape of the ISA Lab1 testbench's last lines.
SUMMARY = r"scenario regress: (?P<passed>\d+)/(?P<total>\d+) cases ok"
GOOD = "[tb] scenario regress: 90/90 cases ok (7 model_only), 87076 checks, 0 failures\nTEST PASSED\n"
BAD = "[tb] scenario regress: 88/90 cases ok (7 model_only), 87076 checks, 2 failures\nTEST PASSED\n"


def _cfg(**kw) -> RegressConfig:
    return RegressConfig(cmd=("make", "sim"), pass_regex="TEST PASSED", summary_regex=SUMMARY, **kw)


def test_read_and_judge():
    assert read_subtests(GOOD, _cfg()) == {"passed": 90, "total": 90, "failed": 0}
    assert judge_exitcode(0, GOOD, _cfg()) == ("PASS", "exit 0, /TEST PASSED/ found, testbench: 90/90 passed")
    # exit 0 and TEST PASSED, but the testbench's own count shows failures: FAIL
    assert judge_exitcode(0, BAD, _cfg()) == ("FAIL", "testbench reports 2 failed of 90")
    assert judge_exitcode(0, "TEST PASSED\n", _cfg())[1].startswith("exit 0 but the testbench summary")


def test_bad_summary_regex_is_refused():
    with pytest.raises(ValueError, match="named groups"):
        RegressConfig(cmd=("x",), summary_regex=r"(\d+) ok")
    with pytest.raises(ValueError, match="named groups"):
        RegressConfig(cmd=("x",), summary_regex=r"(?P<total>\d+) ok")
    with pytest.raises(ValueError, match="exitcode"):
        RegressConfig(cmd=("x",), case_list="l", format="torv", summary_regex=SUMMARY)


def _project(tmp_path: Path, output: str):
    (tmp_path / "out.txt").write_text(output)
    toml = tmp_path / "p.toml"
    toml.write_text(f'name = "p"\nroot = "{tmp_path}"\nallowed_commands = ["cat"]\n[lint]\ntop = "t"\n'
                    f'[regress]\ncmd = ["cat", "out.txt"]\npass_regex = "TEST PASSED"\n'
                    f"summary_regex = '{SUMMARY}'\n")
    return load_project(toml)


def test_regress_reports_subtests_and_gate_accepts_their_counts(tmp_path):
    r = run_regress(_project(tmp_path, GOOD), ["all"], log_dir=tmp_path / "logs")
    assert r.status == "pass" and (r.metrics["subtests_passed"], r.metrics["subtests_total"]) == (90, 90)
    assert r.summary.endswith("testbench sub-tests: 90 passed, 0 failed of 90")
    assert "testbench's own summary line" in r.claim_scope
    assert not any("which sub-tests ran" in x for x in r.residual_risk)
    report = ("regress all: pass (E2), 1 passed; the testbench reports 90 passed, 0 failed.\n\n"
              "Residual risk: the counts are the testbench's own; no coverage or mutation score.")
    assert check_report(report, [r.to_dict()]).passed
    fake = report.replace("90 passed", "95 passed")
    assert not check_report(fake, [r.to_dict()]).passed

    bad = run_regress(_project(tmp_path, BAD), ["all"], log_dir=tmp_path / "logs")
    assert bad.status == "fail" and "2 failed of 90" in bad.diagnostics[0].msg
