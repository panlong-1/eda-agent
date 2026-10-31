"""Experience tables: which runs a design's flow knowledge rests on, and where each came from.

Two tables per design (a design = one project config), never mixed across designs:
- "imported": runs found in the design's own runs directory, made before or outside eda-agent.
  Read-only; who made them and how is unknown beyond what the run itself recorded.
- "eda_agent": runs eda-agent's flow_run made for this design, one JSON line each in
  <[pnr] experience_dir>/<project>/eda_agent_runs.jsonl (run id, run directory, config patch,
  image digest, SDC hash, source commit, the agent run that asked for it). Starts empty.

What a design learned from its own eda_agent table is written to summary.json there; another
design may read it only as hypotheses, marked "from <design>, not verified here".
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from ..core.project import Project
from ..core.hashing import sha256_file

RUNS_FILE = "eda_agent_runs.jsonl"
SUMMARY_FILE = "summary.json"
IMPORTED = "imported"
EDA_AGENT = "eda_agent"
PROVENANCE = {
    IMPORTED: "imported: found in the design's own runs directory, made before or outside eda-agent; who made "
              "them and with what exactly is unknown beyond what each run recorded",
    EDA_AGENT: "made by eda-agent flow_run for this design, with run id, config patch, image digest, SDC hash "
               "and source commit recorded when it ran",
}


def experience_root(project: Project) -> Path:
    return Path(project.pnr.experience_dir)


def experience_dir(project: Project) -> Path:
    return experience_root(project) / project.name


def eda_records(project: Project) -> list[dict]:
    """The eda_agent table's records, in the order they were made."""
    f = experience_dir(project) / RUNS_FILE
    if not f.is_file():
        return []
    return [json.loads(ln) for ln in f.read_text().splitlines() if ln.strip()]


def record_eda_run(project: Project, run_dir: Path, side: dict, overrides: dict | None, sidecar: Path | None) -> dict:
    """Append one row to this design's eda_agent table (append-only)."""
    rec = {"run_id": run_dir.name, "run_dir": str(run_dir.resolve()), "design": project.name,
           "backend": project.pnr.backend, "made_by": "eda-agent flow_run",
           "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "agent_run_id": os.environ.get("EDA_AGENT_RUN_ID"),  # set by the agent loop when it starts the server
           "config_patch": dict(overrides or {}), "image": side.get("image"), "sdc_sha256": side.get("sdc_sha256"),
           "config_sha256": side.get("config_sha256"), "source_commit": side.get("source_commit"),
           "source_dirty": side.get("source_dirty"), "returncode": side.get("returncode"),
           "sidecar_sha256": sha256_file(sidecar) if sidecar else None}
    d = experience_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    with (d / RUNS_FILE).open("a") as fh:
        fh.write(json.dumps(rec) + "\n")
    return rec


def table_of(project: Project, run: Path, design_runs: Path) -> dict:
    """Which experience table a run belongs to, with its provenance."""
    run = run.resolve()
    for rec in eda_records(project):
        if Path(rec["run_dir"]) == run:
            return {"table": EDA_AGENT, "provenance": PROVENANCE[EDA_AGENT], "record": rec}
    if run.is_relative_to(design_runs.resolve()):
        return {"table": IMPORTED, "provenance": PROVENANCE[IMPORTED]}
    return {"table": "unlisted", "provenance": "neither in the design's runs directory nor recorded by flow_run"}


def write_summary(project: Project, summary: dict) -> Path:
    d = experience_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    (d / SUMMARY_FILE).write_text(json.dumps(summary, indent=1) + "\n")
    return d / SUMMARY_FILE


def other_designs(project: Project) -> list[dict]:
    """What other designs verified with their own eda-agent runs: hypotheses here, never facts."""
    root = experience_root(project)
    out = []
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if d.name == project.name or not (d / SUMMARY_FILE).is_file():
            continue
        try:
            s = json.loads((d / SUMMARY_FILE).read_text())
        except ValueError:
            continue
        for k in s.get("verified_knobs", []):
            out.append({"from_design": d.name, "backend": s.get("backend"), "knob": k.get("knob"),
                        "effect": k.get("summary"), "status": f"from {d.name}, not verified here"})
    return out
