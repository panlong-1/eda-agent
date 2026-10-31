import os

import pytest

from eda_agent.agent.receipt import render_files
from eda_agent.agent.receipt_view import render_html, static_linker
from eda_agent.agent.sources import Refused, Sources, project_roots
from eda_agent.review_ui import ReviewApp


@pytest.fixture
def tree(tmp_path):
    """A project (with an RTL file, a key-shaped string and a .env), a runs/ folder and an artifact
    outside both, listed by the receipt."""
    root = tmp_path / "design"
    (root / "rtl").mkdir(parents=True)
    (root / "rtl" / "top.sv").write_text("".join(f"line {n}\n" for n in range(1, 301)))
    (root / "rtl" / "leak.sv").write_text("// key sk-" + "a" * 32 + "\n")
    (root / ".env").write_text("X=1\n")
    (root / "blob.bin").write_bytes(b"\0\1\2")
    repo = tmp_path / "eda-agent"
    runs = repo / "runs"
    runs.mkdir(parents=True)
    (runs / "r1.jsonl").write_text('{"kind": "start"}\n')
    (repo / "projects").mkdir()
    (repo / "projects" / "p.toml").write_text(f'name = "p"\nroot = "{root}"\n')
    other = tmp_path / "flow"
    other.mkdir()
    (other / "sta.log").write_text("".join(f"sta {n}\n" for n in range(1, 1001)))
    (other / "secret.txt").write_text("not listed\n")
    (tmp_path / "outside.txt").write_text("nope\n")
    os.symlink(tmp_path / "outside.txt", root / "rtl" / "link.sv")
    r = {"run_id": "r1", "project": "p", "trace": "runs/r1.jsonl",
         "checks_run": [{"tool": "synth", "artifacts": [{"path": str(other / "sta.log"), "sha256": "x"}]}]}
    return tmp_path, runs, r


def test_resolve_and_read_with_context(tree):
    tmp, runs, r = tree
    src = Sources(runs, project_roots([runs.parent / "projects"]))
    assert src.resolve(r, "rtl/top.sv:179") == (tmp / "design" / "rtl" / "top.sv", 179)
    assert src.resolve(r, "runs/r1.jsonl")[0] == runs / "r1.jsonl"
    assert src.resolve(r, "top.sv:5") == (tmp / "design" / "rtl" / "top.sv", 5)  # a bare, unique name
    t = src.read(r, "rtl/top.sv:179")
    assert (t.first, t.line, t.total, len(t.lines)) == (139, 179, 300, 81) and t.lines[40] == "line 179"
    assert len(src.read(r, "rtl/top.sv", whole=True).lines) == 300
    log = src.read(r, str(tmp / "flow" / "sta.log"))  # listed artifact outside the project: allowed
    assert log.first == 601 and log.lines[-1] == "sta 1000"  # a long file opens at its tail
    assert src.read(r, "runs/r1.jsonl").lines == ['{"kind": "start"}']


@pytest.mark.parametrize("ref, reason", [
    ("rtl/leak.sv", "API key"), (".env", "never shown"), ("blob.bin", "binary"),
    ("rtl/link.sv", "outside"), ("../outside.txt", "outside"), ("rtl/../../outside.txt", "outside"),
    ("FLOW/secret.txt", "outside"), ("rtl/missing.sv:3", "not found"), ("/etc/hostname", "outside")])
def test_refused(tree, ref, reason):
    tmp, runs, r = tree
    src = Sources(runs, project_roots([runs.parent / "projects"]))
    ref = ref.replace("FLOW", str(tmp / "flow"))
    with pytest.raises(Refused, match=reason):
        src.read(r, ref)


def test_static_page_shows_references_as_text(tree):
    tmp, runs, r = tree
    src = Sources(runs, project_roots([runs.parent / "projects"]))
    link = static_linker(r, src)
    assert link("rtl/top.sv:179") == f'<code class="ref" title="{tmp}/design/rtl/top.sv">rtl/top.sv:179</code>'
    assert "(file not found)" in link("rtl/missing.sv:3")


def test_review_page_links_and_shows_source(tree):
    tmp, runs, r = tree
    app = ReviewApp(runs, token="tok", by="T")
    link = app._linker(r)
    html = link("rtl/top.sv:179")
    assert html.startswith('<a class="ref" href="/source?t=tok&amp;run=r1&amp;ref=rtl/top.sv%3A179#L179">')
    assert "(file not found)" in link("rtl/missing.sv")
    import json
    (runs / "r1.receipt.json").write_text(json.dumps({**r, "gate": {}, "status": "done"}))
    code, page = app.source_page("r1", "rtl/top.sv:179")
    assert code == 200 and '<div class="ln hit" id="L179"><span class="no">179</span>' in page
    assert "show whole file" in page and "read only" in page
    code, page = app.source_page("r1", "rtl/leak.sv")
    assert code == 403 and "line" not in page.split("Not shown")[1][:20] and "sk-" not in page
    assert app.source_page("r1", "rtl/nope.sv")[0] == 404


def test_render_files_finds_the_project_next_to_runs(tree, tmp_path):
    tmp, runs, r = tree
    full = {**r, "task": "t", "provider": "x", "model": "m", "prompt": "", "stop_reason": "end", "status": "done",
            "turns": 1, "tool_calls": 0, "tool_errors": 0, "usage": {}, "wall_s": 0,
            "gate": {"status": "passed", "rewrites": 0, "decisions": []},
            "checks_run": [{**r["checks_run"][0], "args": {}, "status": "pass", "evidence_level": "E4",
                            "summary": "s", "claim_scope": "c", "tool_versions": {"v": "1"}}],
            "not_run": {"lint_run": True, "described_but_not_run": [], "cases_not_run": []},
            "final_claims": "", "residual_risk": {"report": "", "from_tools": []}, "case_sources": {"a": "rtl/top.sv"},
            "reviews": [{"reviewer": "rules", "model": "", "verdict": "accept", "context": "", "provider": "",
                         "checklist": [], "findings": [{"severity": "medium", "text": "x at rtl/top.sv:5"}]}]}
    _, page = render_files(full, tmp_path / "out", base=runs)
    text = page.read_text()
    assert 'title="' + str(tmp / "design" / "rtl" / "top.sv") + '">rtl/top.sv:5</code>' in text
    assert "Cases the run read" in text and "<a " not in render_html(full)
