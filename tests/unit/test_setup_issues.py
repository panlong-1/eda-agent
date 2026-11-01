"""Setup diagnosis (plan §0.11): the rule table on tool output, file checks, [setup] steps, run_setup,
regress/synth reporting "environment not ready", levels, init proposals and doctor --deep.

Every log line here is synthetic (made-up designs: widget, acme); no design's real output is copied.
"""

import stat
from pathlib import Path

import pytest

from eda_agent.agent.levels import assess
from eda_agent.core.project import load_project
from eda_agent.core.setup_issues import (LOG_RULES, Context, classify, make_context, path_issues, precheck,
                                         producer, read_makefile, summary, vendor_issues, fusesoc_issues)
from eda_agent.tools.backends.librelane import LOG_RULES as PNR_RULES, setup_issues as pnr_setup_issues

# rule id -> (a synthetic log line, the category it must give, a word the "missing" text must contain)
SAMPLES = {
    "sim.include": ("%Error: rtl/top.sv:3:10: Cannot find include file: 'widget_defs.svh'", "missing_file",
                    "widget_defs.svh"),
    "sim.file": ("ERROR: Setup failed : Cannot find rtl/widget_cfg.sv in :", "missing_file", "widget_cfg.sv"),
    "sim.file_before": ("rtl/widget_alu.sv: No such file or directory", "missing_file", "widget_alu.sv"),
    "sim.make_rule": ("make: *** No rule to make target 'build/gen/boot.dat', needed by 'sim'.  Stop.",
                      "missing_file", "boot.dat"),
    "sim.module": ("%Error: tb/tb_top.sv:12:3: Cannot find file containing module: 'widget_core'",
                   "unknown_module", "widget_core"),
    "sim.package": ("%Error: rtl/a.sv:1:8: Package/class 'widget_pkg' not found, and needs to be predeclared",
                    "unknown_package", "widget_pkg"),
    "sim.readmem": ("%Warning: tb/tb.sv:40: $readmemh file not found: 'build/fw/prog.hex'", "memory_init",
                    "prog.hex"),
    "sim.fusesoc_deps": ("Requirements: 'acme_widget == 1.0.0-0' <- 'acme__corp.org__fifo_lib >= 0-0'",
                         "vendor_ip", "acme-corp.org::fifo_lib"),
    "sim.fusesoc_core": ("Failed to resolve dependencies for acme:ip:widget:1.0.0", "vendor_ip", "acme:ip:widget"),
    "sim.command": ("bash: riscv64-unknown-elf-gcc: command not found", "tool", "riscv64-unknown-elf-gcc"),
    "sim.python_module": ("ModuleNotFoundError: No module named 'hjson'", "tool", "hjson"),
    "sim.timing": ("%Error-NEEDTIMINGOPT: tb/tb.sv:20:5: Use --timing or --no-timing to specify how timing "
                   "controls should be handled", "tool_version", "--timing"),
    "sim.version": ("error: this design requires verilator 5.020 or newer", "tool_version", "verilator 5.020"),
    "sim.env": ("ERROR: environment variable RISCV is not set", "env", "RISCV"),
    "synth.liberty": ("ERROR: Can't open input file `/pdk/acme/lib/cells_typ.lib' for reading: No such file or "
                      "directory", "pdk", "cells_typ.lib"),
    "synth.blackbox": ("ERROR: Module `\\widget_mul' referenced in module `\\widget_top' in cell `\\u_mul' is "
                       "not part of the design.", "unknown_module", "widget_mul"),
    "synth.no_clock": ("the SDC defines no clock on port 'clk' (clocks: none)", "constraints", "clock"),
    "synth.sim_only": ("rtl/widget.sv:42: ERROR: initial block is not synthesizable", "sim_only_code",
                       "rtl/widget.sv:42"),
    "synth.memory_flops": ("Replacing memory \\buf_mem with list of registers. See rtl/fifo.sv:20",
                           "memory_macro", "buf_mem"),
    "synth.image": ("docker: Unable to find image 'ghcr.io/acme/eda:1.0' locally", "image", "ghcr.io/acme/eda:1.0"),
    "synth.docker": ("Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon "
                     "running?", "tool", "docker"),
}


def test_every_rule_has_a_sample():
    assert {r.id for r in LOG_RULES} == set(SAMPLES)


@pytest.mark.parametrize("rule", LOG_RULES, ids=lambda r: r.id)
def test_rule_on_a_synthetic_line(rule):
    line, category, word = SAMPLES[rule.id]
    assert rule.pattern.search(line), f"{rule.id} does not match its sample"
    found = classify(f"some output\n{line}\nmore output\n", rule.stages[0])
    hit = [i for i in found if i["category"] == category and word in i["missing"]]
    assert hit, found
    i = hit[0]
    assert i["stage"] == rule.stages[0] and i["evidence"] == line.strip()[:300]
    assert i["who"] == "person" and i["suggestion"] and i["blocking"] is rule.blocking


def test_clean_output_gives_no_issue():
    text = "%Warning-UNUSED: rtl/a.sv:3: Signal is not used: 'x'\n[tb] scenario basic: 12/12 cases ok\nTEST PASSED\n"
    assert classify(text, "sim") == [] and classify(text, "synth") == []


def test_rules_are_per_stage():
    line = SAMPLES["sim.readmem"][0]
    assert classify(line, "sim") and not classify(line, "synth")


# --- making the fix concrete -----------------------------------------------------------------------

MAKEFILE = """\
NAME ?= widget
GEN  := rtl
.PHONY: vendor regs sim clean
vendor:
\tpython3 util/fetch.py deps/acme.vendor.hjson
regs:
\tpython3 tools/regtool.py -t $(GEN) data/regs.hjson
\t@echo "generated $(GEN)/$(NAME)_reg_{pkg,top}.sv"
fw: build/fw/prog.hex
build/fw/prog.hex:
\triscv64-unknown-elf-gcc -o build/fw/prog.elf sw/main.c
sim: vectors
\tfusesoc run --target sim acme:ip:widget
vectors:
\tpython3 model/gen.py -o vec
clean:
\trm -rf build rtl
"""


def _mk(tmp_path: Path, text: str = MAKEFILE) -> Path:
    (tmp_path / "Makefile").write_text(text)
    return tmp_path / "Makefile"


def test_read_makefile_expands_variables_and_braces(tmp_path):
    t = read_makefile(_mk(tmp_path))
    assert t["sim"].prereqs == ["vectors"] and 'generated rtl/widget_reg_{pkg,top}.sv' in t["regs"].recipe[1]
    assert "clean" in t and ".PHONY" not in t


def test_producer_ranks_file_name_then_directory(tmp_path):
    t = read_makefile(_mk(tmp_path))
    assert producer("rtl/widget_reg_top.sv", t)[0] == "regs"           # named in a recipe (brace expanded)
    assert producer("build/fw/prog.hex", t)[0] == "build/fw/prog.hex"  # the target is the file itself
    assert producer("vec/case_0.dat", t)[0] == "vectors"               # written into its directory (-o vec)
    assert producer("rtl/other.sv", t) is None                         # -t rtl is not taken as "writes all of rtl/"
    assert producer("doc/notes.txt", t) is None


def test_missing_generated_file_points_at_the_step(tmp_path):
    _mk(tmp_path)
    ctx = make_context(tmp_path)
    found = classify("%Error: Cannot find file 'rtl/widget_reg_pkg.sv'", "sim", ctx)
    assert found[0]["category"] == "generated" and found[0]["step"] == ["make", "regs"]
    assert found[0]["who"] == "person" and "add ['make', 'regs'] to [setup]" in found[0]["suggestion"]
    ctx.steps = (("make", "regs"),)
    approved = classify("%Error: Cannot find file 'rtl/widget_reg_pkg.sv'", "sim", ctx)[0]
    assert approved["who"] == "setup_step" and "approved [setup] step `make regs`" in approved["suggestion"]


def test_absolute_paths_in_the_checkout_map_to_the_makefile(tmp_path):
    _mk(tmp_path)
    ctx = make_context(tmp_path)
    found = classify(f"ERROR: Setup failed : Cannot find {tmp_path}/rtl/widget_reg_pkg.sv in :", "sim", ctx)
    assert found[0]["missing"] == "generated file rtl/widget_reg_pkg.sv"


def test_fusesoc_dependency_suggests_the_vendor_step(tmp_path):
    _mk(tmp_path)
    ctx = make_context(tmp_path, steps=[["make", "vendor"]])
    i = classify(SAMPLES["sim.fusesoc_deps"][0], "sim", ctx)[0]
    assert (i["category"], i["who"], i["step"]) == ("vendor_ip", "setup_step", ["make", "vendor"])


@pytest.mark.parametrize("module,category", [("sg_dfrbp_1", "library_cell"), ("acme_clk_gating", "tech_cell"),
                                             ("widget_sram_64x32", "memory_model"), ("widget_mul", "unknown_module")])
def test_unknown_module_is_told_apart(module, category):
    line = f"ERROR: Module `\\{module}' referenced in module `\\top' in cell `\\u0' is not part of the design."
    assert classify(line, "synth", Context(lib_prefixes=("sg_",)))[0]["category"] == category


def test_summary_line():
    issues = classify(SAMPLES["sim.command"][0] + "\n" + SAMPLES["sim.env"][0], "sim")
    assert summary(issues).startswith("environment not ready (sim): missing program riscv64-unknown-elf-gcc")


# --- file checks ------------------------------------------------------------------------------------

def test_vendor_descriptor_and_submodule_not_fetched(tmp_path):
    (tmp_path / "deps").mkdir()
    (tmp_path / "deps" / "acme.vendor.hjson").write_text("{\n  name: \"acme\",\n  target_dir: \"acme\",\n}\n")
    (tmp_path / ".gitmodules").write_text('[submodule "ext/fifo"]\n\tpath = ext/fifo\n\turl = x\n')
    (tmp_path / "ext" / "fifo").mkdir(parents=True)
    _mk(tmp_path)
    ctx = make_context(tmp_path, steps=[["make", "vendor"]])
    found = vendor_issues(tmp_path, "sim", ctx)
    assert [(i["rule"], i["who"], i["step"]) for i in found] == [
        ("file.vendor", "setup_step", ["make", "vendor"]),
        ("file.submodule", "person", ["git", "submodule", "update", "--init", "--recursive"])]
    (tmp_path / "deps" / "acme").mkdir()
    (tmp_path / "deps" / "acme" / "x.sv").write_text("")
    assert [i["rule"] for i in vendor_issues(tmp_path, "sim", ctx)] == ["file.submodule"]


CORE = """\
CAPI=2:
name: acme:ip:widget:1.0.0
filesets:
  rtl:
    files:
      - rtl/widget_reg_pkg.sv
      - rtl/widget.sv
      - rtl/lost.sv
    file_type: systemVerilogSource
    depend:
      - acme.org::fifo_lib
      - acme.org::here
  tb:
    files:
      - tb/tb.sv
targets:
  sim:
    filesets: [rtl, tb]
  synth:
    filesets: [rtl]
"""


def test_fusesoc_files_and_dependencies(tmp_path):
    (tmp_path / "rtl").mkdir()
    (tmp_path / "rtl" / "widget.sv").write_text("")
    (tmp_path / "tb").mkdir()
    (tmp_path / "tb" / "tb.sv").write_text("")
    (tmp_path / "widget.core").write_text(CORE)
    (tmp_path / "deps").mkdir()
    (tmp_path / "deps" / "here.core").write_text("CAPI=2:\nname: acme.org:lib:here:0.1\n")
    _mk(tmp_path)
    ctx = make_context(tmp_path)
    found = {i["rule"]: i for i in fusesoc_issues(tmp_path, "sim", ctx)}
    assert found["file.fusesoc_generated"]["step"] == ["make", "regs"]
    assert found["file.fusesoc_file"]["missing"] == "file rtl/lost.sv"
    assert found["file.fusesoc_dep"]["missing"] == "FuseSoC dependency acme.org::fifo_lib"
    assert "acme.org::here" not in str(found)


def test_path_issues_explain_the_variable(tmp_path):
    ctx = Context(root=tmp_path)
    pdk = tmp_path / "pdk"
    unset = path_issues({"lib": "${NOPE_ROOT}/x.lib"}, {}, tmp_path, "synth", ctx)
    assert (unset[0]["category"], unset[0]["rule"]) == ("env", "file.env_unset")
    nowhere = path_issues({"lib": "${PDK_ROOT}/acme/x.lib"}, {"PDK_ROOT": str(pdk)}, tmp_path, "synth", ctx,
                          pdk="acme-a1")
    assert (nowhere[0]["category"], nowhere[0]["rule"]) == ("pdk", "file.env_dir")
    assert "ciel enable --pdk-family acme-a1" in nowhere[0]["suggestion"]
    pdk.mkdir()
    not_installed = path_issues({"lib": "${PDK_ROOT}/acme-a1/x.lib"}, {"PDK_ROOT": str(pdk)}, tmp_path, "synth",
                                ctx, pdk="acme-a1")
    assert not_installed[0]["rule"] == "file.pdk_missing"
    (pdk / "acme-a1").mkdir()
    corner = path_issues({"lib": "${PDK_ROOT}/acme-a1/x.lib"}, {"PDK_ROOT": str(pdk)}, tmp_path, "synth", ctx,
                         pdk="acme-a1")
    assert (corner[0]["category"], corner[0]["rule"]) == ("pdk", "file.path")
    (pdk / "acme-a1" / "x.lib").write_text("")
    assert path_issues({"lib": "${PDK_ROOT}/acme-a1/x.lib"}, {"PDK_ROOT": str(pdk)}, tmp_path, "synth", ctx) == []


# --- physical design: the LibreLane adapter's rows, same shape -----------------------------------------

PNR_SAMPLES = {
    "pdk_views": "[ERROR] /pdk/acme/libs.ref/cells/lef/cells.lef: No such file or directory",
    "corner_data": "Error: could not open /pdk/acme/libs.ref/cells/lib/cells_ss.lib",
    "signoff_decks": "ERROR: /pdk/acme/libs.tech/klayout/drc/acme.lydrc does not exist",
    "floorplan_config": "[ERROR IFP-0024] Core area too small for the instances.",
    "pdn_config": "[ERROR PDN-0179] Unable to repair all channels.",
    "macro_placement": "[ERROR MPL-0002] Macro u_ram does not fit the core.",
    "image": "pull access denied for acme/librelane, repository does not exist",
    "io_ring": "PAD_CORNER cell is missing from the pad library",
}


@pytest.mark.parametrize("category", sorted(PNR_SAMPLES))
def test_pnr_log_rule(category):
    found = pnr_setup_issues({}, f"Running step\n{PNR_SAMPLES[category]}\n")
    assert any(i["category"] == category and i["stage"] == "pnr" for i in found), found


def test_every_pnr_log_rule_has_a_sample():
    assert {cat for _, cat, _, _ in PNR_RULES} == set(PNR_SAMPLES)


def test_pnr_pdk_root_line():
    assert pnr_setup_issues({}, "PDK_ROOT not set\n")[0]["missing"] == "PDK installation"


# --- [setup], run_setup, regress and synth on a project -------------------------------------------------

def _script(path: Path, body: str) -> None:
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _project(tmp_path: Path, run_body: str, extra: str = "", makefile: str | None = None) -> Path:
    root = tmp_path / "d"
    (root / "rtl").mkdir(parents=True)
    (root / "rtl" / "top.sv").write_text("module top; endmodule\n")
    _script(root / "run.sh", run_body)
    if makefile is not None:
        (root / "Makefile").write_text(makefile)
    cfg = tmp_path / "p.toml"
    cfg.write_text(f'name = "w"\nroot = "{root}"\n[versions]\n[lint]\ntop = "top"\nfiles = ["rtl/top.sv"]\n'
                   f'[regress]\ncmd = ["./run.sh"]\nfail_regex = "TEST FAILED"\n{extra}')
    return cfg


def test_setup_config_parses_and_validates(tmp_path):
    p = load_project(_project(tmp_path, "exit 0\n", '[setup]\nsteps = [["make", "vendor"], ["make", "regs"]]\n'))
    assert p.setup.steps == (("make", "vendor"), ("make", "regs"))
    with pytest.raises(ValueError, match="setup"):
        load_project(_project(tmp_path / "b", "exit 0\n", "[setup]\nsteps = [[]]\n"))
    with pytest.raises(ValueError, match="unknown \\[setup\\]"):
        load_project(_project(tmp_path / "c", "exit 0\n", "[setup]\nstep = []\n"))
    assert load_project(_project(tmp_path / "e", "exit 0\n")).setup.steps == ()


def test_run_setup_runs_only_approved_steps_and_records_them(tmp_path):
    from eda_agent.tools.setup import run_setup
    cfg = _project(tmp_path, "exit 0\n", '[setup]\nsteps = [["./prep.sh"]]\n')
    _script(tmp_path / "d" / "prep.sh", "echo prepared > gen.txt\n")
    r = run_setup(load_project(cfg), log_dir=tmp_path / "logs")
    assert (r.status, r.evidence_level) == ("pass", "E0") and (tmp_path / "d" / "gen.txt").is_file()
    assert r.metrics["steps"][0]["step"] == ["./prep.sh"] and r.metrics["steps"][0]["returncode"] == 0
    assert r.artifacts and r.artifacts[0]["sha256"]


def test_run_setup_failure_is_diagnosed(tmp_path):
    from eda_agent.tools.setup import run_setup
    cfg = _project(tmp_path, "exit 0\n", '[setup]\nsteps = [["./prep.sh"], ["./never.sh"]]\n')
    _script(tmp_path / "d" / "prep.sh", "echo 'ModuleNotFoundError: No module named \"hjson\"' >&2\n"
                                        "echo \"ModuleNotFoundError: No module named 'hjson'\"\nexit 1\n")
    _script(tmp_path / "d" / "never.sh", "touch ran\n")
    r = run_setup(load_project(cfg), log_dir=tmp_path / "logs")
    assert (r.status, r.error_class) == ("error", "setup") and len(r.metrics["steps"]) == 1
    assert r.metrics["setup_issues"][0]["missing"] == "Python package hjson"
    assert not (tmp_path / "d" / "ran").exists()


def test_run_setup_refuses_without_steps_or_outside_the_allowlist(tmp_path):
    from eda_agent.tools.setup import run_setup
    assert run_setup(load_project(_project(tmp_path, "exit 0\n"))).error_class == "not_configured"
    r = run_setup(load_project(_project(tmp_path / "b", "exit 0\n", '[setup]\nsteps = [["curl", "x"]]\n')))
    assert r.error_class == "not_allowed"


def test_regress_that_cannot_build_is_not_a_test_failure(tmp_path):
    from eda_agent.tools.regress import run_regress
    mk = "regs:\n\t@echo \"generated rtl/widget_reg_{pkg,top}.sv\"\n"
    cfg = _project(tmp_path, "echo \"%Error: Cannot find file 'rtl/widget_reg_pkg.sv'\"\nexit 1\n",
                   '[setup]\nsteps = [["make", "regs"]]\n', makefile=mk)
    r = run_regress(load_project(cfg), log_dir=tmp_path / "logs")
    assert (r.status, r.error_class) == ("error", "setup")
    assert r.summary.startswith("environment not ready (sim)") and "not a test result" in r.summary
    i = r.metrics["setup_issues"][0]
    assert (i["category"], i["who"], i["step"]) == ("generated", "setup_step", ["make", "regs"])
    assert r.metrics["setup_blocked_cases"] == ["all"] and r.metrics["failed_cases"] == []


def test_regress_with_a_real_failure_stays_a_failure(tmp_path):
    from eda_agent.tools.regress import run_regress
    # The output names a missing file, but the testbench ran and said it failed: a test result.
    cfg = _project(tmp_path, "echo 'rtl/old.sv: No such file or directory'\necho 'TEST FAILED'\nexit 1\n")
    r = run_regress(load_project(cfg), log_dir=tmp_path / "logs")
    assert (r.status, r.error_class) == ("fail", None)


def test_precheck_sim_reports_a_missing_command(tmp_path):
    cfg = _project(tmp_path, "exit 0\n")
    (tmp_path / "d" / "run.sh").unlink()
    found = precheck(load_project(cfg), "sim")
    assert found[0]["category"] == "tool" and "./run.sh" in found[0]["missing"]


# --- levels: Fatal "environment not ready", with the fix ----------------------------------------------

def _receipt(checks):
    return {"gate": {"status": "passed", "rewrites": 0, "decisions": []}, "stop_reason": "end", "tool_errors": 0,
            "checks_run": checks, "not_run": {"described_but_not_run": [], "cases_not_run": [], "lint_run": True},
            "reviews": [], "approval": {}}


def test_levels_show_environment_not_ready_instead_of_a_failure():
    issue = classify(SAMPLES["sim.fusesoc_deps"][0], "sim")[0]
    r = _receipt([{"tool": "regress", "status": "error", "summary": "environment not ready (sim): ...",
                   "metrics": {"setup_issues": [issue]}, "tool_versions": {}}])
    items = assess(r)
    fatal = [i for i in items if i.level == "fatal"]
    assert [i.text for i in fatal] == ["Environment not ready (sim): missing FuseSoC dependency "
                                       "acme-corp.org::fifo_lib"]
    assert fatal[0].quote.startswith("fix (a person): fetch the third-party IP")
    assert not [i for i in items if "ended with status" in i.text or "case(s) failed" in i.text]
    assert not [i for i in items if "No tool versions" in i.text]


def test_levels_non_blocking_setup_finding_is_a_warning():
    issue = classify(SAMPLES["synth.memory_flops"][0], "synth")[0]
    r = _receipt([{"tool": "synth", "status": "pass", "summary": "", "metrics": {"setup_issues": [issue]},
                   "tool_versions": {"yosys": "x"}}])
    assert [i.level for i in assess(r) if i.text.startswith("Environment not ready")] == ["warning"]


# --- init proposes [setup]; doctor --deep runs it ----------------------------------------------------

def test_init_proposes_setup_steps(tmp_path):
    from eda_agent.onboard.detect import detect, setup_steps
    from eda_agent.onboard.init import render
    _mk(tmp_path, MAKEFILE.replace("sim: vectors", "sim: vectors rtl/widget_reg_pkg.sv"))
    steps = setup_steps(tmp_path / "Makefile", ["make", "sim"])
    assert [s for s, _ in steps] == [["make", "vendor"], ["make", "regs"], ["make", "vectors"]]
    assert "already a prerequisite of `sim`" in dict((s[1], w) for s, w in steps)["vectors"]
    text = render(detect(tmp_path, run_fusesoc=False), "2026-11-01")
    assert '[setup]\nsteps = [["make", "vendor"], ["make", "regs"], ["make", "vectors"]]  # CHECK from:' in text


DEEP_MAKEFILE = "vendor:\n\tmkdir -p deps/acme && touch deps/acme/fifo.sv\n"


def _deep_project(tmp_path: Path, steps: str) -> Path:
    cfg = _project(tmp_path, "test -f deps/acme/fifo.sv || { echo 'Requirements: x <- '\"'\"'acme__corp.org__fifo "
                             ">= 0'\"'\"''; exit 1; }\necho 'TEST PASSED'\n", steps, makefile=DEEP_MAKEFILE)
    (tmp_path / "d" / "deps").mkdir()
    (tmp_path / "d" / "deps" / "acme.vendor.hjson").write_text("{ target_dir: \"acme\" }\n")
    return cfg


def test_doctor_deep_reports_then_runs_the_approved_step(tmp_path, monkeypatch, capsys):
    from eda_agent.cli.doctor import deep
    monkeypatch.chdir(tmp_path)
    cfg = _deep_project(tmp_path, "")
    assert deep(cfg, ["sim"], out_dir=tmp_path / "rec") == 1
    out = capsys.readouterr().out
    assert "ENVIRONMENT NOT READY" in out and "vendored IP deps/acme/" in out and "make vendor" in out
    cfg2 = _deep_project(tmp_path / "ok", '[setup]\nsteps = [["make", "vendor"]]\n')
    assert deep(cfg2, ["sim"], out_dir=tmp_path / "rec") == 0
    out = capsys.readouterr().out
    assert "[setup] pass: 1 setup step(s) ran: make vendor" in out and "environment ready" in out
    assert (tmp_path / "ok" / "d" / "deps" / "acme" / "fifo.sv").is_file()


def test_doctor_deep_no_setup_only_reports(tmp_path, monkeypatch):
    from eda_agent.cli.doctor import deep
    monkeypatch.chdir(tmp_path)
    cfg = _deep_project(tmp_path, '[setup]\nsteps = [["make", "vendor"]]\n')
    assert deep(cfg, ["sim"], run_steps=False, out_dir=tmp_path / "rec") == 1
    assert not (tmp_path / "d" / "deps" / "acme").exists()


def test_mcp_server_has_run_setup(tmp_path):
    from eda_agent.server.mcp_server import build_server
    import asyncio
    server = build_server(load_project(_project(tmp_path, "exit 0\n", '[setup]\nsteps = [["make", "regs"]]\n')))
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert "run_setup" in names
