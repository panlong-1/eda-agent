"""Regression result formats: how a project's own regression reports pass/fail.

Each module runs the regression command for some cases and reads its result into
rows (name, PASS/FAIL/SKIP, detail). It also says, in CLAIM_SCOPE and
RESIDUAL_RISK, what that kind of result can and cannot tell. `[regress] format`
in the project TOML picks the module.
"""
from __future__ import annotations

from dataclasses import dataclass, field

Row = tuple[str, str, str]  # name, PASS/FAIL/SKIP, detail

NOT_ADEQUACY = ("It does not show that the tests would catch a bug (no coverage or mutation "
                "score), and says nothing about timing or power.")
ADEQUACY_RISK = "test adequacy unknown: no coverage or mutation score yet"


@dataclass
class Outcome:
    rows: list[Row]
    artifacts: list[dict] = field(default_factory=list)
    diags: dict[str, str] = field(default_factory=dict)  # failing case -> detail text
    subtests: dict[str, dict] = field(default_factory=dict)  # case -> testbench's own counts
    logs: dict[str, str] = field(default_factory=dict)  # case -> file with that case's own output


def tail(text: str, lines: int = 8) -> str:
    return "\n".join(text.splitlines()[-lines:])
