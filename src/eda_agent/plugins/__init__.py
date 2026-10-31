"""Plug-ins: importers of reports made elsewhere, and flow adapters (docs/plugins.md).

A plug-in is a small, separate Python package installed into the project's .venv. It declares
entry points in its pyproject.toml:

    [project.entry-points."eda_agent.importers"]
    mytool = "my_pkg.importer:MyImporter"      # class or instance with name, detect(), parse()
    [project.entry-points."eda_agent.adapters"]
    myflow = "my_pkg.adapter:MyFlow"           # a tools.backends.Backend subclass

Importers only parse. The core validates what they return (plugins/result.py), recomputes file
hashes, and decides status, evidence level and claim scope by category (core/categories.py).
"""
from __future__ import annotations

from importlib.metadata import entry_points

IMPORTER_GROUP = "eda_agent.importers"
ADAPTER_GROUP = "eda_agent.adapters"

_IMPORTERS: dict[str, object] = {}
_LOADED = {"importers": False, "adapters": False}
LOAD_ERRORS: list[str] = []


def _instance(obj):
    return obj() if isinstance(obj, type) else obj


def register_importer(importer) -> None:
    imp = _instance(importer)
    _IMPORTERS[imp.name] = imp


def importers() -> dict[str, object]:
    """Installed importers by name (entry points, loaded once) plus any registered in-process."""
    if not _LOADED["importers"]:
        for ep in entry_points(group=IMPORTER_GROUP):
            try:
                imp = _instance(ep.load())
                _IMPORTERS.setdefault(getattr(imp, "name", ep.name), imp)
            except Exception as e:  # a broken plug-in must not break the core
                LOAD_ERRORS.append(f"importer {ep.name}: {e}")
        _LOADED["importers"] = True
    return dict(_IMPORTERS)


def load_adapters() -> list[str]:
    """Register flow adapters from entry points; returns their names."""
    from ..tools.backends import register_backend
    names = []
    if not _LOADED["adapters"]:
        for ep in entry_points(group=ADAPTER_GROUP):
            try:
                be = _instance(ep.load())
                register_backend(be)
                names.append(be.name)
            except Exception as e:
                LOAD_ERRORS.append(f"adapter {ep.name}: {e}")
        _LOADED["adapters"] = True
    return names


def choose(path, name: str | None = None):
    """The importer for a report directory: the named one, or the single one whose detect() claims it.
    Returns (importer, None) or (None, reason)."""
    imps = importers()
    if name:
        return (imps[name], None) if name in imps else (None, f"no importer {name!r} (installed: {', '.join(sorted(imps)) or 'none'})")
    scores = []
    for n, imp in imps.items():
        try:
            s = float(imp.detect(path) or 0)
        except Exception as e:
            LOAD_ERRORS.append(f"importer {n} detect failed: {e}")
            s = 0.0
        if s > 0:
            scores.append((s, n))
    if not scores:
        return None, f"no installed importer recognizes {path} (installed: {', '.join(sorted(imps)) or 'none'})"
    scores.sort(reverse=True)
    if len(scores) > 1 and scores[0][0] == scores[1][0]:
        return None, f"importers {scores[0][1]} and {scores[1][1]} both claim {path}: choose one with --as"
    return imps[scores[0][1]], None
