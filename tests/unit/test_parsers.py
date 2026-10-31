from pathlib import Path

from eda_agent.core.project import expand_vars, read_filelist
from eda_agent.tools.lint import parse_verilator
from eda_agent.tools.formats.torv import parse_summary

VERILATOR_OUT = """\
%Warning-UNUSEDSIGNAL: /work/rtl/a.sv:12:5: Signal is not used: 'x'
                       : ... note: In instance 'top'
%Error: /work/rtl/top.sv:265:22: Can't find definition of 'fifo_tx_valid'
%Warning-LATCH: /work/rtl/b.sv:40:3: Latch inferred for signal 'q'
%Error: Exiting due to 1 error(s), 2 warning(s)
"""


def test_parse_verilator():
    diags = parse_verilator(VERILATOR_OUT)
    assert [(d.severity, d.code, d.line) for d in diags] == [
        ("warning", "UNUSEDSIGNAL", 12), ("error", "ERROR", 265), ("warning", "LATCH", 40),
    ]
    assert diags[1].file == "/work/rtl/top.sv" and diags[1].col == 22


def test_expand_vars():
    env = {"TORV_ROOT": "/r", "PDK_ROOT": "/p"}
    assert expand_vars("$TORV_ROOT/rtl/a.sv", env) == "/r/rtl/a.sv"
    assert expand_vars("${PDK_ROOT}/x.v", env) == "/p/x.v"
    assert expand_vars("$UNSET/x", env) == "$UNSET/x"


def test_read_filelist(tmp_path: Path):
    fl = tmp_path / "files.f"
    fl.write_text("# comment\n\n$TORV_ROOT/rtl/a.sv\nrtl/b.sv\n// c\n")
    files = read_filelist(fl, {"TORV_ROOT": "/r"}, tmp_path)
    assert files == [Path("/r/rtl/a.sv"), tmp_path / "rtl/b.sv"]


def test_parse_summary():
    text = "# TORV regression summary\ncase,status,detail\nsmoke,PASS,/d/smoke\nalu,FAIL,/d/alu\nold,SKIP,legacy\n"
    assert parse_summary(text) == [("smoke", "PASS", "/d/smoke"), ("alu", "FAIL", "/d/alu"),
                                   ("old", "SKIP", "legacy")]
