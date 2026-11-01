"""R4 skipped: a case the agent described but did not run is named in the residual-risk section."""
from __future__ import annotations

import re

from .common import described_cases, executed_cases


def check_skipped(residual: str, results: list[dict]) -> list[str]:
    return [f"described case '{name}' but neither ran it nor named it under residual risk: run it, "
            "or say there why it was left out"
            for name in sorted(described_cases(results) - executed_cases(results))
            if not re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", residual)]
