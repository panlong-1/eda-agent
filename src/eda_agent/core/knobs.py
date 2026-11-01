"""Which flow knobs the tuning loop may change, and which it may never change (plan §0.15, §0.8).

Human-set standards, kept in code on purpose: a proposal (a patch of flow config keys) is checked
here before anything runs, and a forbidden item is Fatal, so the proposal is rejected without a run.

Forbidden, per back end:
- the SDC and the clock (period, port, net) and any constraint value the flow writes into its
  fallback SDC (I/O delay, clock uncertainty/transition, derating, max slew/cap/fanout): they define
  what "meets timing" means and belong to the design team (§0.8);
- anything that turns off or weakens a check: skipping DRC, LVS, antenna, XOR, STA, linting;
  not stopping on a check's errors (ERROR_ON_* / QUIT_ON_* false); waiving violations; dropping
  signoff or violation corners; relaxing a tolerance; letting a repair step accept violations.

Tunable (LibreLane): flow knobs that change how the layout is made, not how it is judged:
utilisation and density, placement, resizer repair and buffering, global/detailed routing
iterations, synthesis strategy and switches, CTS parameters. A key that is neither forbidden nor
tunable is an Error: a person adds it to the tunable list first. flow_run applies its own second
list, [pnr] allowed_overrides, set by a person per project.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# -------------------------------------------------------------------- forbidden (never changed)

# keys never set, whatever the value (exact names and patterns)
FORBIDDEN_KEYS = {
    "librelane": (
        "PNR_SDC_FILE", "SIGNOFF_SDC_FILE", "FALLBACK_SDC", "FALLBACK_SDC_FILE", "RCX_SDC_FILE",
        "CLOCK_PERIOD", "CLOCK_PORT", "CLOCK_NET", "STA_CORNERS", "DEFAULT_CORNER", "PNR_CORNERS",
        "RSZ_CORNERS", "CTS_CORNERS",
    ),
}
FORBIDDEN_PATTERNS = {
    "librelane": (
        (re.compile(r"SDC"), "the SDC belongs to the design team (plan §0.8)"),
        (re.compile(r"^CLOCK_"), "the clock belongs to the design team (plan §0.8)"),
        (re.compile(r"_CONSTRAINT$"), "a timing/electrical constraint the flow writes into its SDC (plan §0.8)"),
        (re.compile(r"WAIVE|WAIVER|IGNORE"), "waiving or ignoring violations weakens a check"),
        (re.compile(r"TOLERANCE"), "relaxing a check's tolerance weakens the check"),
        (re.compile(r"CORNERS$"), "the signoff and violation corners are part of the check"),
        (re.compile(r"ALLOW_.*VIO|ALLOW_CONGESTION"), "letting a step accept violations weakens a check"),
    ),
}
# boolean switches that must not be turned off: they run or enforce a check
CHECK_SWITCHES = re.compile(r"^(?:RUN_.*(?:DRC|LVS|ANTENNA|XOR|STA|LINTER|CHECK|ERC|IRDROP|EQY|SPEF)"
                            r"|ERROR_ON_.*|QUIT_ON_.*|.*_CHECKS?)$")
REASON_CHECK_OFF = "turning off a check (or not stopping on its errors) weakens the check"

# -------------------------------------------------------------------- tunable (may be proposed)

TUNABLE = {
    "librelane": re.compile(
        r"^(?:FP_CORE_UTIL|FP_ASPECT_RATIO|PL_TARGET_DENSITY_PCT|PL_TIMING_DRIVEN|PL_ROUTABILITY_DRIVEN"
        r"|PL_WIRE_LENGTH_COEF|PL_MAX_DISPLACEMENT_[XY]|PL_OPTIMIZE_MIRRORING"
        r"|PL_RESIZER_(?:SETUP|HOLD)_[A-Z_]+|GRT_RESIZER_(?:SETUP|HOLD)_[A-Z_]+"
        r"|RUN_POST_(?:CTS|GRT)_RESIZER_TIMING|RUN_POST_(?:GPL|GRT)_DESIGN_REPAIR|PL_RESIZER_FIX_HOLD_FIRST"
        r"|GRT_RESIZER_FIX_HOLD_FIRST|DESIGN_REPAIR_[A-Z_]+|GRT_DESIGN_REPAIR_[A-Z_]+"
        r"|GRT_OVERFLOW_ITERS|GRT_ADJUSTMENT|GRT_LAYER_ADJUSTMENTS|DRT_OPT_ITERS"
        r"|SYNTH_STRATEGY|SYNTH_ABC_BUFFERING|SYNTH_MUL_BOOTH|SYNTH_SIZING|SYNTH_SHARE_RESOURCES"
        r"|SYNTH_ADDER_TYPE|SYNTH_ABC_[A-Z_0-9]+|SYNTH_DIRECT_WIRE_BUFFERING|SYNTH_HIERARCHY_MODE"
        r"|CTS_[A-Z_]+)$"),
}


@dataclass(frozen=True)
class KnobFinding:
    level: str    # fatal | error
    key: str
    value: object
    reason: str

    def to_dict(self) -> dict:
        return {"level": self.level, "key": self.key, "value": self.value, "reason": self.reason}


def forbidden_name(key: str, backend: str = "librelane") -> str | None:
    """Why a key may never be set (whatever its value), or None."""
    if key in FORBIDDEN_KEYS.get(backend, ()):
        return "never changed by the agent: the SDC, the clock and the signoff corners belong to the design team"
    for pat, why in FORBIDDEN_PATTERNS.get(backend, ()):
        if pat.search(key):
            return why
    return None


def _falsy(v) -> bool:
    return v is False or (isinstance(v, str) and v.strip().lower() in ("false", "0", "no", "off", "")) or v == 0


def check_patch(patch: dict, backend: str = "librelane", baseline: dict | None = None) -> list[KnobFinding]:
    """Every reason a proposed patch may not run: Fatal for a forbidden key or value, Error for a key that
    is not on the tunable list (or a no-op). An empty list means the knob policy allows it."""
    out: list[KnobFinding] = []
    if not patch:
        return [KnobFinding("error", "", None, "the proposal changes nothing")]
    tunable = TUNABLE.get(backend)
    for key, value in patch.items():
        key = str(key)
        if why := forbidden_name(key, backend):
            out.append(KnobFinding("fatal", key, value, why))
        elif CHECK_SWITCHES.match(key) and _falsy(value):
            out.append(KnobFinding("fatal", key, value, REASON_CHECK_OFF))
        elif CHECK_SWITCHES.match(key):
            out.append(KnobFinding("error", key, value, "a check switch; only a person changes it"))
        elif not (tunable and tunable.match(key)):
            out.append(KnobFinding("error", key, value, "not on the tunable list (core/knobs.py); a person adds "
                                                        "a knob there first"))
        elif baseline is not None and key in baseline and _same(baseline[key], value):
            out.append(KnobFinding("error", key, value, f"already {baseline[key]!r} in the current best run"))
    return out


def _same(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return str(a).lower() == str(b).lower()
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)


def fatal(findings: list[KnobFinding]) -> bool:
    return any(f.level == "fatal" for f in findings)
