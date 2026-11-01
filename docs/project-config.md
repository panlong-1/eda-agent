# Project config reference

A project is one TOML file (`projects/<name>.toml`, or `eda-agent.toml` in the design directory). `eda-agent init` drafts it ([usage.md](usage.md#adding-a-project)). The sections:

| section | what it sets | details |
|---|---|---|
| top level, `[lint]`, `[regress]`, `[versions]`, `allowed_commands` | the design, lint, the regression | below |
| `[flow]` | synthesis and pre-PnR timing | [below](#synthesis-and-timing-flow) |
| `[mutation]` | mutation runs | [mutation-and-tests.md](mutation-and-tests.md#mutation-runs-mutation) |
| `[tests]` | new tests against surviving mutants | [mutation-and-tests.md](mutation-and-tests.md#new-tests-against-surviving-mutants-tests-write-tests) |
| `[perf]` | cycle counts against a person-approved baseline | [mutation-and-tests.md](mutation-and-tests.md#performance-perf) |
| `[pnr]` | the RTL-to-GDS flow (LibreLane) | [flow-signoff.md](flow-signoff.md#rtl-to-gds-pnr) |
| `[memory]` | documents retrieved as cited hints; how many hints; context compaction | [memory.md](memory.md) |

## Design, lint and regression

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

## Synthesis and timing: `[flow]`

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
