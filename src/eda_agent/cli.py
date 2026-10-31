"""Command line entry point.

    eda-agent init ~/project/mydesign --out projects/mydesign.toml       (draft a project config)
    eda-agent doctor --project projects/mydesign.toml                   (is everything there?)
    eda-agent check  --project projects/torv.toml [--full]              (fixed lint/regress[/synth] task)
    eda-agent run --project projects/torv.toml "Lint the design and run the smoke case"
    eda-agent run --provider deepseek --review I2 --project projects/torv.toml "..."
    eda-agent review <run_id> --review I3 --project projects/torv.toml   (review a finished run)
    eda-agent approve <run_id> [--reason "..."] [--override]           (human gate; see human_gate.py)
    eda-agent reject <run_id> --reason "..."
    eda-agent pending <run_id> --note "..."                             (decide later; the run stays open)
    eda-agent review-ui [--port 8799]                                   (decide by clicking, on localhost)
    eda-agent receipt <run_id> [--narrate]                              (re-render .md/.html [+ model summary])

--project may be left out when the current directory (or a parent) has eda-agent.toml.
Personal defaults (provider, model, effort, review, narrate) live in ~/.config/eda-agent/config.toml.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tomllib
from dataclasses import asdict
from datetime import datetime
from importlib.resources import files
from pathlib import Path

from .agent import human_gate
from .agent.loop import Budget, run_agent
from .agent.mcp_client import mcp_tools
from .agent.models import AnthropicAdapter, OpenAICompatAdapter
from .agent.receipt import build_receipt, render_files, write_receipt
from .agent.receipt_llm import PROMPT as SUMMARY_PROMPT
from .agent.receipt_llm import write_narrative
from .agent.receipt_tables import with_trace_data
from .agent.receipt_view import render_summary_md, summarize
from .agent.reviewer import LEVELS, load_trace, model_review, rules_review
from .core.project import load_project, regress_cases
from .core.settings import PROJECT_FILE, find_project, user_defaults

DEFAULT_MODELS = {"anthropic": "claude-opus-5-5", "deepseek": "deepseek-v4-pro"}
# An I3 reviewer must not be the author's model; with only one vendor available, the
# other model of that vendor is the fallback (weaker independence, stated in the receipt).
I3_ALTERNATIVE = {"deepseek-v4-pro": "deepseek-v4-flash", "deepseek-v4-flash": "deepseek-v4-pro",
                  "claude-opus-5-5": "claude-sonnet-5-5"}
# System prompts ship in the package (eda_agent/prompts/<name>.md); the name, with its version,
# goes into the receipt. A path to any .md file works too, for trying a draft prompt.
DEFAULT_PROMPT = "system.v2"  # v2 (M2): the timing rule; system.v1 stays loadable by name
REVIEW_PROMPT = "reviewer.v1"
REVIEW_CHOICES = ("none", "rules", *LEVELS)
PRESETS = "presets.v1"  # fixed tasks for `eda-agent check`


def load_prompt(name: str) -> str:
    if name.endswith(".md"):
        return Path(name).read_text(encoding="utf-8").strip()
    return (files("eda_agent") / "prompts" / f"{name}.md").read_text(encoding="utf-8").strip()


def load_preset(name: str) -> str:
    data = tomllib.loads((files("eda_agent") / "prompts" / f"{PRESETS}.toml").read_text(encoding="utf-8"))
    return data[name].strip()


def resolve_project(given: str | None) -> str:
    """--project, else eda-agent.toml in the current directory or a parent."""
    if given:
        return given
    found = find_project()
    if not found:
        raise SystemExit(f"error: no --project given and no {PROJECT_FILE} in this directory or its parents")
    return str(found)


def make_model(provider: str, model: str, effort: str):
    if provider == "deepseek":
        return OpenAICompatAdapter(model=model, effort=effort)
    return AnthropicAdapter(model=model, effort=effort)


def _server_args(project_path: str) -> list[str]:
    return ["-m", "eda_agent.server.mcp_server", "--project", str(Path(project_path).resolve())]


def review_model_choice(level: str, author_provider: str, author_model: str,
                        provider: str | None, model: str | None) -> tuple[str, str]:
    provider = provider or author_provider
    if model:
        return provider, model
    if level == "I3" and provider == author_provider:
        return provider, I3_ALTERNATIVE.get(author_model, DEFAULT_MODELS[provider])
    return provider, author_model if provider == author_provider else DEFAULT_MODELS[provider]


async def run_reviews(args: argparse.Namespace, receipt: dict, trace: Path, tools, execute) -> list[dict]:
    """Rule-based review always (when any review is asked for), then the model review at args.review."""
    run = load_trace(trace)
    rtl = None
    if any(t.name == "rtl_rules" for t in tools):
        text, is_error = await execute("rtl_rules", {})
        try:
            rtl = None if is_error else json.loads(text)
        except ValueError:
            rtl = None
    reviews = [rules_review(run, rtl).to_dict()]
    if args.review in LEVELS:
        provider, model_name = review_model_choice(args.review, receipt["provider"], receipt["model"],
                                                   args.review_provider, args.review_model)
        model = make_model(provider, model_name, args.review_effort)
        review_trace = trace.with_name(f"{trace.stem}.review_{args.review}.jsonl")
        review = await model_review(model, args.review, run, tools, execute, load_prompt(REVIEW_PROMPT),
                                    provider=provider, author_model=receipt["model"], trace=review_trace,
                                    prompt=REVIEW_PROMPT)
        reviews.append(review.to_dict())
    return reviews


def _print_reviews(reviews: list[dict]) -> None:
    for v in reviews:
        print(f"\n--- review: {v['reviewer']} ({v['model'] or 'no model'}) verdict {v['verdict'] or '?'} ---")
        for f in v["findings"]:
            print(f"  [{f['severity']}] {f['text']}")


async def _run(args: argparse.Namespace) -> int:
    run_id = Path(args.trace).stem if args.trace else f"{datetime.now():%Y%m%d_%H%M%S}"
    trace = Path(args.trace or f"runs/{run_id}.jsonl")
    trace.parent.mkdir(parents=True, exist_ok=True)
    project = load_project(args.project)
    system = load_prompt(args.prompt)
    model_name = args.model or DEFAULT_MODELS[args.provider]
    if args.review == "I3":  # refuse before the author runs, not after
        _, review_model = review_model_choice("I3", args.provider, model_name, args.review_provider,
                                              args.review_model)
        if review_model == model_name:
            print(f"error: --review I3 needs a reviewer model other than the author's ({model_name})",
                  file=sys.stderr)
            return 2
    model = make_model(args.provider, model_name, args.effort)
    async with mcp_tools(sys.executable, _server_args(args.project)) as (tools, execute, instructions):
        if instructions:  # the tool server's own rules travel with the system prompt
            system = f"{system}\n\n## Tool server instructions\n{instructions}"
        report = await run_agent(
            model,
            tools, execute, args.task, system,
            Budget(max_turns=args.max_turns, max_tool_calls=args.max_tool_calls),
            trace=trace,
        )
        receipt = build_receipt(report, run_id=run_id, task=args.task, provider=args.provider,
                                project=project.name, all_cases=regress_cases(project), trace=str(trace),
                                prompt=args.prompt, sdc_origin=project.flow.sdc_origin if project.flow else "",
                                project_root=str(project.root))
        if getattr(args, "preset", None):
            receipt["preset"] = f"{args.preset}@{PRESETS}"
        if args.review != "none":
            try:
                receipt["reviews"] = await run_reviews(args, receipt, trace, tools, execute)
            except Exception as e:  # a failed review must not lose the author's receipt
                receipt["review_error"] = f"{type(e).__name__}: {e}"
                print(f"review failed: {receipt['review_error']}", file=sys.stderr)
    if args.narrate:
        await narrate(receipt, args.provider, model_name, args.effort, trace.parent)
    if report.status == "done" and not args.no_approval:
        human_gate.request_approval(receipt, "final report",
                                    "the evidence gate passed; a person accepts the result")
    js, md = write_receipt(receipt, trace.parent)
    print(report.final_text)
    print("\n--- run report ---")
    print(json.dumps({k: v for k, v in asdict(report).items() if k not in ("final_text", "checks")},
                     indent=2, ensure_ascii=False))
    _print_reviews(receipt.get("reviews", []))
    print(f"\nstatus: {receipt['status']}")
    print(f"trace: {trace}")
    print(f"receipt: {md.with_suffix('.html')} (also {md.name}, json: {js.name})")
    if receipt["status"] == human_gate.AWAITING:
        print(f"waiting for a person ({_levels(receipt)}): eda-agent review-ui, or eda-agent approve / "
              f"reject {run_id} (the receipt's commands say what approve needs)")
    return 0 if report.status == "done" else 1


async def _review(args: argparse.Namespace) -> int:
    runs_dir = Path(args.runs_dir)
    receipt, _ = human_gate.load_receipt(args.run_id, runs_dir)
    if (receipt.get("approval") or {}).get("status") in ("approved", "rejected"):
        print(f"run {args.run_id} is already {receipt['approval']['status']}; a review now would not be "
              "seen by the decision", file=sys.stderr)
        return 2
    async with mcp_tools(sys.executable, _server_args(args.project)) as (tools, execute, _):
        reviews = await run_reviews(args, receipt, Path(receipt["trace"]), tools, execute)
    if any(v["reviewer"] == "rules" for v in receipt.get("reviews", [])):
        reviews = [v for v in reviews if v["reviewer"] != "rules"]  # deterministic: once is enough
    receipt["reviews"] = receipt.get("reviews", []) + reviews
    js, md = write_receipt(receipt, runs_dir)
    _print_reviews(reviews)
    print(f"receipt: {md.with_suffix('.html')} (also {md.name}, json: {js.name})")
    return 0


def _levels(receipt: dict) -> str:
    from .agent.levels import counts_line
    return counts_line((receipt.get("levels") or {}).get("counts") or {"fatal": 0, "error": 0, "warning": 0})


def _decide(args: argparse.Namespace, decision: str) -> int:
    runs_dir = Path(args.runs_dir)
    by = args.by or human_gate.default_approver()
    try:
        receipt, _ = human_gate.load_receipt(args.run_id, runs_dir)
        if decision == "pending":
            human_gate.defer(receipt, by=by, note=args.note or "")
        else:
            human_gate.decide(receipt, decision, by=by, reason=args.reason or "",
                              override=getattr(args, "override", False))
    except human_gate.ApprovalError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    js, md = write_receipt(receipt, runs_dir)
    a = receipt["approval"]
    if decision == "pending":
        print(f"run {args.run_id}: still open; note {len(a['notes'])} recorded by {by}")
    else:
        print(f"run {args.run_id}: {a['status']} by {a['by']} at {a['at']} ({_levels(receipt)})")
    print(f"receipt: {md.with_suffix('.html')} (also {md.name}, json: {js.name})")
    return 0


async def narrate(receipt: dict, provider: str, model_name: str, effort: str, base: Path) -> None:
    """Add a model-written summary to receipt["narrative"]. A failure (no key, network) is
    reported and the receipt is rendered without it."""
    try:
        n = await write_narrative(with_trace_data(receipt, base), make_model(provider, model_name, effort),
                                  load_prompt(SUMMARY_PROMPT), provider=provider)
    except Exception as e:  # the receipt must not depend on the model
        print(f"summary not written: {type(e).__name__}: {e}", file=sys.stderr)
        return
    receipt["narrative"] = n
    print(f"summary by {provider}/{n['model']}: evidence gate "
          f"{'passed' if n['gate']['passed'] else 'REJECTED'} after {n['gate']['rewrites']} rewrite(s)")


def _receipt(args: argparse.Namespace) -> int:
    """Re-render the .md and .html of a receipt from its .json and bring its `levels` up to
    date. With --narrate, first add a model-written summary (outside the decided sha256)."""
    runs_dir = Path(args.runs_dir)
    try:
        receipt, _ = human_gate.load_receipt(args.run_id, runs_dir)
    except human_gate.ApprovalError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.narrate:
        provider = args.provider or receipt["provider"]
        model_name = args.model or (receipt["model"] if provider == receipt["provider"] else DEFAULT_MODELS[provider])
        asyncio.run(narrate(receipt, provider, model_name, args.effort, runs_dir))
    if args.out_dir:
        md, page = render_files(receipt, Path(args.out_dir), base=runs_dir)
    else:
        _, md = write_receipt(receipt, runs_dir)
        page = md.with_suffix(".html")
    if args.print:
        print(render_summary_md(summarize(with_trace_data(receipt, runs_dir))))
    print(f"receipt: {page} (also {md.name})")
    return 0


def _review_ui(args: argparse.Namespace) -> int:
    from .review_ui import serve
    return serve(Path(args.runs_dir), port=args.port, by=args.by,
                 projects_dir=Path(args.projects_dir) if args.projects_dir else None)


def _init(args: argparse.Namespace) -> int:
    from .onboard.doctor import diagnose
    from .onboard.init import write_draft
    design = Path(args.design_dir).expanduser()
    out = Path(args.out or f"projects/{design.resolve().name}.toml")
    try:
        path, det = write_draft(design, out, f"{datetime.now():%Y-%m-%d}", env_script=args.env_script,
                                run_fusesoc=not args.no_fusesoc, force=args.force)
    except FileExistsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"draft written: {path}")
    confirm = [f"[{s}] {k}" for s, d in (("lint", det.lint), ("regress", det.regress), ("flow", det.flow))
               for k, g in d.items() if g.confirm]
    if confirm:
        print("check these first: " + ", ".join(confirm))
    for n in det.notes:
        print(f"note: {n}")
    print("\nprobe (doctor on the draft):")
    checks = diagnose(path)
    _print_checks(checks)
    print(f"\nnext: read {path}, fix what is marked, then: eda-agent doctor --project {path}")
    return 0


def _print_checks(checks) -> None:
    mark = {"ok": "ok  ", "warn": "WARN", "fail": "FAIL"}
    for c in checks:
        print(f"  {mark[c.status]} {c.name}: {c.detail}")


def _doctor(args: argparse.Namespace) -> int:
    from .onboard.doctor import diagnose
    checks = diagnose(Path(resolve_project(args.project)), provider=args.provider)
    _print_checks(checks)
    failed = sum(c.status == "fail" for c in checks)
    print(f"\n{failed} problem(s)" if failed else "\nall checks passed")
    return 1 if failed else 0


def _add_run_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--project", help=f"project config (default: {PROJECT_FILE} here or in a parent directory)")
    p.add_argument("--provider", default="anthropic", choices=sorted(DEFAULT_MODELS))
    p.add_argument("--model", help="defaults per provider: " + ", ".join(
        f"{prov}={m}" for prov, m in DEFAULT_MODELS.items()))
    p.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--prompt", default=DEFAULT_PROMPT,
                   help=f"prompt name in eda_agent/prompts/, or a path to a .md file (default {DEFAULT_PROMPT})")
    p.add_argument("--max-turns", type=int, default=30)
    p.add_argument("--max-tool-calls", type=int, default=60)
    p.add_argument("--trace")
    p.add_argument("--no-approval", action="store_true",
                   help="end with status done instead of awaiting_approval (no human gate)")
    p.add_argument("--narrate", action="store_true",
                   help="add a model-written summary to the receipt (same provider and model; checked by "
                        "the evidence gate; the tables stay the record)")
    _add_review_args(p, "none")


def _add_review_args(p: argparse.ArgumentParser, default: str) -> None:
    p.add_argument("--review", default=default, choices=REVIEW_CHOICES,
                   help="independent review after the run: rules (no model) or I0-I3 (rules + a model "
                        "reviewer); I0 full author history, I1 final report only, I2 task + tool results "
                        "and may re-run tools, I3 = I2 on a different model")
    p.add_argument("--review-provider", choices=sorted(DEFAULT_MODELS),
                   help="reviewer provider (default: the author's)")
    p.add_argument("--review-model", help="reviewer model (default: the author's; for I3 the provider's "
                                          "other model, e.g. deepseek-v4-flash for deepseek-v4-pro)")
    p.add_argument("--review-effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])


def main() -> None:
    ap = argparse.ArgumentParser(prog="eda-agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run one task against a project")
    run.add_argument("task")
    _add_run_args(run)

    check = sub.add_parser("check", help="run a fixed task: lint + one regression case (--full: whole "
                                         "regression + synth)")
    check.add_argument("--full", action="store_true")
    _add_run_args(check)

    init = sub.add_parser("init", help="draft a project config from a design repository")
    init.add_argument("design_dir")
    init.add_argument("--out", help="where to write the draft (default projects/<dir name>.toml)")
    init.add_argument("--env-script", help="script to source before running the design's tools")
    init.add_argument("--no-fusesoc", action="store_true", help="do not run fusesoc --setup")
    init.add_argument("--force", action="store_true", help="overwrite an existing draft")

    doc = sub.add_parser("doctor", help="check that a project's tools, files and keys are all there")
    doc.add_argument("--project", help=f"project config (default: {PROJECT_FILE} here or in a parent)")
    doc.add_argument("--provider", choices=sorted(DEFAULT_MODELS), help="also require this provider's API key")

    rev = sub.add_parser("review", help="review a finished run (writes into its receipt)")
    rev.add_argument("run_id")
    rev.add_argument("--project", help=f"project config (default: {PROJECT_FILE} here or in a parent)")
    rev.add_argument("--runs-dir", default="runs")
    _add_review_args(rev, "I2")

    rec = sub.add_parser("receipt", help="re-render a receipt's .md and .html (review summary on top)")
    rec.add_argument("run_id")
    rec.add_argument("--runs-dir", default="runs")
    rec.add_argument("--out-dir", help="write the rendered files here instead of next to the .json")
    rec.add_argument("--print", action="store_true", help="also print the review summary")
    rec.add_argument("--narrate", action="store_true",
                     help="add a model-written summary (checked by the evidence gate); needs the provider's "
                          "API key")
    rec.add_argument("--provider", choices=sorted(DEFAULT_MODELS), help="for --narrate (default: the run's)")
    rec.add_argument("--model", help="for --narrate (default: the run's model)")
    rec.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])

    ui = sub.add_parser("review-ui", help="a local page to approve / leave pending / reject runs by clicking")
    ui.add_argument("--runs-dir", default="runs")
    ui.add_argument("--port", type=int, default=8799)
    ui.add_argument("--by", help="who decides (default: git user.name, else the login name)")
    ui.add_argument("--projects-dir", help="where the project configs are, to find each run's project root for "
                                           "its file references (default: projects/ next to the runs folder)")

    # Personal defaults apply to run and check; anything on the command line wins.
    try:
        defaults = user_defaults()
    except ValueError as e:
        raise SystemExit(f"error: {e}")
    run.set_defaults(**defaults)
    check.set_defaults(**defaults)
    if "provider" in defaults:
        doc.set_defaults(provider=defaults["provider"])
    # provider/model of `receipt --narrate` default to the run's

    for name, help_text in (("approve", "approve a run awaiting approval (Error items need --reason, Fatal "
                                        "items --override and --reason)"),
                            ("reject", "reject a run"),
                            ("pending", "decide later: record a note, the run stays awaiting approval")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("run_id")
        p.add_argument("--by", help="who decides (default: git user.name, else the login name)")
        if name == "pending":
            p.add_argument("--note", help="what is missing, or why later")
        else:
            p.add_argument("--reason", required=name == "reject")
        p.add_argument("--runs-dir", default="runs")
        if name == "approve":
            p.add_argument("--override", "--override-gate", dest="override", action="store_true",
                           help="approve despite Fatal items or the evidence gate's rejection (needs --reason; "
                                "recorded in the receipt)")

    args = ap.parse_args()
    if args.cmd == "init":
        sys.exit(_init(args))
    if args.cmd == "doctor":
        sys.exit(_doctor(args))
    if args.cmd == "receipt":
        sys.exit(_receipt(args))
    if args.cmd == "review-ui":
        sys.exit(_review_ui(args))
    if args.cmd == "check":
        args.preset = "full" if args.full else "check"
        args.task = load_preset(args.preset)
    if args.cmd in ("run", "check", "review"):
        args.project = resolve_project(args.project)
    if args.cmd in ("run", "check"):
        sys.exit(asyncio.run(_run(args)))
    if args.cmd == "review":
        sys.exit(asyncio.run(_review(args)))
    sys.exit(_decide(args, args.cmd))


if __name__ == "__main__":
    main()
