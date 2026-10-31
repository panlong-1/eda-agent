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
  envelope.py        ToolResult: status, evidence_level, claim_scope, metrics, diagnostics, sha256
  projects.py        per-design TOML: env script, filelists, regression entry point, case list
  tools/lint.py      Verilator --lint-only, parsed diagnostics (E0)
  tools/describe.py  what a regression case covers: source file + its leading comment
  tools/regress.py   the project's own regression, result format torv | junit | exitcode (E2)
  mcp_server.py      MCP server (stdio): lint / list_regress_cases / describe_case / regress
  agent/model.py     ModelAdapter protocol; AnthropicAdapter; OpenAICompatAdapter (DeepSeek);
                     ScriptedAdapter for tests
  agent/loop.py      tool loop with budgets: turns, tool calls, 2-strike retry stop; JSONL trace
  agent/gate.py      evidence gate on the final report (no LLM), up to 2 rewrites
  agent/receipt.py   runs/<id>.receipt.json + .md at the end of every run
  agent/mcp_client.py  connects the loop to the MCP server
  cli.py             eda-agent run ...
projects/torv.toml      TORV rv32m SoC (format torv)
projects/picorv32.toml  YosysHQ PicoRV32 (format exitcode + pass/fail regex)
```

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

## Evidence gate

When the model ends its turn, `agent/gate.py` checks the report against the tool
results of this run. All rules are in that one file:

- **R1 citations.** Every status/evidence-level pair the report cites ("regress: pass (E2)")
  and every "N passed / N failed" count must come from a tool result of this run. A level
  named as a requirement ("that would need E3") is not a citation.
- **R2 strength.** Words that claim functional correctness (handles, correct, verified,
  works, bug-free, … and Chinese equivalents such as 正确, 已验证) need a passing result at
  E3 or higher. With E2 the report may only say which cases passed. A negation earlier in
  the same clause ("does not show … correct") makes it a stated limit, not a claim.
- **R3 residual risk.** The report must have a section headed "Residual risk" with content.
- **R4 skipped cases.** A case the agent read with `describe_case` but did not run must be
  named in that section, so that leaving out a relevant test is stated, not silent.

A rejected report goes back to the model with the reasons, at most twice; then the run
stops with `stop_reason: gate_rejected` and `status: pending` for a human to judge. The
checks are deliberately conservative and explainable: they can reject a fair sentence,
which costs a rewrite, but they do not try to understand the design.

## Roadmap

M1 case descriptions, evidence gate, receipts · M2 Yosys synth tool, independent
reviewer with configurable independence (I0–I3), human gate · M3 simulation-failure
localisation (logs, waveforms), lessons store · M4 LibreLane flow tool (PPA, DRC/LVS).
