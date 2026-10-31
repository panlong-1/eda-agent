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
class RegressConfig:
    cmd: tuple[str, ...]
    case_list: str
    case_flag: str = "--case"
    summary_file: str = "summary.rpt"
    timeout_s: int = 1800


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
            case_list=regress["case_list"],
            case_flag=regress.get("case_flag", "--case"),
            summary_file=regress.get("summary_file", "summary.rpt"),
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


def regress_cases(project: Project) -> list[str]:
    """Case names from the project's case list (first column)."""
    names = []
    for raw in (project.root / project.regress.case_list).read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            names.append(line.split()[0])
    return names
