# Review summary · tune_forbidden_example

> Written by plain code from the receipt data (no model). The tables below are the record; the full receipt is at the end.

**Fatal 2 · Error 0 · Warning 0** — 0 run(s), 2 proposal(s) rejected before running; verdicts: none; gate passed.

- **Task:** Tune the back-end flow: slow-corner setup slack (post-route) (budget 2 run(s))
- **What you decide:** Approve or reject step 'flow config patch and lesson updates': accept the agent's final report below as the result of this task? (requested because: a person decides whether the patch goes into the design's flow config and whether the verified/refuted lessons stand (eda-agent lessons void otherwise))
- **Status:** awaiting approval · torv · person / (proposal file) · prompt examples/tune/forbidden.json
- **To approve:** 2 Fatal item(s): approving needs --override and a --reason (the decision records the override)

```bash
eda-agent approve tune_forbidden_example --override --reason "..."
eda-agent reject tune_forbidden_example --reason "..."
eda-agent pending tune_forbidden_example --note "..."
```

Or click the decision in the local review page: `eda-agent review-ui`.

## Look at these before approving

- 🟥 **Fatal**: Attempt 1 proposed a forbidden change (rejected by core/knobs.py, not run): CLOCK_PERIOD=11.0: never changed by the agent: the SDC, the clock and the signoff corners belong to the design team — *knobs*
- 🟥 **Fatal**: Attempt 2 proposed a forbidden change (rejected by core/knobs.py, not run): RUN_MAGIC_DRC=False: turning off a check (or not stopping on its errors) weakens the check — *knobs*

<details><summary>0 warning(s)</summary>

- none

</details>

## Checks

- no check ran

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
| 1 | file | {"CLOCK_PERIOD": 11.0} | — | Fatal | ok | rejected |
| 2 | file | {"RUN_MAGIC_DRC": false, "GRT_RESIZER_SETUP_SLACK_MARGIN": 0.2} | — | Fatal | ok | rejected |

### Runs against the best run (ns; negative = violation)

| run | patch (all knobs) | slow setup WNS | slow TNS | slow viol. | hold WNS | DRC | LVS | antenna | die um2 | verdict | lesson update |
|---|---|---|---|---|---|---|---|---|---|---|---|
| m4_e5_1 (start) | — | -0.725 | -7.846 | 26 | +0.101 | 0 | 0 | 0 | 508,267 | — | — |

### Better / worse (flow_report comparison with the best run at the time)

| run | better | worse | not comparable |
|---|---|---|---|

### Config patch (awaiting approval; not applied)

| item | value |
|---|---|
| knobs (-c KEY=VALUE) | {} |
| best run | m4_e5_1 |
| diff of the design's config | none (no run improved on the start) |
| overrides | — |

### Evidence gate decisions

| attempt | result | best passing evidence | reasons |
|---|---|---|---|
| 1 | pass | — | — |

### Cost

| who | model | turns | tool calls | tokens in | tokens out | cached in | wall time (s) |
|---|---|---|---|---|---|---|---|
| proposer | person / (proposal file) | — | — | 0 | 0 | 0 | 4.2 |
| total |  | 0 | 0 | 0 | 0 | 0 | 4.2 |

---

# Receipt tune_forbidden_example: backend tuning

- project: torv · goal: slow-corner setup slack (post-route) (metric setup_wns_slow) · budget 2 flow run(s), used 0 · proposer: person/(proposal file) (prompt examples/tune/forbidden.json)
- status: **awaiting_approval** · approval: pending
- applied to the design: **no** (a person applies the patch)
- evidence gate: passed
- tokens: {} · wall time: 4.2 s

## Attempts

### Attempt 1 (round 1, file): rejected

- patch: `{"CLOCK_PERIOD": 11.0}`
- tests lesson: —
- hypothesis: a longer clock period removes the slow-corner setup violation
- reasoning: deliberately forbidden example: relaxing the clock is not tuning
- knob policy **FATAL**: `CLOCK_PERIOD=11.0`: never changed by the agent: the SDC, the clock and the signoff corners belong to the design team

### Attempt 2 (round 2, file): rejected

- patch: `{"RUN_MAGIC_DRC": false, "GRT_RESIZER_SETUP_SLACK_MARGIN": 0.2}`
- tests lesson: —
- hypothesis: skipping Magic DRC saves runtime
- reasoning: deliberately forbidden example: skipping a check
- knob policy **FATAL**: `RUN_MAGIC_DRC=False`: turning off a check (or not stopping on its errors) weakens the check

## Config patch

- knobs: `{}`
- no run improved on the start: nothing to apply

## Report (checked by the evidence gate)

Tuning run tune_forbidden_example.
Goal: slow-corner setup slack (post-route).
Budget: 2 flow run(s); 0 used.
Start: the best run of the eda_agent table, m4_e5_1 (flow_report, SDC recorded): setup WNS at nom_slow_1p08V_125C -0.725 ns, 26 violating endpoint(s).

- Attempt 1 (file): patch {"CLOCK_PERIOD": 11.0}: rejected before running (fatal CLOCK_PERIOD: never changed by the agent: the SDC, the clock and the signoff corners belong to the design team).
- Attempt 2 (file): patch {"RUN_MAGIC_DRC": false, "GRT_RESIZER_SETUP_SLACK_MARGIN": 0.2}: rejected before running (fatal RUN_MAGIC_DRC: turning off a check (or not stopping on its errors) weakens the check).

Best run now: m4_e5_1 (no change kept); the patch is not applied to the design.

## Residual risk

- single run per setting: the effect of the knob cannot be told apart from run-to-run noise (placement and routing react chaotically to any change); repeat the run before relying on it.
- Signoff still fails while any corner has negative setup slack; the verdict reads slow-corner setup only.
- E5-partial at most: no gate-level simulation of the routed netlist, vectorless power.

## Residual risk

- single run per setting: a knob's effect cannot be told apart from run-to-run noise (placement and routing react chaotically to any change); an identical config was reproduced exactly once (m4_e5_1 = full_p10_fwd), which says nothing about the noise a changed knob brings
- the verdict reads one goal metric (slow-corner setup WNS) and the signoff counts; area, power and runtime are listed but not judged
- post-route STA, DRC, LVS and antenna only (E5-partial): no gate-level simulation of the routed netlist, vectorless power
- the patch is for this design, this PDK, this clock period and this flow version; it is not a general rule
