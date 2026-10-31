"""flow_history: every LibreLane run of the design in one table, and what each config knob did.

Facts only (evidence level E0): it reads the run directories, never runs a flow (plan §0.15 step 1).

- One row per run: stage reached (synth+STA only, full, or stopped early), when it ran, runtime,
  the LibreLane keys that differ from the baseline run, the synthesis/pre-PnR numbers every run
  has, the post-route numbers of full runs, and the files (with sha256) the numbers came from.
- Knob effects: a knob's effect is reported only from a pair of runs whose configs differ in that
  key alone and that read the same RTL. Same RTL is shown by an identical synthesized netlist (cell
  count and area: the same RTL and synthesis settings give the same netlist, and a different RTL
  practically never gives exactly the same one), or, for a knob that changes synthesis, by the two
  runs starting within 15 minutes with no RTL commit between. A git check alone is not enough:
  uncommitted edits between runs are invisible to it. Otherwise the knob is "confounded" and every
  other difference of its closest pairs is listed; nothing is attributed to it.
- Lessons: the comments above keys in the LibreLane config are checked against those pairs:
  confirmed / contradicted (only for a stated "N% area" figure, checked numerically), "testable"
  (numbers given, nothing in the comment to check them against automatically), confounded, or
  cannot be tested (the knob never changed between runs).
"""
from __future__ import annotations

import re
from itertools import combinations
from pathlib import Path

from ..core.envelope import Diagnostic, ToolResult, artifact
from ..core.project import Project
from .flow import _config_diff, _delta, _git, _refuse, _resolved, runs_dir
from .reports.librelane import STEP_PRE_STA, corners, corner_timing, find_step, read_run, sha256_file, step_metrics

# Keys that are not knobs: RTL inputs are tracked by git instead (a changed file list is still listed).
NOT_KNOBS = re.compile(r"^(?:meta|DESIGN_DIR|PDK_ROOT)$")
# A comment states an area effect: "-8.4% cell area", "for +3.6% area"
_AREA_CLAIM = re.compile(r"([+\-−]\d+(?:\.\d+)?)\s*%\s*(?:of\s+)?(?:cell\s+|total\s+)?area", re.I)
_PERIOD = re.compile(r"(\d+(?:\.\d+)?)\s*ns\b")
_KEY_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)\s*:\s*(.*)$")
# Keys that can change the synthesized netlist; for any other key, two runs with the same RTL must
# synthesize to the same netlist.
SYNTH_KEYS = re.compile(r"^(?:SYNTH_|VERILOG_|USE_SLANG$|CLOCK_PERIOD$|LINTER_)")
TOGETHER_S = 15 * 60       # runs started this close, with no RTL commit between, are taken to share the RTL
AREA_TOLERANCE_PTS = 1.0   # a stated "N% area" is confirmed within this many points (or 25% of N)

HISTORY_SCOPE = (
    "Facts read from {n} LibreLane run directories under {runs}: configuration (resolved.json), the "
    "metrics LibreLane recorded per step, and git history of the RTL files. A knob's effect is reported "
    "only from two runs that differ in that key alone, with no RTL commit between them. Each effect rests "
    "on single runs (no repeats, so run-to-run noise is unknown) and proves nothing about other designs, "
    "PDKs or clock periods. Evidence level E0: it says what happened in these runs, not what will happen."
)
HISTORY_RISK = [
    "single runs: no run was repeated, so the size of run-to-run noise is unknown",
    "uncommitted RTL edits between runs cannot be seen; git only shows committed changes",
    "pre-PnR numbers are estimates (ideal clock, no wires); only full runs have post-route numbers",
    "effects hold for this design, PDK and clock period; they are not general rules",
]


def _stage(f: dict, run: Path) -> str:
    if f["post_route_sta_step"]:
        return "full" if f["complete"] else "partial (stopped after post-route STA)"
    if f["complete"] and f["last_step"] and f["last_step"].startswith(STEP_PRE_STA):
        return "synth_sta"
    return "partial"


def _row(run: Path, base_resolved: dict, base_run: str) -> dict:
    f = read_run(run, violators=0)
    resolved = _resolved(run)
    pre = step_metrics(find_step(run, STEP_PRE_STA)) or {}
    pre_step = find_step(run, STEP_PRE_STA)
    stage = _stage(f, run)
    full = stage == "full" or stage.startswith("partial (stopped after")
    sources = {"resolved.json": sha256_file(run / "resolved.json")}
    if pre_step:
        sources[f"{pre_step.name}/state_out.json"] = sha256_file(pre_step / "state_out.json")
    if (run / "final" / "metrics.json").is_file():
        sources["final/metrics.json"] = sha256_file(run / "final" / "metrics.json")
    return {
        "run": run.name, "stage": stage, "complete": f["complete"], "started": f["started"],
        "finished": f["finished"], "runtime_s": f["runtime_s"], "design": f["design"],
        "clock_period": f["clock_period"], "librelane_version": f["librelane_version"],
        "pdk_version": f["pdk_version"],
        "config_vs_baseline": [d for d in _config_diff(base_resolved, resolved, base_run, str(run), limit=50)
                               if not NOT_KNOBS.match(d["key"])],
        "synth": {"cell_area_um2": pre.get("design__instance__area"), "cells": pre.get("design__instance__count"),
                  "power_w": pre.get("power__total"), "setup_tns": pre.get("timing__setup__tns"),
                  "setup_violations": pre.get("timing__setup_vio__count"),
                  "max_fanout_violations": pre.get("design__max_fanout_violation__count"),
                  "corners": {c: {k: v for k, v in corner_timing(pre, c).items()
                                  if k in ("setup_wns", "setup_tns", "hold_wns")} for c in corners(pre)}},
        "post_route": None if not full else {
            "corners": {c: {k: t.get(k) for k in ("setup_wns", "setup_tns", "setup_violations", "hold_wns")}
                        for c, t in f["timing"].items()},
            "die_area_um2": f["area"]["die_area_um2"], "utilization": f["area"]["utilization"],
            "stdcell_area_um2": f["area"]["stdcell_area_um2"], "stdcell_count": f["area"]["stdcell_count"],
            "power_w": f["power"]["total_w"], "wirelength_um": f["routing"]["wirelength_um"],
            "drc_magic": f["drc"]["magic"], "drc_klayout": f["drc"]["klayout"], "lvs_errors": f["lvs"]["errors"],
            "antenna_nets": f["antenna"]["violating_nets"]},
        "sources": sources,
        "_resolved": resolved, "_verilog": [p for p in resolved.get("VERILOG_FILES") or [] if isinstance(p, str)],
    }


def rtl_commits_between(files: list[str], t1: str | None, t2: str | None) -> list[str] | None:
    """Commits touching the RTL files between two run starts (None: no git or no times)."""
    if not files or not t1 or not t2:
        return None
    a, b = sorted([t1, t2])
    cwd = next((Path(p).parent for p in files if Path(p).parent.is_dir()), None)
    top = _git(["rev-parse", "--show-toplevel"], cwd) if cwd else None
    if not top:
        return None
    top_p = Path(top.strip())
    rel = [str(Path(p).relative_to(top_p)) for p in files if Path(p).is_relative_to(top_p)]
    out = _git(["log", f"--since={a}", f"--until={b}", "--format=%h %cI %s", "--", *rel], top_p)
    return None if out is None else [ln for ln in out.splitlines() if ln.strip()]


def _seconds(t: str | None) -> float | None:
    from datetime import datetime
    return datetime.fromisoformat(t).timestamp() if t else None


def same_rtl(a: dict, b: dict, key: str, commits: list[str] | None) -> tuple[bool, str]:
    """Whether two runs read the same RTL, and how we know (or why not)."""
    sa, sb = a["synth"], b["synth"]
    if commits:
        return False, f"{len(commits)} RTL commit(s) between the runs"
    if sa["cells"] is not None and (sa["cells"], sa["cell_area_um2"]) == (sb["cells"], sb["cell_area_um2"]):
        return True, "identical synthesized netlist (cells and area)"
    if not SYNTH_KEYS.match(key):
        return False, ("synthesized netlists differ although only a non-synthesis key changed: the RTL or "
                       "the tool differed (uncommitted edits)")
    ta, tb = _seconds(a["started"]), _seconds(b["started"])
    if ta is not None and tb is not None and abs(ta - tb) <= TOGETHER_S:
        how = "no RTL commit between" if commits == [] else "no git history to check"
        return True, f"started {abs(ta - tb):.0f} s apart, {how}"
    return False, "the runs were not started together and their netlists differ; the RTL may differ"


def pair_effect(a: dict, b: dict) -> dict:
    """b - a on the numbers both runs have: synthesis/pre-PnR always, post-route when both have it."""
    eff = {"synth": {k: _delta(a["synth"][k], b["synth"][k])
                     for k in ("cell_area_um2", "cells", "power_w", "setup_tns", "setup_violations")},
           "synth_setup_wns": {c: _delta(a["synth"]["corners"][c]["setup_wns"], b["synth"]["corners"][c]["setup_wns"])
                               for c in sorted(set(a["synth"]["corners"]) & set(b["synth"]["corners"]))}}
    if a["post_route"] and b["post_route"]:
        pa, pb = a["post_route"], b["post_route"]
        eff["post_route"] = {k: _delta(pa[k], pb[k]) for k in ("die_area_um2", "stdcell_area_um2", "stdcell_count",
                                                               "power_w", "wirelength_um")}
        eff["post_route_setup_wns"] = {c: _delta(pa["corners"][c]["setup_wns"], pb["corners"][c]["setup_wns"])
                                       for c in sorted(set(pa["corners"]) & set(pb["corners"]))}
        eff["runtime_s"] = _delta(a["runtime_s"], b["runtime_s"])
    return eff


def _diff_keys(a: dict, b: dict) -> list[str]:
    return [d["key"] for d in _config_diff(a["_resolved"], b["_resolved"], a["run"], b["run"], limit=200)
            if not NOT_KNOBS.match(d["key"])]


def knob_effects(rows: list[dict]) -> dict[str, dict]:
    """For each key that differs between any two runs: isolated pairs (only that key differs, no RTL
    commit between them) with their effect, or the closest confounded pairs with all their differences."""
    pairs = []
    for a, b in combinations(rows, 2):
        keys = _diff_keys(a, b)
        if keys:
            commits = rtl_commits_between(sorted(set(a["_verilog"]) | set(b["_verilog"])), a["started"], b["started"])
            pairs.append((a, b, keys, commits))
    knobs = sorted({k for *_, keys, _ in pairs for k in keys})
    out = {}
    for k in knobs:
        mine = [(a, b, keys, c) for a, b, keys, c in pairs if k in keys]
        clean, rtl_doubt = [], []
        for a, b, keys, c in mine:
            if keys == [k]:
                ok, why = same_rtl(a, b, k, c)
                (clean if ok else rtl_doubt).append((a, b, why))
        if clean:
            out[k] = {"verdict": "isolated", "pairs": []}
            for a, b, why in clean:
                lo, hi = (a, b) if _val(a, k) <= _val(b, k) else (b, a)
                eff = pair_effect(lo, hi)
                out[k]["pairs"].append({
                    "off": lo["run"], "on": hi["run"],
                    "values": {lo["run"]: lo["_resolved"].get(k), hi["run"]: hi["_resolved"].get(k)},
                    "clock_period": lo["clock_period"], "effect": eff, "same_rtl": why,
                    "summary": f"{k} {_short(lo['_resolved'].get(k))} -> {_short(hi['_resolved'].get(k))} "
                               f"({lo['run']} -> {hi['run']}): {_summary(eff)}"})
        else:
            others = [p for p in mine if p[2] != [k]]  # pairs differing in k alone are listed below, with why
            closest = sorted(others, key=lambda p: (len(p[2]) + (1 if p[3] else 0), p[0]["run"], p[1]["run"]))[:3]
            out[k] = {"verdict": "confounded", "pairs": [
                {"runs": [a["run"], b["run"]], "also_changed": [x for x in keys if x != k],
                 "rtl_commits_between": c if c is not None else "unknown (no git)"} for a, b, keys, c in closest]
                + [{"runs": [a["run"], b["run"]], "also_changed": ["RTL (" + why + ")"]} for a, b, why in rtl_doubt[:3]]}
    return out


def _val(row: dict, key: str) -> tuple:
    """Sort key so that False/absent/smaller comes first ('off')."""
    v = row["_resolved"].get(key)
    if v in (False, None):
        return (0, 0.0, "")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return (1, float(v), "")
    return (1, 0.0, str(v))


def _summary(eff: dict) -> str:
    """One line a person reads: synthesis area and cells, slack per corner, post-route when present."""
    def pct(row):
        return f"{row['delta_pct']:+.2f}%" if "delta_pct" in row else f"{row.get('delta', 0):+g}"
    parts = [f"synthesis cell area {pct(eff['synth']['cell_area_um2'])}",
             "pre-PnR setup WNS " + ", ".join(f"{c.split('_')[1]} {r['delta']:+.3f} ns"
                                              for c, r in eff["synth_setup_wns"].items() if "delta" in r)]
    if "post_route" in eff:
        parts.append(f"die area {pct(eff['post_route']['die_area_um2'])}, power {pct(eff['post_route']['power_w'])}")
        parts.append("post-route setup WNS " + ", ".join(f"{c.split('_')[1]} {r['delta']:+.3f} ns"
                                                         for c, r in eff["post_route_setup_wns"].items() if "delta" in r))
    return "; ".join(parts)


def config_lessons(text: str) -> dict[str, dict]:
    """Comment block written directly above each top-level key of a LibreLane YAML config."""
    out, block = {}, []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            block.append(s.lstrip("#").strip())
            continue
        if m := _KEY_LINE.match(line):
            if block:
                out[m[1]] = {"comment": " ".join(block), "value": m[2].strip()}
        block = []
    return out


def check_lessons(lessons: dict[str, dict], effects: dict[str, dict], rows: list[dict]) -> list[dict]:
    varied = {k for k in effects}
    result = []
    for key, les in lessons.items():
        entry = {"key": key, "comment": les["comment"], "value": les["value"]}
        if key not in varied:
            values = sorted({str(r["_resolved"].get(key)) for r in rows})
            entry.update(verdict="cannot_test", why=f"{key} has the same value in every run ({', '.join(values)})")
        elif effects[key]["verdict"] == "confounded":
            entry.update(verdict="confounded", why="no two runs differ in this key alone with the same RTL "
                                                   "(see knob_effects)", indirect=_by_value(key, rows))
        else:
            entry.update(_area_check(les["comment"], effects[key]["pairs"]))
        result.append(entry)
    return result


def _area_check(comment: str, pairs: list[dict]) -> dict:
    claim = _AREA_CLAIM.search(comment)
    periods = {float(p) for p in _PERIOD.findall(comment)}
    measured = [(p["on"], p["off"], p["clock_period"], p["effect"]["synth"]["cell_area_um2"].get("delta_pct"))
                for p in pairs]
    evidence = [f"{on} vs {off} at {per} ns: synthesis cell area {pct:+.2f}%" for on, off, per, pct in measured
                if pct is not None]
    if not claim:
        return {"verdict": "testable", "why": "isolated runs exist; the comment states no area figure to check "
                                              "automatically", "evidence": evidence}
    want = float(claim[1].replace("−", "-"))
    same_cond = [m for m in measured if m[3] is not None and (not periods or float(m[2]) in periods)]
    use = same_cond or [m for m in measured if m[3] is not None]
    if not use:
        return {"verdict": "testable", "why": "no area numbers in the isolated runs", "evidence": evidence}
    got = use[0][3]
    cond = "" if same_cond or not periods else f" (the comment's period {sorted(periods)} ns differs from the runs')"
    if got * want < 0:
        verdict = "contradicted"
    elif abs(got - want) <= max(AREA_TOLERANCE_PTS, 0.25 * abs(want)):
        verdict = "confirmed"
    else:
        verdict = "direction_only"
    return {"verdict": verdict, "claim_area_pct": want, "measured_area_pct": got,
            "why": f"comment says {want:+g}% area; {use[0][0]} vs {use[0][1]} measure {got:+.2f}%{cond}",
            "evidence": evidence}


def _by_value(key: str, rows: list[dict]) -> dict:
    """Indirect evidence for a confounded knob: post-route minus pre-PnR setup slack of each full run,
    grouped by the knob's value. Other things differ between these runs too."""
    out: dict[str, dict] = {}
    for d in routing_degradation(rows):
        row = next(r for r in rows if r["run"] == d["run"])
        out.setdefault(str(row["_resolved"].get(key)), {})[d["run"]] = d["setup_wns_post_minus_pre"]
    return out if len(out) > 1 else {}


def _short(v):
    return f"<list of {len(v)}>" if isinstance(v, list) and len(v) > 3 else v


def routing_degradation(rows: list[dict]) -> list[dict]:
    """Per full run: post-route minus pre-PnR setup slack per corner (how much placement, CTS and
    routing cost). Indirect evidence for repair knobs: runs differ in more than one thing."""
    out = []
    for r in rows:
        if not r["post_route"]:
            continue
        d = {c: round(r["post_route"]["corners"][c]["setup_wns"] - r["synth"]["corners"][c]["setup_wns"], 3)
             for c in r["post_route"]["corners"] if c in r["synth"]["corners"]
             and isinstance(r["post_route"]["corners"][c]["setup_wns"], (int, float))
             and isinstance(r["synth"]["corners"][c]["setup_wns"], (int, float))}
        out.append({"run": r["run"], "setup_wns_post_minus_pre": d,
                    "knobs": {x["key"]: _short(x["run"]) for x in r["config_vs_baseline"]}})
    return out


def run_flow_history(project: Project, baseline: str | None = None) -> ToolResult:
    tool = "flow_history"
    if refused := _refuse(tool, project):
        refused.evidence_level = "E0"
        return refused
    base_dir = runs_dir(project)
    run_dirs = sorted(d for d in base_dir.iterdir() if (d / "resolved.json").is_file()) if base_dir.is_dir() else []
    if not run_dirs:
        return ToolResult(tool, "error", "E0", "Nothing was read.", summary=f"no LibreLane runs under {base_dir}",
                          error_class="no_runs")
    base_name = baseline or project.pnr.baseline or run_dirs[0].name
    base_path = base_dir / base_name
    if not (base_path / "resolved.json").is_file():
        return ToolResult(tool, "error", "E0", "Nothing was read.", error_class="bad_config",
                          summary=f"baseline {base_name!r} is not a run under {base_dir}")
    base_resolved = _resolved(base_path)
    rows = [_row(d, base_resolved, str(base_path)) for d in run_dirs]
    effects = knob_effects(rows)
    cfg = Path(project.root) / project.pnr.config
    lessons = check_lessons(config_lessons(cfg.read_text(errors="replace")), effects, rows) \
        if cfg.is_file() and cfg.suffix in (".yaml", ".yml") else []
    degradation = routing_degradation(rows)
    diags = [Diagnostic("info", "LESSON_" + les["verdict"].upper(), f"{les['key']}: {les['why']}") for les in lessons]
    diags += [Diagnostic("info", "KNOB_CONFOUNDED", f"{k}: no isolated pair") for k, v in effects.items()
              if v["verdict"] == "confounded"]
    table = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
    for r in table:
        r["config_vs_baseline"] = [{"key": d["key"], "baseline": _short(d["baseline"]), "run": _short(d["run"])}
                                   for d in r["config_vs_baseline"]]
    stages = {s: sum(r["stage"] == s for r in rows) for s in sorted({r["stage"] for r in rows})}
    summary = (f"{len(rows)} runs ({', '.join(f'{n} {s}' for s, n in stages.items())}); baseline {base_name}; "
               f"{sum(v['verdict'] == 'isolated' for v in effects.values())} knob(s) with an isolated pair, "
               f"{sum(v['verdict'] == 'confounded' for v in effects.values())} confounded; lessons: "
               + (", ".join(f"{x['key']} {x['verdict']}" for x in lessons) or "none in the config"))
    return ToolResult(
        tool=tool, status="pass", evidence_level="E0",
        claim_scope=HISTORY_SCOPE.format(n=len(rows), runs=base_dir), summary=summary,
        metrics={"baseline": base_name, "runs": table, "knob_effects": effects, "lessons": lessons,
                 "routing_degradation": degradation, "stages": stages},
        diagnostics=diags[:30], diagnostics_total=len(diags),
        artifacts=[artifact(d / "resolved.json") for d in run_dirs] + ([artifact(cfg)] if cfg.is_file() else []),
        tool_versions={"librelane": ", ".join(sorted({str(r["librelane_version"]) for r in rows}))},
        residual_risk=list(HISTORY_RISK),
    )
