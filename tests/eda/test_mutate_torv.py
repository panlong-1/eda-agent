"""mutate against the real TORV checkout: mutants judged in a copy, and the equivalence check."""
import json
import shutil
import sys
from pathlib import Path

import pytest

from eda_agent.agent.loop import run_agent
from eda_agent.agent.mcp_client import mcp_tools
from eda_agent.agent.models import ScriptedAdapter, ToolCall, Turn
from eda_agent.core.proc import docker_available
from eda_agent.core.project import load_project
from eda_agent.tools import mutate as M

PROJECT = Path(__file__).parents[2] / "projects" / "torv.toml"
FILE = "rtl/torv_forwarding_unit.sv"
pytestmark = [
    pytest.mark.eda,
    pytest.mark.skipif(shutil.which("verilator") is None or not load_project(PROJECT).root.is_dir()
                       or M._avl_command() is None, reason="needs Verilator, the TORV checkout and avl-core"),
]


def test_forwarding_mutants_judged_in_a_copy(tmp_path):
    project = load_project(PROJECT)
    res = M.run_mutate(project, FILE, count=2, types=["condition"], cases=["bypass"], jobs=2,
                       equivalence=False, work_base=tmp_path)
    m = res.metrics
    assert res.status in ("pass", "fail"), res.summary
    assert m["root_unchanged"] and m["invalid"] == 0 and m["error"] == 0
    # Forcing a forwarding condition to a constant breaks bypass: at least one is caught.
    assert m["killed"] >= 1 and all(x["killed_by"] == ["bypass"] for x in m["mutant_list"] if x["status"] == "killed")
    assert res.tool_versions["avl-core"] == "1.2.0" and "verilator" in res.tool_versions


async def test_mutate_over_mcp():
    """The agent's path: the MCP server runs one mutant and returns the envelope."""
    script = [
        Turn(stop="tool_use", tool_calls=[ToolCall("m", "mutate", {
            "file": FILE, "count": 1, "types": ["condition"], "cases": ["bypass"]})]),
        Turn(stop="end", text="mutate (E0): one mutant judged.\n\nResidual risk: one mutant only."),
    ]
    model = ScriptedAdapter(script)
    async with mcp_tools(sys.executable, ["-m", "eda_agent.server.mcp_server", "--project", str(PROJECT)]) as (tools, ex, _):
        assert "mutate" in {t.name for t in tools}
        await run_agent(model, tools, ex, "find test gaps in the forwarding unit", "sys")
    (res,) = (json.loads(r.content) for r in model.received[0])
    assert res["tool"] == "mutate" and res["evidence_level"] == "E0"
    assert res["metrics"]["mutants"] == 1 and res["metrics"]["root_unchanged"]


@pytest.mark.skipif(not docker_available() or load_project(PROJECT).flow is None, reason="needs Docker and [flow]")
def test_equivalence_check_on_forwarding(tmp_path):
    project = load_project(PROJECT)
    verdicts = {}
    for kind in ("array_packed", "condition"):
        work = tmp_path / kind
        work.mkdir()
        (mutant,) = M.generate(project, FILE, 1, (kind,), 1, work)
        verdicts[kind] = M.check_equivalence(project, FILE, mutant, work, cycles=4)
    # Turning a port's bit numbering around keeps every value; forcing a branch does not.
    assert verdicts == {"array_packed": "equivalent", "condition": "different"}
