# Run receipt m1_torv_hazard3

- **Task:** Run the forwarding and load-use hazard cases and tell me if the pipeline hazards are handled
- **Project:** torv  ·  **Model:** deepseek / deepseek-v4-pro
- **Stop:** end  ·  **Status:** done  ·  **Gate:** passed (1 rewrite(s))
- **Cost:** 5 turns, 4 tool calls (0 errors), 14847 in / 1211 out tokens, 52.02 s

## Checks run

| tool | args | status | evidence | summary |
|---|---|---|---|---|
| regress | `{"cases": ["bypass", "hazard", "muldiv_stall"]}` | pass | E2 | 3 passed, 0 failed, 0 skipped |

**regress {"cases": ["bypass", "hazard", "muldiv_stall"]}** claim scope: The listed directed cases ran to completion and each reported pass through the testbench's own self-check. It does not show that the tests would catch a bug (no coverage or mutation score), and says nothing about timing or power.
- artifact `/home/panlong/project/rv32m/regress/results/20261031_155939/summary.rpt` sha256 `2653007748814c76dd9c75a7ab9cda41439ffa5b6182a89a8e3e848c89ceb832`

## Not run

- lint run: no
- described but not run: none
- cases in the list not run (30): smoke, alu, imm, branch, compare_edges, shift_edges, jump, flush, x0, muldiv, loadstore, mem_offsets, sbus, bus_mix_dbus_sbus, bus_sbus_widths, bus_ibus_dbus_fill, hazard_branch_mem_stall, hazard_ibus_store_dup, hazard_sbus_replay_smoke, hazard_ibus_insn_dup, hazard_ibus_load_dup, hazard_sbus_rx_replay_smoke, sram_wbuf, perf_alu_chain, perf_branch_loop, perf_loadstore_loop, perf_mul_loop, uart_smoke, replay_ibus_store, replay_div_store

## Gate decisions

1. reject (best passing evidence E2)
   - 'correctly' claims functional correctness, which needs E3+; the best passing evidence here is E2, which supports only 'these checks/cases passed': 'That supports that the tested forwarding and load-use scenarios run correctly in simulation'
2. pass (best passing evidence E2)

## Final claims

I ran the directed regression cases for forwarding and load-use hazards.

What I ran:
- `bypass` — forwarding: EX chain, register shifts, load→ALU on DBUS 0x2000_0000
- `hazard` — load-use (ALU/branch), narrow load sign/zero extend, load→store forwarding
- `muldiv_stall` — stall + forwarding after multi-cycle DIV/REM

Result:
- Tool status: `pass`, evidence_level `E2`
- `3 passed, 0 failed, 0 skipped`
- Passing cases: `bypass`, `muldiv_stall`, `hazard`
- 0 diagnostics

Conclusion:
At evidence level E2, these directed hazard cases passed. The tool's claim scope is: "The listed directed cases ran to completion and each reported pass through the testbench's own self-check." This supports only that these checks/cases passed; it does not establish that the forwarding and load-use logic is correct or that pipeline hazards are fully handled.

## Residual risk

:
- No coverage or mutation score was produced, so the tests’ ability to catch a wrong implementation is unproven.
- Pass/fail is reported by each test program’s own self-check.
- These checks are functional simulation cases; they say nothing about timing, power, or physical implementation.
- Cases not run were not evaluated.

From the tools:
- pass/fail is reported by the test program itself
- test adequacy unknown: no coverage or mutation score yet

Trace: `runs/m1_torv_hazard3.jsonl`
