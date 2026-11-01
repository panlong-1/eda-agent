"""`eda-agent tune`: experience-based back-end tuning under a run budget (plan §0.15 step 3).

    eda-agent tune --project projects/torv.toml --goal "slow-corner setup" --budget-runs 2 --provider deepseek
    eda-agent tune --proposal-file p.json [--dry-run]     a person's proposal(s), checked the same way, no model

The model proposes, code checks (core/knobs.py, [pnr] allowed_overrides, R11), flow_run runs on a copy,
code compares and judges, the lesson is verified or refuted, and the receipt waits for a person with
the config patch. Nothing is applied to the design.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from ..agent import human_gate
from ..agent.model_output import gate_decision
from ..agent.receipt import write_receipt
from ..agent.receipt.tune import tune_receipt
from ..agent.tuning import PROPOSE_PROMPT, SINGLE_RUN, TuneSetup, cli_value, config_diff, now_id, tune
from ..core.memory import LESSON_ID, experience_base, known_ids, lesson_stores
from ..core.project import load_project
from ..core.settings import PROJECT_FILE
from .common import DEFAULT_MODELS, load_prompt, make_model


def add_parser(sub) -> None:
    p = sub.add_parser("tune", help="propose flow-knob changes from the design's lessons, run them on a copy within "
                                    "a run budget, compare, update the lessons; a person approves the patch")
    p.add_argument("--project", help=f"project config (default: {PROJECT_FILE} here or in a parent)")
    p.add_argument("--goal", default="slow-corner setup slack (post-route)", help="what to improve, in words")
    p.add_argument("--budget-runs", type=int, default=2, help="at most this many new flow runs (default 2)")
    p.add_argument("--provider", default="deepseek", choices=sorted(DEFAULT_MODELS))
    p.add_argument("--model")
    p.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--max-tokens", type=int, default=16000)
    p.add_argument("--proposal-file", help="JSON proposal (or a list of them) written by a person: no model call")
    p.add_argument("--dry-run", action="store_true", help="check the proposals, run no flow")
    p.add_argument("--run-id")
    p.add_argument("--rereport", metavar="RUN_ID",
                   help="a tune receipt still awaiting approval: write its report again with this code and re-run the "
                        "gate on the runs' flow results (read again); the old report and gate decision stay in it")
    p.add_argument("--reason", default="", help="--rereport: why")


class Flow:
    """The flow tools the tuning loop uses (agent/tuning.py FlowOps), called directly: the command line is
    the top layer and may use tools; the loop itself never imports them."""

    def __init__(self, project):
        from ..tools.flow import backend_of
        self.project, self.be = project, backend_of(project)

    def start(self):
        """(run id, run dir, config patch) of the best finished run of the design's eda_agent table by
        slow-corner setup WNS; None if it has no finished run."""
        from ..tools import experience
        best = None
        for rec in experience.eda_records(self.project):
            d = Path(rec["run_dir"])
            if rec.get("returncode") != 0 or not self.be.is_run(d):
                continue
            f = self.be.read_run(d, violators=0)
            slow = next((t.get("setup_wns") for c, t in f["timing"].items() if "slow" in c), None)
            if f["complete"] and isinstance(slow, (int, float)) and (best is None or slow > best[0]):
                best = (slow, rec["run_id"], d, rec.get("config_patch") or {})
        return best[1:] if best else None

    def report(self, run_dir: str) -> dict:
        from ..tools.flow import run_flow_report
        return run_flow_report(self.project, run_dir, baseline="").to_dict()

    def run(self, tag: str, baseline: str, overrides: dict) -> dict:
        from ..tools.flow import run_flow
        return run_flow(self.project, tag=tag, baseline=baseline, overrides=overrides).to_dict()

    def history(self) -> dict:
        from ..tools.flow_history import run_flow_history
        return run_flow_history(self.project).to_dict().get("metrics") or {}

    def config(self, run_dir: str) -> dict:
        return self.be.config(Path(run_dir))


def report_text(body: dict) -> str:
    """The tuning report, written by code from the flow results (the gate checks it like any report)."""
    s = body["start_best"]
    sn = s["numbers"]
    out = [f"Tuning run {body['run_id']}.", f"Goal: {body['goal']}.",
           f"Budget: {body['budget_runs']} flow run(s); {body['runs_used']} used.",
           f"Start: the best run of the eda_agent table, {s['run']} (flow_report, SDC recorded): setup WNS at "
           f"{sn['slow_corner']} {sn['setup_wns_slow']:+.3f} ns, {sn['setup_violations_slow']} violating "
           "endpoint(s).", ""]
    for a in body["attempts"]:
        p = a.get("proposal") or {}
        head = f"- Attempt {a['n']} ({a['source']}): patch {json.dumps(p.get('patch'))}"
        if a["decision"] != "ran":
            why = "; ".join([f"{f['level']} {f['key']}: {f['reason']}" for f in a.get("policy", [])] + a.get("reasons", []))
            out.append(f"{head}: {a['decision']} before running ({why}).")
            continue
        r, n = a["run"], a["run"]["numbers"]
        lu = a.get("lesson_update") or {}
        out.append(f"{head}: flow_run {r['tag']} {r['status']} ({r['evidence_level']}); setup WNS at "
                   f"{n['slow_corner']} {n['setup_wns_slow']:+.3f} ns, against "
                   f"{a['comparison'].get('baseline') or 'the best run'}; verdict (code) {a['verdict']}: "
                   f"{a['verdict_detail']}.")
        if lu.get("kind") in ("verify", "refute"):
            # "supports"/"contradicts": the gate reads "verified" as a claim of functional correctness (R2)
            out.append(f"  Lesson store (source: flow_run {r['tag']}): this run "
                       f"{'supports' if lu['kind'] == 'verify' else 'contradicts'} {lu['lesson']}; new entry "
                       f"{lu['entry']} (kind {lu['kind']}, single run).")
    b = body["best"]
    out += ["", f"Best run now: {b['run']}" + (f" with knobs {json.dumps(body['best_patch'])}" if body["best_patch"]
                                               else " (no change kept)") + "; the patch is not applied to the design.",
            "", "## Residual risk", "", f"- {SINGLE_RUN}.",
            "- Signoff still fails while any corner has negative setup slack; the verdict reads slow-corner setup "
            "only.", "- E5-partial at most: no gate-level simulation of the routed netlist, vectorless power."]
    return "\n".join(out)


def _gate(body: dict, report: str, project) -> dict:
    stores = lesson_stores(experience_base(project), project.name)
    retrieved = sorted({m.group(0) for m in LESSON_ID.finditer(report)})
    known = known_ids(stores, [])
    # a lesson this run refuted itself is reported, not used: judge it by its status before the run
    for a in body["attempts"]:
        lu = a.get("lesson_update") or {}
        if lu.get("kind") == "refute" and lu.get("lesson") in known["lessons"]:
            known["lessons"][lu["lesson"]] = "hypothesis"
    mem = {"tool": "memory", "retrieved": retrieved, "known": known}
    return gate_decision(report, [*body["results"], mem])


def _rereport(args: argparse.Namespace) -> int:
    """Report and gate again for an undecided tune receipt. Nothing runs and no lesson changes: the flow
    results are read again from the run directories the receipt names (flow_report), the report is written
    by this code, and the earlier report and gate decision are kept under `rereports`."""
    from datetime import datetime
    project = load_project(args.project)
    path = Path("runs") / f"{args.rereport}.receipt.json"
    r = json.loads(path.read_text(encoding="utf-8"))
    if r.get("kind") != "tune" or (r.get("approval") or {}).get("status") in ("approved", "rejected"):
        print(f"error: {path} is not an undecided tune receipt")
        return 2
    flow = Flow(project)
    body = {k: r[k] for k in ("goal", "metric", "budget_runs", "runs_used", "start_best", "best", "best_patch",
                              "attempts")}
    body["run_id"] = r["run_id"]
    body["results"] = [flow.report(r["start_best"]["run_dir"])] + [
        flow.report(a["run"]["run_dir"]) for a in r["attempts"] if a["decision"] == "ran" and a["run"].get("run_dir")]
    report = report_text(body)
    gate = _gate(body, report, project)
    r.setdefault("rereports", []).append({"at": datetime.now().astimezone().isoformat(timespec="seconds"),
                                         "by": human_gate.default_approver(), "reason": args.reason,
                                         "previous_report": r["final_claims"], "previous_gate": r["gate"]["decision"]})
    r["final_claims"], r["gate"] = report, {"decision": gate}
    ran = any(a["decision"] == "ran" for a in r["attempts"])
    r["agent_status"] = "done" if gate.get("passed") and (ran or r["attempts"]) else "failed"
    js, md = write_receipt(r, Path("runs"))
    print(report + f"\n\ngate: {'passed' if gate.get('passed') else 'REJECTED: ' + '; '.join(gate['reasons'])}\n"
          f"receipt: {md.with_suffix('.html')}; status {r['status']}")
    return 0 if gate.get("passed") else 1


async def _tune(args: argparse.Namespace) -> int:
    if args.rereport:
        return _rereport(args)
    t0 = time.monotonic()
    project = load_project(args.project)
    run_id = args.run_id or f"tune_{now_id()}"
    given = []
    if args.proposal_file:
        data = json.loads(Path(args.proposal_file).read_text(encoding="utf-8"))
        given = data if isinstance(data, list) else [data]
    model_name = args.model or DEFAULT_MODELS[args.provider]
    model = None if given else make_model(args.provider, model_name, args.effort, max_tokens=args.max_tokens)
    usage: dict = {}
    setup = TuneSetup(goal=args.goal, budget_runs=args.budget_runs, run_id=run_id, proposals=given,
                      dry_run=args.dry_run)
    body = await tune(project, setup, Flow(project), model=model, system="" if given else load_prompt(PROPOSE_PROMPT),
                      usage=usage, by=f"agent ({model_name})" if not given else human_gate.default_approver(),
                      log=lambda s: print(s, flush=True))
    if body.get("error"):
        print(f"error: {body['error']}")
        return 2
    patch_files: dict = {}
    if body["best_patch"] and body["best"]["run"] != body["start_best"]["run"]:
        out = Path("generated") / run_id
        out.mkdir(parents=True, exist_ok=True)
        cfg = Path(project.root) / project.pnr.config
        diff = config_diff(cfg, project.pnr.config, body["best_patch"],
                           f"eda-agent tune {run_id}: best run {body['best']['run']} (awaiting approval)")
        (out / "config.patch").write_text(diff, encoding="utf-8")
        (out / "overrides.json").write_text(json.dumps({k: cli_value(v) for k, v in body["best_patch"].items()},
                                                       indent=1) + "\n", encoding="utf-8")
        patch_files = {"diff": str(out / "config.patch"), "overrides": str(out / "overrides.json"), "diff_text": diff}
    report = report_text(body)
    gate = _gate(body, report, project)
    receipt = tune_receipt(run_id=run_id, project=project.name, provider="person" if given else args.provider,
                           model="(proposal file)" if given else model_name,
                           prompt=args.proposal_file if given else PROPOSE_PROMPT, body=body, report=report, gate=gate,
                           patch_files=patch_files, usage=usage, wall_s=round(time.monotonic() - t0, 1))
    receipt["dry_run"] = args.dry_run
    if not args.dry_run:
        human_gate.request_approval(receipt, "flow config patch and lesson updates",
                                    "a person decides whether the patch goes into the design's flow config and "
                                    "whether the verified/refuted lessons stand (eda-agent lessons void otherwise)")
    js, md = write_receipt(receipt, Path("runs"))
    print("\n" + report)
    print(f"\nreceipt: {md.with_suffix('.html')} (also {md.name}, json: {js.name}); status {receipt['status']}")
    return 0 if gate.get("passed") else 1
