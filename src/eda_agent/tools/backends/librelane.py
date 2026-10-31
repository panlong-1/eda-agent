"""LibreLane adapter: the first and default back end ([pnr] backend = "librelane").

Runs: `runs/<tag>/` with resolved.json, numbered step directories and final/; read by
tools/reports/librelane.py into the neutral result. Config: resolved.json (LibreLane's own key
names). Runs locally in the LibreLane container image without network.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from ...core.pnr import NEVER_OVERRIDE
from ...core.proc import docker_command
from ..reports.librelane import read_run as _read_run
from .base import Backend

_PATH_KEYS = {  # resolved.json key -> (category, what it is)
    "TECH_LEFS": ("pdk_views", "technology LEF"), "CELL_LEFS": ("pdk_views", "standard-cell LEF"),
    "CELL_GDS": ("pdk_views", "standard-cell GDS"), "LIB": ("corner_data", "timing library (Liberty)"),
    "RCX_RULESETS": ("corner_data", "RC extraction rules"),
    "MAGIC_TECH": ("signoff_decks", "Magic tech file (DRC deck)"), "MAGICRC": ("signoff_decks", "Magic rc file"),
    "KLAYOUT_TECH": ("signoff_decks", "KLayout tech file"),
    "KLAYOUT_DRC_RUNSET": ("signoff_decks", "KLayout DRC deck"), "NETGEN_SETUP": ("signoff_decks", "Netgen LVS setup"),
    "EXTRA_LEFS": ("macro_views", "macro LEF"), "EXTRA_GDS": ("macro_views", "macro GDS"),
    "EXTRA_LIBS": ("macro_views", "macro Liberty"),
}
_SUGGEST = {
    "pdk_views": "check the PDK installation is complete for this version (ciel enable <version>, PDK_ROOT)",
    "corner_data": "list the missing corners; install the PDK version the config names",
    "signoff_decks": "check the PDK installation and that the tool versions in the image match the decks",
    "macro_views": "generate the macro's views (memory compiler) or take them from the PDK",
}
_RUN_FLAGS = {"drc_magic": "RUN_MAGIC_DRC", "drc_klayout": "RUN_KLAYOUT_DRC", "lvs": "RUN_LVS"}
# (regex on flow.log/error.log, category, what is missing, suggestion)
_NOT_THERE = r"(?=.*(?:no such file or directory|not found|does not exist|cannot open|could not open))"
LOG_RULES = [
    (re.compile(rf"(?im)^{_NOT_THERE}(?=.*\.(?:lef|gds|gds2|oas)\b).*$"),
     "pdk_views", "LEF/GDS view", _SUGGEST["pdk_views"]),
    (re.compile(rf"(?im)^{_NOT_THERE}(?=.*\.(?:lib|rules|spef)\b).*$"),
     "corner_data", "timing library or RC rules of a corner", _SUGGEST["corner_data"]),
    (re.compile(rf"(?im)^{_NOT_THERE}(?=.*\.(?:drc|lydrc|lyt|tech|magicrc|lvs)\b).*$"),
     "signoff_decks", "DRC/LVS deck", _SUGGEST["signoff_decks"]),
    (re.compile(r"(?m)^.*\b(?:PDK_ROOT|PDK)\b.*(?i:not found|not set|does not exist|missing|invalid).*$"),
     "pdk_views", "PDK installation", "install or enable the PDK with ciel and set PDK_ROOT"),
    (re.compile(r"(?m)^.*\[ERROR (?:IFP|PPL)-\d+\].*$"), "floorplan_config", "floorplan or pin placement setting",
     "set DIE_AREA / FP_CORE_UTIL / IO_PIN_ORDER_CFG in the LibreLane config; the values are the designer's"),
    (re.compile(r"(?m)^.*\[ERROR PDN-\d+\].*$"), "pdn_config", "power distribution setting",
     "set PDN_* keys (or PDN_CFG) in the LibreLane config, starting from the PDK defaults"),
    (re.compile(r"(?m)^.*\[ERROR MPL-\d+\].*$"), "macro_placement", "macro placement",
     "give MACRO_PLACEMENT_CFG / MACROS placement in the LibreLane config"),
    (re.compile(r"(?im)^.*(?:unable to find image|pull access denied|manifest unknown).*$"), "image",
     "container image", "docker pull the image named in [flow]/[pnr] (a person approves the download)"),
    (re.compile(r"(?im)^.*\b(?:padring|pad ring|PAD_CORNER|PAD_SITE_NAME)\b.*(?:error|not|missing).*$"), "io_ring",
     "IO ring", "pads belong to chip-level integration, not to this block's flow"),
]
_KEY_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)\s*:\s*(.*)$")


def _paths_of(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [p for v in value for p in _paths_of(v)]
    if isinstance(value, dict):
        return [p for v in value.values() for p in _paths_of(v)]
    return []


def setup_issues(resolved: dict, log_text: str, rules: dict | None = None) -> list[dict]:
    """§0.11 physical-design rows this adapter can detect: PDK and macro views or signoff decks
    the config names but that are not on disk, signoff checks switched off, and setup errors in
    the logs. Each issue: stage, category, missing, evidence, suggestion, who."""
    issues = []
    for key, (cat, what) in _PATH_KEYS.items():
        for p in _paths_of(resolved.get(key)):
            if p.startswith("/") and not Path(p).exists():
                issues.append({"stage": "pnr", "category": cat, "missing": f"{what}: {p}",
                               "evidence": f"resolved.json {key}", "suggestion": _SUGGEST[cat], "who": "person"})
    for name, macro in (resolved.get("MACROS") or {}).items():
        views = macro if isinstance(macro, dict) else {}
        for view in ("gds", "lef"):
            if not _paths_of(views.get(view)):
                issues.append({"stage": "pnr", "category": "macro_views", "missing": f"macro {name}: no {view.upper()}",
                               "evidence": "resolved.json MACROS", "suggestion": _SUGGEST["macro_views"],
                               "who": "person"})
    for rule, flag in _RUN_FLAGS.items():
        if rules and rules.get(rule, {}).get("outcome") == "missing" and resolved.get(flag) is False:
            issues.append({"stage": "pnr", "category": "signoff_disabled", "missing": f"{rule}: {flag} is false",
                           "evidence": f"resolved.json {flag}", "suggestion": f"set {flag}: true in the LibreLane config",
                           "who": "person"})
    seen = set()
    for rx, cat, what, suggestion in LOG_RULES:
        for m in rx.finditer(log_text):
            line = m.group(0).strip()[:300]
            if (cat, line) in seen:
                continue
            seen.add((cat, line))
            issues.append({"stage": "pnr", "category": cat, "missing": what, "evidence": line,
                           "suggestion": suggestion, "who": "person"})
    return issues[:30]


def config_lessons(text: str) -> dict[str, dict]:
    """Comment block written directly above each top-level key of a LibreLane YAML config."""
    out, block = {}, []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            block.append(s.lstrip("#").strip())
            continue
        if m := _KEY_LINE.match(line):
            if block:
                out[m[1]] = {"comment": " ".join(block), "value": m[2].strip()}
        block = []
    return out


# Lineage: LibreLane hands a "state" (view -> file) from step to step; each step's state_in.json must
# equal the previous step's state_out.json. Steps are grouped into stages; for each stage the views it
# reads and the views it produced are recorded with sha256. Checker/report steps change no view.
_STAGE_OF = [  # (step-name regex, stage)
    (r"yosys-synthesis$", "synthesis"), (r"openroad-rcx$", "rc_extraction"),
    (r"openroad-stapostpnr$", "post_route_sta"), (r"(?:magic|klayout)-streamout$", "gds_streamout"),
    (r"(?:magic|klayout)-drc$", "drc"), (r"magic-spiceextraction$", "lvs_extraction"),
    (r"netgen-lvs$", "lvs"),
    (r"^(?:openroad|odb)-(?!sta|check|report|irdrop|rcx|cellfrequency)", "place_and_route"),
]
_READS = {"synthesis": [], "place_and_route": ["nl"], "rc_extraction": ["odb", "def"],
          "post_route_sta": ["pnl", "odb", "spef", "sdc"], "gds_streamout": ["def", "odb"],
          "drc": ["gds", "klayout_gds"], "lvs_extraction": ["gds"], "lvs": ["spice", "pnl"]}
_EXPECTED = ("synthesis", "place_and_route", "rc_extraction", "post_route_sta", "gds_streamout", "drc",
             "lvs_extraction", "lvs")
# What a full E5 chain would add after these; LibreLane makes none of them.
_NOT_PRESENT = ["gate_level_sim (routed netlist + post-route SDF)", "switching activity (VCD/SAIF) from that simulation",
                "power from that activity"]


def _views(state: dict) -> dict[str, str]:
    """view -> path, per-corner views flattened as view:corner."""
    out = {}
    for k, v in state.items():
        if k == "metrics" or v is None:
            continue
        if isinstance(v, dict):
            out.update({f"{k}:{c}": p for c, p in v.items() if isinstance(p, str)})
        elif isinstance(v, str):
            out[k] = v
    return out


def _stage(step_name: str) -> str | None:
    return next((st for rx, st in _STAGE_OF if re.search(rx, step_name)), None)


def lineage(run: Path) -> dict:
    from ..reports.librelane import steps as _steps
    run = Path(run)
    cache: dict[str, str | None] = {}

    def entry(path: str) -> dict:
        if path not in cache:
            p = Path(path)
            cache[path] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
        try:
            shown = str(Path(path).relative_to(run))
        except ValueError:
            shown = path
        return {"path": shown, "sha256": cache[path]}

    stages: list[dict] = []
    breaks: list[dict] = []
    prev_out: dict | None = None
    for _, name, d in _steps(run):
        try:
            sin = _views(json.loads((d / "state_in.json").read_text()))
            sout = _views(json.loads((d / "state_out.json").read_text()))
        except (OSError, ValueError):
            continue  # a step that did not finish has no state_out; the run stopped there
        if prev_out is not None and sin != prev_out:
            changed = sorted(k for k in set(sin) | set(prev_out) if sin.get(k) != prev_out.get(k))
            breaks.append({"what": f"step {d.name} did not start from the previous step's output ({', '.join(changed[:5])})",
                           "level": "fatal"})
        prev_out = sout
        st = _stage(name)
        if not st:
            continue
        produced = {k: v for k, v in sout.items() if sin.get(k) != v}
        if stages and stages[-1]["stage"] == st:
            stages[-1]["steps"][1] = d.name
            stages[-1]["_out"].update(produced)
            continue
        reads = {k: v for k, v in sin.items() if k.split(":")[0] in _READS.get(st, [])}
        stages.append({"stage": st, "steps": [d.name, d.name], "_in": reads, "_out": dict(produced)})
    producer: dict[str, str] = {}
    for s in stages:
        s["inputs"] = {k: {**entry(p), "from": producer.get(k, "outside the run" if s["stage"] == "synthesis" else "?")}
                       for k, p in s.pop("_in").items()}
        s["outputs"] = {k: entry(p) for k, p in s.pop("_out").items()}
        for k, e in s["inputs"].items():
            if e["sha256"] is None:
                breaks.append({"what": f"{s['stage']}: input {k} ({e['path']}) is missing", "level": "fatal"})
        for k, e in s["outputs"].items():
            if e["sha256"] is None:
                breaks.append({"what": f"{s['stage']}: output {k} ({e['path']}) is missing", "level": "fatal"})
            producer[k] = s["stage"]
        s["ok"] = all(e["sha256"] for e in [*s["inputs"].values(), *s["outputs"].values()])
    final = []
    if prev_out is not None and (run / "final").is_dir():
        for view, path in sorted(prev_out.items()):
            name, _, corner = view.partition(":")
            base = run / "final" / name
            cands = [p for p in (base / corner).glob("*") if p.is_file()] if corner else \
                [p for p in base.glob("*") if p.is_file()] if base.is_dir() else []
            src = entry(path)
            match = [p for p in cands if p.name == Path(path).name or len(cands) == 1]
            if match:
                fe = entry(str(match[0]))
                ok = fe["sha256"] == src["sha256"]
                final.append({"view": view, "final": fe["path"], "from": src["path"], "sha256": fe["sha256"], "matches": ok})
                if not ok:
                    breaks.append({"what": f"final/{name} differs from the step output it should copy ({src['path']})",
                                   "level": "fatal"})
    got = [s["stage"] for s in stages]
    absent = [st for st in _EXPECTED if st not in got]
    return {"run": run.name, "backend": "librelane", "available": True, "stages": stages, "final": final,
            "breaks": breaks, "stages_absent": absent, "not_present": list(_NOT_PRESENT),
            "complete": not breaks and not absent}


class LibreLane(Backend):
    name = "librelane"
    run_mode = "local_container"
    synthesis_keys = re.compile(r"^(?:SYNTH_|VERILOG_|USE_SLANG$|CLOCK_PERIOD$|LINTER_)")
    not_knobs = re.compile(r"^(?:meta|DESIGN_DIR|PDK_ROOT)$")
    never_override = NEVER_OVERRIDE["librelane"]

    def is_run(self, path: Path) -> bool:
        return (Path(path) / "resolved.json").is_file()

    def read_run(self, path: Path, violators: int = 10) -> dict:
        return _read_run(Path(path), violators=violators)

    def config(self, path: Path) -> dict:
        try:
            data = json.loads((Path(path) / "resolved.json").read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def log_text(self, path: Path) -> str:
        return "\n".join((Path(path) / n).read_text(errors="replace") for n in ("flow.log", "error.log")
                         if (Path(path) / n).is_file())

    def setup_issues(self, config: dict, log_text: str, rules: dict | None = None) -> list[dict]:
        return setup_issues(config, log_text, rules)

    def lineage(self, path: Path) -> dict:
        return lineage(Path(path))

    def lessons(self, config_file: Path) -> dict[str, dict]:
        if not config_file.is_file() or config_file.suffix not in (".yaml", ".yml"):
            return {}
        return config_lessons(config_file.read_text(errors="replace"))

    def run_command(self, project, work: Path, tag: str, overrides: dict | None = None):
        """(docker command or the reason it may not run, run directory) for LibreLane on the copy in work/src."""
        from ..flow import _root_path, runs_dir
        pnr, flow = project.pnr, project.flow
        image = pnr.image or (flow.image if flow else "")
        pdk = pnr.pdk or (flow.pdk if flow else "")
        if not image or not pdk:
            return "[pnr] needs image and pdk (or a [flow] section that has them)", work
        src = work / "src"
        config = src / Path(pnr.config)
        pdk_root = _root_path(project, pnr.pdk_root)
        over = []
        for k, v in (overrides or {}).items():
            if k in self.never_override or k not in pnr.allowed_overrides:
                return f"config key {k!r} may not be overridden (allowed: {list(pnr.allowed_overrides) or 'none'})", work
            over += ["-c", f"{k}={v}"]
        inner = ["librelane", "--manual-pdk", "--pdk-root", str(pdk_root), "--pdk", pdk,
                 "--run-tag", tag, *over, str(config)]
        mounts = [(pdk_root, False)]
        if pdk_root.exists() and pdk_root.resolve() != pdk_root:
            mounts.append((pdk_root.resolve(), False))
        mounts.append((work.resolve(), True))
        cmd = docker_command(image, inner, mounts, project.allowed_images, env={"HOME": str(work.resolve())},
                             workdir=str(config.parent))
        rd = runs_dir(project)
        rel_runs = rd.relative_to(project.root) if rd.is_relative_to(project.root) else None
        run_dir = (src / rel_runs if rel_runs else config.parent / "runs") / tag
        return cmd, run_dir
