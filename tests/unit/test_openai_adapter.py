import json

from openai.types.chat import ChatCompletion

from eda_agent.agent.loop import run_agent
from eda_agent.agent.models import OpenAICompatAdapter, ToolSpec

TOOLS = [ToolSpec("lint", "lint the design", {"type": "object", "properties": {}})]
REPORT = "lint pass (E0)\n\nResidual risk: lint says nothing about function."


def completion(message: dict, finish: str) -> ChatCompletion:
    return ChatCompletion.model_validate({
        "id": "x", "object": "chat.completion", "created": 0, "model": "deepseek-v4-pro",
        "choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", **message}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
                  "prompt_cache_hit_tokens": 64},
    })


class FakeClient:
    """Records requests and replays canned DeepSeek-style responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.chat = self
        self.completions = self

    async def create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs)))  # snapshot, history keeps growing
        return self.responses.pop(0)


async def test_deepseek_round_trip_keeps_reasoning(tmp_path):
    client = FakeClient([
        completion({"content": "", "reasoning_content": "need lint first", "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "lint", "arguments": "{}"}}]},
            "tool_calls"),
        completion({"content": "lint pass (E0)", "reasoning_content": "done"}, "stop"),
        completion({"content": REPORT, "reasoning_content": "add the risk"}, "stop"),
    ])
    model = OpenAICompatAdapter(client=client)

    async def execute(name, args):
        return json.dumps({"status": "pass", "evidence_level": "E0"}), False

    report = await run_agent(model, TOOLS, execute, "lint it", "sys", trace=tmp_path / "t.jsonl")

    assert report.stop_reason == "end" and report.final_text == REPORT
    assert report.usage["cache_read_input_tokens"] == 192
    # The gate rejected the first report (no residual risk); the rejection goes back as a user message.
    third = client.requests[2]["messages"]
    assert third[-2]["role"] == "assistant" and third[-2]["reasoning_content"] == "done"
    assert third[-1]["role"] == "user" and "Evidence gate rejected" in third[-1]["content"]
    second = client.requests[1]
    # The second request must carry the first turn's reasoning_content back (DeepSeek returns 400 otherwise).
    assistant = second["messages"][2]
    assert assistant["reasoning_content"] == "need lint first"
    assert assistant["tool_calls"][0]["id"] == "call_1"
    assert second["messages"][3] == {"role": "tool", "tool_call_id": "call_1",
                                     "content": json.dumps({"status": "pass", "evidence_level": "E0"})}
    assert second["tools"][0]["function"]["name"] == "lint"
    assert second["extra_body"] == {"thinking": {"type": "enabled"}}


async def test_bad_arguments_and_tool_error_are_visible():
    client = FakeClient([completion({"content": None, "tool_calls": [
        {"id": "c", "type": "function", "function": {"name": "lint", "arguments": "{oops"}}]}, "tool_calls")])
    model = OpenAICompatAdapter(client=client)
    model.start("sys", TOOLS, "task")
    turn = await model.step()
    assert turn.stop == "tool_use" and turn.text == ""
    assert turn.tool_calls[0].input == {"_unparsed_arguments": "{oops"}

    from eda_agent.agent.models import ToolOutcome
    model.add_tool_results([ToolOutcome("c", "bad args", is_error=True)])
    assert model.messages[-1]["content"] == "[tool error] bad args"
