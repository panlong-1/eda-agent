"""RTL-to-GDS flow results (LibreLane): `flow_report` reads a finished run, `flow_run` makes one.

`flow_report` parses one run directory (tools/reports/librelane.py), applies the signoff rules
below, and compares the run with a baseline run, with the same run's own pre-PnR STA and,
optionally, with the `synth` estimate (M2). It runs nothing heavy and changes nothing.

`flow_run` runs LibreLane in the project's container image, without network, on a copy of the
project root under [pnr] work_dir, so the design checkout and its own runs stay untouched. Then
it reports the new run like `flow_report`. One flow at a time (a lock file); it takes about an
hour for a small CPU.

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
from ..core.pnr import NEVER_OVERRIDE
from ..core.proc import docker_command, image_digest, run
from ..core.project import Project, expand_vars, project_env
from .reports.librelane import read_run, sha256_file

SIDECAR = "eda_agent_flow.json"   # written by flow_run next to the run: what LibreLane does not record
LOCK = ".flow_run.lock"

# --- Signoff rules: the only place that decides pass/fail of a flow result -----------------------
# Each rule is evaluated to "pass", "fail" or "missing" (the check did not run or left no result).
# Status: any "fail" -> fail; else any "missing" -> error (incomplete signoff); else pass.
# Not rules (reported as warnings, they do not change the status): max slew/cap/fanout
# violations, detailed-router DRC (superseded by Magic/KLayout on the final layout), IR drop,
# XOR differences, lint warnings.
SIGNOFF_RULES = {
    "setup": "post-route setup worst slack >= 0 on every signoff corner",
    "hold": "post-route hold worst slack >= 0 on every signoff corner",
    "drc_magic": "Magic DRC error count == 0",
    "drc_klayout": "KLayout DRC error count == 0",
    "lvs": "Netgen LVS error count == 0 (and the LVS report, when present, says the circuits match)",
    "antenna": "antenna-violating nets == 0 and pins == 0 after routing",
}
# What E5 needs; flow_report fills the first four from the run.
E5_PARTS = ("post_route_sta_multi_corner", "drc", "lvs", "antenna", "gate_level_sim", "vcd_power")

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
    for rule, tool, n in (("drc_magic", "Magic", f["drc"]["magic"]), ("drc_klayout", "KLayout", f["drc"]["klayout"])):
        out[rule] = (_rule("missing", f"no {tool} DRC result in this run") if n is None else
                     _rule("pass" if n == 0 else "fail", f"{n} {tool} DRC error(s)"))
    lvs = f["lvs"]
    if lvs["errors"] is None:
        out["lvs"] = _rule("missing", "no Netgen LVS result in this run")
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
        "drc": ran("drc_magic") and ran("drc_klayout"),
        "lvs": ran("lvs"),
        "antenna": ran("antenna"),
        "gate_level_sim": False,   # LibreLane does not simulate the routed netlist
        "vcd_power": False,        # its power is vectorless (default switching activity)
    }


def signoff_level(parts: dict[str, bool]) -> str:
    if all(parts.values()):
        return "E5"
    if parts["post_route_sta_multi_corner"] and parts["drc"] and parts["lvs"]:
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


def _resolved(run_dir: Path) -> dict:
    try:
        data = json.loads((run_dir / "resolved.json").read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def inputs_changed(resolved: dict, started: str | None) -> dict | None:
    """RTL files of the run that no longer exist, or have commits or edits after the run started
    (None: cannot tell)."""
    named = [Path(p) for p in resolved.get("VERILOG_FILES") or [] if isinstance(p, str)]
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


# --- Setup diagnosis (plan §0.11, physical design rows) -----------------------------------------

_PATH_KEYS = {  # resolved.json key -> (category, what it is)
    "TECH_LEFS": ("pdk_views", "technology LEF"), "CELL_LEFS": ("pdk_views", "standard-cell LEF"),
    "CELL_GDS": ("pdk_views", "standard-cell GDS"), "LIB": ("corner_data", "timing library (Liberty)"),
    "RCX_RULESETS": ("corner_data", "RC extraction rules"),
    "MAGIC_TECH": ("signoff_decks", "Magic tech file (DRC deck)"), "MAGICRC": ("signoff_decks", "Magic rc file"),
    "KLAYOUT_TECH": ("signoff_decks", "KLayout tech file"),
    "KLAYOUT_DRC_RUNSET": ("signoff_decks", "KLayout DRC deck"), "NETGEN_SETUP": ("signoff_decks", "Netgen LVS setup"),
    "EXTRA_LEFS": ("macro_views", "macro LEF"), "EXTRA_GDS": ("macro_views", "macro GDS"),
    "EXTRA_LIBS": ("macro_views", "macro Liberty"),
}
_SUGGEST = {
    "pdk_views": "check the PDK installation is complete for this version (ciel enable <version>, PDK_ROOT)",
    "corner_data": "list the missing corners; install the PDK version the config names",
    "signoff_decks": "check the PDK installation and that the tool versions in the image match the decks",
    "macro_views": "generate the macro's views (memory compiler) or take them from the PDK",
}
_RUN_FLAGS = {"drc_magic": "RUN_MAGIC_DRC", "drc_klayout": "RUN_KLAYOUT_DRC", "lvs": "RUN_LVS"}
# (regex on flow.log/error.log, category, what is missing, suggestion)
_NOT_THERE = r"(?=.*(?:no such file or directory|not found|does not exist|cannot open|could not open))"
LOG_RULES = [
    (re.compile(rf"(?im)^{_NOT_THERE}(?=.*\.(?:lef|gds|gds2|oas)\b).*$"),
     "pdk_views", "LEF/GDS view", _SUGGEST["pdk_views"]),
    (re.compile(rf"(?im)^{_NOT_THERE}(?=.*\.(?:lib|rules|spef)\b).*$"),
     "corner_data", "timing library or RC rules of a corner", _SUGGEST["corner_data"]),
    (re.compile(rf"(?im)^{_NOT_THERE}(?=.*\.(?:drc|lydrc|lyt|tech|magicrc|lvs)\b).*$"),
     "signoff_decks", "DRC/LVS deck", _SUGGEST["signoff_decks"]),
    (re.compile(r"(?m)^.*\b(?:PDK_ROOT|PDK)\b.*(?i:not found|not set|does not exist|missing|invalid).*$"),
     "pdk_views", "PDK installation", "install or enable the PDK with ciel and set PDK_ROOT"),
    (re.compile(r"(?m)^.*\[ERROR (?:IFP|PPL)-\d+\].*$"), "floorplan_config", "floorplan or pin placement setting",
     "set DIE_AREA / FP_CORE_UTIL / IO_PIN_ORDER_CFG in the LibreLane config; the values are the designer's"),
    (re.compile(r"(?m)^.*\[ERROR PDN-\d+\].*$"), "pdn_config", "power distribution setting",
     "set PDN_* keys (or PDN_CFG) in the LibreLane config, starting from the PDK defaults"),
    (re.compile(r"(?m)^.*\[ERROR MPL-\d+\].*$"), "macro_placement", "macro placement",
     "give MACRO_PLACEMENT_CFG / MACROS placement in the LibreLane config"),
    (re.compile(r"(?im)^.*(?:unable to find image|pull access denied|manifest unknown).*$"), "image",
     "container image", "docker pull the image named in [flow]/[pnr] (a person approves the download)"),
    (re.compile(r"(?im)^.*\b(?:padring|pad ring|PAD_CORNER|PAD_SITE_NAME)\b.*(?:error|not|missing).*$"), "io_ring",
     "IO ring", "pads belong to chip-level integration, not to this block's flow"),
]


def _paths_of(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [p for v in value for p in _paths_of(v)]
    if isinstance(value, dict):
        return [p for v in value.values() for p in _paths_of(v)]
    return []


def setup_issues(resolved: dict, log_text: str, rules: dict[str, dict] | None = None) -> list[dict]:
    """§0.11 physical-design rows this tool can detect: PDK and macro views or signoff decks
    the config names but that are not on disk, signoff checks switched off, and setup errors in
    the logs. Each issue: stage, category, missing, evidence, suggestion, who."""
    issues = []
    for key, (cat, what) in _PATH_KEYS.items():
        for p in _paths_of(resolved.get(key)):
            if p.startswith("/") and not Path(p).exists():
                issues.append({"stage": "pnr", "category": cat, "missing": f"{what}: {p}",
                               "evidence": f"resolved.json {key}", "suggestion": _SUGGEST[cat], "who": "person"})
    for name, macro in (resolved.get("MACROS") or {}).items():
        views = macro if isinstance(macro, dict) else {}
        for view in ("gds", "lef"):
            if not _paths_of(views.get(view)):
                issues.append({"stage": "pnr", "category": "macro_views", "missing": f"macro {name}: no {view.upper()}",
                               "evidence": "resolved.json MACROS", "suggestion": _SUGGEST["macro_views"],
                               "who": "person"})
    for rule, flag in _RUN_FLAGS.items():
        if rules and rules[rule]["outcome"] == "missing" and resolved.get(flag) is False:
            issues.append({"stage": "pnr", "category": "signoff_disabled", "missing": f"{rule}: {flag} is false",
                           "evidence": f"resolved.json {flag}", "suggestion": f"set {flag}: true in the LibreLane config",
                           "who": "person"})
    seen = set()
    for rx, cat, what, suggestion in LOG_RULES:
        for m in rx.finditer(log_text):
            line = m.group(0).strip()[:300]
            if (cat, line) in seen:
                continue
            seen.add((cat, line))
            issues.append({"stage": "pnr", "category": cat, "missing": what, "evidence": line,
                           "suggestion": suggestion, "who": "person"})
    return issues[:30]


# --- Comparisons ----------------------------------------------------------------------------

COMPARED = [
    ("die_area_um2", ("area", "die_area_um2")), ("core_area_um2", ("area", "core_area_um2")),
    ("utilization", ("area", "utilization")), ("stdcell_count", ("area", "stdcell_count")),
    ("stdcell_area_um2", ("area", "stdcell_area_um2")), ("sequential_count", ("area", "sequential_count")),
    ("timing_repair_buffers", ("area", "timing_repair_buffers")),
    ("power_total_w", ("power", "total_w")), ("wirelength_um", ("routing", "wirelength_um")),
    ("vias", ("routing", "vias")), ("drc_magic", ("drc", "magic")), ("drc_klayout", ("drc", "klayout")),
    ("lvs_errors", ("lvs", "errors")), ("antenna_nets", ("antenna", "violating_nets")),
    ("max_slew_violations", ("electrical", "max_slew_violations")),
    ("max_cap_violations", ("electrical", "max_cap_violations")),
    ("max_fanout_violations", ("electrical", "max_fanout_violations")),
    ("runtime_s", ("runtime_s",)),
]
CORNER_COMPARED = ("setup_wns", "setup_tns", "setup_violations", "hold_wns", "hold_violations")
_CONFIG_DIFF_SKIP = re.compile(r"^(?:meta|DESIGN_DIR|PDK_ROOT)$")


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


def compare_runs(new: dict, base: dict, new_resolved: dict, base_resolved: dict) -> dict:
    """Deltas run - baseline, plus what makes the two runs not directly comparable."""
    out = {"baseline": base["tag"], "metrics": {k: _delta(_get(base, p), _get(new, p)) for k, p in COMPARED},
           "corners": {}}
    for c in sorted(set(new["timing"]) & set(base["timing"])):
        out["corners"][c] = {k: _delta(base["timing"][c].get(k), new["timing"][c].get(k)) for k in CORNER_COMPARED}
    notes = []
    for label, key in (("top", "design"), ("LibreLane version", "librelane_version"), ("PDK version", "pdk_version"),
                       ("clock period", "clock_period")):
        if new.get(key) != base.get(key):
            notes.append(f"different {label}: baseline {base.get(key)}, run {new.get(key)}")
    if new.get("sdc", {}).get("sha256") is None or base.get("sdc", {}).get("sha256") is None:
        notes.append("the SDC of at least one run is not known for sure; timing deltas may mix constraints")
    elif new["sdc"]["sha256"] != base["sdc"]["sha256"]:
        notes.append("different SDC (sha256 differs): timing deltas mix design and constraint changes")
    out["not_comparable"] = notes
    out["config_differences"] = _config_diff(base_resolved, new_resolved, base["run"], new["run"])
    return out


def _config_diff(a: dict, b: dict, run_a: str, run_b: str, limit: int = 20) -> list[dict]:
    """LibreLane keys whose values differ, ignoring paths into each run's own directory."""
    def norm(v):
        s = json.dumps(v, sort_keys=True)
        return s.replace(run_a, "<run>").replace(run_b, "<run>")
    keys = sorted(k for k in set(a) | set(b) if not _CONFIG_DIFF_SKIP.match(k) and norm(a.get(k)) != norm(b.get(k)))
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
        pre = f["pre_pnr_timing"].get(c)
        if pre:
            out[c] = {"setup_wns": _delta(pre.get("setup_wns"), post.get("setup_wns")),
                      "hold_wns": _delta(pre.get("hold_wns"), post.get("hold_wns"))}
    return out


def compare_synth(f: dict, synth: dict, sdc_sha: str | None) -> dict:
    """The M2 synth estimate (an envelope dict) against this run's post-route numbers."""
    m = synth.get("metrics") or {}
    out = {"synth_status": synth.get("status"), "synth_top": m.get("top") or None, "run_top": f.get("design"),
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
    notes = ["post-route cell counts include CTS, timing-repair, tap and antenna cells that synthesis does not have"]
    if not out["same_sdc"]:
        notes.append("synth and this run were timed under different (or unknown) SDCs")
    out["notes"] = notes
    return out


# --- The tools ------------------------------------------------------------------------------

def _root_path(project: Project, p: str) -> Path:
    q = Path(os.path.expanduser(expand_vars(p, project_env(project))))
    return (q if q.is_absolute() else project.root / q).resolve()


def runs_dir(project: Project) -> Path:
    pnr = project.pnr
    assert pnr is not None
    return _root_path(project, pnr.runs_dir) if pnr.runs_dir else _root_path(project, pnr.config).parent / "runs"


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
    if not (p / "resolved.json").is_file():
        known = sorted(d.name for d in base.iterdir() if (d / "resolved.json").is_file()) if base.is_dir() else []
        return f"no LibreLane run at {p} (runs under {base}: {', '.join(known) or 'none'})"
    return p


def _refuse(tool: str, project: Project) -> ToolResult | None:
    if project.pnr is None:
        return ToolResult(tool, "error", "E4", "Nothing was read or run.",
                          summary=f"project '{project.name}' has no [pnr] section (config = the LibreLane config): "
                                  "the RTL-to-GDS flow is not set up for it", error_class="not_configured")
    return None


def _scope(f: dict, sdc: dict, corners: list[str], level: str) -> str:
    decks = f["decks"]
    sdc_txt = (f"SDC {sdc['path']} (sha256 {sdc['sha256']}, {sdc['basis']})" if sdc["sha256"] else
               f"SDC {sdc['path']} whose content at run time is not known ({sdc['basis']})")
    return (
        f"LibreLane {f['librelane_version']} run '{f['tag']}' of top '{f['design']}' on {f['pdk']} "
        f"(PDK version {str(f['pdk_version'])[:12]}): post-route STA with extracted parasitics on corner(s) "
        f"{', '.join(corners) or 'none'} at a {f['clock_period']} ns clock under {sdc_txt}; Magic DRC "
        f"({Path(str(decks['magic_tech'])).name}), KLayout DRC ({Path(str(decks['klayout_drc_runset'])).name}), "
        f"Netgen LVS ({Path(str(decks['netgen_setup'])).name}) and the OpenROAD antenna check on the final "
        f"layout. Signoff level {level}" + {"E5-partial": ": the physical-signoff parts of E5", "E5": "",
                                            "E4": " (not all of post-route STA on several corners, DRC and LVS "
                                                  "have results)"}[level] + ". It does not show gate-level "
        "simulation of the routed netlist, power from real switching activity (power here is vectorless), "
        "timing at RC-extraction corners other than those named, or anything about function."
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
        risks.append("no gate-level simulation of the routed netlist (with SDF): behaviour after PnR is not checked")
        risks.append("power is vectorless (OpenSTA default switching activity at the default corner), not from a VCD")
    if corners and all(c.startswith("nom_") for c in corners):
        risks.append("only nominal RC extraction was timed (nom_* corners); min/max RC corners were not analysed")
    risks.append("timing is only as meaningful as the SDC: its I/O delays and exceptions are assumptions")
    if not sdc["sha256"]:
        risks.append(f"the SDC used at run time is not known for sure ({sdc['basis']}): timing cannot be tied "
                     "to the current SDC")
    if "Equivalence Check" in f["skipped_steps"] or any("EQY" in s for s in f["skipped_steps"]):
        risks.append("the netlist was not checked for equivalence with the RTL (Yosys.EQY skipped)")
    if f["ir_drop"]["worst_v"] is not None:
        risks.append("IR drop is OpenROAD's static estimate without real supply-pad locations")
    if _stale(changed):
        risks.append(f"the RTL changed after this run ({_stale(changed)}): it may not describe the current design")
    return risks


def run_flow_report(project: Project, run_ref: str, baseline: str | None = None,
                    synth_result: dict | None = None, max_violators: int = 5) -> ToolResult:
    tool = "flow_report"
    if refused := _refuse(tool, project):
        return refused
    pnr = project.pnr
    path = resolve_run(project, run_ref)
    if isinstance(path, str):
        return ToolResult(tool, "error", "E4", "Nothing was read.", summary=path, error_class="bad_config")
    f = read_run(path, violators=max_violators)
    resolved = _resolved(path)
    corners = [c for c in (pnr.signoff_corners or f["sta_corners"])]
    rules = evaluate_signoff(f, corners)
    status = signoff_status(rules)
    parts = e5_parts(f, rules, corners)
    level_name = signoff_level(parts)
    level = "E5" if level_name == "E5" else "E4"
    sdc = sdc_provenance(f, path)
    f["sdc"] = sdc
    changed = inputs_changed(resolved, f["started"])
    log_text = "\n".join((path / n).read_text(errors="replace") for n in ("flow.log", "error.log")
                         if (path / n).is_file())
    issues = setup_issues(resolved, log_text, rules)

    diags = [Diagnostic("error", f"SIGNOFF_{r.upper()}", d["detail"]) for r, d in rules.items() if d["outcome"] == "fail"]
    diags += [Diagnostic("warning", f"MISSING_{r.upper()}", d["detail"]) for r, d in rules.items()
              if d["outcome"] == "missing"]
    if not f["complete"]:
        diags.append(Diagnostic("warning", "INCOMPLETE", f"the flow did not finish: last step {f['last_step']}, "
                                                         f"metrics from {f['metrics_source']}"))
    for k, v in f["electrical"].items():
        if k.endswith("violations") and v:
            diags.append(Diagnostic("warning", k.upper(), f"{v} {k.replace('_', ' ')} (not a signoff rule here)"))
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
            bf = read_run(bpath, violators=0)
            bf["sdc"] = sdc_provenance(bf, bpath)
            comparison["baseline"] = compare_runs(f, bf, resolved, _resolved(bpath))
    if synth_result:
        comparison["synth"] = compare_synth(f, synth_result, sdc["sha256"])

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
        "drc_magic": f["drc"]["magic"], "drc_klayout": f["drc"]["klayout"], "lvs_errors": f["lvs"]["errors"],
        "lvs_match": f["lvs"]["report_match"], "antenna_nets": f["antenna"]["violating_nets"],
        "antenna_pins": f["antenna"]["violating_pins"],
        "area": f["area"], "power": f["power"], "ir_drop": f["ir_drop"], "routing": f["routing"],
        "electrical": f["electrical"], "drc": f["drc"], "lvs": f["lvs"], "antenna": f["antenna"],
        "flow_counts": f["flow_counts"], "cross_checks": f["cross_checks"], "runtime_s": f["runtime_s"],
        "config_sha256": f["config_sha256"], "pdk": f["pdk"], "pdk_version": f["pdk_version"],
        "librelane_version": f["librelane_version"], "inputs_changed": changed, "setup_issues": issues,
        "comparison": comparison,
    }
    if sdc["sha256"]:
        metrics["sdc_sha256"] = sdc["sha256"]  # only then may a timing claim cite this result (gate R5)
    side = _sidecar(path)
    versions = {"librelane": f["librelane_version"] or "unknown",
                "pdk": f"{f['pdk']}@{f['pdk_version'] or 'unknown'}",
                "image": side.get("image") or "not recorded in the run"}
    summary = (f"{f['tag']} ({f['design']}): signoff {status.upper()} [{level_name}]; setup WNS "
               f"{'n/a' if wns is None else f'{wns:+.3f}'} ns ({worst}), hold WNS "
               f"{'n/a' if hold is None else f'{hold:+.3f}'} ns; DRC Magic {f['drc']['magic']} / KLayout "
               f"{f['drc']['klayout']}; LVS errors {f['lvs']['errors']}; antenna nets "
               f"{f['antenna']['violating_nets']}; die {f['area']['die_area_um2']} um2, util "
               f"{f['area']['utilization']}, {f['area']['stdcell_count']} cells")
    arts = [artifact(path / "resolved.json"), artifact(path / "final" / "metrics.json")]
    if f["design"] and (gds := path / "final" / "gds" / f"{f['design']}.gds").is_file():
        arts.append(artifact(gds))
    arts += [artifact(p) for k, p in f["reports"].items() if not k.startswith("violators_")]
    if sdc["path"] and Path(sdc["path"]).is_file():
        arts.append(artifact(sdc["path"]))
    return ToolResult(
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
    """(docker command or the reason it may not run, run directory) for LibreLane on the copy in work/src."""
    pnr, flow = project.pnr, project.flow
    assert pnr is not None
    image = pnr.image or (flow.image if flow else "")
    pdk = pnr.pdk or (flow.pdk if flow else "")
    if not image or not pdk:
        return "[pnr] needs image and pdk (or a [flow] section that has them)", work
    src = work / "src"
    config = src / Path(pnr.config)
    pdk_root = _root_path(project, pnr.pdk_root)
    over = []
    for k, v in (overrides or {}).items():
        if k in NEVER_OVERRIDE or k not in pnr.allowed_overrides:
            return f"config key {k!r} may not be overridden (allowed: {list(pnr.allowed_overrides) or 'none'})", work
        over += ["-c", f"{k}={v}"]
    inner = ["librelane", "--manual-pdk", "--pdk-root", str(pdk_root), "--pdk", pdk,
             "--run-tag", tag, *over, str(config)]
    mounts = [(pdk_root, False)]
    if pdk_root.exists() and pdk_root.resolve() != pdk_root:
        mounts.append((pdk_root.resolve(), False))
    mounts.append((work.resolve(), True))
    cmd = docker_command(image, inner, mounts, project.allowed_images, env={"HOME": str(work.resolve())},
                         workdir=str(config.parent))
    rel_runs = runs_dir(project).relative_to(project.root) if runs_dir(project).is_relative_to(project.root) else None
    run_dir = (src / rel_runs if rel_runs else config.parent / "runs") / tag
    return cmd, run_dir


def _copy_root(project: Project, dest: Path) -> None:
    pnr = project.pnr
    root = project.root.resolve()
    skip = {(root / e).resolve() for e in pnr.copy_exclude} | {runs_dir(project).resolve()}

    def ignore(d: str, names: list[str]) -> list[str]:
        return [n for n in names if (Path(d) / n).resolve() in skip]
    shutil.copytree(root, dest, ignore=ignore, symlinks=True)


def run_flow(project: Project, tag: str | None = None, baseline: str | None = None,
             overrides: dict | None = None) -> ToolResult:
    """LibreLane RTL-to-GDS on a copy of the project root, then flow_report on the new run."""
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
        (work / "librelane.log").write_text(proc.output)
        side["finished"] = datetime.now().astimezone().isoformat(timespec="seconds")
        side["returncode"] = proc.returncode
        if run_dir.is_dir():
            (run_dir / SIDECAR).write_text(json.dumps(side, indent=1))
        if proc.returncode is None:
            res = ToolResult(tool, "timeout", "E4", "The flow did not finish.", error_class="timeout",
                             summary=f"LibreLane timed out after {pnr.timeout_s} s; partial run at {run_dir}")
        elif not (run_dir / "resolved.json").is_file():
            issues = setup_issues({}, proc.output)
            res = ToolResult(tool, "error", "E4", "The flow did not start.", error_class="setup" if issues else "infra",
                             summary="LibreLane produced no run: " + (proc.output.strip().splitlines() or ["no output"])[-1][:300],
                             metrics={"setup_issues": issues},
                             diagnostics=[Diagnostic("error", "SETUP", f"{i['missing']}: {i['evidence']}") for i in issues])
        else:
            res = run_flow_report(project, str(run_dir), baseline=baseline)
            res.tool = tool
            res.metrics["returncode"] = proc.returncode
        res.artifacts.append(artifact(work / "librelane.log"))
        return res
    finally:
        lock.unlink(missing_ok=True)
