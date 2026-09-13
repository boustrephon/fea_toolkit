"""Command-line entry point for the ``fea_toolkit`` package.

Run as::

    python -m fea_toolkit                      # lazy listing of the public API
    python -m fea_toolkit plot                 # filter by name / substring / glob
    python -m fea_toolkit plot_mesh --details  # signatures and docstrings
    python -m fea_toolkit plot_mesh --source   # definition source, still lazy
    python -m fea_toolkit --version            # toolkit version, then exit

The default listing is deliberately *lazy*: it reads the declared
``__all__`` / ``_LAZY_IMPORTS`` metadata and classifies each name by parsing
source with :mod:`ast`, so it never imports ``openseespy`` or ``pyvista``.
``--details`` opts into importing each name for exact signatures and
docstrings; ``--source`` stays lazy by reading the definition from the module
source instead.
"""

import argparse
import ast
import fnmatch
import inspect
import sys
from pathlib import Path
from typing import NamedTuple, Optional

import fea_toolkit

# --- Lazy introspection helpers -------------------------------------

# Facades such as ``fea_toolkit.plotting.viz`` re-export names defined in
# sibling modules; follow the import chain this far before giving up.
_MAX_REEXPORT_DEPTH = 4

_UNKNOWN = "unknown"
_VALUE = "value"
_WILDCARD_CHARS = "*?["


class _ModuleInfo(NamedTuple):
    """Parsed view of one module source file."""

    kinds: dict[str, str]
    reexports: dict[str, str]
    nodes: dict[str, ast.AST]


class _Resolved(NamedTuple):
    """Where a public name is defined, resolved without importing it."""

    kind: str
    module: str
    path: Optional[Path] = None
    node: Optional[ast.AST] = None


def _package_root() -> Path:
    """Return the on-disk directory of the installed ``fea_toolkit`` package."""
    return Path(fea_toolkit.__file__).resolve().parent


def _module_path(dotted: str) -> Optional[Path]:
    """Map a dotted module name to its source file under ``fea_toolkit``.

    Args:
        dotted: Fully-qualified module name, e.g. ``"fea_toolkit.plotting.viz"``.

    Returns:
        The path to the module's ``.py`` file, or ``None`` when it is not a
        source file inside the package (external or C-extension module).
    """
    parts = dotted.split(".")
    if not parts or parts[0] != fea_toolkit.__name__:
        return None
    base = _package_root().joinpath(*parts[1:])
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _dotted_name(path: Path) -> str:
    """Return the dotted module name for a source file inside the package."""
    parts = list(path.relative_to(_package_root()).parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][:-3]
    return ".".join([fea_toolkit.__name__, *parts])


def _resolve_import(path: Path, current: str, node: ast.ImportFrom) -> Optional[str]:
    """Return the absolute dotted module targeted by an ``ImportFrom`` node."""
    if node.level == 0:
        return node.module
    parts = current.split(".")
    extra = 1 if path.name == "__init__.py" else 0
    keep = len(parts) - (node.level - extra)
    base_parts = parts[:keep] if keep > 0 else []
    if node.module:
        base_parts = base_parts + node.module.split(".")
    return ".".join(base_parts) if base_parts else None


def _scan_module(path: Path) -> _ModuleInfo:
    """Classify top-level definitions and imports in one module source file.

    Args:
        path: Source file to parse.

    Returns:
        A :class:`_ModuleInfo` holding the top-level definition names, their
        kinds (``"class"`` / ``"function"``), the module each imported name
        is re-exported from, and the defining :mod:`ast` nodes.
    """
    kinds: dict[str, str] = {}
    reexports: dict[str, str] = {}
    nodes: dict[str, ast.AST] = {}
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return _ModuleInfo(kinds, reexports, nodes)
    current = _dotted_name(path)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            kinds.setdefault(node.name, "class")
            nodes.setdefault(node.name, node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kinds.setdefault(node.name, "function")
            nodes.setdefault(node.name, node)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    nodes.setdefault(target.id, node)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                nodes.setdefault(node.target.id, node)
        elif isinstance(node, ast.ImportFrom):
            target = _resolve_import(path, current, node)
            if target:
                for alias in node.names:
                    if alias.name != "*":
                        reexports.setdefault(alias.asname or alias.name, target)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                reexports.setdefault(alias.asname or alias.name.split(".")[0], alias.name)
    return _ModuleInfo(kinds, reexports, nodes)


_MODULE_CACHE: dict[Path, _ModuleInfo] = {}


def _scan(path: Path) -> _ModuleInfo:
    """Return the cached :func:`_scan_module` result for ``path``."""
    if path not in _MODULE_CACHE:
        _MODULE_CACHE[path] = _scan_module(path)
    return _MODULE_CACHE[path]


def _resolve(dotted: str, name: str) -> _Resolved:
    """Resolve a public name to its definition without importing the module.

    Args:
        dotted: Module the name is exported from (the ``_LAZY_IMPORTS`` value
            for lazy names, or ``__module__`` for eager ones).
        name: Public name to resolve.

    Returns:
        A :class:`_Resolved` with the kind, defining module, source path and
        :mod:`ast` node; ``kind`` is ``"unknown"`` when nothing was found.
    """
    current = dotted
    seen: set[str] = set()
    for _ in range(_MAX_REEXPORT_DEPTH):
        if current in seen:
            break
        seen.add(current)
        path = _module_path(current)
        if path is None:
            break
        info = _scan(path)
        if name in info.nodes:
            return _Resolved(info.kinds.get(name, _VALUE), current, path, info.nodes[name])
        target = info.reexports.get(name)
        if target is None:
            break
        current = target
    return _Resolved(_UNKNOWN, dotted)


def _resolve_name(name: str) -> _Resolved:
    """Resolve one public name lazily, importing only already-cheap names."""
    dotted = fea_toolkit._LAZY_IMPORTS.get(name)
    if dotted is not None:
        return _resolve(dotted, name)
    obj = getattr(fea_toolkit, name)
    module = getattr(obj, "__module__", None) or fea_toolkit.__name__
    resolved = _resolve(module, name)
    if resolved.kind != _UNKNOWN:
        return resolved
    if inspect.isclass(obj):
        kind = "class"
    elif callable(obj):
        kind = "function"
    else:
        kind = _VALUE
    return _Resolved(kind, module)


def _rows_for(names: list[str]) -> list[tuple[str, str, str]]:
    """Return ``(name, kind, module)`` rows for ``names`` (lazy resolution)."""
    rows: list[tuple[str, str, str]] = []
    for name in names:
        resolved = _resolve_name(name)
        rows.append((name, resolved.kind, resolved.module))
    return rows


def describe_public_api() -> list[tuple[str, str, str]]:
    """Return ``(name, kind, module)`` rows for the package-root public API.

    Lazily exported names are classified by reading source with :mod:`ast`
    rather than importing them, so no heavy backend (``openseespy`` /
    ``pyvista``) is loaded.

    Returns:
        Sorted rows, one per name in :data:`fea_toolkit.__all__`.
    """
    return _rows_for(sorted(fea_toolkit.__all__))


def _select_names(pattern: Optional[str]) -> list[str]:
    """Return the public names matching ``pattern``.

    Args:
        pattern: ``None`` for every name; otherwise an exact name, a
            substring, or a glob (``*`` / ``?`` / ``[]``). Matching is
            case-insensitive, and bare substrings match anywhere in the name.

    Returns:
        Sorted list of matching public names (empty when nothing matches).
    """
    names = sorted(fea_toolkit.__all__)
    if pattern is None:
        return names
    needle = pattern.lower()
    if not any(char in pattern for char in _WILDCARD_CHARS):
        needle = f"*{needle}*"
    return [name for name in names if fnmatch.fnmatchcase(name.lower(), needle)]


def _definition_source(resolved: _Resolved) -> str:
    """Return the source text of a resolved definition (decorators included)."""
    if resolved.path is None or resolved.node is None:
        return ""
    lines = resolved.path.read_text(encoding="utf-8").splitlines()
    start = getattr(resolved.node, "lineno", 1)
    for decorator in getattr(resolved.node, "decorator_list", ()):
        start = min(start, getattr(decorator, "lineno", start))
    end = getattr(resolved.node, "end_lineno", start)
    return "\n".join(lines[start - 1 : end])


def _format_source(names: list[str]) -> str:
    """Render the ``--source`` listing: each definition's source text."""
    blocks: list[str] = []
    for name in names:
        resolved = _resolve_name(name)
        header = f"=== {name} [{resolved.kind}] ({resolved.module}) ==="
        source = _definition_source(resolved)
        text = f"{header}\n{source}" if source else f"{header}\n(no source definition found)"
        blocks.append(text)
    return "\n\n".join(blocks)


def _format_table(rows: list[tuple[str, str, str]]) -> str:
    """Render the lazy listing as an aligned ``name / kind / module`` table."""
    name_w = max(len(name) for name, _, _ in rows)
    kind_w = max(len(kind) for _, kind, _ in rows)
    lines = [
        f"{'NAME':<{name_w}}  {'KIND':<{kind_w}}  MODULE",
        f"{'-' * name_w}  {'-' * kind_w}  {'-' * 24}",
    ]
    for name, kind, module in rows:
        lines.append(f"{name:<{name_w}}  {kind:<{kind_w}}  {module}")
    return "\n".join(lines)


def _format_details(rows: list[tuple[str, str, str]]) -> str:
    """Render the ``--details`` listing with signatures and docstrings."""
    blocks: list[str] = []
    for name, kind, module in rows:
        obj = getattr(fea_toolkit, name)
        block = [f"{name}  [{kind}]", f"    module:    {module}"]
        if callable(obj):
            try:
                signature = str(inspect.signature(obj))
            except (TypeError, ValueError):
                signature = "(...)"
            block.append(f"    signature: {name}{signature}")
        doc = inspect.getdoc(obj)
        if doc:
            block.append(f"    doc:       {doc.strip().splitlines()[0]}")
        blocks.append("\n".join(block))
    return "\n\n".join(blocks)


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point for ``python -m fea_toolkit``.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code: ``0`` on success; ``2`` when ``name`` matches no
        public name (argparse usage errors also exit ``2``).
    """
    parser = argparse.ArgumentParser(
        prog="python -m fea_toolkit",
        description=(
            "List the public API exported by the fea_toolkit package root, "
            "optionally filtered by name. The default listing is lazy and "
            "loads no OpenSeesPy or PyVista."
        ),
    )
    parser.add_argument(
        "name",
        nargs="?",
        default=None,
        help=(
            "Optional filter: an exact name, a substring, or a glob "
            "(e.g. 'plot_mesh', 'plot', 'plot_*')."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--details",
        action="store_true",
        help="Show signatures and docstrings (imports the packages, about 1 s).",
    )
    mode.add_argument(
        "--source",
        action="store_true",
        help="Show the definition source (stays lazy; imports nothing).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"fea_toolkit {fea_toolkit.__version__}",
        help="Show the fea_toolkit version and exit.",
    )
    args = parser.parse_args(argv)

    if args.source and args.name is None:
        parser.error("--source requires a NAME")

    names = _select_names(args.name)
    if not names:
        print(f"error: no public name matches {args.name!r}", file=sys.stderr)
        return 2

    if args.source:
        print(_format_source(names))
        return 0

    label = "public name" if len(names) == 1 else "public names"
    print(f"fea_toolkit {fea_toolkit.__version__} - {len(names)} {label}")
    print()
    rows = _rows_for(names)
    if args.details:
        print(_format_details(rows))
    else:
        print(_format_table(rows))
        print()
        print("Tip: pass --details for signatures, or --source for the definition.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
