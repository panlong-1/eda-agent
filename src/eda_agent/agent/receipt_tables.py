"""Data tables of a receipt: the numbers behind each check, the findings, the gate and the cost.

Plain code over the receipt data. Receipts written before the per-check numbers were kept
(metrics, diagnostics, duration) get them, for display only, from the run's own trace when it
is still there (`with_trace_data`); the .json is never changed, and the tables say so.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .levels import DIAG_LEVEL, FINDING_LEVEL, HOLD_MARGIN_NS, SETUP_MARGIN, TITLE

SEVERITIES = ("high", "medium", "low", "info")
_LOCATION = re.compile(r"[\w./-]+\.(?:sv|svh|v|vh|vhd|sdc|tcl|S|s|c|h|py|yaml|toml)(?::\d+)?(?![\w])")

TT = {
    "data": "Data",
    "from_trace": "† in a table title: its numbers come from the run's trace ({trace}), because this "
                  "receipt was written before receipts kept them; the receipt .json is unchanged.",
    # regress
    "regress": "Simulation (regress)",
    "cases_asked": "cases asked", "run": "run", "passed": "passed",
    "failed": "failed", "skipped": "skipped", "subtests": "testbench sub-tests",
    "failed_cases": "failed cases", "format": "result format",
    "sub_fmt": "{p} passed, {f} failed of {t}",
    # lint
    "lint": "Lint", "errors": "errors", "warnings": "warnings",
    "lint_by_code": "Lint: by warning code", "code": "code", "count": "count",
    "lint_by_file": "Lint: by file (of the {n} of {total} diagnostics kept)",
    "file": "file", "lint_list": "Lint: diagnostics kept ({n} of {total})",
    "severity": "severity", "level": "level", "location": "location", "message": "message",
    # synth
    "synth": "Synthesis and timing estimate (synth, pre-PnR)",
    "item": "item", "value": "value",
    "cells": "cells", "area": "area", "seq_area": "sequential area",
    "latches": "latches", "liberty": "library", "clock": "clock",
    "timing_met": "timing met (under this SDC)", "sdc": "SDC",
    "yes": "yes", "no": "NO",
    "corners": "Timing per corner (ns; negative = violation)",
    "corner": "corner", "setup_wns": "setup WNS", "setup_tns": "setup TNS",
    "hold_wns": "hold WNS", "hold_tns": "hold TNS", "worst": "worst",
    "worst_path": "Worst path", "start": "startpoint", "end": "endpoint",
    "slack": "slack", "arrival": "arrival",
    "cell_types": "Most used cells", "cell": "cell",
    "void": "VOID: the SDC changed later in the run",
    # check_sdc
    "check_sdc": "Constraint check (check_sdc)", "clock_name": "clock",
    "period": "period (ns)", "sources": "sources",
    "unconstrained": "unconstrained endpoints",
    "other_warn": "other check_setup warnings",
    # generic
    "other": "{tool}: numbers",
    # findings, gate, cost
    "findings": "Reviewer findings", "reviewer": "reviewer", "finding": "finding",
    "no_findings": "no findings",
    "gate": "Evidence gate decisions", "attempt": "attempt", "result": "result",
    "best_level": "best passing evidence", "reasons": "reasons",
    "pass": "pass", "reject": "reject",
    "cost": "Cost", "who": "who", "model": "model", "turns": "turns",
    "tool_calls": "tool calls", "tok_in": "tokens in", "tok_out": "tokens out",
    "tok_cache": "cached in", "wall": "wall time (s)",
    "author": "author (the agent)", "summary_writer": "summary writer (model)",
    "rules": "rule-based (no model)", "times": " ×{n}",
    "total": "total",
}


def tt(key: str, **kw) -> str:
    return TT[key].format(**kw)


@dataclass
class Table:
    title: str
    cols: list[str]
    rows: list[list] = field(default_factory=list)   # a cell is a str, or {"text": ..., "cls": ...}
    note: str = ""
    collapsed: bool = False                          # long lists: folded in the HTML, after the summary in .md


def cell(text, cls: str = "") -> dict:
    return {"text": str(text), "cls": cls}


LEVEL_CLS = {"fatal": "fatal", "error": "bad", "warning": "warn"}


def level_cell(level: str) -> dict:
    return cell(TITLE[level], LEVEL_CLS[level])


def fmt_ns(x) -> str:
    """A slack or TNS in ns: signed to the ps, but a plain 0 for zero."""
    if not isinstance(x, (int, float)):
        return "—"
    return "0" if abs(x) < 0.0005 else f"{x:+.3f}"


def _num(x) -> str:
    if isinstance(x, float):
        return f"{x:,.1f}" if abs(x) >= 1000 else f"{x:g}"
    return f"{x:,}" if isinstance(x, int) else ("—" if x is None else str(x))


def _slack_cls(x, small: float | None = HOLD_MARGIN_NS) -> str:
    """Colour only what means something, by the same rule as levels.py: negative (red), under
    the small-slack margin (orange); fine values stay plain."""
    if not isinstance(x, (int, float)):
        return ""
    return "bad" if x < 0 else "warn" if small is not None and x < small else ""


def _tns_cls(x) -> str:
    return "bad" if isinstance(x, (int, float)) and x < 0 else ""


_NETLIST_NAME = re.compile(r"^_\d+_$")


def net(name: str):
    """A point of a path; Yosys's numbered internal nets are marked as such."""
    return cell(f"{name} (internal net)", "dim") if _NETLIST_NAME.match(name or "") else (name or "—")


# ---------------------------------------------------------------- trace data for older receipts

def resolve_trace(r: dict, base: Path | None) -> Path | None:
    trace = r.get("trace")
    if not trace:
        return None
    for p in (Path(trace), *(([base / Path(trace).name, base.parent / trace]) if base else [])):
        if p.is_file():
            return p
    return None


def with_trace_data(r: dict, base: Path | None = None) -> dict:
    """A copy of the receipt where checks without numbers get metrics, diagnostics and duration
    from the run's trace (matched in order by tool and args). The receipt itself is not changed."""
    if all("metrics" in c for c in r["checks_run"]):
        return r
    path = resolve_trace(r, base)
    if not path:
        return r
    calls, last_t = [], 0.0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        t = float(rec.get("t", last_t) or 0)
        if rec.get("kind") == "tool":
            try:
                result = json.loads(rec["result"])
            except (ValueError, TypeError):
                result = None
            if isinstance(result, dict):
                calls.append((rec["name"], rec.get("input"), result, round(t - last_t, 2)))
        last_t = t
    out = copy.deepcopy(r)
    i = 0
    for c in out["checks_run"]:
        if "metrics" in c:
            continue
        for j in range(i, len(calls)):
            name, args, result, duration = calls[j]
            if name == c["tool"] and args == c["args"] and result.get("summary") == c["summary"]:
                c.update(metrics=result.get("metrics") or {}, diagnostics=(result.get("diagnostics") or [])[:50],
                         diagnostics_total=result.get("diagnostics_total", 0), duration_s=duration, from_trace=True)
                i = j + 1
                break
    out["_trace_used"] = r["trace"]
    out["_source"] = r  # the receipt as saved, e.g. for its sha256
    return out


# ---------------------------------------------------------------- tables

def _counts_from_summary(summary: str) -> dict:
    out = {}
    m = re.search(r"(\d+) passed, (\d+) failed(?:, (\d+) skipped)?", summary)
    if m:
        out |= {"passed": int(m[1]), "failed": int(m[2]), "skipped": int(m[3] or 0)}
    m = re.search(r"sub-tests: (\d+) passed, (\d+) failed of (\d+)", summary)
    if m:
        out |= {"subtests_passed": int(m[1]), "subtests_failed": int(m[2]), "subtests_total": int(m[3])}
    return out


def _mark(c: dict) -> str:
    return " †" if c.get("from_trace") else ""


def regress_tables(r: dict) -> list[Table]:
    checks = [c for c in r["checks_run"] if c["tool"] == "regress"]
    if not checks:
        return []
    t = Table(tt("regress"), [tt(k) for k in ("cases_asked", "run", "passed", "failed", "skipped",
                                                         "subtests", "failed_cases")])
    for c in checks:
        m = {**_counts_from_summary(c["summary"]), **(c.get("metrics") or {})}
        asked = ", ".join(c["args"].get("cases", [])) or "—"
        ran = (m.get("passed", 0) + m.get("failed", 0)) if "passed" in m else "—"
        sub = (tt("sub_fmt", p=m["subtests_passed"], f=m["subtests_failed"], t=m["subtests_total"])
               if "subtests_total" in m else "—")
        t.rows.append([asked + _mark(c), _num(ran), cell(_num(m.get("passed", "—")), "ok" if m.get("passed") else ""),
                       cell(_num(m.get("failed", "—")), "bad" if m.get("failed") else ""),
                       cell(_num(m.get("skipped", "—")), "warn" if m.get("skipped") else ""),
                       cell(sub, "bad" if m.get("subtests_failed") else ""),
                       ", ".join(m.get("failed_cases", [])) or "—"])
    return [t]


def lint_tables(r: dict) -> list[Table]:
    out = []
    for c in (c for c in r["checks_run"] if c["tool"] == "lint"):
        m = c.get("metrics") or {}
        diags = c.get("diagnostics") or []
        total = c.get("diagnostics_total") or len(diags)
        if m.get("by_code"):
            t = Table(tt("lint_by_code") + _mark(c), [tt("code"), tt("count")])
            t.rows = [[code, _num(n)] for code, n in sorted(m["by_code"].items(), key=lambda x: -x[1])]
            t.note = f"{_num(m.get('errors', 0))} error(s), {_num(m.get('warnings', 0))} warning(s) in all"
            out.append(t)
        if diags:
            files: dict[str, list[int]] = {}
            for d in diags:
                f = files.setdefault(d.get("file") or "—", [0, 0])
                f[0 if d.get("severity") == "error" else 1] += 1
            t = Table(tt("lint_by_file", n=len(diags), total=total) + _mark(c),
                      [tt("file"), tt("errors"), tt("warnings")])
            t.rows = [[f, cell(_num(e), "bad" if e else ""), _num(w)]
                      for f, (e, w) in sorted(files.items(), key=lambda x: (-x[1][0], -x[1][1], x[0]))]
            out.append(t)
            t = Table(tt("lint_list", n=len(diags), total=total) + _mark(c),
                      [tt("level"), tt("code"), tt("location"), tt("message")], collapsed=True)
            t.rows = [[level_cell(DIAG_LEVEL.get(d.get("severity"), "warning")),
                       d.get("code", ""), f"{d.get('file', '')}:{d.get('line', '')}", d.get("msg", "")]
                      for d in diags]
            out.append(t)
    return out


def synth_tables(r: dict) -> list[Table]:
    out = []
    for c in (c for c in r["checks_run"] if c["tool"] == "synth"):
        m = {**(c.get("timing") or {}), **(c.get("metrics") or {})}
        void = (c.get("timing") or {}).get("void")
        t = Table(tt("synth") + _mark(c), [tt("item"), tt("value")])
        rows = [("cells", _num(m.get("cells"))), ("area", _num(m.get("area"))),
                ("seq_area", _num(m.get("sequential_area"))),
                ("latches", cell(_num(m.get("latches")), "bad" if m.get("latches") else "")),
                ("liberty", m.get("liberty") or "—"),
                ("clock", f"{m.get('clock_port') or ''} {_num(m.get('clock_period'))} ns".strip()),
                ("timing_met", cell(tt("yes") if m.get("timing_met") else tt("no"),
                                    "ok" if m.get("timing_met") else "bad")),
                ("sdc", f"{c.get('sdc') or m.get('sdc') or '—'} · sha256 {str(c.get('sdc_sha256') or '')[:16]}…")]
        t.rows = [[tt(k), v] for k, v in rows if v not in ("—", " ns")]
        if void:
            t.rows.append([cell(tt("void"), "bad"), ""])
        out.append(t)
        corners = m.get("corners") or {}
        period = m.get("clock_period")
        setup_small = SETUP_MARGIN * period if isinstance(period, (int, float)) else None
        if corners:
            worst = m.get("worst_corner")
            t = Table(tt("corners") + _mark(c), [tt(k) for k in ("corner", "setup_wns", "setup_tns",
                                                                         "hold_wns", "hold_tns")])
            for name, v in corners.items():
                t.rows.append([name + (f" ({tt('worst')})" if name == worst else ""),
                               cell(fmt_ns(v.get("setup_wns")), _slack_cls(v.get("setup_wns"), setup_small)),
                               cell(fmt_ns(v.get("setup_tns")), _tns_cls(v.get("setup_tns"))),
                               cell(fmt_ns(v.get("hold_wns")), _slack_cls(v.get("hold_wns"))),
                               cell(fmt_ns(v.get("hold_tns")), _tns_cls(v.get("hold_tns")))])
            out.append(t)
        elif "wns" in m:  # receipt timing only: the run's worst values
            t = Table(tt("corners"), [tt(k) for k in ("corner", "setup_wns", "setup_tns", "hold_wns")])
            t.rows.append([f"{m.get('worst_corner')} ({tt('worst')})",
                           cell(fmt_ns(m["wns"]), _slack_cls(m["wns"], setup_small)),
                           cell(fmt_ns(m.get("tns")), _tns_cls(m.get("tns"))),
                           cell(fmt_ns(m.get("hold_wns")), _slack_cls(m.get("hold_wns")))])
            out.append(t)
        wp = m.get("worst_path")
        if wp:
            t = Table(tt("worst_path") + _mark(c),
                      [tt(k) for k in ("start", "end", "slack", "arrival", "corner")])
            t.rows.append([net(wp.get("startpoint", "")), net(wp.get("endpoint", "")),
                           cell(fmt_ns(wp.get("slack")), _slack_cls(wp.get("slack"), setup_small)),
                           _num(wp.get("arrival")), wp.get("corner", "")])
            out.append(t)
        if m.get("top_cell_types"):
            t = Table(tt("cell_types") + _mark(c), [tt("cell"), tt("count")], collapsed=True)
            t.rows = [[k, _num(v)] for k, v in m["top_cell_types"].items()]
            out.append(t)
    return out


def sdc_tables(r: dict) -> list[Table]:
    out = []
    for c in (c for c in r["checks_run"] if c["tool"] == "check_sdc"):
        m = c.get("metrics") or {}
        if not m:
            continue
        t = Table(tt("check_sdc") + _mark(c), [tt("clock_name"), tt("period"), tt("sources")])
        t.rows = [[k.get("name", ""), _num(k.get("period")), ", ".join(k.get("sources", []))]
                  for k in m.get("clocks", [])]
        n = m.get("unconstrained_endpoints")
        t.rows.append([cell(tt("unconstrained"), "k"), cell(_num(n), "bad" if n else "ok"), ""])
        others = m.get("other_warnings") or []
        t.rows.append([cell(tt("other_warn"), "k"), cell(_num(len(others)), "warn" if others else "ok"),
                       "; ".join(map(str, others[:5]))])
        t.rows.append([cell(tt("sdc"), "k"), m.get("sdc") or "", str(m.get("sdc_sha256") or "")[:16] + "…"])
        out.append(t)
    return out


KNOWN = ("regress", "lint", "synth", "check_sdc")


def other_tables(r: dict) -> list[Table]:
    """Any other tool (mutate, rtl_rules, ...): its plain numbers and short values."""
    out = []
    for c in (c for c in r["checks_run"] if c["tool"] not in KNOWN):
        m = c.get("metrics") or {}
        rows = [[k, _num(v) if not isinstance(v, list) else ", ".join(map(str, v[:12])) + (" …" if len(v) > 12 else "")]
                for k, v in m.items() if isinstance(v, (int, float, str, list)) and not isinstance(v, bool)]
        rows += [[k, tt("yes") if v else tt("no")] for k, v in m.items() if isinstance(v, bool)]
        if rows:
            out.append(Table(tt("other", tool=c["tool"]) + _mark(c), [tt("item"), tt("value")], rows))
    return out


def _rank(severity: str) -> int:
    return SEVERITIES.index(severity) if severity in SEVERITIES else len(SEVERITIES)


def _who(v: dict) -> str:
    return tt("rules") if v["reviewer"] == "rules" else f"{v['reviewer']} ({v.get('model') or '?'})"


def findings_table(r: dict) -> list[Table]:
    rows: dict[str, list] = {}
    for v in r.get("reviews", []):
        for f in v.get("findings", []):
            key = " ".join(f["text"].split())
            entry = rows.setdefault(key, [f["severity"], [], f["text"]])
            if _rank(f["severity"]) < _rank(entry[0]):
                entry[0] = f["severity"]
            if _who(v) not in entry[1]:
                entry[1].append(_who(v))
    if not r.get("reviews"):
        return []
    t = Table(tt("findings"), [tt(k) for k in ("level", "severity", "reviewer", "finding", "location")])
    order = sorted(rows.values(), key=lambda e: _rank(e[0]))
    for sev, who, text in order:
        found = list(dict.fromkeys(_LOCATION.findall(text)))
        locs = [x for x in found if not any(y != x and y.endswith("/" + x) for y in found)]
        t.rows.append([level_cell(FINDING_LEVEL.get(sev, "warning")), sev, ", ".join(who), text,
                       ", ".join(locs) or "—"])
    if not t.rows:
        t.rows.append(["—", "—", "—", tt("no_findings"), "—"])
    return [t]


def gate_table(r: dict) -> list[Table]:
    decisions = r["gate"]["decisions"]
    if not decisions:
        return []
    t = Table(tt("gate"), [tt(k) for k in ("attempt", "result", "best_level", "reasons")])
    for i, d in enumerate(decisions, 1):
        t.rows.append([str(i), cell(tt("pass") if d["passed"] else tt("reject"), "ok" if d["passed"] else "bad"),
                       d.get("max_level") or "—", "\n".join(d["reasons"]) or "—"])
    return [t]


def cost_table(r: dict) -> list[Table]:
    t = Table(tt("cost"), [tt(k) for k in ("who", "model", "turns", "tool_calls", "tok_in", "tok_out",
                                                  "tok_cache", "wall")])
    tot = [0, 0, 0, 0, 0, 0.0]

    def add(who, model, turns, calls, usage, wall):
        usage = usage or {}
        vals = [turns, calls, usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                usage.get("cache_read_input_tokens", 0), wall or 0]
        for i, x in enumerate(vals):
            tot[i] += x if isinstance(x, (int, float)) else 0
        t.rows.append([who, model or "—", *(_num(x) if x != "" else "—" for x in vals[:5]), _num(round(vals[5], 2))])

    add(tt("author"), f"{r['provider']} / {r['model']}", r["turns"], r["tool_calls"], r.get("usage"),
        r.get("wall_s"))
    seen: dict[str, int] = {}
    for v in r.get("reviews", []):
        key = json.dumps([v["reviewer"], v.get("model"), v.get("findings")], ensure_ascii=False, sort_keys=True)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1 and v["reviewer"] == "rules":
            continue  # the same deterministic review written twice: one row
        add(_who(v), v.get("model") or "—", "", len(v.get("tool_calls", [])), v.get("usage"), v.get("wall_s"))
    n = r.get("narrative")
    if n:
        add(tt("summary_writer"), n.get("model"), n.get("turns", 1), 0, n.get("usage"), n.get("wall_s"))
    t.rows.append([cell(tt("total"), "k"), "", _num(tot[0]), _num(tot[1]), _num(tot[2]), _num(tot[3]),
                   _num(tot[4]), _num(round(tot[5], 2))])
    return [t]


def data_tables(r: dict) -> list[Table]:
    return (regress_tables(r) + lint_tables(r) + synth_tables(r) + sdc_tables(r) + other_tables(r)
            + findings_table(r) + gate_table(r) + cost_table(r))


def trace_note(r: dict) -> str:
    return tt("from_trace", trace=r["_trace_used"]) if r.get("_trace_used") else ""
