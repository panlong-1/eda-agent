import json
import os
import subprocess
import sys

from eda_agent.agent import human_gate
from eda_agent.agent.models import ScriptedAdapter, Turn
from eda_agent.agent.receipt import render_files, render_markdown, write_receipt
from eda_agent.agent.receipt_llm import gate_results, model_view, write_narrative
from eda_agent.agent.receipt_tables import data_tables, with_trace_data
from eda_agent.agent.receipt_view import md_light, render_html, summarize

SYNTH_METRICS = {"cells": 14766, "area": 236630.87, "sequential_area": 83476.9, "latches": 0,
                 "liberty": "typ.lib", "clock_port": "hclk", "clock_period": 10.0, "sdc": "c.sdc",
                 "sdc_sha256": "ab" * 32, "wns": 0.698, "tns": 0.0, "hold_wns": 0.068, "worst_corner": "slow",
                 "timing_met": True, "top_cell_types": {"dfrbpq": 1704},
                 "worst_path": {"startpoint": "_27317_", "endpoint": "d_htrans[1]", "slack": 0.698,
                                "arrival": 7.002, "corner": "slow"},
                 "corners": {"typ": {"setup_wns": 3.2, "setup_tns": 0.0, "hold_wns": 0.149, "hold_tns": 0.0},
                             "slow": {"setup_wns": 0.698, "setup_tns": 0.0, "hold_wns": 0.291, "hold_tns": 0.0},
                             "fast": {"setup_wns": 4.449, "setup_tns": 0.0, "hold_wns": 0.068, "hold_tns": 0.0}}}
LINT = {"tool": "lint", "args": {}, "status": "pass", "evidence_level": "E0", "summary": "0 error(s), 3 warning(s)",
        "claim_scope": "parsed", "tool_versions": {"verilator": "5"}, "artifacts": [], "duration_s": 0.2,
        "metrics": {"errors": 0, "warnings": 3, "by_code": {"LATCH": 1, "UNUSEDSIGNAL": 2}},
        "diagnostics": [{"severity": "warning", "code": "LATCH", "msg": "latch <x>", "file": "rtl/a.sv", "line": 9},
                        {"severity": "warning", "code": "UNUSEDSIGNAL", "msg": "u", "file": "rtl/a.sv", "line": 3},
                        {"severity": "warning", "code": "UNUSEDSIGNAL", "msg": "u", "file": "rtl/b.sv", "line": 4}],
        "diagnostics_total": 3}
REGRESS = {"tool": "regress", "args": {"cases": ["all"]}, "status": "pass", "evidence_level": "E2",
           "summary": "1 passed, 0 failed, 0 skipped; testbench sub-tests: 90 passed, 0 failed of 90",
           "claim_scope": "exit 0", "tool_versions": {"v": "1"}, "artifacts": [], "duration_s": 20.5,
           "metrics": {"passed": 1, "failed": 0, "cases": 1, "passed_cases": ["all"], "failed_cases": [],
                       "subtests_passed": 90, "subtests_failed": 0, "subtests_total": 90}}
SYNTH = {"tool": "synth", "args": {}, "status": "pass", "evidence_level": "E4",
         "summary": "14766 cells; setup WNS +0.698 ns", "claim_scope": "estimate", "tool_versions": {"yosys": "0.62"},
         "artifacts": [], "duration_s": 14.0, "metrics": SYNTH_METRICS, "sdc": "c.sdc", "sdc_sha256": "ab" * 32,
         "timing": {"clock_period": 10.0, "wns": 0.698, "tns": 0.0, "hold_wns": 0.068, "worst_corner": "slow",
                    "timing_met": True, "cells": 14766, "area": 236630.87, "latches": 0, "void": False}}
MUTATE = {"tool": "mutate", "args": {}, "status": "pass", "evidence_level": "E3", "summary": "9 of 10 killed",
          "claim_scope": "mutants", "tool_versions": {"v": "1"}, "artifacts": [],
          "metrics": {"killed": 9, "total": 10}}
GOOD = ("lint: pass (E0) with 3 warnings. regress: pass (E2), 1 passed. synth: pass (E4), a pre-PnR estimate "
        "with setup WNS +0.698 ns at 10 ns.\n\nResidual risk\n- no coverage; pre-PnR only; latch in rtl/a.sv")


def receipt(**over) -> dict:
    r = {"run_id": "r1", "task": "t", "project": "torv", "provider": "deepseek", "model": "m", "prompt": "",
         "stop_reason": "end", "status": "done", "turns": 3, "tool_calls": 3, "tool_errors": 0,
         "usage": {"input_tokens": 1000, "output_tokens": 100}, "wall_s": 9.5, "trace": "",
         "gate": {"status": "passed", "rewrites": 1, "decisions": [
             {"passed": False, "reasons": ["'correct' claims functional correctness"], "max_level": "E4"},
             {"passed": True, "reasons": [], "max_level": "E4"}]},
         "checks_run": [LINT, REGRESS, SYNTH], "other_tool_calls": [],
         "not_run": {"lint_run": True, "described_but_not_run": [], "cases_not_run": []},
         "final_claims": "x", "residual_risk": {"report": "y", "from_tools": []},
         "reviews": [{"reviewer": "I2", "context": "c", "provider": "deepseek", "model": "m2", "note": "",
                      "checklist": [], "verdict": "accept_with_findings", "format_ok": True, "text": "",
                      "tool_calls": [{"tool": "lint", "args": {}, "status": "pass", "evidence_level": "E0",
                                      "summary": "s"}],
                      "usage": {"input_tokens": 50, "output_tokens": 5},
                      "wall_s": 3.0, "findings": [
                          {"severity": "medium", "text": "LATCH at ip/uart.sv:195 and uart.sv:195 — evidence: lint"},
                          {"severity": "info", "text": "fine"}]}]}
    r.update(over)
    return r


def tables(r) -> dict:
    return {t.title: t for t in data_tables(r)}


def texts(table) -> list[list[str]]:
    return [[c["text"] if isinstance(c, dict) else c for c in row] for row in table.rows]


def test_data_tables_show_the_numbers():
    t = tables(receipt())
    assert texts(t["Simulation (regress)"]) == [["all", "1", "1", "0", "0", "90 passed, 0 failed of 90", "—"]]
    assert texts(t["Lint: by warning code"])[:2] == [["UNUSEDSIGNAL", "2"], ["LATCH", "1"]]
    by_file = texts(t["Lint: by file (of the 3 of 3 diagnostics kept)"])
    assert by_file == [["rtl/a.sv", "0", "2"], ["rtl/b.sv", "0", "1"]]
    assert t["Lint: diagnostics kept (3 of 3)"].collapsed
    synth = dict(texts(t["Synthesis and timing estimate (synth, pre-PnR)"]))
    assert synth["cells"] == "14,766" and synth["timing met (under this SDC)"] == "yes"
    corners = texts(t["Timing per corner (ns; negative = violation)"])
    assert corners[1] == ["slow (worst)", "+0.698", "+0.000", "+0.291", "+0.000"]
    assert texts(t["Worst path"]) == [["_27317_", "d_htrans[1]", "+0.698", "7.002", "slow"]]
    findings = texts(t["Reviewer findings"])
    assert findings[0][:3] == ["Error", "medium", "I2 (m2)"] and findings[0][4] == "ip/uart.sv:195"  # no bare dup
    assert findings[1][:2] == ["Warning", "info"]
    assert texts(t["Lint: diagnostics kept (3 of 3)"])[0][0] == "Warning"
    gate = texts(t["Evidence gate decisions"])
    assert gate[0][:3] == ["1", "reject", "E4"] and "'correct'" in gate[0][3]
    cost = texts(t["Cost"])
    assert cost[0][:6] == ["author (the agent)", "deepseek / m", "3", "3", "1,000", "100"]
    assert cost[-1][0] == "total" and cost[-1][4] == "1,050"


def test_unknown_tools_get_a_plain_numbers_table():
    t = tables(receipt(checks_run=[MUTATE]))
    assert texts(t["mutate: numbers"]) == [["killed", "9"], ["total", "10"]]


def test_summary_checks_show_duration():
    s = summarize(receipt())
    assert [c["duration"] for c in s.checks] == [0.2, 20.5, 14.0]
    assert "| 20.5 |" in render_markdown(receipt())


def test_old_receipt_gets_numbers_from_its_trace_without_changing_it(tmp_path):
    old = receipt(trace="runs/r1.jsonl", checks_run=[{k: v for k, v in LINT.items()
                                                      if k not in ("metrics", "diagnostics", "diagnostics_total",
                                                                   "duration_s")}])
    old = human_gate.decide(human_gate.request_approval(old, "final report", "w"), "approve", by="A",
                            reason="known")
    trace = tmp_path / "r1.jsonl"
    trace.write_text("\n".join(json.dumps(x) for x in [
        {"t": 1.0, "kind": "turn"},
        {"t": 1.5, "kind": "tool", "name": "lint", "input": {}, "result": json.dumps(
            {"summary": LINT["summary"], "metrics": LINT["metrics"], "diagnostics": LINT["diagnostics"],
             "diagnostics_total": 3})}]))
    before = json.dumps(old, sort_keys=True)
    view = with_trace_data(old, tmp_path)
    assert json.dumps(old, sort_keys=True) == before  # the receipt itself is not changed
    c = view["checks_run"][0]
    assert c["metrics"] == LINT["metrics"] and c["duration_s"] == 0.5 and c["from_trace"]
    assert "Lint: by warning code †" in tables(view)
    assert not any("changed after its decision" in i.text for i in summarize(view).issues)
    md, _ = render_files(old, tmp_path / "out", base=tmp_path)
    assert "come from the run's trace" in md.read_text()


async def test_narrative_goes_through_the_gate_and_is_recorded():
    r = receipt()
    overclaim = "The design is correct and verified.\n\nResidual risk\n- none at all, really"
    model = ScriptedAdapter([Turn(stop="end", text=overclaim),
                             Turn(stop="end", text=GOOD, usage={"input_tokens": 10, "output_tokens": 5})], name="m-x")
    n = await write_narrative(r, model, "sys", provider="scripted")
    assert n["gate"]["passed"] and n["gate"]["rewrites"] == 1 and n["turns"] == 2
    assert "'correct' claims functional correctness" in " ".join(n["gate"]["decisions"][0]["reasons"])
    assert (n["text"], n["model"], n["prompt"]) == (GOOD, "m-x", "receipt_summary.v1")
    # what the model was given: the receipt data only, without the decision or the trace
    assert '"run_id": "r1"' in model.task and '"approval"' not in model.task
    assert "Evidence gate rejected" in model.user_messages[0]


async def test_rejected_narrative_is_marked_and_flagged(tmp_path):
    r = human_gate.request_approval(receipt(), "final report", "w")
    sha = human_gate.content_sha256(r)
    bad = Turn(stop="end", text="Everything works.\n\nResidual risk\n- nothing worth naming here")
    r["narrative"] = await write_narrative(r, ScriptedAdapter([bad, bad]), "sys", provider="s")
    assert human_gate.content_sha256(r) == sha  # the narrative is outside the decided content
    assert not r["narrative"]["gate"]["passed"]
    assert any("rejected by the evidence gate" in i.text and i.level == "warning" for i in summarize(r).issues)
    page = render_html(r)
    assert "REJECTED by the evidence gate" in page and "Everything works." in page
    assert "Summary written by a model (AI)" in render_markdown(r)


def test_gate_results_rebuild_timing_and_described_cases():
    r = receipt(not_run={"lint_run": True, "described_but_not_run": ["hazard"], "cases_not_run": []})
    res = gate_results(r)
    synth = next(x for x in res if x["tool"] == "synth")
    assert synth["metrics"]["sdc_sha256"] == "ab" * 32 and synth["metrics"]["wns"] == 0.698
    assert {"tool": "describe_case", "status": "ok", "cases": [{"name": "hazard"}]} in res
    assert "approval" not in model_view(human_gate.request_approval(r, "s", "w"))


def test_narrative_box_is_marked_and_escaped():
    r = receipt(narrative={"text": "**bold** <script>x</script>\n- one item", "provider": "p", "model": "m",
                           "prompt": "receipt_summary.v1", "at": "2026-11-01T00:00:00+01:00",
                           "gate": {"passed": True, "rewrites": 0, "decisions": []}})
    page = render_html(r)
    assert '<span class="tag">AI</span><b>Summary written by a model</b>' in page and "passed the evidence gate" in page
    assert "<b>bold</b>" in page and "<script" not in page and "&lt;script&gt;" in page
    assert "Check it against the tables: they are the record." in page


def test_md_light_escapes_everything():
    assert md_light("a <b>x</b> `c<d>`\n- **y**") == ("<p>a &lt;b&gt;x&lt;/b&gt; <code>c&lt;d&gt;</code></p>"
                                                      "<ul><li><b>y</b></li></ul>")


def test_cli_narrate_without_a_key_still_renders(tmp_path):
    write_receipt(receipt(), tmp_path)
    env = {k: v for k, v in os.environ.items() if k not in ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY")}
    res = subprocess.run([sys.executable, "-m", "eda_agent.cli", "receipt", "r1", "--runs-dir", str(tmp_path),
                          "--narrate"], capture_output=True, text=True, env=env)
    assert res.returncode == 0, res.stderr
    assert "summary not written" in res.stderr
    assert (tmp_path / "r1.receipt.md").read_text().startswith("# Review summary")
    assert "narrative" not in json.loads((tmp_path / "r1.receipt.json").read_text())
