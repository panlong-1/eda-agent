"""Plan §0.16 compaction: the run-state table is built by code, numbers never change, whole turns are
replaced in a way DeepSeek accepts, get_result reads the raw result back, and the receipt records it all."""
import json

from openai.types.chat import ChatCompletion

from eda_agent.agent.levels import assess
from eda_agent.agent.loop import Budget, run_agent
from eda_agent.agent.memory import Compaction, RunMemory
from eda_agent.agent.models import AnthropicAdapter, OpenAICompatAdapter, ScriptedAdapter, ToolCall, ToolSpec, Turn
from eda_agent.agent.receipt import build_receipt, render_markdown
from eda_agent.agent.runstate import key_numbers

TOOLS = [ToolSpec("regress", "run cases", {"type": "object", "properties": {"cases": {"type": "array"}}}),
         ToolSpec("synth", "synthesis", {"type": "object", "properties": {}})]
RISK = "\n\n### Residual risk\nPre-PnR estimate only; cases not listed were not run."
REPORT = ("regress: pass (E2), 3 passed. synth: pass (E4): WNS 0.725448 ns at the 10 ns clock under the SDC, "
          "pre-PnR estimate." + RISK)


def regress(case):
    return {"tool": "regress", "status": "pass", "evidence_level": "E2", "summary": f"1 passed, 0 failed ({case})",
            "metrics": {"passed": 1, "failed": 0, "passed_cases": [case]}}


SYNTH = {"tool": "synth", "status": "pass", "evidence_level": "E4", "summary": "timing met, WNS 0.725448 ns",
         "metrics": {"wns": 0.725448, "tns": 0.0, "clock_period": 10.0, "cells": 12345, "sdc_sha256": "ab" * 32,
                     "timing_met": True}}


async def execute(name, args):
    return json.dumps(regress(args["cases"][0]) if name == "regress" else SYNTH), False


def script():
    turns = [Turn(stop="tool_use", tool_calls=[ToolCall(f"c{i}", "regress", {"cases": [c]})],
                  usage={"input_tokens": 1000 * (i + 1), "output_tokens": 10})
             for i, c in enumerate(["bypass", "hazard", "flush"])]
    turns.append(Turn(stop="tool_use", tool_calls=[ToolCall("c9", "synth", {})], usage={"input_tokens": 5000}))
    turns.append(Turn(stop="tool_use", tool_calls=[ToolCall("g1", "get_result", {"id": "r4"})],
                      usage={"input_tokens": 900}))
    turns.append(Turn(stop="end", text=REPORT, usage={"input_tokens": 1200}))
    return turns


async def run(tmp_path, name, compaction):
    model = ScriptedAdapter(script())
    report = await run_agent(model, TOOLS, execute, "long task", "sys", Budget(), trace=tmp_path / f"{name}.jsonl",
                             compaction=compaction, memory=RunMemory())
    return model, report


async def test_compacted_and_uncompacted_runs_agree(tmp_path):
    plain_model, plain = await run(tmp_path, "plain", Compaction(context_window=10**9))
    model, rep = await run(tmp_path, "comp", Compaction(context_window=4000, compact_at=0.5, keep_turns=1))
    # same report, same gate decision, same checks and numbers
    assert rep.final_text == plain.final_text == REPORT
    assert rep.gate == plain.gate and rep.gate[-1]["passed"]
    assert [c["result"] for c in rep.checks] == [c["result"] for c in plain.checks]
    assert not plain.compactions and len(rep.compactions) >= 1
    first = rep.compactions[0]
    assert first["replaced_turns"][0] == 1 and first["reason"].startswith("input tokens")
    table = model.compactions[-1]
    assert "| r4 | synth |" in table and "wns=0.725448" in table and "clock_period=10.0" in table
    assert "get_result(id)" in table and "Budget used: turns" in table
    # get_result returned the raw text, logged as a fetch, not as a check
    assert rep.fetches[0] == {"tool": "get_result", "id": "r4", "turn": 5, "found": True}
    assert json.loads(model.received[-1][0].content) == SYNTH
    assert len(rep.checks) == 4
    kinds = [json.loads(x)["kind"] for x in (tmp_path / "comp.jsonl").read_text().splitlines()]
    assert "compact" in kinds and "fetch" in kinds and kinds.count("tool") == 4

    receipts = [build_receipt(r, run_id=n, task="long task", provider="x", project="torv", all_cases=[], hints=[])
                for n, r in (("plain", plain), ("comp", rep))]
    strip = lambda r: {k: v for k, v in r.items() if k not in ("run_id", "memory", "wall_s")}  # noqa: E731
    assert strip(receipts[0]) == strip(receipts[1])   # identical but for the memory section and wall time
    mem = receipts[1]["memory"]
    assert mem["compactions"] == rep.compactions and mem["fetches"][0]["id"] == "r4"
    texts = [i.text for i in assess(receipts[1])]
    assert any("compacted" in t for t in texts)
    md = render_markdown(receipts[1])
    assert "Context compactions" in md and "get_result fetches" in md


async def test_turn_count_trigger_and_unknown_result(tmp_path):
    model = ScriptedAdapter([
        Turn(stop="tool_use", tool_calls=[ToolCall("a", "regress", {"cases": ["x"]})]),
        Turn(stop="tool_use", tool_calls=[ToolCall("b", "regress", {"cases": ["y"]})]),
        Turn(stop="tool_use", tool_calls=[ToolCall("c", "get_result", {"id": "r9"})]),
        Turn(stop="end", text="regress: pass (E2), 1 passed." + RISK)])
    rep = await run_agent(model, TOOLS, execute, "t", "sys", compaction=Compaction(after_turns=2, keep_turns=1))
    assert [c["replaced_turns"] for c in rep.compactions] == [[1, 1]]
    assert "every 2" in rep.compactions[0]["reason"]
    assert rep.fetches[0]["found"] is False and model.received[-1][0].is_error
    assert rep.tool_calls == 2


def test_key_numbers_are_verbatim():
    assert key_numbers(SYNTH) == ("timing met, WNS 0.725448 ns; wns=0.725448; tns=0.0; clock_period=10.0; "
                                  "cells=12345")


# ---------------------------------------------------------------- DeepSeek: a fake client

def completion(message: dict, finish: str, prompt=100) -> ChatCompletion:
    return ChatCompletion.model_validate({
        "id": "x", "object": "chat.completion", "created": 0, "model": "deepseek-v4-pro",
        "choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", **message}}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": 20, "total_tokens": prompt + 20}})


def tool_turn(i, case, prompt):
    return completion({"content": "", "reasoning_content": f"think {i}", "tool_calls": [
        {"id": f"call_{i}", "type": "function",
         "function": {"name": "regress", "arguments": json.dumps({"cases": [case]})}}]}, "tool_calls", prompt)


class FakeClient:
    def __init__(self, responses):
        self.responses, self.requests = list(responses), []
        self.chat = self.completions = self

    async def create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs, default=str)))
        return self.responses.pop(0)


def valid_deepseek_history(messages: list[dict]) -> None:
    """Every tool message answers a tool call of the assistant message before it; every assistant
    message with tool calls carries its reasoning_content (DeepSeek returns 400 otherwise)."""
    assert messages[0]["role"] == "system" and messages[1]["role"] == "user"
    open_calls: set[str] = set()
    for m in messages[1:]:
        if m["role"] == "assistant":
            assert not open_calls, "tool calls left unanswered"
            if m.get("tool_calls"):
                assert m.get("reasoning_content"), "reasoning_content missing"
                open_calls = {c["id"] for c in m["tool_calls"]}
        elif m["role"] == "tool":
            assert m["tool_call_id"] in open_calls
            open_calls.discard(m["tool_call_id"])


async def test_deepseek_compaction_replaces_whole_turns(tmp_path):
    client = FakeClient([tool_turn(1, "bypass", 50000), tool_turn(2, "hazard", 90000), tool_turn(3, "flush", 95000),
                         completion({"content": "regress: pass (E2), 3 passed." + RISK, "reasoning_content": "r"},
                                    "stop")])
    model = OpenAICompatAdapter(client=client)
    rep = await run_agent(model, TOOLS, execute, "run three cases", "sys", trace=tmp_path / "t.jsonl",
                          compaction=Compaction(context_window=128000, compact_at=0.6, keep_turns=1))
    assert rep.status == "done"
    assert [c["replaced_turns"] for c in rep.compactions] == [[1, 1], [2, 2]]
    for req in client.requests:
        valid_deepseek_history(req["messages"])
    after = client.requests[2]["messages"]  # the request right after the first compaction
    assert after[1]["content"].startswith("run three cases\n\n## Run state (compaction 1")
    assert "| r1 | regress |" in after[1]["content"] and after[2]["reasoning_content"] == "think 2"
    assert len(after) == 4  # system, task+state, the kept turn (assistant + tool)
    last = client.requests[3]["messages"]
    assert "| r2 | regress |" in last[1]["content"] and last[2]["reasoning_content"] == "think 3"
    # the gate read every check, not the compacted context
    assert len(rep.checks) == 3 and rep.gate[-1]["passed"]


def test_anthropic_compaction_is_append_only():
    model = AnthropicAdapter(client=object())
    model.start("sys", TOOLS, "task")
    for i in range(3):
        model.messages.append({"role": "assistant", "content": [{"type": "tool_use", "id": f"t{i}"}]})
        model.messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}",
                                                             "content": "x"}]})
    before = json.dumps(model.messages[:-1])
    done = model.compact(lambda a, b: f"STATE {a}-{b}", 1)
    assert done["replaced_turns"] == [1, 2] and model.context_editing
    assert json.dumps(model.messages[:-1]) == before          # nothing already sent was edited
    assert model.messages[-1]["content"][-1] == {"type": "text", "text": "STATE 1-2"}
    assert model.compact(lambda a, b: "again", 1) is None  # nothing new to cover
