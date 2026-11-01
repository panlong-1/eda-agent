"""`eda-agent lessons ...`: the design's lessons store (plan §0.16), read and changed by a person.

    eda-agent lessons list [--all]                      every entry with its status now
    eda-agent lessons search "query"                    what a run would retrieve (lessons + documents)
    eda-agent lessons add "text" --source run_id=X [--status verified] [--scope tool=synth ...]
    eda-agent lessons refute <id> "why" --source ...    a new entry that refutes an older one
    eda-agent lessons verify <id> --source ...          mark a hypothesis verified (with its evidence)
    eda-agent lessons void <id> --reason "..."          the human gate: void an entry (itself an entry)
    eda-agent lessons seed [--receipt runs/<id>.receipt.json ...]
        lessons from the flow config's comments and the imported experience table (flow_history:
        hypotheses), and from flow receipts (verified only if a person approved the receipt and the
        run is in this design's eda_agent table). Seeding twice adds nothing new.

Entries are append-only; who did it is git user.name (the same as approvals).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..agent.human_gate import default_approver
from ..agent.receipt import with_trace_data
from ..core.hashing import sha256_file
from ..core.memory import LessonStore, experience_base, known_ids, lesson_stores, project_docs, search
from ..core.project import load_project


def add_parser(sub) -> argparse.ArgumentParser:
    p = sub.add_parser("lessons", help="the design's lessons store: list, search, add, refute, verify, void, seed")
    p.add_argument("action", choices=["list", "search", "add", "refute", "verify", "void", "seed"])
    p.add_argument("args", nargs="*", help="search: query; add: text; refute: id text; verify/void: id")
    p.add_argument("--project")
    p.add_argument("--all", action="store_true", help="list: also voided, refuted and marker entries")
    p.add_argument("--status", default="hypothesis", choices=["hypothesis", "verified"])
    p.add_argument("--scope", action="append", default=[], help="key=value (module, tool, pdk, corner, problem)")
    p.add_argument("--source", action="append", default=[],
                   help="key=value[,key=value]: run_id, receipt, receipt_sha256, file, sha256, note")
    p.add_argument("--reason", default="")
    p.add_argument("--receipt", action="append", default=[], help="seed: a flow receipt to learn from")
    return p


def _kv(items: list[str]) -> dict:
    out = {}
    for it in items:
        k, _, v = it.partition("=")
        if not v:
            raise SystemExit(f"error: expected key=value, got {it!r}")
        out[k.strip()] = v.strip()
    return out


def _sources(items: list[str]) -> list[dict]:
    out = []
    for it in items:
        src = _kv(it.split(","))
        if src.get("file") and not src.get("sha256"):
            src["sha256"] = sha256_file(Path(src["file"]).expanduser())
        out.append(src)
    return out


def _print(e: dict) -> None:
    extra = f" -> {e['refers_to']}" if e.get("refers_to") else ""
    print(f"{e['id']}  [{e['kind']}{extra}] {e.get('status') or ''}  {e['text'] or e.get('reason', '')}")


def lessons(args: argparse.Namespace) -> int:
    project = load_project(args.project)
    store = LessonStore.for_project(project)
    by = default_approver()
    a = args.args
    try:
        if args.action == "list":
            for e in store.current().values():
                if args.all or (e["kind"] in ("lesson", "refute") and e["status"] in ("verified", "hypothesis")):
                    _print(e)
            print(f"({store.file})")
        elif args.action == "search":
            stores = lesson_stores(experience_base(project), project.name)
            mem = project.memory
            for h in search(" ".join(a), design=project.name, stores=stores, docs=project_docs(project),
                            lessons=mem.lessons, doc_hints=mem.doc_hints):
                print(f"{h.score:7.3f}  {h.id}  ({h.status}{'; ' + h.note if h.note else ''})  "
                      f"{' '.join(h.text.split())[:160]}\n          source: {h.source}")
        elif args.action == "add":
            _print(store.add(" ".join(a), status=args.status, scope=_kv(args.scope), sources=_sources(args.source),
                             by=by, origin="person", reason=args.reason))
        elif args.action == "refute":
            _print(store.add(" ".join(a[1:]), kind="refute", refers_to=a[0], status=args.status,
                             scope=_kv(args.scope), sources=_sources(args.source), by=by, origin="person",
                             reason=args.reason))
        elif args.action == "verify":
            if not args.source:
                raise ValueError("verify needs --source: the run or receipt that verifies it here")
            _print(store.add("", kind="verify", refers_to=a[0], sources=_sources(args.source), by=by,
                             origin="person", reason=args.reason))
        elif args.action == "void":
            _print(store.add("", kind="void", refers_to=a[0], by=by, origin="person (human gate)", reason=args.reason))
        else:
            for e in seed(project, store, [Path(r) for r in args.receipt], by):
                _print(e)
            print(f"known ids now: {len(known_ids([store], [])['lessons'])} ({store.file})")
    except (ValueError, IndexError) as e:
        print(f"error: {e}")
        return 2
    return 0


# ---------------------------------------------------------------- seeding from existing data

def flow_lessons(project) -> list[dict]:
    """Lessons from the flow config's comments, judged against the imported experience table
    (flow_history). Imported runs are hypotheses here (§0.15)."""
    from ..tools.flow_history import run_flow_history
    res = run_flow_history(project).to_dict()
    m = res.get("metrics") or {}
    if res.get("status") not in ("pass", "fail") or not m.get("lessons"):
        return []
    cfg = Path(project.root) / project.pnr.config
    runs = {Path(a["path"]).parent.name: a.get("sha256") for a in res.get("artifacts", [])}
    out = []
    for les in m["lessons"]:
        text = (f"{m.get('backend')} config {les['key']} = {les.get('value')!r}: \"{les.get('comment')}\". "
                f"Imported-table verdict: {les.get('verdict')} ({les.get('why')})")
        if les.get("evidence"):
            text += "; " + "; ".join(les["evidence"])
        sources = [{"file": str(cfg), "sha256": sha256_file(cfg), "note": "comment above the key"}]
        for ev in les.get("evidence") or []:
            for run, sha in runs.items():
                if run in ev.split() and all(s.get("run_id") != run for s in sources):
                    sources.append({"run_id": run, "file": f"{run}/resolved.json", "sha256": sha,
                                    "note": "imported run"})
        out.append({"text": text, "status": "hypothesis",
                    "scope": {"tool": m.get("backend"), "pdk": (project.flow.pdk if project.flow else ""),
                              "problem": les["key"]}, "sources": sources, "origin": "seed: flow config + imported table"})
    return out


def receipt_lessons(project, receipt_path: Path) -> list[dict]:
    """One lesson per flow result of a receipt: its signoff rules and details, copied as recorded."""
    r = json.loads(receipt_path.read_text(encoding="utf-8"))
    view = with_trace_data(r, receipt_path.parent)
    approved = (r.get("approval") or {}).get("status") == "approved"
    out = []
    for c in view["checks_run"]:
        m = c.get("metrics") or {}
        if c["tool"] not in ("flow_report", "flow_run") or not m.get("signoff"):
            continue
        rules = "; ".join(f"{k} {v.get('outcome')} ({v.get('detail')})" for k, v in m["signoff"].items())
        here = approved and m.get("experience_table") == "eda_agent" and r.get("project") == project.name
        text = (f"Flow run {m.get('tag')} ({m.get('backend')}, clock period {m.get('clock_period')} ns, PDK "
                f"{m.get('pdk')}): signoff_passed={m.get('signoff_passed')}, signoff_level "
                f"{m.get('signoff_level')}; {rules}")
        out.append({"text": text, "status": "verified" if here else "hypothesis",
                    "scope": {"tool": m.get("backend"), "pdk": m.get("pdk"), "problem": "signoff"},
                    "sources": [{"run_id": r["run_id"], "receipt": str(receipt_path),
                                 "receipt_sha256": sha256_file(receipt_path),
                                 "note": f"flow run {m.get('tag')}, receipt "
                                         + ("approved by " + str(r['approval'].get('by')) if approved else "not approved")}],
                    "origin": "seed: flow receipt"})
    return out


def seed(project, store: LessonStore, receipts: list[Path], by: str) -> list[dict]:
    have = {e["text"] for e in store.entries()}
    new = []
    for les in (flow_lessons(project) if project.pnr else []) + [x for p in receipts for x in receipt_lessons(project, p)]:
        if les["text"] in have:
            continue
        new.append(store.add(les["text"], status=les["status"], scope=les["scope"], sources=les["sources"], by=by,
                             origin=les["origin"]))
        have.add(les["text"])
    return new
