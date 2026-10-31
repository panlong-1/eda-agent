"""Model adapters.

The loop never touches a provider's message format. Each adapter keeps its own
native history (append-only, so provider-side features such as preserved
thinking stay valid) and hands the loop provider-neutral turns. Swapping the
model, e.g. for an independent reviewer on a different provider (level I3),
means writing one more adapter.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Literal, Protocol

import anthropic

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
            tools=self.tools,
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


_OPENAI_STOP_MAP: dict[str, Stop] = {
    "tool_calls": "tool_use", "stop": "end", "length": "max_tokens", "content_filter": "refusal",
}


class OpenAICompatAdapter:
    """Any OpenAI-compatible Chat Completions endpoint; defaults to DeepSeek.

    DeepSeek in thinking mode returns `reasoning_content`, and with tools present it
    must be sent back on every later request or the API answers 400. Keeping the
    assistant message as returned (append-only) covers that.
    """

    def __init__(self, model: str = "deepseek-v4-pro", effort: str = "high",
                 max_tokens: int = 16000, base_url: str = "https://api.deepseek.com",
                 api_key_env: str = "DEEPSEEK_API_KEY", thinking: bool = True, client=None):
        self.name = model
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.thinking = thinking
        if client is None:
            import openai
            client = openai.AsyncOpenAI(base_url=base_url, api_key=os.environ.get(api_key_env))
        self.client = client
        self.tools: list[dict] = []
        self.messages: list[dict] = []

    def start(self, system: str, tools: list[ToolSpec], task: str) -> None:
        self.tools = [{"type": "function", "function": {
            "name": t.name, "description": t.description, "parameters": t.input_schema}} for t in tools]
        self.messages = [{"role": "system", "content": system}, {"role": "user", "content": task}]

    async def step(self) -> Turn:
        resp = await self.client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=self.messages,
            tools=self.tools,
            reasoning_effort=self.effort,
            extra_body={"thinking": {"type": "enabled" if self.thinking else "disabled"}},
        )
        choice = resp.choices[0]
        msg = choice.message
        # exclude_none drops the null fields some endpoints reject when echoed back.
        self.messages.append(msg.model_dump(exclude_none=True))
        calls = []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                # Pass the raw text through; the server's schema check turns it into a tool error.
                args = {"_unparsed_arguments": tc.function.arguments}
            calls.append(ToolCall(tc.id, tc.function.name, args))
        u = resp.usage
        return Turn(
            stop=_OPENAI_STOP_MAP.get(choice.finish_reason or "", "end"),
            text=msg.content or "",
            tool_calls=calls,
            usage={
                "input_tokens": u.prompt_tokens,
                "output_tokens": u.completion_tokens,
                # DeepSeek reports context-cache hits under its own field name.
                "cache_read_input_tokens": getattr(u, "prompt_cache_hit_tokens", 0) or 0,
                "cache_creation_input_tokens": 0,
            },
        )

    def add_tool_results(self, results: list[ToolOutcome]) -> None:
        # Chat Completions has no is_error flag; mark it in the content so the model sees it.
        for r in results:
            content = f"[tool error] {r.content}" if r.is_error else r.content
            self.messages.append({"role": "tool", "tool_call_id": r.call_id, "content": content})

    def add_user_message(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})


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
