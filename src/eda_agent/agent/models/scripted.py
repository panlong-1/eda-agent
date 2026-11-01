"""A model stand-in that replays prepared turns."""
from __future__ import annotations

from .base import StateFn, ToolOutcome, ToolSpec, Turn


class ScriptedAdapter:
    """Deterministic stand-in for a model: replays prepared turns. For tests and dry runs."""

    def __init__(self, turns: list[Turn], name: str = "scripted"):
        self.name = name
        self._turns = list(turns)
        self.received: list[list[ToolOutcome]] = []
        self.user_messages: list[str] = []
        self.task = ""
        self.turns_seen = 0
        self.compactions: list[str] = []  # the run-state tables it was given
        self.replaced = 0

    def start(self, system: str, tools: list[ToolSpec], task: str) -> None:
        self.task = task

    async def step(self) -> Turn:
        self.turns_seen += 1
        if not self._turns:
            return Turn(stop="end", text="(script exhausted)")
        return self._turns.pop(0)

    def add_tool_results(self, results: list[ToolOutcome]) -> None:
        self.received.append(results)

    def add_user_message(self, text: str) -> None:
        self.user_messages.append(text)

    def compact(self, state: StateFn, keep_turns: int) -> dict | None:
        """Records the table; the replayed turns are not affected (so a compacted and an uncompacted
        replay of the same script must end with the same report)."""
        if self.turns_seen <= keep_turns or self.turns_seen - keep_turns <= self.replaced:
            return None
        first, last = self.replaced + 1, self.turns_seen - keep_turns
        self.compactions.append(state(first, last))
        self.replaced = last
        return {"replaced_turns": [first, last], "kept_turns": keep_turns, "method": "scripted"}
