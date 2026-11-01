"""Claude through the Messages API."""
from __future__ import annotations

import anthropic

from .base import StateFn, Stop, ToolCall, ToolOutcome, ToolSpec, Turn

# Server-side context editing: the API clears old tool results itself, so the history the
# client sends stays append-only (preserved thinking rejects edited history).
CONTEXT_EDITING_BETA = "context-management-2025-06-27"

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
        self.replaced = 0               # turns covered by a run-state table so far
        self.context_editing = False    # on after the first compaction

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
            betas=["server-side-fallback-2026-07-01"] + ([CONTEXT_EDITING_BETA] if self.context_editing else []),
            fallbacks="default",
            **({"context_management": {"edits": [{"type": "clear_tool_uses_20250919"}]}}
               if self.context_editing else {}),
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

    def compact(self, state: StateFn, keep_turns: int) -> dict | None:
        """Append-only, as preserved thinking requires: the run-state table goes in as a text block
        after the tool results not yet sent, and from now on the API's context editing
        (clear_tool_uses_20250919) clears old tool results server-side. Earlier turns are not
        rewritten by the client. Not tested against the live API (no Anthropic key yet)."""
        turns = sum(m["role"] == "assistant" for m in self.messages)
        last_msg = self.messages[-1] if self.messages else {}
        if turns <= keep_turns or last_msg.get("role") != "user" or not isinstance(last_msg.get("content"), list):
            return None
        first, last = self.replaced + 1, turns - keep_turns
        if last < first:
            return None
        last_msg["content"].append({"type": "text", "text": state(first, last)})
        self.replaced, self.context_editing = last, True
        return {"replaced_turns": [first, last], "kept_turns": keep_turns,
                "method": "append-only: run-state table after the tool results; old tool results cleared by "
                          "the API's context editing (clear_tool_uses_20250919), history not rewritten"}
