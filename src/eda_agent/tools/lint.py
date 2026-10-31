"""Verilator lint (evidence level E0)."""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from ..core.envelope import Diagnostic, ToolResult
from ..core.project import Project, project_env, read_filelist
from ..core.proc import run, tool_version

# %Warning-UNUSEDSIGNAL: /path/file.sv:12:5: Signal is not used: 'x'
# %Error: /path/file.sv:265:22: Can't find definition ...
_DIAG = re.compile(
    r"^%(?P<sev>Warning|Error)(?:-(?P<code>[A-Z0-9_]+))?: "
    r"(?:(?P<file>[^:\s]+):(?P<line>\d+):(?P<col>\d+): )?(?P<msg>.*)$"
)

CLAIM_SCOPE = (
    "Verilator parsed and elaborated the design under the project's flags. "
    "This proves nothing about functional behaviour or timing."
)


def parse_verilator(output: str) -> list[Diagnostic]:
    diags = []
    for line in output.splitlines():
        m = _DIAG.match(line)
        if not m or m["msg"].startswith("Exiting due to"):
            continue
        diags.append(Diagnostic(
            severity=m["sev"].lower(),
            code=m["code"] or ("ERROR" if m["sev"] == "Error" else "WARNING"),
            msg=m["msg"].strip(),
            file=m["file"] or "",
            line=int(m["line"] or 0),
            col=int(m["col"] or 0),
        ))
    return diags


def lint_command(project: Project, env: dict[str, str]) -> list[str]:
    cfg, root = project.lint, project.root
    files: list[Path] = []
    for fl in cfg.filelists:
        if (root / fl).is_file():
            files += read_filelist(root / fl, env, root)
    files += [root / f for f in cfg.files]
    return [
        "verilator", "--lint-only", *cfg.flags,
        "--top-module", cfg.top,
        *(f"-I{root / d}" for d in cfg.include_dirs),
        *(f"-D{d}" for d in cfg.defines),
        *map(str, files),
    ]


def run_lint(project: Project, max_diagnostics: int = 20, include_external: bool = False) -> ToolResult:
    env = project_env(project)
    proc = run(lint_command(project, env), project.root, env, timeout_s=300)
    version = tool_version(["verilator", "--version"], env)
    if proc.returncode is None:
        return ToolResult("lint", "timeout", "E0", CLAIM_SCOPE, summary="lint timed out after 300 s",
                          tool_version=version, error_class="timeout")

    all_diags = parse_verilator(proc.output)
    if proc.returncode != 0 and not all_diags:
        return ToolResult("lint", "error", "E0", CLAIM_SCOPE,
                          summary="verilator failed without parseable diagnostics: " + proc.output[-500:],
                          tool_version=version, error_class="infra")

    diags = all_diags
    root = str(project.root)
    if not include_external:
        # PDK / third-party models are not ours to fix; keep errors from them, drop warnings.
        diags = [d for d in diags if d.severity == "error" or not d.file or d.file.startswith(root)]
    errors = [d for d in diags if d.severity == "error"]
    warnings = [d for d in diags if d.severity == "warning"]

    for d in diags:  # report paths relative to the project root
        if d.file.startswith(root):
            d.file = d.file[len(root) + 1:]
    shown = errors[:max_diagnostics] + warnings[: max(0, max_diagnostics - len(errors))]
    return ToolResult(
        tool="lint",
        status="fail" if errors else "pass",
        evidence_level="E0",
        claim_scope=CLAIM_SCOPE,
        summary=f"{len(errors)} error(s), {len(warnings)} warning(s), top={project.lint.top}",
        metrics={"errors": len(errors), "warnings": len(warnings),
                 "by_code": dict(Counter(d.code for d in diags).most_common())},
        diagnostics=shown,
        diagnostics_total=len(diags),
        tool_version=version,
        error_class="elaboration" if errors else None,
        residual_risk=["lint says nothing about function; run regress for E2"],
    )
