"""Plan §0.18: design snapshots, LibreLane lineage, consistency of results used together, gate R8."""
import json
import shutil
from pathlib import Path

from eda_agent.agent.gate import check_report
from eda_agent.agent.receipt_lineage import render
from eda_agent.core.envelope import ToolResult
from eda_agent.core.snapshot import (OLDER, attach, compare_snapshots, consistency, make_snapshot,
                                     project_snapshot)
from eda_agent.tools.backends.librelane import lineage
from eda_agent.tools.flow import compare_synth

RISK = "\n\nResidual risk: no gate-level simulation of the routed netlist and vectorless power only."


def _design(tmp_path: Path, body="module a; endmodule\n") -> Path:
    d = tmp_path / "d"
    (d / "rtl").mkdir(parents=True, exist_ok=True)
    (d / "rtl" / "a.sv").write_text(body)
    (d / "c.sdc").write_text("create_clock -period 10 [get_ports clk]\n")
    return d


def _snap(d: Path, **kw) -> dict:
    return make_snapshot([str(d / "rtl" / "a.sv")], [d], sdc_sha256="s" * 64, pdk_version="v1", **kw)


# --- snapshots ---------------------------------------------------------------------------------------

def test_snapshot_is_stable_and_follows_the_rtl(tmp_path):
    d = _design(tmp_path)
    a, b = _snap(d), _snap(d)
    assert a["id"] == b["id"] and a["design_id"] and a["state"] == "current" and a["parts"]["design"]["rtl"] == \
        {"rtl/a.sv": a["parts"]["design"]["rtl"]["rtl/a.sv"]}
    c = _snap(d, tools={"yosys": "0.50"})
    assert c["design_id"] == a["design_id"] and c["id"] != a["id"]  # same design, other tool versions
    (d / "rtl" / "a.sv").write_text("module a; wire w; endmodule\n")
    e = _snap(d)
    assert e["design_id"] != a["design_id"]
    assert compare_snapshots(a, e) == {"same_design": False, "common_rtl_files": 1, "differences": ["RTL rtl/a.sv differs"]}


def test_a_copy_of_the_design_is_the_same_design(tmp_path):
    d = _design(tmp_path)
    copy = tmp_path / "work" / "src"
    shutil.copytree(d, copy)
    assert compare_snapshots(_snap(d), _snap(copy))["same_design"] is True


def test_older_snapshot_has_no_id(tmp_path):
    s = make_snapshot(["/gone/a.sv"], [], sdc_sha256=None, pdk_version="v1", state=OLDER, note="RTL changed")
    assert s["id"] is None and s["state"] == "older"


def test_project_snapshot_and_attach(tmp_path):
    d = _design(tmp_path)
    (d / "files.f").write_text("rtl/a.sv\n")
    cfg = tmp_path / "p.toml"
    cfg.write_text(f'name = "p"\nroot = "{d}"\n[versions]\n[lint]\ntop = "a"\nfilelists = ["files.f"]\n'
                   '[regress]\ncmd = ["true"]\n')
    from eda_agent.core.project import load_project
    p = load_project(cfg)
    s = project_snapshot(p)
    assert list(s["parts"]["design"]["rtl"]) == ["rtl/a.sv"] and s["unknown_parts"] == ["SDC", "PDK version"]
    r = attach(ToolResult("lint", "pass", "E0", "x"), p)
    assert r.snapshot["parts"]["design"]["rtl"] == s["parts"]["design"]["rtl"]
    own = ToolResult("flow_report", "pass", "E4", "x", snapshot={"id": "mine"})
    assert attach(own, p).snapshot == {"id": "mine"}


# --- consistency -------------------------------------------------------------------------------------

def _env(tool, snap, **metrics):
    return {"tool": tool, "status": "pass", "evidence_level": "E4", "snapshot": snap, "metrics": metrics}


def test_consistency_catches_older_snapshots_other_rtl_sdc_and_breaks(tmp_path):
    d = _design(tmp_path)
    cur = _snap(d)
    assert consistency([_env("synth", cur), _env("flow_report", cur)])["ok"]
    old = make_snapshot([], [], sdc_sha256=None, pdk_version=None, state=OLDER, note="RTL changed after the run")
    c = consistency([_env("synth", cur), _env("flow_report", old)])
    assert not c["ok"] and c["issues"][0]["level"] == "fatal" and "older snapshot" in c["issues"][0]["what"]
    (d / "rtl" / "a.sv").write_text("module a; reg r; endmodule\n")
    assert any("not the same design" in i["what"] for i in consistency([_env("synth", cur), _env("x", _snap(d))])["issues"])
    two_sdc = consistency([_env("a", cur, sdc_sha256="1" * 64), _env("b", cur, sdc_sha256="2" * 64)])
    assert any(i["level"] == "fatal" and "SDC" in i["what"] for i in two_sdc["issues"])
    broken = _env("flow_report", cur, lineage={"run": "r", "breaks": [{"what": "x missing", "level": "fatal"}]})
    assert any("lineage broken" in i["what"] for i in consistency([broken])["issues"])
    clk = consistency([_env("a", cur, clock_period=10), _env("b", cur, clock_period=7)])
    assert any(i["level"] == "error" and "clock period" in i["what"] for i in clk["issues"])


# --- LibreLane lineage on a small synthetic run ---------------------------------------------------

STEPS = [  # (dir, views it writes)
    ("06-yosys-synthesis", {"nl": "t.nl.v"}),
    ("07-checker-yosysunmappedcells", {}),
    ("13-openroad-floorplan", {"nl": "t.nl.v", "odb": "t.odb", "def": "t.def"}),
    ("44-openroad-detailedrouting", {"odb": "t.odb", "def": "t.def", "pnl": "t.pnl.v", "sdc": "t.sdc"}),
    ("54-openroad-rcx", {"spef": {"nom_*": "t.spef"}}),
    ("55-openroad-stapostpnr", {"sdf": {"nom_slow": "t.sdf"}}),
    ("57-magic-streamout", {"gds": "t.gds"}),
    ("64-magic-drc", {}), ("65-klayout-drc", {}),
    ("68-magic-spiceextraction", {"spice": "t.spice"}),
    ("70-netgen-lvs", {}),
]


def _run(tmp_path: Path) -> Path:
    run = tmp_path / "runs" / "r1"
    state: dict = {}
    for name, writes in STEPS:
        d = run / name
        d.mkdir(parents=True)
        (d / "state_in.json").write_text(json.dumps({**state, "metrics": {}}))
        for view, f in writes.items():
            if isinstance(f, dict):
                state[view] = {}
                for c, fn in f.items():
                    (d / fn).write_text(f"{name} {view} {c}\n")
                    state[view][c] = str(d / fn)
            else:
                (d / f).write_text(f"{name} {view}\n")
                state[view] = str(d / f)
        (d / "state_out.json").write_text(json.dumps({**state, "metrics": {}}))
    for view in ("gds", "pnl", "spice"):
        (run / "final" / view).mkdir(parents=True)
        shutil.copy(state[view], run / "final" / view / Path(state[view]).name)
    return run


def test_lineage_of_a_complete_run(tmp_path):
    lin = lineage(_run(tmp_path))
    assert lin["complete"] and lin["breaks"] == [] and lin["stages_absent"] == []
    stages = {s["stage"]: s for s in lin["stages"]}
    assert list(stages) == ["synthesis", "place_and_route", "rc_extraction", "post_route_sta", "gds_streamout", "drc",
                            "lvs_extraction", "lvs"]
    assert stages["place_and_route"]["inputs"]["nl"]["from"] == "synthesis"
    assert stages["post_route_sta"]["inputs"]["spef:nom_*"]["from"] == "rc_extraction"
    assert stages["drc"]["inputs"]["gds"]["from"] == "gds_streamout"
    assert stages["lvs"]["inputs"]["spice"]["from"] == "lvs_extraction" and stages["lvs"]["inputs"]["pnl"]["from"] == \
        "place_and_route"
    assert all(f["matches"] for f in lin["final"]) and len(lin["final"]) == 3
    assert "gate_level_sim" in lin["not_present"][0]


def test_lineage_breaks_are_found(tmp_path):
    run = _run(tmp_path)
    (run / "final" / "gds" / "t.gds").write_text("edited after the flow\n")
    s = json.loads((run / "54-openroad-rcx" / "state_in.json").read_text())
    s["def"] = "/elsewhere/other.def"
    (run / "54-openroad-rcx" / "state_in.json").write_text(json.dumps(s))
    (run / "68-magic-spiceextraction" / "t.spice").unlink()
    lin = lineage(run)
    what = " | ".join(b["what"] for b in lin["breaks"])
    assert not lin["complete"]
    assert "54-openroad-rcx did not start from the previous step's output (def)" in what
    assert "final/gds differs" in what and "output spice" in what and "missing" in what


def test_receipt_renders_the_lineage_table_with_breaks(tmp_path):
    run = _run(tmp_path)
    (run / "70-netgen-lvs" / "state_in.json").write_text(json.dumps({"metrics": {}}))
    lin = lineage(run)
    md = "\n".join(render({"checks_run": [{"tool": "flow_report", "lineage": lin,
                                            "snapshot": {"id": "abc", "design_id": "d", "state": "current"}}],
                           "consistency": consistency([{"tool": "flow_report", "metrics": {"lineage": lin}}])}))
    assert "## Lineage: flow_report run r1" in md and "| synthesis |" in md and "**FATAL**" in md
    assert "NOT one design" in md and "`abc`" in md


# --- gate R8: one design for multi-stage conclusions; synth vs flow is a different netlist ------------

def _flow_env(snap, lin_ok=True):
    m = {"tag": "r1", "drc_by_tool": {"Magic": 0}, "lvs_errors": 0, "lvs_match": True, "antenna_nets": 0,
         "antenna_pins": 0, "timing_met": True, "signoff_level": "E5-partial", "signoff_passed": True,
         "wns": 0.1, "hold_wns": 0.1, "clock_period": 10,
         "lineage": {"run": "r1", "available": True, "complete": lin_ok,
                     "breaks": [] if lin_ok else [{"what": "final/gds differs", "level": "fatal"}]}}
    return {"tool": "flow_report", "status": "pass", "evidence_level": "E4", "metrics": m, "snapshot": snap}


def test_signoff_from_one_snapshot_passes(tmp_path):
    cur = _snap(_design(tmp_path))
    d = check_report("flow_report r1: signoff passed." + RISK, [_env("synth", cur), _flow_env(cur)])
    assert d.passed, d.reasons


def test_old_run_timing_mixed_with_current_rtl_is_fatal(tmp_path):
    cur = _snap(_design(tmp_path))
    old = make_snapshot(["/x/rtl/old.sv"], [], sdc_sha256=None, pdk_version="v1", state=OLDER,
                        note="RTL changed after the run: 15 of 15 RTL file(s) it read no longer exist")
    d = check_report("flow_report r1: signoff passed, so this version is ready." + RISK,
                     [_env("synth", cur), _flow_env(old)])
    assert not d.passed and any(r.startswith("[fatal]") and "older snapshot" in r for r in d.reasons)


def test_broken_lineage_blocks_a_signoff_conclusion(tmp_path):
    cur = _snap(_design(tmp_path))
    d = check_report("flow_report r1: signoff passed." + RISK, [_flow_env(cur, lin_ok=False)])
    assert not d.passed and any("[fatal]" in r and "lineage broken" in r for r in d.reasons)


def test_timing_and_power_both_met_needs_a_power_target(tmp_path):
    cur = _snap(_design(tmp_path))
    d = check_report("flow_report r1: timing and power are both met." + RISK, [_flow_env(cur)])
    assert not d.passed and any("power target" in r for r in d.reasons)


def test_synth_against_flow_is_labelled_different_netlist(tmp_path):
    cur = _snap(_design(tmp_path))
    f = {"design": "a", "timing": {}, "area": {"stdcell_count": 10, "stdcell_area_um2": 5.0}}
    c = compare_synth(f, {"status": "pass", "metrics": {"cells": 8, "area": 4.0, "sdc_sha256": "s"}, "snapshot": cur},
                      "s", cur)
    assert c["label"] == "different netlist" and c["same_design"] is True
    assert c["notes"][0].startswith("different netlist")
