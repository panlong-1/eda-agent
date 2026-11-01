import json

from eda_agent.agent.loop import run_agent
from eda_agent.agent.models import ScriptedAdapter, ToolCall, ToolSpec, Turn
from eda_agent.agent.receipt import build_receipt, write_receipt

TOOLS = [ToolSpec(n, n, {"type": "object", "properties": {}}) for n in ("describe_case", "regress")]
REGRESS = {"tool": "regress", "status": "pass", "evidence_level": "E2", "summary": "2 passed, 0 failed",
           "claim_scope": "these cases passed", "residual_risk": ["test adequacy unknown"],
           "metrics": {"passed": 2, "failed": 0, "passed_cases": ["bypass", "hazard"], "failed_cases": []},
           "artifacts": [{"path": "/r/summary.rpt", "sha256": "ab" * 32}]}
REPORT = ("regress bypass, hazard: pass (E2), 2 passed.\n\n"
          "Residual risk: muldiv_stall was described but not run; no coverage data.")


async def execute(name, args):
    if name == "describe_case":
        return json.dumps({"tool": "describe_case", "status": "ok",
                           "cases": [{"name": n} for n in args["cases"]]}), False
    return json.dumps(REGRESS), False


async def test_receipt_records_checks_gaps_and_gate(tmp_path):
    model = ScriptedAdapter([
        Turn(stop="tool_use", tool_calls=[ToolCall("a", "describe_case", {"cases": ["bypass", "hazard",
                                                                                    "muldiv_stall"]})]),
        Turn(stop="tool_use", tool_calls=[ToolCall("b", "regress", {"cases": ["bypass", "hazard"]})],
             usage={"input_tokens": 100, "output_tokens": 10}),
        Turn(stop="end", text=REPORT),
    ])
    report = await run_agent(model, TOOLS, execute, "run the hazard cases", "sys")
    receipt = build_receipt(report, run_id="r1", task="run the hazard cases", provider="scripted",
                            project="torv", all_cases=["smoke", "bypass", "hazard", "muldiv_stall"])

    assert (receipt["stop_reason"], receipt["status"], receipt["gate"]["status"]) == ("end", "done", "passed")
    (check,) = receipt["checks_run"]
    assert check.pop("duration_s") >= 0
    assert check.pop("result_id") == "r2"
    assert check == {"tool": "regress", "args": {"cases": ["bypass", "hazard"]}, "status": "pass",
                     "evidence_level": "E2", "summary": "2 passed, 0 failed",
                     "claim_scope": "these cases passed", "tool_versions": {},
                     "artifacts": [{"path": "/r/summary.rpt", "sha256": "ab" * 32}],
                     "metrics": REGRESS["metrics"], "diagnostics": [], "diagnostics_total": 0}
    assert receipt["other_tool_calls"][0]["tool"] == "describe_case"
    assert receipt["not_run"] == {"described_but_not_run": ["muldiv_stall"],
                                  "cases_not_run": ["smoke", "muldiv_stall"], "lint_run": False}
    assert receipt["final_claims"].startswith("regress bypass") and "Residual" not in receipt["final_claims"]
    assert "muldiv_stall was described" in receipt["residual_risk"]["report"]
    assert receipt["residual_risk"]["from_tools"] == ["test adequacy unknown"]
    assert receipt["usage"]["input_tokens"] == 100 and receipt["wall_s"] >= 0

    js, md = write_receipt(receipt, tmp_path)
    assert js.name == "r1.receipt.json" and md.name == "r1.receipt.md"
    assert json.loads(js.read_text()) == receipt
    text = md.read_text()
    assert "**Gate:** passed" in text and "| regress |" in text and "described but not run: muldiv_stall" in text


async def test_receipt_of_gate_rejected_run(tmp_path):
    model = ScriptedAdapter([Turn(stop="end", text="Everything works.")] * 3)
    report = await run_agent(model, TOOLS, execute, "t", "s")
    receipt = build_receipt(report, run_id="r2", task="t", provider="scripted", project="p", all_cases=["a"])
    assert (receipt["status"], receipt["gate"]["status"], receipt["gate"]["rewrites"]) == ("pending", "rejected", 2)
    assert receipt["checks_run"] == [] and receipt["not_run"]["cases_not_run"] == ["a"]
    assert "reject" in write_receipt(receipt, tmp_path)[1].read_text()


def test_markdown_table_survives_pipe_in_args():
    from eda_agent.agent.receipt import render_details
    receipt = {"run_id": "r", "task": "t", "project": "p", "provider": "x", "model": "m", "prompt": "",
               "stop_reason": "end", "status": "done", "turns": 1, "tool_calls": 1, "tool_errors": 0,
               "usage": {}, "wall_s": 0, "trace": "",
               "gate": {"status": "passed", "rewrites": 0, "decisions": []},
               "checks_run": [{"tool": "regress", "args": {"filter": "a|b"}, "status": "pass",
                               "evidence_level": "E2", "summary": "x | y", "claim_scope": "", "artifacts": []}],
               "not_run": {"lint_run": False, "described_but_not_run": [], "cases_not_run": []},
               "final_claims": "", "residual_risk": {"report": "", "from_tools": []}}
    row = next(line for line in render_details(receipt).splitlines() if line.startswith("| regress"))
    assert row.count("|") - row.count("\\|") == 6  # five cells, unchanged by the '|' inside them
