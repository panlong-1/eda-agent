# Evidence levels and the evidence gate

## Evidence levels

Every tool result carries one level; what it lets a reader conclude, and what it does not (the same text the receipts show, `agent/receipt/summary.py`):

| level | in words | may say | may NOT say |
|---|---|---|---|
| E0 | parses / structural facts | the design parses and elaborates, or the constraints exist as stated | that the design works, or meets timing |
| E1 | someone's score | a person or a model rated it | anything measured by a tool |
| E2 | these tests passed | the listed test cases ran and passed by the testbench's own checks | that the design is correct, or that the tests would catch a bug (no coverage) |
| E3 | bit-exact / mutation evidence | outputs match a reference bit for bit, or the tests kill injected bugs | timing, power or anything after layout |
| E4 | synthesis estimate | area and timing as estimated before placement and routing, under this SDC | function, or signoff timing after placement and routing |
| E5 | signoff | post-route multi-corner timing, DRC/LVS and gate-level checks passed | anything outside the signed-off corners and modes |
## The gate (R1-R11)

When the model ends its turn, `agent/gate/` checks the report against the tool
results of this run. One module per rule (`r1_citations.py` .. `r11_memory.py`):

- **R1 citations.** Every status/evidence-level pair the report cites ("regress: pass (E2)")
  and every "N passed / N failed" count must come from a tool result of this run. A level
  named as a requirement ("that would need E3") is not a citation.
- **R2 strength.** Words that claim functional correctness (handles, correct, verified,
  works, bug-free, … and Chinese equivalents such as 正确, 已验证) need a passing result at a
  level that speaks about function, E3 (bit-exact comparison or mutants) or E5 (signoff with
  gate-level simulation), and the same sentence must name that result (its tool or a case it
  passed), so evidence on one check cannot vouch for another. E4 (synthesis, PPA) is above E3
  on the ladder but never vouches for function. A sentence of the form "does not prove: A, B,
  or gate-level functional correctness" lists limits after its colon, not claims. With E2 the report may only say which cases passed. A negation earlier in
  the same clause ("does not show … correct") makes it a stated limit, not a claim, and so
  are the list items that follow it ("not … handled, correct, or bug-free"); a clause with
  more than a list connector before the word ("…, but the design is correct") is a new claim.
- **R3 residual risk.** The report must have a section headed "Residual risk" with content.
- **R4 skipped cases.** A case the agent read with `describe_case` but did not run must be
  named in that section, so that leaving out a relevant test is stated, not silent.
- **R5 timing.** A timing claim ("meets 100 MHz", "positive slack", "timing met", "no setup
  violations", 满足时序 …) must name, in the same sentence, a result of this run that carries
  an SDC sha256 (`synth`, `STA`, `E4` or the hash), and agree with it: the result must say
  timing is met, a stated frequency must match its clock period, and every WNS/TNS/slack
  number must be one the result reports (rounding allowed). If the SDC hash changes during a
  run, timing results measured under the earlier SDC are void and cannot be cited.

- **R6 tests.** A claim that a test catches / detects / kills a bug or mutant, or that the kill
  rate rose, must name a `score_tests` result in the same sentence (`mutate` also counts for
  plain facts about mutants, never for a rise); its percentages must be ones those results
  report, and every mutant it names must have been killed there.
- **R7 performance.** A performance claim (faster, slower, speed-up, "N cycles", IPC/CPI,
  throughput, 更快/更慢/周期数 …) must name a `perf` result (or `mutate`, for a mutant) in the
  same sentence, and its cycle, IPC/CPI and percentage numbers must be ones those results
  report. "No performance regression" / "not slower" needs a `perf` result compared with the
  approved baseline and slower on no case. A negation earlier in the clause makes it a limit.
- **R8 signoff** (`agent/gate/r8_signoff.py`). "DRC clean", "LVS clean", "antenna clean",
  "post-route timing met", "signoff passed", "tapeout-ready" (and 签核通过, 可以流片 …) must name
  a `flow_report`/`flow_run` result (or its run tag) in the same sentence, and a flow result of
  this run must show exactly that fact. "Tapeout-ready" needs every signoff rule passing **and**
  E5 (gate-level simulation and VCD power), which a LibreLane run alone does not give. Counts
  ("0 DRC errors") must be counts a flow result reports.
- **R9 category** (`agent/gate/r9_categories.py`). "lint/CDC/RDC clean", "DFT passed", "test coverage
  N%", "formally equivalent" cite a result of that category with its numbers; categories never
  stand in for each other (imported results, [plugins.md](plugins.md)).
- **R10 localisation** (`agent/gate/r10_localise.py`). "The bug is in X", "caused by line N", "root
  cause", "localised to", 定位到 / 根因 place the cause of a failure: a diagnosis. The sentence must
  name a `trace_driver` / `trace_load` / `waveform_query` result of this run, every `file:line` or
  "line N" in it must be a line a trace result of this run reached, it must not present the
  diagnosis as a proven fix ("fixed", "fix verified"), and the report must call it a hypothesis
  or diagnosis somewhere. See [failure-localisation.md](failure-localisation.md).
- **R11 memory** (`agent/gate/r11_memory.py`, [memory.md](memory.md)). Lessons and documents are hints,
  never evidence. Every lesson id (`lesson-<design>-NNN`) the report names must exist and must have been
  retrieved in this run (a hint at the start, or returned by `search_memory`); a voided or refuted lesson
  may not be cited. A document is cited with its section (`doc:<doc>#<section>`), which must exist and
  have been retrieved. The sentence must mark the citation as a hint or source, and may not use it to
  confirm, prove or verify anything unless it names a tool result of this run. Items of a
  `## Lessons (hypotheses)` section are new lessons, not citations: only the existence checks apply.

A rejected report goes back to the model with the reasons, at most twice; then the run
stops with `stop_reason: gate_rejected` and `status: pending` for a human to judge. The
checks are deliberately conservative and explainable: they can reject a fair sentence,
which costs a rewrite, but they do not try to understand the design.
