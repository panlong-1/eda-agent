# Review, approval and receipts

## Independent review (I0-I3)

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
`approval` record `pending`. Nothing waits on a prompt. A person decides later, on the
command line or by clicking in `eda-agent review-ui`:

```bash
eda-agent approve <run_id> [--reason "..."] [--override] [--by NAME]
eda-agent reject  <run_id> --reason "..."
eda-agent pending <run_id> --note "..."      # decide later: a note, the run stays open
```

What `approve` needs depends on the receipt's issue levels (below):

| the receipt has | `approve` needs |
|---|---|
| Fatal items | `--override` and `--reason`; the override is recorded |
| Error items (no Fatal) | `--reason`: why they are acceptable |
| Warnings only | nothing more |

A gate-rejected run (status `pending`) has a Fatal item, so approving it is an override too
(`--override-gate` still works as another name for `--override`). The receipt records who
decided (`--by`, else `git config user.name`, else the login name; "via review-ui" when
clicked), when, the reason, the levels at that moment, and the sha256 of the receipt content
they decided on. Decisions are final; a rejected run is fixed by a new run. `--no-approval`
skips the gate (status `done`).

## Fatal, Error, Warning

Everything the approver must weigh gets one of three levels, by the rules in
`agent/levels.py` (plain code over the receipt's own data). The summary, the "look at these"
list, every findings table, the `levels` field of the receipt's JSON, `approve` and the review
page all use the same list.

| level | means | from |
|---|---|---|
| **Fatal** | the result cannot be trusted or used as it stands | a check ended in error, timeout or fail; a tool call errored; the run stopped early; the gate rejected the report; a failing case or testbench sub-test; lint errors; latches in synthesis; timing not met; a reviewer said reject; mutation changed the original checkout; the receipt changed after its decision; an imported report that fails its category's rule or could not be judged |
| **Error** | a real problem the approver accepts knowingly | reviewer findings `high`/`medium` (incl. the rule-based reviewer's multiple drivers, width truncation, latches, blocking assignments); gate rewrites; a case read but not run; a skipped case; no tool versions; small slack (setup under 10 % of the period, hold under 0.1 ns); a void timing result; an SDC that is not the design's own (`sdc_origin`); an override; a review that failed; mutants that errored; an imported report that does not identify its design |
| **Warning** | worth knowing, does not block | lint warnings; reviewer findings `low`/`info`, reviewer notes; part of the case list not run; lint not run; no independent review; pass/fail from the testbench's own self-check; timing pre-PnR only; surviving or likely-equivalent mutants; a failure localisation (a diagnosis from trace/waveform facts, a hypothesis, not a proof or a fix); a model-written summary the gate rejected; an imported report's missing input files, waived items, and that eda-agent did not run the tool; a decision made with eda-agent code older than the receipt's own, or one the current approval policy would not allow (e.g. Fatal approved without `--override` and a reason) |

Tool diagnostics map `error` to Fatal and `warning` to Warning; reviewer severities map
`high`/`medium` to Error and `low`/`info` to Warning. The receipt's `levels` field holds the
counts and every item (level, text, quote, source); like the model-written summary it is
derived, and left out of the sha256 a decision records.

## Deciding by clicking: `eda-agent review-ui`

```bash
eda-agent review-ui [--port 8799] [--runs-dir runs]
```

prints a URL such as `http://localhost:8799/?t=<token>`; open it in the browser (under WSL2 the
Windows browser reaches it at localhost). The first page lists every receipt in `runs/` with its
status and Fatal / Error / Warning counts, open runs first. A run's page is its receipt with a
decision panel under the summary:

- **Approve**: Warnings only, one click. With Error items, tick each one you accept (the
  ticked items become the reason) or write a reason. With Fatal items, tick the override and
  type a reason; that friction is on purpose.
- **Pending**: decide later; an optional note is recorded and the run stays open.
- **Reject**: tick what is wrong, or write a reason.

Every file the receipt refers to is a link: a finding's or a lint diagnostic's `file:line`,
the artifacts (reports, logs, netlist), the SDC, the trace and the source of each case the run
read. It opens a read-only view inside the review page, with line numbers and the line
highlighted (40 lines around it, or the tail of a long log, and "show whole file"). Relative
paths are taken from the run's project root (`project_root` in new receipts, else the
`projects/*.toml` of that name, `--projects-dir`); a bare name such as `apb_uart.sv:195` is the
project's only file of that name. The view shows only files under the project root or the runs
folder, or listed in the receipt, after following symlinks; never dotfiles such as `.bashrc` or
`.env`, `.git`, keys, binaries, or a file with an API-key-shaped string. Nothing opens in
another program. The static `.html` shows the same references as plain, copyable text (the
absolute path as a tooltip) and marks a missing file "(file not found)".

A click calls the same `human_gate` functions as the command line, so the same rules hold,
then re-renders the receipt files. The server binds 127.0.0.1 only, answers only to
`Host: localhost`/`127.0.0.1`, needs a random token made at each start (in the URL and in every
form), sends a strict Content-Security-Policy, and reads and writes nothing but
`runs/<id>.receipt.*` (plus the run's trace, read only). It uses only the standard library.
The static `.html` receipts show the exact commands instead.

## Reading a receipt

The person who approves should not have to read tables of claim scopes, checklists and three
reviews to find out what they are deciding. So every rendered receipt (`.html`, and `.md`)
is laid out for that person:

1. **Review summary**, about one screen, written by plain code from the receipt's own data:
   - the counts, coloured: **Fatal · Error · Warning**, then one line: how many checks passed,
     the gate, the reviewers' verdicts;
   - the task, **what you decide** (or who already decided, when, and the levels then), what
     approving needs, and the exact `approve` / `reject` / `pending` commands for this run;
   - **look at these before approving**: every Fatal and Error item, most serious first, one row
     per issue: a level badge, one plain line, then who raised it and where (`file:line`), with
     the full texts one click away. Findings that name the same first `file:line` (or say the
     same thing) are one row at the highest level any reviewer gave; the warnings fold under;
   - each check with pass/fail, its evidence level in words (E0 "parses / structural facts",
     E2 "these tests passed", E4 "synthesis estimate", …) and how long it took; the highest
     level reached and what each level does and does not let you say; what was **not**
     checked; the gate, one line per reviewer with its worst finding, the approval status and
     any pending notes.
2. **Summary written by a model** (optional, marked "AI"): see below.
3. **Data**, as tables: regression (cases asked/run/passed/failed/skipped, testbench sub-tests,
   failed cases); lint (warnings by code, by file, and the diagnostics kept, with their level);
   synthesis (cells, area, latches, library, clock, timing met, SDC and its hash), timing per
   corner (setup WNS/TNS, hold WNS/TNS), the worst path and the most used cells; the SDC check
   (clocks, unconstrained endpoints); plain numbers of any other tool; every reviewer finding
   (level, severity, reviewer, text, file:line); the gate's decisions and reasons; and the cost
   (turns, tool calls, tokens, wall time of the author, each reviewer and the summary).
4. **Details**: the whole receipt as before, unchanged (in the HTML, sections that open on a click).

The HTML page is a single file with no scripts or external links, so it opens offline.
`approve`, `reject` and `pending` re-render the files; `eda-agent receipt <run_id>` re-renders
any receipt and brings its `levels` up to date without touching anything else in its `.json`.
Receipts keep each check's metrics, diagnostics (up to 50) and duration; for receipts written
before that, the tables take the numbers from the run's own trace when it is still there,
marked †.

**The model-written summary.** `--narrate` on `run`/`check`/`receipt` (or `narrate = true`)
asks a model (by default the run's own) for a short summary. It gets the receipt data only:
no trace, no files, no tools, and not the approval status or the levels. Its text goes through
the same evidence gate as the agent's report (citations must match the run's results, no
functional-correctness words without E3/E5 evidence, timing claims must name the synth result
and its numbers, a residual-risk section that names the cases read but not run), with one
rewrite. The receipt keeps the text with provider, model, prompt
(`prompts/receipt_summary.v1.md`), the gate's decisions, cost and time under `narrative`. The
page shows it in a box marked "AI", with whether it passed the gate, and says the tables are
the record; a rejected text is folded away and listed as a Warning. The summary is outside the
sha256 a decision records, so one added later does not change what was decided (the page says
it was written after the decision). Without an API key the receipt renders as before. In the
first runs the gate caught the same slip twice: the model cited a reviewer's
`rtl_rules fail (E0)` as if it were a result of the run, and the rewrite fixed it.

Example: [docs/examples/m2_torv_e2e.receipt.html](examples/m2_torv_e2e.receipt.html).
