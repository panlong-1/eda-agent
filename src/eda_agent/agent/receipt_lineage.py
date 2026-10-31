"""Receipt parts for plan §0.18: which design snapshot each check describes, whether the checks used
together are one design, and each flow run's lineage (stage, inputs, outputs, sha256, breaks)."""
from __future__ import annotations

from ..core.snapshot import consistency


def snapshot_of(result: dict) -> dict:
    s = result.get("snapshot") or {}
    return {"snapshot": {k: s.get(k) for k in ("id", "design_id", "state", "note")}} if s else {}


def lineage_of(result: dict) -> dict:
    lin = (result.get("metrics") or {}).get("lineage")
    return {"lineage": lin} if lin else {}


def receipt_consistency(envelopes: list[dict]) -> dict:
    """Facts about all checks of the run taken together (same design, SDC, PDK, corners, lineage)."""
    return consistency([e for e in envelopes if e.get("snapshot") or (e.get("metrics") or {}).get("lineage")])


def _sha(e: dict) -> str:
    return (e.get("sha256") or "MISSING")[:10]


def render(r: dict) -> list[str]:
    snaps = [c for c in r.get("checks_run", []) if c.get("snapshot")]
    cons = r.get("consistency")
    out: list[str] = []
    if snaps:
        out += ["", "## Design snapshot", "", "| tool | snapshot | design | state | note |", "|---|---|---|---|---|"]
        for c in snaps:
            s = c["snapshot"]
            out.append(f"| {c['tool']} | `{s.get('id') or '-'}` | `{s.get('design_id') or '-'}` | {s.get('state')} "
                       f"| {s.get('note') or ''} |")
    if cons:
        out += ["", "Checks used together: " + ("one design, no conflict" if cons["ok"] else "**NOT one design**")]
        out += [f"- **{i['level'].upper()}** {i['what']}" for i in cons["issues"]]
    for c in r.get("checks_run", []):
        lin = c.get("lineage")
        if not lin:
            continue
        out += ["", f"## Lineage: {c['tool']} run {lin.get('run')} ({lin.get('backend')})", ""]
        if not lin.get("available"):
            out.append("- the flow adapter cannot extract a lineage for this run")
            continue
        out += ["| stage | steps | inputs (sha256, from) | outputs (sha256) | ok |", "|---|---|---|---|---|"]
        for s in lin["stages"]:
            ins = "; ".join(f"{k} {_sha(e)} ← {e.get('from')}" for k, e in s["inputs"].items()) or "-"
            outs = "; ".join(f"{k} {_sha(e)}" for k, e in s["outputs"].items()) or "-"
            out.append(f"| {s['stage']} | {s['steps'][0]} .. {s['steps'][1]} | {ins} | {outs} | "
                       f"{'yes' if s['ok'] else '**BREAK**'} |")
        fin = lin.get("final") or []
        out.append(f"- final/ copies matching their step outputs: {sum(f['matches'] for f in fin)} of {len(fin)}")
        out += [f"- **{b.get('level', 'error').upper()}** {b['what']}" for b in lin.get("breaks", [])]
        if lin.get("stages_absent"):
            out.append(f"- stages not in this run: {', '.join(lin['stages_absent'])}")
        out.append(f"- not part of this flow (needed for full E5): {', '.join(lin.get('not_present') or [])}")
    return out
