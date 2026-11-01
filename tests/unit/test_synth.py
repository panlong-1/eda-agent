from pathlib import Path

import pytest

from eda_agent.core.proc import docker_command, image_repo
from eda_agent.core.project import FlowConfig, load_project
from eda_agent.tools.synth import _clock_diags, _Setup, parse_check, parse_sta, parse_stat, run_check_sdc, \
    run_synth, synth_script

FIXTURES = Path(__file__).parents[1] / "fixtures"
PROJECTS = Path(__file__).parents[2] / "projects"


def test_parse_sta_reads_slack_tns_path_and_clock():
    r = parse_sta((FIXTURES / "sta_torv_slow.log").read_text())
    assert (r["setup_wns"], r["setup_tns"], r["hold_wns"], r["hold_tns"]) == (0.698, 0.0, 0.291, 0.0)
    assert r["worst_path"] == {"startpoint": "_27317_", "endpoint": "d_htrans[1]", "slack": 0.698,
                               "arrival": pytest.approx(r["worst_path"]["arrival"])}
    assert r["time_unit"] == "ns"
    assert r["clocks"] == [{"name": "CLK", "period": 10.0, "sources": ["hclk"]}]


def test_parse_sta_without_constrained_paths():
    r = parse_sta("worst slack max INF\ntns max 0.000\n")
    assert r["setup_wns"] is None and r["worst_path"] == {}


def test_parse_check_counts_unconstrained_endpoints_and_other_warnings():
    r = parse_check((FIXTURES / "sta_check_partial_sdc.log").read_text())
    assert r["unconstrained_endpoints"] == 169
    assert r["unconstrained"][:2] == ["d_haddr[0]", "d_haddr[10]"] and "s_hwrite" in r["unconstrained"]
    assert r["other_warnings"] == ["There is 1 combinational loop in the design."]


def test_parse_stat():
    stat = {"modules": {"\\top": {"num_cells": 3, "area": 12.5, "sequential_area": 4.0,
                                  "num_cells_by_type": {"a": 1, "b": 2}}}}
    assert parse_stat(stat, "top") == {"cells": 3, "area": 12.5, "sequential_area": 4.0,
                                       "top_cell_types": {"b": 2, "a": 1}}


def test_clock_diags_need_a_clock_on_the_port_with_the_flow_period():
    flow = FlowConfig(top="t", sdc="s", clock_port="hclk", clock_period=10.0, liberty="l", image="i")
    assert _clock_diags(flow, [{"name": "CLK", "period": 10.0, "sources": ["hclk"]}]) == []
    assert _clock_diags(flow, [])[0].code == "NO_CLOCK"
    assert _clock_diags(flow, [{"name": "CLK", "period": 5.0, "sources": ["hclk"]}])[0].code == "CLOCK_PERIOD"


def _flow_project(tmp_path: Path, **flow) -> Path:
    (tmp_path / "rtl").mkdir()
    (tmp_path / "rtl" / "top.sv").write_text("module top(input clk); endmodule\n")
    (tmp_path / "c.sdc").write_text("create_clock -period 10 [get_ports clk]\n")
    (tmp_path / "lib.lib").write_text("library(x) {}\n")
    (tmp_path / "du.cells").write_text("bad_cell_1\n# comment\nbad_cell_2\n")
    cfg = {"top": "top", "files": ["rtl/top.sv"], "sdc": "c.sdc", "clock_port": "clk", "clock_period": 10.0,
           "liberty": "lib.lib", "image": "ghcr.io/librelane/librelane:3.0.16", "pdk_root": "",
           "dont_use_file": "du.cells", "abc_driving_cell": "buf_4", "abc_load": 6.0, **flow}
    lines = [f'name = "t"\nroot = "{tmp_path}"\n[lint]\ntop = "top"\n[regress]\ncmd = ["true"]\n'
             '[[regress.cases]]\nname = "a"\n[flow]']
    for k, v in cfg.items():
        lines.append(f"{k} = {v!r}".replace("'", '"') if not isinstance(v, list) else
                     f"{k} = [{', '.join(repr(x).replace(chr(39), chr(34)) for x in v)}]")
    path = tmp_path / "p.toml"
    path.write_text("\n".join(lines) + "\n")
    return path


def test_flow_config_and_synth_script(tmp_path):
    project = load_project(_flow_project(tmp_path))
    assert project.flow.top == "top" and project.flow.clock_period == 10.0
    setup = _Setup(project)
    assert setup.missing() == [] and list(setup.corners) == ["default"]
    script = synth_script(setup, tmp_path / "w")
    assert script.startswith("read_liberty -lib ") and "read_slang --top top --ignore-unknown-modules" in script and str(tmp_path / "rtl" / "top.sv") in script
    assert "-dont_use bad_cell_1 -dont_use bad_cell_2" in script and "comment" not in script
    assert "-D 10000" in script and "-constr" in script and "stat -json -liberty" in script
    # the design checkout is mounted read-only; only the work dir is writable
    mounts = setup.mounts(tmp_path / "w")
    assert (tmp_path.resolve(), False) in mounts and mounts[-1] == ((tmp_path / "w").resolve(), True)


def test_unknown_flow_key_is_rejected(tmp_path):
    with pytest.raises(TypeError):
        FlowConfig(top="t", sdc="s", clock_port="c", clock_period=1, liberty="l", image="i", bogus=1)
    path = _flow_project(tmp_path, bogus_key="x")
    with pytest.raises(ValueError, match="bogus_key"):
        load_project(path)


def test_missing_flow_input_is_a_setup_error(tmp_path):
    # A missing input is "environment not ready" (plan §0.11), with the fix, not a design result.
    (tmp_path / "x").mkdir()
    project = load_project(_flow_project(tmp_path / "x", sdc="nope.sdc"))
    r = run_synth(project, work_base=tmp_path / "runs")
    assert (r.status, r.error_class, r.evidence_level) == ("error", "setup", "E4")
    assert "nope.sdc" in r.summary and r.summary.startswith("environment not ready (synth)")
    assert [i for i in r.metrics["setup_issues"] if "nope.sdc" in i["missing"] and i["who"] == "person"]


def test_project_without_flow_refuses_cleanly():
    project = load_project(PROJECTS / "picorv32.toml")
    assert project.flow is None
    for r in (run_synth(project), run_check_sdc(project)):
        assert (r.status, r.error_class) == ("error", "not_configured")
        assert "no [flow] section" in r.summary and "{" not in r.claim_scope
    assert run_check_sdc(project).evidence_level == "E0"  # check_sdc never promotes


def test_torv_flow_section_mirrors_librelane_config():
    flow = load_project(PROJECTS / "torv.toml").flow
    assert (flow.top, flow.clock_port, flow.clock_period, flow.sdc) == \
        ("torv_cpu", "hclk", 10.0, "librelane/constraints.sdc")
    assert set(flow.corners) == {"typ", "slow", "fast"}


def test_docker_command_allowlist():
    cmd = docker_command("ghcr.io/librelane/librelane:3.0.16", ["sta", "-exit", "x.tcl"],
                         [(Path("/d"), False), (Path("/w"), True)])
    assert cmd[:2] == ["docker", "run"] and "--network" in cmd
    assert "/d:/d:ro" in cmd and "/w:/w" in cmd and cmd[-3:] == ["sta", "-exit", "x.tcl"]
    assert "not allowed" in docker_command("evil/image:1", ["yosys"], [])
    assert "not allowed inside" in docker_command("ghcr.io/librelane/librelane:3.0.16", ["bash", "-c", "x"], [])
    assert isinstance(docker_command("my/img:2", ["yosys"], [], extra_images=("my/img",)), list)
    assert image_repo("ghcr.io/librelane/librelane@sha256:ab") == "ghcr.io/librelane/librelane"
    assert image_repo("localhost:5000/a/b:1") == "localhost:5000/a/b"
