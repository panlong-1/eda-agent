"""The backend boundary and the experience tables.

A tiny fake adapter ("fakeflow", import mode, its own knob names and DRC tool) drives flow_report,
flow_history, the gate and flow_run without any LibreLane file: the neutral layer must not need
LibreLane. The experience tests use the LibreLane history fixture (cut from TORV's runs)."""
import json
import os
import re
import shutil
from pathlib import Path

from eda_agent.agent.gate import check_report
from eda_agent.core.project import load_project
from eda_agent.tools import experience
from eda_agent.tools.backends import Backend, empty_facts, register_backend
from eda_agent.tools.flow import compare_runs, run_flow, run_flow_report
from eda_agent.tools.flow_history import run_flow_history

HIST = Path(__file__).parents[1] / "fixtures" / "librelane_history"
RISK = "\n\nResidual risk: no gate-level simulation of the routed netlist and vectorless power only."


class FakeFlow(Backend):
    """A run is a directory with run.json: {"config": {...}, "facts": {neutral overrides}}."""
    name = "fakeflow"
    run_mode = "import"
    synthesis_keys = re.compile(r"^SYN_")
    not_knobs = re.compile(r"^NOTE$")

    def is_run(self, path):
        return (Path(path) / "run.json").is_file()

    def _data(self, path):
        return json.loads((Path(path) / "run.json").read_text())

    def read_run(self, path, violators=10):
        f = empty_facts(Path(path))
        for k, v in self._data(path)["facts"].items():
            f[k] = {**f[k], **v} if isinstance(f.get(k), dict) and isinstance(v, dict) else v
        f["backend"], f["backend_version"] = self.name, "0.1"
        f["reports"] = {"run": str(Path(path) / "run.json")}
        return f

    def config(self, path):
        return self._data(path)["config"]

    def lessons(self, config_file):
        return {"SYN_EFFORT": {"comment": "high effort: -5% cell area", "value": "high"}}


register_backend(FakeFlow())
CORNERS = ["slow", "fast"]


def _fake_run(d: Path, area: float, effort: str, wns: float = 0.2, drc: int = 0, started: str = "2026-10-30T10:00:00+01:00"):
    d.mkdir(parents=True)
    timing = {c: {"setup_wns": wns, "setup_tns": 0.0, "setup_violations": 0, "hold_wns": 0.05, "hold_tns": 0.0,
                  "hold_violations": 0} for c in CORNERS}
    facts = {"design": "blk", "complete": True, "stage": "full", "post_route_sta_step": "sta", "started": started,
             "clock_period": 5.0, "sta_corners": CORNERS, "timing": timing,
             "clock": {c: {"propagated": 10, "ideal": 0, "nonzero_source_latency": [], "unpropagated_clocks": []}
                       for c in CORNERS},
             "pre_pnr": {"cell_area_um2": area, "cells": int(area / 10), "corners": {c: {"setup_wns": 1.0} for c in CORNERS}},
             "electrical": {"unannotated_nets_filtered": 0},
             "drc": {"by_tool": {"FakeDRC": drc}}, "lvs": {"tool": "FakeLVS", "errors": 0, "report_match": True},
             "antenna": {"tool": "FakeANT", "violating_nets": 0, "violating_pins": 0},
             "signoff_tools": {"drc": {"FakeDRC": "/decks/x.drc"}, "lvs": {"FakeLVS": "/decks/x.lvs"}, "antenna": "FakeANT"},
             "area": {"die_area_um2": area * 2}, "power": {"total_w": 0.01, "method": "vectorless"}}
    (d / "run.json").write_text(json.dumps({"config": {"SYN_EFFORT": effort, "NOTE": d.name}, "facts": facts}))


def _fake_project(tmp_path: Path) -> Path:
    (tmp_path / "blk").mkdir()
    (tmp_path / "blk" / "flow.cfg").write_text("SYN_EFFORT high\n")
    cfg = tmp_path / "p.toml"
    cfg.write_text(f'name = "blk"\nroot = "{tmp_path / "blk"}"\n[lint]\ntop = "x"\n[regress]\ncmd = ["true"]\n'
                   f'[pnr]\nbackend = "fakeflow"\nconfig = "flow.cfg"\nbaseline = "a"\n'
                   f'work_dir = "{tmp_path / "work"}"\nexperience_dir = "{tmp_path / "exp"}"\n')
    return cfg


def test_neutral_layer_runs_on_a_fake_backend(tmp_path):
    cfg = _fake_project(tmp_path)
    runs = tmp_path / "blk" / "runs"
    _fake_run(runs / "a", 1000.0, "low")
    _fake_run(runs / "b", 950.0, "high", started="2026-10-30T10:05:00+01:00")
    r = run_flow_report(load_project(cfg), "b")
    assert r.status == "pass" and r.evidence_level == "E4"
    assert r.metrics["signoff"]["drc_fakedrc"]["outcome"] == "pass" and r.metrics["signoff_level"] == "E5-partial"
    assert r.metrics["experience_table"] == "imported" and r.metrics["backend"] == "fakeflow"
    assert "FakeDRC 0" in r.summary and "fakeflow 0.1" in r.claim_scope and "FakeLVS" in r.claim_scope
    b = r.metrics["comparison"]["baseline"]
    assert [d["key"] for d in b["config_differences"]] == ["SYN_EFFORT"]  # NOTE is not a knob for this adapter
    assert check_report("flow_report b: the layout is DRC clean, 0 FakeDRC DRC errors." + RISK, [r.to_dict()]).passed


def test_history_and_lessons_on_a_fake_backend(tmp_path):
    cfg = _fake_project(tmp_path)
    runs = tmp_path / "blk" / "runs"
    _fake_run(runs / "a", 1000.0, "low")
    _fake_run(runs / "b", 950.0, "high", started="2026-10-30T10:05:00+01:00")
    h = run_flow_history(load_project(cfg))
    imp = h.metrics["tables"]["imported"]
    assert imp["knob_effects"]["SYN_EFFORT"]["verdict"] == "isolated"  # synthesis knob, started 5 min apart
    les = h.metrics["lessons"][0]
    assert les["verdict"] == "confirmed" and les["status"].startswith("hypothesis: from imported runs of blk")


def test_runs_of_different_backends_compare_on_neutral_numbers_only(tmp_path):
    cfg = _fake_project(tmp_path)
    _fake_run(tmp_path / "blk" / "runs" / "a", 1000.0, "low")
    be = FakeFlow()
    new = be.read_run(tmp_path / "blk" / "runs" / "a")
    base = {**new, "backend": "librelane", "tag": "x", "run": "x", "sdc": {"sha256": None}}
    new["sdc"] = {"sha256": None}
    c = compare_runs(new, base, {"SYN_EFFORT": "low"}, {"SYNTH_MUL_BOOTH": True}, be)
    assert c["config_differences"] == [] and any(n.startswith("different backend") for n in c["not_comparable"])


def test_an_import_only_backend_cannot_start_runs(tmp_path):
    r = run_flow(load_project(_fake_project(tmp_path)), tag="t")
    assert r.status == "error" and r.error_class == "not_allowed" and "import its reports" in r.summary


# --- experience tables (LibreLane fixture from TORV) ------------------------------------------------

def _torv_like(tmp_path: Path) -> tuple[Path, Path]:
    ll = tmp_path / "design" / "librelane"
    shutil.copytree(HIST / "runs", ll / "runs")
    shutil.copy(HIST / "config.yaml", ll / "config.yaml")
    started = json.loads((HIST / "started.json").read_text())
    for name, t in started.items():
        os.utime(ll / "runs" / name / "resolved.json", (t, t))
    cfg = tmp_path / "torv.toml"
    cfg.write_text(f'name = "torvx"\nroot = "{tmp_path / "design"}"\n[lint]\ntop = "x"\n[regress]\ncmd = ["true"]\n'
                   f'[pnr]\nconfig = "librelane/config.yaml"\nbaseline = "p10"\nwork_dir = "{tmp_path / "work"}"\n'
                   f'experience_dir = "{tmp_path / "exp"}"\n')
    return cfg, ll / "runs"


def _eda_run(project, src: Path, tmp_path: Path, name: str, patch: dict) -> Path:
    """Pretend flow_run made this run: copy it under the work dir and record it."""
    d = tmp_path / "work" / f"torvx_{name}" / "src" / "librelane" / "runs" / name
    shutil.copytree(src, d)
    t = os.stat(src / "resolved.json").st_mtime
    os.utime(d / "resolved.json", (t, t))
    experience.record_eda_run(project, d, {"image": "img@sha256:abc", "sdc_sha256": "s" * 64}, patch, None)
    return d


def test_new_design_starts_with_an_empty_eda_agent_table(tmp_path):
    cfg, _ = _torv_like(tmp_path)
    h = run_flow_history(load_project(cfg))
    assert h.metrics["tables"]["eda_agent"]["runs"] == [] and len(h.metrics["tables"]["imported"]["runs"]) == 7
    assert all(x["status"].startswith("hypothesis") for x in h.metrics["lessons"])


def test_eda_agent_runs_have_provenance_and_verify_a_lesson_here(tmp_path):
    cfg, runs = _torv_like(tmp_path)
    p = load_project(cfg)
    a = _eda_run(p, runs / "p7_nobooth", tmp_path, "nobooth", {})
    _eda_run(p, runs / "p7_booth", tmp_path, "booth", {"SYNTH_MUL_BOOTH": True})
    h = run_flow_history(p)
    eda = h.metrics["tables"]["eda_agent"]
    row = next(r for r in eda["runs"] if r["run"] == "booth")
    assert row["table"] == "eda_agent" and row["provenance"]["config_patch"] == {"SYNTH_MUL_BOOTH": True}
    assert row["provenance"]["image"] == "img@sha256:abc" and row["provenance"]["made_by"] == "eda-agent flow_run"
    les = next(x for x in h.metrics["lessons"] if x["key"] == "SYNTH_MUL_BOOTH")
    assert les["status"] == "verified here by eda-agent runs (confirmed)"
    rep = run_flow_report(p, str(a), baseline="")
    assert rep.metrics["experience_table"] == "eda_agent"


def test_tables_are_per_design_and_other_designs_are_hypotheses(tmp_path):
    cfg, runs = _torv_like(tmp_path)
    p = load_project(cfg)
    other = tmp_path / "exp" / "otherchip"
    other.mkdir(parents=True)
    (other / experience.SUMMARY_FILE).write_text(json.dumps(
        {"design": "otherchip", "backend": "librelane",
         "verified_knobs": [{"knob": "FP_CORE_UTIL", "summary": "FP_CORE_UTIL 40 -> 50: die area -12%"}]}))
    (other / experience.RUNS_FILE).write_text(json.dumps({"run_id": "x", "run_dir": str(runs / "p10")}) + "\n")
    h = run_flow_history(p)
    assert h.metrics["tables"]["eda_agent"]["runs"] == []  # another design's record is not mixed in
    hyp = h.metrics["hypotheses_from_other_designs"]
    assert hyp == [{"from_design": "otherchip", "backend": "librelane", "knob": "FP_CORE_UTIL",
                    "effect": "FP_CORE_UTIL 40 -> 50: die area -12%", "status": "from otherchip, not verified here"}]
    assert (tmp_path / "exp" / "torvx" / experience.SUMMARY_FILE).is_file()
