"""Run receipts: what was asked, what was checked, what was not, and what was claimed.

Written at the end of every run as runs/<id>.receipt.json (for tools) and, for a human
reviewer, runs/<id>.receipt.md and runs/<id>.receipt.html. Everything in it is taken from the
run report and the tool envelopes; nothing is re-derived by a model. The .json also carries
`levels`: the Fatal / Error / Warning issues (agent/levels.py), derived from the rest.

One pipeline for every kind of receipt (agent runs, write-tests runs, imports), in three layers:

    data.py       layer 1: what the receipt records (build_receipt, testgen_receipt, trace data)
    tables.py     layer 2: the numbers as tables      summary.py: the review summary
    markdown.py   layer 3: the .md page               page.py: the .html page
    files.py      writing the .json, .md and .html    narrative.py: optional model-written summary
"""
from .data import (MAX_DIAGNOSTICS, _flow, build_receipt, receipt_id, receipt_kind,  # noqa: F401
                   testgen_receipt, with_trace_data)
from .files import render_files, write_import_receipt, write_receipt  # noqa: F401
from .markdown import render_details, render_markdown  # noqa: F401
