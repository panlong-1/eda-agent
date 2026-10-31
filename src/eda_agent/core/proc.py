"""Subprocess helpers: a hard timeout that also kills child processes, and a command allowlist.

Tools only run commands from the project config, never text from the model. The allowlist
is a second line: a config that names an unexpected program is refused before it runs.
"""
from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Open-source EDA and build programs a check may start. A project adds its own with
# allowed_commands; an executable script inside the project root is always allowed.
ALLOWED_COMMANDS = frozenset({
    "verilator", "make", "yosys", "iverilog", "vvp", "sby", "eqy", "mcy", "wavepeek",
})


@dataclass
class ProcResult:
    returncode: int | None  # None means the timeout fired
    output: str


def run(cmd: list[str], cwd: Path, env: dict[str, str], timeout_s: int) -> ProcResult:
    proc = subprocess.Popen(
        cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, start_new_session=True,
    )
    try:
        out, _ = proc.communicate(timeout=timeout_s)
        return ProcResult(proc.returncode, out)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        out, _ = proc.communicate()
        return ProcResult(None, out)


def check_allowed(cmd: list[str], root: Path, extra: tuple[str, ...] = ()) -> str | None:
    """None if cmd may run, else the reason it may not."""
    if not cmd:
        return "empty command"
    exe = cmd[0]
    if Path(exe).name in ALLOWED_COMMANDS or exe in extra:
        return None
    path = (root / exe).resolve()
    if "/" in exe and path.is_relative_to(root.resolve()) and path.is_file() and os.access(path, os.X_OK):
        return None
    return (f"command {exe!r} is not allowed: add it to allowed_commands in the project config "
            f"if it should run (built in: {', '.join(sorted(ALLOWED_COMMANDS))})")


def tool_version(cmd: list[str], env: dict[str, str] | None = None) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=30)
        return (out.stdout or out.stderr).strip().splitlines()[0]
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return "unknown"
