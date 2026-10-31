"""A model stand-in that replays prepared turns."""
from __future__ import annotations

from .base import ToolOutcome, ToolSpec, Turn


class ScriptedAdapter:
    """Deterministic stand-in for a model: replays prepared turns. For tests and dry runs."""

    def __init__(self, turns: list[Turn], name: str = "scripted"):
        self.name = name
        self._turns = list(turns)
        self.received: list[list[ToolOutcome]] = []
        self.user_messages: list[str] = []
        self.task = ""

    def start(self, system: str, tools: list[ToolSpec], task: str) -> None:
        self.task = task

    async def step(self) -> Turn:
        if not self._turns:
            return Turn(stop="end", text="(script exhausted)")
        return self._turns.pop(0)

    def add_tool_results(self, results: list[ToolOutcome]) -> None:
        self.received.append(results)

    def add_user_message(self, text: str) -> None:
        self.user_messages.append(text)
