"""Split a Claude Code subject session (arm A or B) into one answer directory per task.

    python eval/arms/collect.py --label A_r1 [--dest ~/project/eval-f]

Reads <dest>/<label>/events.jsonl: a desktop session's transcript (the session's .jsonl under
~/.claude/projects/, copied there) or `claude -p --output-format stream-json --verbose` output; or,
when neither exists, <dest>/<label>/final.md (the last message pasted as is, no evidence). Writes <dest>/_out/<label>_<task>/
answer.md (the text after that task's `=== ANSWER <task> ===` marker), events.jsonl (the whole
session: its tool results are the evidence for every task of the session) and meta.json. Cost and
time are for the whole session (meta.session_tasks says how many tasks shared it).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path


def final_text(events: Path) -> tuple[str, dict]:
    """The session's final message and cost: from the `result` event of claude -p stream-json, or,
    for a desktop session's transcript (same message shape, no result event), the last assistant text
    and the summed usage of its assistant messages."""
    text, meta, last, usage, turns, stamps = "", {}, "", {}, 0, []
    for line in events.read_text(errors="replace").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("timestamp"):
            stamps.append(e["timestamp"])
        if e.get("type") == "result":
            text = e.get("result") or ""
            meta = {k: e.get(k) for k in ("total_cost_usd", "usage", "duration_ms", "num_turns", "is_error",
                                          "session_id")}
        elif e.get("type") == "assistant":
            msg = e.get("message") or {}
            turns += 1
            for k, v in (msg.get("usage") or {}).items():
                if isinstance(v, int):
                    usage[k] = usage.get(k, 0) + v
            t = "".join(b.get("text", "") for b in msg.get("content") or [] if isinstance(b, dict)
                        and b.get("type") == "text")
            if t.strip():
                last = t
    if not text:
        text = last
        meta = {"usage": usage, "assistant_messages": turns, "source": "session transcript",
                "first_timestamp": stamps[0] if stamps else None, "last_timestamp": stamps[-1] if stamps else None}
    return text, meta


def split(text: str, tasks: list[str]) -> dict[str, str]:
    marks = list(re.finditer(r"(?m)^\W*=== ANSWER (T\d+) ===\W*$", text))
    out = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out[m.group(1)] = text[m.end():end].strip() + "\n"
    return {t: out[t] for t in tasks if t in out}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", required=True)
    ap.add_argument("--dest", default="~/project/eval-f")
    args = ap.parse_args()
    ldir = Path(args.dest).expanduser() / args.label
    session = json.loads((ldir / "session.json").read_text())
    events = ldir / "events.jsonl"
    if events.is_file():
        text, meta = final_text(events)
    elif (ldir / "final.md").is_file():
        text, meta = (ldir / "final.md").read_text(), {"source": "desktop session, final message pasted"}
    else:
        sys.exit(f"neither {events} nor {ldir / 'final.md'}")
    answers = split(text, session["tasks"])
    missing = [t for t in session["tasks"] if t not in answers]
    for t in session["tasks"]:
        d = Path(args.dest).expanduser() / "_out" / f"{args.label}_{t}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "answer.md").write_text(answers.get(t, "(no answer for this task in the session's final message)\n"))
        if events.is_file():
            shutil.copy(events, d / "events.jsonl")
        (d / "meta.json").write_text(json.dumps({"arm": session["arm"], "rep": session["rep"], "session": args.label,
                                                 "session_tasks": len(session["tasks"]), **meta}, indent=2) + "\n")
    print(json.dumps({"label": args.label, "answers": sorted(answers), "missing": missing}))


if __name__ == "__main__":
    main()
