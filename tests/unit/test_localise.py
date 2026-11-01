"""Failure localisation: hierarchy, trace_driver/trace_load (pyslang), waveform_query (pywellen), the
waveform-to-slang name mapping, regress waves=true, gate rule R10, levels and the review page.

Fixtures (tests/fixtures/localise) are synthetic: mini.sv has a planted constant condition at
line 15, mini.vcd is a hand-written Verilator-style waveform (TOP.tb.dut is the design's top).
"""
from pathlib import Path

import pytest

pytest.importorskip("pyslang")
pytest.importorskip("pywellen")

from eda_agent.agent.gate import check_report  # noqa: E402
from eda_agent.agent.gate.r10_localise import check_localisation  # noqa: E402
from eda_agent.agent.levels import assess  # noqa: E402
from eda_agent.agent.receipt.localise import localise_tables, wave_svg  # noqa: E402
from eda_agent.agent.receipt_view import render_html  # noqa: E402
from eda_agent.core.envelope import ToolResult  # noqa: E402
from eda_agent.core.project import load_project  # noqa: E402
from eda_agent.tools.regress import run_regress, waves_files  # noqa: E402
from eda_agent.tools.rtl_trace import run_hierarchy, run_trace_driver, run_trace_load  # noqa: E402
from eda_agent.tools.waves import fmt, run_waveform_query  # noqa: E402

FIX = Path(__file__).parents[1] / "fixtures" / "localise"
BUG_LINE = 15  # mini.sv: `else if (1'b0)`


@pytest.fixture()
def project(tmp_path):
    toml = tmp_path / "mini.toml"
    toml.write_text(f'name = "mini"\nroot = "{FIX}"\n[lint]\ntop = "top"\nfiles = ["mini.sv"]\n'
                    '[regress]\ncmd = ["true"]\nformat = "exitcode"\n'
                    'cases = [{name = "c1"}, {name = "c2"}, {name = "c3"}]\n')
    return load_project(toml)


def by_at(steps, at):
    return [s for s in steps if s["at"] == at]


# ---------------------------------------------------------------- tools

def test_hierarchy_lists_instances_with_locations(project):
    r = run_hierarchy(project, signals=True, depth=2)
    assert r.status == "pass" and r.evidence_level == "E0" and r.diagnostic
    tree = r.metrics["tree"]
    assert tree["module"] == "top" and tree["children"][0]["path"] == "top.u_sel"
    assert tree["children"][0]["module"] == "sel" and tree["children"][0]["instantiated_at"] == "mini.sv:33"
    assert tree["children"][0]["defined_at"] == "mini.sv:4"
    sig = {s["path"]: s for s in r.metrics["signals"]}
    assert sig["top.out"]["direction"] == "out" and sig["top.cnt"]["direction"] == "internal"
    assert sig["top.cnt"]["width"] == 4 and sig["top.cnt"]["drivers"] == 2
    assert r.tool_versions.get("pyslang") and "diagnostic" in r.to_dict()


def test_hierarchy_of_unknown_instance_is_not_a_failed_check(project):
    out = run_hierarchy(project, "u_nope")
    assert isinstance(out, dict) and out["found"] is False


def test_trace_driver_reaches_the_planted_condition(project):
    r = run_trace_driver(project, "out", depth=5)
    steps = r.metrics["steps"]
    assert r.metrics["signal"] == "top.out" and r.metrics["direction"] == "drivers"
    assert by_at(steps, "mini.sv:34")[0]["kind"] == "assign"
    port = by_at(steps, "mini.sv:33")
    assert port and port[0]["kind"] == "port_out" and port[0]["via"] == "top.u_sel.y"
    y_b = by_at(steps, "mini.sv:16")[0]
    assert y_b["kind"] == "always_comb" and y_b["block_at"] == "mini.sv:11"
    conds = [(c["at"], c["branch"], c["text"]) for c in y_b["conditions"]]
    assert conds == [("mini.sv:13", "false", "if (a_en)"), (f"mini.sv:{BUG_LINE}", "true", "else if (1'b0)")]
    assert y_b["conditions"][1]["reads"] == []            # the constant: nothing is read
    assert f"mini.sv:{BUG_LINE}" in r.metrics["locations"]
    # the input side: a_en is driven by the parent's connection of en
    assert any(s["kind"] == "port_in" and s["signal"] == "top.u_sel.a_en" and s["reads"] == ["top.en"] for s in steps)
    assert "top.en" in r.metrics["undriven"]               # a top-level input


def test_trace_driver_with_waveform_gives_values_at_the_time(project):
    r = run_trace_driver(project, "u_sel.y", depth=1, waveform=str(FIX / "mini.vcd"), time=30)
    assert r.metrics["waveform"]["scope_prefix"] == "TOP.tb.dut"
    y_b = by_at(r.metrics["steps"], "mini.sv:16")[0]
    assert y_b["values"]["top.u_sel.y"] == "4'h0"           # should be 3: b_en is 1
    assert y_b["values"]["top.u_sel.b"] == "4'h3"
    assert y_b["values"]["top.u_sel.a_en"] == "0"
    assert any("mapped" in x for x in r.residual_risk)


def test_waveform_names_are_accepted_by_trace(project):
    r = run_trace_driver(project, "TOP.tb.dut.u_sel.y", depth=1, waveform=str(FIX / "mini.vcd"), time=30)
    assert r.metrics["signal"] == "top.u_sel.y"


def test_trace_load_follows_reads_forward(project):
    r = run_trace_load(project, "cnt", depth=2)
    sigs = {(s["signal"], s["kind"]) for s in r.metrics["steps"]}
    assert ("top.cnt", "always_ff") in sigs and ("top.u_sel.a", "port_in") in sigs
    assert ("top.u_sel.y", "always_comb") in sigs
    ff = [s for s in r.metrics["steps"] if s["kind"] == "always_ff"][0]
    assert ff["clock"] == "top.clk"


def test_unknown_signal_gives_suggestions_not_an_error(project):
    out = run_trace_driver(project, "u_sel.yy")
    assert out["found"] is False and out["tool"] == "trace_driver"
    out = run_trace_driver(project, "top.nope.y")
    assert "top.u_sel.y" in out["suggestions"]


def test_waveform_query_maps_slang_paths_and_caps_changes(project):
    r = run_waveform_query(project, str(FIX / "mini.vcd"), ["top.u_sel.y", "u_sel.b_en", "TOP.tb.dut.cnt", "nope"],
                           0, 60, max_changes=2)
    assert r.status == "pass" and r.diagnostic and r.metrics["timescale"] == "1ns"
    rows = {x["waveform_path"]: x for x in r.metrics["signals"]}
    y = rows["TOP.tb.dut.u_sel.y"]
    assert y["slang"] == "top.u_sel.y" and y["at_start"] == "4'h0"
    assert y["changes"] == [[15, "4'h1"], [20, "4'h0"]] and not y["truncated"]
    cnt = rows["TOP.tb.dut.cnt"]
    assert cnt["changes_in_window"] == 5 and len(cnt["changes"]) == 2 and cnt["truncated"]
    assert rows["TOP.tb.dut.u_sel.b_en"]["changes"] == [[20, "1"]]
    assert r.metrics["missing"][0]["signal"] == "nope"
    assert "TOP.tb.dut" in r.metrics["mapping_note"]


def test_waveform_query_patterns_window_and_x(project):
    r = run_waveform_query(project, str(FIX / "mini.vcd"), ["TOP.tb.dut.u_sel.*"], 50, 60)
    names = [x["waveform_path"] for x in r.metrics["signals"]]
    assert names == sorted(names) and "TOP.tb.dut.u_sel.b" in names
    b = [x for x in r.metrics["signals"] if x["waveform_path"].endswith("u_sel.b")][0]
    assert b["at_start"] == "4'h3" and b["changes"] == [[60, "4'bx1x0"]]


def test_waveform_query_refuses_files_outside_the_project(project, tmp_path):
    other = tmp_path / "x.vcd"
    other.write_text((FIX / "mini.vcd").read_text())
    r = run_waveform_query(project, str(other), ["top.out"], 0, 10)
    assert r.status == "error" and "under the project root" in r.summary
    r = run_waveform_query(project, "mini.sv", ["top.out"], 0, 10)
    assert r.status == "error" and "not a waveform" in r.summary


def test_value_format():
    assert fmt(1, 1) == "1" and fmt(10, 8) == "8'ha" and fmt("x1", 2) == "2'bx1" and fmt(None, 4) is None


# ---------------------------------------------------------------- regress waves=true

def test_regress_waves_needs_config_and_few_cases(project):
    r = run_regress(project, ["c1"], waves=True)
    assert r.status == "error" and r.error_class == "not_configured"
    from dataclasses import replace
    p = replace(project, regress=replace(project.regress, waves_flag="--waves", waves_file="{case_dir}/{name}.vcd"))
    r = run_regress(p, ["c1", "c2", "c3"], waves=True)
    assert r.status == "error" and "at most 2" in r.summary


def test_waves_files_finds_the_case_waveform(tmp_path, project):
    from dataclasses import replace
    (tmp_path / "c1").mkdir()
    (tmp_path / "c1" / "c1.vcd").write_text("x")
    cfg = replace(project.regress, waves_flag="--waves", waves_file="{case_dir}/{name}.vcd")
    logs = {"c1": str(tmp_path / "c1" / "c1.sim.log"), "c2": str(tmp_path / "c2" / "c2.sim.log")}
    assert waves_files(cfg, logs, ["c1", "c2", "c3"]) == {"c1": str(tmp_path / "c1" / "c1.vcd")}


# ---------------------------------------------------------------- gate rule R10

def trace_result(locations=("mini.sv:15", "mini.sv:16", "mini.sv:34"), tool="trace_driver") -> dict:
    return {"tool": tool, "status": "pass", "evidence_level": "E0", "diagnostic": True,
            "metrics": {"locations": list(locations)}}


GOOD = ("Hypothesis (a diagnosis, not a proof): trace_driver shows that the root cause is the constant "
        "condition at mini.sv:15, so y never takes b.")


def test_r10_accepts_a_cited_hypothesis():
    assert check_localisation(GOOD, [trace_result()], GOOD) == []


def test_r10_needs_a_trace_result_and_the_tool_named():
    assert "there is none" in check_localisation(GOOD, [], GOOD)[0]
    plain = "Hypothesis: the root cause is the constant condition at mini.sv:15."
    assert "must name" in check_localisation(plain, [trace_result()], plain)[0]


def test_r10_rejects_lines_no_trace_reached():
    text = "Hypothesis: trace_driver shows the bug is in mini.sv:22 and line 40."
    reasons = check_localisation(text, [trace_result()], text)
    assert any("mini.sv:22" in x for x in reasons) and any("line 40" in x for x in reasons)
    ok = "Hypothesis: per trace_driver the failure is caused by line 15 (the else-if)."
    assert check_localisation(ok, [trace_result()], ok) == []


def test_r10_a_diagnosis_is_not_a_fix():
    text = "Hypothesis: trace_driver localised it to mini.sv:15, and the bug is fixed."
    assert any("not proof that a fix works" in x for x in check_localisation(text, [trace_result()], text))


def test_r10_wants_the_word_hypothesis_somewhere():
    text = "trace_driver shows the root cause is mini.sv:15."
    assert any("hypothesis" in x for x in check_localisation(text, [trace_result()], text))


def test_r10_negated_and_chinese_claims():
    neg = "The tools did not lead to a root cause; no line could be named."
    assert check_localisation(neg, [], neg) == []
    cn = "假设：trace_driver 把问题定位到 mini.sv:15 的常量条件。"
    assert check_localisation(cn, [trace_result()], cn) == []
    assert check_localisation("问题定位到 mini.sv:15。", [], "x")


def test_r10_runs_inside_the_gate():
    report = GOOD + "\n\nResidual risk: one case, one waveform; the cause is not proven."
    assert check_report(report, [trace_result()]).passed
    assert not check_report(report, []).passed


# ---------------------------------------------------------------- receipt: levels and the review page

def receipt_with_localisation(project) -> dict:
    vcd = str(FIX / "mini.vcd")
    tr = run_trace_driver(project, "u_sel.y", depth=2, waveform=vcd, time=30).to_dict()
    wq = run_waveform_query(project, vcd, ["u_sel.y", "u_sel.b_en", "u_sel.b", "cnt"], 0, 60).to_dict()

    def check(res: dict, args: dict) -> dict:
        return {"tool": res["tool"], "args": args, "status": res["status"], "evidence_level": "E0",
                "summary": res["summary"], "claim_scope": res["claim_scope"], "tool_versions": res["tool_versions"],
                "artifacts": [], "metrics": res["metrics"], "diagnostic": True}
    return {"run_id": "loc1", "task": "case c1 fails; localise the cause", "project": "mini",
            "project_root": str(FIX), "provider": "scripted", "model": "m", "prompt": "system.v3",
            "stop_reason": "end", "status": "done", "turns": 1, "tool_calls": 2, "tool_errors": 0, "usage": {},
            "wall_s": 1.0, "trace": "", "gate": {"status": "passed", "rewrites": 0, "decisions": []},
            "checks_run": [check(tr, {"signal": "u_sel.y"}), check(wq, {"signals": ["u_sel.y"]})],
            "other_tool_calls": [], "not_run": {"lint_run": True, "described_but_not_run": [], "cases_not_run": []},
            "final_claims": GOOD, "residual_risk": {"report": "one case", "from_tools": []}}


def test_levels_mark_the_localisation_as_a_diagnosis(project):
    issues = assess(receipt_with_localisation(project))
    loc = [i for i in issues if i.source == "localisation"]
    assert loc and loc[0].level == "warning" and "mini.sv:15" in loc[0].quote
    assert not [i for i in issues if i.level == "fatal"]


def test_page_shows_chain_table_and_waveform(project):
    r = receipt_with_localisation(project)
    tables = localise_tables(r)
    assert tables and tables[0].cols[3] == "source"
    row = [x for x in tables[0].rows if x[3] != "—" and x[3]["text"] == "mini.sv:16"][0]
    assert row[5]["text"] == "mini.sv:13, mini.sv:15" and row[6].startswith("4'h0")
    links = []
    page = render_html(r, link=lambda ref: links.append(ref) or f'<a href="/source?ref={ref}">{ref}</a>')
    assert "Failure localisation (diagnosis)" in page and "<svg" in page and "t=30 (trace)" in page
    assert "mini.sv:15" in links and page.count("Trace chain (trace_driver)") == 1   # not repeated under Data
    assert "<script" not in page


def test_wave_svg_marks_x_values_and_is_empty_without_a_window():
    m = {"window": [0, 10], "timescale": "1ns", "signals": [
        {"slang": "t.a", "width": 4, "at_start": "4'h1", "changes": [[5, "4'bx0x0"]], "truncated": False},
        {"slang": "t.b", "width": 1, "at_start": "0", "changes": [[2, "1"]], "truncated": False}]}
    svg = wave_svg(m, mark=5)
    assert svg.startswith("<svg") and "var(--fatal)" in svg and "t=5 (trace)" in svg
    assert wave_svg({"window": [3, 3], "signals": m["signals"]}) == ""


def test_envelope_marker_only_on_diagnostic_results():
    assert "diagnostic" not in ToolResult("lint", "pass", "E0", "s").to_dict()
    assert ToolResult("trace_driver", "pass", "E0", "s", diagnostic=True).to_dict()["diagnostic"] is True
