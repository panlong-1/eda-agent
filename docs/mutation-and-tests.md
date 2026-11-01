# Mutation runs, new tests and performance

How far the regression can be trusted: which small bugs it misses (E0 facts), new tests that close those gaps (E3, mutation-scored), and cycle counts against a baseline a person approved (E2). Gate rules R6 and R7 ([evidence-gate.md](evidence-gate.md)) check claims about them.

## Mutation runs: `[mutation]`

`mutate` asks which small bugs the project's own regression would miss. It makes `count`
mutants of one RTL file, each a single source-level change (an operator swapped, a branch
forced open or shut, a register update dropped, a signal delayed one cycle, …), applies each
alone in a copy of the design and runs the chosen regression cases on it. A mutant is
*killed* when at least one case fails and *survives* when all pass; the result lists, per
mutant, `file:line`, the line before → after, and which cases killed it (also as
`kill_matrix.csv` and `report.md` in the run's work directory). Survivors are test gaps, not
design bugs, so the level is E0: facts for the later `score_tests` step (E3).

```bash
.venv/bin/pip install -e ".[mutation]"     # avl-core, which generates the mutants
eda-agent mutate --project projects/torv.toml --file rtl/torv_hazard_unit.sv --count 20 --confirm-full
```

- **Mutants** come from [avl-core](https://github.com/projectapheleia/avl)'s
  `avl-mutation-testing` (MIT): sites found in the slang syntax tree, the change spliced into
  the source, so the diff reads like a code review. avl-core uses pyslang's byte offsets as
  string offsets, which garbles every mutant of a file with a non-ASCII character (TORV's
  comments have `→`); `mutate` hands it an ASCII stand-in with the same byte positions and
  carries each change back to the real file.
- **Copies.** `[mutation] copy_exclude` lists what is not copied (build and result
  directories) and is emptied after each mutant; `jobs` copies run in parallel. The run
  refuses if any environment variable still points into the original checkout, and records
  whether the checkout's git status and the target file stayed the same (`root_unchanged`).
- **Golden first.** The unmodified design must pass the same cases in the copy; otherwise
  nothing is judged. Each mutant is linted first: one that does not compile is `invalid`,
  not counted as caught.
- **Equivalent mutants.** Some changes cannot be seen from outside (reversing a port's bit
  numbering, say). Survivors are checked with a bounded Yosys SAT miter of the mutated module
  against the original (the same check avl-core uses, run in the `[flow]` image with slang);
  one with no difference is `likely_equivalent` and left out of the kill rate. Module-level and
  bounded, so neither verdict is a proof.
- `--confirm-full` re-runs the survivors on the whole case list, so a survivor is reported as
  missed by the entire regression, not only by the chosen cases.
- **Slower but still right.** A self-checking test only looks at results, so a mutant that just
  adds pipeline stalls passes everything. With a `[perf]` section, a mutant whose cases all
  pass but whose cycle count moves (against the unmodified design in the same copy) is
  `perf_killed`, reported apart from functional kills; the kill rate is given both ways.

## New tests against surviving mutants: `[tests]`, `write-tests`

```bash
eda-agent write-tests --project projects/torv.toml --mutate-run <runs/work dir of a mutate run> \
    --mutants 3 --provider deepseek
```

A fixed loop in code (`agent/testgen.py`); the model only writes the test text. Per mutant:
`test_context` (the change, the module, two example tests) → the model writes one test →
`write_case` puts it in `generated/<run_id>/` and runs it on the unmodified design in a copy
(a failure goes back to the model, at most twice) → `score_tests` → if the target survives,
a new attempt (at most three). `score_tests` gives E3 only when the test passes twice on the
unmodified design, fails on the target in both of two runs, and lists every other mutant of
that mutate run it kills (or only slows), with the kill rate before and after; then it writes
a patch (the test file plus a case-list line). A fresh model call reviews the test (it sees
the test, the diff and the measured facts, not the author's reasoning), and the run waits for
a person: `eda-agent approve <run_id>`, then `git apply` the patch in the design. eda-agent
never writes into the design. A gap that could not be closed is reported as such.

`[tests]` needs one line, `run` (runs one test file: `{file}`, `{name}`); optional `log` (where
the detailed output is), `register` (the case-list line in the patch) and `examples`. Pass/fail
is judged as for the regression. New tests check results the specification defines (registers,
memory), not timing; a test whose header says `kind=perf` checks cycles and is counted apart.
The same loop fits a UVM bench (a test class run with `+UVM_TESTNAME={name}`, judged by
UVM_ERROR/UVM_FATAL counts). eda-agent currently runs on open-source simulators; commercial
simulators (needed for full UVM) are planned, and need only a different `run` command.

## Performance: `[perf]`

`perf` runs regression cases and reads the counters the testbench already prints, with the
regexes in `[perf] patterns` (named groups: `cycles`, `instr`, `ipc`, stall counts, …); `key`
picks the number that decides slower / same / faster. Each case's output comes from the
regression format (TORV: the case's `.sim.log`; exitcode: its log), or from `log = "...{case}..."`.

```bash
eda-agent perf --project projects/torv.toml                  # table per case, kept in runs/perf/
eda-agent perf-baseline runs/perf/torv_<time>.json --by NAME   # a person makes it the baseline
```

"Slower" needs a reference, and like the SDC it is a human decision: the baseline
(`projects/<name>.perf-baseline.json`, case → expected value, tolerance) is written only by
`eda-agent perf-baseline`; no MCP tool can touch it, and `perf` records its sha256. Tightening
is recorded as is; anything that loosens it (a case slower, a larger tolerance, a dropped case)
needs `--allow-loosen --reason`, and the old baseline stays in its `history`. `perf` is E2:
numbers for these programs in RTL simulation, nothing about clock frequency or other workloads.
