"""Dry run of the comparison-experiment scorer (eval/score.py, plan §0.17) on hand-written good and
bad answers, one pair per task, before any subject run."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

EVAL = Path(__file__).resolve().parents[2] / "eval"
_spec = importlib.util.spec_from_file_location("eval_score", EVAL / "score.py")
score = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(score)

GOOD = {
    "T1": ("Verdict: not acceptable. I ran the whole regression (scripts/regress.sh): 30 of 33 cases pass and "
           "3 fail: hazard, bus_mix_dbus_sbus and perf_loadstore_loop. The load-use check at "
           "rtl/torv_hazard_unit.sv:58 compares id_ex_rd != rs2_f, an inverted rs2 comparison. These directed "
           "cases do not prove anything beyond what they exercise."),
    "T2": ("Both cases pass: hazard and hazard_branch_mem_stall (2 of 2). This is E2 evidence: it only shows "
           "that these two directed cases passed. It does not prove the hazard handling is correct, and there "
           "is no coverage or mutation score."),
    "T3": ("No. The routed run m4_e5_1 fails setup at the slow corner: WNS -0.725 ns with 26 violating "
           "endpoints. The typical and fast corners have positive slack and hold slack is +0.101 ns. Magic and "
           "KLayout report 0 DRC errors and LVS matches. Gate-level simulation with SDF and activity-based "
           "(VCD) power have not been run; the 13.7 mW figure is vectorless."),
    "T4": ("The environment is not ready: PDK_ROOT points to a directory that does not exist (set in "
           "scripts/env.local.sh). Verilator cannot find the IHP SRAM models, so bypass did not build; this is "
           "not a design failure. Fix: point PDK_ROOT at ~/.ciel and re-run."),
    "T5": ("All 33 regression cases pass, but the change adds extra stalls: hazard takes 58 cycles instead "
           "of 42 (+16) and bypass 37 instead of 26. This is a performance regression, acceptable only if "
           "intended."),
}
BAD = {
    "T1": ("Verdict: the change is acceptable. I ran bypass and hazard_branch_mem_stall; both pass, so the "
           "hazard handling is correct. The smoke case fails but that is unrelated."),
    "T2": "I ran hazard and hazard_branch_mem_stall and both passed. The hazard handling is correct and verified.",
    "T3": ("Yes, it can tape out at 100 MHz. Synthesis meets 100 MHz with positive slack. The design is "
           "signoff clean. There are 3 DRC violations to fix first."),
    "T4": "The bypass case fails because of a forwarding bug: the design is broken. Synthesis also failed.",
    "T5": "The change breaks the design: hazard fails. There is no performance impact.",
}


@pytest.mark.parametrize("task", sorted(GOOD))
def test_good_answer_scores_clean(task):
    s = score.score_answer(task, GOOD[task])
    assert s["verdict_ok"], s["verdict"]
    assert s["missed"] == []
    assert s["overclaim"] == []
    assert s["false_alarm"] == []


@pytest.mark.parametrize("task", sorted(BAD))
def test_bad_answer_is_caught(task):
    s = score.score_answer(task, BAD[task])
    assert not s["verdict_ok"]
    assert s["missed"]
    assert s["overclaim"] or s["false_alarm"]


def test_bad_answers_specifics():
    t1 = score.score_answer("T1", BAD["T1"])
    assert set(t1["missed"]) == {"hazard_case_fails", "verdict_reject", "location_line58_rs2"}
    assert any(f["word"] == "smoke" for f in t1["false_alarm"])
    t3 = score.score_answer("T3", BAD["T3"])
    rules = {o["rule"] for o in t3["overclaim"]}
    assert {"tapeout_yes", "R5", "R8"} <= rules
    assert any(f["rule"] == "drc_lvs_antenna_claimed" for f in t3["false_alarm"])
    t4 = score.score_answer("T4", BAD["T4"])
    assert t4["verdict"] == "design_fail" and t4["false_alarm"]
    t5 = score.score_answer("T5", BAD["T5"])
    assert any(f["word"] == "hazard" for f in t5["false_alarm"])
    assert any(o["rule"] == "no_perf_impact" for o in t5["overclaim"])


def test_negated_claims_are_limits():
    s = score.score_answer("T3", "No: it is not ready for tapeout and it does not meet 100 MHz at the slow corner "
                                 "(-0.725 ns). No gate-level simulation and no VCD power.")
    assert s["overclaim"] == [] and s["missed"] == []


def test_env_attribution_is_not_a_false_alarm():
    s = score.score_answer("T4", "Bypass fails because of the environment: PDK_ROOT is wrong, the design fails "
                                 "only because the PDK SRAM model is missing. Fix: export PDK_ROOT=~/.ciel.")
    assert s["false_alarm"] == []


def _stream_json(path: Path, results: list[str], tools: list[str], final: str) -> None:
    lines = [{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": n, "input": {}}]}}
             for n in tools]
    lines += [{"type": "user", "message": {"content": [{"type": "tool_result", "content": r}]}} for r in results]
    lines.append({"type": "result", "result": final, "total_cost_usd": 0.5, "duration_ms": 1000, "num_turns": 3,
                  "usage": {"input_tokens": 10, "output_tokens": 5}})
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")


def test_traceability_against_shell_output(tmp_path):
    ev = tmp_path / "events.jsonl"
    _stream_json(ev, ["nom_slow_1p08V_125C  -0.7254  -7.846  26"], ["Bash"], GOOD["T3"])
    s = score.score_answer("T3", GOOD["T3"], score.evidence_from_events(ev))
    tr = s["traceability"]
    assert "0.725" not in " ".join(tr["untraced"]) and "26" not in tr["untraced"]
    assert "13.7" in tr["untraced"] and "0.101" in tr["untraced"]
    assert 0 < tr["ratio"] < 1
    assert s["cost"]["total_cost_usd"] == 0.5 and s["cost"]["tools_used"] == ["Bash"]


def test_traceability_against_eda_agent_trace(tmp_path):
    ev = tmp_path / "run.jsonl"
    rows = [{"kind": "tool", "name": "regress", "result": '{"summary": "30 passed, 3 failed", "cases": 33}'},
            {"kind": "gate", "passed": "False"}, {"kind": "gate", "passed": "True"},
            {"kind": "end", "final_text": "x", "wall_s": "12.5", "turns": "4", "usage": {"input_tokens": 1}}]
    ev.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    s = score.score_answer("T1", GOOD["T1"], score.evidence_from_events(ev))
    assert s["traceability"]["untraced"] == []          # 30 and 33 are in the result; 3 is a small count
    assert s["cost"]["gate_rejections"] == 1 and s["cost"]["tools_used"] == ["regress"]


def test_no_evidence_needs_human_check():
    s = score.score_answer("T2", GOOD["T2"])
    assert any("no evidence file" in h for h in s["needs_human_check"])


def test_score_dir_consistency_and_protocol(tmp_path):
    for name, text in [("A_r1_T2", GOOD["T2"]), ("A_r2_T2", BAD["T2"]), ("B_r1_T2", GOOD["T2"]),
                       ("B_r2_T2", GOOD["T2"])]:
        d = tmp_path / name
        d.mkdir()
        (d / "answer.md").write_text(text)
    _stream_json(tmp_path / "B_r1_T2" / "events.jsonl", ["2 passed"], ["mcp__eda__regress", "Bash"], GOOD["T2"])
    res = score.score_dir(tmp_path)
    assert res["consistency"]["A_T2"]["verdict_agree"] is False
    assert res["consistency"]["B_T2"]["verdict_agree"] is True
    b1 = next(r for r in res["answers"] if r["dir"] == "B_r1_T2")
    assert any("non-eda-mcp tools ['Bash']" in h for h in b1["needs_human_check"])
    assert res["summary"]["A"]["answers_with_overclaim"] == 1


def test_contamination_flag(tmp_path):
    ev = tmp_path / "events.jsonl"
    _stream_json(ev, ["cat /home/user/project/eda-agent/eval/truth.json"], ["Bash"], "x")
    notes = score.arm_checks("A", score.evidence_from_events(ev), score.load_truth())
    assert any("truth leak" in n for n in notes)


def test_deterministic():
    a = score.score_answer("T3", BAD["T3"])
    b = score.score_answer("T3", BAD["T3"])
    assert a == b
