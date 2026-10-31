"""Subprocess helper with a hard timeout that also kills child processes."""
from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path


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


def tool_version(cmd: list[str], env: dict[str, str] | None = None) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=30)
        return (out.stdout or out.stderr).strip().splitlines()[0]
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return "unknown"
