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


@dataclass(frozen=True)
class LintConfig:
    top: str
    filelists: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    include_dirs: tuple[str, ...] = ()
    defines: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class CaseSpec:
    """One regression case: its name, extra command arguments and where it is described."""
    name: str
    args: tuple[str, ...] = ()
    source: str = ""        # test source file, relative to the project root
    description: str = ""   # used when the source has no leading comment
    list_entry: str = ""    # the raw case-list line, if the case came from a list file


# How the regression reports results (see tools/regress.py):
#   torv     "Result directory: <dir>" in the output, <dir>/<summary_file> rows name,STATUS,detail
#   junit    a JUnit XML file written by the run
#   exitcode the command's exit status per case, optionally confirmed by pass/fail regexes
REGRESS_FORMATS = ("torv", "junit", "exitcode")


@dataclass(frozen=True)
class RegressConfig:
    cmd: tuple[str, ...]
    case_list: str = ""
    cases: tuple[CaseSpec, ...] = ()  # inline cases, used when there is no case_list
    case_flag: str = "--case"
    format: str = "torv"
    summary_file: str = "summary.rpt"
    junit_file: str = ""
    pass_regex: str = ""
    fail_regex: str = ""
    timeout_s: int = 1800

    def __post_init__(self) -> None:
        if self.format not in REGRESS_FORMATS:
            raise ValueError(f"unknown regress format {self.format!r}; use one of {REGRESS_FORMATS}")
        if self.format == "junit" and not self.junit_file:
            raise ValueError("regress format 'junit' needs junit_file")
        if not self.case_list and not self.cases:
            raise ValueError("regress needs case_list or inline [[regress.cases]]")


@dataclass(frozen=True)
class Project:
    name: str
    root: Path
    lint: LintConfig
    regress: RegressConfig
    env_script: str | None = None
    env_defaults: dict = field(default_factory=dict, hash=False, compare=False)


def load_project(path: str | Path) -> Project:
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    root = Path(os.path.expanduser(data["root"])).resolve()
    lint = data["lint"]
    regress = data["regress"]
    return Project(
        name=data["name"],
        root=root,
        env_script=data.get("env_script"),
        env_defaults=data.get("env_defaults", {}),
        lint=LintConfig(
            top=lint["top"],
            filelists=tuple(lint.get("filelists", ())),
            files=tuple(lint.get("files", ())),
            include_dirs=tuple(lint.get("include_dirs", ())),
            defines=tuple(lint.get("defines", ())),
            flags=tuple(lint.get("flags", ())),
        ),
        regress=RegressConfig(
            cmd=tuple(regress["cmd"]),
            case_list=regress.get("case_list", ""),
            cases=tuple(CaseSpec(name=c["name"], args=tuple(c.get("args", ())),
                                 source=c.get("source", ""), description=c.get("description", ""))
                        for c in regress.get("cases", ())),
            case_flag=regress.get("case_flag", "--case"),
            format=regress.get("format", "torv"),
            summary_file=regress.get("summary_file", "summary.rpt"),
            junit_file=regress.get("junit_file", ""),
            pass_regex=regress.get("pass_regex", ""),
            fail_regex=regress.get("fail_regex", ""),
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


_VAR = re.compile(r"\$\{(\w+)\}|\$(\w+)")


def expand_vars(text: str, env: dict[str, str]) -> str:
    return _VAR.sub(lambda m: env.get(m.group(1) or m.group(2), m.group(0)), text)


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


def case_specs(project: Project) -> list[CaseSpec]:
    """Cases from the inline config, else from the case list file.

    In a list file the first column is the name; the first later column that names
    an existing file under the project root is taken as the case's source.
    """
    cfg = project.regress
    if not cfg.case_list:
        return list(cfg.cases)
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
