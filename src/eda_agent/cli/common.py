"""What several subcommands share: models, prompts, the project lookup, reviews, narration."""
from __future__ import annotations

import argparse
import json
import sys
import tomllib
from importlib.resources import files
from pathlib import Path

from ..agent.models import AnthropicAdapter, OpenAICompatAdapter
from ..agent.receipt import with_trace_data
from ..agent.receipt.narrative import PROMPT as SUMMARY_PROMPT
from ..agent.receipt.narrative import write_narrative
from ..agent.reviewer import LEVELS, load_trace, model_review, rules_review
from ..core.settings import PROJECT_FILE, find_project

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


def make_model(provider: str, model: str, effort: str, max_tokens: int = 16000):
    if provider == "deepseek":
        return OpenAICompatAdapter(model=model, effort=effort, max_tokens=max_tokens)
    return AnthropicAdapter(model=model, effort=effort, max_tokens=max_tokens)


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


def _levels(receipt: dict) -> str:
    from ..agent.levels import counts_line
    return counts_line((receipt.get("levels") or {}).get("counts") or {"fatal": 0, "error": 0, "warning": 0})


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


def _print_checks(checks) -> None:
    mark = {"ok": "ok  ", "warn": "WARN", "fail": "FAIL"}
    for c in checks:
        print(f"  {mark[c.status]} {c.name}: {c.detail}")


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
