import json
import subprocess
import sys

import pytest

from eda_agent.agent import human_gate
from eda_agent.agent.levels import RULES, assess, counts
from eda_agent.agent.receipt import render_details, render_files, render_markdown, write_receipt
from eda_agent.agent.receipt_view import render_html, render_summary_md, summarize
from eda_agent.core.settings import user_defaults

HIGH = "multi_driven x1: signal 'torv_soc.apb_presetn' has multiple drivers (at rtl/torv_soc.sv:179)"


def check(tool, level, summary, status="pass", versions=None, **extra) -> dict:
    return {"tool": tool, "args": {}, "status": status, "evidence_level": level, "summary": summary,
            "claim_scope": f"scope of {tool}", "tool_versions": {"v": "1"} if versions is None else versions,
            "artifacts": [{"path": f"/a/{tool}.log", "sha256": "cd" * 32}], **extra}


def synth(wns=0.698, hold=0.068, met=True, void=False, latches=0) -> dict:
    return check("synth", "E4", f"setup WNS {wns:+.3f} ns", sdc="c.sdc", sdc_sha256="ab" * 32,
                 timing={"clock_period": 10.0, "wns": wns, "tns": 0.0, "hold_wns": hold, "worst_corner": "slow",
                         "timing_met": met, "cells": 1, "area": 2.0, "latches": latches, "void": void})


def review(who="rules", verdict="reject", findings=(("high", HIGH), ("medium", "latch x1"))) -> dict:
    return {"reviewer": who, "context": "ctx", "provider": "" if who == "rules" else "deepseek",
            "model": "" if who == "rules" else "deepseek-v4-flash", "note": "", "checklist": ["c1"],
            "findings": [{"severity": s, "text": t} for s, t in findings], "verdict": verdict, "format_ok": True,
            "text": "", "tool_calls": [], "usage": {}, "wall_s": 0, "stop_reason": "", "trace": "", "prompt": ""}


def receipt(**over) -> dict:
    r = {"run_id": "r1", "task": "check <the> core", "project": "torv", "provider": "deepseek",
         "model": "deepseek-v4-pro", "prompt": "system.v2", "stop_reason": "end", "status": "done",
         "turns": 3, "tool_calls": 3, "tool_errors": 0, "usage": {}, "wall_s": 1.0, "trace": "runs/r1.jsonl",
         "gate": {"status": "passed", "rewrites": 0, "decisions": [{"passed": True, "reasons": [], "max_level": "E4"}]},
         "checks_run": [check("lint", "E0", "0 error(s), 2 warning(s)"),
                        check("regress", "E2", "1 passed, 0 failed, 0 skipped"), synth(wns=2.0, hold=0.3)],
         "other_tool_calls": [],
         "not_run": {"lint_run": True, "described_but_not_run": [], "cases_not_run": ["alu", "branch"]},
         "final_claims": "lint: pass (E0) <ok>", "residual_risk": {"report": "pre-PnR only", "from_tools": [
             "test adequacy unknown: no coverage or mutation score yet"]}}
    r.update(over)
    return r


def awaiting(**over) -> dict:
    return human_gate.request_approval(receipt(**over), "final report",
                                       "the evidence gate passed; a person accepts the result")


def texts(s) -> str:
    return " | ".join(f"{i.level}:{i.text}{i.quote}" for i in s.issues)


def test_warnings_only_run_says_a_plain_approve_is_enough():
    s = summarize(awaiting())
    assert s.counts == {"fatal": 0, "error": 0, "warning": counts(assess(awaiting()))["warning"]}
    assert s.counts["warning"] >= 3  # lint warnings, partial regression, pre-PnR, no review, ...
    assert "plain approve" in s.needs
    assert s.commands == ["eda-agent approve r1", 'eda-agent reject r1 --reason "..."',
                          'eda-agent pending r1 --note "..."']
    assert "accept the agent's final report" in s.decision
    assert [c["level_text"] for c in s.checks] == ["parses / structural facts", "these tests passed",
                                                   "synthesis estimate"]
    assert s.evidence[0].startswith("Highest evidence reached: E4")
    assert any("2 case(s)" in x and "alu, branch" in x for x in s.not_checked)


def test_levels_follow_the_rules():
    r = awaiting(reviews=[review(), dict(review()), review("I3", "accept_with_findings",
                                                          (("high", HIGH), ("medium", "other"), ("info", "x")))])
    s = summarize(r)
    assert s.issues[0].level == "fatal" and s.issues[0].text == "Rule-based reviewer said reject"
    grouped = [i for i in s.issues if i.location == "rtl/torv_soc.sv:179"]
    assert len(grouped) == 1                                     # same finding from two reviewers: one row
    g = grouped[0]
    assert g.level == "error" and g.text == "multi_driven x1: signal 'torv_soc.apb_presetn' has multiple drivers"
    assert g.source == "rule-based reviewer, I3 (deepseek-v4-flash)" and len(g.details) == 2
    assert {(i.level, i.text) for i in s.issues} >= {("error", "latch x1"), ("warning", "x")}
    assert s.reviews[0]["who"].endswith("(×2, identical)") and s.reviews[0]["verdict"] == "reject"
    assert [i.level for i in s.issues] == sorted((i.level for i in s.issues), key=["fatal", "error", "warning"].index)
    assert "--override" in s.commands[0] and "Fatal" in s.needs
    assert len(RULES) > 20 and {lvl for _, lvl in RULES} == {"fatal", "error", "warning"}


def test_findings_are_grouped_by_place_with_one_line_and_full_texts():
    from eda_agent.agent.levels import first_sentence
    long = ("The synthesized top is `torv_cpu`, but lint ran on `torv_soc`; the SoC logic is never timed. "
            "Evidence: synth claim_scope (at rtl/top.sv:12)")
    r = receipt(reviews=[review("I2", "accept", (("medium", long),)),
                         review("I3", "accept", (("high", "Top mismatch at rtl/top.sv:12, see synth"),))])
    (g,) = [i for i in summarize(r).issues if i.location == "rtl/top.sv:12"]
    assert g.level == "error" and len(g.details) == 2 and "I2 (deepseek-v4-flash)" in g.source
    assert first_sentence(long) == "The synthesized top is `torv_cpu`, but lint ran on `torv_soc`"
    page = render_html(r)
    assert "show the 2 full text(s)" in page and '<span class="pill p-error">Error</span>' in page


def test_fatal_and_error_items_from_the_checks():
    r = receipt(checks_run=[check("lint", "E0", "2 error(s), 1 warning(s)", versions={}),
                            check("regress", "E2", "3 passed, 1 failed, 2 skipped", status="fail"),
                            synth(wns=0.5, hold=0.05, latches=2)],
                tool_errors=1, sdc_origin="hand translation of the course DC constraints",
                not_run={"lint_run": True, "described_but_not_run": ["hazard"], "cases_not_run": []},
                gate={"status": "passed", "rewrites": 1, "decisions": [
                    {"passed": False, "reasons": ["'correct' claims functional correctness"], "max_level": "E2"},
                    {"passed": True, "reasons": [], "max_level": "E2"}]})
    t = texts(summarize(r))
    for expected in ("fatal:regress ended with status fail", "fatal:regress: 1 case(s) failed",
                     "fatal:lint: 2 error(s)", "fatal:synth: 2 latch(es) inferred", "fatal:1 tool call(s)",
                     "error:regress: 2 case(s) skipped", "error:synth: small setup slack, +0.500 ns = 5%",
                     "error:synth: small hold slack, +0.050 ns", "error:No tool versions recorded for: lint",
                     "error:The SDC is not the design's own", "error:Read but not run: hazard",
                     "error:The gate sent the agent's report back 1 time(s)", "warning:lint: 1 warning(s)",
                     "warning:No independent review was run"):
        assert expected in t, expected


def test_timing_not_met_void_subtests_and_mutation():
    mut = check("mutate", "E3", "9 killed", metrics={"survived": 2, "likely_equivalent": 1, "error": 1,
                                                    "root_unchanged": False})
    r = receipt(checks_run=[check("regress", "E2", "1 passed, 0 failed, 0 skipped; testbench sub-tests: "
                                                   "88 passed, 2 failed of 90"), synth(wns=-0.2, met=False, void=True),
                            mut])
    t = texts(summarize(r))
    for expected in ("fatal:synth: timing NOT met", "error:synth: this timing result is void",
                     "fatal:regress: 2 testbench sub-test(s) failed", "which the testbench counts as 88 of 90",
                     "fatal:mutate: the original checkout changed", "error:mutate: 1 mutant(s) ended in an error",
                     "warning:mutate: 2 mutant(s) survived", "warning:mutate: 1 mutant(s) likely equivalent"):
        assert expected in t, expected


def test_gate_rejected_run_needs_override_and_the_override_is_flagged():
    r = receipt(status="pending", gate={"status": "rejected", "rewrites": 2, "decisions": []})
    s = summarize(r)
    assert "overrides the gate" in s.decision and s.issues[0].text == "The evidence gate rejected the final report"
    assert s.commands[0] == 'eda-agent approve r1 --override --reason "..."'
    human_gate.decide(r, "approve", by="A", reason="sentence was fine", override=True)
    s = summarize(r)
    assert s.commands == [] and "Already decided: approved by A" in s.decision
    assert any("approved over a Fatal item" in i.text for i in s.issues)
    assert r["approval"]["levels"]["fatal"] == 1 and r["approval"]["override"]


def test_receipt_changed_after_the_decision_is_fatal():
    r = awaiting()
    human_gate.decide(r, "approve", by="A")
    assert not any("changed after" in i.text for i in summarize(r).issues)
    r["final_claims"] += " edited later"
    assert summarize(r).issues[0].text.startswith("The receipt changed after its decision")


def test_nothing_from_the_details_is_lost():
    r = awaiting(reviews=[review(), review("I2", "accept", (("info", "fine <really>"),))])
    md = render_markdown(r)
    assert render_details(r) in md and "# Run receipt r1" in md and "**Gate:** passed" in md
    assert md.startswith("# Review summary · r1") and "**Fatal 1 · Error 2 · Warning" in md
    page = render_html(r)
    for text in ("scope of synth", "cd" * 32, "/a/regress.log", "lint: pass (E0) &lt;ok&gt;", "pre-PnR only",
                 "fine &lt;really&gt;", "c1", "runs/r1.jsonl", "check &lt;the&gt; core",
                 'eda-agent approve r1 --override --reason &quot;...&quot;', "Timing estimates (E4, pre-PnR)",
                 "cases in the list not run (2): alu, branch", "Fatal 1", "Error 2"):
        assert text in page, text
    assert "<the>" not in page and "<really>" not in page


def test_html_is_self_contained_and_english():
    page = render_html(awaiting(reviews=[review()]))
    assert page.startswith("<!doctype html>") and "<style>" in page and 'lang="en"' in page
    for banned in ("<script", "src=", "href=", "http://", "https://", "@import", "lang-zh", "审核"):
        assert banned not in page, banned


def test_markdown_summary_table_survives_pipe_in_summary():
    r = receipt(checks_run=[check("regress", "E2", "x | y")])
    row = next(line for line in render_summary_md(summarize(r)).splitlines() if line.startswith("| regress"))
    assert row.count("|") - row.count("\\|") == 6  # five cells


def test_write_receipt_adds_levels_outside_the_decided_hash(tmp_path):
    r = awaiting(reviews=[review()])
    sha = human_gate.content_sha256(r)
    js, md = write_receipt(r, tmp_path)
    saved = json.loads(js.read_text())
    assert saved["levels"]["counts"]["fatal"] == 1 and saved["levels"]["items"][0]["level"] == "fatal"
    assert {"id", "level", "text", "quote", "source"} <= set(saved["levels"]["items"][0])
    assert human_gate.content_sha256(saved) == sha
    before = js.read_bytes()
    md2, page = render_files(saved, tmp_path / "other")
    assert js.read_bytes() == before and page.name == "r1.receipt.html"


def test_cli_receipt_approve_pending_reject(tmp_path):
    write_receipt(awaiting(reviews=[review("I2", "accept", (("medium", "latch x1"),))]), tmp_path)
    cli = [sys.executable, "-m", "eda_agent.cli"]
    def run(*a):
        return subprocess.run([*cli, *a, "--runs-dir", str(tmp_path)], capture_output=True, text=True)
    res = run("receipt", "r1", "--print")
    assert res.returncode == 0 and "Fatal 0 · Error 1" in res.stdout
    res = run("pending", "r1", "--note", "waiting for the full regression", "--by", "A")
    assert res.returncode == 0 and "still open" in res.stdout
    saved = json.loads((tmp_path / "r1.receipt.json").read_text())
    assert saved["status"] == "awaiting_approval" and saved["approval"]["notes"][0]["note"].startswith("waiting")
    res = run("approve", "r1", "--by", "A")
    assert res.returncode == 2 and "1 Error item(s)" in res.stderr and "--reason" in res.stderr
    res = run("approve", "r1", "--by", "A", "--reason", "the latch is in third-party UART code")
    assert res.returncode == 0, res.stderr
    page = (tmp_path / "r1.receipt.html").read_text()
    assert "Already decided: approved by A" in page and "levels at the decision: Fatal 0 · Error 1" in page


def test_user_config_has_no_language_key(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('narrate = true\n')
    assert user_defaults(cfg) == {"narrate": True}
    cfg.write_text('lang = "zh"\n')
    with pytest.raises(ValueError, match="unknown key"):
        user_defaults(cfg)
