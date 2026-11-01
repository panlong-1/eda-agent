#!/usr/bin/env bash
# Arm D of the comparison experiment (plan §0.17): eda-agent with DeepSeek, one run per task.
#
#   wsl -d Ubuntu-24.04 -- bash -ic 'bash ~/project/eda-agent-f/eval/arms/run_D.sh 1 T2 T4 T3 T1'
#
# (bash -ic: DEEPSEEK_API_KEY is set in ~/.bashrc; this script only checks that it is set.)
# Per task: a fresh copy (label D_r<rep>), then `eda-agent run` with the task text from eval/tasks.json,
# the I2 reviewer, no lesson learning (repeats stay independent) and the human gate left on: every
# run ends awaiting_approval and nobody approves it on the user's behalf. The trace, receipt and
# answer go to ~/project/eval-f/_out/D_r<rep>_<task>/ (D_r<rep>_<task>.jsonl = the trace, also copied to
# events.jsonl for the scorer; D_r<rep>_<task>.receipt.* = the receipt; answer.md = the
# gated final report, meta.json = status, gate rejections, reviews, usage, wall time).
set -euo pipefail
rep=${1:?usage: run_D.sh <rep> <task>...}
shift
REPO=$(cd "$(dirname "$0")/../.." && pwd)
EVALF=${EVALF:-$HOME/project/eval-f}
PY="$REPO/.venv/bin/python"
if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
  echo "DEEPSEEK_API_KEY is not set in this shell (run through bash -ic)" >&2
  exit 2
fi
cd "$REPO"
for task in "$@"; do
  label="D_r${rep}"
  out="$EVALF/_out/${label}_${task}"
  mkdir -p "$out"
  project=$("$PY" eval/setup_task.py --task "$task" --label "$label" --dest "$EVALF" | tail -1 |
            "$PY" -c 'import json,sys; print(json.load(sys.stdin)["project"])')
  text=$("$PY" -c 'import json,sys; print(next(t["text"] for t in json.load(open("eval/tasks.json"))["tasks"] if t["id"]==sys.argv[1]))' "$task")
  start=$(date +%s)
  set +e
  .venv/bin/eda-agent run --project "$project" --provider deepseek --review I2 --no-learn \
      --trace "$out/${label}_${task}.jsonl" "$text" > "$out/stdout.txt" 2>&1
  rc=$?
  set -e
  "$PY" - "$out" "$rc" "$start" "$rep" "${label}_${task}" <<'PY'
import json, sys, time
from pathlib import Path
out, rc, start, rep, run_id = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
trace = out / f"{run_id}.jsonl"
if trace.is_file():
    (out / "events.jsonl").write_bytes(trace.read_bytes())
final, end = "", {}
for line in (out / "events.jsonl").read_text(errors="replace").splitlines():
    e = json.loads(line)
    if e.get("kind") == "end":
        end = e
        final = e.get("final_text", "")
(out / "answer.md").write_text(final or "(no final report)\n")
rec = out / f"{run_id}.receipt.json"
r = json.loads(rec.read_text()) if rec.is_file() else {}
meta = {"arm": "D", "rep": rep, "exit_code": rc, "wall_s_outer": int(time.time()) - start,
        "status": r.get("status"), "gate": (r.get("gate") or {}).get("status"),
        "usage": r.get("usage"), "wall_s": r.get("wall_s"), "turns": r.get("turns"),
        "tool_calls": r.get("tool_calls"), "model": r.get("model"),
        "reviews": [{k: v.get(k) for k in ("mode", "verdict", "usage", "model") if k in v}
                    for v in r.get("reviews", []) if isinstance(v, dict)],
        "receipt": str(rec) if rec.is_file() else None}
(out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
print(json.dumps({"out": str(out), "status": meta["status"], "gate": meta["gate"], "exit_code": rc}))
PY
done
