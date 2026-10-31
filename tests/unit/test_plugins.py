"""Plug-ins (plan §0.19): contract tests, discovery, `import`, human-set standards, category rules,
gate R9, the scan-insertion lineage link and the tapeout checklist. Uses the FAKE example importer
in examples/fake_cdc_importer (made-up format and data)."""
import json
import sys
from pathlib import Path

import pytest

import eda_agent.plugins as plugins
from eda_agent.agent.gate import check_report
from eda_agent.core import standards
from eda_agent.core.categories import judge, signoff_extras
from eda_agent.core.project import load_project
from eda_agent.core.snapshot import consistency
from eda_agent.plugins.contract import check_importer
from eda_agent.plugins.result import NotMine, validate
from eda_agent.tools.importing import imported, run_import

sys.path.insert(0, str(Path(__file__).parents[2] / "examples" / "fake_cdc_importer"))
from fake_cdc_importer import FakeCdcImporter  # noqa: E402

SAMPLES = Path(__file__).parents[2] / "examples" / "fake_cdc_importer" / "fake_cdc_importer" / "samples"
RISK = "\n\nResidual risk: imported reports only; eda-agent did not run those tools."


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    plugins.register_importer(FakeCdcImporter)
    d = tmp_path / "design"
    d.mkdir()
    cfg = tmp_path / "p.toml"
    cfg.write_text(f'name = "toy"\nroot = "{d}"\n[lint]\ntop = "x"\n[regress]\ncmd = ["true"]\n'
                   f'[pnr]\nconfig = "c.yaml"\nexperience_dir = "{tmp_path / "exp"}"\n')
    return load_project(cfg)


# --- contract ----------------------------------------------------------------------------------------

def test_fake_importer_passes_the_contract():
    assert check_importer(FakeCdcImporter()) == []


class _Bad(FakeCdcImporter):
    name = "bad"


def test_contract_catches_core_only_fields_and_guessing(tmp_path):
    class SetsStatus(_Bad):
        def parse(self, path):
            return {**super().parse(path), "status": "pass"}

    class Guesses(_Bad):
        def detect(self, path):
            return 1.0

        def parse(self, path):
            try:
                return super().parse(path)
            except NotMine:
                return {"category": "cdc", "tool": {"name": "x", "version": "1"}, "metrics": {}, "inputs": [],
                        "outputs": []}

    class NoVersion(_Bad):
        def parse(self, path):
            r = super().parse(path)
            return {**r, "tool": {"name": "ToyCDC"}, "metrics": {k: v for k, v in r["metrics"].items()
                                                                  if k != "constraints_complete"}}

    class WrongHash(_Bad):
        def parse(self, path):
            r = super().parse(path)
            return {**r, "outputs": [{"path": "fake_cdc.rpt", "role": "report", "sha256": "0" * 64}]}

    class NotRepeatable(_Bad):
        n = 0

        def parse(self, path):
            NotRepeatable.n += 1
            return {**super().parse(path), "notes": [str(NotRepeatable.n)]}

    assert any("a plug-in may not set them: status" in p for p in check_importer(SetsStatus()))
    assert any("claims a directory" in p for p in check_importer(Guesses()))
    probs = check_importer(NoVersion())
    assert any("tool.version" in p for p in probs) and any("constraints_complete is required" in p for p in probs)
    assert any("sha256 given by the plug-in does not match" in p for p in check_importer(WrongHash()))
    assert any("not repeatable" in p for p in check_importer(NotRepeatable()))


def test_validate_rejects_unknown_category():
    assert validate({"category": "magic", "tool": {"name": "a", "version": "1"}, "metrics": {}, "inputs": [],
                     "outputs": [{"path": "x"}]})[0].startswith("unknown category")


def test_entry_points_are_discovered(monkeypatch):
    class EP:
        name = "fake_cdc_ep"

        def load(self):
            return FakeCdcImporter

    class Broken:
        name = "broken"

        def load(self):
            raise ImportError("no such module")
    monkeypatch.setattr(plugins, "_IMPORTERS", {})
    monkeypatch.setattr(plugins, "_LOADED", {"importers": False, "adapters": False})
    monkeypatch.setattr(plugins, "entry_points", lambda group: [EP(), Broken()] if group == plugins.IMPORTER_GROUP else [])
    assert list(plugins.importers()) == ["fake_cdc"]
    assert any("broken" in e for e in plugins.LOAD_ERRORS)


# --- import -----------------------------------------------------------------------------------------

def test_import_judges_records_and_writes_a_receipt(project, tmp_path):
    r = run_import(project, SAMPLES / "clean")
    assert r.status == "pass" and r.evidence_level == "E0" and r.tool == "imported"
    m = r.metrics
    assert m["category"] == "cdc" and m["provenance"] == "imported" and m["tool_name"] == "ToyCDC"
    assert m["files"]["outputs"][0]["sha256"] and m["files"]["inputs"][0]["sha256"] is None  # rtl not here
    assert r.snapshot["state"] == "unknown" and "clock-domain-crossing" in r.claim_scope
    assert "the core judged it" in r.claim_scope
    rec = imported(project, "cdc")
    assert len(rec) == 1 and rec[0]["made_by"] == "unknown (imported report)"
    assert Path(m["receipt"]).is_file()
    from eda_agent.agent.receipt import write_receipt
    js, md = write_receipt(json.loads(Path(m["receipt"]).read_text()), Path(m["receipt"]).parent)
    assert js == Path(m["receipt"]) and md.is_file() and md.with_suffix(".html").is_file()
    assert "Import receipt" in md.read_text() and "Fatal 0 · Error 1 · Warning 2" in md.read_text()


def test_unrecognized_directory_is_refused(project, tmp_path):
    (tmp_path / "x").mkdir()
    r = run_import(project, tmp_path / "x")
    assert r.error_class == "no_importer" and imported(project) == []


def test_waivers_count_only_when_a_person_approved_them(project):
    r = run_import(project, SAMPLES / "waived", write=False)
    assert r.status == "fail" and any("W-0001 is not approved" in d.msg for d in r.diagnostics)
    assert any("2 unsynchronized" in d.msg for d in r.diagnostics)
    with pytest.raises(ValueError, match="reason"):
        standards.waive(project, "cdc", "W-0001", "alice", "")
    standards.waive(project, "cdc", "W-0001", "alice", "async FIFO, reviewed")
    r = run_import(project, SAMPLES / "waived", write=False)
    assert r.metrics["approved_waivers"] == 1 and any("1 unsynchronized" in d.msg for d in r.diagnostics)


def test_targets_tightening_is_free_loosening_needs_a_reason(project):
    standards.set_target(project, "atpg.test_coverage_pct", 95.0, "alice")
    standards.set_target(project, "atpg.test_coverage_pct", 97.0, "alice")
    with pytest.raises(ValueError, match="reason"):
        standards.set_target(project, "atpg.test_coverage_pct", 90.0, "bob")
    standards.set_target(project, "atpg.test_coverage_pct", 90.0, "bob", "tester limits, agreed with test team")
    h = standards.load(project)["history"]
    assert [x["loosening"] for x in h] == [False, False, True] and h[-1]["reason"].startswith("tester")


def test_category_rules():
    atpg = {"test_coverage_pct": 96.0, "fault_coverage_pct": 94.0, "patterns": 800}
    assert judge("atpg", atpg, set(), {})[0] == "error"
    assert judge("atpg", atpg, set(), {"atpg.test_coverage_pct": 95.0})[0] == "pass"
    assert judge("atpg", atpg, set(), {"atpg.test_coverage_pct": 97.0})[0] == "fail"
    eq = {"equivalent_points": 10, "non_equivalent_points": 0, "unmapped_points": 2, "reference": "a", "implementation": "b"}
    assert judge("equivalence", eq, set(), {})[0] == "error"
    assert judge("equivalence", {**eq, "unmapped_points": 0}, set(), {})[0] == "pass"
    assert judge("cdc", {"unsynchronized": 0, "constraints_complete": False}, set(), {})[0] == "error"


# --- gate R9 and the extended checklist -----------------------------------------------------------

def _cat(category, status="pass", **metrics):
    return {"tool": "imported", "status": status, "evidence_level": "E0",
            "metrics": {"category": category, "tool_name": "ToyCDC", **metrics}}


def test_r9_category_claims(project):
    cdc = run_import(project, SAMPLES / "clean", write=False).to_dict()
    ok = "The imported ToyCDC result: CDC clean, 0 unsynchronized crossings, 0 approved waivers, 2 crossings checked."
    assert check_report(ok + RISK, [cdc]).passed
    d = check_report(ok + RISK, [])
    assert not d.passed and any("no cdc result" in r for r in d.reasons)
    d = check_report("The imported ToyCDC result: CDC clean." + RISK, [cdc])
    assert not d.passed and any("key numbers" in r for r in d.reasons)
    d = check_report("The imported ToyCDC result: CDC clean, 3 crossings checked." + RISK, [cdc])
    assert not d.passed and any("cites 3" in r for r in d.reasons)
    failing = run_import(project, SAMPLES / "waived", write=False).to_dict()
    d = check_report("The imported ToyCDC result: CDC clean, 2 unsynchronized." + RISK, [failing])
    assert not d.passed and any("fail by the core's rule" in r for r in d.reasons)


def test_r9_atpg_coverage_is_not_verification_coverage():
    atpg = _cat("atpg", test_coverage_pct=96.0, fault_coverage_pct=94.0, patterns=800)
    d = check_report("The imported atpg result shows test coverage 96.0%, so verification coverage is complete." + RISK,
                     [atpg])
    assert not d.passed and any(r.startswith("[fatal]") for r in d.reasons)
    assert check_report("The imported atpg result: test coverage 96.0% (target 95)." + RISK,
                        [{**atpg, "metrics": {**atpg["metrics"], "targets": {"atpg.test_coverage_pct": 95}}}]).passed


def test_cdc_clean_never_means_functionally_correct():
    cdc = _cat("cdc", unsynchronized=0, constraints_complete=True)
    d = check_report("The imported cdc result: CDC clean with 0 unsynchronized, so the design is correct." + RISK, [cdc])
    assert not d.passed  # R2: an E0 category never backs a functional-correctness word


def _netlist(sha):
    return {"path": f"/n/{sha}.v", "role": "netlist", "sha256": sha}


def test_scan_insertion_needs_equivalence_between_pre_and_post_scan():
    dft = _cat("dft", scan_chains=4, dft_rule_violations=0,
               files={"inputs": [_netlist("pre")], "outputs": [_netlist("post")]})
    def scan_issues(c):
        return [i for i in c["issues"] if "scan insertion changed the netlist" in i["what"]]
    c = consistency([dft])
    assert not c["ok"] and scan_issues(c)[0]["level"] == "fatal"
    eq = _cat("equivalence", equivalent_points=9, non_equivalent_points=0, unmapped_points=0,
              files={"inputs": [_netlist("pre"), _netlist("post")], "outputs": []})
    assert scan_issues(consistency([dft, eq])) == []
    assert signoff_extras([dft, eq]) == {"cdc": "not covered", "rdc": "not covered", "dft": "pass",
                                         "atpg": "not covered", "post_scan_equivalence": "pass"}


def test_tapeout_lists_the_checklist_items_not_covered():
    flow = {"tool": "flow_report", "status": "pass", "evidence_level": "E5",
            "metrics": {"tag": "r", "signoff_level": "E5", "signoff_passed": True, "timing_met": True,
                        "lineage": {"available": True, "breaks": []}}}
    d = check_report("flow_report r: ready for tapeout." + RISK, [flow])
    assert any("cdc not covered" in r and "post_scan_equivalence not covered" in r for r in d.reasons)


# --- findings from the private importers: power unit, RTL manifests, --all -------------------------

def test_power_without_an_established_unit_is_an_error():
    from eda_agent.core.categories import scope
    m = {"total_w": None, "total_raw": "5.327", "method": "vcd"}
    st, why = judge("power", m, set(), {})
    assert st == "error" and "unit not established" in why[0]
    text = scope("power", {"name": "P", "version": "1"}, "d", m, 0, {})
    assert "None W" not in text and "unit not established" in text
    assert judge("power", {"total_w": 0.005, "method": "vcd"}, set(), {})[0] == "pass"


def _manifest(design: Path, tmp_path: Path, as_json=False) -> Path:
    import hashlib
    (design / "rtl").mkdir(exist_ok=True)
    (design / "rtl" / "top.sv").write_text("module top; endmodule\n")
    h = hashlib.sha256((design / "rtl" / "top.sv").read_bytes()).hexdigest()
    f = tmp_path / ("rtl.json" if as_json else "SRC.sha256")
    f.write_text(json.dumps({"files": {"rtl/top.sv": h}}) if as_json else f"{h}  rtl/top.sv\n")
    return f


def test_a_person_supplied_rtl_manifest_identifies_the_design(project, tmp_path):
    from eda_agent.core.snapshot import compare_snapshots, make_snapshot
    mf = _manifest(project.root, tmp_path)
    r = run_import(project, SAMPLES / "clean", write=False, manifest=mf)
    s = r.snapshot
    assert s["state"] == "current" and s["id"] and s["rtl_manifest"]["supplied_by"] == "a person-supplied RTL manifest"
    assert s["unknown_parts"] == ["SDC", "PDK version"]  # allowed for an import; the RTL is what is required
    mine = make_snapshot([str(project.root / "rtl" / "top.sv")], [project.root], sdc_sha256="s" * 64, pdk_version="v")
    c = compare_snapshots(mine, s)
    assert c["same_design"] is True and c["unknown"]  # unknown SDC/PDK is not a difference
    bad = tmp_path / "empty.txt"
    bad.write_text("nothing here\n")
    assert run_import(project, SAMPLES / "clean", write=False, manifest=bad).error_class == "bad_config"


def test_an_importer_may_name_an_rtl_manifest_input(project, tmp_path):
    mf = _manifest(project.root, tmp_path, as_json=True)

    class Named(FakeCdcImporter):
        name = "fake_cdc_manifest"

        def parse(self, path):
            r = super().parse(path)
            return {**r, "inputs": r["inputs"] + [{"path": str(mf), "role": "rtl_manifest"}]}
    plugins.register_importer(Named)
    r = run_import(project, SAMPLES / "clean", "fake_cdc_manifest", write=False)
    assert r.snapshot["state"] == "current" and "names" in r.snapshot["rtl_manifest"]["supplied_by"]


def test_import_all_runs_every_importer_that_recognizes_the_folder(project, monkeypatch):
    from eda_agent.tools.importing import run_import_all

    class FakeRdc(FakeCdcImporter):
        name = "fake_rdc"
        categories = ("rdc",)

        def parse(self, path):
            return {**super().parse(path), "category": "rdc"}
    monkeypatch.setattr(plugins, "_IMPORTERS", {})
    monkeypatch.setattr(plugins, "_LOADED", {"importers": True, "adapters": True})
    plugins.register_importer(FakeCdcImporter)
    plugins.register_importer(FakeRdc)
    assert plugins.choose(SAMPLES / "clean")[0] is None  # a tie: a single import needs --as
    res = run_import_all(project, SAMPLES / "clean")
    assert sorted(r.metrics["category"] for r in res) == ["cdc", "rdc"]
    assert len({r.metrics["receipt"] for r in res}) == 2 and len(imported(project)) == 2
