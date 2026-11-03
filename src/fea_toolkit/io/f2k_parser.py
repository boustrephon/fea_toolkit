"""Parse CSi SAFE ``.f2k`` text exports into :class:`SAPModelData`.

SAFE's ``.f2k`` (File → Import/Export Tabular Database) is the SAFE analogue
of SAP2000's ``.s2k`` / ETABS's ``.e2k``.  It shares the ``TABLE: "…"`` +
``Key=Value`` grammar (with trailing ``_`` record continuation), but its object
vocabulary differs: **points** are the geometry primitive
(``POINT OBJECT CONNECTIVITY``); **floors** and **walls**
(``FLOOR/WALL OBJECT CONNECTIVITY``) are 4-node quads mapped to ``AreaElement``;
**null lines** (``NULL LINE OBJECT CONNECTIVITY``) are the beam/frame objects;
slab/deck/wall properties come from ``SLAB/DECK/WALL PROPERTY DEFINITIONS``;
point/line/area springs, tendons and design strips are SAFE-specific objects
captured on ``SAPModelData``'s additive fields; and SAFE does not pre-mesh
floors — the automatic-mesh intent is captured into
``SAPModelData.floor_mesh_options`` for the gmsh meshing stage.

This module imports nothing from the ``opensees`` package, so it is safe to
import inside Rhino 8.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional, Union

from ..model.load_combinations import classify_combination_refs
from ..model.sap_data import (
    SHAPE_NAMES,
    AreaElement,
    AreaSpring,
    AreaUniformLoad,
    ConcreteRectangularSection,
    DeckSection,
    DesignStrip,
    FrameElement,
    GeneralSection,
    Group,
    ISection,
    LineSpring,
    LoadCase,
    LoadCombination,
    LoadCombinationEntry,
    LoadPattern,
    MassSource,
    Material,
    Node,
    PointSpring,
    Restraint,
    SAPModelData,
    Section,
    ShellSection,
    TendonProperty,
    is_opening_section,
)
from ._table_text import parse_table_file, read_file_with_encodings

# CSI long-form case-type names → the toolkit's canonical short tokens
# (mirrors ``e2k_parser._CASE_TYPE_MAP``).  ``auto_detect_static_cases``
# matches the short form, so a SAFE case must land on ``LinStatic``.
_CASE_TYPE_MAP = {
    "LINEAR STATIC": "LinStatic",
    "NONLINEAR STATIC": "NonlinStatic",
    "MODAL - EIGEN": "LinModal",
    "RESPONSE SPECTRUM": "LinRespSpec",
}


def _normalize_case_type(value: Any) -> str:
    """Map a CSI case-type string to the toolkit's canonical token."""
    return _CASE_TYPE_MAP.get(str(value).strip().upper(), str(value))


class SAFEF2KParser:
    """Parse a SAFE ``.f2k`` file and convert it to :class:`SAPModelData`.

    Usage::

        model = SAFEF2KParser("model.f2k").parse().get_model_data()
    """

    def __init__(self, file_path: Union[str, Path]):
        self.file_path = Path(file_path)
        self._raw_tables: Optional[dict[str, list[dict[str, Any]]]] = None

    @property
    def raw_tables(self) -> dict[str, list[dict[str, Any]]]:
        """Public accessor for the raw parsed table data."""
        return self._raw_tables if self._raw_tables is not None else {}

    def parse(self, warn_unhandled: bool = False) -> SAFEF2KParser:
        """Parse the ``.f2k`` file and store raw tables internally."""
        content = read_file_with_encodings(self.file_path)
        self._raw_tables = parse_table_file(content)
        if warn_unhandled:
            self._warn_unhandled_tables()
        return self

    def _warn_unhandled_tables(self) -> None:
        """Log a warning for each table not recognised as SAFE handled/ignored."""
        from .safe_table_registry import safe_unhandled_tables

        for name, rows in sorted(safe_unhandled_tables(self.raw_tables).items()):
            logging.warning(
                "SAFE table %r (%d rows) is not recognised by fea_toolkit.",
                name,
                rows,
            )

    def table_coverage(self):
        """Triage the parsed tables into handled / ignored / unhandled."""
        from .safe_table_registry import safe_table_coverage

        if self._raw_tables is None:
            raise RuntimeError("No model data loaded — call parse() before table_coverage().")
        return safe_table_coverage(self._raw_tables)

    def get_model_units(self) -> dict[str, str]:
        """Extract ``{F, L, T}`` units from the ``PROGRAM CONTROL`` table."""
        pc = self._raw_tables.get("PROGRAM CONTROL", []) if self._raw_tables else []
        if not pc:
            return {"F": "N", "L": "m", "T": "C"}
        units_str = pc[0].get("CurrUnits", "")
        if not units_str:
            return {"F": "N", "L": "m", "T": "C"}
        force, length, temp = [p.strip() for p in str(units_str).split(",")]
        return {"F": force, "L": length, "T": temp}

    # ── JSON round-trip (raw tables) ─────────────────────────────────

    def to_json(self, output_path: Union[str, Path]) -> None:
        """Save raw tables to a JSON file."""
        with open(Path(output_path), "w") as f:
            json.dump(self._raw_tables, f, indent=2, default=str)

    @classmethod
    def from_json(cls, json_path: Union[str, Path]) -> SAFEF2KParser:
        """Create a parser pre-loaded from a JSON file."""
        parser = cls(json_path)
        with open(Path(json_path)) as f:
            parser._raw_tables = json.load(f)
        return parser

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        """Safely convert a value to float, or ``None`` on failure."""
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_bool(val: Any) -> bool:
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.lower() in ("yes", "true", "1")
        return bool(val)

    @staticmethod
    def _id(rec: dict[str, Any], keys: tuple[str, ...]) -> Any:
        """First non-empty id value among *keys* (SAFE's id spelling varies)."""
        for key in keys:
            value = rec.get(key)
            if value is not None and str(value) not in ("", "None", "N.A."):
                return value
        return None

    def _first_row(self, table: str, key: str, value: Any) -> dict[str, Any]:
        """First row of *table* whose *key* equals *value* (or ``{}``)."""
        for rec in self._raw_tables.get(table, []):
            cell = rec.get(key)
            if cell == value or str(cell) == str(value):
                return rec
        return {}

    # ── Extraction ──────────────────────────────────────────────────

    def _get_all_nodes(self) -> dict[str, Node]:
        """Nodes from ``POINT OBJECT CONNECTIVITY`` (SAFE points = joints)."""
        nodes: dict[str, Node] = {}
        tag = 1
        for rec in self._raw_tables.get("POINT OBJECT CONNECTIVITY", []):
            nid = self._id(rec, ("UniqueName", "Unique Name"))
            if nid is None:
                continue
            special = rec.get("IsSpecial", False)
            if isinstance(special, str):
                special = special.lower() == "yes"
            nodes[str(nid)] = Node(
                node_id=str(nid),
                node_tag=tag,
                x=float(rec.get("X", 0.0)),
                y=float(rec.get("Y", 0.0)),
                z=float(rec.get("Z", 0.0)),
                is_special=bool(special),
            )
            tag += 1
        return nodes

    def _get_all_restraints(self) -> dict[str, Restraint]:
        """Point restraints (empty for a typical ``.f2k``; SAFE uses springs)."""
        restraints: dict[str, Restraint] = {}
        for table in ("POINT RESTRAINT ASSIGNMENTS", "JOINT RESTRAINT ASSIGNMENTS"):
            for rec in self._raw_tables.get(table, []):
                nid = self._id(rec, ("Point", "Joint", "UniqueName", "Unique Name"))
                if nid is None:
                    continue
                dofs: list[int] = []
                for dof in ("Ux", "Uy", "Uz", "Rx", "Ry", "Rz"):
                    val = rec.get(dof, False)
                    if isinstance(val, str):
                        val = val.lower() in ("yes", "true", "1")
                    dofs.append(1 if val else 0)
                restraints[str(nid)] = Restraint(dofs=dofs)
        return restraints

    def _get_all_materials(self) -> dict[str, Material]:
        """Merge every ``MATERIAL PROPERTIES - *`` table keyed by ``Material``."""
        data: dict[str, dict[str, Any]] = {}
        for table_name, records in self._raw_tables.items():
            if not table_name.startswith("MATERIAL PROPERTIES"):
                continue
            for rec in records:
                name = rec.get("Material")
                if not name:
                    continue
                d = data.setdefault(str(name), {})
                for key, value in rec.items():
                    if key != "Material":
                        d[key] = value
        materials: dict[str, Material] = {}
        for name, props in data.items():
            materials[name] = Material(
                name=name,
                type=str(props.get("Type", "")),
                grade=props.get("Grade"),
                E_mod=self._to_float(props.get("E1", 0.0)) or 0.0,
                G_mod=self._to_float(props.get("G12", 0.0)) or 0.0,
                nu=self._to_float(props.get("U12", 0.0)) or 0.0,
                unit_weight=self._to_float(props.get("UnitWeight", 0.0)) or 0.0,
                unit_mass=self._to_float(props.get("UnitMass", 0.0)) or 0.0,
                Fy=self._to_float(props.get("Fy")),
                Fu=self._to_float(props.get("Fu")),
                Fc=self._to_float(props.get("Fc")),
            )
        return materials

    def _get_sections(self) -> dict[str, Section]:
        """Area (slab/wall/deck) sections + frame sections."""
        sections: dict[str, Section] = {}
        for rec in self._raw_tables.get("AREA SECTION PROPERTY DEFINITIONS - SUMMARY", []):
            name = rec.get("Name")
            if not name:
                continue
            sec_type = str(rec.get("Type", "Slab"))
            element_type = str(rec.get("Element Type", "Shell-Thin"))
            material = str(rec.get("Material", ""))
            thickness = self._to_float(rec.get("Total Thickness", 0.0)) or 0.0
            if sec_type == "Deck":
                sections[str(name)] = DeckSection(
                    name=str(name),
                    shape="Deck",
                    material=material,
                    thickness=thickness,
                    shell_type=element_type,
                    deck_material=rec.get("Deck Material"),
                    deck_depth=self._to_float(rec.get("Deck Depth", 0.0)) or 0.0,
                )
            else:
                sections[str(name)] = ShellSection(
                    name=str(name),
                    shape=sec_type,
                    material=material,
                    thickness=thickness,
                    shell_type=element_type,
                )
        for rec in self._raw_tables.get("SLAB PROPERTY DEFINITIONS", []):
            sec = sections.get(str(rec.get("Name", "")))
            if sec is None:
                continue
            mods = {}
            for key in (
                "f11 Modifier",
                "f22 Modifier",
                "f12 Modifier",
                "m11 Modifier",
                "m22 Modifier",
                "m12 Modifier",
                "v13 Modifier",
                "v23 Modifier",
                "Mass Modifier",
                "Weight Modifier",
            ):
                value = self._to_float(rec.get(key))
                if value is not None:
                    mods[key] = value
            sec.modifiers = mods
        sections.update(self._get_frame_sections())
        return sections

    def _get_frame_sections(self) -> dict[str, Section]:
        """Frame sections from the ``FRAME SECTION PROPERTY DEFINITIONS`` family."""
        sections: dict[str, Section] = {}
        for rec in self._raw_tables.get("FRAME SECTION PROPERTY DEFINITIONS - SUMMARY", []):
            name = str(rec.get("Name", ""))
            if not name:
                continue
            shape = str(rec.get("Shape", ""))
            material = str(rec.get("Material", ""))
            area = self._to_float(rec.get("Area", 0.0)) or 0.0
            j = self._to_float(rec.get("J", 0.0)) or 0.0
            i33 = self._to_float(rec.get("I33", 0.0)) or 0.0
            i22 = self._to_float(rec.get("I22", 0.0)) or 0.0
            canonical = SHAPE_NAMES.get(shape, "GEN")
            if canonical == "I":
                dims = self._first_row(
                    "FRAME SECTION PROPERTY DEFINITIONS - STEEL I/WIDE FLANGE", "Name", name
                )
                sections[name] = ISection(
                    name=name,
                    shape=shape,
                    material=material,
                    A=area,
                    I33=i33,
                    I22=i22,
                    J=j,
                    depth=self._to_float(dims.get("Total Depth", 0.0)) or 0.0,
                    bf=self._to_float(dims.get("Top Flange Width", 0.0)) or 0.0,
                    tf=self._to_float(dims.get("Top Flange Thickness", 0.0)) or 0.0,
                    tw=self._to_float(dims.get("Web Thickness", 0.0)) or 0.0,
                )
            elif canonical == "R":
                dims = self._first_row(
                    "FRAME SECTION PROPERTY DEFINITIONS - CONCRETE RECTANGULAR", "Name", name
                )
                sections[name] = ConcreteRectangularSection(
                    name=name,
                    shape=shape,
                    material=material,
                    A=area,
                    I33=i33,
                    I22=i22,
                    J=j,
                    depth=self._to_float(dims.get("Depth", 0.0)) or 0.0,
                    bf=self._to_float(dims.get("Width", 0.0)) or 0.0,
                    cover=self._to_float(dims.get("Cover Top", 0.0)) or 0.0,
                    rebar_material=dims.get("Longitudinal Rebar Material"),
                )
            else:
                sections[name] = GeneralSection(
                    name=name, shape=shape, material=material, A=area, I33=i33, I22=i22, J=j
                )
        return sections

    def _get_frame_elements(self) -> dict[str, FrameElement]:
        """Frame elements from ``NULL LINE OBJECT CONNECTIVITY``."""
        elements: dict[str, FrameElement] = {}
        tag = 1
        for rec in self._raw_tables.get("NULL LINE OBJECT CONNECTIVITY", []):
            eid = self._id(rec, ("Unique Name", "UniqueName"))
            if eid is None:
                continue
            elements[str(eid)] = FrameElement(
                elem_id=str(eid),
                elem_tag=tag,
                node_i=str(rec.get("UniquePtI", "")),
                node_j=str(rec.get("UniquePtJ", "")),
                angle=0.0,
            )
            tag += 1
        return elements

    def _get_area_elements(self) -> dict[str, AreaElement]:
        """Area elements from ``FLOOR`` / ``WALL OBJECT CONNECTIVITY``."""
        areas: dict[str, AreaElement] = {}
        tag = 1
        for table_name in ("FLOOR OBJECT CONNECTIVITY", "WALL OBJECT CONNECTIVITY"):
            for rec in self._raw_tables.get(table_name, []):
                aid = self._id(rec, ("Unique Name", "UniqueName"))
                if aid is None:
                    continue
                node_ids = [str(rec.get(f"UniquePt{i}", "")) for i in (1, 2, 3, 4)]
                areas[str(aid)] = AreaElement(area_id=str(aid), area_tag=tag, node_ids=node_ids)
                tag += 1
        return areas

    def _get_area_roles(self) -> dict[str, str]:
        """SAFE-authored object roles: area_id -> ``"floor"`` | ``"wall"``."""
        roles: dict[str, str] = {}
        for table_name in ("FLOOR OBJECT CONNECTIVITY", "WALL OBJECT CONNECTIVITY"):
            role = "floor" if "FLOOR" in table_name else "wall"
            for rec in self._raw_tables.get(table_name, []):
                aid = self._id(rec, ("Unique Name", "UniqueName"))
                if aid is not None:
                    roles[str(aid)] = role
        return roles

    def _get_frame_assignments(self) -> dict[str, str]:
        """Frame -> section name (``FRAME ASSIGNMENTS - SECTION PROPERTIES``)."""
        assign: dict[str, str] = {}
        for rec in self._raw_tables.get("FRAME ASSIGNMENTS - SECTION PROPERTIES", []):
            fid = self._id(rec, ("UniqueName", "Unique Name"))
            if fid is None:
                continue
            sec = rec.get("Section Property")
            if sec is None or str(sec) in ("", "None", "N.A."):
                continue
            assign[str(fid)] = str(sec)
        return assign

    def _get_area_assignments(self) -> dict[str, str]:
        """Area -> section name (``AREA ASSIGNMENTS - SECTION PROPERTIES``)."""
        assign: dict[str, str] = {}
        for rec in self._raw_tables.get("AREA ASSIGNMENTS - SECTION PROPERTIES", []):
            aid = self._id(rec, ("UniqueName", "Unique Name"))
            if aid is None:
                continue
            sec = rec.get("Section Property")
            if sec is None or str(sec) in ("", "None"):
                continue
            assign[str(aid)] = str(sec)
        return assign

    def _get_opening_area_ids(self) -> set[str]:
        """Area ids whose section is ``None`` / ``NONE`` (voids/opening)."""
        opening: set[str] = set()
        for rec in self._raw_tables.get("AREA ASSIGNMENTS - SECTION PROPERTIES", []):
            aid = self._id(rec, ("UniqueName", "Unique Name"))
            if aid is None:
                continue
            sec = rec.get("Section Property")
            if sec is None or is_opening_section(sec):
                opening.add(str(aid))
        return opening

    def _get_groups(self) -> dict[str, Group]:
        groups: dict[str, Group] = {}
        for rec in self._raw_tables.get("GROUP DEFINITIONS", []):
            name = rec.get("Name")
            if not name:
                continue
            groups[str(name)] = Group(name=str(name), color=rec.get("Color"))
        return groups

    def _get_load_patterns(self) -> dict[str, LoadPattern]:
        patterns: dict[str, LoadPattern] = {}
        for rec in self._raw_tables.get("LOAD PATTERN DEFINITIONS", []):
            name = rec.get("Name")
            if not name:
                continue
            patterns[str(name)] = LoadPattern(
                name=str(name),
                pattern_type=str(rec.get("Type", "")),
                self_weight_factor=self._to_float(rec.get("Self Weight Multiplier", 0.0)) or 0.0,
            )
        return patterns

    def _get_mass_sources(self) -> dict[str, MassSource]:
        sources: dict[str, MassSource] = {}
        for rec in self._raw_tables.get("MASS SOURCE DEFINITION", []):
            name = rec.get("Name")
            if not name:
                continue
            sources[str(name)] = MassSource(
                name=str(name),
                elements=self._to_bool(rec.get("Source Self Mass?", False)),
                masses=self._to_bool(rec.get("Source Added Mass?", False)),
                loads=self._to_bool(rec.get("Source Load Patterns?", False)),
                is_default=self._to_bool(rec.get("Is Default", False)),
            )
        return sources

    def _get_area_uniform_loads(self) -> list[AreaUniformLoad]:
        loads: list[AreaUniformLoad] = []
        for rec in self._raw_tables.get("AREA LOAD ASSIGNMENTS - UNIFORM", []):
            aid = self._id(rec, ("UniqueName", "Unique Name"))
            if aid is None:
                continue
            loads.append(
                AreaUniformLoad(
                    pattern=str(rec.get("Load Pattern", "")),
                    area_id=str(aid),
                    direction=str(rec.get("Direction", "Gravity")),
                    value=self._to_float(rec.get("Load", 0.0)) or 0.0,
                )
            )
        return loads

    def get_load_cases(self) -> dict[str, LoadCase]:
        cases: dict[str, LoadCase] = {}
        types = {
            str(r.get("Name")): r.get("Type")
            for r in self._raw_tables.get("LOAD CASE DEFINITIONS - SUMMARY", [])
            if r.get("Name") is not None
        }
        assigns: dict[str, list[dict[str, Any]]] = {}
        for rec in self._raw_tables.get("LOAD CASE DEFINITIONS - LINEAR STATIC", []):
            name = str(rec.get("Name", ""))
            if not name:
                continue
            assigns.setdefault(name, []).append(
                {
                    "LoadName": rec.get("Load Name", ""),
                    "LoadSF": self._to_float(rec.get("Load SF", 1.0)) or 1.0,
                }
            )
            if name not in cases:
                cases[name] = LoadCase(
                    case_name=name,
                    case_type=_normalize_case_type(types.get(name, "Linear Static")),
                    design_type_option=str(rec.get("Design Type", "Program Determined")),
                    design_type=str(rec.get("Design Type", "Program Determined")),
                    design_action_option="Prog Det",
                    design_action="",
                    initial_condition=str(rec.get("Initial Condition", "Zero")),
                )
        for name, case in cases.items():
            if name in assigns:
                case.case_data["CASE - STATIC 1 - LOAD ASSIGNMENTS"] = assigns[name]
        return cases

    def _get_load_combinations(self, load_cases: dict[str, LoadCase]) -> dict[str, LoadCombination]:
        combos: dict[str, LoadCombination] = {}
        for rec in self._raw_tables.get("LOAD COMBINATION DEFINITIONS", []):
            name = str(rec.get("Name", ""))
            if not name:
                continue
            if name not in combos:
                combos[name] = LoadCombination(
                    name=name, combo_type=str(rec.get("Type", "Linear Add"))
                )
            entry_name = rec.get("Load Name")
            if entry_name is not None:
                combos[name].entries.append(
                    LoadCombinationEntry(
                        name=str(entry_name), factor=self._to_float(rec.get("SF", 1.0)) or 1.0
                    )
                )
        classify_combination_refs(combos, load_cases)
        return combos

    def _get_point_springs(self) -> tuple[dict[str, PointSpring], dict[str, str]]:
        defs: dict[str, PointSpring] = {}
        for rec in self._raw_tables.get("SPRING PROPERTY DEFINITIONS - POINT SPRINGS", []):
            name = rec.get("Name")
            if not name:
                continue
            stiffness = {}
            for dof in ("UX", "UY", "UZ", "RX", "RY", "RZ"):
                value = self._to_float(rec.get(f"Stiffness {dof}"))
                if value is not None:
                    stiffness[dof] = value
            defs[str(name)] = PointSpring(
                name=str(name),
                stiffness=stiffness,
                nonlinear_option=str(rec.get("Nonlinear Option", "None (Linear)")),
            )
        assigns: dict[str, str] = {}
        for rec in self._raw_tables.get("JOINT ASSIGNMENTS - SPRINGS", []):
            nid = self._id(rec, ("UniqueName", "Unique Name"))
            if nid is not None:
                assigns[str(nid)] = str(rec.get("SpringProp", ""))
        return defs, assigns

    def _get_line_springs(self) -> tuple[dict[str, LineSpring], dict[str, str]]:
        defs: dict[str, LineSpring] = {}
        for rec in self._raw_tables.get("SPRING PROPERTY DEFINITIONS - LINE SPRINGS", []):
            name = rec.get("Name")
            if not name:
                continue
            defs[str(name)] = LineSpring(
                name=str(name),
                vertical_stiffness=self._to_float(rec.get("Vertical Stffness", 0.0)) or 0.0,
                rotational_stiffness=self._to_float(rec.get("Rotational Stffness", 0.0)) or 0.0,
                nonlinear_option=str(rec.get("Nonlinear Option", "None (Linear)")),
            )
        assigns: dict[str, str] = {}
        for rec in self._raw_tables.get("FRAME ASSIGNMENTS - LINE SPRINGS", []):
            fid = self._id(rec, ("UniqueName", "Unique Name"))
            if fid is not None:
                assigns[str(fid)] = str(rec.get("Spring Property", ""))
        return defs, assigns

    def _get_area_springs(self) -> tuple[dict[str, AreaSpring], dict[str, str]]:
        defs: dict[str, AreaSpring] = {}
        for rec in self._raw_tables.get("SPRING PROPERTY DEFINITIONS - AREA SPRINGS", []):
            name = rec.get("Name")
            if not name:
                continue
            stiffness = {}
            nonlinear = {}
            for dof in ("U1", "U2", "U3"):
                value = self._to_float(rec.get(f"Stiffnes {dof}"))
                if value is not None:
                    stiffness[dof] = value
                opt = rec.get(f"Nonlinear Option for {dof}")
                if opt is not None:
                    nonlinear[dof] = str(opt)
            defs[str(name)] = AreaSpring(
                name=str(name), stiffness=stiffness, nonlinear_options=nonlinear
            )
        assigns: dict[str, str] = {}
        for rec in self._raw_tables.get("AREA ASSIGNMENTS - AREA SPRINGS", []):
            aid = self._id(rec, ("UniqueName", "Unique Name"))
            if aid is not None:
                assigns[str(aid)] = str(rec.get("Spring Property", ""))
        return defs, assigns

    def _get_tendons(self) -> dict[str, TendonProperty]:
        tendons: dict[str, TendonProperty] = {}
        for rec in self._raw_tables.get("TENDON SECTION PROPERTIES", []):
            name = rec.get("Name")
            if not name:
                continue
            tendons[str(name)] = TendonProperty(
                name=str(name),
                material=str(rec.get("Material", "")),
                strand_area=self._to_float(rec.get("StrandArea", 0.0)) or 0.0,
            )
        return tendons

    def _get_design_strips(self) -> dict[str, DesignStrip]:
        strips: dict[str, DesignStrip] = {}
        for rec in self._raw_tables.get("STRIP OBJECT CONNECTIVITY", []):
            name = rec.get("Name")
            if not name:
                continue
            name = str(name)
            if name not in strips:
                strips[name] = DesignStrip(
                    name=name,
                    layer=str(rec.get("Layer", "A")),
                    auto_widen=self._to_bool(rec.get("Auto Widen", False)),
                )
            strips[name].segments.append(
                {
                    "start_point": str(rec.get("Strip Start Point", "")),
                    "end_point": str(rec.get("Segment End Point", "")),
                    "start_width_left": self._to_float(rec.get("Start Width Left", 0.0)) or 0.0,
                    "start_width_right": self._to_float(rec.get("Start Width Right", 0.0)) or 0.0,
                    "end_width_left": self._to_float(rec.get("End Width Left", 0.0)) or 0.0,
                    "end_width_right": self._to_float(rec.get("End Width Right", 0.0)) or 0.0,
                }
            )
        return strips

    def _get_floor_mesh_options(self) -> dict[str, Any]:
        opts: dict[str, Any] = {}
        for rec in self._raw_tables.get(
            "ANALYSIS OPTIONS - AUTOMATIC MESH SETTINGS FOR FLOORS", []
        ):
            opts["mesh_option"] = str(rec.get("Mesh Option", "General"))
            value = self._to_float(rec.get("Maximum Mesh Size"))
            if value is not None:
                opts["max_size"] = value
        per_area: dict[str, dict[str, Any]] = {}
        for rec in self._raw_tables.get("AREA ASSIGNMENTS - FLOOR AUTO MESH OPTIONS", []):
            aid = self._id(rec, ("UniqueName", "Unique Name"))
            if aid is None:
                continue
            per_area[str(aid)] = {
                "mesh_option": str(rec.get("Mesh Option", "Default")),
                "add_restraints": self._to_bool(rec.get("Add Restraints", False)),
            }
        if per_area:
            opts["per_area"] = per_area
        for rec in self._raw_tables.get(
            "ANALYSIS OPTIONS - AUTOMATIC RECTANGULAR MESH OPTIONS FOR WALLS", []
        ):
            value = self._to_float(rec.get("Maximum Mesh Size"))
            if value is not None:
                opts["wall_max_size"] = value
        return opts

    def _get_frame_auto_mesh(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for rec in self._raw_tables.get("FRAME ASSIGNMENTS - FRAME AUTO MESH OPTIONS", []):
            fid = self._id(rec, ("UniqueName", "Unique Name"))
            if fid is None:
                continue
            result[str(fid)] = {
                k: v for k, v in rec.items() if k not in ("UniqueName", "Unique Name")
            }
        return result

    def get_model_data(self) -> SAPModelData:
        """Convert the raw tables into a :class:`SAPModelData`."""
        if self._raw_tables is None:
            raise RuntimeError("No model data loaded — call parse() before get_model_data().")

        area_assignments = self._get_area_assignments()
        sections = self._get_sections()
        opening_ids = self._get_opening_area_ids()
        area_elements = self._get_area_elements()
        for aid, elem in area_elements.items():
            sec_name = area_assignments.get(aid)
            if sec_name:
                sec = sections.get(sec_name)
                if isinstance(sec, ShellSection):
                    elem.thickness = sec.thickness
            if aid in opening_ids:
                elem.opening = True

        load_cases = self.get_load_cases()
        point_springs, point_spring_assignments = self._get_point_springs()
        line_springs, line_spring_assignments = self._get_line_springs()
        area_springs, area_spring_assignments = self._get_area_springs()

        md = SAPModelData(
            nodes=self._get_all_nodes(),
            restraints=self._get_all_restraints(),
            materials=self._get_all_materials(),
            sections=sections,
            frame_elements=self._get_frame_elements(),
            area_elements=area_elements,
            frame_assignments=self._get_frame_assignments(),
            area_assignments=area_assignments,
            groups=self._get_groups(),
            frame_auto_mesh=self._get_frame_auto_mesh(),
            load_patterns=self._get_load_patterns(),
            mass_sources=self._get_mass_sources(),
            area_uniform_loads=self._get_area_uniform_loads(),
            load_cases=load_cases,
            load_combinations=self._get_load_combinations(load_cases),
            point_springs=point_springs,
            point_spring_assignments=point_spring_assignments,
            line_springs=line_springs,
            line_spring_assignments=line_spring_assignments,
            area_springs=area_springs,
            area_spring_assignments=area_spring_assignments,
            tendons=self._get_tendons(),
            design_strips=self._get_design_strips(),
            area_roles=self._get_area_roles(),
            floor_mesh_options=self._get_floor_mesh_options(),
            units=self.get_model_units(),
            source=str(self.file_path),
        )
        md.apply_material_defaults()
        return md
