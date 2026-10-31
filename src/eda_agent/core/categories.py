"""Check categories for imported results (plan §0.19): what each answers, its key numbers, and the
core's rules. A plug-in only parses a report into a neutral result; status, evidence level and
claim scope are decided here, never by the plug-in.

Levels: categories that are static or structural checks (lint rules, CDC/RDC, DFT, ATPG coverage,
equivalence) are E0: facts in their own category, not steps on the functional ladder, so they never
stand in for simulation (E2/E3) or signoff (E5). Physical and timing results are E4, simulation E2.

Human-set standards (waivers, coverage targets) come from core/standards.py: a plug-in's own
"waived" list counts only for waivers a person approved there.
"""
from __future__ import annotations

# category -> spec. required: metrics a parse must give; numbers: metrics the gate may see cited.
CATEGORIES: dict[str, dict] = {
    "lint_rules": {
        "question": "does the design follow the rule set",
        "required": ["violations_by_severity", "ruleset"], "level": "E0",
        "scope": "{tool} {version} rule check ({ruleset}) of {design}: {errors} error, {warnings} warning "
                 "violation(s) after {waived} approved waiver(s). Says nothing about function or timing.",
    },
    "cdc": {
        "question": "are clock-domain crossings synchronized",
        "required": ["unsynchronized", "constraints_complete"], "level": "E0",
        "scope": "{tool} {version} clock-domain-crossing check of {design}: {unsynchronized} unsynchronized "
                 "crossing(s) after {waived} approved waiver(s), constraints complete: {constraints_complete}. "
                 "Structural: says nothing about function, reset behaviour or timing.",
    },
    "rdc": {
        "question": "are reset-domain crossings safe",
        "required": ["unsynchronized", "constraints_complete"], "level": "E0",
        "scope": "{tool} {version} reset-domain-crossing check of {design}: {unsynchronized} unsafe crossing(s) "
                 "after {waived} approved waiver(s), constraints complete: {constraints_complete}. Structural only.",
    },
    "dft": {
        "question": "is the design testable: scan inserted, DFT rules met",
        "required": ["scan_chains", "dft_rule_violations"], "level": "E0",
        "scope": "{tool} {version} DFT of {design}: {scan_chains} scan chain(s) (longest {max_chain_length}), "
                 "{dft_rule_violations} DFT rule violation(s) after {waived} approved waiver(s). Testability "
                 "of manufacturing defects, not functional verification.",
    },
    "atpg": {
        "question": "how much of the manufacturing-fault universe the test patterns cover",
        "required": ["test_coverage_pct", "fault_coverage_pct", "patterns"], "level": "E0",
        "scope": "{tool} {version} ATPG of {design}: test coverage {test_coverage_pct}% (target {target}), fault "
                 "coverage {fault_coverage_pct}%, {patterns} pattern(s). Manufacturing-fault coverage, not "
                 "verification coverage.",
    },
    "equivalence": {
        "question": "are the two netlists (or RTL and netlist) functionally equivalent",
        "required": ["equivalent_points", "non_equivalent_points", "unmapped_points", "reference", "implementation"],
        "level": "E0",
        "scope": "{tool} {version} equivalence of {implementation} against {reference}: {equivalent_points} "
                 "equivalent, {non_equivalent_points} non-equivalent, {unmapped_points} unmapped, {aborted_points} "
                 "aborted point(s). Equivalence of these two only; neither is shown correct.",
    },
    "synthesis": {
        "question": "what netlist the RTL maps to, and its pre-layout estimate",
        "required": ["cells", "area_um2"], "level": "E4",
        "scope": "{tool} {version} synthesis of {design}: {cells} cells, {area_um2} um2. A pre-layout estimate.",
    },
    "pnr": {
        "question": "what the placed and routed layout looks like",
        "required": ["die_area_um2", "utilization"], "level": "E4",
        "scope": "{tool} {version} place and route of {design}: die {die_area_um2} um2, utilization {utilization}.",
    },
    "sta": {
        "question": "does the design meet timing under the SDC on these corners",
        "required": ["corners", "clock_period"], "level": "E4",
        "scope": "{tool} {version} STA of {design} on corner(s) {corner_names} at {clock_period} ns, propagated "
                 "clock: {propagated_clock}, parasitics: {parasitics}. Timing under this SDC only.",
    },
    "drc": {
        "question": "does the layout meet the foundry's design rules",
        "required": ["violations", "deck"], "level": "E4",
        "scope": "{tool} {version} DRC of {design} with deck {deck}: {violations} violation(s) after {waived} "
                 "approved waiver(s).",
    },
    "lvs": {
        "question": "does the layout match the netlist",
        "required": ["match", "errors"], "level": "E4",
        "scope": "{tool} {version} LVS of {design}: match {match}, {errors} error(s).",
    },
    "simulation": {
        "question": "do the tests pass in simulation",
        "required": ["passed", "failed"], "level": "E2",
        "scope": "{tool} {version} simulation of {design}: {passed} passed, {failed} failed; gate level: {gate_level}, "
                 "SDF: {sdf}, timing checks: {timing_checks}. Only these tests.",
    },
    "power": {
        "question": "how much power the design draws, and from what activity",
        "required": ["total_w", "method"], "level": "E4",
        "scope": "{tool} {version} power of {design}: {total_w} W, method {method}, activity annotation "
                 "{annotation_coverage_pct}%, activity corner {activity_corner}, library corner {lib_corner}.",
    },
}
# Keys a plug-in may never set: the core decides them.
FORBIDDEN_KEYS = ("status", "evidence_level", "level", "passed_check", "verdict", "claim_scope", "signoff",
                  "signoff_passed", "clean", "pass", "fail")
CATEGORY_NAMES = tuple(CATEGORIES)


def _waived(metrics: dict, approved: set[str]) -> tuple[int, list[str]]:
    """(approved waivers among the report's waived items, ids waived in the report but not approved)."""
    ids = [str(w.get("id")) for w in metrics.get("waivers") or [] if isinstance(w, dict)]
    return sum(i in approved for i in ids), [i for i in ids if i not in approved]


def judge(category: str, metrics: dict, approved_waivers: set[str], targets: dict) -> tuple[str, list[str]]:
    """(status, reasons) by the core's rule for the category. Waived items that no person approved
    count as violations."""
    m = metrics
    ok_waived, unapproved = _waived(m, approved_waivers)
    reasons = [f"waiver {i} is not approved (eda-agent standard waive)" for i in unapproved]
    extra = len(unapproved)
    if category == "lint_rules":
        errs = int((m.get("violations_by_severity") or {}).get("error", 0)) + extra
        return ("fail" if errs else "pass"), reasons + ([f"{errs} unwaived error violation(s)"] if errs else [])
    if category in ("cdc", "rdc"):
        bad = int(m["unsynchronized"]) + extra
        if bad:
            return "fail", reasons + [f"{bad} unsynchronized crossing(s)"]
        if not m.get("constraints_complete"):
            return "error", ["constraints incomplete: the check cannot be complete"]
        return "pass", reasons
    if category == "dft":
        bad = int(m["dft_rule_violations"]) + extra
        if bad:
            return "fail", reasons + [f"{bad} DFT rule violation(s)"]
        if int(m["scan_chains"]) == 0:
            return "fail", ["no scan chain"]
        return "pass", reasons
    if category == "atpg":
        target = targets.get("atpg.test_coverage_pct")
        if target is None:
            return "error", ["no approved coverage target (eda-agent standard target atpg.test_coverage_pct ...)"]
        cov = float(m["test_coverage_pct"])
        return ("pass", []) if cov >= target else ("fail", [f"test coverage {cov}% below target {target}%"])
    if category == "equivalence":
        if int(m["non_equivalent_points"]):
            return "fail", [f"{m['non_equivalent_points']} non-equivalent point(s)"]
        if int(m["unmapped_points"]) or int(m.get("aborted_points", 0)):
            return "error", [f"{m['unmapped_points']} unmapped and {m.get('aborted_points', 0)} aborted point(s): "
                             "equivalence not established for those"]
        return "pass", []
    if category == "sta":
        worst = [(c, v.get("setup_wns"), v.get("hold_wns")) for c, v in (m.get("corners") or {}).items()]
        if not worst:
            return "error", ["no corner"]
        bad = [f"{c}: setup {s}, hold {h}" for c, s, h in worst
               if (isinstance(s, (int, float)) and s < 0) or (isinstance(h, (int, float)) and h < 0)]
        return ("fail", bad) if bad else ("pass", [])
    if category == "drc":
        bad = int(m["violations"]) + extra
        return ("fail", reasons + [f"{bad} DRC violation(s)"]) if bad else ("pass", reasons)
    if category == "lvs":
        return ("pass", []) if m["match"] and not int(m["errors"]) else ("fail", [f"match {m['match']}, {m['errors']} error(s)"])
    if category == "simulation":
        return ("fail", [f"{m['failed']} failed"]) if int(m["failed"]) else ("pass", [])
    if category == "synthesis":
        return ("fail", [f"{m['latches']} latch(es)"]) if m.get("latches") else ("pass", [])
    if category == "power":
        target = targets.get("power.total_w")
        if target is not None and float(m["total_w"]) > target:
            return "fail", [f"{m['total_w']} W above target {target} W"]
        return "pass", []
    return "pass", []


def _netlists(r: dict, side: str) -> set[str]:
    files = (r.get("metrics") or {}).get("files") or {}
    return {f["sha256"] for f in files.get(side, []) if f.get("role") == "netlist" and f.get("sha256")}


def scan_links(results: list[dict]) -> list[dict]:
    """Each DFT result that changed the netlist (pre-scan in, post-scan out), and whether a passing
    equivalence result of the run covers both netlists."""
    eqs = [r for r in results if (r.get("metrics") or {}).get("category") == "equivalence" and r.get("status") == "pass"]
    out = []
    for r in results:
        if (r.get("metrics") or {}).get("category") != "dft":
            continue
        pre, post = _netlists(r, "inputs"), _netlists(r, "outputs")
        if not pre or not post or pre == post:
            continue
        ok = any((pre | post) <= (_netlists(e, "inputs") | _netlists(e, "outputs")) for e in eqs)
        out.append({"pre_scan": sorted(pre), "post_scan": sorted(post), "equivalence_passed": ok})
    return out


# What "ready for tapeout" needs beyond the physical signoff (plan §0.19); missing ones are "not covered".
SIGNOFF_EXTRAS = ("cdc", "rdc", "dft", "atpg", "post_scan_equivalence")


def signoff_extras(results: list[dict]) -> dict[str, str]:
    out = {}
    for cat in ("cdc", "rdc", "dft", "atpg"):
        mine = [r for r in results if (r.get("metrics") or {}).get("category") == cat]
        out[cat] = "not covered" if not mine else ("pass" if mine[-1].get("status") == "pass" else mine[-1].get("status"))
    links = scan_links(results)
    out["post_scan_equivalence"] = "not covered" if not links else ("pass" if all(x["equivalence_passed"] for x in links)
                                                                    else "missing")
    return out


def scope(category: str, tool: dict, design: str | None, metrics: dict, approved: int, targets: dict) -> str:
    spec = CATEGORIES[category]
    fields = {k: (v if not isinstance(v, (dict, list)) else "…") for k, v in metrics.items()}
    sev = metrics.get("violations_by_severity") or {}
    fields.update(tool=tool.get("name"), version=tool.get("version"), design=design or "the design",
                  waived=approved, errors=sev.get("error", 0), warnings=sev.get("warning", 0),
                  target=targets.get("atpg.test_coverage_pct", "none set"),
                  corner_names=", ".join(metrics.get("corners") or {}) if isinstance(metrics.get("corners"), dict) else "?")

    class _D(dict):
        def __missing__(self, k):
            return "?"
    return spec["scope"].format_map(_D(fields))
