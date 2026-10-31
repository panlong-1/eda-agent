"""Contract checks every importer must pass (docs/plugins.md). Run them with

    eda-agent plugins check [--plugin NAME]

or from a plug-in's own tests:  `from eda_agent.plugins.contract import check_importer`.

An importer lists the report directories it must accept in `contract_samples()` (its own test data,
which may be private). The core adds directories it must refuse.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from .result import NotMine, validate


def _foreign_dirs(root: Path) -> list[Path]:
    empty = root / "empty"
    empty.mkdir()
    other = root / "other"
    other.mkdir()
    (other / "notes.txt").write_text("not an EDA report\n")
    (other / "report.rpt").write_text("Report : something else entirely\nTotal : 42\n")
    return [empty, other]


def check_importer(imp, foreign: list[Path] | None = None) -> list[str]:
    """Problems found ([] = passes). Checks: name; samples detected and parsed into a valid neutral
    result with no core-only fields; parse is repeatable; unknown directories are refused (detect
    false and parse raises NotMine) instead of guessed."""
    problems = []
    name = getattr(imp, "name", None)
    if not name:
        return ["importer has no name"]
    for attr in ("detect", "parse", "contract_samples"):
        if not callable(getattr(imp, attr, None)):
            problems.append(f"{name}: missing {attr}()")
    if problems:
        return problems
    samples = [Path(s) for s in imp.contract_samples()]
    if not samples:
        problems.append(f"{name}: contract_samples() gives no sample directory")
    for s in samples:
        if not imp.detect(s):
            problems.append(f"{name}: does not detect its own sample {s}")
            continue
        try:
            first, second = imp.parse(s), imp.parse(s)
        except Exception as e:
            problems.append(f"{name}: parse({s}) raised {type(e).__name__}: {e}")
            continue
        problems += [f"{name} {s}: {p}" for p in validate(first, s)]
        if first != second:
            problems.append(f"{name}: parse({s}) is not repeatable")
    with tempfile.TemporaryDirectory() as tmp:
        for d in [*(foreign or []), *_foreign_dirs(Path(tmp))]:
            if any(d.resolve() == s.resolve() for s in samples):
                continue
            if imp.detect(d):
                problems.append(f"{name}: claims a directory that is not its report: {d.name}")
            try:
                imp.parse(d)
                problems.append(f"{name}: parse() of an unknown directory ({d.name}) returned instead of raising NotMine")
            except NotMine:
                pass
            except Exception as e:
                problems.append(f"{name}: parse() of an unknown directory raised {type(e).__name__}, not NotMine")
    return problems
