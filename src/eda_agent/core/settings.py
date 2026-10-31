"""Where the CLI finds its inputs when the command line leaves them out.

- The project config: --project, else `eda-agent.toml` in the current directory or a parent.
- Personal defaults (provider, model, effort, review): ~/.config/eda-agent/config.toml, e.g.
      provider = "deepseek"
      review = "I2"
  Command-line options always win.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

PROJECT_FILE = "eda-agent.toml"
USER_KEYS = ("provider", "model", "effort", "review", "review_provider", "review_model")


def user_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "eda-agent" / "config.toml"


def user_defaults(path: Path | None = None) -> dict:
    path = path or user_config_path()
    if not path.is_file():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    unknown = set(data) - set(USER_KEYS)
    if unknown:
        raise ValueError(f"{path}: unknown key(s) {sorted(unknown)}; known: {', '.join(USER_KEYS)}")
    return data


def find_project(start: Path | None = None) -> Path | None:
    here = (start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        if (d / PROJECT_FILE).is_file():
            return d / PROJECT_FILE
    return None
