# Run receipt m2_torv_e2e_approval_demo

- **Task:** Check the TORV core end to end: lint it, run the smoke regression case, synthesize it and estimate timing at the project clock, and check that the SDC constrains every endpoint. Report what passed, and whether the timing estimate meets the project clock target.
- **Project:** torv  ·  **Model:** deepseek / deepseek-v4-pro  ·  **Prompt:** system.v2
- **Stop:** end  ·  **Status:** approved (agent: done)  ·  **Gate:** passed (0 rewrite(s))
- **Cost:** 6 turns, 6 tool calls (0 errors), 35711 in / 2543 out tokens, 70.13 s

## Checks run

| tool | args | status | evidence | summary |
|---|---|---|---|---|
| lint | `{}` | pass | E0 | 0 error(s), 43 warning(s), top=torv_soc |
| regress | `{"cases": ["smoke"]}` | pass | E2 | 1 passed, 0 failed, 0 skipped |
| synth | `{}` | pass | E4 | 14766 cells, area 236631, 0 latch(es); setup WNS +0.698 ns (corner slow), hold WNS +0.068 at 10 ns; timing met under this SDC, pre-PnR estimate |
| check_sdc | `{}` | pass | E0 | 1 clock(s), 0 unconstrained endpoint(s), 0 other check_setup warning(s) |

**lint {}** claim scope: Verilator parsed and elaborated the design under the project's flags. This proves nothing about functional behaviour or timing.
- verilator: Verilator 5.020 2024-01-01 rev (Debian 5.020-1)

**regress {"cases": ["smoke"]}** claim scope: The listed directed cases ran to completion and each reported pass through the testbench's own self-check. It does not show that the tests would catch a bug (no coverage or mutation score), and says nothing about timing or power.
- artifact `/home/panlong/project/rv32m/regress/results/20261031_181317/summary.rpt` sha256 `165805b2d8209d8d851ceaf72e166ba75b269a541945b61857501b4c6a2fb90c`
- verilator: Verilator 5.020 2024-01-01 rev (Debian 5.020-1)
- gcc: riscv64-unknown-elf-gcc (13.2.0-11ubuntu1+12) 13.2.0

**synth {}** claim scope: Yosys mapped top 'torv_cpu' to sg13g2_stdcell_typ_1p20V_25C.lib; OpenSTA estimated timing of that netlist under SDC librelane/constraints.sdc (sha256 00c08cf20a426fef862822406e1ac4f9ac59338e8efa830866170b9c91390472) at a 10 ns clock on corner(s) typ, slow, fast. This is an estimate under this SDC, before placement and routing: ideal clocks, no wire parasitics. It is not signoff and says nothing about function.
- artifact `/home/panlong/project/rv32m/librelane/constraints.sdc` sha256 `00c08cf20a426fef862822406e1ac4f9ac59338e8efa830866170b9c91390472`
- artifact `/home/panlong/project/eda-agent/runs/flow/torv_synth_20261031_181336_535898/netlist.v` sha256 `8128e28ccc3400ed1473b1d2892fa934c5e284edeb09b3dacc157a0389d1eafa`
- artifact `/home/panlong/project/eda-agent/runs/flow/torv_synth_20261031_181336_535898/stat.json` sha256 `f1b2cd307a91f3833c587242091d3f5091e8da95e4156abcb9132faedf53d696`
- artifact `/home/panlong/project/eda-agent/runs/flow/torv_synth_20261031_181336_535898/yosys.log` sha256 `0ecd2a84f3bea323ddd8d4c691d20b0db09e8ae3e4bd766fdd4fd13294fbb852`
- artifact `/home/panlong/project/eda-agent/runs/flow/torv_synth_20261031_181336_535898/sta_typ.log` sha256 `f7c4f42f6b60da190970721ce28b0297a1cf83292b98ed6156d6ad8ab41a9e80`
- artifact `/home/panlong/project/eda-agent/runs/flow/torv_synth_20261031_181336_535898/sta_slow.log` sha256 `aa22b13ac94c84dc62b1c571a7b89368bf695be64e73ae39ab70cabb251f7d12`
- artifact `/home/panlong/project/eda-agent/runs/flow/torv_synth_20261031_181336_535898/sta_fast.log` sha256 `7a2f4dad0672cd4ae97b935ab514f223270cfaf80f634ba0dab3c2cc589ec4a1`
- image: ghcr.io/librelane/librelane@sha256:02d50f259d483979e126ef188a50f5b61e1d285b9d538312f280b087439b0302
- yosys: Yosys 0.62 (git sha1 7326bb7d6641500ecb285c291a54a662cb1e76cf, clang++ 21.1.2 -fPIC -O3)
- opensta: 2.7.0

**check_sdc {}** claim scope: OpenSTA check_setup on the synthesized netlist of 'torv_cpu' under SDC librelane/constraints.sdc (sha256 00c08cf20a426fef862822406e1ac4f9ac59338e8efa830866170b9c91390472): clocks defined, unconstrained endpoints, loops. Facts about the constraints only; it says nothing about whether the design meets them.
- artifact `/home/panlong/project/rv32m/librelane/constraints.sdc` sha256 `00c08cf20a426fef862822406e1ac4f9ac59338e8efa830866170b9c91390472`
- artifact `/home/panlong/project/eda-agent/runs/flow/torv_check_sdc_20261031_181352_619596/sta_check_check.log` sha256 `37fa400607b27d0c2c4ae889e51174a17d93b2f0068e5168f0c9d080f1541e26`
- image: ghcr.io/librelane/librelane@sha256:02d50f259d483979e126ef188a50f5b61e1d285b9d538312f280b087439b0302
- yosys: Yosys 0.62 (git sha1 7326bb7d6641500ecb285c291a54a662cb1e76cf, clang++ 21.1.2 -fPIC -O3)
- opensta: 2.7.0

## Timing estimates (E4, pre-PnR)

| tool | clock | setup WNS | TNS | hold WNS | worst corner | met | SDC sha256 | void |
|---|---|---|---|---|---|---|---|---|
| synth | 10.0 ns | 0.698 | 0.0 | 0.068 | slow | yes | `00c08cf20a426fef…` | no |

## Not run

- lint run: yes
- described but not run: none
- cases in the list not run (32): alu, imm, branch, compare_edges, shift_edges, jump, flush, x0, bypass, muldiv, muldiv_stall, hazard, loadstore, mem_offsets, sbus, bus_mix_dbus_sbus, bus_sbus_widths, bus_ibus_dbus_fill, hazard_branch_mem_stall, hazard_ibus_store_dup, hazard_sbus_replay_smoke, hazard_ibus_insn_dup, hazard_ibus_load_dup, hazard_sbus_rx_replay_smoke, sram_wbuf, perf_alu_chain, perf_branch_loop, perf_loadstore_loop, perf_mul_loop, uart_smoke, replay_ibus_store, replay_div_store

## Gate decisions

1. pass (best passing evidence E4)

## Final claims

## End-to-end TORV core check — report

### What I ran and what each result proves

1. **Lint (E0)** — `lint` returned **pass**: "Verilator parsed and elaborated the design under the project's flags. This proves nothing about functional behaviour or timing." Result: 0 errors, 43 warnings. Notable warning codes present: `MULTIDRIVEN`, `LATCH`, `WIDTHTRUNC`, plus `UNUSEDSIGNAL`, `PINCONNECTEMPTY`, etc. This is parse/elaboration only.

2. **Smoke regression (E2)** — `regress ["smoke"]` returned **pass**: 1 passed, 0 failed, 0 skipped. The `smoke` case (`tests/core/asm/smoke.S`) covers ALU deps, MUL, arithmetic right shift, JAL/JALR, tohost. Claim scope: "The listed directed cases ran to completion and each reported pass through the testbench's own self-check." It does not establish coverage or that the test would catch a bug.

3. **Synthesis + timing estimate (E4)** — `synth` returned **pass**, `timing_met: true`. Yosys mapped `torv_cpu` to `sg13g2_stdcell_typ_1p20V_25C.lib`; OpenSTA estimated timing under `librelane/constraints.sdc` (sha256 `00c08cf20a426fef862822406e1ac4f9ac59338e8efa830866170b9c91390472`) at a **10 ns** clock (`hclk`). Result: 14766 cells, area 236631, 0 latches. **Setup WNS +0.698 ns (slow corner), TNS 0.0 ns; hold WNS +0.068 ns (fast corner)**. Worst path: `_27317_` → `d_htrans[1]`, slack 0.698 ns, corner slow. This is an estimate **before placement and routing** (ideal clocks, no wire parasitics); it is not signoff.

4. **SDC endpoint check (E0)** — `check_sdc` returned **pass**: "1 clock(s), 0 unconstrained endpoint(s), 0 other check_setup warning(s)" on the synthesized `torv_cpu` netlist under the same SDC. This is facts about the constraints only; it does not say the design meets them (the E4 synth result is what shows the timing estimate).

### Does the timing estimate meet the project clock target?

Yes — according to the `synth` result (E4), the pre-placement-and-routing OpenSTA estimate under the project SDC meets the 10 ns clock target: setup WNS is **+0.698 ns at the slow corner** (TNS 0.0 ns) and hold WNS is **+0.068 ns at the fast corner**, so no setup or hold violations were reported. This is an estimate before PnR under the project's SDC (sha256 `00c08cf20a426fef862822406e1ac4f9ac59338e8efa830866170b9c91390472`), not a placed/routed timing result.

###

## Residual risk

- **Lint (E0)** proves only parse/elaboration, not function or timing. The flagged `MULTIDRIVEN`, `LATCH`, and `WIDTHTRUNC` warnings were not investigated in this run.
- **Regression (E2)** proves only that the `smoke` case passed via the testbench's own self-check; no coverage or mutation score exists, so it does not demonstrate bug-catching adequacy.
- **Synth (E4)** is pre-PnR: ideal clock, no wire parasitics; post-route slack is usually worse. No logic-equivalence check against the RTL and no gate-level simulation were run.
- **check_sdc (E0)** confirms 0 unconstrained endpoints and 1 defined clock but does not judge the SDC values (I/O budgets, uncertainty), and inputs without `set_input_delay` are not listed as endpoints.

From the tools:
- inputs without set_input_delay are not endpoints and are not listed here
- lint says nothing about function; run regress for E2
- pass/fail is reported by the test program itself
- pre-PnR estimate: no wire parasitics, ideal clock tree; post-route slack is usually worse
- synthesis is not checked for equivalence with the RTL and no gate-level simulation was run
- test adequacy unknown: no coverage or mutation score yet
- the SDC's values (I/O budgets, uncertainty) are not judged, only whether they exist
- timing is only as meaningful as the SDC: I/O delays and exceptions there are assumptions

## Review: rule-based (no model)

- **Saw:** the author's tool results and the rtl_rules tool; no model
- **Verdict:** reject
- **Re-ran:** rtl_rules {} -> fail E0

Checklist (written before the findings):

- every tool the author ran finished (no error or timeout)
- lint has no errors; regress has no failing case
- rtl_rules: no blocking assignment in a clocked block, no non-blocking one in combinational logic
- rtl_rules: no width truncation, latch or multiple driver; reset tests match edge and name
- synth: no latches, timing met under the SDC on every corner
- check_sdc: every endpoint constrained, the clock on the configured port
- all timing results of the run were measured under one SDC (same sha256)

Findings:

- [high] multi_driven x1: signal driven from more than one block: Bits [0:0] of signal 'torv_soc.apb_presetn' have multiple combinational drivers (at rtl/torv_soc.sv:179)
- [medium] width_truncation x1: value truncated to a narrower width: Operator ASSIGN expects 8 bits on the Assign RHS, but Assign RHS's SHIFTR generates 32 bits. (at rtl/torv_core.sv:602)
- [medium] latch x1: latch inferred: Latch inferred for signal 'torv_soc.u_apb_uart.fifo_tx_data' (not all control paths of combinational always assign a value) (at ip/third_party/apb_uart_sv/apb_uart.sv:195)

## Review: I2, deepseek / deepseek-v4-pro

- **Saw:** a fresh conversation with the task and the tool results of the run, none of the author's text or conclusions; may re-run the project's tools
- **Verdict:** accept_with_findings
- **Re-ran:** lint {"max_diagnostics": 50} -> pass E0; rtl_rules {"max_diagnostics": 30} -> fail E0; regress {"cases": ["smoke"]} -> pass E2; synth {} -> pass E4; check_sdc {} -> pass E0

Checklist (written before the findings):

- Lint must have been run on top `torv_soc` with the project's flags and its status/error count recorded (E0).
- The smoke regression case must have been run and reported pass/fail through the testbench self-check (E2).
- Synthesis must have been run on top `torv_cpu` to standard cells and reported cells/area/latches (E4).
- Timing estimate must be reported for the project clock (hclk, 10 ns) under `librelane/constraints.sdc`, with WNS/TNS/hold WNS and worst corner named (E4).
- The timing conclusion must cite the E4 synthesis/STA result and state that it is a pre-PnR estimate, not signoff.
- `check_sdc` must have been run on the synthesized netlist under the same SDC and must report 0 unconstrained endpoints and no check_setup warnings (E0).
- The same SDC (same sha256) must be used for the timing estimate and the SDC check; any mismatch invalidates the comparison.
- The report must distinguish evidence levels: lint clean is not functional correctness, smoke pass is not full correctness, and SDC check does not prove timing closure.
- The report must state whether the timing estimate meets the 10 ns project clock target, based on the slow-corner setup WNS.
- Lint warnings that matter (LATCH, MULTIDRIVEN, WIDTHTRUNC, SYNCASYNCNET, PINCONNECTEMPTY) must not be glossed over, even if the lint status is pass.
- No conclusion may overreach the claim_scope of each tool result (e.g., no claim of signoff timing, no claim the core is correct end-to-end beyond the smoke case).

Findings:

- [info] All requested checks ran with pass status in their tool sense: lint E0 (0 errors, 43 warnings), smoke regression E2 (1/1 passed), synthesis/STA E4 (timing_met true), check_sdc E0 (0 unconstrained endpoints) — evidence: rerun tool results.
- [medium] Lint is not clean: 43 warnings include a LATCH at `ip/third_party/apb_uart_sv/apb_uart.sv:195`, MULTIDRIVEN on `torv_soc.apb_presetn` at `rtl/torv_soc.sv:179`, WIDTHTRUNC at `rtl/torv_core.sv:602`, and SYNCASYNCNET on `hresetn` at `rtl/torv_soc.sv:33` — evidence: lint rerun with full diagnostics.
- [medium] Independent RTL rule check fails on a MULTI_DRIVEN error for `torv_soc.apb_presetn` and also flags the UART latch and core width truncation — evidence: `rtl_rules` status fail.
- [low] The E4 synthesis/STA and SDC check cover top `torv_cpu` only, while the lint top is `torv_soc`; the UART latch and `apb_presetn` multiple-driver findings are outside the synthesis/SDC evidence — evidence: synth/check_sdc top `torv_cpu`, lint top `torv_soc`.
- [info] `check_sdc` reports 0 unconstrained endpoints, but inputs without `set_input_delay` are not endpoints and are not listed — evidence: check_sdc residual_risk.
- [info] Timing estimate meets the 10 ns project clock under `librelane/constraints.sdc` (slow-corner setup WNS +0.698 ns, hold WNS +0.068 ns), and is explicitly a pre-PnR estimate, not signoff — evidence: synth E4.

## Review: I3, deepseek / deepseek-v4-flash

- **Saw:** as I2, on a different model than the author
- **Verdict:** accept_with_findings
- **Note:** I3 here is the author's provider with a different model (deepseek-v4-pro -> deepseek-v4-flash); a different vendor would be more independent (shared training data and blind spots).
- **Re-ran:** lint {"max_diagnostics": 50} -> pass E0; rtl_rules {"max_diagnostics": 40} -> fail E0; regress {"cases": ["smoke", "branch", "jump", "loadstore", "mem_offsets", "sbus", "muldiv", "hazard", "flush", "x0"]} -> pass E2; synth {} -> pass E4; check_sdc {} -> pass E0

Checklist (written before the findings):

- Lint of the core ran with 0 errors and every correctness-relevant warning code (LATCH, MULTIDRIVEN, WIDTHTRUNC, SYNCASYNCNET) was individually triaged, not just counted.
- The truncated lint output (20 of 43 diagnostics shown) was completed or re-run so the LATCH/MULTIDRIVEN/SYNCASYNCNET sites are actually identified.
- E0 lint is reported only as "parses/elaborates", never as evidence of function or timing.
- The requested smoke case was executed and its own pass criterion (tohost PASS in the log) is confirmed, E2 scope stated ("these directed cases pass", not "core is correct").
- Coverage gap of smoke-only regression (ALU/branch/jump, load-store, SBUS/DBUS, muldiv, hazard/flush cases unrun) is stated rather than implied verified.
- Synthesis E4 ran at the project clock (10 ns / 100 MHz) with the project SDC, and the SDC path + sha256 are cited for every timing statement.
- The synthesized top matches the linted top (torv_soc vs torv_cpu discrepancy must be explained, and SoC-level logic either timed or declared out of scope).
- Setup and hold WNS/TNS are reported for every corner, worst corner named, and the "meets target" conclusion is tied to that specific result, pre-PnR.
- Synthesized latch count (0) is reconciled with lint's LATCH warning rather than both being reported unexamined.
- SDC check confirms a clock on hclk at 10 ns, 0 unconstrained endpoints, 0 loops, and the SDC sha256 matches the one used in synth.
- SDC completeness beyond check_setup is addressed: input/output delays, any generated clocks, exceptions, and the stated caveat that unconstrained inputs are not listed as endpoints.
- The reported worst path (endpoint d_htrans[1]) is checked to be genuinely constrained by an output delay; otherwise "timing met" is narrower than claimed.
- No claim anywhere exceeds the claim_scope/evidence level of the result it rests on, and pre-PnR/no-parasitics/no-gate-sim residual risks are carried into the conclusion.

Findings:

- [high] A real rule error was never surfaced: `rtl_rules` (E0) **fails** with `MULTI_DRIVEN` on `torv_soc.apb_presetn` bits[0:0], "driven from more than one block" — the author reported only `lint` status=pass and did not triage the matching `MULTIDRIVEN` warning in the lint output. Evidence: rtl_rules status=fail, rtl/torv_soc.sv:179; lint diagnostics code=MULTIDRIVEN, torv_soc.sv:179.
- [high] The synthesized/STA'd top is `torv_cpu`, but lint was run on `torv_soc`; the SoC-level logic (AHB/APB bridge, `apb_uart_sv`, `torv_ahb_sram`, AHB dport) is never synthesized, timed, or covered by the SDC — so "timing meets the project clock" says nothing about the SoC half of the design. Evidence: synth/check_sdc claim_scope "top 'torv_cpu'"; lint summary "top=torv_soc".
- [medium] Latch warning unexamined and not reconcilable with the E4 report: lint/rtl_rules infer a latch on `torv_soc.u_apb_uart.fifo_tx_data`, while synth reports "0 latch(es)" — because synth excludes the UART. Reporting both without noting they cover different tops is misleading. Evidence: lint LATCH apb_uart.sv:195; rtl_rules LATCH apb_uart.sv:195; synth metrics latches=0, top torv_cpu.
- [medium] `WIDTHTRUNC` on `rtl/torv_core.sv:602` ("ASSIGN expects 8 bits, RHS SHIFTR generates 32 bits") is in the core RTL and was neither investigated nor declared benign. Evidence: lint warning torv_core.sv:602; rtl_rules WIDTH_TRUNCATION torv_core.sv:602.
- [medium] Reset handling risk unaddressed: `hresetn` is flopped as both synchronous and asynchronous (`SYNCASYNCNET`), a common source of simulation/synthesis divergence. Evidence: lint warning, rtl/torv_soc.sv:33.
- [medium] The "SDC constrains every endpoint" claim is narrower than it sounds: `check_setup` reports 0 unconstrained endpoints, but the tool itself states inputs without `set_input_delay` are not endpoints and are not listed, and SDC values (I/O budgets, uncertainty) are not judged. Evidence: check_sdc claim_scope/residual_risk.
- [medium] The reported critical path ends at an output port (`d_htrans[1]`, slow corner, slack +0.698, arrival 7.002); no evidence was shown that its required time comes from a real `set_output_delay` in the SDC rather than a default, so the "met" result hinges on an unverified I/O assumption. Evidence: synth worst_path; check_sdc diagnostics list empty (no I/O delay content shown).
- [medium] Evidence base for "core works" is one directed case: `smoke` covers only ALU deps/MUL/shift/JAL/tohost — no load-store, branch, hazard, or bus traffic. My wider run of 10 cases (smoke, branch, jump, flush, x0, muldiv, hazard, loadstore, mem_offsets, sbus) also passes, but 24 further listed cases (bus mix, sram_wbuf, uart, replay/dup hazards, perf) remain unrun and no coverage/mutation score exists. Evidence: describe_case smoke; regress 10 passed 0 failed; regress residual_risk "test adequacy unknown".
- [low] Timing conclusion must remain labelled pre-PnR, single-SDC, no parasitics, no gate-level sim / no RTL-vs-netlist equivalence — the E4 tool states post-route slack is usually worse, so +0.698 ns at slow is not a signoff margin. Evidence: synth residual_risk and claim_scope.
- [low] Numbers reproduce exactly on re-run (14766 cells, area 236630.87, setup WNS +0.698 slow / hold +0.068 fast, same SDC sha256 `00c08cf2…`), so the E4 figures themselves are not in doubt — only what they cover. Evidence: my synth run vs the author's, identical metrics and netlist.v sha256.
- [info] Pure-noise warnings (EOFNEWLINE, GENUNNAMED, DECLFILENAME, 30 UNUSEDSIGNAL) are correctly non-blocking; the concerning ones are the four listed above, not the count of 43.

## Review: I1, deepseek / deepseek-v4-pro

- **Saw:** a fresh conversation with the author's final report only
- **Verdict:** accept_with_findings

Checklist (written before the findings):

- Lint must be E0 pass with 0 errors; the 43 warnings must be categorized, especially `LATCH`, `MULTIDRIVEN`, and `WIDTHTRUNC`, and not described as clean or harmless without analysis.
- Smoke regression must be E2 pass with the actual `smoke` case executed, 1 passed, 0 failed, 0 skipped, and the testbench self-check must drive pass/fail.
- Any functional claim must not exceed what one smoke case supports; no coverage, mutation score, or bug-catching adequacy may be implied.
- If the task is an end-to-end check involving more than smoke, the required regression set must be run or the report must explicitly say only smoke was run.
- Synthesis must be E4 pass for the intended top `torv_cpu` targeting `sg13g2_stdcell_typ_1p20V_25C.lib`.
- Timing estimate must use the current project SDC, sha256 `00c08cf20a426fef862822406e1ac4f9ac59338e8efa830866170b9c91390472`, at the specified 10 ns `hclk` clock.
- The same SDC hash must be used for synthesis/timing and for the SDC endpoint check.
- Timing must report setup WNS/TNS at slow corner and hold WNS at fast corner, with no setup or hold violations.
- Any timing conclusion must be explicitly scoped as pre-placement/routing estimate only: ideal clocks, no wire parasitics, not signoff, not post-route.
- SDC endpoint check must be E0 run on the synthesized netlist and report 1 clock and 0 unconstrained endpoints.
- Zero unconstrained endpoints must not be used to claim the SDC I/O budgets, input/output delays, or uncertainty values are correct.
- LEC, gate-level simulation, or formal equivalence must be listed as not run; synthesis pass alone must not imply functional equivalence to RTL.
- Claim scopes must match evidence levels: E0 is only parse/elaboration or constraint facts, E2 is only the stated passing cases, E4 is only an estimate under one SDC before placement and routing.

Findings:

- [medium] Only the single `smoke` regression case was run; the report title “End-to-end TORV core check” overstates the functional evidence, which supports only the listed directed case, not the core as a whole. No broader regression, coverage, or mutation result exists. — evidence: report §2 and residual risk.
- [medium] `lint` reports `LATCH`, `MULTIDRIVEN`, and `WIDTHTRUNC` warnings and leaves them unresolved; synthesis reports “0 latches” but that is not reconciled with the `LATCH` warning, and no LEC or gate-level simulation was run. — evidence: report §1, §3, residual risk.
- [low] `check_sdc` “0 unconstrained endpoints” does not validate the SDC I/O budgets, input/output delays, or uncertainty; the timing result is only an internal `hclk` estimate and should not be read as full chip timing. The report does disclose this in residual risk. — evidence: report §4 and residual risk.
- [info] Hold TNS is not reported; only hold WNS is given. Hold WNS > 0 implies no reported fast-corner hold violation, but this remains a pre-PnR estimate. — evidence: report §3.

## Human approval

- **Step:** final report  ·  **Status:** approved
- requested: 2026-10-31T18:15:49+01:00 (the evidence gate passed; a person accepts the result)
- decided by **Claude (demo of the mechanism, not a human decision)** at 2026-10-31T18:22:03+01:00
- receipt content sha256 at decision: `f641dee858b6e36cfa44b279b6a3ecb2527c4c64b27885e153209c62e06f7cf3`

Trace: `runs/m2_torv_e2e.jsonl`
