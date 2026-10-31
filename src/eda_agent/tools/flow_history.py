"""flow_history: a design's flow runs in two experience tables, and what each config knob did.

Facts only (evidence level E0): it reads run directories through the project's flow adapter and
never runs a flow (plan §0.15 step 1). Back-end neutral: numbers come from the neutral result;
knob names and values are the flow's own and are only compared between runs of the same back end.

Two tables per design (tools/experience.py), never mixed:
- imported: runs in the design's own runs directory, made before or outside eda-agent;
- eda_agent: runs eda-agent's flow_run made for this design (starts empty).
Each row says which table it is in and where it came from (files with sha256, or the flow_run record).

Within each table:
- Knob effects: reported only from a pair of runs whose configs differ in that key alone and that
  read the same RTL. Same RTL is shown by an identical synthesized netlist (cell count and area:
  the same RTL and synthesis settings give the same netlist, and a different RTL practically never
  gives exactly the same one), or, for a knob that changes synthesis, by the two runs starting
  within 15 minutes. A git check alone is not enough: uncommitted edits between runs are invisible
  to it. Otherwise the knob is "confounded" and every other difference of its closest pairs is listed.
- Lessons: comments the adapter finds in the flow's config (LibreLane: the comment above a key) are
  checked against those pairs: confirmed / contradicted (only a stated "N% area", numerically),
  testable, confounded (with indirect per-run numbers), or cannot_test. A verdict from the imported
  table is a hypothesis; only the eda_agent table can verify it for this design.
Lessons another design verified with its own eda-agent runs are listed as hypotheses,
"from <design>, not verified here".
"""
from __future__ import annotations

import re
from datetime import datetime
from itertools import combinations
from pathlib import Path

from ..core.envelope import Diagnostic, ToolResult, artifact
from ..core.project import Project
from . import experience
from .backends import Backend
from .backends.base import sha256_file
from .flow import _config_diff, _delta, _git, _refuse, _root_path, backend_of, runs_dir

# A comment states an area effect: "-8.4% cell area", "for +3.6% area"
_AREA_CLAIM = re.compile(r"([+\-−]\d+(?:\.\d+)?)\s*%\s*(?:of\s+)?(?:cell\s+|total\s+)?area", re.I)
_PERIOD = re.compile(r"(\d+(?:\.\d+)?)\s*ns\b")
TOGETHER_S = 15 * 60       # runs started this close are taken to share the RTL (for synthesis knobs)
AREA_TOLERANCE_PTS = 1.0   # a stated "N% area" is confirmed within this many points (or 25% of N)

HISTORY_SCOPE = (
    "Facts read from the runs of design '{design}' through the {backend} adapter: {imported} imported run(s) "
    "under {runs} and {eda} run(s) made by eda-agent flow_run. A knob's effect is reported only from two runs "
    "of the same table that differ in that key alone and read the same RTL. Each effect rests on single runs "
    "(no repeats, so run-to-run noise is unknown) and says nothing about other designs, PDKs or clock periods. "
    "Verdicts from the imported table are hypotheses until an eda-agent run of this design confirms them. "
    "Evidence level E0: what happened in these runs, not what will happen."
)
HISTORY_RISK = [
    "single runs: no run was repeated, so the size of run-to-run noise is unknown",
    "uncommitted RTL edits between runs cannot be seen; git only shows committed changes",
    "pre-PnR numbers are estimates (ideal clock, no wires); only full runs have post-route numbers",
    "imported runs were made outside eda-agent: who made them, with what exactly, is not known",
    "effects hold for this design, PDK and clock period; they are not general rules",
]


def _short(v):
    return f"<list of {len(v)}>" if isinstance(v, list) and len(v) > 3 else v


def _row(be: Backend, run: Path, base: tuple[dict, str] | None, table: str, record: dict | None) -> dict:
    f = be.read_run(run, violators=0)
    config = be.config(run)
    full = f["post_route_sta_step"] is not None
    diff = _config_diff(base[0], config, base[1], str(run), limit=50, skip=be.not_knobs) if base else []
    prov = {"table": table, "provenance": experience.PROVENANCE[table], "made_by": "unknown (made outside eda-agent)"}
    if record:
        prov.update(made_by=record.get("made_by"), run_id=record.get("run_id"), image=record.get("image"),
                    config_patch=record.get("config_patch"), agent_run_id=record.get("agent_run_id"),
                    source_commit=record.get("source_commit"), recorded_at=record.get("recorded_at"))
    return {
        "run": run.name, "table": table, "stage": f["stage"], "complete": f["complete"], "started": f["started"],
        "finished": f["finished"], "runtime_s": f["runtime_s"], "design": f["design"],
        "backend": f["backend"] or be.name, "backend_version": f["backend_version"],
        "clock_period": f["clock_period"], "pdk_version": f["pdk_version"],
        "config_vs_baseline": [{"key": d["key"], "baseline": _short(d["baseline"]), "run": _short(d["run"])}
                               for d in diff],
        "synth": f["pre_pnr"],
        "post_route": None if not full else {
            "corners": {c: {k: t.get(k) for k in ("setup_wns", "setup_tns", "setup_violations", "hold_wns")}
                        for c, t in f["timing"].items()},
            "die_area_um2": f["area"]["die_area_um2"], "utilization": f["area"]["utilization"],
            "stdcell_area_um2": f["area"]["stdcell_area_um2"], "stdcell_count": f["area"]["stdcell_count"],
            "power_w": f["power"]["total_w"], "wirelength_um": f["routing"]["wirelength_um"],
            "drc_by_tool": f["drc"]["by_tool"], "lvs_errors": f["lvs"]["errors"],
            "antenna_nets": f["antenna"]["violating_nets"]},
        "provenance": prov,
        "sources": {k: sha256_file(p) for k, p in {"config": _config_file(run), **f["reports"]}.items()
                    if p and not k.startswith(("violators_", "paths_"))},
        "_config": config, "_rtl": f["rtl_files"],
    }


def _config_file(run: Path) -> Path | None:
    for name in ("resolved.json", "config.json", "run.json"):
        if (run / name).is_file():
            return run / name
    return None


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
    return datetime.fromisoformat(t).timestamp() if t else None


def same_rtl(a: dict, b: dict, key: str, commits: list[str] | None, be: Backend) -> tuple[bool, str]:
    """Whether two runs read the same RTL, and how we know (or why not)."""
    sa, sb = a["synth"], b["synth"]
    if commits:
        return False, f"{len(commits)} RTL commit(s) between the runs"
    if sa["cells"] is not None and (sa["cells"], sa["cell_area_um2"]) == (sb["cells"], sb["cell_area_um2"]):
        return True, "identical synthesized netlist (cells and area)"
    if not be.synthesis_keys.match(key):
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


def _summary(eff: dict) -> str:
    """One line a person reads: synthesis area, slack per corner, post-route when present."""
    def pct(row):
        return f"{row['delta_pct']:+.2f}%" if "delta_pct" in row else f"{row.get('delta', 0):+g}"

    def wns(rows):
        return ", ".join(f"{c.split('_')[1] if '_' in c else c} {r['delta']:+.3f} ns" for c, r in rows.items()
                         if "delta" in r)
    parts = [f"synthesis cell area {pct(eff['synth']['cell_area_um2'])}", f"pre-PnR setup WNS {wns(eff['synth_setup_wns'])}"]
    if "post_route" in eff:
        parts.append(f"die area {pct(eff['post_route']['die_area_um2'])}, power {pct(eff['post_route']['power_w'])}")
        parts.append(f"post-route setup WNS {wns(eff['post_route_setup_wns'])}")
    return "; ".join(parts)


def _diff_keys(a: dict, b: dict, be: Backend) -> list[str]:
    if a["backend"] != b["backend"]:
        return [f"(backend: {a['backend']} vs {b['backend']})"]  # different flows: their knobs are not comparable
    return [d["key"] for d in _config_diff(a["_config"], b["_config"], a["run"], b["run"], limit=200, skip=be.not_knobs)]


def _val(row: dict, key: str) -> tuple:
    """Sort key so that False/absent/smaller comes first ('off')."""
    v = row["_config"].get(key)
    if v in (False, None):
        return (0, 0.0, "")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return (1, float(v), "")
    return (1, 0.0, str(v))


def knob_effects(rows: list[dict], be: Backend) -> dict[str, dict]:
    """For each key that differs between two runs: isolated pairs (only that key differs, same RTL)
    with their effect, or the closest confounded pairs with all their differences."""
    pairs = []
    for a, b in combinations(rows, 2):
        keys = _diff_keys(a, b, be)
        if keys:
            commits = rtl_commits_between(sorted(set(a["_rtl"]) | set(b["_rtl"])), a["started"], b["started"])
            pairs.append((a, b, keys, commits))
    out = {}
    for k in sorted({k for *_, keys, _ in pairs for k in keys}):
        mine = [(a, b, keys, c) for a, b, keys, c in pairs if k in keys]
        clean, rtl_doubt = [], []
        for a, b, keys, c in mine:
            if keys == [k] and not k.startswith("(backend"):
                ok, why = same_rtl(a, b, k, c, be)
                (clean if ok else rtl_doubt).append((a, b, why))
        if clean:
            out[k] = {"verdict": "isolated", "pairs": []}
            for a, b, why in clean:
                lo, hi = (a, b) if _val(a, k) <= _val(b, k) else (b, a)
                eff = pair_effect(lo, hi)
                out[k]["pairs"].append({
                    "off": lo["run"], "on": hi["run"],
                    "values": {lo["run"]: lo["_config"].get(k), hi["run"]: hi["_config"].get(k)},
                    "clock_period": lo["clock_period"], "effect": eff, "same_rtl": why,
                    "summary": f"{k} {_short(lo['_config'].get(k))} -> {_short(hi['_config'].get(k))} "
                               f"({lo['run']} -> {hi['run']}): {_summary(eff)}"})
        else:
            others = [p for p in mine if p[2] != [k]]  # pairs differing in k alone are listed below, with why
            closest = sorted(others, key=lambda p: (len(p[2]) + (1 if p[3] else 0), p[0]["run"], p[1]["run"]))[:3]
            out[k] = {"verdict": "confounded", "pairs": [
                {"runs": [a["run"], b["run"]], "also_changed": [x for x in keys if x != k],
                 "rtl_commits_between": c if c is not None else "unknown (no git)"} for a, b, keys, c in closest]
                + [{"runs": [a["run"], b["run"]], "also_changed": ["RTL (" + why + ")"]} for a, b, why in rtl_doubt[:3]]}
    return out


def check_lessons(lessons: dict[str, dict], effects: dict[str, dict], rows: list[dict]) -> list[dict]:
    result = []
    for key, les in lessons.items():
        entry = {"key": key, "comment": les["comment"], "value": les["value"]}
        if key not in effects:
            values = sorted({str(r["_config"].get(key)) for r in rows})
            entry.update(verdict="cannot_test", why=f"{key} has the same value in every run ({', '.join(values)})"
                         if rows else "no runs in this table")
        elif effects[key]["verdict"] == "confounded":
            entry.update(verdict="confounded", why="no two runs differ in this key alone with the same RTL "
                                                   "(see knob_effects)", indirect=_by_value(key, rows))
        else:
            entry.update(_area_check(les["comment"], effects[key]["pairs"], les["value"]))
        result.append(entry)
    return result


def _same_value(config_value, written: str) -> bool:
    """A recorded config value against the value written in the config file (YAML text)."""
    w = written.strip().strip("'\"").lower()
    if isinstance(config_value, bool):
        return w in (("true", "yes", "on") if config_value else ("false", "no", "off"))
    if isinstance(config_value, (int, float)):
        try:
            return float(w) == float(config_value)
        except ValueError:
            return False
    return str(config_value).lower() == w


def _oriented(p: dict, value: str) -> tuple[str, str, float | None]:
    """(run with the value the comment recommends, the other run, area % of the first against the
    second); the pair's own off -> on order when neither run has that value."""
    pct = p["effect"]["synth"]["cell_area_um2"].get("delta_pct")
    on, off = p["on"], p["off"]
    if _same_value(p["values"][off], value) and not _same_value(p["values"][on], value):
        a_on, a_off = p["effect"]["synth"]["cell_area_um2"]["run"], p["effect"]["synth"]["cell_area_um2"]["baseline"]
        pct = round(100.0 * (a_off - a_on) / abs(a_on), 2) if a_on else None
        on, off = off, on
    return on, off, pct


def _area_check(comment: str, pairs: list[dict], value: str = "") -> dict:
    claim = _AREA_CLAIM.search(comment)
    periods = {float(p) for p in _PERIOD.findall(comment)}
    measured = [(*_oriented(p, value)[:2], p["clock_period"], _oriented(p, value)[2]) for p in pairs]
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


def routing_degradation(rows: list[dict]) -> list[dict]:
    """Per full run: post-route minus pre-PnR setup slack per corner (what placement, CTS and routing
    cost). Indirect evidence for repair knobs: these runs differ in more than one thing."""
    out = []
    for r in rows:
        if not r["post_route"]:
            continue
        pre = r["synth"]["corners"]
        d = {c: round(t["setup_wns"] - pre[c]["setup_wns"], 3) for c, t in r["post_route"]["corners"].items()
             if c in pre and isinstance(t["setup_wns"], (int, float)) and isinstance(pre[c]["setup_wns"], (int, float))}
        out.append({"run": r["run"], "table": r["table"], "setup_wns_post_minus_pre": d,
                    "knobs": {x["key"]: x["run"] for x in r["config_vs_baseline"]}})
    return out


def _by_value(key: str, rows: list[dict]) -> dict:
    out: dict[str, dict] = {}
    for d in routing_degradation(rows):
        row = next(r for r in rows if r["run"] == d["run"])
        out.setdefault(str(row["_config"].get(key)), {})[d["run"]] = d["setup_wns_post_minus_pre"]
    return out if len(out) > 1 else {}


def _table(rows: list[dict], be: Backend, lessons: dict, baseline: str | None, table: str) -> dict:
    effects = knob_effects(rows, be)
    stages = {s: sum(r["stage"] == s for r in rows) for s in sorted({r["stage"] for r in rows})}
    return {"table": table, "provenance": experience.PROVENANCE[table], "baseline": baseline, "stages": stages,
            "runs": [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows],
            "knob_effects": effects, "lessons": check_lessons(lessons, effects, rows) if lessons else [],
            "routing_degradation": routing_degradation(rows)}


def _combine_lessons(imported: list[dict], here: list[dict], design: str) -> list[dict]:
    """One line per lesson: the imported table's verdict (a hypothesis) and whether eda-agent runs of
    this design verified it."""
    mine = {x["key"]: x for x in here}
    out = []
    for les in imported:
        h = mine.get(les["key"])
        verified = h and h["verdict"] in ("confirmed", "contradicted", "direction_only", "testable")
        out.append({**les, "source_table": experience.IMPORTED,
                    "status": (f"verified here by eda-agent runs ({h['verdict']})" if verified else
                               f"hypothesis: from imported runs of {design}, not verified by an eda-agent run"),
                    "eda_agent_verdict": h["verdict"] if h else None})
    return out


def run_flow_history(project: Project, baseline: str | None = None) -> ToolResult:
    tool = "flow_history"
    if refused := _refuse(tool, project):
        refused.evidence_level = "E0"
        return refused
    be = backend_of(project)
    base_dir = runs_dir(project)
    imported_dirs = sorted(d for d in base_dir.iterdir() if be.is_run(d)) if base_dir.is_dir() else []
    records = [r for r in experience.eda_records(project) if be.is_run(Path(r["run_dir"]))]
    if not imported_dirs and not records:
        return ToolResult(tool, "error", "E0", "Nothing was read.", error_class="no_runs",
                          summary=f"no {be.name} runs under {base_dir} and none recorded by flow_run for "
                                  f"'{project.name}'")
    base_name = baseline or project.pnr.baseline or (imported_dirs[0].name if imported_dirs else None)
    base_path = base_dir / base_name if base_name else None
    if imported_dirs and not (base_path and be.is_run(base_path)):
        return ToolResult(tool, "error", "E0", "Nothing was read.", error_class="bad_config",
                          summary=f"baseline {base_name!r} is not a run under {base_dir}")
    cfg = _root_path(project, project.pnr.config)
    lessons = be.lessons(cfg)
    imp_base = (be.config(base_path), str(base_path)) if imported_dirs else None
    imp_rows = [_row(be, d, imp_base, experience.IMPORTED, None) for d in imported_dirs]
    eda_base = (be.config(Path(records[0]["run_dir"])), records[0]["run_dir"]) if records else None
    eda_rows = [_row(be, Path(r["run_dir"]), eda_base, experience.EDA_AGENT, r) for r in records]
    tables = {experience.IMPORTED: _table(imp_rows, be, lessons, base_name, experience.IMPORTED),
              experience.EDA_AGENT: _table(eda_rows, be, lessons, records[0]["run_id"] if records else None,
                                           experience.EDA_AGENT)}
    combined = _combine_lessons(tables[experience.IMPORTED]["lessons"], tables[experience.EDA_AGENT]["lessons"],
                                project.name)
    others = experience.other_designs(project)
    verified = [{"knob": k, "summary": p["summary"]} for k, v in tables[experience.EDA_AGENT]["knob_effects"].items()
                if v["verdict"] == "isolated" for p in v["pairs"]]
    experience.write_summary(project, {"design": project.name, "backend": be.name, "verified_knobs": verified,
                                       "written_at": datetime.now().astimezone().isoformat(timespec="seconds")})
    diags = [Diagnostic("info", "LESSON_" + les["verdict"].upper(), f"{les['key']}: {les['why']} [{les['status']}]")
             for les in combined]
    for name, t in tables.items():
        diags += [Diagnostic("info", "KNOB_CONFOUNDED", f"{name} table, {k}: no isolated pair")
                  for k, v in t["knob_effects"].items() if v["verdict"] == "confounded"]
    imp = tables[experience.IMPORTED]
    summary = (f"design {project.name} ({be.name}): imported table {len(imp_rows)} run(s) "
               f"({', '.join(f'{n} {s}' for s, n in imp['stages'].items()) or 'none'}), baseline {base_name}; "
               f"eda_agent table {len(eda_rows)} run(s); "
               f"{sum(v['verdict'] == 'isolated' for v in imp['knob_effects'].values())} knob(s) with an isolated "
               f"pair in the imported table; lessons (hypotheses unless verified here): "
               + (", ".join(f"{x['key']} {x['verdict']}" for x in combined) or "none in the config"))
    return ToolResult(
        tool=tool, status="pass", evidence_level="E0",
        claim_scope=HISTORY_SCOPE.format(design=project.name, backend=be.name, imported=len(imp_rows), runs=base_dir,
                                         eda=len(eda_rows)),
        summary=summary,
        metrics={"design": project.name, "backend": be.name, "tables": tables, "lessons": combined,
                 "hypotheses_from_other_designs": others,
                 "experience_dir": str(experience.experience_dir(project))},
        diagnostics=diags[:30], diagnostics_total=len(diags),
        artifacts=[artifact(p) for p in (_config_file(d) for d in imported_dirs) if p] + ([artifact(cfg)] if cfg.is_file() else []),
        tool_versions={be.name: ", ".join(sorted({str(r["backend_version"]) for r in imp_rows + eda_rows}))},
        residual_risk=list(HISTORY_RISK),
    )
