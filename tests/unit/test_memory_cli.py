"""The run's memory as the CLI builds it: hints from the store and documents, learning from a report,
and lessons seeded from a flow receipt."""
import json
from types import SimpleNamespace

from eda_agent.cli.lessons import receipt_lessons
from eda_agent.cli.run import learn, run_memory
from eda_agent.core.memory import LessonStore
from eda_agent.core.project import load_project

RISK = "\n\n### Residual risk\nOnly two cases ran."


def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "spec.md").write_text("# Hazards\nload-use stall of one cycle on the hazard unit\n")
    cfg = tmp_path / "p.toml"
    cfg.write_text(f'name = "demo"\nroot = "{tmp_path}"\n[lint]\ntop = "t"\n[regress]\ncmd = ["true"]\n'
                   '[memory]\ndocs = [{ id = "spec", path = "spec.md" }]\n')
    return load_project(cfg)


def test_hints_learning_and_second_run(tmp_path, monkeypatch):
    p = project(tmp_path, monkeypatch)
    first = run_memory(p, "check the hazard unit stall cases", True)
    assert [h["id"] for h in first.hints] == ["doc:spec#hazards"]
    assert first.known["docs"] == ["doc:spec#hazards"] and run_memory(p, "x", False) is None

    trace = tmp_path / "run1.jsonl"
    trace.write_text("{}\n")
    report = SimpleNamespace(final_text="regress: pass (E2), 2 passed.\n\n## Lessons (hypotheses)\n"
                                        "- the hazard case passes in 1200 cycles (regress result)\n" + RISK,
                             checks=[{"tool": "regress"}])
    ids = learn(p, {"run_id": "run1", "gate": {"status": "passed"}}, report, trace, "deepseek-v4-pro")
    assert ids == ["lesson-demo-001"]
    e = LessonStore.for_project(p).entries()[0]
    assert e["status"] == "hypothesis" and e["scope"]["tool"] == "regress" and e["sources"][0]["run_id"] == "run1"
    assert e["sources"][0]["sha256"] and e["origin"] == "run"
    assert learn(p, {"run_id": "r2", "gate": {"status": "rejected"}}, report, trace, "m") == []

    second = run_memory(p, "run the hazard cases again", True)
    assert "lesson-demo-001" in [h["id"] for h in second.hints]


def test_seed_from_flow_receipt(tmp_path, monkeypatch):
    p = project(tmp_path, monkeypatch)
    rec = {"run_id": "m4", "project": "demo", "approval": {"status": "approved", "by": "pan"}, "checks_run": [
        {"tool": "flow_report", "args": {}, "status": "fail", "summary": "", "metrics": {
            "tag": "e5", "backend": "librelane", "clock_period": 10.0, "pdk": "ihp-sg13g2", "signoff_passed": False,
            "signoff_level": "E5-partial", "experience_table": "eda_agent",
            "signoff": {"setup": {"outcome": "fail", "detail": "slow: setup WNS -0.725 ns"},
                        "lvs": {"outcome": "pass", "detail": "0 LVS errors"}}}}]}
    path = tmp_path / "m4.receipt.json"
    path.write_text(json.dumps(rec))
    (les,) = receipt_lessons(p, path)
    assert les["status"] == "verified" and "setup fail (slow: setup WNS -0.725 ns)" in les["text"]
    assert les["sources"][0]["receipt_sha256"]
    rec["approval"] = {"status": "awaiting_approval"}
    path.write_text(json.dumps(rec))
    assert receipt_lessons(p, path)[0]["status"] == "hypothesis"
