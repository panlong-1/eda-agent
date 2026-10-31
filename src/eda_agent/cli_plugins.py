"""`eda-agent import`, `eda-agent plugins`, `eda-agent standard`: imported reports and their human-set standards.

    eda-agent import <dir> [--as <importer>]              judge a report made elsewhere, record it (provenance imported)
    eda-agent plugins list | check [--plugin NAME]        installed importers/adapters; run the contract tests
    eda-agent standard waive <category> <id> --reason R   a person approves a waiver (loosening: reason required)
    eda-agent standard unwaive <category> <id>
    eda-agent standard target <key> <value|none> [--reason R]   e.g. atpg.test_coverage_pct 95
"""
from __future__ import annotations

import argparse
import json

from .core.project import load_project
from .core.settings import PROJECT_FILE


def add_parsers(sub) -> None:
    im = sub.add_parser("import", help="import a report made elsewhere through an installed importer plug-in")
    im.add_argument("dir", help="the report directory")
    im.add_argument("--as", dest="plugin", help="importer name (default: the one that recognizes the directory)")
    im.add_argument("--project", help=f"project config (default: {PROJECT_FILE} here or in a parent)")
    pl = sub.add_parser("plugins", help="installed importer and adapter plug-ins; their contract tests")
    pl.add_argument("action", choices=["list", "check"])
    pl.add_argument("--plugin", help="only this importer")
    st = sub.add_parser("standard", help="human-set waivers and targets for imported checks (a person decides)")
    st.add_argument("action", choices=["waive", "unwaive", "target", "show"])
    st.add_argument("args", nargs="*", help="waive/unwaive: <category> <id>; target: <key> <value|none>")
    st.add_argument("--reason", default="", help="why; required when loosening (new waiver, lower target)")
    st.add_argument("--by", help="who decides (default: git user.name, else the login name)")
    st.add_argument("--project", help=f"project config (default: {PROJECT_FILE} here or in a parent)")


def dispatch(args: argparse.Namespace, resolve_project) -> int | None:
    if args.cmd == "plugins":
        from .plugins import LOAD_ERRORS, importers, load_adapters
        from .plugins.contract import check_importer
        imps = importers()
        if args.action == "list":
            adapters = load_adapters()
            for n, imp in sorted(imps.items()):
                print(f"importer {n} {getattr(imp, 'version', '?')}: categories {', '.join(getattr(imp, 'categories', ()))}")
            print(f"adapters from plug-ins: {', '.join(adapters) or 'none'} (built in: librelane)")
            for e in LOAD_ERRORS:
                print(f"LOAD ERROR {e}")
            return 0
        bad = 0
        for n, imp in sorted(imps.items()):
            if args.plugin and n != args.plugin:
                continue
            problems = check_importer(imp)
            bad += bool(problems)
            print(f"{n}: {'PASS' if not problems else 'FAIL'}")
            for p in problems:
                print(f"  - {p}")
        if not imps:
            print("no importer installed")
        return 1 if bad else 0
    if args.cmd not in ("import", "standard"):
        return None
    project = load_project(resolve_project(args.project))
    if args.cmd == "import":
        from pathlib import Path

        from .tools.importing import run_import
        res = run_import(project, Path(args.dir), args.plugin).to_dict()
        print(f"import: {res['status'].upper()} [{res['evidence_level']}] — {res['summary']}")
        print(f"claim scope: {res['claim_scope']}")
        snap = res.get("snapshot") or {}
        print(f"snapshot: {snap.get('id') or '-'} ({snap.get('state')}: {snap.get('note')})")
        for d in res["diagnostics"]:
            print(f"  {d['severity']:7} {d['code']} {d['msg']}")
        if r := res["metrics"].get("receipt"):
            print(f"receipt: {r}")
        return {"pass": 0, "fail": 1}.get(res["status"], 2)
    from .agent import human_gate
    from .core import standards
    by = args.by or human_gate.default_approver()
    try:
        if args.action == "show":
            print(json.dumps(standards.load(project), indent=1))
            return 0
        if args.action in ("waive", "unwaive"):
            cat, wid = args.args
            f = (standards.waive if args.action == "waive" else standards.unwaive)(project, cat, wid, by, args.reason)
        else:
            key, val = args.args
            f = standards.set_target(project, key, None if val == "none" else float(val), by, args.reason,
                                     lower_is_stricter=key.startswith("power."))
    except (ValueError, TypeError) as e:
        print(f"error: {e}")
        return 2
    print(f"recorded by {by} in {f}")
    return 0
