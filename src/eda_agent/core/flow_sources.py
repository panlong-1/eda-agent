"""Read [flow] settings from where a design already keeps them, instead of copying them.

`[flow] librelane = "librelane/config.yaml"` fills top, files, include dirs, frontend,
clock and SDC from a LibreLane config; a known PDK then fills the library settings from
PDK_PRESETS. Keys written in [flow] always win over both.
"""
from __future__ import annotations

import json
from pathlib import Path

LIBRELANE_IMAGE = "ghcr.io/librelane/librelane:3.0.16"

# Library settings per PDK, as LibreLane uses them (SYNTH_*, STA_CORNERS). PDK data, not design data.
_IHP = "${PDK_ROOT}/ihp-sg13g2"
_IHP_LIB = f"{_IHP}/libs.ref/sg13g2_stdcell/lib/sg13g2_stdcell"
PDK_PRESETS: dict[str, dict] = {
    "ihp-sg13g2": {
        "pdk_root": "${PDK_ROOT}",
        "liberty": f"{_IHP_LIB}_typ_1p20V_25C.lib",
        "dont_use_file": f"{_IHP}/libs.tech/librelane/sg13g2_stdcell/synth_exclude.cells",
        "tie_hi": "sg13g2_tiehi/L_HI",
        "tie_lo": "sg13g2_tielo/L_LO",
        "buffer": "sg13g2_buf_1/A/X",
        "abc_driving_cell": "sg13g2_buf_4",
        "abc_load": 6.0,
        "corners": {
            "typ": f"{_IHP_LIB}_typ_1p20V_25C.lib",
            "slow": f"{_IHP_LIB}_slow_1p08V_125C.lib",
            "fast": f"{_IHP_LIB}_fast_1p32V_m40C.lib",
        },
    },
}


def _load(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return json.loads(text)
    import yaml
    return yaml.safe_load(text) or {}


def _dir_path(value: str, config_dir: Path, root: Path) -> str:
    """LibreLane's dir::x (relative to the config file) as a path relative to the project root."""
    if value.startswith("dir::"):
        p = (config_dir / value[5:]).resolve()
    elif value.startswith("ref::") or value.startswith("pdk_dir::"):
        raise ValueError(f"LibreLane path {value!r} is not supported; write it in [flow] instead")
    else:
        p = Path(value)
        p = p if p.is_absolute() else (root / p).resolve()
    try:
        return str(p.relative_to(root))
    except ValueError:
        return str(p)


def _as_list(value) -> list:
    return value if isinstance(value, list) else [value]


def read_librelane(config: Path, root: Path) -> dict:
    """[flow] keys taken from a LibreLane config (YAML or JSON)."""
    data = _load(config)
    here = config.parent.resolve()
    out: dict = {}
    if "DESIGN_NAME" in data:
        out["top"] = data["DESIGN_NAME"]
    if "VERILOG_FILES" in data:
        out["files"] = [_dir_path(f, here, root) for f in _as_list(data["VERILOG_FILES"])]
    if "VERILOG_INCLUDE_DIRS" in data:
        out["include_dirs"] = [_dir_path(f, here, root) for f in _as_list(data["VERILOG_INCLUDE_DIRS"])]
    if "VERILOG_DEFINES" in data:
        out["defines"] = list(_as_list(data["VERILOG_DEFINES"]))
    out["frontend"] = "slang" if data.get("USE_SLANG") else "verilog"
    if data.get("SYNTH_MUL_BOOTH"):
        out["synth_flags"] = ["-booth"]
    if "CLOCK_PORT" in data:
        out["clock_port"] = data["CLOCK_PORT"]
    if "CLOCK_PERIOD" in data:
        out["clock_period"] = float(data["CLOCK_PERIOD"])
    sdc = data.get("SIGNOFF_SDC_FILE") or data.get("PNR_SDC_FILE")
    if sdc:
        out["sdc"] = _dir_path(sdc, here, root)
    if "PDK" in data:
        out["pdk"] = data["PDK"]
    return out


def resolve_flow(data: dict, root: Path) -> dict:
    """[flow] as written, completed from `librelane = ...`, then from the PDK preset."""
    data = dict(data)
    merged: dict = {}
    if lib := data.pop("librelane", None):
        merged.update(read_librelane(root / lib, root))
    pdk = data.get("pdk", merged.get("pdk", ""))
    preset = PDK_PRESETS.get(pdk, {}) if data.pop("pdk_preset", True) else {}
    if lib or preset:
        merged.setdefault("image", LIBRELANE_IMAGE)
    return {**preset, **merged, **data}
