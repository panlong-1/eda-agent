"""Localisation tools on the real TORV checkout (read-only; nothing is simulated or written).

The forwarding unit's forward_b is traced from the core: the always_comb inside
rtl/torv_forwarding_unit.sv must show each assignment with its if/else-if conditions, so a wrong
condition (the kind of change a mutant makes) is one step away.
"""
from pathlib import Path

import pytest

from eda_agent.core.project import load_project

pytest.importorskip("pyslang")
PROJECT = Path(__file__).parents[2] / "projects" / "torv.toml"
pytestmark = [pytest.mark.eda,
              pytest.mark.skipif(not (load_project(PROJECT).root / "rtl" / "torv_forwarding_unit.sv").is_file(),
                                 reason="TORV checkout missing")]


def test_torv_elaborates_without_errors():
    from eda_agent.tools.rtl_trace import run_hierarchy
    r = run_hierarchy(load_project(PROJECT), "u_cpu.u_core", depth=1)
    assert r.status == "pass" and r.metrics["compile"]["error_count"] == 0
    kids = {c["path"].rsplit(".", 1)[-1]: c["module"] for c in r.metrics["tree"]["children"]}
    assert kids["u_fwd"] == "torv_forwarding_unit" and kids["u_hz"] == "torv_hazard_unit"


def test_forward_b_trace_reaches_the_forwarding_conditions():
    from eda_agent.tools.rtl_trace import run_trace_driver
    r = run_trace_driver(load_project(PROJECT), "u_cpu.u_core.forward_b", depth=3)
    locs = set(r.metrics["locations"])
    assert {"rtl/torv_forwarding_unit.sv:21", "rtl/torv_forwarding_unit.sv:23",
            "rtl/torv_forwarding_unit.sv:24"} <= locs
    step = [s for s in r.metrics["steps"] if s["at"] == "rtl/torv_forwarding_unit.sv:24"][0]
    assert [c["branch"] for c in step["conditions"]] == ["false", "true"]
    assert "mem_wb_rd" in step["conditions"][1]["text"]
