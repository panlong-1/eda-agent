import json
import subprocess
import sys

import pytest

from eda_agent.agent import human_gate
from eda_agent.agent.receipt import render_details, render_files, render_markdown, write_receipt
from eda_agent.agent.receipt_view import render_html, render_summary_md, summarize
from eda_agent.core.settings import user_defaults

HIGH = "multi_driven x1: signal 'torv_soc.apb_presetn' has multiple drivers (at rtl/torv_soc.sv:179)"


def check(tool, level, summary, status="pass", versions=None, **extra) -> dict:
    return {"tool": tool, "args": {}, "status": status, "evidence_level": level, "summary": summary,
            "claim_scope": f"scope of {tool}", "tool_versions": {"v": "1"} if versions is None else versions,
            "artifacts": [{"path": f"/a/{tool}.log", "sha256": "cd" * 32}], **extra}


def synth(wns=0.698, hold=0.068, met=True, void=False) -> dict:
    return check("synth", "E4", f"setup WNS {wns:+.3f} ns", sdc="c.sdc", sdc_sha256="ab" * 32,
                 timing={"clock_period": 10.0, "wns": wns, "tns": 0.0, "hold_wns": hold, "worst_corner": "slow",
                         "timing_met": met, "cells": 1, "area": 2.0, "latches": 0, "void": void})


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
         "checks_run": [check("lint", "E0", "0 error(s)"), check("regress", "E2", "1 passed, 0 failed, 0 skipped"),
                        synth()],
         "other_tool_calls": [],
         "not_run": {"lint_run": True, "described_but_not_run": [], "cases_not_run": ["alu", "branch"]},
         "final_claims": "lint: pass (E0) <ok>", "residual_risk": {"report": "pre-PnR only", "from_tools": [
             "test adequacy unknown: no coverage or mutation score yet", "a sentence no table knows"]}}
    r.update(over)
    return r


def awaiting(**over) -> dict:
    return human_gate.request_approval(receipt(**over), "final report",
                                       "the evidence gate passed; a person accepts the result")


def levels(s) -> list[str]:
    return [i.level for i in s.attention]


def test_pending_summary_says_what_is_decided_and_how():
    s = summarize(awaiting(), "en")
    assert "accept the agent's final report" in s.decision and "final report" in s.decision
    assert s.commands == ["eda-agent approve r1", 'eda-agent reject r1 --reason "..."']
    assert [c["level_text"] for c in s.checks] == ["parses / structural facts", "these tests passed",
                                                   "synthesis estimate"]
    assert s.evidence[0].startswith("Highest evidence reached: E4")
    assert "On function the highest is E2" in s.evidence[1]
    assert any("2 case(s)" in x and "alu, branch" in x for x in s.not_checked)
    assert "a sentence no table knows" in s.not_checked  # unknown risks are kept as written
    assert "3 of 3 check(s) passed" in s.headline


def test_chinese_summary_keeps_quotes_in_their_language():
    r = awaiting(reviews=[review()])
    s = summarize(r, "zh")
    assert "批准或拒绝" in s.decision and "证据门已通过" in s.decision
    assert "测试是否充分未知：还没有覆盖率或变异测试分数" in s.not_checked
    md = render_summary_md(s)
    assert "批准前请先看这些" in md and HIGH in md and "eda-agent approve r1" in md
    assert "E2（这些测试通过了）" in md
    with pytest.raises(ValueError, match="language"):
        summarize(r, "fr")


def test_attention_orders_reviewer_reject_first_and_dedupes_findings():
    rules = review()
    r = awaiting(reviews=[rules, dict(rules), review("I3", "accept_with_findings",
                                                    (("high", HIGH), ("medium", "other"), ("low", "x")))])
    s = summarize(r, "en")
    assert s.attention[0].level == "stop" and "REJECT" in s.attention[0].text
    assert [i.quote for i in s.attention].count(HIGH) == 1        # same finding from two reviewers: once
    assert any("2 more medium finding" in i.text for i in s.attention)
    assert s.reviews[0]["who"].endswith("(×2, identical)") and s.reviews[0]["verdict"] == "reject"
    assert levels(s) == sorted(levels(s), key=["stop", "warn", "info"].index)


def test_attention_flags_timing_versions_cases_and_process():
    r = receipt(checks_run=[check("lint", "E0", "0 error(s)", versions={}),
                            check("regress", "E2", "3 passed, 1 failed, 2 skipped", status="fail"),
                            synth(wns=0.5, hold=0.05)],
                tool_errors=1, sdc_origin="hand translation of the course DC constraints",
                not_run={"lint_run": True, "described_but_not_run": ["hazard"], "cases_not_run": []},
                gate={"status": "passed", "rewrites": 1, "decisions": [
                    {"passed": False, "reasons": ["'correct' claims functional correctness"], "max_level": "E2"},
                    {"passed": True, "reasons": [], "max_level": "E2"}]})
    s = summarize(r, "en")
    text = " | ".join(f"{i.level}:{i.text}{i.quote}" for i in s.attention)
    for expected in ("stop:regress ended with status fail", "stop:regress: 1 case(s) failed",
                     "warn:regress: 2 case(s) skipped", "warn:synth: small setup slack, +0.500 ns = 5%",
                     "warn:synth: small hold slack, +0.050 ns", "warn:No tool versions recorded for: lint",
                     "1 tool call(s) ended in an error", "hand translation of the course DC constraints",
                     "Read but not run: hazard", "sent the agent's report back 1 time(s)",
                     "'correct' claims functional correctness", "No independent review was run"):
        assert expected in text, expected
    assert s.gate_reasons == ["'correct' claims functional correctness"]


def test_timing_not_met_and_void_and_subtests():
    r = receipt(checks_run=[check("regress", "E2", "1 passed, 0 failed, 0 skipped; testbench sub-tests: "
                                                   "88 passed, 2 failed of 90"), synth(wns=-0.2, met=False, void=True)])
    s = summarize(r, "en")
    text = " | ".join(f"{i.level}:{i.text}" for i in s.attention)
    assert "stop:synth: timing NOT met" in text and "warn:synth: this timing result is void" in text
    assert "1 case(s) passed, which the testbench counts as 88 of 90 sub-tests (2 failed)" in text


def test_gate_rejected_run_offers_override_and_overridden_approval_is_flagged():
    r = receipt(status="pending", gate={"status": "rejected", "rewrites": 2, "decisions": []})
    s = summarize(r, "en")
    assert "overrides the gate" in s.decision
    assert 'eda-agent approve r1 --override-gate --reason "..."' in s.commands
    assert s.attention[0].text == "The evidence gate rejected the final report."
    human_gate.decide(r, "approve", by="A", reason="sentence was fine", override_gate=True)
    s = summarize(r, "en")
    assert s.commands == [] and "Already decided: approved by A" in s.decision
    assert any("approved over the evidence gate" in i.text for i in s.attention)


def test_receipt_changed_after_the_decision_is_flagged():
    r = awaiting()
    human_gate.decide(r, "approve", by="A")
    assert not any("changed after the decision" in i.text for i in summarize(r).attention)
    r["final_claims"] += " edited later"
    assert summarize(r).attention[0].text.startswith("The receipt content changed after the decision")


def test_nothing_from_the_details_is_lost():
    r = awaiting(reviews=[review(), review("I2", "accept", (("info", "fine <really>"),))])
    md = render_markdown(r, "zh")
    assert md.endswith(render_details(r))  # the full receipt, unchanged, below the summary
    assert "# Run receipt r1" in md and "**Gate:** passed" in md
    page = render_html(r, "zh")
    for text in ("scope of synth", "cd" * 32, "/a/regress.log", "lint: pass (E0) &lt;ok&gt;", "pre-PnR only",
                 "fine &lt;really&gt;", "c1", "runs/r1.jsonl", "check &lt;the&gt; core", "eda-agent approve r1",
                 "Timing estimates (E4, pre-PnR)", "cases in the list not run (2): alu, branch"):
        assert text in page, text
    assert "<the>" not in page and "<really>" not in page


def test_html_is_self_contained():
    page = render_html(awaiting(reviews=[review()]), "en")
    assert page.startswith("<!doctype html>") and "<style>" in page
    for banned in ("<script", "src=", "href=", "http://", "https://", "@import"):
        assert banned not in page, banned


def test_markdown_summary_table_survives_pipe_in_summary():
    r = receipt(checks_run=[check("regress", "E2", "x | y")])
    row = next(line for line in render_summary_md(summarize(r)).splitlines() if line.startswith("| regress"))
    assert row.count("|") - row.count("\\|") == 5  # four cells


def test_render_files_leaves_the_json_alone(tmp_path):
    r = awaiting()
    js, md = write_receipt(r, tmp_path, "zh")
    before = js.read_bytes()
    md2, page = render_files(json.loads(before), tmp_path, "en")
    assert js.read_bytes() == before and md2 == md and page.name == "r1.receipt.html"
    assert md.read_text().startswith("# Review summary · r1")


def test_cli_receipt_rerenders_and_approve_refreshes_the_html(tmp_path):
    write_receipt(awaiting(), tmp_path)
    cli = [sys.executable, "-m", "eda_agent.cli"]
    out = tmp_path / "out"
    res = subprocess.run([*cli, "receipt", "r1", "--runs-dir", str(tmp_path), "--out-dir", str(out), "--lang", "zh",
                          "--print"], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert "审核摘要" in res.stdout and "审核摘要" in (out / "r1.receipt.html").read_text()
    assert not (out / "r1.receipt.json").exists()
    missing = subprocess.run([*cli, "receipt", "nope", "--runs-dir", str(tmp_path)], capture_output=True, text=True)
    assert missing.returncode == 2 and "no receipt" in missing.stderr
    ok = subprocess.run([*cli, "approve", "r1", "--by", "A", "--runs-dir", str(tmp_path), "--lang", "zh"],
                        capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr
    page = (tmp_path / "r1.receipt.html").read_text()
    assert "已经决定" in page and "A 于" in page


def test_lang_in_user_config(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('lang = "zh"\n')
    assert user_defaults(cfg) == {"lang": "zh"}
    cfg.write_text('lang = "fr"\n')
    with pytest.raises(ValueError, match="lang"):
        user_defaults(cfg)
