"""Layer 3, Markdown page: runs/<id>.receipt.md.

The review summary (summary.py), the model-written summary if there is one, the data tables
(tables.py); then the full receipt ("details") and the long lists.
"""
from __future__ import annotations

import json

from ...core.numbers import shorten
from ..levels import TITLE, Issue, counts_line
from .data import receipt_kind
from .summary import GENERATED, SHORT, Summary, narrative_meta, summarize
from .tables import Table, cell_text, data_tables, fmt_secs, trace_note

MARK = {"fatal": "🟥 **Fatal**", "error": "🟧 **Error**", "warning": "🟨 Warning"}
BADGE_MD = {"pass": "✅", "fail": "❌", "error": "❌", "timeout": "❌"}


def md_cell(text: str) -> str:
    """One line, safe inside a Markdown table cell ('|' escaped; GFM honours it inside code spans too)."""
    return " ".join(str(text).split()).replace("|", "\\|")


def _issue_md(i: Issue) -> str:
    where = " · ".join(x for x in (i.source, f"`{i.location}`" if i.location else "") if x)
    quote = f": {md_cell(shorten(i.quote, SHORT))}" if i.quote else ""
    return f"- {MARK[i.level]}: {md_cell(i.text)}{quote}" + (f" — *{where}*" if where else "")


def table_md(tb: Table) -> list[str]:
    out = [f"### {tb.title}", ""]
    if tb.note:
        out += [tb.note, ""]
    out += ["| " + " | ".join(md_cell(c) for c in tb.cols) + " |", "|" + "---|" * len(tb.cols)]
    out += ["| " + " | ".join(md_cell(cell_text(c)).replace("\n", "<br>") for c in row) + " |" for row in tb.rows]
    return out + [""]


def render_summary_md(s: Summary) -> str:
    out = [f"# Review summary · {s.run_id}", "", f"> {GENERATED}", "",
           f"**{counts_line(s.counts)}** — {s.headline}", "",
           f"- **Task:** {s.task}", f"- **What you decide:** {s.decision}",
           f"- **Status:** {s.status} · {s.run_line}"]
    if s.commands:
        out += [f"- **To approve:** {s.needs}", "", "```bash", *s.commands, "```",
                "", "Or click the decision in the local review page: `eda-agent review-ui`.", ""]
    elif s.commands_note:
        out += ["", f"*Commands: {s.commands_note}*", ""]
    else:
        out.append("")
    blocking = [i for i in s.issues if i.level != "warning"]
    warnings = [i for i in s.issues if i.level == "warning"]
    out += ["## Look at these before approving", ""]
    out += [_issue_md(i) for i in blocking] or ["- no Fatal or Error items"]
    out += ["", f"<details><summary>{len(warnings)} warning(s)</summary>", ""]
    out += [_issue_md(i) for i in warnings] or ["- none"]
    out += ["", "</details>", "", "## Checks", ""]
    if s.checks:
        out += ["| check | result | evidence | time (s) | tool summary |", "|---|---|---|---|---|"]
        for c in s.checks:
            name = c["tool"] + (f" `{md_cell(c['args'])}`" if c["args"] else "")
            out.append(f"| {name} | {BADGE_MD.get(c['status'], '❔')} {c['status_text']} "
                       f"| {c['level']} {c['level_text']} | {fmt_secs(c.get('duration'))} | {md_cell(c['summary'])} |")
    else:
        out.append("- no check ran")
    out += ["", "## What the evidence allows", ""] + [f"- {x}" for x in s.evidence]
    out += ["", "## Not checked", ""] + [f"- {x}" for x in s.not_checked]
    out += ["", "## Evidence gate", "", f"- {s.gate}"] + [f"  - {md_cell(x)}" for x in s.gate_reasons]
    out += ["", "## Reviewers", ""]
    for v in s.reviews:
        counts_text = " · ".join(f"{TITLE[k]} {n}" for k, n in v["counts"].items()) or "no findings"
        line = f"- **{v['who']}**: {v['verdict_text']} ({counts_text})"
        if v["worst"] and v["worst"]["severity"] in ("high", "medium"):
            line += f"; most serious: [{v['worst']['severity']}] {md_cell(shorten(v['worst']['text'], 160))}"
        out.append(line)
    if not s.reviews:
        out.append(f"- {s.no_review}")
    out += ["", "## Approval", "", f"- {s.approval}"] + [f"- note: {n}" for n in s.notes]
    return "\n".join(out) + "\n"


def render_narrative_md(r: dict) -> str:
    n = r.get("narrative")
    if not n:
        return ""
    meta, gate = narrative_meta(r, n)
    quoted = "\n".join(f"> {line}" if line.strip() else ">" for line in n["text"].splitlines())
    return f"## Summary written by a model (AI)\n\n*{meta}* **{gate}**\n\n{quoted}\n"


def render_data_md(r: dict) -> tuple[str, str]:
    """(the data tables, the long lists) as Markdown."""
    tables = data_tables(r)
    main = ["## Data", ""] + ([trace_note(r), ""] if trace_note(r) else [])
    for tb in tables:
        if not tb.collapsed:
            main += table_md(tb)
    more = [line for tb in tables if tb.collapsed for line in table_md(tb)]
    return "\n".join(main), ("\n".join(["## More data (long lists)", ""] + more) if more else "")


def render_markdown(r: dict) -> str:
    """The review summary, the model-written summary if there is one, the data tables; then the
    full receipt (details) and the long lists."""
    data, more = render_data_md(r)
    narrative = render_narrative_md(r)
    return (render_summary_md(summarize(r)) + ("\n" + narrative if narrative else "") + "\n" + data
            + "\n---\n\n" + details_md(r) + ("\n" + more if more else ""))


def details_md(r: dict) -> str:
    """The full receipt of any kind, as Markdown."""
    kind = receipt_kind(r)
    if kind == "testgen":
        return testgen_details_md(r)
    if kind == "import":
        return import_details_md(r)
    return render_details(r)


# ---------------------------------------------------------------- run receipts

def render_details(r: dict) -> str:
    gate = r["gate"]
    out = [
        f"# Run receipt {r['run_id']}",
        "",
        f"- **Task:** {r['task']}",
        f"- **Project:** {r['project']}  ·  **Model:** {r['provider']} / {r['model']}"
        f"  ·  **Prompt:** {r.get('prompt') or 'n/a'}",
        f"- **Stop:** {r['stop_reason']}  ·  **Status:** {r['status']}"
        + (f" (agent: {r['agent_status']})" if r.get("agent_status") else "") + "  ·  "
        f"**Gate:** {gate['status']} ({gate['rewrites']} rewrite(s))",
        f"- **Cost:** {r['turns']} turns, {r['tool_calls']} tool calls ({r['tool_errors']} errors), "
        f"{r['usage'].get('input_tokens', 0)} in / {r['usage'].get('output_tokens', 0)} out tokens, "
        f"{r['wall_s']} s",
        *([f"- **Preset:** {r['preset']}"] if r.get("preset") else []),
        *([f"- **SDC origin:** {r['sdc_origin']}"] if r.get("sdc_origin") else []),
        "",
        "## Checks run",
        "",
        "| tool | args | status | evidence | summary |",
        "|---|---|---|---|---|",
    ]
    for c in r["checks_run"]:
        summary = c["summary"].splitlines()[0] if c["summary"] else ""
        out.append(f"| {md_cell(c['tool'])} | `{md_cell(json.dumps(c['args']))}` | {md_cell(c['status'])} "
                   f"| {md_cell(c['evidence_level'])} | {md_cell(summary)} |")
    for c in r["checks_run"]:
        out += ["", f"**{c['tool']} {json.dumps(c['args'])}** claim scope: {c['claim_scope']}"]
        out += [f"- artifact `{a['path']}` sha256 `{a['sha256']}`" for a in c["artifacts"]]
        out += [f"- {name}: {version}" for name, version in c.get("tool_versions", {}).items()]
    timed = [c for c in r["checks_run"] if c.get("timing")]
    if timed:
        out += ["", "## Timing estimates (E4, pre-PnR)", "",
                "| tool | clock | setup WNS | TNS | hold WNS | worst corner | met | SDC sha256 | void |",
                "|---|---|---|---|---|---|---|---|---|"]
        for c in timed:
            t = c["timing"]
            out.append(f"| {c['tool']} | {t['clock_period']} ns | {t['wns']} | {t['tns']} | {t['hold_wns']} "
                       f"| {t['worst_corner']} | {'yes' if t['timing_met'] else 'no'} | `{c['sdc_sha256'][:16]}…` "
                       f"| {'VOID (SDC changed later in the run)' if t['void'] else 'no'} |")
    out += render_lineage(r)
    nr = r["not_run"]
    out += ["", "## Not run", "",
            f"- lint run: {'yes' if nr['lint_run'] else 'no'}",
            f"- described but not run: {', '.join(nr['described_but_not_run']) or 'none'}",
            f"- cases in the list not run ({len(nr['cases_not_run'])}): "
            f"{', '.join(nr['cases_not_run']) or 'none'}"]
    if gate["decisions"]:
        out += ["", "## Gate decisions", ""]
        for i, d in enumerate(gate["decisions"], 1):
            verdict = "pass" if d["passed"] else "reject"
            out.append(f"{i}. {verdict} (best passing evidence {d['max_level'] or 'none'})")
            out += [f"   - {reason}" for reason in d["reasons"]]
    out += ["", "## Final claims", "", r["final_claims"] or "(none)",
            "", "## Residual risk", "", r["residual_risk"]["report"] or "(none stated in the report)",
            "", "From the tools:"]
    out += [f"- {x}" for x in r["residual_risk"]["from_tools"]] or ["- (none)"]
    for review in r.get("reviews", []):
        out += _render_review(review)
    if r.get("approval"):
        a = r["approval"]
        out += ["", "## Human approval", "",
                f"- **Step:** {a.get('step')}  ·  **Status:** {a['status']}",
                f"- requested: {a.get('requested_at')} ({a.get('why')})"]
        if a["status"] != "pending":
            out += [f"- decided by **{a['by']}** at {a['at']}" + (f": {a['reason']}" if a.get("reason") else ""),
                    f"- receipt content sha256 at decision: `{a['receipt_sha256']}`"]
            if a.get("overrode_gate"):
                out.append("- this approval overrode the evidence gate's rejection")
        else:
            out.append(f"- decide with `eda-agent approve {r['run_id']}` or "
                       f"`eda-agent reject {r['run_id']} --reason \"...\"`")
    if r["trace"]:
        out += ["", f"Trace: `{r['trace']}`"]
    return "\n".join(out) + "\n"


def _render_review(v: dict) -> list[str]:
    who = "rule-based (no model)" if v["reviewer"] == "rules" else f"{v['reviewer']}, {v['provider']} / {v['model']}"
    out = ["", f"## Review: {who}", "", f"- **Saw:** {v['context']}",
           f"- **Verdict:** {v['verdict'] or '(none given)'}"
           + ("" if v.get("format_ok", True) else "  ·  format not followed (checklist first, findings, verdict)")]
    if v.get("note"):
        out.append(f"- **Note:** {v['note']}")
    if v.get("tool_calls"):
        out.append("- **Re-ran:** " + "; ".join(f"{c['tool']} {json.dumps(c['args'])} -> {c['status']} "
                                                f"{c['evidence_level']}" for c in v["tool_calls"]))
    out += ["", "Checklist (written before the findings):", ""]
    out += [f"- {x}" for x in v["checklist"]] or ["- (none)"]
    out += ["", "Findings:", ""]
    out += [f"- [{f['severity']}] {f['text']}" for f in v["findings"]] or ["- (none)"]
    return out


def _sha(e: dict) -> str:
    return (e.get("sha256") or "MISSING")[:10]


def render_lineage(r: dict) -> list[str]:
    """Design snapshot per check, whether the checks are one design, each flow run's lineage."""
    snaps = [c for c in r.get("checks_run", []) if c.get("snapshot")]
    cons = r.get("consistency")
    out: list[str] = []
    if snaps:
        out += ["", "## Design snapshot", "", "| tool | snapshot | design | state | note |", "|---|---|---|---|---|"]
        for c in snaps:
            s = c["snapshot"]
            out.append(f"| {c['tool']} | `{s.get('id') or '-'}` | `{s.get('design_id') or '-'}` | {s.get('state')} "
                       f"| {s.get('note') or ''} |")
    if cons:
        out += ["", "Checks used together: " + ("one design, no conflict" if cons["ok"] else "**NOT one design**")]
        out += [f"- **{i['level'].upper()}** {i['what']}" for i in cons["issues"]]
    for c in r.get("checks_run", []):
        lin = c.get("lineage")
        if not lin:
            continue
        out += ["", f"## Lineage: {c['tool']} run {lin.get('run')} ({lin.get('backend')})", ""]
        if not lin.get("available"):
            out.append("- the flow adapter cannot extract a lineage for this run")
            continue
        out += ["| stage | steps | inputs (sha256, from) | outputs (sha256) | ok |", "|---|---|---|---|---|"]
        for s in lin["stages"]:
            ins = "; ".join(f"{k} {_sha(e)} ← {e.get('from')}" for k, e in s["inputs"].items()) or "-"
            outs = "; ".join(f"{k} {_sha(e)}" for k, e in s["outputs"].items()) or "-"
            out.append(f"| {s['stage']} | {s['steps'][0]} .. {s['steps'][1]} | {ins} | {outs} | "
                       f"{'yes' if s['ok'] else '**BREAK**'} |")
        fin = lin.get("final") or []
        out.append(f"- final/ copies matching their step outputs: {sum(f['matches'] for f in fin)} of {len(fin)}")
        out += [f"- **{b.get('level', 'error').upper()}** {b['what']}" for b in lin.get("breaks", [])]
        if lin.get("stages_absent"):
            out.append(f"- stages not in this run: {', '.join(lin['stages_absent'])}")
        out.append(f"- not part of this flow (needed for full E5): {', '.join(lin.get('not_present') or [])}")
    return out


# ---------------------------------------------------------------- write-tests receipts

def testgen_details_md(r: dict) -> str:
    """The receipt of a write-tests run, for a person deciding on the patch."""
    a = r.get("approval") or {}
    lines = [f"# Receipt {r['run_id']}: new tests against surviving mutants", "",
             f"- project: {r['project']} · mutate run: `{r['mutate_run']}` · model: {r['provider']}/{r['model']} "
             f"(prompt {r['prompt']}, review prompt {r['review_prompt']})",
             f"- status: **{r['status']}**" + (f" · approval: {a.get('status')}"
                                              + (f" by {a['by']} at {a['at']}" if a.get("by") else "") if a else ""),
             f"- evidence gate: {'passed' if r['gate']['decision']['passed'] else 'REJECTED'}"
             + "".join(f"\n  - {x}" for x in r["gate"]["decision"]["reasons"]),
             f"- tokens: {r['usage']} · wall time: {r['wall_s']} s", "", "## Per mutant", ""]
    for t in r["results"]:
        lines.append(f"### Mutant {t['mutant']}: {t['status']}")
        if t["diff"]:
            lines += ["", "```diff", t["diff"].rstrip(), "```"]
        lines += ["", "| attempt | fix | test | on the unmodified design |", "|---|---|---|---|"]
        lines += [f"| {x['attempt']} | {x['fix']} | `{x['file'] or '-'}` | {x['original']} |" for x in t["tries"]]
        for s in t["scores"]:
            m = s.get("metrics") or {}
            lines.append(f"\n- score_tests ({s.get('status')}, {s.get('evidence_level')}): {s.get('summary', '')}")
            if m.get("perf_killed"):
                lines.append(f"  - perf-killed (passed, cycle count moved): {m['perf_killed']}")
            base = m.get("original_perf") or {}
            for tid, tv in (m.get("targets") or {}).items():
                for k in ("cycles", *[k for k in base if k != "cycles"][:3]):
                    runs = [p.get(k) for p in tv.get("perf") or []]
                    if k in base and runs:
                        lines.append(f"  - mutant {tid}, {k}: unmodified design {base[k]} -> mutant "
                                     f"{', '.join(map(str, runs))}")
        if t["status"] == "closed":
            v = t.get("review") or {}
            lines += [f"\n- test: `{t['test']}` · patch: `{t['patch']}`",
                      f"- review: verdict {v.get('verdict', 'not run')}, architectural only "
                      f"{v.get('architectural_only', '?')}, expected values checked "
                      f"{v.get('expected_values_checked', '?')}" + (f" — {v['notes']}" if v.get("notes") else "")]
        elif t["note"]:
            lines.append(f"\n- {t['note']}")
        lines.append("")
    lines += ["## Report (checked by the evidence gate)", "", r["final_claims"], "",
              "Apply an approved patch in the design repository with `git apply <patch>`; eda-agent never "
              "writes there.", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------- import receipts

def import_details_md(rec: dict) -> str:
    """The receipt of one import: what was read, from where, by which importer, and the core's verdict."""
    e = rec["envelope"]
    m = e["metrics"]
    lines = [f"# Import receipt {rec['id']}", "", f"- **Design:** {rec['design']}  ·  **Category:** {m['category']}  ·  "
             f"**Tool:** {m['tool_name']} {e['tool_versions']}", f"- **Imported:** {rec['imported_at']} from "
             f"`{m['source_dir']}` by importer {m['importer']} {m['importer_version']}; made by: {rec['made_by']}",
             f"- **Status (core's rule):** {e['status']}  ·  **Level:** {e['evidence_level']}",
             f"- **Snapshot:** {e['snapshot'].get('id') or '-'} ({e['snapshot'].get('state')}: {e['snapshot'].get('note')})",
             "", f"Claim scope: {e['claim_scope']}", "", "| side | role | path | sha256 |", "|---|---|---|---|"]
    lines += [f"| {side} | {f['role']} | `{f['path']}` | `{(f['sha256'] or 'not found')[:16]}` |"
              for side in ("inputs", "outputs") for f in m["files"][side]]
    lines += ["", "Findings:"] + ([f"- [{d['severity']}] {d['msg']}" for d in e["diagnostics"]] or ["- none"])
    lines += ["", "Not covered:"] + [f"- {r}" for r in e["residual_risk"]]
    return "\n".join(lines) + "\n"
