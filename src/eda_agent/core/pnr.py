"""[pnr]: the project's RTL-to-GDS flow for the flow_report, flow_history and flow_run tools."""
from __future__ import annotations

from dataclasses import dataclass

# Per back end: config keys flow_run never sets, whatever the config allows. The SDC defines what
# "meets timing" means and belongs to the design team (plan §0.8); the agent only reads it.
NEVER_OVERRIDE = {
    "librelane": ("PNR_SDC_FILE", "SIGNOFF_SDC_FILE", "FALLBACK_SDC", "RCX_SDC_FILE", "CLOCK_PERIOD", "CLOCK_PORT"),
}


@dataclass(frozen=True)
class PnrConfig:
    """Paths are relative to the project root unless absolute; ${VAR}s are expanded."""
    config: str                          # the flow's config file (LibreLane: config.yaml / config.json)
    backend: str = "librelane"           # the flow adapter (tools/backends); LibreLane is the default
    runs_dir: str = ""                   # where the design's own runs are; default: the adapter's (<config dir>/runs)
    baseline: str = ""                   # run compared against by default (a name under runs_dir)
    signoff_corners: tuple[str, ...] = ()  # empty: every STA corner of the run
    image: str = ""                      # default: [flow] image
    pdk: str = ""                        # default: [flow] pdk
    pdk_root: str = "${PDK_ROOT}"
    work_dir: str = "runs/pnr"           # flow_run writes its copies and runs here (eda-agent side)
    experience_dir: str = "runs/experience"  # per-design experience tables: <experience_dir>/<project name>/
    copy_exclude: tuple[str, ...] = (".git",)  # left out of flow_run's copy of the root, besides runs_dir
    timeout_s: int = 3 * 3600
    allowed_overrides: tuple[str, ...] = ()    # config keys flow_run may set (LibreLane: -c KEY=VALUE)
    agent_may_run: bool = False          # the MCP flow_run tool refuses unless true (an hour of CPU)

    def __post_init__(self) -> None:
        if not self.config:
            raise ValueError("[pnr] needs config (the flow's config file)")
        if self.timeout_s < 1:
            raise ValueError("[pnr] timeout_s must be positive")
        if bad := set(self.allowed_overrides) & set(NEVER_OVERRIDE.get(self.backend, ())):
            raise ValueError(f"[pnr] allowed_overrides may not include {sorted(bad)}: the SDC and clock "
                             "belong to the design team")


def load_pnr(data: dict | None) -> PnrConfig | None:
    if data is None:
        return None
    unknown = set(data) - set(PnrConfig.__dataclass_fields__)
    if unknown:
        raise ValueError(f"unknown [pnr] key(s) {sorted(unknown)}")
    tuples = ("signoff_corners", "copy_exclude", "allowed_overrides")
    return PnrConfig(**{k: tuple(v) if k in tuples else v for k, v in data.items()})
