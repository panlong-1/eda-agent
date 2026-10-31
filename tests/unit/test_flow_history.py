"""flow_history: one table of all LibreLane runs, knob effects only from clean pairs, config lessons.

The fixture is cut from TORV's librelane/runs (p10, synth, hier, p7_booth, p7_nobooth, full_p10,
full_p10_booth): resolved.json keys, the pre/post-route STA step metrics and final metrics only."""
import json
import os
import shutil
from pathlib import Path

from eda_agent.agent.receipt import _flow
from eda_agent.core.project import load_project
from eda_agent.tools.flow_history import config_lessons, run_flow_history

FIXTURE = Path(__file__).parents[1] / "fixtures" / "librelane_history"
SLOW = "nom_slow_1p08V_125C"


def _project(tmp_path: Path, comment_edit=None) -> tuple[Path, Path]:
    ll = tmp_path / "design" / "librelane"
    shutil.copytree(FIXTURE / "runs", ll / "runs")
    cfg = (FIXTURE / "config.yaml").read_text()
    (ll / "config.yaml").write_text(comment_edit(cfg) if comment_edit else cfg)
    for name, t in json.loads((FIXTURE / "started.json").read_text()).items():
        os.utime(ll / "runs" / name / "resolved.json", (t, t))
    p = tmp_path / "p.toml"
    p.write_text(f'name = "t"\nroot = "{tmp_path / "design"}"\n[lint]\ntop = "x"\n[regress]\ncmd = ["true"]\n'
                 '[pnr]\nconfig = "librelane/config.yaml"\nbaseline = "p10"\n')
    return p, ll / "runs"


def _lesson(res, key):
    return next(x for x in res.metrics["lessons"] if x["key"] == key)


def test_table_has_every_run_with_its_stage_and_sources(tmp_path):
    cfg, _ = _project(tmp_path)
    r = run_flow_history(load_project(cfg))
    assert r.status == "pass" and r.evidence_level == "E0"
    runs = {x["run"]: x for x in r.metrics["runs"]}
    assert set(runs) == {"p10", "synth", "hier", "p7_booth", "p7_nobooth", "full_p10", "full_p10_booth"}
    assert r.metrics["stages"] == {"full": 2, "synth_sta": 5}
    assert runs["p7_booth"]["synth"]["cell_area_um2"] == 245313.7218 and runs["p7_booth"]["post_route"] is None
    assert runs["full_p10"]["post_route"]["corners"][SLOW]["setup_wns"] < -4.3
    assert [d["key"] for d in runs["p7_booth"]["config_vs_baseline"]] == ["CLOCK_PERIOD", "SYNTH_MUL_BOOTH",
                                                                          "VERILOG_FILES"]
    assert all(len(h) == 64 for h in runs["full_p10"]["sources"].values())


def test_booth_isolated_by_runs_started_together_and_lesson_confirmed(tmp_path):
    cfg, _ = _project(tmp_path)
    r = run_flow_history(load_project(cfg))
    booth = r.metrics["knob_effects"]["SYNTH_MUL_BOOTH"]
    assert booth["verdict"] == "isolated"
    pair = booth["pairs"][0]
    assert (pair["off"], pair["on"]) == ("p7_nobooth", "p7_booth") and "apart" in pair["same_rtl"]
    assert "synthesis cell area -8.43%" in pair["summary"]
    les = _lesson(r, "SYNTH_MUL_BOOTH")
    assert les["verdict"] == "confirmed" and les["measured_area_pct"] == -8.43 and les["claim_area_pct"] == -8.4


def test_untested_and_confounded_lessons(tmp_path):
    cfg, _ = _project(tmp_path)
    r = run_flow_history(load_project(cfg))
    assert _lesson(r, "SYNTH_ABC_BUFFERING")["verdict"] == "cannot_test"
    grt = _lesson(r, "RUN_POST_GRT_RESIZER_TIMING")
    assert grt["verdict"] == "confounded" and set(grt["indirect"]) == {"False", "True"}
    assert grt["indirect"]["False"]["full_p10"][SLOW] < grt["indirect"]["True"]["full_p10_booth"][SLOW]
    conf = r.metrics["knob_effects"]["RUN_POST_GRT_RESIZER_TIMING"]
    assert conf["verdict"] == "confounded" and "SYNTH_MUL_BOOTH" in conf["pairs"][0]["also_changed"]


def test_identical_netlist_isolates_a_period_change(tmp_path):
    cfg, _ = _project(tmp_path)
    r = run_flow_history(load_project(cfg))
    pairs = r.metrics["knob_effects"]["CLOCK_PERIOD"]["pairs"]
    p = next(x for x in pairs if {x["off"], x["on"]} == {"synth", "p10"})
    assert p["same_rtl"].startswith("identical synthesized netlist")
    assert p["effect"]["synth_setup_wns"][SLOW]["delta"] == 5.0  # 5 ns -> 10 ns, same netlist: slack +5 ns


def test_runs_far_apart_with_different_netlists_are_confounded(tmp_path):
    cfg, runs = _project(tmp_path)
    t = json.loads((FIXTURE / "started.json").read_text())["p7_nobooth"] + 2 * 86400
    os.utime(runs / "p7_nobooth" / "resolved.json", (t, t))
    r = run_flow_history(load_project(cfg))
    booth = r.metrics["knob_effects"]["SYNTH_MUL_BOOTH"]
    assert booth["verdict"] == "confounded"
    assert any("RTL" in c for p in booth["pairs"] for c in p["also_changed"])
    assert _lesson(r, "SYNTH_MUL_BOOTH")["verdict"] == "confounded"


def test_non_synthesis_knob_with_a_different_netlist_is_not_attributed(tmp_path):
    cfg, runs = _project(tmp_path)
    shutil.copytree(runs / "p10", runs / "p10_util")
    res = json.loads((runs / "p10_util/resolved.json").read_text())
    res["FP_CORE_UTIL"] = 45
    (runs / "p10_util/resolved.json").write_text(json.dumps(res))
    for f in (runs / "p10_util/final/metrics.json", ):
        m = json.loads(f.read_text())
        m["design__instance__area"] = 1.0
        f.write_text(json.dumps(m))
    step = next((runs / "p10_util").glob("*-openroad-staprepnr"))
    s = json.loads((step / "state_out.json").read_text())
    s["metrics"]["design__instance__area"] = 1.0
    (step / "state_out.json").write_text(json.dumps(s))
    r = run_flow_history(load_project(cfg))
    util = r.metrics["knob_effects"]["FP_CORE_UTIL"]
    assert util["verdict"] == "confounded" and "netlists differ" in util["pairs"][-1]["also_changed"][0]


def test_contradicted_area_claim(tmp_path):
    cfg, _ = _project(tmp_path, lambda t: t.replace("-8.4% cell area", "+5% cell area"))
    r = run_flow_history(load_project(cfg))
    assert _lesson(r, "SYNTH_MUL_BOOTH")["verdict"] == "contradicted"


def test_config_lessons_takes_the_comment_block_above_a_key():
    text = "# a\n# b c\nKEY_ONE: 1\n\nKEY_TWO: x\n# lone\n\n# d\nKEY_3: true\n"
    assert config_lessons(text) == {"KEY_ONE": {"comment": "a b c", "value": "1"},
                                    "KEY_3": {"comment": "d", "value": "true"}}


def test_receipt_keeps_the_history_summary(tmp_path):
    cfg, _ = _project(tmp_path)
    d = _flow(run_flow_history(load_project(cfg)).to_dict())["flow_history"]
    assert d["baseline"] == "p10" and len(d["runs"]) == 7
    assert any(x["key"] == "SYNTH_MUL_BOOTH" and x["verdict"] == "confirmed" for x in d["lessons"])
    assert any("-8.43%" in s for s in d["knobs"]["SYNTH_MUL_BOOTH"]["pairs"])


def test_no_runs_is_an_error(tmp_path):
    cfg, runs = _project(tmp_path)
    shutil.rmtree(runs)
    assert run_flow_history(load_project(cfg)).error_class == "no_runs"
