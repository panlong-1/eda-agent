"""flow_report: LibreLane run parsing, signoff rules, provenance, comparisons, setup diagnosis, flow_run's command.

The fixture is cut from TORV's librelane/runs/full_p10 (real report excerpts, PDK paths replaced)."""
import json
import os
import shutil
import time
from pathlib import Path

import pytest

from eda_agent.core.pnr import PnrConfig
from eda_agent.core.project import load_project
from eda_agent.tools import flow as flow_mod
from eda_agent.tools.flow import (compare_synth, evaluate_signoff, flow_command, resolve_run, run_flow_report,
                                  sdc_body, setup_issues, signoff_status)
from eda_agent.tools.reports import librelane as ll

FIXTURE = Path(__file__).parents[1] / "fixtures" / "librelane_full_p10"
SLOW, TYP, FAST = "nom_slow_1p08V_125C", "nom_typ_1p20V_25C", "nom_fast_1p32V_m40C"


def _project(tmp_path: Path, runs=("full_p10",), pnr_extra: str = "") -> tuple[Path, Path]:
    design = tmp_path / "design"
    (design / "librelane").mkdir(parents=True)
    (design / "librelane" / "config.yaml").write_text("DESIGN_NAME: rv32im_ahb_top\n")
    for name in runs:
        shutil.copytree(FIXTURE, design / "librelane" / "runs" / name)
    cfg = tmp_path / "p.toml"
    cfg.write_text(f'name = "t"\nroot = "{design}"\n[lint]\ntop = "x"\n[regress]\ncmd = ["true"]\n'
                   f'[pnr]\nconfig = "librelane/config.yaml"\nwork_dir = "{tmp_path / "work"}"\n{pnr_extra}')
    return cfg, design / "librelane" / "runs"


def _edit_metrics(run: Path, **changes) -> None:
    """Change metrics in final/metrics.json and in the post-route STA step's own metrics."""
    for p in (run / "final" / "metrics.json",):
        m = json.loads(p.read_text())
        m.update(changes)
        p.write_text(json.dumps(m))
    p = run / "55-openroad-stapostpnr" / "state_out.json"
    s = json.loads(p.read_text())
    s["metrics"].update({k: v for k, v in changes.items() if k.startswith(("timing__", "design__max"))})
    p.write_text(json.dumps(s))


def _close_timing(run: Path) -> None:
    _edit_metrics(run, **{f"timing__setup__ws__corner:{SLOW}": 0.12, f"timing__setup__tns__corner:{SLOW}": 0,
                          f"timing__setup_vio__count__corner:{SLOW}": 0})


# --- parsers on the real excerpts ------------------------------------------------------------

def test_report_parsers_on_real_excerpts():
    assert ll.parse_magic_drc((FIXTURE / "64-magic-drc/reports/drc.magic.rpt").read_text()) == 0
    assert ll.parse_klayout_drc(json.loads((FIXTURE / "65-klayout-drc/reports/drc.klayout.json").read_text())) == (0, {})
    assert ll.parse_lvs_report((FIXTURE / "70-netgen-lvs/reports/lvs.netgen.rpt").read_text()) == \
        {"final": "Circuits match uniquely.", "match": True}
    assert ll.parse_antenna_summary((FIXTURE / "46-openroad-checkantennas-1/reports/antenna.rpt").read_text()) \
        == {"nets": 0, "pins": 0}
    assert ll.parse_manufacturability(
        (FIXTURE / "76-misc-reportmanufacturability/manufacturability.rpt").read_text()) == \
        {"Antenna": True, "LVS": True, "DRC": True}
    v = ll.parse_violators((FIXTURE / "55-openroad-stapostpnr" / SLOW / "violator_list.rpt").read_text(), 3)
    assert v[0] == {"check": "setup", "group": "reg-out", "from": "_36563_/Q", "to": "s_haddr[8]", "slack": -4.349882}
    assert len(v) == 3
    p = ll.parse_power_report((FIXTURE / "55-openroad-stapostpnr" / TYP / "power.rpt").read_text())
    assert p["total_w"] == pytest.approx(1.473059e-02)
    assert ll.parse_runtime("00:53:59.979") == pytest.approx(3239.979) and ll.parse_runtime("12.5") is None
    log = ll.parse_flow_log((FIXTURE / "flow.log").read_text())
    assert log["complete"] and "Equivalence Check" in log["skipped"] and "Netgen.LVS" in log["steps_run"]


def test_report_parsers_on_failures():
    assert ll.parse_magic_drc("top\n[INFO] COUNT: 17\n") == 17
    assert ll.parse_klayout_drc({"M1.a": 2, "V1.b": 0, "M2.b": 5}) == (7, {"M1.a": 2, "M2.b": 5})
    assert ll.parse_lvs_report("Final result: Netlists do not match.\n")["match"] is False
    assert ll.parse_lvs_report("no summary") == {"final": None, "match": None}
    assert ll.parse_antenna_summary("[INFO ANT-0002] Found 3 net violations.\n[INFO ANT-0001] Found 4 pin violations.") \
        == {"nets": 3, "pins": 4}
    assert ll.parse_manufacturability("* DRC\nFailed ❌\n") == {"DRC": False}
    assert ll.parse_flow_log("[ERROR] something broke\n")["errors"] == ["something broke"]


def test_read_run_key_numbers():
    f = ll.read_run(FIXTURE)
    assert f["complete"] and f["design"] == "rv32im_ahb_top" and f["librelane_version"] == "3.0.16"
    assert f["pdk_version"] == "c4b8b4e5e7a05f375cca3815d51b3a37721fbf5c" and f["clock_period"] == 10
    assert f["area"]["die_area_um2"] == 559132 and f["area"]["utilization"] == 0.566466
    assert f["area"]["stdcell_count"] == 23130
    assert set(f["timing"]) == {SLOW, TYP, FAST}
    assert f["timing"][SLOW]["setup_wns"] == pytest.approx(-4.3499, abs=1e-4)
    assert f["timing"][SLOW]["setup_violations"] == 508 and f["timing"][FAST]["hold_wns"] == pytest.approx(0.10125, abs=1e-5)
    assert f["pre_pnr_timing"][SLOW]["setup_wns"] == pytest.approx(-1.7862, abs=1e-4)
    assert (f["drc"]["magic"], f["drc"]["klayout"], f["lvs"]["errors"], f["lvs"]["report_match"]) == (0, 0, 0, True)
    assert f["antenna"]["violating_nets"] == 0 and f["electrical"]["max_fanout_violations"] == 141
    assert f["power"]["total_w"] == pytest.approx(0.01473, abs=1e-5) and f["routing"]["wirelength_um"] == 1246192
    assert all(x["agree"] for x in f["cross_checks"]) and len(f["cross_checks"]) == 4
    assert f["config_sha256"] and f["runtime_s"] > 0


# --- signoff rules and the envelope ----------------------------------------------------------

def test_full_p10_fails_on_slow_corner_setup_only(tmp_path):
    cfg, _ = _project(tmp_path)
    r = run_flow_report(load_project(cfg), "full_p10")
    assert r.status == "fail" and r.evidence_level == "E4"
    rules = r.metrics["signoff"]
    assert rules["setup"]["outcome"] == "fail" and SLOW in rules["setup"]["detail"]
    assert {k: v["outcome"] for k, v in rules.items() if k != "setup"} == dict.fromkeys(
        ("hold", "drc_magic", "drc_klayout", "lvs", "antenna"), "pass")
    assert r.metrics["signoff_level"] == "E5-partial"
    assert r.metrics["e5_parts"]["gate_level_sim"] is False and r.metrics["e5_parts"]["drc"] is True
    assert r.metrics["wns"] == pytest.approx(-4.3499, abs=1e-4) and r.metrics["worst_corner"] == SLOW
    assert r.metrics["worst_path"]["endpoint"] == "s_haddr[8]"
    assert not r.metrics["timing_met"]
    assert "sdc_sha256" not in r.metrics  # the SDC file of the fixture does not exist: nothing to cite
    assert "gate-level simulation" in r.claim_scope and any("vectorless" in x for x in r.residual_risk)
    assert {d.code for d in r.diagnostics} >= {"SIGNOFF_SETUP", "MAX_FANOUT_VIOLATIONS", "SDC_UNKNOWN"}


def test_closed_timing_passes_every_rule(tmp_path):
    cfg, runs = _project(tmp_path)
    _close_timing(runs / "full_p10")
    r = run_flow_report(load_project(cfg), "full_p10")
    assert r.status == "pass" and r.metrics["signoff_passed"] and r.metrics["timing_met"]
    assert r.evidence_level == "E4"  # E5-partial never raises the envelope to E5


def test_drc_count_fails_and_disagreement_with_report_is_flagged(tmp_path):
    cfg, runs = _project(tmp_path)
    _close_timing(runs / "full_p10")
    _edit_metrics(runs / "full_p10", **{"klayout__drc_error__count": 3})
    r = run_flow_report(load_project(cfg), "full_p10")
    assert r.status == "fail" and r.metrics["signoff"]["drc_klayout"]["outcome"] == "fail"
    codes = {d.code for d in r.diagnostics}
    assert {"SIGNOFF_DRC_KLAYOUT", "CROSS_CHECK"} <= codes


def test_lvs_report_mismatch_fails_even_with_zero_count(tmp_path):
    cfg, runs = _project(tmp_path)
    _close_timing(runs / "full_p10")
    (runs / "full_p10" / "70-netgen-lvs/reports/lvs.netgen.rpt").write_text("Final result: Netlists do not match.\n")
    r = run_flow_report(load_project(cfg), "full_p10")
    assert r.metrics["signoff"]["lvs"]["outcome"] == "fail" and r.status == "fail"


def test_incomplete_run_is_an_error_not_a_pass(tmp_path):
    cfg, runs = _project(tmp_path)
    run = runs / "full_p10"
    _close_timing(run)
    m = json.loads((run / "final/metrics.json").read_text())
    for k in ("design__lvs_error__count", "klayout__drc_error__count"):
        m.pop(k)
    (run / "64-magic-drc").mkdir(exist_ok=True)
    (run / "64-magic-drc/state_out.json").write_text(json.dumps({"metrics": m}))
    shutil.rmtree(run / "final")
    shutil.rmtree(run / "70-netgen-lvs")
    shutil.rmtree(run / "65-klayout-drc")
    r = run_flow_report(load_project(cfg), "full_p10")
    assert r.status == "error" and r.error_class == "incomplete"
    assert r.metrics["signoff"]["lvs"]["outcome"] == "missing" and r.metrics["signoff_level"] == "E4"
    assert r.metrics["timing_met"]  # timing itself was met; only signoff is incomplete
    assert any(d.code == "INCOMPLETE" for d in r.diagnostics)


def test_no_post_route_sta_means_timing_missing():
    f = ll.read_run(FIXTURE)
    f["timing"] = {}
    rules = evaluate_signoff(f, [SLOW, TYP])
    assert rules["setup"]["outcome"] == "missing" and signoff_status(rules) == "error"


# --- provenance ---------------------------------------------------------------------------------

def _point_sdc(run: Path, sdc: Path) -> None:
    p = run / "resolved.json"
    r = json.loads(p.read_text())
    r["SIGNOFF_SDC_FILE"] = r["PNR_SDC_FILE"] = str(sdc)
    p.write_text(json.dumps(r))


def test_sdc_written_before_the_run_is_trusted_and_later_edit_is_not(tmp_path):
    cfg, runs = _project(tmp_path)
    run = runs / "full_p10"
    sdc = tmp_path / "c.sdc"
    sdc.write_text("create_clock -period 10 [get_ports hclk]\n")
    old = time.time() - 3600
    os.utime(sdc, (old, old))
    _point_sdc(run, sdc)
    r = run_flow_report(load_project(cfg), "full_p10")
    assert r.metrics["sdc_basis"] == "mtime_before_run" and len(r.metrics["sdc_sha256"]) == 64
    os.utime(sdc, None)
    os.utime(run / "resolved.json", (old, old))
    r = run_flow_report(load_project(cfg), "full_p10")
    assert r.metrics["sdc_basis"] == "unknown" and "sdc_sha256" not in r.metrics


def test_sidecar_records_the_sdc_hash(tmp_path):
    cfg, runs = _project(tmp_path)
    (runs / "full_p10" / flow_mod.SIDECAR).write_text(json.dumps({"sdc_sha256": "ab" * 32, "image": "img@sha256:1"}))
    r = run_flow_report(load_project(cfg), "full_p10")
    assert r.metrics["sdc_sha256"] == "ab" * 32 and r.metrics["sdc_basis"] == "recorded"
    assert r.tool_versions["image"] == "img@sha256:1"


def test_sdc_body_ignores_comments():
    assert sdc_body(b"# a\ncreate_clock  -period 10 x # c\n\n") == sdc_body(b"create_clock -period 10 x\n# other\n")
    assert sdc_body(b"set_input_delay 2.0 x\n") != sdc_body(b"set_input_delay 1.0 x\n")


# --- comparisons ---------------------------------------------------------------------------------

def test_compare_with_baseline_reports_deltas_and_config_differences(tmp_path):
    cfg, runs = _project(tmp_path, runs=("full_p10", "new"))
    _edit_metrics(runs / "new", **{"design__die__area": 500000, f"timing__setup__ws__corner:{SLOW}": -0.7})
    r = json.loads((runs / "new/resolved.json").read_text())
    r["SYNTH_MUL_BOOTH"] = True
    (runs / "new/resolved.json").write_text(json.dumps(r))
    res = run_flow_report(load_project(cfg), "new", baseline="full_p10")
    b = res.metrics["comparison"]["baseline"]
    assert b["metrics"]["die_area_um2"]["delta"] == 500000 - 559132
    assert b["corners"][SLOW]["setup_wns"]["delta"] == pytest.approx(-0.7 + 4.349882, abs=1e-5)
    assert [d["key"] for d in b["config_differences"]] == ["SYNTH_MUL_BOOTH"]
    assert res.metrics["comparison"]["pre_pnr_same_run"][SLOW]["setup_wns"]["baseline"] == pytest.approx(-1.786, abs=1e-3)


def test_missing_baseline_is_reported_not_fatal(tmp_path):
    cfg, _ = _project(tmp_path)
    res = run_flow_report(load_project(cfg), "full_p10", baseline="nope")
    assert "no LibreLane run" in res.metrics["comparison"]["baseline_error"] and res.status == "fail"


def test_compare_synth_matches_corners_by_name():
    f = ll.read_run(FIXTURE)
    synth = {"status": "pass", "metrics": {"sdc_sha256": "x", "cells": 14766, "area": 250000.0,
                                           "corners": {"slow": {"setup_wns": 0.698, "hold_wns": 0.3},
                                                       "typ": {"setup_wns": 3.2, "hold_wns": 0.15}}}}
    c = compare_synth(f, synth, "x")
    assert c["same_sdc"] and set(c["corners"]) == {SLOW, TYP}
    assert c["corners"][SLOW]["setup_wns"]["delta"] == pytest.approx(-4.349882 - 0.698, abs=1e-5)
    assert c["cells"]["delta"] == 23130 - 14766


# --- setup diagnosis (plan §0.11, physical rows) ---------------------------------------------

def test_setup_issues_from_missing_views_and_decks():
    res = json.loads((FIXTURE / "resolved.json").read_text())  # PDK paths point at /fixture-pdk
    cats = {i["category"] for i in setup_issues(res, "")}
    assert {"signoff_decks", "pdk_views"} <= cats


def test_setup_issue_for_a_macro_without_views_and_a_disabled_lvs():
    rules = {"lvs": {"outcome": "missing"}, "drc_magic": {"outcome": "pass"}, "drc_klayout": {"outcome": "pass"}}
    issues = setup_issues({"MACROS": {"sram": {"gds": [], "lef": ["/x.lef"]}}, "RUN_LVS": False}, "", rules)
    missing = {i["missing"] for i in issues}
    assert "macro sram: no GDS" in missing and "lvs: RUN_LVS is false" in missing


@pytest.mark.parametrize("line,category", [
    ("[ERROR] could not open /pdk/libs.ref/sg13g2_stdcell/lef/sg13g2_tech.lef: No such file or directory", "pdk_views"),
    ("Error: /pdk/lib/sg13g2_stdcell_slow_1p08V_125C.lib not found", "corner_data"),
    ("ERROR: KLayout DRC deck /pdk/libs.tech/klayout/tech/drc/ihp-sg13g2.drc does not exist", "signoff_decks"),
    ("[ERROR] PDK_ROOT is not set", "pdk_views"),
    ("[ERROR IFP-0024] Core area is too small for the design.", "floorplan_config"),
    ("[ERROR PDN-0175] Unable to find a strap layer.", "pdn_config"),
    ("[ERROR MPL-0001] Macro placement failed.", "macro_placement"),
    ("docker: Error response from daemon: pull access denied for ghcr.io/x", "image"),
    ("[ERROR] PAD_CORNER cell missing for the padring", "io_ring"),
])
def test_setup_issue_log_rules(line, category):
    issues = setup_issues({}, f"Running 'X'…\n{line}\n")
    assert [i["category"] for i in issues] == [category] and issues[0]["evidence"] == line.strip()


# --- safety: run resolution, refusal, flow_run command ----------------------------------------

def test_run_outside_the_project_is_refused(tmp_path):
    cfg, _ = _project(tmp_path)
    p = load_project(cfg)
    assert "outside" in resolve_run(p, "/etc")
    r = run_flow_report(p, "/etc")
    assert r.status == "error" and r.error_class == "bad_config"


def test_project_without_pnr_is_refused(tmp_path):
    cfg = tmp_path / "p.toml"
    cfg.write_text(f'name = "t"\nroot = "{tmp_path}"\n[lint]\ntop = "x"\n[regress]\ncmd = ["true"]\n')
    r = run_flow_report(load_project(cfg), "full_p10")
    assert r.error_class == "not_configured"


def test_pnr_config_may_not_allow_sdc_overrides():
    with pytest.raises(ValueError, match="SDC"):
        PnrConfig(config="c.yaml", allowed_overrides=("SIGNOFF_SDC_FILE",))


def test_flow_command_runs_librelane_without_network_on_the_copy(tmp_path):
    cfg, _ = _project(tmp_path, pnr_extra='image = "ghcr.io/librelane/librelane:3.0.16"\npdk = "ihp-sg13g2"\n'
                                          'allowed_overrides = ["FP_CORE_UTIL"]\n')
    p = load_project(cfg)
    work = tmp_path / "work" / "t_x"
    cmd, run_dir = flow_command(p, work, "x", {"FP_CORE_UTIL": 45})
    assert cmd[:6] == ["docker", "run", "--rm", "--network", "none", "-u"]
    i = cmd.index("ghcr.io/librelane/librelane:3.0.16")
    assert cmd[i + 1:i + 3] == ["librelane", "--manual-pdk"] and "FP_CORE_UTIL=45" in cmd
    assert cmd[-1] == str(work / "src" / "librelane" / "config.yaml")
    assert run_dir == work / "src" / "librelane" / "runs" / "x"
    assert f"{work.resolve()}:{work.resolve()}" in cmd  # the copy is the only writable mount
    assert isinstance(flow_command(p, work, "x", {"CLOCK_PERIOD": 5})[0], str)
    assert isinstance(flow_command(p, work, "x", {"PL_TARGET_DENSITY_PCT": 60})[0], str)


def test_flow_run_refuses_while_another_runs(tmp_path):
    cfg, _ = _project(tmp_path, pnr_extra='image = "ghcr.io/librelane/librelane:3.0.16"\npdk = "ihp-sg13g2"\n')
    p = load_project(cfg)
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / flow_mod.LOCK).write_text(str(os.getpid()))
    r = flow_mod.run_flow(p, tag="x")
    assert r.error_class == "busy"
