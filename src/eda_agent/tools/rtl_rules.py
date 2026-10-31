"""Deterministic RTL rule checks, no model involved (evidence level E0).

The rule-based reviewer's checks (idea from mcp-rtl-review): blocking assignments in
sequential blocks and non-blocking ones in combinational blocks, width truncation,
inferred latches and multiple drivers come from Verilator's own warnings; reset polarity
comes from a plain scan of the RTL text. Each finding points at a file and line.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..core.envelope import Diagnostic, ToolResult
from ..core.proc import check_allowed, run, tool_version
from ..core.project import Project, project_env
from .lint import lint_command, parse_verilator

CLAIM_SCOPE = (
    "Fixed rules over the RTL: Verilator warnings BLKSEQ, COMBDLY, WIDTHTRUNC, LATCH, MULTIDRIVEN "
    "and a text scan for reset polarity. A clean result means none of these patterns was found; "
    "it proves nothing about function or timing."
)
RESIDUAL_RISK = [
    "pattern rules only: a design can pass all of them and still be wrong",
    "the reset scan reads text, not the elaborated design; unusual coding styles can hide a reset",
]

# Verilator code -> (rule, severity, what it means)
VERILATOR_RULES = {
    "BLKSEQ": ("blocking_in_sequential", "warning", "blocking assignment (=) in a clocked block"),
    "COMBDLY": ("nonblocking_in_combinational", "warning", "non-blocking assignment (<=) in combinational logic"),
    "WIDTHTRUNC": ("width_truncation", "warning", "value truncated to a narrower width"),
    "WIDTH": ("width_truncation", "warning", "width mismatch"),
    "LATCH": ("latch", "warning", "latch inferred"),
    "MULTIDRIVEN": ("multi_driven", "error", "signal driven from more than one block"),
}

_ALWAYS = re.compile(
    r"\balways(?:_ff)?\s*@\s*\(\s*(?P<e1>pos|neg)edge\s+(?P<s1>\w+)"
    r"(?:\s*(?:or|,)\s*(?P<e2>pos|neg)edge\s+(?P<s2>\w+))?\s*\)")
_RESET_NAME = re.compile(r"(?:^|_)(?:rst|reset)(?:_?n|_b|_l)?$|^(?:rst|reset)n?$|resetn$|rstn$", re.I)
_ACTIVE_LOW_NAME = re.compile(r"(?:_n|_b|_l|resetn|rstn)$", re.I)
_IF = re.compile(r"\bif\s*\(\s*(?P<neg>!|~)?\s*(?P<sig>\w+)\s*(?:==\s*1'b(?P<val>[01]))?\s*\)")
_NEXT_BLOCK = re.compile(r"\balways|\bendmodule\b|\bassign\b|\binitial\b")
_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
SCAN_CHARS = 600  # how far into an always block the reset test is looked for


def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def scan_reset_polarity(path: Path, text: str) -> list[Diagnostic]:
    """Reset tests that disagree with the edge they are triggered on, or with the reset's name."""
    clean = _COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)  # keep offsets and lines
    diags = []
    for m in _ALWAYS.finditer(clean):
        edges = {m["s1"]: m["e1"], **({m["s2"]: m["e2"]} if m["s2"] else {})}
        body = clean[m.end():m.end() + SCAN_CHARS]
        if nxt := _NEXT_BLOCK.search(body):  # stay inside this block
            body = body[:nxt.start()]
        if not body.lstrip().startswith("begin"):  # a single statement: up to its ';'
            body = body[:body.find(";") + 1]
        test = next((t for t in _IF.finditer(body) if _RESET_NAME.search(t["sig"])), None)
        if not test:
            continue
        sig = test["sig"]
        active_low = bool(test["neg"]) != (test["val"] == "0")  # !rst or rst==0: acts when low
        line = _line(clean, m.end() + test.start())
        where = f"if ({test.group(0)[test.group(0).index('(') + 1:-1].strip()})"
        if sig in edges:
            edge_low = edges[sig] == "neg"
            if edge_low != active_low:
                diags.append(Diagnostic("error", "RESET_EDGE",
                                        f"{edges[sig]}edge {sig} but the block resets on {where}, i.e. "
                                        f"when {sig} is {'low' if active_low else 'high'}", str(path), line))
                continue
        if _ACTIVE_LOW_NAME.search(sig) and not active_low:
            diags.append(Diagnostic("warning", "RESET_NAME", f"{sig} is named active-low but the block "
                                                             f"resets on {where} (active-high)", str(path), line))
        elif not _ACTIVE_LOW_NAME.search(sig) and active_low and sig in edges:
            diags.append(Diagnostic("warning", "RESET_NAME", f"{sig} is named active-high but the block "
                                                             f"resets on {where} (active-low)", str(path), line))
    return diags


def run_rtl_rules(project: Project, max_diagnostics: int = 30) -> ToolResult:
    env = project_env(project)
    cmd = lint_command(project, env)
    if "-Wall" not in cmd:
        cmd.insert(2, "-Wall")
    if refused := check_allowed(cmd, project.root, project.allowed_commands):
        return ToolResult("rtl_rules", "error", "E0", CLAIM_SCOPE, summary=refused, error_class="not_allowed")
    versions = {"verilator": tool_version(["verilator", "--version"], env)}
    proc = run(cmd, project.root, env, timeout_s=300)
    if proc.returncode is None:
        return ToolResult("rtl_rules", "timeout", "E0", CLAIM_SCOPE, summary="verilator timed out after 300 s",
                          tool_versions=versions, error_class="timeout")
    root = str(project.root)
    findings: list[Diagnostic] = []
    for d in parse_verilator(proc.output):
        if d.code not in VERILATOR_RULES or (d.file and not d.file.startswith(root + "/")):
            continue
        rule, severity, meaning = VERILATOR_RULES[d.code]
        findings.append(Diagnostic(severity, rule.upper(), f"{meaning}: {d.msg}",
                                   d.file[len(root) + 1:] if d.file.startswith(root + "/") else d.file, d.line, d.col))
    sources = [Path(p) for p in cmd if p.startswith(root + "/") and p.endswith((".v", ".sv"))]
    for src in sources:
        for d in scan_reset_polarity(src.relative_to(project.root), src.read_text(errors="replace")):
            findings.append(d)
    by_rule: dict[str, int] = {}
    for d in findings:
        by_rule[d.code] = by_rule.get(d.code, 0) + 1
    errors = [d for d in findings if d.severity == "error"]
    shown = errors[:max_diagnostics] + [d for d in findings if d.severity != "error"][
        :max(0, max_diagnostics - len(errors))]
    return ToolResult(
        tool="rtl_rules",
        status="fail" if errors else "pass",
        evidence_level="E0",
        claim_scope=CLAIM_SCOPE,
        summary=f"{len(errors)} error(s), {len(findings) - len(errors)} warning(s) over {len(sources)} RTL file(s)",
        metrics={"errors": len(errors), "warnings": len(findings) - len(errors), "by_rule": by_rule,
                 "files_scanned": len(sources)},
        diagnostics=shown,
        diagnostics_total=len(findings),
        tool_versions=versions,
        residual_risk=list(RESIDUAL_RISK),
    )
