"""Prepare one task's copy of TORV for the comparison experiment (plan §0.17).

    python eval/setup_task.py --task T1 --label A_r1 [--dest ~/project/eval-f]

Makes <dest>/<label>/<task>/torv (a copy of the checkout without .git, builds and old runs),
applies the task's mutant or environment fault from eval/tasks.json, writes
<dest>/<label>/<task>/torv.toml (projects/torv.toml with root pointing at the copy) and a
manifest <dest>/_manifests/<label>_<task>.json with the hashes. The checkout itself is only
read. The manifest lives outside the task directory so a subject does not see what was changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

EVAL = Path(__file__).resolve().parent
REPO = EVAL.parent


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def rsync(src: Path, dst: Path, exclude: list[str]) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    cmd = ["rsync", "-a", "--delete", *[f"--exclude={e}" for e in exclude], f"{src}/", f"{dst}/"]
    subprocess.run(cmd, check=True)


def git_head(path: Path) -> str:
    r = subprocess.run(["git", "-C", str(path), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    return r.stdout.strip()


def git_clean(path: Path) -> bool:
    r = subprocess.run(["git", "-C", str(path), "status", "--porcelain"], capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() == ""


def apply_mutation(copy: Path, m: dict) -> dict:
    f = copy / m["file"]
    lines = f.read_text().split("\n")
    i = m["line"] - 1
    if lines[i] != m["before"]:
        sys.exit(f"{m['file']}:{m['line']} is {lines[i]!r}, expected {m['before']!r}")
    before = sha256(f)
    lines[i] = m["after"]
    f.write_text("\n".join(lines))
    return {"file": m["file"], "line": m["line"], "sha256_before": before, "sha256_after": sha256(f)}


def project_toml(copy: Path, setup: dict) -> str:
    text = (REPO / "projects" / "torv.toml").read_text()
    text, n = re.subn(r'(?m)^root = ".*"$', f'root = "{copy}"', text)
    assert n == 1, "projects/torv.toml has no root line"
    if rr := setup.get("routed_run"):
        text, n = re.subn(r'(?m)^baseline = ".*"$', f'baseline = "{rr["pnr_baseline"]}"', text)
        assert n == 1
    if ef := setup.get("env_fault"):
        text, n = re.subn(r'(?m)^PDK_ROOT = ".*"$', f'PDK_ROOT = "{ef["pdk_root"]}"', text)
        assert n == 1
    return text


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--task", required=True)
    ap.add_argument("--label", required=True, help="arm and repeat, e.g. A_r1, B_r2, D_r1")
    ap.add_argument("--dest", default="~/project/eval-f")
    args = ap.parse_args()

    spec = json.loads((EVAL / "tasks.json").read_text())
    task = next((t for t in spec["tasks"] if t["id"] == args.task), None)
    if task is None:
        sys.exit(f"no task {args.task} in eval/tasks.json")
    src = Path(spec["source"]["checkout"]).expanduser()
    head = git_head(src)
    if not spec["source"]["commit"].startswith(head) or not git_clean(src):
        sys.exit(f"{src} is at {head} (clean={git_clean(src)}); tasks.json expects {spec['source']['commit']}, clean")

    base = Path(args.dest).expanduser() / args.label / args.task
    copy = base / "torv"
    if base.exists():
        shutil.rmtree(base)
    rsync(src, copy, spec["source"]["copy_exclude"])

    setup = task["setup"]
    manifest = {"task": args.task, "label": args.label, "created": datetime.now().isoformat(timespec="seconds"),
                "source": str(src), "source_commit": head, "copy": str(copy), "changes": []}
    if m := setup.get("mutation"):
        manifest["changes"].append({"mutation": apply_mutation(copy, m)})
    if rr := setup.get("routed_run"):
        run_src = Path(spec["source"]["routed_run"]).expanduser()
        rsync(run_src, copy / rr["into"], rr["exclude"])
        metrics = copy / rr["into"] / "final" / "metrics.json"
        manifest["changes"].append({"routed_run": {"from": str(run_src), "into": rr["into"],
                                                   "final_metrics_sha256": sha256(metrics)}})
    if ef := setup.get("env_fault"):
        f = copy / ef["file"]
        with f.open("a") as fh:
            fh.write(ef["append"])
        manifest["changes"].append({"env_fault": {"file": ef["file"], "pdk_root": ef["pdk_root"],
                                                  "sha256_after": sha256(f)}})
    toml = base / "torv.toml"
    toml.write_text(project_toml(copy, setup))
    manifest["project_toml"] = str(toml)

    mdir = Path(args.dest).expanduser() / "_manifests"
    mdir.mkdir(parents=True, exist_ok=True)
    (mdir / f"{args.label}_{args.task}.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"copy": str(copy), "project": str(toml)}))


if __name__ == "__main__":
    main()
