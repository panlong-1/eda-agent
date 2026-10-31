"""Claude through the Messages API."""
from __future__ import annotations

import anthropic

from .base import Stop, ToolCall, ToolOutcome, ToolSpec, Turn

_STOP_MAP: dict[str, Stop] = {
    "tool_use": "tool_use", "end_turn": "end", "stop_sequence": "end",
    "max_tokens": "max_tokens", "refusal": "refusal", "pause_turn": "pause",
}


class AnthropicAdapter:
    """Claude through the Messages API, with a hand-written (not SDK-run) tool loop."""

    def __init__(self, model: str = "claude-opus-5-5", effort: str = "high",
                 max_tokens: int = 16000, client: anthropic.AsyncAnthropic | None = None):
        self.name = model
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.client = client or anthropic.AsyncAnthropic()
        self.system = ""
        self.tools: list[dict] = []
        self.messages: list[dict] = []

    def start(self, system: str, tools: list[ToolSpec], task: str) -> None:
        self.system = system
        self.tools = [{"name": t.name, "description": t.description, "input_schema": t.input_schema}
                      for t in tools]
        self.messages = [{"role": "user", "content": task}]

    async def step(self) -> Turn:
        resp = await self.client.beta.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=self.system,
            **({"tools": self.tools} if self.tools else {}),
            messages=self.messages,
            output_config={"effort": self.effort},
            cache_control={"type": "ephemeral"},
            # On a safety decline, let the API re-run the request on a fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        # Append the full content (thinking and fallback blocks included), never just the text.
        self.messages.append({"role": "assistant", "content": resp.content})
        u = resp.usage
        return Turn(
            stop=_STOP_MAP.get(resp.stop_reason or "", "end"),
            text="".join(b.text for b in resp.content if b.type == "text"),
            tool_calls=[ToolCall(b.id, b.name, dict(b.input)) for b in resp.content if b.type == "tool_use"],
            usage={
                "input_tokens": u.input_tokens,
                "output_tokens": u.output_tokens,
                "cache_read_input_tokens": u.cache_read_input_tokens or 0,
                "cache_creation_input_tokens": u.cache_creation_input_tokens or 0,
            },
        )

    def add_tool_results(self, results: list[ToolOutcome]) -> None:
        # All results of one turn go back in a single user message.
        self.messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": r.call_id, "content": r.content, "is_error": r.is_error}
            for r in results
        ]})

    def add_user_message(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})
