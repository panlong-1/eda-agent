"""Experience-based back-end tuning (plan §0.15 steps 2-3): the knob policy kept in code, lessons with
checked citations, and the propose -> check -> run -> compare -> lesson loop with fake flow tools."""
import asyncio
import json
from pathlib import Path

import pytest

from eda_agent.agent.human_gate import ApprovalError, decide, request_approval
from eda_agent.agent.models.base import Turn
from eda_agent.agent.receipt import write_receipt
from eda_agent.agent.receipt.tune import tune_receipt
from eda_agent.agent.tuning import (TuneSetup, check_lesson, config_diff, experience_facts, judge, propose_lessons,
                                    tune)
from eda_agent.cli.tune import _gate, report_text
from eda_agent.core.knobs import check_patch, forbidden_name
from eda_agent.core.memory import LessonStore, known_ids
from eda_agent.core.pnr import PnrConfig
from eda_agent.core.project import load_project

SLOW, FAST = "nom_slow_1p08V_125C", "nom_fast_1p32V_m40C"


# ---------------------------------------------------------------- the knob policy (core/knobs.py)

@pytest.mark.parametrize("patch", [
    {"CLOCK_PERIOD": 11.0}, {"CLOCK_PORT": "clk2"}, {"PNR_SDC_FILE": "dir::loose.sdc"}, {"SIGNOFF_SDC_FILE": "x"},
    {"STA_CORNERS": ["nom_typ_1p20V_25C"]}, {"TIMING_VIOLATION_CORNERS": []}, {"IO_DELAY_CONSTRAINT": 5},
    {"CLOCK_UNCERTAINTY_CONSTRAINT": 0.0}, {"RUN_MAGIC_DRC": False}, {"RUN_KLAYOUT_DRC": "false"},
    {"RUN_LVS": False}, {"RUN_ANTENNA_REPAIR": True, "RUN_KLAYOUT_XOR": 0}, {"ERROR_ON_LVS_ERROR": False},
    {"ERROR_ON_MAGIC_DRC": False}, {"QUIT_ON_TIMING_VIOLATIONS": False}, {"GRT_ALLOW_CONGESTION": True},
    {"PL_RESIZER_ALLOW_SETUP_VIOS": True}, {"MAGIC_DRC_WAIVER_FILE": "w.txt"}, {"KLAYOUT_DRC_TOLERANCE": 2},
])
def test_forbidden_knobs_are_fatal(patch):
    found = check_patch(patch, "librelane")
    assert any(f.level == "fatal" for f in found), found


def test_tunable_knobs_pass_and_unknown_ones_are_errors():
    assert check_patch({"GRT_RESIZER_SETUP_SLACK_MARGIN": 0.2, "SYNTH_STRATEGY": "DELAY 2"}, "librelane") == []
    assert check_patch({"PL_TARGET_DENSITY_PCT": 55, "RUN_POST_GRT_DESIGN_REPAIR": True}, "librelane") == []
    (f,) = check_patch({"SOME_NEW_KEY": 1}, "librelane")
    assert f.level == "error" and "tunable list" in f.reason
    (f,) = check_patch({"RUN_MAGIC_DRC": True}, "librelane")      # turning a check ON is still a person's call
    assert f.level == "error"
    (f,) = check_patch({"FP_CORE_UTIL": 50}, "librelane", baseline={"FP_CORE_UTIL": 50})
    assert f.level == "error" and "already" in f.reason
    assert check_patch({}, "librelane")[0].level == "error"


def test_pnr_config_refuses_forbidden_allowed_overrides():
    assert forbidden_name("STA_CORNERS") and forbidden_name("CLOCK_PERIOD") and not forbidden_name("FP_CORE_UTIL")
    with pytest.raises(ValueError, match="STA_CORNERS"):
        PnrConfig(config="c.yaml", allowed_overrides=("FP_CORE_UTIL", "STA_CORNERS"))
    PnrConfig(config="c.yaml", allowed_overrides=("FP_CORE_UTIL",))


# ---------------------------------------------------------------- step 2: lessons with checked citations

HISTORY = {"backend": "librelane", "tables": {"imported": {"baseline": "a", "runs": [
    {"run": "full_a", "stage": "full", "clock_period": 10, "runtime_s": 5000.0,
     "synth": {"cell_area_um2": 264188.0, "cells": 20088, "corners": {SLOW: {"setup_wns": -1.79}}},
     "post_route": {"corners": {SLOW: {"setup_wns": -4.35, "setup_tns": -669.3, "setup_violations": 508, "hold_wns": 0.2}},
                    "die_area_um2": 559132, "drc_by_tool": {"Magic": 0}, "lvs_errors": 0, "antenna_nets": 0},
     "config_vs_baseline": [], "sources": {"sta_summary": "a" * 64}},
    {"run": "full_b", "stage": "full", "clock_period": 10, "runtime_s": 5400.0,
     "synth": {"cell_area_um2": 245314.0, "cells": 18022, "corners": {SLOW: {"setup_wns": 0.346}}},
     "post_route": {"corners": {SLOW: {"setup_wns": -0.763, "setup_tns": -14.87, "setup_violations": 38, "hold_wns": 0.27}},
                    "die_area_um2": 520301, "drc_by_tool": {"Magic": 0}, "lvs_errors": 0, "antenna_nets": 0},
     "config_vs_baseline": [{"key": "SYNTH_MUL_BOOTH", "baseline": False, "run": True}],
     "sources": {"sta_summary": "b" * 64}}],
    "knob_effects": {}}}, "lessons": []}


def _project(tmp_path: Path, allowed=("GRT_RESIZER_SETUP_SLACK_MARGIN", "PL_TARGET_DENSITY_PCT")):
    root = tmp_path / "design"
    (root / "librelane").mkdir(parents=True)
    (root / "librelane" / "config.yaml").write_text("DESIGN_NAME: x\nCLOCK_PERIOD: 10.0\nFP_CORE_UTIL: 50\n")
    cfg = tmp_path / "p.toml"
    cfg.write_text(f'name = "blk"\nroot = "{root}"\n[lint]\ntop = "x"\n[regress]\ncmd = ["true"]\n'
                   f'[pnr]\nconfig = "librelane/config.yaml"\nwork_dir = "{tmp_path / "work"}"\n'
                   f'experience_dir = "{tmp_path / "exp"}"\nallowed_overrides = {json.dumps(list(allowed))}\n')
    return load_project(cfg)


def _check(text, cites, store=None, shown=()):
    facts = experience_facts(HISTORY)
    known = known_ids([store], []) if store else {"lessons": {}, "docs": []}
    return check_lesson({"text": text, "cites": cites}, facts, known, list(shown), {"full_a", "full_b"}, set())


def test_a_lesson_with_cited_numbers_passes():
    assert _check("full_a -> full_b (SYNTH_MUL_BOOTH on, confounded): slow post-route setup WNS -4.35 -> -0.763 ns "
                  "(+3.587 ns), synthesis area -7.14%.",
                  ["full_a:post_setup_wns_slow", "full_b:post_setup_wns_slow", "full_a:synth_area_um2",
                   "full_b:synth_area_um2"]) == []
    # the clock period of a cited run, and its frequency, explain "10 ns" / "100 MHz"
    assert _check("At 10 ns (100 MHz) full_b misses slow setup by 0.763 ns.", ["full_b:post_setup_wns_slow"]) == []


def test_invented_numbers_uncited_runs_and_facts_are_rejected():
    r = _check("full_b improves slow setup WNS to -0.5 ns.", ["full_b:post_setup_wns_slow"])
    assert any("0.5" in x for x in r)
    r = _check("full_a and full_b both miss slow setup (-0.763 ns).", ["full_b:post_setup_wns_slow"])
    assert any("names run full_a" in x for x in r)
    r = _check("full_b is clean.", ["full_b:drc_calibre"])
    assert any("not a fact" in x for x in r)
    assert _check("full_b looks fast.", []) == ["(R1) the lesson cites no fact of the experience tables: a lesson is "
                                                "written only from data"]


def test_a_lesson_may_name_only_lessons_that_exist_and_were_shown(tmp_path):
    st = LessonStore(tmp_path, "blk")
    e = st.add("x", sources=[{"run_id": "full_a"}])
    cites = ["full_b:post_setup_wns_slow"]
    assert _check(f"Refines {e['id']}: full_b misses by 0.763 ns.", cites, st, [e["id"]]) == []
    assert any("(R11)" in x for x in _check("Refines lesson-blk-099: full_b misses by 0.763 ns.", cites, st, [e["id"]]))
    assert any("not retrieved" in x for x in _check(f"Refines {e['id']}: full_b misses by 0.763 ns.", cites, st, []))


class FakeModel:
    """A model adapter that answers each call with the next canned reply."""
    name = "fake"

    def __init__(self, replies):
        self.replies, self.tasks = list(replies), []

    def start(self, system, tools, task):
        self.tasks.append(task)

    async def step(self):
        return Turn(stop="end", text=self.replies.pop(0), usage={"input_tokens": 10, "output_tokens": 5})


def test_propose_lessons_stores_only_checked_ones(tmp_path):
    p = _project(tmp_path)
    good = {"text": "full_b misses slow setup by 0.763 ns at 10 ns.", "knob": "", "cites": ["full_b:post_setup_wns_slow"]}
    bad = {"text": "full_b would pass at 12 ns.", "knob": "CLOCK_PERIOD", "cites": ["full_b:post_setup_wns_slow"]}
    m = FakeModel(["```json\n" + json.dumps({"lessons": [good, bad]}) + "\n```"])
    out = asyncio.run(propose_lessons(p, m, "sys", HISTORY, usage={}, by="t"))
    assert [x["lesson"]["text"] for x in out["accepted"]] == [good["text"]] and len(out["rejected"]) == 1
    (e,) = LessonStore.for_project(p).entries()
    assert e["status"] == "hypothesis" and e["scope"]["corner"] == "slow" and e["sources"][0]["run_id"] == "full_b"
    assert "full_b [imported" in m.tasks[0] and "post_setup_wns_slow=-0.763" in m.tasks[0]   # the facts it cites


# ---------------------------------------------------------------- step 3: the loop with fake flow tools

def _metrics(run_dir, wns, drc=0, viol=26):
    return {"run": str(run_dir), "wns": wns, "tns": -7.85, "hold_wns": 0.101, "complete": True, "worst_corner": SLOW,
            "corners": {SLOW: {"setup_wns": wns, "setup_tns": -7.85, "setup_violations": viol, "hold_wns": 0.2},
                        FAST: {"setup_wns": 4.1, "setup_tns": 0, "setup_violations": 0, "hold_wns": 0.101}},
            "drc_by_tool": {"Magic": drc, "KLayout": 0}, "lvs_errors": 0, "antenna_nets": 0, "sdc_sha256": "s" * 64,
            "clock_period": 10.0, "signoff_passed": False, "area": {"die_area_um2": 508267, "stdcell_count": 18824},
            "power": {"total_w": 0.0137}, "violators": {SLOW: [{"from": "a", "to": "b", "slack": wns}]},
            "comparison": {"baseline": {"baseline": "base", "better": [], "worse": [], "not_comparable": []}}}


class FakeFlow:
    def __init__(self, tmp_path, results):
        self.tmp, self.results, self.runs = tmp_path, list(results), []
        (tmp_path / "base").mkdir(exist_ok=True)

    def start(self):
        return "base", self.tmp / "base", {}

    def report(self, run_dir):
        return {"tool": "flow_report", "status": "fail", "evidence_level": "E4", "summary": "base",
                "metrics": _metrics(run_dir, -0.725)}

    def run(self, tag, baseline, overrides):
        self.runs.append((tag, baseline, overrides))
        d = self.tmp / tag
        d.mkdir()
        (d / "eda_agent_flow.json").write_text("{}")
        wns, drc = self.results.pop(0)
        return {"tool": "flow_run", "status": "fail", "evidence_level": "E4", "summary": f"{tag}",
                "metrics": _metrics(d, wns, drc)}

    def history(self):
        return HISTORY

    def config(self, run_dir):
        return {"FP_CORE_UTIL": 50, "GRT_RESIZER_SETUP_SLACK_MARGIN": 0.025}


def _reply(patch, **kw):
    return "```json\n" + json.dumps({"patch": patch, **kw}) + "\n```"


def test_forbidden_proposal_is_fatal_and_nothing_runs(tmp_path):
    p = _project(tmp_path)
    flow = FakeFlow(tmp_path, [])
    m = FakeModel([_reply({"CLOCK_PERIOD": 11}, hypothesis="h"), _reply({"RUN_MAGIC_DRC": False}, hypothesis="h"),
                   _reply({"SIGNOFF_SDC_FILE": "x"}, hypothesis="h"), _reply({"ERROR_ON_LVS_ERROR": False}, hypothesis="h")])
    body = asyncio.run(tune(p, TuneSetup(goal="slow setup", budget_runs=2, run_id="tune_t"), flow, model=m,
                            log=lambda s: None))
    assert flow.runs == [] and body["runs_used"] == 0
    assert all(any(f["level"] == "fatal" for f in a["policy"]) for a in body["attempts"])
    assert len(body["attempts"]) == 2      # round 1: two tries, both Fatal -> the loop stops
    assert "Fatal" in m.tasks[1] or "forbidden" in m.tasks[1]   # the rejection went back to the model


def test_improvement_verifies_and_a_miss_refutes_within_the_budget(tmp_path):
    p = _project(tmp_path)
    st = LessonStore.for_project(p)
    les = st.add("raising the post-GRT setup margin helps", sources=[{"run_id": "x"}],
                 scope={"problem": "GRT_RESIZER_SETUP_SLACK_MARGIN"})
    flow = FakeFlow(tmp_path, [(-0.5, 0), (-0.9, 0)])
    m = FakeModel([_reply({"GRT_RESIZER_SETUP_SLACK_MARGIN": 0.2}, tests_lesson=les["id"],
                          reasoning=f"repair harder (hint: {les['id']})"),
                   _reply({"PL_TARGET_DENSITY_PCT": 45}, hypothesis="lower density shortens wires on the slow paths",
                          reasoning="spread cells")])
    body = asyncio.run(tune(p, TuneSetup(goal="slow setup", budget_runs=2, run_id="tune_t"), flow, model=m,
                            log=lambda s: None))
    assert [t for t, *_ in flow.runs] == ["tune_t_1", "tune_t_2"] and body["runs_used"] == 2
    # the second run carries the first (improved) patch plus its own, compared with the first run
    assert flow.runs[1][2] == {"GRT_RESIZER_SETUP_SLACK_MARGIN": "0.2", "PL_TARGET_DENSITY_PCT": "45"}
    assert flow.runs[1][1].endswith("tune_t_1")
    a1, a2 = body["attempts"]
    assert a1["verdict"] == "improved" and a1["lesson_update"]["kind"] == "verify"
    assert a2["verdict"] == "not_improved" and a2["lesson_update"]["kind"] == "refute"
    now = st.current()
    assert now[les["id"]]["status"] == "verified"
    assert now[a2["hypothesis_lesson"]]["status"] == "refuted"     # the hypothesis was stored before its run
    assert body["best"]["run"] == "tune_t_1" and body["best_patch"] == {"GRT_RESIZER_SETUP_SLACK_MARGIN": 0.2}
    # the code-written report passes the gate; the receipt waits for a person and a Fatal-free approve works
    report = report_text(body)
    gate = _gate(body, report, p)
    assert gate["passed"], gate["reasons"]
    diff = config_diff(Path(p.root) / p.pnr.config, p.pnr.config, body["best_patch"], "tune")
    assert "+GRT_RESIZER_SETUP_SLACK_MARGIN: 0.2" in diff and "CLOCK_PERIOD" not in diff.replace(" CLOCK_PERIOD", "")
    r = tune_receipt(run_id="tune_t", project="blk", provider="fake", model="fake", prompt="p", body=body,
                     report=report, gate=gate, patch_files={"diff": "generated/tune_t/config.patch", "diff_text": diff},
                     usage={}, wall_s=1.0)
    request_approval(r, "patch", "a person decides")
    js, md = write_receipt(r, tmp_path / "runs")
    text = md.read_text()
    assert r["status"] == "awaiting_approval" and "tune_t_1" in text and "refute" in text and "NOT applied" in text
    assert json.loads(js.read_text())["levels"]["counts"]["fatal"] == 0


def test_the_budget_caps_runs_and_signoff_regressions_are_not_improvements(tmp_path):
    p = _project(tmp_path)
    flow = FakeFlow(tmp_path, [(-0.1, 3)])
    m = FakeModel([_reply({"PL_TARGET_DENSITY_PCT": 45}, hypothesis="h1")])
    body = asyncio.run(tune(p, TuneSetup(goal="g", budget_runs=1, run_id="tune_b"), flow, model=m, log=lambda s: None))
    (a,) = body["attempts"]
    assert len(flow.runs) == 1 and a["verdict"] == "signoff_worse" and body["best"]["run"] == "base"
    assert judge({"setup_wns_slow": -0.7}, {"complete": False}) == ("no_result", "the run did not finish with "
                                                                    "post-route numbers")


def test_a_receipt_with_a_forbidden_proposal_needs_an_override(tmp_path):
    p = _project(tmp_path)
    body = asyncio.run(tune(p, TuneSetup(goal="g", run_id="tune_f", proposals=[{"patch": {"CLOCK_PERIOD": 12},
                                                                               "hypothesis": "h"}]),
                            FakeFlow(tmp_path, []), log=lambda s: None))
    report = report_text(body)
    r = tune_receipt(run_id="tune_f", project="blk", provider="person", model="-", prompt="f", body=body,
                     report=report, gate=_gate(body, report, p), patch_files={}, usage={}, wall_s=0.1)
    request_approval(r, "patch", "x")
    with pytest.raises(ApprovalError, match="Fatal"):
        decide(r, "approve", "someone")
