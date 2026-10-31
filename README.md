# eda-agent

An evidence-gated agent for digital chip design. It drives open-source EDA tools
(Verilator; Yosys and OpenSTA in the LibreLane container) through an MCP server, and every tool
answers with a typed envelope that says **what the check proved and what it did not**.

Status: **M2** — synthesis with a pre-PnR timing estimate under the project's SDC (E4),
an SDC completeness check, gate rule R5 for timing claims, an independent reviewer with
configurable independence (I0–I3) plus a rule-based reviewer, and a human gate: a run that
passes the evidence gate waits in `awaiting_approval` until a person approves it.
(M1: case descriptions, regression result formats, PicoRV32, evidence gate, receipts.)

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
    flow_sources.py      [flow] from a LibreLane config; PDK presets (IHP SG13G2)
    settings.py          finds eda-agent.toml; personal defaults in ~/.config/eda-agent/config.toml
  onboard/               connecting a new design (no model involved)
    detect.py, init.py   eda-agent init: guess a draft config, each value with where it came from
    doctor.py            eda-agent doctor: are the tools, files, image and API key all there?
  tools/                 one EDA capability per file; each only returns an envelope (facts)
    lint.py              Verilator --lint-only, parsed diagnostics (E0)
    describe.py          what a regression case covers: source file + its leading comment
    regress.py           the project's own regression (E2), read by one of formats/
    formats/             torv.py | junit.py | exitcode.py: run cases, read results, claim_scope
    synth.py             synth: Yosys + OpenSTA per corner in the project's container (E4);
                         check_sdc: OpenSTA check_setup on the netlist (facts, E0)
    rtl_rules.py         fixed RTL rules: Verilator BLKSEQ/COMBDLY/WIDTHTRUNC/LATCH/MULTIDRIVEN
                         + a reset-polarity text scan (E0)
  server/mcp_server.py   MCP server (stdio): lint / list_regress_cases / describe_case / regress /
                         rtl_rules / synth / check_sdc
  agent/                 the agent runtime; reaches the tools only through MCP
    models/              base.py (ModelAdapter protocol), anthropic.py, openai_compat.py (DeepSeek),
                         scripted.py (for tests)
    loop.py              tool loop with budgets: turns, tool calls, 2-strike retry stop; JSONL trace
    gate.py              evidence gate on the final report (no LLM), up to 2 rewrites
    receipt.py           runs/<id>.receipt.json + .md + .html at the end of every run
    receipt_view.py      the review summary on top of a receipt (plain code, en/zh) and the HTML page
    receipt_tables.py    the data tables of a receipt (per check, findings, gate, cost)
    receipt_llm.py       optional model-written summary, checked by the evidence gate
    reviewer.py          independent review I0-I3 (context assembled by code) + rule-based reviewer
    human_gate.py        awaiting_approval / approve / reject, recorded in the receipt
    mcp_client.py        connects the loop to the MCP server
  cli.py                 eda-agent init | doctor | check | run | review | approve | reject
projects/torv.toml       TORV rv32m SoC (format torv)
projects/picorv32.toml   YosysHQ PicoRV32 (format exitcode + pass/fail regex)
src/eda_agent/prompts/  system.v2.md (default; v1 kept), reviewer.v1.md, presets.v1.toml (the fixed
                         tasks of `check`); names and versions are recorded in every receipt
tests/unit/              offline and free, a few seconds
tests/eda/               marker "eda": needs Verilator and the design checkouts; the synth test also
                         needs docker, the LibreLane image and the IHP PDK (skipped otherwise)
tests/fixtures/          recorded tool output and model replies (for later tests)
docs/examples/           real run receipts: M1 (PicoRV32, TORV hazard task); M2 TORV end to end
                         (lint, smoke, synth+STA, check_sdc, reviews rules/I2/I3) awaiting approval,
                         and a copy approved only to demonstrate the human gate
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

# ... with an independent review (rule-based + an I2 model reviewer), then a person decides
.venv/bin/eda-agent run --provider deepseek --review I2 --project projects/torv.toml \
    "Lint, run smoke, synthesize and estimate timing at the project clock"
.venv/bin/eda-agent approve <run_id>                       # or:
.venv/bin/eda-agent reject <run_id> --reason "slow corner too close"

# review a finished run again, e.g. at I3 (another model), without re-running the author
.venv/bin/eda-agent review <run_id> --review I3 --project projects/torv.toml

# re-render a receipt's .md and .html from its .json, with the review summary in Chinese
.venv/bin/eda-agent receipt <run_id> --lang zh [--all-langs] [--print] [--out-dir DIR]
# ... and add a model-written summary in both languages (needs the API key)
.venv/bin/eda-agent receipt <run_id> --narrate --all-langs
```

Each run writes `runs/<id>.jsonl` (every model turn, tool call, gate decision, token
count and wall time) and a receipt `runs/<id>.receipt.json`, rendered for people as
`runs/<id>.receipt.html` and `.md` (see [Reading a receipt](#reading-a-receipt)): task, model,
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

Start from a draft instead of an empty file:

```bash
.venv/bin/eda-agent init ~/project/mydesign --out projects/mydesign.toml   # writes a DRAFT
.venv/bin/eda-agent doctor --project projects/mydesign.toml --provider deepseek
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
as `provider = "deepseek"`, `review = "I2"`, `lang = "zh"` (receipt summaries in Chinese) and
`narrate = true` (a model-written summary in each receipt).

Left out, a project gets defaults: lint flags `-Wall -Wno-fatal`, `[versions]` with
Verilator, regress format `exitcode` with no case flag, and with no case list one case,
`all`, that runs the whole command. The smallest config is seven lines:

```toml
name = "mycore"
root = "~/project/mycore"
[lint]
top = "mycore"
files = ["rtl/mycore.v"]
[regress]
cmd = ["make", "test"]
```

For a FuseSoC project `[lint]` can take `command_files` (Verilator `-f` files; lint runs
from the file's directory, because its paths are relative to it), `external` (path prefixes
of third-party code: their warnings are left out and counted in `external_warnings_dropped`)
and `copy_of_root` (FuseSoC's copy of the core's own files under `build/`, mapped back to the
original files so their warnings stay ours and point at the real lines).

A project is one TOML file. `[lint]` gives the top and the files (`filelists` and/or
`files`). `[regress]` gives the command and how to read its result:

| `format` | reads | per-case detail | config |
|---|---|---|---|
| `torv` | `Result directory: <dir>` in the output, `<dir>/<summary_file>` rows `name,STATUS,detail` | yes | `case_list`, `case_flag`, `summary_file` |
| `junit` | a JUnit XML file the run writes (rejected if not rewritten by this run) | yes, per `<testcase>` | `junit_file` |
| `exitcode` | the exit status of one command per case, optionally confirmed by `pass_regex` / `fail_regex` | no: one status may cover many tests | `pass_regex`, `fail_regex` |

An `exitcode` project can also name the testbench's own summary line with `summary_regex`,
using named groups `passed`, `failed`, `total` (e.g. `scenario \S+: (?P<passed>\d+)/(?P<total>\d+)
cases ok`). The counts then decide too: exit 0 and the pass pattern are not enough when the
testbench reports a failure or prints no summary. They are reported as `subtests_passed` /
`subtests_failed` / `subtests_total`, the gate accepts them as counts a report may cite, and
the claim scope says they are the testbench's own count. `init` proposes the regex from a
`$display("... %0d/%0d cases ok ...")` it finds in the testbench.

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

### Synthesis and timing: `[flow]`

`synth` runs Yosys (`read_slang` or `read_verilog -sv`, `synth -flatten`, `dfflibmap`, `abc`
with fanout buffering) and then OpenSTA on the netlist under the project's own SDC, once per
Liberty corner, inside the container image named in `[flow] image` (docker, as the calling
user, no network, the design and PDK mounted read-only). It reports cells, area, latches,
setup WNS/TNS, hold WNS and the worst path per corner; the worst corner decides `status`.
The envelope records the SDC's sha256 (also as an artifact), the image digest and the
Yosys/OpenSTA versions, and its `claim_scope` says: an estimate under this SDC, before
placement and routing. `check_sdc` runs OpenSTA `check_setup` (clocks on the configured
port, unconstrained endpoints, loops) and reports facts at E0: a complete SDC raises no level.

The SDC is a design input written by people; the tools only read it. `[flow]` names it with
`top`, `files`/`filelists`, `sdc`, `clock_port`, `clock_period` (also handed to the SDC as
`$::env(CLOCK_PERIOD)`, as LibreLane does), `liberty`, `[flow.corners]`, `image`, `pdk_root`
and PDK details (`dont_use_file`, tie and buffer cells, ABC driving cell and load). Most of this
need not be copied: `librelane = "librelane/config.yaml"` reads top, files, include dirs,
frontend, Booth multiplier, clock and SDC from the LibreLane config, and a known PDK
(`ihp-sg13g2` today) fills the library, corner and cell settings; keys written in `[flow]`
win. A test checks that these two lines give exactly the same setup as the hand-written
section in `projects/torv.toml`. For a FuseSoC design, `command_files` takes the synth
target's file list as a Verilator command file (`fusesoc run --setup --target synth --tool
verilator <core>`); the design's own files are read from the checkout through
`lint.copy_of_root`, because FuseSoC rewrites its copy for whichever target was set up last.
Library cells instantiated in the RTL (an integrated clock gate, say) come from the Liberty
file as blackboxes; any other unknown module still fails `hierarchy -check`. TORV's section mirrors its `librelane/config.yaml`; on TORV the estimate is within about 0.7 ns of
LibreLane's own pre-PnR STA per corner. A project without `[flow]` (PicoRV32 today) gets a
clean `not_configured` refusal. Containers come from an allowlist (`ghcr.io/librelane/librelane`
built in; `allowed_images` adds more) and may only start allowlisted programs.
When the SDC is not the design's own (a hand translation of other constraints, say), write
that in `[flow] sdc_origin = "..."`: every receipt copies it and the review summary asks
the approver to look at it.

## Evidence gate

When the model ends its turn, `agent/gate.py` checks the report against the tool
results of this run. All rules are in that one file:

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

A rejected report goes back to the model with the reasons, at most twice; then the run
stops with `stop_reason: gate_rejected` and `status: pending` for a human to judge. The
checks are deliberately conservative and explainable: they can reject a fair sentence,
which costs a rewrite, but they do not try to understand the design.

## Independent review

`--review` adds a review after the run; the program, not a model, assembles what the
reviewer sees, from the author's trace:

| level | the reviewer sees | tools |
|---|---|---|
| I0 | the author's whole history: task, turns, tool calls and results, final report | no |
| I1 | a fresh conversation with the final report only | no |
| I2 | the task and the tool results, none of the author's text or conclusions | may re-run the project's tools |
| I3 | as I2, on a different model | as I2 |

The reviewer first writes a checklist in a turn of its own, then goes through it and
writes findings (`[high|medium|low|info] … — evidence: …`) and a verdict (accept,
accept_with_findings, reject); a tool call before the checklist, or a missing section,
is marked `format_ok: false`. Whenever any review is asked for, a **rule-based reviewer**
(no model) also runs: the `rtl_rules` tool (blocking assignments in clocked blocks,
non-blocking in combinational logic, width truncation, latches, multiple drivers, reset
polarity against edge and name) plus plain checks of the author's results (errors, failing
cases, latches, timing not met, mixed SDC hashes). `--review rules` runs it alone. All
reviews go into the receipt.

**On I3 here.** Only a DeepSeek key is available, so I3 uses DeepSeek's other model
(`deepseek-v4-flash` reviewing `deepseek-v4-pro`, or the reverse). Two models of one vendor
likely share training data and blind spots, so this is weaker independence than a reviewer
from a different vendor; the receipt says so in the review's `note`. With an Anthropic key,
`--review-provider anthropic` gives a cross-vendor I3.

## Human gate

A run whose report passes the evidence gate does not end as `done`: its receipt has
`status: awaiting_approval` (the agent's own outcome is kept as `agent_status`) and an
`approval` record `pending`. Nothing waits on a prompt. A person decides later:

```bash
eda-agent approve <run_id> [--by NAME]
eda-agent reject  <run_id> --reason "..."
eda-agent approve <run_id> --override-gate --reason "..."   # a gate-rejected (pending) run
```

The receipt then records who decided (`--by`, else `git config user.name`, else the login
name), when, the reason, and the sha256 of the receipt content they decided on. Decisions
are final; a rejected run is fixed by a new run. `--no-approval` skips the gate (status `done`).

### Reading a receipt

The person who approves should not have to read tables of claim scopes, checklists and three
reviews to find out what they are deciding. So every rendered receipt (`.html`, and `.md`)
is laid out for that person, in English or Chinese:

1. **Review summary**, about one screen, written by plain code from the receipt's own data:
   - one line: how many checks passed, the gate, the reviewers' verdicts, how many items to look at;
   - the task, **what you decide** (or who already decided, and when), and the exact
     `approve` / `reject` commands for this run;
   - **look at these before approving**, sorted serious → warning → note: a reviewer that said
     reject, every high-severity finding (once, even if several reviewers found it), failing or
     skipped cases, a small setup or hold slack (under 10 % of the clock period, or under
     0.1 ns), timing not met or void, checks without tool versions, tool errors, gate rewrites
     and why, cases read but not run, testbench sub-test counts, a hand-made SDC
     (`sdc_origin`), an approval that overrode the gate, a receipt changed after its decision,
     a model-written summary the gate rejected;
   - each check with pass/fail, its evidence level in words (E0 "parses / structural facts",
     E2 "these tests passed", E4 "synthesis estimate", …) and how long it took; the highest
     level reached and what each level does and does not let you say; what was **not**
     checked; the gate, one line per reviewer with its worst finding, and the approval status.
2. **Summary written by a model** (optional, marked "AI"): see below.
3. **Data**, as tables: regression (cases asked/run/passed/failed/skipped, testbench sub-tests,
   failed cases); lint (warnings by code, by file, and the diagnostics kept); synthesis
   (cells, area, latches, library, clock, timing met, SDC and its hash), timing per corner
   (setup WNS/TNS, hold WNS/TNS), the worst path and the most used cells; the SDC check
   (clocks, unconstrained endpoints); plain numbers of any other tool; every reviewer
   finding (severity, reviewer, text, file:line); the gate's decisions and reasons; and the
   cost (turns, tool calls, tokens, wall time of the author, each reviewer and each summary).
4. **Details**: the whole receipt as before, unchanged (in the HTML, sections that open on a click).

The HTML page holds both languages, with an English / 中文 switch at the top (`--lang` picks the
one shown first); it is a single file with no scripts or external links, so it opens
offline. The `.md` is in `--lang`; `--all-langs` also writes `<id>.receipt.en.md` and `.zh.md`.
`--lang zh` (or `lang = "zh"` in the user config) puts Chinese first; tool output, file names,
findings and the agent's report stay as they were written. `approve` and `reject` re-render
the files; `eda-agent receipt <run_id>` re-renders any receipt without touching its `.json`.
Receipts keep each check's metrics, diagnostics (up to 50) and duration; for receipts written
before that, the tables take the numbers from the run's own trace when it is still there,
marked †.

**The model-written summary.** `--narrate` on `run`/`check`/`receipt` (or `narrate = true`)
asks a model (by default the run's own) for a short summary in the receipt's language. It gets
the receipt data only: no trace, no files, no tools, and not the approval status. Its text goes
through the same evidence gate as the agent's report (citations must match the run's results,
no functional-correctness words without E3/E5 evidence, timing claims must name the synth
result and its numbers, a residual-risk section that names the cases read but not run), with
one rewrite. The receipt keeps the text with provider, model, prompt
(`prompts/receipt_summary.v1.md`), the gate's decisions, cost and time under
`narratives.<lang>`. The page shows it in a box marked "AI", with whether it passed the gate,
and says the tables are the record; a rejected text is folded away and flagged. Narratives are
outside the sha256 a decision records, so one added later does not change what was decided
(the page says it was written after the decision). Without an API key the receipt renders
as before. In the first runs the gate caught the same slip twice: the model cited a
reviewer's `rtl_rules fail (E0)` as if it were a result of the run, and the rewrite fixed it.

Example: [docs/examples/m2_torv_e2e.receipt.html](docs/examples/m2_torv_e2e.receipt.html).

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
| [LibreLane](https://github.com/librelane/librelane) | (dependency) its container image with Yosys and OpenSTA; its ABC script, driving cell and corners for IHP SG13G2 | we run only synthesis + pre-PnR STA, and record the SDC hash and image digest in an envelope |
| [mcp-rtl-review](https://github.com/zesun33/mcp-rtl-review) | deterministic RTL review rules (blocking assignments, reset polarity, width truncation) as a reviewer without an LLM | it walks an AST; our rules come from Verilator's own warnings plus one text scan; findings feed the receipt next to a model reviewer |
| [Agent-as-a-Judge](https://github.com/metauto-ai/agent-as-a-judge) | a judge that uses tools to check the work instead of reading the author's account | the I2/I3 reviewer gets the task and raw tool results, never the author's conclusions, and re-runs the same MCP tools |

## Roadmap

M1 case descriptions, evidence gate, receipts · M1.1 tool versions, command allowlist,
whole tool results in traces · M2 (done) synth + pre-PnR STA under the SDC, check_sdc, gate
R5, reviewer I0–I3 + rule-based reviewer, human gate · M1.2 (done) init, doctor, check
presets, defaults, LibreLane/FuseSoC inputs; tried on a third design, an INT8 linear-layer
accelerator (FuseSoC, pulp IP) · M2.5 test generation scored by mutants · M3
simulation-failure localisation (logs, waveforms), lessons store · M4 LibreLane flow tool
(PPA, DRC/LVS).
