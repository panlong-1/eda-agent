"""New tests against surviving mutants: test_context (facts), write_case, score_tests (E3).

A mutate run (runs/work/<name>/) leaves mutants that the regression does not catch. A new test
is written only to generated/<run_id>/ and run only in copies of the design (runs/work/<run_id>/),
never in the design itself. score_tests applies the four conditions agreed for M2.5:

  1. it passes on the unmodified design, in two runs (a test that fails there is wrong, not a
     bug finder);
  2. it fails on each target mutant;
  3. the same result in two runs;
  4. every other mutant of that mutate run it kills (or only slows: perf_killed) is listed.

Only then is the result E3, and its claim covers the mutants it killed and nothing else. A test
whose header says `kind=perf` checks cycle counts, which depend on this implementation; it is
counted apart from functional tests. On success score_tests writes a patch (the test file plus
the [tests] register line in the case list) for a person to apply after approval.
"""
from __future__ import annotations

import difflib
import json
import re
import shutil
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from queue import Queue

from ..core.envelope import Diagnostic, ToolResult, artifact, not_configured
from ..core.proc import check_allowed, run
from ..core.project import Project, case_specs, project_env
from .formats.exitcode import judge_exitcode
from .mutate import WORK_DIR, _clean, _copy_design, env_leaks
from .perf import parse_perf, timing_list

GEN_DIR = Path("generated")
ATTEMPT_RUNS = 2          # the original and each target are run this many times (stability)
MAX_TEST_BYTES = 100_000
_ID = re.compile(r"^[A-Za-z0-9_.\-]+$")
_KIND_PERF = re.compile(r"kind\s*=\s*perf\b", re.I)


def _refuse(tool: str, project: Project) -> ToolResult | None:
    if project.tests is None:
        return not_configured(tool, "E0", "Nothing was written or run.",
                              f"project '{project.name}' has no [tests] section (run = command that runs one "
                              "test file, with {file})")
    if project.mutation is None:
        return not_configured(tool, "E0", "Nothing was written or run.",
                              f"project '{project.name}' has no [mutation] section")
    return None


def load_mutate_run(name: str, work_base: Path = WORK_DIR) -> dict | str:
    """metrics of a finished mutate run (its mutate.json), or why it cannot be used."""
    d = work_base / name
    if not _ID.match(name) or not (d / "mutate.json").is_file():
        return f"no mutate run {name!r} under {work_base}/ (give the directory name a mutate result reports)"
    return json.loads((d / "mutate.json").read_text())


def _mutant(run: dict, mid: int) -> dict | None:
    return next((x for x in run.get("mutant_list", []) if x["id"] == mid), None)


def example_cases(project: Project, run: dict, limit: int = 2) -> list[str]:
    """[tests] examples, else the cases of this mutate run that killed most mutants of the file:
    they exercise the module, so they show how a test of it looks."""
    if project.tests and project.tests.examples:
        return list(project.tests.examples)
    known = {s.name for s in case_specs(project)}
    hits = Counter(c for x in run.get("mutant_list", []) for c in x.get("killed_by", []) if c in known)
    ranked = [c for c, _ in hits.most_common()] or list(run.get("cases", []))
    return ranked[:limit]


def test_suffix(project: Project, examples: list[str]) -> str:
    specs = {s.name: s for s in case_specs(project)}
    sources = [specs[c].source for c in examples if c in specs and specs[c].source] or \
              [s.source for s in specs.values() if s.source]
    suffixes = Counter(Path(s).suffix for s in sources if Path(s).suffix)
    return suffixes.most_common(1)[0][0] if suffixes else ".txt"


def run_test_context(project: Project, mutate_run: str, mutant: int, max_lines: int = 400) -> dict:
    """What a test writer needs, as facts: the change, the module, examples, how tests are judged."""
    if refused := _refuse("test_context", project):
        return refused.to_dict()
    run = load_mutate_run(mutate_run)
    if isinstance(run, str):
        return {"tool": "test_context", "status": "error", "error": run}
    m = _mutant(run, mutant)
    if m is None:
        return {"tool": "test_context", "status": "error", "error": f"mutant {mutant} is not in {mutate_run}"}
    src = (project.root / run["file"]).read_text(errors="replace").splitlines()
    patch = Path(m["path"]).with_name("mutant.patch")
    specs = {s.name: s for s in case_specs(project)}
    examples = []
    for c in example_cases(project, run):
        s = specs.get(c)
        if s and s.source and (project.root / s.source).is_file():
            text = (project.root / s.source).read_text(errors="replace").splitlines()
            examples.append({"case": c, "path": s.source, "text": "\n".join(text[:200])})
    cfg = project.regress
    return {
        "tool": "test_context", "status": "ok", "mutate_run": mutate_run,
        "mutant": {k: m.get(k) for k in ("id", "type", "line", "changes", "status", "killed_by", "full", "perf",
                                         "equivalence")} | {"file": run["file"]},
        "patch": patch.read_text() if patch.is_file() else "",
        "module_source": "\n".join(f"{i + 1:4} {ln}" for i, ln in enumerate(src[:max_lines])),
        "cases_that_did_not_catch_it": run.get("cases", []),
        "examples": examples,
        "test_suffix": test_suffix(project, [e["case"] for e in examples]),
        "judged_by": "exit status 0 = pass" + (f"; output must match /{cfg.pass_regex}/" if cfg.pass_regex else "")
                     + (f"; output must not match /{cfg.fail_regex}/" if cfg.fail_regex else ""),
        "perf_key": project.perf.key if project.perf else None,
    }


# ---- running one test in a copy --------------------------------------------------------------

def _copies(project: Project, run_id: str, n: int) -> list[Project] | str:
    """n copies of the design for this run_id, made once and reused; or why there are none."""
    mcfg = project.mutation
    assert mcfg is not None
    out = []
    for k in range(n):
        dest = (WORK_DIR / run_id / f"w{k}").resolve()
        if not dest.is_dir():
            _copy_design(project.root, dest, mcfg.copy_exclude)
        cp = replace(project, root=dest, regress=replace(project.regress, timeout_s=mcfg.timeout_s))
        if leaks := env_leaks(project_env(cp), project.root):
            return f"environment variable(s) {leaks} point at the original checkout {project.root}"
        out.append(cp)
    return out


def run_one(cp: Project, file: Path, name: str) -> dict:
    """Run one test file in one copy: {status PASS|FAIL, detail, tail, perf}."""
    t = cp.tests
    assert t is not None and cp.mutation is not None
    cmd = [a.format(file=str(file.resolve()), name=name) for a in t.run]
    if refused := check_allowed(cmd, cp.root, cp.allowed_commands):
        return {"status": "ERROR", "detail": refused, "tail": "", "perf": {}}
    log = cp.root / t.log.format(name=name) if t.log else None
    if log and log.exists():
        log.unlink()  # a stale log from an earlier run must not be read as this one's
    try:
        proc = run(cmd, cp.root, project_env(cp), cp.regress.timeout_s)
        text = proc.output + ("\n" + log.read_text(errors="replace") if log and log.is_file() else "")
        status, detail = judge_exitcode(proc.returncode, text, cp.regress)
        perf = parse_perf(text, cp.perf.patterns) if cp.perf else {}
        return {"status": status, "detail": detail, "tail": "\n".join(text.splitlines()[-30:]), "perf": perf}
    finally:
        _clean(cp.root, cp.mutation.copy_exclude)


def _test_path(run_id: str, name: str, suffix: str) -> Path:
    return GEN_DIR / run_id / f"{name}{suffix}"


def run_write_case(project: Project, run_id: str, name: str, content: str, mutate_run: str) -> ToolResult:
    """Write a new test to generated/<run_id>/ and run it once on the unmodified design (a copy)."""
    if refused := _refuse("write_case", project):
        return refused
    scope = ("The new test ran once on the unmodified design, in a copy. Passing there only means it is "
             "not obviously wrong; it says nothing yet about which bugs it catches (score_tests).")
    if not (_ID.match(run_id) and _ID.match(name)):
        return ToolResult("write_case", "error", "E0", scope, summary="run_id and name: letters, digits, _ . - only",
                          error_class="bad_request")
    if len(content.encode()) > MAX_TEST_BYTES:
        return ToolResult("write_case", "error", "E0", scope, error_class="bad_request",
                          summary=f"test longer than {MAX_TEST_BYTES} bytes")
    run = load_mutate_run(mutate_run)
    if isinstance(run, str):
        return ToolResult("write_case", "error", "E0", scope, summary=run, error_class="bad_request")
    path = _test_path(run_id, name, test_suffix(project, example_cases(project, run)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if content.endswith("\n") else content + "\n")
    copies = _copies(project, run_id, 1)
    if isinstance(copies, str):
        return ToolResult("write_case", "error", "E0", scope, summary=copies, error_class="infra")
    r = run_one(copies[0], path, name)
    ok = r["status"] == "PASS"
    return ToolResult(
        "write_case", "pass" if ok else ("error" if r["status"] == "ERROR" else "fail"), "E2", scope,
        summary=f"{path}: {'passes' if ok else 'FAILS'} on the unmodified design ({r['detail']})",
        metrics={"path": str(path), "original": r["status"], "detail": r["detail"], "perf": r["perf"]},
        diagnostics=[] if ok else [Diagnostic("error", "FAILS_ON_ORIGINAL", r["tail"][-2000:], file=str(path))],
        diagnostics_total=0 if ok else 1,
        artifacts=[artifact(path)],
        residual_risk=[] if ok else ["a test that fails on the unmodified design is wrong; it cannot be used"],
    )


# ---- scoring ---------------------------------------------------------------------------------

def _apply_and_run(cp: Project, rel: str, mutant_file: Path | None, test: Path, name: str, times: int) -> list[dict]:
    target = cp.root / rel
    saved = target.read_bytes()
    try:
        if mutant_file is not None:
            target.write_bytes(mutant_file.read_bytes())
        return [run_one(cp, test, name) for _ in range(times)]
    finally:
        target.write_bytes(saved)


def _classify(results: list[dict], reference: float | None, key: str | None) -> str:
    """killed (the test failed) | perf_killed (passed, the [perf] key moved) | survived | error."""
    first = results[0]
    if first["status"] == "ERROR":
        return "error"
    if first["status"] == "FAIL":
        return "killed"
    value = first["perf"].get(key) if key else None
    if reference is not None and value is not None and value != reference:
        return "perf_killed"
    return "survived"


def _before(run: dict) -> tuple[set[int], set[int], set[int], set[int]]:
    """(scored, killed, perf_killed, likely_equivalent) mutant ids of the mutate run, counting a
    whole-list re-run when there was one."""
    scored, killed, perf, equiv = set(), set(), set(), set()
    for x in run.get("mutant_list", []):
        st, full = x["status"], (x.get("full") or {}).get("status")
        if st == "likely_equivalent":
            equiv.add(x["id"])
        if st not in ("killed", "perf_killed", "survived"):
            continue
        scored.add(x["id"])
        if st == "killed" or full == "killed":
            killed.add(x["id"])
        elif st == "perf_killed" or full == "perf_killed":
            perf.add(x["id"])
    return scored, killed, perf, equiv


def _rate(n: int, d: int) -> float | None:
    return round(n / d, 4) if d else None


TIMING_LIST_HEADER = ("# eda-agent: implementation-specific timing tests (kind=perf). Each passes on its own and\n"
                      "# checks no cycle number itself; `eda-agent perf` compares its cycle count with the approved\n"
                      "# baseline. Keep them out of the functional regression: an accepted slowdown is recorded\n"
                      "# by loosening the baseline (perf-baseline --allow-loosen --reason), not by editing these.\n")


def make_patch(project: Project, test: Path, name: str, examples: list[str], kind: str = "functional"
               ) -> tuple[str, str]:
    """(patch text, path of the new test in the design): the test file plus the register line, in
    the case list for a functional test, in the separate timing list (perf.timing_list) for kind=perf."""
    specs = {s.name: s for s in case_specs(project)}
    src = next((specs[c].source for c in examples if c in specs and specs[c].source), "")
    dest = str(Path(src).parent / f"{name}{test.suffix}") if src else f"{name}{test.suffix}"
    body = test.read_text()
    parts = ["".join(difflib.unified_diff([], body.splitlines(keepends=True), "/dev/null", f"b/{dest}"))]
    t, cl = project.tests, project.regress.case_list
    target = timing_list(project) if kind == "perf" else cl
    if t and t.register and target:
        exists = (project.root / target).is_file()
        old = (project.root / target).read_text() if exists else ""
        head = "" if exists or kind != "perf" else TIMING_LIST_HEADER
        new = head + old + ("" if not old or old.endswith("\n") else "\n") + t.register.format(name=name, path=dest) + "\n"
        parts.append("".join(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                                  f"a/{target}" if exists else "/dev/null", f"b/{target}")))
    return "".join(parts), dest


def run_score_tests(project: Project, run_id: str, file: str, mutate_run: str, targets: list[int]) -> ToolResult:
    if refused := _refuse("score_tests", project):
        return refused
    run = load_mutate_run(mutate_run)
    test = Path(file)
    scope_no = "Nothing was scored."
    bad = None
    if isinstance(run, str):
        bad = run
    elif not _ID.match(run_id) or test.resolve().parent != (GEN_DIR / run_id).resolve() or not test.is_file():
        bad = f"the test must be a file written by write_case under {GEN_DIR}/{run_id}/"
    elif not targets or any(_mutant(run, t) is None for t in targets):
        bad = f"targets must be mutant ids of {mutate_run}"
    if bad:
        return ToolResult("score_tests", "error", "E2", scope_no, summary=bad, error_class="bad_request")
    rel, name = run["file"], test.stem
    kind = "perf" if _KIND_PERF.search("\n".join(test.read_text(errors="replace").splitlines()[:20])) else "functional"
    mcfg = project.mutation
    copies = _copies(project, run_id, max(1, mcfg.jobs))
    if isinstance(copies, str):
        return ToolResult("score_tests", "error", "E2", scope_no, summary=copies, error_class="infra")
    key = project.perf.key if project.perf else None

    golden = _apply_and_run(copies[0], rel, None, test, name, ATTEMPT_RUNS)
    if any(g["status"] != "PASS" for g in golden):
        return ToolResult(
            "score_tests", "fail", "E2",
            f"The new test {test} ran {ATTEMPT_RUNS} times on the unmodified design and did not pass every time. "
            "A test that fails on the unmodified design is wrong; it is not evidence of a bug.",
            summary=f"fails on the unmodified design: {[g['status'] for g in golden]} ({golden[-1]['detail']})",
            metrics={"test": str(test), "original": [g["status"] for g in golden], "kind": kind},
            diagnostics=[Diagnostic("error", "FAILS_ON_ORIGINAL", golden[-1]["tail"][-2000:])], diagnostics_total=1,
            artifacts=[artifact(test)], error_class=None,
            residual_risk=["the test is wrong on the unmodified design and was not scored against any mutant"])
    reference = golden[0]["perf"].get(key) if key else None
    golden_stable = len({g["perf"].get(key) for g in golden}) == 1 if key else True

    mutants = [x for x in run["mutant_list"] if x["status"] not in ("invalid", "error")]
    free: Queue = Queue()
    for c in copies:
        free.put(c)
    results: dict[int, list[dict]] = {}

    def job(x: dict) -> None:
        cp = free.get()
        try:
            results[x["id"]] = _apply_and_run(cp, rel, Path(x["path"]), test, name,
                                              ATTEMPT_RUNS if x["id"] in targets else 1)
        finally:
            free.put(cp)

    with ThreadPoolExecutor(len(copies)) as ex:
        list(ex.map(job, mutants))

    verdict = {mid: _classify(rs, reference, key) for mid, rs in results.items()}
    stable = {t: len({(r["status"], r["perf"].get(key) if key else None) for r in results[t]}) == 1 for t in targets}
    scored, killed_before, perf_before, equiv = _before(run)
    new_killed = {m for m, v in verdict.items() if v == "killed"}
    new_perf = {m for m, v in verdict.items() if v == "perf_killed"}
    after_killed = (killed_before | new_killed) & scored
    after_perf = ((perf_before | new_perf) - after_killed) & scored
    # A functional test must make the target fail; a kind=perf test may instead move its cycle count.
    hits = ("killed", "perf_killed") if kind == "perf" else ("killed",)
    target_ok = {t: verdict.get(t) in hits and stable[t] for t in targets}
    success = all(target_ok.values()) and golden_stable
    metrics = {
        "test": str(test), "kind": kind, "mutate_run": mutate_run, "file": rel,
        "original": [g["status"] for g in golden], "original_perf": golden[0]["perf"],
        "targets": {str(t): {"runs": [r["status"] for r in results[t]], "verdict": verdict.get(t),
                             "stable": stable[t], "detail": results[t][0]["detail"],
                             # the [perf] numbers of each run, so a person sees e.g. 16005 -> 18004 cycles
                             "perf": [r["perf"] for r in results[t]]} for t in targets},
        # the [perf] key of every mutant whose cycle count this test moved (vs original_perf)
        "perf_moved": {str(m): results[m][0]["perf"].get(key) for m, v in verdict.items() if v == "perf_killed"},
        "killed": sorted(new_killed), "perf_killed": sorted(new_perf - new_killed),
        "newly_killed": sorted((new_killed - killed_before) & scored),
        "equivalent_killed": sorted(new_killed & equiv),
        "scored": len(scored),
        "kill_rate_before": _rate(len(killed_before & scored), len(scored)),
        "kill_rate_after": _rate(len(after_killed), len(scored)),
        "kill_rate_with_perf_before": _rate(len((killed_before | perf_before) & scored), len(scored)),
        "kill_rate_with_perf_after": _rate(len(after_killed | after_perf), len(scored)),
    }
    arts = [artifact(test)]
    if success:
        patch, dest = make_patch(project, test, name, example_cases(project, run), kind)
        pfile = test.with_suffix(".patch")
        pfile.write_text(patch)
        metrics |= {"patch": str(pfile), "patch_dest": dest}
        arts.append(artifact(pfile))
    killed_txt = ", ".join(map(str, sorted(new_killed))) or "none"
    if kind == "perf":
        perf_txt = ", ".join(map(str, sorted(new_perf - new_killed))) or "none"
        killed_txt += f" (and moved the cycle count on mutants {perf_txt})"
    scope = (f"The new test {test} passed {ATTEMPT_RUNS} times on the unmodified design and was run on the "
             f"{len(mutants)} judged mutants of {rel} from {mutate_run}" + (f" ({ATTEMPT_RUNS} times on each target)")
             + f". It failed on mutants {killed_txt}. "
             + ("E3 covers exactly those mutants: the test detects these changes; it says nothing about other "
                "bugs, and kill rates are over this run's mutants only." if success else
                "It did not fail on every target in every run, so this is not E3 for the targets."))
    if kind == "perf":
        scope += (" The test is kind=perf: it detects its target by the cycle count the testbench prints "
                  "(compared with the unmodified design), so it depends on this implementation's timing and "
                  "counts only toward the kill rate with perf, not the functional one.")
    diags = [Diagnostic("warning", "TARGET_NOT_KILLED", f"mutant {t}: {metrics['targets'][str(t)]}")
             for t, ok in target_ok.items() if not ok]
    risks = ["kill rates are over the mutants of one mutate run (a sample of one module), not over the design"]
    if not golden_stable:
        risks.append(f"the unmodified design's {key} differed between the two runs: perf comparisons are unreliable")
    if metrics["equivalent_killed"]:
        risks.append(f"mutants {metrics['equivalent_killed']} were marked likely equivalent but this test kills "
                     "them: they were not equivalent (the bounded module-level check missed them)")
    rb, ra = metrics["kill_rate_before"], metrics["kill_rate_after"]
    per_target = "; ".join(f"{t}: {v['verdict']} {v['runs']}" for t, v in metrics["targets"].items())
    rates = f"; kill rate {rb:.0%} -> {ra:.0%}" if rb is not None and ra is not None else ""
    return ToolResult(
        tool="score_tests",
        status="pass" if success else "fail",
        evidence_level="E3" if success else "E2",
        claim_scope=scope,
        summary=(f"{'kills' if success else 'does NOT kill'} target(s) {targets} ({per_target}); "
                 f"fails on mutants {killed_txt}{rates}"),
        metrics=metrics, diagnostics=diags, diagnostics_total=len(diags), artifacts=arts,
        residual_risk=risks,
    )
