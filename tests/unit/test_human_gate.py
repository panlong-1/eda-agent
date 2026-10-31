import json
import subprocess
import sys

import pytest

from eda_agent.agent import human_gate
from eda_agent.agent.receipt import render_markdown, write_receipt


def receipt(status="done") -> dict:
    return {"run_id": "r1", "task": "t", "project": "p", "provider": "x", "model": "m", "prompt": "",
            "stop_reason": "end", "status": status, "turns": 1, "tool_calls": 0, "tool_errors": 0,
            "usage": {}, "wall_s": 0, "trace": "",
            "gate": {"status": "passed", "rewrites": 0, "decisions": []}, "checks_run": [],
            "not_run": {"lint_run": False, "described_but_not_run": [], "cases_not_run": []},
            "final_claims": "", "residual_risk": {"report": "", "from_tools": []}}


def test_request_then_approve_records_who_when_and_what():
    r = human_gate.request_approval(receipt(), "final report", "gate passed")
    assert (r["status"], r["agent_status"], r["approval"]["status"]) == ("awaiting_approval", "done", "pending")
    assert "eda-agent approve r1" in render_markdown(r)
    sha = human_gate.content_sha256(r)
    human_gate.decide(r, "approve", by="Long Pan")
    a = r["approval"]
    assert (r["status"], a["status"], a["by"], a["receipt_sha256"]) == ("approved", "approved", "Long Pan", sha)
    assert a["at"] and not a["overrode_gate"]
    assert "decided by **Long Pan**" in render_markdown(r)
    with pytest.raises(human_gate.ApprovalError, match="final"):
        human_gate.decide(r, "reject", by="x", reason="changed my mind")


def test_reject_needs_a_reason():
    r = human_gate.request_approval(receipt(), "final report", "gate passed")
    with pytest.raises(human_gate.ApprovalError, match="reason"):
        human_gate.decide(r, "reject", by="x")
    human_gate.decide(r, "reject", by="x", reason="slow corner not looked at")
    assert (r["status"], r["approval"]["reason"]) == ("rejected", "slow corner not looked at")


def test_gate_rejected_run_needs_an_explicit_override():
    r = receipt(status="pending")
    with pytest.raises(human_gate.ApprovalError, match="override"):
        human_gate.decide(r, "approve", by="x")
    with pytest.raises(human_gate.ApprovalError, match="reason"):
        human_gate.decide(r, "approve", by="x", override_gate=True)
    human_gate.decide(r, "approve", by="x", reason="the rejected sentence was fine", override_gate=True)
    assert r["status"] == "approved" and r["approval"]["overrode_gate"] and r["agent_status"] == "pending"


def test_incomplete_run_cannot_be_approved():
    with pytest.raises(human_gate.ApprovalError, match="incomplete"):
        human_gate.decide(receipt(status="incomplete"), "approve", by="x")


def test_cli_approve_and_reject_write_the_receipt(tmp_path):
    r = human_gate.request_approval(receipt(), "final report", "gate passed")
    write_receipt(r, tmp_path)
    cli = [sys.executable, "-m", "eda_agent.cli"]
    bad = subprocess.run([*cli, "reject", "r1", "--runs-dir", str(tmp_path)], capture_output=True, text=True)
    assert bad.returncode != 0  # --reason is required
    ok = subprocess.run([*cli, "approve", "r1", "--by", "Reviewer A", "--runs-dir", str(tmp_path)],
                        capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr
    saved = json.loads((tmp_path / "r1.receipt.json").read_text())
    assert saved["status"] == "approved" and saved["approval"]["by"] == "Reviewer A"
    again = subprocess.run([*cli, "approve", "r1", "--runs-dir", str(tmp_path)], capture_output=True, text=True)
    assert again.returncode == 2 and "final" in again.stderr
