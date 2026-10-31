"""Provider-neutral types shared by the loop and every model adapter.

The loop never touches a provider's message format. Each adapter keeps its own
native history (append-only, so provider-side features such as preserved
thinking stay valid) and hands the loop provider-neutral turns. Swapping the
model, e.g. for an independent reviewer on a different provider (level I3),
means writing one more adapter.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

Stop = Literal["tool_use", "end", "max_tokens", "refusal", "pause"]


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class ToolOutcome:
    call_id: str
    content: str
    is_error: bool = False


@dataclass
class Turn:
    stop: Stop
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict = field(default_factory=dict)


class ModelAdapter(Protocol):
    name: str

    def start(self, system: str, tools: list[ToolSpec], task: str) -> None: ...
    async def step(self) -> Turn: ...
    def add_tool_results(self, results: list[ToolOutcome]) -> None: ...
    def add_user_message(self, text: str) -> None: ...
