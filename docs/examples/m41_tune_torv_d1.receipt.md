# Review summary · tune_torv_d1

> Written by plain code from the receipt data (no model). The tables below are the record; the full receipt is at the end.

**Fatal 0 · Error 0 · Warning 5** — 2 run(s), 1 proposal(s) rejected before running; verdicts: improved, improved; gate passed.

- **Task:** Tune the back-end flow: post-route setup slack at the slow corner (nom_slow_1p08V_125C) at the team's 10 ns clock (budget 2 run(s))
- **What you decide:** Approve or reject step 'flow config patch and lesson updates': accept the agent's final report below as the result of this task? (requested because: a person decides whether the patch goes into the design's flow config and whether the verified/refuted lessons stand (eda-agent lessons void otherwise))
- **Status:** awaiting approval · torv · deepseek / deepseek-v4-pro · prompt tune-propose.v1
- **To approve:** Warnings only: a plain approve is enough

```bash
eda-agent approve tune_torv_d1
eda-agent reject tune_torv_d1 --reason "..."
eda-agent pending tune_torv_d1 --note "..."
```

Or click the decision in the local review page: `eda-agent review-ui`.

## Look at these before approving

- no Fatal or Error items

<details><summary>5 warning(s)</summary>

- 🟨 Warning: Lesson lesson-torv-016 was verified by one run (tune_torv_d1_1); a person may void the entry (lesson-torv-017) — *lessons*
- 🟨 Warning: Attempt 2 was rejected before running: RUN_POST_GRT_DESIGN_REPAIR: not on the tunable list (core/knobs.py); a person adds a knob there first — *tune*
- 🟨 Warning: Lesson lesson-torv-019 was verified by one run (tune_torv_d1_2); a person may void the entry (lesson-torv-020) — *lessons*
- 🟨 Warning: Single run per setting: the effect cannot be told apart from run-to-run noise — *tune*
- 🟨 Warning: The config patch is NOT applied to the design: a person applies it after approval: generated/tune_torv_d1/config.patch — *tune*

</details>

## Checks

| check | result | evidence | time (s) | tool summary |
|---|---|---|---|---|
| flow_run `{"tag": "tune_torv_d1_1"}` | ❌ FAIL | E4 synthesis estimate | — | tune_torv_d1_1 (torv_cpu): signoff FAIL [E5-partial]; setup WNS -0.541 ns (nom_slow_1p08V_125C), hold WNS +0.103 ns; DRC Magic 0, KLayout 0; LVS errors 0; antenna nets 0; die 508267 um2, util 0.544666, 18200 cells |
| flow_run `{"tag": "tune_torv_d1_2"}` | ❌ FAIL | E4 synthesis estimate | — | tune_torv_d1_2 (torv_cpu): signoff FAIL [E5-partial]; setup WNS -0.377 ns (nom_slow_1p08V_125C), hold WNS +0.104 ns; DRC Magic 0, KLayout 0; LVS errors 0; antenna nets 0; die 508267 um2, util 0.545208, 18209 cells |

## What the evidence allows

- No passing evidence: nothing may be concluded.

## Not checked

- single run per setting: a knob's effect cannot be told apart from run-to-run noise (placement and routing react chaotically to any change); an identical config was reproduced exactly once (m4_e5_1 = full_p10_fwd), which says nothing about the noise a changed knob brings
- the verdict reads one goal metric (slow-corner setup WNS) and the signoff counts; area, power and runtime are listed but not judged
- post-route STA, DRC, LVS and antenna only (E5-partial): no gate-level simulation of the routed netlist, vectorless power
- the patch is for this design, this PDK, this clock period and this flow version; it is not a general rule

## Evidence gate

- passed; the report is written by code from the flow results

## Reviewers

- no independent review: the verdicts are computed by code from the flow results

## Approval

- pending

## Data

### Proposals

| # | source | patch | tests lesson | knob policy | other checks | decision |
|---|---|---|---|---|---|---|
| 1 | model | {"PL_TIMING_DRIVEN": true} | lesson-torv-016 | ok | ok | ran |
| 2 | model | {"RUN_POST_GRT_DESIGN_REPAIR": true} | — | ERROR RUN_POST_GRT_DESIGN_REPAIR: not on the tunable list (core/knobs.py); a person adds a knob there first | ok | rejected |
| 3 | model | {"GRT_RESIZER_SETUP_SLACK_MARGIN": 0.1} | lesson-torv-019 | ok | ok | ran |

### Runs against the best run (ns; negative = violation)

| run | patch (all knobs) | slow setup WNS | slow TNS | slow viol. | hold WNS | DRC | LVS | antenna | die um2 | verdict | lesson update |
|---|---|---|---|---|---|---|---|---|---|---|---|
| m4_e5_1 (start) | — | -0.725 | -7.846 | 26 | +0.101 | 0 | 0 | 0 | 508,267 | — | — |
| tune_torv_d1_1 | {"PL_TIMING_DRIVEN": true} | -0.541 | -1.341 | 8 | +0.103 | 0 | 0 | 0 | 508,267 | improved | verify lesson-torv-016 (lesson-torv-017) |
| tune_torv_d1_2 | {"PL_TIMING_DRIVEN": true, "GRT_RESIZER_SETUP_SLACK_MARGIN": 0.1} | -0.377 | -0.637 | 5 | +0.104 | 0 | 0 | 0 | 508,267 | improved | verify lesson-torv-019 (lesson-torv-020) |

### Better / worse (flow_report comparison with the best run at the time)

| run | better | worse | not comparable |
|---|---|---|---|
| tune_torv_d1_1 | power_total_w 0.013677507638931274 -> 0.013454895466566086; max_fanout_violations 141 -> 137; hold_wns@nom_fast_1p32V_m40C 0.10087869713992108 -> 0.10318321489288734; setup_wns@nom_slow_1p08V_125C -0.7254481665165831 -> -0.5414380208542079; setup_tns@nom_slow_1p08V_125C -7.845814886514664 -> -1.3411583334619341; setup_violations@nom_slow_1p08V_125C 26 -> 8; hold_wns@nom_slow_1p08V_125C 0.21507163325690928 -> 0.24418823210189983; setup_wns@nom_typ_1p20V_25C 2.3537270576568807 -> 2.484922560276508; hold_wns@nom_typ_1p20V_25C 0.20531715797552602 -> 0.2058517858881546 | setup_wns@nom_fast_1p32V_m40C 4.1201851985284215 -> 3.9331823355962268 | not shown to be the same design (snapshot current vs current): deltas mix design and flow changes |
| tune_torv_d1_2 | max_fanout_violations 137 -> 136; hold_wns@nom_fast_1p32V_m40C 0.10318321489288734 -> 0.10407089373733167; setup_wns@nom_slow_1p08V_125C -0.5414380208542079 -> -0.3772511299017597; setup_tns@nom_slow_1p08V_125C -1.3411583334619341 -> -0.6370602004019229; setup_violations@nom_slow_1p08V_125C 8 -> 5; hold_wns@nom_slow_1p08V_125C 0.24418823210189983 -> 0.24567615304158374; setup_wns@nom_typ_1p20V_25C 2.484922560276508 -> 2.583212383783238 | power_total_w 0.013454895466566086 -> 0.013459336943924427; setup_wns@nom_fast_1p32V_m40C 3.9331823355962268 -> 3.904769951235673; hold_wns@nom_typ_1p20V_25C 0.2058517858881546 -> 0.20581986697529386 | — |

### Config patch (awaiting approval; not applied)

| item | value |
|---|---|
| knobs (-c KEY=VALUE) | {"PL_TIMING_DRIVEN": true, "GRT_RESIZER_SETUP_SLACK_MARGIN": 0.1} |
| best run | tune_torv_d1_2 |
| diff of the design's config | generated/tune_torv_d1/config.patch |
| overrides | generated/tune_torv_d1/overrides.json |

### Evidence gate decisions

| attempt | result | best passing evidence | reasons |
|---|---|---|---|
| 1 | pass | — | — |

### Cost

| who | model | turns | tool calls | tokens in | tokens out | cached in | wall time (s) |
|---|---|---|---|---|---|---|---|
| proposer | deepseek / deepseek-v4-pro | — | — | 23,699 | 12,027 | 7,936 | 3,575.0 |
| total |  | 0 | 0 | 23,699 | 12,027 | 7,936 | 3,575.0 |

---

# Receipt tune_torv_d1: backend tuning

- project: torv · goal: post-route setup slack at the slow corner (nom_slow_1p08V_125C) at the team's 10 ns clock (metric setup_wns_slow) · budget 2 flow run(s), used 2 · proposer: deepseek/deepseek-v4-pro (prompt tune-propose.v1)
- status: **awaiting_approval** · approval: pending
- applied to the design: **no** (a person applies the patch)
- evidence gate: passed
- tokens: {'input_tokens': 23699, 'output_tokens': 12027, 'cache_read_input_tokens': 7936, 'cache_creation_input_tokens': 0} · wall time: 3575.0 s

## Attempts

### Attempt 1 (round 1, model): ran

- patch: `{"PL_TIMING_DRIVEN": true}`
- tests lesson: lesson-torv-016
- hypothesis: Enabling timing-driven global placement shortens the slow-corner register-to-output paths that currently dominate WNS, improving post-route setup WNS from -0.725 ns toward 0 ns.
- reasoning: Current worst paths are register-to-output paths from _29567_/Q to output ports, and PL_TIMING_DRIVEN is false, so placement optimizes mainly wirelength. Timing-driven placement should reduce data-path delay on those critical paths.
- run: `tune_torv_d1_1` (fail, E4) at `/home/panlong/project/eda-agent-d/runs/pnr/torv_tune_torv_d1_1/src/librelane/runs/tune_torv_d1_1`: tune_torv_d1_1 (torv_cpu): signoff FAIL [E5-partial]; setup WNS -0.541 ns (nom_slow_1p08V_125C), hold WNS +0.103 ns; DRC Magic 0, KLayout 0; LVS errors 0; antenna nets 0; die 508267 um2, util 0.544666, 18200 cells
- verdict (code): **improved**: setup_wns_slow -0.725 -> -0.541 ns (+0.184)
- better: power_total_w 0.013677507638931274 -> 0.013454895466566086; max_fanout_violations 141 -> 137; hold_wns@nom_fast_1p32V_m40C 0.10087869713992108 -> 0.10318321489288734; setup_wns@nom_slow_1p08V_125C -0.7254481665165831 -> -0.5414380208542079; setup_tns@nom_slow_1p08V_125C -7.845814886514664 -> -1.3411583334619341; setup_violations@nom_slow_1p08V_125C 26 -> 8; hold_wns@nom_slow_1p08V_125C 0.21507163325690928 -> 0.24418823210189983; setup_wns@nom_typ_1p20V_25C 2.3537270576568807 -> 2.484922560276508; hold_wns@nom_typ_1p20V_25C 0.20531715797552602 -> 0.2058517858881546
- worse: setup_wns@nom_fast_1p32V_m40C 4.1201851985284215 -> 3.9331823355962268
- lesson update: {"kind": "verify", "lesson": "lesson-torv-016", "entry": "lesson-torv-017", "outcome_entry": "lesson-torv-018"}

### Attempt 2 (round 2, model): rejected

- patch: `{"RUN_POST_GRT_DESIGN_REPAIR": true}`
- tests lesson: —
- hypothesis: Enabling post-GRT design repair buffers long high-load output nets after global routing; this reduces RC delay on the dominant _29567_/Q -> i_haddr[9] register-to-output path and improves slow-corner setup WNS from -0.541438 ns toward 0 ns.
- reasoning: The worst paths all start from _29567_/Q and end at output ports (i_haddr, s_haddr, etc.), suggesting RC-dominant long output wires. RUN_POST_GRT_DESIGN_REPAIR is currently false, so no post-route max-cap/slew/wire-length repair runs before timing repair. Enabling it uses DESIGN_REPAIR_BUFFER_OUTPUT_PORTS=true and should add buffers on those output paths.
- knob policy **ERROR**: `RUN_POST_GRT_DESIGN_REPAIR=True`: not on the tunable list (core/knobs.py); a person adds a knob there first

### Attempt 3 (round 2, model): ran

- patch: `{"GRT_RESIZER_SETUP_SLACK_MARGIN": 0.1}`
- tests lesson: lesson-torv-019
- hypothesis: Increasing the post-global-route resizer setup slack margin from 0.025 ns to 0.1 ns makes the setup resizer optimize additional near-violating register-to-output paths (e.g. _29567_/Q -> i_haddr[9]), improving slow-corner setup WNS from -0.541 ns toward approximately -0.3 ns or better.
- reasoning: The current worst paths are register-to-output paths dominated by _29567_/Q. RUN_POST_GRT_RESIZER_TIMING is already true, so GRT_RESIZER_SETUP_SLACK_MARGIN directly controls how aggressively the post-GRT setup resizer repairs near-threshold paths. A larger positive slack margin should reduce the remaining 8 slow-corner violations and improve WNS/TNS without relaxing any signoff check.
- run: `tune_torv_d1_2` (fail, E4) at `/home/panlong/project/eda-agent-d/runs/pnr/torv_tune_torv_d1_2/src/librelane/runs/tune_torv_d1_2`: tune_torv_d1_2 (torv_cpu): signoff FAIL [E5-partial]; setup WNS -0.377 ns (nom_slow_1p08V_125C), hold WNS +0.104 ns; DRC Magic 0, KLayout 0; LVS errors 0; antenna nets 0; die 508267 um2, util 0.545208, 18209 cells
- verdict (code): **improved**: setup_wns_slow -0.541 -> -0.377 ns (+0.164)
- better: max_fanout_violations 137 -> 136; hold_wns@nom_fast_1p32V_m40C 0.10318321489288734 -> 0.10407089373733167; setup_wns@nom_slow_1p08V_125C -0.5414380208542079 -> -0.3772511299017597; setup_tns@nom_slow_1p08V_125C -1.3411583334619341 -> -0.6370602004019229; setup_violations@nom_slow_1p08V_125C 8 -> 5; hold_wns@nom_slow_1p08V_125C 0.24418823210189983 -> 0.24567615304158374; setup_wns@nom_typ_1p20V_25C 2.484922560276508 -> 2.583212383783238
- worse: power_total_w 0.013454895466566086 -> 0.013459336943924427; setup_wns@nom_fast_1p32V_m40C 3.9331823355962268 -> 3.904769951235673; hold_wns@nom_typ_1p20V_25C 0.2058517858881546 -> 0.20581986697529386
- lesson update: {"kind": "verify", "lesson": "lesson-torv-019", "entry": "lesson-torv-020", "outcome_entry": "lesson-torv-021"}

## Config patch

- knobs: `{"PL_TIMING_DRIVEN": true, "GRT_RESIZER_SETUP_SLACK_MARGIN": 0.1}`

```diff
--- a/librelane/config.yaml
+++ b/librelane/config.yaml
@@ -46,3 +46,6 @@
 CLOCK_PERIOD: 10.0
 PNR_SDC_FILE: dir::constraints.sdc
 SIGNOFF_SDC_FILE: dir::constraints.sdc
+# eda-agent tune tune_torv_d1: best run tune_torv_d1_2 (awaiting approval)
+PL_TIMING_DRIVEN: true
+GRT_RESIZER_SETUP_SLACK_MARGIN: 0.1
```

Apply after approval, in the design repository: `git apply generated/tune_torv_d1/config.patch`; eda-agent never writes there.

## Report (checked by the evidence gate)

Tuning run tune_torv_d1.
Goal: post-route setup slack at the slow corner (nom_slow_1p08V_125C) at the team's 10 ns clock.
Budget: 2 flow run(s); 2 used.
Start: the best run of the eda_agent table, m4_e5_1 (flow_report, SDC recorded): setup WNS at nom_slow_1p08V_125C -0.725 ns, 26 violating endpoint(s).

- Attempt 1 (model): patch {"PL_TIMING_DRIVEN": true}: flow_run tune_torv_d1_1 fail (E4); setup WNS at nom_slow_1p08V_125C -0.541 ns, against m4_e5_1; verdict (code) improved: setup_wns_slow -0.725 -> -0.541 ns (+0.184).
  Lesson store (source: flow_run tune_torv_d1_1): this run supports lesson-torv-016; new entry lesson-torv-017 (single run).
- Attempt 2 (model): patch {"RUN_POST_GRT_DESIGN_REPAIR": true}: rejected before running (error RUN_POST_GRT_DESIGN_REPAIR: not on the tunable list (core/knobs.py); a person adds a knob there first).
- Attempt 3 (model): patch {"GRT_RESIZER_SETUP_SLACK_MARGIN": 0.1}: flow_run tune_torv_d1_2 fail (E4); setup WNS at nom_slow_1p08V_125C -0.377 ns, against tune_torv_d1_1; verdict (code) improved: setup_wns_slow -0.541 -> -0.377 ns (+0.164).
  Lesson store (source: flow_run tune_torv_d1_2): this run supports lesson-torv-019; new entry lesson-torv-020 (single run).

Best run now: tune_torv_d1_2 with knobs {"PL_TIMING_DRIVEN": true, "GRT_RESIZER_SETUP_SLACK_MARGIN": 0.1}; the patch is not applied to the design.

## Residual risk

- single run per setting: the effect of the knob cannot be told apart from run-to-run noise (placement and routing react chaotically to any change); repeat the run before relying on it.
- Signoff still fails while any corner has negative setup slack; the verdict reads slow-corner setup only.
- E5-partial at most: no gate-level simulation of the routed netlist, vectorless power.

## Residual risk

- single run per setting: a knob's effect cannot be told apart from run-to-run noise (placement and routing react chaotically to any change); an identical config was reproduced exactly once (m4_e5_1 = full_p10_fwd), which says nothing about the noise a changed knob brings
- the verdict reads one goal metric (slow-corner setup WNS) and the signoff counts; area, power and runtime are listed but not judged
- post-route STA, DRC, LVS and antenna only (E5-partial): no gate-level simulation of the routed netlist, vectorless power
- the patch is for this design, this PDK, this clock period and this flow version; it is not a general rule
