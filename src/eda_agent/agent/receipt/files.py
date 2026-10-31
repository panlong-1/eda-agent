"""Writing a receipt: its .json (with `levels` brought up to date) and both pages, for every kind.

    write_receipt(receipt, out_dir)   <id>.receipt.json + .md + .html
    render_files(receipt, out_dir)    (re-)write .md and .html only; the .json is not touched
"""
from __future__ import annotations

import json
from pathlib import Path

from ..levels import levels_record
from ..sources import Sources, project_roots
from .data import receipt_id, receipt_kind, with_trace_data
from .markdown import render_markdown
from .page import render_html, static_linker

IMPORT_RECEIPTS = Path("runs/imports")


def _dump(receipt: dict) -> str:
    """Import receipts keep the compact form the imported table uses; the others are indented."""
    if receipt_kind(receipt) == "import":
        return json.dumps(receipt, indent=1) + "\n"
    return json.dumps(receipt, indent=2, ensure_ascii=False) + "\n"


def write_receipt(receipt: dict, out_dir: Path, base: Path | None = None) -> tuple[Path, Path]:
    """runs/<id>.receipt.json (with its `levels` brought up to date), plus the rendered .md and
    .html next to it. `base`: where the receipt's trace may be found (default out_dir)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    js = out_dir / f"{receipt_id(receipt)}.receipt.json"
    receipt["levels"] = levels_record(with_trace_data(receipt, base or out_dir))
    js.write_text(_dump(receipt), encoding="utf-8")
    md, _ = render_files(receipt, out_dir, base=base)
    return js, md


def write_import_receipt(rec: dict, out_dir: Path = IMPORT_RECEIPTS) -> tuple[Path, Path]:
    """The receipt of one import (runs/imports/<design>_<id>.receipt.*)."""
    return write_receipt(rec, out_dir)


def render_files(receipt: dict, out_dir: Path, *, base: Path | None = None) -> tuple[Path, Path]:
    """(Re-)write runs/<id>.receipt.md and .html from the receipt data; the .json is not
    touched. `base`: where the receipt's trace may be found (default out_dir)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    view = with_trace_data(receipt, base or out_dir)
    name = f"{receipt_id(receipt)}.receipt"
    md, page = out_dir / f"{name}.md", out_dir / f"{name}.html"
    md.write_text(render_markdown(view), encoding="utf-8")
    runs_dir = base or out_dir
    sources = Sources(runs_dir, project_roots([runs_dir.parent / "projects"]))
    page.write_text(render_html(view, link=static_linker(view, sources)), encoding="utf-8")
    return md, page
