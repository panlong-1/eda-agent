"""The layers depend one way only (README "Layout"). Scans the imports of every module, including
imports inside functions, so a new module cannot quietly break the direction.

    core      nothing else of eda_agent
    plugins   core: the public plug-in interface (docs/plugins.md), as low as core
    tools     core, plugins (the import tool and the flow adapters use the plug-in interface)
    server    core, tools
    agent     core; never tools (the agent reaches tools only through MCP)
    onboard   core
    cli, review_ui   anything (the top)

One import goes against this on purpose, listed in EXCEPTIONS with the reason; the test also
fails when an exception is no longer needed, so the list stays true.

Plug-ins (examples/, and every installed plug-in) use only the public plug-in interface:
eda_agent.plugins.* and, for flow adapters, eda_agent.tools.backends (docs/plugins.md).
"""
import ast
from pathlib import Path

ROOT = Path(__file__).parents[2]
PKG = ROOT / "src" / "eda_agent"

ALLOWED = {
    "core": {"core"},
    "plugins": {"core", "plugins"},
    "tools": {"core", "plugins", "tools"},
    "server": {"core", "tools", "server"},
    "agent": {"core", "agent"},
    "onboard": {"core", "onboard"},
}
TOP = {"cli", "cli_flow", "cli_plugins", "review_ui", "__init__"}  # cli_*: old paths, re-exports of cli/
PUBLIC = ("eda_agent.plugins", "eda_agent.tools.backends")
EXCEPTIONS = {
    # plugins.load_adapters() puts installed adapters into the backend registry, which lives with the
    # Backend base class in tools/backends (itself part of the public plug-in interface). Moving
    # either would change the public interface the private plug-in package builds on.
    ("src/eda_agent/plugins/__init__.py", "eda_agent.tools.backends"),
}


def _module(path: Path) -> str:
    parts = list(path.relative_to(PKG.parent).with_suffix("").parts)
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def imports(path: Path, package: str) -> list[tuple[int, str]]:
    """(line, absolute module) of every import in the file, relative ones resolved."""
    out = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out += [(node.lineno, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - node.level + 1]
                mod = ".".join(base + ([node.module] if node.module else []))
            else:
                mod = node.module or ""
            # "from . import x" names submodules
            out += [(node.lineno, f"{mod}.{a.name}" if not node.module else mod) for a in node.names]
    return out


def _layer(module: str) -> str:
    return module.split(".")[1] if module.count(".") else "__init__"


def _crossings() -> list[tuple[str, int, str, str]]:
    """(file, line, layer, imported module) for every import of another layer it may not use."""
    out = []
    for f in sorted(PKG.rglob("*.py")):
        mod = _module(f)
        package = mod if f.name == "__init__.py" else mod.rsplit(".", 1)[0]
        layer = _layer(mod)
        if layer in TOP:
            continue
        for line, target in imports(f, package):
            if target.startswith("eda_agent.") and _layer(target) not in ALLOWED[layer]:
                out.append((str(f.relative_to(ROOT)), line, layer, target))
    return out


def test_layers_depend_one_way():
    bad = [f"{f}:{line} ({layer}) imports {t}" for f, line, layer, t in _crossings()
           if (f, t) not in EXCEPTIONS]
    assert not bad, "layer violations:\n" + "\n".join(bad)


def test_every_exception_is_still_needed():
    found = {(f, t) for f, _, _, t in _crossings()}
    assert EXCEPTIONS <= found, f"no longer needed, remove from EXCEPTIONS: {EXCEPTIONS - found}"


def test_every_layer_is_known():
    layers = {_layer(_module(f)) for f in PKG.rglob("*.py")}
    assert layers <= set(ALLOWED) | TOP, layers - set(ALLOWED) - TOP


def test_plugins_use_only_the_public_interface():
    bad = []
    for f in sorted((ROOT / "examples").rglob("*.py")):
        for line, target in imports(f, ""):
            if target.startswith("eda_agent") and not target.startswith(PUBLIC):
                bad.append(f"{f.relative_to(ROOT)}:{line} imports {target}")
    assert not bad, "plug-ins must use only eda_agent.plugins / eda_agent.tools.backends:\n" + "\n".join(bad)


def test_the_scanner_sees_relative_and_inner_imports(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("from ..tools import lint\n\ndef g():\n    from ...core.envelope import ToolResult\n")
    found = [t for _, t in imports(f, "eda_agent.agent.receipt")]
    assert found == ["eda_agent.agent.tools", "eda_agent.core.envelope"]


def test_a_violation_is_caught(tmp_path, monkeypatch):
    """The check itself works: an agent module that imports tools is reported."""
    fake = tmp_path / "src" / "eda_agent"
    (fake / "agent").mkdir(parents=True)
    (fake / "agent" / "x.py").write_text("from ..tools.lint import run_lint\n")
    monkeypatch.setitem(globals(), "PKG", fake)
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    assert [(c[2], c[3]) for c in _crossings()] == [("agent", "eda_agent.tools.lint")]
