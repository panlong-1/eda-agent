"""Failure localisation in a receipt: the trace-chain table and a small waveform drawing.

Both come from the run's own diagnostic results (trace_driver, trace_load, waveform_query), never
from the model's text. The chain table is a data table (.md and .html); on the review page its
file:line cells open the source view. The waveform is inline SVG drawn here (no script, no library):
only the signals a waveform_query of the run read, only its time window, with the failing time
the trace used marked.
"""
from __future__ import annotations

import html

from .tables import Table, cell

TRACE_TOOLS = ("trace_driver", "trace_load")
MAX_ROWS = 40
MAX_WAVE_SIGNALS = 12
NOTE = ("A diagnosis from trace and waveform facts of this run: a hypothesis about the cause, not a proof and "
        "not a fix. Values are at the time the trace used.")


def _short(path: str, top: str) -> str:
    return path[len(top) + 1:] if top and path.startswith(top + ".") else path


def _vals(step: dict, top: str) -> str:
    vals = step.get("values") or {}
    if not vals:
        return "—"
    own = vals.get(step["signal"])
    rest = [f"{_short(k, top).rsplit('.', 1)[-1]}={v}" for k, v in vals.items() if k != step["signal"]]
    return (f"{own}" if own is not None else "?") + (f" (reads: {', '.join(rest[:6])}"
                                                      + (" …" if len(rest) > 6 else "") + ")" if rest else "")


def localise_tables(r: dict) -> list[Table]:
    out = []
    for c in r.get("checks_run", []):
        if c["tool"] not in TRACE_TOOLS or c.get("status") != "pass":
            continue
        m = c.get("metrics") or {}
        top = ((m.get("compile") or {}).get("top")) or ""
        t = m.get("time")
        title = (f"Trace chain ({c['tool']}): {m.get('direction', '')} of {_short(m.get('signal', ''), top)}"
                 + (f", values at t={t}" if m.get("waveform") else ""))
        tb = Table(title, ["#", "signal", "written by", "source", "under condition", "condition at", "value at t"],
                   note=NOTE + (" Truncated: more steps exist." if m.get("truncated") else ""))
        for i, s in enumerate((m.get("steps") or [])[:MAX_ROWS], 1):
            conds = s.get("conditions") or []
            tb.rows.append([
                f"{s.get('depth', '')}.{i}", _short(s["signal"], top),
                f"{s['kind']}: {s.get('text', '')}" + (f" (port {_short(s['via'], top)})" if s.get("via") else ""),
                cell(s["at"], "ref") if s.get("at") else "—",
                "; ".join(f"{x['text']} [{x['branch']}]" for x in conds) or "—",
                cell(", ".join(dict.fromkeys(x["at"] for x in conds)), "ref") if conds else "—",
                _vals(s, top)])
        out.append(tb)
    return out


# ---------------------------------------------------------------- the waveform drawing

def _e(x) -> str:
    return html.escape(str(x), quote=True)


def _failing_time(r: dict) -> int | None:
    for c in reversed(r.get("checks_run", [])):
        m = c.get("metrics") or {}
        if c["tool"] in TRACE_TOOLS and m.get("waveform") and isinstance(m.get("time"), int):
            return m["time"]
    return None


def wave_svg(m: dict, mark: int | None = None) -> str:
    """One waveform_query result as an SVG: a row per signal, 1-bit as a line, buses as segments."""
    sigs = (m.get("signals") or [])[:MAX_WAVE_SIGNALS]
    start, end = (m.get("window") or [0, 0])[:2]
    if not sigs or end <= start:
        return ""
    label_w, plot_w, row_h, top_pad = 230, 560, 26, 22
    width, height = label_w + plot_w + 10, top_pad + row_h * len(sigs) + 18

    def x(t: int) -> float:
        return label_w + plot_w * (min(max(t, start), end) - start) / (end - start)

    out = [f'<svg class="wave" role="img" viewBox="0 0 {width} {height}" width="100%" '
           f'style="max-width:{width}px;font:11px ui-monospace,monospace" '
           f'aria-label="waveform of {len(sigs)} signals from t={start} to t={end}">']
    out.append(f'<text x="{label_w}" y="12" style="fill:var(--muted)">t={_e(start)}</text>'
               f'<text x="{label_w + plot_w}" y="12" text-anchor="end" style="fill:var(--muted)">t={_e(end)} '
               f'({_e(m.get("timescale", ""))})</text>')
    for i, s in enumerate(sigs):
        y0 = top_pad + i * row_h
        hi, lo, mid = y0 + 4, y0 + row_h - 6, y0 + row_h / 2 - 1
        name = s.get("slang") or s.get("waveform_path") or s.get("signal")
        short = name.split(".")[-2:] if "." in name else [name]
        label = ".".join(short) + (" …" if s.get("truncated") else "")
        out.append(f'<text x="4" y="{mid + 4:.1f}" style="fill:var(--fg)"><title>{_e(name)}</title>{_e(label)}</text>')
        out.append(f'<line x1="{label_w}" y1="{lo + 3}" x2="{label_w + plot_w}" y2="{lo + 3}" '
                   'style="stroke:var(--line)"/>')
        segs = [(start, s.get("at_start"))] + [(int(t), v) for t, v in s.get("changes") or []]
        last_t = int(s["changes"][-1][0]) if s.get("truncated") and s.get("changes") else end
        for j, (t0, v) in enumerate(segs):
            t1 = segs[j + 1][0] if j + 1 < len(segs) else last_t
            if t1 <= t0 and j + 1 < len(segs):
                continue
            xa, xb = x(t0), x(t1)
            v = "?" if v is None else str(v)
            bad = "x" in v.lower().split("'")[-1] or "z" in v.lower().split("'")[-1]
            colour = "var(--fatal)" if bad else "var(--accent)"
            if int(s.get("width") or 0) <= 1 and not bad:
                yv = hi if v == "1" else lo
                out.append(f'<line x1="{xa:.1f}" y1="{yv}" x2="{xb:.1f}" y2="{yv}" style="stroke:{colour};stroke-width:1.5"/>')
                if j:
                    out.append(f'<line x1="{xa:.1f}" y1="{hi}" x2="{xa:.1f}" y2="{lo}" style="stroke:{colour};stroke-width:1.5"/>')
            else:
                d = 3 if xb - xa > 8 else 0
                out.append(f'<polygon points="{xa:.1f},{mid} {xa + d:.1f},{hi} {xb - d:.1f},{hi} {xb:.1f},{mid} '
                           f'{xb - d:.1f},{lo} {xa + d:.1f},{lo}" style="fill:none;stroke:{colour};stroke-width:1.2">'
                           f'<title>{_e(name)} = {_e(v)} from t={_e(t0)}</title></polygon>')
                txt = v.split("'", 1)[-1][1:] if "'" in v else v
                if xb - xa > 7 * len(txt) + 8:
                    out.append(f'<text x="{(xa + xb) / 2:.1f}" y="{mid + 4:.1f}" text-anchor="middle" '
                               f'style="fill:var(--fg)">{_e(txt)}</text>')
        if s.get("truncated"):
            out.append(f'<text x="{x(last_t) + 4:.1f}" y="{mid + 4:.1f}" style="fill:var(--muted)">more changes '
                       'not read</text>')
    if mark is not None and start <= mark <= end:
        out.append(f'<line x1="{x(mark):.1f}" y1="16" x2="{x(mark):.1f}" y2="{height - 14}" '
                   'style="stroke:var(--fatal);stroke-dasharray:4 3"/>'
                   f'<text x="{x(mark) + 3:.1f}" y="{height - 3}" style="fill:var(--fatal)">t={_e(mark)} (trace)</text>')
    out.append("</svg>")
    return "".join(out)


def localise_html(r: dict, link) -> str:
    """The review page's localisation section: chain tables and up to two waveform drawings."""
    from .page import table_html  # page.py imports this module
    tables = localise_tables(r)
    waves = [c for c in r.get("checks_run", []) if c["tool"] == "waveform_query" and c.get("status") == "pass"]
    if not tables and not waves:
        return ""
    h = ["<h2>Failure localisation (diagnosis)</h2>", f'<p class="note">{_e(NOTE)}</p>']
    h += [table_html(tb, link) for tb in tables]
    mark = _failing_time(r)
    for c in waves[-2:]:
        m = c.get("metrics") or {}
        svg = wave_svg(m, mark)
        if svg:
            h.append(f"<h3>Waveform: {len((m.get('signals') or [])[:MAX_WAVE_SIGNALS])} signal(s), "
                     f"t={_e((m.get('window') or ['?'])[0])}…{_e((m.get('window') or ['?', '?'])[1])}</h3>")
            h.append(f'<p class="note">From {link(m.get("waveform", ""))}; '
                     f'{_e(m.get("mapping_note") or "")}</p><div class="tbl">{svg}</div>')
    return "\n".join(h)
