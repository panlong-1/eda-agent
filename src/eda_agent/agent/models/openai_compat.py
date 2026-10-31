"""Any OpenAI-compatible Chat Completions endpoint (DeepSeek by default)."""
from __future__ import annotations

import json
import os

from .base import Stop, ToolCall, ToolOutcome, ToolSpec, Turn

_STOP_MAP: dict[str, Stop] = {
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
