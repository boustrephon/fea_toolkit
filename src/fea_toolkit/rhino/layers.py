"""
Rhino layer management for SAP2000 model export.

Creates a hierarchical layer structure under a ``SAP2000`` root layer,
with separate sub-trees for each pipeline stage (``SAP`` and ``Mesh``)
so geometry from the two stages never overlays in the same layers:

.. code::

    SAP2000
    ├── SAP
    │   ├── Joints
    │   ├── Frames
    │   │   ├── Centreline
    │   │   │   ├── {SectionName_1}
    │   │   │   ├── {SectionName_2}
    │   │   │   └── ...
    │   │   └── Extrusion
    │   │       ├── {SectionName_1}
    │   │       ├── {SectionName_2}
    │   │       └── ...
    │   └── Shells
    │       ├── Centreline
    │       │   ├── {SectionName_1}
    │       │   ├── {SectionName_2}
    │       │   └── ...
    │       └── Extrusion
    │           ├── {SectionName_1}
    │           ├── {SectionName_2}
    │           └── ...
    └── Mesh
        ├── Joints
        ├── Frames
        │   ├── Centreline ...
        │   └── Extrusion ...
        └── Shells ...

The root name is configurable via ``root_name`` (default ``SAP2000``);
the importer derives ``SAP2000/SAP`` / ``SAP2000/Mesh`` from the stage.
"""

import typing as t
from contextlib import contextmanager

from .colors import FRAME_PALETTE, SHELL_PALETTE, get_sap2000_color, safe_str


@contextmanager
def suppress_redraw():
    """Temporarily disable viewport redraws during bulk document changes.

    Rhino invalidates/redraws viewports after every ``Add*`` and attribute
    commit; with thousands of objects (frame/shell geometry, result flags,
    deformed overlays) that per-object redraw dominates runtime.  Wrapping
    a batch in this context manager is the canonical McNeel-documented
    speed-up (``doc.Views.RedrawEnabled = False``): the document change
    notifications are skipped until the batch completes.

    Redraw is restored and a single ``Views.Redraw()`` is issued on exit,
    in a ``try/finally`` so a crash mid-batch never leaves Rhino's
    viewport frozen.

    Example::

        with suppress_redraw():
            for ... in range(1000):
                doc.Objects.AddExtrusion(ext, attrs)
        # one viewport refresh happens here
    """
    import scriptcontext as sc

    doc = sc.doc
    was_enabled = bool(doc.Views.RedrawEnabled)
    doc.Views.RedrawEnabled = False
    try:
        yield
    finally:
        doc.Views.RedrawEnabled = was_enabled
        doc.Views.Redraw()


def _ensure_rhino():
    """Lazy-import Rhino modules — only works inside the Rhino process."""
    try:
        import Rhino  # noqa: F401
        import Rhino.DocObjects as rd
        import scriptcontext as sc

        return sc, rd
    except ImportError:
        raise RuntimeError(
            "Rhino modules are not available. This code must run inside Rhinoceros 3D (IronPython)."
        ) from None


# ── Layer name utilities ─────────────────────────────────────────────────


def sanitize_layer_name(name: str) -> str:
    """Sanitise a string for use as a Rhino layer name.

    Replaces characters that Rhino disallows in layer names with underscores.
    Returns ``"Unnamed"`` if the result would be empty.
    """
    name = safe_str(name)
    for char in ["/", "\\", ":", "*", "?", '"', "<", ">", "|", "."]:
        name = name.replace(char, "_")
    if not name:
        return "Unnamed"
    if len(name) > 40:
        name = name[:37] + "..."
    return name


def _find_layer(layer_table, path: str) -> int:
    """Find a layer index by full path, tolerant of the Rhino separator.

    Rhino exposes ``Layer.FullPath`` with a ``::`` separator while the
    toolkit API uses ``/`` (``LayerTable.Find`` matches the native form,
    so a ``/``-path misses multi-segment layers).  Try both forms, then
    fall back to a manual scan over the table.
    """
    idx = layer_table.Find(path, True)
    if idx >= 0:
        return idx
    if "/" in path:
        idx = layer_table.Find(path.replace("/", "::"), True)
        if idx >= 0:
            return idx
    target = path.replace("::", "/")
    for i in range(layer_table.Count):
        layer = layer_table[i]
        if layer is not None and layer.FullPath.replace("::", "/") == target:
            return i
    return -1


def create_or_get_layer(
    layer_name: str, parent_layer_index: t.Optional[int] = None, color=None
) -> int:
    """Find or create a Rhino layer.

    Nested paths (``'SAP2000/SAP/Frames'``) create any missing ancestor
    layers along the way.  When *parent_layer_index* is given, only the
    final segment is created beneath that parent (the ancestors are
    assumed to exist); when it is ``None``, the full ancestor chain is
    created as a nested root tree.

    Args:
        layer_name: Full layer name (e.g. ``'SAP2000/SAP/Frames/UB300'``).
        parent_layer_index: Index of the parent layer, or ``None`` for root.
        color: ``System.Drawing.Color`` for the layer.

    Returns:
        The layer index in the Rhino document's layer table.
    """
    sc, rd = _ensure_rhino()
    doc = sc.doc
    layer_table = doc.Layers

    # Check if layer already exists
    layer_index = _find_layer(layer_table, layer_name)
    if layer_index >= 0:
        return layer_index

    if parent_layer_index is not None:
        # Only the final segment is new — create it beneath the parent.
        new_layer = rd.Layer()
        new_layer.Name = layer_name.rsplit("/", maxsplit=1)[-1]
        if color is not None:
            new_layer.Color = color
        parent_layer = layer_table[parent_layer_index]
        new_layer.ParentLayerId = parent_layer.Id
        return layer_table.Add(new_layer)

    # Root-level nested path — create the ancestor chain first.
    parts = layer_name.split("/")
    parent_index: t.Optional[int] = None
    for i, part in enumerate(parts):
        path = "/".join(parts[: i + 1])
        idx = _find_layer(layer_table, path)
        if idx < 0:
            new_layer = rd.Layer()
            new_layer.Name = part
            if color is not None and i == len(parts) - 1:
                new_layer.Color = color
            if parent_index is not None:
                new_layer.ParentLayerId = layer_table[parent_index].Id
            idx = layer_table.Add(new_layer)
            if idx < 0:
                # Duplicate-name rejection (e.g. when Find missed an
                # existing "::"-separated layer) — recover by re-finding.
                idx = _find_layer(layer_table, path)
                if idx < 0:
                    # Cannot recover; return the last known good ancestor.
                    return parent_index if parent_index is not None else -1
        parent_index = idx
    return parent_index


# ── Top-level layer structure ────────────────────────────────────────────


def create_root_layer(name: str = "SAP2000", parent: t.Optional[int] = None) -> int:
    """Create a root layer (default: ``SAP2000``).

    When *parent* is provided, the layer is created as a sub‑layer of that
    parent (used for the ``SAP2000/Mesh/Meshed`` tree).  Nested *name*
    paths (e.g. ``'SAP2000/SAP'``) create the ancestor chain as needed.

    Args:
        name: Layer name (default ``'SAP2000'``).
        parent: Optional parent layer index.

    Returns:
        Layer index of the created layer.
    """
    try:
        from System.Drawing import Color
    except ImportError:
        Color = None
    root_color = Color.LightGray if Color else None
    return create_or_get_layer(name, parent_layer_index=parent, color=root_color)


def create_joints_layer(root_layer_index: int, root_name: str = "SAP2000") -> int:
    """Create the ``{root_name}/Joints`` sub-layer.

    Args:
        root_layer_index: Index of the ``SAP2000`` root layer.
        root_name: Full path of the root layer (default ``SAP2000``).

    Returns:
        Layer index of the Joints layer.
    """
    return create_or_get_layer(f"{root_name}/Joints", parent_layer_index=root_layer_index)


# ── Helper: build section sub-layers under a parent path ─────────────────


def _create_section_layers(
    parent_path: str,
    parent_index: int,
    section_names: t.Iterable[str],
    palette: list[tuple],
    section_props: dict[str, dict],
) -> dict[str, int]:
    """Create one sub-layer per section name under *parent_path*.

    Args:
        parent_path: Path prefix e.g. ``'SAP2000/Frames/Centreline'``.
        parent_index: Layer index of the parent.
        section_names: Sorted section names to create layers for.
        palette: List of ``(r, g, b)`` fallback colour tuples.
        section_props: Dict of section name → props dict (for ``Color``).

    Returns:
        Dict mapping section name → layer index.
    """
    try:
        from System.Drawing import Color
    except ImportError:
        Color = None

    layers: dict[str, int] = {}

    for color_index, sec_name in enumerate(section_names):
        props = section_props.get(sec_name, {})
        color_value = props.get("Color", "") if props else ""
        default_rgb = palette[color_index % len(palette)]
        default_color = Color.FromArgb(*default_rgb) if Color else None
        color = get_sap2000_color(color_value, default_color)

        safe_name = sanitize_layer_name(sec_name)
        layer_name = f"{parent_path}/{safe_name}"
        idx = create_or_get_layer(layer_name, parent_layer_index=parent_index, color=color)
        layers[sec_name] = idx

    if not layers:
        # Fallback default layer
        layer_name = f"{parent_path}/Default"
        idx = create_or_get_layer(layer_name, parent_layer_index=parent_index)
        layers["Default"] = idx

    return layers


# ── Frame layer tree ─────────────────────────────────────────────────────


class FrameLayerSet:
    """Container for frame centreline and extrusion layer indices.

    Attributes:
        centreline: Dict mapping section name → centreline layer index.
        extrusion:   Dict mapping section name → extrusion layer index.
    """

    def __init__(self, centreline: dict[str, int], extrusion: dict[str, int]):
        self.centreline = centreline
        self.extrusion = extrusion


def create_frame_layers(
    root_layer_index: int,
    frame_sections: dict[str, dict],
    prefix: str = "",
    root_name: str = "SAP2000",
) -> FrameLayerSet:
    """Create the frame layer tree.

    Layout::

        {root_name}/Frames/Centreline/{Section}
        {root_name}/Frames/Extrusion/{Section}

    When *prefix* is ``'Meshed/'`` and *root_name* is ``'SAP2000/SAP'``
    the layers become::

        SAP2000/SAP/Meshed/Frames/Centreline/{Section}

    Args:
        root_layer_index: Index of the root layer.
        frame_sections: Dict of ``{section_name: props_dict}``.
        prefix: Optional path prefix (e.g. ``'Meshed/'``).
        root_name: Full path of the root layer (default ``SAP2000``).

    Returns:
        A :class:`FrameLayerSet` with centreline and extrusion dicts.
    """
    base = root_name + ("/" + prefix.rstrip("/") if prefix else "")
    frames_parent = create_or_get_layer(f"{base}/Frames", parent_layer_index=root_layer_index)

    cl_parent = create_or_get_layer(f"{base}/Frames/Centreline", parent_layer_index=frames_parent)
    cl_layers = _create_section_layers(
        f"{base}/Frames/Centreline",
        cl_parent,
        sorted(frame_sections) if frame_sections else [],
        FRAME_PALETTE,
        frame_sections,
    )

    ex_parent = create_or_get_layer(f"{base}/Frames/Extrusion", parent_layer_index=frames_parent)
    ex_layers = _create_section_layers(
        f"{base}/Frames/Extrusion",
        ex_parent,
        sorted(frame_sections) if frame_sections else [],
        FRAME_PALETTE,
        frame_sections,
    )

    return FrameLayerSet(centreline=cl_layers, extrusion=ex_layers)


# ── Shell layer tree ─────────────────────────────────────────────────────


class ShellLayerSet:
    """Container for shell centreline and extrusion layer indices.

    Attributes:
        centreline: Dict mapping section name → centreline layer index.
        extrusion:   Dict mapping section name → extrusion layer index.
    """

    def __init__(self, centreline: dict[str, int], extrusion: dict[str, int]):
        self.centreline = centreline
        self.extrusion = extrusion


def create_shell_layers(
    root_layer_index: int,
    shell_sections: dict[str, dict],
    prefix: str = "",
    root_name: str = "SAP2000",
) -> ShellLayerSet:
    """Create the shell layer tree.

    Layout::

        {root_name}/Shells/Centreline/{Section}
        {root_name}/Shells/Extrusion/{Section}

    When *prefix* is ``'Meshed/'`` and *root_name* is ``'SAP2000/SAP'``
    the layers become::

        SAP2000/SAP/Meshed/Shells/Centreline/{Section}

    Args:
        root_layer_index: Index of the root layer.
        shell_sections: Dict of ``{section_name: props_dict}``.
        prefix: Optional path prefix (e.g. ``'Meshed/'``).
        root_name: Full path of the root layer (default ``SAP2000``).

    Returns:
        A :class:`ShellLayerSet` with centreline and extrusion dicts.
    """
    base = root_name + ("/" + prefix.rstrip("/") if prefix else "")
    shells_parent = create_or_get_layer(f"{base}/Shells", parent_layer_index=root_layer_index)

    cl_parent = create_or_get_layer(f"{base}/Shells/Centreline", parent_layer_index=shells_parent)
    cl_layers = _create_section_layers(
        f"{base}/Shells/Centreline",
        cl_parent,
        sorted(shell_sections) if shell_sections else [],
        SHELL_PALETTE,
        shell_sections,
    )

    ex_parent = create_or_get_layer(f"{base}/Shells/Extrusion", parent_layer_index=shells_parent)
    ex_layers = _create_section_layers(
        f"{base}/Shells/Extrusion",
        ex_parent,
        sorted(shell_sections) if shell_sections else [],
        SHELL_PALETTE,
        shell_sections,
    )

    return ShellLayerSet(centreline=cl_layers, extrusion=ex_layers)
