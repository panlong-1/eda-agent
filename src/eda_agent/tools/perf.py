"""Performance numbers from simulation (E2): cycle counts and other counters the testbench prints.

`perf` runs the project's own regression cases and reads, from each case's own output, the
numbers that the [perf] patterns name (cycles, instr, ipc, stall counters, ...). Nothing is
computed by us; the testbench's counters are the measurement.

"Slower" needs a reference. For a design over time that is the baseline file
(projects/<name>.perf-baseline.json): case -> expected value of the key, with a tolerance.
Like the SDC it defines what a claim means, so only a person writes it (`eda-agent
perf-baseline`, never an MCP tool); tightening is recorded as is, loosening needs an explicit
reason. For a mutant the reference is the unmodified design run in the same copy (tools/mutate.py).
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from ..core.envelope import Diagnostic, ToolResult, artifact
from ..core.hashing import sha256_file
from ..core.numbers import parse_number
from ..core.proc import check_allowed, run
from ..core.project import PerfConfig, Project, case_specs, project_env
from .formats.exitcode import judge_exitcode
from .regress import LOG_DIR, run_regress

CLAIM_SCOPE = (
    "Counters the testbench printed for cases {cases} in this cycle-accurate RTL simulation; '{key}' "
    "decides slower / same / faster{ref}. They describe these programs on this RTL only: nothing about "
    "other workloads, the clock frequency, timing after synthesis, or power."
)


def parse_perf(text: str, patterns: tuple[str, ...]) -> dict[str, int | float]:
    """Named groups of every pattern, as numbers; when a line repeats, the last one counts."""
    out: dict[str, int | float] = {}
    for p in patterns:
        for m in re.finditer(p, text, re.M):
            for k, v in m.groupdict().items():
                if v is not None:
                    try:
                        out[k] = parse_number(v)
                    except ValueError:
                        pass
    return out


def case_perf(project: Project, regress: ToolResult | dict) -> dict[str, dict]:
    """Numbers of every case that ran, read from its own output (the regress result's case_logs,
    or [perf] log = "...{root}...{case}...")."""
    cfg = project.perf
    assert cfg is not None
    m = (regress.metrics if isinstance(regress, ToolResult) else regress.get("metrics")) or {}
    logs = m.get("case_logs") or {}
    out = {}
    for case in [*m.get("passed_cases", []), *m.get("failed_cases", [])]:
        path = cfg.log.format(root=project.root, case=case) if cfg.log else logs.get(case, "")
        p = Path(path) if path else None
        out[case] = parse_perf(p.read_text(errors="replace"), cfg.patterns) if p and p.is_file() else {}
    return out


def compare(values: dict[str, dict], reference: dict[str, float], cfg: PerfConfig,
            tolerance_pct: float | None = None) -> dict[str, dict]:
    """Per case: the key's value, the reference, the difference and slower / same / faster."""
    tol = cfg.tolerance_pct if tolerance_pct is None else tolerance_pct
    rows = {}
    for case, v in values.items():
        new, ref = v.get(cfg.key), reference.get(case)
        row: dict = {cfg.key: new, "reference": ref, "delta": None, "delta_pct": None}
        if new is None:
            row["verdict"] = "no number"
        elif ref is None:
            row["verdict"] = "no reference"
        else:
            delta = new - ref
            pct = 100.0 * delta / ref if ref else (0.0 if delta == 0 else math.inf)
            worse = pct if cfg.lower_is_better else -pct
            row.update(delta=round(delta, 6), delta_pct=round(pct, 2),
                       verdict="slower" if worse > tol else "faster" if worse < -tol else "same")
        rows[case] = row
    return rows


def load_baseline(path: str | Path) -> dict | None:
    p = Path(path)
    return json.loads(p.read_text()) if p.is_file() else None


def timing_list(project: Project) -> str:
    """Where approved kind=perf tests are listed: <case list>_perf next to the case list, e.g.
    regress/cases/soc_perf.list. They always pass on their own (they check results, not cycles), so
    they stay out of the functional regression; perf measures them against the baseline."""
    cl = project.regress.case_list
    return str(Path(cl).with_name(f"{Path(cl).stem}_perf{Path(cl).suffix}")) if cl else ""


def timing_cases(project: Project) -> dict[str, str]:
    """name -> test file of the approved timing tests (empty when there is no such list)."""
    tl = timing_list(project)
    if not tl or not (project.root / tl).is_file() or project.tests is None:
        return {}
    specs = case_specs(replace(project, regress=replace(project.regress, case_list=tl)))
    return {s.name: s.source for s in specs if s.source}


def run_timing_case(project: Project, name: str, source: str) -> tuple[str, dict]:
    """One timing test run with [tests] run, in the project root like the regression: (PASS|FAIL, numbers)."""
    t, cfg = project.tests, project.perf
    assert t is not None and cfg is not None
    cmd = [a.format(file=str((project.root / source).resolve()), name=name) for a in t.run]
    if check_allowed(cmd, project.root, project.allowed_commands):
        return "FAIL", {}
    proc = run(cmd, project.root, project_env(project), project.regress.timeout_s)
    log = project.root / t.log.format(name=name) if t.log else None
    text = proc.output + ("\n" + log.read_text(errors="replace") if log and log.is_file() else "")
    status, _ = judge_exitcode(proc.returncode, text, project.regress)
    return status, parse_perf(text, cfg.patterns)


def run_perf(project: Project, cases: list[str] | None = None, log_dir: Path = LOG_DIR) -> ToolResult:
    cfg = project.perf
    if cfg is None:
        return ToolResult("perf", "error", "E2", "Nothing was measured.",
                          summary=f"project '{project.name}' has no [perf] section (patterns, key, cases): "
                                  "performance numbers are not set up for it", error_class="not_configured")
    known = [s.name for s in case_specs(project)]
    timing = timing_cases(project)  # approved kind=perf tests: measured, never part of the regression
    chosen = list(cases or [*(cfg.cases or known), *timing])
    if bad := [c for c in chosen if c not in known and c not in timing]:
        return ToolResult("perf", "error", "E2", "Nothing was measured.", error_class="bad_request",
                          summary=f"unknown case(s) {bad}; call list_regress_cases first")
    baseline = load_baseline(cfg.baseline)
    if baseline is not None and baseline.get("key") != cfg.key:
        baseline = None  # recorded for another number; not comparable
    scope = CLAIM_SCOPE.format(cases=", ".join(chosen), key=cfg.key,
                               ref=f", against the approved baseline (sha256 {sha256_file(cfg.baseline)[:12]}…)"
                               if baseline else "; no approved baseline, so nothing is called slower")
    regress_part = [c for c in chosen if c in known]
    res = run_regress(project, regress_part, log_dir=log_dir) if regress_part else \
        ToolResult("regress", "pass", "E2", "", metrics={"passed_cases": [], "failed_cases": []})
    if res.status in ("error", "timeout"):
        return ToolResult("perf", res.status, "E2", scope, summary=f"regression {res.status}: {res.summary}",
                          tool_versions=res.tool_versions, error_class=res.error_class)
    values = case_perf(project, res)
    failed = set(res.metrics.get("failed_cases", []))
    for c in chosen:
        if c in timing and c not in known:
            status, numbers = run_timing_case(project, c, timing[c])
            values[c] = numbers
            if status != "PASS":
                failed.add(c)
    good = {c: v for c, v in values.items() if c not in failed}
    no_number = sorted(c for c, v in good.items() if cfg.key not in v)
    comparison = compare(good, baseline["cases"], cfg, baseline.get("tolerance_pct", cfg.tolerance_pct)) \
        if baseline else None
    slower = sorted(c for c, r in (comparison or {}).items() if r["verdict"] == "slower")
    faster = sorted(c for c, r in (comparison or {}).items() if r["verdict"] == "faster")

    diags = [Diagnostic("error", "CASE_FAIL", f"{c}: failed, so its numbers are not used") for c in sorted(failed)]
    diags += [Diagnostic("warning", "PERF_SLOWER", f"{c}: {r[cfg.key]} {cfg.key} vs baseline {r['reference']} "
                                                  f"({r['delta']:+g}, {r['delta_pct']:+g}%)")
              for c, r in (comparison or {}).items() if r["verdict"] == "slower"]
    diags += [Diagnostic("warning", "PERF_MISSING", f"{c}: no '{cfg.key}' in its output") for c in no_number]
    risks = ["the numbers are the testbench's own counters for these programs; other programs may behave differently",
             "cycle counts say nothing about clock frequency: combine with synth timing for time per program"]
    if comparison is None:
        risks.append("no approved baseline: nothing was compared, so 'no slower than before' cannot be said; "
                     "a person records one with `eda-agent perf-baseline`")
    elif missing_ref := sorted(c for c, r in comparison.items() if r["verdict"] == "no reference"):
        risks.append(f"case(s) {missing_ref} are not in the baseline and were not compared")

    table = "; ".join(f"{c} {v.get(cfg.key, '?')}" for c, v in good.items())
    summary = (f"{cfg.key} per case: {table}" + (f"; failed: {sorted(failed)}" if failed else "")
               + (f"; vs baseline: {len(slower)} slower, {len(faster)} faster, "
                  f"{sum(r['verdict'] == 'same' for r in comparison.values())} same" if comparison else
                  "; no baseline"))
    return ToolResult(
        tool="perf",
        status="fail" if failed or slower or no_number else "pass",
        evidence_level="E2",
        claim_scope=scope,
        summary=summary,
        metrics={"key": cfg.key, "lower_is_better": cfg.lower_is_better, "cases": good,
                 "comparison": comparison, "slower": slower, "faster": faster,
                 "passed_cases": sorted(good), "failed_cases": sorted(failed),
                 "baseline_sha256": sha256_file(cfg.baseline) if baseline else None,
                 "baseline_by": baseline.get("recorded_by") if baseline else None,
                 "baseline_recorded_at": baseline.get("recorded_at") if baseline else None,
                 "baseline_reason": baseline.get("reason") if baseline else None,
                 # what the latest baseline change gave up (old -> new), with its reason, so an accepted
                 # slowdown stays visible in every later result
                 "baseline_loosened": baseline.get("loosened") if baseline else None,
                 "baseline_justified_by": baseline.get("justified_by") if baseline else None},
        diagnostics=diags,
        diagnostics_total=len(diags),
        artifacts=res.artifacts + ([artifact(cfg.baseline)] if baseline else []),
        tool_versions=res.tool_versions,
        residual_risk=risks,
    )


def record_baseline(project: Project, source: Path, by: str, reason: str = "", tolerance_pct: float | None = None,
                    allow_loosen: bool = False, justified_by: str = "") -> tuple[dict, list[str]]:
    """A person records a perf result as the baseline. Returns (new baseline, what it loosens).
    Raises ValueError when the source is not a clean perf result, or when it loosens the current
    baseline without allow_loosen and a reason."""
    cfg = project.perf
    if cfg is None:
        raise ValueError(f"project '{project.name}' has no [perf] section")
    env = json.loads(source.read_text())
    m = env.get("metrics") or {}
    if env.get("tool") != "perf" or env.get("status") == "error" or m.get("key") != cfg.key:
        raise ValueError(f"{source} is not a perf result for key '{cfg.key}'")
    if m.get("failed_cases"):
        raise ValueError(f"cases {m['failed_cases']} failed in that run; a baseline needs passing cases")
    cases = {c: v[cfg.key] for c, v in (m.get("cases") or {}).items() if cfg.key in v}
    if not cases:
        raise ValueError(f"{source} has no '{cfg.key}' numbers")
    tol = cfg.tolerance_pct if tolerance_pct is None else tolerance_pct
    old = load_baseline(cfg.baseline)
    loosened: list[str] = []
    if old:
        if old.get("key") != cfg.key:
            loosened.append(f"key changes from {old.get('key')} to {cfg.key}")
        if tol > old.get("tolerance_pct", 0.0):
            loosened.append(f"tolerance rises from {old.get('tolerance_pct', 0.0)}% to {tol}%")
        for c, ref in old.get("cases", {}).items():
            if c not in cases:
                loosened.append(f"{c} is dropped")
            elif (cases[c] > ref) if cfg.lower_is_better else (cases[c] < ref):
                loosened.append(f"{c}: {ref} -> {cases[c]} {cfg.key}")
    if loosened and not (allow_loosen and reason.strip()):
        raise ValueError("this loosens the approved baseline (" + "; ".join(loosened) + "): pass "
                         "--allow-loosen with --reason to record it anyway")
    entry = {"project": project.name, "key": cfg.key, "lower_is_better": cfg.lower_is_better,
             "tolerance_pct": tol, "cases": cases, "recorded_by": by,
             "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
             "reason": reason, "loosened": loosened,
             "justified_by": justified_by,  # e.g. the run/receipt of the functional fix that costs cycles
             "source": {"path": str(source), "sha256": sha256_file(source)}}
    history = (old or {}).get("history", [])
    if old:
        history = [*history, {k: v for k, v in old.items() if k != "history"}]
    new = {**entry, "history": history}
    Path(cfg.baseline).write_text(json.dumps(new, indent=2) + "\n")
    return new, loosened
