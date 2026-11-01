# Memory and context compaction

Long runs (failure localisation, test generation, the vertical acceptance run, backend tuning) need
memory across runs and more context than one model window. Both are built so that they cannot
change what a run concludes:

- **memory is a hint, never evidence**: a conclusion still needs a tool result of this run (gate R1),
  and a report may cite only lessons and documents that exist and were retrieved in this run (gate R11);
- **compaction never changes a number**: older turns are replaced by a table that code copies from the
  tool results; the raw results stay in the trace; the gate and the reviewers read the full trace.

## Lessons: a cited store per design

One JSON line per entry in `<[pnr] experience_dir>/<design>/lessons.jsonl` (default
`runs/experience/<design>/`), next to the design's experience tables ([flow-signoff.md](flow-signoff.md)).
Append-only: nothing is edited in place.

| field | meaning |
|---|---|
| `id` | `lesson-<design>-NNN` |
| `kind` | `lesson`; `refute` (a new lesson that refutes `refers_to`); `verify` (marks a hypothesis verified, with its source); `void` (a person voids `refers_to`, with a reason) |
| `text` | the lesson, as written by the person, the seeding code or the run that proposed it |
| `scope` | design, module, tool, pdk, corner, problem |
| `status` | `verified`, `hypothesis`; an entry's status *now* is folded with the later entries pointing to it (`refuted`, `void`) |
| `sources` | run ids, receipts and their sha256, files and their sha256 (at least one) |
| `by`, `at`, `origin`, `reason` | who, when, from where, why (a void needs a reason) |

Where lessons come from:

- **seeding** from data that exists: `eda-agent lessons seed [--receipt runs/<id>.receipt.json]` turns the
  flow config's comments, judged against the imported experience table (`flow_history`), into
  hypotheses, and a flow receipt's signoff rules into one lesson per flow result (verified only when a
  person approved the receipt and the run is in this design's `eda_agent` table). Seeding twice adds
  nothing;
- **runs**: a report may end with a section `## Lessons (hypotheses)` of at most 3 items. When the gate
  passed, each item is recorded as a hypothesis sourced to the run id and its trace's sha256
  (`--no-learn` turns this off). A person verifies or voids it later;
- **the experience tables**: `eda-agent lessons propose` (a model proposes, code checks every cited
  number against the tables; hypotheses only) and `eda-agent tune` (each tuning run verifies or refutes
  the lesson it tested, citing the run): [tuning.md](tuning.md);
- **a person**: `eda-agent lessons add | refute | verify | void` (the person is git `user.name`, as for
  approvals). `void` is the human gate for memory.

Lessons of other designs are offered only as hypotheses, noted "from <design>, not verified here" (§0.15).

## Retrieval (the cited RAG)

At run start, the task text is searched over this design's lessons, the other designs' lessons and the
sections of the documents the project names:

```toml
[memory]
docs = [
    { id = "torv-spec", path = "spec/RV32IM_Current_Implementation.md" },   # relative to the project root
    { id = "torv-flow", path = "docs/open_source_flow.md" },
]
lessons = 5      # lessons put into the context
doc_hints = 3    # document sections put into the context
```

The index is SQLite FTS5 from the standard library, built in memory for each search: no service, no
extra package. Markdown is split at headings (`#` to `####`), other text at blank lines; a section is
cited as `doc:<doc id>#<section>`. Ranking is bm25 plus 1.0 for the same design, 0.5 for a verified
lesson and 0.5 for each scope value (module, tool, pdk, corner, problem) the query names. Refuted and
voided lessons are never offered.

The hits go into the system prompt as "Hints from memory (NOT evidence)", each with its id, status and
source. During the run the agent can call `search_memory(query)`; what it returns becomes citable. The
receipt lists every lesson and section used as a hint, how it was found and whether the report cites it.

## Gate rule R11

See [evidence-gate.md](evidence-gate.md#the-gate-r1-r11). A rejection by R11 is Fatal in the receipt
(`agent/levels.py`); hints used and compactions are Warnings, so an approver sees them.

## Context compaction

```toml
[memory]
compact_at = 0.6           # when a turn's tokens pass this share of the window
context_window = 128000
compact_after_turns = 0    # or every N turns (0: off)
keep_turns = 2             # newest turns kept verbatim
```

(or `--compact-at`, `--context-window`, `--compact-after-turns`, `--keep-turns`, `--no-compaction` on
`run` and `check`). When due, the loop asks the model adapter to compact; the adapter replaces older
turns by the **run-state table** (`agent/runstate.py`), built by code from the envelopes of this run:

- every check with its result id (`r1`, `r2`, ...), tool, arguments, status, evidence level and key
  numbers (the summary line and the numeric metrics, copied through `json.dumps`);
- open items (checks in error, cases described but not run, the last gate rejection);
- the memory ids retrieved so far, and the budget used (turns, tool calls, tokens).

The agent fetches any raw result with `get_result(id)`; each fetch is logged (trace `fetch`, receipt
"get_result fetches"). The receipt lists every compaction: when, which turns, why, how.

Provider behaviour lives in the adapters; the loop does not know it:

| adapter | how it compacts |
|---|---|
| DeepSeek (`openai_compat.py`) | whole turns only: the system message stays, the task message becomes task + run-state table, the newest `keep_turns` turns follow unchanged (assistant message with its `reasoning_content` and tool calls, then its tool messages), so the history stays valid for DeepSeek's thinking mode |
| Claude (`anthropic.py`) | append-only, because Claude's preserved thinking rejects an edited history: the table is appended after the tool results not yet sent, and from then on the API's context editing (`clear_tool_uses_20250919`, beta `context-management-2025-06-27`) clears old tool results server-side. Not yet tested against the live API (no Anthropic key) |
| scripted (tests) | records the table; the replay is unchanged |
