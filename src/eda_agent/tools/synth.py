"""Yosys synthesis and a pre-PnR OpenSTA timing estimate, both in the project's container image.

`synth` (evidence level E4) maps the [flow] top to the project's standard-cell library and
times the netlist under the project's own SDC on every configured corner. `check_sdc` asks
OpenSTA whether that SDC constrains the netlist completely; it reports facts and proves
nothing (E0). Both record the SDC's sha256: the SDC defines what "meets timing" means, so a
timing result is only meaningful together with the exact SDC it was measured against.

The design checkout and the PDK are mounted read-only; only this run's work directory
(runs/flow/<project>_<time>/) is writable.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

from ..core.envelope import Diagnostic, ToolResult, artifact, not_configured
from ..core.hashing import sha256_file
from ..core.numbers import parse_float
from ..core.proc import docker_command, image_digest, run
from ..core.project import FlowConfig, Project, expand_vars, project_env, read_command_file, read_filelist
from .lint import source_path

WORK_DIR = Path("runs/flow")
_VERSION_CACHE: dict[str, dict] = {}

# The ABC script LibreLane uses for its default strategy, cut to the mapping and the
# fanout buffering/sizing that keeps a pre-PnR estimate from being dominated by unbuffered nets.
ABC_SCRIPT = "strash\n&get -n\n&st\n&dch\n&nf\n&put\nbuffer -N 10\nupsize\ndnsize\nstime -p\n"

SYNTH_SCOPE = (
    "Yosys mapped top '{top}' to {lib}; OpenSTA estimated timing of that netlist under SDC {sdc} "
    "(sha256 {sha}) at a {period} ns clock on corner(s) {corners}. This is an estimate under this "
    "SDC, before placement and routing: ideal clocks, no wire parasitics. It is not signoff and "
    "says nothing about function."
)
SYNTH_RISK = [
    "pre-PnR estimate: no wire parasitics, ideal clock tree; post-route slack is usually worse",
    "timing is only as meaningful as the SDC: I/O delays and exceptions there are assumptions",
    "synthesis is not checked for equivalence with the RTL and no gate-level simulation was run",
]
CHECK_SCOPE = (
    "OpenSTA check_setup on the synthesized netlist of '{top}' under SDC {sdc} (sha256 {sha}): "
    "clocks defined, unconstrained endpoints, loops. Facts about the constraints only; it says "
    "nothing about whether the design meets them."
)
CHECK_RISK = [
    "inputs without set_input_delay are not endpoints and are not listed here",
    "the SDC's values (I/O budgets, uncertainty) are not judged, only whether they exist",
]

_STARTPOINT = re.compile(r"^Startpoint: (\S+)", re.M)
_ENDPOINT = re.compile(r"^Endpoint: (\S+)", re.M)
_ARRIVAL = re.compile(r"^\s*(-?[\d.]+)\s+data arrival time", re.M)
_SLACK = re.compile(r"^\s*(-?[\d.]+)\s+slack \((MET|VIOLATED)\)", re.M)
_WORST = re.compile(r"^worst slack (max|min) (\S+)", re.M)
_TNS = re.compile(r"^tns (max|min) (\S+)", re.M)
_TIME_UNIT = re.compile(r"^\s*time (\S+)", re.M)
_CLOCK = re.compile(r"^EDA_CLOCK (\S+) (\S+) ?(.*)$", re.M)
_UNCONSTRAINED = re.compile(r"^Warning: There (?:is|are) (\d+) unconstrained endpoints?\.\n((?:  \S.*\n?)*)", re.M)
_CHECK_WARN = re.compile(r"^Warning: (There (?:is|are) \d+ .*)$", re.M)
_YOSYS_ERROR = re.compile(r"^(?:ERROR|error): (.*)$|^\S+:\d+:\d+: error: (.*)$", re.M)
_PROBLEMS = re.compile(r"Found and reported (\d+) problems")
_LATCHES = re.compile(r"^(\d+) objects?\.", re.M)


class _Setup:
    """[flow] paths resolved against the project root and environment."""

    def __init__(self, project: Project):
        flow = project.flow
        assert flow is not None
        self.flow = flow
        self.env = env = project_env(project)
        root = project.root

        def path(p: str) -> Path:
            q = Path(os.path.expanduser(expand_vars(p, env)))
            return (q if q.is_absolute() else root / q).resolve()

        self.root = root
        self.sdc = path(flow.sdc)
        self.liberty = path(flow.liberty)
        self.corners = {name: path(lib) for name, lib in flow.corners.items()} or {"default": self.liberty}
        self.pdk_root = path(flow.pdk_root) if flow.pdk_root else None
        self.dont_use = path(flow.dont_use_file) if flow.dont_use_file else None
        files: list[Path] = []
        incs: list[Path] = []
        for cf in flow.command_files:
            cf_files, cf_incs = read_command_file(root / cf)
            # The design's own files are read from the checkout, not from the tool's copy
            # (lint.copy_of_root): FuseSoC rewrites that copy for whichever target was set up last.
            files += [Path(source_path(str(f), root, project)) for f in cf_files]
            incs += cf_incs
        for fl in flow.filelists:
            files += read_filelist(root / fl, env, root)
        self.files = [*files, *(path(f) for f in flow.files)]
        self.include_dirs = [*incs, *(path(d) for d in flow.include_dirs)]

    def missing(self) -> list[str]:
        needed = [self.sdc, self.liberty, *self.corners.values(), *self.files]
        if self.dont_use:
            needed.append(self.dont_use)
        return [str(p) for p in needed if not p.is_file()]

    def mounts(self, work: Path) -> list[tuple[Path, bool]]:
        ro = {self.root, *(p.parent for p in [self.sdc, *self.files])}
        if self.pdk_root:
            ro.add(self.pdk_root)
        ro |= {lib.parent for lib in self.corners.values()} | {self.liberty.parent}
        # Drop mounts already inside another read-only mount; the work dir is the only writable one.
        keep = [p for p in sorted(ro) if not any(p != q and p.is_relative_to(q) for q in ro)]
        return [*((p, False) for p in keep), (work.resolve(), True)]


def _dont_use_args(setup: _Setup) -> str:
    if not setup.dont_use:
        return ""
    cells = [ln.strip() for ln in setup.dont_use.read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    return " ".join(f"-dont_use {c}" for c in cells)


def synth_script(setup: _Setup, work: Path) -> str:
    flow = setup.flow
    incs = " ".join(f"-I {d}" for d in setup.include_dirs)
    defs = " ".join(f"-D {d}" for d in flow.defines)
    files = " ".join(map(str, setup.files))
    lib, du = setup.liberty, _dont_use_args(setup)
    # Library cells instantiated in the RTL (e.g. an integrated clock gate) are blackboxes from the
    # Liberty file; slang leaves them unresolved and `hierarchy -check` still fails on any module
    # that is neither in the RTL nor in the library.
    if flow.frontend == "slang":
        read = f"read_slang --top {flow.top} --ignore-unknown-modules {incs} {defs} {files}"
    else:
        read = f"read_verilog -sv {incs} {defs} {files}"
    read = f"read_liberty -lib {lib}\n{read}"
    abc = f"abc -liberty {lib} -D {flow.clock_period * 1000:g} -script {work / 'abc.script'} {du}"
    if flow.abc_driving_cell:
        abc += f" -constr {work / 'abc.constr'}"
    lines = [
        read,
        f"hierarchy -check -top {flow.top}",
        f"synth -top {flow.top} -flatten {' '.join(flow.synth_flags)}".strip(),
        f"tee -o {work / 'check.txt'} check",
        f"tee -o {work / 'latches.txt'} select -count t:$_DLATCH* t:$dlatch* t:$adlatch* t:$_SR_* t:$sr",
        f"dfflibmap -liberty {lib} {du}".strip(),
        abc.strip(),
        "setundef -zero",
    ]
    if flow.tie_hi and flow.tie_lo:
        (hc, hp), (lc, lp) = flow.tie_hi.split("/"), flow.tie_lo.split("/")
        lines.append(f"hilomap -singleton -hicell {hc} {hp} -locell {lc} {lp}")
    lines += ["splitnets", "opt_clean -purge"]
    if flow.buffer:
        lines.append(f"insbuf -buf {' '.join(flow.buffer.split('/'))}")
    lines += [
        f"tee -o {work / 'stat.json'} stat -json -liberty {lib}",
        f"write_verilog -noattr -noexpr -nohex -nodec {work / 'netlist.v'}",
    ]
    return "\n".join(lines) + "\n"


def sta_script(setup: _Setup, work: Path, liberty: Path, check_only: bool = False) -> str:
    flow = setup.flow
    head = [
        f"read_liberty {liberty}",
        f"read_verilog {work / 'netlist.v'}",
        f"link_design {flow.top}",
        # LibreLane SDCs read the clock from the environment; give them the [flow] values.
        f"set ::env(CLOCK_PERIOD) {flow.clock_period}",
        f"set ::env(CLOCK_PORT) {flow.clock_port}",
        f"read_sdc {setup.sdc}",
        "report_units",
        "foreach c [all_clocks] { puts \"EDA_CLOCK [get_name $c] [get_property $c period] "
        "[get_full_name [get_property $c sources]]\" }",
    ]
    if check_only:
        return "\n".join(head + ["check_setup -verbose -unconstrained_endpoints -multiple_clock "
                                 "-no_clock -loops -generated_clocks"]) + "\n"
    return "\n".join(head + [
        "report_checks -path_delay max -fields {fanout cap slew} -digits 3",
        "report_worst_slack -max -digits 3",
        "report_tns -max -digits 3",
        "report_worst_slack -min -digits 3",
        "report_tns -min -digits 3",
    ]) + "\n"


def parse_sta(text: str) -> dict:
    worst = {k: parse_float(v) for k, v in _WORST.findall(text)}
    tns = {k: parse_float(v) for k, v in _TNS.findall(text)}
    path = {}
    if (s := _STARTPOINT.search(text)) and (e := _ENDPOINT.search(text)):
        slack = _SLACK.search(text)
        arrival = _ARRIVAL.search(text)
        path = {"startpoint": s[1], "endpoint": e[1],
                "slack": float(slack[1]) if slack else None,
                "arrival": float(arrival[1]) if arrival else None}
    unit = _TIME_UNIT.search(text)
    unit = re.sub(r"^1(?=[a-z])", "", unit[1]) if unit else ""
    return {
        "setup_wns": worst.get("max"), "setup_tns": tns.get("max"),
        "hold_wns": worst.get("min"), "hold_tns": tns.get("min"),
        "worst_path": path, "time_unit": unit,
        "clocks": [{"name": n, "period": parse_float(p), "sources": s.split()} for n, p, s in _CLOCK.findall(text)],
    }


def parse_check(text: str) -> dict:
    unconstrained: list[str] = []
    count = 0
    if m := _UNCONSTRAINED.search(text):
        count = int(m[1])
        unconstrained = [ln.strip() for ln in m[2].splitlines() if ln.strip()]
    warnings = [w for w in _CHECK_WARN.findall(text) if "unconstrained endpoint" not in w]
    unit = _TIME_UNIT.search(text)
    return {"unconstrained_endpoints": count, "unconstrained": unconstrained, "other_warnings": warnings,
            "time_unit": re.sub(r"^1(?=[a-z])", "", unit[1]) if unit else "",
            "clocks": [{"name": n, "period": parse_float(p), "sources": s.split()} for n, p, s in _CLOCK.findall(text)]}


def parse_stat(stat: dict, top: str) -> dict:
    mods = stat.get("modules", {})
    mod = mods.get(f"\\{top}") or next(iter(mods.values()), {})
    cells = mod.get("num_cells_by_type") or mod.get("cells", {})
    return {"cells": mod.get("num_cells", 0), "area": mod.get("area"),
            "sequential_area": mod.get("sequential_area"),
            "top_cell_types": dict(sorted(cells.items(), key=lambda kv: -kv[1])[:8])}


def _versions(flow: FlowConfig, setup: _Setup, extra_images: tuple[str, ...] = ()) -> dict:
    """Image digest plus the yosys/sta version lines inside it (cached per image and process)."""
    if flow.image in _VERSION_CACHE:
        return _VERSION_CACHE[flow.image]
    versions = {"image": image_digest(flow.image)}
    for name, inner in (("yosys", ["yosys", "-V"]), ("opensta", ["sta", "-version"])):
        cmd = docker_command(flow.image, inner, [], extra_images)
        versions[name] = (run(cmd, setup.root, setup.env, 60).output.strip().splitlines() or ["unknown"])[0] \
            if isinstance(cmd, list) else "unknown"
    if versions["image"] != "unknown":
        _VERSION_CACHE[flow.image] = versions
    return versions


def _work_dir(project: Project, base: Path, kind: str) -> Path:
    work = base / f"{project.name}_{kind}_{datetime.now():%Y%m%d_%H%M%S_%f}"
    work.mkdir(parents=True, exist_ok=True)
    return work.resolve()  # mounted at the same path inside the container


def _refuse(tool: str, level: str, project: Project) -> ToolResult | None:
    if project.flow is None:
        return not_configured(tool, level, "Nothing was synthesized or timed.",
                              f"project '{project.name}' has no [flow] section (top, sdc, clock_port, "
                              "clock_period, liberty, image): synthesis and timing are not set up for it")
    return None


def _synthesize(project: Project, setup: _Setup, work: Path, tool: str, level: str, scope: str
                ) -> tuple[ToolResult | None, dict]:
    """Run Yosys; (an error/fail envelope or None, facts)."""
    flow = setup.flow
    (work / "abc.script").write_text(ABC_SCRIPT)
    if flow.abc_driving_cell:
        (work / "abc.constr").write_text(f"set_driving_cell {flow.abc_driving_cell}\nset_load {flow.abc_load:g}\n")
    (work / "synth.ys").write_text(synth_script(setup, work))
    inner = ["yosys", *(["-m", "slang"] if flow.frontend == "slang" else []), "-q",
             "-l", str(work / "yosys.log"), "-s", str(work / "synth.ys")]
    cmd = docker_command(flow.image, inner, setup.mounts(work), project.allowed_images)
    if isinstance(cmd, str):
        return ToolResult(tool, "error", level, scope, summary=cmd, error_class="not_allowed"), {}
    proc = run(cmd, setup.root, setup.env, flow.timeout_s)
    log = (work / "yosys.log").read_text(errors="replace") if (work / "yosys.log").is_file() else proc.output
    if proc.returncode is None:
        return ToolResult(tool, "timeout", level, scope, summary=f"yosys timed out after {flow.timeout_s} s",
                          error_class="timeout"), {}
    if proc.returncode != 0:
        errors = [a or b for a, b in _YOSYS_ERROR.findall(log + "\n" + proc.output)]
        if not errors:
            return ToolResult(tool, "error", level, scope, error_class="infra",
                              summary="yosys failed without a parseable error: " + proc.output[-500:]), {}
        return ToolResult(tool, "fail", level, scope, summary=f"synthesis failed: {errors[0]}",
                          metrics={"synth_errors": len(errors)}, error_class="elaboration",
                          diagnostics=[Diagnostic("error", "SYNTH", e) for e in errors[:10]],
                          diagnostics_total=len(errors), artifacts=[artifact(work / "yosys.log")]), {}
    check = (work / "check.txt").read_text() if (work / "check.txt").is_file() else ""
    latches = _LATCHES.search((work / "latches.txt").read_text()) if (work / "latches.txt").is_file() else None
    problems = _PROBLEMS.search(check)
    facts = {
        **parse_stat(json.loads((work / "stat.json").read_text()), flow.top),
        "latches": int(latches[1]) if latches else None,
        "check_problems": int(problems[1]) if problems else None,
        "check_lines": [ln.strip() for ln in check.splitlines() if ln.startswith("Warning")][:10],
    }
    return None, facts


def _sta(project: Project, setup: _Setup, work: Path, corner: str, liberty: Path, check_only: bool
         ) -> str | None:
    """Run OpenSTA on one corner; its output, or None if it could not run."""
    name = f"sta_{corner}" + ("_check" if check_only else "")
    (work / f"{name}.tcl").write_text(sta_script(setup, work, liberty, check_only))
    cmd = docker_command(setup.flow.image, ["sta", "-no_splash", "-exit", str(work / f"{name}.tcl")],
                         setup.mounts(work), project.allowed_images)
    if isinstance(cmd, str):
        return None
    proc = run(cmd, setup.root, setup.env, setup.flow.timeout_s)
    (work / f"{name}.log").write_text(proc.output)
    return proc.output if proc.returncode is not None else None


def _prepare(project: Project, tool: str, level: str, scope_t: str) -> tuple[ToolResult | None, _Setup | None, str]:
    if refused := _refuse(tool, level, project):
        return refused, None, scope_t
    setup = _Setup(project)
    sha = sha256_file(setup.sdc) if setup.sdc.is_file() else "missing"
    scope = scope_t.format(top=setup.flow.top, lib=setup.liberty.name, sdc=setup.flow.sdc, sha=sha,
                           period=f"{setup.flow.clock_period:g}", corners=", ".join(setup.corners))
    if missing := setup.missing():
        return ToolResult(tool, "error", level, scope, summary=f"missing input file(s): {missing[:5]}",
                          error_class="bad_config"), None, scope
    return None, setup, scope


def _clock_diags(flow: FlowConfig, clocks: list[dict]) -> list[Diagnostic]:
    """The SDC's clock on clock_port must exist and have the [flow] period."""
    on_port = [c for c in clocks if flow.clock_port in c["sources"]]
    if not on_port:
        return [Diagnostic("error", "NO_CLOCK", f"the SDC defines no clock on port '{flow.clock_port}' "
                                                f"(clocks: {[c['name'] for c in clocks] or 'none'})")]
    return [Diagnostic("warning", "CLOCK_PERIOD", f"clock {c['name']} has period {c['period']} but "
                                                  f"[flow] clock_period is {flow.clock_period}")
            for c in on_port if c["period"] is not None and abs(c["period"] - flow.clock_period) > 1e-6]


def run_synth(project: Project, work_base: Path = WORK_DIR) -> ToolResult:
    level = "E4"
    refused, setup, scope = _prepare(project, "synth", level, SYNTH_SCOPE)
    if refused:
        return refused
    flow = setup.flow
    work = _work_dir(project, work_base, "synth")
    versions = _versions(flow, setup, project.allowed_images)
    sdc_sha = sha256_file(setup.sdc)
    failed, facts = _synthesize(project, setup, work, "synth", level, scope)
    if failed:
        failed.tool_versions = versions
        failed.metrics["sdc_sha256"] = sdc_sha
        return failed

    corners: dict[str, dict] = {}
    for corner, lib in setup.corners.items():
        out = _sta(project, setup, work, corner, lib, check_only=False)
        if out is None:
            return ToolResult("synth", "error", level, scope, summary=f"OpenSTA did not finish on corner {corner}",
                              tool_versions=versions, error_class="infra", metrics={"sdc_sha256": sdc_sha})
        corners[corner] = parse_sta(out)
    first = next(iter(corners.values()))
    diags = _clock_diags(flow, first["clocks"])
    timed = {c: r for c, r in corners.items() if r["setup_wns"] is not None}
    if not timed or any(d.code == "NO_CLOCK" for d in diags):
        return ToolResult("synth", "error", level, scope, error_class="no_timing",
                          summary="OpenSTA found no constrained path: check the SDC with check_sdc",
                          diagnostics=diags, tool_versions=versions, metrics={"sdc_sha256": sdc_sha, **facts})
    worst_corner = min(timed, key=lambda c: timed[c]["setup_wns"])
    wns = timed[worst_corner]["setup_wns"]
    hold = min((r["hold_wns"] for r in timed.values() if r["hold_wns"] is not None), default=None)
    met = wns >= 0 and (hold is None or hold >= 0)
    if facts["latches"]:
        diags.append(Diagnostic("warning", "LATCH", f"{facts['latches']} latch(es) inferred"))
    diags += [Diagnostic("warning", "SYNTH_CHECK", ln) for ln in facts["check_lines"]]
    unit = first["time_unit"] or "?"
    path = timed[worst_corner]["worst_path"]
    # The period timing was measured at is the SDC's, which may differ from [flow] (CLOCK_PERIOD warning).
    sdc_period = next((c["period"] for c in first["clocks"]
                       if flow.clock_port in c["sources"] and c["period"] is not None), flow.clock_period)
    return ToolResult(
        tool="synth",
        status="pass" if met else "fail",
        evidence_level=level,
        claim_scope=scope,
        summary=(f"{facts['cells']} cells, area {facts['area']:.0f}, {facts['latches']} latch(es); "
                 f"setup WNS {wns:+.3f} {unit} (corner {worst_corner}), hold WNS "
                 f"{hold if hold is None else f'{hold:+.3f}'} at {sdc_period:g} ns; "
                 f"{'timing met' if met else 'timing NOT met'} under this SDC, pre-PnR estimate"),
        metrics={
            **{k: facts[k] for k in ("cells", "area", "sequential_area", "latches", "check_problems",
                                     "top_cell_types")},
            "clock_port": flow.clock_port, "clock_period": sdc_period, "flow_clock_period": flow.clock_period,
            "time_unit": unit, "sdc": flow.sdc, "sdc_sha256": sdc_sha, "liberty": setup.liberty.name,
            "wns": wns, "tns": min(r["setup_tns"] or 0.0 for r in timed.values()), "hold_wns": hold,
            "hold_tns": min(r["hold_tns"] or 0.0 for r in timed.values()),
            "worst_corner": worst_corner, "worst_path": {**path, "corner": worst_corner},
            "timing_met": met,
            "corners": {c: {k: r[k] for k in ("setup_wns", "setup_tns", "hold_wns", "hold_tns")}
                        for c, r in corners.items()},
        },
        diagnostics=diags[:20],
        diagnostics_total=len(diags),
        artifacts=[artifact(setup.sdc), artifact(work / "netlist.v"), artifact(work / "stat.json"),
                   artifact(work / "yosys.log"), *(artifact(work / f"sta_{c}.log") for c in corners)],
        tool_versions=versions,
        residual_risk=SYNTH_RISK + ([] if len(corners) > 1 else
                                    ["one library corner only: slow-corner timing was not estimated"]),
    )


def run_check_sdc(project: Project, max_listed: int = 20, work_base: Path = WORK_DIR) -> ToolResult:
    level = "E0"  # facts about the constraints; no evidence-level promotion
    refused, setup, scope = _prepare(project, "check_sdc", level, CHECK_SCOPE)
    if refused:
        return refused
    flow = setup.flow
    work = _work_dir(project, work_base, "check_sdc")
    versions = _versions(flow, setup, project.allowed_images)
    sdc_sha = sha256_file(setup.sdc)
    failed, _ = _synthesize(project, setup, work, "check_sdc", level, scope)
    if failed:
        failed.tool_versions = versions
        failed.metrics["sdc_sha256"] = sdc_sha
        return failed
    out = _sta(project, setup, work, "check", setup.liberty, check_only=True)
    if out is None:
        return ToolResult("check_sdc", "error", level, scope, summary="OpenSTA did not finish",
                          tool_versions=versions, error_class="infra", metrics={"sdc_sha256": sdc_sha})
    facts = parse_check(out)
    diags = _clock_diags(flow, facts["clocks"])
    diags += [Diagnostic("warning", "UNCONSTRAINED", f"unconstrained endpoint {p}")
              for p in facts["unconstrained"][:max_listed]]
    diags += [Diagnostic("warning", "CHECK_SETUP", w) for w in facts["other_warnings"]]
    complete = not diags
    return ToolResult(
        tool="check_sdc",
        status="pass" if complete else "fail",
        evidence_level=level,
        claim_scope=scope,
        summary=(f"{len(facts['clocks'])} clock(s), {facts['unconstrained_endpoints']} unconstrained "
                 f"endpoint(s), {len(facts['other_warnings'])} other check_setup warning(s)"),
        metrics={"clocks": facts["clocks"], "unconstrained_endpoints": facts["unconstrained_endpoints"],
                 "other_warnings": facts["other_warnings"], "time_unit": facts["time_unit"],
                 "sdc": flow.sdc, "sdc_sha256": sdc_sha, "clock_port": flow.clock_port,
                 "clock_period": flow.clock_period},
        diagnostics=diags[:max_listed + 5],
        diagnostics_total=len(diags),
        artifacts=[artifact(setup.sdc), artifact(work / "sta_check_check.log")],
        tool_versions=versions,
        residual_risk=list(CHECK_RISK),
    )
