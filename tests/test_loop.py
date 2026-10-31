import json

from eda_agent.agent.loop import Budget, run_agent
from eda_agent.agent.model import ScriptedAdapter, ToolCall, ToolSpec, Turn

TOOLS = [ToolSpec("lint", "lint the design", {"type": "object", "properties": {}})]


def call(i: int) -> Turn:
    return Turn(stop="tool_use", tool_calls=[ToolCall(f"c{i}", "lint", {})],
                usage={"input_tokens": 10, "output_tokens": 2})


async def test_tool_round_trip_and_usage(tmp_path):
    seen = []

    async def execute(name, args):
        seen.append(name)
        return json.dumps({"status": "pass", "evidence_level": "E0"}), False

    model = ScriptedAdapter([call(1), Turn(stop="end", text="done", usage={"input_tokens": 5})])
    trace = tmp_path / "t.jsonl"
    report = await run_agent(model, TOOLS, execute, "lint it", "sys", trace=trace)

    assert report.stop_reason == "end" and report.final_text == "done"
    assert (report.turns, report.tool_calls, report.tool_errors) == (2, 1, 0)
    assert report.usage == {"input_tokens": 15, "output_tokens": 2}
    assert seen == ["lint"] and model.received[0][0].call_id == "c1"
    kinds = [json.loads(line)["kind"] for line in trace.read_text().splitlines()]
    assert kinds == ["start", "turn", "tool", "turn", "end"]


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

    model = ScriptedAdapter([call(1), call(2), call(3), Turn(stop="end", text="ok")])
    report = await run_agent(model, TOOLS, execute, "t", "s")
    assert report.stop_reason == "end" and report.tool_errors == 0


async def test_turn_budget():
    async def execute(name, args):
        return "{}", False

    model = ScriptedAdapter([call(i) for i in range(10)])
    report = await run_agent(model, TOOLS, execute, "t", "s", Budget(max_turns=3))
    assert report.stop_reason == "budget_turns" and report.turns == 3
