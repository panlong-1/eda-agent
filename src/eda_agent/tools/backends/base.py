"""The boundary between a back-end flow (LibreLane, later others) and everything above it.

An adapter knows one flow: how to find and read its runs, how its config is recorded, which of its
knobs change synthesis, which must never be touched, how to start a run (if it can), and how to
diagnose a run that could not start. It turns a run directory into the *neutral result* below.

Everything above the adapter works on the neutral result only: the signoff rules and E5 checklist
(tools/flow.py), comparisons and better/worse lists, the run history and experience tables
(tools/flow_history.py), gate rule R8 and the receipt. A second back end is a second adapter.

Three ways an adapter can make runs available:
- run_mode "local_container": the flow runs here in a container (LibreLane);
- run_mode "remote": the flow runs on another machine (e.g. a licensed server reached over SSH,
  with credentials the user manages); the adapter starts it and fetches the reports;
- run_mode "import": nothing is run; reports produced elsewhere are copied in and read.
Reading existing runs (`read_run`) is the same in all three, so imported runs need no extra code.

Neutral result (a dict; `empty_facts` lists every key with its default). Units: ns, um, um2, W.
  identity     run, tag, backend, backend_version, design, pdk, pdk_version, std_cell_library,
               started/finished (ISO), runtime_s, complete, stage ("full", "synth_sta", "partial",
               "partial (stopped after post-route STA)"), steps, last_step, skipped_steps,
               flow_errors, metrics_source, post_route_sta_step, config_sha256, rtl_files, sdc_file
  timing       clock_port, clock_period, sta_corners, default_corner,
               timing[corner] = setup_wns/setup_tns/setup_violations/hold_wns/hold_tns/hold_violations
                                (+ max_slew/cap/fanout_violations), post-route, signed worst slack;
               pre_pnr = cell_area_um2, cells, power_w, setup_tns, setup_violations,
                         max_fanout_violations, corners[corner] = setup_wns/setup_tns/hold_wns;
               clock[corner] = propagated, ideal (path counts), nonzero_source_latency, unpropagated_clocks;
               clock_skew; violators[corner] = [{check, group, from, to, slack}]
  physical     area (die/core/utilization/stdcell area and count/sequential/fill/...), power
               (total/internal/switching/leakage W, by_corner, method), ir_drop, routing, electrical
  signoff      drc = {by_tool: {tool: errors or None}, ...}; lvs = {tool, errors, report_match, ...};
               antenna = {tool, violating_nets, violating_pins, ...}; equivalence_checked (bool/None);
               signoff_tools = {"drc": {tool: deck}, "lvs": {tool: deck}, "antenna": tool};
               cross_checks; flow_counts; reports {name: path}
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path


def sha256_file(path: Path) -> str | None:
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def empty_facts(run: Path) -> dict:
    """The neutral result with nothing known: an adapter fills what its flow reports."""
    return {
        "run": str(run), "tag": Path(run).name, "backend": None, "backend_version": None, "exists": Path(run).is_dir(),
        "complete": False, "stage": "partial", "steps": 0, "last_step": None, "skipped_steps": [], "flow_errors": [],
        "metrics_source": None, "post_route_sta_step": None, "runtime_s": None, "started": None, "finished": None,
        "design": None, "pdk": None, "std_cell_library": None, "pdk_root": None, "pdk_version": None,
        "clock_port": None, "clock_period": None, "sta_corners": [], "default_corner": None, "config_sha256": None,
        "sdc_file": None, "rtl_files": [], "decks": {},
        "area": dict.fromkeys(("die_area_um2", "core_area_um2", "die_bbox", "utilization", "stdcell_area_um2",
                               "stdcell_count", "sequential_count", "fill_count", "instance_count",
                               "timing_repair_buffers", "io")),
        "timing": {}, "pre_pnr": {"cell_area_um2": None, "cells": None, "power_w": None, "setup_tns": None,
                                  "setup_violations": None, "max_fanout_violations": None, "corners": {}},
        "violators": {}, "clock": {}, "clock_skew": {"setup": None, "hold": None},
        "electrical": dict.fromkeys(("max_slew_violations", "max_cap_violations", "max_fanout_violations",
                                     "unannotated_nets", "unannotated_nets_filtered", "floating_nets")),
        "drc": {"by_tool": {}, "detailed_router": None, "illegal_overlap": None, "xor_differences": None,
                "rules_violated": {}},
        "lvs": {"tool": None, "errors": None, "report_final": None, "report_match": None},
        "antenna": {"tool": None, "violating_nets": None, "violating_pins": None},
        "equivalence_checked": None, "signoff_tools": {"drc": {}, "lvs": {}, "antenna": None},
        "manufacturability": {}, "power": {"total_w": None, "by_corner": {}, "method": None},
        "ir_drop": {"worst_v": None, "average_v": None}, "routing": dict.fromkeys(("wirelength_um", "vias")),
        "flow_counts": {}, "reports": {}, "cross_checks": [],
    }


class Backend:
    """Base adapter; subclasses fill in the flow-specific parts."""
    name = "base"
    run_mode = "import"                      # local_container | remote | import
    synthesis_keys = re.compile(r"^$")       # config keys that can change the synthesized netlist
    not_knobs = re.compile(r"^$")            # recorded config keys that are not knobs (paths, metadata)
    never_override: tuple[str, ...] = ()     # keys flow_run never sets (SDC, clock), on top of [pnr] limits

    def is_run(self, path: Path) -> bool:
        raise NotImplementedError

    def read_run(self, path: Path, violators: int = 10) -> dict:
        """The neutral result of one run directory; absent things stay None."""
        raise NotImplementedError

    def config(self, path: Path) -> dict:
        """The run's own record of its configuration, keys and values as the flow names them."""
        return {}

    def log_text(self, path: Path) -> str:
        return ""

    def default_runs_dir(self, config_file: Path) -> Path:
        return config_file.parent / "runs"

    def setup_issues(self, config: dict, log_text: str, rules: dict | None = None) -> list[dict]:
        return []

    def lessons(self, config_file: Path) -> dict[str, dict]:
        """Lessons people wrote into the flow's config (key -> {comment, value}); none by default."""
        return {}

    def run_command(self, project, work: Path, tag: str, overrides: dict | None = None):
        """(command or the reason it cannot run, run directory) for a run on the copy in work/src."""
        return f"the {self.name} adapter cannot start runs here (run_mode {self.run_mode}); import its reports", work


_REGISTRY: dict[str, Backend] = {}


def register_backend(backend: Backend) -> None:
    _REGISTRY[backend.name] = backend


def get_backend(name: str) -> Backend:
    if name not in _REGISTRY:
        raise ValueError(f"unknown flow backend {name!r} (known: {', '.join(sorted(_REGISTRY))})")
    return _REGISTRY[name]
