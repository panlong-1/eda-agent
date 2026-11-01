"""Layer 3, HTML page: runs/<id>.receipt.html, one self-contained page (no script, nothing
loaded from elsewhere). The review page (review_ui.py) shows the same page with its buttons.

    render_html(receipt) -> str
"""
from __future__ import annotations

import html
import json
import re

from ..levels import FINDING_LEVEL, LEVELS, TITLE, Issue, counts_line, first_sentence
from .data import receipt_kind
from .markdown import details_md
from .summary import GENERATED, STATUS_CLASS, narrative_meta, summarize
from .tables import Table, cell_text, data_tables, fmt_secs, trace_note

# One type scale (13 / 15 / 18 / 24 px), spacing in steps of 8 px, colours as tokens for light and
# dark. Level colours: Fatal red, Error orange, Warning yellow, used by one pill component.
CSS = """
:root{--bg:#f7f7f5;--fg:#1f2328;--muted:#5f6670;--card:#fff;--line:#dfe1e4;--soft:#f0f1f3;--accent:#2f5d8a;
--fatal:#b42318;--fatal-bg:#fdecea;--error:#b54708;--error-bg:#fff3e6;--warn:#7a5d00;--warn-bg:#fdf6d8;
--ok:#1e7a3c;--ok-bg:#e6f4ea;--info:#2f5d8a;--info-bg:#e8f0f8;--ai:#6b4fa0;--ai-bg:#f4f0fa;--on-fatal:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#141619;--fg:#e6e8eb;--muted:#9aa1aa;--card:#1c1f23;--line:#30353b;
--soft:#24282d;--accent:#8fb6e0;--fatal:#ff8a80;--fatal-bg:#3a1d1b;--error:#ffb070;--error-bg:#3a2a18;
--warn:#f0d264;--warn-bg:#332c14;--ok:#7fd49a;--ok-bg:#183323;--info:#8fb6e0;--info-bg:#1b2a3a;--ai:#c7b2ef;
--ai-bg:#251f31;--on-fatal:#1c1f23}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,"Segoe UI",Roboto,system-ui,sans-serif;
overflow-wrap:break-word}
main{max-width:980px;margin:0 auto;padding:24px}
@media (max-width:600px){main{padding:16px}}
h1{font-size:24px;line-height:32px;margin:0 0 8px}
h2{font-size:18px;line-height:24px;margin:32px 0 8px;padding-bottom:8px;border-bottom:1px solid var(--line)}
h3{font-size:15px;line-height:24px;margin:24px 0 8px}
p{margin:0 0 8px}
ul{margin:0 0 8px;padding-left:24px}
li{margin:4px 0}
a{color:var(--accent)}
a.ref{text-decoration:none}a.ref code{color:var(--accent)}a.ref:hover code{text-decoration:underline}
.muted{color:var(--muted)}.small{font-size:13px;line-height:20px}
code,pre,.mono{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:13px}
code{background:var(--soft);border-radius:4px;padding:1px 4px;overflow-wrap:anywhere}
pre{background:var(--soft);border-radius:8px;padding:8px 16px;margin:0 0 8px;white-space:pre-wrap;
overflow-wrap:anywhere}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px 24px;margin:16px 0}
@media (max-width:600px){.card{padding:16px}}
.decide{border-left:4px solid var(--accent)}
.k{font-size:13px;line-height:20px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;
margin-top:8px}
.title-row{display:flex;flex-wrap:wrap;align-items:center;gap:8px}
.pill{display:inline-block;min-width:72px;padding:2px 8px;border-radius:999px;font-size:12px;line-height:18px;
font-weight:600;text-align:center;white-space:nowrap;border:1px solid transparent}
.pill.auto{min-width:0}
.p-fatal{background:var(--fatal);color:var(--on-fatal)}
.p-error{background:var(--error-bg);color:var(--error);border-color:var(--error)}
.p-warning{background:var(--warn-bg);color:var(--warn)}.p-ok{background:var(--ok-bg);color:var(--ok)}
.p-bad{background:var(--fatal-bg);color:var(--fatal)}.p-info{background:var(--info-bg);color:var(--info)}
.p-muted{background:var(--soft);color:var(--muted)}
.counts{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 8px}
.counts .pill{font-size:14px;line-height:22px;padding:2px 16px;min-width:96px}
.headline{margin:0 0 8px;padding-bottom:8px;border-bottom:1px solid var(--line)}
.issues{list-style:none;margin:0;padding:0}
.issue{display:grid;grid-template-columns:80px minmax(0,1fr);gap:4px 16px;align-items:start;margin:8px 0;
padding:8px 16px;
background:var(--card);border:1px solid var(--line);border-left:4px solid var(--line);border-radius:8px}
.issue.fatal{border-left-color:var(--fatal);background:var(--fatal-bg)}
.issue.error{border-left-color:var(--error)}.issue.warning{border-left-color:var(--warn)}
.issue .pill{margin-top:2px;justify-self:start}
.issue .meta{font-size:13px;line-height:20px;color:var(--muted)}
.issue details{margin:4px 0 0;padding:0;border:0;background:none}
.issue details>summary{padding:0;font-size:13px;font-weight:400;color:var(--accent)}
.issue details[open]>summary{border:0;margin:0 0 4px}
.issue blockquote{margin:4px 0;padding:4px 8px;border-left:2px solid var(--line);font-size:13px;line-height:20px}
@media (max-width:600px){.issue{grid-template-columns:minmax(0,1fr)}}
.tbl{overflow-x:auto;margin:0 0 16px}
table{border-collapse:collapse;font-size:14px;line-height:20px;font-variant-numeric:tabular-nums}
table.wide{width:100%}
th,td{text-align:left;vertical-align:top;padding:4px 12px;border-bottom:1px solid var(--line)}
th:first-child,td:first-child{padding-left:8px}
th{font-size:13px;font-weight:600;color:var(--muted);background:var(--soft);white-space:nowrap}
th.num,td.num{text-align:right;white-space:nowrap}
td.nowrap{white-space:nowrap}
table.kv td:first-child{color:var(--muted);padding-right:24px}
td .pill{min-width:0}
.v-bad{color:var(--fatal);font-weight:600}.v-warn{color:var(--error);font-weight:600}.v-ok{color:var(--ok)}
.v-k{font-weight:600}.v-dim{color:var(--muted)}
details{background:var(--card);border:1px solid var(--line);border-radius:8px;margin:8px 0;padding:0 16px}
details>summary{cursor:pointer;padding:8px 0;font-weight:600}
details[open]>summary{border-bottom:1px solid var(--line);margin-bottom:8px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:0 24px}
.note{font-size:13px;line-height:20px;color:var(--muted);margin:0 0 16px}
.narr{background:var(--ai-bg);border:1px dashed var(--ai);border-radius:8px;padding:16px 24px;margin:16px 0}
.narr .tag{display:inline-block;background:var(--ai);color:var(--ai-bg);border-radius:4px;padding:0 8px;font-size:12px;
font-weight:700;margin-right:8px}
.narr .meta{font-size:13px;line-height:20px;color:var(--muted);margin:4px 0 8px}
.panel{background:var(--card);border:2px solid var(--accent);border-radius:8px;padding:16px 24px;margin:16px 0}
.panel form{margin:8px 0 0;padding:8px 0 0;border-top:1px solid var(--line)}
.panel label{display:block;margin:4px 0}
.panel textarea{width:100%;min-height:48px;font:inherit;padding:8px;border:1px solid var(--line);border-radius:8px;
background:var(--bg);color:var(--fg)}
.panel button{font:inherit;font-weight:600;padding:4px 24px;border-radius:8px;border:1px solid var(--line);
cursor:pointer;margin-top:8px}
.b-approve{background:var(--ok-bg);color:var(--ok)}.b-reject{background:var(--fatal-bg);color:var(--fatal)}
.b-pending{background:var(--warn-bg);color:var(--warn)}
.msg{padding:8px 16px;border-radius:8px;margin:8px 0}
.msg.ok{background:var(--ok-bg)}.msg.bad{background:var(--fatal-bg)}
@media print{details{border:0}.panel{display:none}}
"""

PILL = {"fatal": "p-fatal", "error": "p-error", "warning": "p-warning", "ok": "p-ok", "bad": "p-bad",
        "warn": "p-warning",
        "info": "p-info", "lvl": "p-muted", "muted": "p-muted"}
VERDICT_CLASS = {"accept": "ok", "accept_with_findings": "warning", "reject": "bad"}
SEV_CLASS = {"high": "error", "medium": "error", "low": "muted", "info": "muted"}
_NUMERIC = re.compile(r"^[+-]?[\d,]+(?:\.\d+)?(?: ns)?$|^—$")


def e(text) -> str:
    return html.escape(str(text), quote=True)


def _badge(text: str, cls: str, auto: bool = True) -> str:
    return f'<span class="pill {PILL.get(cls, "p-muted")}{" auto" if auto else ""}">{e(text)}</span>'


def _level_badge(level: str) -> str:
    return _badge(TITLE[level], level, auto=False)


def levels_html(c: dict) -> str:
    return '<div class="counts">' + "".join(
        _badge(f"{TITLE[lvl]} {c[lvl]}", lvl if c[lvl] else "muted", auto=False) for lvl in LEVELS) + "</div>"


def _numeric_columns(tb: Table) -> list[bool]:
    cols = []
    for j in range(len(tb.cols)):
        cells = [cell_text(row[j]) for row in tb.rows if j < len(row)]
        cols.append(bool(cells) and all(_NUMERIC.match(x) for x in cells) and any(x != "—" for x in cells))
    return cols


def plain_link(ref: str) -> str:
    """A file reference as copyable text (the static page has no viewer to link to)."""
    return f'<code class="ref">{e(ref)}</code>'


def static_linker(r: dict, sources):
    """References as text, with the absolute path as a tooltip and "(file not found)" when the
    file is not there."""
    def link(ref: str) -> str:
        path, _ = sources.resolve(r, ref)
        found = bool(path) and path.is_file()
        title = f' title="{e(path)}"' if path else ""
        return (f'<code class="ref"{title}>{e(ref)}</code>'
                + ("" if found else ' <span class="small muted">(file not found)</span>'))
    return link


def _refs(text: str, link) -> str:
    return ", ".join(link(x.strip()) for x in text.split(",") if x.strip())


def _cell_html(c, numeric: bool, link=plain_link) -> str:
    text = cell_text(c)
    cls = c.get("cls", "") if isinstance(c, dict) else ""
    inner = e(text).replace("\n", "<br>")
    if cls == "ref" and text != "—":
        return f"<td>{_refs(text, link)}</td>"
    if cls and not _NUMERIC.match(text) and cls in PILL and cls not in ("muted",):
        inner = _badge(text, cls)
    elif cls:
        inner = f'<span class="v-{cls}">{inner}</span>'
    return f'<td class="num">{inner}</td>' if numeric else f"<td>{inner}</td>"


def table_html(tb: Table, link=plain_link) -> str:
    kv = len(tb.cols) == 2 and tb.cols[0] == "item"
    numeric = [False, False] if kv else _numeric_columns(tb)
    wide = not kv and any(len(cell_text(c)) > 48 for row in tb.rows for c in row)
    cls = " ".join(x for x in ("kv" if kv else "", "wide" if wide else "") if x)
    body = (f'<div class="tbl"><table{f" class={chr(34)}{cls}{chr(34)}" if cls else ""}><tr>'
            + "".join(f'<th{" class=num" if n else ""}>{e(c)}</th>' for c, n in zip(tb.cols, numeric)) + "</tr>"
            + "".join("<tr>" + "".join(_cell_html(c, n, link) for c, n in zip(row, numeric)) + "</tr>"
                      for row in tb.rows)
            + "</table></div>")
    note = f'<p class="note">{e(tb.note)}</p>' if tb.note else ""
    if tb.collapsed:
        return f"<details><summary>{e(tb.title)} ({len(tb.rows)})</summary>{note}{body}</details>"
    return f"<h3>{e(tb.title)}</h3>{note}{body}"


_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`]+)`")


def _inline(text: str) -> str:
    return _CODE.sub(r"<code>\1</code>", _BOLD.sub(r"<b>\1</b>", e(text)))


def md_light(text: str) -> str:
    """The few Markdown forms a model summary uses (paragraphs, bullets, headings, bold, code);
    everything is escaped first, so nothing in the text can become HTML."""
    out, in_list = [], False
    for line in text.splitlines():
        s = line.strip()
        bullet = re.match(r"^[-*•]\s+(.*)$|^\d+[.)]\s+(.*)$", s)
        if bullet:
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(bullet.group(1) or bullet.group(2))}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if not s:
            continue
        heading = re.match(r"^#{1,6}\s+(.*)$", s)
        out.append(f"<h3>{_inline(heading.group(1))}</h3>" if heading else f"<p>{_inline(s)}</p>")
    if in_list:
        out.append("</ul>")
    return "".join(out)


def _narrative_html(r: dict) -> str:
    n = r.get("narrative")
    if not n:
        return ""
    meta, gate = narrative_meta(r, n)
    reasons = ""
    body = md_light(n["text"])
    if not n["gate"]["passed"]:
        last = (n["gate"].get("decisions") or [{}])[-1].get("reasons", [])
        reasons = "<ul>" + "".join(f"<li>{_inline(x)}</li>" for x in last) + "</ul>"
        body = f"<details><summary>{e(gate)}</summary>{body}</details>"
    return (f'<div class="narr"><div><span class="tag">AI</span><b>Summary written by a model</b> '
            f'{_badge(gate, "ok" if n["gate"]["passed"] else "bad")}</div>'
            f'<div class="meta">{e(meta)}</div>{reasons}{body}</div>')


def _args_text(args: str) -> str:
    """Tool arguments as short text: {"cases": ["smoke"]} -> cases: smoke."""
    try:
        data = json.loads(args)
    except ValueError:
        return args
    return "; ".join(f"{k}: {', '.join(map(str, v)) if isinstance(v, list) else v}" for k, v in data.items())


def _issue_html(i: Issue, link=plain_link) -> str:
    meta = " · ".join(x for x in (e(i.source), _refs(i.location, link) if i.location else "") if x)
    more = ""
    quote = i.quote
    if quote and len(quote) <= 160 and not i.details:
        meta += (" · " if meta else "") + _inline(quote)
        quote = ""
    texts = ([quote] if quote else []) + i.details
    if texts:
        label = f"show the {len(texts)} full text(s)" if len(texts) > 1 else "show the full text"
        more = (f"<details><summary>{label}</summary>"
                + "".join(f"<blockquote>{_inline(t)}</blockquote>" for t in texts) + "</details>")
    return (f'<li class="issue {i.level}">{_level_badge(i.level)}<div><div>{_inline(i.text)}</div>'
            f'{f"<div class={chr(34)}meta{chr(34)}>{meta}</div>" if meta else ""}{more}</div></li>')


def render_html(r: dict, panel: str = "", link=plain_link) -> str:
    """The receipt as one self-contained page (no script, nothing loaded from elsewhere).
    `panel`: HTML put under the decision card (the review page's buttons); `link`: how a file
    reference is shown (text here, a link to the source viewer on the review page)."""
    s = summarize(r)
    h = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         f"<title>Review summary {e(s.run_id)}</title><style>{CSS}</style></head><body><main>",
         f'<div class="title-row"><h1>Review summary · {e(s.run_id)}</h1>'
         f"{_badge(s.status, s.status_class)}</div>",
         f'<p class="note">{e(GENERATED)}</p>',
         '<div class="card decide">', levels_html(s.counts),
         f'<p class="headline">{e(s.headline)}</p>',
         f'<div class="k">Task</div><p>{e(s.task)}</p>',
         f'<div class="k">What you decide</div><p>{e(s.decision)}</p>']
    if s.commands:
        h.append(f'<div class="k">To approve</div><p>{e(s.needs)}</p>')
        h.append(f'<div class="k">Commands</div><pre>{e(chr(10).join(s.commands))}</pre>'
                 '<p class="small muted">Or decide with buttons in the local review page: '
                 "<code>eda-agent review-ui</code>.</p>")
    elif s.commands_note:
        h.append(f'<p class="small muted">Commands: {e(s.commands_note)}</p>')
    h.append(f'<p class="small muted">{e(s.run_line)}</p></div>')
    h.append(panel)

    blocking = [i for i in s.issues if i.level != "warning"]
    warnings = [i for i in s.issues if i.level == "warning"]
    h.append("<h2>Look at these before approving</h2>")
    h.append('<ul class="issues">' + "".join(_issue_html(i, link) for i in blocking) + "</ul>" if blocking
             else '<p class="muted">No Fatal or Error items.</p>')
    if warnings:
        h.append(f'<details><summary>{len(warnings)} warning(s)</summary><ul class="issues">'
                 + "".join(_issue_html(i, link) for i in warnings) + "</ul></details>")
    h.append(_narrative_html(r))

    h.append("<h2>Checks</h2>")
    if s.checks:
        h.append('<div class="tbl"><table class="wide"><tr><th>check</th><th>result</th><th>evidence</th>'
                 '<th class="num">time (s)</th><th>tool summary</th></tr>')
        for c in s.checks:
            args = f'<div class="small muted mono">{e(_args_text(c["args"]))}</div>' if c["args"] else ""
            h.append(f"<tr><td><b>{e(c['tool'])}</b>{args}</td>"
                     f"<td>{_badge(c['status_text'], STATUS_CLASS.get(c['status'], 'warning'))}</td>"
                     f'<td class="nowrap"><span class="mono">{e(c["level"])}</span> '
                     f'<span class="muted">{e(c["level_text"])}</span></td>'
                     f'<td class="num">{e(fmt_secs(c.get("duration")))}</td><td>{e(c["summary"])}</td></tr>')
        h.append("</table></div>")
    else:
        h.append('<p class="muted">No check ran.</p>')

    h.append('<div class="grid"><div><h2>What the evidence allows</h2><ul>'
             + "".join(f"<li>{e(x)}</li>" for x in s.evidence) + "</ul></div>")
    h.append("<div><h2>Not checked</h2><ul>" + "".join(f"<li>{e(x)}</li>" for x in s.not_checked)
             + "</ul></div></div>")
    gate_cls = {"passed": "ok", "rejected": "bad"}.get(s.gate_status, "warning")
    h.append(f"<h2>Evidence gate</h2><p>{_badge(s.gate_status, gate_cls)} {e(s.gate)}</p>")
    if s.gate_reasons:
        h.append("<ul>" + "".join(f"<li>{_inline(x)}</li>" for x in s.gate_reasons) + "</ul>")
    h.append("<h2>Reviewers</h2>")
    if s.reviews:
        h.append('<div class="tbl"><table class="wide"><tr><th>reviewer</th><th>verdict</th><th>findings</th>'
                 "<th>most serious</th></tr>")
        for v in s.reviews:
            counts_html = " ".join(_badge(f"{TITLE[k]} {n}", k) for k, n in v["counts"].items()) or "none"
            worst = ""
            if v["worst"] and v["worst"]["severity"] in ("high", "medium"):
                worst = _inline(first_sentence(v["worst"]["text"], 120))
            h.append(f"<tr><td><b>{e(v['who'])}</b></td>"
                     f"<td>{_badge(v['verdict_text'], VERDICT_CLASS.get(v['verdict'], 'warning'))}</td>"
                     f"<td>{counts_html}</td><td>{worst}</td></tr>")
        h.append("</table></div>")
    else:
        h.append(f'<p class="muted">{e(s.no_review[:1].upper() + s.no_review[1:])}.</p>')
    h.append(f"<h2>Approval</h2><p>{e(s.approval)}</p>")
    if s.notes:
        h.append("<ul>" + "".join(f"<li>note: {e(n)}</li>" for n in s.notes) + "</ul>")

    h.append("<h2>Data</h2>")
    if trace_note(r):
        h.append(f'<p class="note">{e(trace_note(r))}</p>')
    h += [table_html(tb, link) for tb in data_tables(r)]
    h.append("<h2>Details (full receipt)</h2>")
    h += _details_html(r, link)
    h.append("</main></body></html>")
    return "\n".join(h) + "\n"


def _details_html(r: dict, link=plain_link) -> list[str]:
    """Everything in the receipt, collapsed."""
    if receipt_kind(r) != "run":  # write-tests and import receipts: their full receipt as in the .md
        return [f"<details><summary>Full receipt (as in the .md)</summary><pre>{e(details_md(r))}</pre></details>"]
    def sec(title: str, body: str, open_: bool = False) -> str:
        return f"<details{' open' if open_ else ''}><summary>{e(title)}</summary>{body}</details>"

    def ul(items) -> str:
        items = list(items)
        return "<ul>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>" if items else '<p class="muted">(none)</p>'

    gate = r["gate"]
    usage = r.get("usage") or {}
    out = [sec("Run", ul([
        f"<b>Task:</b> {e(r['task'])}",
        f"<b>Project:</b> {e(r['project'])} · <b>Model:</b> {e(r['provider'])} / {e(r['model'])} · "
        f"<b>Prompt:</b> {e(r.get('prompt') or 'n/a')}"
        + (f" · <b>Preset:</b> {e(r['preset'])}" if r.get("preset") else ""),
        f"<b>Stop:</b> {e(r['stop_reason'])} · <b>Status:</b> {e(r['status'])}"
        + (f" (agent: {e(r['agent_status'])})" if r.get("agent_status") else "")
        + f" · <b>Gate:</b> {e(gate['status'])} ({gate['rewrites']} rewrite(s))",
        f"<b>Cost:</b> {r['turns']} turns, {r['tool_calls']} tool calls ({r['tool_errors']} errors), "
        f"{usage.get('input_tokens', 0)} in / {usage.get('output_tokens', 0)} out tokens, {r['wall_s']} s",
    ] + ([f"<b>SDC origin:</b> {e(r['sdc_origin'])}"] if r.get("sdc_origin") else [])))]

    checks = []
    for c in r["checks_run"]:
        rows = [f"<b>status</b> {e(c['status'])} · <b>evidence</b> {e(c['evidence_level'])}",
                f"<b>summary</b> {e(c['summary'])}", f"<b>claim scope</b> {e(c['claim_scope'])}"]
        rows += [f"artifact {link(a['path'])} sha256 <code>{e(a['sha256'])}</code>" for a in c["artifacts"]]
        rows += [f"{e(k)}: <code>{e(v)}</code>" for k, v in c.get("tool_versions", {}).items()]
        if c.get("sdc_sha256"):
            rows.append(f"SDC {link(c.get('sdc') or '')} sha256 <code>{e(c['sdc_sha256'])}</code>")
        checks.append(f"<h3>{e(c['tool'])} <code>{e(json.dumps(c['args'], ensure_ascii=False))}</code></h3>{ul(rows)}")
    if r.get("other_tool_calls"):
        checks.append("<h3>Other tool calls</h3>" + ul(
            f"{e(o['tool'])} <code>{e(json.dumps(o['args'], ensure_ascii=False))}</code> {e(o['status'])}"
            for o in r["other_tool_calls"]))
    out.append(sec("Checks run: claim scope, artifacts, tool versions",
                   "".join(checks) or '<p class="muted">(none)</p>'))

    timed = [c for c in r["checks_run"] if c.get("timing")]
    if timed:
        rows = "".join(
            f"<tr><td>{e(c['tool'])}</td><td>{e(tm['clock_period'])} ns</td><td>{e(tm['wns'])}</td>"
            f"<td>{e(tm['tns'])}</td><td>{e(tm['hold_wns'])}</td><td>{e(tm['worst_corner'])}</td>"
            f"<td>{'yes' if tm['timing_met'] else 'no'}</td><td>{e(tm.get('cells'))}</td><td>{e(tm.get('area'))}</td>"
            f"<td>{e(tm.get('latches'))}</td><td><code>{e(c['sdc_sha256'][:16])}…</code></td>"
            f"<td>{'VOID (SDC changed later in the run)' if tm.get('void') else 'no'}</td></tr>"
            for c in timed for tm in [c["timing"]])
        out.append(sec("Timing estimates (E4, pre-PnR)",
                       '<div class="tbl"><table><tr><th>tool</th><th>clock</th><th>setup WNS</th><th>TNS</th>'
                       "<th>hold WNS</th><th>worst corner</th><th>met</th><th>cells</th><th>area</th><th>latches</th>"
                       f"<th>SDC sha256</th><th>void</th></tr>{rows}</table></div>"))

    nr = r["not_run"]
    out.append(sec("Not run", ul([
        f"lint run: {'yes' if nr['lint_run'] else 'no'}",
        f"described but not run: {e(', '.join(nr['described_but_not_run']) or 'none')}",
        f"cases in the list not run ({len(nr['cases_not_run'])}): {e(', '.join(nr['cases_not_run']) or 'none')}"])))

    if gate["decisions"]:
        out.append(sec("Gate decisions", "<ol>" + "".join(
            f"<li>{'pass' if d['passed'] else 'reject'} (best passing evidence {e(d['max_level'] or 'none')})"
            + (ul(f'<span class="q">{e(x)}</span>' for x in d["reasons"]) if d["reasons"] else "") + "</li>"
            for d in gate["decisions"]) + "</ol>"))

    out.append(sec("Final claims (the agent's report, verbatim)", f"<pre>{e(r['final_claims'] or '(none)')}</pre>"))
    out.append(sec("Residual risk", f"<pre>{e(r['residual_risk']['report'] or '(none stated in the report)')}</pre>"
                   + "<p><b>From the tools:</b></p>" + ul(e(x) for x in r["residual_risk"]["from_tools"])))

    for v in r.get("reviews", []):
        who = ("rule-based (no model)" if v["reviewer"] == "rules"
               else f"{v['reviewer']}, {v['provider']} / {v['model']}")
        rows = [f"<b>Saw:</b> {e(v['context'])}",
                f"<b>Verdict:</b> {_badge(v['verdict'] or '(none given)', VERDICT_CLASS.get(v['verdict'], 'warn'))}"
                + ("" if v.get("format_ok", True) else " · format not followed (checklist first, findings, verdict)")]
        if v.get("note"):
            rows.append(f"<b>Note:</b> {e(v['note'])}")
        if v.get("tool_calls"):
            rows.append("<b>Re-ran:</b> " + "; ".join(
                f"{e(c['tool'])} <code>{e(json.dumps(c['args'], ensure_ascii=False))}</code> → {e(c['status'])} "
                f"{e(c['evidence_level'])}" for c in v["tool_calls"]))
        body = ul(rows) + "<h3>Checklist (written before the findings)</h3>" + ul(e(x) for x in v["checklist"])
        body += "<h3>Findings</h3>" + ul(
            f"{_level_badge(FINDING_LEVEL.get(f['severity'], 'warning'))} "
            f"{_badge(f['severity'], SEV_CLASS.get(f['severity'], 'lvl'))} "
            f'<span class="q">{e(f["text"])}</span>' for f in v["findings"])
        out.append(sec(f"Review: {who} — {v['verdict'] or '(none given)'}", body))
    if r.get("review_error"):
        out.append(sec("Review error", f'<p class="q">{e(r["review_error"])}</p>'))

    a = r.get("approval")
    if a:
        rows = [f"<b>Step:</b> {e(a.get('step'))} · <b>Status:</b> {e(a['status'])}",
                f"requested: {e(a.get('requested_at'))} ({e(a.get('why'))})"]
        rows += [f"note by {e(n.get('by'))} at {e(n.get('at'))}: {e(n.get('note') or '')}" for n in a.get("notes", [])]
        if a["status"] != "pending":
            rows += [f"decided by <b>{e(a['by'])}</b> at {e(a['at'])}"
                     + (f": {e(a['reason'])}" if a.get("reason") else ""),
                     f"receipt content sha256 at decision: <code>{e(a['receipt_sha256'])}</code>"]
            if a.get("levels"):
                rows.append(f"levels at the decision: {e(counts_line(a['levels']))}")
            if a.get("overrode_gate") or a.get("override"):
                rows.append("this approval overrode a Fatal item or the evidence gate's rejection")
        out.append(sec("Human approval", ul(rows)))
    if r.get("trace"):
        out.append(sec("Trace", f"<p>{link(r['trace'])}</p>"))
    return out
