from eda_agent.agent.gate import check_report

LINT = {"tool": "lint", "status": "pass", "evidence_level": "E0", "metrics": {"errors": 0, "warnings": 12}}
REGRESS = {"tool": "regress", "status": "pass", "evidence_level": "E2",
           "metrics": {"passed": 3, "failed": 0, "cases": 3}}
RESULTS = [LINT, REGRESS, {"status": "ok", "cases": []}]  # the last one is describe_case (no evidence level)

SCOPED = """\
Ran lint: pass (E0), 0 errors. Ran regress on bypass, hazard, muldiv_stall: pass (E2), 3 passed, 0 failed.
These three directed cases passed under the testbench's own self-check.

Residual risk:
- no coverage or mutation score, so it is not known whether these tests would catch a hazard bug
- other hazard combinations (CSR, interrupts) were not exercised
"""


def test_properly_scoped_report_passes():
    d = check_report(SCOPED, RESULTS)
    assert d.passed, d.reasons
    assert d.max_level == "E2"


def test_overclaim_is_rejected():
    report = SCOPED.replace("These three directed cases passed under the testbench's own self-check.",
                            "So the pipeline handles all forwarding and load-use hazards correctly.")
    d = check_report(report, RESULTS)
    assert not d.passed
    assert any("'handles'" in r and "needs E3" in r for r in d.reasons)


def test_overclaim_in_chinese_is_rejected():
    d = check_report(SCOPED + "\n", RESULTS)
    assert d.passed
    report = SCOPED.replace("These three", "流水线冒险处理正确。These three")
    assert not check_report(report, RESULTS).passed


def test_negated_strong_word_is_a_limit_not_a_claim():
    report = SCOPED.replace("These three directed cases passed",
                            "This does not show that the forwarding logic is correct in general; the cases passed")
    assert check_report(report, RESULTS).passed


def test_negation_carries_over_the_items_of_a_list():
    # Rejected by mistake in a live DeepSeek run (fix_torv_hazard): the comma cut off the "not".
    limit = SCOPED.replace("These three directed cases passed under the testbench's own self-check.",
                           "That supports only that these cases passed; it does **not** establish that "
                           "pipeline hazards are generally handled, correct, or bug-free.")
    assert check_report(limit, RESULTS).passed, check_report(limit, RESULTS).reasons
    zh = SCOPED.replace("These three", "这不能说明冒险处理正确，或无 bug。These three")
    assert check_report(zh, RESULTS).passed, check_report(zh, RESULTS).reasons


def test_a_new_claim_after_a_limit_is_still_rejected():
    for sentence in ("This is not E3 evidence, but the pipeline is correct.",
                     "Lint does not cover timing, and the forwarding logic works.",
                     "It does not show coverage, correct hazard handling is shown by the cases."):
        report = SCOPED.replace("These three directed cases passed under the testbench's own self-check.",
                                sentence)
        assert not check_report(report, RESULTS).passed, sentence


def test_strong_words_allowed_with_e3_evidence_the_sentence_names():
    named = SCOPED.replace("These three directed cases passed", "mutate: pass (E3); the forwarding logic is verified")
    e3 = [*RESULTS, {"tool": "mutate", "status": "pass", "evidence_level": "E3", "metrics": {}}]
    assert not check_report(named, RESULTS).passed
    assert check_report(named, e3).passed, check_report(named, e3).reasons

    # E3 on one check does not vouch for a sentence about another.
    unnamed = SCOPED.replace("These three directed cases passed", "The forwarding logic is verified")
    d = check_report(unnamed, e3)
    assert not d.passed and any("does not name the check" in r and "mutate" in r for r in d.reasons)


def test_synthesis_never_vouches_for_function():
    # Found in a live run: E4 ranks above E3, but synthesis says nothing about function.
    synth = {"tool": "synth", "status": "pass", "evidence_level": "E4", "metrics": {}}
    report = SCOPED.replace("These three directed cases passed", "synth: pass (E4); so the design is correct")
    d = check_report(report, [*RESULTS, synth])
    assert not d.passed and any("synthesis" in r for r in d.reasons)


def test_colon_list_after_a_limit_is_not_a_claim():
    # Rejected by mistake in a live run (m12_isa_v1b_full): the comma cut "or ... correctness" off.
    limit = SCOPED.replace("These three directed cases passed under the testbench's own self-check.",
                           "Does not prove: post-route timing (usually worse), RTL-to-netlist "
                           "equivalence, or gate-level functional correctness.")
    assert check_report(limit, RESULTS).passed, check_report(limit, RESULTS).reasons
    zh = SCOPED.replace("These three", "不能证明：时序、等价性或功能正确。These three")
    assert check_report(zh, RESULTS).passed, check_report(zh, RESULTS).reasons
    # the list ends with its sentence; the next sentence is judged on its own
    after = SCOPED.replace("These three directed cases passed under the testbench's own self-check.",
                           "Does not prove: post-route timing. The pipeline is correct.")
    assert not check_report(after, RESULTS).passed


def test_fabricated_citation_is_rejected():
    report = SCOPED.replace("pass (E2), 3 passed", "pass (E3), 3 passed")
    d = check_report(report, RESULTS)
    assert not d.passed and any("no tool result of this run has evidence_level E3" in r for r in d.reasons)

    wrong_status = SCOPED.replace("Ran lint: pass (E0)", "Ran lint: fail (E0)")
    assert any("fail at E0" in r for r in check_report(wrong_status, RESULTS).reasons)


def test_fabricated_count_is_rejected():
    report = SCOPED.replace("3 passed", "33 passed")
    d = check_report(report, RESULTS)
    assert not d.passed and any("'33 passed'" in r for r in d.reasons)


def test_counts_of_repeated_regress_calls_may_be_summed():
    # Two calls with the same count: the total (6) must be accepted, not only the distinct values.
    report = SCOPED.replace("3 passed, 0 failed", "6 passed, 0 failed")
    assert check_report(report, [*RESULTS, REGRESS]).passed


def test_level_named_as_requirement_is_not_a_citation():
    # From live run 2: this sentence was wrongly rejected before R1 learned about requirements.
    report = SCOPED.replace("These three directed cases passed under the testbench's own self-check.",
                            "This is only an E2 pass, so I cannot conclude that hazards are handled in "
                            "general — that would require stronger evidence (E3 or higher).")
    d = check_report(report, RESULTS)
    assert d.passed, d.reasons


DESCRIBED = {"tool": "describe_case", "status": "ok",
             "cases": [{"name": n} for n in ("bypass", "hazard", "muldiv_stall")]}
RAN = {**REGRESS, "metrics": {**REGRESS["metrics"], "passed": 2, "passed_cases": ["bypass", "hazard"]}}


def test_described_but_not_run_must_be_named():
    report = SCOPED.replace("3 passed", "2 passed").replace("bypass, hazard, muldiv_stall", "bypass, hazard")
    d = check_report(report, [LINT, RAN, DESCRIBED])
    assert not d.passed and d.reasons == [
        "described case 'muldiv_stall' but neither ran it nor named it under residual risk: run it, "
        "or say there why it was left out"]
    owned = report + "- muldiv_stall (stall + forwarding) was not run in this session\n"
    assert check_report(owned, [LINT, RAN, DESCRIBED]).passed
    ran_all = {**REGRESS, "metrics": {**REGRESS["metrics"], "passed_cases": ["bypass", "hazard", "muldiv_stall"]}}
    assert check_report(SCOPED, [LINT, ran_all, DESCRIBED]).passed


def test_citation_without_any_tool_run_is_rejected():
    assert not check_report(SCOPED, []).passed


def test_missing_residual_risk_is_rejected():
    report = SCOPED.split("Residual risk")[0]
    d = check_report(report, RESULTS)
    assert d.reasons == ["no residual-risk section: add a section headed 'Residual risk' that says what "
                         "the checks did not cover"]
    assert check_report(report + "## 剩余风险\n没有覆盖率或变异测试, 不知道测试能否发现错误。", RESULTS).passed
