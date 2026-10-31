"""Gate rule R8: signoff words need a flow result of this run that shows exactly that fact."""
from eda_agent.agent.gate import check_report

RISK = "\n\nResidual risk: no gate-level simulation of the routed netlist and vectorless power only."
SHA = "c" * 64


def flow(status="fail", drc=(0, 0), lvs=0, ant=0, timing_met=False, level="E5-partial", sha=None, wns=-4.35):
    m = {"tag": "full_p10", "drc_by_tool": {"Magic": drc[0], "KLayout": drc[1]}, "lvs_errors": lvs, "lvs_match": lvs == 0,
         "antenna_nets": ant, "antenna_pins": 0, "timing_met": timing_met, "signoff_level": level,
         "signoff_passed": status == "pass", "wns": wns, "hold_wns": 0.101, "clock_period": 10,
         "corners": {"nom_slow_1p08V_125C": {"setup_wns": wns, "hold_wns": 0.251}}}
    if sha:
        m["sdc_sha256"] = sha
    m["lineage"] = {"run": "full_p10", "available": True, "stages": [], "breaks": [], "complete": True}
    snap = {"id": "s1", "design_id": "d1", "state": "current",
            "parts": {"design": {"rtl": {"rtl/a.sv": "aa"}, "sdc_sha256": "c" * 64, "pdk_version": "p"}}}
    return {"tool": "flow_report", "status": status, "evidence_level": "E4", "metrics": m, "snapshot": snap}


def test_drc_and_lvs_clean_cited_from_flow_report_pass():
    d = check_report("flow_report full_p10 (fail, E4): the layout is DRC clean and LVS clean, with 0 DRC errors "
                     "and no antenna violations." + RISK, [flow()])
    assert d.passed, d.reasons


def test_signoff_word_without_any_flow_result_is_rejected():
    d = check_report("The design is DRC clean." + RISK, [])
    assert not d.passed and any("no flow_report or flow_run result" in r for r in d.reasons)


def test_signoff_word_must_name_the_flow_result():
    d = check_report("The layout is DRC clean." + RISK, [flow()])
    assert not d.passed and any("must name the flow result" in r for r in d.reasons)
    d = check_report("Run full_p10 is DRC clean." + RISK, [flow()])  # the run's tag names it
    assert d.passed, d.reasons


def test_drc_clean_rejected_when_klayout_reports_errors():
    d = check_report("flow_report: the layout is DRC clean." + RISK, [flow(drc=(0, 3))])
    assert not d.passed and any("every signoff DRC tool" in r for r in d.reasons)


def test_wrong_drc_count_is_rejected():
    d = check_report("flow_report shows 2 DRC errors." + RISK, [flow(drc=(0, 3))])
    assert not d.passed and any("2 DRC errors" in r for r in d.reasons)
    assert check_report("flow_report shows 3 KLayout DRC errors and 0 Magic DRC errors." + RISK,
                        [flow(drc=(0, 3))]).passed


def test_tapeout_ready_needs_e5_not_just_passing_signoff():
    passing = flow(status="pass", timing_met=True, wns=0.1)
    d = check_report("flow_report: signoff passed, the design is ready for tapeout." + RISK, [passing])
    assert not d.passed and any("E5" in r and "tapeout" in r for r in d.reasons)
    assert check_report("flow_report: signoff passed." + RISK, [passing]).passed
    assert check_report("flow_report: signoff passed." + RISK, [flow(status="pass", level="E5", timing_met=True,
                                                                     wns=0.1)]).passed


def test_negated_signoff_words_are_limits():
    d = check_report("flow_report full_p10 fails setup at the slow corner, so the design is not ready for tapeout "
                     "and signoff is not complete." + RISK, [flow()])
    assert d.passed, d.reasons


def test_chinese_tapeout_claim_is_rejected():
    d = check_report("根据 flow_report，可以流片。" + RISK, [flow()])
    assert not d.passed


def test_post_route_timing_claim_needs_timing_met():
    d = check_report("flow_report: post-route timing is met on all corners." + RISK, [flow(sha=SHA)])
    assert not d.passed and any("post-route setup and hold met" in r for r in d.reasons)


def test_flow_numbers_count_as_timing_evidence_only_with_a_known_sdc():
    text = "flow_report full_p10: setup WNS -4.35 ns at the slow corner post-route." + RISK
    assert check_report(text, [flow(sha=SHA)]).passed
    d = check_report(text, [flow()])
    assert not d.passed and any("SDC sha256" in r for r in d.reasons)


def test_signoff_level_e5_partial_is_not_a_citation_of_e5():
    d = check_report("flow_report full_p10: status fail at E4, signoff level E5-partial (the physical parts only)."
                     + RISK, [flow()])
    assert d.passed, d.reasons
    assert not check_report("flow_report full_p10 reached E5." + RISK, [flow()]).passed
