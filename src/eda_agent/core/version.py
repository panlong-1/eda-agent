"""Which eda-agent code is running: its git commit when it runs from a checkout, else the package
version. Recorded in every receipt and every human-gate decision, so a decision made with older
code (or older approval rules) can be told apart later."""
from __future__ import annotations

import subprocess
from datetime import datetime
from functools import lru_cache
from importlib import metadata
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]  # src/eda_agent


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(_SRC), *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


@lru_cache(maxsize=1)
def code_version() -> dict:
    """{"commit", "commit_time", "dirty", "package"}; commit fields are None outside a git checkout."""
    try:
        package = metadata.version("eda-agent")
    except metadata.PackageNotFoundError:
        package = None
    commit = _git("rev-parse", "HEAD")
    return {
        "commit": commit,
        "commit_time": _git("show", "-s", "--format=%cI", "HEAD") if commit else None,
        "dirty": bool(_git("status", "--porcelain", "--untracked-files=no", "--", ".")) if commit else None,
        "package": package,
    }


def label(v: dict | None) -> str:
    """A version for a person: the short commit (and whether it had uncommitted changes), else the package."""
    if not v:
        return "not recorded"
    if v.get("commit"):
        return v["commit"][:12] + (" + uncommitted changes" if v.get("dirty") else "") + \
            (f" ({v['commit_time']})" if v.get("commit_time") else "")
    return f"package {v.get('package') or '?'}"


def older(a: dict | None, b: dict | None) -> bool:
    """Is code version a older than b? Only commits with known times can be compared."""
    ta, tb = (a or {}).get("commit_time"), (b or {}).get("commit_time")
    if not ta or not tb or (a or {}).get("commit") == (b or {}).get("commit"):
        return False
    return datetime.fromisoformat(ta) < datetime.fromisoformat(tb)
