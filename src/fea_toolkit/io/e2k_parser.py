"""Parse ETABS ``.E2K`` / ``.$ET`` text files into :class:`SAPModelData`.

ETABS text exports differ structurally from SAP2000's ``.s2k`` format in two
ways this module handles up front:

* **Grammar.**  Every data line is a whitespace-separated sequence of tokens
  where an unquoted token is a *keyword*, a quoted token is a *string value*,
  and a number is a *value*.  A keyword binds to every value token that follows
  it up to the next keyword.  Quoted strings survive intact even when they
  contain spaces (e.g. ``RESTRAINT "UX UY UZ"``).

* **Deferred geometry.**  ETABS models are parametric.  ``$ POINT COORDINATES``
  gives 2-D plan points (X, Y and an optional ``DeltaZ``); ``$ LINE/AREA
  CONNECTIVITIES`` give element *templates* (type, point references and a
  per-end "drop" count of storeys); and the 3-D model only materialises when a
  point or element is *assigned* to a storey (``$ POINT/LINE/AREA ASSIGNS``).
  A node's Z coordinate is ``ABS_ELEV[storey] - DeltaZ``, and storey elevations
  are derived from the top-down ``$ STORIES`` sequence (``HEIGHT`` / ``ELEV``).

Each ``(name, storey)`` pair is one *instance* and receives a unique string id
(``name@storey``) plus an integer tag, because the same line/area/point
template is instantiated once per storey.

**Scope (M0–M4).**  This module tokenises and resolves *geometry* (nodes, frame
and area elements, their section assignments and the model unit system), the
*property* sections (materials, frame/shell sections, restraints, and diaphragm
constraints), and the *load* sections (load cases/patterns, object loads, and
load combinations).  Profile (catalogue) sections resolve through the
``data/section_dict.pkl`` catalogue when available; without it they become
``GeneralSection`` placeholders.  Wind auto-generation parameters are captured
on the load pattern/case but not expanded into element loads.

Object auto-meshing is **not** materialised yet: the ``AUTOMESH`` /
``MESHATINTERSECTIONS`` / ``FLOORMESH`` / ``MINNUMSTA`` / ``MAXSTASPC`` (line)
and ``OBJMESHTYPE`` / ``MESHAT`` / ``MAXMESHSIZE`` / ``LINECONSTRAINT`` (area)
tokens are parsed into each assignment's attribute dict but left there —
floor/line meshing (cookie-cut at beams/walls/grids, multi-node floor slabs) is
deferred pending a ``gmsh``-based remeshing path.

This module imports nothing from the ``opensees`` package, so it is safe to
import inside Rhino 8.
"""

from __future__ import annotations

import logging
import re
from itertools import accumulate
from pathlib import Path
from typing import Any, Optional, Union

from .._unit_scaling import force_unit_label, length_unit_label
from ..model.sap_data import (
    SHAPE_NAMES,
    AngleSection,
    AreaElement,
    AreaUniformLoad,
    BoxSection,
    ChannelSection,
    CircularSection,
    ConcreteCircularSection,
    ConcreteRectangularSection,
    Constraint,
    DoubleAngleSection,
    FrameDistributedLoad,
    FrameElement,
    FrameEndOffset,
    GeneralSection,
    ISection,
    JointLoad,
    LoadCase,
    LoadCombination,
    LoadCombinationEntry,
    LoadPattern,
    MassSource,
    Material,
    Node,
    PipeSection,
    RectangularSection,
    Restraint,
    SAPModelData,
    SDSection,
    Section,
    ShellSection,
    ShellUniformLoadSet,
    TeeSection,
)
from ..model.section_properties import (
    angle_props,
    box_props,
    channel_props,
    circle_props,
    double_angle_props,
    i_props,
    pipe_props,
    polygon_props,
    rect_props,
    tee_props,
)

logger = logging.getLogger(__name__)

# ── Tokeniser ───────────────────────────────────────────────────────
# A grouped line is ``[(keyword, [value, ...]), ...]``.  The leading group
# carries the object type + name (+ any positional identity values); the
# remaining groups are keyword/value attribute pairs.

Group = tuple[str, list[Any]]
GroupedLine = list[Group]


def _try_numeric(token: str) -> Any:
    """Coerce a bare token to int/float; keep quoted strings as strings.

    Quoted tokens are *always* strings (they are ETABS string values, and the
    toolkit keeps object ids as strings), so ``"1"`` stays ``"1"`` whereas a
    bare ``1`` becomes ``int``.
    """
    if token.startswith('"') and token.endswith('"'):
        return token[1:-1]
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        return token


def _is_key(token: str) -> bool:
    """Return True if *token* is a keyword (unquoted and non-numeric)."""
    if '"' in token:
        return False
    try:
        float(token)
        return False
    except ValueError:
        return True


def _line_split(line: str) -> list[str]:
    """Split an E2K line into tokens, preserving quoted strings as one token."""
    tokens: list[str] = []
    for i, chunk in enumerate(line.split('"')):
        if i % 2 == 1:
            tokens.append('"' + chunk + '"')
        else:
            tokens.extend(chunk.split())
    return tokens


def _gather(tokens: list[str]) -> GroupedLine:
    """Group tokens into ``(keyword, [values])`` pairs, left-to-right.

    Walking right-to-left, each keyword binds to the value tokens accumulated
    since the previous keyword.
    """
    groups: GroupedLine = []
    values: list[Any] = []
    for token in reversed(tokens):
        if _is_key(token):
            groups.append((token, values))
            values = []
        else:
            values.insert(0, _try_numeric(token))
    groups.reverse()
    return groups


# ── Text reading & section dispatch ─────────────────────────────────


def _read_text(path: Path) -> str:
    """Read a CSI text export, falling back across common encodings."""
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin-1", errors="replace")


def _parse_sections(content: str) -> dict[str, list[GroupedLine]]:
    """Tokenise an E2K file into ``{section_name: [grouped line, ...]}``.

    ``$``-prefixed lines delimit sections.  The ``$ File`` header and the
    trailing ``$ LOG`` … ``$ END OF MODEL FILE`` block are ignored.
    """
    sections: dict[str, list[GroupedLine]] = {}
    current: Optional[str] = None
    in_log = False
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("$"):
            name = line[1:].strip()
            upper = name.upper()
            if upper.startswith("FILE"):
                current = None
                continue
            if upper == "END OF MODEL FILE":
                break
            if upper == "LOG":
                in_log = True
                current = None
                continue
            if in_log:
                continue
            current = name
            sections.setdefault(current, [])
            continue
        if in_log or current is None:
            continue
        sections[current].append(_gather(_line_split(line)))
    return sections


# ── Composite instance ids ──────────────────────────────────────────
# ``(name, storey)`` is the identity of every node/element instance.  It is
# encoded as a single string id ``name@storey`` for the ``SAPModelData`` dicts,
# with ``@`` (and ``\\``) escaped so the id round-trips losslessly.


def _compose_id(name: str, storey: str) -> str:
    """Encode a ``(name, storey)`` instance identity as a single string id."""

    def _esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("@", "\\@")

    return f"{_esc(name)}@{_esc(storey)}"


def _split_id(composite: str) -> tuple[str, str]:
    """Inverse of :func:`_compose_id` — recover ``(name, storey)`` from an id."""
    parts: list[str] = []
    buf: list[str] = []
    i = 0
    while i < len(composite):
        ch = composite[i]
        if ch == "\\" and i + 1 < len(composite):
            buf.append(composite[i + 1])
            i += 2
            continue
        if ch == "@":
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf))
    if len(parts) != 2:
        raise ValueError(f"malformed instance id {composite!r}")
    return parts[0], parts[1]


# ── Parser ──────────────────────────────────────────────────────────


class ETABSParser:
    """Parse an ETABS ``.E2K`` / ``.$ET`` file into :class:`SAPModelData`.

    Usage::

        parser = ETABSParser("model.E2K").parse()
        model_data = parser.get_model_data()

    :meth:`parse` tokenises the file into a private section map; only
    :meth:`get_model_data` materialises the geometry (storey elevations →
    nodes → elements), mirroring the two-stage split of
    :class:`~fea_toolkit.io.s2k_parser.SAP2000Parser`.
    """

    def __init__(
        self,
        file_path: Union[str, Path],
        section_db_path: Optional[Union[str, Path]] = None,
    ) -> None:
        """Initialise the parser with a path to an ETABS text export.

        Args:
            file_path: Path to the ``.E2K`` / ``.$ET`` file.
            section_db_path: Optional path to ``section_dict.pkl`` (the
                manufacturer profile catalogue).  Profile sections resolve
                through it when present; otherwise they become
                :class:`~fea_toolkit.model.sap_data.GeneralSection`
                placeholders.  When omitted, the repo's private
                ``data/section_dict.pkl`` is probed automatically.
        """
        self.file_path = Path(file_path)
        self.section_db_path = Path(section_db_path) if section_db_path else None
        # ``None`` means "not loaded yet" (distinct from "loaded, no sections").
        self._sections: Optional[dict[str, list[GroupedLine]]] = None
        # ``None`` = catalogue not attempted; ``{}`` = attempted and unavailable.
        self._catalogues: Optional[dict[str, dict]] = None

    def parse(self) -> ETABSParser:
        """Parse the file and store the tokenised sections internally.

        Returns:
            ``self``, so the call can be chained —
            ``ETABSParser("model.E2K").parse().get_model_data()``.
        """
        content = _read_text(self.file_path)
        self._sections = _parse_sections(content)
        return self

    @property
    def sections(self) -> dict[str, list[GroupedLine]]:
        """Tokenised sections as ``{section_name: [grouped line, ...]}``."""
        return self._sections if self._sections is not None else {}

    def get_model_data(self) -> SAPModelData:
        """Resolve the parsed sections into a :class:`SAPModelData`.

        Raises:
            RuntimeError: If no data has been loaded — call :meth:`parse` first.
        """
        if self._sections is None:
            raise RuntimeError("No model data loaded — call parse() before get_model_data().")
        sections = self._sections

        units = self.get_model_units()
        length_unit = _normalise_length_unit(units["L"])
        catalogues = self._section_db()

        stories, abs_elev = _compute_story_elevations(sections)
        points = _get_points(sections)
        lines = _get_line_connectivity(sections)
        areas = _get_area_connectivity(sections)
        point_assigns = _get_assigns(sections, "POINT ASSIGNS", "POINTASSIGN")
        line_assigns = _get_assigns(sections, "LINE ASSIGNS", "LINEASSIGN")
        area_assigns = _get_assigns(sections, "AREA ASSIGNS", "AREAASSIGN")

        (
            nodes,
            frame_elements,
            area_elements,
            frame_assignments,
            area_assignments,
            restraints,
        ) = _resolve_geometry(
            stories,
            abs_elev,
            points,
            lines,
            areas,
            point_assigns,
            line_assigns,
            area_assigns,
        )

        materials = _get_materials(sections)
        all_sections: dict[str, Section] = {}
        all_sections.update(_get_shell_sections(sections))
        all_sections.update(_get_frame_sections(sections, materials, length_unit, catalogues))
        all_sections.update(_get_sd_sections(sections))
        # Null "LINE"/"AREA" objects are assigned ETABS's built-in "NONE"
        # section, which is never defined in ``$ FRAME SECTIONS``.  Synthesise
        # a zero-stiffness placeholder so those assignments resolve — they
        # carry no stiffness or mass and exist only to receive loads.
        assigned = set(frame_assignments.values()) | set(area_assignments.values())
        if "NONE" in assigned and "NONE" not in all_sections:
            all_sections["NONE"] = GeneralSection(name="NONE", shape="NA", material="")

        # ── Populate area thickness from assigned shell sections ──
        for aid, area in area_elements.items():
            sec_name = area_assignments.get(aid)
            sec = all_sections.get(sec_name) if sec_name else None
            if isinstance(sec, ShellSection):
                area.thickness = sec.thickness

        # ── Cardinal-point insertion offsets (matches S2K) ──────────
        # Built fresh (not merged) because the ETABS parser does not yet read a
        # longitudinal end-offset table; when ``$ FRAME END OFFSETS`` support is
        # added this must merge into any pre-existing offsets the way
        # ``SAP2000Parser._merge_cardinal_into_offsets`` does, preserving
        # ``end_i``/``end_j`` and ``rigid_factor``.
        frame_end_offsets = _build_frame_end_offsets(
            frame_elements, all_sections, frame_assignments
        )

        # ── Diaphragm constraints (M4) ──────────────────────────────
        diaphragm_names = _get_diaphragm_names(sections)
        constraints, constraint_assignments = _build_diaphragm_constraints(
            area_elements, point_assigns, area_assigns, diaphragm_names
        )

        # ── Loads (M3 + M7) ───────────────────────────────────────
        # Legacy ``$ STATIC LOADS`` merges the load-case and load-pattern
        # concepts; v19+ splits them into ``$ LOAD PATTERNS`` + ``$ LOAD CASES``
        # and adds ``$ MASS SOURCE``.
        load_cases, load_patterns = _get_load_cases_and_patterns(sections)
        load_patterns.update(_get_load_patterns(sections))
        load_cases.update(_get_load_cases(sections))
        mass_sources = _get_mass_sources(sections)
        load_combinations = _get_load_combinations(sections)
        joint_loads = _get_joint_loads(sections)
        frame_dist_loads = _get_frame_distributed_loads(sections)
        shell_uniform_load_sets = _get_shell_uniform_load_sets(sections)
        shell_uniform_load_set_assignments = _get_shell_uniform_load_set_assignments(sections)
        area_uniform_loads = _get_area_uniform_loads(sections, shell_uniform_load_sets)

        md = SAPModelData(
            nodes=nodes,
            restraints=restraints,
            constraints=constraints,
            constraint_assignments=constraint_assignments,
            materials=materials,
            sections=all_sections,
            frame_elements=frame_elements,
            area_elements=area_elements,
            frame_assignments=frame_assignments,
            area_assignments=area_assignments,
            groups={},
            frame_auto_mesh={},
            frame_end_offsets=frame_end_offsets,
            load_cases=load_cases,
            load_patterns=load_patterns,
            mass_sources=mass_sources,
            load_combinations=load_combinations,
            joint_loads=joint_loads,
            frame_dist_loads=frame_dist_loads,
            area_uniform_loads=area_uniform_loads,
            shell_uniform_load_sets=shell_uniform_load_sets,
            shell_uniform_load_set_assignments=shell_uniform_load_set_assignments,
            units=units,
            source=str(self.file_path),
        )
        # ── Apply material defaults in-place ──────────────────────
        # Every material is guaranteed non-zero E_mod, Fy, Fc, unit_weight
        # and unit_mass after this call.
        md.apply_material_defaults()
        return md

    def get_model_units(self) -> dict[str, str]:
        """Extract the model unit system from the ``$ CONTROLS`` section.

        ETABS declares two units (``UNITS "KN" "M"``); temperature defaults to
        Celsius.  Force and length are canonicalised to the display labels the
        downstream unit consumers accept (``"KN"`` becomes ``"kN"``, ``"M"``
        becomes ``"m"``).  Returns ``{"F", "L", "T"}``.
        """
        force = "N"
        length = "m"
        for groups in self.sections.get("CONTROLS", []):
            for kw, vals in groups:
                if kw == "UNITS":
                    force = str(vals[0]) if vals else "N"
                    length = str(vals[1]) if len(vals) > 1 else "m"
                    break
        return {
            "F": force_unit_label({"F": force}),
            "L": length_unit_label({"L": length}),
            "T": "C",
        }

    def _section_db(self) -> dict[str, dict]:
        """Return the profile catalogue, loading it lazily (empty if absent).

        ``self.section_db_path`` is honoured when set; otherwise the repo's
        private ``data/section_dict.pkl`` is probed once.  Any failure caches
        ``{}`` so profile sections degrade to placeholders without re-reading
        the file.
        """
        if self._catalogues is None:
            path = self.section_db_path
            if path is None:
                candidate = Path(__file__).resolve().parents[3] / "data" / "section_dict.pkl"
                path = candidate if candidate.exists() else None
            if path is not None:
                try:
                    self._catalogues = _load_catalogues(path)
                except (OSError, EOFError, ImportError, ValueError) as exc:
                    logger.warning("Could not load section catalogue %s: %s", path, exc)
                    self._catalogues = {}
            else:
                self._catalogues = {}
        return self._catalogues


# ── Section extractors ──────────────────────────────────────────────


def _as_float(value: Any) -> float:
    """Coerce a scalar to float, defaulting to ``0.0`` on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _compute_story_elevations(
    sections: dict[str, list[GroupedLine]],
) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, float]]:
    """Return the ordered story list (top-first) and per-story absolute Z.

    Story elevations are derived, not read: the base is the sum of every
    ``ELEV`` (usually the lowest story's), and ``HEIGHT`` is accumulated
    bottom-up so the top story lands at ``base + total height``.

    Note: multi-tower models are not yet separated into per-tower stacks; a
    ``TOWER`` keyword is carried forward onto subsequent stories but all
    stories share one elevation ladder (matching the reference parser for the
    single-tower case).
    """
    stories: list[tuple[str, dict[str, Any]]] = []
    tower: Optional[str] = None
    for groups in sections.get("STORIES - IN SEQUENCE FROM TOP", []):
        name: Optional[str] = None
        attrs: dict[str, Any] = {}
        for kw, vals in groups:
            if kw == "STORY":
                name = str(vals[0])
            elif kw == "TOWER":
                tower = str(vals[0])
                attrs["TOWER"] = tower
            else:
                attrs[kw] = vals[0] if len(vals) == 1 else tuple(vals)
        if name is None:
            continue
        if tower is not None and "TOWER" not in attrs:
            attrs["TOWER"] = tower
        stories.append((name, attrs))

    base = sum(_as_float(attrs.get("ELEV", 0.0)) for _, attrs in stories)
    heights = [_as_float(attrs.get("HEIGHT", 0.0)) for _, attrs in stories]
    relative = list(accumulate(heights[::-1]))[::-1]
    abs_elev = {name: base + rel for (name, _), rel in zip(stories, relative)}
    return stories, abs_elev


def _get_points(sections: dict[str, list[GroupedLine]]) -> dict[str, tuple]:
    """Extract 2-D plan points: ``{point: (X, Y[, DeltaZ])}``."""
    points: dict[str, tuple] = {}
    for groups in sections.get("POINT COORDINATES", []):
        for kw, vals in groups:
            if kw == "POINT":
                points[str(vals[0])] = tuple(vals[1:])
    return points


def _get_line_connectivity(
    sections: dict[str, list[GroupedLine]],
) -> dict[str, tuple]:
    """Extract line templates: ``{line: (type, point_i, point_j, drop)}``.

    The trailing ``drop`` is the number of storeys the *first* end sits below
    the assigned storey (a column drops 1, a beam drops 0).  The connectivity
    *type* is the keyword of the second group: ``COLUMN``/``BEAM``/``BRACE``
    for structural frames, or ``LINE`` (and ``CABLE``/``TENDON``) for null /
    drawn lines that carry loads but no stiffness.
    """
    lines: dict[str, tuple] = {}
    for groups in sections.get("LINE CONNECTIVITIES", []):
        if len(groups) < 2 or groups[0][0] != "LINE":
            continue
        name = str(groups[0][1][0])
        kind, vals = groups[1]
        if kind in ("COLUMN", "BEAM", "BRACE", "LINE", "CABLE", "TENDON"):
            lines[name] = (kind, str(vals[0]), str(vals[1]), int(vals[2]))
    return lines


def _get_area_connectivity(
    sections: dict[str, list[GroupedLine]],
) -> dict[str, tuple]:
    """Extract area templates: ``{area: (type, [points], [drops])}``.

    The type group holds ``npoints`` followed by the corner points and then one
    storey-drop per corner.
    """
    areas: dict[str, tuple] = {}
    for groups in sections.get("AREA CONNECTIVITIES", []):
        name: Optional[str] = None
        for kw, vals in groups:
            if kw == "AREA":
                name = str(vals[0])
                break
        if name is None:
            continue
        for kw, vals in groups[1:]:
            n = int(vals[0])
            pts = [str(v) for v in vals[1 : 1 + n]]
            drops = [int(v) for v in vals[1 + n : 1 + 2 * n]]
            areas[name] = (kw, pts, drops)
            break
    return areas


# Assignment attributes whose repeated values aggregate into a list rather
# than replacing one another.  ``MESHAT "BEAMS" MESHAT "WALLS"`` is the
# canonical example; every other assignment keyword is scalar.
_MULTI_VALUE_ASSIGN_KEYS = frozenset({"MESHAT"})


def _merge_assign_value(existing: Any, value: Any, kw: str) -> Any:
    """Merge a repeated assignment attribute value.

    Multi-valued keys (see :data:`_MULTI_VALUE_ASSIGN_KEYS`) aggregate repeated
    values into a flat list.  When the incoming value is itself a sequence
    (e.g. ``MESHAT "BEAMS" "WALLS"``), its elements are extended into the
    accumulated values rather than appended as a nested element; a scalar value
    is appended.  Every other key is scalar, so a later value replaces an
    earlier one.

    Args:
        existing: The previously accumulated value for *kw*, or ``None`` if
            this is the first occurrence.
        value: The incoming value for the current occurrence of *kw*.
        kw: The assignment keyword being merged.

    Returns:
        The merged value: a flat list of accumulated values for multi-valued
        keys, or *value* itself (replacing any earlier value) for scalar keys.
    """
    if kw in _MULTI_VALUE_ASSIGN_KEYS:
        items = list(value) if isinstance(value, (list, tuple)) else [value]
        if existing is None:
            return items
        if isinstance(existing, list):
            return [*existing, *items]
        return [existing, *items]
    return value


def _get_assigns(
    sections: dict[str, list[GroupedLine]],
    section: str,
    keyword: str,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Extract ``(name, storey)`` instance assignments for a section.

    The instance identity is the first two values of the leading group
    (``POINTASSIGN "1" "BASE"``, ``LINEASSIGN "C1" "STORY11"``, …); the
    remaining groups are attributes.  Repeated scalar keywords keep the last
    value, while explicitly multi-valued keywords (e.g. ``MESHAT``) fold their
    values into a list.
    """
    assigns: dict[tuple[str, str], dict[str, Any]] = {}
    for groups in sections.get(section, []):
        name = story = None
        attrs: dict[str, Any] = {}
        for kw, vals in groups:
            if kw == keyword:
                name = str(vals[0])
                story = str(vals[1])
            else:
                value = vals[0] if len(vals) == 1 else tuple(vals)
                attrs[kw] = _merge_assign_value(attrs.get(kw), value, kw)
        if name is not None and story is not None:
            key = (name, story)
            existing = assigns.get(key)
            if existing is None:
                assigns[key] = attrs
            else:
                for kw, value in attrs.items():
                    existing[kw] = _merge_assign_value(existing.get(kw), value, kw)
    return assigns


def _rel_story(story: str, story_sequence: list[str], drop: int) -> str:
    """Return the story ``drop`` levels below *story* (clamped at the ends).

    *story_sequence* is top-first, so ``index + drop`` descends toward the
    base.
    """
    try:
        idx = story_sequence.index(story)
    except ValueError:
        return story
    return story_sequence[max(0, min(idx + drop, len(story_sequence) - 1))]


# ── Geometry resolution (M1) ────────────────────────────────────────


def _resolve_geometry(
    stories: list[tuple[str, dict[str, Any]]],
    abs_elev: dict[str, float],
    points: dict[str, tuple],
    lines: dict[str, tuple],
    areas: dict[str, tuple],
    point_assigns: dict[tuple[str, str], dict[str, Any]],
    line_assigns: dict[tuple[str, str], dict[str, Any]],
    area_assigns: dict[tuple[str, str], dict[str, Any]],
) -> tuple[
    dict[str, Node],
    dict[str, FrameElement],
    dict[str, AreaElement],
    dict[str, str],
    dict[str, str],
    dict[str, Restraint],
]:
    """Materialise the deferred geometry into typed model collections.

    Nodes are created from ``(point, storey)`` instances (explicitly assigned
    points first, then any referenced by elements); frame and area elements are
    created from ``(name, storey)`` instances resolved through their templates,
    with the line/area "drop" offset applied against the storey sequence.

    Each distinct instance receives a composite string id (``name@storey``)
    and a sequential integer tag (independent counters for nodes, frames and
    areas, matching the S2K parser).
    """
    story_sequence = [name for name, _ in stories]  # top-first

    nodes: dict[str, Node] = {}
    frame_elements: dict[str, FrameElement] = {}
    area_elements: dict[str, AreaElement] = {}
    frame_assignments: dict[str, str] = {}
    area_assignments: dict[str, str] = {}
    restraints: dict[str, Restraint] = {}

    next_node_tag = 1

    def node_for(point: str, storey: str) -> Optional[Node]:
        """Return the node for ``(point, storey)``, creating it if absent."""
        nonlocal next_node_tag
        node_id = _compose_id(point, storey)
        existing = nodes.get(node_id)
        if existing is not None:
            return existing
        coords = points.get(point)
        if coords is None:
            logger.warning(
                "Point %r referenced at storey %r is not defined; skipping node.",
                point,
                storey,
            )
            return None
        elev = abs_elev.get(storey)
        if elev is None:
            logger.warning(
                "Storey %r referenced by point %r is not defined; skipping node.",
                storey,
                point,
            )
            return None
        x = _as_float(coords[0])
        y = _as_float(coords[1])
        dz = _as_float(coords[2]) if len(coords) > 2 else 0.0
        node = Node(
            node_id=node_id,
            node_tag=next_node_tag,
            x=x,
            y=y,
            z=elev - dz,
        )
        next_node_tag += 1
        nodes[node_id] = node
        return node

    # 1. Explicitly assigned points (restraint / diaphragm locations).
    for (point, storey), attrs in point_assigns.items():
        node_for(point, storey)
        restraint = attrs.get("RESTRAINT")
        if restraint:
            restraints[_compose_id(point, storey)] = _parse_restraint(str(restraint))

    # 2. Frame elements from LINE ASSIGNS × LINE CONNECTIVITIES.
    next_elem_tag = 1
    for (line_name, storey), attrs in line_assigns.items():
        conn = lines.get(line_name)
        if conn is None:
            logger.warning("LINE %r assigned at %r but not defined; skipping.", line_name, storey)
            continue
        _kind, point_i, point_j, drop = conn
        storey_i = _rel_story(storey, story_sequence, drop)
        ni = node_for(point_i, storey_i)
        nj = node_for(point_j, storey)
        if ni is None or nj is None:
            continue
        elem_id = _compose_id(line_name, storey)
        frame_elements[elem_id] = FrameElement(
            elem_id=elem_id,
            elem_tag=next_elem_tag,
            node_i=ni.node_id,
            node_j=nj.node_id,
            angle=_as_float(attrs.get("ANG", 0.0)),
            cardinal_point=_coerce_cardinal_point(attrs.get("CARDINALPT")),
        )
        next_elem_tag += 1
        section = attrs.get("SECTION")
        if section:
            frame_assignments[elem_id] = str(section)

    # 3. Area elements from AREA ASSIGNS × AREA CONNECTIVITIES.
    next_area_tag = 1
    for (area_name, storey), attrs in area_assigns.items():
        conn = areas.get(area_name)
        if conn is None:
            logger.warning("AREA %r assigned at %r but not defined; skipping.", area_name, storey)
            continue
        _kind, corner_points, corner_drops = conn
        node_ids: list[str] = []
        for point, drop in zip(corner_points, corner_drops):
            corner_story = _rel_story(storey, story_sequence, drop)
            nd = node_for(point, corner_story)
            if nd is None:
                node_ids = []
                break
            node_ids.append(nd.node_id)
        if len(node_ids) < 3:
            continue
        area_id = _compose_id(area_name, storey)
        area_elements[area_id] = AreaElement(
            area_id=area_id,
            area_tag=next_area_tag,
            node_ids=node_ids,
        )
        next_area_tag += 1
        section = attrs.get("SECTION")
        if section:
            area_assignments[area_id] = str(section)

    return nodes, frame_elements, area_elements, frame_assignments, area_assignments, restraints


def _coerce_cardinal_point(value: Any) -> int:
    """Coerce a ``CARDINALPT`` token to a 1-11 insertion point (default 10).

    Reuses :meth:`SAP2000Parser._parse_cardinal_point` so the ETABS and SAP2000
    paths share one spelling of the insertion-point grammar — a bare integer
    (``CARDINALPT 8``) or the labelled form (``"8 (top centre)"``); anything
    outside 1-11 is rejected and falls back to 10 (centroid).
    """
    from .s2k_parser import SAP2000Parser

    point = SAP2000Parser._parse_cardinal_point(value)
    return 10 if point is None else point


def _build_frame_end_offsets(
    frame_elements: dict[str, FrameElement],
    sections: dict[str, Section],
    frame_assignments: dict[str, str],
) -> dict[str, FrameEndOffset]:
    """Fold each frame's cardinal-point insertion offset into end offsets.

    Mirrors :meth:`SAP2000Parser._merge_cardinal_into_offsets`, reusing the S2K
    parser's static ``_get_section_depth_width`` and ``_cardinal_point_offset``
    helpers so the two parsers share one spelling of the 1-11 numbering table.
    The lateral offset is applied identically at both ends; cardinal points 10
    (centroid) and 11 (shear centre) on a symmetric section yield no offset.
    """
    from .s2k_parser import SAP2000Parser

    tol = SAP2000Parser._LATERAL_OFFSET_TOL
    offsets: dict[str, FrameEndOffset] = {}
    for eid, fe in frame_elements.items():
        sec_name = frame_assignments.get(eid)
        sec = sections.get(sec_name) if sec_name else None
        if sec is None:
            continue
        D, B = SAP2000Parser._get_section_depth_width(sec)
        if D == 0.0 and B == 0.0 and fe.cardinal_point not in (10, 11):
            continue
        off_y, off_z = SAP2000Parser._cardinal_point_offset(
            fe.cardinal_point,
            D,
            B,
            getattr(sec, "cg_offset_2", 0.0),
            getattr(sec, "cg_offset_3", 0.0),
            getattr(sec, "ecc_v2", 0.0),
            getattr(sec, "ecc_v3", 0.0),
        )
        if abs(off_y) < tol and abs(off_z) < tol:
            continue
        offsets[eid] = FrameEndOffset(off_y_i=off_y, off_z_i=off_z, off_y_j=off_y, off_z_j=off_z)
    return offsets


def _get_diaphragm_names(sections: dict[str, list[GroupedLine]]) -> dict[str, str]:
    """Extract ``$ DIAPHRAGM NAMES`` definitions: ``{name: rigidity}``.

    ``rigidity`` is ``"RIGID"`` or ``"SEMIRIGID"`` — the only two diaphragm
    types ETABS defines.  ``RIGID`` is a rigid in-plane constraint (no flexural
    stiffness); ``SEMIRIGID`` is a grouping with *no* constraint (in-plane
    stiffness comes from the shell objects), usable to apply inertial loads in
    seismic analysis.
    """
    names: dict[str, str] = {}
    for groups in sections.get("DIAPHRAGM NAMES", []):
        name: Optional[str] = None
        rigidity: str = "RIGID"
        for kw, vals in groups:
            if kw == "DIAPHRAGM" and vals:
                name = str(vals[0])
            elif kw in ("RIGID", "SEMIRIGID"):
                # Unquoted ``TYPE RIGID``: the tokeniser records ``RIGID`` /
                # ``SEMIRIGID`` as their own keyword group with no values.
                rigidity = kw
            elif kw == "TYPE" and vals:
                # Defensive: quoted ``TYPE "RIGID"`` if any exporter emits it.
                rigidity = str(vals[0]).upper()
        if name is not None:
            names[name] = rigidity
    return names


def _build_diaphragm_constraints(
    area_elements: dict[str, AreaElement],
    point_assigns: dict[tuple[str, str], dict[str, Any]],
    area_assigns: dict[tuple[str, str], dict[str, Any]],
    diaphragm_names: dict[str, str],
) -> tuple[dict[str, Constraint], dict[str, str]]:
    """Synthesise per-storey diaphragm constraints from ``DIAPH`` assignments.

    ETABS assigns one named diaphragm (``$ DIAPHRAGM NAMES``) to areas/points
    at many storeys, yet each storey's floor is a *separate* rigid plane.  The
    S2K constraint model assumes "one name → one joint group", so each
    ``(name, storey)`` pair becomes its own :class:`Constraint` instance keyed
    ``name@storey`` (the composite-id convention).  The participating nodes are
    the corner nodes of ``DIAPH``-assigned area elements plus the node of each
    ``DIAPH``-assigned point, minus points explicitly marked
    ``DIAPH "DISCONNECTED"``.

    ``RIGID`` diaphragms populate ``constraint_assignments`` so the existing
    rigid-diaphragm pipeline applies ``ops.rigidDiaphragm``; ``SEMIRIGID``
    diaphragms are recorded (``constraint_data["Rigidity"]``) but produce no
    assignments — their in-plane stiffness comes from the shells.

    Returns:
        ``(constraints, constraint_assignments)``, both keyed by the per-storey
        instance id.
    """
    # Per-(name, storey) membership, accumulated from the assigned objects
    # (point nodes and area corner nodes), plus points opted out per storey.
    members: dict[tuple[str, str], set[str]] = {}
    disconnected: dict[str, set[str]] = {}
    for (obj, storey), attrs in point_assigns.items():
        value = attrs.get("DIAPH")
        if value is None:
            continue
        upper = str(value).upper()
        if upper == "DISCONNECTED":
            disconnected.setdefault(storey, set()).add(obj)
        elif upper not in ("", "NONE"):
            members.setdefault((str(value), storey), set()).add(_compose_id(obj, storey))
    for (area, storey), attrs in area_assigns.items():
        value = attrs.get("DIAPH")
        if value is None or str(value).upper() in ("", "NONE", "DISCONNECTED"):
            continue
        elem = area_elements.get(_compose_id(area, storey))
        if elem is None:
            continue
        members.setdefault((str(value), storey), set()).update(elem.node_ids)

    constraints: dict[str, Constraint] = {}
    assignments: dict[str, str] = {}
    for (name, storey), node_ids in members.items():
        rigidity = diaphragm_names.get(name)
        if rigidity is None:
            logger.warning(
                "DIAPH %r assigned at storey %r but not defined; skipping.",
                name,
                storey,
            )
            continue
        excluded = disconnected.get(storey, set())
        member_nodes = [nid for nid in node_ids if _split_id(nid)[0] not in excluded]
        if rigidity == "RIGID" and len(member_nodes) < 2:
            logger.warning(
                "DIAPH %r at storey %r has fewer than two connected member nodes "
                "after filtering disconnected points; skipping RIGID constraint.",
                name,
                storey,
            )
            continue
        cname = _compose_id(name, storey)
        constraints[cname] = Constraint(
            name=cname,
            constraint_type="DIAPHRAGM",
            coord_sys="GLOBAL",
            constraint_data={
                "Axis": "Z",
                "Rigidity": rigidity,
                "Diaphragm": name,
            },
        )
        if rigidity == "RIGID":
            for nid in member_nodes:
                assignments[nid] = cname
    return constraints, assignments


# ── Materials & sections & restraints (M2) ──────────────────────────


def _opt_float(value: Any) -> Optional[float]:
    """Return ``float(value)``, or ``None`` when the value is missing/unparsable."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ── Restraints ──

_RESTRAINT_DOF_ALIASES = {
    "U1": {"U1", "UX"},
    "U2": {"U2", "UY"},
    "U3": {"U3", "UZ"},
    "R1": {"R1", "RX"},
    "R2": {"R2", "RY"},
    "R3": {"R3", "RZ"},
}


def _parse_restraint(text: str) -> Restraint:
    """Parse an ETABS ``RESTRAINT "UX UY UZ"`` string into a DOF vector."""
    tokens = {tok.upper() for tok in text.split()}
    return Restraint(
        dofs=[1 if aliases & tokens else 0 for aliases in _RESTRAINT_DOF_ALIASES.values()]
    )


# ── Multi-line record merge ──


def _merge_keyword_lines(
    sections: dict[str, list[GroupedLine]],
    section: str,
    keyword: str,
) -> dict[str, dict[str, Any]]:
    """Merge records that repeat their leading ``(keyword, name)`` pair.

    ETABS spreads one object across physical lines by repeating the leading
    ``(type, name)`` tokens (e.g. ``MATERIAL "STEEL" …`` on two lines).  Each
    line contributes its attribute groups; a repeated key folds into a list.
    Returns ``{name: merged_attrs}``.
    """
    merged: dict[str, dict[str, Any]] = {}
    for groups in sections.get(section, []):
        name: Optional[str] = None
        attrs: dict[str, Any] = {}
        for kw, vals in groups:
            if kw == keyword:
                name = str(vals[0])
            else:
                value = vals[0] if len(vals) == 1 else tuple(vals)
                if kw in attrs:
                    existing = attrs[kw]
                    attrs[kw] = (
                        [*existing, value] if isinstance(existing, list) else [existing, value]
                    )
                else:
                    attrs[kw] = value
        if name is None:
            continue
        if name in merged:
            merged[name].update(attrs)
        else:
            merged[name] = attrs
    return merged


# ── Materials ──

_MATERIAL_TYPE_MAP = {
    "STEEL": "Steel",
    "CONCRETE": "Concrete",
    "REBAR": "Rebar",
    "TENDON": "Tendon",
    "OTHER": "Other",
    "NONE": "Other",
}

_MATERIAL_CONSUMED_KEYS = {"M", "W", "TYPE", "E", "U", "DESIGNTYPE", "FY", "FU", "FC"}


def _get_materials(sections: dict[str, list[GroupedLine]]) -> dict[str, Material]:
    """Extract materials from the ``$ MATERIAL PROPERTIES`` section.

    Each material spans two lines — an isotropic property line (``M``, ``W``,
    ``TYPE``, ``E``, ``U``, ``A``) and a design line (``DESIGNTYPE``, ``FY``,
    ``FU``, ``FC``, ``FYS``) — merged by name.  Values stay in model units
    (matching the S2K parser, which does no SI scaling); ``G_mod`` is derived
    from ``E`` and ``U``.
    """
    materials: dict[str, Material] = {}
    for name, props in _merge_keyword_lines(sections, "MATERIAL PROPERTIES", "MATERIAL").items():
        design_type = str(props.get("DESIGNTYPE", "OTHER"))
        mat_type = _MATERIAL_TYPE_MAP.get(design_type.upper(), "Other")
        e_mod = _as_float(props.get("E", 0.0))
        nu = _as_float(props.get("U", 0.0))
        g_mod = e_mod / (2.0 * (1.0 + nu)) if e_mod > 0 and nu > 0 else 0.0
        extra = {k: v for k, v in props.items() if k not in _MATERIAL_CONSUMED_KEYS}
        materials[name] = Material(
            name=name,
            type=mat_type,
            E_mod=e_mod,
            G_mod=g_mod,
            nu=nu,
            unit_weight=_as_float(props.get("W", 0.0)),
            unit_mass=_as_float(props.get("M", 0.0)),
            Fy=_opt_float(props.get("FY")),
            Fu=_opt_float(props.get("FU")),
            Fc=_opt_float(props.get("FC")),
            extra=extra,
        )
    return materials


# ── Section resolution ──

_LENGTH_UNIT_ALIASES = {
    "m": "m",
    "meter": "m",
    "meters": "m",
    "metre": "m",
    "metres": "m",
    "mm": "mm",
    "millimeter": "mm",
    "millimeters": "mm",
    "millimetre": "mm",
    "millimetres": "mm",
    "cm": "cm",
    "centimeter": "cm",
    "centimeters": "cm",
    "centimetre": "cm",
    "centimetres": "cm",
    "in": "in",
    "inch": "in",
    "inches": "in",
    "ft": "ft",
    "foot": "ft",
    "feet": "ft",
}

_METERS_PER_UNIT = {"m": 1.0, "mm": 1e-3, "cm": 1e-2, "in": 0.0254, "ft": 0.3048}

# Length exponent of each catalogue property (kept in step with
# ``SectionLibrary._EXPONENT_MAP`` in ``model/sections.py``).
_LENGTH_EXPONENT = {
    "D": 1,
    "B": 1,
    "BF": 1,
    "TF": 1,
    "TW": 1,
    "OD": 1,
    "TDES": 1,
    "DIS": 1,
    "KDES": 1,
    "R33": 1,
    "R22": 1,
    "X": 1,
    "Y": 1,
    "depth": 1,
    "width": 1,
    "A": 2,
    "AS2": 2,
    "AS3": 2,
    "Z33": 3,
    "Z22": 3,
    "S33POS": 3,
    "S22POS": 3,
    "S33NEG": 3,
    "S22NEG": 3,
    "I33": 4,
    "I22": 4,
    "J": 4,
    "Cw": 4,
}

_SECTION_TYPE_CLASSES = {
    "STEEL_I_SECTION": ISection,
    "STEEL_PIPE": PipeSection,
    "STEEL_ROUND": CircularSection,
    "STEEL_RECTANGULAR_HOLLOW": BoxSection,
    "STEEL_BOX": BoxSection,
    "STEEL_ANGLE": AngleSection,
    "STEEL_DOUBLE_ANGLE": DoubleAngleSection,
    "STEEL_CHANNEL": ChannelSection,
    "STEEL_TEE": TeeSection,
    "STEEL_RECTANGULAR": RectangularSection,
    "STEEL_SOLID_RECTANGLE": RectangularSection,
}

# Canonical ids (SHAPE_NAMES values) that a parametric ``FRAMESECTION`` record
# can carry explicit dimensions for.  These are dispatched to per-shape property
# builders; anything else is treated as a catalogue profile name or a ``General``
# section.
_PARAMETRIC_BUILDERS = frozenset({"I", "CH", "CHS", "RHS", "A", "AA", "T", "R", "C"})

_TRAILING_DIGITS = re.compile(r"\d+$")


def _normalise_catalogue_key(key: Any) -> str:
    """Lowercase a catalogue key, dropping a trailing edition number.

    ``"AISC14"`` → ``"aisc"`` and ``"AISC"`` → ``"aisc"``, so an ETABS ``FILE``
    token such as ``"AISC14.PRO"`` matches the ``"AISC"`` catalogue.
    """
    return _TRAILING_DIGITS.sub("", str(key).strip().lower())


def _normalise_length_unit(unit: str) -> str:
    """Canonicalise an ETABS length unit (``"M"`` → ``"m"``, ``"in"``, …)."""
    return _LENGTH_UNIT_ALIASES.get(str(unit).strip().lower(), "m")


def _length_factor(from_units: str, to_units: str) -> float:
    """Linear conversion factor so ``value_to = value_from * factor``."""
    try:
        return _METERS_PER_UNIT[from_units] / _METERS_PER_UNIT[to_units]
    except KeyError:
        return 1.0


def _load_catalogues(path: Path) -> dict[str, dict]:
    """Load ``section_dict.pkl`` (a ``{catalogue: {SECTIONS: {name: props}}}`` map)."""
    import pickle

    with open(path, "rb") as fh:
        return pickle.load(fh)


def _find_profile(catalogues: dict[str, dict], name: str, file_name: Optional[str] = None):
    """Return ``(catalogue_data, section_props)`` for *name*, else ``None``.

    When *file_name* (the ETABS ``FILE`` token) is given, the lookup is scoped
    to that catalogue first (matched case-insensitively, ignoring a trailing
    edition number); an unknown ``FILE`` falls back to a global search.
    """
    if file_name is not None:
        want = _normalise_catalogue_key(Path(str(file_name)).stem)
        for key, cat_data in catalogues.items():
            if not isinstance(cat_data, dict):
                continue
            if _normalise_catalogue_key(key) != want:
                continue
            sections_dict = cat_data.get("SECTIONS", cat_data)
            if isinstance(sections_dict, dict) and name in sections_dict:
                return cat_data, sections_dict[name]
    for cat_data in catalogues.values():
        if not isinstance(cat_data, dict):
            continue
        sections_dict = cat_data.get("SECTIONS", cat_data)
        if isinstance(sections_dict, dict) and name in sections_dict:
            return cat_data, sections_dict[name]
    return None


def _resolve_profile(
    catalogues: dict[str, dict],
    name: str,
    material: Optional[str],
    length_unit: str,
    file_name: Optional[str] = None,
) -> Optional[Section]:
    """Resolve a catalogue profile name into a typed :class:`Section`.

    Dimensions and section properties are converted from the catalogue's
    declared units (``UNITS``, e.g. ``"in"``) to the model length unit using
    each property's length exponent.  ``SECTION_TYPE`` selects the concrete
    :class:`Section` subclass.  When *file_name* is given it scopes the lookup
    to that catalogue.
    """
    found = _find_profile(catalogues, name, file_name=file_name)
    if found is None:
        return None
    cat_data, props = found
    if not isinstance(props, dict):
        return None

    from_units = _normalise_length_unit(
        str(props.get("UNITS") or cat_data.get("CONTROL", {}).get("LENGTH_UNITS") or "mm")
    )
    factor = _length_factor(from_units, length_unit)

    def cv(key: str) -> Optional[float]:
        raw = props.get(key)
        if raw is None:
            return None
        try:
            return float(raw) * (factor ** _LENGTH_EXPONENT[key])
        except (TypeError, ValueError, KeyError):
            return None

    common = {
        "name": name,
        "shape": name,
        "material": material or "",
        "A": cv("A") or 0.0,
        "I33": cv("I33") or 0.0,
        "I22": cv("I22") or cv("I33") or 0.0,
        "J": cv("J") or 0.0,
        "Z33": cv("Z33"),
        "Z22": cv("Z22") or cv("Z33"),
    }

    cls = _SECTION_TYPE_CLASSES.get(str(props.get("SECTION_TYPE", "")), GeneralSection)
    if cls is PipeSection:
        return PipeSection(**common, od=cv("OD") or 0.0, t=cv("TDES") or 0.0)
    if cls is CircularSection:
        return CircularSection(**common, diameter=cv("D") or cv("OD") or 0.0)
    if cls in (ISection, BoxSection, AngleSection, ChannelSection, TeeSection):
        return cls(
            **common,
            depth=cv("D") or 0.0,
            bf=cv("BF") or cv("B") or 0.0,
            tf=cv("TF") or 0.0,
            tw=cv("TW") or 0.0,
        )
    if cls is DoubleAngleSection:
        return DoubleAngleSection(
            **common,
            depth=cv("D") or 0.0,
            bf=cv("B") or cv("BF") or 0.0,
            tf=cv("TF") or 0.0,
            tw=cv("TW") or 0.0,
            dis=cv("DIS") or 0.0,
        )
    if cls is RectangularSection:
        return RectangularSection(
            **common,
            depth=cv("D") or 0.0,
            bf=cv("B") or cv("BF") or 0.0,
        )
    return GeneralSection(**common)


def _solid_rect_props(depth: float, width: float) -> dict[str, float]:
    """Derive elastic section properties for a solid rectangle (legacy alias).

    See :func:`fea_toolkit.model.section_properties.rect_props` — the
    computation moved there so every parametric shape lives in one module.
    """
    return rect_props(depth, width)


def _build_general_section(
    name: str,
    shape: str,
    material: Optional[str],
    dims: dict[str, Any],
) -> GeneralSection:
    """Build a :class:`GeneralSection` from explicit derived-property tokens.

    ETABS ``General`` sections carry ``AREA``/``I33``/``I22``/``TORSION``/
    ``Z33``/``Z22`` rather than dimensions, so they are read straight through.
    """
    return GeneralSection(
        name=name,
        shape=shape,
        material=material or "",
        A=_as_float(dims.get("AREA", 0.0)),
        I33=_as_float(dims.get("I33", 0.0)),
        I22=_as_float(dims.get("I22", 0.0)),
        J=_as_float(dims.get("TORSION", dims.get("J", 0.0))),
        Z33=_opt_float(dims.get("Z33")),
        Z22=_opt_float(dims.get("Z22")),
    )


def _build_parametric_section(
    name: str,
    shape: str,
    canonical: str,
    material: Optional[str],
    dims: dict[str, Any],
    mat_type: str,
    concrete_attrs: dict[str, Any],
) -> Section:
    """Build a parametric :class:`Section` from its dimension tokens."""
    d = _as_float(dims.get("D", 0.0))
    b = _as_float(dims.get("B", 0.0))
    tf = _as_float(dims.get("TF", 0.0))
    tw = _as_float(dims.get("TW", 0.0))
    cover = _as_float(concrete_attrs.get("COVER", 0.0))

    if canonical == "R":
        props = rect_props(d, b)
        if mat_type == "concrete":
            return ConcreteRectangularSection(
                name=name,
                shape=shape,
                material=material or "",
                depth=d,
                bf=b,
                cover=cover,
                **props,
            )
        return RectangularSection(
            name=name, shape=shape, material=material or "", depth=d, bf=b, **props
        )
    if canonical == "C":
        props = circle_props(d)
        if mat_type == "concrete":
            return ConcreteCircularSection(
                name=name,
                shape=shape,
                material=material or "",
                diameter=d,
                cover=cover,
                **props,
            )
        return CircularSection(name=name, shape=shape, material=material or "", diameter=d, **props)
    if canonical == "CHS":
        od = d
        t = _as_float(dims.get("T", 0.0)) or _as_float(dims.get("TDES", 0.0))
        return PipeSection(
            name=name,
            shape=shape,
            material=material or "",
            od=od,
            t=t,
            **pipe_props(od, t),
        )
    if canonical == "I":
        return ISection(
            name=name,
            shape=shape,
            material=material or "",
            depth=d,
            bf=b,
            tf=tf,
            tw=tw,
            **i_props(d, b, tf, tw),
        )
    if canonical == "CH":
        return ChannelSection(
            name=name,
            shape=shape,
            material=material or "",
            depth=d,
            bf=b,
            tf=tf,
            tw=tw,
            **channel_props(d, b, tf, tw),
        )
    if canonical == "RHS":
        return BoxSection(
            name=name,
            shape=shape,
            material=material or "",
            depth=d,
            bf=b,
            tf=tf,
            tw=tw,
            **box_props(d, b, tf, tw),
        )
    if canonical == "A":
        return AngleSection(
            name=name,
            shape=shape,
            material=material or "",
            depth=d,
            bf=b,
            tf=tf,
            tw=tw,
            **angle_props(d, b, tf, tw),
        )
    if canonical == "AA":
        dis = _as_float(dims.get("DIS", 0.0))
        return DoubleAngleSection(
            name=name,
            shape=shape,
            material=material or "",
            depth=d,
            bf=b,
            tf=tf,
            tw=tw,
            dis=dis,
            **double_angle_props(d, b, tf, tw, dis),
        )
    if canonical == "T":
        return TeeSection(
            name=name,
            shape=shape,
            material=material or "",
            depth=d,
            bf=b,
            tf=tf,
            tw=tw,
            **tee_props(d, b, tf, tw),
        )
    return GeneralSection(name=name, shape=shape, material=material or "")


def _build_frame_section(
    name: str,
    shape: str,
    material: Optional[str],
    dims: dict[str, Any],
    concrete_attrs: dict[str, Any],
    materials: dict[str, Material],
    length_unit: str,
    catalogues: dict[str, dict],
    file_name: Optional[str] = None,
) -> Optional[Section]:
    """Build a frame :class:`Section` from one ``FRAMESECTION`` record.

    Dispatch mirrors ``E2K_utilities.enhance_frame_properties``:

    * ``FILE``-present (or a ``SHAPE`` that is not a recognised shape type) →
      a catalogue profile, resolved within the ``FILE``-named catalogue.
    * a recognised parametric shape type → per-shape property builders.
    * ``General`` / ``NA`` → explicit derived-property tokens.
    """
    mat_type = ""
    if material and material in materials:
        mat_type = materials[material].type.lower()

    canonical = SHAPE_NAMES.get(shape)

    # Section Designer sections carry no dimensions here — their polygon
    # geometry lives in ``$ SECTION DESIGNER SECTIONS`` and is resolved by
    # :func:`_get_sd_sections` into a :class:`SDSection`.
    if canonical == "SD":
        return None

    # Catalogue (profile) section: an explicit FILE token, or a SHAPE that is a
    # profile name rather than a recognised shape type.
    if file_name is not None or canonical is None:
        sec = _resolve_profile(catalogues, shape, material, length_unit, file_name=file_name)
        if sec is not None:
            return sec
        logger.warning(
            "Profile section %r (shape %r) not resolved; using a placeholder.", name, shape
        )
        return GeneralSection(name=name, shape=shape, material=material or "")

    if canonical == "GEN":
        return _build_general_section(name, shape, material, dims)

    if canonical not in _PARAMETRIC_BUILDERS:
        logger.warning("Section %r has unsupported shape %r; using a placeholder.", name, shape)
        return GeneralSection(name=name, shape=shape, material=material or "")

    return _build_parametric_section(
        name, shape, canonical, material, dims, mat_type, concrete_attrs
    )


def _get_frame_sections(
    sections: dict[str, list[GroupedLine]],
    materials: dict[str, Material],
    length_unit: str,
    catalogues: dict[str, dict],
) -> dict[str, Section]:
    """Extract frame sections from ``$ FRAME SECTIONS`` (+ ``$ CONCRETE SECTIONS``).

    ``$ CONCRETE SECTIONS`` supplies rebar/cover detail for parametric concrete
    sections.  Auto-select lists are skipped (they reference ``AUTOSECTION``
    lists, handled in M3).
    """
    concrete = _merge_keyword_lines(sections, "CONCRETE SECTIONS", "CONCRETESECTION")
    frame_sections: dict[str, Section] = {}
    for groups in sections.get("FRAME SECTIONS", []):
        name: Optional[str] = None
        material: Optional[str] = None
        shape: Optional[str] = None
        file_name: Optional[str] = None
        dims: dict[str, Any] = {}
        for kw, vals in groups:
            if kw == "FRAMESECTION":
                name = str(vals[0])
            elif kw == "MATERIAL":
                material = str(vals[0])
            elif kw == "SHAPE":
                shape = str(vals[0])
            elif kw == "FILE":
                file_name = str(vals[0])
            else:
                dims[kw] = vals[0] if len(vals) == 1 else tuple(vals)
        if name is None or shape is None:
            continue
        if shape.lower() == "auto select list":
            logger.debug("Skipping auto-select list section %r (resolved in M3).", name)
            continue
        sec = _build_frame_section(
            name,
            shape,
            material,
            dims,
            concrete.get(name, {}),
            materials,
            length_unit,
            catalogues,
            file_name,
        )
        if sec is not None:
            frame_sections[name] = sec
    return frame_sections


def _get_sd_sections(sections: dict[str, list[GroupedLine]]) -> dict[str, SDSection]:
    """Extract Section Designer sections into :class:`SDSection`.

    ``$ SECTION DESIGNER SECTIONS`` spreads one section across many lines: a
    header (``BASEMATERIAL`` …), one ``SHAPE n … SHAPETYPE "POLYGON"`` line per
    polygon, and one ``POLYCORNER k X … Y …`` line per corner.  Only
    ``POLYGON`` shapes contribute section area; ``REF LINE`` (and any other
    non-polygon) shapes are drawing aids and are skipped.

    The polygon corners are read in ``X`` / ``Y`` Section Designer coordinates
    and mapped onto the section local axes — ``X`` → local 3, ``Y`` → local 2
    (the ETABS frame-section default; the ``ANGLE`` rotation token is not yet
    applied).  Gross elastic properties (``A``, ``I33``, ``I22``, ``J``) are
    computed from the polygon(s) via :func:`polygon_props`; ``J`` is the polar
    second moment, an upper-bound approximation to the St. Venant torsional
    constant of a solid section.  Multi-polygon composites are combined with
    the parallel-axis theorem about their shared centroid.
    """
    base_materials: dict[str, str] = {}
    polygons: dict[str, dict[int, tuple[str, list[tuple[float, float]]]]] = {}
    corners: dict[str, dict[int, dict[int, tuple[float, float]]]] = {}
    for groups in sections.get("SECTION DESIGNER SECTIONS", []):
        if not groups or groups[0][0] != "SDSECTION":
            continue
        name = str(groups[0][1][0])
        attrs = {kw: v[0] if len(v) == 1 else tuple(v) for kw, v in groups[1:]}
        if "BASEMATERIAL" in attrs:
            base_materials[name] = str(attrs["BASEMATERIAL"])
            continue
        if "SHAPE" not in attrs:
            continue
        shape_idx = int(attrs["SHAPE"])
        if "POLYCORNER" in attrs:
            corner_idx = int(attrs["POLYCORNER"])
            x = _as_float(attrs.get("X", 0.0))
            y = _as_float(attrs.get("Y", 0.0))
            corners.setdefault(name, {}).setdefault(shape_idx, {})[corner_idx] = (x, y)
        elif attrs.get("SHAPETYPE"):
            if str(attrs["SHAPETYPE"]).upper() == "POLYGON":
                material = (
                    str(attrs["MATERIAL"]) if "MATERIAL" in attrs else base_materials.get(name, "")
                )
                polygons.setdefault(name, {})[shape_idx] = (material, [])

    built: dict[str, SDSection] = {}
    for name, shape_map in polygons.items():
        poly_list: list[tuple[str, list[tuple[float, float]]]] = []
        prop_list: list[dict[str, float]] = []
        for shape_idx in sorted(shape_map):
            material, _ = shape_map[shape_idx]
            corner_map = corners.get(name, {}).get(shape_idx, {})
            ordered = [corner_map[i] for i in sorted(corner_map)]
            if len(ordered) < 3:
                continue
            # (local 2, local 3) = (SD Y, SD X)
            verts = [(y, x) for (x, y) in ordered]
            prop_list.append(polygon_props(verts))
            poly_list.append((material, verts))
        area = sum(p["A"] for p in prop_list)
        if area <= 0.0 or not poly_list:
            continue
        cy = sum(p["A"] * p["cy"] for p in prop_list) / area
        cz = sum(p["A"] * p["cz"] for p in prop_list) / area
        i33 = sum(p["I33"] + p["A"] * (p["cy"] - cy) ** 2 for p in prop_list)
        i22 = sum(p["I22"] + p["A"] * (p["cz"] - cz) ** 2 for p in prop_list)
        built[name] = SDSection(
            name=name,
            shape="SD Section",
            material=base_materials.get(name, ""),
            A=area,
            I33=i33,
            I22=i22,
            J=i33 + i22,
            polygons=poly_list,
        )
    return built


# ── Shell sections ──


def _get_shell_sections(
    sections: dict[str, list[GroupedLine]],
) -> dict[str, Section]:
    """Extract shell/area sections from ``$ WALL/SLAB/DECK PROPERTIES``.

    A shell property may span several ``SHELLPROP`` lines (e.g. a deck's
    membrane + fill + stud lines); they are merged by name.  Thickness is taken
    from ``TM``, else ``TB``, else ``SLABDEPTH`` (a deck approximation).
    """
    shells: dict[str, Section] = {}
    for name, props in _merge_keyword_lines(
        sections, "WALL/SLAB/DECK PROPERTIES", "SHELLPROP"
    ).items():
        material = str(props.get("MATERIAL") or props.get("CONCMATERIAL") or "")
        thickness = _as_float(props.get("TM") or props.get("TB") or props.get("SLABDEPTH") or 0.0)
        shells[name] = ShellSection(
            name=name,
            shape="Shell",
            material=material,
            thickness=thickness,
        )
    return shells


# ── Loads (M3) ──────────────────────────────────────────────────────


_COMBO_TYPE_MAP = {
    "ADD": "Linear Add",
    "ENVE": "Envelope",
    "ABS": "Absolute Add",
    "SRSS": "SRSS",
    "RANGE": "Range Add",
}

_COMBO_ENTRY_KIND = {
    "LOAD": "case",
    "LOADCASE": "case",
    "LOADPATTERN": "case",
    "SPEC": "case",
    "COMBO": "combo",
    "LOADCOMBO": "combo",
}

# ETABS v19+ load-case ``TYPE`` strings → the toolkit's ``case_type``
# vocabulary.  SAP2000's ``.s2k`` spells a linear-static case ``LinStatic``;
# :func:`~fea_toolkit.analysis.case_listing.list_static_cases` auto-detects a
# static case by matching ``linstatic`` / ``static`` case-insensitively, so the
# ETABS spellings below must land on those tokens.
_CASE_TYPE_MAP = {
    "LINEAR STATIC": "LinStatic",
    "MODAL - EIGEN": "LinModal",
    "RESPONSE SPECTRUM": "LinRespSpec",
}


def _yes(value: Any) -> bool:
    """Interpret an ETABS ``"Yes"`` / ``"No"`` token as a bool."""
    return str(value).strip().lower() in ("yes", "true", "1")


# Load types recognised but not mapped to a load dataclass (deferred):
# temperature, ground displacement, and point force/moment at a frame midspan.
# Logged at debug level rather than dropped silently or spamming warnings.
_DEFERRED_LOAD_TYPES = {"TEMP", "GROUNDDISP", "POINTF", "POINTM"}


def _get_load_cases_and_patterns(
    sections: dict[str, list[GroupedLine]],
) -> tuple[dict[str, LoadCase], dict[str, LoadPattern]]:
    """Extract load cases and patterns from the legacy ``$ STATIC LOADS``.

    Pre-v19 ETABS exports merge the load-case and load-pattern concepts into a
    single ``LOADCASE`` line: each line yields both a :class:`LoadCase` and a
    :class:`LoadPattern` of the same name, and the ``SELFWEIGHT`` factor maps to
    the pattern's self-weight multiplier.  A ``WIND`` line attaches the
    auto-generation parameters to the matching case and pattern.

    v19+ splits this into ``$ LOAD PATTERNS`` + ``$ LOAD CASES`` (see
    :func:`_get_load_patterns` and :func:`_get_load_cases`); this function only
    reads the legacy form.
    """
    load_cases: dict[str, LoadCase] = {}
    load_patterns: dict[str, LoadPattern] = {}
    for groups in sections.get("STATIC LOADS", []):
        if not groups:
            continue
        leading, vals = groups[0]
        attrs = {kw: v[0] if len(v) == 1 else tuple(v) for kw, v in groups[1:]}
        if leading == "LOADCASE":
            name = str(vals[0])
            design_type = str(attrs.get("TYPE", "OTHER"))
            self_weight = _as_float(attrs.get("SELFWEIGHT", 0.0))
            load_cases[name] = LoadCase(
                case_name=name,
                case_type="LinStatic",
                design_type_option="Prog Det",
                design_type=design_type,
                design_action_option="Prog Det",
                design_action="",
                run_case=True,
            )
            load_patterns[name] = LoadPattern(
                name=name,
                pattern_type=design_type,
                self_weight_factor=self_weight,
            )
        elif leading == "WIND":
            name = str(vals[0])
            code = str(vals[1]) if len(vals) > 1 else ""
            wind_data: dict[str, Any] = {"code": code}
            wind_data.update(attrs)
            if name in load_patterns:
                load_patterns[name].auto_data["WIND"] = wind_data
            if name in load_cases:
                load_cases[name].case_data["WIND"] = wind_data
        else:
            logger.debug("Ignoring unrecognised STATIC LOADS record %r.", leading)
    return load_cases, load_patterns


def _get_load_patterns(
    sections: dict[str, list[GroupedLine]],
) -> dict[str, LoadPattern]:
    """Extract load patterns from ``$ LOAD PATTERNS`` (v19+).

    ``LOADPATTERN "name" TYPE "type" SELFWEIGHT factor`` yields a
    :class:`LoadPattern`; ``SEISMIC`` / ``WIND`` auto-generation records attach
    to ``auto_data`` (captured, not expanded — matching the SAP2000 parser's
    treatment of ``AUTO*`` tables).
    """
    patterns: dict[str, LoadPattern] = {}
    for groups in sections.get("LOAD PATTERNS", []):
        if not groups:
            continue
        leading, vals = groups[0]
        attrs = {kw: v[0] if len(v) == 1 else tuple(v) for kw, v in groups[1:]}
        name = str(vals[0]) if vals else ""
        if not name:
            continue
        if leading == "LOADPATTERN":
            patterns[name] = LoadPattern(
                name=name,
                pattern_type=str(attrs.get("TYPE", "Other")),
                self_weight_factor=_as_float(attrs.get("SELFWEIGHT", 0.0)),
            )
        elif leading == "SEISMIC":
            if name not in patterns:
                logger.debug("SEISMIC record %r has no matching LOADPATTERN.", name)
                continue
            data = patterns[name].auto_data.setdefault("SEISMIC", {})
            if "USERLOAD" in attrs:
                data.setdefault("user_loads", []).append(attrs)
            elif len(vals) > 1:
                data["source"] = str(vals[1])
        elif leading == "WIND":
            if name not in patterns:
                continue
            wind_data: dict[str, Any] = {"code": str(vals[1]) if len(vals) > 1 else ""}
            wind_data.update(attrs)
            patterns[name].auto_data["WIND"] = wind_data
        else:
            logger.debug("Ignoring unrecognised LOAD PATTERNS record %r.", leading)
    return patterns


def _get_load_cases(
    sections: dict[str, list[GroupedLine]],
) -> dict[str, LoadCase]:
    """Extract load cases from ``$ LOAD CASES`` (v19+).

    A ``LOADCASE "name" TYPE …`` header starts a case; later ``LOADCASE "name"
    LOADPAT "pattern" SF factor`` lines are its static load assignments (stored
    under the same ``CASE - STATIC 1 - LOAD ASSIGNMENTS`` key the SAP2000 parser
    writes, so :func:`~fea_toolkit.model.sap_data.patterns_from_case` reads them
    unchanged).  Modal / response-spectrum parameters are kept losslessly under
    ``case_data["ETABS"]``.
    """
    load_cases: dict[str, LoadCase] = {}
    for groups in sections.get("LOAD CASES", []):
        if not groups or groups[0][0] != "LOADCASE":
            continue
        vals = groups[0][1]
        if not vals:
            continue
        name = str(vals[0])
        attrs = {kw: v[0] if len(v) == 1 else tuple(v) for kw, v in groups[1:]}
        lc = load_cases.setdefault(
            name,
            LoadCase(
                case_name=name,
                case_type="",
                design_type_option="Prog Det",
                design_type="",
                design_action_option="Prog Det",
                design_action="",
                run_case=True,
            ),
        )
        if "TYPE" in attrs:
            raw_type = str(attrs["TYPE"])
            lc.case_type = _CASE_TYPE_MAP.get(raw_type.upper(), raw_type)
        if "INITCOND" in attrs:
            lc.initial_condition = str(attrs["INITCOND"])
        if "MODALCASE" in attrs:
            lc.modal_case = str(attrs["MODALCASE"])
        if "LOADPAT" in attrs:
            lc.case_data.setdefault("CASE - STATIC 1 - LOAD ASSIGNMENTS", []).append(
                {"LoadName": str(attrs["LOADPAT"]), "LoadSF": _as_float(attrs.get("SF", 1.0))}
            )
        extra_skip = {"TYPE", "INITCOND", "MODALCASE", "LOADPAT"}
        if "LOADPAT" in attrs:
            extra_skip.add("SF")
        extra = {k: v for k, v in attrs.items() if k not in extra_skip}
        if extra:
            lc.case_data.setdefault("ETABS", {}).update(extra)
    return load_cases


def _get_mass_sources(
    sections: dict[str, list[GroupedLine]],
) -> dict[str, MassSource]:
    """Extract mass sources from ``$ MASS SOURCE`` (v19+).

    ``MASSSOURCE "name" INCLUDEELEMENTS … ISDEFAULT …`` starts a source (the
    ``Yes``/``No`` flags map to :class:`MassSource`'s ``elements`` / ``masses``
    / ``loads`` / ``is_default``); each ``MASSSOURCELOAD "name" "pattern"
    multiplier`` row adds a load-pattern multiplier (summed when a pattern
    repeats).
    """
    mass_sources: dict[str, MassSource] = {}
    for groups in sections.get("MASS SOURCE", []):
        if not groups:
            continue
        leading, vals = groups[0]
        attrs = {kw: v[0] if len(v) == 1 else tuple(v) for kw, v in groups[1:]}
        name = str(vals[0]) if vals else ""
        if not name:
            continue
        if leading == "MASSSOURCE":
            mass_sources[name] = MassSource(
                name=name,
                elements=_yes(attrs.get("INCLUDEELEMENTS")),
                masses=_yes(attrs.get("INCLUDEADDEDMASS")),
                loads=_yes(attrs.get("INCLUDELOADS")),
                is_default=_yes(attrs.get("ISDEFAULT")),
            )
        elif leading == "MASSSOURCELOAD":
            source = mass_sources.get(name)
            if source is not None and len(vals) >= 3:
                pattern = str(vals[1])
                mult = _as_float(vals[2])
                source.load_pattern[pattern] = source.load_pattern.get(pattern, 0.0) + mult
        else:
            logger.debug("Ignoring unrecognised MASS SOURCE record %r.", leading)
    return mass_sources


def _get_load_combinations(
    sections: dict[str, list[GroupedLine]],
) -> dict[str, LoadCombination]:
    """Extract load combinations from ``$ LOAD COMBINATIONS``.

    A combination spans a header line (``TYPE`` / ``DESIGN``) plus one
    ``LOAD … SF …`` line per reference; every line repeats the ``COMBO
    "name"`` leading pair, so lines are grouped by name with order and repeats
    preserved.
    """
    combos: dict[str, LoadCombination] = {}
    for groups in sections.get("LOAD COMBINATIONS", []):
        name: Optional[str] = None
        combo_type: Optional[str] = None
        design: Optional[str] = None
        combo_design_type: Optional[str] = None
        entry_name: Optional[str] = None
        entry_kind: Optional[str] = None
        sf = 1.0
        for kw, vals in groups:
            if kw == "COMBO":
                name = str(vals[0])
            elif kw == "TYPE":
                combo_type = str(vals[0])
            elif kw == "DESIGN":
                design = str(vals[0])
            elif kw == "COMBOTYPE":
                combo_design_type = str(vals[0])
            elif kw in _COMBO_ENTRY_KIND:
                entry_kind = kw
                entry_name = str(vals[0])
            elif kw == "SF":
                sf = _as_float(vals[0]) if vals else 1.0
        if name is None:
            continue
        combo = combos.setdefault(name, LoadCombination(name=name, combo_type="Linear Add"))
        if combo_type is not None:
            combo.combo_type = _COMBO_TYPE_MAP.get(combo_type.upper(), combo_type)
        if design is not None:
            combo.design["Design"] = design
        if combo_design_type is not None:
            combo.design["ComboType"] = combo_design_type
        if entry_name is not None and entry_kind is not None:
            combo.entries.append(
                LoadCombinationEntry(
                    name=entry_name,
                    factor=sf,
                    kind=_COMBO_ENTRY_KIND.get(entry_kind, "case"),
                )
            )
    return combos


def _iter_object_loads(
    sections: dict[str, list[GroupedLine]],
    section: str,
    keyword: str,
):
    """Yield ``(name, storey, attrs)`` for each ``(keyword) "name" "storey" …`` record."""
    for groups in sections.get(section, []):
        if not groups or groups[0][0] != keyword:
            continue
        vals = groups[0][1]
        name = str(vals[0])
        storey = str(vals[1]) if len(vals) > 1 else ""
        attrs: dict[str, Any] = {}
        for kw, v in groups[1:]:
            attrs[kw] = v[0] if len(v) == 1 else tuple(v)
        yield name, storey, attrs


def _iter_object_loads_any(
    sections: dict[str, list[GroupedLine]],
    section_names: tuple[str, ...],
    keyword: str,
):
    """Yield object loads from the first of *section_names* present in *sections*.

    Real ETABS exports name the frame/area load sections ``$ FRAME OBJECT
    LOADS`` / ``$ SHELL OBJECT LOADS``; the project's synthetic fixtures spell
    them ``$ LINE OBJECT LOADS`` / ``$ AREA OBJECT LOADS``.  Both use the same
    ``LINELOAD`` / ``AREALOAD`` record keyword, so both spellings are accepted.
    """
    for section in section_names:
        if section in sections:
            yield from _iter_object_loads(sections, section, keyword)
            return


# ETABS ``DIR`` numeric codes 1/2/3 are the frame *local* axes (1 = along the
# member, 2 = local 2, 3 = local 3); global directions are spelled ``X``/``Y``/
# ``Z`` and gravity is ``GRAV``.
_LOCAL_DIR_MAP = {"1": "LocalX", "2": "LocalY", "3": "LocalZ"}


def _map_direction(direction: Any) -> str:
    """Map an ETABS ``DIR`` token to the toolkit direction vocabulary.

    ``GRAV`` maps to ``"Gravity"``; the numeric local-axis codes ``1``/``2``/
    ``3`` map to ``"LocalX"``/``"LocalY"``/``"LocalZ"`` (the local 1/2/3 axes);
    everything else passes through unchanged.
    """
    d = str(direction).strip().upper()
    if d == "GRAV":
        return "Gravity"
    return _LOCAL_DIR_MAP.get(d, str(direction))


def _get_joint_loads(sections: dict[str, list[GroupedLine]]) -> list[JointLoad]:
    """Extract point forces from ``$ POINT OBJECT LOADS`` (``TYPE "FORCE"``)."""
    loads: list[JointLoad] = []
    for name, storey, attrs in _iter_object_loads(sections, "POINT OBJECT LOADS", "POINTLOAD"):
        ltype = str(attrs.get("TYPE", "")).upper()
        if ltype == "FORCE":
            loads.append(
                JointLoad(
                    pattern=str(attrs.get("LC", "")),
                    node_id=_compose_id(name, storey),
                    fx=_as_float(attrs.get("FX", 0.0)),
                    fy=_as_float(attrs.get("FY", 0.0)),
                    fz=_as_float(attrs.get("FZ", 0.0)),
                    mx=_as_float(attrs.get("MX", 0.0)),
                    my=_as_float(attrs.get("MY", 0.0)),
                    mz=_as_float(attrs.get("MZ", 0.0)),
                )
            )
        elif ltype in _DEFERRED_LOAD_TYPES:
            logger.debug("Point load %r at %r: %s loads are deferred.", name, storey, ltype)
        else:
            logger.warning("Unrecognised point load type %r at %r@%r.", ltype, name, storey)
    return loads


def _get_frame_distributed_loads(
    sections: dict[str, list[GroupedLine]],
) -> list[FrameDistributedLoad]:
    """Extract frame loads from ``$ FRAME OBJECT LOADS`` (or ``$ LINE OBJECT
    LOADS``) — ``UNIFF`` / ``TRAPF``."""
    loads: list[FrameDistributedLoad] = []
    for name, storey, attrs in _iter_object_loads_any(
        sections, ("FRAME OBJECT LOADS", "LINE OBJECT LOADS"), "LINELOAD"
    ):
        ltype = str(attrs.get("TYPE", "")).upper()
        pattern = str(attrs.get("LC", ""))
        frame_id = _compose_id(name, storey)
        direction = _map_direction(attrs.get("DIR"))
        if ltype == "UNIFF":
            val = _as_float(attrs.get("FVAL", 0.0))
            loads.append(
                FrameDistributedLoad(
                    pattern=pattern,
                    frame_id=frame_id,
                    direction=direction,
                    load_type="Force",
                    shape="Uniform",
                    val_a=val,
                    val_b=val,
                    rdist_a=0.0,
                    rdist_b=1.0,
                    dist_a=0.0,
                    dist_b=0.0,
                )
            )
        elif ltype == "TRAPF":
            val_a = _as_float(attrs.get("FSTART", 0.0))
            val_b = _as_float(attrs.get("FEND", 0.0))
            rdist_a = _as_float(attrs.get("RDSTART", 0.0))
            rdist_b = _as_float(attrs.get("RDEND", 1.0))
            shape = "Linear" if (rdist_a == 0.0 and rdist_b == 1.0) else "Trapezoidal"
            loads.append(
                FrameDistributedLoad(
                    pattern=pattern,
                    frame_id=frame_id,
                    direction=direction,
                    load_type="Force",
                    shape=shape,
                    val_a=val_a,
                    val_b=val_b,
                    rdist_a=rdist_a,
                    rdist_b=rdist_b,
                    dist_a=0.0,
                    dist_b=0.0,
                )
            )
        elif ltype == "UNIFM":
            # Uniform distributed *moment* along the frame (``MVAL`` is the
            # magnitude in [F·L/L] = force units; ``DIR`` is the moment axis).
            val = _as_float(attrs.get("MVAL", 0.0))
            loads.append(
                FrameDistributedLoad(
                    pattern=pattern,
                    frame_id=frame_id,
                    direction=direction,
                    load_type="Moment",
                    shape="Uniform",
                    val_a=val,
                    val_b=val,
                    rdist_a=0.0,
                    rdist_b=1.0,
                    dist_a=0.0,
                    dist_b=0.0,
                )
            )
        elif ltype in _DEFERRED_LOAD_TYPES:
            logger.debug("Line load %r at %r: %s loads are deferred.", name, storey, ltype)
        else:
            logger.warning("Unrecognised line load type %r at %r@%r.", ltype, name, storey)
    return loads


def _get_shell_uniform_load_sets(
    sections: dict[str, list[GroupedLine]],
) -> dict[str, ShellUniformLoadSet]:
    """Extract shell uniform load sets from ``$ SHELL UNIFORM LOAD SETS`` (v19+).

    A *uniform load set* is a named bundle of gravity-direction pressures keyed
    by load pattern — e.g. ``{"Roof": {"SDL": 0.0034, "Live": 0.00098}}``.  Each
    ``SHELLUNIFORMLOADSET "name" LOADPAT "pattern" VALUE value`` row adds one
    ``(pattern, value)`` pair to the named set.  Sets are assigned to shell
    objects via ``$ SHELL OBJECT LOADS`` ``TYPE "UNIFLOADSET" "name"`` (see
    :func:`_get_shell_uniform_load_set_assignments`); the reference is resolved
    into concrete :class:`AreaUniformLoad` records by
    :func:`_get_area_uniform_loads`.
    """
    sets: dict[str, ShellUniformLoadSet] = {}
    for groups in sections.get("SHELL UNIFORM LOAD SETS", []):
        if not groups or groups[0][0] != "SHELLUNIFORMLOADSET":
            continue
        vals = groups[0][1]
        if not vals:
            continue
        name = str(vals[0])
        attrs = {kw: v[0] if len(v) == 1 else tuple(v) for kw, v in groups[1:]}
        pattern = str(attrs.get("LOADPAT", ""))
        if not pattern:
            continue
        sets.setdefault(name, ShellUniformLoadSet(name=name)).load_pattern[pattern] = _as_float(
            attrs.get("VALUE", 0.0)
        )
    return sets


def _get_shell_uniform_load_set_assignments(
    sections: dict[str, list[GroupedLine]],
) -> dict[str, str]:
    """Map each shell object to the uniform load set it references.

    Reads ``$ SHELL OBJECT LOADS`` (or ``$ AREA OBJECT LOADS``) and, for every
    ``AREALOAD`` whose ``TYPE`` is ``("UNIFLOADSET", set_name)``, records
    ``composed area_id -> set_name`` (the same ``area_id`` used by
    :class:`AreaUniformLoad` and :attr:`SAPModelData.area_elements`).
    """
    assignments: dict[str, str] = {}
    for name, storey, attrs in _iter_object_loads_any(
        sections, ("SHELL OBJECT LOADS", "AREA OBJECT LOADS"), "AREALOAD"
    ):
        raw_type = attrs.get("TYPE")
        if not (isinstance(raw_type, tuple) and len(raw_type) == 2):
            continue
        if str(raw_type[0]).upper() != "UNIFLOADSET":
            continue
        assignments[_compose_id(name, storey)] = str(raw_type[1])
    return assignments


def _get_area_uniform_loads(
    sections: dict[str, list[GroupedLine]],
    load_sets: Optional[dict[str, ShellUniformLoadSet]] = None,
) -> list[AreaUniformLoad]:
    """Extract area pressures from ``$ SHELL OBJECT LOADS`` (or ``$ AREA OBJECT
    LOADS``) — ``TYPE "UNIFF"`` and ``TYPE "UNIFLOADSET"``.

    ``UNIFLOADSET`` references a named shell uniform load set (``$ SHELL
    UNIFORM LOAD SETS``): its ``TYPE`` keyword binds two values —
    ``("UNIFLOADSET", set_name)`` — and each ``(pattern, value)`` pair in that
    set becomes one gravity-direction :class:`AreaUniformLoad`.  ``load_sets``
    may be supplied to reuse an already-parsed mapping (the parser computes it
    once and also stores it as supplementary metadata); otherwise it is read
    from *sections*.
    """
    if load_sets is None:
        load_sets = _get_shell_uniform_load_sets(sections)
    loads: list[AreaUniformLoad] = []
    for name, storey, attrs in _iter_object_loads_any(
        sections, ("SHELL OBJECT LOADS", "AREA OBJECT LOADS"), "AREALOAD"
    ):
        raw_type = attrs.get("TYPE")
        if isinstance(raw_type, tuple):
            ltype = str(raw_type[0]).upper()
            set_name = str(raw_type[1]) if len(raw_type) > 1 else ""
        else:
            ltype = str(raw_type or "").upper()
            set_name = ""
        if ltype == "UNIFF":
            loads.append(
                AreaUniformLoad(
                    pattern=str(attrs.get("LC", "")),
                    area_id=_compose_id(name, storey),
                    direction=_map_direction(attrs.get("DIR")),
                    value=_as_float(attrs.get("FVAL", 0.0)),
                )
            )
        elif ltype == "UNIFLOADSET":
            entry = load_sets.get(set_name)
            if entry is None:
                logger.warning(
                    "Area load %r at %r references unknown uniform load set %r.",
                    name,
                    storey,
                    set_name,
                )
                continue
            for pattern, value in entry.load_pattern.items():
                loads.append(
                    AreaUniformLoad(
                        pattern=pattern,
                        area_id=_compose_id(name, storey),
                        direction="Gravity",
                        value=value,
                        source_set=set_name,
                    )
                )
        elif ltype in _DEFERRED_LOAD_TYPES:
            logger.debug("Area load %r at %r: %s loads are deferred.", name, storey, ltype)
        else:
            logger.warning("Unrecognised area load type %r at %r@%r.", ltype, name, storey)
    return loads
