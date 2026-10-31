"""The second design, PicoRV32, through the same MCP tools: only projects/picorv32.toml is design-specific."""
import json
import shutil
import sys
from pathlib import Path

import pytest

from eda_agent.agent.loop import run_agent
from eda_agent.agent.mcp_client import mcp_tools
from eda_agent.agent.models import ScriptedAdapter, ToolCall, Turn
from eda_agent.core.project import load_project

PROJECT = Path(__file__).parents[2] / "projects" / "picorv32.toml"
pytestmark = [
    pytest.mark.eda,
    pytest.mark.skipif(
        any(shutil.which(t) is None for t in ("verilator", "make", "riscv64-unknown-elf-gcc"))
        or not load_project(PROJECT).root.is_dir(),
        reason="needs Verilator, make, riscv64-unknown-elf-gcc and the PicoRV32 checkout"),
]


async def test_picorv32_lint_describe_regress():
    script = [
        Turn(stop="tool_use", tool_calls=[ToolCall("a", "lint", {}),
                                          ToolCall("b", "describe_case", {"cases": ["firmware"]})]),
        Turn(stop="tool_use", tool_calls=[ToolCall("c", "regress", {"cases": ["firmware"]})]),
        Turn(stop="end", text="lint: pass (E0). firmware: pass (E2), 1 passed.\n\n"
                              "Residual risk: one firmware image; exit status and output pattern only."),
    ]
    model = ScriptedAdapter(script)
    async with mcp_tools(sys.executable, ["-m", "eda_agent.server.mcp_server", "--project", str(PROJECT)]) as (tools, ex):
        report = await run_agent(model, tools, ex, "check picorv32", "sys")

    lint, described = (json.loads(r.content) for r in model.received[0])
    assert lint["status"] == "pass" and lint["evidence_level"] == "E0" and lint["metrics"]["errors"] == 0
    assert "riscv-tests" in described["cases"][0]["description"]
    regress = json.loads(model.received[1][0].content)
    assert regress["status"] == "pass", regress["summary"] + str(regress["diagnostics"])
    assert regress["metrics"]["format"] == "exitcode" and "no per-check detail" in regress["claim_scope"]
    assert regress["artifacts"][0]["sha256"]
    assert report.stop_reason == "end" and report.status == "done"
