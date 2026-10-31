"""mutate: avl-core's offsets, carrying edits back, the copies, and one whole run on a toy design.

The toy run needs avl-core in the venv (pip install -e '.[mutation]') but no simulator: lint is
stubbed and the "regression" is two shell scripts that grep the RTL.
"""
import asyncio
import csv
from pathlib import Path

import pytest

from eda_agent.core.envelope import ToolResult
from eda_agent.core.project import MutationConfig, load_project
from eda_agent.tools import mutate as M

# A '→' before the operators is what shifts avl-core's offsets on TORV's files.
ADDER = """\
// adder → sum, and a mask (spec §1)
module add (input logic [3:0] a, b, c, output logic [3:0] y, z);
  assign y = a + b;  // sum → y
  assign z = a & c;
endmodule
"""


def test_stand_in_keeps_byte_offsets():
    text = "// a → b — c\nassign y = a + b;\n"
    stand = M.ascii_stand_in(text)
    assert stand.isascii() and len(stand) == len(text.encode())
    assert stand.index("+") == text.encode().index(b"+")


def test_carry_back_keeps_non_ascii_and_moves_the_edit():
    original = ADDER.encode()
    stand = M.ascii_stand_in(ADDER)
    mutated = stand.replace("a + b;", "a - b;  /* AVL MUTATION - arith: + -> - */")
    body = M.carry_back(original, stand, mutated).decode()
    assert "a - b;" in body and "// sum → y" in body and body.startswith("// adder → sum")
    # A whole inserted line (avl's pipeline class adds a register declaration) is carried too.
    inserted = stand.replace("  assign z", "  logic [3:0] avl_pipe_1;\n  assign z")
    assert "logic [3:0] avl_pipe_1;\n  assign z = a & c;" in M.carry_back(original, stand, inserted).decode()


def test_describe_change_hides_avl_marker():
    before = ADDER.encode()
    after = ADDER.replace("a & c;", "a | c;  /* AVL MUTATION - bitwise: & -> | */").encode()
    assert M.describe_change(before, after) == [{"line": 4, "before": "  assign z = a & c;",
                                                 "after": "  assign z = a | c;"}]


def test_env_leaks(tmp_path):
    root = tmp_path / "design"
    env = {"A": f"{root}/rtl", "B": f"{root}_copy", "PATH": f"/usr/bin:{root}/bin", "PWD": str(root), "C": "x"}
    assert M.env_leaks(env, root) == ["A", "PATH"]


def test_copy_skips_and_cleans_excluded(tmp_path):
    root = tmp_path / "d"
    for p in ("rtl/a.sv", "build/x.o", "regress/results/r/s.rpt", "regress/cases/l.list"):
        (root / p).parent.mkdir(parents=True, exist_ok=True)
        (root / p).write_text("x")
    dest = tmp_path / "copy"
    M._copy_design(root, dest, ("build", "/regress/results"))
    assert (dest / "rtl/a.sv").is_file() and (dest / "regress/cases/l.list").is_file()
    assert not (dest / "build").exists() and not (dest / "regress/results").exists()
    (dest / "build").mkdir()
    M._clean(dest, ("build", "regress/results"))
    assert not (dest / "build").exists() and (dest / "rtl").is_dir()


def test_mutation_config(tmp_path):
    base = 'name = "p"\nroot = "{r}"\n[lint]\ntop = "t"\n[regress]\ncmd = ["true"]\n'.format(r=tmp_path)
    toml = tmp_path / "p.toml"
    toml.write_text(base)
    assert load_project(toml).mutation is None
    toml.write_text(base + '[mutation]\nfiles = ["rtl/a.sv"]\ncases = ["c"]\njobs = 3\n')
    assert load_project(toml).mutation == MutationConfig(files=("rtl/a.sv",), cases=("c",), jobs=3)
    toml.write_text(base + "[mutation]\njob = 3\n")
    with pytest.raises(ValueError, match="unknown \\[mutation\\] key"):
        load_project(toml)
    toml.write_text(base + "[mutation]\njobs = 0\n")
    with pytest.raises(ValueError, match="positive"):
        load_project(toml)


def test_kill_rate_and_matrix(tmp_path):
    ms = [M.Mutant(1, "arith", "m", 3, [{"line": 3, "before": "a + b", "after": "a - b"}], tmp_path, "killed", ["c1"]),
          M.Mutant(2, "bitwise", "m", 4, [], tmp_path, "survived", full={"status": "survived", "killed_by": []}),
          M.Mutant(3, "width", "m", 5, [], tmp_path, "invalid"),
          M.Mutant(4, "unary", "m", 6, [], tmp_path, "likely_equivalent")]
    assert M.kill_rate(ms) == 0.5 and M.kill_rate(ms[2:]) is None
    M.write_matrix(ms, ["c1", "c2"], tmp_path / "m.csv")
    rows = list(csv.reader((tmp_path / "m.csv").open()))
    assert rows[0] == ["id", "line", "type", "status", "change", "c1", "c2", "full_regression"]
    assert rows[1][3:] == ["killed", "a + b -> a - b", "K", "-", ""]
    assert rows[2][-1] == "survived" and rows[3][5:7] == ["", ""]


def test_refusals(tmp_path):
    toml = tmp_path / "p.toml"
    (tmp_path / "rtl").mkdir()
    (tmp_path / "rtl" / "add.sv").write_text(ADDER)
    toml.write_text(f'name = "p"\nroot = "{tmp_path}"\n[lint]\ntop = "t"\n[regress]\ncmd = ["true"]\n')
    assert M.run_mutate(load_project(toml), "rtl/add.sv").error_class == "not_configured"
    toml.write_text(toml.read_text() + '[mutation]\nfiles = ["rtl/add.sv"]\n')
    p = load_project(toml)
    for bad in ("../x.sv", "rtl/other.sv", "/etc/passwd", "p.toml"):
        assert M.run_mutate(p, bad).error_class == "bad_request", bad
    assert M.run_mutate(p, "rtl/add.sv", count=0).error_class == "bad_request"
    assert M.run_mutate(p, "rtl/add.sv", cases=["nope"]).error_class == "bad_request"


def _toy(tmp_path: Path) -> Path:
    root = tmp_path / "design"
    (root / "rtl").mkdir(parents=True)
    (root / "rtl" / "add.sv").write_text(ADDER)
    (root / "tests").mkdir()
    # "sum" checks the adder; "idle" checks nothing, so it kills no mutant.
    (root / "tests" / "sum.sh").write_text("grep -q 'assign y = a + b;' rtl/add.sv\n")
    (root / "tests" / "idle.sh").write_text("true\n")
    (root / "build").mkdir()
    (root / "build" / "junk").write_text("not copied")
    toml = tmp_path / "toy.toml"
    toml.write_text(f'''name = "toy"
root = "{root}"
allowed_commands = ["sh"]
[versions]
sh = ["sh", "-c", "echo sh 1"]
[lint]
top = "add"
files = ["rtl/add.sv"]
[regress]
cmd = ["sh"]
[[regress.cases]]
name = "sum"
args = ["tests/sum.sh"]
[[regress.cases]]
name = "idle"
args = ["tests/idle.sh"]
[mutation]
files = ["rtl/add.sv"]
cases = ["sum", "idle"]
types = ["arith", "bitwise"]
copy_exclude = ["build"]
jobs = 2
''')
    return toml


def _lint_stub(project, max_diagnostics=20, **_):
    """Stands in for Verilator: an `assign` without a ';' does not compile."""
    text = (project.root / "rtl" / "add.sv").read_text()
    errors = sum(1 for ln in text.splitlines() if ln.strip().startswith("assign") and ";" not in ln)
    return ToolResult("lint", "fail" if errors else "pass", "E0", "", metrics={"errors": errors})


@pytest.mark.skipif(M._avl_command() is None, reason="needs avl-core (pip install -e '.[mutation]')")
def test_toy_run_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "run_lint", _lint_stub)
    project = load_project(_toy(tmp_path))
    before = (project.root / "rtl" / "add.sv").read_bytes()
    res = M.run_mutate(project, "rtl/add.sv", count=5, work_base=tmp_path / "work")

    assert res.tool == "mutate" and res.evidence_level == "E0"
    m = res.metrics
    by_type = {x["type"]: x for x in m["mutant_list"]}
    assert by_type["arith"]["status"] == "killed" and by_type["arith"]["killed_by"] == ["sum"]
    assert by_type["arith"]["changes"][0]["after"].strip() == "assign y = a - b;  // sum → y"
    assert by_type["bitwise"]["status"] == "survived"
    assert res.status == "fail" and m["killed"] == 1 and m["survived"] == 1 and m["kill_rate"] == 0.5
    assert m["root_unchanged"] and (project.root / "rtl" / "add.sv").read_bytes() == before
    assert res.diagnostics[0].code == "SURVIVED" and res.diagnostics[0].line == 4
    assert "assign z = a & c; -> assign z = a | c;" in res.diagnostics[0].msg
    assert any("not checked for equivalence" in r for r in res.residual_risk)
    assert res.tool_versions["avl-core"] != "not installed" and res.tool_versions["sh"] == "sh 1"
    work = Path(m["work_dir"])
    assert not (work / "w0" / "build").exists()  # excluded from the copy
    assert all(a["sha256"] for a in res.artifacts)
    # The mutant files themselves keep the non-ASCII comment and compile (no garbled splice).
    for x in m["mutant_list"]:
        assert "// adder → sum" in Path(x["path"]).read_text()


@pytest.mark.skipif(M._avl_command() is None, reason="needs avl-core (pip install -e '.[mutation]')")
def test_golden_failure_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "run_lint", _lint_stub)
    toml = _toy(tmp_path)
    (tmp_path / "design" / "tests" / "idle.sh").write_text("exit 1\n")
    res = M.run_mutate(load_project(toml), "rtl/add.sv", count=2, work_base=tmp_path / "work")
    assert res.status == "error" and res.error_class == "golden_failed"


@pytest.mark.skipif(M._avl_command() is None, reason="needs avl-core (pip install -e '.[mutation]')")
def test_env_pointing_at_original_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "run_lint", _lint_stub)
    toml = _toy(tmp_path)
    monkeypatch.setenv("DESIGN_HOME", str(tmp_path / "design"))
    M.project_env.cache_clear()
    try:
        res = M.run_mutate(load_project(toml), "rtl/add.sv", count=2, work_base=tmp_path / "work")
    finally:
        M.project_env.cache_clear()
    assert res.status == "error" and "DESIGN_HOME" in res.summary


def test_doctor_checks_mutation(tmp_path):
    from eda_agent.onboard.doctor import diagnose
    toml = _toy(tmp_path)
    toml.write_text(toml.read_text().replace('cases = ["sum", "idle"]', 'cases = ["sum", "gone"]'))
    checks = {c.name: c for c in diagnose(toml)}
    assert checks["mutation files"].status == "ok"
    assert checks["mutation cases"].status == "fail" and "gone" in checks["mutation cases"].detail
    assert checks["avl-core"].status == ("ok" if M._avl_command() else "fail")


def test_mcp_tool_registered(tmp_path):
    from eda_agent.server.mcp_server import build_server
    toml = tmp_path / "p.toml"
    toml.write_text(f'name = "p"\nroot = "{tmp_path}"\n[lint]\ntop = "t"\n[regress]\ncmd = ["true"]\n')
    tools = asyncio.run(build_server(load_project(toml)).list_tools())
    tool = next(t for t in tools if t.name == "mutate")
    assert "not configured" in tool.description and "E0" in tool.description
