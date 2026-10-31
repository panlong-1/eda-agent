"""Design snapshots and lineage consistency (plan §0.18): do results used together describe one design?

A snapshot names what a result was computed from:
- `design_id`: the RTL files with their sha256 (paths relative to the design root), the SDC's sha256
  and the PDK version: the design and its constraints;
- `id`: design_id plus defines, include directories, the flow's config, tool versions and the image
  digest: the design as one tool saw it.
Different tools read different subsets of the RTL (lint the whole SoC, synthesis one top), so two
results are the *same design* when every RTL file both list has the same hash and their SDC and PDK
agree. A result whose inputs changed after it was made has `state: older`.

`consistency(results)` checks a set of results used together (facts only): same design, same SDC,
PDK, clock period and corner set where they report them, no older snapshot, and every lineage
unbroken. Each issue carries a level for the receipt (fatal / error / warning).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

CURRENT, OLDER, UNKNOWN = "current", "older", "unknown"


def _h(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def sha256_path(path: str | Path) -> str | None:
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def rel_to(path: str | Path, roots: list[Path]) -> str:
    """A path relative to the first root it is under (resolved), else as given."""
    p = Path(path)
    rp = p.resolve() if p.exists() else p
    for r in roots:
        for cand in (p, rp):
            try:
                return str(cand.relative_to(r))
            except ValueError:
                continue
    return str(p)


def make_snapshot(rtl_files: list[str], roots: list[Path], *, sdc_sha256: str | None, pdk_version: str | None,
                  defines=(), include_dirs=(), flow_config_sha256: str | None = None, tools: dict | None = None,
                  image: str | None = None, state: str = CURRENT, note: str = "") -> dict:
    roots = [Path(r).resolve() for r in roots if r]
    rtl = {rel_to(f, roots): (sha256_path(f) if state == CURRENT else None) for f in rtl_files}
    design = {"rtl": rtl, "sdc_sha256": sdc_sha256, "pdk_version": pdk_version}
    full = {"design": design, "defines": sorted(defines), "include_dirs": sorted(rel_to(d, roots) for d in include_dirs),
            "flow_config_sha256": flow_config_sha256, "tools": tools or {}, "image": image}
    unknown = [k for k, v in rtl.items() if v is None] + (["SDC"] if not sdc_sha256 else []) + \
        (["PDK version"] if not pdk_version else [])
    known = state == CURRENT and not unknown
    return {"id": _h(full)[:16] if known else None, "design_id": _h(design)[:16] if known else None,
            "state": state if state != CURRENT or known else UNKNOWN, "note": note,
            "unknown_parts": unknown[:10], "rtl_files": len(rtl), "parts": full}


def pdk_version_of(pdk_root: str | Path | None, pdk: str = "") -> str | None:
    """A ciel PDK version (the hash in .../versions/<hash>/...), following symlinks."""
    if not pdk_root:
        return None
    p = Path(os.path.expanduser(str(pdk_root)))
    for cand in ([p / pdk] if pdk else []) + [p]:
        real = str(cand.resolve()) if cand.exists() else str(cand)
        if "/versions/" in real:
            return real.split("/versions/", 1)[1].split("/", 1)[0]
    return None


_PROJECT_CACHE: dict[tuple, dict] = {}


def project_snapshot(project) -> dict:
    """The design as the project's checkout holds it now: every RTL file the project config names
    (lint filelists and files, [flow] files), defines, include directories, the [flow] SDC, the PDK
    version, the project's tool versions and the [flow] image digest. Attached by the MCP server to
    every result that has no snapshot of its own (lint, regress, synth, mutate, perf). Cached per
    process and file modification times, so it is cheap to repeat."""
    from .proc import image_digest
    from .project import expand_vars, project_env, project_versions, read_filelist
    env = project_env(project)
    root = project.root

    def path(p: str) -> Path:
        q = Path(os.path.expanduser(expand_vars(p, env)))
        return q if q.is_absolute() else root / q
    files: list[Path] = []
    for fl in project.lint.filelists:
        if (root / fl).is_file():
            files += read_filelist(root / fl, env, root)
    files += [path(f) for f in project.lint.files]
    flow = project.flow
    if flow:
        files += [path(f) for f in flow.files]
    files = sorted({f.resolve() if f.exists() else f for f in files})
    sdc = path(flow.sdc) if flow else None
    key = (project.name, tuple((str(f), f.stat().st_mtime if f.exists() else 0) for f in files),
           sdc.stat().st_mtime if sdc and sdc.exists() else 0)
    if key in _PROJECT_CACHE:
        return _PROJECT_CACHE[key]
    defines = sorted(set(project.lint.defines) | set(flow.defines if flow else ()))
    incs = [path(d) for d in (*project.lint.include_dirs, *(flow.include_dirs if flow else ()))]
    tools = dict(project_versions(project))
    image = image_digest(flow.image) if flow else None
    snap = make_snapshot([str(f) for f in files], [root], sdc_sha256=sha256_path(sdc) if sdc else None,
                         pdk_version=pdk_version_of(path(flow.pdk_root), flow.pdk) if flow else None,
                         defines=defines, include_dirs=[str(d) for d in incs],
                         flow_config_sha256=_h(repr(flow)) if flow else None, tools=tools, image=image,
                         note="the project's checkout at the time of the call")
    _PROJECT_CACHE[key] = snap
    return snap


def attach(result, project):
    """Give a ToolResult the project's snapshot unless it carries its own (flow results do)."""
    if not getattr(result, "snapshot", None):
        try:
            result.snapshot = project_snapshot(project)
        except Exception as e:  # a snapshot must never stop a check from reporting
            result.snapshot = {"id": None, "design_id": None, "state": UNKNOWN, "note": f"snapshot failed: {e}"}
    return result


def compare_snapshots(a: dict, b: dict) -> dict:
    """Same design? Compares the RTL files both list, the SDC and the PDK version."""
    if not a or not b:
        return {"same_design": None, "differences": ["a result has no snapshot"]}
    if a.get("state") != CURRENT or b.get("state") != CURRENT:
        return {"same_design": None, "differences": [f"snapshot state {a.get('state')} / {b.get('state')}"]}
    da, db = a["parts"]["design"], b["parts"]["design"]
    common = set(da["rtl"]) & set(db["rtl"])
    diff = [f"RTL {f} differs" for f in sorted(common) if da["rtl"][f] != db["rtl"][f]]
    if not common:
        diff.append("no RTL file in common")
    if da["sdc_sha256"] != db["sdc_sha256"]:
        diff.append("SDC differs")
    if da["pdk_version"] != db["pdk_version"]:
        diff.append("PDK version differs")
    return {"same_design": not diff, "common_rtl_files": len(common), "differences": diff[:10]}


def consistency(results: list[dict]) -> dict:
    """Facts about a set of tool results (envelope dicts) used together. Issues: [{level, what}]."""
    issues: list[dict] = []
    snap = [(r.get("tool"), r.get("snapshot") or {}) for r in results]
    for tool, s in snap:
        if s.get("state") == OLDER:
            issues.append({"level": "fatal", "what": f"{tool}: made from an older snapshot ({s.get('note') or 'its inputs changed after it ran'})"})
        elif not s or s.get("state") == UNKNOWN:
            issues.append({"level": "error", "what": f"{tool}: snapshot unknown ({', '.join(s.get('unknown_parts') or ['none recorded'])})"})
    current = [(t, s) for t, s in snap if s.get("state") == CURRENT]
    for i, (ta, sa) in enumerate(current):
        for tb, sb in current[i + 1:]:
            c = compare_snapshots(sa, sb)
            if c["same_design"] is False and "no RTL file in common" not in c["differences"]:
                issues.append({"level": "fatal", "what": f"{ta} and {tb} are not the same design: {'; '.join(c['differences'])}"})
    for key, label in (("clock_period", "clock period"), ("pdk_version", "PDK version")):
        vals = {(r.get("metrics") or {}).get(key) for r in results} - {None}
        if len(vals) > 1:
            issues.append({"level": "error", "what": f"different {label}s: {sorted(map(str, vals))}"})
    shas = {(r.get("metrics") or {}).get("sdc_sha256") for r in results} - {None, "missing"}
    if len(shas) > 1:
        issues.append({"level": "fatal", "what": f"results under {len(shas)} different SDCs"})
    corner_sets = {tuple(sorted((r.get("metrics") or {}).get("signoff_corners") or [])) for r in results
                   if r.get("tool") in ("flow_report", "flow_run")} - {()}
    if len(corner_sets) > 1:
        issues.append({"level": "error", "what": f"different signoff corner sets: {sorted(corner_sets)}"})
    from .categories import scan_links
    for link in scan_links(results):  # scan insertion made a new netlist: only equivalence ties it to the old one
        if not link["equivalence_passed"]:
            issues.append({"level": "fatal", "what": "scan insertion changed the netlist and no passing equivalence "
                                                     "result covers the pre-scan and post-scan netlists"})
    for r in results:
        lin = (r.get("metrics") or {}).get("lineage")
        if lin and lin.get("breaks"):
            issues.append({"level": "fatal", "what": f"{r.get('tool')} {lin.get('run')}: lineage broken: "
                                                     f"{'; '.join(b['what'] for b in lin['breaks'][:3])}"})
    return {"ok": not any(i["level"] in ("fatal", "error") for i in issues), "issues": issues,
            "snapshots": sorted({s.get("id") or f"<{s.get('state') or 'none'}>" for _, s in snap})}
