import json

import pytest

from eda_agent.agent.loop import run_agent
from eda_agent.agent.models import ScriptedAdapter, ToolCall, ToolSpec, Turn
from eda_agent.agent.reviewer import build_context, load_trace, model_review, parse_review, rules_review

TOOLS = [ToolSpec(n, n, {"type": "object", "properties": {}}) for n in ("lint", "synth", "rtl_rules")]
LINT = {"tool": "lint", "status": "pass", "evidence_level": "E0", "summary": "0 error(s)", "metrics": {"errors": 0}}
SYNTH = {"tool": "synth", "status": "fail", "evidence_level": "E4", "summary": "timing NOT met",
         "metrics": {"sdc_sha256": "c" * 64, "wns": -0.4, "hold_wns": 0.1, "timing_met": False,
                     "worst_corner": "slow", "latches": 2}}
AUTHOR_REPORT = "lint: pass (E0). AUTHOR-CONCLUSION: all good.\n\nResidual risk: no simulation was run at all."


async def execute(name, args):
    return json.dumps({"lint": LINT, "synth": SYNTH}.get(name, {"status": "error"})), False


async def author_trace(tmp_path):
    model = ScriptedAdapter([
        Turn(stop="tool_use", text="AUTHOR-THINKING: I will lint", tool_calls=[ToolCall("a", "lint", {})]),
        Turn(stop="tool_use", tool_calls=[ToolCall("b", "synth", {})]),
        Turn(stop="end", text=AUTHOR_REPORT),
    ], name="author-model")
    trace = tmp_path / "r.jsonl"
    await run_agent(model, TOOLS, execute, "Lint and synthesize the core", "sys", trace=trace, gate=None)
    return load_trace(trace)


async def test_context_per_level_hides_what_it_should(tmp_path):
    run = await author_trace(tmp_path)
    assert run.task == "Lint and synthesize the core" and run.model == "author-model"
    i0, i1, i2 = (build_context(level, run) for level in ("I0", "I1", "I2"))
    assert "AUTHOR-THINKING" in i0 and "AUTHOR-CONCLUSION" in i0 and '"wns": -0.4' in i0
    assert "AUTHOR-CONCLUSION" in i1 and "Lint and synthesize" not in i1 and "wns" not in i1
    assert "Lint and synthesize" in i2 and '"wns": -0.4' in i2
    assert "AUTHOR-THINKING" not in i2 and "AUTHOR-CONCLUSION" not in i2
    assert build_context("I3", run) == i2


CHECKLIST = "## Checklist\n- lint ran without errors\n- timing met under the SDC on all corners\n"
FINDINGS = ("## Findings\n- [high] synth timing not met: WNS -0.4 at slow — evidence: synth\n"
            "- [medium] 2 latches inferred — evidence: synth metrics\n\n## Verdict\nreject\n")


def test_parse_review():
    checklist, findings, verdict, ok = parse_review(CHECKLIST, FINDINGS)
    assert checklist == ["lint ran without errors", "timing met under the SDC on all corners"]
    assert [f["severity"] for f in findings] == ["high", "medium"] and verdict == "reject" and ok
    # findings already in the checklist turn break "checklist first"
    assert not parse_review(CHECKLIST + FINDINGS, FINDINGS)[3]
    assert not parse_review(CHECKLIST, "## Findings\n- [low] x\n")[3]  # no verdict


async def test_model_review_i2_writes_checklist_then_may_rerun_tools(tmp_path):
    run = await author_trace(tmp_path)
    reviewer = ScriptedAdapter([
        Turn(stop="end", text=CHECKLIST),
        Turn(stop="tool_use", tool_calls=[ToolCall("r1", "synth", {})]),
        Turn(stop="end", text=FINDINGS),
    ], name="reviewer-model")
    review = await model_review(reviewer, "I2", run, TOOLS, execute, "reviewer system",
                                provider="scripted", author_model="author-model", trace=tmp_path / "rv.jsonl")
    assert review.reviewer == "I2" and review.verdict == "reject" and review.format_ok
    assert review.checklist[0] == "lint ran without errors" and len(review.findings) == 2
    assert [c["tool"] for c in review.tool_calls] == ["synth"]
    assert "AUTHOR-CONCLUSION" not in reviewer.task  # the first message is the I2 context
    assert len(reviewer.user_messages) == 1 and "Step 2 of 2" in reviewer.user_messages[0]
    assert "differs from the author's" in review.note


async def test_tool_call_before_the_checklist_breaks_the_format(tmp_path):
    run = await author_trace(tmp_path)
    reviewer = ScriptedAdapter([Turn(stop="tool_use", tool_calls=[ToolCall("x", "lint", {})]),
                                Turn(stop="end", text=CHECKLIST), Turn(stop="end", text=FINDINGS)])
    review = await model_review(reviewer, "I2", run, TOOLS, execute, "s", provider="scripted")
    assert not review.format_ok


async def test_i0_and_i1_get_no_tools_and_i3_needs_another_model(tmp_path):
    run = await author_trace(tmp_path)
    seen = {}

    class Spy(ScriptedAdapter):
        def start(self, system, tools, task):
            seen["tools"] = tools
            super().start(system, tools, task)

    review = await model_review(Spy([Turn(stop="end", text=CHECKLIST), Turn(stop="end", text=FINDINGS)]),
                                "I1", run, TOOLS, execute, "s", provider="scripted")
    assert seen["tools"] == [] and review.context.startswith("a fresh conversation with the author's final")
    with pytest.raises(ValueError, match="different model"):
        await model_review(ScriptedAdapter([], name="m"), "I3", run, TOOLS, execute, "s",
                           provider="p", author_model="m")
    i3 = await model_review(ScriptedAdapter([Turn(stop="end", text=CHECKLIST), Turn(stop="end", text=FINDINGS)],
                                            name="other"), "I3", run, TOOLS, execute, "s",
                            provider="deepseek", author_model="m")
    assert "weaker" in i3.note or "more independent" in i3.note


async def test_rules_review_without_a_model(tmp_path):
    run = await author_trace(tmp_path)
    rtl = {"tool": "rtl_rules", "status": "fail", "evidence_level": "E0", "summary": "1 error(s)",
           "metrics": {"by_rule": {"MULTI_DRIVEN": 1, "BLOCKING_IN_SEQUENTIAL": 4}},
           "diagnostics": [
               {"severity": "error", "code": "MULTI_DRIVEN", "msg": "driven twice", "file": "a.sv", "line": 3},
               {"severity": "warning", "code": "BLOCKING_IN_SEQUENTIAL", "msg": "blocking", "file": "b.sv",
                "line": 9}]}
    review = rules_review(run, rtl)
    texts = " | ".join(f"{f['severity']}:{f['text']}" for f in review.findings)
    assert "high:synth: timing not met" in texts and "medium:synth inferred 2 latch(es)" in texts
    assert "high:multi_driven x1" in texts and "medium:blocking_in_sequential x4" in texts and "b.sv:9" in texts
    assert review.verdict == "reject" and review.checklist and review.model == ""
    assert rules_review(run, None).findings[-1]["text"] == "rtl_rules did not run"
