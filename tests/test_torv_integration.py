"""End-to-end against the real TORV checkout: MCP server + tools + loop (scripted model)."""
import json
import shutil
import sys
from pathlib import Path

import pytest

from eda_agent.agent.loop import run_agent
from eda_agent.agent.mcp_client import mcp_tools
from eda_agent.agent.model import ScriptedAdapter, ToolCall, Turn
from eda_agent.projects import load_project

PROJECT = Path(__file__).parent.parent / "projects" / "torv.toml"
pytestmark = [
    pytest.mark.eda,
    pytest.mark.skipif(shutil.which("verilator") is None or not load_project(PROJECT).root.is_dir(),
                       reason="needs Verilator and the TORV checkout"),
]


async def test_mcp_lint_and_regress_round_trip():
    script = [
        Turn(stop="tool_use", tool_calls=[ToolCall("a", "lint", {"max_diagnostics": 3}),
                                          ToolCall("d", "describe_case", {"cases": ["muldiv_stall"]})]),
        Turn(stop="tool_use", tool_calls=[ToolCall("b", "regress", {"cases": ["smoke"]}),
                                          ToolCall("c", "regress", {"cases": ["no_such_case"]})]),
        Turn(stop="end", text="lint: pass (E0). smoke: pass (E2), 1 passed.\n\n"
                              "Residual risk: one directed case; muldiv_stall was described but not run; "
                              "no coverage or mutation score."),
    ]
    model = ScriptedAdapter(script)
    async with mcp_tools(sys.executable, ["-m", "eda_agent.mcp_server", "--project", str(PROJECT)]) as (tools, ex):
        assert {t.name for t in tools} == {"lint", "list_regress_cases", "describe_case", "regress"}
        report = await run_agent(model, tools, ex, "check torv", "sys")

    assert report.stop_reason == "end" and report.status == "done" and report.tool_calls == 4
    lint, described = (json.loads(r.content) for r in model.received[0])
    assert "forwarding" in described["cases"][0]["description"]
    assert lint["status"] == "pass" and lint["evidence_level"] == "E0"
    assert lint["metrics"]["errors"] == 0 and len(lint["diagnostics"]) <= 3

    smoke, bogus = (json.loads(r.content) for r in model.received[1])
    assert smoke["status"] == "pass" and smoke["metrics"]["passed"] == 1
    assert smoke["evidence_level"] == "E2" and smoke["artifacts"][0]["sha256"]
    assert bogus["status"] == "error" and bogus["error_class"] == "bad_request"
