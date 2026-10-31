"""`eda-agent import <dir>`: a report made elsewhere becomes a judged result of this design.

1. an installed importer (plugins/) recognizes and parses the directory into a neutral result;
2. the core validates it, recomputes every file's sha256, and judges it by its category's rule
   (core/categories.py) with the human-set waivers and targets (core/standards.py);
3. the result is appended to the design's imported table (<experience_dir>/<project>/imported_reports.jsonl,
   provenance "imported") and a receipt is written (runs/imports/).
The agent reads these with the MCP tool `imported_results`; it never runs an import itself.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..core import standards
from ..core.categories import CATEGORIES, judge, scope
from ..core.envelope import Diagnostic, ToolResult
from ..core.project import Project
from ..core.snapshot import UNKNOWN, make_snapshot, sha256_path
from ..plugins import choose
from ..plugins.result import NotMine, files_with_hashes, validate

REPORTS_FILE = "imported_reports.jsonl"
RECEIPTS_DIR = Path("runs/imports")


def _exp_dir(project: Project) -> Path:
    base = project.pnr.experience_dir if project.pnr else "runs/experience"
    return Path(base) / project.name


def read_manifest(path: Path) -> dict[str, str]:
    """RTL hashes from a manifest: `sha256sum` lines ("<sha256>  <path>") or JSON {path: sha256}
    (also {"files": {...}}). Paths as the manifest writes them (relative to the design root)."""
    text = Path(path).read_text(errors="replace")
    try:
        data = json.loads(text)
        data = data.get("files", data) if isinstance(data, dict) else {}
        return {str(k): str(v).lower() for k, v in data.items() if isinstance(v, str) and len(v) == 64}
    except ValueError:
        pass
    out = {}
    for line in text.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2 and len(parts[0]) == 64 and all(c in "0123456789abcdefABCDEF" for c in parts[0]):
            out[parts[1].lstrip("*").strip()] = parts[0].lower()
    return out


def _snapshot(project: Project, res: dict, files: dict, src: Path, manifest: Path | None = None) -> dict:
    """The design an imported report describes: RTL from a person-supplied manifest (--rtl-manifest),
    else from an `rtl_manifest` input the importer named, else from `rtl` inputs found here. SDC and
    PDK version may stay unknown; the RTL may not."""
    sdc = next((f["sha256"] for f in files["inputs"] if f["role"] == "sdc"), None)
    tools = {res["tool"]["name"]: res["tool"]["version"]}
    named = next((Path(f["path"]) for f in files["inputs"] if f["role"] == "rtl_manifest" and f["sha256"]), None)
    for mf, who in ((manifest, "a person-supplied RTL manifest"), (named, "the RTL manifest the report names")):
        if mf:
            hashes = read_manifest(mf)
            if hashes:
                snap = make_snapshot(hashes, [project.root], sdc_sha256=sdc, pdk_version=res.get("pdk_version"),
                                     tools=tools, required=("RTL",),
                                     note=f"imported report; RTL from {who} ({mf.name}, {len(hashes)} file(s))")
                snap["rtl_manifest"] = {"path": str(mf), "sha256": sha256_path(mf), "supplied_by": who}
                return snap
    rtl = [f["path"] for f in files["inputs"] if f["role"] == "rtl"]
    if not rtl:
        return {"id": None, "design_id": None, "state": UNKNOWN, "unknown_parts": ["RTL"],
                "note": "imported report: it names no RTL inputs and no RTL manifest was given, so the design it "
                        "describes is not identified (eda-agent import --rtl-manifest FILE)"}
    return make_snapshot(rtl, [project.root, src], sdc_sha256=sdc, pdk_version=res.get("pdk_version"),
                         tools=tools, required=("RTL",), note="imported report: RTL inputs as found here at import time")


def import_result(project: Project, src: Path, importer, res: dict, manifest: Path | None = None) -> ToolResult:
    """Judge a parsed neutral result (already validated) into an envelope."""
    cat = res["category"]
    std = standards.load(project)
    approved = set(std["waivers"].get(cat, []))
    status, reasons = judge(cat, res["metrics"], approved, std["targets"])
    files = files_with_hashes(res, src)
    n_approved = sum(str(w.get("id")) in approved for w in res["metrics"].get("waivers") or [] if isinstance(w, dict))
    metrics = {**res["metrics"], "category": cat, "tool_name": res["tool"]["name"], "design": res.get("design"),
               "approved_waivers": n_approved, "targets": {k: v for k, v in std["targets"].items() if k.startswith(cat)},
               "files": files, "importer": getattr(importer, "name", "?"),
               "importer_version": getattr(importer, "version", "?"), "source_dir": str(src.resolve()),
               "provenance": "imported", "experience": res.get("experience") or [], "notes": res.get("notes") or []}
    return ToolResult(
        tool="imported", status=status, evidence_level=CATEGORIES[cat]["level"],
        claim_scope=scope(cat, res["tool"], res.get("design"), res["metrics"], n_approved, std["targets"])
        + f" Imported from {src} by importer {metrics['importer']}; the core judged it, the report did not.",
        summary=f"{cat} ({res['tool']['name']} {res['tool']['version']}): {status}"
        + (f": {'; '.join(reasons)}" if reasons else ""),
        metrics=metrics, diagnostics=[Diagnostic("error" if status == "fail" else "warning", cat.upper(), r)
                                      for r in reasons][:20],
        diagnostics_total=len(reasons), artifacts=[{"path": f["path"], "sha256": f["sha256"]}
                                                   for f in files["outputs"] + files["inputs"]],
        tool_versions={res["tool"]["name"]: res["tool"]["version"]},
        error_class=None if status in ("pass", "fail") else "incomplete",
        residual_risk=[f"imported: {CATEGORIES[cat]['question']} is answered by {res['tool']['name']}'s own report; "
                       "eda-agent did not run the tool", "the result is only as complete as the report the tool wrote"],
        snapshot=_snapshot(project, res, files, src, manifest))


def run_import_all(project: Project, src: Path, write: bool = True, manifest: Path | None = None) -> list[ToolResult]:
    """Every installed importer that recognizes the directory (a synthesis run folder holds synthesis,
    STA and power reports): one judged result, record and receipt each."""
    from ..plugins import importers
    hits = []
    for n, imp in sorted(importers().items()):
        try:
            if float(imp.detect(src) or 0) > 0:
                hits.append(n)
        except Exception:
            continue
    if not hits:
        return [run_import(project, src, None, write, manifest)]  # the same "no importer" error as a single import
    return [run_import(project, src, n, write, manifest) for n in hits]


def run_import(project: Project, src: Path, plugin: str | None = None, write: bool = True,
               manifest: Path | None = None) -> ToolResult:
    src = Path(src)
    if not src.exists():
        return ToolResult("imported", "error", "E0", "Nothing was imported.", summary=f"{src} does not exist",
                          error_class="bad_config")
    if manifest is not None and not read_manifest(manifest):
        return ToolResult("imported", "error", "E0", "Nothing was imported.", error_class="bad_config",
                          summary=f"{manifest}: no RTL hashes in it (sha256sum lines or JSON {{path: sha256}})")
    imp, why = choose(src, plugin)
    if imp is None:
        return ToolResult("imported", "error", "E0", "Nothing was imported.", summary=why, error_class="no_importer")
    try:
        res = imp.parse(src)
    except NotMine as e:
        return ToolResult("imported", "error", "E0", "Nothing was imported.", error_class="no_importer",
                          summary=f"importer {imp.name} does not recognize {src}: {e}")
    if problems := validate(res, src):
        return ToolResult("imported", "error", "E0", "Nothing was imported.", error_class="bad_plugin_output",
                          summary=f"importer {imp.name} returned an invalid result: {'; '.join(problems[:5])}",
                          diagnostics=[Diagnostic("error", "PLUGIN_CONTRACT", p) for p in problems[:20]])
    env = import_result(project, src, imp, res, manifest)
    if write:
        rec = {"id": f"{res['category']}_{getattr(imp, 'name', 'x')}_{datetime.now():%Y%m%d_%H%M%S_%f}", "design": project.name,
               "imported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
               "made_by": "unknown (imported report)", "envelope": env.to_dict()}
        env.metrics["import_id"] = rec["id"]
        rec["envelope"] = env.to_dict()
        d = _exp_dir(project)
        d.mkdir(parents=True, exist_ok=True)
        with (d / REPORTS_FILE).open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
        env.metrics["receipt"] = str(write_receipt(rec))
    return env


def imported(project: Project, category: str | None = None) -> list[dict]:
    """Records of this design's imported reports, oldest first."""
    f = _exp_dir(project) / REPORTS_FILE
    recs = [json.loads(ln) for ln in f.read_text().splitlines() if ln.strip()] if f.is_file() else []
    return [r for r in recs if category is None or r["envelope"]["metrics"].get("category") == category]


def write_receipt(rec: dict) -> Path:
    """A small receipt for one import (json + md), next to the run receipts."""
    RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
    e = rec["envelope"]
    m = e["metrics"]
    js = RECEIPTS_DIR / f"{rec['design']}_{rec['id']}.receipt.json"
    js.write_text(json.dumps(rec, indent=1) + "\n")
    lines = [f"# Import receipt {rec['id']}", "", f"- **Design:** {rec['design']}  ·  **Category:** {m['category']}  ·  "
             f"**Tool:** {m['tool_name']} {e['tool_versions']}", f"- **Imported:** {rec['imported_at']} from "
             f"`{m['source_dir']}` by importer {m['importer']} {m['importer_version']}; made by: {rec['made_by']}",
             f"- **Status (core's rule):** {e['status']}  ·  **Level:** {e['evidence_level']}",
             f"- **Snapshot:** {e['snapshot'].get('id') or '-'} ({e['snapshot'].get('state')}: {e['snapshot'].get('note')})",
             "", f"Claim scope: {e['claim_scope']}", "", "| side | role | path | sha256 |", "|---|---|---|---|"]
    lines += [f"| {side} | {f['role']} | `{f['path']}` | `{(f['sha256'] or 'not found')[:16]}` |"
              for side in ("inputs", "outputs") for f in m["files"][side]]
    lines += ["", "Findings:"] + ([f"- [{d['severity']}] {d['msg']}" for d in e["diagnostics"]] or ["- none"])
    lines += ["", "Not covered:"] + [f"- {r}" for r in e["residual_risk"]]
    (RECEIPTS_DIR / f"{rec['design']}_{rec['id']}.receipt.md").write_text("\n".join(lines) + "\n")
    return js
