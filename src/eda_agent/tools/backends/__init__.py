"""Back-end flow adapters. LibreLane is registered by default; see base.py for the contract."""
from .base import Backend, empty_facts, get_backend, register_backend
from .librelane import LibreLane

register_backend(LibreLane())

__all__ = ["Backend", "empty_facts", "get_backend", "register_backend", "LibreLane"]
