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

SEVERITIES = ("high", "medium", "low", "info")
_LOCATION = re.compile(r"[\w./-]+\.(?:sv|svh|v|vh|vhd|sdc|tcl|S|s|c|h|py|yaml|toml)(?::\d+)?(?![\w])")

TT = {
    "data": ("Data", "数据"),
    "from_trace": ("Numbers marked † come from the run's trace {trace} (this receipt predates them); "
                   "the receipt .json is unchanged.",
                   "带 † 的数字来自本次运行的 trace 文件 {trace}（这份收据早于这些字段）；收据 .json 没有改动。"),
    # regress
    "regress": ("Simulation (regress)", "仿真回归（regress）"),
    "cases_asked": ("cases asked", "要求的用例"), "run": ("run", "运行"), "passed": ("passed", "通过"),
    "failed": ("failed", "失败"), "skipped": ("skipped", "跳过"), "subtests": ("testbench sub-tests", "测试平台子测试"),
    "failed_cases": ("failed cases", "失败的用例"), "format": ("result format", "结果格式"),
    "sub_fmt": ("{p} passed, {f} failed of {t}", "{t} 个中通过 {p}、失败 {f}"),
    # lint
    "lint": ("Lint", "Lint 静态检查"), "errors": ("errors", "错误"), "warnings": ("warnings", "警告"),
    "lint_by_code": ("Lint: by warning code", "Lint：按警告代码"), "code": ("code", "代码"), "count": ("count", "数量"),
    "lint_by_file": ("Lint: by file (of the {n} of {total} diagnostics kept)",
                     "Lint：按文件（基于保留的 {total} 条中的 {n} 条）"),
    "file": ("file", "文件"), "lint_list": ("Lint: diagnostics kept ({n} of {total})", "Lint：保留的诊断（{total} 条中的 {n} 条）"),
    "severity": ("severity", "严重度"), "location": ("location", "位置"), "message": ("message", "内容"),
    # synth
    "synth": ("Synthesis and timing estimate (synth, pre-PnR)", "综合与时序估算（synth，布局布线前）"),
    "item": ("item", "项目"), "value": ("value", "数值"),
    "cells": ("cells", "单元数"), "area": ("area", "面积"), "seq_area": ("sequential area", "时序单元面积"),
    "latches": ("latches", "锁存器"), "liberty": ("library", "工艺库"), "clock": ("clock", "时钟"),
    "timing_met": ("timing met (under this SDC)", "时序满足（在这份 SDC 下）"), "sdc": ("SDC", "SDC"),
    "yes": ("yes", "是"), "no": ("NO", "否"),
    "corners": ("Timing per corner (ns; negative = violation)", "各工艺角时序（ns；负数表示违例）"),
    "corner": ("corner", "工艺角"), "setup_wns": ("setup WNS", "建立 WNS"), "setup_tns": ("setup TNS", "建立 TNS"),
    "hold_wns": ("hold WNS", "保持 WNS"), "hold_tns": ("hold TNS", "保持 TNS"), "worst": ("worst", "最差"),
    "worst_path": ("Worst path", "最差路径"), "start": ("startpoint", "起点"), "end": ("endpoint", "终点"),
    "slack": ("slack", "余量"), "arrival": ("arrival", "到达时间"),
    "cell_types": ("Most used cells", "用得最多的单元"), "cell": ("cell", "单元"),
    "void": ("VOID: the SDC changed later in the run", "作废：运行中后来换了 SDC"),
    # check_sdc
    "check_sdc": ("Constraint check (check_sdc)", "约束检查（check_sdc）"), "clock_name": ("clock", "时钟"),
    "period": ("period (ns)", "周期（ns）"), "sources": ("sources", "来源端口"),
    "unconstrained": ("unconstrained endpoints", "未约束端点"),
    "other_warn": ("other check_setup warnings", "其他 check_setup 警告"),
    # generic
    "other": ("{tool}: numbers", "{tool}：数据"),
    # findings, gate, cost
    "findings": ("Reviewer findings", "评审发现"), "reviewer": ("reviewer", "评审"), "finding": ("finding", "发现"),
    "no_findings": ("no findings", "没有发现"),
    "gate": ("Evidence gate decisions", "证据门决定"), "attempt": ("attempt", "第几次"), "result": ("result", "结果"),
    "best_level": ("best passing evidence", "最高通过证据"), "reasons": ("reasons", "原因"),
    "pass": ("pass", "通过"), "reject": ("reject", "拒绝"),
    "cost": ("Cost", "成本"), "who": ("who", "谁"), "model": ("model", "模型"), "turns": ("turns", "轮数"),
    "tool_calls": ("tool calls", "工具调用"), "tok_in": ("tokens in", "输入 token"), "tok_out": ("tokens out", "输出 token"),
    "tok_cache": ("cached in", "缓存命中"), "wall": ("wall time (s)", "耗时（秒）"),
    "author": ("author (the agent)", "作者（智能体）"), "summary_writer": ("summary writer ({language})", "摘要撰写（{language}）"),
    "rules": ("rule-based (no model)", "规则评审（无模型）"), "times": (" ×{n}", " ×{n}"),
    "total": ("total", "合计"),
}


def tt(lang: str, key: str, **kw) -> str:
    return TT[key][0 if lang == "en" else 1].format(**kw)


@dataclass
class Table:
    title: str
    cols: list[str]
    rows: list[list] = field(default_factory=list)   # a cell is a str, or {"text": ..., "cls": ...}
    note: str = ""
    collapsed: bool = False                          # long lists: folded in the HTML, after the summary in .md


def cell(text, cls: str = "") -> dict:
    return {"text": str(text), "cls": cls}


def fmt_ns(x) -> str:
    return f"{x:+.3f}" if isinstance(x, (int, float)) else "—"


def _num(x) -> str:
    if isinstance(x, float):
        return f"{x:,.1f}" if abs(x) >= 1000 else f"{x:g}"
    return f"{x:,}" if isinstance(x, int) else ("—" if x is None else str(x))


def _slack_cls(x) -> str:
    return "" if not isinstance(x, (int, float)) else ("bad" if x < 0 else "warn" if x < 0.1 else "ok")


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


def regress_tables(r: dict, L: str) -> list[Table]:
    checks = [c for c in r["checks_run"] if c["tool"] == "regress"]
    if not checks:
        return []
    t = Table(tt(L, "regress"), [tt(L, k) for k in ("cases_asked", "run", "passed", "failed", "skipped",
                                                         "subtests", "failed_cases")])
    for c in checks:
        m = {**_counts_from_summary(c["summary"]), **(c.get("metrics") or {})}
        asked = ", ".join(c["args"].get("cases", [])) or "—"
        ran = (m.get("passed", 0) + m.get("failed", 0)) if "passed" in m else "—"
        sub = (tt(L, "sub_fmt", p=m["subtests_passed"], f=m["subtests_failed"], t=m["subtests_total"])
               if "subtests_total" in m else "—")
        t.rows.append([asked + _mark(c), _num(ran), cell(_num(m.get("passed", "—")), "ok" if m.get("passed") else ""),
                       cell(_num(m.get("failed", "—")), "bad" if m.get("failed") else ""),
                       cell(_num(m.get("skipped", "—")), "warn" if m.get("skipped") else ""),
                       cell(sub, "bad" if m.get("subtests_failed") else ""),
                       ", ".join(m.get("failed_cases", [])) or "—"])
    return [t]


def lint_tables(r: dict, L: str) -> list[Table]:
    out = []
    for c in (c for c in r["checks_run"] if c["tool"] == "lint"):
        m = c.get("metrics") or {}
        diags = c.get("diagnostics") or []
        total = c.get("diagnostics_total") or len(diags)
        if m.get("by_code"):
            t = Table(tt(L, "lint_by_code") + _mark(c), [tt(L, "code"), tt(L, "count")])
            t.rows = [[code, _num(n)] for code, n in sorted(m["by_code"].items(), key=lambda x: -x[1])]
            t.rows.append([cell(f"{tt(L, 'errors')} / {tt(L, 'warnings')}", "k"),
                           f"{_num(m.get('errors', 0))} / {_num(m.get('warnings', 0))}"])
            out.append(t)
        if diags:
            files: dict[str, list[int]] = {}
            for d in diags:
                f = files.setdefault(d.get("file") or "—", [0, 0])
                f[0 if d.get("severity") == "error" else 1] += 1
            t = Table(tt(L, "lint_by_file", n=len(diags), total=total) + _mark(c),
                      [tt(L, "file"), tt(L, "errors"), tt(L, "warnings")])
            t.rows = [[f, cell(_num(e), "bad" if e else ""), _num(w)]
                      for f, (e, w) in sorted(files.items(), key=lambda x: (-x[1][0], -x[1][1], x[0]))]
            out.append(t)
            t = Table(tt(L, "lint_list", n=len(diags), total=total) + _mark(c),
                      [tt(L, "severity"), tt(L, "code"), tt(L, "location"), tt(L, "message")], collapsed=True)
            t.rows = [[cell(d.get("severity", ""), "bad" if d.get("severity") == "error" else "warn"),
                       d.get("code", ""), f"{d.get('file', '')}:{d.get('line', '')}", d.get("msg", "")]
                      for d in diags]
            out.append(t)
    return out


def synth_tables(r: dict, L: str) -> list[Table]:
    out = []
    for c in (c for c in r["checks_run"] if c["tool"] == "synth"):
        m = {**(c.get("timing") or {}), **(c.get("metrics") or {})}
        void = (c.get("timing") or {}).get("void")
        t = Table(tt(L, "synth") + _mark(c), [tt(L, "item"), tt(L, "value")])
        rows = [("cells", _num(m.get("cells"))), ("area", _num(m.get("area"))),
                ("seq_area", _num(m.get("sequential_area"))),
                ("latches", cell(_num(m.get("latches")), "bad" if m.get("latches") else "")),
                ("liberty", m.get("liberty") or "—"),
                ("clock", f"{m.get('clock_port') or ''} {_num(m.get('clock_period'))} ns".strip()),
                ("timing_met", cell(tt(L, "yes") if m.get("timing_met") else tt(L, "no"),
                                    "ok" if m.get("timing_met") else "bad")),
                ("sdc", f"{c.get('sdc') or m.get('sdc') or '—'} · sha256 {str(c.get('sdc_sha256') or '')[:16]}…")]
        t.rows = [[tt(L, k), v] for k, v in rows if v not in ("—", " ns")]
        if void:
            t.rows.append([cell(tt(L, "void"), "bad"), ""])
        out.append(t)
        corners = m.get("corners") or {}
        if corners:
            worst = m.get("worst_corner")
            t = Table(tt(L, "corners") + _mark(c), [tt(L, k) for k in ("corner", "setup_wns", "setup_tns",
                                                                         "hold_wns", "hold_tns")])
            for name, v in corners.items():
                t.rows.append([name + (f" ({tt(L, 'worst')})" if name == worst else ""),
                               cell(fmt_ns(v.get("setup_wns")), _slack_cls(v.get("setup_wns"))),
                               fmt_ns(v.get("setup_tns")),
                               cell(fmt_ns(v.get("hold_wns")), _slack_cls(v.get("hold_wns"))),
                               fmt_ns(v.get("hold_tns"))])
            out.append(t)
        elif "wns" in m:  # receipt timing only: the run's worst values
            t = Table(tt(L, "corners"), [tt(L, k) for k in ("corner", "setup_wns", "setup_tns", "hold_wns")])
            t.rows.append([f"{m.get('worst_corner')} ({tt(L, 'worst')})", cell(fmt_ns(m["wns"]), _slack_cls(m["wns"])),
                           fmt_ns(m.get("tns")), cell(fmt_ns(m.get("hold_wns")), _slack_cls(m.get("hold_wns")))])
            out.append(t)
        wp = m.get("worst_path")
        if wp:
            t = Table(tt(L, "worst_path") + _mark(c),
                      [tt(L, k) for k in ("start", "end", "slack", "arrival", "corner")])
            t.rows.append([wp.get("startpoint", ""), wp.get("endpoint", ""),
                           cell(fmt_ns(wp.get("slack")), _slack_cls(wp.get("slack"))),
                           _num(wp.get("arrival")), wp.get("corner", "")])
            out.append(t)
        if m.get("top_cell_types"):
            t = Table(tt(L, "cell_types") + _mark(c), [tt(L, "cell"), tt(L, "count")], collapsed=True)
            t.rows = [[k, _num(v)] for k, v in m["top_cell_types"].items()]
            out.append(t)
    return out


def sdc_tables(r: dict, L: str) -> list[Table]:
    out = []
    for c in (c for c in r["checks_run"] if c["tool"] == "check_sdc"):
        m = c.get("metrics") or {}
        if not m:
            continue
        t = Table(tt(L, "check_sdc") + _mark(c), [tt(L, "clock_name"), tt(L, "period"), tt(L, "sources")])
        t.rows = [[k.get("name", ""), _num(k.get("period")), ", ".join(k.get("sources", []))]
                  for k in m.get("clocks", [])]
        n = m.get("unconstrained_endpoints")
        t.rows.append([cell(tt(L, "unconstrained"), "k"), cell(_num(n), "bad" if n else "ok"), ""])
        others = m.get("other_warnings") or []
        t.rows.append([cell(tt(L, "other_warn"), "k"), cell(_num(len(others)), "warn" if others else "ok"),
                       "; ".join(map(str, others[:5]))])
        t.rows.append([cell(tt(L, "sdc"), "k"), m.get("sdc") or "", str(m.get("sdc_sha256") or "")[:16] + "…"])
        out.append(t)
    return out


KNOWN = ("regress", "lint", "synth", "check_sdc")


def other_tables(r: dict, L: str) -> list[Table]:
    """Any other tool (mutate, rtl_rules, ...): its plain numbers and short values."""
    out = []
    for c in (c for c in r["checks_run"] if c["tool"] not in KNOWN):
        m = c.get("metrics") or {}
        rows = [[k, _num(v) if not isinstance(v, list) else ", ".join(map(str, v[:12])) + (" …" if len(v) > 12 else "")]
                for k, v in m.items() if isinstance(v, (int, float, str, list)) and not isinstance(v, bool)]
        rows += [[k, tt(L, "yes") if v else tt(L, "no")] for k, v in m.items() if isinstance(v, bool)]
        if rows:
            out.append(Table(tt(L, "other", tool=c["tool"]) + _mark(c), [tt(L, "item"), tt(L, "value")], rows))
    return out


def _rank(severity: str) -> int:
    return SEVERITIES.index(severity) if severity in SEVERITIES else len(SEVERITIES)


def _who(v: dict, L: str) -> str:
    return tt(L, "rules") if v["reviewer"] == "rules" else f"{v['reviewer']} ({v.get('model') or '?'})"


def findings_table(r: dict, L: str) -> list[Table]:
    rows: dict[str, list] = {}
    for v in r.get("reviews", []):
        for f in v.get("findings", []):
            key = " ".join(f["text"].split())
            entry = rows.setdefault(key, [f["severity"], [], f["text"]])
            if _rank(f["severity"]) < _rank(entry[0]):
                entry[0] = f["severity"]
            if _who(v, L) not in entry[1]:
                entry[1].append(_who(v, L))
    if not r.get("reviews"):
        return []
    t = Table(tt(L, "findings"), [tt(L, k) for k in ("severity", "reviewer", "finding", "location")])
    order = sorted(rows.values(), key=lambda e: _rank(e[0]))
    for sev, who, text in order:
        found = list(dict.fromkeys(_LOCATION.findall(text)))
        locs = [x for x in found if not any(y != x and y.endswith("/" + x) for y in found)]
        t.rows.append([cell(sev, {"high": "bad", "medium": "warn", "low": "info"}.get(sev, "lvl")),
                       ", ".join(who), text, ", ".join(locs) or "—"])
    if not t.rows:
        t.rows.append(["—", "—", tt(L, "no_findings"), "—"])
    return [t]


def gate_table(r: dict, L: str) -> list[Table]:
    decisions = r["gate"]["decisions"]
    if not decisions:
        return []
    t = Table(tt(L, "gate"), [tt(L, k) for k in ("attempt", "result", "best_level", "reasons")])
    for i, d in enumerate(decisions, 1):
        t.rows.append([str(i), cell(tt(L, "pass") if d["passed"] else tt(L, "reject"), "ok" if d["passed"] else "bad"),
                       d.get("max_level") or "—", "\n".join(d["reasons"]) or "—"])
    return [t]


def cost_table(r: dict, L: str) -> list[Table]:
    t = Table(tt(L, "cost"), [tt(L, k) for k in ("who", "model", "turns", "tool_calls", "tok_in", "tok_out",
                                                  "tok_cache", "wall")])
    tot = [0, 0, 0, 0, 0, 0.0]

    def add(who, model, turns, calls, usage, wall):
        usage = usage or {}
        vals = [turns, calls, usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                usage.get("cache_read_input_tokens", 0), wall or 0]
        for i, x in enumerate(vals):
            tot[i] += x if isinstance(x, (int, float)) else 0
        t.rows.append([who, model or "—", *(_num(x) if x != "" else "—" for x in vals[:5]), _num(round(vals[5], 2))])

    add(tt(L, "author"), f"{r['provider']} / {r['model']}", r["turns"], r["tool_calls"], r.get("usage"),
        r.get("wall_s"))
    seen: dict[str, int] = {}
    for v in r.get("reviews", []):
        key = json.dumps([v["reviewer"], v.get("model"), v.get("findings")], ensure_ascii=False, sort_keys=True)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1 and v["reviewer"] == "rules":
            continue  # the same deterministic review written twice: one row
        add(_who(v, L), v.get("model") or "—", "", len(v.get("tool_calls", [])), v.get("usage"), v.get("wall_s"))
    for lang, n in (r.get("narratives") or {}).items():
        add(tt(L, "summary_writer", language=lang), n.get("model"), n.get("turns", 1), 0, n.get("usage"),
            n.get("wall_s"))
    t.rows.append([cell(tt(L, "total"), "k"), "", _num(tot[0]), _num(tot[1]), _num(tot[2]), _num(tot[3]),
                   _num(tot[4]), _num(round(tot[5], 2))])
    return [t]


def data_tables(r: dict, L: str) -> list[Table]:
    return (regress_tables(r, L) + lint_tables(r, L) + synth_tables(r, L) + sdc_tables(r, L) + other_tables(r, L)
            + findings_table(r, L) + gate_table(r, L) + cost_table(r, L))


def trace_note(r: dict, L: str) -> str:
    return tt(L, "from_trace", trace=r["_trace_used"]) if r.get("_trace_used") else ""
