"""Memory and compaction inside one agent run (plan §0.16): the hints retrieved at the start, the
two tools the loop answers itself (no MCP: they read this run's own trace and memory), and the
compaction settings.

    get_result(id)        the full raw result of an earlier check of this run, as the tool returned it
    search_memory(query)  more lessons / document sections; what it returns becomes citable

Both are logged in the trace ("fetch", "recall") and listed in the receipt; neither is a check:
their output is never evidence and the gate does not count it as a tool result.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from .models import ToolSpec

GET_RESULT = ToolSpec(
    "get_result",
    "Fetch the full raw result of an earlier tool call of THIS run by its id (r1, r2, ... as listed in "
    "the run-state table after the context was compacted). Returns exactly what the tool returned. Not a "
    "new check: it runs nothing.",
    {"type": "object", "properties": {"id": {"type": "string", "description": "result id, e.g. r3"}},
     "required": ["id"]})
SEARCH_MEMORY = ToolSpec(
    "search_memory",
    "Search this design's lessons from earlier runs and the project's documents (spec, manuals, PDK "
    "notes). Returns hints with ids (lesson-<design>-NNN, doc:<doc>#<section>) and sources. Hints are "
    "NOT evidence: conclusions still need tool results of this run. Only ids returned here or listed as "
    "hints at the start may be cited.",
    {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]})


MAX_PROPOSED = 3


def proposed_lessons(report: str) -> list[str]:
    """The items of the report's "Lessons" section (at most 3), as written."""
    from .gate.r10_memory import _sections
    _, section = _sections(report or "")
    items = []
    for line in section.splitlines():
        m = re.match(r"\s*(?:[-*]|\d+[.)])\s+(.*\S)", line)
        if m:
            items.append(m.group(1).strip())
    return items[:MAX_PROPOSED]


@dataclass
class Compaction:
    """When to compact; see core.memory.MemoryConfig for the project-level defaults."""
    context_window: int = 128000
    compact_at: float = 0.6
    after_turns: int = 0      # also every N turns since the last compaction (0: tokens only)
    keep_turns: int = 2

    def due(self, last_input_tokens: int, turns_since: int) -> str:
        if last_input_tokens >= self.compact_at * self.context_window:
            return f"input tokens {last_input_tokens} >= {self.compact_at:g} x {self.context_window}"
        if self.after_turns and turns_since >= self.after_turns:
            return f"{turns_since} turns since the last compaction (every {self.after_turns})"
        return ""


@dataclass
class RunMemory:
    """What the run was given from memory, and how to search for more."""
    hints: list[dict] = field(default_factory=list)          # Hit.to_dict() of the hints at start
    known: dict = field(default_factory=dict)                # core.memory.known_ids(): lessons, docs
    search: Callable[[str], list[dict]] | None = None        # query -> Hit.to_dict() list
    retrieved: list[str] = field(default_factory=list)       # ids given or returned so far, in order

    def __post_init__(self) -> None:
        for h in self.hints:
            self.note(h["id"])

    def note(self, ref: str) -> None:
        if ref not in self.retrieved:
            self.retrieved.append(ref)

    def gate_record(self) -> dict:
        """The pseudo-result the gate's R10 reads (no evidence_level: never counted as evidence)."""
        return {"tool": "memory", "retrieved": list(self.retrieved), "known": self.known}
