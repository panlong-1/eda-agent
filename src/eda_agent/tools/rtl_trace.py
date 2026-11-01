"""hierarchy, trace_driver, trace_load: the elaborated design as pyslang (slang) sees it (facts, E0).

The design is compiled in-process with the project's lint file set (filelists, include dirs,
defines, top). From the elaborated AST one pass builds an index of *edges*: every place where a
value is written, with what it reads:

    assign       a continuous assignment (or a net declaration with an initializer)
    always_comb / always_ff / always_latch / always / initial
                 one edge per assignment inside the block, with the if/case conditions it sits
                 under (file:line and text of each), so a constant or wrong condition shows up
    port_in      an instance's input port, written by the expression its parent connects to it
    port_out     the parent's net, written by an instance's output port

trace_driver walks edges backwards from a signal (what writes it, and what those read), trace_load
forwards (what reads it). Every step names `file:line` in the project and, given a waveform and a
time, the value of each signal there (tools/waves.py). The tools report facts only: they never
raise the evidence level, and a localisation built from them is a hypothesis (envelope
`diagnostic`, gate rule R10).

Limits (also in residual_risk): bit-level paths are not followed (a write to `x[3]` is a write to
`x`); reads inside function and task bodies are not followed into the function; interfaces and
modports are not traced; a variable index on the left-hand side counts as a read of the target.
"""
from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..core.envelope import ToolResult
from ..core.project import Project, project_env, read_filelist

LEVEL = "E0"
CLAIM_SCOPE = ("Structure of the elaborated RTL as slang sees it: who writes and who reads a signal, with "
               "file:line, and (with a waveform) the values there. Diagnostic facts for localising a failure: "
               "they prove nothing about correctness, and a cause found with them is a hypothesis.")
RESIDUAL = ["bit-level paths are not followed (a write to x[3] counts as a write to x)",
            "function/task bodies, interfaces and modports are not traced into",
            "slang elaborates with the lint file set; a simulation file set that differs is not seen"]
MAX_STEPS = 40
MAX_DEPTH = 6
MAX_CHILDREN = 60


@dataclass
class Cond:
    line: int
    text: str
    branch: str          # true | false | case item text
    reads: list[str] = field(default_factory=list)


@dataclass
class Edge:
    target: str          # hierarchical path of the written signal
    kind: str
    file: str
    line: int
    text: str
    reads: list[str] = field(default_factory=list)
    conditions: list[Cond] = field(default_factory=list)
    clock: str = ""
    via: str = ""        # instance.port for port edges
    block_line: int = 0  # the always block's first line

    def where(self) -> str:
        return f"{self.file}:{self.line}"


def available() -> str:
    """'' when pyslang can be imported, else why not."""
    try:
        import pyslang  # noqa: F401
    except ImportError as exc:  # pragma: no cover - depends on the environment
        return f"pyslang is not installed ({exc}); pip install 'eda-agent[debug]'"
    return ""


def versions() -> dict:
    from importlib.metadata import PackageNotFoundError, version
    out = {}
    for name in ("pyslang", "pywellen"):
        try:
            out[name] = version(name)
        except PackageNotFoundError:
            pass
    return out


# ---------------------------------------------------------------- compiling and indexing

class Design:
    """One compiled design and its edge index."""

    def __init__(self, project: Project):
        from pyslang import analysis, driver

        self.project = project
        self.root_dir = project.root
        env = project_env(project)
        files: list[Path] = []
        for fl in project.lint.filelists:
            if (project.root / fl).is_file():
                files += read_filelist(project.root / fl, env, project.root)
        files += [project.root / f for f in project.lint.files]
        self.files = files
        args = ["slang", "--top", project.lint.top, "--timescale", "1ns/1ps", "--ignore-unknown-modules",
                *(f"-I{project.root / d}" for d in project.lint.include_dirs),
                *(f"-D{d}" for d in project.lint.defines),
                *(a for cf in project.lint.command_files for a in ("-f", str(project.root / cf))),
                *map(str, files)]
        self.driver = d = driver.Driver()
        d.addStandardArgs()
        if not d.parseCommandLine(" ".join(args), driver.CommandLineOptions()) or not d.processOptions():
            raise RuntimeError("slang did not accept the project's file set")
        d.parseAllSources()
        self.comp = d.createCompilation()
        self.sm = self.comp.sourceManager
        diags = self.comp.getAllDiagnostics()
        self.errors = [self._diag(x) for x in diags if x.isError()]
        self.warnings = sum(1 for x in diags if not x.isError())
        self.am = analysis.AnalysisManager()
        self.am.analyze(self.comp)
        self.root = self.comp.getRoot()
        self.tops = list(self.root.topInstances)
        self.signals: dict[str, object] = {}
        self.instances: dict[str, object] = {}
        self.edges: list[Edge] = []
        self._lines: dict[str, list[str]] = {}
        for inst in self.tops:
            self._index_instance(inst)
        self.drivers: dict[str, list[Edge]] = {}
        self.loads: dict[str, list[Edge]] = {}
        for e in self.edges:
            self.drivers.setdefault(e.target, []).append(e)
            for r in dict.fromkeys(e.reads + [x for c in e.conditions for x in c.reads]):
                self.loads.setdefault(r, []).append(e)

    # -- locations
    def _diag(self, x) -> str:
        loc = x.location
        try:
            return f"{self.rel(self.sm.getFullPath(loc.buffer))}:{self.sm.getLineNumber(loc)} {x.code}"
        except Exception:  # noqa: BLE001 - a diagnostic without a location
            return str(x.code)

    def rel(self, path) -> str:
        p = Path(str(path))
        try:
            return str(p.resolve().relative_to(self.root_dir.resolve()))
        except ValueError:
            return str(p)

    def loc(self, sr) -> tuple[str, int]:
        start = sr.start if hasattr(sr, "start") else sr
        try:
            return self.rel(self.sm.getFullPath(start.buffer)), int(self.sm.getLineNumber(start))
        except Exception:  # noqa: BLE001
            return "", 0

    def line_text(self, file: str, line: int) -> str:
        if not file or not line:
            return ""
        if file not in self._lines:
            p = Path(file) if Path(file).is_absolute() else self.root_dir / file
            try:
                self._lines[file] = p.read_text(errors="replace").splitlines()
            except OSError:
                self._lines[file] = []
        lines = self._lines[file]
        return lines[line - 1].strip()[:200] if 0 < line <= len(lines) else ""

    # -- the index
    def _reads(self, expr) -> list[str]:
        if expr is None:
            return []
        from pyslang import ast
        out: list[str] = []

        def visit(node):
            if isinstance(node, ast.Expression) and node.kind in (ast.ExpressionKind.NamedValue,
                                                                 ast.ExpressionKind.HierarchicalValue):
                sym = node.symbol
                if sym is not None and sym.kind in (ast.SymbolKind.Variable, ast.SymbolKind.Net):
                    out.append(sym.hierarchicalPath)
            return True
        expr.visit(visit)
        return list(dict.fromkeys(out))

    def _index_instance(self, inst) -> None:
        self.instances[inst.hierarchicalPath] = inst
        self._index_scope(inst.body)

    def _index_scope(self, scope) -> None:
        from pyslang import ast
        K = ast.SymbolKind
        for m in scope:
            k = m.kind
            if k in (K.Variable, K.Net):
                self.signals[m.hierarchicalPath] = m
                if k == K.Net and m.initializer is not None:
                    f, ln = self.loc(m.location)
                    self.edges.append(Edge(m.hierarchicalPath, "assign", f, ln, self.line_text(f, ln),
                                           self._reads(m.initializer)))
            elif k == K.ContinuousAssign:
                a = m.assignment
                self._assignment(a, "assign", [], "", 0)
            elif k == K.ProceduralBlock:
                kind = str(m.procedureKind).rsplit(".", 1)[-1]
                kind = {"AlwaysComb": "always_comb", "AlwaysFF": "always_ff", "AlwaysLatch": "always_latch",
                        "Always": "always", "Initial": "initial", "Final": "final"}.get(kind, kind.lower())
                _, bl = self.loc(m.location)
                self._statement(m.body, kind, [], "", bl)
            elif k == K.Instance:
                self._ports(m)
                self._index_instance(m)
            elif k == K.GenerateBlock:
                if not m.isUninstantiated:
                    self._index_scope(m)
            elif k == K.GenerateBlockArray:
                for entry in m.entries:
                    if not entry.isUninstantiated:
                        self._index_scope(entry)
            elif k == K.InstanceArray:
                for el in m.elements:
                    if el.kind == K.Instance:
                        self._ports(el)
                        self._index_instance(el)

    def _ports(self, inst) -> None:
        from pyslang import ast
        for pc in inst.portConnections:
            port, expr = pc.port, pc.expression
            if expr is None or port.kind != ast.SymbolKind.Port or port.internalSymbol is None:
                continue
            inner = port.internalSymbol.hierarchicalPath
            sr = expr.sourceRange if expr.syntax is not None else None
            f, ln = self.loc(sr) if sr is not None else self.loc(inst.location)
            via = f"{inst.hierarchicalPath}.{port.name}"
            direction = str(port.direction).rsplit(".", 1)[-1]
            if direction == "In":
                self.edges.append(Edge(inner, "port_in", f, ln, self.line_text(f, ln), self._reads(expr), via=via))
            elif direction == "Out":
                lhs = expr.left if expr.kind == ast.ExpressionKind.Assignment else expr
                for t in self._reads(lhs):
                    self.edges.append(Edge(t, "port_out", f, ln, self.line_text(f, ln), [inner], via=via))

    def _cond(self, expr, branch: str) -> Cond:
        f, ln = self.loc(expr.sourceRange)
        return Cond(ln, self.line_text(f, ln), branch, self._reads(expr))

    def _assignment(self, a, kind: str, conds: list[Cond], clock: str, block_line: int) -> None:
        from pyslang import ast
        if a is None or a.kind != ast.ExpressionKind.Assignment:
            return
        f, ln = self.loc(a.sourceRange)
        targets = self._reads(a.left)
        reads = self._reads(a.right)
        for t in targets[:1] or []:
            extra = [x for x in targets[1:] if x != t]  # index variables on the left
            self.edges.append(Edge(t, kind, f, ln, self.line_text(f, ln), list(dict.fromkeys(reads + extra)),
                                   list(conds), clock, block_line=block_line))

    def _statement(self, s, kind: str, conds: list[Cond], clock: str, bl: int) -> None:
        from pyslang import ast
        if s is None:
            return
        S = ast.StatementKind
        k = s.kind
        if k == S.List:
            for x in s.list:
                self._statement(x, kind, conds, clock, bl)
        elif k == S.Block:
            self._statement(s.body, kind, conds, clock, bl)
        elif k == S.ExpressionStatement:
            self._assignment(s.expr, kind, conds, clock, bl)
        elif k == S.Timed:
            clk = clock
            if kind == "always_ff" and not clock:
                clk = ",".join(self._reads(_timing_expr(s.timing))) if _timing_expr(s.timing) is not None else ""
            self._statement(s.stmt, kind, conds, clk, bl)
        elif k == S.Conditional:
            here = [self._cond(c.expr, "true") for c in s.conditions]
            self._statement(s.ifTrue, kind, conds + here, clock, bl)
            # else: none of the conditions held (with `if (a &&& b)` lists, each one may be the false one)
            self._statement(s.ifFalse, kind, conds + [Cond(c.line, c.text, "false", c.reads) for c in here],
                            clock, bl)
        elif k == S.Case:
            for item in s.items:
                label = ", ".join(str(e.syntax).strip() if e.syntax is not None else "?" for e in item.expressions)
                c = self._cond(s.expr, f"case {label}")
                c.reads = list(dict.fromkeys(c.reads + [r for e in item.expressions for r in self._reads(e)]))
                self._statement(item.stmt, kind, conds + [c], clock, bl)
            if s.defaultCase is not None:
                c = self._cond(s.expr, "case default")
                self._statement(s.defaultCase, kind, conds + [c], clock, bl)
        elif k in (S.ForLoop, S.WhileLoop, S.DoWhileLoop, S.ForeverLoop, S.RepeatLoop, S.ForeachLoop):
            self._statement(s.body, kind, conds, clock, bl)
        else:
            # anything else (wait, event triggers, ...): assignments in it, without further conditions
            found = []

            def visit(node):
                if isinstance(node, ast.Expression) and node.kind == ast.ExpressionKind.Assignment:
                    found.append(node)
                return True
            try:
                s.visit(visit)
            except Exception:  # noqa: BLE001
                return
            for a in found:
                self._assignment(a, kind, conds, clock, bl)

    # -- lookups
    @property
    def top(self) -> str:
        return self.tops[0].name if self.tops else ""

    def resolve(self, name: str, wave_prefix: str = "") -> str | None:
        """A slang path for a name given as a slang path, a path below the top, or a waveform path."""
        name = name.strip()
        if name in self.signals:
            return name
        if f"{self.top}.{name}" in self.signals:
            return f"{self.top}.{name}"
        if wave_prefix and name.startswith(wave_prefix + "."):
            cand = f"{self.top}.{name[len(wave_prefix) + 1:]}"
            if cand in self.signals:
                return cand
        return None

    def suggest(self, name: str, n: int = 6) -> list[str]:
        leaf = name.rsplit(".", 1)[-1]
        hits = [p for p in self.signals if p.rsplit(".", 1)[-1] == leaf]
        if not hits:
            hits = [p for p in self.signals if leaf.lower() in p.rsplit(".", 1)[-1].lower()]
        return sorted(hits, key=len)[:n]

    def width(self, path: str) -> int:
        sym = self.signals.get(path)
        try:
            return int(sym.type.bitWidth) if sym is not None else 0
        except Exception:  # noqa: BLE001
            return 0


def _timing_expr(timing):
    for attr in ("expr", "events"):
        x = getattr(timing, attr, None)
        if x is not None:
            return x if attr == "expr" else (x[0].expr if len(x) and hasattr(x[0], "expr") else None)
    return None


_CACHE: dict[str, Design] = {}


def _key(project: Project) -> str:
    h = hashlib.sha256(str(project.root).encode())
    env = project_env(project)
    for fl in project.lint.filelists:
        p = project.root / fl
        if p.is_file():
            for f in [p, *read_filelist(p, env, project.root)]:
                try:
                    h.update(f"{f}:{f.stat().st_mtime_ns}:{f.stat().st_size}".encode())
                except OSError:
                    h.update(str(f).encode())
    return h.hexdigest()


def load_design(project: Project) -> Design:
    """The compiled design, rebuilt when a source file changes (the MCP server lives long)."""
    key = _key(project)
    if key not in _CACHE:
        _CACHE.clear()
        _CACHE[key] = Design(project)
    return _CACHE[key]


# ---------------------------------------------------------------- results

def _envelope(tool: str, status: str, summary: str, metrics: dict, design: Design | None = None,
              error_class: str | None = None, risks: list[str] | None = None) -> ToolResult:
    m = dict(metrics)
    if design is not None:
        m.setdefault("compile", {"top": design.top, "files": len(design.files), "errors": design.errors[:10],
                                 "error_count": len(design.errors), "warnings": design.warnings})
    risk = list(RESIDUAL) + list(risks or [])
    if design is not None and design.errors:
        risk.append(f"slang reported {len(design.errors)} error(s) elaborating the design; the index may be "
                    "incomplete")
    return ToolResult(tool, status, LEVEL, CLAIM_SCOPE, summary=summary, metrics=m, tool_versions=versions(),
                      error_class=error_class, residual_risk=risk, diagnostic=True)


def not_found(tool: str, name: str, design: Design) -> dict:
    """A name that is not a signal of the design: plain facts (no envelope), so it is not a failed check."""
    return {"tool": tool, "found": False, "signal": name,
            "message": f"no signal '{name}' in the elaborated design (top '{design.top}'); give a hierarchical "
                       "path from the top or the waveform's own path",
            "suggestions": design.suggest(name)}


def _unavailable(tool: str, why: str) -> ToolResult:
    return ToolResult(tool, "error", LEVEL, CLAIM_SCOPE, summary=why, error_class="not_installed",
                      tool_versions=versions(), diagnostic=True)


def _compile(tool: str, project: Project) -> Design | ToolResult:
    if why := available():
        return _unavailable(tool, why)
    try:
        return load_design(project)
    except Exception as exc:  # noqa: BLE001 - report, do not crash the server
        return ToolResult(tool, "error", LEVEL, CLAIM_SCOPE, summary=f"slang could not compile the design: {exc}",
                          error_class="infra", tool_versions=versions(), diagnostic=True)


def run_hierarchy(project: Project, instance: str | None = None, depth: int = 3, signals: bool = False,
                  max_signals: int = 80) -> ToolResult | dict:
    d = _compile("hierarchy", project)
    if isinstance(d, ToolResult):
        return d
    from pyslang import ast
    start = d.tops[0] if not instance else (d.instances.get(instance) or d.instances.get(f"{d.top}.{instance}"))
    if start is None:
        return {"tool": "hierarchy", "found": False, "instance": instance,
                "message": f"no instance '{instance}'", "suggestions": sorted(
                    p for p in d.instances if p.rsplit(".", 1)[-1] == instance.rsplit(".", 1)[-1])[:6]}
    depth = max(0, min(depth, MAX_DEPTH))
    count = [0]

    def node(inst, level: int) -> dict:
        count[0] += 1
        f, ln = d.loc(inst.location)
        df, dl = d.loc(inst.definition.location) if inst.definition is not None else ("", 0)
        out = {"path": inst.hierarchicalPath, "module": inst.definition.name if inst.definition else "?",
               "instantiated_at": f"{f}:{ln}" if f else "", "defined_at": f"{df}:{dl}" if df else ""}
        kids = []
        if level < depth:
            for c in _child_instances(inst):
                if count[0] >= MAX_CHILDREN:
                    out["truncated"] = True
                    break
                kids.append(node(c, level + 1))
        if kids:
            out["children"] = kids
        return out

    tree = node(start, 0)
    sigs = []
    if signals:
        for m in start.body:
            if m.kind == ast.SymbolKind.Port:
                continue
            if m.kind in (ast.SymbolKind.Variable, ast.SymbolKind.Net):
                f, ln = d.loc(m.location)
                port = next((p for p in start.body.portList if getattr(p, "internalSymbol", None) is not None
                             and p.internalSymbol.hierarchicalPath == m.hierarchicalPath), None)
                sigs.append({"path": m.hierarchicalPath, "width": d.width(m.hierarchicalPath),
                             "direction": str(port.direction).rsplit(".", 1)[-1].lower() if port else "internal",
                             "declared_at": f"{f}:{ln}", "drivers": len(d.drivers.get(m.hierarchicalPath, [])),
                             "loads": len(d.loads.get(m.hierarchicalPath, []))})
    metrics = {"instance": start.hierarchicalPath, "tree": tree, "instances_listed": count[0]}
    if signals:
        metrics |= {"signals": sigs[:max_signals], "signals_total": len(sigs)}
    return _envelope("hierarchy", "pass",
                     f"{start.hierarchicalPath} ({tree['module']}): {count[0]} instance(s) to depth {depth}"
                     + (f", {len(sigs)} signal(s)" if signals else ""), metrics, d)


def _child_instances(inst) -> list:
    from pyslang import ast
    K = ast.SymbolKind
    out = []

    def scan(scope):
        for m in scope:
            if m.kind == K.Instance:
                out.append(m)
            elif m.kind == K.GenerateBlock and not m.isUninstantiated:
                scan(m)
            elif m.kind == K.GenerateBlockArray:
                for e in m.entries:
                    if not e.isUninstantiated:
                        scan(e)
            elif m.kind == K.InstanceArray:
                out.extend(x for x in m.elements if x.kind == K.Instance)
    scan(inst.body)
    return out


def _step(d: Design, e: Edge, depth: int, values) -> dict:
    out = {"depth": depth, "signal": e.target, "kind": e.kind, "at": e.where(), "text": e.text}
    if e.via:
        out["via"] = e.via
    if e.block_line:
        out["block_at"] = f"{e.file}:{e.block_line}"
    if e.clock:
        out["clock"] = e.clock
    if e.conditions:
        out["conditions"] = [{"at": f"{e.file}:{c.line}", "text": c.text, "branch": c.branch,
                              "reads": c.reads} for c in e.conditions]
    out["reads"] = e.reads
    if values is not None:
        names = [e.target, *e.reads, *[r for c in e.conditions for r in c.reads]]
        out["values"] = {n: values(n) for n in dict.fromkeys(names)}
    return out


def _trace(tool: str, project: Project, signal: str, depth: int, max_steps: int, waveform: str | None,
           time: int | None, backwards: bool) -> ToolResult | dict:
    d = _compile(tool, project)
    if isinstance(d, ToolResult):
        return d
    values, wave_meta = None, {}
    if waveform:
        from .waves import value_reader
        got = value_reader(project, waveform, time, d)
        if isinstance(got, ToolResult):
            return got
        values, wave_meta = got
    prefix = wave_meta.get("scope_prefix", "")
    start = d.resolve(signal, prefix)
    if start is None:
        return not_found(tool, signal, d)
    depth = max(1, min(depth, MAX_DEPTH))
    max_steps = max(1, min(max_steps, MAX_STEPS))
    index = d.drivers if backwards else d.loads
    steps, seen_edges, seen_sigs = [], set(), {start}
    queue = deque([(start, 1)])
    truncated = False
    no_driver = []
    while queue:
        sig, lvl = queue.popleft()
        edges = index.get(sig, [])
        if backwards and not edges:
            no_driver.append(sig)
        for e in edges:
            key = id(e)
            if key in seen_edges:
                continue
            if len(steps) >= max_steps:
                truncated = True
                break
            seen_edges.add(key)
            steps.append(_step(d, e, lvl, values))
            if lvl >= depth:
                continue
            nxt = (e.reads + [r for c in e.conditions for r in c.reads]) if backwards else [e.target]
            for n in nxt:
                if n not in seen_sigs:
                    seen_sigs.add(n)
                    queue.append((n, lvl + 1))
        if truncated:
            break
    files = sorted({s["at"].rsplit(":", 1)[0] for s in steps if s["at"]})
    metrics = {"signal": start, "direction": "drivers" if backwards else "loads", "depth": depth,
               "steps": steps, "step_count": len(steps), "truncated": truncated, "files": files,
               "locations": sorted({s["at"] for s in steps} | {c["at"] for s in steps for c in s.get("conditions", [])})}
    if backwards:
        metrics["undriven"] = no_driver[:20]  # top-level inputs, or written by something not indexed
    if waveform:
        metrics |= {"waveform": wave_meta, "time": time}
    what = "driver" if backwards else "load"
    summary = (f"{len(steps)} {what} step(s) from {start} to depth {depth}"
               + (" (truncated)" if truncated else "") + (f"; values at t={time}" if waveform else ""))
    risks = ([f"waveform names were mapped to slang paths: {wave_meta.get('mapping_note')}"]
             if waveform and wave_meta.get("mapping_note") else [])
    return _envelope(tool, "pass", summary, metrics, d, risks=risks)


def run_trace_driver(project: Project, signal: str, depth: int = 3, max_steps: int = 25,
                     waveform: str | None = None, time: int | None = None) -> ToolResult | dict:
    return _trace("trace_driver", project, signal, depth, max_steps, waveform, time, backwards=True)


def run_trace_load(project: Project, signal: str, depth: int = 2, max_steps: int = 25,
                   waveform: str | None = None, time: int | None = None) -> ToolResult | dict:
    return _trace("trace_load", project, signal, depth, max_steps, waveform, time, backwards=False)


def edges_as_dicts(d: Design) -> list[dict]:
    """For tests and debugging: the whole index."""
    return [asdict(e) for e in d.edges]
