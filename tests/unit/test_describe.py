from pathlib import Path

from eda_agent.core.project import load_project, regress_cases
from eda_agent.tools.describe import MAX_LINES, describe_cases, leading_comment, list_cases

TORV_STYLE = """\
# =============================================================================
# muldiv_stall.S — after multi-cycle DIV/REM mix immediates/MUL (stall + forwarding)
# =============================================================================
  .section .text.init
# not part of the header
"""

RISCV_TESTS_STYLE = """\
# See LICENSE for license details.

#*****************************************************************************
# add.S
#-----------------------------------------------------------------------------
#
# Test add instruction.
#

#include "riscv_test.h"
"""

SV_STYLE = """\
/*
 * tb_fifo: fills the FIFO to full,
 * then drains it.
 */
// checks the almost_full flag too
module tb_fifo;
"""


def test_leading_comment_torv():
    text, truncated = leading_comment(TORV_STYLE)
    assert text == "muldiv_stall.S — after multi-cycle DIV/REM mix immediates/MUL (stall + forwarding)"
    assert not truncated


def test_leading_comment_stops_at_preprocessor_directive():
    text, _ = leading_comment(RISCV_TESTS_STYLE)
    assert "Test add instruction." in text and "include" not in text
    assert text.startswith("See LICENSE")


def test_leading_comment_block_and_line_comments():
    text, _ = leading_comment(SV_STYLE)
    assert text.splitlines() == ["tb_fifo: fills the FIFO to full,", "then drains it.",
                                 "checks the almost_full flag too"]


def test_leading_comment_truncates():
    text, truncated = leading_comment("".join(f"// line {i}\n" for i in range(50)) + "code\n")
    assert truncated and len(text.splitlines()) == MAX_LINES


def _list_project(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/muldiv_stall.S").write_text(TORV_STYLE)
    (tmp_path / "tests/bypass.S").write_text("# bypass.S — forwarding: EX chain\nnop\n")
    (tmp_path / "cases.list").write_text(
        "# name type source cycles\n"
        "bypass       asm tests/bypass.S       8000\n"
        "muldiv_stall asm tests/muldiv_stall.S 30000\n"
        "orphan       asm tests/missing.S      100\n")
    toml = tmp_path / "p.toml"
    toml.write_text(f'name = "p"\nroot = "{tmp_path}"\n[lint]\ntop = "t"\n'
                    '[regress]\ncmd = ["true"]\ncase_list = "cases.list"\n')
    return toml


def test_describe_cases_from_case_list(tmp_path):
    project = load_project(_list_project(tmp_path))
    assert regress_cases(project) == ["bypass", "muldiv_stall", "orphan"]
    out = describe_cases(project, ["muldiv_stall", "orphan"])
    assert out["status"] == "ok"
    stall, orphan = out["cases"]
    # The case that a name-only choice missed: its header says it also tests forwarding.
    assert stall["source"] == "tests/muldiv_stall.S" and "forwarding" in stall["description"]
    assert stall["list_entry"].startswith("muldiv_stall")
    assert orphan["source"] == "" and orphan["description"].startswith("(no description")


def test_list_cases_has_one_line_summaries(tmp_path):
    project = load_project(_list_project(tmp_path))
    out = list_cases(project)["cases"]
    assert [c["name"] for c in out] == ["bypass", "muldiv_stall", "orphan"]
    assert "forwarding" in out[1]["summary"]  # visible in the listing, before any describe_case call


def test_list_cases_skips_licence_line(tmp_path):
    (tmp_path / "add.S").write_text(RISCV_TESTS_STYLE)
    toml = tmp_path / "p.toml"
    toml.write_text(f'name = "p"\nroot = "{tmp_path}"\n[lint]\ntop = "t"\n'
                    '[regress]\nformat = "exitcode"\ncmd = ["true"]\n'
                    '[[regress.cases]]\nname = "add"\nsource = "add.S"\n')
    assert list_cases(load_project(toml))["cases"] == [{"name": "add", "summary": "add.S"}]


def test_describe_cases_rejects_unknown_names(tmp_path):
    project = load_project(_list_project(tmp_path))
    out = describe_cases(project, ["bypass", "../etc/passwd"])
    assert out["status"] == "error" and out["error_class"] == "bad_request"
    assert describe_cases(project, [])["status"] == "error"


def test_inline_case_description_is_preferred(tmp_path):
    (tmp_path / "start.S").write_text("// licence text\n")
    toml = tmp_path / "p.toml"
    toml.write_text(f'name = "p"\nroot = "{tmp_path}"\n[lint]\ntop = "t"\n'
                    '[regress]\nformat = "exitcode"\ncmd = ["true"]\n'
                    '[[regress.cases]]\nname = "fw"\nsource = "start.S"\ndescription = "runs everything"\n')
    (case,) = describe_cases(load_project(toml), ["fw"])["cases"]
    assert case == {"name": "fw", "source": "start.S", "description": "runs everything", "truncated": False}
