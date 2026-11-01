# Design snapshots and lineage

**Design snapshot and lineage** (`core/snapshot.py`, plan §0.18). Every result carries a
`snapshot`: `design_id` hashes the RTL files (per-file sha256, paths relative to the design root),
the SDC and the PDK version; `id` adds defines, include directories, the flow config, tool versions
and the image digest. The MCP server attaches the checkout's snapshot to every result that has no
own one (lint, regress, synth, mutate, perf); a flow result carries the snapshot of the run's own
inputs, `state: older` when its RTL or SDC changed after it ran. A flow result also carries its
`lineage`: for LibreLane, the stages synthesis → place and route → RC extraction → post-route STA
→ GDS streamout → DRC → LVS extraction → LVS with each stage's input and output files (sha256, and
which stage produced each input), the check that every step started from the previous step's
output, and that every `final/` file equals the step output it copies. Gate-level simulation,
activity and power from it are listed as not part of the flow. `consistency()` checks results used
together: same design where their RTL overlaps, same SDC, PDK, clock period and corner set, no
older snapshot, no lineage break. synth's Yosys netlist and the flow's own synthesis are labelled
"different netlist". Gate R8 refuses a multi-stage conclusion ("signoff passed", "ready for
tapeout", "timing and power both met") unless its results are one snapshot with an unbroken lineage;
the receipt shows the snapshots, the consistency facts and a lineage table with breaks marked.

The flow side (signoff rules, comparisons, run history) is in [flow-signoff.md](flow-signoff.md).
