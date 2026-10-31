"""Model adapters: one file per provider, all speaking the types in base.py."""
from .anthropic import AnthropicAdapter
from .base import ModelAdapter, Stop, ToolCall, ToolOutcome, ToolSpec, Turn
from .openai_compat import OpenAICompatAdapter
from .scripted import ScriptedAdapter

__all__ = ["AnthropicAdapter", "ModelAdapter", "OpenAICompatAdapter", "ScriptedAdapter", "Stop",
           "ToolCall", "ToolOutcome", "ToolSpec", "Turn"]
