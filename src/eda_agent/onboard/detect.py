"""Guess a project config from what a design repository already contains.

Every guess carries where it came from, so the draft can say so line by line. Nothing
here runs the design's tools except FuseSoC's `--setup` (it writes the Verilator command
file and builds nothing); the draft must still be read and confirmed by a person, because
the config decides what each check means.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS = {".git", "build", "runs", "node_modules", ".venv", "venv", "__pycache__"}
MAKE_TEST_TARGETS = ("regress", "regression", "test", "tests", "check", "sim", "test_verilator")
# What testbenches print at the end; the first one found in tb/ or test sources becomes the pattern.
PASS_MARKERS = ("ALL TESTS PASSED", "TEST PASSED", "TESTS PASSED", "SIMULATION PASSED")
FAIL_MARKERS = ("TEST FAILED", "TESTS FAILED", "SIMULATION FAILED")


@dataclass
class Guess:
    value: object
    source: str           # where it came from, written next to it in the draft
    confirm: bool = False  # a person must check this one in particular


@dataclass
class Detection:
    root: Path
    name: Guess
    lint: dict[str, Guess] = field(default_factory=dict)
    regress: dict[str, Guess] = field(default_factory=dict)
    flow: dict[str, Guess] = field(default_factory=dict)
    versions: dict[str, Guess] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _files(root: Path, pattern: str, depth: int = 3) -> list[Path]:
    out = []
    for p in sorted(root.rglob(pattern)):
        rel = p.relative_to(root)
        if len(rel.parts) <= depth and not SKIP_DIRS & set(rel.parts[:-1]):
            out.append(p)
    return out


def make_targets(makefile: Path) -> list[str]:
    targets = []
    for line in makefile.read_text(errors="replace").splitlines():
        m = re.match(r"^([A-Za-z0-9_.-]+)\s*:(?!=)", line)
        if m and not m.group(1).startswith(".") and m.group(1) not in targets:
            targets.append(m.group(1))
    return targets


def make_variable(makefile: Path, name: str) -> str | None:
    m = re.search(rf"^{name}\s*[:?]?=\s*(\S+)", makefile.read_text(errors="replace"), re.M)
    return m.group(1) if m else None


def _markers(root: Path, markers: tuple[str, ...]) -> tuple[str, Path] | None:
    for d in ("tb", "test", "tests", "sim", "verif", "testbench", "."):
        base = root / d
        if not base.is_dir():
            continue
        for src in _files(base, "*", depth=1 if d == "." else 3):
            if src.suffix not in (".sv", ".v", ".c", ".py", ".cpp"):
                continue
            text = src.read_text(errors="replace")
            for m in markers:
                if m in text:
                    return m, src
    return None


def read_fusesoc_core(path: Path) -> dict:
    import yaml
    text = path.read_text(errors="replace")
    return yaml.safe_load(text) or {}


def fusesoc_lint_setup(root: Path, core_name: str, fusesoc: str) -> tuple[Path | None, str]:
    """Let FuseSoC write the lint target's Verilator command file; (its path, the command run)."""
    cmd = [fusesoc, "--cores-root", ".", "run", "--setup", "--target", "lint", core_name]
    shown = " ".join(["fusesoc", *cmd[1:]])
    try:
        subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=300, check=True)
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"{shown} failed: {e}"
    vcs = sorted((root / "build").glob("*/lint-verilator/*.vc"), key=lambda p: p.stat().st_mtime)
    return (vcs[-1] if vcs else None), shown


def detect(root: Path, run_fusesoc: bool = True) -> Detection:
    root = root.resolve()
    det = Detection(root=root, name=Guess(root.name, "directory name"))
    makefile = root / "Makefile"
    targets = make_targets(makefile) if makefile.is_file() else []

    # --- LibreLane: top, files, clock, SDC in one place --------------------------------
    for cfg in _files(root, "config.yaml") + _files(root, "config.json"):
        text = cfg.read_text(errors="replace")
        if "DESIGN_NAME" in text and "VERILOG_FILES" in text:
            rel = str(cfg.relative_to(root))
            det.flow["librelane"] = Guess(rel, f"LibreLane config {rel}")
            from ..core.flow_sources import read_librelane
            lib = read_librelane(cfg, root)
            if "top" in lib:
                det.lint.setdefault("top", Guess(lib["top"], f"{rel} DESIGN_NAME"))
            if "files" in lib:
                det.lint.setdefault("files", Guess(lib["files"], f"{rel} VERILOG_FILES"))
            if "include_dirs" in lib:
                det.lint.setdefault("include_dirs", Guess(lib["include_dirs"], f"{rel} VERILOG_INCLUDE_DIRS"))
            break

    # --- FuseSoC: lint top, and its own Verilator command file -------------------------
    cores = _files(root, "*.core", depth=1)
    if cores:
        core = cores[0]
        data = read_fusesoc_core(core)
        core_name = str(data.get("name", "")).rsplit(":", 1)[0] if data.get("name") else ""
        lint_t = (data.get("targets") or {}).get("lint") or {}
        if lint_t.get("toplevel"):
            det.lint["top"] = Guess(lint_t["toplevel"], f"{core.name} targets.lint.toplevel")
        opts = (((lint_t.get("tools") or {}).get("verilator") or {}).get("verilator_options"))
        if opts:
            det.lint["flags"] = Guess(list(opts), f"{core.name} targets.lint verilator_options")
        fusesoc = shutil.which("fusesoc")
        if core_name and lint_t and run_fusesoc and fusesoc:
            vc, shown = fusesoc_lint_setup(root, core_name, fusesoc)
            if vc:
                det.lint["command_files"] = Guess([str(vc.relative_to(root))],
                                                  f"written by `{shown}`; rerun it if the core changes",
                                                  confirm=True)
                det.lint["external"] = Guess(["build", "vendor"],
                                             "FuseSoC copies dependencies under build/; vendored IP in vendor/")
                # FuseSoC also copies this core's own files to build/<core>/src/<core>/
                own = vc.parent.parent / "src" / vc.parent.parent.name
                if own.is_dir():
                    det.lint["copy_of_root"] = Guess([str(own.relative_to(root))],
                                                     "FuseSoC's copy of this core's own files; mapped back")
                det.lint.pop("files", None)
            else:
                det.notes.append(f"FuseSoC setup did not produce a lint command file ({shown})")
        elif core_name and lint_t:
            det.notes.append(f"{core.name} has a lint target, but fusesoc is not on PATH: activate the "
                             "project's Python environment and rerun init, or list the RTL files by hand")

    # --- plain filelists ---------------------------------------------------------------
    if "command_files" not in det.lint and "files" not in det.lint:
        fls = [str(p.relative_to(root)) for p in _files(root, "*.f", depth=2)]
        if fls:
            det.lint["filelists"] = Guess(fls, "*.f files in the repository", confirm=True)
    if "top" not in det.lint:
        det.lint["top"] = Guess("TODO_top_module", "not found: write the top module name", confirm=True)

    # --- regression -------------------------------------------------------------------
    scripts = [p for p in _files(root, "*regress*", depth=2) if p.is_file() and p.suffix in ("", ".sh", ".py")]
    target = next((t for t in MAKE_TEST_TARGETS if t in targets), None)
    if target:
        det.regress["cmd"] = Guess(["make", target], f"Makefile target `{target}`", confirm=True)
    elif scripts:
        rel = str(scripts[0].relative_to(root))
        det.regress["cmd"] = Guess([rel], f"script {rel}", confirm=True)
    else:
        det.regress["cmd"] = Guess(["TODO"], "no test target or regress script found", confirm=True)
    if found := _markers(root, PASS_MARKERS):
        det.regress["pass_regex"] = Guess(found[0], f"printed by {found[1].relative_to(root)}", confirm=True)
    if found := _markers(root, FAIL_MARKERS):
        det.regress["fail_regex"] = Guess(found[0], f"printed by {found[1].relative_to(root)}")
    lists = _files(root, "*.list", depth=3)
    if lists:
        det.notes.append(f"case list(s) found: {', '.join(str(p.relative_to(root)) for p in lists)}. "
                         "If the regression runs cases one by one, set case_list, case_flag and format")

    # --- SDC and clock when there is no LibreLane config ---------------------------------
    sdcs = [str(p.relative_to(root)) for p in _files(root, "*.sdc", depth=4)]
    if sdcs and "librelane" not in det.flow:
        period = make_variable(makefile, "CLK_PERIOD") if makefile.is_file() else None
        det.notes.append(f"SDC found ({', '.join(sdcs)}) but no LibreLane config"
                         + (f"; the Makefile sets CLK_PERIOD = {period}" if period else "")
                         + ". To run synth, add a [flow] section by hand (see projects/torv.toml)")

    # --- versions ---------------------------------------------------------------------
    det.versions["verilator"] = Guess(["verilator", "--version"], "every project lints with Verilator")
    text = makefile.read_text(errors="replace") if makefile.is_file() else ""
    if m := re.search(r"(riscv(?:32|64)-[\w-]*?-)gcc|\b(riscv(?:32|64)-[\w-]+-)\b", text):
        prefix = m.group(1) or m.group(2)
        det.versions["gcc"] = Guess([f"{prefix}gcc", "--version"], "RISC-V prefix in the Makefile")
    if cores:
        det.versions["fusesoc"] = Guess(["fusesoc", "--version"], f"{cores[0].name}")
    return det
