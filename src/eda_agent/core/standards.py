"""Human-set standards for imported checks: approved waivers and targets (plan §0.19).

Like the SDC and the perf baseline, these are a person's decisions. They live in
<[pnr] experience_dir>/<project>/standards.json and change only through `eda-agent standard ...`
(never through an MCP tool). Loosening (a new waiver, a lower target, a removed target) needs a
reason; tightening (removing a waiver, raising a target) does not. Every change is kept in
`history` with who, when and why.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def standards_file(project) -> Path:
    base = project.pnr.experience_dir if project.pnr else "runs/experience"
    return Path(base) / project.name / "standards.json"


def load(project) -> dict:
    f = standards_file(project)
    try:
        data = json.loads(f.read_text())
    except (OSError, ValueError):
        data = {}
    return {"waivers": data.get("waivers", {}), "targets": data.get("targets", {}), "history": data.get("history", [])}


def _save(project, data: dict) -> Path:
    f = standards_file(project)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=1) + "\n")
    return f


def _record(data: dict, change: str, by: str, reason: str, loosening: bool) -> None:
    if loosening and not reason.strip():
        raise ValueError(f"{change} loosens a standard: give --reason (a person's decision, recorded)")
    data["history"].append({"change": change, "by": by, "reason": reason, "loosening": loosening,
                            "at": datetime.now().astimezone().isoformat(timespec="seconds")})


def waive(project, category: str, waiver_id: str, by: str, reason: str) -> Path:
    data = load(project)
    ids = data["waivers"].setdefault(category, [])
    if waiver_id not in ids:
        _record(data, f"waive {category} {waiver_id}", by, reason, loosening=True)
        ids.append(waiver_id)
    return _save(project, data)


def unwaive(project, category: str, waiver_id: str, by: str, reason: str = "") -> Path:
    data = load(project)
    ids = data["waivers"].get(category, [])
    if waiver_id in ids:
        _record(data, f"unwaive {category} {waiver_id}", by, reason, loosening=False)
        ids.remove(waiver_id)
    return _save(project, data)


def set_target(project, key: str, value: float | None, by: str, reason: str = "",
               lower_is_stricter: bool = False) -> Path:
    """Set or remove (value None) a target such as atpg.test_coverage_pct. Higher is stricter for
    coverage; for budgets such as power.total_w pass lower_is_stricter=True."""
    data = load(project)
    old = data["targets"].get(key)
    if value is None:
        loosening = old is not None
    elif old is None:
        loosening = False
    else:
        loosening = value > old if lower_is_stricter else value < old
    _record(data, f"target {key}: {old} -> {value}", by, reason, loosening)
    if value is None:
        data["targets"].pop(key, None)
    else:
        data["targets"][key] = value
    return _save(project, data)
