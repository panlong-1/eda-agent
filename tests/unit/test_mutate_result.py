"""mutate_result: a recorded mutate run read back, flagged when the mutated file changed since."""
import json
from pathlib import Path

from eda_agent.agent.levels import assess
from eda_agent.core.project import load_project
from eda_agent.tools.testcase import run_mutate_result

RTL = "module add (input logic [3:0] a, b, output logic [3:0] y);\n  assign y = a + b;\nendmodule\n"


def _setup(tmp_path: Path, now: str) -> tuple:
    root = tmp_path / "design"
    (root / "rtl").mkdir(parents=True)
    (root / "rtl" / "add.sv").write_text(now)
    toml = tmp_path / "toy.toml"
    toml.write_text(f'name = "toy"\nroot = "{root}"\n[lint]\ntop = "add"\n[regress]\nformat = "exitcode"\n'
                    'cmd = ["sh", "run.sh"]\n')
    work = tmp_path / "work"
    run = work / "toy_mutate_1"
    (run / "w0" / "rtl").mkdir(parents=True)
    (run / "w0" / "rtl" / "add.sv").write_text(RTL)
    metrics = {"file": "rtl/add.sv", "mutants": 2, "killed": 2, "survived": 0, "kill_rate": 1.0,
               "root_unchanged": True, "mutant_list": [{"id": 1, "status": "killed", "line": 2}]}
    (run / "mutate.json").write_text(json.dumps(metrics))
    (run / "envelope.json").write_text(json.dumps({
        "tool": "mutate", "status": "pass", "evidence_level": "E0", "claim_scope": "2 mutants.",
        "summary": "2 of 2 killed", "metrics": metrics, "residual_risk": ["sampled"]}))
    return load_project(toml), work


def _receipt(env: dict) -> dict:
    """The smallest agent-run receipt levels.assess accepts, around one mutate_result call (the receipt
    names the check by the tool the agent called)."""
    return {"run_id": "r", "status": "done", "stop_reason": "end", "tool_errors": 0,
            "gate": {"status": "passed", "rewrites": 0, "decisions": []},
            "checks_run": [{"tool": "mutate_result", "args": {}, "status": env["status"], "evidence_level": "E0",
                            "summary": env["summary"], "metrics": env["metrics"], "tool_versions": {"avl": "1"}}],
            "not_run": {"described_but_not_run": [], "cases_not_run": [], "lint_run": True}}


def _levels(env: dict, level: str) -> list[str]:
    return [i.text for i in assess(_receipt(env)) if i.level == level and "recorded run" in i.text]


def test_unchanged_file_is_read_back_with_a_warning(tmp_path):
    project, work = _setup(tmp_path, RTL)
    env = run_mutate_result(project, "toy_mutate_1", work_base=work)
    rec = env["metrics"]["recorded"]
    assert env["tool"] == "mutate" and rec["file_changed"] is False and rec["file_sha256_then"] == rec["file_sha256_now"]
    assert env["claim_scope"].startswith("Read back") and "unchanged" in env["claim_scope"]
    assert env["residual_risk"] == ["sampled"]
    assert _levels(env, "warning") and not _levels(env, "error")


def test_changed_file_is_flagged_and_the_diff_shown(tmp_path):
    project, work = _setup(tmp_path, RTL.replace("a + b", "a - b"))
    env = run_mutate_result(project, "toy_mutate_1", work_base=work)
    rec = env["metrics"]["recorded"]
    assert rec["file_changed"] is True
    assert "-  assign y = a + b;" in rec["diff"] and "+  assign y = a - b;" in rec["diff"]
    assert "earlier version" in env["claim_scope"] and "do not cover" in env["residual_risk"][-1]
    assert _levels(env, "error") and not _levels(env, "warning")


def test_unknown_run_is_an_error(tmp_path):
    project, work = _setup(tmp_path, RTL)
    assert run_mutate_result(project, "nope", work_base=work)["status"] == "error"
    assert run_mutate_result(project, "../x", work_base=work)["status"] == "error"
