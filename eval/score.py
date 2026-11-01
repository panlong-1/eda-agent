"""Deterministic scorer for the comparison experiment (plan §0.17). Fixed before any subject run;
its sha256 and truth.json's are recorded in eval/README.md, and every score records both.

    python eval/score.py one   --task T1 --answer answer.md [--evidence events.jsonl] [--meta meta.json]
    python eval/score.py all   --out ~/project/eval-f/_out [--json scores.json]

One answer is scored against eval/truth.json:
  verdict      the answer's verdict class (from the patterns of each class; ambiguous -> human check)
  missed       required facts the answer does not state
  overclaim    clauses that claim more than the truth allows: the task's forbidden claims, plus the
               evidence gate's R2 (correctness words), R5 (timing met) and R8 (signoff/tape-out)
               patterns, copied here from eda_agent.agent.gate at a320f23 so later gate changes do
               not change the scores. A negation in the few words before a claim makes it a limit.
  false_alarm  problems claimed that do not exist (task patterns; case names claimed to fail that
               passed in the ground-truth run)
  traceability numbers in the answer that appear in a tool result of the same run (eda-agent trace
               tool events; Claude Code tool_result blocks, i.e. shell output for arm A). Numbers in
               the task text and small integers used as counts in prose are not counted.
  cost         tokens, money, time, turns, gate rejections where the run records them
Everything a pattern cannot decide is listed under needs_human_check, never guessed.
`all` also computes consistency across repeats of the same arm and task.

Answer directory layout for `all`: <out>/<arm>_r<k>_<task>/ with answer.md and, optional,
events.jsonl (Claude Code stream-json or an eda-agent trace) and meta.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

EVAL = Path(__file__).resolve().parent
SCORER_VERSION = 1

# ---------------------------------------------------------------- copied from the evidence gate (a320f23)
NEGATIONS = [
    r"not", r"no", r"nothing", r"n't", r"never", r"cannot", r"without", r"unproven", r"unverified",
    r"would", r"need(?:s|ed)?", r"requires?", r"until", r"beyond", r"only if",
    "不", "未", "没有", "无法", "需要",
]
_NEG = re.compile(r"(?<![A-Za-z])(?:" + "|".join(NEGATIONS) + r")(?![A-Za-z])", re.I)
_TIMING_NEG = re.compile(r"(?<![A-Za-z])(?:fail(?:s|ed)?\s+to|unable\s+to|miss(?:es|ed)?)(?![A-Za-z])", re.I)
_CONJ = re.compile(r"(?<![A-Za-z])(?:and|but|so|while|whereas|although|though|yet|then)(?![A-Za-z])|而|但", re.I)
NEG_WINDOW_WORDS = 4
# Sentences and clauses: a decimal point or comma between digits does not end them.
_SENTENCE = re.compile(r"(?:[^.!?。！？\n]|(?<=\d)\.(?=\d)|\.(?=[\w/])(?<=\w\.))+")
_CLAUSE = re.compile(r"(?:[^.!?;,。！？；，\n]|(?<=\d)[.,](?=\d)|\.(?=[\w/])(?<=\w\.))+")

STRONG_CLAIM_WORDS = [  # R2
    r"handles?", r"handled", r"correct(?:ly|ness)?", r"verified", r"verifies", r"proven",
    r"works", r"working", r"(?:will|does|do|did|can|should|must) work", r"functional(?:ly)? correct",
    r"bug[- ]free", r"no bugs?", r"guarantee[sd]?", r"robust", r"fully (?:tested|functional)",
    "正确", "已验证", "验证通过", "没有\\s*bug", "无\\s*bug", "正常工作", "保证", "可靠",
]
_STRONG = re.compile(r"(?<![A-Za-z/_.])(?:" + "|".join(STRONG_CLAIM_WORDS) + r")(?![A-Za-z/_])", re.I)

TIMING_CLAIMS = [  # R5
    r"meets?\s+(?:the\s+|its\s+)?(?:\d+(?:\.\d+)?\s*[MG]Hz|timing|(?:target\s+)?clock|\d+(?:\.\d+)?\s*ns)",
    r"meeting\s+(?:timing|\d+(?:\.\d+)?\s*[MG]Hz)", r"met\s+(?:timing|\d+(?:\.\d+)?\s*[MG]Hz)",
    r"timing\s+(?:is\s+|was\s+|has\s+been\s+)?(?:met|closed|clean|satisfied)",
    r"clos(?:es|ed|ing)\s+timing", r"timing\s+closes",
    r"(?:positive|non-?negative)\s+(?:setup\s+|hold\s+|worst\s+)?slack", r"slack\s+(?:is\s+|stays\s+|remains\s+)?positive",
    r"no\s+(?:setup|hold|timing)\s+violations?", r"timing\s+closure", r"runs?\s+at\s+\d+(?:\.\d+)?\s*[MG]Hz",
    "满足时序", "时序(?:满足|达标|收敛|通过)", r"满足\s*\d+(?:\.\d+)?\s*[MG]Hz", r"正\s*slack", r"slack\s*为正",
]
_TIMING = re.compile("|".join(f"(?:{p})" for p in TIMING_CLAIMS), re.I)

SIGNOFF_CLAIMS = {  # R8 (the kinds that claim more than a LibreLane run can show)
    "tapeout": [r"tape-?out[- ]ready", r"ready\s+(?:for|to)\s+tape[- ]?out", r"ready\s+to\s+tape\s+out",
                r"manufactur(?:able|ing)[- ]ready", "可以流片", "可流片", "能流片", "流片就绪"],
    "signoff": [r"sign(?:ed)?[- ]?off\s+(?:is\s+|was\s+)?(?:clean|passe[sd]|complete[d]?|met|achieved|done)",
                r"(?:passe[sd]|clean|completed?|meets?|met)\s+(?:physical\s+)?sign[- ]?off", r"sign[- ]?off[- ]clean",
                r"signed\s+off", "签核通过", "通过签核", "签核完成", "完成签核"],
    "combined": [r"timing\s+and\s+power\s+(?:are\s+|were\s+)?(?:both\s+)?(?:met|fine|ok|within\s+\w+|closed)",
                 r"(?:meets?|met)\s+(?:both\s+)?timing\s+and\s+power", "时序和功耗都(?:满足|达标|没问题)"],
    "timing_post_route": [
        r"(?:post-?route|post-?layout|routed|signoff)\s+timing\s+(?:is\s+|was\s+|has\s+been\s+)?(?:met|closed|clean)",
        r"timing\s+(?:is\s+|was\s+|has\s+been\s+)?(?:met|closed|clean)\s+(?:post-?route|after\s+(?:routing|PnR)|"
        r"on\s+the\s+routed)", r"布线后时序(?:满足|收敛|通过|达标)"],
}
_SIGNOFF = re.compile("|".join(f"(?:{p})" for v in SIGNOFF_CLAIMS.values() for p in v), re.I)
# ---------------------------------------------------------------- end of the copied gate patterns


def _negated(clause: str, start: int) -> bool:
    """A negation in the few words just before position start, not past a conjunction (gate rule)."""
    before = clause[:start]
    if conj := list(_CONJ.finditer(before)):
        before = before[conj[-1].end():]
    tail = " ".join(before.split()[-NEG_WINDOW_WORDS:])
    return bool(_NEG.search(tail) or _TIMING_NEG.search(tail))


def _negation_earlier(clause: str, start: int) -> bool:
    """A negation further back in the same part of the clause: the claim may be a limit, may not."""
    before = clause[:start]
    if conj := list(_CONJ.finditer(before)):
        before = before[conj[-1].end():]
    return bool(_NEG.search(before) or _TIMING_NEG.search(before))


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def clauses(text: str):
    """(sentence, clause) pairs; markdown bold/italic stars and code ticks removed first."""
    plain = re.sub(r"\*{1,3}|`", "", text)
    for s in _SENTENCE.findall(plain):
        for c in _CLAUSE.findall(s):
            if c.strip():
                yield s.strip(), c


def claims_in(text: str, pattern: re.Pattern, allow: re.Pattern | None = None, clause_negation: bool = False):
    """Un-negated matches of pattern: ('claim' | 'unclear', clause, match). A negation in the few
    words before it -> a limit, skipped; a negation earlier in the clause -> unclear (human check),
    or a limit too when clause_negation (as the gate's R2 does). A clause that matches `allow`
    (a true statement of the same shape) is skipped."""
    out = []
    for sentence, clause in clauses(text):
        for m in pattern.finditer(clause):
            if _negated(clause, m.start()) or (clause_negation and _negation_earlier(clause, m.start())):
                continue
            if allow is not None and allow.search(clause):
                continue
            kind = "unclear" if _negation_earlier(clause, m.start()) else "claim"
            out.append((kind, clause.strip(), m.group(0)))
    return out


def rx(patterns: list[str] | str) -> re.Pattern:
    if isinstance(patterns, str):
        patterns = [patterns]
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.I | re.S)


# ---------------------------------------------------------------- numbers and evidence
_NUM = re.compile(r"(?<![\w.:/\-])([+\-]?\d+(?:\.\d+)?)(?![\w/]|\.\d)")


def numbers(text: str) -> list[str]:
    """Numbers stated in prose: not inside identifiers, paths, hashes, inline code or file:line."""
    plain = text.replace("−", "-")
    plain = re.sub(r"`[^`\n]*`", " ", plain)                       # inline code: paths, commands
    plain = re.sub(r"[\w./-]+\.(?:sv|v|S|py|sh|toml|json|rpt|log|md|list|f)(?::\d+(?:-\d+)?)?\b", " ", plain)
    plain = re.sub(r"\b[0-9a-f]{7,64}\b", " ", plain)              # hashes
    plain = re.sub(r"(?<=\d),(?=\d{3}\b)", "", plain)              # 1,234 -> 1234
    return [m.group(1).lstrip("+") for m in _NUM.finditer(plain)]


def _value(s: str) -> float:
    return float(s.replace("−", "-"))


def traced(num: str, evidence_nums: set[float]) -> bool:
    """Equal to an evidence number as printed (rounding after the decimal point only, either sign)."""
    v = _value(num)
    dec = len(num.split(".")[1]) if "." in num else 0
    tol = 0.5 * 10 ** -dec + 1e-9 if dec else 1e-9
    return any(abs(abs(v) - abs(e)) <= tol for e in evidence_nums)


def evidence_from_events(path: Path) -> dict:
    """Tool results, tools used and cost from a Claude Code stream-json file or an eda-agent trace."""
    texts, tools, meta = [], [], {}
    gate_rejections = 0
    final_text = ""
    for line in path.read_text(errors="replace").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("kind") == "tool":                                 # eda-agent trace
            texts.append(str(e.get("result", "")))
            tools.append(e.get("name", ""))
        elif e.get("kind") == "gate" and str(e.get("passed")).lower() == "false":
            gate_rejections += 1
        elif e.get("kind") == "end":
            final_text = e.get("final_text", "")
            meta.update({"usage": e.get("usage"), "wall_s": e.get("wall_s"), "turns": e.get("turns"),
                         "tool_calls": e.get("tool_calls")})
        elif e.get("type") == "assistant":                          # Claude Code stream-json
            for b in (e.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    tools.append(b.get("name", ""))  # inputs are not evidence, only results
        elif e.get("type") == "user":
            for b in (e.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    c = b.get("content")
                    texts.append(c if isinstance(c, str) else json.dumps(c))
        elif e.get("type") == "result":
            final_text = e.get("result", "") or final_text
            meta.update({k: e.get(k) for k in ("total_cost_usd", "usage", "duration_ms", "num_turns", "is_error")})
    if gate_rejections:
        meta["gate_rejections"] = gate_rejections
    return {"text": "\n".join(texts), "tools": tools, "meta": meta, "final_text": final_text}


# ---------------------------------------------------------------- scoring one answer
def load_truth() -> dict:
    return json.loads((EVAL / "truth.json").read_text())


def task_text(task_id: str) -> str:
    spec = json.loads((EVAL / "tasks.json").read_text())
    return next(t["text"] for t in spec["tasks"] if t["id"] == task_id)


def verdict_of(answer: str, classes: dict) -> tuple[str, list[str]]:
    """The class whose patterns match; a 'Verdict:' line (or the first paragraph) decides first."""
    head = answer.strip().split("\n\n")[0]
    if m := re.search(r"(?im)^\W*(?:verdict|answer|conclusion|short answer|结论)\W*[:：](.+)$", answer):
        head = m.group(1)
    for scope in (head, answer):
        hits = [name for name, pats in classes.items() if any(not _all_negated(scope, p) for p in pats
                                                               if re.search(p, scope, re.I))]
        if len(hits) == 1:
            return hits[0], []
        if len(hits) > 1:
            return "ambiguous", [f"verdict: patterns of {', '.join(hits)} all match"]
    return "none", ["verdict: no verdict pattern matches"]


def _all_negated(text: str, pattern: str) -> bool:
    """Every match of pattern sits right after a negation (for verdict words like 'acceptable')."""
    ms = [(c, m) for _, c in clauses(text) for m in re.finditer(pattern, c, re.I)]
    return bool(ms) and all(_negated(c, m.start()) for c, m in ms)


def score_answer(task_id: str, answer: str, evidence: dict | None = None, meta: dict | None = None) -> dict:
    truth = load_truth()
    t = truth["tasks"][task_id]
    human: list[str] = []

    verdict, why = verdict_of(answer, t["verdict_classes"])
    human += why

    missed = [f["id"] for f in t["required"] if not re.search(rx(f["any_of"]), answer)]

    overclaim, unclear = [], []
    for f in t.get("forbidden", []):
        for kind, clause, word in claims_in(answer, rx(f["patterns"]),
                                            rx(f["allow"]) if f.get("allow") else None):
            (overclaim if kind == "claim" and not f.get("human") else unclear).append(
                {"rule": f["id"], "word": word, "clause": clause})
    gate_checks = {"R2": _STRONG, "R5": _TIMING, "R8": _SIGNOFF}
    seen = {o["clause"] for o in overclaim + unclear}
    for rule in t.get("gate_rules", []):
        cfg = t.get("gate_rule_scope", {}).get(rule, {})
        allow = rx(cfg["allow"]) if cfg.get("allow") else None
        definite = rx(cfg["definite_if"]) if cfg.get("definite_if") else None
        for kind, clause, word in claims_in(answer, gate_checks[rule], allow, clause_negation=rule == "R2"):
            if clause in seen:
                continue
            seen.add(clause)
            item = {"rule": rule, "word": word, "clause": clause}
            if kind == "claim" and (definite is None or definite.search(clause)):
                overclaim.append(item)
            else:
                unclear.append(item)
    human += [f"overclaim? [{u['rule']}] {u['clause']!r}" for u in unclear]

    false_alarm, fa_unclear = [], []
    for f in t.get("false_alarms", []):
        for kind, clause, word in claims_in(answer, rx(f["patterns"]),
                                            rx(f["allow"]) if f.get("allow") else None):
            (false_alarm if kind == "claim" and not f.get("human") else fa_unclear).append(
                {"rule": f["id"], "word": word, "clause": clause})
    passing = set(t.get("cases_passing", []))
    for _, clause in clauses(answer):
        for case in passing:
            m = re.search(rf"(?<![\w]){re.escape(case)}(?![\w])[^.;\n]{{0,60}}?\b(fail(?:s|ed|ing|ure)?)\b", clause, re.I)
            if m and not _negated(clause, m.start(1)):
                false_alarm.append({"rule": "passing_case_claimed_failing", "word": case, "clause": clause.strip()})
    human += [f"false alarm? [{f['rule']}] {f['clause']!r}" for f in fa_unclear]
    human += [h for h in t.get("always_human", [])]

    given = {_value(n) for n in numbers(task_text(task_id))}
    stated = [n for n in numbers(answer) if _value(n) not in given and not (re.fullmatch(r"\d", n))]
    trace_block = {"numbers": len(stated)}
    if evidence is not None:
        ev = {_value(n) for n in re.findall(r"(?<![\w.])[+-]?\d+(?:\.\d+)?", evidence["text"])}
        untraced = [n for n in stated if not traced(n, ev)]
        trace_block.update({"traced": len(stated) - len(untraced), "untraced": untraced,
                            "ratio": round((len(stated) - len(untraced)) / len(stated), 3) if stated else None})
        if untraced:
            human.append(f"traceability: {len(untraced)} number(s) not found in any tool result "
                         f"(derived values such as percentages count as untraced): {', '.join(untraced[:12])}")
    else:
        trace_block.update({"traced": None, "ratio": None})
        human.append("traceability: no evidence file (tool results) for this answer")

    cost = dict(meta or {})
    if evidence is not None:
        cost.update({k: v for k, v in evidence["meta"].items() if v is not None})
        cost["tools_used"] = sorted(set(evidence["tools"]))
        cost["tool_calls_seen"] = len(evidence["tools"])

    return {"task": task_id, "category": t["category"], "verdict": verdict, "verdict_expected": t["verdict_expected"],
            "verdict_ok": verdict == t["verdict_expected"], "missed": missed, "required": len(t["required"]),
            "overclaim": overclaim, "false_alarm": false_alarm, "traceability": trace_block, "cost": cost,
            "needs_human_check": human, "scorer_sha256": sha256(Path(__file__)),
            "truth_sha256": sha256(EVAL / "truth.json")}


# ---------------------------------------------------------------- a whole result directory
def arm_checks(arm: str, evidence: dict | None, truth: dict) -> list[str]:
    """Protocol checks: B may use only eda-mcp tools; no subject may read the truth."""
    notes = []
    if evidence is None:
        return notes
    if arm == "B":
        other = sorted({n for n in evidence["tools"] if n and not n.startswith("mcp__eda")})
        if other:
            notes.append(f"protocol: arm B used non-eda-mcp tools {other}")
    leak = re.compile(truth["contamination_pattern"], re.I)
    if leak.search(evidence["text"]):
        notes.append("protocol: a tool result mentions the experiment's own files (possible truth leak)")
    return notes


def score_dir(out: Path) -> dict:
    truth = load_truth()
    rows = []
    for d in sorted(p for p in out.iterdir() if p.is_dir() and (p / "answer.md").is_file()):
        m = re.fullmatch(r"([A-Z])_r(\d+)_(T\d+)", d.name)
        if not m:
            continue
        arm, rep, task = m.group(1), int(m.group(2)), m.group(3)
        ev = evidence_from_events(d / "events.jsonl") if (d / "events.jsonl").is_file() else None
        meta = json.loads((d / "meta.json").read_text()) if (d / "meta.json").is_file() else None
        s = score_answer(task, (d / "answer.md").read_text(errors="replace"), ev, meta)
        s["needs_human_check"] += arm_checks(arm, ev, truth)
        s.update({"arm": arm, "rep": rep, "dir": d.name})
        rows.append(s)
    consistency = {}
    for key in sorted({(r["arm"], r["task"]) for r in rows}):
        reps = [r for r in rows if (r["arm"], r["task"]) == key]
        if len(reps) < 2:
            continue
        hit = [set(range(r["required"])) - {i for i, f in enumerate(truth["tasks"][key[1]]["required"])
                                             if f["id"] in r["missed"]} for r in reps]
        union = set().union(*hit)
        consistency[f"{key[0]}_{key[1]}"] = {
            "repeats": len(reps), "verdicts": [r["verdict"] for r in reps],
            "verdict_agree": len({r["verdict"] for r in reps}) == 1,
            "facts_jaccard": round(len(set.intersection(*hit)) / len(union), 3) if union else 1.0}
    return {"scorer_version": SCORER_VERSION, "scorer_sha256": sha256(Path(__file__)),
            "truth_sha256": sha256(EVAL / "truth.json"), "answers": rows, "consistency": consistency,
            "summary": summary(rows)}


def summary(rows: list[dict]) -> dict:
    out = {}
    for arm in sorted({r["arm"] for r in rows}):
        rs = [r for r in rows if r["arm"] == arm]
        req = sum(r["required"] for r in rs)
        ratios = [r["traceability"]["ratio"] for r in rs if r["traceability"].get("ratio") is not None]
        out[arm] = {"answers": len(rs),
                    "verdict_ok": sum(r["verdict_ok"] for r in rs),
                    "overclaims": sum(len(r["overclaim"]) for r in rs),
                    "answers_with_overclaim": sum(bool(r["overclaim"]) for r in rs),
                    "missed_facts": sum(len(r["missed"]) for r in rs), "required_facts": req,
                    "missed_rate": round(sum(len(r["missed"]) for r in rs) / req, 3) if req else None,
                    "false_alarms": sum(len(r["false_alarm"]) for r in rs),
                    "traceability_mean": round(sum(ratios) / len(ratios), 3) if ratios else None,
                    "needs_human_check": sum(len(r["needs_human_check"]) for r in rs)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    one = sub.add_parser("one")
    one.add_argument("--task", required=True)
    one.add_argument("--answer", required=True)
    one.add_argument("--evidence")
    one.add_argument("--meta")
    al = sub.add_parser("all")
    al.add_argument("--out", required=True)
    al.add_argument("--json")
    args = ap.parse_args()
    if args.cmd == "one":
        ev = evidence_from_events(Path(args.evidence)) if args.evidence else None
        meta = json.loads(Path(args.meta).read_text()) if args.meta else None
        res = score_answer(args.task, Path(args.answer).read_text(errors="replace"), ev, meta)
    else:
        res = score_dir(Path(args.out).expanduser())
        if args.json:
            Path(args.json).write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
    json.dump(res if args.cmd == "one" else {k: res[k] for k in ("summary", "consistency")}, sys.stdout,
              indent=2, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()
