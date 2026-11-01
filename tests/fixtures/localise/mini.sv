// Synthetic design for the localisation tests (written for eda-agent; not from any project).
// sel: y = a when a_en, else b when b_en, else 0. The b_en branch below has a planted bug:
// its condition is a constant, so y never takes b.
module sel (
  input  logic       a_en,
  input  logic       b_en,
  input  logic [3:0] a,
  input  logic [3:0] b,
  output logic [3:0] y
);
  always_comb begin
    y = 4'd0;
    if (a_en)
      y = a;
    else if (1'b0)
      y = b;
  end
endmodule

module top (
  input  logic       clk,
  input  logic       rst,
  input  logic       en,
  input  logic       ben,
  output logic [3:0] out
);
  logic [3:0] cnt;
  logic [3:0] sum;
  always_ff @(posedge clk) begin
    if (rst) cnt <= '0;
    else     cnt <= cnt + 4'd1;
  end
  sel u_sel (.a_en(en), .b_en(ben), .a(cnt), .b(4'd3), .y(sum));
  assign out = sum;
endmodule
