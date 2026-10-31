"""Subprocess helpers: a hard timeout that also kills child processes, and a command allowlist.

Tools only run commands from the project config, never text from the model. The allowlist
is a second line: a config that names an unexpected program is refused before it runs.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
from dataclasses import dataclass
from functools import cache
from pathlib import Path

# Open-source EDA and build programs a check may start. A project adds its own with
# allowed_commands; an executable script inside the project root is always allowed.
ALLOWED_COMMANDS = frozenset({
    "verilator", "make", "yosys", "sta", "iverilog", "vvp", "sby", "eqy", "mcy", "wavepeek",
    "avl-mutation-testing", "librelane",
})
# Container images a tool may start (repository, any tag). A project adds its own with
# allowed_images. Inside the container the program must still be in ALLOWED_COMMANDS.
ALLOWED_IMAGES = frozenset({"ghcr.io/librelane/librelane"})


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


def image_repo(image: str) -> str:
    """'ghcr.io/x/y' from 'ghcr.io/x/y:tag' or 'ghcr.io/x/y@sha256:...'."""
    name = image.split("@")[0]
    head, _, last = name.rpartition("/")
    return f"{head}/{last.split(':')[0]}" if head else last.split(":")[0]


def docker_command(image: str, inner: list[str], mounts: list[tuple[Path, bool]],
                   extra_images: tuple[str, ...] = (), env: dict[str, str] | None = None,
                   workdir: str | None = None) -> list[str] | str:
    """`docker run` of an allowed program in an allowed image, as the calling user and without
    network; else the reason it may not run. mounts: (host path, writable), each at the same path."""
    if image_repo(image) not in ALLOWED_IMAGES | set(extra_images):
        return (f"image {image!r} is not allowed: add its repository to allowed_images in the project "
                f"config if it should run (built in: {', '.join(sorted(ALLOWED_IMAGES))})")
    if not inner or Path(inner[0]).name not in ALLOWED_COMMANDS:
        return f"command {inner[:1]!r} is not allowed inside the container"
    cmd = ["docker", "run", "--rm", "--network", "none", "-u", f"{os.getuid()}:{os.getgid()}"]
    for path, writable in mounts:
        cmd += ["-v", f"{path}:{path}" + ("" if writable else ":ro")]
    for k, v in (env or {}).items():
        cmd += ["-e", f"{k}={v}"]
    if workdir:
        cmd += ["-w", workdir]
    return [*cmd, image, *inner]


@cache
def image_digest(image: str) -> str:
    """'<repo>@sha256:...' of the local image (its registry digest when known), or 'unknown'."""
    try:
        out = subprocess.run(["docker", "image", "inspect", "--format", "{{json .RepoDigests}} {{.Id}}", image],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    if out.returncode != 0:
        return "unknown"
    digests, _, image_id = out.stdout.strip().rpartition(" ")
    repo_digest = next((d for d in json.loads(digests or "[]") if "@sha256:" in d), "")
    return repo_digest or f"{image_repo(image)}@{image_id}"


def docker_available() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
