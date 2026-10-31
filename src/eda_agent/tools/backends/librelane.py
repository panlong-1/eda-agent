"""LibreLane adapter: the first and default back end ([pnr] backend = "librelane").

Runs: `runs/<tag>/` with resolved.json, numbered step directories and final/; read by
tools/reports/librelane.py into the neutral result. Config: resolved.json (LibreLane's own key
names). Runs locally in the LibreLane container image without network.
"""
from __future__ import annotations

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
