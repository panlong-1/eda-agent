"""perf: counters read from case output, the person-approved baseline, perf_killed mutants, gate R7."""
import json
from pathlib import Path

import pytest

from eda_agent.agent.gate import check_perf, check_report
from eda_agent.core.envelope import ToolResult
from eda_agent.core.project import PerfConfig, load_project
from eda_agent.tools import mutate as M
from eda_agent.tools.perf import compare, parse_perf, record_baseline, run_perf

TORV_PATTERNS = (r"\[PERF\] cycles=(?P<cycles>\d+) instr=(?P<instr>\d+) ipc=(?P<ipc>[\d.]+)",
                 r"\[PERF\] stall if=(?P<stall_if>\d+) id=(?P<stall_id>\d+) ex=(?P<stall_ex>\d+) flush=(?P<flush>\d+)")


def test_parse_torv_and_picorv32_lines():
    torv = ("[PASS] hazard\n[PERF] cycles=1520 instr=1200 ipc=0.789\n"
            "[PERF] stall if=3 id=40 ex=0 flush=12 dbus_r=0 dbus_w=0 sbus_r=0 sbus_w=0\n")
    assert parse_perf(torv, TORV_PATTERNS) == {"cycles": 1520, "instr": 1200, "ipc": 0.789,
                                               "stall_if": 3, "stall_id": 40, "stall_ex": 0, "flush": 12}
    pico = "Cycle counter ......... 429704\nInstruction counter .... 89030\nCPI: 4.82\n"
    pats = (r"Cycle counter \.+ (?P<cycles>\d+)", r"Instruction counter \.+ (?P<instr>\d+)", r"CPI: (?P<cpi>[\d.]+)")
    assert parse_perf(pico, pats) == {"cycles": 429704, "instr": 89030, "cpi": 4.82}


def test_perf_config_checks():
    with pytest.raises(ValueError, match="named group"):
        PerfConfig(patterns=(r"cycles=\d+",))
    with pytest.raises(ValueError, match="key 'cycles'"):
        PerfConfig(patterns=(r"n=(?P<n>\d+)",))
    with pytest.raises(ValueError, match="negative"):
        PerfConfig(patterns=(r"(?P<cycles>\d+)",), tolerance_pct=-1)


def test_compare_verdicts():
    cfg = PerfConfig(patterns=(r"(?P<cycles>\d+)",), tolerance_pct=1.0)
    rows = compare({"a": {"cycles": 102}, "b": {"cycles": 100}, "c": {"cycles": 95}, "d": {}, "e": {"cycles": 1}},
                   {"a": 100, "b": 100, "c": 100, "d": 100}, cfg)
    assert [rows[k]["verdict"] for k in "abcde"] == ["slower", "same", "faster", "no number", "no reference"]
    assert rows["a"]["delta"] == 2 and rows["a"]["delta_pct"] == 2.0
    ipc = PerfConfig(patterns=(r"(?P<ipc>[\d.]+)",), key="ipc", lower_is_better=False)
    assert compare({"a": {"ipc": 0.5}}, {"a": 0.8}, ipc)["a"]["verdict"] == "slower"


def _toy(tmp_path: Path, cycles: dict[str, int], perf_extra: str = "") -> Path:
    """Two cases printing a cycle count; 'b' fails when its marker file says so."""
    root = tmp_path / "design"
    (root / "tests").mkdir(parents=True, exist_ok=True)
    for case, n in cycles.items():
        (root / "tests" / f"{case}.sh").write_text(f"echo '[PERF] cycles={n} instr=10 ipc=0.5'\n"
                                                    f"test ! -f {case}.broken\n")
    toml = tmp_path / "toy.toml"
    toml.write_text(f'''name = "toy"
root = "{root}"
allowed_commands = ["sh"]
[versions]
sh = ["sh", "-c", "echo sh 1"]
[lint]
top = "t"
[regress]
cmd = ["sh"]
[[regress.cases]]
name = "a"
args = ["tests/a.sh"]
[[regress.cases]]
name = "b"
args = ["tests/b.sh"]
[perf]
patterns = ['\\[PERF\\] cycles=(?P<cycles>\\d+) instr=(?P<instr>\\d+) ipc=(?P<ipc>[\\d.]+)']
{perf_extra}
''')
    return toml


def _save(res: ToolResult, path: Path) -> Path:
    path.write_text(json.dumps(res.to_dict()))
    return path


def test_perf_without_and_with_baseline(tmp_path):
    toml = _toy(tmp_path, {"a": 100, "b": 200})
    p = load_project(toml)
    assert p.perf.baseline == str(tmp_path / "toy.perf-baseline.json")
    first = run_perf(p, log_dir=tmp_path / "logs")
    assert first.status == "pass" and first.evidence_level == "E2"
    assert first.metrics["cases"]["a"] == {"cycles": 100, "instr": 10, "ipc": 0.5}
    assert first.metrics["comparison"] is None and any("no approved baseline" in r for r in first.residual_risk)

    base, loosened = record_baseline(p, _save(first, tmp_path / "r1.json"), by="Pan", reason="first")
    assert base["cases"] == {"a": 100, "b": 200} and base["recorded_by"] == "Pan" and not loosened

    _toy(tmp_path, {"a": 100, "b": 230})  # b got slower
    second = run_perf(p, log_dir=tmp_path / "logs")
    assert second.status == "fail" and second.metrics["slower"] == ["b"]
    assert second.metrics["comparison"]["b"]["delta"] == 30 and second.metrics["comparison"]["b"]["delta_pct"] == 15.0
    assert second.metrics["baseline_sha256"] and second.diagnostics[0].code == "PERF_SLOWER"

    # Recording a slower result loosens the baseline: refused without --allow-loosen and a reason.
    with pytest.raises(ValueError, match="loosens"):
        record_baseline(p, _save(second, tmp_path / "r2.json"), by="Pan")
    with pytest.raises(ValueError, match="loosens"):
        record_baseline(p, tmp_path / "r2.json", by="Pan", allow_loosen=True)  # no reason
    with pytest.raises(ValueError, match="tolerance"):
        record_baseline(p, tmp_path / "r1.json", by="Pan", tolerance_pct=5)
    base, loosened = record_baseline(p, tmp_path / "r2.json", by="Pan", reason="b now checks more", allow_loosen=True)
    assert loosened == ["b: 200 -> 230 cycles"] and base["history"][0]["cases"]["b"] == 200

    _toy(tmp_path, {"a": 90, "b": 230})  # tightening needs nothing extra
    third = run_perf(p, log_dir=tmp_path / "logs")
    assert third.status == "pass" and third.metrics["faster"] == ["a"]
    assert record_baseline(p, _save(third, tmp_path / "r3.json"), by="Pan")[1] == []


def test_baseline_needs_passing_cases(tmp_path):
    toml = _toy(tmp_path, {"a": 100, "b": 200})
    (tmp_path / "design" / "b.broken").write_text("")
    p = load_project(toml)
    res = run_perf(p, log_dir=tmp_path / "logs")
    assert res.status == "fail" and res.metrics["failed_cases"] == ["b"] and "b" not in res.metrics["cases"]
    with pytest.raises(ValueError, match="failed"):
        record_baseline(p, _save(res, tmp_path / "r.json"), by="Pan")
    assert run_perf(load_project(tmp_path / "toy.toml"), cases=["zz"]).error_class == "bad_request"


def test_doctor_and_mcp(tmp_path):
    import asyncio

    from eda_agent.onboard.doctor import diagnose
    from eda_agent.server.mcp_server import build_server
    toml = _toy(tmp_path, {"a": 1, "b": 2}, perf_extra='cases = ["a", "gone"]')
    checks = {c.name: c for c in diagnose(toml)}
    assert checks["perf cases"].status == "fail" and "gone" in checks["perf cases"].detail
    assert checks["perf baseline"].status == "warn"
    toml = _toy(tmp_path, {"a": 1, "b": 2})
    tools = {t.name: t for t in asyncio.run(build_server(load_project(toml)).list_tools())}
    assert "E2" in tools["perf"].description and "baseline cannot be changed" in tools["perf"].description
    assert not any("baseline" in name for name in tools)  # no tool writes the baseline


def test_no_perf_section_refuses(tmp_path):
    toml = tmp_path / "p.toml"
    toml.write_text(f'name = "p"\nroot = "{tmp_path}"\n[lint]\ntop = "t"\n[regress]\ncmd = ["true"]\n')
    assert run_perf(load_project(toml)).error_class == "not_configured"


ADDER = "module add (input logic [3:0] a, b, c, output logic [3:0] y, z);\n  assign y = a + b;\n  assign z = a & c;\nendmodule\n"


@pytest.mark.skipif(M._avl_command() is None, reason="needs avl-core (pip install -e '.[mutation]')")
def test_mutant_that_only_changes_cycles_is_perf_killed(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "run_lint", lambda project, **_: ToolResult("lint", "pass", "E0", "", metrics={"errors": 0}))
    root = tmp_path / "design"
    (root / "rtl").mkdir(parents=True)
    (root / "rtl" / "add.sv").write_text(ADDER)
    (root / "tests").mkdir()
    # "sum" checks the adder's result; "cyc" always passes but its cycle count depends on the mask
    # logic, as a stall would: a bitwise mutant there keeps every result right and only costs cycles.
    (root / "tests" / "sum.sh").write_text("grep -q 'a + b;' rtl/add.sv\n")
    (root / "tests" / "cyc.sh").write_text(
        "if grep -q 'a & c;' rtl/add.sv; then n=100; else n=112; fi\necho \"[PERF] cycles=$n instr=10 ipc=0.1\"\n")
    toml = tmp_path / "toy.toml"
    toml.write_text(f'''name = "toy"
root = "{root}"
allowed_commands = ["sh"]
[lint]
top = "add"
[regress]
cmd = ["sh"]
[[regress.cases]]
name = "sum"
args = ["tests/sum.sh"]
[[regress.cases]]
name = "cyc"
args = ["tests/cyc.sh"]
[mutation]
files = ["rtl/add.sv"]
types = ["arith", "bitwise"]
jobs = 1
[perf]
patterns = ['\\[PERF\\] cycles=(?P<cycles>\\d+)']
''')
    res = M.run_mutate(load_project(toml), "rtl/add.sv", count=5, work_base=tmp_path / "work")
    by_type = {x["type"]: x for x in res.metrics["mutant_list"]}
    assert by_type["arith"]["status"] == "killed"
    pk = by_type["bitwise"]
    assert pk["status"] == "perf_killed" and pk["killed_by"] == ["cyc"]
    assert pk["perf"]["cyc"]["delta"] == 12 and "cyc +12 cycles (+12%)" in pk["detail"]
    m = res.metrics
    assert m["perf_killed"] == 1 and m["kill_rate"] == 0.5 and m["kill_rate_with_perf"] == 1.0
    assert m["perf_reference"] == {"cyc": 100} and res.status == "pass"
    assert any(d.code == "PERF_KILLED" for d in res.diagnostics)
    assert "perf_killed" in res.claim_scope
    rows = (Path(m["work_dir"]) / "kill_matrix.csv").read_text().splitlines()
    assert any(r.startswith(f"{pk['id']},") and ",-,P," in r for r in rows)  # columns: sum, cyc


# ---- gate R7 -----------------------------------------------------------------------------------

PERF_RESULT = {
    "tool": "perf", "status": "fail", "evidence_level": "E2",
    "metrics": {"key": "cycles", "cases": {"hazard": {"cycles": 1520, "ipc": 0.789}, "bypass": {"cycles": 900}},
                "comparison": {"hazard": {"cycles": 1520, "reference": 1500, "delta": 20, "delta_pct": 1.33,
                                          "verdict": "slower"},
                               "bypass": {"cycles": 900, "reference": 900, "delta": 0, "delta_pct": 0.0,
                                          "verdict": "same"}},
                "slower": ["hazard"], "faster": []},
}
RISK = "\n\nResidual risk: only two cases were measured, other programs may differ.\n"


def test_r7_needs_a_perf_result():
    reasons = check_perf("The change makes hazard 20 cycles faster.", [])
    assert reasons and "no perf result" in reasons[0]
    assert check_perf("This report says nothing about whether it is faster.", []) == []  # a limit, not a claim


def test_r7_cite_and_numbers():
    ok = "perf: hazard takes 1520 cycles, 20 cycles slower than the baseline (1.33% slower); bypass IPC is unchanged per perf."
    assert check_perf(ok, [PERF_RESULT]) == []
    assert "must name" in check_perf("hazard takes 1520 cycles.", [PERF_RESULT])[0]
    bad = check_perf("perf shows hazard at 1490 cycles.", [PERF_RESULT])
    assert bad and "1490" in bad[0]
    assert check_perf("perf: hazard IPC 0.79.", [PERF_RESULT]) == []  # rounding as printed


def test_r7_no_regression_needs_a_clean_baseline_comparison():
    reasons = check_perf("perf shows no performance regression.", [PERF_RESULT])
    assert reasons and "hazard" in reasons[0] and "slower" in reasons[0]
    clean = json.loads(json.dumps(PERF_RESULT))
    clean["metrics"]["slower"] = []
    assert check_perf("perf shows no performance regression.", [clean]) == []
    unbased = json.loads(json.dumps(PERF_RESULT))
    unbased["metrics"]["comparison"] = None
    assert "baseline" in check_perf("perf: not slower than before.", [unbased])[0]


def test_r7_in_the_whole_gate_and_mutate_numbers():
    mutate = {"tool": "mutate", "status": "pass", "evidence_level": "E0",
              "metrics": {"perf_key": "cycles", "perf_reference": {"cyc": 100}, "passed": 0,
                          "mutant_list": [{"perf": {"cyc": {"cycles": 112, "reference": 100, "delta": 12,
                                                            "delta_pct": 12.0, "verdict": "slower"}}}]}}
    d = check_report("mutate: mutant 2 adds 12 cycles on cyc (100 -> 112 cycles)." + RISK, [mutate])
    assert d.passed, d.reasons
    assert not check_report("mutate: mutant 2 adds 15 cycles." + RISK, [mutate]).passed


def test_reviewer_flags_perf_regression():
    from eda_agent.agent.reviewer import AuthorRun, rules_review
    run = AuthorRun(task="t", turns=[], tools=[{"name": "perf", "input": {}, "result": PERF_RESULT}],
                    final_text="r", model="m")
    texts = [f["text"] for f in rules_review(run, None).findings]
    assert any(t.startswith("perf: hazard is slower") for t in texts)
