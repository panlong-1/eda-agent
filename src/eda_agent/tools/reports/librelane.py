"""Read one LibreLane run directory (runs/<tag>/) into plain facts. Nothing is run here.

Where each fact comes from, most trusted first:
- the metrics LibreLane recorded after a step (`<NN>-<step>/state_out.json`, and
  `final/metrics.json` once the whole flow finished): post-route timing is read from the
  post-route STA step's own metrics, so a run that stopped later still reports it, and a run
  that never reached it reports none;
- `resolved.json`: the configuration the run actually used (design, PDK and its version,
  corners, DRC/LVS decks, SDC path, LibreLane version);
- the signoff reports themselves (Magic and KLayout DRC, Netgen LVS, antenna), to cross-check
  the metric counts; a disagreement is reported, never silently resolved;
- flow.log: which steps ran, which were skipped, and whether the flow finished.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

_STEP_DIR = re.compile(r"^(\d+)-(.+)$")
_RUNNING = re.compile(r"^Running '([^']+)' at", re.M)
_SKIPPED = re.compile(r"^Skipping step '([^']+)'", re.M)
_LOG_ERROR = re.compile(r"^(?:\[ERROR[^\]]*\]|ERROR:?|Error:)\s*(.*)$", re.M)
_MAGIC_COUNT = re.compile(r"COUNT:\s*(\d+)")
_LVS_FINAL = re.compile(r"^Final result:\s*(.+?)\s*$", re.M)
_ANT_NETS = re.compile(r"Found (\d+) net violations")
_ANT_PINS = re.compile(r"Found (\d+) pin violations")
_MANUF = re.compile(r"^\* (\w[\w ]*)\s*\n\s*(Passed|Failed)", re.M)
_VIOLATOR = re.compile(r"^\[(setup|hold) ([\w-]+)\] (\S+) -> (\S+) : (-?[\d.]+)", re.M)
_POWER_TOTAL = re.compile(r"^Total\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)", re.M)
_CORNER_KEY = re.compile(r"^timing__setup__ws__corner:(.+)$")
_PDK_VERSION = re.compile(r"/versions/([0-9a-f]{7,40})(?:/|$)")
_RUNTIME = re.compile(r"^(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)$")

# Step names (after the number) whose outputs are read. "-1", "-2" suffixes mark repeats; the last wins.
STEP_PRE_STA = "openroad-staprepnr"
STEP_POST_STA = "openroad-stapostpnr"
STEP_MAGIC_DRC = "magic-drc"
STEP_KLAYOUT_DRC = "klayout-drc"
STEP_LVS = "netgen-lvs"
STEP_ANTENNA = "openroad-checkantennas"
STEP_MANUF = "misc-reportmanufacturability"


def sha256_file(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _json(path: Path) -> dict | list | None:
    try:
        return json.loads(path.read_text(errors="replace"))
    except (OSError, ValueError):
        return None


def _text(path: Path | None) -> str:
    try:
        return path.read_text(errors="replace") if path else ""
    except OSError:
        return ""


def steps(run: Path) -> list[tuple[int, str, Path]]:
    """(number, name, directory) of each step directory, in order."""
    out = []
    for d in run.iterdir() if run.is_dir() else ():
        if d.is_dir() and (m := _STEP_DIR.match(d.name)):
            out.append((int(m[1]), m[2], d))
    return sorted(out)


def find_step(run: Path, name: str) -> Path | None:
    """The last directory of step `name` (repeats are `name-1`, `name-2`, ...)."""
    hits = [d for _, n, d in steps(run) if n == name or re.fullmatch(rf"{re.escape(name)}-\d+", n)]
    return hits[-1] if hits else None


def step_metrics(step: Path | None) -> dict | None:
    """Metrics recorded after a step (cumulative over the flow so far), or None if it did not finish."""
    if not step:
        return None
    state = _json(step / "state_out.json")
    return state.get("metrics") if isinstance(state, dict) and isinstance(state.get("metrics"), dict) else None


def parse_flow_log(text: str) -> dict:
    return {"complete": "Flow complete." in text,
            "steps_run": _RUNNING.findall(text),
            "skipped": _SKIPPED.findall(text),
            "errors": [e.strip() for e in _LOG_ERROR.findall(text) if e.strip()][:20]}


def parse_magic_drc(text: str) -> int | None:
    m = _MAGIC_COUNT.search(text)
    return int(m[1]) if m else None


def parse_klayout_drc(data) -> tuple[int | None, dict]:
    """(total, rules with a non-zero count) from LibreLane's drc.klayout.json (rule -> count)."""
    if not isinstance(data, dict):
        return None, {}
    counts = {k: v for k, v in data.items() if isinstance(v, int) and not isinstance(v, bool)}
    return sum(counts.values()), {k: v for k, v in counts.items() if v}


def parse_lvs_report(text: str) -> dict:
    final = _LVS_FINAL.findall(text)
    last = final[-1] if final else None
    return {"final": last, "match": None if last is None else "match uniquely" in last.lower()}


def parse_antenna_summary(text: str) -> dict:
    nets, pins = _ANT_NETS.search(text), _ANT_PINS.search(text)
    return {"nets": int(nets[1]) if nets else None, "pins": int(pins[1]) if pins else None}


def parse_manufacturability(text: str) -> dict:
    return {name.strip(): verdict == "Passed" for name, verdict in _MANUF.findall(text)}


def parse_violators(text: str, limit: int = 10) -> list[dict]:
    return [{"check": kind, "group": group, "from": a, "to": b, "slack": float(s)}
            for kind, group, a, b, s in _VIOLATOR.findall(text)[:limit]]


def parse_power_report(text: str) -> dict | None:
    """Watts from the 'Total' row of an OpenSTA report_power."""
    m = _POWER_TOTAL.search(text)
    if not m:
        return None
    try:
        internal, switching, leakage, total = (float(x) for x in m.groups())
    except ValueError:
        return None
    return {"internal_w": internal, "switching_w": switching, "leakage_w": leakage, "total_w": total}


_CLK_NET = re.compile(r"clock network delay \((propagated|ideal)\)")
_SRC_LAT = re.compile(r"^\s*(-?[\d.]+)\s+(-?[\d.]+)\s+clock source latency", re.M)


def parse_clock_paths(text: str) -> dict:
    """How the clock was timed in OpenSTA path reports: paths with a propagated or an ideal
    clock network, and launch/capture clock source latencies that are not zero (a source latency
    applied to one side only shifts every slack)."""
    kinds = _CLK_NET.findall(text)
    nonzero = sorted({float(b) for a, b in _SRC_LAT.findall(text) if float(b) != 0.0})
    return {"propagated": kinds.count("propagated"), "ideal": kinds.count("ideal"),
            "nonzero_source_latency": nonzero[:5]}


def parse_runtime(text: str) -> float | None:
    m = _RUNTIME.match(text.strip())
    return (int(m[1] or 0) * 3600 + int(m[2]) * 60 + float(m[3])) if m else None


def corners(metrics: dict) -> list[str]:
    return sorted({m[1] for k in metrics if (m := _CORNER_KEY.match(k))})


def corner_timing(metrics: dict, corner: str) -> dict:
    """Setup/hold worst slack (signed, ns), TNS and violation counts of one corner."""
    def g(key: str):
        return metrics.get(f"{key}__corner:{corner}")
    return {
        "setup_wns": g("timing__setup__ws"), "setup_tns": g("timing__setup__tns"),
        "setup_violations": g("timing__setup_vio__count"), "setup_r2r_wns": g("timing__setup_r2r__ws"),
        "hold_wns": g("timing__hold__ws"), "hold_tns": g("timing__hold__tns"),
        "hold_violations": g("timing__hold_vio__count"),
        "max_slew_violations": g("design__max_slew_violation__count"),
        "max_cap_violations": g("design__max_cap_violation__count"),
        "max_fanout_violations": g("design__max_fanout_violation__count"),
    }


def _mtime(path: Path) -> str | None:
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds") \
        if path.exists() else None


def pdk_version(resolved: dict) -> str | None:
    m = _PDK_VERSION.search(str(resolved.get("PDK_ROOT") or ""))
    return m[1] if m else None


def read_run(run: Path, violators: int = 10) -> dict:
    """Every fact the flow tool needs from one run directory. Absent things are None, never guessed."""
    run = Path(run)
    resolved = _json(run / "resolved.json")
    resolved = resolved if isinstance(resolved, dict) else {}
    all_steps = steps(run)
    log = parse_flow_log(_text(run / "flow.log"))
    final = _json(run / "final" / "metrics.json")
    final = final if isinstance(final, dict) else None
    last_done = next((m for _, _, d in reversed(all_steps) if (m := step_metrics(d)) is not None), None)
    metrics = final if final is not None else (last_done or {})

    pre_step, post_step = find_step(run, STEP_PRE_STA), find_step(run, STEP_POST_STA)
    pre, post = step_metrics(pre_step), step_metrics(post_step)
    names = corners(post) if post else []
    reports: dict[str, Path] = {}

    def report(key: str, step_name: str, rel: str) -> Path | None:
        step = find_step(run, step_name)
        p = step / rel if step else None
        if p and p.is_file():
            reports[key] = p
            return p
        return None

    timing = {c: corner_timing(post, c) for c in names} if post else {}
    viol, power_by_corner, clock_by_corner = {}, {}, {}
    for c in names:
        if (vp := post_step / c / "violator_list.rpt").is_file():
            viol[c] = parse_violators(vp.read_text(errors="replace"), violators)
            reports[f"violators_{c}"] = vp
        if (pp := post_step / c / "power.rpt").is_file():
            power_by_corner[c] = parse_power_report(pp.read_text(errors="replace"))
        paths = [post_step / c / n for n in ("max.rpt", "min.rpt") if (post_step / c / n).is_file()]
        if paths:
            clk = parse_clock_paths("\n".join(p.read_text(errors="replace") for p in paths))
            unprop = post_step / c / "unpropagated.rpt"  # LibreLane lists clocks left ideal here
            clk["unpropagated_clocks"] = [ln.strip() for ln in _text(unprop).splitlines() if ln.strip()][:5]
            clock_by_corner[c] = clk
            reports[f"paths_{c}"] = paths[0]
    if post_step and (s := post_step / "summary.rpt").is_file():
        reports["sta_summary"] = s

    magic_rpt = report("magic_drc", STEP_MAGIC_DRC, "reports/drc.magic.rpt")
    kl_rpt = report("klayout_drc", STEP_KLAYOUT_DRC, "reports/drc.klayout.json")
    lvs_rpt = report("lvs", STEP_LVS, "reports/lvs.netgen.rpt")
    ant_rpt = report("antenna", STEP_ANTENNA, "reports/antenna.rpt")  # ends with "Found N net violations"
    manuf_rpt = report("manufacturability", STEP_MANUF, "manufacturability.rpt")
    kl_total, kl_rules = parse_klayout_drc(_json(kl_rpt)) if kl_rpt else (None, {})
    lvs = parse_lvs_report(_text(lvs_rpt)) if lvs_rpt else {"final": None, "match": None}
    ant = parse_antenna_summary(_text(ant_rpt)) if ant_rpt else {"nets": None, "pins": None}

    m = metrics
    runtime = [parse_runtime(_text(d / "runtime.txt")) for _, _, d in all_steps]
    facts = {
        "run": str(run), "tag": run.name, "exists": run.is_dir(),
        "complete": log["complete"] and final is not None,
        "steps": len(all_steps), "last_step": all_steps[-1][1] if all_steps else None,
        "skipped_steps": log["skipped"], "flow_errors": log["errors"],
        "metrics_source": "final/metrics.json" if final is not None else
        (next((f"{d.name}/state_out.json" for _, _, d in reversed(all_steps) if step_metrics(d) is not None), None)),
        "post_route_sta_step": post_step.name if post_step and post else None,
        "runtime_s": round(sum(r for r in runtime if r), 1) if any(runtime) else None,
        "started": _mtime(run / "resolved.json"), "finished": _mtime(run / "final" / "metrics.json"),
        "design": resolved.get("DESIGN_NAME"),
        "pdk": resolved.get("PDK"), "std_cell_library": resolved.get("STD_CELL_LIBRARY"),
        "pdk_root": resolved.get("PDK_ROOT"), "pdk_version": pdk_version(resolved),
        "librelane_version": (resolved.get("meta") or {}).get("librelane_version"),
        "flow_type": (resolved.get("meta") or {}).get("flow"),
        "clock_port": resolved.get("CLOCK_PORT"), "clock_period": resolved.get("CLOCK_PERIOD"),
        "sta_corners": resolved.get("STA_CORNERS") or names,
        "default_corner": resolved.get("DEFAULT_CORNER"),
        "config_sha256": sha256_file(run / "resolved.json"),
        "sdc_file": resolved.get("SIGNOFF_SDC_FILE") or resolved.get("PNR_SDC_FILE"),
        "pnr_sdc_file": resolved.get("PNR_SDC_FILE"),
        "decks": {"magic_tech": resolved.get("MAGIC_TECH"), "magicrc": resolved.get("MAGICRC"),
                  "magic_drc_runset": resolved.get("MAGIC_DRC_RUNSET"),
                  "klayout_drc_runset": resolved.get("KLAYOUT_DRC_RUNSET"),
                  "klayout_tech": resolved.get("KLAYOUT_TECH"), "netgen_setup": resolved.get("NETGEN_SETUP")},
        "area": {
            "die_area_um2": m.get("design__die__area"), "core_area_um2": m.get("design__core__area"),
            "die_bbox": m.get("design__die__bbox"),
            "utilization": m.get("design__instance__utilization"),
            "stdcell_area_um2": m.get("design__instance__area__stdcell"),
            "stdcell_count": m.get("design__instance__count__stdcell"),
            "sequential_count": m.get("design__instance__count__class:sequential_cell"),
            "fill_count": m.get("design__instance__count__class:fill_cell"),
            "instance_count": m.get("design__instance__count"),
            "timing_repair_buffers": m.get("design__instance__count__class:timing_repair_buffer"),
            "io": m.get("design__io"),
        },
        "timing": timing,
        "pre_pnr_timing": {c: corner_timing(pre, c) for c in corners(pre)} if pre else {},
        "violators": viol,
        "clock": clock_by_corner,
        "clock_skew": {"setup": m.get("clock__skew__worst_setup"), "hold": m.get("clock__skew__worst_hold")},
        "electrical": {"max_slew_violations": m.get("design__max_slew_violation__count"),
                       "max_cap_violations": m.get("design__max_cap_violation__count"),
                       "max_fanout_violations": m.get("design__max_fanout_violation__count"),
                       "unannotated_nets": m.get("timing__unannotated_net__count"),
                       # nets without parasitics after LibreLane drops those that need none (e.g. constants)
                       "unannotated_nets_filtered": m.get("timing__unannotated_net_filtered__count"),
                       "floating_nets": m.get("timing__drv__floating__nets")},
        "drc": {"magic": m.get("magic__drc_error__count"), "klayout": m.get("klayout__drc_error__count"),
                "detailed_router": m.get("route__drc_errors"),
                "magic_illegal_overlap": m.get("magic__illegal_overlap__count"),
                "xor_differences": m.get("design__xor_difference__count"),
                "magic_report_count": parse_magic_drc(_text(magic_rpt)) if magic_rpt else None,
                "klayout_report_count": kl_total, "klayout_rules_violated": kl_rules},
        "lvs": {"errors": m.get("design__lvs_error__count"),
                "unmatched_nets": m.get("design__lvs_unmatched_net__count"),
                "unmatched_devices": m.get("design__lvs_unmatched_device__count"),
                "unmatched_pins": m.get("design__lvs_unmatched_pin__count"),
                "property_fails": m.get("design__lvs_property_fail__count"),
                "report_final": lvs["final"], "report_match": lvs["match"]},
        "antenna": {"violating_nets": m.get("antenna__violating__nets"),
                    "violating_pins": m.get("antenna__violating__pins"),
                    "router_violations": m.get("route__antenna_violation__count"),
                    "diodes": m.get("antenna_diodes_count"),
                    "report_nets": ant["nets"], "report_pins": ant["pins"]},
        "manufacturability": parse_manufacturability(_text(manuf_rpt)) if manuf_rpt else {},
        "power": {"total_w": m.get("power__total"), "internal_w": m.get("power__internal__total"),
                  "switching_w": m.get("power__switching__total"), "leakage_w": m.get("power__leakage__total"),
                  "by_corner": power_by_corner},
        "ir_drop": {"worst_v": m.get("ir__drop__worst"), "average_v": m.get("ir__drop__avg")},
        "routing": {"wirelength_um": m.get("route__wirelength"),
                    "global_route_wirelength_um": m.get("global_route__wirelength"),
                    "max_wire_um": m.get("route__wirelength__max"), "vias": m.get("route__vias"),
                    "nets": m.get("route__net")},
        "flow_counts": {"errors": m.get("flow__errors__count"), "warnings": m.get("flow__warnings__count"),
                        "lint_warnings": m.get("design__lint_warning__count"),
                        "disconnected_pins": m.get("design__disconnected_pin__count"),
                        "critical_disconnected_pins": m.get("design__critical_disconnected_pin__count")},
        "reports": {k: str(v) for k, v in reports.items()},
    }
    facts["cross_checks"] = _cross_checks(facts)
    return facts


def _cross_checks(f: dict) -> list[dict]:
    """Metric counts against the counts in the report files they come from."""
    pairs = [
        ("Magic DRC count", f["drc"]["magic"], f["drc"]["magic_report_count"]),
        ("KLayout DRC count", f["drc"]["klayout"], f["drc"]["klayout_report_count"]),
        ("antenna violating nets", f["antenna"]["violating_nets"], f["antenna"]["report_nets"]),
        ("LVS clean", None if f["lvs"]["errors"] is None else f["lvs"]["errors"] == 0, f["lvs"]["report_match"]),
    ]
    return [{"what": what, "metric": a, "report": b, "agree": a == b}
            for what, a, b in pairs if a is not None and b is not None]
