# eda-agent

An evidence-gated agent for digital chip design. It drives open-source EDA tools
(Verilator today; Yosys and LibreLane next) through an MCP server, and every tool
answers with a typed envelope that says **what the check proved and what it did not**.

Status: **M1** — case descriptions, pluggable regression result formats, a second
design (PicoRV32), an evidence gate on the final report, and a receipt for every run.

## Why

A passing check only proves the property the checker looked at. "Lint clean" is not
"works", and "regression passes" is not "the tests would catch a bug". The agent
is built so that it cannot blur these: each result carries an evidence level
(E0 lint … E2 simulation … E5 signoff), a `claim_scope` and a `residual_risk`, and
a plain-code gate checks the final report against those results before the run ends.

## Layout

```
src/eda_agent/
  core/                  shared parts; depend on no tool and no model
    envelope.py          ToolResult: status, evidence_level, claim_scope, metrics, diagnostics, sha256
    project.py           per-design TOML: env script, filelists, regression entry point, case list
    proc.py              subprocess with a hard timeout that also kills child processes
  tools/                 one EDA capability per file; each only returns an envelope (facts)
    lint.py              Verilator --lint-only, parsed diagnostics (E0)
    describe.py          what a regression case covers: source file + its leading comment
    regress.py           the project's own regression (E2), read by one of formats/
    formats/             torv.py | junit.py | exitcode.py: run cases, read results, claim_scope
  server/mcp_server.py   MCP server (stdio): registers lint / list_regress_cases / describe_case / regress
  agent/                 the agent runtime; reaches the tools only through MCP
    models/              base.py (ModelAdapter protocol), anthropic.py, openai_compat.py (DeepSeek),
                         scripted.py (for tests)
    loop.py              tool loop with budgets: turns, tool calls, 2-strike retry stop; JSONL trace
    gate.py              evidence gate on the final report (no LLM), up to 2 rewrites
    receipt.py           runs/<id>.receipt.json + .md at the end of every run
    mcp_client.py        connects the loop to the MCP server
  cli.py                 eda-agent run ...
projects/torv.toml       TORV rv32m SoC (format torv)
projects/picorv32.toml   YosysHQ PicoRV32 (format exitcode + pass/fail regex)
src/eda_agent/prompts/system.v1.md  system prompt, shipped with the package; its name is recorded
                         in every receipt (--prompt picks another, or a path to a draft .md)
tests/unit/              offline and free, a few seconds
tests/eda/               marker "eda": needs Verilator and the design checkouts
tests/fixtures/          recorded tool output and model replies (for later tests)
docs/examples/           two real run receipts (PicoRV32, TORV hazard task)
runs/                    traces, receipts and logs of your runs (not in git)
```

Dependencies point one way: `core` ← `tools` ← `server`, and `core` ← `agent`. The agent
never imports `tools`, so the MCP server can serve any other agent too. Nothing in `src/`
is specific to one design; that lives in `projects/*.toml`.

## Use

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q -m "not eda"         # unit tests, no tools or API needed
.venv/bin/pytest -q                      # + TORV and PicoRV32 integration (skipped if missing)

# MCP server alone (e.g. for Claude Code or another MCP client)
.venv/bin/eda-mcp --project projects/torv.toml

# the agent on Claude (needs ANTHROPIC_API_KEY)
.venv/bin/eda-agent run --project projects/torv.toml "Lint the design and run the smoke case"

# the agent on DeepSeek (needs DEEPSEEK_API_KEY)
.venv/bin/eda-agent run --provider deepseek --project projects/torv.toml "Lint the design and run the smoke case"
```

Each run writes `runs/<id>.jsonl` (every model turn, tool call, gate decision, token
count and wall time) and a receipt `runs/<id>.receipt.md` / `.json`: task, model,
stop reason and gate status, the checks run (args, status, evidence level,
claim_scope, artifact sha256), cases that were described or listed but not run,
the final claims, residual risk, tokens and wall time. The CLI prints the receipt path
and exits 0 only when the gate passed.

### A second design: PicoRV32

```bash
git clone https://github.com/YosysHQ/picorv32 ~/project/picorv32
.venv/bin/eda-agent run --provider deepseek --project projects/picorv32.toml \
    "Lint the core and run the regression; report what passed"
```

Needs Verilator, `make` and `riscv64-unknown-elf-gcc` (the firmware is built for
rv32imc with `TOOLCHAIN_PREFIX=riscv64-unknown-elf-`). Nothing in `src/` is specific to
PicoRV32: `projects/picorv32.toml` names the top, the RTL file, the `make test_verilator`
command and the pass/fail patterns, and describes its one case (`firmware`).

### Adding a project

A project is one TOML file. `[lint]` gives the top and the files (`filelists` and/or
`files`). `[regress]` gives the command and how to read its result:

| `format` | reads | per-case detail | config |
|---|---|---|---|
| `torv` | `Result directory: <dir>` in the output, `<dir>/<summary_file>` rows `name,STATUS,detail` | yes | `case_list`, `case_flag`, `summary_file` |
| `junit` | a JUnit XML file the run writes (rejected if not rewritten by this run) | yes, per `<testcase>` | `junit_file` |
| `exitcode` | the exit status of one command per case, optionally confirmed by `pass_regex` / `fail_regex` | no: one status may cover many tests | `pass_regex`, `fail_regex` |

Cases come from a `case_list` file (first column = name; the first later column that
is an existing file is taken as the source for `describe_case`) or from inline
`[[regress.cases]]` entries with `name`, `args`, `source` and `description`. Each format's
`claim_scope` and `residual_risk` say what it can and cannot tell.

Two optional top-level keys make results repeatable and keep the tools on a short leash:

- `[versions]` maps a tool name to the command that prints its version, e.g.
  `verilator = ["verilator", "--version"]`. Every regress result records them in
  `tool_versions` (lint records Verilator's), and the receipt lists them.
- `allowed_commands` adds programs a check may start. Tools only run commands from the
  project config, never text from the model, and `core/proc.py` refuses anything that is
  neither an open-source EDA program it knows (verilator, make, yosys, iverilog, sby, …)
  nor an executable script inside the project root (`error_class: not_allowed`).

Every trace (`runs/<id>.jsonl`) keeps each tool result whole, so a finished run can be
replayed through a changed gate.

## Evidence gate

When the model ends its turn, `agent/gate.py` checks the report against the tool
results of this run. All rules are in that one file:

- **R1 citations.** Every status/evidence-level pair the report cites ("regress: pass (E2)")
  and every "N passed / N failed" count must come from a tool result of this run. A level
  named as a requirement ("that would need E3") is not a citation.
- **R2 strength.** Words that claim functional correctness (handles, correct, verified,
  works, bug-free, … and Chinese equivalents such as 正确, 已验证) need a passing result at
  E3 or higher, and the same sentence must name that result (its tool or a case it passed),
  so E3 on one check cannot vouch for another. With E2 the report may only say which cases passed. A negation earlier in
  the same clause ("does not show … correct") makes it a stated limit, not a claim, and so
  are the list items that follow it ("not … handled, correct, or bug-free"); a clause with
  more than a list connector before the word ("…, but the design is correct") is a new claim.
- **R3 residual risk.** The report must have a section headed "Residual risk" with content.
- **R4 skipped cases.** A case the agent read with `describe_case` but did not run must be
  named in that section, so that leaving out a relevant test is stated, not silent.

A rejected report goes back to the model with the reasons, at most twice; then the run
stops with `stop_reason: gate_rejected` and `status: pending` for a human to judge. The
checks are deliberately conservative and explainable: they can reject a fair sentence,
which costs a rewrite, but they do not try to understand the design.

## Related work: what we borrowed

Ideas only, unless marked as a dependency; no code was copied.

| Project | What we took | Where we differ |
|---|---|---|
| [MCP4EDA](https://github.com/NellyW8/MCP4EDA), [mcp-verilog](https://github.com/zesun33/mcp-verilog) | EDA tools behind an MCP server; record tool versions so a result can be repeated | their tools return raw logs; ours return an envelope with an evidence level, claim scope and residual risk |
| [fpgaZeroMCP](https://github.com/lcapossio/fpgaZeroMCP) | only a fixed set of EDA commands may run | the allowlist sits under project configs, not under model input, which never reaches a command line |
| [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) | a small hand-written loop with step and cost limits and a linear, fully logged trajectory | stop rules from the methodology: the same tool failing twice ends the run; a gate checks the final report |
| [SWE-agent](https://github.com/SWE-agent/SWE-agent) | short, stable tool feedback written for a model to read | |
| [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) guardrails | an output check that can stop a run and say why | our gate is plain code over this run's tool results, and sends the reasons back for a bounded number of rewrites |
| [VerilogCoder](https://github.com/NVlabs/VerilogCoder), [MAGE](https://github.com/stable-lab/MAGE) | (planned, M3) trace a failing signal back to its drivers; debug with checkpoints | |
| [wave-mcp](https://github.com/Tencent/wave-mcp), [wavepeek](https://github.com/kleverhq/wavepeek) | (planned, M3, dependency) read waveforms | wrapped as two diagnostic tools that report values, not causes |
| [MCY](https://github.com/YosysHQ/mcy) | (planned, M2.5, dependency) mutants to score generated tests | |

## Roadmap

M1 case descriptions, evidence gate, receipts · M1.1 tool versions, command allowlist,
whole tool results in traces · M2 Yosys synth tool, independent reviewer with configurable
independence (I0–I3), human gate · M2.5 test generation scored by mutants · M3
simulation-failure localisation (logs, waveforms), lessons store · M4 LibreLane flow tool
(PPA, DRC/LVS).
