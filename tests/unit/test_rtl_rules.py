from pathlib import Path

from eda_agent.tools.rtl_rules import scan_reset_polarity

GOOD = """
module a(input clk, input rst_n, output reg q);
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) q <= 1'b0;   // reset when low: matches negedge
    else q <= 1'b1;
  end
endmodule
"""


def codes(text: str) -> list[str]:
    return [d.code for d in scan_reset_polarity(Path("a.sv"), text)]


def test_matching_async_reset_is_clean():
    assert codes(GOOD) == []
    assert codes(GOOD.replace("if (!rst_n)", "if (rst_n == 1'b0)")) == []


def test_edge_and_test_disagree():
    d = scan_reset_polarity(Path("a.sv"), GOOD.replace("if (!rst_n)", "if (rst_n)"))
    assert [x.code for x in d] == ["RESET_EDGE"] and d[0].line == 4 and d[0].file == "a.sv"
    assert codes(GOOD.replace("negedge rst_n", "posedge rst_n")) == ["RESET_EDGE"]


def test_name_says_active_low_but_sync_reset_tests_high():
    sync = "always @(posedge clk) begin\n  if (resetn) x <= 0;\nend\n"
    assert codes(sync) == ["RESET_NAME"]
    assert codes(sync.replace("if (resetn)", "if (!resetn)")) == []


def test_reset_test_in_a_later_block_or_comment_is_not_taken():
    text = ("always @(posedge clk)\n  last <= x;\n"          # one statement, no begin
            "// if (resetn) in a comment\n"
            "always @* begin\n  if (resetn) y = 1;\nend\n")  # combinational: not scanned
    assert codes(text) == []
