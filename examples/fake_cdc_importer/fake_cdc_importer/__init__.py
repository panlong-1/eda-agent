"""FAKE example importer for eda-agent's plug-in interface. Made-up format, made-up data.

It reads a toy "CDC report" that no real tool writes:

    FAKECDC REPORT v1
    tool: ToyCDC 0.0-fake
    design: <top>
    constraints: complete|incomplete
    crossing <id> <from_clock> -> <to_clock> : synchronized|UNSYNCHRONIZED [waived:<id>]
    input rtl <path>
    input constraints <path>

Use it as a template for a real importer: name, version, detect(), parse() -> neutral result,
contract_samples(); raise NotMine for anything that is not your format.
"""
from __future__ import annotations

import re
from pathlib import Path

from eda_agent.plugins.result import NotMine

REPORT = "fake_cdc.rpt"
_LINE = re.compile(r"^crossing (\S+) (\S+) -> (\S+) : (synchronized|UNSYNCHRONIZED)(?: waived:(\S+))?$")


class FakeCdcImporter:
    name = "fake_cdc"
    version = "0.1"
    categories = ("cdc",)

    def detect(self, path) -> float:
        f = Path(path) / REPORT
        return 1.0 if f.is_file() and f.read_text(errors="replace").startswith("FAKECDC REPORT v1") else 0.0

    def parse(self, path) -> dict:
        if not self.detect(path):
            raise NotMine(f"no {REPORT} starting with 'FAKECDC REPORT v1'")
        text = (Path(path) / REPORT).read_text()
        head = dict(re.findall(r"^(tool|design|constraints): (.+)$", text, re.M))
        tool_name, _, tool_version = head.get("tool", "ToyCDC ?").partition(" ")
        crossings = [m.groups() for m in map(_LINE.match, text.splitlines()) if m]
        unsync = [c for c in crossings if c[3] == "UNSYNCHRONIZED" and not c[4]]
        waivers = [{"id": c[4], "rule": f"{c[1]}->{c[2]}", "reason": "waived in the toy report"}
                   for c in crossings if c[4]]
        inputs = [{"path": p, "role": role} for role, p in re.findall(r"^input (rtl|constraints) (\S+)$", text, re.M)]
        return {"category": "cdc", "tool": {"name": tool_name, "version": tool_version},
                "design": head.get("design"),
                "metrics": {"crossings": len(crossings), "unsynchronized": len(unsync), "waivers": waivers,
                            "constraints_complete": head.get("constraints") == "complete"},
                "inputs": inputs, "outputs": [{"path": REPORT, "role": "report"}],
                "notes": ["fake example data"]}

    def contract_samples(self) -> list[Path]:
        return [Path(__file__).parent / "samples" / "clean", Path(__file__).parent / "samples" / "waived"]
