"""Prepare one Claude Code subject session (arm A or B) of the comparison experiment.

    python eval/arms/prepare.py --arm A --rep 1 [--tasks T2,T4,T3,T1] [--dest ~/project/eval-f]

For each task: a fresh copy (eval/setup_task.py, label <arm>_r<rep>). Then <dest>/<label>/prompt.md
(the session prompt: the task texts from eval/tasks.json word for word, the copy paths, the answer
format) and, for arm B, <dest>/<label>/mcp.json (one eda-mcp server per task copy, nothing else).
One session does all tasks so that 2 arms x 2 repeats stay within the 8-session budget; T1 (the
mutant) comes last so that what a subject learns there cannot colour T2.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ARMS = Path(__file__).resolve().parent
EVAL = ARMS.parent
DISTRO = "Ubuntu-24.04"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arm", choices=["A", "B"], required=True)
    ap.add_argument("--rep", type=int, required=True)
    ap.add_argument("--tasks", default="T2,T4,T3,T1")
    ap.add_argument("--dest", default="~/project/eval-f")
    ap.add_argument("--win-dir", required=True,
                    help="where the subject session's (empty) Windows working directory is made, e.g. /mnt/c/Users/<you>/eval-f-subjects")
    args = ap.parse_args()

    spec = {t["id"]: t for t in json.loads((EVAL / "tasks.json").read_text())["tasks"]}
    label = f"{args.arm}_r{args.rep}"
    dest = Path(args.dest).expanduser()
    tasks = args.tasks.split(",")
    made = {}
    for t in tasks:
        out = subprocess.run([sys.executable, str(EVAL / "setup_task.py"), "--task", t, "--label", label,
                              "--dest", str(dest)], check=True, capture_output=True, text=True).stdout
        made[t] = json.loads(out.strip().splitlines()[-1])

    template = (ARMS / f"{args.arm}_prompt.md").read_text()
    blocks = []
    for t in tasks:
        where = (f"Design copy: `{made[t]['copy']}`" if args.arm == "A"
                 else f"Tools: the MCP server `eda_{t}` (its tools are named `mcp__eda_{t}__*`); it works on this "
                      f"task's own design copy.")
        blocks.append(f"### Task {t}\n\n{where}\n\n> {spec[t]['text']}\n")
    markers = ", ".join(f"`=== ANSWER {t} ===`" for t in tasks)
    prompt = template.replace("{TASKS}", "\n".join(blocks)).replace("{MARKERS}", markers)
    ldir = dest / label
    (ldir / "prompt.md").write_text(prompt)

    # The subject session's working directory on Windows: empty (no project CLAUDE.md), with the
    # prompt, and for B a project .mcp.json holding only the eda-mcp servers of this session.
    win = Path(args.win_dir) / label
    win.mkdir(parents=True, exist_ok=True)
    (win / "prompt.md").write_text(prompt)
    if args.arm == "B":
        exe = str(EVAL.parent / ".venv" / "bin" / "eda-mcp")
        servers = {f"eda_{t}": {"command": "wsl", "args": ["-d", DISTRO, "--", exe, "--project", made[t]["project"]]}
                   for t in tasks}
        cfg = json.dumps({"mcpServers": servers}, indent=2) + "\n"
        (ldir / "mcp.json").write_text(cfg)
        (win / ".mcp.json").write_text(cfg)
    (ldir / "session.json").write_text(json.dumps({"arm": args.arm, "rep": args.rep, "tasks": tasks,
                                                   "copies": made}, indent=2) + "\n")
    print(json.dumps({"label": label, "dir": str(ldir), "session_cwd": str(win), "tasks": tasks}))


if __name__ == "__main__":
    main()
