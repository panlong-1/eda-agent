# Review summary · tg_m3_3

> Written by plain code from the receipt data (no model). The tables below are the record; the full receipt is at the end.

**Fatal 0 · Error 0 · Warning 0** — 1 of 1 gap(s) closed; gate passed; reviewers: 1× accept.

- **Task:** Write new tests that kill surviving mutants 3 of mutate run torv_mutate_20261031_204719_053393
- **What you decide:** Already decided: approved by Long Pan at 2026-10-31T23:15:53+01:00 (user). Decisions are final; this summary was re-rendered afterwards.
- **Status:** approved · torv · deepseek / deepseek-v4-pro · prompt testgen.v3 · review prompt testgen-review.v1

*Commands: already decided; decisions are final (a new run fixes a rejected one)*

## Look at these before approving

- no Fatal or Error items

<details><summary>0 warning(s)</summary>

- none

</details>

## Checks

| check | result | evidence | time (s) | tool summary |
|---|---|---|---|---|
| score_tests `{"mutant": 3}` | ✅ pass | E3 bit-exact / mutation evidence | — | kills target(s) [3] (3: perf_killed ['PASS', 'PASS']); fails on mutants 6, 13, 17, 19 (and moved the cycle count on mutants 3, 7, 8, 12, 16, 20); kill rate 75% -> 75% |
| score_tests `{"mutant": 3}` | ✅ pass | E3 bit-exact / mutation evidence | — | kills target(s) [3] (3: perf_killed ['PASS', 'PASS']); fails on mutants 6, 13, 17, 19 (and moved the cycle count on mutants 3, 7, 8, 12, 16, 20); kill rate 75% -> 75% |

## What the evidence allows

- Highest evidence reached: E3 (bit-exact / mutation evidence).
- E3 (bit-exact / mutation evidence): may say outputs match a reference bit for bit, or the tests kill injected bugs; may NOT say timing, power or anything after layout.

## Not checked

- kill rates cover one mutate run's sample of one module; a test that kills a mutant shows that these changes are detected, nothing about other bugs; each new test still needs a person's approval before it enters the regression.

## Evidence gate

- passed; the report is written by code from the score_tests results (no rewrites)

## Reviewers

- **test reviewer (deepseek-v4-pro), mutant 3**: accept (no findings)

## Approval

- approved by Long Pan at 2026-10-31T23:15:53+01:00 (user) · levels at the decision: Fatal 0 · Error 0 · Warning 0

## Data

### Mutants targeted

| mutant | status | tries | score_tests | test | patch | review |
|---|---|---|---|---|---|---|
| 3 | closed | 2 | pass E3 | generated/tg_m3_3/m3_a1.S | generated/tg_m3_3/m3_a1.patch | accept |

### Evidence gate decisions

| attempt | result | best passing evidence | reasons |
|---|---|---|---|
| 1 | pass | E3 | — |

### Cost

| who | model | turns | tool calls | tokens in | tokens out | cached in | wall time (s) |
|---|---|---|---|---|---|---|---|
| test author (model) | deepseek / deepseek-v4-pro | — | — | 9,288 | 53,480 | 3,712 | 577.1 |
| total |  | 0 | 0 | 9,288 | 53,480 | 3,712 | 577.1 |

---

# Receipt tg_m3_3: new tests against surviving mutants

- project: torv · mutate run: `torv_mutate_20261031_204719_053393` · model: deepseek/deepseek-v4-pro (prompt testgen.v3, review prompt testgen-review.v1)
- status: **approved** · approval: approved by Long Pan at 2026-10-31T23:15:53+01:00
- evidence gate: passed
- tokens: {'input_tokens': 9288, 'output_tokens': 53480, 'cache_read_input_tokens': 3712, 'cache_creation_input_tokens': 0} · wall time: 577.1 s

## Per mutant

### Mutant 3: closed

```diff
--- a/rtl/torv_hazard_unit.sv
+++ b/rtl/torv_hazard_unit.sv
@@ -32,22 +32,22 @@
   localparam logic [6:0] OP_REG   = 7'b0110011;
   localparam logic [6:0] OP_LUI   = 7'b0110111;
 
-  logic [6:0] op;
+  logic [5:0] op;  /* AVL MUTATION - width: op [6:0] -> op [5:0], 7 bits -> 6 */
   logic [4:0] rs1_f;
   logic [4:0] rs2_f;
   logic       uses_rs1;
   logic       uses_rs2;
 
-  assign op    = if_id_inst[6:0];
+  assign op    = 6'(if_id_inst[6:0]);  /* AVL MUTATION - width: op [6:0] -> op [5:0], 7 bits -> 6 */
   assign rs1_f = if_id_inst[19:15];
   assign rs2_f = if_id_inst[24:20];
 
   always_comb begin
-    unique case (op)
+    unique case (7'(op))  /* AVL MUTATION - width: op [6:0] -> op [5:0], 7 bits -> 6 */
       OP_JAL, OP_LUI, OP_AUIPC: uses_rs1 = 1'b0;
       default: uses_rs1 = 1'b1;
     endcase
-    unique case (op)
+    unique case (7'(op))  /* AVL MUTATION - width: op [6:0] -> op [5:0], 7 bits -> 6 */
       OP_REG, OP_STORE, OP_BR: uses_rs2 = 1'b1;
       default: uses_rs2 = 1'b0;
     endcase
```

| attempt | fix | test | on the unmodified design |
|---|---|---|---|
| 1 | 0 | `generated/tg_m3_3/m3_a1.S` | FAIL |
| 1 | 1 | `generated/tg_m3_3/m3_a1.S` | PASS |

- score_tests (pass, E3): kills target(s) [3] (3: perf_killed ['PASS', 'PASS']); fails on mutants 6, 13, 17, 19 (and moved the cycle count on mutants 3, 7, 8, 12, 16, 20); kill rate 75% -> 75%
  - perf-killed (passed, cycle count moved): [3, 7, 8, 12, 16, 20]

- score_tests (pass, E3): kills target(s) [3] (3: perf_killed ['PASS', 'PASS']); fails on mutants 6, 13, 17, 19 (and moved the cycle count on mutants 3, 7, 8, 12, 16, 20); kill rate 75% -> 75%
  - perf-killed (passed, cycle count moved): [3, 7, 8, 12, 16, 20]
  - mutant 3, cycles: unmodified design 16005 -> mutant 18004, 18004
  - mutant 3, instr: unmodified design 8005 -> mutant 8005, 8005
  - mutant 3, ipc: unmodified design 0.5 -> mutant 0.445, 0.445
  - mutant 3, stall_if: unmodified design 0 -> mutant 1999, 1999

- test: `generated/tg_m3_3/m3_a1.S` · patch: `generated/tg_m3_3/m3_a1.patch`
- review: verdict accept, architectural only no, expected values checked yes — Valid perf test; it creates a load to x31 followed by a JAL whose rs1 field is x31, so the mutant adds load-use stalls while the original does not. The pass/fail store value is correct.

## Report (checked by the evidence gate)

score_tests: the new timing test (kind=perf) generated/tg_m3_3/m3_a1.S kills mutant 3 by cycle count (in 2 of 2 runs; it passed 2 of 2 on the unmodified design); kill rate with perf 94% -> 100% over 16 mutants (score_tests).

Residual risk: kill rates cover one mutate run's sample of one module; a test that kills a mutant shows that these changes are detected, nothing about other bugs; each new test still needs a person's approval before it enters the regression.

Apply an approved patch in the design repository with `git apply <patch>`; eda-agent never writes there.

## More data (long lists)

### Every try (attempt.fix)

| mutant | attempt | test | on the unmodified design |
|---|---|---|---|
| 3 | 1.0 | generated/tg_m3_3/m3_a1.S | FAIL |
| 3 | 1.1 | generated/tg_m3_3/m3_a1.S | PASS |
