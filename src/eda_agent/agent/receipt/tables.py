"""Layer 2, tables: the numbers behind each check, the findings, the gate and the cost.

Plain code over the receipt data (layer 1). A Table is drawn by both pages (layer 3): as
Markdown in the .md and as HTML in the .html, so the two never show different numbers.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ...core.numbers import fmt_num, severity_rank
from ..levels import DIAG_LEVEL, FINDING_LEVEL, HOLD_MARGIN_NS, SETUP_MARGIN, TITLE, regress_counts
from .data import receipt_kind

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
    "timing_met": "timing met (under this SDC)", "sdc": "SDC", "sdc_sha": "SDC sha256",
    "case_sources": "Cases the run read (describe_case)", "case": "case", "source": "source file",
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
    "mem_hints": "Memory used as hints (not evidence)", "mem_id": "id", "mem_kind": "kind",
    "mem_status": "status", "mem_how": "how", "mem_cited": "cited in report", "mem_source": "source",
    "compactions": "Context compactions (run-state table built by code)", "number": "#",
    "at_turn": "at turn", "replaced": "turns replaced", "reason": "why", "method": "how",
    "fetches": "get_result fetches (raw results read back from the trace)", "turn": "turn", "found": "found",
    "gate": "Evidence gate decisions", "attempt": "attempt", "result": "result",
    "best_level": "best passing evidence", "reasons": "reasons",
    "pass": "pass", "reject": "reject",
    "cost": "Cost", "who": "who", "model": "model", "turns": "turns",
    "tool_calls": "tool calls", "tok_in": "tokens in", "tok_out": "tokens out",
    "tok_cache": "cached in", "wall": "wall time (s)",
    "author": "author (the agent)", "summary_writer": "summary writer (model)",
    "rules": "rule-based (no model)", "times": " ×{n}",
    "total": "total",
    # write-tests
    "mutants": "Mutants targeted", "mutant": "mutant", "status": "status", "tries": "tries",
    "test": "test", "patch": "patch", "review": "review", "kills": "score_tests",
    "tries_list": "Every try (attempt.fix)", "fix": "fix", "on_original": "on the unmodified design",
    "test_author": "test author (model)",
    # import
    "import": "Imported result", "category": "category", "tool": "tool", "importer": "importer",
    "imported_at": "imported", "made_by": "made by", "source_dir": "from", "snapshot": "design snapshot",
    "files": "Files of the report (sha256 recomputed at import)", "side": "side", "role": "role",
    "path": "path", "sha256": "sha256", "not_found": "not found",
    "import_findings": "Findings (the core's rule)",
    "import_metrics": "Numbers the report gives",
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


def cell_text(c) -> str:
    return c["text"] if isinstance(c, dict) else str(c)


def fmt_secs(x) -> str:
    return f"{x:g}" if isinstance(x, (int, float)) else "—"


LEVEL_CLS = {"fatal": "fatal", "error": "bad", "warning": "warn"}


def level_cell(level: str) -> dict:
    return cell(TITLE[level], LEVEL_CLS[level])


def fmt_ns(x) -> str:
    """A slack or TNS in ns: signed to the ps, but a plain 0 for zero."""
    if not isinstance(x, (int, float)):
        return "—"
    return "0" if abs(x) < 0.0005 else f"{x:+.3f}"


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


# ---------------------------------------------------------------- run receipts

def _mark(c: dict) -> str:
    return " †" if c.get("from_trace") else ""


def regress_tables(r: dict) -> list[Table]:
    checks = [c for c in r["checks_run"] if c["tool"] == "regress"]
    if not checks:
        return []
    t = Table(tt("regress"), [tt(k) for k in ("cases_asked", "run", "passed", "failed", "skipped",
                                                         "subtests", "failed_cases")])
    for c in checks:
        m = {**regress_counts(c["summary"]), **(c.get("metrics") or {})}
        asked = ", ".join(c["args"].get("cases", [])) or "—"
        ran = (m.get("passed", 0) + m.get("failed", 0)) if "passed" in m else "—"
        sub = (tt("sub_fmt", p=m["subtests_passed"], f=m["subtests_failed"], t=m["subtests_total"])
               if "subtests_total" in m else "—")
        t.rows.append([asked + _mark(c), fmt_num(ran), cell(fmt_num(m.get("passed", "—")), "ok" if m.get("passed") else ""),
                       cell(fmt_num(m.get("failed", "—")), "bad" if m.get("failed") else ""),
                       cell(fmt_num(m.get("skipped", "—")), "warn" if m.get("skipped") else ""),
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
            t.rows = [[code, fmt_num(n)] for code, n in sorted(m["by_code"].items(), key=lambda x: -x[1])]
            t.note = f"{fmt_num(m.get('errors', 0))} error(s), {fmt_num(m.get('warnings', 0))} warning(s) in all"
            out.append(t)
        if diags:
            files: dict[str, list[int]] = {}
            for d in diags:
                f = files.setdefault(d.get("file") or "—", [0, 0])
                f[0 if d.get("severity") == "error" else 1] += 1
            t = Table(tt("lint_by_file", n=len(diags), total=total) + _mark(c),
                      [tt("file"), tt("errors"), tt("warnings")])
            t.rows = [[cell(f, "ref"), cell(fmt_num(e), "bad" if e else ""), fmt_num(w)]
                      for f, (e, w) in sorted(files.items(), key=lambda x: (-x[1][0], -x[1][1], x[0]))]
            out.append(t)
            t = Table(tt("lint_list", n=len(diags), total=total) + _mark(c),
                      [tt("level"), tt("code"), tt("location"), tt("message")], collapsed=True)
            t.rows = [[level_cell(DIAG_LEVEL.get(d.get("severity"), "warning")),
                       d.get("code", ""), cell(f"{d.get('file', '')}:{d.get('line', '')}", "ref"), d.get("msg", "")]
                      for d in diags]
            out.append(t)
    return out


def synth_tables(r: dict) -> list[Table]:
    out = []
    for c in (c for c in r["checks_run"] if c["tool"] == "synth"):
        m = {**(c.get("timing") or {}), **(c.get("metrics") or {})}
        void = (c.get("timing") or {}).get("void")
        t = Table(tt("synth") + _mark(c), [tt("item"), tt("value")])
        rows = [("cells", fmt_num(m.get("cells"))), ("area", fmt_num(m.get("area"))),
                ("seq_area", fmt_num(m.get("sequential_area"))),
                ("latches", cell(fmt_num(m.get("latches")), "bad" if m.get("latches") else "")),
                ("liberty", m.get("liberty") or "—"),
                ("clock", f"{m.get('clock_port') or ''} {fmt_num(m.get('clock_period'))} ns".strip()),
                ("timing_met", cell(tt("yes") if m.get("timing_met") else tt("no"),
                                    "ok" if m.get("timing_met") else "bad")),
                ("sdc", cell(c.get("sdc") or m.get("sdc") or "—", "ref")),
                ("sdc_sha", f"{str(c.get('sdc_sha256') or '')[:16]}…" if c.get("sdc_sha256") else "—")]
        t.rows = [[tt(k), v] for k, v in rows if (v["text"] if isinstance(v, dict) else v) not in ("—", " ns")]
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
                           fmt_num(wp.get("arrival")), wp.get("corner", "")])
            out.append(t)
        if m.get("top_cell_types"):
            t = Table(tt("cell_types") + _mark(c), [tt("cell"), tt("count")], collapsed=True)
            t.rows = [[k, fmt_num(v)] for k, v in m["top_cell_types"].items()]
            out.append(t)
    return out


def sdc_tables(r: dict) -> list[Table]:
    out = []
    for c in (c for c in r["checks_run"] if c["tool"] == "check_sdc"):
        m = c.get("metrics") or {}
        if not m:
            continue
        t = Table(tt("check_sdc") + _mark(c), [tt("clock_name"), tt("period"), tt("sources")])
        t.rows = [[k.get("name", ""), fmt_num(k.get("period")), ", ".join(k.get("sources", []))]
                  for k in m.get("clocks", [])]
        n = m.get("unconstrained_endpoints")
        t.rows.append([cell(tt("unconstrained"), "k"), cell(fmt_num(n), "bad" if n else "ok"), ""])
        others = m.get("other_warnings") or []
        t.rows.append([cell(tt("other_warn"), "k"), cell(fmt_num(len(others)), "warn" if others else "ok"),
                       "; ".join(map(str, others[:5]))])
        t.rows.append([cell(tt("sdc"), "k"), cell(m.get("sdc") or "—", "ref"),
                       str(m.get("sdc_sha256") or "")[:16] + "…"])
        out.append(t)
    return out


KNOWN = ("regress", "lint", "synth", "check_sdc", "hierarchy", "trace_driver", "trace_load", "waveform_query")


def _plain_rows(m: dict) -> list[list]:
    """A result's plain numbers and short values as (item, value) rows."""
    rows = [[k, fmt_num(v) if not isinstance(v, list) else ", ".join(map(str, v[:12])) + (" …" if len(v) > 12 else "")]
            for k, v in m.items() if isinstance(v, (int, float, str, list)) and not isinstance(v, bool)]
    return rows + [[k, tt("yes") if v else tt("no")] for k, v in m.items() if isinstance(v, bool)]


def other_tables(r: dict) -> list[Table]:
    """Any other tool (mutate, rtl_rules, ...): its plain numbers and short values."""
    out = []
    for c in (c for c in r["checks_run"] if c["tool"] not in KNOWN):
        rows = _plain_rows(c.get("metrics") or {})
        if rows:
            out.append(Table(tt("other", tool=c["tool"]) + _mark(c), [tt("item"), tt("value")], rows))
    return out


def _who(v: dict) -> str:
    return tt("rules") if v["reviewer"] == "rules" else f"{v['reviewer']} ({v.get('model') or '?'})"


def findings_table(r: dict) -> list[Table]:
    rows: dict[str, list] = {}
    for v in r.get("reviews", []):
        for f in v.get("findings", []):
            key = " ".join(f["text"].split())
            entry = rows.setdefault(key, [f["severity"], [], f["text"]])
            if severity_rank(f["severity"]) < severity_rank(entry[0]):
                entry[0] = f["severity"]
            if _who(v) not in entry[1]:
                entry[1].append(_who(v))
    if not r.get("reviews"):
        return []
    t = Table(tt("findings"), [tt(k) for k in ("level", "severity", "reviewer", "finding", "location")])
    order = sorted(rows.values(), key=lambda e: severity_rank(e[0]))
    for sev, who, text in order:
        found = list(dict.fromkeys(_LOCATION.findall(text)))
        locs = [x for x in found if not any(y != x and y.endswith("/" + x) for y in found)]
        t.rows.append([level_cell(FINDING_LEVEL.get(sev, "warning")), sev, ", ".join(who), text,
                       cell(", ".join(locs), "ref") if locs else "—"])
    if not t.rows:
        t.rows.append(["—", "—", "—", tt("no_findings"), "—"])
    return [t]


def gate_table(r: dict) -> list[Table]:
    decisions = r["gate"]["decisions"]
    if not decisions:
        return []
    return [_gate_rows(decisions)]


def _gate_rows(decisions: list[dict]) -> Table:
    t = Table(tt("gate"), [tt(k) for k in ("attempt", "result", "best_level", "reasons")])
    for i, d in enumerate(decisions, 1):
        t.rows.append([str(i), cell(tt("pass") if d["passed"] else tt("reject"), "ok" if d["passed"] else "bad"),
                       d.get("max_level") or "—", "\n".join(d["reasons"]) or "—"])
    return t


class _Cost:
    """Rows of the cost table and their total."""

    def __init__(self) -> None:
        self.t = Table(tt("cost"), [tt(k) for k in ("who", "model", "turns", "tool_calls", "tok_in", "tok_out",
                                                    "tok_cache", "wall")])
        self.tot = [0, 0, 0, 0, 0, 0.0]

    def add(self, who, model, turns, calls, usage, wall) -> None:
        usage = usage or {}
        vals = [turns, calls, usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                usage.get("cache_read_input_tokens", 0), wall or 0]
        for i, x in enumerate(vals):
            self.tot[i] += x if isinstance(x, (int, float)) else 0
        self.t.rows.append([who, model or "—", *(fmt_num(x) if x != "" else "—" for x in vals[:5]),
                            fmt_num(round(vals[5], 2))])

    def table(self) -> Table:
        tot = self.tot
        self.t.rows.append([cell(tt("total"), "k"), "", fmt_num(tot[0]), fmt_num(tot[1]), fmt_num(tot[2]),
                            fmt_num(tot[3]), fmt_num(tot[4]), fmt_num(round(tot[5], 2))])
        return self.t


def cost_table(r: dict) -> list[Table]:
    cost = _Cost()
    cost.add(tt("author"), f"{r['provider']} / {r['model']}", r["turns"], r["tool_calls"], r.get("usage"),
             r.get("wall_s"))
    seen: dict[str, int] = {}
    for v in r.get("reviews", []):
        key = json.dumps([v["reviewer"], v.get("model"), v.get("findings")], ensure_ascii=False, sort_keys=True)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1 and v["reviewer"] == "rules":
            continue  # the same deterministic review written twice: one row
        cost.add(_who(v), v.get("model") or "—", "", len(v.get("tool_calls", [])), v.get("usage"), v.get("wall_s"))
    n = r.get("narrative")
    if n:
        cost.add(tt("summary_writer"), n.get("model"), n.get("turns", 1), 0, n.get("usage"), n.get("wall_s"))
    return [cost.table()]


def case_tables(r: dict) -> list[Table]:
    sources = r.get("case_sources") or r.get("_case_sources") or {}
    if not sources:
        return []
    mark = " †" if "case_sources" not in r else ""
    return [Table(tt("case_sources") + mark, [tt("case"), tt("source")],
                  [[name, cell(src, "ref")] for name, src in sources.items()])]


# ---------------------------------------------------------------- write-tests receipts

def testgen_tables(r: dict) -> list[Table]:
    t = Table(tt("mutants"), [tt(k) for k in ("mutant", "status", "tries", "kills", "test", "patch", "review")])
    tries = Table(tt("tries_list"), [tt(k) for k in ("mutant", "attempt", "test", "on_original")], collapsed=True)
    for x in r.get("results", []):
        closed = x["status"] == "closed"
        last = (x.get("scores") or [{}])[-1]
        v = x.get("review") or {}
        t.rows.append([str(x["mutant"]), cell(x["status"], "ok" if closed else "bad"), fmt_num(len(x.get("tries", []))),
                       cell(f"{last.get('status')} {last.get('evidence_level')}", "ok" if last.get("status") == "pass"
                            else "bad") if x.get("scores") else "—",
                       cell(x["test"], "ref") if x.get("test") else "—", cell(x["patch"], "ref") if x.get("patch") else "—",
                       v.get("verdict") or ("not run" if closed else "—")])
        tries.rows += [[str(x["mutant"]), f"{y['attempt']}.{y['fix']}", cell(y["file"], "ref") if y.get("file") else "—",
                        y.get("original") or "—"] for y in x.get("tries", [])]
    cost = _Cost()
    cost.add(tt("test_author"), f"{r['provider']} / {r['model']}", "", "", r.get("usage"), r.get("wall_s"))
    decision = (r.get("gate") or {}).get("decision") or {}
    return [t, _gate_rows([decision] if decision else []), cost.table()] + ([tries] if tries.rows else [])


# ---------------------------------------------------------------- import receipts

def import_tables(r: dict) -> list[Table]:
    e = r["envelope"]
    m = e.get("metrics") or {}
    snap = e.get("snapshot") or {}
    t = Table(tt("import"), [tt("item"), tt("value")])
    t.rows = [[tt("category"), m.get("category") or "—"],
              [tt("tool"), " ".join(f"{k} {v}" for k, v in (e.get("tool_versions") or {}).items()) or "—"],
              [tt("status"), cell(e["status"], {"pass": "ok"}.get(e["status"], "bad"))],
              [tt("level"), e.get("evidence_level") or "—"],
              [tt("importer"), f"{m.get('importer')} {m.get('importer_version')}"],
              [tt("imported_at"), r.get("imported_at") or "—"], [tt("made_by"), r.get("made_by") or "—"],
              [tt("source_dir"), m.get("source_dir") or "—"],
              [tt("snapshot"), cell(f"{snap.get('id') or '-'} ({snap.get('state')})",
                                    "bad" if snap.get("state") == "unknown" else "")]]
    files = Table(tt("files"), [tt(k) for k in ("side", "role", "path", "sha256")])
    files.rows = [[side, f.get("role") or "—", cell(f["path"], "ref"),
                   (f["sha256"] or "")[:16] or cell(tt("not_found"), "warn")]
                  for side in ("inputs", "outputs") for f in (m.get("files") or {}).get(side, [])]
    out = [t, files]
    diags = e.get("diagnostics") or []
    if diags:
        out.append(Table(tt("import_findings"), [tt(k) for k in ("level", "code", "message")],
                         [[level_cell(DIAG_LEVEL.get(d.get("severity"), "warning")), d.get("code", ""), d.get("msg", "")]
                          for d in diags]))
    skip = {"files", "targets", "experience", "notes", "waivers", "receipt", "source_dir", "importer",
            "importer_version", "tool_name", "category", "design", "provenance"}
    rows = _plain_rows({k: v for k, v in m.items() if k not in skip})
    if rows:
        out.append(Table(tt("import_metrics"), [tt("item"), tt("value")], rows))
    return out


def memory_tables(r: dict) -> list[Table]:
    m = r.get("memory") or {}
    out = []
    if m.get("used_as_hints"):
        t = Table(tt("mem_hints"), [tt(k) for k in ("mem_id", "mem_kind", "mem_status", "mem_how", "mem_cited",
                                                     "mem_source")], note=m.get("note", ""))
        for u in m["used_as_hints"]:
            t.rows.append([u["id"], u.get("kind", ""), (u.get("status") or "") + (f" ({u['note']})" if u.get("note")
                                                                                 else ""),
                           u.get("how", ""), tt("yes") if u.get("cited") else "no", u.get("source", "")])
        out.append(t)
    if m.get("compactions"):
        t = Table(tt("compactions"), [tt(k) for k in ("number", "at_turn", "replaced", "reason", "method")])
        for c in m["compactions"]:
            a, b = c.get("replaced_turns") or ["?", "?"]
            t.rows.append([c.get("number"), c.get("at_turn"), f"{a}-{b}", c.get("reason", ""), c.get("method", "")])
        out.append(t)
    if m.get("fetches"):
        t = Table(tt("fetches"), [tt(k) for k in ("turn", "mem_id", "found")])
        t.rows = [[f.get("turn"), f.get("id"), tt("yes") if f.get("found") else "no"] for f in m["fetches"]]
        out.append(t)
    return out


# ---------------------------------------------------------------- all

def data_tables(r: dict) -> list[Table]:
    kind = receipt_kind(r)
    if kind == "testgen":
        return testgen_tables(r)
    if kind == "import":
        return import_tables(r)
    if kind == "tune":
        from .tune import tune_tables  # it builds on Table
        return tune_tables(r)
    from .localise import localise_tables  # it builds on Table
    return (regress_tables(r) + case_tables(r) + lint_tables(r) + synth_tables(r) + sdc_tables(r)
            + localise_tables(r) + other_tables(r) + findings_table(r) + gate_table(r) + memory_tables(r)
            + cost_table(r))


def trace_note(r: dict) -> str:
    return tt("from_trace", trace=r["_trace_used"]) if r.get("_trace_used") else ""
