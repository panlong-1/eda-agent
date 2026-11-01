"""`eda-agent init <design dir>`: write a draft project config from what the repository holds.

The draft names, next to every value, where the value came from, and marks the ones a
person must check (CHECK). It is a draft: the config decides what each check means, so
nothing uses it until a person has read it.
"""
from __future__ import annotations

import json
from pathlib import Path

from .detect import Detection, Guess, detect

HEADER = """\
# DRAFT written by `eda-agent init` on {date}. Read every line before using it:
# this file decides what lint, regress and synth check. Lines marked CHECK are guesses
# that most need a person. Delete this header once the file is confirmed.
"""


def _toml(value) -> str:
    if isinstance(value, str):
        return json.dumps(value)  # a TOML basic string is a JSON string for these inputs
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml(v) for v in value) + "]"
    return json.dumps(value)


def _line(key: str, g: Guess) -> str:
    mark = "CHECK " if g.confirm else ""
    return f"{key} = {_toml(g.value)}  # {mark}from: {g.source}"


def render(det: Detection, date: str, env_script: str | None = None) -> str:
    out = [HEADER.format(date=date).rstrip(), "", _line("name", det.name), f"root = {_toml(str(det.root))}"]
    if env_script:
        out.append(f"env_script = {_toml(env_script)}  # from: --env-script")
    if det.versions:
        out += ["", "[versions]"] + [_line(k, g) for k, g in det.versions.items()]
    out += ["", "[lint]"] + [_line(k, g) for k, g in det.lint.items()]
    out += ["", "[regress]", "# One case, `all`, unless you list cases; exit status (and pass_regex) decide."]
    out += [_line(k, g) for k, g in det.regress.items()]
    if det.flow:
        out += ["", "# Synthesis and timing (synth, check_sdc): read from the LibreLane config;",
                "# PDK library settings come from the built-in preset for its PDK."]
        out += ["[flow]"] + [_line(k, g) for k, g in det.flow.items()]
    if det.setup:
        out += ["", "# Steps that prepare the checkout (fetch IP, generate files). The agent may run these and",
                "# only these (run_setup, doctor --deep): delete every step you do not approve.",
                "[setup]"] + [_line(k, g) for k, g in det.setup.items()]
    if det.notes:
        out += ["", "# Notes from init:"] + [f"# - {n}" for n in det.notes]
    return "\n".join(out) + "\n"


def write_draft(design_dir: Path, out: Path, date: str, env_script: str | None = None,
                run_fusesoc: bool = True, force: bool = False) -> tuple[Path, Detection]:
    if out.exists() and not force:
        raise FileExistsError(f"{out} exists; pass --force to overwrite it")
    det = detect(design_dir, run_fusesoc=run_fusesoc)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(det, date, env_script), encoding="utf-8")
    return out, det
