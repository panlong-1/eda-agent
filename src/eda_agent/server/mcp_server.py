"""MCP server exposing the EDA tools of one project over stdio.

    eda-mcp --project projects/torv.toml
"""
from __future__ import annotations

import argparse

from mcp.server.mcpserver import MCPServer

from ..core.project import Project, load_project
from ..tools.describe import describe_cases, list_cases
from ..tools.lint import run_lint
from ..tools.regress import run_regress
from ..tools.rtl_rules import run_rtl_rules
from ..tools.synth import run_check_sdc, run_synth

INSTRUCTIONS = (
    "Tools for one digital design project. Every result carries status, evidence_level "
    "(E0 lint ... E5 signoff) and claim_scope: state conclusions only within claim_scope, "
    "and report residual_risk."
)


def build_server(project: Project) -> MCPServer:
    server = MCPServer(name="eda-mcp", instructions=INSTRUCTIONS)

    @server.tool(description=(
        f"Verilator lint of the design top '{project.lint.top}' with the project's flags. "
        "Returns errors/warnings with file:line. Evidence level E0."
    ))
    def lint(max_diagnostics: int = 20) -> dict:
        return run_lint(project, max_diagnostics=max_diagnostics).to_dict()

    @server.tool(description=(
        "List every regression case that 'regress' accepts, each with a one-line summary of what "
        "it covers. Read all summaries: a test named for one feature often exercises others."
    ))
    def list_regress_cases() -> dict:
        return list_cases(project)

    @server.tool(description=(
        "Describe what the given regression cases cover: source file and the test's leading "
        "comment. Call it before choosing cases; names alone do not say everything a test exercises."
    ))
    def describe_case(cases: list[str]) -> dict:
        return describe_cases(project, cases)

    @server.tool(description=(
        "Run the project's regression on the given case names (all cases if omitted; the "
        "full list may take several minutes). Returns pass/fail (per case where the project's "
        f"'{project.regress.format}' result format allows) and failing log tails. Evidence level E2."
    ))
    def regress(cases: list[str] | None = None) -> dict:
        return run_regress(project, cases).to_dict()

    @server.tool(description=(
        "Fixed RTL rule checks, no model: blocking assignments in clocked blocks, non-blocking in "
        "combinational logic, width truncation, latches, multiple drivers (Verilator) and reset "
        "polarity (text scan). Findings with file:line. Evidence level E0."
    ))
    def rtl_rules(max_diagnostics: int = 30) -> dict:
        return run_rtl_rules(project, max_diagnostics=max_diagnostics).to_dict()

    flow = project.flow
    target = (f"top '{flow.top}', clock {flow.clock_port} at {flow.clock_period:g} ns, SDC {flow.sdc}"
              if flow else "not configured for this project: the tool will refuse")

    @server.tool(description=(
        f"Yosys synthesis to standard cells, then an OpenSTA timing estimate under the project's SDC "
        f"({target}) on each configured corner, before placement and routing. Returns cells, area, "
        "latches, setup WNS/TNS, hold WNS and the worst path, with the SDC's sha256. Evidence level "
        "E4. Any timing statement in the report must cite this result. Takes about a minute."
    ))
    def synth() -> dict:
        return run_synth(project).to_dict()

    @server.tool(description=(
        "Check that the project's SDC constrains the synthesized design: OpenSTA check_setup "
        "(clocks, unconstrained endpoints, loops). Facts only; does not raise the evidence level."
    ))
    def check_sdc(max_listed: int = 20) -> dict:
        return run_check_sdc(project, max_listed=max_listed).to_dict()

    return server


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", required=True, help="project TOML file")
    args = ap.parse_args()
    build_server(load_project(args.project)).run("stdio")


if __name__ == "__main__":
    main()
