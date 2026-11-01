"""Setup diagnosis (plan §0.11): when a project's own script cannot run, say what is missing and how
to fix it, instead of reporting a test failure.

Two sources, both plain rules (no model), so the same input always gives the same answer:

- a **rule table** on tool output (LOG_RULES), per stage: sim, synth, pnr;
- **file checks** on the checkout (`file_issues`): FuseSoC filesets and dependencies, vendored IP
  descriptors, git submodules, and ${VAR} paths of the config (PDK_ROOT, ...).

Each issue is a dict: stage, category, missing, evidence (the log line or the file fact), suggestion,
who ("setup_step": an approved `[setup]` step the agent may run; "person": a person must act), step
(the command of the fix, when there is one) and rule (the rule id). A setup issue is not a test
failure: results that carry blocking ones are `error_class: setup`, Fatal "environment not ready" in
the receipt. The LibreLane adapter keeps its own physical-design rows (tools/backends/librelane.py)
in the same shape.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

STAGES = ("sim", "synth", "pnr")
WHO_STEP = "setup_step"
WHO_PERSON = "person"
# Makefile target names that usually prepare a checkout rather than test it (init proposes them).
SETUP_TARGET_NAMES = ("vendor", "regs", "gen", "generate", "mcu-gen", "mcu_gen", "firmware", "hex",
                      "vectors", "deps", "submodules")
_CLEAN = re.compile(r"(?i)clean|help")
_HDL = r"(?:sv|svh|v|vh|vhd|vhdl|f|core|hex|mem|bin|elf|img|exp|dat|lib|sdc|lef|gds)"


@dataclass(frozen=True)
class LogRule:
    id: str
    stages: tuple[str, ...]
    pattern: re.Pattern
    category: str
    missing: str           # what is missing; {item} is the captured name
    suggestion: str
    blocking: bool = True  # False: worth knowing, the stage still ran (a Warning, not "not ready")


def _rx(text: str) -> re.Pattern:
    return re.compile(text, re.M)


# The §0.11 rows that output alone can show. `item` (a named group) is the missing name.
LOG_RULES: tuple[LogRule, ...] = (
    # --- simulation (also lint and synth front ends, which read the same files) ----------------
    LogRule("sim.include", ("sim", "synth"),
            _rx(r"^.*Cannot find include file:?\s*['\"`]?(?P<item>[^'\"`\s]+).*$"),
            "missing_file", "include file {item}",
            "find which file includes it; check +incdir+ / include_dirs in the filelist or config"),
    LogRule("sim.file", ("sim", "synth"),
            _rx(rf"^.*(?:No such file or directory|Cannot find(?: file)?|[Cc]an'?not open (?:input )?file|Unable to open "
                rf"(?:input )?file|could not open)\b.*?['\"`]?(?P<item>[\w./${{}}+-]+\.{_HDL})\b.*$"),
            "missing_file", "file {item}",
            "check the filelist / file paths in the config; if a step generates it, add that step to [setup]"),
    LogRule("sim.file_before", ("sim", "synth"),
            _rx(rf"^.*?['\"`]?(?P<item>[\w./${{}}+-]+\.{_HDL})['\"`]?:?\s*(?:No such file or directory|not found)\b.*$"),
            "missing_file", "file {item}",
            "check the filelist / file paths in the config; if a step generates it, add that step to [setup]"),
    LogRule("sim.make_rule", ("sim", "synth", "pnr"),
            _rx(r"^.*No rule to make target ['`](?P<item>[^'`]+)'.*$"),
            "missing_file", "file {item} (make has no rule to build it)",
            "the file is neither in the checkout nor built by make: run the step that generates it, or fix the path"),
    LogRule("sim.module", ("sim",),
            _rx(r"^.*(?:Cannot find file containing module|Unknown module type|unknown module|"
                r"Cannot find module)[:\s]*['\"`]?(?P<item>[\w$]+).*$"),
            "unknown_module", "module {item}",
            "add the file that defines it to the filelist, or the step that generates it to [setup]"),
    LogRule("sim.package", ("sim", "synth"),
            _rx(r"^.*(?:Package/class '?(?P<item>\w+)'? not found|Import package not found:?\s*'?(?P<item2>\w+)|"
                r"unknown (?:class or )?package '?(?P<item3>\w+))'?.*$"),
            "unknown_package", "package {item}",
            "put the package file first in the file order, or add it (or the step that generates it)"),
    LogRule("sim.readmem", ("sim",),
            _rx(r"^.*\$readmem[hb]?\b.*?(?:[Ff]ile not found|[Uu]nable to open|cannot open|No such file)"
                r"[:\s]*['\"`]?(?P<item>[^'\"`\s]*).*$"),
            "memory_init", "memory initialisation file {item}",
            "say which step writes it (firmware build, vectors, hex) and add that step to [setup]"),
    LogRule("sim.fusesoc_deps", ("sim", "synth"),
            _rx(r"^.*Requirements: .*<- '(?P<item>[\w.]+)\b.*$"),
            "vendor_ip", "FuseSoC dependency {item}",
            "fetch the third-party IP (e.g. `make vendor` or `git submodule update --init`): a download, a person approves it"),
    LogRule("sim.fusesoc_core", ("sim", "synth"),
            _rx(r"^.*(?:Failed to resolve dependencies for\s+(?P<item>[\w.:-]+)|Could not find core\s+'?"
                r"(?P<item2>[\w.:-]+)|[Cc]ore '(?P<item3>[\w.:-]+)' not found).*$"),
            "vendor_ip", "FuseSoC core or one of its dependencies: {item}",
            "fetch the third-party IP the core depends on (vendor step or submodules): a person approves the download"),
    LogRule("sim.command", ("sim", "synth", "pnr"),
            _rx(r"^.*?(?:(?P<item>[\w.+-]+): command not found|(?:/bin/sh|sh|bash): \d*:? ?(?P<item2>[\w.+-]+): not found|"
                r"make(?:\[\d+\])?: (?P<item3>[\w.+-]+): No such file or directory).*$"),
            "tool", "program {item}",
            "install it, or put it on PATH through the project's env_script (activate the right environment)"),
    LogRule("sim.python_module", ("sim", "synth", "pnr"),
            _rx(r"^.*ModuleNotFoundError: No module named '(?P<item>[\w.]+)'.*$"),
            "tool", "Python package {item}",
            "activate the project's Python environment in env_script, or install the package there (a person decides)"),
    LogRule("sim.timing", ("sim",),
            _rx(r"^.*(?:NEEDTIMINGOPT|(?:[Uu]se|requires?|needs?) --timing\b).*$"),
            "tool_version", "simulator timing support (--timing)",
            "add --timing to the simulator flags, or use a simulator version that supports timing controls"),
    LogRule("sim.version", ("sim", "synth"),
            _rx(r"^.*(?:requires?|needs?) (?P<item>(?:verilator|iverilog|yosys|python)[ -]?(?:version )?[\d.]+\S*).*$"),
            "tool_version", "tool version {item}",
            "record the installed version (doctor) and install the version the project needs"),
    LogRule("sim.env", ("sim", "synth", "pnr"),
            _rx(r"^.*?(?:\$\{?(?P<item>[A-Z][A-Z0-9_]*)\}? (?:is )?(?:not set|unset|undefined)|"
                r"(?:environment variable|env var)\s+['\"]?(?P<item2>[A-Z][A-Z0-9_]*)['\"]? (?:is )?(?:not set|unset|undefined)|"
                r"(?P<item3>PDK_ROOT|RISCV|VERILATOR_ROOT|RISCV_TOOLCHAIN|TOOLCHAIN_PREFIX) (?:is )?(?:not set|unset|undefined|empty)).*$"),
            "env", "environment variable {item}",
            "set it in [env_defaults] or in the env_script"),
    # --- synthesis --------------------------------------------------------------------------------
    LogRule("synth.liberty", ("synth",),
            _rx(r"^.*(?:[Cc]an'?t open liberty file|[Cc]an'?t open input file|cannot read file|"
                r"No such file)[^\n]*?['\"`]?(?P<item>[^'\"`\s]+\.lib(?:\.gz)?)\b.*$"),
            "pdk", "timing library {item}",
            "install or enable the PDK (ciel) and set PDK_ROOT; check the corner names in [flow.corners]"),
    LogRule("synth.blackbox", ("synth",),
            _rx(r"^.*Module `\\?(?P<item>[\w$]+)' referenced in module `\\?[\w$]+'.*is not part of the design.*$"),
            "unknown_module", "module {item}",
            "a missing RTL file, or a library cell that should come from the Liberty file (read_liberty -lib)"),
    LogRule("synth.no_clock", ("synth",),
            _rx(r"^.*(?:NO_CLOCK|the SDC defines no clock on port|[Nn]o clocks? (?:defined|found)).*$"),
            "constraints", "a clock in the SDC",
            "a person writes or fixes the SDC (the agent never writes constraints, plan §0.8)"),
    LogRule("synth.sim_only", ("synth",),
            _rx(r"^.*?(?P<item>[\w./-]+\.s?vh?:\d+).*(?:initial|\$display|\$finish|delay|timing control).*"
                r"(?:not supported|unsupported|not synthesi[sz]able|ignor).*$"),
            "sim_only_code", "simulation-only code at {item}",
            "keep simulation-only code out of the synthesis files (a fileset, or `ifndef SYNTHESIS)"),
    LogRule("synth.memory_flops", ("synth",),
            _rx(r"^.*(?:Replacing memory|[Mm]apping memory) \\?(?P<item>[\w.$\\]+) (?:with list of registers|to flip-?flops).*$"),
            "memory_macro", "memory macro for {item}",
            "a behavioural memory became flip-flops: use the PDK's SRAM macro (LIB/LEF), or confirm it is intended",
            blocking=False),
    LogRule("synth.image", ("synth", "pnr"),
            _rx(r"^.*(?:Unable to find image '?(?P<item>[^'\s]+)|pull access denied for (?P<item2>[^\s,]+)|"
                r"No such image:? (?P<item3>\S+)|manifest unknown).*$"),
            "image", "container image {item}",
            "docker pull the image named in [flow]/[pnr] (a download, a person approves it)"),
    LogRule("synth.docker", ("synth", "pnr"),
            _rx(r"^.*(?:Cannot connect to the Docker daemon|docker: command not found|permission denied while trying to "
                r"connect to the Docker daemon).*$"),
            "tool", "a running docker daemon the user may use",
            "start docker (Docker Desktop's WSL integration) or add the user to the docker group (a person does it)"),
)

# Physical-design rows live with their adapter (tools/backends/librelane.py: LOG_RULES and the
# resolved.json view checks). Rows of §0.11 no rule can detect yet, with the reason:
NOT_DETECTABLE = {
    ("sim", "behavioural SRAM/IP model missing, when the error does not name the module"):
        "only the module name tells a memory model from other RTL",
    ("synth", "clock gate / technology cell not in the library"):
        "detected only as an unknown module whose name looks like a clock gate (tech_cell)",
    ("pnr", "missing DIE_AREA / FP_CORE_UTIL / pin order / macro placement values"):
        "LibreLane falls back to defaults; detected only through the IFP/PPL/MPL errors they cause",
    ("pnr", "missing post-layout corner data"): "detected only when a corner file named in resolved.json is absent",
    ("any", "a file that is present but stale or of the wrong version (e.g. a PDK of another release)"):
        "file checks see presence only",
}


@dataclass
class Context:
    """What turns a missing item into a concrete fix: the root, the approved [setup] steps and the
    Makefile targets that could produce files."""
    root: Path | None = None
    steps: tuple[tuple[str, ...], ...] = ()
    targets: dict[str, "MakeTarget"] = field(default_factory=dict)
    lib_prefixes: tuple[str, ...] = ()  # standard-cell name prefixes (e.g. sg13g2_) of the [flow] library


def issue(stage: str, category: str, missing: str, evidence: str, suggestion: str, rule: str,
          step: list[str] | None = None, ctx: Context | None = None, blocking: bool = True) -> dict:
    """One setup issue; `who` is setup_step when `step` is an approved [setup] step."""
    approved = bool(step) and ctx is not None and tuple(step) in ctx.steps
    if step and not approved:
        suggestion = (f"{suggestion} — run `{' '.join(step)}`: add {list(step)} to [setup] steps "
                      "once a person approves it")
    elif step:
        suggestion = f"{suggestion} — approved [setup] step `{' '.join(step)}`"
    return {"stage": stage, "category": category, "missing": missing, "evidence": evidence[:300],
            "suggestion": suggestion, "who": WHO_STEP if approved else WHO_PERSON,
            "step": list(step) if step else None, "rule": rule, "blocking": blocking}


# --- Makefiles: which target produces a file ------------------------------------------------------

@dataclass
class MakeTarget:
    name: str
    prereqs: list[str]
    recipe: list[str]  # expanded lines


_TARGET = re.compile(r"^([A-Za-z0-9_./${}()-]+(?:\s+[A-Za-z0-9_./${}()-]+)*)\s*:(?![=:])\s*([^;=]*)$")
_ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(?:\?=|:=|::=|=)\s*(.*?)\s*$")
_REF = re.compile(r"\$[({]([A-Za-z_][A-Za-z0-9_]*)[)}]")


def _expand(text: str, variables: dict[str, str]) -> str:
    for _ in range(5):
        new = _REF.sub(lambda m: variables.get(m.group(1), m.group(0)), text)
        if new == text:
            break
        text = new
    return text


def read_makefile(path: Path) -> dict[str, MakeTarget]:
    """Targets with their prerequisites and recipe lines, simple variables expanded (`?=`, `:=`, `=`)."""
    variables: dict[str, str] = {}
    targets: dict[str, MakeTarget] = {}
    current: list[MakeTarget] = []
    lines = path.read_text(errors="replace").replace("\\\n", " ").splitlines()
    for line in lines:
        if line.startswith("\t"):
            for t in current:
                t.recipe.append(_expand(line.strip(), variables))
            continue
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if (m := _ASSIGN.match(line)) and not _TARGET.match(line.split("=")[0] + ":"):
            variables.setdefault(m.group(1), m.group(2).split("#")[0].strip())
            current = []
            continue
        if m := _TARGET.match(line):
            names = _expand(m.group(1), variables).split()
            prereqs = _expand(m.group(2).split("#")[0], variables).split()
            current = []
            for n in names:
                if n.startswith(".") and "/" not in n:
                    continue  # .PHONY and other special targets
                t = targets.setdefault(n, MakeTarget(n, [], []))
                t.prereqs += [p for p in prereqs if p not in t.prereqs]
                current.append(t)
            continue
        current = []
    return targets


def _braces(token: str) -> list[str]:
    m = re.search(r"\{([^{}]*,[^{}]*)\}", token)
    if not m:
        return [token]
    return [x for part in m.group(1).split(",") for x in _braces(token[:m.start()] + part + token[m.end():])]


def _tokens(text: str) -> list[str]:
    out = []
    for tok in re.split(r"[\s\"'=;|<>()]+", text):
        tok = tok.strip(",:")
        if tok:
            out += _braces(tok)
    return out


def producer(rel: str, targets: dict[str, MakeTarget]) -> tuple[str, str] | None:
    """(target, why) of the Makefile target that most likely writes `rel`, or None.

    Ranked: a target named like the file; a recipe naming the file; a recipe naming its file name;
    a recipe writing into its directory (`-o dir`, `--out dir`, `-O dir`)."""
    rel = rel.lstrip("./")
    name = Path(rel).name
    parent = str(Path(rel).parent)
    best: tuple[int, str, str] | None = None
    for t in targets.values():
        if _CLEAN.search(t.name):
            continue
        if t.name in (rel, name):
            return t.name, f"Makefile target `{t.name}` is the file itself"
        for line in t.recipe:
            toks = [x.lstrip("./") for x in _tokens(line)]
            words = line.split()
            outs = {w.strip("'\"").lstrip("./").rstrip("/") for flag, w in zip(words, words[1:])
                    if flag in ("-o", "-O", "--out", "--output", "--outdir", "--out-dir")}
            rank = (3 if rel in toks else 2 if any(Path(x).name == name for x in toks)
                    else 1 if parent not in ("", ".") and parent in outs else 0)
            if rank and (best is None or rank > best[0]):
                best = (rank, t.name, f"Makefile target `{t.name}` recipe: {line[:160]}")
    if best:
        return best[1], best[2]
    # a named guess: register files from a regs target, vectors from a vectors target
    for pat, tname in ((r"_reg(?:s|_pkg|_top)?\b|regs?\.h$", "regs"), (r"\.(?:hex|elf|bin|mem)$", "firmware"),
                       (r"\.(?:hex|elf|bin|mem)$", "hex"), (r"vector|\.(?:img|exp)$", "vectors")):
        if re.search(pat, rel, re.I) and tname in targets:
            return tname, f"guess from the file name: Makefile has a `{tname}` target"
    return None


def make_context(root: Path, steps=(), lib_prefixes: tuple[str, ...] = ()) -> Context:
    mk = root / "Makefile"
    targets = read_makefile(mk) if mk.is_file() else {}
    return Context(root=root, steps=tuple(tuple(s) for s in steps), targets=targets, lib_prefixes=lib_prefixes)


def _step_for(target: str, ctx: Context) -> list[str]:
    """The approved step that runs this make target, else plain `make <target>`."""
    for s in ctx.steps:
        if s and Path(s[0]).name == "make" and target in s[1:]:
            return list(s)
    return ["make", target]


def _vendor_step(ctx: Context) -> list[str] | None:
    for name in ("vendor", "deps", "submodules"):
        if name in ctx.targets:
            return _step_for(name, ctx)
    return None


# --- the rule table on output ---------------------------------------------------------------------

def _item(m: re.Match) -> str:
    groups = m.groupdict()
    for k in ("item", "item2", "item3"):
        if groups.get(k):
            return groups[k]
    return ""


def _fusesoc_name(raw: str) -> str:
    """'pulp__platform.org__common_cells' (FuseSoC's escaped VLNV) -> 'pulp-platform.org::common_cells'."""
    if "__" not in raw:
        return raw
    vendor, _, name = raw.rpartition("__")
    return f"{vendor.replace('__', '-')}::{name}"


def _refine(stage: str, rule: LogRule, item: str, line: str, ctx: Context | None) -> dict:
    """The rule's issue, made concrete with the checkout: the target that writes a missing file, the
    vendor step for third-party IP, library cells and memory models among unknown modules."""
    cat, missing, suggestion, step = rule.category, rule.missing.format(item=item or "?"), rule.suggestion, None
    ctx = ctx or Context()
    if item.startswith("/") and ctx.root and Path(item).is_relative_to(ctx.root):
        item = str(Path(item).relative_to(ctx.root))  # a path in the checkout, as the Makefile names it
    if cat in ("missing_file", "memory_init") and item and ctx.targets:
        if found := producer(item, ctx.targets):
            if cat == "missing_file":
                cat, missing = "generated", f"generated file {item}"
            suggestion = f"generated by a step that has not run ({found[1]})"
            step = _step_for(found[0], ctx)
        elif re.search(r"(?:^|/)(?:vendor|third_party|ext|deps|submodules?)/", item):
            cat, step = "vendor_ip", _vendor_step(ctx)
            suggestion = "third-party IP is not fetched (a download: a person approves it)"
    elif cat in ("unknown_module", "unknown_package") and item:
        name = item.lower()
        if any(name.startswith(p.lower()) for p in ctx.lib_prefixes if p):
            cat, suggestion = "library_cell", ("a standard cell of the library: it must come from the Liberty file "
                                               "(read_liberty -lib), not from RTL; check [flow] liberty")
        elif re.search(r"clk_?gat|clock_?gat|\bicg|tc_clk", name):
            cat, suggestion = "tech_cell", ("a technology cell (clock gate, ...): use the technology-specific "
                                            "implementation file for this stage (a tech fileset)")
        elif re.search(r"sram|_ram\b|^ram|tc_sram|_mem\b|^rm_", name):
            cat, suggestion = "memory_model", ("a memory model or macro: simulation needs the behavioural model "
                                               "(a generic mem fileset), synthesis the PDK macro")
        elif ctx.targets and (found := producer(f"{item}.sv", ctx.targets)):
            cat, suggestion, step = "generated", f"generated by a step that has not run ({found[1]})", \
                _step_for(found[0], ctx)
    elif cat == "vendor_ip":
        missing = rule.missing.format(item=_fusesoc_name(item))
        step = _vendor_step(ctx)
    return issue(stage, cat, missing, line.strip(), suggestion, rule.id, step, ctx, rule.blocking)


def classify(text: str, stage: str, ctx: Context | None = None, limit: int = 20) -> list[dict]:
    """Setup issues the rule table finds in a tool's output, one per (category, missing)."""
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for rule in LOG_RULES:
        if stage not in rule.stages:
            continue
        for m in rule.pattern.finditer(text or ""):
            got = _refine(stage, rule, _item(m), m.group(0), ctx)
            key = (got["category"], got["missing"])
            if key in seen:
                continue
            seen.add(key)
            out.append(got)
            if len(out) >= limit:
                return out
    return out


def blocking(issues: list[dict]) -> list[dict]:
    return [i for i in issues if i.get("blocking", True)]


# --- file checks on the checkout ------------------------------------------------------------------

_SKIP = {".git", "build", "runs", "node_modules", ".venv", "venv", "__pycache__"}


def _walk(root: Path, pattern: str, depth: int) -> list[Path]:
    out = []
    for p in sorted(root.rglob(pattern)):
        rel = p.relative_to(root)
        if len(rel.parts) <= depth and not _SKIP & set(rel.parts[:-1]):
            out.append(p)
    return out


def _vlnv(text: str) -> tuple[str, str]:
    """(vendor, name) of a FuseSoC VLNV or dependency string, version and operators dropped."""
    s = re.sub(r"^[<>=~^!]+", "", text.strip())
    parts = s.split(":")
    if len(parts) >= 3:
        return parts[0], parts[2]
    return (parts[0], "") if len(parts) == 1 else (parts[0], parts[1])


def _empty_dir(p: Path) -> bool:
    return not p.is_dir() or not any(p.iterdir())


def vendor_issues(root: Path, stage: str, ctx: Context) -> list[dict]:
    """Vendored IP that is described but not fetched: OpenTitan-style *.vendor.hjson whose target_dir is
    missing or empty, and git submodules that are not checked out."""
    out = []
    for desc in _walk(root, "*.vendor.hjson", 4):
        m = re.search(r"target_dir\s*:\s*['\"]?([^'\",\s}]+)", desc.read_text(errors="replace"))
        if not m:
            continue
        target = desc.parent / m.group(1)
        if _empty_dir(target):
            rel = target.relative_to(root)
            out.append(issue(stage, "vendor_ip", f"vendored IP {rel}/",
                             f"{desc.relative_to(root)} names target_dir {m.group(1)}; {rel}/ is "
                             f"{'empty' if target.is_dir() else 'not there'}",
                             "fetch the third-party IP (a download: a person approves it)", "file.vendor",
                             _vendor_step(ctx), ctx))
    gm = root / ".gitmodules"
    if gm.is_file():
        for path in re.findall(r"^\s*path\s*=\s*(\S+)", gm.read_text(errors="replace"), re.M):
            if _empty_dir(root / path):
                out.append(issue(stage, "vendor_ip", f"git submodule {path}/", f".gitmodules lists {path}; it is empty",
                                 "check out the submodules (a download: a person approves it)", "file.submodule",
                                 ["git", "submodule", "update", "--init", "--recursive"], ctx))
    return out


def fusesoc_issues(root: Path, stage: str, ctx: Context) -> list[dict]:
    """Files a FuseSoC core's filesets list for this stage's targets that are not on disk, and
    dependencies no .core under the root provides."""
    try:
        import yaml
    except ImportError:  # pragma: no cover - pyyaml is a dependency
        return []
    cores = _walk(root, "*.core", 1)
    if not cores:
        return []
    provided = set()
    for c in _walk(root, "*.core", 8):
        m = re.search(r"^name\s*:\s*['\"]?([^'\"\s]+)", c.read_text(errors="replace"), re.M)
        if m:
            provided.add(_vlnv(m.group(1)))
    out = []
    for core in cores:
        try:
            data = yaml.safe_load(core.read_text(errors="replace")) or {}
        except yaml.YAMLError:
            continue
        filesets = data.get("filesets") or {}
        targets = data.get("targets") or {}
        wanted = {"sim": ("sim", "lint", "default"), "synth": ("synth", "default"), "pnr": ("synth", "default")}[stage]
        used = [fs for t, spec in targets.items() if t.startswith(wanted) for fs in (spec or {}).get("filesets", [])
                if isinstance(fs, str)] or list(filesets)
        missing_deps: dict[str, str] = {}
        for fs_name in dict.fromkeys(used):
            fs = filesets.get(fs_name) or {}
            for f in fs.get("files") or []:
                rel = next(iter(f)) if isinstance(f, dict) else str(f)
                if not (core.parent / rel).exists():
                    found = producer(rel, ctx.targets) if ctx.targets else None
                    evidence = f"{core.name} fileset {fs_name} lists {rel}; not on disk"
                    if found:
                        out.append(issue(stage, "generated", f"generated file {rel}", f"{evidence}; {found[1]}",
                                         "generated by a step that has not run", "file.fusesoc_generated",
                                         _step_for(found[0], ctx), ctx))
                    else:
                        out.append(issue(stage, "missing_file", f"file {rel}", evidence,
                                         "the core lists a file that is not in the checkout: fix the core or add it",
                                         "file.fusesoc_file", None, ctx))
            for dep in fs.get("depend") or []:
                if _vlnv(str(dep)) not in provided and not _provided_loosely(_vlnv(str(dep)), provided):
                    missing_deps[str(dep)] = fs_name
        for dep, fs_name in missing_deps.items():
            out.append(issue(stage, "vendor_ip", f"FuseSoC dependency {dep}",
                             f"{core.name} fileset {fs_name} depends on {dep}; no .core under the root provides it",
                             "fetch the third-party IP (a download: a person approves it)", "file.fusesoc_dep",
                             _vendor_step(ctx), ctx))
    return out


def _provided_loosely(want: tuple[str, str], provided: set[tuple[str, str]]) -> bool:
    """`vendor::name` matches a core named `vendor:lib:name` too."""
    vendor, name = want
    return any(v == vendor and n == name for v, n in provided) or (not name and any(v == vendor for v, _ in provided))


_VAR = re.compile(r"\$\{(\w+)\}|\$(\w+)")


def path_issues(paths: dict[str, str], env: dict[str, str], root: Path, stage: str, ctx: Context,
                pdk: str = "") -> list[dict]:
    """Configured paths (what -> path with ${VARS}) that are not on disk, explained: an unset variable,
    a variable that points nowhere (PDK_ROOT), a PDK that is not installed under it, or the file."""
    out, said = [], set()
    for what, raw in paths.items():
        if not raw:
            continue
        names = [a or b for a, b in _VAR.findall(raw)]
        unset = [n for n in names if not env.get(n)]
        if unset:
            for n in unset:
                if n not in said:
                    said.add(n)
                    out.append(issue(stage, "env", f"environment variable {n}", f"{what} = {raw}; {n} is not set",
                                     "set it in [env_defaults] or in the env_script", "file.env_unset", None, ctx))
            continue
        expanded = _VAR.sub(lambda m: env.get(m.group(1) or m.group(2), m.group(0)), raw)
        p = Path(expanded).expanduser()
        p = p if p.is_absolute() else root / p
        if p.exists():
            continue
        bad_var = next((n for n in names if not Path(env[n]).expanduser().is_dir()), None)
        if bad_var:
            if bad_var not in said:
                said.add(bad_var)
                family = pdk if "-" in pdk else re.sub(r"[A-Z]$", "", pdk)  # sky130A -> sky130
                pdk_hint = (f" (install or enable {pdk} with ciel, e.g. `ciel enable --pdk-family {family} "
                            "<version>`)" if pdk else "")
                out.append(issue(stage, "pdk" if "PDK" in bad_var else "env",
                                 f"{bad_var} directory {env[bad_var]}",
                                 f"{what} = {raw}; {bad_var}={env[bad_var]} is not a directory",
                                 f"point {bad_var} at the installed directory in [env_defaults] or the env_script"
                                 + pdk_hint, "file.env_dir", None, ctx))
            continue
        pdk_root = env.get("PDK_ROOT", "")
        if pdk and pdk_root and str(p).startswith(str(Path(pdk_root).expanduser())) \
                and not (Path(pdk_root).expanduser() / pdk).is_dir():
            key = f"pdk:{pdk}"
            if key not in said:
                said.add(key)
                out.append(issue(stage, "pdk", f"PDK {pdk} under {pdk_root}",
                                 f"{what} = {expanded}; {pdk_root}/{pdk} is not there",
                                 f"install or enable {pdk} with ciel under PDK_ROOT", "file.pdk_missing", None, ctx))
            continue
        cat = "pdk" if pdk_root and str(p).startswith(str(Path(pdk_root).expanduser())) else "missing_file"
        found = producer(str(p.relative_to(root)), ctx.targets) if cat == "missing_file" and \
            p.is_relative_to(root) and ctx.targets else None
        if found:
            out.append(issue(stage, "generated", f"{what} {expanded}", f"{what} = {raw}; not on disk; {found[1]}",
                             "generated by a step that has not run", "file.generated", _step_for(found[0], ctx), ctx))
        else:
            out.append(issue(stage, cat, f"{what} {expanded}", f"{what} = {raw}; not on disk",
                             "a file of the PDK installation: check that this PDK version is complete" if cat == "pdk"
                             else "fix the path in the config, or add the step that writes it to [setup]",
                             "file.path", None, ctx))
    return out


def dedupe(issues: list[dict]) -> list[dict]:
    seen, out = set(), []
    for i in issues:
        key = (i["stage"], i["category"], i["missing"])
        if key not in seen:
            seen.add(key)
            out.append(i)
    return out


def summary(issues: list[dict]) -> str:
    """One line: 'environment not ready (sim): missing vendored IP ..., generated file ...'."""
    stages = ", ".join(dict.fromkeys(i["stage"] for i in issues))
    items = "; ".join(i["missing"] for i in issues[:4]) + (f"; +{len(issues) - 4} more" if len(issues) > 4 else "")
    return f"environment not ready ({stages}): missing {items}"


# --- per stage, for a project -----------------------------------------------------------------------

def context(project) -> Context:
    """The project's Context: its root, approved [setup] steps, Makefile targets and library prefix."""
    prefixes: tuple[str, ...] = ()
    if project.flow and project.flow.tie_hi:
        prefixes = (project.flow.tie_hi.split("_")[0] + "_",)  # sg13g2_tiehi/L_HI -> sg13g2_
    return make_context(project.root, project.setup.steps, prefixes)


def precheck(project, stage: str, ctx: Context | None = None) -> list[dict]:
    """File checks of one stage before anything runs: what the stage needs that is not there."""
    import shutil
    from .project import project_env
    ctx = ctx or context(project)
    root = project.root
    env = project_env(project)
    out: list[dict] = []
    if stage in ("sim", "synth"):
        out += vendor_issues(root, stage, ctx) + fusesoc_issues(root, stage, ctx)
    if stage == "sim":
        lint, reg = project.lint, project.regress
        out += path_issues({f"[lint] file {f}": f for f in (*lint.filelists, *lint.files)}, env, root, stage, ctx)
        if reg.case_list:
            out += path_issues({"[regress] case_list": reg.case_list}, env, root, stage, ctx)
        exe = reg.cmd[0] if reg.cmd else ""
        if exe and exe != "TODO":
            there = (root / exe).is_file() if "/" in exe else shutil.which(exe, path=env.get("PATH")) is not None
            if not there:
                out.append(issue(stage, "tool", f"program {exe}", f"[regress] cmd starts with {exe}; not found",
                                 "install it or put it on PATH through env_script", "file.command", None, ctx))
    elif stage == "synth" and project.flow:
        f = project.flow
        paths = {"[flow] sdc": f.sdc, "[flow] liberty": f.liberty, "[flow] dont_use_file": f.dont_use_file,
                 **{f"[flow.corners] {k}": v for k, v in f.corners.items()},
                 **{f"[flow] file {x}": x for x in (*f.files, *f.filelists)}}
        out += path_issues(paths, env, root, stage, ctx, pdk=f.pdk)
        for cf in f.command_files:
            if not (root / cf).is_file():
                out.append(issue(stage, "generated", f"command file {cf}", f"[flow] command_files names {cf}; not on disk",
                                 "written by the build tool's setup (e.g. `fusesoc run --setup --target synth "
                                 "--tool verilator <core>`); run it once", "file.command_file", None, ctx))
    elif stage == "pnr" and project.pnr:
        p = project.pnr
        pdk = p.pdk or (project.flow.pdk if project.flow else "")
        paths = {"[pnr] config": p.config}
        if pdk:
            paths["[pnr] pdk"] = f"{p.pdk_root}/{pdk}"
        out += path_issues(paths, env, root, stage, ctx, pdk=pdk)
    return dedupe(out)
