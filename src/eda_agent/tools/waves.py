"""waveform_query: values and changes of a few signals in a time window of a VCD/FST file (facts, E0).

Read in-process with pywellen. Output is capped (signals, changes per signal), so a query never
floods the model's context. Signals may be named

  - by the waveform's own path (`TOP.tb_torv_soc.dut.u_cpu.u_core.u_fwd.forward_b`), or a pattern with
    `*` over those paths;
  - by the slang path of the elaborated design (`torv_soc.u_cpu.u_core.u_fwd.forward_b`, as hierarchy
    and trace_* print them), or a path below the top (`u_cpu.u_core.u_fwd.forward_b`).

Mapping slang paths to waveform paths (`scope_prefix`). Verilator puts its own `TOP` scope above
the testbench, and the design under test sits below the testbench under the instance name the
testbench gives it (`TOP.tb_torv_soc.dut` for slang's `torv_soc`). The prefix is found by
looking for the waveform scope whose children best match the slang top's instances and signals;
the score and runner-up are reported. Where it is uncertain (documented in residual_risk):

  - a testbench that instantiates the design twice: the first best match wins;
  - generate blocks and arrays: Verilator may write `gen[0]` or `gen(0)`, unnamed blocks are
    `unnamedblkN` in Verilator and have no name in slang; such paths may not map;
  - signals Verilator optimised away or inlined (no `--trace` of them, or `__DOT__` flattened names
    in older versions) are missing from the waveform; the tool says "not in waveform", never guesses;
  - the lint file set (what slang elaborates) and the simulation file set may differ.
"""
from __future__ import annotations

import fnmatch
from pathlib import Path

from ..core.envelope import ToolResult
from ..core.project import Project

LEVEL = "E0"
CLAIM_SCOPE = ("Values and changes of the named signals in a waveform the simulation wrote, in the given time "
               "window, as recorded (no re-simulation). Diagnostic facts: they show what happened, not why, "
               "and prove nothing about correctness elsewhere.")
RESIDUAL = ["only the signals and the time window asked for were read; the output is capped",
            "a waveform shows one simulation run of one case"]
MAX_SIGNALS = 16
MAX_CHANGES = 60
MAX_BYTES = 2 * 1024 ** 3
SUFFIXES = (".vcd", ".fst", ".ghw")
RUNS = Path("runs")


def available() -> str:
    try:
        import pywellen  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        return f"pywellen is not installed ({exc}); pip install 'eda-agent[debug]'"
    return ""


def _versions() -> dict:
    from .rtl_trace import versions
    return versions()


def fmt(value, width: int) -> str | None:
    """A value as Verilog-style text: 1-bit 0/1/x/z, else <w>'h.. (or <w>'b.. when it has x/z bits)."""
    if value is None:
        return None
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):  # pywellen: a bit string when some bit is x/z
        return value if width <= 1 else f"{width}'b{value}"
    if width <= 1:
        return str(int(value))
    return f"{width}'h{int(value):x}"


class Wave:
    """One opened waveform: its variables by full path and its scope paths."""

    def __init__(self, path: Path):
        import pywellen
        self.path = path
        self.w = pywellen.Waveform(str(path))
        self.vars = {}
        for v in self.w.all_vars():
            self.vars.setdefault(v.full_name, v)
        self.scopes = {s.full_name for s in self.w.all_scopes()}
        self.timescale = str(self.w.timescale)
        self.format = str(self.w.file_format)
        self._prefix: dict[str, tuple[str, str]] = {}

    def value_at(self, name: str, t: int) -> str | None:
        v = self.vars[name]
        return fmt(v.signal.value_at(int(t)), int(v.bitwidth or 0))

    def changes(self, name: str, start: int, end: int, cap: int) -> tuple[str | None, list, int, int | None]:
        """(value at start, [(t, value)] in (start, end] up to cap, how many changes in the window, last change)."""
        v = self.vars[name]
        s, width = v.signal, int(v.bitwidth or 0)
        n = len(s)
        lo, hi = 0, n  # first change with time > start
        while lo < hi:
            mid = (lo + hi) // 2
            if s[mid][0] <= start:
                lo = mid + 1
            else:
                hi = mid
        out, total = [], 0
        i = lo
        while i < n and s[i][0] <= end:
            total += 1
            if len(out) < cap:
                out.append([int(s[i][0]), fmt(s[i][1], width)])
            i += 1
        last = int(s[n - 1][0]) if n else None
        return fmt(s.value_at(int(start)), width), out, total, last

    def end_time(self) -> int | None:
        """The latest change of a few 1-bit signals near the top (a clock usually), as the run's end."""
        best = None
        for name, v in list(self.vars.items())[:200]:
            if int(v.bitwidth or 0) == 1:
                s = v.signal
                if len(s):
                    t = int(s[len(s) - 1][0])
                    best = t if best is None or t > best else best
        return best

    def prefix_for(self, top: str, children: list[str]) -> tuple[str, str]:
        """(waveform scope that stands for slang's top, a note on how sure that is)."""
        key = top + "|" + ",".join(children)
        if key in self._prefix:
            return self._prefix[key]
        scored = []
        for sc in self.scopes:
            hit = sum(1 for c in children if f"{sc}.{c}" in self.scopes or f"{sc}.{c}" in self.vars)
            if hit:
                scored.append((hit, -len(sc), sc))
        scored.sort(reverse=True)
        if not scored:
            res = ("", f"no waveform scope contains the instances or signals of slang top '{top}'")
        else:
            hit, _, sc = scored[0]
            note = f"slang top '{top}' = waveform scope '{sc}' ({hit} of {len(children)} names matched"
            if len(scored) > 1 and scored[1][0] == hit:
                note += f"; '{scored[1][2]}' matched as well, the shorter path was taken"
            res = (sc, note + ")")
        self._prefix[key] = res
        return res


_CACHE: dict[tuple[str, int], Wave] = {}


def open_wave(project: Project, waveform: str) -> Wave | str:
    """The waveform if it is a VCD/FST under the project root or eda-agent's runs/ folder, else why not."""
    if why := available():
        return why
    p = Path(waveform).expanduser()
    if not p.is_absolute():
        p = project.root / p
    try:
        real = p.resolve(strict=True)
    except (OSError, RuntimeError):
        return f"waveform not found: {waveform}"
    bases = [project.root.resolve(), RUNS.resolve()]
    if not any(real.is_relative_to(b) for b in bases):
        return "the waveform must be under the project root or eda-agent's runs/ folder"
    if real.suffix.lower() not in SUFFIXES:
        return f"not a waveform file ({', '.join(SUFFIXES)})"
    if real.stat().st_size > MAX_BYTES:
        return f"waveform too large ({real.stat().st_size} bytes > {MAX_BYTES}); dump fewer signals or a shorter run"
    key = (str(real), real.stat().st_mtime_ns)
    if key not in _CACHE:
        _CACHE.clear()  # one waveform at a time: they can be large
        try:
            _CACHE[key] = Wave(real)
        except Exception as exc:  # noqa: BLE001
            return f"pywellen could not read {waveform}: {exc}"
    return _CACHE[key]


def _top_names(design) -> tuple[str, list[str]]:
    from .rtl_trace import _child_instances
    top = design.tops[0]
    kids = [c.name for c in _child_instances(top)]
    sigs = [p.rsplit(".", 1)[-1] for p in design.signals if p.count(".") == 1 and p.startswith(design.top + ".")]
    return design.top, kids + sigs


_BRACKETS = (("[", "("), ("]", ")"))


def to_wave_name(wave: Wave, design, slang_path: str) -> str | None:
    if design is None:
        return None
    top, names = _top_names(design)
    prefix, _ = wave.prefix_for(top, names)
    if not prefix or not slang_path.startswith(top + "."):
        return None
    cand = prefix + slang_path[len(top):]
    if cand in wave.vars:
        return cand
    alt = cand
    for a, b in _BRACKETS:
        alt = alt.replace(a, b)
    return alt if alt in wave.vars else None


def value_reader(project: Project, waveform: str, time: int | None, design):
    """For trace_*: (name -> value at `time` or a reason, waveform facts), or an error envelope."""
    wave = open_wave(project, waveform)
    if isinstance(wave, str):
        return ToolResult("waveform_query", "error", LEVEL, CLAIM_SCOPE, summary=wave, error_class="bad_request",
                          tool_versions=_versions(), diagnostic=True)
    top, names = _top_names(design)
    prefix, note = wave.prefix_for(top, names)
    t = int(time or 0)

    def values(slang_path: str) -> str:
        name = to_wave_name(wave, design, slang_path)
        if name is None:
            return "not in waveform"
        return wave.value_at(name, t) or "unknown"
    meta = {"path": str(wave.path), "timescale": wave.timescale, "scope_prefix": prefix, "mapping_note": note}
    return values, meta


def run_waveform_query(project: Project, waveform: str, signals: list[str], start: int, end: int | None = None,
                       max_changes: int = 40) -> ToolResult | dict:
    wave = open_wave(project, waveform)
    if isinstance(wave, str):
        return ToolResult("waveform_query", "error", LEVEL, CLAIM_SCOPE, summary=wave, error_class="bad_request",
                          tool_versions=_versions(), diagnostic=True)
    start = int(start)
    end = start if end is None else int(end)
    if end < start:
        start, end = end, start
    cap = max(1, min(int(max_changes), MAX_CHANGES))
    design, note, prefix = None, "", ""
    from .rtl_trace import available as slang_available, load_design
    if not slang_available():
        try:
            design = load_design(project)
            top, names = _top_names(design)
            prefix, note = wave.prefix_for(top, names)
        except Exception as exc:  # noqa: BLE001 - waveform names still work without the design
            note = f"slang could not compile the design ({exc}); only waveform paths can be used"
    rows, missing, expanded = [], [], 0
    for asked in signals:
        names: list[tuple[str, str]] = []  # (waveform path, slang path)
        if "*" in asked or "?" in asked:
            hits = sorted(n for n in wave.vars if fnmatch.fnmatchcase(n, asked))
            if not hits and design is not None and prefix:
                pat = prefix + asked[len(design.top):] if asked.startswith(design.top + ".") else f"{prefix}.{asked}"
                hits = sorted(n for n in wave.vars if fnmatch.fnmatchcase(n, pat))
            expanded += len(hits)
            names = [(h, (design.resolve(h, prefix) or "") if design else "") for h in hits]
        elif asked in wave.vars:
            names = [(asked, (design.resolve(asked, prefix) or "") if design else "")]
        elif design is not None and (sp := design.resolve(asked, prefix)):
            wn = to_wave_name(wave, design, sp)
            if wn:
                names = [(wn, sp)]
            else:
                missing.append({"signal": asked, "slang": sp, "reason": "in the design but not in the waveform "
                                "(optimised away, not traced, or a scope name that does not map)"})
                continue
        if not names:
            leaf = asked.rsplit(".", 1)[-1].strip("*")
            missing.append({"signal": asked, "reason": "no such signal",
                            "suggestions": sorted((n for n in wave.vars if n.rsplit(".", 1)[-1] == leaf), key=len)[:5]})
            continue
        for wn, sp in names:
            if len(rows) >= MAX_SIGNALS:
                break
            v = wave.vars[wn]
            at_start, changes, total, last = wave.changes(wn, start, end, cap)
            rows.append({"signal": asked if asked == wn or asked == sp else wn, "waveform_path": wn, "slang": sp,
                         "width": int(v.bitwidth or 0), "at_start": at_start, "changes": changes,
                         "changes_in_window": total, "truncated": total > len(changes), "last_change": last})
    if not rows:
        return {"tool": "waveform_query", "found": False, "waveform": str(wave.path), "missing": missing,
                "scope_prefix": prefix, "mapping_note": note,
                "message": "none of the signals is in the waveform; use waveform paths, slang paths from hierarchy, "
                           "or a pattern with *"}
    truncated = len(rows) >= MAX_SIGNALS and (expanded > MAX_SIGNALS or len(signals) > MAX_SIGNALS)
    metrics = {"waveform": str(wave.path), "format": wave.format, "timescale": wave.timescale,
               "window": [start, end], "signals": rows, "missing": missing, "signals_truncated": truncated,
               "scope_prefix": prefix, "mapping_note": note, "end_time": wave.end_time()}
    risks = list(RESIDUAL)
    if note:
        risks.append(f"slang paths were mapped to waveform paths: {note}")
    summary = (f"{len(rows)} signal(s) in [{start}, {end}] ({wave.timescale} units)"
               + (f"; {len(missing)} not found" if missing else "")
               + (" (signals capped)" if truncated else ""))
    return ToolResult("waveform_query", "pass", LEVEL, CLAIM_SCOPE, summary=summary, metrics=metrics,
                      tool_versions=_versions(), residual_risk=risks, diagnostic=True)
