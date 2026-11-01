# Architecture: layout and layers

```
src/eda_agent/
  core/                  shared parts; depend on nothing else in eda_agent, no tool and no model
    envelope.py          ToolResult: status, evidence_level, claim_scope, metrics, diagnostics, sha256
    project.py           per-design TOML: env script, filelists, regression entry point, case list
    pnr.py               [pnr]: the project's RTL-to-GDS flow; settings no run may override
    proc.py              subprocess with a hard timeout that also kills child processes; allowlist
    flow_sources.py      [flow] from a LibreLane config; PDK presets (IHP SG13G2)
    settings.py          finds eda-agent.toml; personal defaults in ~/.config/eda-agent/config.toml
    snapshot.py          design snapshots and lineage consistency: do results describe one design?
    categories.py        check categories of imported results (CDC, DFT, ...): their rules and numbers
    standards.py         human-set waivers and targets for imported checks
    hashing.py           sha256 of a file, of bytes, of JSON data (receipt content, snapshot)
    numbers.py           parsing numbers from tool output, ranking levels, shortening text
    setup_issues.py      setup diagnosis: the rule table on tool output per stage (sim/synth/pnr), file
                         checks (vendored IP, FuseSoC, generated files, ${VAR} paths), which [setup] step fixes it
  plugins/               the public plug-in interface (plugins.md); depends on core only
    __init__.py          importer and adapter discovery (entry points), choose an importer
    result.py            the neutral result an importer returns, and the core's validation of it
    contract.py          contract checks every importer must pass
  onboard/               connecting a new design (no model involved)
    detect.py, init.py   eda-agent init: guess a draft config, each value with where it came from
    doctor.py            eda-agent doctor: are the tools, files, image and API key all there?
                         (--deep, per stage with one real run, is in cli/doctor.py: it runs tools)
  tools/                 one EDA capability per file; each only returns an envelope (facts)
    lint.py              Verilator --lint-only, parsed diagnostics (E0)
    describe.py          what a regression case covers: source file + its leading comment
    regress.py           the project's own regression (E2), read by one of formats/; a run that could not
                         build or start is "environment not ready" (error_class setup), not a failure
    setup.py             run_setup: the approved [setup] steps only, each recorded (E0)
    formats/             torv.py | junit.py | exitcode.py: run cases, read results, claim_scope
    synth.py             synth: Yosys + OpenSTA per corner in the project's container (E4);
                         check_sdc: OpenSTA check_setup on the netlist (facts, E0)
    rtl_rules.py         fixed RTL rules: Verilator BLKSEQ/COMBDLY/WIDTHTRUNC/LATCH/MULTIDRIVEN
                         + a reset-polarity text scan (E0)
    mutate.py            mutants (avl-core) of one RTL file, each run in a copy of the design
                         against regression cases: kill matrix, survivors, perf_killed (E0)
    testcase.py          test_context, write_case, score_tests: new tests against surviving mutants (E3)
    rtl_trace.py         hierarchy, trace_driver, trace_load: the design elaborated in-process by pyslang
                         (lint file set), an index of who writes / reads each signal with file:line and
                         the if/case conditions of each assignment (diagnostic facts, E0)
    waves.py             waveform_query: values and changes in a time window of a VCD/FST (pywellen,
                         in-process, capped); maps waveform scope names to slang paths (diagnostic, E0)
    source.py            read_source: numbered lines of a project file (the test, RTL around a trace)
    perf.py              cycle counts and other testbench counters per case, against the
                         person-approved baseline (E2); recording that baseline (CLI only)
    flow.py              flow_report: a flow run judged by fixed signoff rules, compared with a
                         baseline run and synth (E4, signoff_level E5-partial); flow_run (copy, no network)
    flow_history.py      flow_history: the design's runs in two experience tables, knob effects, lessons (E0)
    experience.py        the imported and eda_agent experience tables of each design
    importing.py         eda-agent import: a report made elsewhere, judged by its category's rule
    backends/            flow adapters: base.py (the neutral result, the adapter registry), librelane.py
    reports/librelane.py reads one LibreLane run directory: metrics, STA, DRC, LVS, antenna, logs
  server/mcp_server.py   MCP server (stdio): the tools above, one MCP tool each
  agent/                 the agent runtime; reaches the tools only through MCP
    models/              base.py (ModelAdapter protocol), anthropic.py, openai_compat.py (DeepSeek),
                         scripted.py (for tests)
    loop.py              tool loop with budgets: turns, tool calls, 2-strike retry stop; JSONL trace
    gate/                evidence gate on the final report (no LLM), up to 2 rewrites: one module
                         per rule, r1_citations.py .. r10_localise.py, common.py, and __init__.py
                         (check_report runs them all)
    model_output.py      a model's reply -> structured fields -> the evidence gate (reviewer,
                         write-tests, the receipt summary)
    reviewer.py          independent review I0-I3 (context assembled by code) + rule-based reviewer
    testgen.py           write-tests: the fixed loop in which a model writes tests against mutants
    levels.py            Fatal / Error / Warning: every rule in one table, used everywhere
    human_gate.py        awaiting_approval / approve / reject / pending, recorded in the receipt
    receipt/             one pipeline for every receipt (agent runs, write-tests, imports):
      data.py            layer 1, what the receipt records (built from run report and envelopes)
      tables.py          layer 2, the data tables (per check, findings, gate, cost)
      summary.py         layer 2, the review summary a person reads first
      markdown.py        layer 3, runs/<id>.receipt.md
      page.py            layer 3, runs/<id>.receipt.html (self-contained)
      files.py           writes the .json (with its levels) and both pages
      narrative.py       optional model-written summary, checked by the evidence gate
      localise.py        failure localisation: the trace-chain table and an inline-SVG waveform snippet
    sources.py           file references in a receipt, and which ones the review page may show
    mcp_client.py        connects the loop to the MCP server
  cli/                   eda-agent: one module per subcommand (run.py: run and check; decide.py:
                         approve, reject, pending; flow.py: flow-*; plugins.py: import, plugins,
                         standard; ...), common.py for what they share
  review_ui.py           eda-agent review-ui: the receipts in a local page, decide by clicking
  prompts/               system.v3.md (default: v2 plus localising a failure; v1, v2 kept), reviewer.v1.md, testgen.v3.md,
                         testgen-review.v1.md, receipt_summary.v1.md, presets.v1.toml (the fixed
                         tasks of `check`); names and versions are recorded in every receipt
projects/torv.toml       TORV rv32m SoC (format torv)
projects/picorv32.toml   YosysHQ PicoRV32 (format exitcode + pass/fail regex)
examples/                fake_cdc_importer: a FAKE importer plug-in to copy (made-up format and data)
tests/unit/              offline and free, a few seconds; test_architecture.py keeps the layers one-way
tests/eda/               marker "eda": needs Verilator and the design checkouts; the synth test also
                         needs docker, the LibreLane image and the IHP PDK (skipped otherwise)
tests/fixtures/          recorded tool output and model replies
docs/                    this documentation; plugins.md: how to write an importer or a flow adapter
docs/examples/           real run receipts: M1 (PicoRV32, TORV hazard task); M2 TORV end to end
                         (lint, smoke, synth+STA, check_sdc, reviews rules/I2/I3) awaiting approval,
                         a copy approved only to demonstrate the human gate; M2.5 TORV write-tests;
                         M4 TORV RTL-to-GDS
runs/                    traces, receipts and logs of your runs (not in git)
```

Dependencies point one way, and `tests/unit/test_architecture.py` checks it on every run:
`core` ← `plugins` ← `tools` ← `server`, `core` ← `agent`, `core` ← `onboard`; `cli` and
`review_ui` sit on top. The agent never imports `tools`, so the MCP server can serve any other
agent too. Plug-ins use only `eda_agent.plugins` and `eda_agent.tools.backends`. One import goes
the other way on purpose: `plugins.load_adapters()` registers installed adapters in
`tools/backends` (both are the public plug-in interface). Nothing in `src/` is specific to one
design; that lives in `projects/*.toml`. Older module paths (`agent/receipt_view.py`,
`agent/gate_signoff.py`, `cli_flow.py`, ...) still import; they only re-export the modules above.
