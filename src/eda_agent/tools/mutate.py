"""Mutation run: which small source-level bugs the project's own regression misses (E0, facts).

avl-core (https://github.com/projectapheleia/avl, MIT) generates the mutants: it finds sites in
the slang syntax tree and splices one change into the source, so every mutant is a readable RTL
diff. The rest is ours. Each mutant is applied alone in a copy of the design and linted (one that
does not compile is `invalid`, not caught), then run against the chosen regression cases: it is
killed when at least one case fails. A survivor can be checked against the golden module with a
bounded Yosys SAT miter in the [flow] image; one that shows no difference is `likely_equivalent`
and is left out of the kill rate.

The design root is never written: copies live under runs/work/, and the root's git status and the
target file's sha256 are compared before and after.
"""
from __future__ import annotations

import csv
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from importlib import metadata
from pathlib import Path
from queue import Queue

from ..core.envelope import Diagnostic, ToolResult, artifact
from ..core.proc import check_allowed, docker_available, docker_command, run
from ..core.project import MutationConfig, Project, case_specs, project_env, project_versions
from .lint import run_lint
from .perf import case_perf, compare
from .regress import run_regress

WORK_DIR = Path("runs/work")
AVL = "avl-mutation-testing"
SOURCE_SUFFIXES = (".sv", ".v", ".svh", ".vh")
MAX_COUNT = 200

CLAIM_SCOPE = (
    "{n} source-level mutants of {file} (avl-core, seed {seed}{types}), each applied alone in a copy "
    "of the design and run against cases {cases}; killed = at least one of those cases failed. The "
    "unmodified design passed the same cases in the same copy. Facts about these mutants and these "
    "cases only: a survivor is a change the cases do not detect, not a bug in the design, and the "
    "kill rate is not a coverage figure for the whole design or the whole regression."
)
RISKS = [
    "mutants are sampled: a module with no survivors here can still have changes the tests miss",
    "a killed mutant was caught by some failing case, not necessarily by a check aimed at that logic",
]

# avl-core writes a comment like this on every line it changed; it is left out of the shown diff.
_MARKER = re.compile(r"\s*/\* AVL MUTATION - .*? \*/")
_LINE_S = re.compile(r"[^\n]*\n|[^\n]+$")
_LINE_B = re.compile(rb"[^\n]*\n|[^\n]+$")

# Same miter as avl-core's own equivalence check, read through the project's [flow] frontend.
EQUIV_SCRIPT = """\
{read_gold}
prep -top {top} -flatten
async2sync
design -stash gold
{read_gate}
prep -top {top} -flatten
async2sync
design -stash gate
design -copy-from gold -as gold {top}
design -copy-from gate -as gate {top}
miter -equiv -flatten -make_assert gold gate miter
hierarchy -top miter
sat -prove-asserts -seq {cycles} -set-init-zero miter
"""
_EQUIV_VERDICT = re.compile(r"model found: (FAIL|SUCCESS)!")


def ascii_stand_in(text: str) -> str:
    """Each non-ASCII character becomes as many '~' as it has UTF-8 bytes.

    avl-core uses pyslang's byte offsets as Python string offsets, so in a file with any non-ASCII
    character (a '→' in a comment is enough) every later mutation lands a few characters off and
    the mutant does not compile. In the stand-in, character offsets equal byte offsets.
    """
    return "".join(c if c < "\x80" else "~" * len(c.encode()) for c in text)


def carry_back(original: bytes, stand_in: str, mutated: str) -> bytes:
    """The edits avl-core made to the stand-in, applied to the original bytes.

    The stand-in has the same lines and byte positions as the original; only its non-ASCII
    characters differ. Unchanged lines are taken from the original as they are; in a changed line,
    the unchanged stretches are too, so a comment such as '→' survives the mutation.
    """
    a, b, orig = _LINE_S.findall(stand_in), _LINE_S.findall(mutated), _LINE_B.findall(original)
    out: list[bytes] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            out += orig[i1:i2]
        elif tag == "replace" and i2 - i1 == j2 - j1:
            out += [_carry_line(orig[i1 + k], a[i1 + k], b[j1 + k]) for k in range(i2 - i1)]
        else:
            out += [line.encode() for line in b[j1:j2]]
    return b"".join(out)


def _carry_line(orig: bytes, stand_in: str, mutated: str) -> bytes:
    ops = difflib.SequenceMatcher(None, stand_in, mutated, autojunk=False).get_opcodes()
    return b"".join(orig[i1:i2] if tag == "equal" else mutated[j1:j2].encode() for tag, i1, i2, j1, j2 in ops)


def describe_change(original: bytes, mutant: bytes) -> list[dict]:
    """Changed lines as {'line', 'before', 'after'}; line numbers are the original's (1-based)."""
    a = original.decode(errors="replace").splitlines()
    b = _MARKER.sub("", mutant.decode(errors="replace")).splitlines()
    return [{"line": i1 + 1, "before": "\n".join(a[i1:i2]), "after": "\n".join(b[j1:j2])}
            for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
            if tag != "equal"]


@dataclass
class Mutant:
    id: int
    type: str
    module: str
    line: int
    changes: list[dict]
    path: Path                      # the mutated file, under the run's mutants/<id>/
    # killed | perf_killed (all cases pass, [perf] key moved) | survived | likely_equivalent | invalid | error
    status: str = "pending"
    killed_by: list[str] = field(default_factory=list)
    detail: str = ""
    seconds: float = 0.0
    equivalence: str = "not checked"  # different | equivalent | unknown | timeout | not checked
    full: dict | None = None        # survivors re-run on the whole case list: {status, killed_by}
    perf: dict | None = None        # case -> [perf] key vs the unmodified design (when it passed)

    def change_text(self) -> str:
        c = self.changes[0] if self.changes else {"before": "?", "after": "?"}
        return f"{' '.join(c['before'].split())} -> {' '.join(c['after'].split())}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["path"] = str(self.path)
        return d


def _avl_command() -> str | None:
    local = Path(sys.executable).parent / AVL
    return str(local) if local.is_file() else shutil.which(AVL)


def avl_versions() -> dict[str, str]:
    out = {}
    for dist in ("avl-core", "pyslang"):
        try:
            out[dist] = metadata.version(dist)
        except metadata.PackageNotFoundError:
            out[dist] = "not installed"
    return out


def generate(project: Project, rel: str, count: int, types: tuple[str, ...], seed: int,
             work: Path) -> list[Mutant] | str:
    """Mutants of one file under work/mutants/<id>/, or the reason there are none."""
    exe = _avl_command()
    if exe is None:
        return f"{AVL} not found: install avl-core into this Python environment (pip install 'eda-agent[mutation]')"
    src = project.root / rel
    original = src.read_bytes()
    try:
        stand_in = ascii_stand_in(original.decode("utf-8"))
    except UnicodeDecodeError:
        return f"{rel} is not UTF-8 text"
    gen = work / "avl"
    (gen / "in").mkdir(parents=True)
    given = gen / "in" / src.name
    given.write_bytes(stand_in.encode("ascii"))
    out = gen / "out"
    cmd = [exe, "--source", str(given), "--count", str(count), "--seed", str(seed), "--no-equiv",
           "--output", str(out), "--json", str(out / "faults.json")]
    if types:
        cmd += ["--types", ",".join(types)]
    proc = run(cmd, gen, dict(os.environ), timeout_s=300)
    (gen / "avl.log").write_text(proc.output)
    if proc.returncode != 0 or not (out / "faults.json").is_file():
        return f"{AVL} failed (exit {proc.returncode}): " + proc.output[-600:]
    mutants = []
    for site in json.loads((out / "faults.json").read_text()):
        body = carry_back(original, stand_in, (out / str(site["id"]) / src.name).read_text(encoding="utf-8"))
        d = work / "mutants" / str(site["id"])
        d.mkdir(parents=True)
        (d / src.name).write_bytes(body)
        patch = difflib.unified_diff(original.decode(errors="replace").splitlines(keepends=True),
                                     body.decode(errors="replace").splitlines(keepends=True),
                                     f"a/{rel}", f"b/{rel}")
        (d / "mutant.patch").write_text("".join(patch))
        mutants.append(Mutant(id=site["id"], type=site["type"], module=site.get("module") or "",
                              line=site["line"], changes=describe_change(original, body), path=d / src.name))
    return mutants


@dataclass
class _Copy:
    project: Project   # root = this copy
    target: Path       # the mutated file inside the copy
    original: bytes


def _copy_design(root: Path, dest: Path, exclude: tuple[str, ...]) -> None:
    skip = {os.path.normpath(root / e.strip("/")) for e in exclude}
    shutil.copytree(root, dest, symlinks=True,
                    ignore=lambda d, names: [n for n in names if os.path.normpath(Path(d) / n) in skip])


def _clean(copy_root: Path, exclude: tuple[str, ...]) -> None:
    """Empty the excluded (generated) paths inside a copy, so results do not pile up."""
    for e in exclude:
        p = copy_root / e.strip("/")
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
        elif p.exists() or p.is_symlink():
            p.unlink()


def env_leaks(env: dict[str, str], root: Path) -> list[str]:
    """Variables that point into the original root: a build would read the unmutated design."""
    pat = re.compile(re.escape(str(root)) + r"(?=$|[/:\s])")
    return sorted(k for k, v in env.items() if k not in ("PWD", "OLDPWD", "_") and pat.search(v))


def _root_state(root: Path, rel: str) -> str:
    """git HEAD + status of the root (when it is a repository) and the target file's sha256."""
    state = hashlib.sha256((root / rel).read_bytes()).hexdigest()
    if (root / ".git").exists():
        for args in (["rev-parse", "HEAD"], ["status", "--porcelain"]):
            out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=60)
            state += "\n" + out.stdout
    return state


def _judge(c: _Copy, m: Mutant, cases: list[str] | None, baseline_errors: int, mcfg: MutationConfig,
           logs: Path, reference: dict[str, float] | None = None) -> tuple[str, list[str], str, dict | None]:
    """(status, cases that caught it, detail, perf comparison) of one mutant in one copy; the copy
    is restored afterwards. With a reference (the unmodified design's [perf] key per case), a mutant
    that passes every case but moves that number beyond the tolerance is perf_killed."""
    try:
        c.target.write_bytes(m.path.read_bytes())
        lint = run_lint(c.project, max_diagnostics=3)
        if lint.status in ("error", "timeout"):
            return "error", [], f"lint {lint.status}: {lint.summary}", None
        if lint.metrics.get("errors", 0) > baseline_errors:
            first = next((d for d in lint.diagnostics if d.severity == "error"), None)
            return "invalid", [], "does not compile: " + (
                f"{first.file}:{first.line} {first.msg}" if first else lint.summary), None
        res = run_regress(c.project, cases, log_dir=logs / str(m.id))
        for a in res.artifacts:  # keep the per-case summary: the copy's results are emptied next
            if a.get("path") and Path(a["path"]).is_file():
                shutil.copy(a["path"], m.path.parent / f"{'full_' if cases is None else ''}{Path(a['path']).name}")
        if res.status == "pass":
            if reference and c.project.perf:
                rows = compare(case_perf(c.project, res), reference, c.project.perf)
                moved = {k: r for k, r in rows.items() if r["verdict"] in ("slower", "faster")}
                if moved:
                    key = c.project.perf.key
                    return "perf_killed", sorted(moved), "all cases pass, but " + ", ".join(
                        f"{k} {r['delta']:+g} {key} ({r['delta_pct']:+g}%)" for k, r in sorted(moved.items())), rows
                return "survived", [], res.summary, rows
            return "survived", [], res.summary, None
        if res.status == "timeout":
            return "killed", ["(timeout)"], f"regression did not finish in {mcfg.timeout_s} s", None
        if res.status == "fail" and res.metrics.get("failed_cases"):
            return "killed", list(res.metrics["failed_cases"]), res.summary, None
        return "error", [], f"regress {res.status}: {res.summary}", None
    except Exception as e:  # noqa: BLE001 - one mutant must not end the run
        return "error", [], f"{type(e).__name__}: {e}", None
    finally:
        c.target.write_bytes(c.original)
        _clean(c.project.root, mcfg.copy_exclude)


def _pool(copies: list[_Copy], items: list, fn) -> None:
    free: Queue = Queue()
    for c in copies:
        free.put(c)

    def task(item):
        c = free.get()
        try:
            fn(c, item)
        finally:
            free.put(c)

    with ThreadPoolExecutor(len(copies)) as ex:
        list(ex.map(task, items))


def check_equivalence(project: Project, rel: str, m: Mutant, work: Path, cycles: int, timeout_s: int = 300) -> str:
    """Bounded SAT miter of the mutated module against the golden one, in the [flow] image."""
    from .synth import _Setup  # [flow] file list, include dirs and read-only mounts
    flow = project.flow
    assert flow is not None
    setup = _Setup(project)
    target = (project.root / rel).resolve()
    gold = setup.files if target in setup.files else [target]
    gate = [m.path.resolve() if f == target else f for f in gold]
    incs = " ".join(f"-I {d}" for d in setup.include_dirs)
    defs = " ".join(f"-D {d}" for d in flow.defines)

    def read(files):
        names = " ".join(map(str, files))
        if flow.frontend == "slang":
            return f"read_slang --top {m.module} --ignore-unknown-modules {incs} {defs} {names}"
        return f"read_verilog -sv {incs} {defs} {names}"

    edir = work / "equiv"
    edir.mkdir(exist_ok=True)
    script = edir / f"{m.id}.ys"
    script.write_text(EQUIV_SCRIPT.format(read_gold=read(gold), read_gate=read(gate), top=m.module, cycles=cycles))
    inner = ["yosys", *(["-m", "slang"] if flow.frontend == "slang" else []), "-s", str(script)]
    cmd = docker_command(flow.image, inner, setup.mounts(work), project.allowed_images)
    if isinstance(cmd, str):
        return "unknown"
    proc = run(cmd, setup.root, setup.env, timeout_s)
    (edir / f"{m.id}.log").write_text(proc.output)
    if proc.returncode is None:
        return "timeout"
    found = _EQUIV_VERDICT.findall(proc.output)
    if not found:
        return "unknown"
    return "different" if found[-1] == "FAIL" else "equivalent"


def kill_rate(mutants: list[Mutant], with_perf: bool = False) -> float | None:
    """killed / (killed + perf_killed + survived); with_perf counts perf_killed as caught too."""
    killed = sum(m.status == "killed" for m in mutants)
    perf = sum(m.status == "perf_killed" for m in mutants)
    scored = killed + perf + sum(m.status == "survived" for m in mutants)
    return round((killed + (perf if with_perf else 0)) / scored, 4) if scored else None


def write_matrix(mutants: list[Mutant], cases: list[str], path: Path) -> None:
    """One row per mutant; per case K = this case failed (killed it), P = it passed but its [perf]
    number moved (perf_killed), - = passed, blank = not run."""
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "line", "type", "status", "change", *cases, "full_regression"])
        for m in mutants:
            ran = m.status in ("killed", "perf_killed", "survived", "likely_equivalent")
            mark = "P" if m.status == "perf_killed" else "K"
            cells = [(mark if c in m.killed_by else "-") if ran else "" for c in cases]
            full = "" if m.full is None else (m.full["status"] + (" by " + " ".join(m.full["killed_by"])
                                                                  if m.full["killed_by"] else ""))
            w.writerow([m.id, m.line, m.type, m.status, m.change_text(), *cells, full])


def write_report(path: Path, rel: str, mutants: list[Mutant], cases: list[str], metrics: dict) -> None:
    rate = metrics["kill_rate"]
    lines = [f"# Mutation run: {rel}", "",
             f"- cases run on every mutant: {', '.join(cases)}",
             f"- mutants: {metrics['mutants']} generated, {metrics['invalid']} did not compile (not counted)",
             f"- killed {metrics['killed']}, perf-killed {metrics['perf_killed']} (results right, cycle count "
             f"moved), survived {metrics['survived']}, likely equivalent {metrics['likely_equivalent']}, "
             f"errors {metrics['error']}",
             f"- kill rate: {'n/a' if rate is None else f'{rate:.0%}'} functional (killed / (killed + perf-killed "
             f"+ survived))" + ("" if metrics["kill_rate_with_perf"] is None else
                                f", {metrics['kill_rate_with_perf']:.0%} counting perf-killed"), ""]
    if metrics.get("full_checked"):
        lines += [f"- survivors re-run on the whole case list: {metrics['full_checked']}, still surviving "
                  f"{metrics['full_survived']}", ""]
    lines += ["| id | line | type | change (before -> after) | result | caught by |", "|---|---|---|---|---|---|"]
    for m in mutants:
        by = ", ".join(m.killed_by) or ("" if m.status != "invalid" else m.detail[:60])
        if m.status == "perf_killed":
            by = m.detail.removeprefix("all cases pass, but ")
        if m.full is not None:
            by += f" (whole list: {m.full['status']}{' by ' + ', '.join(m.full['killed_by']) if m.full['killed_by'] else ''})"
        lines.append(f"| {m.id} | {m.line} | {m.type} | `{m.change_text().replace('|', chr(92) + '|')}` | {m.status} | {by} |")
    path.write_text("\n".join(lines) + "\n")


def _reference(project: Project, golden: ToolResult) -> dict[str, float]:
    """The unmodified design's [perf] key per case, read before the copy's results are emptied."""
    if project.perf is None or golden.status != "pass":
        return {}
    key = project.perf.key
    return {c: v[key] for c, v in case_perf(project, golden).items() if key in v}


def _error(summary: str, scope: str, error_class: str, **kw) -> ToolResult:
    return ToolResult("mutate", "error", "E0", scope, summary=summary, error_class=error_class, **kw)


def run_mutate(project: Project, file: str, count: int = 20, types: list[str] | None = None,
               cases: list[str] | None = None, seed: int = 1, confirm_full: bool = False,
               equivalence: bool = True, jobs: int | None = None, work_base: Path = WORK_DIR) -> ToolResult:
    t0 = time.monotonic()
    mcfg = project.mutation
    no_claim = "Nothing was mutated or run."
    if mcfg is None:
        return _error(f"project '{project.name}' has no [mutation] section (files, cases, copy_exclude, jobs)",
                      no_claim, "not_configured")
    rel = os.path.normpath(file)
    allowed = mcfg.files or ()
    if (rel.startswith("..") or os.path.isabs(rel) or not rel.endswith(SOURCE_SUFFIXES)
            or not (project.root / rel).is_file() or (allowed and rel not in allowed)):
        return _error(f"cannot mutate {file!r}: give a source file relative to the project root"
                      + (f", one of {list(allowed)}" if allowed else ""), no_claim, "bad_request")
    if not 1 <= count <= MAX_COUNT:
        return _error(f"count must be 1..{MAX_COUNT}", no_claim, "bad_request")
    known = [s.name for s in case_specs(project)]
    chosen = list(cases or mcfg.cases or known)
    bad = [c for c in chosen if c not in known]
    if bad:
        return _error(f"unknown case(s) {bad}; call list_regress_cases first", no_claim, "bad_request")
    if refused := check_allowed(list(project.regress.cmd), project.root, project.allowed_commands):
        return _error(refused, no_claim, "not_allowed")
    types_t = tuple(types or mcfg.types)
    scope = CLAIM_SCOPE.format(n=count, file=rel, seed=seed, cases=", ".join(chosen),
                               types=f", types {', '.join(types_t)}" if types_t else "")
    if project.perf:
        scope += (f" perf_killed = every case passed but its '{project.perf.key}' moved more than "
                  f"{project.perf.tolerance_pct:g}% from the unmodified design's: the change is visible only as speed.")

    work = (work_base / f"{project.name}_mutate_{datetime.now():%Y%m%d_%H%M%S_%f}").resolve()
    work.mkdir(parents=True)
    versions = {**avl_versions(), **project_versions(project)}
    before = _root_state(project.root, rel)

    mutants = generate(project, rel, count, types_t, seed, work)
    if isinstance(mutants, str):
        return _error(mutants, no_claim, "infra", tool_versions=versions)

    n_jobs = max(1, min(jobs or mcfg.jobs, len(mutants)))
    copies = []
    for k in range(n_jobs):
        dest = work / f"w{k}"
        _copy_design(project.root, dest, mcfg.copy_exclude)
        cp = replace(project, root=dest, regress=replace(project.regress, timeout_s=mcfg.timeout_s))
        leaks = env_leaks(project_env(cp), project.root)
        if leaks:
            return _error(f"environment variable(s) {leaks} point at the original checkout {project.root}: a "
                          "mutant build could read the unmutated design. Unset them and retry.", no_claim,
                          "infra", tool_versions=versions)
        copies.append(_Copy(cp, dest / rel, (project.root / rel).read_bytes()))

    golden_lint = run_lint(copies[0].project, max_diagnostics=3)
    golden = run_regress(copies[0].project, chosen, log_dir=work / "logs" / "golden")
    golden_perf = _reference(copies[0].project, golden)
    _clean(copies[0].project.root, mcfg.copy_exclude)
    if golden_lint.status in ("error", "timeout") or golden.status != "pass":
        return _error(f"the unmodified design does not pass in the copy, so no mutant can be judged: lint "
                      f"{golden_lint.status}, regress {golden.status}: {golden.summary}", no_claim, "golden_failed",
                      tool_versions=versions, metrics={"work_dir": str(work)})
    baseline_errors = golden_lint.metrics.get("errors", 0)

    full_perf: dict[str, float] = {}

    def judge(c: _Copy, m: Mutant, run_cases: list[str] | None = chosen) -> None:
        t = time.monotonic()
        ref = golden_perf if run_cases is not None else full_perf
        status, by, detail, perf = _judge(c, m, run_cases, baseline_errors, mcfg, work / "logs", ref)
        if run_cases is None:
            m.full = {"status": status, "killed_by": by, "detail": detail}
        else:
            m.status, m.killed_by, m.detail, m.seconds = status, by, detail, round(time.monotonic() - t, 1)
            m.perf = perf

    _pool(copies, mutants, judge)

    risks = list(RISKS)
    survivors = [m for m in mutants if m.status == "survived"]
    if survivors and equivalence:
        if project.flow is None or not docker_available():
            risks.append("survivors were not checked for equivalence (needs [flow] and Docker): some may be "
                         "changes no test could ever detect")
        else:
            from .synth import _Setup, _versions
            versions |= {f"equiv_{k}": v for k, v in _versions(project.flow, _Setup(project), project.allowed_images).items()}
            for m in survivors:
                m.equivalence = check_equivalence(project, rel, m, work, mcfg.equiv_cycles)
                if m.equivalence == "equivalent":
                    m.status = "likely_equivalent"
            risks.append(f"equivalence was checked on the mutated module alone, for {mcfg.equiv_cycles} cycles "
                         "from an all-zero state: 'different' there may still be invisible at the design's "
                         "ports, and 'likely_equivalent' is not a proof")
    elif survivors:
        risks.append("survivors were not checked for equivalence: some may be changes no test could detect")

    full_cases = [s.name for s in case_specs(project)]
    survivors = [m for m in mutants if m.status == "survived"]
    full_note = ""
    if confirm_full and survivors and set(full_cases) != set(chosen):
        g = run_regress(copies[0].project, None, log_dir=work / "logs" / "golden_full")
        full_perf.update(_reference(copies[0].project, g))
        _clean(copies[0].project.root, mcfg.copy_exclude)
        if g.status == "pass":
            _pool(copies, survivors, lambda c, m: judge(c, m, None))
        else:
            full_note = f"; whole-list re-run skipped: the unmodified design does not pass it ({g.summary})"
            risks.append("survivors were not re-run on the whole case list: the unmodified design fails it")

    after = _root_state(project.root, rel)
    counts = {s: sum(m.status == s for m in mutants)
              for s in ("killed", "perf_killed", "survived", "likely_equivalent", "invalid", "error")}
    full_checked = [m for m in mutants if m.full is not None]
    if project.perf is None:
        risks.append("no [perf] section: a mutant that only makes the design slower (extra stall cycles, "
                     "results still right) counts as survived")
    elif not golden_perf:
        risks.append(f"the unmodified design printed no '{project.perf.key}' for these cases, so no mutant "
                     "could be perf-killed")
    metrics = {
        "file": rel, "mutants": len(mutants), **counts, "kill_rate": kill_rate(mutants),
        "kill_rate_with_perf": kill_rate(mutants, with_perf=True) if project.perf else None,
        "perf_key": project.perf.key if project.perf else None, "perf_reference": golden_perf,
        "cases": chosen, "seed": seed, "types": list(types_t),
        "full_checked": len(full_checked),
        "full_survived": sum(m.full["status"] == "survived" for m in full_checked),
        "root_unchanged": before == after, "jobs": n_jobs,
        "seconds": round(time.monotonic() - t0, 1), "work_dir": str(work),
        "mutant_list": [m.to_dict() for m in mutants],
    }
    if not metrics["root_unchanged"]:
        risks.append(f"the original checkout {project.root} changed during the run (git status or the target "
                     "file differs): check it")
    if counts["invalid"]:
        risks.append(f"{counts['invalid']} mutant(s) did not compile and are not counted")
    if counts["error"]:
        risks.append(f"{counts['error']} mutant(s) could not be judged (errors) and are not counted")

    write_matrix(mutants, chosen, work / "kill_matrix.csv")
    write_report(work / "report.md", rel, mutants, chosen, metrics)
    (work / "mutate.json").write_text(json.dumps(metrics, indent=2) + "\n")
    diags = [Diagnostic("warning", "SURVIVED", f"mutant {m.id} ({m.type}) {m.change_text()}"
                        + ("; also survives the whole case list" if m.full and m.full["status"] == "survived" else ""),
                        file=rel, line=m.line)
             for m in mutants if m.status == "survived"]
    diags += [Diagnostic("info", "PERF_KILLED", f"mutant {m.id} ({m.type}) {m.change_text()}: {m.detail}",
                         file=rel, line=m.line)
              for m in mutants if m.status == "perf_killed"]
    rate, prate = metrics["kill_rate"], metrics["kill_rate_with_perf"]
    judged = counts["killed"] + counts["perf_killed"] + counts["survived"]
    summary = (f"{counts['killed']} of {judged} judged mutants killed "
               f"({'n/a' if rate is None else f'{rate:.0%}'})"
               + (f", {counts['perf_killed']} more caught only by cycle count ({prate:.0%} with them)"
                  if counts["perf_killed"] and prate is not None else "")
               + f", {counts['survived']} survived"
               f", {counts['likely_equivalent']} likely equivalent, {counts['invalid']} did not compile"
               + (f", {counts['error']} errors" if counts["error"] else "")
               + (f"; {metrics['full_survived']} of {len(full_checked)} survivors also pass the whole case list"
                  if full_checked else "") + full_note)
    return ToolResult(
        tool="mutate",
        status="error" if not metrics["root_unchanged"] else ("fail" if counts["survived"] else "pass"),
        evidence_level="E0",
        claim_scope=scope,
        summary=summary,
        metrics=metrics,
        diagnostics=diags,
        diagnostics_total=len(diags),
        artifacts=[artifact(work / n) for n in ("kill_matrix.csv", "report.md", "mutate.json")]
        + [artifact(work / "avl" / "out" / "faults.json")],
        tool_versions=versions,
        error_class=None if metrics["root_unchanged"] else "root_changed",
        residual_risk=risks,
    )
