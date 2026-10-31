"""`eda-agent flow-report` and `eda-agent flow-run`: the RTL-to-GDS flow tools without a model.

    eda-agent flow-report full_p10_fwd [--baseline full_p10] [--with-synth] [--json out.json]
    eda-agent flow-run [--tag mytag] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core.project import load_project
from .core.settings import PROJECT_FILE


def add_parsers(sub) -> None:
    fr = sub.add_parser("flow-report", help="read a finished LibreLane run: signoff checks, PPA, comparison")
    fr.add_argument("run", help="run name under the design's runs directory (or a path)")
    fr.add_argument("--project", help=f"project config (default: {PROJECT_FILE} here or in a parent)")
    fr.add_argument("--baseline", help="run to compare with (default: [pnr] baseline; '' for none)")
    fr.add_argument("--with-synth", action="store_true", help="also run synth and compare (about a minute)")
    fr.add_argument("--json", help="write the whole result here")
    rn = sub.add_parser("flow-run", help="run LibreLane RTL-to-GDS on a copy of the design (about an hour)")
    rn.add_argument("--project", help=f"project config (default: {PROJECT_FILE} here or in a parent)")
    rn.add_argument("--tag", help="run tag (default eda_<time>)")
    rn.add_argument("--baseline", help="run to compare with (default: [pnr] baseline)")
    rn.add_argument("--dry-run", action="store_true", help="print the command, run nothing")
    rn.add_argument("--json", help="write the whole result here")


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:.4g}"
    return "-" if v is None else str(v)


def print_result(res: dict) -> None:
    m = res.get("metrics") or {}
    print(f"{res['tool']}: {res['status'].upper()} — {res.get('summary', '')}")
    if res.get("status") == "error" and not m.get("signoff"):
        return
    print(f"\nevidence level {res['evidence_level']}, signoff level {m.get('signoff_level')}; "
          f"E5 parts: " + ", ".join(f"{k} {'yes' if v else 'no'}" for k, v in (m.get("e5_parts") or {}).items()))
    print("\nsignoff rules:")
    for rule, r in (m.get("signoff") or {}).items():
        print(f"  {r['outcome'].upper():8} {rule:12} {r['detail']}")
    print("\ntiming per corner (ns):  setup WNS   setup TNS  setup viol   hold WNS  hold viol")
    for c, t in (m.get("corners") or {}).items():
        print(f"  {c:22} {_fmt(t.get('setup_wns')):>9} {_fmt(t.get('setup_tns')):>11} "
              f"{_fmt(t.get('setup_violations')):>11} {_fmt(t.get('hold_wns')):>10} {_fmt(t.get('hold_violations')):>10}")
    a, p, r = m.get("area") or {}, m.get("power") or {}, m.get("routing") or {}
    print(f"\ndie {_fmt(a.get('die_area_um2'))} um2, core {_fmt(a.get('core_area_um2'))} um2, utilization "
          f"{_fmt(a.get('utilization'))}, {_fmt(a.get('stdcell_count'))} cells ({_fmt(a.get('sequential_count'))} flops)")
    print(f"power {_fmt(p.get('total_w'))} W (vectorless), wirelength {_fmt(r.get('wirelength_um'))} um, "
          f"vias {_fmt(r.get('vias'))}, flow runtime {_fmt(m.get('runtime_s'))} s")
    print(f"SDC {m.get('sdc')} ({m.get('sdc_basis')}), sha256 {m.get('sdc_sha256', 'not known for the run')}")
    print(f"versions: {res.get('tool_versions')}")
    cmp = m.get("comparison") or {}
    if b := cmp.get("baseline"):
        print(f"\nagainst baseline {b['baseline']} (run - baseline):")
        for k, row in b["metrics"].items():
            if "delta" in row and row["delta"]:
                pct = f" ({row['delta_pct']:+g}%)" if "delta_pct" in row else ""
                print(f"  {k:24} {_fmt(row['baseline']):>12} -> {_fmt(row['run']):>12}  {row['delta']:+g}{pct}")
        for c, rows in b["corners"].items():
            s = rows["setup_wns"]
            if "delta" in s:
                print(f"  setup WNS {c:14} {_fmt(s['baseline']):>12} -> {_fmt(s['run']):>12}  {s['delta']:+g}")
        for n in b["not_comparable"]:
            print(f"  note: {n}")
        if b["config_differences"]:
            print("  config keys that differ: " + ", ".join(d["key"] for d in b["config_differences"]))
    elif cmp.get("baseline_error"):
        print(f"\nbaseline: {cmp['baseline_error']}")
    if pre := cmp.get("pre_pnr_same_run"):
        print("\nsame run, pre-PnR STA -> post-route STA (setup WNS):")
        for c, rows in pre.items():
            s = rows["setup_wns"]
            print(f"  {c:22} {_fmt(s['baseline']):>9} -> {_fmt(s['run']):>9}  ({s.get('delta', 0):+g})")
    if sy := cmp.get("synth"):
        print(f"\nsynth estimate -> post-route (same SDC: {sy['same_sdc']}):")
        for c, rows in sy["corners"].items():
            s = rows["setup_wns"]
            print(f"  {c:22} {_fmt(s['baseline']):>9} -> {_fmt(s['run']):>9}  ({s.get('delta', 0):+g})")
        print(f"  cells {_fmt(sy['cells']['baseline'])} -> {_fmt(sy['cells']['run'])}; "
              f"cell area {_fmt(sy['cell_area_um2']['baseline'])} -> {_fmt(sy['cell_area_um2']['run'])} um2")
        for n in sy["notes"]:
            print(f"  note: {n}")
    if res.get("diagnostics"):
        print("\nfindings:")
        for d in res["diagnostics"]:
            print(f"  {d['severity']:7} {d['code']:22} {d['msg']}")
    print("\nnot covered:")
    for r in res.get("residual_risk") or []:
        print(f"  - {r}")


def dispatch(args: argparse.Namespace, resolve_project) -> int | None:
    if args.cmd not in ("flow-report", "flow-run"):
        return None
    from .tools.flow import flow_command, run_flow, run_flow_report
    project = load_project(resolve_project(args.project))
    if args.cmd == "flow-report":
        synth = None
        if args.with_synth and project.flow:
            from .tools.synth import run_synth
            synth = run_synth(project).to_dict()
        res = run_flow_report(project, args.run, baseline=args.baseline, synth_result=synth).to_dict()
    elif args.dry_run:
        if project.pnr is None:
            print("no [pnr] section")
            return 2
        work = Path(project.pnr.work_dir).resolve() / f"{project.name}_{args.tag or 'TAG'}"
        cmd, run_dir = flow_command(project, work, args.tag or "TAG")
        print(cmd if isinstance(cmd, str) else " ".join(cmd))
        print(f"run directory: {run_dir}")
        return 0
    else:
        res = run_flow(project, tag=args.tag, baseline=args.baseline).to_dict()
    print_result(res)
    if args.json:
        Path(args.json).write_text(json.dumps(res, indent=2) + "\n")
        print(f"\nresult: {args.json}")
    return {"pass": 0, "fail": 1}.get(res["status"], 2)
