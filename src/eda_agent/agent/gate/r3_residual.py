"""R3 risk: the report has a residual-risk section with some content."""
from __future__ import annotations


MIN_RESIDUAL_CHARS = 20


def check_residual(residual: str) -> list[str]:
    if len(residual.strip(" \t\n:#*-")) < MIN_RESIDUAL_CHARS:
        return ["no residual-risk section: add a section headed 'Residual risk' that says what "
                "the checks did not cover"]
    return []
