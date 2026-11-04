"""Parse CSi SAFE ``.$sf`` native-database text exports into :class:`SAPModelData`.

A ``.$sf`` (File → Import SAFE Text Database) is SAFE's *native* flat-file
format — older and lower-level than the ``.f2k`` tabular export.  It shares the
``TABLE: "…"`` + ``Key=Value`` line grammar with ``.f2k`` (CRLF line endings,
a trailing ``END TABLE DATA`` marker, no ``_`` continuations), so it reuses the
shared tokeniser — but its table names and key spellings are all different, and
its records have one structural quirk:

* **Areas may span several physical lines** (``OBJECT GEOMETRY - AREAS 01 -
  GENERAL``).  A polygon with more than four corners repeats the leading
  ``Area=`` key on each line, one ``Point1..Point4`` run per line, so the
  parser must *merge* consecutive same-id rows into one vertex list.
* **Design strips** (``OBJECT GEOMETRY - DESIGN STRIPS``) write one row per
  strip *end* (an ``A`` side with ``WALeft``/``WARight`` and a ``B`` side with
  ``WBLeft``/``WBRight``), not per segment.

Everything else maps onto the same :class:`SAPModelData` shape as the
:mod:`fea_toolkit.io.f2k_parser` — points→nodes, areas→``AreaElement``,
lines→``FrameElement`` (beams via ``BEAM PROPERTY ASSIGNMENTS``), slab/wall/
beam/column properties→sections, restraints/springs/loads/cases/combos/mass,
design strips and punching-shear overwrites.

This module imports nothing from the ``opensees`` package.
"""

from __future__ import annotations

import logging
from typing import Any, Union

from ..model.sap_data import (
    AreaElement,
    AreaUniformLoad,
    ConcreteRectangularSection,
    DesignStrip,
    FrameDistributedLoad,
    FrameElement,
    Group,
    JointLoad,
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
    is_null_section,
)
from .f2k_parser import SAFEF2KParser, _normalize_case_type
from .safe_table_registry import safe_db_table_coverage, safe_db_unhandled_tables


def _rect_props(depth: float, bf: float) -> tuple[float, float, float]:
    """Rectangular section ``(A, I33, I22)`` from depth (local 3) / width (local 2)."""
    return depth * bf, bf * depth**3 / 12.0, depth * bf**3 / 12.0


class SAFEDBParser(SAFEF2KParser):
    """Parse a SAFE ``.$sf`` native text database into :class:`SAPModelData`."""

    def _warn_unhandled_tables(self) -> None:
        for name, rows in sorted(safe_db_unhandled_tables(self.raw_tables).items()):
            logging.warning("SAFE .$sf table %r (%d rows) is not recognised.", name, rows)

    def table_coverage(self):
        if self._raw_tables is None:
            raise RuntimeError("No model data loaded — call parse() before table_coverage().")
        return safe_db_table_coverage(self._raw_tables)

    # ── Geometry ─────────────────────────────────────────────────────

    def _get_all_nodes(self) -> dict[str, Node]:
        nodes: dict[str, Node] = {}
        tag = 1
        for rec in self._raw_tables.get("OBJECT GEOMETRY - POINT COORDINATES", []):
            pid = rec.get("Point")
            if pid is None:
                continue
            special = rec.get("SpecialPt", False)
            if isinstance(special, str):
                special = special.lower() == "yes"
            nodes[str(pid)] = Node(
                node_id=str(pid),
                node_tag=tag,
                x=float(rec.get("GlobalX", 0.0)),
                y=float(rec.get("GlobalY", 0.0)),
                z=float(rec.get("GlobalZ", 0.0)),
                is_special=bool(special),
            )
            tag += 1
        return nodes

    def _get_all_restraints(self) -> dict[str, Restraint]:
        restraints: dict[str, Restraint] = {}
        for rec in self._raw_tables.get("POINT RESTRAINT ASSIGNMENTS", []):
            pid = rec.get("Point")
            if pid is None:
                continue
            dofs = [
                1 if self._to_bool(rec.get(d, False)) else 0
                for d in ("Ux", "Uy", "Uz", "Rx", "Ry", "Rz")
            ]
            restraints[str(pid)] = Restraint(dofs=dofs)
        return restraints

    def _get_all_materials(self) -> dict[str, Material]:
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
                E_mod=self._to_float(props.get("E", 0.0)) or 0.0,
                nu=self._to_float(props.get("U", 0.0)) or 0.0,
                unit_weight=self._to_float(props.get("UnitWt", 0.0)) or 0.0,
                Fy=self._to_float(props.get("Fy")),
                Fu=self._to_float(props.get("Fu")),
                Fc=self._to_float(props.get("Fc")),
            )
        return materials

    def _get_sections(self) -> dict[str, Section]:
        sections: dict[str, Section] = {}

        # Slab (and drop/stiff) sections — thickness from SOLID SLABS, type from GENERAL.
        slab_types = {
            str(r.get("Slab")): r.get("Type")
            for r in self._raw_tables.get("SLAB PROPERTIES 01 - GENERAL", [])
            if r.get("Slab") is not None
        }
        for rec in self._raw_tables.get("SLAB PROPERTIES 02 - SOLID SLABS", []):
            name = rec.get("Slab")
            if not name:
                continue
            sections[str(name)] = ShellSection(
                name=str(name),
                shape=str(slab_types.get(str(name), "Slab")),
                material=str(rec.get("MatProp", "")),
                thickness=self._to_float(rec.get("Thickness", 0.0)) or 0.0,
                shell_type="Shell-Thin",
            )

        # Wall sections.
        for rec in self._raw_tables.get("WALL PROPERTIES", []):
            name = rec.get("Wall")
            if not name:
                continue
            sections[str(name)] = ShellSection(
                name=str(name),
                shape="Wall",
                material=str(rec.get("MatProp", "")),
                thickness=self._to_float(rec.get("Thickness", 0.0)) or 0.0,
                shell_type="Shell-Thin",
            )

        # Beam sections (rectangular) + rebar material / cover from DESIGN DATA.
        design = {
            str(r.get("Beam")): r
            for r in self._raw_tables.get("BEAM PROPERTIES 06 - DESIGN DATA", [])
            if r.get("Beam") is not None
        }
        for rec in self._raw_tables.get("BEAM PROPERTIES 02 - RECTANGULAR BEAM", []):
            name = str(rec.get("Beam", ""))
            if not name:
                continue
            depth = self._to_float(rec.get("Depth", 0.0)) or 0.0
            bf = self._to_float(rec.get("WidthTop", 0.0)) or 0.0
            area, i33, i22 = _rect_props(depth, bf)
            dims = design.get(name, {})
            sections[name] = ConcreteRectangularSection(
                name=name,
                shape="Concrete Rectangular",
                material=str(rec.get("MatProp", "")),
                A=area,
                I33=i33,
                I22=i22,
                depth=depth,
                bf=bf,
                cover=self._to_float(dims.get("CoverTop", 0.0)) or 0.0,
                rebar_material=dims.get("MatRebarL"),
            )

        # Column sections (rectangular).
        for rec in self._raw_tables.get("COLUMN PROPERTIES 02 - RECTANGULAR", []):
            name = str(rec.get("Column", ""))
            if not name:
                continue
            depth = self._to_float(rec.get("SecDim3", 0.0)) or 0.0
            bf = self._to_float(rec.get("SecDim2", 0.0)) or 0.0
            area, i33, i22 = _rect_props(depth, bf)
            sections[name] = ConcreteRectangularSection(
                name=name,
                shape="Concrete Rectangular",
                material=str(rec.get("MatProp", "")),
                A=area,
                I33=i33,
                I22=i22,
                depth=depth,
                bf=bf,
            )
        return sections

    def _get_frame_elements(self) -> dict[str, FrameElement]:
        """Frame elements from ``OBJECT GEOMETRY - LINES 01 - GENERAL``."""
        elements: dict[str, FrameElement] = {}
        tag = 1
        for rec in self._raw_tables.get("OBJECT GEOMETRY - LINES 01 - GENERAL", []):
            lid = rec.get("Line")
            if lid is None:
                continue
            elements[str(lid)] = FrameElement(
                elem_id=str(lid),
                elem_tag=tag,
                node_i=str(rec.get("PointI", "")),
                node_j=str(rec.get("PointJ", "")),
                angle=0.0,
            )
            tag += 1
        return elements

    def _get_area_elements(self) -> dict[str, AreaElement]:
        """Areas from ``OBJECT GEOMETRY - AREAS 01 - GENERAL``.

        A polygon with more than four corners spans several physical rows that
        repeat the leading ``Area=`` key; consecutive same-id rows are merged
        into one vertex list.
        """
        areas: dict[str, AreaElement] = {}
        tag = 1
        current_id: Union[str, None] = None
        current_points: list[str] = []

        def _flush() -> None:
            nonlocal tag
            if current_id is not None and current_points:
                areas[current_id] = AreaElement(
                    area_id=current_id, area_tag=tag, node_ids=current_points
                )
                tag += 1

        for rec in self._raw_tables.get("OBJECT GEOMETRY - AREAS 01 - GENERAL", []):
            aid = rec.get("Area")
            if aid is None:
                continue
            aid = str(aid)
            if aid != current_id:
                _flush()
                current_id = aid
                current_points = []
            for i in (1, 2, 3, 4):
                point = rec.get(f"Point{i}")
                if point is not None:
                    current_points.append(str(point))
        _flush()
        return areas

    def _get_area_roles(self) -> dict[str, str]:
        # The native .$sf area table carries no floor/wall distinction; the
        # Preprocessor classifies by geometry instead.
        return {}

    def _get_frame_assignments(self) -> dict[str, str]:
        assign: dict[str, str] = {}
        for rec in self._raw_tables.get("BEAM PROPERTY ASSIGNMENTS", []):
            lid = rec.get("Line")
            beam = rec.get("BeamProp")
            if lid is None or beam is None or str(beam) in ("", "None", "N.A."):
                continue
            assign[str(lid)] = str(beam)
        return assign

    def _get_area_assignments(self) -> dict[str, str]:
        assign: dict[str, str] = {}
        for rec in self._raw_tables.get("SLAB PROPERTY ASSIGNMENTS", []):
            aid = rec.get("Area")
            slab = rec.get("SlabProp")
            if aid is None or slab is None or str(slab) in ("", "None"):
                continue
            assign[str(aid)] = str(slab)
        return assign

    def _get_null_area_ids(self) -> set[str]:
        """Area ids whose slab property is ``None`` / ``NONE`` (geometric).

        An *unloaded* opening (``OpeningType=Unloaded``) is a geometric void,
        not a null area — it is handled by :meth:`_get_opening_ids`.
        """
        null_ids: set[str] = set()
        for rec in self._raw_tables.get("SLAB PROPERTY ASSIGNMENTS", []):
            aid = rec.get("Area")
            if aid is None:
                continue
            slab = rec.get("SlabProp")
            if slab is not None and not is_null_section(slab):
                continue
            if str(rec.get("OpeningType", "") or "").strip().lower() != "unloaded":
                null_ids.add(str(aid))
        return null_ids

    def _get_opening_ids(self) -> set[str]:
        """Area ids flagged ``OpeningType=Unloaded`` — geometric voids.

        SAFE marks an *unloaded* opening as a void to be cut out of the slab; a
        *loaded* opening (``OpeningType=Loaded``) stays a null load patch (its
        pressure transfers to the enclosing slab) and is *not* an opening.
        """
        opening_ids: set[str] = set()
        for rec in self._raw_tables.get("SLAB PROPERTY ASSIGNMENTS", []):
            aid = rec.get("Area")
            if aid is None:
                continue
            if str(rec.get("OpeningType", "") or "").strip().lower() == "unloaded":
                opening_ids.add(str(aid))
        return opening_ids

    def _get_groups(self) -> dict[str, Group]:
        groups: dict[str, Group] = {}
        for rec in self._raw_tables.get("GROUP DEFINITIONS", []):
            name = rec.get("Group")
            if not name:
                continue
            groups[str(name)] = Group(name=str(name), color=rec.get("Color"))
        for rec in self._raw_tables.get("GROUP ASSIGNMENTS", []):
            gname = rec.get("Group")
            obj_type = rec.get("ObjType")
            obj_label = rec.get("ObjLabel")
            if gname and obj_type is not None and obj_label is not None:
                target = groups.get(str(gname))
                if target is not None:
                    target.objects.append(f"{obj_type}:{obj_label}")
        return groups

    def _get_load_patterns(self) -> dict[str, LoadPattern]:
        patterns: dict[str, LoadPattern] = {}
        for rec in self._raw_tables.get("LOAD PATTERNS", []):
            name = rec.get("LoadPat")
            if not name:
                continue
            patterns[str(name)] = LoadPattern(
                name=str(name),
                pattern_type=str(rec.get("Type", "")),
                self_weight_factor=self._to_float(rec.get("SelfWtMult", 0.0)) or 0.0,
            )
        return patterns

    def _get_mass_sources(self) -> dict[str, MassSource]:
        load_pattern: dict[str, float] = {}
        for rec in self._raw_tables.get("MASS SOURCE", []):
            name = rec.get("LoadPat")
            if name is None:
                continue
            load_pattern[str(name)] = self._to_float(rec.get("Multiplier", 1.0)) or 1.0
        if not load_pattern:
            return {}
        return {
            "MASS": MassSource(name="MASS", loads=True, is_default=True, load_pattern=load_pattern)
        }

    def _get_area_uniform_loads(self) -> list[AreaUniformLoad]:
        loads: list[AreaUniformLoad] = []
        for rec in self._raw_tables.get("LOAD ASSIGNMENTS - SURFACE LOADS", []):
            aid = rec.get("Area")
            if aid is None:
                continue
            loads.append(
                AreaUniformLoad(
                    pattern=str(rec.get("LoadPat", "")),
                    area_id=str(aid),
                    direction=str(rec.get("Dir", "Gravity")),
                    value=self._to_float(rec.get("UnifLoad", 0.0)) or 0.0,
                )
            )
        return loads

    def _get_frame_distributed_loads(self) -> list[FrameDistributedLoad]:
        loads: list[FrameDistributedLoad] = []
        for rec in self._raw_tables.get("LOAD ASSIGNMENTS - LINE OBJECTS - DISTRIBUTED LOADS", []):
            lid = rec.get("Line")
            if lid is None:
                continue
            val_a = self._to_float(rec.get("FOverLA", 0.0)) or 0.0
            val_b = self._to_float(rec.get("FOverLB", 0.0)) or 0.0
            shape = "Uniform" if val_a == val_b else "Linear"
            loads.append(
                FrameDistributedLoad(
                    pattern=str(rec.get("LoadPat", "")),
                    frame_id=str(lid),
                    direction=str(rec.get("Dir", "Gravity")),
                    load_type=str(rec.get("Type", "Force")),
                    shape=shape,
                    val_a=val_a,
                    val_b=val_b,
                    rdist_a=self._to_float(rec.get("RelDistA", 0.0)) or 0.0,
                    rdist_b=self._to_float(rec.get("RelDistB", 1.0)) or 1.0,
                    dist_a=self._to_float(rec.get("AbsDistA", 0.0)) or 0.0,
                    dist_b=self._to_float(rec.get("AbsDistB", 0.0)) or 0.0,
                )
            )
        return loads

    def _get_joint_loads(self) -> list[JointLoad]:
        loads: list[JointLoad] = []
        for rec in self._raw_tables.get("LOAD ASSIGNMENTS - POINT LOADS", []):
            pid = rec.get("Point")
            if pid is None:
                continue
            loads.append(
                JointLoad(
                    pattern=str(rec.get("LoadPat", "")),
                    node_id=str(pid),
                    fx=self._to_float(rec.get("Fx", 0.0)) or 0.0,
                    fy=self._to_float(rec.get("Fy", 0.0)) or 0.0,
                    # SAFE's ``Fgrav`` is the global-Z point load (positive +Z
                    # up), so a gravity (downward) load is negative — mapped
                    # directly onto the toolkit's global-Z ``fz``.
                    fz=self._to_float(rec.get("Fgrav", 0.0)) or 0.0,
                    mx=self._to_float(rec.get("Mx", 0.0)) or 0.0,
                    my=self._to_float(rec.get("My", 0.0)) or 0.0,
                    mz=self._to_float(rec.get("Mz", 0.0)) or 0.0,
                )
            )
        return loads

    def get_load_cases(self) -> dict[str, LoadCase]:
        cases: dict[str, LoadCase] = {}
        for rec in self._raw_tables.get("LOAD CASES 01 - GENERAL", []):
            name = rec.get("LoadCase")
            if not name:
                continue
            cases[str(name)] = LoadCase(
                case_name=str(name),
                case_type=_normalize_case_type(rec.get("Type", "LinStatic")),
                design_type_option=str(rec.get("DesignOpt", "Program Determined")),
                design_type=str(rec.get("DesignType", "")),
                design_action_option="Prog Det",
                design_action="",
                initial_condition="Zero",
            )
        for rec in self._raw_tables.get("LOAD CASES 02 - STATIC", []):
            name = str(rec.get("LoadCase", ""))
            if name in cases:
                cases[name].initial_condition = str(rec.get("InitialCond", "Zero"))
        assigns: dict[str, list[dict[str, Any]]] = {}
        for rec in self._raw_tables.get("LOAD CASES 06 - LOADS APPLIED", []):
            name = str(rec.get("LoadCase", ""))
            if not name:
                continue
            load_sf = self._to_float(rec.get("SF"))
            assigns.setdefault(name, []).append(
                {
                    "LoadName": rec.get("LoadPat", ""),
                    "LoadSF": load_sf if load_sf is not None else 1.0,
                }
            )
        for name, case in cases.items():
            if name in assigns:
                case.case_data["CASE - STATIC 1 - LOAD ASSIGNMENTS"] = assigns[name]
        return cases

    def _get_load_combinations(self, load_cases: dict[str, LoadCase]) -> dict[str, LoadCombination]:
        combos: dict[str, LoadCombination] = {}
        for rec in self._raw_tables.get("LOAD COMBINATIONS", []):
            name = str(rec.get("Combo", ""))
            if not name:
                continue
            if name not in combos:
                combos[name] = LoadCombination(
                    name=name, combo_type=str(rec.get("Type", "Linear Add"))
                )
            entry_name = rec.get("Load")
            if entry_name is not None:
                combos[name].entries.append(
                    LoadCombinationEntry(
                        name=str(entry_name), factor=self._to_float(rec.get("SF", 1.0)) or 1.0
                    )
                )
        from ..model.load_combinations import classify_combination_refs

        classify_combination_refs(combos, load_cases)
        return combos

    def _get_point_springs(self) -> tuple[dict[str, PointSpring], dict[str, str]]:
        defs: dict[str, PointSpring] = {}
        for rec in self._raw_tables.get("SPRING PROPERTIES - POINT", []):
            name = rec.get("Spring")
            if not name:
                continue
            stiffness = {}
            for dof in ("Ux", "Uy", "Uz", "Rx", "Ry", "Rz"):
                value = self._to_float(rec.get(dof))
                if value is not None:
                    stiffness[dof.upper()] = value
            defs[str(name)] = PointSpring(
                name=str(name),
                stiffness=stiffness,
                nonlinear_option=str(rec.get("NonlinOpt", "None (Linear)")),
            )
        return defs, {}  # no point-spring assignment table in the native .$sf

    def _get_line_springs(self) -> tuple[dict[str, LineSpring], dict[str, str]]:
        defs: dict[str, LineSpring] = {}
        for rec in self._raw_tables.get("SPRING PROPERTIES - LINE", []):
            name = rec.get("Spring")
            if not name:
                continue
            defs[str(name)] = LineSpring(
                name=str(name),
                vertical_stiffness=self._to_float(rec.get("VertStiff", 0.0)) or 0.0,
                rotational_stiffness=self._to_float(rec.get("RotStiff", 0.0)) or 0.0,
                nonlinear_option=str(rec.get("NonlinOpt", "None (Linear)")),
            )
        assigns: dict[str, str] = {}
        for rec in self._raw_tables.get("LINE SPRING ASSIGNMENTS", []):
            lid = rec.get("Line")
            spring = rec.get("Spring")
            if lid is not None and spring is not None:
                assigns[str(lid)] = str(spring)
        return defs, assigns

    def _get_area_springs(self) -> tuple[dict, dict]:
        return {}, {}

    def _get_tendons(self) -> dict[str, TendonProperty]:
        tendons: dict[str, TendonProperty] = {}
        for rec in self._raw_tables.get("TENDON PROPERTIES", []):
            name = rec.get("TendonProp")
            if not name:
                continue
            tendons[str(name)] = TendonProperty(
                name=str(name),
                material=str(rec.get("MatProp", "")),
                strand_area=self._to_float(rec.get("StrandArea", 0.0)) or 0.0,
            )
        return tendons

    def _get_design_strips(self) -> dict[str, DesignStrip]:
        strips: dict[str, DesignStrip] = {}
        for rec in self._raw_tables.get("OBJECT GEOMETRY - DESIGN STRIPS", []):
            name = rec.get("Strip")
            if not name:
                continue
            name = str(name)
            strip = strips.setdefault(name, DesignStrip(name=name))
            point = rec.get("Point")
            if "WALeft" in rec or "WARight" in rec:
                strip.ends.append(
                    {
                        "point": str(point) if point is not None else "",
                        "side": "A",
                        "width_left": self._to_float(rec.get("WALeft", 0.0)) or 0.0,
                        "width_right": self._to_float(rec.get("WARight", 0.0)) or 0.0,
                    }
                )
            else:
                strip.ends.append(
                    {
                        "point": str(point) if point is not None else "",
                        "side": "B",
                        "width_left": self._to_float(rec.get("WBLeft", 0.0)) or 0.0,
                        "width_right": self._to_float(rec.get("WBRight", 0.0)) or 0.0,
                    }
                )
        return strips

    def _get_punching_shear(self) -> dict[str, Any]:
        punching: dict[str, Any] = {}
        for rec in self._raw_tables.get("PUNCHING SHEAR DESIGN OVERWRITES 01 - GENERAL", []):
            pid = rec.get("Point")
            if pid is None:
                continue
            punching[str(pid)] = {k: v for k, v in rec.items() if k != "Point"}
        return punching

    def _get_floor_mesh_options(self) -> dict[str, Any]:
        opts: dict[str, Any] = {}
        for rec in self._raw_tables.get("AUTOMATIC SLAB MESH OPTIONS", []):
            opts["mesh_option"] = str(rec.get("MeshOpt", "Rectangular"))
            value = self._to_float(rec.get("MaxSize"))
            if value is not None:
                opts["max_size"] = value
            opts["localize"] = self._to_bool(rec.get("Localize", False))
            opts["merge"] = self._to_bool(rec.get("Merge", False))
        return opts

    def _get_frame_auto_mesh(self) -> dict[str, dict[str, Any]]:
        return {}

    def get_model_data(self) -> SAPModelData:
        if self._raw_tables is None:
            raise RuntimeError("No model data loaded — call parse() before get_model_data().")

        area_assignments = self._get_area_assignments()
        sections = self._get_sections()
        null_ids = self._get_null_area_ids()
        opening_ids = self._get_opening_ids()
        area_elements = self._get_area_elements()
        for aid, elem in area_elements.items():
            sec_name = area_assignments.get(aid)
            if sec_name:
                sec = sections.get(sec_name)
                if isinstance(sec, ShellSection):
                    elem.thickness = sec.thickness
            if aid in null_ids:
                elem.null = True
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
            frame_dist_loads=self._get_frame_distributed_loads(),
            joint_loads=self._get_joint_loads(),
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
            punching_shear=self._get_punching_shear(),
            area_roles=self._get_area_roles(),
            floor_mesh_options=self._get_floor_mesh_options(),
            units=self.get_model_units(),
            source=str(self.file_path),
        )
        md.apply_material_defaults()
        return md
