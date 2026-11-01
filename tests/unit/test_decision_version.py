"""The code version and approval policy recorded with a decision, and the Warnings about decisions made
with older code or under older approval rules (decisions themselves never change)."""
from eda_agent.agent.human_gate import POLICY, decide, request_approval
from eda_agent.agent.levels import assess
from eda_agent.agent.receipt import render_markdown
from eda_agent.core.version import code_version, label, older


def receipt(status="pass", **kw):
    r = {"run_id": "r1", "task": "t", "provider": "p", "model": "m", "prompt": "", "project": "toy",
         "stop_reason": "end", "status": "done", "gate": {"status": "passed", "rewrites": 0, "decisions": []},
         "checks_run": [{"tool": "lint", "args": {}, "status": status, "evidence_level": "E0", "summary": "0 error(s)",
                         "claim_scope": "", "tool_versions": {"verilator": "5"}, "artifacts": [], "metrics": {}}],
         "not_run": {"described_but_not_run": [], "cases_not_run": [], "lint_run": True},
         "final_claims": "", "residual_risk": {"report": "", "from_tools": []}, "usage": {}, "turns": 1,
         "tool_calls": 1, "tool_errors": 0, "wall_s": 1, "trace": "", **kw}
    return request_approval(r, "final report", "why")


def warnings(r):
    return [i.text for i in assess(r) if i.level == "warning" and i.source == "approval"]


def test_a_decision_records_the_code_version_and_the_policy():
    r = decide(receipt(), "approve", by="Pan")
    a = r["approval"]
    assert a["policy"] == POLICY and a["code_version"] == code_version()
    assert a["code_version"]["package"] and warnings(r) == []
    assert "approval policy levels.v1" in render_markdown(r)


def test_an_old_approval_of_a_fatal_run_without_override_is_flagged():
    r = receipt(status="fail")  # a failed check: Fatal
    r["approval"].update(status="approved", by="Pan", at="2026-10-31T23:50:00+01:00", reason=None,
                         receipt_sha256=None, overrode_gate=False)  # as the pre-policy code wrote it
    r["status"] = "approved"
    r["approval"]["receipt_sha256"] = __import__("eda_agent.agent.human_gate", fromlist=["x"]).content_sha256(r)
    w = warnings(r)
    assert len(w) == 1 and "--override and a reason" in w[0] and "no override and no reason" in w[0]


def test_an_approval_under_the_current_policy_is_not_flagged():
    r = decide(receipt(status="fail"), "approve", by="Pan", reason="known", override=True)
    assert warnings(r) == []


def test_a_decision_with_older_code_than_the_receipt_is_flagged():
    r = decide(receipt(), "approve", by="Pan")
    r["approval"]["code_version"] = {"commit": "a" * 40, "commit_time": "2026-10-31T23:00:00+01:00", "dirty": False}
    r["code_version"] = {"commit": "b" * 40, "commit_time": "2026-11-01T00:00:00+01:00", "dirty": False}
    w = warnings(r)
    assert len(w) == 1 and "older than the code that wrote this receipt" in w[0]


def test_version_helpers():
    a = {"commit": "a" * 40, "commit_time": "2026-10-31T23:00:00+01:00"}
    b = {"commit": "b" * 40, "commit_time": "2026-10-31T23:30:00+00:00"}
    assert older(a, b) and not older(b, a) and not older(a, a) and not older(None, b)
    assert label(None) == "not recorded" and label({"package": "0.0.1"}) == "package 0.0.1"
    assert label({**a, "dirty": True}).startswith("aaaaaaaaaaaa + uncommitted changes")
