"""Any OpenAI-compatible Chat Completions endpoint (DeepSeek by default)."""
from __future__ import annotations

import json
import os

from .base import StateFn

from .base import Stop, ToolCall, ToolOutcome, ToolSpec, Turn

_STOP_MAP: dict[str, Stop] = {
    "tool_calls": "tool_use", "stop": "end", "length": "max_tokens", "content_filter": "refusal",
}


class OpenAICompatAdapter:
    """Any OpenAI-compatible Chat Completions endpoint; defaults to DeepSeek.

    DeepSeek in thinking mode returns `reasoning_content`, and with tools present it
    must be sent back on every later request or the API answers 400. Keeping the
    assistant message as returned (append-only) covers that.

    Compaction therefore replaces whole turns only: the system message stays, the task message
    becomes "task + run-state table", and the newest `keep_turns` turns (each assistant message with
    its reasoning_content and tool calls, then its tool messages) follow unchanged. No tool message
    is left without the assistant message that called it.
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
        self.task = ""
        self.replaced = 0  # turns replaced so far by compaction

    def start(self, system: str, tools: list[ToolSpec], task: str) -> None:
        self.tools = [{"type": "function", "function": {
            "name": t.name, "description": t.description, "parameters": t.input_schema}} for t in tools]
        self.messages = [{"role": "system", "content": system}, {"role": "user", "content": task}]
        self.task, self.replaced = task, 0

    async def step(self) -> Turn:
        resp = await self.client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=self.messages,
            # An empty tool list is sent as no tools: some endpoints reject tools=[].
            **({"tools": self.tools} if self.tools else {}),
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
            stop=_STOP_MAP.get(choice.finish_reason or "", "end"),
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

    def compact(self, state: StateFn, keep_turns: int) -> dict | None:
        turns = [i for i, m in enumerate(self.messages) if m.get("role") == "assistant"]
        if len(turns) <= keep_turns or self.messages[0].get("role") != "system":
            return None
        first, last = self.replaced + 1, self.replaced + len(turns) - keep_turns
        before, chars = len(self.messages), len(json.dumps(self.messages, default=str))
        task = {"role": "user", "content": f"{self.task}\n\n{state(first, last)}"}
        self.messages = [self.messages[0], task] + self.messages[turns[-keep_turns]:]
        self.replaced = last
        return {"replaced_turns": [first, last], "kept_turns": keep_turns, "messages_before": before,
                "messages_after": len(self.messages), "chars_before": chars,
                "chars_after": len(json.dumps(self.messages, default=str)),
                "method": "whole turns replaced: task message + run-state table; kept turns unchanged "
                          "(reasoning_content and tool calls included)"}
