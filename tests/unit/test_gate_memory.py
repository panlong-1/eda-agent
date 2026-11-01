"""Gate rule R11: memory citations exist, were retrieved in this run, are hints, and are not evidence."""
from eda_agent.agent.gate import check_report
from eda_agent.agent.memory import RunMemory, proposed_lessons

RISK = "\n\n### Residual risk\nLint says nothing about function; no simulation was run."
LINT = {"tool": "lint", "status": "pass", "evidence_level": "E0", "summary": "0 errors"}
KNOWN = {"lessons": {"lesson-torv-001": "hypothesis", "lesson-torv-002": "verified", "lesson-torv-003": "void"},
         "docs": ["doc:torv-spec#9-hazard-handling"], "doc_ids": ["torv-spec"]}


def results(retrieved=("lesson-torv-001", "lesson-torv-003", "doc:torv-spec#9-hazard-handling")):
    mem = RunMemory(known=KNOWN)
    for r in retrieved:
        mem.note(r)
    return [LINT, mem.gate_record()]


def reasons(text, res=None):
    return check_report(text + RISK, res if res is not None else results()).reasons


def test_valid_hint_citations_pass():
    assert reasons("lint: pass (E0), 0 errors. As a hint (lesson-torv-001), the ABC buffering knob matters "
                   "for timing; see the source doc:torv-spec#9-hazard-handling.") == []


def test_nonexistent_lesson_is_rejected():
    r = reasons("lint: pass (E0). Hint: lesson-torv-042 says the multiplier is slow.")
    assert len(r) == 1 and r[0].startswith("(R11)") and "not in any lessons store" in r[0]


def test_lesson_not_retrieved_in_this_run_is_rejected():
    r = reasons("lint: pass (E0). Hint: lesson-torv-002 applies.")
    assert any("not retrieved in this run" in x for x in r)


def test_no_memory_record_means_nothing_may_be_cited():
    r = reasons("lint: pass (E0). Hint: lesson-torv-001.", [LINT])
    assert any("lesson-torv-001" in x and "not in any lessons store" in x for x in r)


def test_void_lesson_and_doc_without_section_are_rejected():
    r = reasons("lint: pass (E0). Hint: lesson-torv-003. Source: doc:torv-spec says so.")
    assert any("is void" in x for x in r) and any("without a section" in x for x in r)
    r = reasons("lint: pass (E0). Source: doc:torv-spec#nowhere.")
    assert any("not a section of any document" in x for x in r)


def test_unmarked_citation_and_memory_as_evidence_are_rejected():
    r = reasons("lint: pass (E0). lesson-torv-001 applies here.")
    assert any("without marking it as a hint" in x for x in r)
    r = reasons("lint: pass (E0). The hint lesson-torv-001 confirms the forwarding path works.")
    assert any("not evidence" in x for x in r)
    # a tool result of this run named in the same sentence may confirm; the lesson stays a hint
    assert reasons("lint: pass (E0). lint confirms 0 errors, as the hint lesson-torv-001 suggested.") == []


def test_lessons_section_is_new_lessons_not_citations():
    text = ("lint: pass (E0), 0 errors.\n\n## Lessons (hypotheses)\n- lint of torv_soc gives 0 errors with "
            "-Wall (lint result of this run)\n- refines lesson-torv-001\n")
    assert reasons(text) == []
    assert proposed_lessons(text + RISK) == ["lint of torv_soc gives 0 errors with -Wall (lint result of this run)",
                                             "refines lesson-torv-001"]
    assert any("lesson-torv-077" in x for x in reasons(text + "- also lesson-torv-077\n"))


async def test_run_citing_a_nonexistent_lesson_ends_rejected_and_fatal():
    import json

    from eda_agent.agent.levels import assess
    from eda_agent.agent.loop import run_agent
    from eda_agent.agent.models import ScriptedAdapter, ToolCall, ToolSpec, Turn
    from eda_agent.agent.receipt import build_receipt

    bad = "lint: pass (E0). Hint: lesson-torv-099 says the forwarding unit is fine." + RISK
    model = ScriptedAdapter([Turn(stop="tool_use", tool_calls=[ToolCall("c", "lint", {})])]
                            + [Turn(stop="end", text=bad)] * 3)

    async def execute(name, args):
        return json.dumps(LINT), False

    mem = RunMemory(hints=[{"kind": "lesson", "id": "lesson-torv-001"}], known=KNOWN)
    rep = await run_agent(model, [ToolSpec("lint", "", {})], execute, "t", "s", memory=mem)
    assert rep.stop_reason == "gate_rejected" and all("(R11)" in d["reasons"][0] for d in rep.gate)
    r = build_receipt(rep, run_id="x", task="t", provider="p", project="torv", all_cases=[], hints=mem.hints)
    fatal = [i.text for i in assess(r) if i.level == "fatal"]
    assert any("(R11)" in t for t in fatal)
    assert r["memory"]["used_as_hints"][0]["id"] == "lesson-torv-001"
