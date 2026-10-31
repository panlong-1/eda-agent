"""M1.2 against real inputs: TORV's LibreLane config, and a Verilator command file."""
import shutil
from pathlib import Path

import pytest

from eda_agent.core.project import load_project
from eda_agent.tools.lint import run_lint

pytestmark = pytest.mark.eda
REPO = Path(__file__).resolve().parents[2]
TORV = Path("~/project/rv32m").expanduser()


@pytest.mark.skipif(not (TORV / "librelane" / "config.yaml").is_file(), reason="TORV checkout missing")
def test_librelane_line_equals_hand_written_torv_flow(tmp_path):
    """Two lines of [flow] give the same synth setup as the 30 hand-written ones in projects/torv.toml."""
    written = load_project(REPO / "projects" / "torv.toml")
    short = tmp_path / "torv_short.toml"
    short.write_text(f'name = "torv"\nroot = "{TORV}"\nenv_script = "scripts/env.sh"\n'
                     '[env_defaults]\nPDK_ROOT = "~/.ciel"\n'
                     '[lint]\ntop = "torv_soc"\n[regress]\nformat = "torv"\ncmd = ["scripts/regress.sh"]\n'
                     'case_list = "regress/cases/soc.list"\n'
                     '[flow]\nlibrelane = "librelane/config.yaml"\n')
    assert load_project(short).flow == written.flow


@pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator missing")
def test_lint_from_a_command_file_drops_external_warnings(tmp_path):
    (tmp_path / "rtl").mkdir()
    (tmp_path / "vendor").mkdir()
    (tmp_path / "rtl" / "top.sv").write_text(
        "module top(input logic a, output logic y);\n  logic spare_ours;\n  ip u(.a(a), .y(y));\nendmodule\n")
    (tmp_path / "vendor" / "ip.sv").write_text(
        "module ip(input logic a, output logic y);\n  logic spare_theirs;\n  assign y = a;\nendmodule\n")
    vc_dir = tmp_path / "build" / "x" / "lint-verilator"
    vc_dir.mkdir(parents=True)
    (vc_dir / "x.vc").write_text("../../../vendor/ip.sv\n../../../rtl/top.sv\n")
    cfg = tmp_path / "p.toml"
    cfg.write_text(f'name = "p"\nroot = "{tmp_path}"\n[lint]\ntop = "top"\n'
                   'command_files = ["build/x/lint-verilator/x.vc"]\nexternal = ["vendor"]\n'
                   '[regress]\ncmd = ["make", "test"]\n')
    r = run_lint(load_project(cfg))
    assert r.status == "pass", r.summary
    files = {d.file for d in r.diagnostics}
    assert "rtl/top.sv" in files and not any(f.startswith("vendor") for f in files)
    assert r.metrics["external_warnings_dropped"] >= 1


@pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator missing")
def test_lint_maps_a_copy_of_the_root_back_to_the_original(tmp_path):
    (tmp_path / "rtl").mkdir()
    src = "module top(input logic a, output logic y);\n  logic spare;\n  assign y = a;\nendmodule\n"
    (tmp_path / "rtl" / "top.sv").write_text(src)
    copy = tmp_path / "build" / "c" / "src" / "c"
    (copy / "rtl").mkdir(parents=True)
    (copy / "rtl" / "top.sv").write_text(src)
    vc_dir = tmp_path / "build" / "c" / "lint-verilator"
    vc_dir.mkdir(parents=True)
    (vc_dir / "c.vc").write_text("../src/c/rtl/top.sv\n")
    cfg = tmp_path / "p.toml"
    cfg.write_text(f'name = "p"\nroot = "{tmp_path}"\n[lint]\ntop = "top"\n'
                   'command_files = ["build/c/lint-verilator/c.vc"]\nexternal = ["build"]\n'
                   'copy_of_root = ["build/c/src/c"]\n[regress]\ncmd = ["make", "test"]\n')
    r = run_lint(load_project(cfg))
    assert {d.file for d in r.diagnostics} == {"rtl/top.sv"} and r.metrics["external_warnings_dropped"] == 0
