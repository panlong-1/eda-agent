"""Experience-based backend tuning (plan §0.15, steps 2 and 3): lessons with citations, then
propose -> check -> run -> compare -> update the lesson, under a run budget and the human gate.

Step 2, lessons. The experience tables (flow_history: the imported runs and this design's eda_agent
runs) become a table of citable facts, one id per number (`full_p10_fwd:post_setup_wns_slow`). A
model proposes lessons from those facts and the config comments; each lesson cites fact ids. Code
then checks every lesson before it is stored:
- every cited fact exists; every run the text names is cited (R1 for numbers);
- every number in the text is a cited value (or its magnitude, the difference or percent change of
  two cited values of one metric, or the clock frequency of a cited period), at the text's precision;
- every lesson id the text names exists and was shown to the model (R11).
A lesson that passes goes into the design's lessons store as a hypothesis, scoped to the design, the
PDK, the tool, the corner and the knob. Nothing is stored that does not pass.

Step 3, tuning. Per round, while runs are left in the budget:
1. the model proposes a patch of flow knobs, the lesson it tests (or a new hypothesis) and its
   reasoning, citing lessons as hints;
2. code checks it: the knob policy (core/knobs.py; a forbidden key or value is Fatal and nothing
   runs), [pnr] allowed_overrides, R11 on the reasoning, a patch tried before. A rejected proposal
   is recorded; the model may try again once per round (the run budget is not spent);
3. flow_run runs the patch on a COPY of the design (never the design's checkout), recorded in the
   eda_agent table, compared with the current best run (better/worse lists, same snapshot rules);
4. code decides the verdict from the numbers (goal metric moved the right way and no signoff check
   got worse) and appends verify or refute to the tested lesson, citing the run;
5. the output is a config patch (the best run's knobs, as -c KEY=VALUE and as a diff of the design's
   config file) and a receipt that waits for a person. Nothing is applied to the design.

One run per setting cannot tell a knob's effect from run-to-run noise: every verdict says so.
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from ..core.hashing import sha256_file
from ..core.knobs import check_patch
from ..core.memory import LESSON_ID, LessonStore, experience_base, known_ids, lesson_stores, search
from ..core.project import Project
from .gate.r11_memory import check_memory
from .model_output import add_usage

LESSONS_PROMPT = "tune-lessons.v1"
PROPOSE_PROMPT = "tune-propose.v1"
MAX_LESSONS = 6           # per proposal call
TRIES_PER_ROUND = 2       # model proposals per run slot (a rejected one may be followed by one more)
SINGLE_RUN = ("single run per setting: the effect of the knob cannot be told apart from run-to-run noise "
              "(placement and routing react chaotically to any change); repeat the run before relying on it")

_NUM = re.compile(r"(?<![\w.])[-+−]?\d+(?:\.\d+)?(?![\w])")
_CORNER = {"fast": "fast", "slow": "slow", "typ": "typ"}


def _short_corner(c: str) -> str:
    for k in _CORNER:
        if k in c:
            return k
    return c


# ---------------------------------------------------------------- the citable facts

def experience_facts(history: dict) -> dict:
    """{fact id: {run, table, metric, value, sha256, file}} from a flow_history result (metrics)."""
    facts: dict[str, dict] = {}
    for tname, t in (history.get("tables") or {}).items():
        for r in t.get("runs", []):
            run = r["run"]
            sha = (r.get("sources") or {}).get("sta_summary") or (r.get("sources") or {}).get("config")

            def put(metric, value, _run=run, _t=tname, _sha=sha):
                if isinstance(value, bool) or value is None:
                    return
                if isinstance(value, float):
                    value = round(value, 3 if abs(value) < 1000 else 0)
                facts[f"{_run}:{metric}"] = {"run": _run, "table": _t, "metric": metric, "value": value,
                                             "sha256": _sha}
            put("clock_period", r.get("clock_period"))
            put("runtime_s", r.get("runtime_s"))
            s = r.get("synth") or {}
            put("synth_area_um2", s.get("cell_area_um2"))
            put("synth_cells", s.get("cells"))
            for c, v in (s.get("corners") or {}).items():
                put(f"pre_setup_wns_{_short_corner(c)}", v.get("setup_wns"))
            p = r.get("post_route") or {}
            for c, v in (p.get("corners") or {}).items():
                k = _short_corner(c)
                for m in ("setup_wns", "setup_tns", "setup_violations", "hold_wns"):
                    put(f"post_{m}_{k}", v.get(m))
            for m in ("die_area_um2", "utilization", "stdcell_area_um2", "stdcell_count", "power_w", "wirelength_um",
                      "lvs_errors", "antenna_nets"):
                put(m, p.get(m))
            for tool, n in (p.get("drc_by_tool") or {}).items():
                put(f"drc_{tool.lower()}", n)
            for d in r.get("config_vs_baseline") or []:
                if isinstance(d["run"], (int, float, str)) and not isinstance(d["run"], bool):
                    put(f"cfg.{d['key']}", d["run"] if not isinstance(d["run"], str) else None)
    return facts


def facts_text(facts: dict, history: dict) -> str:
    """The facts as the model sees them: one line per run, every number with its id."""
    out = []
    by_run: dict[str, list[str]] = {}
    for fid, f in facts.items():
        by_run.setdefault(f"{f['run']} ({f['table']})", []).append(f"{f['metric']}={f['value']}")
    for tname, t in (history.get("tables") or {}).items():
        for r in t.get("runs", []):
            key = f"{r['run']} ({tname})"
            diff = ", ".join(f"{d['key']}={d['run']}" for d in r.get("config_vs_baseline") or []) or "same config"
            out.append(f"- {r['run']} [{tname}, stage {r['stage']}, started {r.get('started')}; config vs "
                       f"{t.get('baseline')}: {diff}]: " + "; ".join(by_run.get(key, [])))
    out.append("")
    for tname, t in (history.get("tables") or {}).items():
        for k, v in (t.get("knob_effects") or {}).items():
            pairs = "; ".join(p.get("summary") or (" vs ".join(p.get("runs", [])) + ": also changed "
                                                   + ", ".join(map(str, p.get("also_changed", []))))
                              for p in v.get("pairs", []))
            out.append(f"- knob {k} ({tname} table): {v.get('verdict')}: {pairs}")
    return "\n".join(out)


def numbers_in(text: str) -> list[tuple[float, int]]:
    """(value, decimals) of every number in a text that is not part of a name."""
    out = []
    for m in _NUM.finditer(text.replace("−", "-")):
        s = m.group(0)
        out.append((float(s), len(s.split(".")[1]) if "." in s else 0))
    return out


def _explained(x: float, decimals: int, values: list[tuple[str, float]], periods: list[float]) -> bool:
    tol = 0.5 * 10 ** -decimals + 1e-9
    cands = []
    for _, v in values:
        cands += [v, abs(v)]
    by_metric: dict[str, list[float]] = {}
    for metric, v in values:
        by_metric.setdefault(metric, []).append(v)
    for vs in by_metric.values():
        for i, a in enumerate(vs):
            for j, b in enumerate(vs):
                if i != j:   # two facts of one metric (equal values give a change of 0)
                    cands += [b - a, abs(b - a)]
                    if a:
                        pct = 100.0 * (b - a) / abs(a)
                        cands += [pct, abs(pct)]
    cands += periods + [1000.0 / p for p in periods if p]
    return any(abs(x - c) <= tol for c in cands)


def check_lesson(lesson: dict, facts: dict, known: dict, shown: list[str], run_names: set[str],
                 existing_texts: set[str]) -> list[str]:
    """Reasons a proposed lesson may not be stored (empty: it may)."""
    reasons = []
    text = (lesson.get("text") or "").strip()
    cites = [c for c in lesson.get("cites") or [] if isinstance(c, str)]
    if not text:
        return ["(R1) the lesson has no text"]
    if text in existing_texts:
        return ["the same lesson is already in the store"]
    if not cites:
        return ["(R1) the lesson cites no fact of the experience tables: a lesson is written only from data"]
    missing = [c for c in cites if c not in facts]
    reasons += [f"(R1) cites {c}, which is not a fact of the experience tables" for c in missing]
    cited = [facts[c] for c in cites if c in facts]
    cited_runs = {f["run"] for f in cited}
    # run names that are also plain words ("synth", "fetch") are not looked for in the text
    named = {r for r in run_names if re.search(r"[\d_]", r)
             and re.search(rf"(?<![\w.-]){re.escape(r)}(?![\w-])", text)}
    reasons += [f"(R1) names run {r} but cites none of its numbers" for r in sorted(named - cited_runs)]
    # numbers: strip names first (run names, knob names, lesson ids, fact ids, corner names like 1p08V)
    bare = LESSON_ID.sub(" ", text)
    for name in sorted(run_names | {c for c in cites}, key=len, reverse=True):
        bare = bare.replace(name, " ")
    bare = re.sub(r"\b[A-Z][A-Z0-9_]{2,}\b", " ", bare)            # KNOB_NAMES
    bare = re.sub(r"\b\w*\d+p\d+V\w*\b|\b\d+C\b", " ", bare)       # corner names
    bare = re.sub(r"\b(?:E[0-5]|R1[01]?|R[2-9]|§\d+(?:\.\d+)*)\b", " ", bare)
    values = [(f["metric"], float(f["value"])) for f in cited if isinstance(f["value"], (int, float))]
    # the clock period of every cited run is part of what the cited numbers mean ("at 7 ns")
    periods = [float(facts[f"{r}:clock_period"]["value"]) for r in cited_runs if f"{r}:clock_period" in facts]
    for x, d in numbers_in(bare):
        if not _explained(x, d, values, periods):
            reasons.append(f"(R1) the number {x:g} is not a cited value (nor the difference or percent change of two "
                           f"cited values of one metric): {text!r}")
    mem = {"tool": "memory", "retrieved": shown, "known": known}
    reasons += check_memory("## Lessons\n" + text, [mem])
    return reasons


def lesson_scope(lesson: dict, facts: dict, project: Project) -> dict:
    corners = sorted({m.rsplit("_", 1)[1] for c in lesson.get("cites") or [] if c in facts
                      for m in [facts[c]["metric"]] if m.rsplit("_", 1)[-1] in _CORNER})
    return {"tool": project.pnr.backend if project.pnr else "", "pdk": project.flow.pdk if project.flow else "",
            "corner": ",".join(corners), "problem": str(lesson.get("knob") or "")}


def lesson_sources(lesson: dict, facts: dict) -> list[dict]:
    out = []
    for c in lesson.get("cites") or []:
        f = facts.get(c)
        if f:
            out.append({"run_id": f["run"], "note": f"{f['table']} table: {f['metric']} = {f['value']}",
                        **({"sha256": f["sha256"], "file": f"{f['run']} metrics/STA summary"} if f.get("sha256") else {})})
    return out


def parse_json_block(text: str) -> dict | None:
    """The last ```json block of a reply (or the reply itself when it is plain JSON)."""
    blocks = re.findall(r"```(?:json)?\s*\n(.*?)```", text or "", re.S)
    for cand in reversed(blocks or [text or ""]):
        try:
            data = json.loads(cand)
            return data if isinstance(data, dict) else None
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------- one model call

async def ask(model, system: str, task: str, usage: dict) -> str:
    model.start(system, [], task)
    turn = await model.step()
    add_usage(usage, turn.usage)
    return turn.text or ""


# ---------------------------------------------------------------- step 2: propose lessons

def lesson_context(project: Project, history: dict, store: LessonStore) -> tuple[str, list[str]]:
    """(the task text for the lessons prompt, the lesson ids shown)."""
    facts = experience_facts(history)
    shown = []
    les = []
    for e in store.current().values():
        if e["kind"] in ("lesson", "refute") and e["status"] in ("verified", "hypothesis"):
            shown.append(e["id"])
            les.append(f"- [{e['id']}] ({e['status']}) {' '.join(e['text'].split())[:500]}")
    comments = "\n".join(f"- {x['key']} = {x.get('value')!r}: \"{x.get('comment')}\" — imported-table verdict "
                         f"{x.get('verdict')} ({x.get('why')})" for x in history.get("lessons") or [])
    task = (f"## Design\n\n{project.name}, back end {history.get('backend')}, PDK "
            f"{project.flow.pdk if project.flow else '?'}.\n\n"
            f"## Facts of the experience tables (cite these ids)\n\nEach number is `<run>:<metric>`; a run's line "
            f"lists its metrics.\n\n{facts_text(facts, history)}\n\n"
            f"## Comments in the flow config, with the imported table's verdict\n\n{comments or '(none)'}\n\n"
            f"## Lessons already in the store (do not repeat them; you may refine one and name its id)\n\n"
            + ("\n".join(les) or "(none)"))
    return task, shown


async def propose_lessons(project: Project, model, system: str, history: dict, *, usage: dict,
                          write: bool = True, by: str = "agent") -> dict:
    """One model call; each proposed lesson checked by code; the ones that pass are stored as hypotheses."""
    store = LessonStore.for_project(project)
    facts = experience_facts(history)
    task, shown = lesson_context(project, history, store)
    reply = await ask(model, system, task, usage)
    data = parse_json_block(reply) or {}
    stores = lesson_stores(experience_base(project), project.name)
    known = known_ids(stores, [])
    run_names = {f["run"] for f in facts.values()}
    texts = {e["text"] for e in store.entries()}
    out = {"accepted": [], "rejected": [], "reply_tail": reply[-1500:], "parsed": bool(data)}
    for les in (data.get("lessons") or [])[:MAX_LESSONS]:
        if not isinstance(les, dict):
            continue
        reasons = check_lesson(les, facts, known, shown, run_names, texts)
        if reasons:
            out["rejected"].append({"lesson": les, "reasons": reasons})
            continue
        entry = None
        if write:
            entry = store.add(les["text"], status="hypothesis", scope=lesson_scope(les, facts, project),
                              sources=lesson_sources(les, facts), by=by,
                              origin=f"model proposal ({LESSONS_PROMPT}) from the experience tables, checked by code",
                              reason="cites: " + ", ".join(les["cites"]))
            texts.add(les["text"])
        out["accepted"].append({"lesson": les, "entry": entry})
    return out


# ---------------------------------------------------------------- step 3: tuning

@dataclass
class Attempt:
    n: int
    round: int
    source: str                      # model | file
    proposal: dict = field(default_factory=dict)
    policy: list[dict] = field(default_factory=list)   # knob findings (fatal/error)
    reasons: list[str] = field(default_factory=list)   # other rejection reasons (R11, allowed_overrides, ...)
    decision: str = "rejected"       # rejected | ran
    hypothesis_lesson: str | None = None
    run: dict = field(default_factory=dict)
    comparison: dict = field(default_factory=dict)
    verdict: str = ""
    verdict_detail: str = ""
    lesson_update: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def goal_numbers(metrics: dict) -> dict:
    """The numbers the goal and the no-regression check read, from a flow_report/flow_run result."""
    corners = metrics.get("corners") or {}
    slow_name = next((c for c in corners if "slow" in c), None)
    slow = corners.get(slow_name) or {}
    return {"slow_corner": slow_name, "setup_wns": metrics.get("wns"), "setup_tns": metrics.get("tns"), "hold_wns": metrics.get("hold_wns"),
            "setup_wns_slow": slow.get("setup_wns"), "setup_tns_slow": slow.get("setup_tns"),
            "setup_violations_slow": slow.get("setup_violations"),
            "drc": sum(v for v in (metrics.get("drc_by_tool") or {}).values() if isinstance(v, int)),
            "lvs_errors": metrics.get("lvs_errors"), "antenna_nets": metrics.get("antenna_nets"),
            "die_area_um2": (metrics.get("area") or {}).get("die_area_um2"),
            "stdcell_count": (metrics.get("area") or {}).get("stdcell_count"),
            "power_w": (metrics.get("power") or {}).get("total_w"), "runtime_s": metrics.get("runtime_s"),
            "signoff_passed": metrics.get("signoff_passed"), "complete": metrics.get("complete")}


def judge(best: dict, new: dict, metric: str = "setup_wns_slow") -> tuple[str, str]:
    """(verdict, detail) by code: improved | not_improved | signoff_worse | no_result."""
    if not new.get("complete") or new.get(metric) is None:
        return "no_result", "the run did not finish with post-route numbers"
    worse = []
    for k in ("drc", "lvs_errors", "antenna_nets"):
        if isinstance(new.get(k), int) and isinstance(best.get(k), int) and new[k] > best[k]:
            worse.append(f"{k} {best[k]} -> {new[k]}")
    if isinstance(new.get("hold_wns"), (int, float)) and new["hold_wns"] < 0 <= (best.get("hold_wns") or 0):
        worse.append(f"hold WNS {best.get('hold_wns')} -> {new['hold_wns']}")
    delta = round(new[metric] - best[metric], 3)
    detail = f"{metric} {best[metric]:+.3f} -> {new[metric]:+.3f} ns ({delta:+.3f})"
    if worse:
        return "signoff_worse", detail + "; signoff got worse: " + ", ".join(worse)
    return ("improved" if delta > 0 else "not_improved"), detail


def yaml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v)
    return s if re.fullmatch(r"[\w.\-/]+", s) and s.lower() not in ("true", "false", "null", "yes", "no") \
        else json.dumps(s)


def cli_value(v) -> str:
    return "true" if v is True else "false" if v is False else str(v)


def config_diff(config_file: Path, rel: str, patch: dict, note: str) -> str:
    """A unified diff (git apply) of the design's flow config with the patch's keys set."""
    old = config_file.read_text(encoding="utf-8").splitlines(keepends=True)
    new = list(old)
    for k, v in patch.items():
        line = f"{k}: {yaml_value(v)}\n"
        idx = next((i for i, ln in enumerate(new) if re.match(rf"^{re.escape(k)}\s*:", ln)), None)
        if idx is not None:
            new[idx] = line
        else:
            if new and not new[-1].endswith("\n"):
                new[-1] += "\n"
            new += [f"# {note}\n", line]
    return "".join(difflib.unified_diff(old, new, f"a/{rel}", f"b/{rel}"))


def policy_summary(project: Project) -> str:
    from ..core import knobs
    be = project.pnr.backend if project.pnr else "librelane"
    allowed = ", ".join(project.pnr.allowed_overrides) if project.pnr else ""
    return (f"Forbidden (Fatal, the proposal is rejected and nothing runs): {', '.join(knobs.FORBIDDEN_KEYS.get(be, ()))}; "
            "any key matching " + "; ".join(f"/{p.pattern}/ ({why})" for p, why in knobs.FORBIDDEN_PATTERNS.get(be, ()))
            + f"; turning off a check switch /{knobs.CHECK_SWITCHES.pattern}/.\n"
            f"Tunable list (core/knobs.py): /{knobs.TUNABLE[be].pattern}/.\n"
            f"Keys a person allowed for this project ([pnr] allowed_overrides): {allowed or 'none'}.")


def propose_context(project: Project, *, goal: str, best: dict, best_patch: dict, best_config: dict,
                    violators: list[dict], hints: list, facts_block: str, attempts: list[Attempt],
                    rejection: list[str] | None) -> str:
    knobs_now = {k: v for k, v in sorted(best_config.items())
                 if re.match(r"^(FP_CORE_UTIL|PL_|GRT_|DRT_OPT|CTS_|SYNTH_(STRATEGY|ABC_BUFFERING|MUL_BOOTH|SIZING|"
                             r"SHARE|ADDER)|DESIGN_REPAIR|RUN_POST)", k) and not isinstance(v, (list, dict))}
    tried = "\n".join(
        f"- attempt {a.n} (round {a.round}): patch {json.dumps(a.proposal.get('patch'))} -> {a.decision}"
        + (f"; verdict {a.verdict}: {a.verdict_detail}" if a.verdict else "")
        + (f"; rejected: {'; '.join([f['reason'] + ' (' + f['key'] + ')' for f in a.policy] + a.reasons)[:400]}"
           if a.decision == "rejected" else "")
        for a in attempts) or "(none yet)"
    hint_lines = "\n".join(f"- [{h.id}] ({h.status}{'; ' + h.note if h.note else ''}) {' '.join(h.text.split())[:500]}"
                           for h in hints) or "(none)"
    vio = "\n".join(f"- {v.get('from')} -> {v.get('to')}: slack {v.get('slack')}" for v in violators[:10]) or "(none)"
    return (f"## Goal\n\n{goal}\n\n"
            f"## Current best run: {best['run']}\n\n"
            f"Config patch on top of the design's own config: {json.dumps(best_patch) if best_patch else '(none)'}\n"
            f"Numbers: {json.dumps(best['numbers'])}\n\nWorst setup paths at the worst corner:\n{vio}\n\n"
            f"Current values of the tunable knobs in that run:\n```json\n{json.dumps(knobs_now, indent=0)}\n```\n\n"
            f"## Lessons (hints, NOT evidence; cite them by id)\n\n{hint_lines}\n\n"
            f"## Experience tables (facts)\n\n{facts_block}\n\n"
            f"## Knob policy (code)\n\n{policy_summary(project)}\n\n"
            f"## Attempts so far in this tuning run\n\n{tried}\n"
            + (f"\n## Your previous proposal was rejected\n\n" + "\n".join(f"- {r}" for r in rejection) + "\n"
               if rejection else ""))


def check_proposal(project: Project, proposal: dict, *, best_config: dict, shown: list[str], known: dict,
                   tried: list[dict]) -> tuple[list[dict], list[str]]:
    """(knob-policy findings, other reasons) for a proposal; no fatal finding and no reason: it may run."""
    patch = proposal.get("patch")
    if not isinstance(patch, dict):
        return [], ["the proposal has no patch object"]
    be = project.pnr.backend
    findings = [f.to_dict() for f in check_patch(patch, be, best_config)]
    reasons = []
    allowed = set(project.pnr.allowed_overrides)
    for k, v in patch.items():
        if isinstance(v, (list, dict)):
            reasons.append(f"{k}: only scalar values are proposed")
        if k not in allowed and not any(f["key"] == k for f in findings):
            reasons.append(f"{k} is not in [pnr] allowed_overrides (a person allows each knob per project)")
    if any(patch == t for t in tried):
        reasons.append("this patch was tried already in this tuning run")
    mem = {"tool": "memory", "retrieved": shown, "known": known}
    text = " ".join(str(proposal.get(k) or "") for k in ("reasoning", "hypothesis"))
    reasons += check_memory(text, [mem, {"tool": "flow_history"}])
    tl = proposal.get("tests_lesson")
    if tl:
        status = (known.get("lessons") or {}).get(str(tl))
        if status is None:
            reasons.append(f"tests_lesson {tl} is not in the lessons store")
        elif status in ("refuted", "void"):
            reasons.append(f"tests_lesson {tl} is {status}")
        elif tl not in shown:
            reasons.append(f"tests_lesson {tl} was not given to you in this run")
        else:
            entry = LessonStore.for_project(project).current().get(str(tl)) or {}
            about = {k.strip() for k in str((entry.get("scope") or {}).get("problem") or "").split(",") if k.strip()}
            if not about & set(patch):
                reasons.append(f"tests_lesson {tl} is about {sorted(about) or 'no knob'}, not the knobs of this patch "
                               f"{sorted(patch)}: name a lesson about these knobs, or give a hypothesis instead")
    elif not (proposal.get("hypothesis") or "").strip():
        reasons.append("the proposal names neither a lesson it tests nor a hypothesis")
    return findings, reasons


def outcome_text(knob_patch: dict, best_run: str, new_run: str, verdict: str, detail: str, n: dict) -> str:
    """The measured outcome of one tuning run, written by code (it becomes the refuting or verifying record)."""
    p = ", ".join(f"{k}={cli_value(v)}" for k, v in knob_patch.items())
    return (f"Flow run {new_run} ({p}) against {best_run}: {verdict.replace('_', ' ')}; {detail}; setup violations "
            f"(slow) {n.get('setup_violations_slow')}, TNS (slow) {n.get('setup_tns_slow')}, die "
            f"{n.get('die_area_um2')} um2, DRC {n.get('drc')}, LVS errors {n.get('lvs_errors')}, antenna nets "
            f"{n.get('antenna_nets')}. {SINGLE_RUN}.")


def now_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def file_sha(p: Path | None) -> str | None:
    return sha256_file(p) if p and Path(p).is_file() else None


# ---------------------------------------------------------------- the tuning loop

@dataclass
class TuneSetup:
    goal: str
    budget_runs: int = 2
    metric: str = "setup_wns_slow"
    run_id: str = ""
    proposals: list[dict] = field(default_factory=list)   # given proposals (from a file): no model call
    tag_prefix: str = "tune"
    dry_run: bool = False      # check proposals, run nothing


class FlowOps(Protocol):
    """What the loop needs from the flow tools. The agent layer never imports tools (README "Layout"):
    the command line passes these in (cli/tune.py), a test passes fakes."""

    def start(self) -> tuple[str, Path, dict] | None: ...      # (run id, run dir, its config patch): best so far
    def report(self, run_dir: str) -> dict: ...                 # flow_report of a run, no baseline
    def run(self, tag: str, baseline: str, overrides: dict) -> dict: ...   # flow_run on a copy
    def history(self) -> dict: ...                              # flow_history metrics
    def config(self, run_dir: str) -> dict: ...                 # the run's resolved flow config


def _scope(project: Project, patch: dict) -> dict:
    return {"tool": project.pnr.backend, "pdk": project.flow.pdk if project.flow else "", "corner": "slow",
            "problem": ",".join(patch)}


async def tune(project: Project, setup: TuneSetup, ops: FlowOps, *, model=None, system: str = "",
               usage: dict | None = None, by: str = "agent", log=print) -> dict:
    """The loop of step 3. Returns the receipt body (without approval); runs at most setup.budget_runs flows."""
    usage = usage if usage is not None else {}
    run_id = setup.run_id or f"tune_{now_id()}"
    backend = project.pnr.backend
    store = LessonStore.for_project(project)
    start = ops.start()
    if not start:
        return {"error": f"no finished run in {project.name}'s eda_agent table to start from (run flow-run first)"}
    best_name, best_dir, best_patch = start
    rep = ops.report(str(best_dir))
    best = {"run": best_name, "run_dir": str(best_dir), "numbers": goal_numbers(rep["metrics"]),
            "summary": rep["summary"]}
    start_best = dict(best)
    best_config = ops.config(str(best_dir))
    violators = (rep["metrics"].get("violators") or {}).get(rep["metrics"].get("worst_corner") or "", [])
    history = ops.history()
    facts_block = facts_text(experience_facts(history), history)
    attempts: list[Attempt] = []
    results: list[dict] = [rep]   # tool results the report and the gate read
    runs_used = n = 0
    given = list(setup.proposals)
    from_file = bool(given)
    rounds = len(given) if from_file else setup.budget_runs
    for rnd in range(1, rounds + 1):
        if runs_used >= setup.budget_runs:
            break
        rejection = None
        ran = False
        for _try in range(1 if from_file else TRIES_PER_ROUND):
            n += 1
            stores = lesson_stores(experience_base(project), project.name)
            known = known_ids(stores, [])
            hits = search(f"{setup.goal} setup slack timing resizer repair placement density routing synthesis "
                          "strategy buffering", design=project.name, stores=stores, lessons=12, doc_hints=0)
            shown = [h.id for h in hits]
            if from_file:
                proposal, source = given.pop(0), "file"
                # a person's proposal may cite any lesson that exists
                shown += [m.group(0) for m in LESSON_ID.finditer(json.dumps(proposal))]
            else:
                task = propose_context(project, goal=setup.goal, best=best, best_patch=best_patch,
                                       best_config=best_config, violators=violators, hints=hits,
                                       facts_block=facts_block, attempts=attempts, rejection=rejection)
                reply = await ask(model, system, task, usage)
                proposal, source = parse_json_block(reply) or {"_unparsed": reply[-800:]}, "model"
            a = Attempt(n=n, round=rnd, source=source, proposal=proposal)
            tried = [x.proposal.get("patch") for x in attempts if x.decision == "ran"]
            a.policy, a.reasons = check_proposal(project, proposal, best_config=best_config, shown=shown,
                                                 known=known, tried=tried)
            attempts.append(a)
            if a.policy or a.reasons:
                forbidden = any(f["level"] == "fatal" for f in a.policy)
                log(f"attempt {n}: {'FATAL (forbidden)' if forbidden else 'rejected'}, not run: "
                    + "; ".join([f"{f['key']}: {f['reason']}" for f in a.policy] + a.reasons))
                rejection = [f"[{f['level']}] {f['key']}={f['value']!r}: {f['reason']}" for f in a.policy] + a.reasons
                if forbidden:
                    rejection.append("a forbidden item is Fatal: never propose the SDC, the clock, the corners or "
                                     "anything that turns off or weakens a check")
                continue
            if setup.dry_run:
                a.decision = "checked (dry run)"
                ran = True
                break
            # the lesson this run tests: an existing one, or the proposal's hypothesis stored now (before the run)
            patch = dict(proposal["patch"])
            tag = f"{setup.tag_prefix}_{run_id.split('_', 1)[-1]}_{rnd}"
            lesson_id = proposal.get("tests_lesson")
            if not lesson_id:
                e = store.add(f"Hypothesis before flow run {tag}: {proposal['hypothesis']}", status="hypothesis",
                              scope=_scope(project, patch),
                              sources=[{"run_id": best_name, "note": f"baseline of the test: {json.dumps(best['numbers'])}"}],
                              by=by, origin=f"tune proposal ({PROPOSE_PROMPT}), to be tested by the next run",
                              reason=str(proposal.get("reasoning") or "")[:600])
                lesson_id = e["id"]
            a.hypothesis_lesson = lesson_id
            full_patch = {**best_patch, **patch}
            log(f"attempt {n}: running {tag} with {full_patch} (tests {lesson_id}) ...")
            res = ops.run(tag, str(best_dir), {k: cli_value(v) for k, v in full_patch.items()})
            runs_used += 1
            results.append(res)
            m = res.get("metrics") or {}
            nums = goal_numbers(m)
            a.decision = "ran"
            a.run = {"tag": tag, "run_dir": m.get("run"), "status": res["status"], "summary": res.get("summary"),
                     "evidence_level": res["evidence_level"], "numbers": nums, "patch": full_patch,
                     "returncode": m.get("returncode"), "sdc_sha256": m.get("sdc_sha256"),
                     "experience_table": m.get("experience_table")}
            cmp = (m.get("comparison") or {}).get("baseline") or {}
            a.comparison = {"baseline": cmp.get("baseline"), "better": cmp.get("better") or [],
                            "worse": cmp.get("worse") or [], "not_comparable": cmp.get("not_comparable") or [],
                            "config_differences": [d["key"] for d in cmp.get("config_differences") or []],
                            "same_design": (cmp.get("design") or {}).get("same_design")}
            a.verdict, a.verdict_detail = judge(best["numbers"], nums, setup.metric)
            log(f"attempt {n}: {tag}: {a.verdict}: {a.verdict_detail}")
            sidecar = Path(m["run"]) / "eda_agent_flow.json" if m.get("run") else None
            src = [{"run_id": tag, "note": f"tune {run_id}, eda_agent table, compared with {best_name}",
                    **({"file": str(sidecar), "sha256": file_sha(sidecar)} if file_sha(sidecar) else {})}]
            text = outcome_text(patch, best_name, tag, a.verdict, a.verdict_detail, nums)
            if a.verdict == "improved":
                v = store.add("", kind="verify", refers_to=lesson_id, sources=src, by=by, origin=f"tune {run_id}",
                              reason=f"{a.verdict_detail}; {SINGLE_RUN}; awaiting approval of receipt {run_id}")
                o = store.add(text, status="hypothesis", scope=_scope(project, patch), sources=src, by=by,
                              origin=f"tune {run_id}: measured outcome")
                a.lesson_update = {"kind": "verify", "lesson": lesson_id, "entry": v["id"], "outcome_entry": o["id"]}
                best = {"run": tag, "run_dir": m.get("run"), "numbers": nums, "summary": res.get("summary")}
                best_name, best_dir, best_patch = tag, Path(m["run"]), full_patch
                best_config = ops.config(str(best_dir))
                violators = (m.get("violators") or {}).get(m.get("worst_corner") or "", [])
            elif a.verdict in ("not_improved", "signoff_worse"):
                r = store.add(text, kind="refute", refers_to=lesson_id, status="hypothesis",
                              scope=_scope(project, patch), sources=src, by=by, origin=f"tune {run_id}",
                              reason=f"{SINGLE_RUN}; awaiting approval of receipt {run_id}")
                a.lesson_update = {"kind": "refute", "lesson": lesson_id, "entry": r["id"]}
            else:
                a.lesson_update = {"kind": "none", "lesson": lesson_id, "why": "no post-route result"}
            ran = True
            break
        if not ran and not from_file:
            log(f"round {rnd}: no proposal passed the checks; stopping")
            break
    return {"run_id": run_id, "goal": setup.goal, "metric": setup.metric, "budget_runs": setup.budget_runs,
            "runs_used": runs_used, "start_best": start_best, "best": best, "best_patch": best_patch,
            "attempts": [a.to_dict() for a in attempts], "results": results}
