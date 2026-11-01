# Using eda-agent: every command

The project config is described in [project-config.md](project-config.md); what each check means in the topic pages linked from the [README](../README.md#documentation).

## Install, test, run

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

# ... with an independent review (rule-based + an I2 model reviewer), then a person decides
.venv/bin/eda-agent run --provider deepseek --review I2 --project projects/torv.toml \
    "Lint, run smoke, synthesize and estimate timing at the project clock"
.venv/bin/eda-agent approve <run_id>                       # or:
.venv/bin/eda-agent reject <run_id> --reason "slow corner too close"

# review a finished run again, e.g. at I3 (another model), without re-running the author
.venv/bin/eda-agent review <run_id> --review I3 --project projects/torv.toml

# re-render a receipt's .md and .html from its .json (review summary on top)
.venv/bin/eda-agent receipt <run_id> [--print] [--out-dir DIR]
# ... and add a model-written summary (needs the API key)
.venv/bin/eda-agent receipt <run_id> --narrate

# decide by clicking: a local page with Approve / Pending / Reject (open the URL it prints)
.venv/bin/eda-agent review-ui
```

Each run writes `runs/<id>.jsonl` (every model turn, tool call, gate decision, token
count and wall time) and a receipt `runs/<id>.receipt.json`, rendered for people as
`runs/<id>.receipt.html` and `.md` (see [Reading a receipt](review-and-approval.md#reading-a-receipt)): task, model,
stop reason and gate status, the checks run (args, status, evidence level,
claim_scope, artifact sha256), cases that were described or listed but not run,
the final claims, residual risk, tokens and wall time. The CLI prints the receipt path
and exits 0 only when the gate passed.

## A second design: PicoRV32

```bash
git clone https://github.com/YosysHQ/picorv32 ~/project/picorv32
.venv/bin/eda-agent run --provider deepseek --project projects/picorv32.toml \
    "Lint the core and run the regression; report what passed"
```

Needs Verilator, `make` and `riscv64-unknown-elf-gcc` (the firmware is built for
rv32imc with `TOOLCHAIN_PREFIX=riscv64-unknown-elf-`). Nothing in `src/` is specific to
PicoRV32: `projects/picorv32.toml` names the top, the RTL file, the `make test_verilator`
command and the pass/fail patterns, and describes its one case (`firmware`).

## Adding a project

Start from a draft instead of an empty file:

```bash
.venv/bin/eda-agent init ~/project/mydesign --out projects/mydesign.toml   # writes a DRAFT
.venv/bin/eda-agent doctor --project projects/mydesign.toml --provider deepseek
.venv/bin/eda-agent doctor --project projects/mydesign.toml --deep     # one real run per stage
.venv/bin/eda-agent check  --project projects/mydesign.toml --provider deepseek    # lint + one case
.venv/bin/eda-agent check  --project projects/mydesign.toml --provider deepseek --full
```

`init` reads what the repository already has: a FuseSoC `.core` (lint top and options; it
runs `fusesoc run --setup --target lint` so Verilator gets FuseSoC's own command file), a
LibreLane `config.yaml` (top, files, clock, SDC), `*.f` filelists, Makefile test targets or a
regress script, and the pass/fail lines the testbench prints. Every value in the draft says
where it came from; the ones marked `CHECK` are guesses a person must confirm. The draft is
not used until a person has read it: the config decides what each check means. `doctor`
then checks that every tool, file, container image and API key it names is there (key
values are never printed). Put the file in the design directory as `eda-agent.toml` and
`--project` can be left out; `~/.config/eda-agent/config.toml` holds personal defaults such
as `provider = "deepseek"`, `review = "I2"` and `narrate = true` (a model-written summary in
each receipt).

## All commands

`--project` may be left out when the current directory (or a parent) has `eda-agent.toml`.
`eda-agent <command> --help` lists every option.

| command | what it does | more |
|---|---|---|
| `eda-agent init <design dir> [--out FILE]` | draft a project config from what the repository holds | [above](#adding-a-project) |
| `eda-agent doctor [--project P] [--provider deepseek]` | are the tools, files, image and API key all there? | [above](#adding-a-project) |
| `eda-agent doctor --deep [--stage sim\|synth\|pnr] [--no-setup]` | per stage: prerequisites, the approved `[setup]` steps if needed, one minimal real run; what is missing and how to fix it | [project-config.md](project-config.md#setup-diagnosis-and-setup-steps-setup) |
| `eda-agent check [--project P] [--full]` | a fixed task: lint + one case (`--full`: whole regression + synth) | [above](#adding-a-project) |
| `eda-agent run "<task>" [--project P] [--provider ...] [--review rules\|I0..I3] [--narrate]` | one task, then the receipt and the human gate | [review-and-approval.md](review-and-approval.md) |
| `eda-agent review <run_id> [--review I3]` | review a finished run again | [review-and-approval.md](review-and-approval.md#independent-review-i0-i3) |
| `eda-agent receipt <run_id> [--print] [--out-dir DIR] [--narrate]` | re-render a receipt's .md and .html | [review-and-approval.md](review-and-approval.md#reading-a-receipt) |
| `eda-agent review-ui [--port 8799] [--runs-dir runs]` | decide by clicking, on localhost | [review-and-approval.md](review-and-approval.md#deciding-by-clicking-eda-agent-review-ui) |
| `eda-agent approve <run_id> [--reason R] [--override]` / `reject <run_id> --reason R` / `pending <run_id> --note N` | the human gate | [review-and-approval.md](review-and-approval.md#human-gate) |
| `eda-agent mutate --file F [--count N] [--confirm-full]` | which mutants the regression misses (no model) | [mutation-and-tests.md](mutation-and-tests.md#mutation-runs-mutation) |
| `eda-agent write-tests --mutate-run DIR --mutants N [N ...]` | new tests that kill surviving mutants | [mutation-and-tests.md](mutation-and-tests.md#new-tests-against-surviving-mutants-tests-write-tests) |
| `eda-agent perf [--cases ...]`, `eda-agent perf-baseline <result>` | cycle counts; a person records the baseline | [mutation-and-tests.md](mutation-and-tests.md#performance-perf) |
| `eda-agent flow-report <run> [--baseline B] [--with-synth]` | read a finished LibreLane run (no model) | [flow-signoff.md](flow-signoff.md) |
| `eda-agent flow-history` | all LibreLane runs in one table, knob effects, config lessons | [flow-signoff.md](flow-signoff.md) |
| `eda-agent flow-run [--tag T] [--dry-run]` | LibreLane RTL-to-GDS on a copy of the design (about an hour) | [flow-signoff.md](flow-signoff.md) |
| `eda-agent import <dir> [--as NAME] [--all] [--rtl-manifest FILE]` | a report made elsewhere, through an importer plug-in | [plugins.md](plugins.md) |
| `eda-agent plugins list\|check` | installed plug-ins; their contract tests | [plugins.md](plugins.md) |
| `eda-agent standard waive\|unwaive\|target\|show ...` | human-set waivers and targets for imported checks | [plugins.md](plugins.md) |
| `eda-mcp --project P` | the MCP server alone, for any MCP client | [architecture.md](architecture.md) |
