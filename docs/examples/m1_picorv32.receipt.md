# Review summary · m1_picorv32

> Written by plain code from the receipt data (no model). Everything else is in the details below; quotes and tool output stay in their original language.

**In one line:** 2 of 2 check(s) passed; gate passed; 0 serious and 1 warning item(s) to look at.

- **Task**: Lint the core and run the regression; report what passed
- **What you decide**: No human decision was requested for this run (status done); nothing to decide.
- **Status**: done (no approval requested) · picorv32 · deepseek / deepseek-v4-pro

## Look at these before approving

- ⚠️ No tool versions recorded for: lint, regress. The result cannot be tied to a tool release.
- ℹ️ No independent review was run: only the gate checked the report.

## Checks

| check | result | evidence | tool summary |
|---|---|---|---|
| lint | ✅ pass | E0 parses / structural facts | 0 error(s), 34 warning(s), top=picorv32 |
| regress `{"cases": ["firmware"]}` | ✅ pass | E2 these tests passed | 1 passed, 0 failed, 0 skipped |

## What the evidence allows

- Highest evidence reached: E2 (these tests passed).
- E2 (these tests passed): may say the listed test cases ran and passed by the testbench's own checks; may NOT say that the design is correct, or that the tests would catch a bug (no coverage).
- E0 (parses / structural facts): may say the design parses and elaborates, or the constraints exist as stated; may NOT say that the design works, or meets timing.

## Not checked

- no synthesis or timing estimate was run
- lint says nothing about function; run regress for E2
- only an exit status (and output pattern) was observed: no per-check detail, so which sub-tests ran is not known
- test adequacy unknown: no coverage or mutation score yet

## Evidence gate

- passed; the agent's report was sent back for a rewrite 0 time(s)

## Reviewers

- no independent review was run

## Approval

- no approval record

---

# Run receipt m1_picorv32

- **Task:** Lint the core and run the regression; report what passed
- **Project:** picorv32  ·  **Model:** deepseek / deepseek-v4-pro  ·  **Prompt:** n/a
- **Stop:** end  ·  **Status:** done  ·  **Gate:** passed (0 rewrite(s))
- **Cost:** 4 turns, 4 tool calls (0 errors), 12193 in / 1689 out tokens, 30.15 s

## Checks run

| tool | args | status | evidence | summary |
|---|---|---|---|---|
| lint | `{}` | pass | E0 | 0 error(s), 34 warning(s), top=picorv32 |
| regress | `{"cases": ["firmware"]}` | pass | E2 | 1 passed, 0 failed, 0 skipped |

**lint {}** claim scope: Verilator parsed and elaborated the design under the project's flags. This proves nothing about functional behaviour or timing.

**regress {"cases": ["firmware"]}** claim scope: Each case's command exited 0 and printed /^ALL TESTS PASSED/. There is no per-check detail: one exit status may cover many tests, and a test that checks nothing looks the same. It does not show that the tests would catch a bug (no coverage or mutation score), and says nothing about timing or power.
- artifact `/home/panlong/project/eda-agent/runs/logs/picorv32_20261031_170623_firmware.log` sha256 `53b0becf21f22f24b53087e48f311045e377f18becdb493f49c9945adfd337e1`

## Not run

- lint run: yes
- described but not run: none
- cases in the list not run (0): none

## Gate decisions

1. pass (best passing evidence E2)

## Final claims

## Report

### Lint
- Tool result: `lint` → **status: pass**, **evidence_level: E0**.
- Claim scope from the tool: *"Verilator parsed and elaborated the design under the project's flags. This proves nothing about functional behaviour or timing."*
- Result: **0 errors, 34 warnings** for top `picorv32`.
  - Warnings by code: `UNUSEDSIGNAL` 15, `BLKSEQ` 11, `GENUNNAMED` 7, `DECLFILENAME` 1.
- This only means the design parses/elaborates under Verilator with the project flags; it is not evidence of function.

### Regression
- Listed cases: 1 case — `firmware`.
- Read `firmware` description before running: the test is intended to cover RV32IMC per-instruction riscv-tests, hello, sieve, multest, stats, and periodic IRQ 4/5 entry/return paths. (Description says what the test is *meant* to cover, not evidence it checks it.)
- Tool result: `regress` → **status: pass**, **evidence_level: E2**.
- Claim scope from the tool: *"Each case's command exited 0 and printed /^ALL TESTS PASSED/. There is no per-check detail: one exit status may cover many tests, and a test that checks nothing looks the same. It does not show that the tests would catch a bug (no coverage or mutation score), and says nothing about timing or power."*
- Result: **1 passed, 0 failed, 0 skipped** — passed case: `firmware`.

### What this establishes
- The core is lint-clean at the parse/elaboration level (E0), with warnings but no errors.
- The `firmware` regression case passed (E2): its command exited 0 and printed `ALL TESTS PASSED`.

###

## Residual risk

- Lint (E0) says nothing about functional behavior or timing.
- The regression result is E2 only: a single exit status/output pattern over potentially many sub-tests; it does not provide per-check detail, coverage, or mutation score, so it does not show the tests would catch a bug.
- No timing, power, or physical checks were run.
- The test description lists intended coverage (RV32IMC instructions, IRQ path, etc.), but the passing result does not independently verify each of those features at E3 or higher.

From the tools:
- lint says nothing about function; run regress for E2
- only an exit status (and output pattern) was observed: no per-check detail, so which sub-tests ran is not known
- test adequacy unknown: no coverage or mutation score yet

Trace: `runs/m1_picorv32.jsonl`
