"""Test generation against surviving mutants: a fixed loop in code, the model writes only test text.

For each target mutant of a mutate run:

    test_context -> model writes a test -> write_case (runs it on the unmodified design)
      -> on failure the output goes back to the model, at most FIXES more times
    -> score_tests (passes twice on the original, fails twice on the target, other kills listed)
      -> if the target survives, the facts go back and a new attempt starts, at most ATTEMPTS

Tools are reached only through MCP (the same executor the agent loop uses). A test that closes
the gap is reviewed by a fresh model call that sees the test, the diff and the measured facts
(not the author's reasoning), then the run waits for a person (human gate); the patch is applied
by that person. A gap that could not be closed is reported as such, with what was tried.
"""
from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field

from .gate import check_report
from .models import ModelAdapter

ATTEMPTS = 3   # new tests per mutant
FIXES = 2      # corrections of one test that fails on the unmodified design
MAX_CUT_OFFS = 2  # replies in a row cut off at the output limit before giving up on a mutant
# v2 asks for brief reasoning (v1 replies hit the output limit, run tg_m3_1); v3 adds timing-only
# mutants and kind=perf tests (run tg_m3_2: the op mutant only adds stalls, no functional test can see it)
PROMPT = "testgen.v3"
REVIEW_PROMPT = "testgen-review.v1"

Execute = Callable[[str, dict], Awaitable[tuple[str, bool]]]
_BLOCK = re.compile(r"```[^\n]*\n(.*?)```", re.S)
_FIELD = re.compile(r"^(architectural_only|expected_values_checked|verdict|notes):\s*(.*)$", re.M | re.I)


def extract_block(text: str) -> str | None:
    """The last fenced code block of a reply."""
    blocks = _BLOCK.findall(text or "")
    return blocks[-1] if blocks else None


def render_task(ctx: dict) -> str:
    m = ctx["mutant"]
    change = "\n".join(f"line {c['line']}:\n  before: {c['before'].strip()}\n  after:  {c['after'].strip()}"
                       for c in m.get("changes") or [])
    examples = "\n\n".join(f"### Example test `{e['path']}` (case {e['case']})\n```\n{e['text']}\n```"
                           for e in ctx["examples"])
    caught = f"; the whole case list was also run: {m['full']['status']}" if m.get("full") else ""
    return (
        f"## The mutant\n\nMutant {m['id']} ({m['type']}) of `{m['file']}`, which the cases "
        f"{', '.join(ctx['cases_that_did_not_catch_it'])} do not catch (status: {m['status']}{caught}).\n\n"
        f"{change}\n\nPatch:\n```diff\n{ctx['patch']}```\n\n"
        f"## The module (original, with line numbers)\n\n```\n{ctx['module_source']}\n```\n\n"
        f"## How a test is judged\n\n{ctx['judged_by']}. Test files end in `{ctx['test_suffix']}`.\n\n"
        f"{examples}\n\n"
        f"Write one new test that passes on the original design and fails on mutant {m['id']}. "
        f"Use the header line `eda-agent: target mutant {m['id']} {m['file']}:{m['line']} kind=functional`."
    )


@dataclass
class Try:
    attempt: int
    fix: int
    file: str = ""
    original: str = ""      # PASS / FAIL / no code
    detail: str = ""
    reply: str = ""         # the end of the model's reply, for a person to see what it did


@dataclass
class TargetResult:
    mutant: int
    status: str = "not closed"   # closed | not closed | error
    tries: list[Try] = field(default_factory=list)
    scores: list[dict] = field(default_factory=list)   # score_tests envelopes, in order
    test: str = ""
    patch: str = ""
    review: dict = field(default_factory=dict)
    note: str = ""
    diff: str = ""          # the mutant's own patch, as test_context gave it


def _add(usage: dict, turn_usage: dict) -> None:
    for k, v in (turn_usage or {}).items():
        if isinstance(v, (int, float)):
            usage[k] = usage.get(k, 0) + v


async def close_gap(model: ModelAdapter, execute: Execute, system: str, *, run_id: str, mutate_run: str,
                    mutant: int, usage: dict, attempts: int = ATTEMPTS, fixes: int = FIXES) -> TargetResult:
    out = TargetResult(mutant)
    text, err = await execute("test_context", {"mutate_run": mutate_run, "mutant": mutant})
    ctx = json.loads(text) if not err else {"status": "error", "error": text}
    if ctx.get("status") != "ok":
        out.status, out.note = "error", ctx.get("error") or ctx.get("summary") or text[:300]
        return out
    out.diff = ctx.get("patch", "")
    model.start(system, [], render_task(ctx))
    cut_offs = 0
    for a in range(1, attempts + 1):
        passed_file = ""
        for f in range(fixes + 1):
            turn = await model.step()
            _add(usage, turn.usage)
            code = extract_block(turn.text)
            t = Try(a, f, reply=(turn.text or "")[-1500:])
            out.tries.append(t)
            if code is None:
                if turn.stop == "max_tokens":
                    t.original = "cut off (output limit)"
                    cut_offs += 1
                    if cut_offs >= MAX_CUT_OFFS:
                        out.note = (f"could not close this gap: the model's reply was cut off at the output limit "
                                    f"{cut_offs} times in a row before it wrote a test")
                        return out
                    model.add_user_message("Your reply was cut off at the output limit before the code block. "
                                           "Reason briefly, then give the complete test file in one fenced block.")
                else:
                    t.original = "no code"
                    model.add_user_message("Reply with the complete test file in exactly one fenced code block.")
                continue
            cut_offs = 0
            res_text, err = await execute("write_case", {"run_id": run_id, "name": f"m{mutant}_a{a}",
                                                         "content": code, "mutate_run": mutate_run})
            res = json.loads(res_text) if not err else {"status": "error", "summary": res_text}
            t.file = (res.get("metrics") or {}).get("path", "")
            t.original = (res.get("metrics") or {}).get("original", res.get("status", ""))
            t.detail = res.get("summary", "")[:300]
            if res.get("status") == "pass":
                passed_file = t.file
                break
            if res.get("status") == "error":
                out.status, out.note = "error", res.get("summary", "")
                return out
            tail = (res.get("diagnostics") or [{}])[0].get("msg", "")
            model.add_user_message(
                f"The test FAILS on the unmodified design ({res.get('summary', '')}), so it is wrong. Output tail:\n"
                f"```\n{tail[-1500:]}\n```\nFix it and reply with the whole corrected file.")
        if not passed_file:
            model.add_user_message("That test never passed on the unmodified design. Start again with a "
                                   "different, simpler test.")
            continue
        s_text, err = await execute("score_tests", {"run_id": run_id, "file": passed_file,
                                                    "mutate_run": mutate_run, "targets": [mutant]})
        score = json.loads(s_text) if not err else {"status": "error", "summary": s_text}
        out.scores.append(score)
        if score.get("status") == "pass":
            out.status, out.test = "closed", passed_file
            out.patch = (score.get("metrics") or {}).get("patch", "")
            return out
        if score.get("status") == "error":
            out.status, out.note = "error", score.get("summary", "")
            return out
        sm = score.get("metrics") or {}
        tgt = (sm.get("targets") or {}).get(str(mutant), {})
        model.add_user_message(
            f"The test passes on the unmodified design, but on mutant {mutant} it gave {tgt.get('runs')} "
            f"(verdict: {tgt.get('verdict')}; the test was {sm.get('kind')}, and on the unmodified design it "
            f"printed {sm.get('original_perf')}): it does not detect the change. Think again about which input "
            "makes the mutated line decide differently. If that can only change timing, follow rule 6 (kind=perf) "
            "and make the situation occur several times; otherwise write a new functional test.")
    out.note = (f"could not close this gap: {attempts} attempts; "
                + "; ".join(f"attempt {t.attempt}.{t.fix}: {t.original}" for t in out.tries))
    return out


def parse_review(text: str) -> dict:
    got = {k.lower(): v.strip() for k, v in _FIELD.findall(text or "")}
    got["format_ok"] = {"architectural_only", "verdict"} <= set(got)
    return got


async def review_test(model: ModelAdapter, system: str, test_text: str, patch: str, score: dict, usage: dict) -> dict:
    facts = {k: (score.get("metrics") or {}).get(k) for k in
             ("original", "targets", "killed", "perf_killed", "kind", "kill_rate_before", "kill_rate_after")}
    model.start(system, [], f"## Mutant diff\n```diff\n{patch}```\n\n## The test\n```\n{test_text}\n```\n\n"
                            f"## Measured facts (score_tests)\n```json\n{json.dumps(facts, indent=2)}\n```")
    turn = await model.step()
    _add(usage, turn.usage)
    return parse_review(turn.text) | {"text": turn.text}


def report_text(results: list[TargetResult]) -> str:
    """The run's report, written by code from the score_tests facts; checked by the evidence gate."""
    lines = []
    for r in results:
        if r.status == "closed":
            m = r.scores[-1]["metrics"]
            runs = len(m["targets"][str(r.mutant)]["runs"])
            if m.get("kind") == "perf":
                lines.append(f"score_tests: the new timing test (kind=perf) {r.test} kills mutant {r.mutant} by cycle "
                             f"count (in {runs} of {runs} runs; it passed {len(m['original'])} of {len(m['original'])} "
                             f"on the unmodified design); kill rate with perf {m['kill_rate_with_perf_before']:.0%} -> "
                             f"{m['kill_rate_with_perf_after']:.0%} over {m['scored']} mutants (score_tests).")
            else:
                lines.append(f"score_tests: the new test {r.test} kills mutant {r.mutant} (failed in {runs} of {runs} "
                             f"runs, passed {len(m['original'])} of {len(m['original'])} on the unmodified design); "
                             f"kill rate {m['kill_rate_before']:.0%} -> {m['kill_rate_after']:.0%} over "
                             f"{m['scored']} mutants (score_tests).")
            if m["newly_killed"] and m["newly_killed"] != [r.mutant]:
                lines.append(f"score_tests: it also newly kills mutants {', '.join(map(str, m['newly_killed']))}.")
        else:
            lines.append(f"Mutant {r.mutant}: {r.note or r.status}.")
    lines += ["", "Residual risk: kill rates cover one mutate run's sample of one module; a test that kills a "
                  "mutant shows that these changes are detected, nothing about other bugs; each new test still "
                  "needs a person's approval before it enters the regression."]
    return "\n".join(lines)


def gate_results(results: list[TargetResult]) -> dict:
    report = report_text(results)
    envelopes = [s for r in results for s in r.scores]
    return {"report": report, "decision": check_report(report, envelopes).to_dict()}


def to_dict(r: TargetResult) -> dict:
    return asdict(r)


def render_markdown(r: dict) -> str:
    """The receipt of a write-tests run, for a person deciding on the patch."""
    a = r.get("approval") or {}
    lines = [f"# Receipt {r['run_id']}: new tests against surviving mutants", "",
             f"- project: {r['project']} · mutate run: `{r['mutate_run']}` · model: {r['provider']}/{r['model']} "
             f"(prompt {r['prompt']}, review prompt {r['review_prompt']})",
             f"- status: **{r['status']}**" + (f" · approval: {a.get('status')}"
                                              + (f" by {a['by']} at {a['at']}" if a.get("by") else "") if a else ""),
             f"- evidence gate: {'passed' if r['gate']['decision']['passed'] else 'REJECTED'}"
             + "".join(f"\n  - {x}" for x in r["gate"]["decision"]["reasons"]),
             f"- tokens: {r['usage']} · wall time: {r['wall_s']} s", "", "## Per mutant", ""]
    for t in r["results"]:
        lines.append(f"### Mutant {t['mutant']}: {t['status']}")
        if t["diff"]:
            lines += ["", "```diff", t["diff"].rstrip(), "```"]
        lines += ["", "| attempt | fix | test | on the unmodified design |", "|---|---|---|---|"]
        lines += [f"| {x['attempt']} | {x['fix']} | `{x['file'] or '-'}` | {x['original']} |" for x in t["tries"]]
        for s in t["scores"]:
            m = s.get("metrics") or {}
            lines.append(f"\n- score_tests ({s.get('status')}, {s.get('evidence_level')}): {s.get('summary', '')}")
            if m.get("perf_killed"):
                lines.append(f"  - perf-killed (passed, cycle count moved): {m['perf_killed']}")
            base = m.get("original_perf") or {}
            for tid, tv in (m.get("targets") or {}).items():
                for k in ("cycles", *[k for k in base if k != "cycles"][:3]):
                    runs = [p.get(k) for p in tv.get("perf") or []]
                    if k in base and runs:
                        lines.append(f"  - mutant {tid}, {k}: unmodified design {base[k]} -> mutant "
                                     f"{', '.join(map(str, runs))}")
        if t["status"] == "closed":
            v = t.get("review") or {}
            lines += [f"\n- test: `{t['test']}` · patch: `{t['patch']}`",
                      f"- review: verdict {v.get('verdict', 'not run')}, architectural only "
                      f"{v.get('architectural_only', '?')}, expected values checked "
                      f"{v.get('expected_values_checked', '?')}" + (f" — {v['notes']}" if v.get("notes") else "")]
        elif t["note"]:
            lines.append(f"\n- {t['note']}")
        lines.append("")
    lines += ["## Report (checked by the evidence gate)", "", r["final_claims"], "",
              "Apply an approved patch in the design repository with `git apply <patch>`; eda-agent never "
              "writes there.", ""]
    return "\n".join(lines)
