# Back-end tuning from experience (plan §0.15, steps 2 and 3)

The flow's run history ([flow-signoff.md](flow-signoff.md), `flow_history`) and the lessons store
([memory.md](memory.md)) are used to propose better flow settings, try them on a copy of the
design within a run budget, and hand a config **patch** to a person. Three human-set standards hold
throughout, in code:

- the SDC and the clock are never touched (§0.8);
- no check is ever weakened;
- the output is a patch and a receipt that waits for approval; nothing is applied to the design.

## Step 2: lessons with citations

```bash
eda-agent lessons propose --project projects/torv.toml [--provider deepseek] [--dry-run]
```

`flow_history` is turned into a table of **facts**, one id per number:
`<run>:<metric>`, e.g. `full_p10_fwd:post_setup_wns_slow` = -0.725, `p7_booth:synth_area_um2`,
`m4_e5_1:die_area_um2`, `full_p10_booth:cfg.SYNTH_MUL_BOOTH`. The model sees these facts, the knob
effects (isolated or confounded pairs), the config comments with the imported table's verdict and
the lessons already stored, and proposes at most 6 lessons, each with the fact ids it cites.

Code checks every lesson (`agent/tuning.py: check_lesson`); one that fails is not stored:

| check | rule |
|---|---|
| it cites facts | at least one, and every cited id exists in the experience tables (R1) |
| runs it names | every run named in the text (names with a digit or `_`) has a cited fact |
| numbers | each number in the text is a cited value, its magnitude, the difference or percent change of two cited values of one metric, a cited run's clock period or its frequency, at the text's precision |
| lesson ids | exist and were shown to the model (R11, the same check as for reports) |
| new | not the same text as a stored lesson |

A lesson that passes goes into `lessons.jsonl` as a **hypothesis**, scoped to the design, tool, PDK,
the corners of its cited metrics and the knob, with one source per cited fact (run id, metric,
value, the run's STA-summary sha256). The proposal and the verdict on every lesson are saved in
`runs/lessons_<time>.json`.

## Step 3: propose, check, run, compare

```bash
eda-agent tune --project projects/torv.toml --goal "slow-corner setup" --budget-runs 2 --provider deepseek
eda-agent tune --proposal-file p.json [--dry-run]     # a person's proposal(s): same checks, no model
```

Per round, while the budget has runs left (`agent/tuning.py: tune`):

1. **Propose.** The model gets the goal, the current best run of the design's eda_agent table (its
   numbers, worst setup paths and the current values of the tunable knobs), the lessons retrieved
   for the goal (hints, with ids), the experience facts, the knob policy and the attempts so far. It
   answers `{"patch": {...}, "tests_lesson": id | null, "hypothesis": "...", "reasoning": "..."}`.
2. **Check, by code, before anything runs.**
   - The **knob policy**, `core/knobs.py`. Forbidden keys are **Fatal**: the SDC files, the clock
     (period, port, net), the STA/signoff/violation corners, every `*_CONSTRAINT` the flow writes
     into its SDC (I/O delay, clock uncertainty and transition, derating, max slew/cap/fanout),
     waivers and "ignore" keys, tolerances, and "allow violations / allow congestion" switches.
     Turning off a check switch (`RUN_*DRC|LVS|ANTENNA|XOR|STA|LINTER...`, `ERROR_ON_*`, `QUIT_ON_*`)
     is Fatal too. A key that is neither forbidden nor on the code's tunable list (utilisation and
     density, placement, resizer repair and buffering, routing iterations, synthesis strategy and
     switches, CTS parameters) is an Error.
   - `[pnr] allowed_overrides`: the person's own list for the project (a forbidden key there is
     refused when the project loads).
   - R11 on the reasoning (lesson ids exist, were retrieved, are marked as hints, never "confirm").
   - The tested lesson (if named) is about a knob of the patch; the patch was not tried before.

   A rejected proposal is recorded with its reasons and goes back to the model once per round;
   no run is spent. Two rejections in a round end the loop.
3. **Run** `flow_run` with the patch on top of the best run's patch, on a copy of the design under
   `[pnr] work_dir` (never in the design's checkout), recorded in the eda_agent table. A proposal
   without `tests_lesson` first stores its hypothesis as a lesson, so the run always tests a lesson.
4. **Compare** with the best run (flow_report's better/worse lists and snapshot rules). The verdict
   is computed by code: `improved` (slow-corner setup WNS went up and no signoff count got worse),
   `not_improved`, `signoff_worse` (DRC, LVS, antenna count up or hold slack turned negative), or
   `no_result`.
5. **Update the lesson.** `improved` appends a `verify` entry (plus the measured outcome as a new
   hypothesis); `not_improved` or `signoff_worse` appends a `refute` entry whose text is the
   measured outcome. Both cite the run (id, sidecar sha256) and say "single run". A person can
   `eda-agent lessons void` either.
6. **Patch and receipt.** If a run improved on the start, the best run's knobs are written as
   `generated/<run_id>/overrides.json` (`-c KEY=VALUE`) and `generated/<run_id>/config.patch` (a diff
   of the design's config, for `git apply` in the design repository). The receipt
   (`runs/<run_id>.receipt.*`, kind `tune`) shows every proposal, the policy findings, the runs,
   the comparison, the verdicts and the lesson entries, and stays `awaiting_approval`.

A forbidden proposal is a **Fatal** item in the receipt, so approving such a receipt needs
`--override` and a reason. The tuning report is written by code and checked by the evidence gate
like any report. `eda-agent tune --rereport <run_id> --reason "..."` writes the report of an
undecided tune receipt again with the current code and re-runs the gate on the runs' flow results
(read again from their directories); nothing runs, no lesson changes, and the earlier report and gate
decision stay in the receipt under `rereports`.

**Noise.** One run per setting cannot separate a knob's effect from run-to-run noise: placement and
routing react chaotically to any change. Every verdict, lesson entry and receipt says so. (On TORV an
unchanged config reproduced exactly once, `m4_e5_1` = `full_p10_fwd`; that does not measure the noise
a changed knob brings.)

## Example (TORV)

`tune_torv_d1` (DeepSeek, budget 2 runs, receipt `docs/examples/m41_tune_torv_d1.receipt.md`, awaiting
approval): from `m4_e5_1` (slow-corner setup WNS -0.725 ns, 26 violating endpoints),
`PL_TIMING_DRIVEN=true` gave -0.541 ns (8 endpoints); adding `GRT_RESIZER_SETUP_SLACK_MARGIN=0.1` gave
-0.377 ns (5 endpoints); DRC, LVS and antenna stayed 0. One proposal (`RUN_POST_GRT_DESIGN_REPAIR`) was
rejected before running. Both hypotheses are supported by one run each; signoff at 100 MHz still fails
at the slow corner. The patch is `docs/examples/m41_tune_torv_d1.config.patch`.

`examples/tune/forbidden.json` holds two deliberately forbidden proposals (`CLOCK_PERIOD` 11 ns;
`RUN_MAGIC_DRC` false). `eda-agent tune --proposal-file examples/tune/forbidden.json` rejects both
as Fatal before anything runs; the receipt records them and needs `--override` to approve.
