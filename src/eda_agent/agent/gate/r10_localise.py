"""R10 localisation: a claim that places a failure's cause ("the bug is in X", "caused by line N", "root
cause", "localised to", "定位到") is a diagnosis. It needs a trace_driver / trace_load / waveform_query
result of this run, names one of them in the same sentence, cites only file:line (or "line N") that a
trace result of this run reached, never presents the diagnosis as a proven fix, and the report calls it
a hypothesis (or diagnosis) somewhere."""
from __future__ import annotations

import re

from .common import _negated

LOCALISE_TOOLS = ("trace_driver", "trace_load", "waveform_query")
TRACE_TOOLS = ("trace_driver", "trace_load")

LOCALISE_CLAIMS = [
    r"root[\s-]+causes?",
    r"(?:bug|fault|defect|error|failure|problem|cause)\s+(?:is|lies|sits|was|comes|originates)\s+(?:\w+\s+){0,2}?"
    r"(?:in|at|on|from)(?![\w-])",
    r"caused\s+by",
    r"(?:locali[sz]ed?|locali[sz]es|traced|pinpoint(?:ed|s)?)\s+(?:\w+\s+){0,4}?to(?![\w-])",
    r"(?:is|are|was|were)\s+(?:most\s+likely\s+|likely\s+|probably\s+)?due\s+to",
    r"responsible\s+for\s+(?:the\s+)?fail",
    "根因|根本原因|定位到|问题出在|原因(?:是|在于|在)|bug\\s*(?:在|位于)|(?:引起|导致)了?(?:该|这个|此)?(?:失败|错误|bug)",
]
FIX_CLAIMS = [
    r"fix(?:es)?\s+(?:is\s+|was\s+)?(?:verified|confirmed|proven)", r"(?:is|are|has\s+been|was)\s+(?:now\s+)?fixed",
    r"proves?\s+the\s+fix", r"resolv(?:es|ed)\s+the\s+(?:bug|failure|fault)",
    "已修复|修复(?:已)?(?:验证|确认|生效)|证明.{0,6}修复",
]
HYPOTHESIS = re.compile(r"hypothes[ie]s|diagnos(?:is|tic|ed)|likely|probabl[ey]|suspect|假设|诊断|推测|可能", re.I)

_CLAIM = re.compile("|".join(f"(?:{p})" for p in LOCALISE_CLAIMS), re.I)
_FIX = re.compile("|".join(f"(?:{p})" for p in FIX_CLAIMS), re.I)
_TOOL = re.compile(r"(?<![\w-])(?:" + "|".join(LOCALISE_TOOLS) + r")(?![\w-])", re.I)
# A sentence ends at . ! ? only before a space or the end, so `rtl/x.sv:23` stays whole.
_SENTENCE = re.compile(r"(?:[^.!?。！？\n]|[.!?](?=[^\s.!?]))+")
_FILE_LINE = re.compile(r"([\w./-]+\.(?:sv|svh|v|vh|vhd|vhdl)):(\d+)(?![\w])")
_LINE = re.compile(r"(?<![\w])(?:lines?\s+|L|第\s*)(\d+)(?:\s*行)?(?![\w])", re.I)


def reached(results: list[dict]) -> set[tuple[str, int]]:
    """(file name, line) of every location a trace result of this run reached."""
    out = set()
    for r in results:
        if r.get("tool") in TRACE_TOOLS and r.get("status") == "pass":
            for loc in (r.get("metrics") or {}).get("locations", []):
                f, _, ln = str(loc).rpartition(":")
                if ln.isdigit():
                    out.add((f.rsplit("/", 1)[-1], int(ln)))
    return out


def check_localisation(claims: str, results: list[dict], report: str = "") -> list[str]:
    usable = [r for r in results if r.get("tool") in LOCALISE_TOOLS and r.get("status") == "pass"]
    places = reached(results)
    reasons, found = [], False
    for sentence in _SENTENCE.findall(claims):
        text = sentence.strip()
        hits = [m for m in _CLAIM.finditer(sentence) if not _negated(sentence, m.start())]
        if not hits:
            continue
        found = True
        first = hits[0].group(0)
        if not usable:
            reasons.append(f"'{first}' places the cause of a failure, which needs a trace_driver, trace_load or "
                           f"waveform_query result of this run; there is none: {text!r}")
            continue
        if not _TOOL.search(sentence):
            reasons.append(f"'{first}' must name the trace_driver / trace_load / waveform_query result it rests on "
                           f"in the same sentence: {text!r}")
        for f, ln in _FILE_LINE.findall(sentence):
            if (f.rsplit("/", 1)[-1], int(ln)) not in places:
                reasons.append(f"cites {f}:{ln}, but no trace_driver/trace_load result of this run reached that "
                               f"line: {text!r}")
        bare = _FILE_LINE.sub(" ", sentence)
        for ln in _LINE.findall(bare):
            if int(ln) not in {x for _, x in places}:
                reasons.append(f"cites line {ln}, but no trace_driver/trace_load result of this run reached a line "
                               f"{ln}: {text!r}")
        if m := _FIX.search(sentence):
            reasons.append(f"'{m.group(0)}': a localisation is a diagnosis, not proof that a fix works; a fix needs "
                           f"its own regress results: {text!r}")
    if found and not HYPOTHESIS.search(report or claims):
        reasons.append("the report places the cause of a failure but never calls it a hypothesis or diagnosis; "
                       "trace and waveform results show where, not that the cause is proven")
    return reasons
