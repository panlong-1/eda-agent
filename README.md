# eda-agent

An evidence-gated agent for digital chip design. It drives open-source EDA tools (Verilator; Yosys,
OpenSTA and LibreLane in the LibreLane container) through an MCP server, and every tool answers
with a typed envelope that says **what the check proved and what it did not**: an evidence level
(E0 lint … E2 simulation … E3 mutation-scored tests … E4 synthesis estimate … E5 signoff), a
`claim_scope` and a `residual_risk`. Before a run ends, a plain-code evidence gate checks the
agent's report against those results, so "lint clean" never becomes "works" and "regression
passes" never becomes "the tests would catch a bug". An independent reviewer (configurable
independence I0–I3, plus a rule-based reviewer) reads the results, and a person approves or
rejects the run from a receipt that shows every Fatal / Error / Warning item first. Nothing in
`src/` is specific to one design: a design is one TOML file.

## Status

Layer by layer, on the TORV rv32m SoC (the vertical slice every feature is tried on first):

| layer | done | not yet |
|---|---|---|
| lint (E0) | Verilator lint with parsed diagnostics; fixed RTL rules (blocking/non-blocking, width truncation, latches, multiple drivers, reset polarity) | other linters (planned through import plug-ins) |
| regression, perf (E2) | the project's own regression read as `torv`, `junit` or `exitcode` (+ the testbench's own summary line); cycle counters per case against a baseline a person approved; gate R7 | coverage; cocotb and UVM log formats |
| mutation, write-tests (E3, mutation-scored) | source-level mutants (avl-core) run in copies, equivalence check, `perf_killed`; a fixed loop in which a model writes tests that `score_tests` must see kill the mutant twice; gate R6 | one module per mutate run; a new test enters the regression only after a person applies the patch |
| synth, pre-PnR STA (E4) | Yosys + OpenSTA per corner under the project's SDC (hash recorded), `check_sdc`; gate R5; IHP SG13G2 preset | other PDK presets; it stays an estimate before placement |
| LibreLane signoff (E5-partial) | `flow_report` / `flow_run`: fixed signoff rules (setup, hold, propagated clock, DRC, LVS, antenna), comparisons, run history; gate R8; flow adapters | gate-level simulation with post-route SDF and VCD power, so no full E5; ORFS and commercial adapters |
| review, human gate, receipts | rule-based and I0–I3 model reviewers; `approve` / `reject` / `pending` with one Fatal / Error / Warning policy; receipts (.json/.md/.html) for runs, write-tests and imports; a local review page | a cross-vendor I3 needs a second provider's key |
| lineage, snapshot | a design snapshot on every result; one-design checks for results used together; LibreLane lineage (8 stages, `final/` copies checked) | imported reports name their RTL only with `--rtl-manifest` |
| plug-ins | importers and flow adapters as entry points; contract tests; human-set waivers and targets; gate R9 | commercial importers live in a private package, not here |

## Results so far

- **Designs.** TORV (rv32m SoC, regression format `torv`, synthesis, timing and RTL-to-GDS on IHP
  SG13G2) and PicoRV32 (format `exitcode` with pass/fail patterns), both in `projects/`; a third
  design, a FuseSoC-based accelerator built from PULP IP, was connected with `init` and `doctor`
  (its config is not in this repository).
- **Mutation on TORV.** One mutate run of `rtl/torv_hazard_unit.sv`, 16 scored mutants: the
  regression kills 75 % of them by results, 94 % counting cycle-count changes. The last gap,
  mutant 3 (the `op` field cut from 7 to 6 bits, which only adds stalls), was closed by a
  model-written timing test (`kind=perf`) that `score_tests` saw kill it in 2 of 2 runs and pass
  2 of 2 on the unmodified design: kill rate with perf 94 % → 100 %, approved by a person.
  Receipt: [docs/examples/m25_torv_write_tests.receipt.md](docs/examples/m25_torv_write_tests.receipt.md).
- **RTL-to-GDS on TORV.** A fresh `flow_run` (`m4_e5_1`) gives signoff FAIL on setup only:
  WNS −0.725 ns at the slow corner (26 endpoints), hold +0.101 ns, DRC (Magic, KLayout) 0, LVS
  clean, antenna 0, 18,824 cells, die 508,267 µm², lineage complete over 8 stages. Every compared
  metric equals the earlier run `full_p10_fwd` (delta 0, asserted by `tests/eda/test_flow_torv.py`).
  Receipt: [docs/examples/m4_torv_e5_1.receipt.md](docs/examples/m4_torv_e5_1.receipt.md).

## Quickstart

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # add ,mutation for mutate
.venv/bin/pytest -q -m "not eda"                               # unit tests, no tools or API needed

.venv/bin/eda-agent init ~/project/mydesign --out projects/mydesign.toml   # a DRAFT: read it, fix CHECK lines
.venv/bin/eda-agent doctor --project projects/mydesign.toml --provider deepseek
.venv/bin/eda-agent check  --project projects/mydesign.toml --provider deepseek   # lint + one case
.venv/bin/eda-agent review-ui                                  # open the URL it prints, or:
.venv/bin/eda-agent approve <run_id>                           # what approve needs is in the receipt
```

`check` needs the provider's API key in the environment (`DEEPSEEK_API_KEY`, or
`ANTHROPIC_API_KEY` with `--provider anthropic`). Each run writes `runs/<id>.jsonl` (the whole
trace) and a receipt `runs/<id>.receipt.json`, rendered as `.html` and `.md`.

## Documentation

- [docs/usage.md](docs/usage.md): every command, a second design, adding a project
- [docs/project-config.md](docs/project-config.md): the project TOML, incl. `[flow]`, with links for `[mutation]`, `[tests]`, `[perf]`, `[pnr]`
- [docs/evidence-gate.md](docs/evidence-gate.md): evidence levels E0–E5 and gate rules R1–R11
- [docs/memory.md](docs/memory.md): lessons store and cited retrieval (hints, never evidence), context compaction
- [docs/review-and-approval.md](docs/review-and-approval.md): reviewers I0–I3, the human gate, Fatal / Error / Warning, `review-ui`, reading a receipt
- [docs/mutation-and-tests.md](docs/mutation-and-tests.md): `mutate`, `write-tests`, `perf`
- [docs/failure-localisation.md](docs/failure-localisation.md): `hierarchy`, `trace_driver`, `trace_load`, `waveform_query`; the trace chain and waveform in the review page
- [docs/flow-signoff.md](docs/flow-signoff.md): `flow_report`, `flow_run`, signoff rules, run history, flow adapters
- [docs/lineage.md](docs/lineage.md): design snapshots and flow lineage
- [docs/plugins.md](docs/plugins.md): importers and flow adapters
- [docs/architecture.md](docs/architecture.md): source layout and the one-way layers
- [docs/related-work.md](docs/related-work.md): what we borrowed, and where we differ
- [docs/examples/](docs/examples/): real receipts (M1 PicoRV32 and TORV, M2 TORV end to end, M2.5 TORV write-tests, M4 TORV RTL-to-GDS)

## Roadmap

Done: M1 (case descriptions, regression formats, evidence gate, receipts), M1.1 (tool versions,
command allowlist), M1.2 (`init`, `doctor`, `check`, FuseSoC/LibreLane inputs), M2 (synthesis and
pre-PnR STA, `check_sdc`, reviewers, human gate), M2.5 (mutation, `perf`, `write-tests`, gates
R6/R7), M4 step 1 (LibreLane `flow_report`/`flow_run`, signoff rules, gate R8, lineage, run
history, flow adapters), plug-ins (importers, gate R9).

Next: setup diagnosis and failure localisation (logs, waveforms), a lessons store; a vertical acceptance run on TORV; a comparison
experiment; later gate-level simulation for a full E5, more PDKs and simulators, and commercial
tools through adapters and importers.

## License

Apache-2.0, see [LICENSE](LICENSE).
