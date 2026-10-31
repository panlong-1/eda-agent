import json

from eda_agent.agent.loop import Budget, run_agent
from eda_agent.agent.models import ScriptedAdapter, ToolCall, ToolSpec, Turn

TOOLS = [ToolSpec("lint", "lint the design", {"type": "object", "properties": {}})]
RISK = "\n\nResidual risk: lint says nothing about function; no simulation was run."
GOOD = "lint: pass (E0), 0 errors." + RISK


def call(i: int) -> Turn:
    return Turn(stop="tool_use", tool_calls=[ToolCall(f"c{i}", "lint", {})],
                usage={"input_tokens": 10, "output_tokens": 2})


async def test_tool_round_trip_and_usage(tmp_path):
    seen = []

    async def execute(name, args):
        seen.append(name)
        return json.dumps({"status": "pass", "evidence_level": "E0"}), False

    model = ScriptedAdapter([call(1), Turn(stop="end", text=GOOD, usage={"input_tokens": 5})])
    trace = tmp_path / "t.jsonl"
    report = await run_agent(model, TOOLS, execute, "lint it", "sys", trace=trace)

    assert report.stop_reason == "end" and report.final_text == GOOD and report.status == "done"
    assert (report.turns, report.tool_calls, report.tool_errors) == (2, 1, 0)
    assert report.usage == {"input_tokens": 15, "output_tokens": 2}
    assert seen == ["lint"] and model.received[0][0].call_id == "c1"
    kinds = [json.loads(line)["kind"] for line in trace.read_text().splitlines()]
    assert kinds == ["start", "turn", "tool", "turn", "gate", "end"]
    assert report.checks == [{"tool": "lint", "args": {}, "result": {"status": "pass", "evidence_level": "E0"}}]


async def test_retry_budget_stops_after_two_consecutive_errors():
    async def execute(name, args):
        return json.dumps({"status": "error", "error_class": "infra"}), False

    model = ScriptedAdapter([call(i) for i in range(5)])
    report = await run_agent(model, TOOLS, execute, "t", "s", Budget(max_consecutive_errors=2))
    assert report.stop_reason == "retry_budget" and report.halted_on == "lint"
    assert report.tool_calls == 2


async def test_fail_is_a_result_not_an_error():
    async def execute(name, args):
        return json.dumps({"status": "fail", "evidence_level": "E0"}), False

    model = ScriptedAdapter([call(1), call(2), call(3), Turn(stop="end", text="Lint ran three times." + RISK)])
    report = await run_agent(model, TOOLS, execute, "t", "s")
    assert report.stop_reason == "end" and report.tool_errors == 0


async def test_turn_budget():
    async def execute(name, args):
        return "{}", False

    model = ScriptedAdapter([call(i) for i in range(10)])
    report = await run_agent(model, TOOLS, execute, "t", "s", Budget(max_turns=3))
    assert report.stop_reason == "budget_turns" and report.turns == 3


async def lint_pass(name, args):
    return json.dumps({"status": "pass", "evidence_level": "E0", "metrics": {"errors": 0}}), False


OVERCLAIM = "lint: pass (E0). The design works correctly." + RISK


async def test_gate_rejection_leads_to_rewrite(tmp_path):
    model = ScriptedAdapter([call(1), Turn(stop="end", text=OVERCLAIM), Turn(stop="end", text=GOOD)])
    trace = tmp_path / "t.jsonl"
    report = await run_agent(model, TOOLS, lint_pass, "t", "s", trace=trace)
    assert (report.stop_reason, report.status) == ("end", "done")
    assert [g["passed"] for g in report.gate] == [False, True]
    assert len(model.user_messages) == 1 and "'works'" in model.user_messages[0]
    gates = [json.loads(x) for x in trace.read_text().splitlines() if json.loads(x)["kind"] == "gate"]
    assert [(g["attempt"], g["passed"]) for g in gates] == [(1, False), (2, True)]


async def test_gate_stops_after_two_rewrites():
    model = ScriptedAdapter([call(1)] + [Turn(stop="end", text=OVERCLAIM)] * 5)
    report = await run_agent(model, TOOLS, lint_pass, "t", "s")
    assert (report.stop_reason, report.status) == ("gate_rejected", "pending")
    assert len(report.gate) == 3 and len(model.user_messages) == 2
    assert "rewrite 2 of 2" in model.user_messages[-1]


async def test_fabricated_citation_without_tools_is_rejected():
    model = ScriptedAdapter([Turn(stop="end", text="regress: pass (E2), 5 passed." + RISK)] * 3)
    report = await run_agent(model, TOOLS, lint_pass, "t", "s", Budget(max_gate_rewrites=0))
    assert report.stop_reason == "gate_rejected" and report.tool_calls == 0
    assert any("E2" in r for r in report.gate[0]["reasons"])


async def test_gate_can_be_disabled():
    model = ScriptedAdapter([Turn(stop="end", text="done")])
    report = await run_agent(model, TOOLS, lint_pass, "t", "s", gate=None)
    assert (report.stop_reason, report.status, report.gate) == ("end", "done", [])


async def test_trace_keeps_the_whole_tool_result(tmp_path):
    import json as _json
    from eda_agent.agent.loop import TRACE_VERSION
    big = _json.dumps({"status": "pass", "evidence_level": "E0", "pad": "x" * 10000})

    async def execute(name, args):
        return big, False

    model = ScriptedAdapter([Turn(stop="tool_use", tool_calls=[ToolCall("a", "lint", {})]),
                             Turn(stop="end", text="lint: pass (E0).\n\nResidual risk: lint only, nothing about function.")])
    trace = tmp_path / "t.jsonl"
    await run_agent(model, [ToolSpec("lint", "lint", {"type": "object"})], execute, "lint", "sys", trace=trace)
    events = [_json.loads(line) for line in trace.read_text().splitlines()]
    assert events[0]["trace_version"] == TRACE_VERSION
    assert next(e for e in events if e["kind"] == "tool")["result"] == big
