import tomllib
from pathlib import Path

import pytest

from eda_agent.core.flow_sources import PDK_PRESETS, read_librelane, resolve_flow
from eda_agent.core.project import load_project
from eda_agent.core.settings import find_project, user_defaults
from eda_agent.onboard.detect import detect, make_targets
from eda_agent.onboard.doctor import diagnose
from eda_agent.onboard.init import render, write_draft

CORE = """\
CAPI=2:
name: acme:lab:my_accel:0.1.0
filesets:
  rtl:
    files: [rtl/my_accel.sv]
    file_type: systemVerilogSource
targets:
  sim:
    default_tool: verilator
    filesets: [rtl]
    toplevel: tb_my_accel
  lint:
    default_tool: verilator
    filesets: [rtl]
    toplevel: my_accel
    tools:
      verilator:
        mode: lint-only
        verilator_options: [-Wall, -Wno-fatal]
"""

MAKEFILE = """\
CLK_PERIOD ?= 8.0
.PHONY: sim lint
help:
\t@echo help
sim: vectors
\tfusesoc run --target sim acme:lab:my_accel
lint:
\tfusesoc run --target lint acme:lab:my_accel
"""

LIBRELANE = """\
PDK: ihp-sg13g2
DESIGN_NAME: core
VERILOG_FILES:
  - dir::../rtl/a.sv
  - dir::../rtl/core.sv
VERILOG_INCLUDE_DIRS: [dir::../rtl]
USE_SLANG: true
SYNTH_MUL_BOOTH: true
CLOCK_PORT: clk
CLOCK_PERIOD: 10.0
PNR_SDC_FILE: dir::constraints.sdc
SIGNOFF_SDC_FILE: dir::constraints.sdc
"""


def _accel_repo(tmp_path: Path) -> Path:
    root = tmp_path / "my_accel"
    (root / "rtl").mkdir(parents=True)
    (root / "tb").mkdir()
    (root / "rtl" / "my_accel.sv").write_text("module my_accel; endmodule\n")
    (root / "tb" / "tb_my_accel.sv").write_text('initial $display("TEST PASSED");\n'
                                                '// on error: $display("TEST FAILED")\n')
    (root / "accel.core").write_text(CORE)
    (root / "Makefile").write_text(MAKEFILE)
    (root / "constraints.sdc").write_text("create_clock -period 8 [get_ports clk_i]\n")
    return root


def test_make_targets():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        mk = Path(d) / "Makefile"
        mk.write_text(MAKEFILE + "X := 1\nfoo.o: foo.c\n")
        assert make_targets(mk) == ["help", "sim", "lint", "foo.o"]


def test_detect_fusesoc_accelerator(tmp_path):
    det = detect(_accel_repo(tmp_path), run_fusesoc=False)
    assert det.lint["top"].value == "my_accel" and "targets.lint.toplevel" in det.lint["top"].source
    assert det.lint["flags"].value == ["-Wall", "-Wno-fatal"]
    assert det.regress["cmd"].value == ["make", "sim"] and det.regress["cmd"].confirm
    assert det.regress["pass_regex"].value == "TEST PASSED"
    assert det.regress["fail_regex"].value == "TEST FAILED"
    assert "fusesoc" in det.versions
    assert any("SDC found" in n and "CLK_PERIOD = 8.0" in n for n in det.notes)
    assert not det.flow  # no LibreLane config: synth is set up by hand


def test_draft_names_sources_and_loads(tmp_path):
    root = _accel_repo(tmp_path)
    out, _ = write_draft(root, tmp_path / "p.toml", "2026-10-31", run_fusesoc=False)
    text = out.read_text()
    assert text.startswith("# DRAFT") and "CHECK from: Makefile target `sim`" in text
    project = load_project(out)
    assert (project.lint.top, project.regress.cmd, project.regress.pass_regex) == \
        ("my_accel", ("make", "sim"), "TEST PASSED")
    with pytest.raises(FileExistsError):
        write_draft(root, out, "2026-10-31", run_fusesoc=False)


def test_doctor_reports_without_secrets(tmp_path, monkeypatch):
    root = _accel_repo(tmp_path)
    out, _ = write_draft(root, tmp_path / "p.toml", "2026-10-31", run_fusesoc=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-not-a-real-key-0000")
    checks = {c.name: c for c in diagnose(out, provider="deepseek")}
    assert checks["config"].status == "ok" and checks["root"].status == "ok"
    assert checks["api key deepseek"].status == "ok"
    assert all("sk-" not in c.detail for c in checks.values())
    assert checks["flow"].status == "warn"
    broken = tmp_path / "broken.toml"
    broken.write_text('name = "x"\nroot = "/nope"\n[lint]\ntop = "t"\n[regress]\ncmd = ["make"]\n')
    assert [c.status for c in diagnose(broken)][-1] == "fail"


def test_librelane_config_and_pdk_preset(tmp_path):
    (tmp_path / "librelane").mkdir()
    (tmp_path / "librelane" / "config.yaml").write_text(LIBRELANE)
    got = read_librelane(tmp_path / "librelane" / "config.yaml", tmp_path)
    assert got["files"] == ["rtl/a.sv", "rtl/core.sv"] and got["include_dirs"] == ["rtl"]
    assert got["sdc"] == "librelane/constraints.sdc" and got["clock_period"] == 10.0
    assert (got["frontend"], got["synth_flags"]) == ("slang", ["-booth"])
    flow = resolve_flow({"librelane": "librelane/config.yaml", "clock_period": 8.0}, tmp_path)
    assert flow["clock_period"] == 8.0  # written in [flow] wins
    assert flow["corners"] == PDK_PRESETS["ihp-sg13g2"]["corners"] and flow["image"].startswith("ghcr.io/")


def test_detect_librelane(tmp_path):
    (tmp_path / "rtl").mkdir()
    (tmp_path / "librelane").mkdir()
    (tmp_path / "librelane" / "config.yaml").write_text(LIBRELANE)
    det = detect(tmp_path, run_fusesoc=False)
    assert det.flow["librelane"].value == "librelane/config.yaml"
    assert det.lint["top"].value == "core" and det.lint["files"].value == ["rtl/a.sv", "rtl/core.sv"]
    tomllib.loads(render(det, "2026-10-31"))  # the draft is valid TOML


def test_find_project_and_user_defaults(tmp_path, monkeypatch):
    (tmp_path / "eda-agent.toml").write_text("")
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    assert find_project(deep) == tmp_path / "eda-agent.toml"
    cfg = tmp_path / "config.toml"
    cfg.write_text('provider = "deepseek"\nreview = "I2"\n')
    assert user_defaults(cfg) == {"provider": "deepseek", "review": "I2"}
    cfg.write_text('provder = "deepseek"\n')
    with pytest.raises(ValueError, match="unknown key"):
        user_defaults(cfg)


def test_summary_regex_from_the_testbench_display(tmp_path):
    import re
    from eda_agent.onboard.detect import summary_regex_from_source
    tb = ('$display("[tb_x] scenario %s: %0d/%0d cases ok (%0d model_only), %0d failures", n, p, t, m, f);\n'
          '$display("accepted %0d/%0d, y_rsp %0d", a, b, c);\n')
    rx = summary_regex_from_source(tb)
    assert re.search(rx, "[tb_x] scenario regress: 88/90 cases ok (7 model_only)").groupdict() == \
        {"passed": "88", "total": "90"}
    assert summary_regex_from_source('$display("accepted %0d/%0d, y_rsp %0d", a, b, c);') is None
    root = _accel_repo(tmp_path)
    (root / "tb" / "tb_my_accel.sv").write_text(tb + '$display("TEST PASSED");\n')
    out, det = write_draft(root, tmp_path / "p.toml", "2026-10-31", run_fusesoc=False)
    assert det.regress["summary_regex"].confirm
    assert load_project(out).regress.summary_regex == rx  # survives the TOML round trip


def test_read_command_file(tmp_path):
    from eda_agent.core.project import read_command_file
    d = tmp_path / "build" / "c" / "synth-verilator"
    d.mkdir(parents=True)
    (d / "c.vc").write_text("--Mdir .\n+incdir+../src/ip/include\n-CFLAGS -I../src/ip/include\n"
                            "../src/ip/src/fifo.sv\n../src/c/rtl/top.sv\n--top-module top\n")
    files, incs = read_command_file(d / "c.vc")
    assert files == [tmp_path / "build/c/src/ip/src/fifo.sv", tmp_path / "build/c/src/c/rtl/top.sv"]
    assert incs == [tmp_path / "build/c/src/ip/include"]


def test_check_presets_load():
    from eda_agent.cli import load_preset
    assert "Residual risk" in load_preset("check") and "synthesize" in load_preset("full")
