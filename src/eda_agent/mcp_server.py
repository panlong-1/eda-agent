"""MCP server exposing the EDA tools of one project over stdio.

    eda-mcp --project projects/torv.toml
"""
from __future__ import annotations

import argparse

from mcp.server.mcpserver import MCPServer

from .projects import Project, load_project, regress_cases
from .tools.lint import run_lint
from .tools.regress import run_regress

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

    @server.tool(description="List the regression case names that 'regress' accepts.")
    def list_regress_cases() -> dict:
        return {"cases": regress_cases(project)}

    @server.tool(description=(
        "Run the project's regression on the given case names (all cases if omitted; the "
        "full list takes several minutes). Returns per-case pass/fail and failing log "
        "tails. Evidence level E2."
    ))
    def regress(cases: list[str] | None = None) -> dict:
        return run_regress(project, cases).to_dict()

    return server


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", required=True, help="project TOML file")
    args = ap.parse_args()
    build_server(load_project(args.project)).run("stdio")


if __name__ == "__main__":
    main()
