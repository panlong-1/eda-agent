"""File references in a receipt: where they point, and whether the review page may show them.

A receipt names files as absolute WSL paths (artifacts, the SDC), as paths relative to the
project root (`rtl/torv_soc.sv:179` in a finding or a lint diagnostic, a case's source), or as
`runs/...` relative to the eda-agent checkout (the trace). `Sources.resolve` turns each into a
path; `Sources.read` gives the text the review page shows, under these rules:

- only files under the run's project root or the runs/ folder, or a file the receipt itself
  lists (an artifact, the SDC, the trace), after following symlinks;
- never a dotfile with secrets (.bashrc, .env, ...), anything under .git, a key or a binary;
- never a file whose text contains something shaped like an API key (`sk-...`): refused.

The static .html shows references as plain text; only the review page links them (to itself).
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

DENY_NAMES = {".bashrc", ".bash_history", ".profile", ".zshrc", ".env", ".netrc", ".pypirc", ".git-credentials",
              "id_rsa", "id_ed25519", "credentials", "config.toml"}
DENY_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
SECRET = re.compile(r"sk-[A-Za-z0-9_-]{20,}")
REF = re.compile(r"^(?P<path>[^\s:]+?)(?::(?P<line>\d+))?$")
MAX_BYTES = 8 * 1024 * 1024
CONTEXT = 40       # lines around the target line
TAIL = 400         # lines shown of a long file opened without a line


class Refused(ValueError):
    pass


def project_roots(dirs) -> dict[str, Path]:
    """name -> root of every project config (*.toml with name and root) in the given folders."""
    roots: dict[str, Path] = {}
    for d in dirs:
        for f in sorted(Path(d).glob("*.toml")) if d and Path(d).is_dir() else []:
            try:
                data = tomllib.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data.get("name"), str) and isinstance(data.get("root"), str):
                roots.setdefault(data["name"], Path(data["root"]).expanduser())
    return roots


def split_ref(ref: str) -> tuple[str, int | None]:
    m = REF.match(ref.strip())
    if not m:
        return ref.strip(), None
    return m["path"], int(m["line"]) if m["line"] else None


@dataclass
class Text:
    path: Path
    lines: list[str]
    first: int          # number of lines[0] in the file
    total: int
    line: int | None    # the target line, if any


class Sources:
    def __init__(self, runs_dir: Path, roots: dict[str, Path] | None = None):
        self.runs_dir = runs_dir
        self.roots = roots or {}
        self._by_name: dict[tuple[Path, str], Path | None] = {}

    def root_of(self, r: dict) -> Path | None:
        root = r.get("project_root") or self.roots.get(r.get("project", ""))
        return Path(root).expanduser() if root else None

    def resolve(self, r: dict, ref: str) -> tuple[Path | None, int | None]:
        """The path a reference names (not checked), and its line."""
        path, line = split_ref(ref)
        p = Path(path).expanduser()
        if p.is_absolute():
            return p, line
        if path.startswith("runs/"):
            return self.runs_dir.parent / path, line
        root = self.root_of(r)
        if not root:
            return None, line
        if "/" not in path and not (root / path).is_file():  # a bare name in a finding: apb_uart.sv
            return self._unique(root, path) or root / path, line
        return root / path, line

    def _unique(self, root: Path, name: str) -> Path | None:
        """The project's only file of that name (not under .git), else None."""
        key = (root, name)
        if key not in self._by_name:
            hits = []
            for p in root.rglob(name):
                if ".git" not in p.parts and p.is_file():
                    hits.append(p)
                    if len(hits) > 1:
                        break
            self._by_name[key] = hits[0] if len(hits) == 1 else None
        return self._by_name[key]

    def listed(self, r: dict) -> set[Path]:
        """Files the receipt itself lists: artifacts, SDCs, the trace."""
        out = set()
        for c in r.get("checks_run", []):
            out |= {Path(a["path"]) for a in c.get("artifacts", []) if a.get("path")}
        if r.get("trace"):
            p, _ = self.resolve(r, r["trace"])
            if p:
                out.add(p)
        return {p.resolve() for p in out if p.is_absolute()}

    def exists(self, r: dict, ref: str) -> bool:
        p, _ = self.resolve(r, ref)
        return bool(p) and p.is_file()

    def check(self, r: dict, path: Path) -> Path:
        """The real path if the review page may show it, else Refused (with the reason)."""
        try:
            real = path.resolve(strict=True)
        except (OSError, RuntimeError):
            raise Refused("file not found")
        if not real.is_file():
            raise Refused("not a file")
        parts = set(real.parts)
        if real.name in DENY_NAMES or real.suffix in DENY_SUFFIXES or ".git" in parts or ".ssh" in parts:
            raise Refused("this kind of file is never shown")
        bases = [b.resolve() for b in (self.root_of(r), self.runs_dir) if b and b.exists()]
        if not (any(real.is_relative_to(b) for b in bases) or real in self.listed(r)):
            raise Refused("outside the project and the runs folder")
        return real

    def read(self, r: dict, ref: str, whole: bool = False) -> Text:
        path, line = self.resolve(r, ref)
        if not path:
            raise Refused("no project root known for this run")
        real = self.check(r, path)
        if real.stat().st_size > MAX_BYTES:
            raise Refused("file too large to show")
        data = real.read_bytes()
        if b"\0" in data[:8192]:
            raise Refused("binary file")
        text = data.decode("utf-8", "replace")
        if SECRET.search(text):
            raise Refused("the file contains something that looks like an API key; not shown")
        lines = text.splitlines()
        total = len(lines)
        if whole:
            return Text(real, lines, 1, total, line)
        if line:
            lo = max(1, line - CONTEXT)
            return Text(real, lines[lo - 1: line + CONTEXT], lo, total, line)
        if total > TAIL:
            return Text(real, lines[-TAIL:], total - TAIL + 1, total, None)
        return Text(real, lines, 1, total, None)
