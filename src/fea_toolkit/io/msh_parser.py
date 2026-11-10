"""Parse Gmsh ``.msh`` ASCII mesh files (format 4.x) into a small data model.

A Gmsh ``.msh`` file stores a *discretised* mesh — nodes, elements and their
physical groups — not the original CAD/NURBS geometry.  This parser reads the
core sections (``$MeshFormat``, ``$PhysicalNames``, ``$Entities``, ``$Nodes``,
``$Elements``) and returns an :class:`MSHMesh` that a consumer (e.g. the Rhino
importer) can turn into geometry.

It is pure Python and imports nothing from the Rhino or OpenSees packages, so
it is safe to import inside Rhino 8's embedded interpreter and to unit-test
headlessly.

Only the ASCII (``filetype == 0``) 4.x layout is supported.  The legacy 2.2
layout and the binary variants raise :class:`ValueError`.

Note on group resolution: in MSH 4.x, elements belong to an *entity*
(dimension + tag) and physical-group membership is recorded on that entity in
``$Entities``.  Elements are linked to their physical group(s) through this
mapping; a mesh written without ``$Entities`` therefore yields ungrouped
elements.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Union

# Gmsh element type -> (name, node count).  Covers the linear and second-order
# types a structural surface mesh is likely to contain; anything else raises a
# clear error because the connectivity length cannot be determined otherwise.
_ELEMENT_TYPES: dict[int, tuple[str, int]] = {
    1: ("line", 2),
    2: ("triangle", 3),
    3: ("quadrangle", 4),
    4: ("tetrahedron", 4),
    5: ("hexahedron", 8),
    6: ("prism", 6),
    7: ("pyramid", 5),
    8: ("line3", 3),
    9: ("triangle6", 6),
    10: ("quadrangle9", 9),
    11: ("tetrahedron10", 10),
    12: ("hexahedron27", 27),
    13: ("prism18", 18),
    14: ("pyramid14", 14),
    15: ("point", 1),
    16: ("quadrangle8", 8),
}


@dataclass
class MSHElement:
    """One element from the ``$Elements`` section.

    Attributes:
        dim: Topological dimension of the entity block the element belongs to
            (0 = point, 1 = line, 2 = surface, 3 = volume).
        tag: Element tag.
        etype: Gmsh element type code (see :data:`_ELEMENT_TYPES`).
        node_tags: Node tags the element connects, in Gmsh order.
        physical_tags: Physical tags assigned to the element's entity; empty
            when the entity belongs to no physical group.
    """

    dim: int
    tag: int
    etype: int
    node_tags: tuple[int, ...]
    physical_tags: tuple[int, ...] = ()

    @property
    def name(self) -> str:
        """Element kind name (``quadrangle``, ``triangle``, ...)."""
        return _ELEMENT_TYPES.get(self.etype, ("unknown", 0))[0]


@dataclass
class MSHMesh:
    """A parsed Gmsh mesh.

    Attributes:
        version: ``$MeshFormat`` version string (e.g. ``"4.1"``).
        nodes: Node tag -> ``(x, y, z)`` coordinates.
        elements: All elements in file order.
        physical_names: Physical tag -> group name.
        points: 0D entity tag -> ``(x, y, z)``.  These are the CAD points,
            which also appear as nodes; useful for a lightweight point import.
    """

    version: str
    nodes: dict[int, tuple[float, float, float]] = field(default_factory=dict)
    elements: list[MSHElement] = field(default_factory=list)
    physical_names: dict[int, str] = field(default_factory=dict)
    points: dict[int, tuple[float, float, float]] = field(default_factory=dict)

    def group_elements(self) -> dict[str, list[MSHElement]]:
        """Partition the elements by physical-group name.

        Elements carrying several physical tags appear in each of their groups;
        elements with none are collected under ``"Ungrouped"``.

        Returns:
            Mapping of group name -> elements (in file order).
        """
        groups: dict[str, list[MSHElement]] = {}
        for elem in self.elements:
            names = [self.physical_names.get(ptag, f"group_{ptag}") for ptag in elem.physical_tags]
            if not names:
                names = ["Ungrouped"]
            for name in names:
                groups.setdefault(name, []).append(elem)
        return groups


# ── Low-level helpers ─────────────────────────────────────────────────────


def _split_tokens(line: str) -> list[str]:
    """Split a whitespace-delimited line, honouring double-quoted fields."""
    tokens: list[str] = []
    buf: list[str] = []
    in_quote = False
    for ch in line:
        if ch == '"':
            in_quote = not in_quote
        elif ch.isspace() and not in_quote:
            if buf:
                tokens.append("".join(buf))
                buf = []
        else:
            buf.append(ch)
    if buf:
        tokens.append("".join(buf))
    return tokens


def _first_tokens(lines: list[str]) -> list[str]:
    """First non-empty, tokenised line of *lines* (or ``[]``)."""
    for line in lines:
        tokens = _split_tokens(line)
        if tokens:
            return tokens
    return []


def _sections(text: str) -> dict[str, list[str]]:
    """Split the raw file into ``{section_name: [lines]}``."""
    sections: dict[str, list[str]] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("$") and not stripped.startswith("$End"):
            name = stripped[1:].strip()
            body: list[str] = []
            i += 1
            while i < len(lines) and lines[i].strip() != f"$End{name}":
                body.append(lines[i])
                i += 1
            sections[name] = body
            i += 1  # skip the $End… line
        else:
            i += 1
    return sections


# ── Section parsers ───────────────────────────────────────────────────────


def _parse_physical_names(lines: list[str]) -> dict[int, str]:
    """Parse ``$PhysicalNames`` into ``{physical_tag: name}``."""
    if not lines:
        return {}
    header = _first_tokens(lines)
    count = int(header[0]) if header else 0
    names: dict[int, str] = {}
    for line in lines[1 : 1 + count]:
        parts = _split_tokens(line)
        if len(parts) >= 3:
            names[int(parts[1])] = parts[2]
    return names


def _parse_entities(
    lines: list[str],
) -> tuple[dict[tuple[int, int], tuple[int, ...]], dict[int, tuple[float, float, float]]]:
    """Parse ``$Entities``.

    Returns:
        ``(entity_physical_tags, points)`` where *entity_physical_tags* maps
        ``(dim, tag)`` -> physical tags and *points* maps 0D entity tag ->
        ``(x, y, z)``.
    """
    entity_physical_tags: dict[tuple[int, int], tuple[int, ...]] = {}
    points: dict[int, tuple[float, float, float]] = {}
    if not lines:
        return entity_physical_tags, points

    counts = _first_tokens(lines)
    num_points = int(counts[0]) if len(counts) > 0 else 0
    num_curves = int(counts[1]) if len(counts) > 1 else 0
    num_surfaces = int(counts[2]) if len(counts) > 2 else 0
    num_volumes = int(counts[3]) if len(counts) > 3 else 0

    data = lines[1:]
    i = 0

    # Points: ``tag x y z numPhysicalTags [physicalTags…]``.
    for _ in range(num_points):
        if i >= len(data):
            break
        parts = _split_tokens(data[i])
        tag = int(parts[0])
        x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
        num_pt = int(parts[4]) if len(parts) > 4 else 0
        ptags = tuple(int(t) for t in parts[5 : 5 + num_pt])
        entity_physical_tags[(0, tag)] = ptags
        points[tag] = (x, y, z)
        i += 1

    # Curves / surfaces / volumes:
    # ``tag xmin ymin zmin xmax ymax zmax numPhysicalTags [physicalTags…]
    #   numBoundary [boundaryTags…]``.
    for dim, count in ((1, num_curves), (2, num_surfaces), (3, num_volumes)):
        for _ in range(count):
            if i >= len(data):
                break
            parts = _split_tokens(data[i])
            tag = int(parts[0])
            num_pt = int(parts[7]) if len(parts) > 7 else 0
            ptags = tuple(int(t) for t in parts[8 : 8 + num_pt])
            entity_physical_tags[(dim, tag)] = ptags
            i += 1

    return entity_physical_tags, points


def _parse_nodes(lines: list[str]) -> dict[int, tuple[float, float, float]]:
    """Parse ``$Nodes`` into ``{node_tag: (x, y, z)}``.

    Gmsh 4.x ASCII writes each node block as ``numNodesInBlock`` node-tag
    lines followed by ``numNodesInBlock`` coordinate lines (i.e. all tags
    first, then all coordinates), unlike the legacy 2.2 ``tag x y z`` layout.
    """
    nodes: dict[int, tuple[float, float, float]] = {}
    if not lines:
        return nodes
    i = 1  # skip "numEntityBlocks numNodes minNodeTag maxNodeTag"
    n = len(lines)
    while i < n:
        header = _split_tokens(lines[i])
        if len(header) < 4:
            break
        num_nodes = int(header[3])
        i += 1
        tags: list[int] = []
        for _ in range(num_nodes):
            if i >= n:
                return nodes
            tags.append(int(_split_tokens(lines[i])[0]))
            i += 1
        for tag in tags:
            if i >= n:
                return nodes
            coord_tokens = _split_tokens(lines[i])
            nodes[tag] = tuple(float(c) for c in coord_tokens[:3])
            i += 1
    return nodes


def _parse_elements(
    lines: list[str],
    entity_physical_tags: dict[tuple[int, int], tuple[int, ...]],
) -> list[MSHElement]:
    """Parse ``$Elements`` into a list of :class:`MSHElement`."""
    elements: list[MSHElement] = []
    if not lines:
        return elements
    i = 1  # skip "numEntityBlocks numElements minElementTag maxElementTag"
    n = len(lines)
    while i < n:
        header = _split_tokens(lines[i])
        if len(header) < 4:
            break
        dim = int(header[0])
        entity_tag = int(header[1])
        etype = int(header[2])
        num_elems = int(header[3])
        if etype not in _ELEMENT_TYPES:
            raise ValueError(
                f"unsupported Gmsh element type {etype} in entity ({dim}, {entity_tag})"
            )
        _, nnodes = _ELEMENT_TYPES[etype]
        ptags = entity_physical_tags.get((dim, entity_tag), ())
        i += 1
        for _ in range(num_elems):
            if i >= n:
                return elements
            tokens = _split_tokens(lines[i])
            tag = int(tokens[0])
            node_tags = tuple(int(t) for t in tokens[1 : 1 + nnodes])
            elements.append(MSHElement(dim, tag, etype, node_tags, ptags))
            i += 1
    return elements


# ── Public API ────────────────────────────────────────────────────────────


def parse_msh_text(text: str) -> MSHMesh:
    """Parse the contents of a Gmsh ``.msh`` ASCII file.

    Args:
        text: The full file contents as a string.

    Returns:
        The parsed :class:`MSHMesh`.

    Raises:
        ValueError: If the file is not a supported ASCII 4.x mesh (missing
            ``$MeshFormat``, a binary ``filetype``, a non-4.x version, or an
            unsupported element type).
    """
    sections = _sections(text)

    fmt = _first_tokens(sections.get("MeshFormat", []))
    if not fmt:
        raise ValueError("not a Gmsh .msh file: missing $MeshFormat section")
    version = fmt[0]
    file_type = int(fmt[1])
    if file_type != 0:
        raise ValueError(f"binary Gmsh .msh (filetype {file_type}) is not supported")
    if version.split(".")[0] != "4":
        raise ValueError(f"unsupported Gmsh .msh version {version!r}: only 4.x ASCII is supported")

    physical_names = _parse_physical_names(sections.get("PhysicalNames", []))
    entity_physical_tags, points = _parse_entities(sections.get("Entities", []))
    nodes = _parse_nodes(sections.get("Nodes", []))
    elements = _parse_elements(sections.get("Elements", []), entity_physical_tags)

    return MSHMesh(
        version=version,
        nodes=nodes,
        elements=elements,
        physical_names=physical_names,
        points=points,
    )


def read_msh(path: Union[str, Path]) -> MSHMesh:
    """Read a Gmsh ``.msh`` ASCII file from disk.

    Args:
        path: Path to the ``.msh`` file.

    Returns:
        The parsed :class:`MSHMesh`.

    Raises:
        FileNotFoundError: If *path* does not exist.
        ValueError: If the file is not a supported ASCII 4.x mesh.
        UnicodeDecodeError: If the file is not UTF-8 text.
    """
    path = Path(path)
    return parse_msh_text(path.read_text(encoding="utf-8"))


__all__ = ["MSHElement", "MSHMesh", "parse_msh_text", "read_msh"]
