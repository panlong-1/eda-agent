"""Gate rule R5: timing claims need a synth result of this run that carries an SDC sha256."""
from eda_agent.agent.gate import check_report, timing_results

SHA_A = "a" * 64
SHA_B = "b" * 64
RISK = "\n\nResidual risk: pre-PnR estimate only; no wire parasitics, no post-route STA was run."


def synth(sha=SHA_A, wns=0.698, met=True, period=10.0, status=None):
    return {"tool": "synth", "status": status or ("pass" if met else "fail"), "evidence_level": "E4",
            "metrics": {"sdc_sha256": sha, "wns": wns, "tns": 0.0 if met else -3.5, "hold_wns": 0.068,
                        "timing_met": met, "clock_period": period, "worst_corner": "slow",
                        "worst_path": {"slack": wns},
                        "corners": {"typ": {"setup_wns": 3.203, "setup_tns": 0.0, "hold_wns": 0.149,
                                            "hold_tns": 0.0}}}}


def check_sdc(sha=SHA_A):
    return {"tool": "check_sdc", "status": "pass", "evidence_level": "E0",
            "metrics": {"sdc_sha256": sha, "unconstrained_endpoints": 0}}


GOOD = ("synth: pass (E4). The synth estimate meets 100 MHz under the project SDC: setup WNS +0.698 ns "
        "at the slow corner, before placement and routing." + RISK)


def test_cited_timing_claim_passes():
    d = check_report(GOOD, [synth()])
    assert d.passed, d.reasons


def test_timing_claim_without_any_timing_result_is_rejected():
    d = check_report("Lint is clean and the design meets 100 MHz." + RISK, [check_sdc()])
    assert not d.passed and any("no result of this run carries an SDC sha256" in r for r in d.reasons)


def test_timing_claim_must_name_its_result():
    d = check_report("The core has positive slack at 100 MHz." + RISK, [synth()])
    assert not d.passed and any("must name the result" in r for r in d.reasons)


def test_timing_claim_against_a_failing_result_is_rejected():
    d = check_report("synth shows timing is met at 100 MHz." + RISK, [synth(wns=-1.2, met=False)])
    assert not d.passed and any("NOT met" in r for r in d.reasons)


def test_wrong_frequency_is_rejected():
    d = check_report("synth (E4) meets 200 MHz." + RISK, [synth()])
    assert not d.passed and any("200 MHz" in r and "10 ns" in r for r in d.reasons)


def test_invented_wns_number_is_rejected_and_rounding_is_allowed():
    assert check_report("synth: setup WNS +0.70 ns, typ WNS 3.2." + RISK, [synth()]).passed
    d = check_report("synth: setup WNS +0.95 ns." + RISK, [synth()])
    assert not d.passed and any("WNS 0.95" in r or "WNS +0.95" in r for r in d.reasons)


def test_negated_timing_statement_is_a_limit():
    report = "lint: pass (E0). Timing was not estimated, so nothing is claimed about meeting 100 MHz." + RISK
    lint = {"tool": "lint", "status": "pass", "evidence_level": "E0", "metrics": {"errors": 0}}
    assert check_report(report, [lint]).passed


def test_sdc_change_voids_earlier_timing_results():
    results = [synth(sha=SHA_A, wns=0.698), check_sdc(sha=SHA_B)]
    valid, void = timing_results(results)
    assert valid == [] and void == [results[0]]
    d = check_report(GOOD, results)
    assert not d.passed and any("void" in r for r in d.reasons)
    # a new synth under the new SDC is valid again; numbers from the void one are not
    results.append(synth(sha=SHA_B, wns=0.512))
    assert not check_report(GOOD, results).passed
    d = check_report(GOOD.replace("+0.698", "+0.512"), results)
    assert d.passed, d.reasons
    stale = check_report(GOOD.replace("WNS +0.698 ns", "WNS +0.512 ns; an earlier slack 0.698 ns"), results)
    assert any("void by an SDC change" in r for r in stale.reasons)


def test_chinese_timing_claim():
    d = check_report("设计满足时序。" + RISK, [])
    assert not d.passed and any("满足时序" in r for r in d.reasons)
