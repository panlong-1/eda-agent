# Failure localisation: from a failing case to source lines

When a regression case fails, the agent can look for the cause the way a person does in a debugger
(driver tracing plus waveform values), and everything it finds is shown in eda-agent's own review
page. There is no external viewer: the source view, the trace-chain table and a small waveform
drawing are all part of the receipt. The libraries run in-process:

```
pip install -e ".[debug]"      # pyslang (slang's Python bindings) and pywellen (waveform reader)
```

## Tools (MCP)

All of them report facts only. Their envelopes have evidence level E0 and `"diagnostic": true`: they
never raise the evidence level, and a cause found with them is a hypothesis.

| tool | library | what it returns |
|---|---|---|
| `hierarchy` | pyslang | instance tree of the elaborated design (the lint file set and top): module, `instantiated_at`, `defined_at`; with `signals=true` the ports and signals of one instance with direction, width, driver and load counts |
| `trace_driver` | pyslang | what writes a signal, back step by step: continuous assigns, always blocks (one step per assignment, with the if/case conditions it sits under, each with `file:line` and text) and instance ports; with `waveform` and `time` every signal in the chain gets its value at that time |
| `trace_load` | pyslang | what reads a signal, forward, in the same form |
| `waveform_query` | pywellen | values and changes of at most 16 signals in a time window, at most 60 changes each |
| `read_source` | (plain file read) | numbered lines (at most 200) of one file under the project root: the failing test program, the RTL around a traced line; no envelope |

`regress` takes `waves=true` for at most two named cases, when the project config says how its
regression dumps waveforms (`[regress] waves_flag`, `waves_file`, e.g. TORV's `--waves` and
`{case_dir}/{name}.vcd`); the file comes back in `metrics.waves`.

Waveforms are read only from under the project root or eda-agent's `runs/` folder, up to 2 GB.

## How the agent uses them (prompt `system.v3`)

1. the failing case and its log tail; 2. waves for that case only; 3. read the test (`read_source`)
for what it checks and expects, then the first wrong signal and time (`waveform_query`, `hierarchy`); 4. `trace_driver` from it, with the waveform and time, back
to a source line; 5. the localisation reported as a hypothesis with its trace chain.

## Gate and levels

Gate rule **R10** ([evidence-gate.md](evidence-gate.md)): a sentence that places the cause ("the bug
is in X", "caused by line N", "root cause", 定位到) names a `trace_*`/`waveform_query` result of
this run, cites only `file:line` a trace of this run reached, is never written as a proven fix, and
the report calls it a hypothesis. In the receipt, a localisation is a **Warning**: "a diagnosis, a
hypothesis about the cause, not a proof and not a fix". The failing regression itself stays Fatal,
so such a run always waits for a person.

## The review page

A section "Failure localisation (diagnosis)" shows, per trace result, the chain table (signal,
what writes it, `file:line`, the conditions it sits under with their `file:line`, the value at the
failing time); on `eda-agent review-ui` every `file:line` opens the source view. Below it, an inline
SVG of the signals a `waveform_query` read, in its time window, with the time the trace used marked
(x/z values in red). No script and no library: it is part of the static page.

## Waveform names and slang paths

Verilator writes its own `TOP` scope above the testbench, and the design sits below the testbench
under the instance name the testbench gives it: TORV's `torv_soc` is `TOP.tb_torv_soc.dut`.
`waves.py` finds that prefix by looking for the waveform scope whose children best match the slang
top's instances and signals, and reports it (`scope_prefix`, `mapping_note`). Where it is uncertain:

- a testbench that instantiates the design twice: the first best match is taken (the note says so);
- generate blocks: Verilator may write `gen[0]` or `gen(0)` (both are tried); unnamed blocks are
  `unnamedblkN` in Verilator and have no name in slang, so signals in them may not map;
- signals Verilator optimised away or did not trace are reported as "not in waveform", never guessed;
- slang elaborates the lint file set; a simulation file set that differs (another top, other
  defines) is not seen.

## Limits

- Bit-level paths are not followed: a write to `x[3]` is a write to `x`.
- Function and task bodies, interfaces and modports are not traced into.
- One waveform at a time is kept open; VCD is read whole by pywellen (a large dump needs memory: dump
  the failing case only, which `regress waves=true` enforces).
- No X-propagation tracing or pass/fail waveform diff yet (plan §0.10 `trace_x`, `wave_diff`).
