"""The neutral result an importer's parse() returns, and the core's validation of it.

    {
      "category": "cdc",                         # one of core/categories.py CATEGORY_NAMES
      "tool": {"name": "SomeCDC", "version": "1.2"},
      "design": "torv_cpu",                       # top or block name, if the report says
      "metrics": {...},                           # the category's key numbers (required keys below)
      "inputs":  [{"path": "...", "role": "rtl|netlist|sdc|constraints|waivers|library|other",
                   "sha256": optional}],
      "outputs": [{"path": "...", "role": "report|netlist|patterns|other", "sha256": optional}],
      "corners": [...],                           # optional
      "experience": [{"knob": ..., "value": ..., "effect": ...}],   # optional, stays a hypothesis
      "notes": ["..."]                            # optional, free text from the report
    }

Never allowed (anywhere in the dict): status, evidence_level, verdict, claim_scope, signoff, ...
(core/categories.py FORBIDDEN_KEYS). The core decides those.
"""
from __future__ import annotations

from pathlib import Path

from ..core.categories import CATEGORIES, FORBIDDEN_KEYS
from ..core.snapshot import sha256_path

ROLES = ("rtl", "rtl_manifest", "netlist", "sdc", "constraints", "waivers", "library", "report", "patterns", "spef", "sdf",
         "vcd", "saif", "def", "gds", "other")


class NotMine(ValueError):
    """Raised by parse() for a directory the importer does not recognize. Never guess."""


def _forbidden(obj, where="") -> list[str]:
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in FORBIDDEN_KEYS:
                found.append(f"{where}{k}")
            found += _forbidden(v, f"{where}{k}.")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found += _forbidden(v, f"{where}{i}.")
    return found


def validate(res, base: Path | None = None) -> list[str]:
    """Problems with a parse() result ([] = acceptable). File paths are resolved against base."""
    if not isinstance(res, dict):
        return ["parse() must return a dict"]
    problems = []
    cat = res.get("category")
    if cat not in CATEGORIES:
        problems.append(f"unknown category {cat!r} (known: {', '.join(CATEGORIES)})")
    tool = res.get("tool") or {}
    if not tool.get("name") or not tool.get("version"):
        problems.append("tool.name and tool.version are required")
    m = res.get("metrics")
    if not isinstance(m, dict):
        problems.append("metrics must be a dict")
    elif cat in CATEGORIES:
        problems += [f"metrics.{k} is required for category {cat}" for k in CATEGORIES[cat]["required"] if k not in m]
    if bad := _forbidden(res):
        problems.append(f"the core decides these, a plug-in may not set them: {', '.join(bad)}")
    for side in ("inputs", "outputs"):
        items = res.get(side)
        if not isinstance(items, list) or not items and side == "outputs":
            problems.append(f"{side} must be a list" + (" with at least the report file" if side == "outputs" else ""))
            continue
        for it in items:
            if not isinstance(it, dict) or not it.get("path"):
                problems.append(f"each {side} entry needs a path")
                continue
            if it.get("role", "other") not in ROLES:
                problems.append(f"{side} role {it.get('role')!r} unknown (known: {', '.join(ROLES)})")
            p = Path(it["path"])
            p = p if p.is_absolute() or base is None else base / p
            real = sha256_path(p)
            if it.get("sha256") and real and it["sha256"] != real:
                problems.append(f"{side} {it['path']}: sha256 given by the plug-in does not match the file")
            if side == "outputs" and real is None:
                problems.append(f"output {it['path']} does not exist")
    return problems


def files_with_hashes(res: dict, base: Path) -> dict:
    """inputs/outputs with the core's own sha256 (None for files not present here)."""
    out = {}
    for side in ("inputs", "outputs"):
        out[side] = []
        for it in res.get(side) or []:
            p = Path(it["path"])
            p = p if p.is_absolute() else base / p
            out[side].append({"path": str(p), "role": it.get("role", "other"), "sha256": sha256_path(p)})
    return out
