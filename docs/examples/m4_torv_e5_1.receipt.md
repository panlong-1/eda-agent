# Run receipt 20261031_233226

- **Task:** Report on the fresh LibreLane RTL-to-GDS run of TORV made by eda-agent. Call flow_report with run="/home/panlong/project/eda-agent-m4/runs/pnr/torv_m4_e5_1/src/librelane/runs/m4_e5_1" and baseline="full_p10_fwd" (one call is enough). Say plainly: whether physical signoff passed and which rule failed, the post-route setup and hold slack per corner, DRC/LVS/antenna, which experience table the run is in, whether its lineage is complete, how it compares with full_p10_fwd, and what this run does not cover (gate-level simulation, activity-based power, RC corners).
- **Project:** torv  ·  **Model:** deepseek / deepseek-v4-pro  ·  **Prompt:** system.v2
- **Stop:** end  ·  **Status:** awaiting_approval (agent: done)  ·  **Gate:** passed (1 rewrite(s))
- **Cost:** 3 turns, 1 tool calls (0 errors), 46411 in / 7913 out tokens, 58.39 s

## Checks run

| tool | args | status | evidence | summary |
|---|---|---|---|---|
| flow_report | `{"run": "/home/panlong/project/eda-agent-m4/runs/pnr/torv_m4_e5_1/src/librelane/runs/m4_e5_1", "baseline": "full_p10_fwd"}` | fail | E4 | m4_e5_1 (torv_cpu): signoff FAIL [E5-partial]; setup WNS -0.725 ns (nom_slow_1p08V_125C), hold WNS +0.101 ns; DRC Magic 0, KLayout 0; LVS errors 0; antenna nets 0; die 508267 um2, util 0.557351, 18824 cells |

**flow_report {"run": "/home/panlong/project/eda-agent-m4/runs/pnr/torv_m4_e5_1/src/librelane/runs/m4_e5_1", "baseline": "full_p10_fwd"}** claim scope: librelane 3.0.16 run 'm4_e5_1' of top 'torv_cpu' on ihp-sg13g2 (PDK version c4b8b4e5e7a0): post-route STA with a propagated clock and extracted parasitics on corner(s) nom_fast_1p32V_m40C, nom_slow_1p08V_125C, nom_typ_1p20V_25C at a 10 ns clock under SDC /home/panlong/project/eda-agent-m4/runs/pnr/torv_m4_e5_1/src/librelane/constraints.sdc (sha256 00c08cf20a426fef862822406e1ac4f9ac59338e8efa830866170b9c91390472, recorded); DRC: Magic (ihp-sg13g2.tech), KLayout (ihp-sg13g2.drc); LVS: Netgen (ihp-sg13g2_setup.tcl); antenna check: OpenROAD, on the final layout. Signoff level E5-partial: the physical-signoff parts of E5. It does not show gate-level simulation of the routed netlist (with post-route SDF and timing checks), power from real switching activity (power here is vectorless), timing at RC-extraction corners other than those named, IR drop or electromigration signoff, or anything about function.
- artifact `/home/panlong/project/eda-agent-m4/runs/pnr/torv_m4_e5_1/src/librelane/runs/m4_e5_1/56-openroad-stapostpnr/nom_fast_1p32V_m40C/max.rpt` sha256 `99bbf6edd10eff02934513a578b702bf5bd43329d70c0d816d312b0250f74ebc`
- artifact `/home/panlong/project/eda-agent-m4/runs/pnr/torv_m4_e5_1/src/librelane/runs/m4_e5_1/56-openroad-stapostpnr/nom_slow_1p08V_125C/max.rpt` sha256 `3daaaf1995f59c68383e2bb49a5995f95c5940c51c229fc2c750a5b67d92c389`
- artifact `/home/panlong/project/eda-agent-m4/runs/pnr/torv_m4_e5_1/src/librelane/runs/m4_e5_1/56-openroad-stapostpnr/nom_typ_1p20V_25C/max.rpt` sha256 `2b5c7ffcccc949cec35e9b9a34f0912148cdf44ad77093bba1808e523e80d913`
- artifact `/home/panlong/project/eda-agent-m4/runs/pnr/torv_m4_e5_1/src/librelane/runs/m4_e5_1/56-openroad-stapostpnr/summary.rpt` sha256 `3b7b83261fc313ffb34b03196b5d8a6fdf2790ace7af60123973040d92b848f5`
- artifact `/home/panlong/project/eda-agent-m4/runs/pnr/torv_m4_e5_1/src/librelane/runs/m4_e5_1/65-magic-drc/reports/drc.magic.rpt` sha256 `17492a50b7ec2ceffb318f6b913ac31bdaa118300e944a5198f6e8483f8c5087`
- artifact `/home/panlong/project/eda-agent-m4/runs/pnr/torv_m4_e5_1/src/librelane/runs/m4_e5_1/66-klayout-drc/reports/drc.klayout.json` sha256 `9eabe90f5b5a2b5837d414392c75dd91d54ed2a4bcb5d72081b03d0c6d5c2a2c`
- artifact `/home/panlong/project/eda-agent-m4/runs/pnr/torv_m4_e5_1/src/librelane/runs/m4_e5_1/71-netgen-lvs/reports/lvs.netgen.rpt` sha256 `0f06b04f2ce835c4112f20af865b11778d3f7d87b7c23cf06cb444d0472d01b3`
- artifact `/home/panlong/project/eda-agent-m4/runs/pnr/torv_m4_e5_1/src/librelane/runs/m4_e5_1/47-openroad-checkantennas-1/reports/antenna.rpt` sha256 `88ceef0b63031420e468a911f7c57fd68d4c409b45a681c14667323216ceb4fb`
- artifact `/home/panlong/project/eda-agent-m4/runs/pnr/torv_m4_e5_1/src/librelane/runs/m4_e5_1/77-misc-reportmanufacturability/manufacturability.rpt` sha256 `038bd8e28c459644f9838180d7a82ccd04cac768605e2348af10b43fa259cf29`
- artifact `/home/panlong/project/eda-agent-m4/runs/pnr/torv_m4_e5_1/src/librelane/constraints.sdc` sha256 `00c08cf20a426fef862822406e1ac4f9ac59338e8efa830866170b9c91390472`
- librelane: 3.0.16
- pdk: ihp-sg13g2@c4b8b4e5e7a05f375cca3815d51b3a37721fbf5c
- image: ghcr.io/librelane/librelane@sha256:02d50f259d483979e126ef188a50f5b61e1d285b9d538312f280b087439b0302

## Timing estimates (E4, pre-PnR)

| tool | clock | setup WNS | TNS | hold WNS | worst corner | met | SDC sha256 | void |
|---|---|---|---|---|---|---|---|---|
| flow_report | 10 ns | -0.7254481665165831 | -7.845814886514664 | 0.10087869713992108 | nom_slow_1p08V_125C | no | `00c08cf20a426fef…` | no |

## Design snapshot

| tool | snapshot | design | state | note |
|---|---|---|---|---|
| flow_report | `d9780af10936f76c` | `094be7c30938e788` | current | files of eda-agent's copy of the design |

Checks used together: one design, no conflict

## Lineage: flow_report run m4_e5_1 (librelane)

| stage | steps | inputs (sha256, from) | outputs (sha256) | ok |
|---|---|---|---|---|
| synthesis | 06-yosys-synthesis .. 06-yosys-synthesis | - | nl 495c60b3ab | yes |
| place_and_route | 13-openroad-floorplan .. 53-openroad-fillinsertion | nl 495c60b3ab ← synthesis | nl 556fa6bfc1; odb 9c3b7294fd; def fb5fa1d288; sdc 7fa9154823; pnl ad41e6145f; vh a998fd44fc | yes |
| rc_extraction | 55-openroad-rcx .. 55-openroad-rcx | odb 9c3b7294fd ← place_and_route; def fb5fa1d288 ← place_and_route | spef:nom_* e8f5637b32 | yes |
| post_route_sta | 56-openroad-stapostpnr .. 56-openroad-stapostpnr | odb 9c3b7294fd ← place_and_route; sdc 7fa9154823 ← place_and_route; pnl ad41e6145f ← place_and_route; spef:nom_* e8f5637b32 ← rc_extraction | sdf:nom_fast_1p32V_m40C 1604cf6e5a; sdf:nom_slow_1p08V_125C c3659ea384; sdf:nom_typ_1p20V_25C 4eb288aac6; lib:nom_fast_1p32V_m40C e602724be6; lib:nom_slow_1p08V_125C 63db7e91e1; lib:nom_typ_1p20V_25C 590cde1fbf | yes |
| gds_streamout | 58-magic-streamout .. 59-klayout-streamout | odb 9c3b7294fd ← place_and_route; def fb5fa1d288 ← place_and_route | mag_gds 9485bd2e67; mag eae052a0d3; gds 9485bd2e67; klayout_gds 02c4e3282a | yes |
| drc | 65-magic-drc .. 66-klayout-drc | gds 9485bd2e67 ← gds_streamout; klayout_gds 02c4e3282a ← gds_streamout | - | yes |
| lvs_extraction | 69-magic-spiceextraction .. 69-magic-spiceextraction | gds 9485bd2e67 ← gds_streamout | spice 99ec862e96 | yes |
| lvs | 71-netgen-lvs .. 71-netgen-lvs | pnl ad41e6145f ← place_and_route; spice 99ec862e96 ← lvs_extraction | - | yes |
- final/ copies matching their step outputs: 20 of 20
- not part of this flow (needed for full E5): gate_level_sim (routed netlist + post-route SDF), switching activity (VCD/SAIF) from that simulation, power from that activity

## Not run

- lint run: no
- described but not run: none
- cases in the list not run (33): smoke, alu, imm, branch, compare_edges, shift_edges, jump, flush, x0, bypass, muldiv, muldiv_stall, hazard, loadstore, mem_offsets, sbus, bus_mix_dbus_sbus, bus_sbus_widths, bus_ibus_dbus_fill, hazard_branch_mem_stall, hazard_ibus_store_dup, hazard_sbus_replay_smoke, hazard_ibus_insn_dup, hazard_ibus_load_dup, hazard_sbus_rx_replay_smoke, sram_wbuf, perf_alu_chain, perf_branch_loop, perf_loadstore_loop, perf_mul_loop, uart_smoke, replay_ibus_store, replay_div_store

## Gate decisions

1. reject (best passing evidence none)
   - cites E5 but no tool result of this run has evidence_level E5: 'I ran `flow_report` on run `/home/panlong/project/eda-agent-m4/runs/pnr/torv_m4_e5_1/src/librelane/runs/m4_e5_1` against baseline `full_p10_fwd`. Tool status: **fail**, evidence_level **E4**, signoff level **E5-partial** (the physical-signoff parts of E5 only).'
   - cites E5 but no tool result of this run has evidence_level E5: 'I ran `flow_report` on run `/home/panlong/project/eda-agent-m4/runs/pnr/torv_m4_e5_1/src/librelane/runs/m4_e5_1` against baseline `full_p10_fwd`. Tool status: **fail**, evidence_level **E4**, signoff level **E5-partial** (the physical-signoff parts of E5 only).'
   - cites E5 but no tool result of this run has evidence_level E5: '### Post-route setup and hold slack per corner (flow_report, E4/E5-partial)'
   - cites slack 0 but no valid timing result of this run reports it as that: '- **Setup** (rule: post-route setup worst slack ≥ 0 on every signoff corner): **FAIL** on `nom_slow_1p08V_125C` — WNS **-0.725448 ns**, TNS **-7.845815 ns**, **26 violating endpoints**'
   - cites WNS -0.079642 but no valid timing result of this run reports it as that: 'On the same corner the register-to-register setup WNS is also negative (-0.079642 ns), so it is not purely an I/O-delay artifact'
2. pass (best passing evidence none)

## Final claims

## Report: LibreLane RTL-to-GDS run `m4_e5_1` (TORV)

I ran `flow_report` (status: **fail**, evidence_level **E4**) on run `/home/panlong/project/eda-agent-m4/runs/pnr/torv_m4_e5_1/src/librelane/runs/m4_e5_1` against baseline `full_p10_fwd`. Per that result's claim_scope, it covers post-route STA with a propagated clock and extracted parasitics on corners `nom_fast_1p32V_m40C`, `nom_slow_1p08V_125C`, `nom_typ_1p20V_25C` at a 10 ns clock under SDC `/home/panlong/project/eda-agent-m4/runs/pnr/torv_m4_e5_1/src/librelane/constraints.sdc` (sha256 `00c08cf20a426fef862822406e1ac4f9ac59338e8efa830866170b9c91390472`), DRC by Magic and KLayout, LVS by Netgen, and antenna check by OpenROAD on the final layout. It does not show gate-level simulation of the routed netlist or power from real switching activity.

### Physical signoff
**Physical signoff did NOT pass** — `signoff_passed: false`. Exactly one signoff rule failed:

- **Setup** (the rule requires the post-route setup worst slack to be non-negative on every signoff corner): **FAIL** on `nom_slow_1p08V_125C` — WNS **-0.725448 ns**, TNS **-7.845815 ns**, **26 violating endpoints**. Worst path `_29567_/Q` → `i_haddr[30]` (reg-out group).

All other signoff rules passed: **hold** on every corner, **clock propagated** (all paths timed with a propagated clock, no ideal clocks left), **DRC**, **LVS**, **antenna**.

### Post-route setup and hold slack per corner (flow_report, evidence_level E4)
| Corner | Setup WNS | Setup viol. | Hold WNS | Hold viol. |
|---|---|---|---|---|
| nom_fast_1p32V_m40C | +4.120185 ns | 0 | +0.100879 ns | 0 |
| nom_slow_1p08V_125C | **-0.725448 ns** | 26 | +0.215072 ns | 0 |
| nom_typ_1p20V_25C | +2.353727 ns | 0 | +0.205317 ns | 0 |

Clock: `hclk`, 10 ns period. All three corners have 0 ideal clocks (832/149/213 propagated paths respectively).

### DRC / LVS / antenna
- **DRC**: Magic **0** errors, KLayout **0** errors (both counts cross-checked and agreeing) → clean.
- **LVS**: Netgen **0** errors; report final "Circuits match uniquely." → clean.
- **Antenna**: OpenROAD **0** violating nets, **0** violating pins → clean.

### Experience table and lineage
- Experience table: **eda_agent** (provenance: made by eda-agent `flow_run` for this design).
- Lineage: **complete: true** — synthesis, place_and_route, rc_extraction, post_route_sta, gds_streamout, drc, lvs_extraction, and lvs all `ok`; `breaks` and `stages_absent` are empty; every final artifact hash matches its source stage. (The physical-flow lineage is complete, but see

## Residual risk

for what the run's own report lists as absent.)

### Comparison with `full_p10_fwd`
`flow_report` flags the two runs as **not comparable**, for three reasons it states:
1. different top: baseline `rv32im_ahb_top`, run `torv_cpu`;
2. the SDC of at least one run is not known for sure (timing deltas may mix constraints);
3. not shown to be the same design (snapshot `current` vs `older`).

The baseline sits in the **imported** table. The reported numeric deltas are all **0.0** (die/core area, utilization, stdcell count/area, sequential count, power, wirelength, vias, DRC counts, and all three corners' WNS/TNS), and `better`/`worse` are both empty — but this is reported under the not-comparable flag, so no conclusion about equivalence or regression should be drawn from those numbers.

### Residual risk
- **Gate-level simulation**: none of the routed netlist with post-route SDF and timing checks; behaviour after place-and-route is not checked.
- **Activity-based power**: power is **vectorless** (OpenSTA default switching activity, default corner), not from a simulation VCD/SAIF with annotation coverage.
- **RC corners**: only **nominal** RC extraction (`nom_*` corners) was timed; min/max RC-extraction corners were not analysed.
- **Equivalence**: the netlist was not checked for equivalence with the RTL (the flow skipped that step).
- **IR drop / EM**: IR drop is the flow's static estimate without real supply-pad locations; electromigration was not checked.
- **Timing depends on SDC assumptions**: I/O delays and exceptions are assumptions, not verified behaviour.
- Non-signoff warning: **141 max fanout violations** reported (not a signoff rule in this run).

In short: this run is manufacturable-clean (DRC/LVS/antenna pass) and hold passes everywhere, but **physical signoff fails on setup in the slow corner** and therefore the run is not signoff-clean.

From the tools:
- IR drop is the flow's static estimate without real supply-pad locations; electromigration was not checked
- no gate-level simulation of the routed netlist: E5 needs it with the post-route SDF at the slow and fast corners and timing checks on; behaviour after PnR is not checked
- only nominal RC extraction was timed (nom_* corners); min/max RC corners were not analysed
- power is vectorless (OpenSTA default switching activity, default corner), not from a simulation VCD with stated annotation coverage and activity/Liberty corners
- the netlist was not checked for equivalence with the RTL (the flow skipped that step)
- timing is only as meaningful as the SDC: its I/O delays and exceptions are assumptions

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
- flow_report / flow_run: every signoff rule passes; the run is of the current top, RTL and SDC

Findings:

- [high] flow_report m4_e5_1: signoff rule setup fails: nom_slow_1p08V_125C: setup WNS -0.725 ns, 26 violating endpoint(s)
- [high] multi_driven x1: signal driven from more than one block: Bits [0:0] of signal 'torv_soc.apb_presetn' have multiple combinational drivers (at rtl/torv_soc.sv:179)
- [medium] width_truncation x1: value truncated to a narrower width: Operator ASSIGN expects 8 bits on the Assign RHS, but Assign RHS's SHIFTR generates 32 bits. (at rtl/torv_core.sv:602)
- [medium] latch x1: latch inferred: Latch inferred for signal 'torv_soc.u_apb_uart.fifo_tx_data' (not all control paths of combinational always assign a value) (at ip/third_party/apb_uart_sv/apb_uart.sv:195)

## Human approval

- **Step:** final report  ·  **Status:** pending
- requested: 2026-10-31T23:33:26+01:00 (the evidence gate passed; a person accepts the result)
- decide with `eda-agent approve 20261031_233226` or `eda-agent reject 20261031_233226 --reason "..."`

Trace: `runs/20261031_233226.jsonl`
