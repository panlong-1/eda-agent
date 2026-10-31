"""write_case / score_tests on a toy design, the generation loop with a scripted model, gate R6."""
import asyncio
import json
from pathlib import Path

import pytest

from eda_agent.agent import testgen
from eda_agent.agent.gate import check_report, check_tests
from eda_agent.agent.human_gate import decide, request_approval
from eda_agent.agent.models import ScriptedAdapter, Turn
from eda_agent.agent.receipt import write_receipt
from eda_agent.core.envelope import ToolResult
from eda_agent.core.project import load_project
from eda_agent.tools import mutate as M
from eda_agent.tools import testcase as T

ADDER = "// adder → sum\nmodule add (input logic [3:0] a, b, c, output logic [3:0] y, z);\n" \
        "  assign y = a + b;\n  assign z = a & c;\nendmodule\n"
needs_avl = pytest.mark.skipif(M._avl_command() is None, reason="needs avl-core (pip install -e '.[mutation]')")


def _toy(tmp_path: Path) -> Path:
    """A design whose 'tests' are shell scripts reading the RTL; 'sum' checks y only."""
    root = tmp_path / "design"
    (root / "rtl").mkdir(parents=True)
    (root / "rtl" / "add.sv").write_text(ADDER)
    (root / "tests").mkdir()
    (root / "tests" / "sum.sh").write_text("# checks the adder\ngrep -q 'a + b;' rtl/add.sv\n")
    (root / "tests" / "run.sh").write_text('sh "tests/$2.sh"\n')  # run.sh --case <name>
    (root / "cases.list").write_text("sum  tests/sum.sh\n")
    toml = tmp_path / "toy.toml"
    toml.write_text(f'''name = "toy"
root = "{root}"
allowed_commands = ["sh"]
[lint]
top = "add"
[regress]
format = "exitcode"
cmd = ["sh", "tests/run.sh"]
case_list = "cases.list"
case_flag = "--case"
[mutation]
files = ["rtl/add.sv"]
types = ["arith", "bitwise"]
jobs = 2
[tests]
run = ["sh", "{{file}}"]
register = "{{name}}  {{path}}"
''')
    return toml


@pytest.fixture
def toy_run(tmp_path, monkeypatch):
    """(project, mutate run name, bitwise mutant id): the bitwise mutant survives 'sum'."""
    monkeypatch.chdir(tmp_path)  # generated/ and runs/work/ are relative to where eda-agent runs
    monkeypatch.setattr(M, "run_lint", lambda project, **_: ToolResult("lint", "pass", "E0", "", metrics={"errors": 0}))
    project = load_project(_toy(tmp_path))
    res = M.run_mutate(project, "rtl/add.sv", count=5, cases=["sum"], work_base=Path("runs/work"))
    survivor = next(x["id"] for x in res.metrics["mutant_list"] if x["type"] == "bitwise")
    assert res.metrics["mutant_list"][survivor - 1]["status"] == "survived"
    return project, Path(res.metrics["work_dir"]).name, survivor


@needs_avl
def test_context_write_and_score(toy_run):
    project, run, mid = toy_run
    ctx = T.run_test_context(project, run, mid)
    assert ctx["status"] == "ok" and ctx["mutant"]["line"] == 4 and ctx["test_suffix"] == ".sh"
    assert ctx["examples"][0]["case"] == "sum" and "a + b" in ctx["examples"][0]["text"]
    assert "a & c" in ctx["patch"] and "   4" in ctx["module_source"]

    bad = T.run_write_case(project, "r1", "wrong", "exit 1\n", run)
    assert bad.status == "fail" and bad.metrics["original"] == "FAIL" and bad.diagnostics[0].code == "FAILS_ON_ORIGINAL"
    assert T.run_write_case(project, "../x", "n", "true", run).error_class == "bad_request"

    weak = T.run_write_case(project, "r1", "weak", "true\n", run)
    assert weak.status == "pass" and Path(weak.metrics["path"]) == Path("generated/r1/weak.sh")
    s = T.run_score_tests(project, "r1", weak.metrics["path"], run, [mid])
    assert s.status == "fail" and s.evidence_level == "E2" and s.metrics["targets"][str(mid)]["verdict"] == "survived"

    good = T.run_write_case(project, "r1", "mask", "# eda-agent: target mutant\ngrep -q 'a & c;' rtl/add.sv\n", run)
    s = T.run_score_tests(project, "r1", good.metrics["path"], run, [mid])
    m = s.metrics
    assert s.status == "pass" and s.evidence_level == "E3" and m["kind"] == "functional"
    assert m["targets"][str(mid)] == {"runs": ["FAIL", "FAIL"], "verdict": "killed", "stable": True, "detail": "exit status 1"}
    assert mid in m["newly_killed"] and m["kill_rate_before"] == 0.5 and m["kill_rate_after"] == 1.0
    assert "E3 covers exactly those mutants" in s.claim_scope
    patch = Path(m["patch"]).read_text()
    assert "+++ b/tests/mask.sh" in patch and "+mask  tests/mask.sh" in patch and "--- a/cases.list" in patch
    assert (project.root / "cases.list").read_text() == "sum  tests/sum.sh\n"  # the design is never written

    # a test outside generated/<run_id>/ is refused
    assert T.run_score_tests(project, "r1", str(project.root / "tests/sum.sh"), run, [mid]).error_class == "bad_request"


@needs_avl
def test_a_timing_test_closes_its_target_by_cycle_count(toy_run):
    project, run, mid = toy_run
    from dataclasses import replace

    from eda_agent.core.project import PerfConfig
    project = replace(project, perf=PerfConfig(patterns=(r"cycles=(?P<cycles>\d+)",)))
    text = ("# eda-agent: target mutant 2 rtl/add.sv:4 kind=perf\n"
            "if grep -q 'a & c;' rtl/add.sv; then n=10; else n=12; fi\necho \"cycles=$n\"\n")
    w = T.run_write_case(project, "r3", "timing", text, run)
    s = T.run_score_tests(project, "r3", w.metrics["path"], run, [mid])
    m = s.metrics
    assert s.status == "pass" and s.evidence_level == "E3" and m["kind"] == "perf"
    assert m["targets"][str(mid)]["verdict"] == "perf_killed" and mid in m["perf_killed"]
    assert m["kill_rate_after"] == m["kill_rate_before"]  # not a functional kill
    assert m["kill_rate_with_perf_after"] > m["kill_rate_with_perf_before"]
    assert "kind=perf" in s.claim_scope
    r = testgen.TargetResult(mid, status="closed", test=w.metrics["path"], scores=[s.to_dict()])
    g = testgen.gate_results([r])
    assert g["decision"]["passed"], g["decision"]["reasons"]
    assert "by cycle count" in g["report"]


@needs_avl
def test_score_refuses_a_test_that_fails_on_the_original(toy_run):
    project, run, mid = toy_run
    path = Path("generated/r2/flaky.sh")
    path.parent.mkdir(parents=True)
    path.write_text("exit 1\n")
    s = T.run_score_tests(project, "r2", str(path), run, [mid])
    assert s.status == "fail" and s.metrics["original"] == ["FAIL", "FAIL"] and "is wrong" in s.claim_scope


def _fake_execute(script: dict):
    """Answers MCP calls from a dict: tool name -> list of results (consumed in order)."""
    calls = []

    async def execute(name, args):
        calls.append((name, args))
        return json.dumps(script[name].pop(0)), False
    return execute, calls


CTX = {"status": "ok", "mutate_run": "mr", "patch": "-a\n+b\n", "module_source": "1 x", "examples": [],
       "cases_that_did_not_catch_it": ["sum"], "judged_by": "exit 0", "test_suffix": ".sh",
       "mutant": {"id": 2, "type": "bitwise", "line": 4, "file": "rtl/add.sv", "status": "survived",
                  "changes": [{"line": 4, "before": "a & c", "after": "a | c"}]}}


def _score(ok: bool) -> dict:
    return {"tool": "score_tests", "status": "pass" if ok else "fail", "evidence_level": "E3" if ok else "E2",
            "summary": "s", "metrics": {"targets": {"2": {"runs": ["FAIL", "FAIL"] if ok else ["PASS", "PASS"],
                                                          "verdict": "killed" if ok else "survived"}},
                                        "original": ["PASS", "PASS"], "killed": [2] if ok else [],
                                        "newly_killed": [2] if ok else [], "scored": 2,
                                        "kill_rate_before": 0.5, "kill_rate_after": 1.0 if ok else 0.5,
                                        "patch": "generated/r/m2_a2.patch" if ok else None}}


def test_loop_feeds_back_and_closes_the_gap():
    code = "```sh\ntrue\n```"
    model = ScriptedAdapter([Turn(stop="end", text="no block"), Turn(stop="end", text=code),
                             Turn(stop="end", text=code), Turn(stop="end", text=code)])
    wc_fail = {"status": "fail", "summary": "FAILS", "metrics": {"path": "generated/r/m2_a1.sh", "original": "FAIL"},
               "diagnostics": [{"msg": "boom"}]}
    wc_ok = {"status": "pass", "summary": "passes", "metrics": {"path": "generated/r/m2_a1.sh", "original": "PASS"}}
    execute, calls = _fake_execute({"test_context": [CTX], "write_case": [wc_fail, wc_ok, wc_ok],
                                    "score_tests": [_score(False), _score(True)]})
    usage: dict = {}
    r = asyncio.run(testgen.close_gap(model, execute, "sys", run_id="r", mutate_run="mr", mutant=2, usage=usage))
    assert r.status == "closed" and r.patch.endswith(".patch") and len(r.scores) == 2
    assert [t.original for t in r.tries] == ["no code", "FAIL", "PASS", "PASS"]
    assert [c[0] for c in calls] == ["test_context", "write_case", "write_case", "score_tests", "write_case", "score_tests"]
    fed = [m["content"] for m in model.messages if m.get("role") == "user"] if hasattr(model, "messages") else []
    assert fed == [] or any("FAILS on the unmodified design" in f for f in fed)


def test_loop_stops_after_replies_cut_off_at_the_limit():
    model = ScriptedAdapter([Turn(stop="max_tokens", text="")] * 2)
    execute, calls = _fake_execute({"test_context": [CTX]})
    r = asyncio.run(testgen.close_gap(model, execute, "sys", run_id="r", mutate_run="mr", mutant=2, usage={}))
    assert r.status == "not closed" and "cut off at the output limit 2 times" in r.note
    assert [t.original for t in r.tries] == ["cut off (output limit)"] * 2 and len(calls) == 1


def test_loop_reports_an_unclosed_gap():
    code = "```sh\ntrue\n```"
    model = ScriptedAdapter([Turn(stop="end", text=code)] * 2)
    wc_ok = {"status": "pass", "summary": "passes", "metrics": {"path": "p.sh", "original": "PASS"}}
    execute, _ = _fake_execute({"test_context": [CTX], "write_case": [wc_ok, wc_ok],
                                "score_tests": [_score(False), _score(False)]})
    r = asyncio.run(testgen.close_gap(model, execute, "sys", run_id="r", mutate_run="mr", mutant=2, usage={},
                                      attempts=2))
    assert r.status == "not closed" and "could not close this gap: 2 attempts" in r.note


def test_report_passes_the_gate_and_receipt_round_trip(tmp_path):
    r = testgen.TargetResult(2, status="closed", test="generated/r/m2_a2.sh", patch="generated/r/m2_a2.patch",
                             scores=[_score(True)], review={"verdict": "accept"}, diff="-a\n+b\n")
    g = testgen.gate_results([r, testgen.TargetResult(5, note="could not close this gap: 3 attempts")])
    assert g["decision"]["passed"], g["decision"]["reasons"]
    assert "kill rate 50% -> 100%" in g["report"]
    receipt = {"kind": "testgen", "run_id": "tg1", "project": "toy", "provider": "p", "model": "m",
               "prompt": testgen.PROMPT, "review_prompt": testgen.REVIEW_PROMPT, "mutate_run": "mr", "targets": [2, 5],
               "results": [testgen.to_dict(r)], "gate": g, "final_claims": g["report"], "usage": {}, "wall_s": 1,
               "status": "done"}
    request_approval(receipt, "new tests", "why")
    js, md = write_receipt(receipt, tmp_path)
    assert "Mutant 2: closed" in md.read_text() and "git apply" in md.read_text()
    decide(receipt, "approve", by="Pan")
    assert receipt["status"] == "approved"


def test_parse_review():
    v = testgen.parse_review("architectural_only: yes\nexpected_values_checked: yes\nverdict: accept\nnotes: ok")
    assert v["verdict"] == "accept" and v["format_ok"]
    assert not testgen.parse_review("looks fine")["format_ok"]


# ---- gate R6 ------------------------------------------------------------------------------------

SCORE = _score(True)
MUTATE = {"tool": "mutate", "status": "fail", "evidence_level": "E0",
          "metrics": {"kill_rate": 0.6875, "mutant_list": [{"id": 6, "status": "killed"}, {"id": 3, "status": "survived"}]}}
RISK = "\n\nResidual risk: one module's mutants only; nothing about other bugs.\n"


def test_r6_needs_score_tests():
    assert "no score_tests" in check_tests("The new test catches the bug.", [])[0]
    assert "higher kill rate" in check_tests("The kill rate rose to 100%.", [MUTATE])[0]
    assert check_tests("score_tests: the new test kills mutant 2; kill rate 50% -> 100%.", [SCORE]) == []
    assert "must name" in check_tests("The new test kills mutant 2.", [SCORE])[0]


def test_r6_numbers_and_ids():
    assert "mutant 3" in check_tests("score_tests: the test kills mutant 3.", [SCORE])[0]
    assert "75%" in check_tests("score_tests: kill rate rose from 50% to 75%.", [SCORE])[0]
    assert check_tests("mutate: mutant 6 is killed, 68.75% overall.", [MUTATE]) == []
    assert check_tests("mutate: mutant 6 is killed, 69% overall.", [MUTATE]) == []  # 68.75% rounded
    assert check_tests("mutate: mutant 6 is killed, 70% overall.", [MUTATE])
    assert "mutant 3" in check_tests("mutate: mutant 3 was killed.", [MUTATE])[0]
    # a stated limit is not a claim
    assert check_tests("The existing cases do not catch mutant 3.", [MUTATE]) == []


def test_r6_in_the_whole_gate():
    assert check_report("score_tests: the new test kills mutant 2 (kill rate 50% -> 100%)." + RISK, [SCORE]).passed
    assert not check_report("The new test catches the hazard bug." + RISK, [SCORE]).passed
