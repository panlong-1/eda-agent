"""Moved to agent/receipt/narrative.py (and agent/model_output.py); this module only re-exports."""
from .model_output import results_from_receipt as gate_results  # noqa: F401
from .receipt.narrative import PROMPT, model_view, write_narrative  # noqa: F401
