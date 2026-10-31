"""Command line entry point.

    eda-agent run --project projects/torv.toml "Lint the design and run the smoke case"
    eda-agent run --provider deepseek --review I2 --project projects/torv.toml "..."
    eda-agent review <run_id> --review I3 --project projects/torv.toml   (review a finished run)
    eda-agent approve <run_id>                                          (human gate)
    eda-agent reject <run_id> --reason "..."
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import datetime
from importlib.resources import files
from pathlib import Path

from .agent import human_gate
from .agent.loop import Budget, run_agent
from .agent.mcp_client import mcp_tools
from .agent.models import AnthropicAdapter, OpenAICompatAdapter
from .agent.receipt import build_receipt, write_receipt
from .agent.reviewer import LEVELS, load_trace, model_review, rules_review
from .core.project import load_project, regress_cases

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


def load_prompt(name: str) -> str:
    if name.endswith(".md"):
        return Path(name).read_text(encoding="utf-8").strip()
    return (files("eda_agent") / "prompts" / f"{name}.md").read_text(encoding="utf-8").strip()


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
                                prompt=args.prompt)
        if args.review != "none":
            try:
                receipt["reviews"] = await run_reviews(args, receipt, trace, tools, execute)
            except Exception as e:  # a failed review must not lose the author's receipt
                receipt["review_error"] = f"{type(e).__name__}: {e}"
                print(f"review failed: {receipt['review_error']}", file=sys.stderr)
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
    print(f"receipt: {md} (json: {js})")
    if receipt["status"] == human_gate.AWAITING:
        print(f"waiting for a person: eda-agent approve {run_id}   or   "
              f"eda-agent reject {run_id} --reason \"...\"")
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
    print(f"receipt: {md} (json: {js})")
    return 0


def _decide(args: argparse.Namespace, decision: str) -> int:
    runs_dir = Path(args.runs_dir)
    try:
        receipt, _ = human_gate.load_receipt(args.run_id, runs_dir)
        human_gate.decide(receipt, decision, by=args.by or human_gate.default_approver(),
                          reason=args.reason or "", override_gate=getattr(args, "override_gate", False))
    except human_gate.ApprovalError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    js, md = write_receipt(receipt, runs_dir)
    a = receipt["approval"]
    print(f"run {args.run_id}: {a['status']} by {a['by']} at {a['at']}")
    print(f"receipt: {md} (json: {js})")
    return 0


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
    run.add_argument("--project", required=True)
    run.add_argument("--provider", default="anthropic", choices=sorted(DEFAULT_MODELS))
    run.add_argument("--model", help="defaults per provider: " + ", ".join(
        f"{p}={m}" for p, m in DEFAULT_MODELS.items()))
    run.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    run.add_argument("--prompt", default=DEFAULT_PROMPT,
                     help=f"prompt name in eda_agent/prompts/, or a path to a .md file (default {DEFAULT_PROMPT})")
    run.add_argument("--max-turns", type=int, default=30)
    run.add_argument("--max-tool-calls", type=int, default=60)
    run.add_argument("--trace")
    run.add_argument("--no-approval", action="store_true",
                     help="end with status done instead of awaiting_approval (no human gate)")
    _add_review_args(run, "none")

    rev = sub.add_parser("review", help="review a finished run (writes into its receipt)")
    rev.add_argument("run_id")
    rev.add_argument("--project", required=True)
    rev.add_argument("--runs-dir", default="runs")
    _add_review_args(rev, "I2")

    for name, help_text in (("approve", "approve a run awaiting approval"), ("reject", "reject a run")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("run_id")
        p.add_argument("--by", help="who decides (default: git user.name, else the login name)")
        p.add_argument("--reason", required=name == "reject")
        p.add_argument("--runs-dir", default="runs")
        if name == "approve":
            p.add_argument("--override-gate", action="store_true",
                           help="approve a run the evidence gate rejected (needs --reason)")

    args = ap.parse_args()
    if args.cmd == "run":
        sys.exit(asyncio.run(_run(args)))
    if args.cmd == "review":
        sys.exit(asyncio.run(_review(args)))
    sys.exit(_decide(args, args.cmd))


if __name__ == "__main__":
    main()
