"""Human gate: steps that only a person may approve.

Nothing waits on input(). A run that needs approval ends with its receipt in status
`awaiting_approval` and an approval record `pending`; a person later runs

    eda-agent approve <run_id> [--by NAME]
    eda-agent reject  <run_id> --reason "..."

which writes the decision, who made it and when, and the sha256 of the receipt content
they decided on, back into the same receipt. A decision is final: a rejected run is fixed by
a new run, not by approving the old one.
"""
from __future__ import annotations

import getpass
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

AWAITING = "awaiting_approval"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def content_sha256(receipt: dict) -> str:
    """Hash of what the person decided on: the receipt without the fields the decision itself
    writes (approval, status, agent_status), so it can be re-checked on the saved receipt."""
    body = {k: v for k, v in receipt.items() if k not in ("approval", "status", "agent_status")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def request_approval(receipt: dict, step: str, why: str) -> dict:
    """Put the run on hold for a person. The agent's own outcome stays in agent_status."""
    receipt["agent_status"] = receipt["status"]
    receipt["status"] = AWAITING
    receipt["approval"] = {"status": "pending", "step": step, "why": why, "requested_at": _now(),
                           "by": None, "at": None, "reason": None, "receipt_sha256": None}
    return receipt


def default_approver() -> str:
    try:
        name = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True,
                              timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        name = ""
    return name or getpass.getuser()


class ApprovalError(ValueError):
    pass


def decide(receipt: dict, decision: str, by: str, reason: str = "", override_gate: bool = False) -> dict:
    """Record approve/reject. Refuses runs that are not waiting for a person, decisions without
    a reason where one is needed, and approving a gate-rejected run without override_gate."""
    if decision not in ("approve", "reject"):
        raise ApprovalError(f"unknown decision {decision!r}")
    approval = receipt.get("approval")
    status = receipt.get("status")
    if approval and approval.get("status") in ("approved", "rejected"):
        raise ApprovalError(f"run {receipt['run_id']} was already {approval['status']} by {approval['by']} "
                            f"at {approval['at']}; decisions are final, start a new run")
    if decision == "reject" and not reason:
        raise ApprovalError("a rejection needs --reason")
    if status != AWAITING:
        if status == "pending" and decision == "approve" and not override_gate:
            raise ApprovalError(f"run {receipt['run_id']} was rejected by the evidence gate (status pending); "
                                "approving it overrides the gate: pass --override-gate and --reason")
        if status == "pending" and override_gate and not reason:
            raise ApprovalError("overriding the evidence gate needs --reason")
        if status not in ("pending",):
            raise ApprovalError(f"run {receipt['run_id']} has status {status!r}; only runs awaiting approval "
                                "(or gate-rejected runs, with --override-gate) can be decided")
    approval = approval or {"step": "final report", "why": "decided without a request", "requested_at": None}
    approval.update({
        "status": "approved" if decision == "approve" else "rejected",
        "by": by, "at": _now(), "reason": reason or None,
        "overrode_gate": status == "pending" and decision == "approve",
        "receipt_sha256": content_sha256(receipt),
    })
    receipt.setdefault("agent_status", status)
    receipt["approval"] = approval
    receipt["status"] = approval["status"]
    return receipt


def load_receipt(run_id: str, runs_dir: Path) -> tuple[dict, Path]:
    path = runs_dir / f"{run_id}.receipt.json"
    if not path.is_file():
        raise ApprovalError(f"no receipt {path}")
    return json.loads(path.read_text(encoding="utf-8")), path
