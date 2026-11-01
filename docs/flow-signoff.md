# RTL-to-GDS flow and signoff

Gate rule R8 ([evidence-gate.md](evidence-gate.md)) checks signoff claims; design snapshots and the flow's lineage are in [lineage.md](lineage.md).

## RTL-to-GDS: `[pnr]`

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
shifts every slack), DRC = 0 for every signoff DRC tool of the run (LibreLane: Magic and
KLayout), LVS errors = 0 with a matching report, antenna-violating nets and pins = 0. Any rule failing → `fail`; a
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

**Back ends.** `[pnr] backend` picks a flow adapter (`tools/backends/`); `librelane` is the
default and the only one so far. The adapter says how to run the flow (here in a container; a
`remote` mode, e.g. a licensed server over SSH, or an `import` mode that only reads reports made
elsewhere, fit the same interface), where its reports are, and how to read them into a
back-end-neutral result. The signoff rules, E5 checklist, comparisons, run history, gate R8 and
receipts read only that neutral result; a unit test drives all of them with a tiny fake
adapter and no LibreLane file. Knob names stay the flow's own; runs of different back ends are
compared on neutral numbers only and labelled "different backend". A second open-source adapter
would most likely be OpenROAD-flow-scripts (both use OpenROAD; its `metrics.json` and reports
look alike; LVS is not a default ORFS step, so it would show as missing). Currently open-source
only; commercial adapters (place-and-route, STA, DRC/LVS, synthesis) are planned.

**Experience tables** (per design, never mixed): **imported** = runs found in the design's own
runs directory, made outside eda-agent (provenance "imported, maker unknown", files with
sha256); **eda_agent** = runs `flow_run` made for this design, one line each in
`<[pnr] experience_dir>/<project>/eda_agent_runs.jsonl` (run id, config patch, image digest, SDC
hash, source commit, the agent run that asked). A new design starts with an empty eda_agent
table. A lesson from the imported table, or from another design, is a hypothesis ("from
<design>, not verified here") until an eda-agent run of this design confirms it. `flow_report`
says which table its run belongs to.

**Run history** (`flow_history`, `eda-agent flow-history`, E0, plan §0.15 step 1): each table's
runs (stage reached, config differences from the baseline,
synthesis/pre-PnR and post-route numbers, runtime, provenance, source files with sha256). A knob's effect is
reported only from two runs that differ in that key alone **and** read the same RTL, shown by an
identical synthesized netlist or, for a synthesis knob, by the two runs starting within 15
minutes; git alone cannot see uncommitted edits between runs. Anything else is "confounded",
with every other difference listed. The comments above keys in the LibreLane config are checked
against those pairs: a stated "N% area" is confirmed or contradicted numerically; otherwise the
lesson is testable (numbers given), confounded (with indirect per-run numbers), or cannot be
tested (the key never changed).

`flow_run` copies the project root (minus `.git`, the design's runs and `copy_exclude`) under
`[pnr] work_dir`, runs `librelane --manual-pdk` in the image with `--network none`, the PDK
read-only and only the copy writable, writes the image digest, SDC sha256 and source commit
next to the run, and reports it. One flow at a time (a lock file). Config overrides are limited
to `allowed_overrides`; the SDC, clock port and period can never be overridden.

**Tuning.** `eda-agent tune` proposes knob patches from the run history and the lessons, checks them
against the forbidden-knob list in `core/knobs.py`, runs them with `flow_run` within a budget and
waits for a person with the patch: [tuning.md](tuning.md).
