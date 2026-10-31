# Plug-ins: importers and flow adapters

eda-agent's core decides; plug-ins only read. A plug-in is a small Python package, installed into
the project's `.venv`, that turns reports made elsewhere (another tool, another machine, a licensed
server) into a **neutral result**. The core validates it, recomputes file hashes, judges it by the
rules of its **category**, records it with provenance `imported`, and applies the same evidence
gate, lineage and receipts as to everything else. Currently only open-source flows run inside
eda-agent; importers for commercial tools are planned and live in private plug-in packages.

## Entry points

```toml
# the plug-in's pyproject.toml
[project.entry-points."eda_agent.importers"]
mytool_cdc = "my_pkg.cdc:MyCdcImporter"     # class (instantiated without arguments) or instance

[project.entry-points."eda_agent.adapters"]
myflow = "my_pkg.flow:MyFlow"                # a eda_agent.tools.backends.Backend subclass
```

Install with `.venv/bin/pip install -e path/to/plugin`. `eda-agent plugins list` shows what was
found (and any plug-in that failed to load, without stopping the core).

## Importer interface

```python
from eda_agent.plugins.result import NotMine

class MyCdcImporter:
    name = "mytool_cdc"            # unique; used by `eda-agent import --as`
    version = "1.0"                # the importer's own version (recorded with every import)
    categories = ("cdc",)          # what it can produce

    def detect(self, path) -> float:      # > 0: "this directory is mine" (higher = surer); 0: not mine
        ...
    def parse(self, path) -> dict:        # the neutral result below; raise NotMine if not your format
        ...
    def contract_samples(self) -> list:   # report directories it must accept (may be private test data)
        ...
```

Rules: `parse` reads files only (no tool runs, no network), is repeatable (same directory, same
result), and raises `NotMine` for anything it does not recognize. Never guess.

## Neutral result

```python
{
  "category": "cdc",                                  # see the table below
  "tool": {"name": "SomeTool", "version": "2026.03"}, # both required
  "design": "torv_cpu",                               # top/block, if the report says
  "pdk_version": "...",                               # optional
  "metrics": {...},                                   # the category's required key numbers (below)
  "inputs":  [{"path": "...", "role": "rtl|rtl_manifest|netlist|sdc|constraints|waivers|library|spef|sdf|vcd|saif|def|gds|other",
               "sha256": "optional; if given it must match the file"}],
  "outputs": [{"path": "report.rpt", "role": "report|netlist|patterns|...", "sha256": "optional"}],  # >= 1, must exist
  "corners": [...],                                   # optional
  "experience": [{"knob": ..., "value": ..., "effect": ...}],   # optional; always a hypothesis in the core
  "notes": ["..."]                                    # optional
}
```

Relative paths are relative to the imported directory. Waived items go in `metrics.waivers` as
`[{"id": ..., "rule": ..., "reason": ...}]`; the core counts a waiver only if a person approved its id.

**Forbidden anywhere in the result** (the core decides them): `status`, `evidence_level`, `level`,
`verdict`, `claim_scope`, `signoff`, `signoff_passed`, `clean`, `pass`, `fail`, `passed_check`.

## Categories and what the core decides

| category | required metrics | core's status rule | level |
|---|---|---|---|
| `lint_rules` | `violations_by_severity` {error, warning, info}, `ruleset` | fail if unwaived errors | E0 |
| `cdc`, `rdc` | `unsynchronized`, `constraints_complete` | fail if unwaived unsynchronized crossings; error if constraints incomplete | E0 |
| `dft` | `scan_chains`, `dft_rule_violations` (opt. `max_chain_length`) | fail on unwaived violations or no chain | E0 |
| `atpg` | `test_coverage_pct`, `fault_coverage_pct`, `patterns` | pass if test coverage ≥ the approved target; error if no target | E0 |
| `equivalence` | `equivalent_points`, `non_equivalent_points`, `unmapped_points`, `reference`, `implementation` (opt. `aborted_points`) | fail if non-equivalent; error if unmapped/aborted | E0 |
| `synthesis` | `cells`, `area_um2` (opt. `latches`) | fail on latches | E4 |
| `pnr` | `die_area_um2`, `utilization` | facts | E4 |
| `sta` | `corners` {corner: setup_wns, setup_tns, hold_wns, ...}, `clock_period` (opt. `propagated_clock`, `parasitics`) | fail if any corner's setup or hold slack < 0 | E4 |
| `drc` | `violations`, `deck` | fail if unwaived violations | E4 |
| `lvs` | `match`, `errors` | fail unless match and 0 errors | E4 |
| `simulation` | `passed`, `failed` (opt. `gate_level`, `sdf`, `timing_checks`) | fail if any failed | E2 |
| `power` | `total_w` (watts; `None` when the report gives no unit, raw value in `total_raw`), `method` (opt. `annotation_coverage_pct`, `activity_corner`, `lib_corner`) | error if the unit is not established; fail only above an approved budget | E4 |

E0 categories are facts in their own category: they never stand in for simulation or signoff.
The claim scope (what the result shows and does not) is written by the core from a template.

## Human-set standards

Waivers and targets are a person's decisions, like the SDC and the perf baseline:

```bash
eda-agent standard waive cdc W-0001 --reason "async FIFO, reviewed with the design lead"
eda-agent standard target atpg.test_coverage_pct 95
eda-agent standard show
```

Adding a waiver or loosening a target needs `--reason`; tightening does not. Every change is kept
with who and when (`<experience_dir>/<project>/standards.json`). No MCP tool can change them.

## Importing

```bash
eda-agent import path/to/report_dir [--as mytool_cdc] [--project projects/torv.toml]
eda-agent import path/to/dc_run --all                      # every importer that recognizes the folder
eda-agent import path/to/report_dir --rtl-manifest SRC.sha256   # which RTL the reports describe
```

One folder often holds several categories (a synthesis run: synthesis, STA and power). `--all`
runs every importer whose `detect()` is above 0 and writes one result and receipt per importer;
without it, a tie between importers is refused and `--as` chooses.

**Which design.** A result is tied to a design snapshot (plan §0.18) only through its RTL hashes.
Reports of commercial tools rarely name their RTL, so give them: a person passes `--rtl-manifest`
(`sha256sum` lines `<sha256>  <path>`, or JSON `{path: sha256}` / `{"files": {...}}`, paths relative
to the design root), or the importer names a manifest it found as an input with role
`rtl_manifest`. Without one, the result's snapshot is unknown and gate R8 cannot join it to other
stages. For imports the SDC and PDK version may stay unknown; unknown is not counted as a difference.

The judged result is appended to the design's imported table
(`<experience_dir>/<project>/imported_reports.jsonl`) and a receipt is written to `runs/imports/`.
The agent reads imported results with the MCP tool `imported_results(category)`.

Also enforced on imported results: gate rule R9 (a category claim cites a result of that category
with its numbers; ATPG coverage is not verification coverage), the one-design check of R8, and the
scan-insertion link: a DFT result whose output netlist differs from its input netlist needs a
passing `equivalence` result covering both, or the pre- and post-scan results are not one design.

## Contract tests

```bash
eda-agent plugins check [--plugin mytool_cdc]
```

or in the plug-in's own tests: `from eda_agent.plugins.contract import check_importer` and assert
`check_importer(MyCdcImporter()) == []`. The checks: a name; every sample detected and parsed into
a valid neutral result (category, tool name and version, required metrics, outputs present, given
hashes match, no core-only field); parse repeatable; directories that are not the importer's
(empty, unrelated text, another tool's report) refused: detect false and parse raising `NotMine`.

## Example

`examples/fake_cdc_importer` is a FAKE importer for a toy report format with made-up data. Copy
it as a template.

## Flow adapters

A flow adapter (`[pnr] backend = "<name>"`) subclasses `eda_agent.tools.backends.Backend`:
`is_run`, `read_run` (the neutral flow result, `tools/backends/base.py`), `config`, `lineage`,
`setup_issues`, `lessons`, and `run_command` for run modes `local_container`, `remote` (e.g. a
licensed server over SSH, credentials managed by the user) or `import` (reports made elsewhere).
