"""Typed result envelope returned by every EDA tool.

Every tool answers with the same shape so that the agent (and the evidence gate
that will sit behind it) can tell *what* a check proved, not just whether it
printed "PASS". See the methodology: tool control L3 (structured results) and
the evidence ladder E0-E5.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

Status = Literal["pass", "fail", "error", "timeout"]

# E0 syntax/compile, E1 LLM or human score, E2 simulation pass,
# E3 bit-exact compare + mutants killed, E4 synthesis/proxy PPA,
# E5 post-route multi-corner STA + DRC/LVS + gate-level + VCD power.
EVIDENCE_LEVELS = ("E0", "E1", "E2", "E3", "E4", "E5")


@dataclass
class Diagnostic:
    severity: str
    code: str
    msg: str
    file: str = ""
    line: int = 0
    col: int = 0


@dataclass
class ToolResult:
    tool: str
    status: Status
    evidence_level: str
    claim_scope: str
    summary: str = ""
    metrics: dict = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    diagnostics_total: int = 0
    artifacts: list[dict] = field(default_factory=list)
    tool_versions: dict = field(default_factory=dict)  # tool name -> first line of its --version
    error_class: str | None = None
    residual_risk: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.evidence_level not in EVIDENCE_LEVELS:
            raise ValueError(f"unknown evidence level {self.evidence_level!r}")

    def to_dict(self) -> dict:
        return asdict(self)


def artifact(path: str | Path) -> dict:
    """Path plus sha256, so a later reader can tell whether the file changed."""
    p = Path(path)
    digest = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
    return {"path": str(p), "sha256": digest}
