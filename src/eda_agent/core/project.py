"""Project description: where a design lives and how its own scripts are driven.

The tools never guess a flow. Each design ships a small TOML file that names
its environment script, filelists and regression entry point, so the agent runs
exactly what a human on that project would run.
"""
from __future__ import annotations

import os
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from .flow_sources import resolve_flow


DEFAULT_LINT_FLAGS = ("-Wall", "-Wno-fatal")
DEFAULT_VERSIONS = {"verilator": ("verilator", "--version")}


@dataclass(frozen=True)
class LintConfig:
    top: str
    filelists: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    include_dirs: tuple[str, ...] = ()
    defines: tuple[str, ...] = ()
    flags: tuple[str, ...] = DEFAULT_LINT_FLAGS
    # Verilator command files (-f), e.g. the .vc that FuseSoC/edalize writes. Paths inside one
    # are relative to its own directory, so lint runs from there.
    command_files: tuple[str, ...] = ()
    # Path prefixes (relative to the root) of third-party code: their warnings are not ours to
    # fix and are left out; their errors are kept.
    external: tuple[str, ...] = ()
    # Directories (relative to the root) that hold a copy of the project's own files, e.g. FuseSoC's
    # build/<core>/src/<core>; diagnostics there are reported against the original file.
    copy_of_root: tuple[str, ...] = ()


@dataclass(frozen=True)
class CaseSpec:
    """One regression case: its name, extra command arguments and where it is described."""
    name: str
    args: tuple[str, ...] = ()
    source: str = ""        # test source file, relative to the project root
    description: str = ""   # used when the source has no leading comment
    list_entry: str = ""    # the raw case-list line, if the case came from a list file


# How the regression reports results (see tools/formats/):
#   torv     "Result directory: <dir>" in the output, <dir>/<summary_file> rows name,STATUS,detail
#   junit    a JUnit XML file written by the run
#   exitcode the command's exit status per case, optionally confirmed by pass/fail regexes
REGRESS_FORMATS = ("torv", "junit", "exitcode")


@dataclass(frozen=True)
class RegressConfig:
    cmd: tuple[str, ...]
    case_list: str = ""
    cases: tuple[CaseSpec, ...] = ()  # inline cases, used when there is no case_list
    case_flag: str = ""
    format: str = "exitcode"
    summary_file: str = "summary.rpt"
    junit_file: str = ""
    pass_regex: str = ""
    fail_regex: str = ""
    # exitcode only: the testbench's own summary line, with named groups passed / failed / total,
    # e.g. "regress: (?P<passed>\d+)/(?P<total>\d+) cases ok". Its counts then decide too.
    summary_regex: str = ""
    timeout_s: int = 1800

    def __post_init__(self) -> None:
        if self.summary_regex:
            groups = set(re.compile(self.summary_regex).groupindex)
            if not groups or groups - {"passed", "failed", "total"} or groups == {"total"}:
                raise ValueError("regress summary_regex needs named groups among passed, failed, total "
                                 "(at least passed or failed), and no others")
            if self.format != "exitcode":
                raise ValueError("regress summary_regex is for format 'exitcode'")
        if self.format not in REGRESS_FORMATS:
            raise ValueError(f"unknown regress format {self.format!r}; use one of {REGRESS_FORMATS}")
        if self.format == "junit" and not self.junit_file:
            raise ValueError("regress format 'junit' needs junit_file")
        if self.format == "torv" and not self.case_list:
            raise ValueError("regress format 'torv' needs case_list")


@dataclass(frozen=True)
class FlowConfig:
    """Synthesis and timing setup ([flow]). Paths are relative to the project root unless
    absolute; ${VAR}s (e.g. ${PDK_ROOT}) are expanded from the project environment."""
    top: str
    sdc: str
    clock_port: str
    clock_period: float              # ns; also given to the SDC as $::env(CLOCK_PERIOD)
    liberty: str                     # the library synthesis maps to
    image: str                       # container image that holds yosys and sta
    pdk: str = ""
    pdk_root: str = "${PDK_ROOT}"    # mounted read-only into the container
    files: tuple[str, ...] = ()
    filelists: tuple[str, ...] = ()
    # Verilator-style command files (e.g. FuseSoC's synth target written with --tool verilator):
    # their source files and +incdir+ directories are used, relative to each file's directory.
    command_files: tuple[str, ...] = ()
    include_dirs: tuple[str, ...] = ()
    defines: tuple[str, ...] = ()
    frontend: str = "slang"          # slang (SystemVerilog plugin) | verilog (read_verilog -sv)
    synth_flags: tuple[str, ...] = ()
    dont_use_file: str = ""          # one cell name per line, kept out of mapping
    tie_hi: str = ""                 # cell/pin, e.g. sg13g2_tiehi/L_HI
    tie_lo: str = ""
    buffer: str = ""                 # cell/in/out, for wires joined to ports
    abc_driving_cell: str = ""       # what ABC assumes drives its inputs; enables fanout buffering
    abc_load: float = 0.0
    # corner name -> Liberty file; STA runs on each, the worst one decides. Default: liberty only.
    corners: dict = field(default_factory=dict, hash=False, compare=False)
    timeout_s: int = 1800
    # Where the SDC came from when it is not the design's own (e.g. "hand translation of the
    # course DC constraints"); copied into each receipt and shown to the approver.
    sdc_origin: str = ""

    def __post_init__(self) -> None:
        if self.frontend not in ("slang", "verilog"):
            raise ValueError(f"unknown flow frontend {self.frontend!r}; use slang or verilog")
        if self.clock_period <= 0:
            raise ValueError("flow clock_period must be positive (ns)")


@dataclass(frozen=True)
class MutationConfig:
    """Mutation runs ([mutation]): which files may be mutated and which cases judge the mutants.
    Each mutant is applied in a copy of the root, never in the root itself."""
    files: tuple[str, ...] = ()         # relative to the root; empty = any .sv/.v file under it
    cases: tuple[str, ...] = ()         # default cases per mutant; empty = the whole case list
    types: tuple[str, ...] = ()         # avl-core mutation classes; empty = all
    # Paths (relative to the root) left out of the copy, e.g. build and result directories. They are
    # also emptied in the copy after each mutant, so results do not pile up.
    copy_exclude: tuple[str, ...] = (".git",)
    jobs: int = 2                       # copies run in parallel
    timeout_s: int = 900                # per mutant, all its cases together
    equiv_cycles: int = 8               # bound of the equivalence check on survivors

    def __post_init__(self) -> None:
        if self.jobs < 1 or self.timeout_s < 1 or self.equiv_cycles < 1:
            raise ValueError("mutation jobs, timeout_s and equiv_cycles must be positive")


@dataclass(frozen=True)
class NewTestsConfig:
    """How one new test file is run ([tests]); used by write_case and score_tests. Pass/fail is
    judged as for the regression: exit status plus [regress] pass/fail/summary regexes."""
    run: tuple[str, ...]        # runs ONE test file: {file} (absolute path), {name} (its stem)
    log: str = ""               # the test's detailed output, relative to the root ({name}); default:
                                # the command's own output
    register: str = ""          # line the patch adds to [regress] case_list ({name}, {path})
    examples: tuple[str, ...] = ()  # cases to imitate; default: those that kill most mutants of the file

    def __post_init__(self) -> None:
        if not any("{file}" in a for a in self.run):
            raise ValueError("[tests] run must contain {file}, e.g. [\"scripts/run_case.sh\", \"{file}\"]")


@dataclass(frozen=True)
class PerfConfig:
    """Performance numbers the testbench already prints ([perf]), read per case with regexes.
    Each pattern's named groups become that case's numbers, e.g. cycles, instr, ipc."""
    patterns: tuple[str, ...]
    key: str = "cycles"                 # the number that decides slower / same / faster
    lower_is_better: bool = True        # cycles: fewer is faster; set false for e.g. ipc
    cases: tuple[str, ...] = ()         # default cases for perf; empty = the whole case list
    # Allowed difference, in percent of the reference, before a case counts as slower or faster.
    # RTL simulation is deterministic, so 0 is the default.
    tolerance_pct: float = 0.0
    # Where a case's output is, when the regression format does not say: {root}, {case}, {detail}.
    log: str = ""
    # The person-approved expectations (case -> key value); see `eda-agent perf-baseline`.
    baseline: str = ""

    def __post_init__(self) -> None:
        if not self.patterns:
            raise ValueError("[perf] needs at least one pattern")
        for p in self.patterns:
            if not re.compile(p).groupindex:
                raise ValueError(f"[perf] pattern {p!r} has no named group, e.g. (?P<cycles>\\d+)")
        if not any(self.key in re.compile(p).groupindex for p in self.patterns):
            raise ValueError(f"[perf] key {self.key!r} is not a named group of any pattern")
        if self.tolerance_pct < 0:
            raise ValueError("[perf] tolerance_pct must not be negative")


@dataclass(frozen=True)
class Project:
    name: str
    root: Path
    lint: LintConfig
    regress: RegressConfig
    env_script: str | None = None
    env_defaults: dict = field(default_factory=dict, hash=False, compare=False)
    # tool name -> command that prints its version; recorded in every result that ran it
    versions: dict = field(default_factory=dict, hash=False, compare=False)
    allowed_commands: tuple[str, ...] = ()
    allowed_images: tuple[str, ...] = ()
    flow: FlowConfig | None = None   # None: synth and check_sdc refuse (no [flow] section)
    mutation: MutationConfig | None = None  # None: mutate refuses (no [mutation] section)
    perf: PerfConfig | None = None          # None: perf refuses (no [perf] section)
    tests: NewTestsConfig | None = None     # None: write_case / score_tests refuse (no [tests])


def _tests(data: dict | None) -> NewTestsConfig | None:
    if data is None:
        return None
    unknown = set(data) - set(NewTestsConfig.__dataclass_fields__)
    if unknown:
        raise ValueError(f"unknown [tests] key(s) {sorted(unknown)}")
    return NewTestsConfig(**{k: tuple(v) if k in ("run", "examples") else v for k, v in data.items()})


def _perf(data: dict | None, config: Path) -> PerfConfig | None:
    """[perf]; the baseline defaults to <config stem>.perf-baseline.json next to the config."""
    if data is None:
        return None
    unknown = set(data) - set(PerfConfig.__dataclass_fields__)
    if unknown:
        raise ValueError(f"unknown [perf] key(s) {sorted(unknown)}")
    base = data.get("baseline") or f"{config.stem}.perf-baseline.json"
    baseline = Path(os.path.expanduser(base))
    data = {**data, "baseline": str(baseline if baseline.is_absolute() else (config.parent / baseline).resolve())}
    tuples = ("patterns", "cases")
    return PerfConfig(**{k: tuple(v) if k in tuples else v for k, v in data.items()})


def _mutation(data: dict | None) -> MutationConfig | None:
    if data is None:
        return None
    unknown = set(data) - set(MutationConfig.__dataclass_fields__)
    if unknown:
        raise ValueError(f"unknown [mutation] key(s) {sorted(unknown)}")
    tuples = ("files", "cases", "types", "copy_exclude")
    return MutationConfig(**{k: tuple(v) if k in tuples else v for k, v in data.items()})


def _flow(data: dict | None, root: Path) -> FlowConfig | None:
    if data is None:
        return None
    data = resolve_flow(data, root)
    tuples = ("files", "filelists", "command_files", "include_dirs", "defines", "synth_flags")
    known = set(FlowConfig.__dataclass_fields__)
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"unknown [flow] key(s) {sorted(unknown)}")
    return FlowConfig(**{k: tuple(v) if k in tuples else v for k, v in data.items()})


def load_project(path: str | Path) -> Project:
    """Load a project config. Left out, these default: lint flags -Wall -Wno-fatal; [versions]
    Verilator; regress format exitcode with no case flag; no case list = one case, `all`."""
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    root = Path(os.path.expanduser(data["root"])).resolve()
    lint = data["lint"]
    regress = data["regress"]
    fmt = regress.get("format", "exitcode")
    return Project(
        name=data["name"],
        root=root,
        env_script=data.get("env_script"),
        env_defaults=data.get("env_defaults", {}),
        versions={k: tuple(v) for k, v in data.get("versions", DEFAULT_VERSIONS).items()},
        allowed_commands=tuple(data.get("allowed_commands", ())),
        allowed_images=tuple(data.get("allowed_images", ())),
        flow=_flow(data.get("flow"), root),
        mutation=_mutation(data.get("mutation")),
        perf=_perf(data.get("perf"), Path(path)),
        tests=_tests(data.get("tests")),
        lint=LintConfig(
            top=lint["top"],
            filelists=tuple(lint.get("filelists", ())),
            files=tuple(lint.get("files", ())),
            include_dirs=tuple(lint.get("include_dirs", ())),
            defines=tuple(lint.get("defines", ())),
            flags=tuple(lint.get("flags", DEFAULT_LINT_FLAGS)),
            command_files=tuple(lint.get("command_files", ())),
            external=tuple(lint.get("external", ())),
            copy_of_root=tuple(lint.get("copy_of_root", ())),
        ),
        regress=RegressConfig(
            cmd=tuple(regress["cmd"]),
            case_list=regress.get("case_list", ""),
            cases=tuple(CaseSpec(name=c["name"], args=tuple(c.get("args", ())),
                                 source=c.get("source", ""), description=c.get("description", ""))
                        for c in regress.get("cases", ())),
            case_flag=regress.get("case_flag", "--case" if fmt == "torv" else ""),
            format=fmt,
            summary_file=regress.get("summary_file", "summary.rpt"),
            junit_file=regress.get("junit_file", ""),
            pass_regex=regress.get("pass_regex", ""),
            fail_regex=regress.get("fail_regex", ""),
            summary_regex=regress.get("summary_regex", ""),
            timeout_s=int(regress.get("timeout_s", 1800)),
        ),
    )


@cache
def project_env(project: Project) -> dict[str, str]:
    """Environment after sourcing the project's env script (cached per project)."""
    env = {k: os.path.expanduser(v) for k, v in project.env_defaults.items()}
    env = {**os.environ, **env}
    if not project.env_script:
        return env
    out = subprocess.run(
        ["bash", "-c", f'source "{project.env_script}" >/dev/null 2>&1; env -0'],
        cwd=project.root, env=env, capture_output=True, check=True,
    ).stdout
    pairs = (item.split("=", 1) for item in out.decode().split("\0") if "=" in item)
    return dict(pairs)


@cache
def project_versions(project: Project) -> dict[str, str]:
    """Version line of each tool named in [versions] (cached per project and process)."""
    from .proc import tool_version
    env = project_env(project)
    return {name: tool_version(list(cmd), env) for name, cmd in project.versions.items()}


_VAR = re.compile(r"\$\{(\w+)\}|\$(\w+)")


def expand_vars(text: str, env: dict[str, str]) -> str:
    return _VAR.sub(lambda m: env.get(m.group(1) or m.group(2), m.group(0)), text)


def read_command_file(path: Path) -> tuple[list[Path], list[Path]]:
    """(source files, include dirs) named in a Verilator command file, relative to its directory.
    Options other than +incdir+ (--Mdir, -CFLAGS ..., --top-module ...) are skipped."""
    base = path.parent
    files, incs = [], []
    tokens = path.read_text(encoding="utf-8").split()
    skip = False
    for tok in tokens:
        if skip:
            skip = False
            continue
        if tok.startswith("+incdir+"):
            incs += [(base / d).resolve() for d in tok[len("+incdir+"):].split("+") if d]
        elif tok in ("-CFLAGS", "--Mdir", "--top-module", "-LDFLAGS", "-f", "-F"):
            skip = True  # option with a value
        elif not tok.startswith(("-", "+")) and tok.endswith((".sv", ".v", ".svh", ".vh")):
            files.append((base / tok).resolve())
    return files, incs


def read_filelist(path: Path, env: dict[str, str], root: Path) -> list[Path]:
    """Files named in a .f list; comments and blank lines skipped, $VARs expanded."""
    files = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "//")):
            continue
        p = Path(expand_vars(line, env))
        files.append(p if p.is_absolute() else root / p)
    return files


IMPLICIT_CASE = "all"  # the only case of a project that lists none


def case_specs(project: Project) -> list[CaseSpec]:
    """Cases from the inline config, else from the case list file, else one case: `all`.

    In a list file the first column is the name; the first later column that names
    an existing file under the project root is taken as the case's source.
    """
    cfg = project.regress
    if not cfg.case_list:
        return list(cfg.cases) or [CaseSpec(
            name=IMPLICIT_CASE,
            description=f"The project's whole regression, run as one command: {' '.join(cfg.cmd)}")]
    specs = []
    for raw in (project.root / cfg.case_list).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, *rest = line.split()
        source = next((f for f in rest if (project.root / f).is_file()), "")
        specs.append(CaseSpec(name=name, source=source, list_entry=line))
    return specs


def regress_cases(project: Project) -> list[str]:
    return [c.name for c in case_specs(project)]
