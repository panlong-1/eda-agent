"""flow_report on TORV's own LibreLane run librelane/runs/full_p10 (read-only; needs the TORV checkout)."""
import subprocess
from pathlib import Path

import pytest

from eda_agent.core.project import load_project
from eda_agent.tools.flow import run_flow_report

PROJECTS = Path(__file__).parents[2] / "projects"
TORV = Path("~/project/rv32m").expanduser()
RUN = TORV / "librelane" / "runs" / "full_p10"

pytestmark = [pytest.mark.eda,
              pytest.mark.skipif(not (RUN / "final" / "metrics.json").is_file(), reason="no TORV full_p10 run")]


def _status() -> str:
    return subprocess.run(["git", "status", "--porcelain"], cwd=TORV, capture_output=True, text=True).stdout


def test_flow_report_full_p10():
    before = _status()
    r = run_flow_report(load_project(PROJECTS / "torv.toml"), "full_p10", baseline="")
    assert _status() == before  # nothing in the TORV checkout changed
    m = r.metrics
    assert r.status == "fail" and m["signoff"]["setup"]["outcome"] == "fail"
    assert all(m["signoff"][k]["outcome"] == "pass" for k in ("hold", "clock_propagated", "drc_magic", "drc_klayout", "lvs", "antenna"))
    assert m["drc_by_tool"] == {"Magic": 0, "KLayout": 0} and m["experience_table"] == "imported"
    assert m["signoff_level"] == "E5-partial" and r.evidence_level == "E4"
    assert m["worst_corner"] == "nom_slow_1p08V_125C" and m["wns"] == pytest.approx(-4.3499, abs=1e-3)
    assert m["area"]["die_area_um2"] == 559132 and m["area"]["stdcell_count"] == 23130
    assert m["design"] == "rv32im_ahb_top" and any(d.code == "OTHER_TOP" for d in r.diagnostics)
    # TORV's SDC was committed again after this run: its run-time content is not known for sure
    assert m["sdc_basis"] == "changed_since_run" and "sdc_sha256" not in m
    assert all(a["sha256"] for a in r.artifacts)


def test_flow_history_over_all_torv_runs():
    import json
    from eda_agent.tools.flow_history import run_flow_history
    r = run_flow_history(load_project(PROJECTS / "torv.toml"), baseline="p10")
    runs = {x["run"]: x for x in r.metrics["tables"]["imported"]["runs"]}
    names = {d.name for d in (TORV / "librelane" / "runs").iterdir() if (d / "resolved.json").is_file()}
    assert set(runs) == names and r.status == "pass" and r.evidence_level == "E0"
    for name, row in runs.items():  # the numbers are the runs' own metrics
        m = json.loads((TORV / "librelane" / "runs" / name / "final" / "metrics.json").read_text()) \
            if (TORV / "librelane" / "runs" / name / "final" / "metrics.json").is_file() else {}
        if row["post_route"] and m:
            assert row["post_route"]["die_area_um2"] == m["design__die__area"]
        elif m:
            assert row["synth"]["cell_area_um2"] == m["design__instance__area"]
    lessons = {x["key"]: x["verdict"] for x in r.metrics["lessons"]}
    assert lessons["SYNTH_MUL_BOOTH"] == "confirmed" and lessons["SYNTH_ABC_BUFFERING"] == "cannot_test"


def test_flow_report_compares_with_baseline():
    r = run_flow_report(load_project(PROJECTS / "torv.toml"), "full_p10_fwd", baseline="full_p10")
    b = r.metrics["comparison"]["baseline"]
    assert b["baseline"] == "full_p10" and b["metrics"]["die_area_um2"]["delta"] < 0
    assert b["corners"]["nom_slow_1p08V_125C"]["setup_wns"]["delta"] > 0


E5_1 = Path(__file__).parents[2] / "runs" / "pnr" / "torv_m4_e5_1" / "src" / "librelane" / "runs" / "m4_e5_1"


@pytest.mark.skipif(not (E5_1 / "final" / "metrics.json").is_file(), reason="m4_e5_1 not run here")
def test_fresh_run_m4_e5_1_lineage_snapshot_and_experience():
    p = load_project(PROJECTS / "torv.toml")
    r = run_flow_report(p, str(E5_1), baseline="full_p10_fwd")
    m = r.metrics
    assert m["experience_table"] == "eda_agent" and m["signoff_level"] == "E5-partial"
    assert m["sdc_basis"] == "recorded" and len(m["sdc_sha256"]) == 64 and m["pdk_version"]
    lin = m["lineage"]
    assert lin["complete"] and not lin["breaks"] and len(lin["stages"]) == 8
    assert all(f["matches"] for f in lin["final"])
    assert r.snapshot["state"] == "current" and r.snapshot["id"]
    assert not any(d.code == "CLOCK_SOURCE_LATENCY" for d in r.diagnostics)
    # the current TORV reproduces full_p10_fwd exactly (same knobs, same netlist)
    b = m["comparison"]["baseline"]
    assert all(row.get("delta", 0) == 0 for k, row in b["metrics"].items() if k != "runtime_s")
    from eda_agent.core.snapshot import project_snapshot
    from eda_agent.core.snapshot import compare_snapshots
    assert compare_snapshots(project_snapshot(p), r.snapshot)["same_design"] is True
