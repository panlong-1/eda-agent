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
    mutate.py            mutants (avl-core) of one RTL file, each run in a copy of the design
                         against regression cases: kill matrix, survivors, perf_killed (E0)
    perf.py              cycle counts and other testbench counters per case, against the
                         person-approved baseline (E2); recording that baseline (CLI only)
    flow.py              flow_report: a LibreLane run judged by fixed signoff rules, compared with a
                         baseline run and synth (E4, signoff_level E5-partial); flow_run (copy, no network)
    reports/librelane.py reads one LibreLane run directory: metrics, STA, DRC, LVS, antenna, logs
  server/mcp_server.py   MCP server (stdio): lint / list_regress_cases / describe_case / regress /
                         rtl_rules / synth / check_sdc / perf / mutate
  agent/                 the agent runtime; reaches the tools only through MCP
    models/              base.py (ModelAdapter protocol), anthropic.py, openai_compat.py (DeepSeek),
                         scripted.py (for tests)
    loop.py              tool loop with budgets: turns, tool calls, 2-strike retry stop; JSONL trace
    gate.py              evidence gate on the final report (no LLM), up to 2 rewrites
    receipt.py           runs/<id>.receipt.json + .md at the end of every run
    reviewer.py          independent review I0-I3 (context assembled by code) + rule-based reviewer
    human_gate.py        awaiting_approval / approve / reject, recorded in the receipt
    mcp_client.py        connects the loop to the MCP server
  cli.py                 eda-agent init | doctor | check | mutate | perf | perf-baseline | run |
                         review | approve | reject
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
as `provider = "deepseek"` and `review = "I2"`.

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

### Mutation runs: `[mutation]`

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

### Performance: `[perf]`

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

### RTL-to-GDS: `[pnr]`

`flow_report` reads a finished LibreLane run (the design's own `runs/<tag>/`) and runs nothing
heavy; `flow_run` makes a new run. `[pnr]` names the LibreLane config; everything else defaults.

```toml
[pnr]
config = "librelane/config.yaml"
baseline = "full_p10"          # run compared with by default
# signoff_corners = [...]      # default: the run's STA_CORNERS
# agent_may_run = false        # the MCP flow_run tool refuses unless true (about an hour of CPU)
```

```bash
eda-agent flow-report full_p10_fwd --project projects/torv.toml [--baseline full_p10] [--with-synth]
eda-agent flow-run --project projects/torv.toml --tag mytag [--dry-run]
```

**Signoff rules** (one table, `SIGNOFF_RULES` in `tools/flow.py`): post-route setup and hold
worst slack ≥ 0 on every signoff corner, the post-route path reports time a propagated clock (no
ideal clock left; a non-zero clock source latency is flagged, since one applied to one side only
shifts every slack), Magic DRC = 0, KLayout DRC = 0, Netgen LVS errors = 0 with a matching
report, antenna-violating nets and pins = 0. Any rule failing → `fail`; a
check that did not run → `error` (incomplete); else `pass`. Max slew/cap/fanout, IR drop and
detailed-router DRC are reported as warnings. Metric counts are cross-checked against the
report files they come from.

**Evidence level.** E5 means post-route multi-corner STA + DRC/LVS + gate-level simulation +
VCD power. LibreLane gives the first two, with vectorless power and no gate-level simulation of
the routed netlist, so `metrics.signoff_level` is `E5-partial` and the envelope stays **E4**.
`e5_parts` is the checklist: multi-corner post-route STA, propagated clock, all nets with
parasitics, DRC, LVS, antenna, then the two a flow cannot give, gate-level simulation of the
routed netlist with post-route SDF at slow and fast corners **with timing checks on**, and power
from that simulation's activity with its annotation coverage and corners named. The envelope
stays E4: the gate treats E5 as a functional level (R2), and a
physical flow says nothing about function.

**Which SDC.** A timing result is tied to its SDC (R5). LibreLane does not record the SDC's
content, so `flow_report` decides how sure it is: `recorded` (flow_run wrote the hash next to
the run), `unchanged_since_run` (git: no commit and no edit of the SDC since the run started),
`mtime_before_run` (no git), else `changed_since_run` / `unknown`. Only the first three put an
`sdc_sha256` in the result; otherwise no timing claim can cite it. The same check is done for
the RTL files the run read (`inputs_changed`) and for the top (`OTHER_TOP`).

**Comparisons**: against a baseline run (deltas, plus LibreLane keys that differ and what
makes the two not comparable), against the same run's own pre-PnR STA, and against `synth`.
**Setup diagnosis**: PDK and macro views, corner data and signoff decks the config names but
that are missing, signoff steps switched off, and floorplan/PDN/macro/image errors in the logs
(`setup_issues`, plan §0.11).

`flow_run` copies the project root (minus `.git`, the design's runs and `copy_exclude`) under
`[pnr] work_dir`, runs `librelane --manual-pdk` in the image with `--network none`, the PDK
read-only and only the copy writable, writes the image digest, SDC sha256 and source commit
next to the run, and reports it. One flow at a time (a lock file). Config overrides are limited
to `allowed_overrides`; the SDC, clock port and period can never be overridden.

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

- **R7 performance.** A performance claim (faster, slower, speed-up, "N cycles", IPC/CPI,
  throughput, 更快/更慢/周期数 …) must name a `perf` result (or `mutate`, for a mutant) in the
  same sentence, and its cycle, IPC/CPI and percentage numbers must be ones those results
  report. "No performance regression" / "not slower" needs a `perf` result compared with the
  approved baseline and slower on no case. A negation earlier in the clause makes it a limit.
- **R8 signoff** (`agent/gate_signoff.py`). "DRC clean", "LVS clean", "antenna clean",
  "post-route timing met", "signoff passed", "tapeout-ready" (and 签核通过, 可以流片 …) must name
  a `flow_report`/`flow_run` result (or its run tag) in the same sentence, and a flow result of
  this run must show exactly that fact. "Tapeout-ready" needs every signoff rule passing **and**
  E5 (gate-level simulation and VCD power), which a LibreLane run alone does not give. Counts
  ("0 DRC errors") must be counts a flow result reports. (R6 is reserved for test scores.)

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
| [avl-core](https://github.com/projectapheleia/avl) | (dependency, `[mutation]` extra) source-level SystemVerilog mutants from the slang syntax tree; its Yosys miter for equivalent mutants | only its generator: we run the project's own regression per mutant in a copy (avl grades cocotb only), lint each mutant, and work around its byte/character offset bug |
| [MCY](https://github.com/YosysHQ/mcy) | considered for M2.5 and kept as an alternative | it mutates the synthesized netlist, so a mutant is not a readable RTL diff and the RTL regression cannot run it as is |
| [LibreLane](https://github.com/librelane/librelane) | (dependency) its container image with Yosys and OpenSTA; its ABC script, driving cell and corners for IHP SG13G2 | we run only synthesis + pre-PnR STA, and record the SDC hash and image digest in an envelope |
| [OpenROAD-MCP](https://github.com/The-OpenROAD-Project/OpenROAD-MCP), [ORFS-Agent](https://github.com/ABKGroup/ORFS-Agent) | back-end tools split into run and report; a flow iteration is a unit with its own budget | we read LibreLane's own run directory into fixed signoff rules and an envelope; parameter tuning is not done |
| [mcp-rtl-review](https://github.com/zesun33/mcp-rtl-review) | deterministic RTL review rules (blocking assignments, reset polarity, width truncation) as a reviewer without an LLM | it walks an AST; our rules come from Verilator's own warnings plus one text scan; findings feed the receipt next to a model reviewer |
| [Agent-as-a-Judge](https://github.com/metauto-ai/agent-as-a-judge) | a judge that uses tools to check the work instead of reading the author's account | the I2/I3 reviewer gets the task and raw tool results, never the author's conclusions, and re-runs the same MCP tools |

## Roadmap

M1 case descriptions, evidence gate, receipts · M1.1 tool versions, command allowlist,
whole tool results in traces · M2 (done) synth + pre-PnR STA under the SDC, check_sdc, gate
R5, reviewer I0–I3 + rule-based reviewer, human gate · M1.2 (done) init, doctor, check
presets, defaults, LibreLane/FuseSoC inputs; tried on a third design, an INT8 linear-layer
accelerator (FuseSoC, pulp IP) · M2.5 test generation scored by mutants (step 1 done:
`mutate`, plus `perf` with a human baseline and gate R7; next `write_case`, `score_tests`,
gate R6) · M3
simulation-failure localisation (logs, waveforms), lessons store · M4 LibreLane flow tool
(step 1 done: `flow_report`, signoff rules, gate R8, `flow_run` written; next: a fresh TORV run
with `flow_run`, gate-level simulation for a full E5).
