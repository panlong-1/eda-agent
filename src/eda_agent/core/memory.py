"""Memory: a cited lessons store per design and the documents a project names, with retrieval
(plan §0.16). Memory is a hint, never evidence: what a run concludes still needs its own tool
results (gate R1); a report that cites a lesson or a document must cite ids that exist and were
retrieved in this run (gate R11).

Lessons live next to the design's experience tables (§0.15), one JSON line each, append-only:

    <[pnr] experience_dir>/<design>/lessons.jsonl

Each entry: id (lesson-<design>-NNN), kind, text, scope (design, module, tool, pdk, corner,
problem), status (verified / hypothesis / refuted), sources (run ids, receipt hashes, file
hashes), who and when. Nothing is ever changed in place:

    lesson   a lesson                               refute  a new lesson that refutes an older one
    verify   marks an older hypothesis verified     void    a person voids an entry (human gate)

so an entry's status now is its own status folded with the later entries that point to it.
Lessons of other designs are offered only as hypotheses, marked "from <design>, not verified here".

Retrieval is SQLite FTS5 (the standard library's sqlite3, in this process, no service): a fresh
in-memory index over the lessons and the project's documents (markdown split at headings, text at
blank lines), ranked by bm25 plus small boosts for the same design, a verified status and a scope
the query names. A document hit is cited as doc:<doc id>#<section>.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .hashing import sha256_file

LESSONS_FILE = "lessons.jsonl"
KINDS = ("lesson", "refute", "verify", "void")
STATUSES = ("verified", "hypothesis", "refuted")
SCOPE_KEYS = ("design", "module", "tool", "pdk", "corner", "problem")
# lesson-<design>-<number>; the design may contain dashes (non-greedy up to the last -digits)
LESSON_ID = re.compile(r"\blesson-([a-z0-9_.]+(?:-[a-z0-9_.]+)*?)-(\d{3,})\b", re.I)
# doc:<doc id>#<section>; the section is required when a report cites a document
DOC_REF = re.compile(r"\bdoc:([A-Za-z0-9_.-]*[A-Za-z0-9_])(?:#([A-Za-z0-9_.-]*[A-Za-z0-9_]))?")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_STOP = set("""the and for with that this from are was were has have had not but all any can its into
only than then them they which when what where who will would should could their there these those
run runs use used using also each per one two out does did done more most less some such about after
before over under your you our task please report say said""".split())
MAX_HINT_CHARS = 600


@dataclass(frozen=True)
class DocSpec:
    id: str
    path: str  # relative to the project root unless absolute


@dataclass(frozen=True)
class MemoryConfig:
    """[memory] in the project config; every key may be left out."""
    docs: tuple[DocSpec, ...] = ()
    lessons: int = 5              # lessons put into the context at run start
    doc_hints: int = 3            # document sections put into the context at run start
    compact_at: float = 0.6       # compact when a turn's input tokens pass this share of the window
    context_window: int = 128000  # tokens; the model's window (DeepSeek: 128k)
    compact_after_turns: int = 0  # also compact every N turns (0: only by tokens)
    keep_turns: int = 2           # the newest turns kept verbatim when compacting

    def __post_init__(self) -> None:
        if not 0 < self.compact_at <= 1:
            raise ValueError("[memory] compact_at must be in (0, 1]")
        if self.keep_turns < 1:
            raise ValueError("[memory] keep_turns must be at least 1")


def load_memory(data: dict | None) -> MemoryConfig:
    data = dict(data or {})
    unknown = set(data) - set(MemoryConfig.__dataclass_fields__)
    if unknown:
        raise ValueError(f"unknown [memory] key(s) {sorted(unknown)}")
    docs = []
    for d in data.pop("docs", []):
        if isinstance(d, str):
            d = {"id": Path(d).stem.lower(), "path": d}
        docs.append(DocSpec(id=str(d["id"]), path=str(d["path"])))
    return MemoryConfig(docs=tuple(docs), **data)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def experience_base(project) -> Path:
    """The experience root (same place as the experience tables and standards)."""
    return Path(project.pnr.experience_dir if getattr(project, "pnr", None) else "runs/experience")


# ---------------------------------------------------------------- the lessons store

class LessonStore:
    """One design's lessons: an append-only JSON-lines file."""

    def __init__(self, base: Path, design: str):
        self.design = design
        self.file = Path(base) / design / LESSONS_FILE

    @classmethod
    def for_project(cls, project) -> "LessonStore":
        return cls(experience_base(project), project.name)

    def entries(self) -> list[dict]:
        if not self.file.is_file():
            return []
        return [json.loads(ln) for ln in self.file.read_text(encoding="utf-8").splitlines() if ln.strip()]

    def add(self, text: str, *, kind: str = "lesson", status: str = "hypothesis", scope: dict | None = None,
            sources: list[dict] | None = None, refers_to: str | None = None, by: str = "", origin: str = "",
            reason: str = "") -> dict:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        if kind in ("lesson", "refute") and status not in STATUSES[:2]:
            raise ValueError("a new lesson is verified or a hypothesis; 'refuted' comes from a refute entry")
        if kind in ("lesson", "refute") and not (text or "").strip():
            raise ValueError("a lesson needs text")
        if kind in ("lesson", "refute") and not sources:
            raise ValueError("a lesson needs at least one source (run id, receipt hash or file hash)")
        if kind in ("refute", "verify", "void"):
            if not refers_to or refers_to not in {e["id"] for e in self.entries()}:
                raise ValueError(f"{kind} must point to an existing entry of {self.design} (got {refers_to!r})")
        if kind == "void" and not reason.strip():
            raise ValueError("voiding an entry needs a reason (a person's decision, recorded)")
        bad = set(scope or {}) - set(SCOPE_KEYS)
        if bad:
            raise ValueError(f"unknown scope key(s) {sorted(bad)}; known: {SCOPE_KEYS}")
        existing = self.entries()
        entry = {"id": f"lesson-{self.design}-{len(existing) + 1:03d}", "design": self.design, "kind": kind,
                 "text": text.strip(), "scope": {"design": self.design, **(scope or {})},
                 "status": status if kind in ("lesson", "refute") else None,
                 "sources": list(sources or []), "refers_to": refers_to, "by": by, "origin": origin,
                 "reason": reason, "at": _now()}
        self.file.parent.mkdir(parents=True, exist_ok=True)
        with self.file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def current(self) -> dict[str, dict]:
        """Every entry by id, lessons with their status now (folded with later entries)."""
        out: dict[str, dict] = {}
        for e in self.entries():
            out[e["id"]] = {**e, "history": []}
        for e in self.entries():
            target = out.get(e.get("refers_to") or "")
            if not target:
                continue
            target["history"].append(e["id"])
            if e["kind"] == "void":
                target["status"], target["void"] = "void", {"by": e["by"], "at": e["at"], "reason": e["reason"],
                                                            "entry": e["id"]}
            elif target.get("status") == "void":
                continue
            elif e["kind"] == "refute":
                target["status"], target["refuted_by"] = "refuted", e["id"]
            elif e["kind"] == "verify" and target.get("status") == "hypothesis":
                target["status"], target["verified_by"] = "verified", e["id"]
        return out


def lesson_stores(base: Path, design: str) -> list[LessonStore]:
    """This design's store first, then every other design under the same experience root."""
    base = Path(base)
    others = sorted(d.name for d in base.iterdir() if d.is_dir() and d.name != design
                    and (d / LESSONS_FILE).is_file()) if base.is_dir() else []
    return [LessonStore(base, design)] + [LessonStore(base, d) for d in others]


def source_text(sources: list[dict]) -> str:
    """Sources in one line: run ids, receipts and files with short hashes."""
    parts = []
    for s in sources or []:
        bits = [f"{k} {s[k]}" for k in ("run_id", "result_id", "receipt", "file", "note") if s.get(k)]
        bits += [f"{k[:-7] or 'sha256'} {s[k][:12]}" for k in ("sha256", "receipt_sha256") if s.get(k)]
        parts.append(", ".join(bits))
    return "; ".join(p for p in parts if p)


# ---------------------------------------------------------------- documents

def _slug(heading: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", heading.lower())[:6]
    return "-".join(words) or "section"


def doc_sections(doc_id: str, path: Path) -> list[dict]:
    """A document split into sections: markdown at headings (# .. ####), other text at blank lines."""
    text = path.read_text(encoding="utf-8", errors="replace")
    sha = sha256_file(path)
    lines = text.splitlines()
    out: list[dict] = []
    if path.suffix.lower() in (".md", ".markdown"):
        starts = [i for i, ln in enumerate(lines) if re.match(r"#{1,4}\s+\S", ln)]
        if not starts or starts[0] != 0:
            starts = [0] + starts
        bounds = list(zip(starts, starts[1:] + [len(lines)]))
        heads = [lines[a].lstrip("#").strip() if re.match(r"#{1,4}\s", lines[a]) else "(start)" for a, _ in bounds]
    else:
        bounds, a = [], 0
        for i, ln in enumerate(lines + [""]):
            if not ln.strip():
                if i > a:
                    bounds.append((a, i))
                a = i + 1
        heads = [f"paragraph {n + 1}" for n in range(len(bounds))]
    seen: dict[str, int] = {}
    for (a, b), head in zip(bounds, heads):
        body = "\n".join(lines[a:b]).strip()
        if not body:
            continue
        slug = _slug(head) if path.suffix.lower() in (".md", ".markdown") else f"p{len(out) + 1}"
        seen[slug] = seen.get(slug, 0) + 1
        if seen[slug] > 1:
            slug = f"{slug}-{seen[slug]}"
        out.append({"doc": doc_id, "section": slug, "heading": head, "text": body, "path": str(path),
                    "line": a + 1, "sha256": sha, "id": f"doc:{doc_id}#{slug}"})
    return out


def project_docs(project) -> list[dict]:
    """Every section of every document the project's [memory] docs name (missing files are skipped)."""
    mem = getattr(project, "memory", None)
    out = []
    for d in mem.docs if mem else ():
        p = Path(d.path).expanduser()
        p = p if p.is_absolute() else Path(project.root) / p
        if p.is_file():
            out += doc_sections(d.id, p)
    return out


# ---------------------------------------------------------------- retrieval

@dataclass
class Hit:
    kind: str          # lesson | doc
    id: str            # lesson-torv-003 | doc:torv-spec#hazard-unit
    text: str
    status: str        # lessons: verified / hypothesis (others' lessons: hypothesis); docs: "source"
    source: str        # where it came from, in one line
    score: float
    design: str = ""
    note: str = ""     # e.g. "from picorv32, not verified here"
    scope: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("kind", "id", "text", "status", "source", "score", "design",
                                              "note", "scope")}


def query_terms(text: str) -> list[str]:
    words = [w.lower() for w in _WORD.findall(text or "")]
    return list(dict.fromkeys(w for w in words if w not in _STOP))


def search(query: str, *, design: str, stores: list[LessonStore], docs: list[dict] = (),
           lessons: int = 5, doc_hints: int = 3) -> list[Hit]:
    """The best lessons (current ones: no refuted or void entry) and document sections for a query.

    Ranking: bm25 over text and scope, plus 1.0 for this design, 0.5 for a verified lesson and 0.5
    for each scope value (module, tool, pdk, corner, problem) the query names. Lessons of other designs
    come back as hypotheses with a note."""
    # the design name matches almost every lesson of the design; it is a boost below, not a search term
    terms = [t for t in query_terms(query) if t != design.lower()]
    if not terms:
        return []
    db = sqlite3.connect(":memory:")
    db.execute("CREATE VIRTUAL TABLE fts USING fts5(ref UNINDEXED, body, scope)")
    rows: dict[str, tuple[str, dict]] = {}
    for st in stores:
        for e in st.current().values():
            if e["kind"] not in ("lesson", "refute") or e["status"] not in ("verified", "hypothesis"):
                continue
            scope_txt = " ".join(str(v) for v in (e.get("scope") or {}).values() if v)
            rows[e["id"]] = ("lesson", e)
            db.execute("INSERT INTO fts VALUES (?, ?, ?)", (e["id"], e["text"], scope_txt))
    for d in docs:
        rows[d["id"]] = ("doc", d)
        db.execute("INSERT INTO fts VALUES (?, ?, ?)", (d["id"], d["heading"] + "\n" + d["text"], d["doc"]))
    match = " OR ".join(f'"{t}"' for t in terms)
    found = db.execute("SELECT ref, bm25(fts) FROM fts WHERE fts MATCH ?", (match,)).fetchall()
    db.close()
    qset = set(terms)
    hits: list[Hit] = []
    for ref, bm in found:
        kind, e = rows[ref]
        score = -bm
        if kind == "lesson":
            same = e["design"] == design
            score += (1.0 if same else 0.0) + (0.5 if e["status"] == "verified" and same else 0.0)
            for k in SCOPE_KEYS[1:]:
                v = str((e.get("scope") or {}).get(k) or "").lower()
                if v and (v in qset or any(t in qset for t in query_terms(v))):
                    score += 0.5
            hits.append(Hit("lesson", e["id"], e["text"], e["status"] if same else "hypothesis",
                            source_text(e.get("sources") or []), round(score, 4), e["design"],
                            "" if same else f"from {e['design']}, not verified here", e.get("scope") or {}))
        else:
            hits.append(Hit("doc", e["id"], e["text"][:MAX_HINT_CHARS], "source",
                            f"{e['path']}:{e['line']} sha256 {(e['sha256'] or '')[:12]}", round(score, 4),
                            note=e["heading"]))
    hits.sort(key=lambda h: (-h.score, h.id))
    return ([h for h in hits if h.kind == "lesson"][:lessons] + [h for h in hits if h.kind == "doc"][:doc_hints])


def known_ids(stores: list[LessonStore], docs: list[dict]) -> dict:
    """Every lesson id with its status now, and every document section id: what R11 checks against."""
    lessons = {i: e.get("status") or e["kind"] for st in stores for i, e in st.current().items()}
    return {"lessons": lessons, "docs": sorted({d["id"] for d in docs}), "doc_ids": sorted({d["doc"] for d in docs})}


def hints_block(hits: list[Hit]) -> str:
    """The hints as they go into the context: each with its id, status and source."""
    if not hits:
        return ""
    out = ["## Hints from memory (NOT evidence)",
           "Lessons from earlier runs and sections of the project's documents, retrieved for this task. "
           "They are hints: a conclusion still needs a tool result of this run. When you use one, cite its "
           "id exactly (lesson-<design>-NNN, or doc:<doc>#<section>) and mark it as a hint or source, e.g. "
           "'(hint: lesson-torv-003)'. Never cite an id that is not listed here or returned by "
           "search_memory. End the report, before the residual risk, with a section '## Lessons (hypotheses)': "
           "1 to 3 short bullet points a later run on this design could use (a fact this run measured, with the "
           "tool and case or number it rests on); leave it out only if the run learned nothing new.", ""]
    for h in hits:
        tag = h.status + (f"; {h.note}" if h.note else "")
        out.append(f"- [{h.id}] ({tag}) {' '.join(h.text.split())[:MAX_HINT_CHARS]}  — source: {h.source}")
    return "\n".join(out)
