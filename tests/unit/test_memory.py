"""Plan §0.16 memory: the append-only lessons store, retrieval ranking, documents, the review page."""
from pathlib import Path

import pytest

from eda_agent.core.memory import (LessonStore, doc_sections, hints_block, known_ids, lesson_stores, load_memory,
                                   search)

SRC = [{"run_id": "r_1", "sha256": "ab" * 32}]


def test_store_is_append_only_and_folds_status(tmp_path):
    st = LessonStore(tmp_path, "torv")
    a = st.add("Booth multiplier cuts cell area by 8.43% at 7 ns", sources=SRC, scope={"tool": "synth"})
    b = st.add("ABC buffering helps WNS at 5 ns", sources=SRC, status="verified")
    assert (a["id"], b["id"]) == ("lesson-torv-001", "lesson-torv-002")
    r = st.add("Booth gives no area gain with slang 9.x", kind="refute", refers_to=a["id"], sources=SRC)
    v = st.add("", kind="void", refers_to=b["id"], by="pan", reason="measured on the wrong RTL")
    lines_before = st.file.read_text().splitlines()
    now = st.current()
    assert now[a["id"]]["status"] == "refuted" and now[a["id"]]["refuted_by"] == r["id"]
    assert now[b["id"]]["status"] == "void" and now[b["id"]]["void"]["reason"] == "measured on the wrong RTL"
    assert now[r["id"]]["status"] == "hypothesis" and v["kind"] == "void"
    # nothing was rewritten: four lines, the first two unchanged
    assert len(lines_before) == 4 and '"status": "hypothesis"' in lines_before[0]


def test_store_refuses_unsourced_unreasoned_and_dangling(tmp_path):
    st = LessonStore(tmp_path, "torv")
    with pytest.raises(ValueError, match="source"):
        st.add("no source", sources=[])
    x = st.add("ok", sources=SRC)
    with pytest.raises(ValueError, match="reason"):
        st.add("", kind="void", refers_to=x["id"])
    with pytest.raises(ValueError, match="existing entry"):
        st.add("why", kind="refute", refers_to="lesson-torv-099", sources=SRC)
    with pytest.raises(ValueError, match="refuted"):
        st.add("x", status="refuted", sources=SRC)
    with pytest.raises(ValueError, match="scope"):
        st.add("x", sources=SRC, scope={"colour": "red"})
    st.add("", kind="verify", refers_to=x["id"], sources=SRC)
    assert st.current()[x["id"]]["status"] == "verified"


def test_retrieval_ranks_same_design_verified_and_scope_first(tmp_path):
    here, other = LessonStore(tmp_path, "torv"), LessonStore(tmp_path, "picorv32")
    h1 = here.add("muldiv stall cases fail when the divider is busy", sources=SRC)
    h2 = here.add("muldiv stall cases pass after the divider fix", sources=SRC, status="verified",
                  scope={"module": "torv_muldiv_unit"})
    o1 = other.add("muldiv stall cases are flaky in picorv32", sources=SRC, status="verified")
    gone = here.add("muldiv stall needs 50000 cycles", sources=SRC)
    here.add("", kind="void", refers_to=gone["id"], reason="wrong", by="p")
    stores = lesson_stores(tmp_path, "torv")
    assert [s.design for s in stores] == ["torv", "picorv32"]
    hits = search("muldiv stall torv_muldiv_unit", design="torv", stores=stores, lessons=5)
    ids = [h.id for h in hits]
    assert ids[0] == h2["id"]                   # verified + module scope named in the query
    assert gone["id"] not in ids                # voided entries are never offered
    assert ids.index(h1["id"]) < ids.index(o1["id"])  # this design before another design
    foreign = next(h for h in hits if h.id == o1["id"])
    assert foreign.status == "hypothesis" and foreign.note == "from picorv32, not verified here"
    assert search("the and for", design="torv", stores=stores) == []
    block = hints_block(hits)
    assert "NOT evidence" in block and f"[{h2['id']}] (verified)" in block and "source: run_id r_1" in block


def test_documents_split_by_heading_and_are_cited_by_section(tmp_path):
    doc = tmp_path / "spec.md"
    doc.write_text("intro line\n\n# 9. Hazard handling\nload-use stalls one cycle\n\n## 9.1 Forwarding\n"
                   "EX/MEM to EX\n\n# 9. Hazard handling\nagain\n")
    secs = doc_sections("torv-spec", doc)
    assert [s["id"] for s in secs] == ["doc:torv-spec#start", "doc:torv-spec#9-hazard-handling",
                                       "doc:torv-spec#9-1-forwarding", "doc:torv-spec#9-hazard-handling-2"]
    assert secs[1]["line"] == 3 and secs[1]["sha256"]
    hits = search("load-use stall", design="torv", stores=[], docs=secs, doc_hints=2)
    assert hits[0].id == "doc:torv-spec#9-hazard-handling" and hits[0].kind == "doc"
    assert known_ids([], secs)["docs"][0].startswith("doc:torv-spec#")
    txt = tmp_path / "pdk.txt"
    txt.write_text("first para\nmore\n\nsecond para\n")
    assert [s["section"] for s in doc_sections("pdk", txt)] == ["p1", "p2"]


def test_memory_config():
    m = load_memory({"docs": ["spec/x.md", {"id": "flow", "path": "docs/f.md"}], "compact_after_turns": 3})
    assert [(d.id, d.path) for d in m.docs] == [("x", "spec/x.md"), ("flow", "docs/f.md")]
    assert m.compact_after_turns == 3 and m.compact_at == 0.6
    with pytest.raises(ValueError):
        load_memory({"compact_at": 0})
    with pytest.raises(ValueError):
        load_memory({"window": 1})


def test_review_page_lists_searches_and_voids(tmp_path):
    from eda_agent.review_ui import ReviewApp
    runs = tmp_path / "runs"
    runs.mkdir()
    st = LessonStore(runs / "experience", "torv")
    a = st.add("Booth multiplier cuts area", sources=SRC)
    st.add("ABC buffering helps WNS", sources=SRC)
    app = ReviewApp(runs, token="tok", by="pan")
    page = app.lessons_page("booth")
    assert a["id"] in page and "ABC buffering" not in page
    msg, ok = app.void_lesson({"id": [a["id"]], "design": ["torv"], "reason": [""]})
    assert not ok and "reason" in msg
    msg, ok = app.void_lesson({"id": [a["id"]], "design": ["torv"], "reason": ["wrong run"]})
    assert ok and st.current()[a["id"]]["status"] == "void"
    assert st.entries()[-1]["by"] == "pan (via review-ui)"
    assert not app.void_lesson({"id": [a["id"]], "design": ["../x"], "reason": ["r"]})[1]
    assert Path(st.file).read_text().count("\n") == 3
