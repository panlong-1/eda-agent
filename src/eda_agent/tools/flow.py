"""RTL-to-GDS flow results: `flow_report` reads a finished run, `flow_run` makes one.

This module is back-end neutral: a flow adapter (tools/backends, [pnr] backend, LibreLane by
default) turns a run directory into the neutral result, and everything here (signoff rules, the
E5 checklist, comparisons, provenance, experience tables) works on that result only.

`flow_report` reads one run, applies the signoff rules below, and compares the run with a
baseline run, with the same run's own pre-PnR STA and, optionally, with the `synth` estimate
(M2). It runs nothing heavy and changes nothing.

`flow_run` asks the adapter for its run command (LibreLane: its container image, no network) on
a copy of the project root under [pnr] work_dir, so the design checkout and its own runs stay
untouched, records the run in the design's eda_agent experience table, and reports it like
`flow_report`. One flow at a time (a lock file).

Evidence level. The ladder's E5 is "post-route multi-corner STA + DRC/LVS + gate-level
simulation + VCD power". A LibreLane run gives the first two (and vectorless power), not
gate-level simulation of the routed netlist, nor power from real switching activity. So:
- metrics.signoff_level says how far the run gets: "E5-partial" when post-route STA on several
  corners, DRC and LVS all ran; "E5" only if gate-level simulation and VCD power are also
  present (not yet produced by any tool here); otherwise "E4";
- the envelope's evidence_level stays "E4" unless signoff_level is "E5". The gate treats E5 as
  a functional level (R2: words like "correct" may rest on it); a physical flow says nothing
  about function, so a partial E5 must not unlock those words.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from ..core.envelope import Diagnostic, ToolResult, artifact
from ..core.proc import image_digest, run
from ..core.snapshot import CURRENT, OLDER, compare_snapshots, make_snapshot
from ..core.project import Project, expand_vars, project_env
from . import experience
from .backends import Backend, get_backend
from .backends.base import sha256_file

SIDECAR = "eda_agent_flow.json"   # written by flow_run next to the run: what the flow does not record
LOCK = ".flow_run.lock"

# --- Signoff rules: the only place that decides pass/fail of a flow result -----------------------
# Each rule is evaluated to "pass", "fail" or "missing" (the check did not run or left no result).
# Status: any "fail" -> fail; else any "missing" -> error (incomplete signoff); else pass.
# Not rules (reported as warnings, they do not change the status): max slew/cap/fanout
# violations, router DRC (superseded by the signoff DRC tools on the final layout), IR drop,
# XOR differences, lint warnings. DRC has one rule per tool the back end ran (drc_<tool>).
SIGNOFF_RULES = {
    "setup": "post-route setup worst slack >= 0 on every signoff corner",
    "clock_propagated": "post-route path reports of every signoff corner time the clock as propagated (real "
                        "clock tree), with no clock left ideal",
    "hold": "post-route hold worst slack >= 0 on every signoff corner",
    "drc_<tool>": "for each signoff DRC tool the run used: error count == 0",
    "lvs": "LVS error count == 0 (and the LVS report, when present, says the circuits match)",
    "antenna": "antenna-violating nets == 0 and pins == 0 after routing",
}
# The E5 checklist, the same for every back end (open-source or commercial). A complete E5 needs every
# item; flow_report fills those the run shows. The last two need a simulation of the routed netlist,
# which a place-and-route flow such as LibreLane does not do:
#   gate_level_sim_sdf: the routed netlist simulated with the post-route SDF at the slow and the fast
#     corner, timing checks ON (a run with +notimingchecks, or zero-delay, does not count);
#   vcd_power: power from that simulation's switching activity, with its annotation coverage and the
#     activity corner and Liberty corner named (vectorless power is an estimate, not this).
E5_PARTS = ("post_route_sta_multi_corner", "propagated_clock", "parasitics_annotated", "drc", "lvs",
            "antenna", "gate_level_sim_sdf", "vcd_power")

_GIT_TIMEOUT = 20


def _rule(outcome: str, detail: str) -> dict:
    return {"outcome": outcome, "detail": detail}


def evaluate_signoff(f: dict, signoff_corners: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    timing = f["timing"]
    for kind in ("setup", "hold"):
        key = f"{kind}_wns"
        missing = [c for c in signoff_corners if not isinstance((timing.get(c) or {}).get(key), (int, float))]
        bad = {c: timing[c][key] for c in signoff_corners if c not in missing and timing[c][key] < 0}
        if bad:
            out[kind] = _rule("fail", "; ".join(f"{c}: {kind} WNS {v:+.3f} ns, "
                                                 f"{timing[c].get(f'{kind}_violations')} violating endpoint(s)"
                                                 for c, v in bad.items()))
        elif missing or not signoff_corners:
            out[kind] = _rule("missing", f"no post-route {kind} timing for corner(s) "
                                         f"{', '.join(missing) or '(none configured)'}")
        else:
            out[kind] = _rule("pass", ", ".join(f"{c} {timing[c][key]:+.3f}" for c in signoff_corners))
    clk = f.get("clock") or {}
    no_rpt = [c for c in signoff_corners if c not in clk]
    ideal = {c: clk[c] for c in signoff_corners if c in clk and (clk[c]["ideal"] or clk[c]["unpropagated_clocks"])}
    if ideal:
        out["clock_propagated"] = _rule("fail", "; ".join(
            f"{c}: {v['ideal']} path(s) with an ideal clock, unpropagated: {v['unpropagated_clocks'] or 'none'}"
            for c, v in ideal.items()))
    elif no_rpt or not signoff_corners:
        out["clock_propagated"] = _rule("missing", f"no post-route path report for corner(s) "
                                                   f"{', '.join(no_rpt) or '(none configured)'}")
    else:
        out["clock_propagated"] = _rule("pass", ", ".join(f"{c} {clk[c]['propagated']} propagated path(s)"
                                                          for c in signoff_corners))
    by_tool = f["drc"]["by_tool"]
    if not by_tool:
        out["drc"] = _rule("missing", "no DRC result in this run")
    for tool, n in by_tool.items():
        out[f"drc_{tool.lower()}"] = (_rule("missing", f"no {tool} DRC result in this run") if n is None else
                                      _rule("pass" if n == 0 else "fail", f"{n} {tool} DRC error(s)"))
    lvs = f["lvs"]
    if lvs["errors"] is None:
        out["lvs"] = _rule("missing", f"no {lvs.get('tool') or ''} LVS result in this run".replace("  ", " "))
    elif lvs["errors"] or lvs["report_match"] is False:
        out["lvs"] = _rule("fail", f"{lvs['errors']} LVS error(s); report: {lvs['report_final'] or 'none'}")
    else:
        out["lvs"] = _rule("pass", f"0 LVS errors; report: {lvs['report_final'] or 'not found'}")
    ant = f["antenna"]
    if ant["violating_nets"] is None and ant["violating_pins"] is None:
        out["antenna"] = _rule("missing", "no post-route antenna check result in this run")
    else:
        n, p = ant["violating_nets"] or 0, ant["violating_pins"] or 0
        out["antenna"] = _rule("pass" if n == 0 and p == 0 else "fail", f"{n} net(s), {p} pin(s) violating")
    return out


def signoff_status(rules: dict[str, dict]) -> str:
    outcomes = {r["outcome"] for r in rules.values()}
    return "fail" if "fail" in outcomes else "error" if "missing" in outcomes else "pass"


def e5_parts(f: dict, rules: dict[str, dict], signoff_corners: list[str]) -> dict[str, bool]:
    ran = lambda r: rules[r]["outcome"] != "missing"  # noqa: E731
    return {
        "post_route_sta_multi_corner": len(signoff_corners) > 1 and ran("setup") and ran("hold"),
        "propagated_clock": rules["clock_propagated"]["outcome"] == "pass",
        "parasitics_annotated": f["electrical"].get("unannotated_nets_filtered") == 0,
        "drc": bool(drc := [r for r in rules if r.startswith("drc_")]) and all(ran(r) for r in drc),
        "lvs": ran("lvs"),
        "antenna": ran("antenna"),
        "gate_level_sim_sdf": False,   # no flow adapter simulates the routed netlist yet
        "vcd_power": False,            # flow power is vectorless (default switching activity)
    }


def signoff_level(parts: dict[str, bool]) -> str:
    if all(parts.values()):
        return "E5"
    if parts["post_route_sta_multi_corner"] and parts["propagated_clock"] and parts["drc"] and parts["lvs"]:
        return "E5-partial"
    return "E4"


# --- Provenance: which SDC and which inputs the run really used -----------------------------------

def _git(args: list[str], cwd: Path) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=_GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def _git_changes_since(paths: list[Path], since: str) -> tuple[list[str], list[str]] | None:
    """(commits touching paths after `since`, paths with uncommitted edits), or None without git."""
    if not paths:
        return [], []
    cwd = paths[0].parent
    top = _git(["rev-parse", "--show-toplevel"], cwd)
    if not top:
        return None
    top_p = Path(top.strip())
    rel = [str(p.resolve().relative_to(top_p)) for p in paths if p.resolve().is_relative_to(top_p)]
    if not rel:
        return None
    log = _git(["log", f"--since={since}", "--format=%h %cI %s", "--", *rel], top_p)
    status = _git(["status", "--porcelain", "--", *rel], top_p)
    if log is None or status is None:
        return None
    return [ln for ln in log.splitlines() if ln.strip()], [ln[3:] for ln in status.splitlines() if ln.strip()]


def sdc_provenance(f: dict, run_dir: Path) -> dict:
    """The SDC the run used and how sure we are of its sha256.

    basis: recorded (flow_run wrote it), unchanged_since_run (git: no commit and no edit since the
    run started), mtime_before_run (no git; the file was last written before the run), or
    changed_since_run / unknown. Only the first three give a sha256 a timing claim may cite."""
    path = Path(f["sdc_file"]) if f.get("sdc_file") else None
    current = sha256_file(path) if path else None
    side = _sidecar(run_dir)
    if side.get("sdc_sha256"):
        return {"path": str(path) if path else side.get("sdc"), "sha256": side["sdc_sha256"], "basis": "recorded",
                "current_sha256": current}
    if not path or not path.is_file() or not f.get("started"):
        return {"path": str(path) if path else None, "sha256": None, "basis": "unknown", "current_sha256": current}
    git = _git_changes_since([path], f["started"])
    if git is not None:
        commits, dirty = git
        if not commits and not dirty:
            return {"path": str(path), "sha256": current, "basis": "unchanged_since_run", "current_sha256": current}
        out = {"path": str(path), "sha256": None, "basis": "changed_since_run", "current_sha256": current,
               "changes": commits[:5] + [f"uncommitted: {d}" for d in dirty]}
        if (old := _git_content_before(path, f["started"])) is not None:
            # Information only: the committed SDC at run time (uncommitted edits then cannot be ruled out).
            out["git_sha256_at_run"] = hashlib.sha256(old).hexdigest()
            out["constraints_same_as_current"] = sdc_body(old) == sdc_body(path.read_bytes())
        return out
    started = datetime.fromisoformat(f["started"]).timestamp()
    if path.stat().st_mtime < started:
        return {"path": str(path), "sha256": current, "basis": "mtime_before_run", "current_sha256": current}
    return {"path": str(path), "sha256": None, "basis": "unknown", "current_sha256": current}


def sdc_body(data: bytes) -> list[str]:
    """SDC commands without comments and blank lines, to tell a comment-only change from a real one."""
    lines = (ln.split("#", 1)[0].strip() for ln in data.decode(errors="replace").splitlines())
    return [" ".join(ln.split()) for ln in lines if ln]


def _git_content_before(path: Path, since: str) -> bytes | None:
    top = _git(["rev-parse", "--show-toplevel"], path.parent)
    if not top:
        return None
    top_p = Path(top.strip())
    rel = str(path.resolve().relative_to(top_p))
    commit = (_git(["log", "-1", "--format=%H", f"--before={since}", "--", rel], top_p) or "").strip()
    if not commit:
        return None
    try:
        out = subprocess.run(["git", "show", f"{commit}:{rel}"], cwd=top_p, capture_output=True, timeout=_GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def _sidecar(run_dir: Path) -> dict:
    try:
        return json.loads((run_dir / SIDECAR).read_text())
    except (OSError, ValueError):
        return {}


def inputs_changed(rtl_files: list[str], started: str | None) -> dict | None:
    """RTL files of the run that no longer exist, or have commits or edits after the run started
    (None: cannot tell)."""
    named = [Path(p) for p in rtl_files]
    files = [p for p in named if p.is_file()]
    gone = [str(p) for p in named if not p.is_file()]
    if not named or not started:
        return None
    git = _git_changes_since(files, started) if files else ([], [])
    if git is None:
        return {"commits": [], "commits_total": 0, "uncommitted": [], "missing_files": gone} if gone else None
    commits, dirty = git
    return {"commits": commits[:10], "commits_total": len(commits), "uncommitted": dirty[:10],
            "missing_files": gone[:10], "missing_total": len(gone), "files_total": len(named)}


# --- Comparisons ----------------------------------------------------------------------------

COMPARED = [
    ("die_area_um2", ("area", "die_area_um2")), ("core_area_um2", ("area", "core_area_um2")),
    ("utilization", ("area", "utilization")), ("stdcell_count", ("area", "stdcell_count")),
    ("stdcell_area_um2", ("area", "stdcell_area_um2")), ("sequential_count", ("area", "sequential_count")),
    ("timing_repair_buffers", ("area", "timing_repair_buffers")),
    ("power_total_w", ("power", "total_w")), ("wirelength_um", ("routing", "wirelength_um")),
    ("vias", ("routing", "vias")), ("lvs_errors", ("lvs", "errors")), ("antenna_nets", ("antenna", "violating_nets")),
    ("max_slew_violations", ("electrical", "max_slew_violations")),
    ("max_cap_violations", ("electrical", "max_cap_violations")),
    ("max_fanout_violations", ("electrical", "max_fanout_violations")),
    ("runtime_s", ("runtime_s",)),
]
CORNER_COMPARED = ("setup_wns", "setup_tns", "setup_violations", "hold_wns", "hold_violations")


def _get(d: dict, path: tuple[str, ...]):
    for k in path:
        d = d.get(k) if isinstance(d, dict) else None
    return d


def _delta(a, b) -> dict:
    row = {"baseline": a, "run": b}
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        row["delta"] = round(b - a, 6)
        if a:
            row["delta_pct"] = round(100.0 * (b - a) / abs(a), 2)
    return row


def compare_runs(new: dict, base: dict, new_config: dict, base_config: dict, backend: Backend | None = None) -> dict:
    """Deltas run - baseline on neutral numbers, plus what makes the two runs not directly comparable.
    Runs of different back ends are compared on neutral numbers only; their configs are not."""
    out = {"baseline": base["tag"], "metrics": {k: _delta(_get(base, p), _get(new, p)) for k, p in COMPARED},
           "corners": {}}
    for tool in sorted(set(new["drc"]["by_tool"]) | set(base["drc"]["by_tool"])):
        out["metrics"][f"drc_{tool.lower()}"] = _delta(base["drc"]["by_tool"].get(tool), new["drc"]["by_tool"].get(tool))
    for c in sorted(set(new["timing"]) & set(base["timing"])):
        out["corners"][c] = {k: _delta(base["timing"][c].get(k), new["timing"][c].get(k)) for k in CORNER_COMPARED}
    notes = []
    same_backend = new.get("backend") == base.get("backend")
    if not same_backend:
        notes.append(f"different backend: baseline {base.get('backend')}, run {new.get('backend')}; compared on "
                     "neutral numbers only, configs not compared")
    for label, key in (("top", "design"), ("flow version", "backend_version"), ("PDK version", "pdk_version"),
                       ("clock period", "clock_period")):
        if new.get(key) != base.get(key):
            notes.append(f"different {label}: baseline {base.get(key)}, run {new.get(key)}")
    if new.get("sdc", {}).get("sha256") is None or base.get("sdc", {}).get("sha256") is None:
        notes.append("the SDC of at least one run is not known for sure; timing deltas may mix constraints")
    elif new["sdc"]["sha256"] != base["sdc"]["sha256"]:
        notes.append("different SDC (sha256 differs): timing deltas mix design and constraint changes")
    out["not_comparable"] = notes
    out["better"], out["worse"] = _better_worse(out)
    skip = backend.not_knobs if backend else re.compile(r"^$")
    out["config_differences"] = _config_diff(base_config, new_config, base["run"], new["run"], skip=skip) \
        if same_backend else []
    return out


# Direction of "better" for compared numbers: +1 higher is better, -1 lower is better. Area, cells and
# runtime have no direction here: smaller is not better if it costs timing.
_BETTER = {"setup_wns": 1, "hold_wns": 1, "setup_tns": 1, "setup_violations": -1, "hold_violations": -1,
           "lvs_errors": -1, "antenna_nets": -1, "max_slew_violations": -1,
           "max_cap_violations": -1, "max_fanout_violations": -1, "power_total_w": -1}


def _direction(name: str) -> int | None:
    return -1 if name.startswith("drc_") else _BETTER.get(name)


def _better_worse(cmp: dict) -> tuple[list[str], list[str]]:
    """Which compared numbers moved in the good and in the bad direction: the list a person reads
    first to accept or reject a change (a rejected optimisation is documented by its 'worse' list)."""
    rows = list(cmp["metrics"].items()) + \
        [(f"{k}@{c}", r) for c, rs in cmp["corners"].items() for k, r in rs.items()]
    better, worse = [], []
    for name, r in rows:
        sign = _direction(name.split("@")[0])
        if sign and r.get("delta"):
            (better if r["delta"] * sign > 0 else worse).append(f"{name} {r['baseline']} -> {r['run']}")
    return better, worse


def _config_diff(a: dict, b: dict, run_a: str, run_b: str, limit: int = 20, skip: re.Pattern | None = None) -> list[dict]:
    """Config keys (the flow's own names) whose values differ, ignoring paths into each run's own
    directory and the spelling of a path (a symlink and its target are the same file)."""
    real: dict[str, str] = {}

    def canon(x):
        if isinstance(x, str) and x.startswith("/"):
            if x not in real:
                real[x] = os.path.realpath(x) if Path(x).exists() else x
            return real[x]
        if isinstance(x, list):
            return [canon(v) for v in x]
        if isinstance(x, dict):
            return {k: canon(v) for k, v in x.items()}
        return x

    def norm(v):
        s = json.dumps(canon(v), sort_keys=True)
        return s.replace(run_a, "<run>").replace(run_b, "<run>")
    keys = sorted(k for k in set(a) | set(b) if not (skip and skip.match(k)) and norm(a.get(k)) != norm(b.get(k)))
    return [{"key": k, "baseline": a.get(k), "run": b.get(k)} for k in keys[:limit]]


def _corner_match(name: str, candidates) -> str | None:
    """The run's corner a short name or Liberty-derived name (typ, slow_1p08V_125C, ...) refers to."""
    low = name.lower()
    hits = [c for c in candidates if low in c.lower()]
    return hits[0] if len(hits) == 1 else None


def compare_pre_post(f: dict) -> dict:
    """The same run's own pre-PnR STA (ideal clocks, no wires) against its post-route STA."""
    out = {}
    for c, post in f["timing"].items():
        pre = f["pre_pnr"]["corners"].get(c)
        if pre:
            out[c] = {"setup_wns": _delta(pre.get("setup_wns"), post.get("setup_wns")),
                      "hold_wns": _delta(pre.get("hold_wns"), post.get("hold_wns"))}
    return out


def compare_synth(f: dict, synth: dict, sdc_sha: str | None, run_snap: dict | None = None) -> dict:
    """The M2 synth estimate (an envelope dict) against this run's post-route numbers. Always a
    comparison of two different netlists: synth maps the RTL with our own Yosys script, the flow
    re-synthesizes it with its own; the numbers are a reference, not a before/after of one design."""
    m = synth.get("metrics") or {}
    same = compare_snapshots(synth.get("snapshot") or {}, run_snap or {})
    out = {"label": "different netlist",
           "netlist_note": "synth's Yosys netlist and the flow's own synthesis are different netlists",
           "same_design": same["same_design"], "design_differences": same["differences"],
           "synth_status": synth.get("status"), "synth_top": m.get("top") or None, "run_top": f.get("design"),
           "same_sdc": bool(sdc_sha) and m.get("sdc_sha256") == sdc_sha,
           "synth_sdc_sha256": m.get("sdc_sha256"), "corners": {},
           "cells": _delta(m.get("cells"), f["area"]["stdcell_count"]),
           "cell_area_um2": _delta(m.get("area"), f["area"]["stdcell_area_um2"])}
    for sc, vals in (m.get("corners") or {}).items():
        c = _corner_match(sc, f["timing"])
        if c:
            out["corners"][c] = {"synth_corner": sc,
                                 "setup_wns": _delta(vals.get("setup_wns"), f["timing"][c].get("setup_wns")),
                                 "hold_wns": _delta(vals.get("hold_wns"), f["timing"][c].get("hold_wns"))}
    notes = ["different netlist: synth's own Yosys netlist vs the flow's re-synthesized one; a reference only",
             "post-route cell counts include CTS, timing-repair, tap and antenna cells that synthesis does not have"]
    if same["same_design"] is False:
        notes.append("not the same design: " + "; ".join(same["differences"]))
    elif same["same_design"] is None:
        notes.append("whether both read the same RTL is not known: " + "; ".join(same["differences"]))
    if not out["same_sdc"]:
        notes.append("synth and this run were timed under different (or unknown) SDCs")
    out["notes"] = notes
    return out


# --- The tools ------------------------------------------------------------------------------

def _root_path(project: Project, p: str) -> Path:
    q = Path(os.path.expanduser(expand_vars(p, project_env(project))))
    return (q if q.is_absolute() else project.root / q).resolve()


def backend_of(project: Project) -> Backend:
    assert project.pnr is not None
    if project.pnr.backend != "librelane":
        from ..plugins import load_adapters
        load_adapters()  # adapters installed as plug-ins (entry point group eda_agent.adapters)
    return get_backend(project.pnr.backend)


def runs_dir(project: Project) -> Path:
    pnr = project.pnr
    assert pnr is not None
    return _root_path(project, pnr.runs_dir) if pnr.runs_dir else \
        backend_of(project).default_runs_dir(_root_path(project, pnr.config))


def resolve_run(project: Project, run_ref: str) -> Path | str:
    """A run directory from a name under runs_dir or a path inside the project root or the
    flow work directory; else the reason it is refused."""
    pnr = project.pnr
    assert pnr is not None
    if not run_ref:
        return "no run given"
    base = runs_dir(project)
    p = base / run_ref if "/" not in run_ref else Path(os.path.expanduser(run_ref))
    p = (p if p.is_absolute() else Path.cwd() / p).resolve()
    allowed = [project.root.resolve(), Path(pnr.work_dir).resolve()]
    if not any(p.is_relative_to(a) for a in allowed):
        return f"run {run_ref!r} is outside the project root and the flow work directory"
    be = backend_of(project)
    if not be.is_run(p):
        known = sorted(d.name for d in base.iterdir() if be.is_run(d)) if base.is_dir() else []
        return f"no {be.name} run at {p} (runs under {base}: {', '.join(known) or 'none'})"
    return p


def _refuse(tool: str, project: Project) -> ToolResult | None:
    if project.pnr is None:
        return ToolResult(tool, "error", "E4", "Nothing was read or run.",
                          summary=f"project '{project.name}' has no [pnr] section (config = the flow's config file): "
                                  "the RTL-to-GDS flow is not set up for it", error_class="not_configured")
    return None


def _scope(f: dict, sdc: dict, corners: list[str], level: str) -> str:
    tools = f["signoff_tools"]
    drc = ", ".join(f"{t} ({Path(str(d)).name})" for t, d in tools["drc"].items()) or "none"
    lvs = ", ".join(f"{t} ({Path(str(d)).name})" for t, d in tools["lvs"].items()) or "none"
    sdc_txt = (f"SDC {sdc['path']} (sha256 {sdc['sha256']}, {sdc['basis']})" if sdc["sha256"] else
               f"SDC {sdc['path']} whose content at run time is not known ({sdc['basis']})")
    return (
        f"{f['backend']} {f['backend_version']} run '{f['tag']}' of top '{f['design']}' on {f['pdk']} "
        f"(PDK version {str(f['pdk_version'])[:12]}): post-route STA with a propagated clock and extracted "
        f"parasitics on corner(s) {', '.join(corners) or 'none'} at a {f['clock_period']} ns clock under "
        f"{sdc_txt}; DRC: {drc}; LVS: {lvs}; antenna check: {tools['antenna'] or 'none'}, on the final "
        f"layout. Signoff level {level}" + {"E5-partial": ": the physical-signoff parts of E5", "E5": "",
                                            "E4": " (not all of post-route STA on several corners with a "
                                                  "propagated clock, DRC and LVS have results)"}[level]
        + ". It does not show gate-level simulation of the routed netlist (with post-route SDF and timing "
        "checks), power from real switching activity (power here is vectorless), timing at RC-extraction "
        "corners other than those named, IR drop or electromigration signoff, or anything about function."
    )


def _stale(changed: dict | None) -> str:
    """'' if the run's RTL is unchanged (or unknown), else how it changed."""
    if not changed:
        return ""
    parts = [f"{changed['commits_total']} commit(s)" if changed.get("commits_total") else "",
             f"{len(changed['uncommitted'])} file(s) with uncommitted edits" if changed.get("uncommitted") else "",
             f"{changed.get('missing_total', len(changed['missing_files']))} of {changed.get('files_total', '?')} "
             "RTL file(s) it read no longer exist" if changed.get("missing_files") else ""]
    return ", ".join(p for p in parts if p)


def _risks(f: dict, sdc: dict, corners: list[str], changed: dict | None, level: str) -> list[str]:
    risks = []
    if level != "E5":
        risks.append("no gate-level simulation of the routed netlist: E5 needs it with the post-route SDF at the "
                     "slow and fast corners and timing checks on; behaviour after PnR is not checked")
        risks.append(f"power is {f['power'].get('method') or 'of unstated method'}, not from a simulation VCD "
                     "with stated annotation coverage and activity/Liberty corners")
    if corners and all(c.startswith("nom_") for c in corners):
        risks.append("only nominal RC extraction was timed (nom_* corners); min/max RC corners were not analysed")
    risks.append("timing is only as meaningful as the SDC: its I/O delays and exceptions are assumptions")
    if not sdc["sha256"]:
        risks.append(f"the SDC used at run time is not known for sure ({sdc['basis']}): timing cannot be tied "
                     "to the current SDC")
    if f.get("equivalence_checked") is False:
        risks.append("the netlist was not checked for equivalence with the RTL (the flow skipped that step)")
    if f["ir_drop"]["worst_v"] is not None:
        risks.append("IR drop is the flow's static estimate without real supply-pad locations; electromigration "
                     "was not checked")
    if _stale(changed):
        risks.append(f"the RTL changed after this run ({_stale(changed)}): it may not describe the current design")
    return risks


def run_snapshot(project: Project, f: dict, path: Path, sdc: dict, changed: dict | None) -> dict:
    """The snapshot of the design this run was made from. A run whose RTL or SDC changed after it
    ran belongs to an older snapshot: its run-time file contents are not known."""
    work = Path(project.pnr.work_dir).resolve()
    src = next((q for q in path.resolve().parents if q.name == "src" and q.is_relative_to(work)), None)
    side = _sidecar(path)
    stale = _stale(changed)
    older = bool(stale) or sdc["basis"] == "changed_since_run"
    note = ("RTL changed after the run: " + stale if stale else
            "SDC changed after the run" if sdc["basis"] == "changed_since_run" else
            "files of eda-agent's copy of the design" if src else "the design's files, unchanged since the run")
    return make_snapshot(f["rtl_files"], [r for r in (src, project.root) if r], sdc_sha256=sdc["sha256"],
                         pdk_version=f["pdk_version"], defines=f.get("defines") or [],
                         include_dirs=f.get("include_dirs") or [],
                         flow_config_sha256=side.get("config_sha256") or f["config_sha256"],
                         tools={str(f["backend"]): f["backend_version"]}, image=side.get("image"),
                         state=OLDER if older else CURRENT, note=note)


def run_flow_report(project: Project, run_ref: str, baseline: str | None = None,
                    synth_result: dict | None = None, max_violators: int = 5) -> ToolResult:
    tool = "flow_report"
    if refused := _refuse(tool, project):
        return refused
    pnr = project.pnr
    path = resolve_run(project, run_ref)
    if isinstance(path, str):
        return ToolResult(tool, "error", "E4", "Nothing was read.", summary=path, error_class="bad_config")
    be = backend_of(project)
    f = be.read_run(path, violators=max_violators)
    config = be.config(path)
    corners = [c for c in (pnr.signoff_corners or f["sta_corners"])]
    rules = evaluate_signoff(f, corners)
    status = signoff_status(rules)
    parts = e5_parts(f, rules, corners)
    level_name = signoff_level(parts)
    level = "E5" if level_name == "E5" else "E4"
    sdc = sdc_provenance(f, path)
    f["sdc"] = sdc
    changed = inputs_changed(f["rtl_files"], f["started"])
    issues = be.setup_issues(config, be.log_text(path), rules)
    table = experience.table_of(project, path, runs_dir(project))
    snap = run_snapshot(project, f, path, sdc, changed)
    lin = be.lineage(path)

    diags = [Diagnostic("error", f"SIGNOFF_{r.upper()}", d["detail"]) for r, d in rules.items() if d["outcome"] == "fail"]
    diags += [Diagnostic("warning", f"MISSING_{r.upper()}", d["detail"]) for r, d in rules.items()
              if d["outcome"] == "missing"]
    if not f["complete"]:
        diags.append(Diagnostic("warning", "INCOMPLETE", f"the flow did not finish: last step {f['last_step']}, "
                                                         f"metrics from {f['metrics_source']}"))
    for k, v in f["electrical"].items():
        if k.endswith("violations") and v:
            diags.append(Diagnostic("warning", k.upper(), f"{v} {k.replace('_', ' ')} (not a signoff rule here)"))
    for c, v in (f.get("clock") or {}).items():
        if v["nonzero_source_latency"]:
            diags.append(Diagnostic("warning", "CLOCK_SOURCE_LATENCY", f"{c}: clock source latency "
                                    f"{v['nonzero_source_latency']} ns in the path reports; a latency on one "
                                    "side only shifts every slack"))
    if (un := f["electrical"].get("unannotated_nets_filtered")):
        diags.append(Diagnostic("warning", "UNANNOTATED_NETS", f"{un} net(s) without extracted parasitics in "
                                                                "post-route STA"))
    for x in f["cross_checks"]:
        if not x["agree"]:
            diags.append(Diagnostic("warning", "CROSS_CHECK", f"{x['what']}: metric {x['metric']} vs report {x['report']}"))
    if project.flow and f["design"] and f["design"] != project.flow.top:
        diags.append(Diagnostic("warning", "OTHER_TOP", f"this run is of top '{f['design']}'; the project's [flow] "
                                                         f"top is '{project.flow.top}'"))
    if not sdc["sha256"]:
        same = {True: "; the committed SDC at run time has the same commands (comments differ)",
                False: "; the committed SDC at run time has different commands"}.get(sdc.get("constraints_same_as_current"), "")
        diags.append(Diagnostic("warning", "SDC_UNKNOWN", f"SDC at run time not known ({sdc['basis']}): "
                                                          f"{'; '.join(sdc.get('changes', [])) or sdc['path']}{same}"))
    if _stale(changed):
        diags.append(Diagnostic("warning", "INPUTS_CHANGED", f"RTL changed since the run: {_stale(changed)}"))
    diags += [Diagnostic("error", "LINEAGE_BREAK", b["what"]) for b in lin.get("breaks", [])]
    if lin.get("available") and lin.get("stages_absent") and f["stage"] == "full":
        diags.append(Diagnostic("warning", "LINEAGE_STAGES_ABSENT", "stages not found: " + ", ".join(lin["stages_absent"])))
    diags += [Diagnostic("warning", "SETUP", f"{i['category']}: {i['missing']} ({i['evidence']})") for i in issues]

    timed = {c: f["timing"][c] for c in corners if isinstance((f["timing"].get(c) or {}).get("setup_wns"), (int, float))}
    worst = min(timed, key=lambda c: timed[c]["setup_wns"]) if timed else None
    holds = [t["hold_wns"] for t in timed.values() if isinstance(t.get("hold_wns"), (int, float))]
    wns = timed[worst]["setup_wns"] if worst else None
    hold = min(holds) if holds else None
    viol = (f["violators"].get(worst) or [None])[0] if worst else None

    comparison: dict = {"pre_pnr_same_run": compare_pre_post(f)}
    base_name = baseline if baseline is not None else pnr.baseline
    if base_name and base_name != run_ref:
        bpath = resolve_run(project, base_name)
        if isinstance(bpath, str):
            comparison["baseline_error"] = bpath
        else:
            bf = be.read_run(bpath, violators=0)
            bf["sdc"] = sdc_provenance(bf, bpath)
            comparison["baseline"] = compare_runs(f, bf, config, be.config(bpath), be)
            comparison["baseline"]["baseline_table"] = experience.table_of(project, bpath, runs_dir(project))["table"]
            bsnap = run_snapshot(project, bf, bpath, bf["sdc"], inputs_changed(bf["rtl_files"], bf["started"]))
            comparison["baseline"]["design"] = compare_snapshots(snap, bsnap)
            if comparison["baseline"]["design"]["same_design"] is not True:
                comparison["baseline"]["not_comparable"].append(
                    "not shown to be the same design (snapshot " + (bsnap.get("state") or "?") + " vs "
                    + (snap.get("state") or "?") + "): deltas mix design and flow changes")
    if synth_result:
        comparison["synth"] = compare_synth(f, synth_result, sdc["sha256"], snap)

    metrics = {
        "run": str(path), "tag": f["tag"], "design": f["design"], "complete": f["complete"],
        "signoff_corners": corners, "signoff": rules, "signoff_rules": SIGNOFF_RULES,
        "signoff_passed": status == "pass", "signoff_level": level_name, "e5_parts": parts,
        "clock_port": f["clock_port"], "clock_period": f["clock_period"], "time_unit": "ns",
        "sdc": sdc["path"], "sdc_basis": sdc["basis"], "sdc_sha256_current": sdc["current_sha256"],
        "wns": wns, "tns": min((t.get("setup_tns") or 0.0 for t in timed.values()), default=None),
        "hold_wns": hold, "hold_tns": min((t.get("hold_tns") or 0.0 for t in timed.values()), default=None),
        "worst_corner": worst,
        "worst_path": {"startpoint": viol["from"], "endpoint": viol["to"], "slack": viol["slack"], "corner": worst}
        if viol else {},
        "timing_met": rules["setup"]["outcome"] == "pass" and rules["hold"]["outcome"] == "pass",
        "corners": f["timing"], "violators": f["violators"],
        "drc_by_tool": f["drc"]["by_tool"], "lvs_errors": f["lvs"]["errors"],
        "lvs_match": f["lvs"]["report_match"], "antenna_nets": f["antenna"]["violating_nets"],
        "antenna_pins": f["antenna"]["violating_pins"],
        "area": f["area"], "power": f["power"], "ir_drop": f["ir_drop"], "routing": f["routing"],
        "electrical": f["electrical"], "drc": f["drc"], "lvs": f["lvs"], "antenna": f["antenna"],
        "flow_counts": f["flow_counts"], "cross_checks": f["cross_checks"], "runtime_s": f["runtime_s"],
        "config_sha256": f["config_sha256"], "pdk": f["pdk"], "pdk_version": f["pdk_version"],
        "backend": f["backend"], "backend_version": f["backend_version"], "inputs_changed": changed,
        "setup_issues": issues, "experience_table": table["table"], "provenance": table["provenance"],
        "comparison": comparison,
        "clock": f["clock"], "clock_skew": f["clock_skew"], "lineage": lin,
        # where each number comes from (their sha256 are in artifacts)
        "sources": {"metrics": f["metrics_source"], "post_route_sta": f["post_route_sta_step"], **f["reports"]},
    }
    if sdc["sha256"]:
        metrics["sdc_sha256"] = sdc["sha256"]  # only then may a timing claim cite this result (gate R5)
    side = _sidecar(path)
    versions = {f["backend"] or be.name: f["backend_version"] or "unknown",
                "pdk": f"{f['pdk']}@{f['pdk_version'] or 'unknown'}",
                "image": side.get("image") or "not recorded in the run"}
    summary = (f"{f['tag']} ({f['design']}): signoff {status.upper()} [{level_name}]; setup WNS "
               f"{'n/a' if wns is None else f'{wns:+.3f}'} ns ({worst}), hold WNS "
               f"{'n/a' if hold is None else f'{hold:+.3f}'} ns; DRC "
               f"{', '.join(f'{t} {n}' for t, n in f['drc']['by_tool'].items()) or 'none'}; LVS errors "
               f"{f['lvs']['errors']}; antenna nets "
               f"{f['antenna']['violating_nets']}; die {f['area']['die_area_um2']} um2, util "
               f"{f['area']['utilization']}, {f['area']['stdcell_count']} cells")
    arts = [artifact(p) for k, p in f["reports"].items() if not k.startswith("violators_")]
    if sdc["path"] and Path(sdc["path"]).is_file():
        arts.append(artifact(sdc["path"]))
    return ToolResult(
        snapshot=snap,
        tool=tool, status=status, evidence_level=level, claim_scope=_scope(f, sdc, corners, level_name),
        summary=summary, metrics=metrics, diagnostics=diags[:30], diagnostics_total=len(diags), artifacts=arts,
        tool_versions=versions, error_class="incomplete" if status == "error" else None,
        residual_risk=_risks(f, sdc, corners, changed, level_name),
    )


# --- flow_run --------------------------------------------------------------------------------

def _lock_held(lock: Path) -> bool:
    try:
        pid = int(lock.read_text().strip())
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def flow_command(project: Project, work: Path, tag: str, overrides: dict | None = None) -> tuple[list[str] | str, Path]:
    """(command or the reason it may not run, run directory) from the project's flow adapter."""
    return backend_of(project).run_command(project, work, tag, overrides)


def _copy_root(project: Project, dest: Path) -> None:
    pnr = project.pnr
    root = project.root.resolve()
    skip = {(root / e).resolve() for e in pnr.copy_exclude} | {runs_dir(project).resolve()}

    def ignore(d: str, names: list[str]) -> list[str]:
        return [n for n in names if (Path(d) / n).resolve() in skip]
    shutil.copytree(root, dest, ignore=ignore, symlinks=True)


def run_flow(project: Project, tag: str | None = None, baseline: str | None = None,
             overrides: dict | None = None) -> ToolResult:
    """The flow on a copy of the project root, recorded in the design's eda_agent experience table, then
    flow_report on the new run."""
    tool = "flow_run"
    if refused := _refuse(tool, project):
        return refused
    pnr = project.pnr
    base = Path(pnr.work_dir)
    base.mkdir(parents=True, exist_ok=True)
    lock = base / LOCK
    if _lock_held(lock):
        return ToolResult(tool, "error", "E4", "Nothing was run.", error_class="busy",
                          summary=f"another flow_run is running (pid {lock.read_text().strip()}); one flow at a time")
    tag = tag or f"eda_{datetime.now():%Y%m%d_%H%M%S}"
    if not re.fullmatch(r"[\w.-]+", tag):
        return ToolResult(tool, "error", "E4", "Nothing was run.", summary=f"bad run tag {tag!r}", error_class="bad_config")
    work = (base / f"{project.name}_{tag}").resolve()
    if work.exists():
        return ToolResult(tool, "error", "E4", "Nothing was run.", summary=f"{work} exists; pick another tag",
                          error_class="bad_config")
    lock.write_text(str(os.getpid()))
    try:
        cmd, run_dir = flow_command(project, work, tag, overrides)
        if isinstance(cmd, str):
            return ToolResult(tool, "error", "E4", "Nothing was run.", summary=cmd, error_class="not_allowed")
        _copy_root(project, work / "src")
        sdc_src = _root_path(project, project.flow.sdc) if project.flow else None
        sdc_copy = work / "src" / sdc_src.relative_to(project.root) if sdc_src and sdc_src.is_relative_to(project.root) \
            else None
        image = pnr.image or project.flow.image
        side = {"image": image_digest(image), "image_ref": image, "command": cmd, "source_root": str(project.root),
                "source_commit": (_git(["rev-parse", "HEAD"], project.root) or "").strip() or None,
                "source_dirty": bool((_git(["status", "--porcelain"], project.root) or "").strip()),
                "sdc": str(sdc_copy) if sdc_copy else None,
                "sdc_sha256": sha256_file(sdc_copy) if sdc_copy else None,
                "config_sha256": sha256_file(work / "src" / pnr.config),
                "started": datetime.now().astimezone().isoformat(timespec="seconds")}
        proc = run(cmd, work, project_env(project), pnr.timeout_s)
        (work / "flow.log").write_text(proc.output)
        side["finished"] = datetime.now().astimezone().isoformat(timespec="seconds")
        side["returncode"] = proc.returncode
        be = backend_of(project)
        sidecar = None
        if run_dir.is_dir():
            sidecar = run_dir / SIDECAR
            sidecar.write_text(json.dumps(side, indent=1))
            experience.record_eda_run(project, run_dir, side, overrides, sidecar)
        if proc.returncode is None:
            res = ToolResult(tool, "timeout", "E4", "The flow did not finish.", error_class="timeout",
                             summary=f"{be.name} timed out after {pnr.timeout_s} s; partial run at {run_dir}")
        elif not be.is_run(run_dir):
            issues = be.setup_issues({}, proc.output)
            res = ToolResult(tool, "error", "E4", "The flow did not start.", error_class="setup" if issues else "infra",
                             summary=f"{be.name} produced no run: " + (proc.output.strip().splitlines() or ["no output"])[-1][:300],
                             metrics={"setup_issues": issues},
                             diagnostics=[Diagnostic("error", "SETUP", f"{i['missing']}: {i['evidence']}") for i in issues])
        else:
            res = run_flow_report(project, str(run_dir), baseline=baseline)
            res.tool = tool
            res.metrics["returncode"] = proc.returncode
        res.artifacts.append(artifact(work / "flow.log"))
        return res
    finally:
        lock.unlink(missing_ok=True)
