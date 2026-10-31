# eda-agent

An evidence-gated agent for digital chip design. It drives open-source EDA tools
(Verilator today; Yosys and LibreLane next) through an MCP server, and every tool
answers with a typed envelope that says **what the check proved and what it did not**.

Status: **M0** — tool layer (lint, regress) + a hand-written, model-agnostic agent loop.

## Why

A passing check only proves the property the checker looked at. "Lint clean" is not
"works", and "regression passes" is not "the tests would catch a bug". The agent
is built so that it cannot blur these: each result carries an evidence level
(E0 lint … E2 simulation … E5 signoff), a `claim_scope` and a `residual_risk`.

## Layout

```
src/eda_agent/
  envelope.py        ToolResult: status, evidence_level, claim_scope, metrics, diagnostics, sha256
  projects.py        per-design TOML: env script, filelists, regression entry point
  tools/lint.py      Verilator --lint-only, parsed diagnostics (E0)
  tools/regress.py   the project's own regression, parsed summary (E2)
  mcp_server.py      MCP server (stdio) exposing lint / list_regress_cases / regress
  agent/model.py     ModelAdapter protocol; AnthropicAdapter; OpenAICompatAdapter (DeepSeek);
                     ScriptedAdapter for tests
  agent/loop.py      tool loop with budgets: turns, tool calls, 2-strike retry stop; JSONL trace
  agent/mcp_client.py  connects the loop to the MCP server
  cli.py             eda-agent run ...
projects/torv.toml   TORV rv32m SoC
```

## Use

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q                      # unit tests + TORV integration (needs Verilator)

# MCP server alone (e.g. for Claude Code or another MCP client)
.venv/bin/eda-mcp --project projects/torv.toml

# the agent on Claude (needs ANTHROPIC_API_KEY)
.venv/bin/eda-agent run --project projects/torv.toml "Lint the design and run the smoke case"

# the agent on DeepSeek (needs DEEPSEEK_API_KEY)
.venv/bin/eda-agent run --provider deepseek --project projects/torv.toml "Lint the design and run the smoke case"
```

Each run writes `runs/<timestamp>.jsonl`: every model turn, tool call, token count and
wall time.

## Roadmap

M1 evidence gate + receipts, synth/regress tools · M2 independent reviewer with
configurable independence (I0–I3) · M3 mutation-based evaluation (mutbench) ·
M4 LibreLane flow tool.
