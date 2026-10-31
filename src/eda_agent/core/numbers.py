"""Small shared helpers for numbers and text: parsing tool output, ranking levels, shortening.

    parse_float("1.25") -> 1.25   ("INF", "abc" -> None)      STA numbers
    parse_number("42") -> 42       parse_number("4.5") -> 4.5    counters a testbench printed
    fmt_num(1234567) -> "1,234,567"                              a number in a table cell
    level_rank("E3") -> 3          evidence levels E0..E5 (unknown: -1)
    severity_rank("medium") -> 1   reviewer severities, most severe first (unknown: last)
    shorten(text, 220)             one line, cut with "…"
"""
from __future__ import annotations

import math
import re

from .envelope import EVIDENCE_LEVELS

SEVERITIES = ("high", "medium", "low", "info")


def parse_float(text: str) -> float | None:
    """A float, or None for text that is not one and for infinity (STA: no constrained path)."""
    try:
        value = float(text)
    except ValueError:
        return None
    return None if math.isinf(value) else value


def parse_number(text: str) -> int | float:
    """An int when the text is a whole number, else a float."""
    return int(text) if re.fullmatch(r"[+-]?\d+", text) else float(text)


def fmt_num(x) -> str:
    """A number for a table: thousands separated, large floats to one decimal; None is "—"."""
    if isinstance(x, float):
        return f"{x:,.1f}" if abs(x) >= 1000 else f"{x:g}"
    return f"{x:,}" if isinstance(x, int) else ("—" if x is None else str(x))


def level_rank(level: str) -> int:
    return EVIDENCE_LEVELS.index(level) if level in EVIDENCE_LEVELS else -1


def severity_rank(severity: str) -> int:
    return SEVERITIES.index(severity) if severity in SEVERITIES else len(SEVERITIES)


def shorten(text: str, n: int = 220) -> str:
    """The text on one line, cut to n characters with "…"."""
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"
