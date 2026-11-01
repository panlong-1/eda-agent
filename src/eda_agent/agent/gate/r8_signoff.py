"""Gate R8: signoff claims must rest on a flow result of this run that shows exactly that fact.

  R8 signoff  "DRC clean", "LVS clean", "antenna clean", "post-route timing met", "signoff
              passed" and "tapeout-ready" (and their Chinese forms) name a flow_report / flow_run
              result in the same sentence (the tool, or the run's tag), and some flow result of
              this run shows the fact: DRC = Magic and KLayout both 0; LVS = 0 errors and a
              matching report; antenna = 0 nets and pins; post-route timing = setup and hold met
              on every signoff corner; signoff = every signoff rule passes; tapeout-ready =
              every signoff rule passes AND the run reached E5 (gate-level simulation and VCD
              power too), which a LibreLane run alone does not. Counts it gives ("0 DRC errors")
              must be counts a flow result reports. A negation just before a claim makes it a
              limit ("not yet DRC clean", "not ready for tapeout").
              A conclusion that joins several stages ("signoff passed", "ready for tapeout",
              "timing and power both met") must also rest on results of ONE design snapshot with an
              unbroken lineage (core/snapshot.py consistency): every result of this run that carries
              a snapshot is the same design (same RTL hashes where they overlap, same SDC and PDK),
              none is from an older snapshot, and the flow run's lineage has no break. Such reasons
              start with "[fatal]" (mixing snapshots, broken lineage) or "[error]"; a rejected report
              is Fatal in the receipt levels either way.

R8 because R6 is reserved for test-score claims (plan §0.6) and R7 is the perf rule.
"""
from __future__ import annotations

import re

from ...core.categories import signoff_extras
from ...core.snapshot import consistency
from .common import _T_CLAUSE, _T_SENTENCE, _negated

FLOW_TOOLS = ("flow_report", "flow_run")

# kind -> claim words. Order matters only for the message.
SIGNOFF_CLAIMS = {
    "tapeout": [r"tape-?out[- ]ready", r"ready\s+(?:for|to)\s+tape[- ]?out", r"ready\s+to\s+tape\s+out",
                r"manufactur(?:able|ing)[- ]ready", "可以流片", "可流片", "能流片", "流片就绪"],
    "signoff": [r"sign(?:ed)?[- ]?off\s+(?:is\s+|was\s+)?(?:clean|passe[sd]|complete[d]?|met|achieved|done)",
                r"(?:passe[sd]|clean|completed?|meets?|met)\s+(?:physical\s+)?sign[- ]?off", r"sign[- ]?off[- ]clean",
                r"signed\s+off", "签核通过", "通过签核", "签核完成", "完成签核"],
    "drc": [r"DRC[- ](?:clean|passe[sd]|free)", r"no\s+(?:Magic\s+|KLayout\s+)?DRC\s+(?:errors?|violations?)",
            r"zero\s+DRC", r"DRC\s+(?:is\s+|was\s+)?(?:clean|passed)", r"DRC\s*(?:干净|通过|无错误?)"],
    "lvs": [r"LVS[- ](?:clean|passe[sd]|match(?:es|ed)?)", r"no\s+LVS\s+(?:errors?|mismatch(?:es)?)",
            r"LVS\s+(?:is\s+|was\s+)?(?:clean|passed)", r"LVS\s*(?:干净|通过|一致)"],
    "antenna": [r"antenna[- ]clean", r"no\s+antenna\s+(?:violations?|errors?)", r"天线(?:检查)?\s*(?:通过|无违例)"],
    "combined": [r"timing\s+and\s+power\s+(?:are\s+|were\s+)?(?:both\s+)?(?:met|fine|ok|within\s+\w+|closed)",
                 r"(?:meets?|met)\s+(?:both\s+)?timing\s+and\s+power", "时序和功耗都(?:满足|达标|没问题)"],
    "timing_post_route": [
        r"(?:post-?route|post-?layout|routed|signoff)\s+timing\s+(?:is\s+|was\s+|has\s+been\s+)?(?:met|closed|clean)",
        r"timing\s+(?:is\s+|was\s+|has\s+been\s+)?(?:met|closed|clean)\s+(?:post-?route|after\s+(?:routing|PnR)|"
        r"on\s+the\s+routed)",
        r"布线后时序(?:满足|收敛|通过|达标)"],
}
_CLAIM = {k: re.compile("|".join(f"(?:{p})" for p in v), re.I) for k, v in SIGNOFF_CLAIMS.items()}
_FLOW_CITE = re.compile(r"(?<![\w-])(?:flow_report|flow_run|flow)(?![\w-])", re.I)
# Counts a sentence may give: "0 DRC errors", "3 KLayout DRC violations", "0 LVS errors", "2 antenna violations"
_COUNTS = {
    "drc": re.compile(r"(\d+)\s+(?:([A-Za-z][\w-]*)\s+)?DRC\s+(?:errors?|violations?)", re.I),
    "lvs": re.compile(r"(\d+)\s+LVS\s+(?:errors?|mismatch(?:es)?)", re.I),
    "antenna": re.compile(r"(\d+)\s+antenna[- ](?:violations?|violating\s+nets?)", re.I),
}
REQUIRES = {
    "tapeout": "every signoff rule passing and evidence level E5 (gate-level simulation and VCD power as well)",
    "signoff": "every signoff rule passing (timing on all signoff corners, DRC, LVS, antenna)",
    "drc": "every signoff DRC tool of the run (e.g. Magic and KLayout) at 0 errors",
    "lvs": "0 LVS errors and a matching LVS report",
    "antenna": "0 antenna-violating nets and pins",
    "timing_post_route": "post-route setup and hold met on every signoff corner",
    "combined": "post-route timing met and a power result judged against a power target (no tool sets one yet)",
}


def _shows(r: dict, kind: str) -> bool:
    m = r.get("metrics") or {}
    if kind == "tapeout":
        return r.get("status") == "pass" and m.get("signoff_level") == "E5"
    if kind == "signoff":
        return r.get("status") == "pass" and bool(m.get("signoff_passed"))
    if kind == "drc":  # every signoff DRC tool the run used, whatever the back end
        by_tool = m.get("drc_by_tool") or {}
        return bool(by_tool) and all(v == 0 for v in by_tool.values())
    if kind == "lvs":
        return m.get("lvs_errors") == 0 and m.get("lvs_match") is not False
    if kind == "antenna":
        return m.get("antenna_nets") == 0 and (m.get("antenna_pins") or 0) == 0
    if kind == "combined":
        return bool(m.get("timing_met")) and m.get("power_met") is True
    return bool(m.get("timing_met"))


def _known_counts(flows: list[dict], kind: str, tool: str | None) -> set[int]:
    out: set[int] = set()
    for r in flows:
        m = r.get("metrics") or {}
        if kind == "drc":
            by_tool = {k.lower(): v for k, v in (m.get("drc_by_tool") or {}).items()}
            named = bool(tool) and tool.lower() in by_tool  # "3 KLayout DRC errors"; other words are not tools
            vals = [by_tool[tool.lower()]] if named else list(by_tool.values())
            if not named and by_tool and all(isinstance(v, int) for v in by_tool.values()):
                vals.append(sum(by_tool.values()))
        elif kind == "lvs":
            vals = [m.get("lvs_errors")]
        else:
            vals = [m.get("antenna_nets"), m.get("antenna_pins")]
        out |= {v for v in vals if isinstance(v, int)}
    return out


def check_signoff(claims: str, results: list[dict]) -> list[str]:
    flows = [r for r in results if r.get("tool") in FLOW_TOOLS and r.get("status") in ("pass", "fail", "error")]
    tags = {str((r.get("metrics") or {}).get("tag")) for r in flows if (r.get("metrics") or {}).get("tag")}
    reasons = []
    for sentence in _T_SENTENCE.findall(claims):
        text = sentence.strip()
        found: list[tuple[str, str]] = []
        for clause in _T_CLAUSE.findall(sentence):
            for kind, rx in _CLAIM.items():
                found += [(kind, m.group(0)) for m in rx.finditer(clause) if not _negated(clause, m.start())]
        counts = [(kind, m) for kind, rx in _COUNTS.items() for m in rx.finditer(sentence)]
        if not found and not counts:
            continue
        if not flows:
            first = found[0][1] if found else counts[0][1].group(0)
            reasons.append(f"signoff statement '{first}' but no flow_report or flow_run result of this run backs it: "
                           f"{text!r}")
            continue
        named = _FLOW_CITE.search(sentence) or any(re.search(rf"(?<![\w-]){re.escape(t)}(?![\w-])", sentence)
                                                   for t in tags)
        if not named:
            first = found[0][1] if found else counts[0][1].group(0)
            reasons.append(f"signoff statement '{first}' must name the flow result it rests on (flow_report, "
                           f"flow_run or the run's tag) in the same sentence: {text!r}")
        for kind, words in found:
            if not any(_shows(r, kind) for r in flows):
                reasons.append(f"'{words}' needs a flow result of this run with {REQUIRES[kind]}; none shows it "
                               f"({_state(flows[-1])}): {text!r}")
        for kind, m in counts:
            tool = m.group(2) if kind == "drc" else None
            if int(m.group(1)) not in _known_counts(flows, kind, tool):
                reasons.append(f"cites '{m.group(0)}' but no flow result of this run reports that count: {text!r}")
        if any(kind in MULTI_STAGE for kind, _ in found):
            reasons += _one_design(results, flows, text)
        if any(kind == "tapeout" for kind, _ in found):
            missing = {k: v for k, v in signoff_extras(results).items() if v != "pass"}
            if missing:
                reasons.append("[error] ready for tapeout also needs CDC/RDC clean (or waivers approved), DFT rules "
                               "passed, ATPG coverage at target and post-scan equivalence; here: "
                               + ", ".join(f"{k} {v}" for k, v in missing.items()) + f": {text!r}")
    return reasons


# Conclusions that join several stages (RTL, netlist, layout, signoff checks, power) into one.
MULTI_STAGE = ("signoff", "tapeout", "combined")


def _one_design(results: list[dict], flows: list[dict], text: str) -> list[str]:
    """The results behind a multi-stage conclusion describe one design with an unbroken lineage."""
    used = [r for r in results if r.get("snapshot")] or flows
    out = [f"[{i['level']}] a multi-stage conclusion needs results of one design snapshot: {i['what']}: {text!r}"
           for i in consistency(used)["issues"] if i["level"] in ("fatal", "error")]
    for r in flows:
        lin = (r.get("metrics") or {}).get("lineage") or {}
        if not lin.get("available"):
            out.append(f"[error] {r.get('tool')} {(r.get('metrics') or {}).get('tag')}: its lineage (netlist -> layout "
                       f"-> signoff) is not known, so its checks cannot be tied to one design: {text!r}")
    return out


def _state(r: dict) -> str:
    m = r.get("metrics") or {}
    return (f"latest: {m.get('tag')} {r.get('status')}, {m.get('signoff_level')}, setup WNS {m.get('wns')}, "
            f"DRC {m.get('drc_by_tool')}, LVS errors {m.get('lvs_errors')}, "
            f"antenna nets {m.get('antenna_nets')}")
