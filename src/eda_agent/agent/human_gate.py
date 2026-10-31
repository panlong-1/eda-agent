"""Human gate: steps that only a person may approve.

Nothing waits on input(). A run that needs approval ends with its receipt in status
`awaiting_approval` and an approval record `pending`; a person later runs

    eda-agent approve <run_id> [--by NAME] [--reason "..."] [--override]
    eda-agent reject  <run_id> --reason "..."
    eda-agent pending <run_id> --note "..."      (decide later: a note, the run stays open)

(or clicks the same in `eda-agent review-ui`), which writes the decision, who made it and
when, and the sha256 of the receipt content they decided on, back into the same receipt.
What `approve` needs depends on the receipt's issue levels (agent/levels.py):

    Fatal > 0    --override and --reason (the override is recorded)
    Error > 0    --reason (why the errors are acceptable)
    Warnings     a plain approve

A decision is final: a rejected run is fixed by a new run, not by approving the old one.
"""
from __future__ import annotations

import getpass
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

AWAITING = "awaiting_approval"
# Fields left out of the decided content: what the decision itself writes, plus derived or
# advisory data that may be (re)written later (issue levels, the model-written summary).
NOT_HASHED = ("approval", "status", "agent_status", "levels", "narrative", "narratives")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def content_sha256(receipt: dict) -> str:
    """Hash of what the person decided on: the receipt without NOT_HASHED, so it can be
    re-checked on the saved receipt."""
    body = {k: v for k, v in receipt.items() if k not in NOT_HASHED and not k.startswith("_")}
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


def _open_approval(receipt: dict) -> dict:
    """The approval record of a run a person may still decide; refuses decided or other runs."""
    approval = receipt.get("approval")
    status = receipt.get("status")
    if approval and approval.get("status") in ("approved", "rejected"):
        raise ApprovalError(f"run {receipt['run_id']} was already {approval['status']} by {approval['by']} "
                            f"at {approval['at']}; decisions are final, start a new run")
    if status not in (AWAITING, "pending"):
        raise ApprovalError(f"run {receipt['run_id']} has status {status!r}; only runs awaiting approval "
                            "(or gate-rejected runs, with --override) can be decided")
    return approval or {"status": "pending", "step": "final report", "why": "decided without a request",
                        "requested_at": None}


def _items(issues, level: str) -> str:
    picked = [i.text for i in issues if i.level == level]
    return "; ".join(picked[:3]) + (f"; … ({len(picked) - 3} more)" if len(picked) > 3 else "")


def decide(receipt: dict, decision: str, by: str, reason: str = "", override_gate: bool = False,
           override: bool = False, via: str = "") -> dict:
    """Record approve/reject. Refuses runs that are not waiting for a person, a rejection without
    a reason, and an approval the issue levels do not allow (see the module docstring)."""
    from .levels import assess, counts  # levels uses content_sha256 from here
    if decision not in ("approve", "reject"):
        raise ApprovalError(f"unknown decision {decision!r}")
    override = override or override_gate
    approval = _open_approval(receipt)
    status = receipt.get("status")
    if decision == "reject" and not reason:
        raise ApprovalError("a rejection needs --reason")
    issues = assess(receipt)
    c = counts(issues)
    if decision == "approve":
        if status == "pending" and not override:
            raise ApprovalError(f"run {receipt['run_id']} was rejected by the evidence gate (status pending); "
                                "approving it overrides the gate: pass --override and --reason")
        if c["fatal"] and not override:
            raise ApprovalError(f"run {receipt['run_id']} has {c['fatal']} Fatal item(s) ({_items(issues, 'fatal')}); "
                                "approving it needs --override and --reason")
        if override and not reason:
            raise ApprovalError("an override needs --reason")
        if c["error"] and not reason:
            raise ApprovalError(f"run {receipt['run_id']} has {c['error']} Error item(s) ({_items(issues, 'error')}); "
                                "approving it needs --reason saying why they are acceptable")
    approval.update({
        "status": "approved" if decision == "approve" else "rejected",
        "by": by + (f" (via {via})" if via else ""), "at": _now(), "reason": reason or None,
        "overrode_gate": status == "pending" and decision == "approve",
        "override": decision == "approve" and bool(c["fatal"] or status == "pending"),
        "levels": c,
        "receipt_sha256": content_sha256(receipt),
    })
    receipt.setdefault("agent_status", status)
    receipt["approval"] = approval
    receipt["status"] = approval["status"]
    return receipt


def defer(receipt: dict, by: str, note: str = "", via: str = "") -> dict:
    """'Pending': decide later. Adds a note to the approval record; the run stays open."""
    approval = _open_approval(receipt)
    approval.setdefault("notes", []).append({"by": by + (f" (via {via})" if via else ""), "at": _now(),
                                            "note": note or None})
    receipt["approval"] = approval
    return receipt


def load_receipt(run_id: str, runs_dir: Path) -> tuple[dict, Path]:
    path = runs_dir / f"{run_id}.receipt.json"
    if not path.is_file():
        raise ApprovalError(f"no receipt {path}")
    return json.loads(path.read_text(encoding="utf-8")), path
