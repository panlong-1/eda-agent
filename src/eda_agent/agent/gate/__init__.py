"""Evidence gate: plain checks of the final report against this run's tool results.

No model is involved. Each rule is a short function returning reasons for rejection,
so a rejection can always be explained by pointing at a line of the report. The rules
are deliberately conservative: a false rejection costs one rewrite, a false pass
lets an overclaim through.

  R1 citations  every (status, evidence level) pair and "N passed/failed" count the
                report cites must come from a tool result of this run; a level named as
                a requirement ("would need E3") is not a citation
  R2 strength   functional-correctness words need a passing result at a functional level
                (E3 or E5; E4 synthesis says nothing about function) that the same sentence
                names (its tool or one of its cases); with E2 the report may only say which
                cases passed. "Does not prove: A, B, or C" lists limits, not claims
  R3 risk       the report has a residual-risk section with some content
  R4 skipped    a case the agent described but did not run is named in that section
  R5 timing     a timing claim ("meets 100 MHz", "positive slack", "timing met") names a result
                of this run that carries an SDC sha256 and agrees with it (met/not met, the
                clock, every WNS/TNS/slack number); when the SDC hash changes during a run,
                timing results measured under the earlier SDC are void
  R6 tests      a claim that a test catches / detects / kills a bug or mutant, or that the kill rate
                rose, names a score_tests result of this run in the same sentence (mutate also counts
                for plain mutant facts, never for a rise); its percentages are ones those results
                report, and every mutant it names was killed there
  R7 perf       a performance claim (faster/slower, speed-up, "N cycles", IPC/CPI, throughput, "no
                performance regression") names a perf (or mutate) result of this run in the same
                sentence, and its numbers are ones those results report; "no regression / not
                slower" needs a perf result compared with the approved baseline and slower nowhere
  R8 signoff    "DRC/LVS/antenna clean", "post-route timing met", "signoff passed", "tapeout-ready"
                name a flow result of this run that shows exactly that; see r8_signoff.py
  R9 category   "lint/CDC/RDC clean", "DFT passed", "test coverage N%", "formally equivalent" cite a
                result of that category with its numbers; categories never stand in for each
                other; see r9_categories.py
  R10 memory    lesson ids and doc:<doc>#<section> citations exist and were retrieved in this run, are
                marked as hints or sources, and never confirm anything without a tool result of this
                run named in the same sentence; see r10_memory.py

One module per rule (r1_citations.py .. r10_memory.py), common.py for what they share; this
module runs them all (check_report) and keeps every name importable from agent.gate.
"""
from __future__ import annotations

from ...core.envelope import EVIDENCE_LEVELS
from ...core.numbers import level_rank
from .common import (_CLAUSE, _CONJ, _NEG, _SENTENCE, _T_CLAUSE, _T_SENTENCE, _TIMING_NEG,  # noqa: F401
                     NEG_WINDOW_WORDS, NEGATIONS, RESIDUAL_HEADING, GateDecision, _matches, _names, _negated,
                     _perf_limit, described_cases, executed_cases, split_residual)
from .r1_citations import check_citations  # noqa: F401
from .r2_strength import FUNCTIONAL_LEVELS, STRONG_CLAIM_MIN_LEVEL, STRONG_CLAIM_WORDS, check_strength  # noqa: F401
from .r3_residual import MIN_RESIDUAL_CHARS, check_residual  # noqa: F401
from .r4_skipped import check_skipped  # noqa: F401
from .r5_timing import TIMING_CLAIMS, check_timing, timing_results  # noqa: F401
from .r6_tests import RISE_CLAIMS, TEST_CLAIMS, check_tests  # noqa: F401
from .r7_perf import PERF_CLAIMS, PERF_SAME_CLAIMS, check_perf, perf_numbers  # noqa: F401
from .r8_signoff import check_signoff
from .r9_categories import check_categories
from .r10_memory import check_memory


def check_report(report: str, results: list[dict]) -> GateDecision:
    """results: the parsed tool results of this run (envelopes, describe_case output, ...)."""
    envelopes = [r for r in results if r.get("evidence_level") in EVIDENCE_LEVELS]
    passing = [r["evidence_level"] for r in envelopes if r.get("status") == "pass"]
    max_level = max(passing, key=level_rank, default="")
    claims, residual = split_residual(report)
    reasons = (check_citations(claims, envelopes) + check_strength(claims, envelopes)
               + check_timing(claims, envelopes) + check_tests(claims, envelopes) + check_perf(claims, envelopes)
               + check_signoff(claims, envelopes) + check_categories(claims, envelopes)
               + check_memory(report, results) + check_residual(residual))
    if not reasons:  # R4 needs the residual-risk section, so it is checked once R1-R3 pass
        reasons = check_skipped(residual, results)
    return GateDecision(passed=not reasons, reasons=reasons, max_level=max_level)


def rejection_message(decision: GateDecision, attempt: int, limit: int) -> str:
    lines = [f"Evidence gate rejected the report (rewrite {attempt} of {limit}). Fix these and "
             "send the full report again; you may run more checks if you need evidence:"]
    lines += [f"- {r}" for r in decision.reasons]
    return "\n".join(lines)
