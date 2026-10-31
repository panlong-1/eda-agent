"""synth + check_sdc + rtl_rules on the real TORV checkout through MCP, in the LibreLane image.

Skipped unless docker runs, the image is present, and TORV and the IHP PDK are on disk.
"""
import json
import shutil
import sys
from pathlib import Path

import pytest

from eda_agent.agent.loop import run_agent
from eda_agent.agent.mcp_client import mcp_tools
from eda_agent.agent.models import ScriptedAdapter, ToolCall, Turn
from eda_agent.agent.reviewer import load_trace, rules_review
from eda_agent.core.proc import docker_available, image_digest
from eda_agent.core.project import load_project
from eda_agent.tools.synth import _Setup

PROJECT = Path(__file__).parents[2] / "projects" / "torv.toml"


def _ready() -> str:
    project = load_project(PROJECT)
    if not project.root.is_dir():
        return "needs the TORV checkout"
    if shutil.which("docker") is None or not docker_available():
        return "needs a working docker"
    if image_digest(project.flow.image) == "unknown":
        return f"needs the image {project.flow.image}"
    if missing := _Setup(project).missing():
        return f"needs the PDK files, e.g. {missing[0]}"
    if shutil.which("verilator") is None:
        return "needs Verilator (rtl_rules)"
    return ""


pytestmark = [pytest.mark.eda, pytest.mark.skipif(bool(_ready()), reason=_ready() or "ready")]


async def test_synth_timing_estimate_and_gate_r5(tmp_path):
    script = [
        Turn(stop="tool_use", tool_calls=[ToolCall("s", "synth", {}), ToolCall("c", "check_sdc", {}),
                                          ToolCall("r", "rtl_rules", {"max_diagnostics": 5})]),
        Turn(stop="end", text="(replaced below)"),
    ]
    model = ScriptedAdapter(script)
    trace = tmp_path / "t.jsonl"
    async with mcp_tools(sys.executable, ["-m", "eda_agent.server.mcp_server", "--project", str(PROJECT)]) \
            as (tools, ex, _):
        assert {"synth", "check_sdc", "rtl_rules"} <= {t.name for t in tools}
        # Run the tools first, then write a report from their numbers, as a model would.
        report = await run_agent(model, tools, ex, "synthesize torv", "sys", trace=trace, gate=None)
    synth, check, rtl = (json.loads(r.content) for r in model.received[0])

    assert synth["evidence_level"] == "E4" and synth["status"] in ("pass", "fail"), synth["summary"]
    m = synth["metrics"]
    assert m["clock_port"] == "hclk" and m["clock_period"] == 10.0 and len(m["sdc_sha256"]) == 64
    assert set(m["corners"]) == {"typ", "slow", "fast"} and m["cells"] > 1000 and m["latches"] == 0
    assert synth["artifacts"][0]["path"].endswith("librelane/constraints.sdc")
    assert synth["artifacts"][0]["sha256"] == m["sdc_sha256"]
    assert synth["tool_versions"]["image"].startswith("ghcr.io/librelane/librelane@sha256:")
    assert "before placement and routing" in synth["claim_scope"]

    assert check["evidence_level"] == "E0" and check["metrics"]["sdc_sha256"] == m["sdc_sha256"]
    assert check["metrics"]["clocks"][0]["sources"] == ["hclk"]
    assert rtl["tool"] == "rtl_rules" and rtl["evidence_level"] == "E0"

    from eda_agent.agent.gate import check_report
    risk = "\n\nResidual risk: pre-PnR estimate; no parasitics, no post-route STA, no gate-level simulation."
    verdict = "meets 100 MHz" if m["timing_met"] else "does not meet 100 MHz"
    good = f"synth: {synth['status']} (E4) {verdict} under the SDC, setup WNS {m['wns']:+.3f} ns, pre-PnR." + risk
    assert check_report(good, [synth, check]).passed or not m["timing_met"]
    assert not check_report(f"Timing is met with WNS {m['wns'] + 1:.3f} ns per synth." + risk, [synth]).passed

    review = rules_review(load_trace(trace), rtl)
    assert review.reviewer == "rules" and review.verdict in ("accept", "accept_with_findings", "reject")
    assert report.tool_calls == 3
