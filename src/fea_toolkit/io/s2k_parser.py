"""Parse SAP2000 .S2K text files into intermediate data model."""

import contextlib
import json
import logging
import math
import re
from pathlib import Path
from typing import Any, Optional, Union

import numpy as np  # noqa: F401

from ..model.sap_data import (
    AreaEdgeConstraint,
    AreaElement,
    AreaGravityLoad,
    AreaMesh,
    AreaUniformLoad,
    ConcreteRectangularSection,
    Constraint,
    FrameDistributedLoad,
    FrameElement,
    FrameEndOffset,
    FrameRelease,
    GravityLoad,
    Group,
    JointLoad,
    LoadCase,
    LoadPattern,
    MassSource,
    Material,
    Node,
    Restraint,
    SAPModelData,
    Section,
    ShellSection,
    StressStrainCurve,
)

# from ..model.geometry import get_SAP_vecxz


class SAP2000Parser:
    """Parse SAP2000 .S2K file and convert to SAPModelData.

    Usage:
        parser = SAP2000Parser("model.s2k")
        parser.parse()
        model_data = parser.get_model_data()

    :meth:`parse` returns ``self``, so the two calls can be chained::

        model_data = SAP2000Parser("model.s2k").parse().get_model_data()

    The raw table data can be saved to JSON for later reuse:
        parser.to_json("model.json")
        parser2 = SAP2000Parser.from_json("model.json")
    """

    def __init__(self, file_path: Union[str, Path]):
        """Initialise parser with path to .S2K file."""
        self.file_path = Path(file_path)
        # ``None`` means "not loaded yet"; an empty dict means "loaded, but the
        # file contained no tables".  The ``is None`` guard in
        # :meth:`get_model_data` relies on this distinction.
        self._raw_tables: Optional[dict[str, list[dict[str, Any]]]] = None

    @property
    def raw_tables(self) -> dict[str, list[dict[str, Any]]]:
        """Public accessor for the raw parsed table data.

        Returns a dict mapping table names (e.g. ``"STORY DATA"``,
        ``"JOINT COORDINATES"``) to lists of row dicts.  Used by
        :func:`~fea_toolkit.model.stories.identify_stories` for
        storey detection and by downstream diagnostic tools.

        Returns an empty dict when nothing has been loaded yet (the internal
        sentinel is ``None`` so :meth:`get_model_data` can tell "not loaded"
        apart from "loaded with no tables").
        """
        return self._raw_tables if self._raw_tables is not None else {}

    # -------------------------------------------------------------------------
    # Parsing (adapted from your parse_sap2000_table_file / parse_file)
    # -------------------------------------------------------------------------
    def parse(self, warn_unhandled: bool = False) -> "SAP2000Parser":
        """Parse the .S2K file and store raw tables internally.

        Args:
            warn_unhandled: When ``True``, emit a :mod:`logging` warning for
                every table in the file that the toolkit does not recognise
                (see :mod:`fea_toolkit.io.table_registry`).  Off by default,
                because a full SAP2000 export always carries design / output /
                bookkeeping tables that are deliberately ignored.

        Returns:
            ``self``, so the call can be chained — e.g.
            ``SAP2000Parser("model.s2k").parse().get_model_data()``.
        """
        content = self._read_file_with_encodings(self.file_path)
        self._raw_tables = self._parse_sap2000_table_file(content)
        if warn_unhandled:
            self._warn_unhandled_tables()
        return self

    def _warn_unhandled_tables(self) -> None:
        """Log a warning for each unrecognised table in the parsed file."""
        from .table_registry import unhandled_tables

        for name, rows in sorted(unhandled_tables(self.raw_tables).items()):
            logging.warning(
                "SAP2000 table %r (%d rows) is not recognised by fea_toolkit — "
                "it may be a table SAP2000 has newly introduced, or one the "
                "toolkit has never handled.",
                name,
                rows,
            )

    def table_coverage(self):
        """Triage the parsed tables into handled / known-gap / ignored / unhandled.

        Returns:
            A :class:`~fea_toolkit.io.table_registry.TableCoverage` result.

        Raises:
            RuntimeError: If no data has been loaded — call :meth:`parse`
                (or :meth:`from_json`) first.
        """
        from .table_registry import table_coverage

        if self._raw_tables is None:
            raise RuntimeError(
                "No model data loaded — call parse() (or from_json()) before table_coverage()."
            )
        return table_coverage(self._raw_tables)

    @staticmethod
    def _read_file_with_encodings(path: Path) -> str:
        """Try multiple encodings to read the file."""
        encodings = ["utf-8", "cp1252", "latin-1"]
        for enc in encodings:
            try:
                return path.read_text(encoding=enc)
            except UnicodeDecodeError:
                continue
        # Fallback
        return path.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def _parse_sap2000_table_file(file_content: str) -> dict[str, list[dict[str, Any]]]:
        """Parse SAP2000 table file with space-separated key-value pairs.

        Args:
            file_content: String containing the file content

        Returns:
            Dictionary with table names as keys and lists of records as values
        """
        # Split content into lines
        lines = file_content.strip().splitlines()

        # Initialize result dictionary
        result: dict[str, list[dict[str, object]]] = {}

        # Parse the first line (file metadata)
        if lines:
            first_line = lines[0].strip()
            # Parse pattern: "File {filepath} was saved on {date} at {time}"
            metadata_pattern = re.compile(r"^File\s+(.+?)\s+was saved on\s+(.+?)\s+at\s+(.+?)$")
            metadata_match = metadata_pattern.match(first_line)

            if metadata_match:
                file_path = metadata_match.group(1)
                date_str = metadata_match.group(2)
                time_str = metadata_match.group(3)

                # Convert date and time to international format (YYYY/MM/DD HH:MM:SS)
                formatted_datetime = ""

                # Only format if both date and time are provided (not the placeholder format)
                if date_str != "m/d/yy" and time_str != "h:mm:ss":
                    try:
                        # Parse date (assuming format like "12/23/26" -> month/day/year)
                        date_parts = date_str.split("/")
                        if len(date_parts) == 3:
                            month = int(date_parts[0])
                            day = int(date_parts[1])
                            year = int(date_parts[2])

                            # Convert 2-digit year to 4-digit (assuming 2000s)
                            if year < 100:
                                year = 2000 + year

                            # Parse time (format like "11:55:03")
                            time_parts = time_str.split(":")
                            if len(time_parts) == 3:
                                hour = int(time_parts[0])
                                minute = int(time_parts[1])
                                second = int(time_parts[2])

                                # Format as YYYY/MM/DD HH:MM:SS with leading zeros
                                formatted_datetime = f"{year:04d}/{month:02d}/{day:02d} {hour:02d}:{minute:02d}:{second:02d}"
                    except (ValueError, IndexError):
                        # If parsing fails, leave empty
                        formatted_datetime = ""

                # Create metadata record
                metadata_record = {"FileName": file_path, "DateSaved": formatted_datetime}

                # Add METADATA table to result
                result["METADATA"] = [metadata_record]

            # Skip the first line for further parsing
            lines = lines[1:]

        current_table: Union[str, None] = None
        current_record: dict[str, object] = {}

        # Regular expressions for parsing
        table_regex = re.compile(r'^TABLE:\s+"([^"]+)"')
        kv_pair_regex = re.compile(r'(\w+)=("[^"]*"|[^\s]+)')

        for raw_line in lines:
            line = raw_line.strip()

            # Skip empty lines
            if not line:
                continue

            # Check if this is a new table header
            table_match = table_regex.match(line)
            if table_match:
                # Save previous record if exists
                if current_record and current_table:
                    if current_table not in result:
                        result[current_table] = []
                    result[current_table].append(current_record)
                    current_record = {}

                # Start new table
                current_table = table_match.group(1)
                continue

            # If no current table, skip this line
            if current_table is None:
                continue

            # Check if line ends with continuation marker
            ends_with_continuation = line.endswith("_")
            if ends_with_continuation:
                line = line[:-1].strip()  # Remove the continuation marker

            # Parse key-value pairs
            for match in kv_pair_regex.finditer(line):
                key = match.group(1)
                value = match.group(2)

                # Remove quotes if present
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]

                # Try to convert to appropriate type
                try:
                    # Try to convert to int
                    value = int(value)
                except ValueError:
                    try:
                        # Try to convert to float
                        value = float(value)
                    except ValueError:
                        # Keep as string
                        # Convert Yes/No to boolean
                        if value.upper() == "YES":
                            value = True
                        elif value.upper() == "NO":
                            value = False
                        elif value.upper() == "NONE":
                            value = None

                current_record[key] = value

            # If line doesn't end with continuation, save the record
            if not ends_with_continuation and current_record:
                if current_table not in result:
                    result[current_table] = []
                result[current_table].append(current_record)
                current_record = {}

        # Handle any remaining record
        if current_record and current_table:
            if current_table not in result:
                result[current_table] = []
            result[current_table].append(current_record)

        return result

    # -------------------------------------------------------------------------
    # JSON serialisation (optional)
    # -------------------------------------------------------------------------
    def to_json(self, output_path: Union[str, Path]) -> None:
        """Save raw tables to a JSON file."""
        path = Path(output_path)
        with open(path, "w") as f:
            json.dump(self._raw_tables, f, indent=2, default=str)

    @classmethod
    def from_json(cls, json_path: Union[str, Path]) -> "SAP2000Parser":
        """Create a parser instance pre‑loaded from a JSON file."""
        path = Path(json_path)
        parser = cls(path)  # temporary path, not used
        with open(path) as f:
            parser._raw_tables = json.load(f)
        return parser

    # -------------------------------------------------------------------------
    # Conversion to SAPModelData (extraction functions)
    # -------------------------------------------------------------------------
    def get_model_data(self) -> SAPModelData:
        """Convert raw parsed tables into SAPModelData.

        Raises:
            RuntimeError: If no data has been loaded — call :meth:`parse`
                (or :meth:`from_json`) first.  Without this guard an
                un-parsed parser silently yields an empty model.
        """
        if self._raw_tables is None:
            raise RuntimeError(
                "No model data loaded — call parse() (or from_json()) before get_model_data()."
            )
        # Call all extraction functions
        nodes = self._get_all_nodes()

        restraints = self._get_all_restraints()
        materials = self._get_all_materials()
        sections = self._get_sections_with_material_properties()
        frame_elements = self._get_frame_elements()
        area_elements = self._get_area_elements()
        frame_assignments = self._get_frame_assignments()
        area_assignments = self._get_area_assignments()
        groups = self._get_groups()
        model_units = self.get_model_units()
        frame_auto_mesh = self._get_frame_auto_mesh()
        frame_end_offsets = self._get_frame_end_offsets()
        frame_releases = self._get_frame_releases()
        area_mesh = self._get_area_mesh_assignments()
        area_edge_constraints = self._get_area_edge_constraints()
        constraints = self._get_constraints()
        constraint_assignments = self._get_constraint_assignments()
        load_patterns = self._get_load_patterns()
        mass_sources = self._get_mass_sources()
        joint_loads = self._get_joint_loads()
        frame_dist_loads = self._get_frame_distributed_loads()
        frame_gravity_loads = self._get_frame_gravity_loads()
        area_uniform_loads, area_gravity_loads = self._get_area_loads()
        load_cases = self.get_load_cases()

        # ── Populate area element thickness from assigned sections ──
        for aid, a_elem in area_elements.items():
            sec_name = area_assignments.get(aid)
            if sec_name:
                sec = sections.get(sec_name)
                if isinstance(sec, ShellSection):
                    a_elem.thickness = sec.thickness

        # ── Apply cardinal points to frame elements ──
        frame_cardinal = self._get_frame_cardinal_points()
        for eid, fe in frame_elements.items():
            cp = frame_cardinal.get(eid)
            if cp is not None:
                fe.cardinal_point = cp

        # ── Apply insertion-point flags (mirror / transform stiffness) ──
        # Sourced from SAP2000's "FRAME INSERTION POINT ASSIGNMENTS" table.
        for eid, info in self._get_frame_insertion_points().items():
            fe = frame_elements.get(eid)
            if fe is None:
                continue
            if "mirror_2" in info:
                fe.mirror_2 = info["mirror_2"]
            if "mirror_3" in info:
                fe.mirror_3 = info["mirror_3"]
            if "transform_stiffness" in info:
                fe.transform_stiffness = info["transform_stiffness"]

        # ── Compute combined offsets (longitudinal + cardinal point) ──
        frame_end_offsets = self._merge_cardinal_into_offsets(
            frame_elements,
            sections,
            frame_assignments,
            frame_end_offsets,
        )

        # ── Build the model data ──────────────────────────────────
        md = SAPModelData(
            nodes=nodes,
            restraints=restraints,
            materials=materials,
            sections=sections,
            frame_elements=frame_elements,
            area_elements=area_elements,
            frame_assignments=frame_assignments,
            area_assignments=area_assignments,
            groups=groups,
            frame_auto_mesh=frame_auto_mesh,
            frame_end_offsets=frame_end_offsets,
            frame_releases=frame_releases,
            area_mesh=area_mesh,
            area_edge_constraints=area_edge_constraints,
            constraints=constraints,
            constraint_assignments=constraint_assignments,
            load_patterns=load_patterns,
            mass_sources=mass_sources,
            joint_loads=joint_loads,
            frame_dist_loads=frame_dist_loads,
            frame_gravity_loads=frame_gravity_loads,
            area_uniform_loads=area_uniform_loads,
            area_gravity_loads=area_gravity_loads,
            load_cases=load_cases,
            units=model_units,
        )

        # ── Apply material defaults in-place ──────────────────────
        # Every material is guaranteed non-zero values for E_mod, Fy,
        # Fc, unit_weight, unit_mass etc. after this call.
        md.apply_material_defaults()

        return md

    def get_model_units(self) -> dict[str, str]:
        """Extract the units used in the SAP2000 model.

        The unit string is found in the 'PROGRAM CONTROL' table, e.g. 'N, mm, C'.
        Returns:
            'mm' or 'in' (converted from 'm', 'cm', 'ft' if necessary).
            Defaults to 'mm' if not found or unrecognised.
        """
        program_control = self._raw_tables.get("PROGRAM CONTROL", [])
        if not program_control:
            return {"F": "N", "L": "m", "T": "C"}  # default

        # Look for the CurrUnits field
        first_record = program_control[0]
        units_str = first_record.get("CurrUnits", "")
        if not units_str:
            return {"F": "N", "L": "m", "T": "C"}

        # Expected format: "Force, Length, Temperature"
        # Example: "N, mm, C" or "kN, m, C" or "kip, in, F"
        force, length, temp = [p.strip() for p in units_str.split(",")]

        return {"F": force, "L": length, "T": temp}

    # ---------- Individual extraction methods (adapted from your SAP2OPS_v4.py) ----------
    def _get_all_nodes(self) -> dict[str, Node]:
        nodes = {}
        tag = 1
        for joint in self._raw_tables.get("JOINT COORDINATES", []):
            nid = str(joint["Joint"])  # keep as string
            special = joint.get("SpecialJt", False)
            if isinstance(special, str):
                special = special.lower() == "yes"
            nodes[nid] = Node(
                node_id=nid,
                node_tag=tag,
                x=float(joint["XorR"]),
                y=float(joint["Y"]),
                z=float(joint["Z"]),
                is_special=bool(special),
            )
            tag += 1
        return nodes

    def _get_all_restraints(self) -> dict[str, Restraint]:
        restraints = {}
        for joint in self._raw_tables.get("JOINT RESTRAINT ASSIGNMENTS", []):
            nid = str(joint["Joint"])
            dofs = []
            for dof in ["U1", "U2", "U3", "R1", "R2", "R3"]:
                val = joint.get(dof, False)
                if isinstance(val, str):
                    val = val.lower() == "true"
                dofs.append(1 if val else 0)
            restraints[nid] = Restraint(dofs=dofs)
        return restraints

    def _get_all_materials(self) -> dict[str, Material]:
        """Extract all materials by merging every MATERIAL PROPERTIES table."""
        # Step 1: collect raw properties for each material from all relevant tables
        materials_data: dict[str, dict[str, Any]] = {}

        for table_name, records in self._raw_tables.items():
            if not table_name.startswith("MATERIAL PROPERTIES"):
                continue
            for rec in records:
                mat_name = rec.get("Material")
                if not mat_name:
                    continue
                if mat_name not in materials_data:
                    materials_data[mat_name] = {}
                # Merge the entire record (skip the 'Material' key itself)
                for k, v in rec.items():
                    if k == "Material":
                        continue
                    materials_data[mat_name][k] = v

        # Step 2: convert each material's property dict into a Material object
        materials = {}
        for name, props in materials_data.items():
            # Basic fields from various tables
            mat_type = props.get("Type", "")
            grade = props.get("Grade", None)

            E_mod = self._to_float(props.get("E1", 0.0))
            G_mod = self._to_float(props.get("G12", 0.0))
            nu = self._to_float(props.get("U12", 0.0))
            unit_weight = self._to_float(props.get("UnitWeight", 0.0))
            unit_mass = self._to_float(props.get("UnitMass", 0.0))

            if any(v is None for v in [E_mod, G_mod, nu, unit_weight, unit_mass]):
                prop_dict = dict(
                    zip(
                        ["E_mod", "G_mod", "nu", "unit_weight", "unit_mass"],
                        [E_mod, G_mod, nu, unit_weight, unit_mass],
                    )
                )
                raise ValueError(f"Missing material properties for material {name}\n{prop_dict}")
            else:
                # Yield / ultimate may appear in 03A, 03E, 03F, etc.
                Fy = self._to_float(props.get("Fy", None))
                Fu = self._to_float(props.get("Fu", None))

                # Concrete properties
                Fc = self._to_float(props.get("Fc", None))
                eFc = self._to_float(props.get("eFc", None))

                # Compute G if missing
                if G_mod == 0 and E_mod > 0 and nu > 0:
                    G_mod = E_mod / (2 * (1 + nu))

                # ── Interactive checks on G_mod for type narrowing ──
                # E_mod, G_mod, nu must be finite numbers at this point.
                if E_mod is None or G_mod is None or nu is None:
                    raise ValueError(
                        f"Missing elastic constants for material {name}: "
                        f"E={E_mod}, G={G_mod}, nu={nu}"
                    )
                if unit_weight is None or unit_mass is None:
                    raise ValueError(f"Missing unit weight/mass for material {name}")

                # Compute G if missing
                if G_mod == 0 and E_mod > 0 and nu > 0:
                    G_mod = E_mod / (2 * (1 + nu))

                # ── Extract effective yield / ultimate (from 03A / 03E) ──
                eff_Fy = self._to_float(props.get("EffFy", None))
                eff_Fu = self._to_float(props.get("EffFu", None))

                # ── Extract StressStrainCurve parameters (from 03A/B/E/F/G) ──
                # These keys are consumed into the structured ss_curve field
                # and removed from extra to avoid duplication.
                _curve_keys = {
                    "SSCurveOpt",
                    "SSHysType",
                    "SHard",
                    "SFc",
                    "SCap",
                    "SMax",
                    "SRup",
                    "FinalSlope",
                    "CoupModType",
                    "FAngle",
                    "DAngle",
                    "UseCTDef",
                }
                has_curve_data = bool(_curve_keys & props.keys())

                if has_curve_data:
                    ss_curve_opt = str(props.get("SSCurveOpt", "Simple"))
                    ss_hys_type = str(props.get("SSHysType", "Kinematic"))
                    s_hard = self._to_float(props.get("SHard", None))
                    s_fc = self._to_float(props.get("SFc", None))
                    s_cap = self._to_float(props.get("SCap", None))
                    s_max = self._to_float(props.get("SMax", None))
                    s_rup = self._to_float(props.get("SRup", None))
                    final_slope = self._to_float(props.get("FinalSlope", None))
                    coup_mod_type = str(props.get("CoupModType", "Von Mises"))
                    f_angle = self._to_float(props.get("FAngle", None))
                    d_angle = self._to_float(props.get("DAngle", None))
                    raw_use_ct = str(props.get("UseCTDef", "No")).lower()
                    use_ct_def = raw_use_ct in ("yes", "true")

                    ss_curve = StressStrainCurve(
                        ss_curve_opt=ss_curve_opt,
                        ss_hys_type=ss_hys_type,
                        s_hard=s_hard,
                        s_fc=s_fc,
                        s_cap=s_cap,
                        s_max=s_max,
                        s_rup=s_rup,
                        final_slope=final_slope,
                        coup_mod_type=coup_mod_type,
                        f_angle=f_angle,
                        d_angle=d_angle,
                        use_ct_def=use_ct_def,
                    )
                else:
                    ss_curve = None

                # ── Remove consumed keys from extra ──
                consumed_keys = {"EffFy", "EffFu"} | _curve_keys
                clean_extra = {k: v for k, v in props.items() if k not in consumed_keys}

                material = Material(
                    name=name,
                    type=mat_type,
                    grade=grade,
                    E_mod=E_mod,
                    G_mod=G_mod,
                    nu=nu,
                    unit_weight=unit_weight,
                    unit_mass=unit_mass,
                    Fy=Fy,
                    Fu=Fu,
                    Fc=Fc,
                    eFc=eFc,
                    eff_Fy=eff_Fy,
                    eff_Fu=eff_Fu,
                    ss_curve=ss_curve,
                    extra=clean_extra,
                )
                materials[name] = material

        return materials

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        """Safely convert a value to float, or return None if conversion fails."""
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    def _get_frame_auto_mesh(self) -> dict[str, dict[str, Any]]:
        """Parse FRAME AUTO MESH ASSIGNMENTS table."""
        auto_mesh = {}
        for rec in self._raw_tables.get("FRAME AUTO MESH ASSIGNMENTS", []):
            frame_id = str(rec.get("Frame", "0"))
            if frame_id != "0":
                auto_mesh[frame_id] = {
                    "AutoMesh": rec.get("AutoMesh", False),
                    "AtJoints": rec.get("AtJoints", False),
                    "AtFrames": rec.get("AtFrames", False),
                    "NumSegments": rec.get("NumSegments", 0),
                    "MaxLength": rec.get("MaxLength", 0),
                    "MaxDegrees": rec.get("MaxDegrees", 0),
                }
        return auto_mesh

    def _get_frame_end_offsets(self) -> dict[str, FrameEndOffset]:
        """Parse frame end-length offsets (rigid zones).

        Probes the known SAP2000 table-name variants — see
        :attr:`_END_OFFSET_TABLE_NAMES` — because the table (and its
        columns) has been renamed across versions.  The modern table is::

            TABLE: "FRAME END OFFSET ASSIGNMENTS"
               Frame=1   Type=Defined   LengthI=0.35   LengthJ=0.35   RigidFactor=1

        ``Type`` is ``Automatic`` (derived from section depth) or
        ``Defined``; ``LengthI``/``LengthJ`` are the longitudinal offset
        lengths at the I/J ends; ``RigidFactor`` is the fraction of the
        offset zone treated as fully rigid (0–1).  The alternative column
        names ``EndI``/``EndJ`` are also accepted.  When a frame appears in
        more than one variant table the first (newest) table wins.

        Returns
        -------
        Dict[str, FrameEndOffset]
            Mapping from frame ID to its I-end and J-end rigid offsets.
        """
        offsets: dict[str, FrameEndOffset] = {}
        for table_name in self._END_OFFSET_TABLE_NAMES:
            for rec in self._raw_tables.get(table_name, []):
                fid = str(rec.get("Frame", "0"))
                if fid == "0" or fid in offsets:
                    continue
                end_i = rec.get("LengthI", rec.get("EndI", 0.0))
                end_j = rec.get("LengthJ", rec.get("EndJ", 0.0))
                rigid = self._to_float(rec.get("RigidFactor"))
                offsets[fid] = FrameEndOffset(
                    end_i=self._to_float(end_i) or 0.0,
                    end_j=self._to_float(end_j) or 0.0,
                    rigid_factor=rigid if rigid is not None else 1.0,
                )
        return offsets

    def _get_area_mesh_assignments(self) -> dict[str, AreaMesh]:
        """Parse area auto-mesh assignments.

        Reads from either ``AREA MESH ASSIGNMENTS`` or
        ``AREA AUTO MESH ASSIGNMENTS`` (SAP2000 uses the latter).

        When an area ID appears in both tables,
        ``AREA MESH ASSIGNMENTS`` takes precedence because it is
        processed last in the loop.

        Returns
        -------
        Dict[str, AreaMesh]
            Mapping from area ID to its mesh control settings.
        """
        meshes: dict[str, AreaMesh] = {}
        # Try both table name variants
        for table_name in ("AREA AUTO MESH ASSIGNMENTS", "AREA MESH ASSIGNMENTS"):
            for rec in self._raw_tables.get(table_name, []):
                aid = str(rec.get("Area", "0"))
                if aid == "0":
                    continue
                # Determine auto_mesh flag: True unless MeshType is "None"
                mesh_type = str(rec.get("MeshType", "")).lower()
                use_auto = mesh_type not in ("", "none", "0", "false")
                if not use_auto:
                    use_auto = self._to_bool(rec.get("AutoMesh", False))

                # Max1/Max2 define max element size in each direction
                max_sz = max(
                    self._to_float(rec.get("Max1", 0.0)) or 0.0,
                    self._to_float(rec.get("Max2", 0.0)) or 0.0,
                    self._to_float(rec.get("MaxSize", 0.0)) or 0.0,
                )
                # Precedence: "AREA MESH ASSIGNMENTS" (second in loop)
                # overwrites "AREA AUTO MESH ASSIGNMENTS" (first) when
                # the same area ID appears in both tables.  This matches
                # SAP2000's own resolution order.
                meshes[aid] = AreaMesh(
                    auto_mesh=use_auto,
                    no_auto_mesh_at_edges=self._to_bool(rec.get("NoAutoMeshAtEdges", False)),
                    no_sub_mesh=self._to_bool(rec.get("NoSubMesh", False)),
                    min_size=self._to_float(rec.get("MinSize", 0.0)) or 0.0,
                    max_size=max_sz,
                )
        return meshes

    def _get_area_edge_constraints(self) -> dict[str, list[AreaEdgeConstraint]]:
        """Parse AREA EDGE CONSTRAINT ASSIGNMENTS table.

        Returns
        -------
        Dict[str, List[AreaEdgeConstraint]]
            Mapping from area ID to a list of its edge constraint assignments.
        """
        constraints: dict[str, list[AreaEdgeConstraint]] = {}
        for rec in self._raw_tables.get("AREA EDGE CONSTRAINT ASSIGNMENTS", []):
            aid = str(rec.get("Area", "0"))
            if aid == "0":
                continue
            c = AreaEdgeConstraint(
                area_id=aid,
                edge=int(rec.get("Edge", 0)),
                constraint=str(rec.get("Constraint", "Default")),
            )
            constraints.setdefault(aid, []).append(c)
        return constraints

    def _get_constraints(self) -> dict[str, Constraint]:
        """Parse joint constraint definitions from ``CONSTRAINT DEFINITIONS - *``.

        Reads every ``CONSTRAINT DEFINITIONS - <TYPE>`` table (BODY,
        DIAPHRAGM, EQUAL, BEAM, ROD, PLATE, WELD, LOCAL) and stores the
        result keyed by constraint name.  The table suffix (e.g.
        ``"DIAPHRAGM"``) becomes ``Constraint.constraint_type``.

        For diaphragm constraints the ``constraint_data`` dict holds the
        axis (``{"Axis": "Z"}``) so downstream consumers (Preprocessor /
        AnalysisBuilder) can derive rigid-diaphragm storey levels.

        Returns
        -------
        Dict[str, Constraint]
            Mapping from constraint name (e.g. ``"D1"``, ``"BODY1"``)
            to its :class:`Constraint` definition.
        """
        constraints: dict[str, Constraint] = {}
        for table_name, records in self._raw_tables.items():
            if not table_name.startswith("CONSTRAINT DEFINITIONS - "):
                continue
            ctype = table_name[len("CONSTRAINT DEFINITIONS - ") :].upper()
            for rec in records:
                name = str(rec.get("Name", "")).strip()
                if not name:
                    continue
                # Keep any column other than Name/CoordSys as constraint_data
                data = {k: v for k, v in rec.items() if k not in ("Name", "CoordSys")}
                if ctype == "DIAPHRAGM":
                    # Normalise axis to uppercase string for uniform lookup
                    axis = data.get("Axis")
                    if axis is not None:
                        data["Axis"] = str(axis).upper()
                constraints[name] = Constraint(
                    name=name,
                    constraint_type=ctype,
                    coord_sys=str(rec.get("CoordSys", "GLOBAL")),
                    constraint_data=data,
                )
        return constraints

    def _get_constraint_assignments(self) -> dict[str, str]:
        """Parse ``JOINT CONSTRAINT ASSIGNMENTS`` table.

        Returns
        -------
        Dict[str, str]
            Mapping from SAP joint ID (string) → constraint name.
        """
        assignments: dict[str, str] = {}
        for rec in self._raw_tables.get("JOINT CONSTRAINT ASSIGNMENTS", []):
            joint = str(rec.get("Joint", ""))
            cname = str(rec.get("Constraint", "")).strip()
            if joint and cname:
                assignments[joint] = cname
        return assignments

    def _get_frame_elements(self) -> dict[str, FrameElement]:
        elements = {}
        tag = 1
        for f in self._raw_tables.get("CONNECTIVITY - FRAME", []):
            eid = str(f["Frame"])
            # Also get angle from FRAME LOCAL AXES table
            angle = 0.0
            for la in self._raw_tables.get("FRAME LOCAL AXES ASSIGNMENTS 1 - TYPICAL", []):
                if str(la.get("Frame")) == eid:
                    angle = float(la.get("Angle", 0))
                    break
            node_i = str(f["JointI"])
            node_j = str(f["JointJ"])
            # vecxz = get_SAP_vecxz(np.array([1, 0, 0]), angle)
            elements[eid] = FrameElement(
                elem_id=eid, elem_tag=tag, node_i=node_i, node_j=node_j, angle=angle
            )
            tag += 1
        return elements

    # DOF columns in SAP2000's "FRAME RELEASE ASSIGNMENTS 1 - GENERAL" table.
    _RELEASE_END_I_KEYS = ("PI", "V2I", "V3I", "TI", "M2I", "M3I")
    _RELEASE_END_J_KEYS = ("PJ", "V2J", "V3J", "TJ", "M2J", "M3J")
    # Table names that carry frame end-release data (modern + legacy).
    _RELEASE_TABLE_NAMES = (
        "FRAME RELEASE ASSIGNMENTS 1 - GENERAL",
        "FRAME RELEASES",
        "FRAME RELEASE ASSIGNMENTS",
    )
    # Companion table carrying partial-fixity spring stiffnesses.
    _PARTIAL_FIXITY_TABLE_NAMES = ("FRAME RELEASE ASSIGNMENTS 2 - PARTIAL FIXITY",)
    # ── End-offset assignment tables (modern → legacy) ─────────────
    # SAP2000 renamed this table across versions and renamed the columns
    # too (``LengthI``/``LengthJ`` vs ``EndI``/``EndJ``).  All known
    # variants are probed so a model from any version parses identically;
    # the first table that carries a given frame wins.
    _END_OFFSET_TABLE_NAMES = (
        "FRAME END OFFSET ASSIGNMENTS",  # modern SAP2000
        "FRAME END LENGTH OFFSETS",  # alternative naming
        "FRAME OFFSET ALONG LENGTH ASSIGNMENTS",  # legacy SAP2000
    )
    # ── Frame insertion-point table (modern SAP2000) ───────────────
    # Holds the cardinal point plus the mirror / transform-stiffness flags.
    _INSERTION_POINT_TABLE_NAMES = ("FRAME INSERTION POINT ASSIGNMENTS",)
    # Column names that have historically carried the cardinal point in the
    # ``FRAME SECTION ASSIGNMENTS`` table (legacy / E2K exports).
    _CARDINAL_POINT_COLUMNS = ("CardinalPoint", "Cardinal", "CARDINALPT", "InsertPoint")
    # Lateral offsets below this magnitude (model length units) are treated as
    # zero.  SAP2000 reports tiny floating-point residue for the ``CGOffset`` /
    # ``EccV`` of doubly-symmetric sections (e.g. ~1e-17 m), which would
    # otherwise create spurious near-zero offset records for cardinal point 10.
    _LATERAL_OFFSET_TOL = 1e-9
    # ── Table-name prefixes scanned by family ──────────────────────
    # The concrete member names are registered individually in
    # :mod:`fea_toolkit.io.table_registry`, so an unrecognised member surfaces
    # as ``unhandled`` instead of being swallowed by a broad prefix.  The
    # ``tests/test_table_registry.py`` AST drift guard resolves these class
    # constants, checks every member the suffix dispatch selects, and treats
    # the bare prefix as a family (``extract_parser_family_prefixes``) rather
    # than as a table name.
    _AREA_LOADS_PREFIX = "AREA LOADS - "
    _AUTO_PREFIX = "AUTO"

    @staticmethod
    def _coerce_release_flag(value: Any) -> int:
        """Coerce a release cell to ``1`` (released) / ``0`` (connected).

        The generic table parser already turns ``Yes``/``No`` into booleans,
        but this also tolerates numeric ``0``/``1`` and raw strings.
        """
        if isinstance(value, bool):
            return 1 if value else 0
        if isinstance(value, (int, float)):
            return 1 if value else 0
        if isinstance(value, str):
            return 1 if value.strip().lower() in ("yes", "true", "1") else 0
        return 0

    @staticmethod
    def _coerce_release_spring(value: Any) -> Optional[float]:
        """Coerce a partial-fixity cell to a positive stiffness, else ``None``.

        SAP2000 writes ``0`` (or omits the value) for a full release; a
        positive value is the semi-rigid spring stiffness (force/length for
        translational DOFs, moment/radian for rotational DOFs) in model
        units.  Non-positive / unparsable entries return ``None`` so they
        are treated as full releases.
        """
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, str):
            text = value.strip()
            if not text or text.lower() in ("no", "none", "n.a.", "na"):
                return None
            try:
                value = float(text)
            except ValueError:
                return None
        try:
            stiffness = float(value)
        except (TypeError, ValueError):
            return None
        return stiffness if stiffness > 0 else None

    def _get_frame_releases(self) -> dict[str, FrameRelease]:
        """Extract frame end releases from the FRAME RELEASE table(s).

        Supports the modern ``"FRAME RELEASE ASSIGNMENTS 1 - GENERAL"``
        table (columns ``PI, V2I, V3I, TI, M2I, M3I`` / ``PJ, V2J, ...``)
        and the legacy ``"FRAME RELEASES"`` table.  Rows for the same frame
        are merged, so a model that lists the I-end and J-end columns in
        separate blocks is handled correctly.

        Only frames with at least one released DOF are returned.
        """
        releases: dict[str, FrameRelease] = {}
        for table_name in self._RELEASE_TABLE_NAMES:
            for rec in self._raw_tables.get(table_name, []):
                raw_id = rec.get("Frame")
                if raw_id is None:
                    continue
                fid = str(raw_id)
                rel = releases.get(fid)
                if rel is None:
                    rel = FrameRelease(frame_id=fid)
                    releases[fid] = rel
                for idx, key in enumerate(self._RELEASE_END_I_KEYS):
                    if key in rec:
                        rel.end_i[idx] = self._coerce_release_flag(rec[key])
                for idx, key in enumerate(self._RELEASE_END_J_KEYS):
                    if key in rec:
                        rel.end_j[idx] = self._coerce_release_flag(rec[key])

        # ── Partial-fixity springs (semi-rigid connections) ──────────
        # A non-zero spring value marks a released DOF as semi-rigid.  The
        # DOF is force-released so downstream consumers always see
        # "released + spring", matching SAP2000's requirement.
        for table_name in self._PARTIAL_FIXITY_TABLE_NAMES:
            for rec in self._raw_tables.get(table_name, []):
                raw_id = rec.get("Frame")
                if raw_id is None:
                    continue
                fid = str(raw_id)
                rel = releases.get(fid)
                if rel is None:
                    rel = FrameRelease(frame_id=fid)
                    releases[fid] = rel
                for idx, key in enumerate(self._RELEASE_END_I_KEYS):
                    if key in rec:
                        stiffness = self._coerce_release_spring(rec[key])
                        if stiffness is not None:
                            rel.end_i_k[idx] = stiffness
                            rel.end_i[idx] = 1
                for idx, key in enumerate(self._RELEASE_END_J_KEYS):
                    if key in rec:
                        stiffness = self._coerce_release_spring(rec[key])
                        if stiffness is not None:
                            rel.end_j_k[idx] = stiffness
                            rel.end_j[idx] = 1

        return {fid: rel for fid, rel in releases.items() if rel.has_releases}

    def _get_area_elements(self) -> dict[str, AreaElement]:
        """Extract area elements from CONNECTIVITY - AREA table.

        Handles both:
        - **Single-row format** (newer SAP2000): all joints for an area in one row.
        - **Multi-row format** (older SAP2000): one area's joints may span
          multiple rows (e.g. Joint1..Joint4 in row 1, Joint5..Joint8 in row 2).
          Joint IDs are consolidated across rows, avoiding duplicates.
        """
        # Intermediate store: area_id -> {node_ids, tag}
        _areas: dict[str, dict] = {}
        _next_tag = 1
        for a in self._raw_tables.get("CONNECTIVITY - AREA", []):
            aid = str(a.get("Area", 0))
            if not aid or aid == "0":
                continue
            # Collect joint IDs from this row
            row_nodes: list[str] = []
            i = 1
            while True:
                joint_key = f"Joint{i}"
                if joint_key in a:
                    row_nodes.append(str(a[joint_key]))
                    i += 1
                else:
                    break
            if len(row_nodes) < 3:
                continue
            # First time seeing this area_id -> initialise
            if aid not in _areas:
                _areas[aid] = {"node_ids": [], "tag": _next_tag}
                _next_tag += 1
            # Append new joint IDs, avoiding duplicates
            info = _areas[aid]
            for jid in row_nodes:
                if jid not in info["node_ids"]:
                    info["node_ids"].append(jid)

        return {
            aid: AreaElement(
                area_id=aid,
                area_tag=info["tag"],
                node_ids=info["node_ids"],
            )
            for aid, info in _areas.items()
        }

    def _get_frame_assignments(self) -> dict[str, str]:
        assign = {}
        for a in self._raw_tables.get("FRAME SECTION ASSIGNMENTS", []):
            eid = str(a.get("Frame", "0"))
            sec = a.get("AnalSect", "")
            if eid != "0" and sec and sec != "N.A.":
                assign[eid] = sec
        return assign

    @staticmethod
    def _parse_cardinal_point(value: Any) -> Optional[int]:
        """Extract the cardinal-point integer from an insertion-point cell.

        Modern SAP2000 writes the cardinal point as a labelled string —
        e.g. ``"8 (top center)"`` or ``"10 (centroid)"`` — while legacy and
        E2K exports use a bare integer.  Both forms are accepted.

        Only the real SAP2000 range (**1–11**) is accepted.  A non-integral
        number, ``0``, or any out-of-range value is not a cardinal point and
        is rejected rather than coerced (``int(8.7) == 8``; ``0`` and ``12``
        would previously have been accepted silently).

        Args:
            value: Raw cell value (``str``, ``int`` or ``float``).

        Returns:
            The cardinal point (1–11), or ``None`` when it cannot be read.
        """
        if value is None or isinstance(value, bool):
            return None
        # Bare numeric form (int / float / numeric string) takes priority so
        # that a non-integral value such as "8.7" is rejected rather than
        # truncated to its leading integer by the labelled-form regex below.
        try:
            number = float(str(value).strip())
        except (TypeError, ValueError):
            # Labelled string form: complete numeric token, e.g. "8 (top center)".
            # Capture the *whole* number — fractional part and exponent notation
            # included — then require only optional whitespace before either an
            # opening parenthesis or the end of the string.  A bare ``\b``
            # boundary is not enough: on "8.7x" the engine backtracks to the
            # leading "8" (the "8"→"." transition is a word boundary), so the
            # malformed cell would read as cardinal point 8.  Consuming the
            # whitespace *before* the lookahead is what rejects a bare trailing
            # word such as "8 invalid": the old ``(?=\s|\(|$)`` matched the
            # first space and accepted the junk that followed.
            match = re.match(r"^\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*(?=\(|$)", str(value))
            if match is None:
                return None
            number = float(match.group(1))
        if not number.is_integer():
            return None
        point = int(number)
        return point if 1 <= point <= 11 else None

    def _get_frame_insertion_points(self) -> dict[str, dict[str, Any]]:
        """Parse the modern ``FRAME INSERTION POINT ASSIGNMENTS`` table.

        Each row carries the cardinal point plus the mirror / transform
        flags, e.g.::

            Frame=24   CardinalPt="8 (top center)"   Mirror2=No   Mirror3=No   Transform=Yes

        ``Transform=Yes`` is SAP2000's default and means the stiffness
        *is* transformed to account for the offset from the centroid (the
        GUI checkbox *"do not transform frame stiffness for offsets from
        centroid"* is therefore unchecked).

        Returns:
            Mapping ``{frame_id: {key: value}}`` where the inner dict may
            contain ``cardinal_point`` (int), ``mirror_2`` (bool),
            ``mirror_3`` (bool) and ``transform_stiffness`` (bool).  Only
            keys actually present in the table are included.
        """
        result: dict[str, dict[str, Any]] = {}
        for table_name in self._INSERTION_POINT_TABLE_NAMES:
            for rec in self._raw_tables.get(table_name, []):
                raw_id = rec.get("Frame")
                if raw_id is None:
                    continue
                eid = str(raw_id)
                info: dict[str, Any] = {}
                cp = self._parse_cardinal_point(
                    rec.get("CardinalPt", rec.get("CardinalPoint", rec.get("InsertPoint")))
                )
                if cp is not None:
                    info["cardinal_point"] = cp
                for key, col in (("mirror_2", "Mirror2"), ("mirror_3", "Mirror3")):
                    if col in rec:
                        info[key] = self._to_bool(rec[col])
                if "Transform" in rec:
                    info["transform_stiffness"] = self._to_bool(rec["Transform"])
                if info:
                    result[eid] = info
        return result

    def _get_frame_cardinal_points(self) -> dict[str, int]:
        """Extract the frame insertion point (cardinal point) per frame.

        Primary source is SAP2000's modern
        ``FRAME INSERTION POINT ASSIGNMENTS`` table, where the value is a
        labelled string (``CardinalPt="8 (top center)"``).  For legacy /
        E2K exports the cardinal point may instead appear as a column
        (``CardinalPoint``, ``Cardinal``, ``CARDINALPT`` or ``InsertPoint``)
        in the ``FRAME SECTION ASSIGNMENTS`` table; that form is used as a
        fallback.

        Cardinal point numbering (1–11):

        =====  ===============
        Value  Position
        =====  ===============
        1      Bottom left
        2      Bottom centre
        3      Bottom right
        4      Middle left
        5      Middle centre
        6      Middle right
        7      Top left
        8      Top centre
        9      Top right
        10     Centroid (default)
        11     Shear centre
        =====  ===============

        Returns
        -------
        Dict[str, int]
            Mapping from frame ID to its cardinal point integer (1–11).
            An empty dict is returned when no cardinal point is available.
        """
        result: dict[str, int] = {}

        # 1) Modern SAP2000 insertion-point table (labelled-string form).
        for eid, info in self._get_frame_insertion_points().items():
            cp = info.get("cardinal_point")
            if cp is not None:
                result[eid] = cp

        # 2) Legacy / E2K fallback — a cardinal-point column inside
        #    FRAME SECTION ASSIGNMENTS.  Only fills frames not already
        #    covered by the insertion-point table above.
        table = self._raw_tables.get("FRAME SECTION ASSIGNMENTS", [])
        col = None
        for a in table:
            for c in self._CARDINAL_POINT_COLUMNS:
                if c in a:
                    col = c
                    break
            if col is not None:
                break
        if col is not None:
            for a in table:
                eid = str(a.get("Frame", "0"))
                if eid == "0" or eid in result:
                    continue
                cp = self._parse_cardinal_point(a.get(col))
                if cp is not None:
                    result[eid] = cp
        return result

    @staticmethod
    def _cardinal_point_offset(
        num: int,
        D: float,
        B: float,
        cg_offset_2: float = 0.0,
        cg_offset_3: float = 0.0,
        ecc_v2: float = 0.0,
        ecc_v3: float = 0.0,
    ) -> tuple[float, float]:
        """Compute (off_y, off_z) from a cardinal point and section dimensions.

        Per SAP2000/ETABS convention (matching E2K_utilities):

        =====  ===============  ==================================
        Value  Position         Offset (y, z) relative to centroid
        =====  ===============  ==================================
        1      Bottom left      (½B,  ½D)
        2      Bottom centre    (0,      ½D)
        3      Bottom right     (-½B, ½D)
        4      Middle left      (½B,  0)
        5      Middle centre    (0,      0)
        6      Middle right     (-½B, 0)
        7      Top left         (½B, -½D)
        8      Top centre       (0,     -½D)
        9      Top right        (-½B,-½D)
        10     Centroid         (-CGOffset2, -CGOffset3)
        11     Shear centre     (-(CGOffset2+EccV2), -(CGOffset3+EccV3))
        =====  ===============  ==================================

        Offsets are relative to the section centroid in the local y-z plane.
        D = depth (local-3 direction), B = width (local-2 direction).
        For circular sections (B = 0), D is used in place of B.

        Points 1–9 sit on the section *bounding box*, which is centred on
        the profile origin, so they are simple half-dimension offsets
        (unchanged from the E2K convention).  Points 10 (centroid) and 11
        (shear centre) instead sit at the section's true centroid / shear
        centre, which for asymmetric shapes (channel, angle, tee) is *not*
        the bounding-box centre.  SAP2000 reports those locations as
        ``CGOffset2``/``CGOffset3`` (centroid relative to the section
        reference point, in the local 2/3 directions) and
        ``EccV2``/``EccV3`` (shear-centre eccentricity, measured from the
        centroid).  The returned offset shifts the bounding-box-centred
        profile so the centroid / shear centre lands on the member
        reference line — hence the negation.  All four values are zero for
        doubly-symmetric shapes, so points 10/11 reduce to ``(0, 0)``
        (SAP2000's default) exactly as before.

        Args:
            num: Cardinal-point number (1–11).
            D: Section depth (local 3).
            B: Section width (local 2; ``0`` for circular → ``D`` used).
            cg_offset_2: ``CGOffset2`` — centroid offset along local 2.
            cg_offset_3: ``CGOffset3`` — centroid offset along local 3.
            ecc_v2: ``EccV2`` — shear-centre eccentricity along local 2.
            ecc_v3: ``EccV3`` — shear-centre eccentricity along local 3.
        """
        if num == 10:  # Centroid — offset the bbox-centred profile onto the CG
            return (-cg_offset_2, -cg_offset_3)
        if num == 11:  # Shear centre — centroid plus shear-centre eccentricity
            return (-(cg_offset_2 + ecc_v2), -(cg_offset_3 + ecc_v3))
        b = D if B == 0 else B  # circular sections: use D for both
        return {
            1: (0.5 * b, 0.5 * D),  # Bottom left
            2: (0.0, 0.5 * D),  # Bottom centre
            3: (-0.5 * b, 0.5 * D),  # Bottom right
            4: (0.5 * b, 0.0),  # Middle left
            5: (0.0, 0.0),  # Middle centre (centroid of bbox)
            6: (-0.5 * b, 0.0),  # Middle right
            7: (0.5 * b, -0.5 * D),  # Top left
            8: (0.0, -0.5 * D),  # Top centre
            9: (-0.5 * b, -0.5 * D),  # Top right
        }.get(num, (0.0, 0.0))

    @staticmethod
    def _get_section_depth_width(sec: "Section") -> tuple[float, float]:
        """Extract section depth D (local-3) and width B (local-2).

        Returns (0, 0) for sections with no explicit dimensions.
        """
        from ..model.sap_data import (
            AngleSection,
            BoxSection,
            ChannelSection,
            CircularSection,
            ConcreteCircularSection,
            DoubleAngleSection,
            ISection,
            PipeSection,
            RectangularSection,
            TeeSection,
        )

        if isinstance(
            sec,
            (ISection, ChannelSection, BoxSection, AngleSection, DoubleAngleSection, TeeSection),
        ):
            return sec.depth, sec.bf
        elif isinstance(sec, PipeSection):
            return sec.od, sec.od  # circular: D = B = od
        elif isinstance(sec, (RectangularSection, ConcreteRectangularSection)):
            return sec.depth, sec.bf
        elif isinstance(sec, (CircularSection, ConcreteCircularSection)):
            return sec.diameter, sec.diameter
        return 0.0, 0.0

    def _merge_cardinal_into_offsets(
        self,
        frame_elements: dict[str, "FrameElement"],
        sections: dict[str, "Section"],
        frame_assignments: dict[str, str],
        existing_offsets: dict[str, FrameEndOffset],
    ) -> dict[str, FrameEndOffset]:
        """Merge cardinal point offsets into FrameEndOffset records.

        For each frame element, computes the lateral (y, z) offset implied by
        its insertion point and stores it in the frame's end-offset record
        (creating one when absent).  The offset is derived from the section
        dimensions; for cardinal points 10 (centroid) and 11 (shear centre)
        the section's ``CGOffset2/3`` and ``EccV2/3`` (SAP2000
        ``FRAME SECTION PROPERTIES 01 - GENERAL``) locate the true centroid /
        shear centre, which is non-zero for asymmetric shapes (channel,
        angle, tee).  Symmetric shapes yield no offset for points 10/11.
        """
        merged = dict(existing_offsets)  # shallow copy
        for eid, fe in frame_elements.items():
            cp = fe.cardinal_point
            sec_name = frame_assignments.get(eid)
            if not sec_name:
                continue
            sec = sections.get(sec_name)
            if sec is None:
                continue
            D, B = self._get_section_depth_width(sec)
            # Cardinal points 10 (centroid) and 11 (shear centre) are located
            # by the section's ``CGOffset2/3`` / ``EccV2/3`` data, not by its
            # bounding-box depth/width, so a section that reports no D/B still
            # has a meaningful offset there (an asymmetric profile with no
            # depth/width is unlikely, but the guard must not silently drop it).
            if D == 0.0 and B == 0.0 and cp not in (10, 11):
                continue
            off_y, off_z = self._cardinal_point_offset(
                cp,
                D,
                B,
                getattr(sec, "cg_offset_2", 0.0),
                getattr(sec, "cg_offset_3", 0.0),
                getattr(sec, "ecc_v2", 0.0),
                getattr(sec, "ecc_v3", 0.0),
            )
            if abs(off_y) < self._LATERAL_OFFSET_TOL and abs(off_z) < self._LATERAL_OFFSET_TOL:
                continue
            # Merge into existing offset or create new (preserves any
            # longitudinal offsets and rigid-zone factor already present).
            extant = merged.get(eid, FrameEndOffset())
            extant.off_y_i = off_y
            extant.off_z_i = off_z
            extant.off_y_j = off_y
            extant.off_z_j = off_z
            merged[eid] = extant
        return merged

    def _get_area_assignments(self) -> dict[str, str]:
        assign = {}
        for a in self._raw_tables.get("AREA SECTION ASSIGNMENTS", []):
            aid = str(a.get("Area", "0"))
            sec = a.get("Section", "")
            if aid != "0" and sec:
                assign[aid] = sec
        return assign

    def _get_area_loads(self):
        """Parse all AREA LOADS - * tables by dispatching on the suffix.

        Returns:
            Tuple of (uniform_loads, gravity_loads).
        """
        uniform_loads: list[AreaUniformLoad] = []
        gravity_loads: list[AreaGravityLoad] = []

        for table_name in self._raw_tables:
            if not table_name.startswith(self._AREA_LOADS_PREFIX):
                continue
            # e.g. "UNIFORM", "GRAVITY" — the table-name suffix after the prefix.
            load_type = table_name[len(self._AREA_LOADS_PREFIX) :]

            if load_type == "UNIFORM":
                for rec in self._raw_tables[table_name]:
                    uniform_loads.append(
                        AreaUniformLoad(
                            pattern=str(rec.get("LoadPat", "")),
                            area_id=str(rec.get("Area", "")),
                            coord_sys=str(rec.get("CoordSys", "GLOBAL")),
                            direction=str(rec.get("Dir", "Gravity")),
                            value=float(rec.get("UnifLoad", 0.0)),
                        )
                    )

            elif load_type == "UNIFORM TO FRAME":
                # SAP "AREA LOADS - UNIFORM TO FRAME" — pressure applied to
                # the frame elements along the panel edges, with an explicit
                # OneWay/TwoWay distribution flag (routed to the edge-load
                # conversion rather than the shell-object path).
                for rec in self._raw_tables[table_name]:
                    uniform_loads.append(
                        AreaUniformLoad(
                            pattern=str(rec.get("LoadPat", "")),
                            area_id=str(rec.get("Area", "")),
                            coord_sys=str(rec.get("CoordSys", "GLOBAL")),
                            direction=str(rec.get("Dir", "Gravity")),
                            value=float(rec.get("UnifLoad", 0.0)),
                            to_frame=True,
                            distribution=str(rec.get("Distribution", "TwoWay")).capitalize(),
                        )
                    )

            elif load_type == "GRAVITY":
                for rec in self._raw_tables[table_name]:
                    gravity_loads.append(
                        AreaGravityLoad(
                            pattern=str(rec.get("LoadPat", "")),
                            area_id=str(rec.get("Area", "")),
                            coord_sys=str(rec.get("CoordSys", "GLOBAL")),
                            multiplier_x=float(rec.get("MultiplierX", 0.0)),
                            multiplier_y=float(rec.get("MultiplierY", 0.0)),
                            multiplier_z=float(rec.get("MultiplierZ", 0.0)),
                        )
                    )

            else:
                # Unrecognised ``AREA LOADS - *`` variant — warn rather than
                # discard silently.  The table-coverage report
                # (:mod:`fea_toolkit.io.table_registry`) surfaces it too.
                logging.warning(
                    "Unrecognised %r table (%d row(s)) is not parsed — "
                    "register it in io/table_registry.py or extend "
                    "_get_area_loads().",
                    table_name,
                    len(self._raw_tables[table_name]),
                )

        return uniform_loads, gravity_loads

    def _get_reinf_tables(
        self,
    ) -> tuple[
        dict[str, dict[str, Any]],
        dict[str, dict[str, Any]],
        dict[str, Optional[str]],
        dict[str, float],
    ]:
        """Parse SAP2000 reinforcement / rebar-size tables.

        Returns:
            ``(column_reinf, beam_reinf, area_rebar_mat, rebar_diameters)``

            * ``column_reinf`` — from ``FRAME SECTION PROPERTIES 02 - CONCRETE COLUMN``:
                ``{SectionName: {rebar_mat, bar_size_along3, bar_size_along2}}``
            * ``beam_reinf`` — from ``FRAME SECTION PROPERTIES 03 - CONCRETE BEAM``:
                ``{SectionName: {rebar_mat, bar_size_top, bar_size_bot}}``
            * ``area_rebar_mat`` — from ``AREA SECTION PROPERTY DESIGN PARAMETERS``:
                ``{Section: rebar_mat_name_or_None}``
            * ``rebar_diameters`` — from ``REBAR SIZES``:
                ``{RebarID: Diameter}`` (model length units)
        """
        # ── REBAR SIZES: RebarID → Diameter (model units) ────────────
        rebar_diameters: dict[str, float] = {}
        for rec in self._raw_tables.get("REBAR SIZES", []):
            rid = str(rec.get("RebarID", "")).strip()
            dia = rec.get("Diameter")
            if rid and dia is not None:
                with contextlib.suppress(ValueError, TypeError):
                    rebar_diameters[rid] = float(dia)

        # Helper: extract a tie/bar size designation and map it to a
        # diameter in model units via the REBAR SIZES table when possible.
        def _tie_diameter(
            rec: dict[str, Any],
            size_key: str,
        ) -> Optional[float]:
            """Resolve a tie-size column to a diameter (model units)."""
            _tid = str(rec.get(size_key, "") or "").strip() or None
            if not _tid:
                return None
            return rebar_diameters.get(_tid)

        def _tie_data(rec: dict[str, Any]) -> dict[str, Any]:
            """Extract transverse-reinforcement data for Mander confinement.

            Reads the standard SAP2000 E2K concrete column/beam table
            columns:
              ``TieSizeL``      — tie bar size designation (longitudinal
                                  direction; column table).
              ``TieSizeT``      — tie bar size designation (transverse
                                  direction; beam table).
              ``TieSpacingL``   — tie centre-to-centre spacing (model
                                  length units).
              ``RebarMatT``     — tie rebar material name.

            Returns an empty dict when no tie data is present.
            """
            data: dict[str, Any] = {}
            tie_dia = (
                _tie_diameter(rec, "TieSizeL")
                or _tie_diameter(rec, "TieSizeT")
                or _tie_diameter(rec, "TieSizeM")
            )
            if tie_dia:
                data["tie_diameter"] = tie_dia
            for k in ("TieSpacingL", "TieSpacingT", "TieSpacingM"):
                v = rec.get(k)
                if v is not None:
                    try:
                        fv = float(v)
                    except (ValueError, TypeError):
                        continue
                    if fv > 0:
                        data["tie_spacing"] = fv
                        break
            tie_mat = str(rec.get("RebarMatT", "") or "").strip() or None
            if tie_mat and tie_mat != "None":
                data["tie_rebar_mat"] = tie_mat
            return data

        # ── FRAME SECTION PROPERTIES 02 - CONCRETE COLUMN ───────────
        column_reinf: dict[str, dict[str, Any]] = {}
        for rec in self._raw_tables.get("FRAME SECTION PROPERTIES 02 - CONCRETE COLUMN", []):
            name = str(rec.get("SectionName", "")).strip()
            if not name:
                continue
            rebar_mat = str(rec.get("RebarMatL", "") or "").strip() or None
            entry: dict[str, Any] = {
                "rebar_mat": rebar_mat,
                "bar_size_along3": str(rec.get("BarSizeL", "") or "").strip() or None,
                "bar_size_along2": str(rec.get("BarSizeL", "") or "").strip() or None,
            }
            # If RebarMatL absent (e.g. older S2K), fall back to RebarMatM
            if rebar_mat is None:
                m = str(rec.get("RebarMatM", "") or "").strip() or None
                entry["rebar_mat"] = m
            entry.update(_tie_data(rec))
            column_reinf[name] = entry

        # ── FRAME SECTION PROPERTIES 03 - CONCRETE BEAM ──────────────
        beam_reinf: dict[str, dict[str, Any]] = {}
        for rec in self._raw_tables.get("FRAME SECTION PROPERTIES 03 - CONCRETE BEAM", []):
            name = str(rec.get("SectionName", "")).strip()
            if not name:
                continue
            rebar_mat = str(rec.get("RebarMatL", "") or "").strip() or None
            if rebar_mat is None:
                rebar_mat = str(rec.get("RebarMatM", "") or "").strip() or None
            entry = {
                "rebar_mat": rebar_mat,
                "bar_size_top": str(rec.get("BarSizeTop", "") or "").strip() or None,
                "bar_size_bot": str(rec.get("BarSizeBot", "") or "").strip() or None,
            }
            entry.update(_tie_data(rec))
            beam_reinf[name] = entry

        # ── AREA SECTION PROPERTY DESIGN PARAMETERS ──────────────────
        area_rebar_mat: dict[str, Optional[str]] = {}
        for rec in self._raw_tables.get("AREA SECTION PROPERTY DESIGN PARAMETERS", []):
            name = str(rec.get("Section", "")).strip()
            if not name:
                continue
            rm = str(rec.get("RebarMat", "") or "").strip() or None
            if rm == "None":
                rm = None
            area_rebar_mat[name] = rm

        return column_reinf, beam_reinf, area_rebar_mat, rebar_diameters

    def _get_sections_with_material_properties(self) -> dict[str, Section]:
        """Combine section geometry from FRAME SECTION PROPERTIES with material data."""
        # Parse SAP2000 reinforcement tables (column/beam rebar material +
        # bar sizes, area-section rebar material, and rebar-size diameters).
        column_reinf, beam_reinf, area_rebar_mat, rebar_diameters = self._get_reinf_tables()
        from ..model.sap_data import (
            AngleSection,
            BoxSection,
            ChannelSection,
            CircularSection,
            ConcreteCircularSection,
            DoubleAngleSection,
            GeneralSection,
            ISection,
            PipeSection,
            RectangularSection,
            SDSection,
            ShellSection,
            TeeSection,
        )

        sections = {}
        for sec in self._raw_tables.get("FRAME SECTION PROPERTIES 01 - GENERAL", []):
            name = sec.get("SectionName", "Unknown")
            shape: str = sec.get("Shape", "Unknown")
            mat_name = sec.get("Material", "Unknown")

            # Common derived properties
            common = {
                "name": name,
                "shape": shape,
                "material": mat_name,
                "A": float(sec.get("Area", 0)),
                "I33": float(sec.get("I33", 0)),
                "I22": float(sec.get("I22", 0)),
                "J": float(sec.get("TorsConst", 0)),
                "Z33": sec.get("Z33", None),
                "Z22": sec.get("Z22", None),
            }
            if common["Z33"] is not None:
                common["Z33"] = float(common["Z33"])
            if common["Z22"] is not None:
                common["Z22"] = float(common["Z22"])

            # Stiffness modifiers from FRAME SECTION PROPERTIES 01 - GENERAL
            # (default = 1.0 meaning no modification; used for elastic builds)
            modifiers = {}
            for mk in ("AMod", "A2Mod", "A3Mod", "JMod", "I2Mod", "I3Mod"):
                mv = sec.get(mk)
                if mv is not None:
                    with contextlib.suppress(ValueError, TypeError):
                        modifiers[mk] = float(mv)
            common["modifiers"] = modifiers

            # Section centroid offset + shear-centre eccentricity, in the
            # section's local 2 / 3 directions (SAP2000 ``CGOffset2`` /
            # ``CGOffset3`` / ``EccV2`` / ``EccV3``).  Zero for
            # doubly-symmetric shapes; non-zero for channel / angle / tee.
            # Used to place the true centroid / shear centre for cardinal
            # points 10 and 11.
            for _key, _col in (
                ("cg_offset_2", "CGOffset2"),
                ("cg_offset_3", "CGOffset3"),
                ("ecc_v2", "EccV2"),
                ("ecc_v3", "EccV3"),
            ):
                _val = self._to_float(sec.get(_col))
                common[_key] = _val if _val is not None else 0.0

            # Shape‑specific dimensions (SAP2000 t3 = depth, t2 = width)
            t3 = float(sec.get("t3", 0))
            t2 = float(sec.get("t2", 0))
            tw_val = float(sec.get("tw", 0))
            tf_val = float(sec.get("tf", 0))

            sec_data: Section

            if shape == "Shell":
                sec_data = ShellSection(**common, thickness=float(sec.get("thickness", 0)))
            elif shape in ("I/Wide Flange", "WIDE FLANGE", "Steel I/Wide Flange"):
                sec_data = ISection(**common, depth=t3, bf=t2, tf=tf_val, tw=tw_val)
            elif shape in ("Channel", "CHANNEL", "Steel Channel", "Concrete Channel"):
                sec_data = ChannelSection(**common, depth=t3, bf=t2, tf=tf_val, tw=tw_val)
            elif shape in ("Angle", "Steel Angle", "Concrete Angle"):
                sec_data = AngleSection(**common, depth=t3, bf=t2, tf=tf_val, tw=tw_val)
            elif shape in ("Double Angle", "Steel Double Angle", "Concrete Double Angle"):
                sec_data = DoubleAngleSection(
                    **common,
                    depth=t3,
                    bf=t2,
                    tf=tf_val,
                    tw=tw_val,
                    dis=float(sec.get("DIS", 0)),
                )
            elif shape == "Tee":
                sec_data = TeeSection(**common, depth=t3, bf=t2, tf=tf_val, tw=tw_val)
            elif shape in ("Pipe", "PIPE", "Steel Pipe", "Concrete Pipe", "Filled Steel Pipe"):
                sec_data = PipeSection(**common, od=t3, t=tw_val)
            elif shape in (
                "Box/Tube",
                "Steel Tube",
                "Concrete Tube",
                "Tube",
                "TUBE",
                "Filled Steel Tube",
            ):
                sec_data = BoxSection(**common, depth=t3, bf=t2, tf=tf_val, tw=tw_val)
            elif shape in ("Rectangular", "Rectangle", "RECTANGLE", "Steel Plate"):
                sec_data = RectangularSection(**common, depth=t3, bf=t2)
            elif shape == "Concrete Rectangular":
                _reinf = column_reinf.get(name) or beam_reinf.get(name) or {}
                _bar_id = (
                    _reinf.get("bar_size_along3")
                    or _reinf.get("bar_size_top")
                    or _reinf.get("bar_size_bot")
                )
                _bar_dia = rebar_diameters.get(_bar_id) if _bar_id else None
                _top_dia = float(sec.get("topBarDia", 0))
                _bot_dia = float(sec.get("botBarDia", 0))
                if (not _top_dia or _top_dia <= 0) and _bar_dia:
                    _top_dia = _bar_dia
                if (not _bot_dia or _bot_dia <= 0) and _bar_dia:
                    _bot_dia = _bar_dia
                # Tie data from the column/beam tables feeds Mander
                # confinement when present (all values are model units).
                sec_data = ConcreteRectangularSection(
                    **common,
                    depth=t3,
                    bf=t2,
                    cover=float(sec.get("cover", 0)),
                    top_bars=int(sec.get("topBars", 0)),
                    bot_bars=int(sec.get("botBars", 0)),
                    top_bar_dia=_top_dia,
                    bot_bar_dia=_bot_dia,
                    rebar_material=_reinf.get("rebar_mat"),
                    tie_diameter=_reinf.get("tie_diameter"),
                    tie_spacing=_reinf.get("tie_spacing"),
                    tie_rebar_mat=_reinf.get("tie_rebar_mat"),
                )
            elif shape in (
                "Circle",
                "CIRCLE",
                "Steel Rod",
                "Steel Circle",
                "Concrete Circular",
                "Concrete Circle",
            ):
                if shape in ("Concrete Circular", "Concrete Circle"):
                    _reinf = column_reinf.get(name) or beam_reinf.get(name) or {}
                    _bar_id = (
                        _reinf.get("bar_size_along3")
                        or _reinf.get("bar_size_along2")
                        or _reinf.get("bar_size_top")
                    )
                    _bar_dia = rebar_diameters.get(_bar_id) if _bar_id else None
                    _dia = float(sec.get("barDia", 0))
                    if (not _dia or _dia <= 0) and _bar_dia:
                        _dia = _bar_dia
                    sec_data = ConcreteCircularSection(
                        **common,
                        diameter=t3,
                        cover=float(sec.get("cover", 0)),
                        bar_count=int(sec.get("barCount", 0)),
                        bar_dia=_dia,
                        rebar_material=_reinf.get("rebar_mat"),
                        tie_diameter=_reinf.get("tie_diameter"),
                        tie_spacing=_reinf.get("tie_spacing"),
                        tie_rebar_mat=_reinf.get("tie_rebar_mat"),
                    )
                else:
                    sec_data = CircularSection(**common, diameter=t3)
            elif shape == "SD Section":
                sec_data = SDSection(**common)
            else:
                sec_data = GeneralSection(**common)

            sections[name] = sec_data

        # ── Post-processing: promote RC sections for fiber analysis ──────
        # SAP2000 often uses shape="Rectangular" for RC beams/columns
        # (not "Concrete Rectangular").  Detect these from the material
        # type and promote to ConcreteRectangularSection so that
        # to_fiber_patches() produces RC patches (confined core +
        # unconfined cover + rebar layers) with sensible defaults.
        # Build a lookup of material names -> type from MATERIAL PROPERTIES
        # 01 - GENERAL table (which has the Type column for each material).
        _mat_types: dict[str, str] = {}
        for rec in self._raw_tables.get("MATERIAL PROPERTIES 01 - GENERAL", []):
            mn = str(rec.get("Material", ""))
            mt = str(rec.get("Type", ""))
            if mn:
                _mat_types[mn] = mt

        # Hoist model units lookup outside the loop
        _model_units = self.get_model_units()
        from ..utils import length_scale_factor

        for sec_name, sec in list(sections.items()):
            if not isinstance(sec, RectangularSection):
                continue
            # Skip sections that are already ConcreteRectangularSection subclasses
            if isinstance(sec, ConcreteRectangularSection):
                continue
            mat_type = _mat_types.get(sec.material, "").lower()
            # Accept any material type containing "concrete"
            if "concrete" not in mat_type:
                continue
            # Sensible defaults for RC sections: 40 mm cover, 20 mm bars.
            # Convert default values from metre-based to model length units.
            lf_scale = length_scale_factor(_model_units)
            cover_val = 0.04 * lf_scale
            bar_dia_val = 0.020 * lf_scale
            # Target reinforcement ratio 0.01 (1 %) of gross area
            bar_area = math.pi * (bar_dia_val / 2.0) ** 2
            n_bars = max(4, int(sec.A * 0.01 / bar_area)) if bar_area > 0 else 4
            # Wire in SAP2000 rebar material/size for this section when
            # the concrete-column / concrete-beam table has an entry
            # (e.g. promoted "Rectangular" sections that also appear in
            # the 02 - CONCRETE COLUMN table).
            _reinf = column_reinf.get(sec_name) or beam_reinf.get(sec_name) or {}
            _bar_id = (
                _reinf.get("bar_size_along3")
                or _reinf.get("bar_size_top")
                or _reinf.get("bar_size_bot")
            )
            _bar_dia = rebar_diameters.get(_bar_id) if _bar_id else None
            sections[sec_name] = ConcreteRectangularSection(
                name=sec.name,
                shape=sec.shape,
                material=sec.material,
                A=sec.A,
                I33=sec.I33,
                I22=sec.I22,
                J=sec.J,
                Z33=sec.Z33,
                Z22=sec.Z22,
                modifiers=sec.modifiers,
                depth=sec.depth,
                bf=sec.bf,
                cover=cover_val,
                top_bars=n_bars,
                bot_bars=n_bars,
                top_bar_dia=_bar_dia or bar_dia_val,
                bot_bar_dia=_bar_dia or bar_dia_val,
                rebar_material=_reinf.get("rebar_mat"),
                tie_diameter=_reinf.get("tie_diameter"),
                tie_spacing=_reinf.get("tie_spacing"),
                tie_rebar_mat=_reinf.get("tie_rebar_mat"),
            )

        # ── AREA SECTION PROPERTIES (shell sections not in frame table) ──
        for sec in self._raw_tables.get("AREA SECTION PROPERTIES", []):
            name = sec.get("Section", "Unknown")
            if name in sections:
                continue  # already defined via frame section properties
            mat_name = sec.get("Material", "Unknown")
            thickness = float(sec.get("Thickness", 0))

            sections[name] = ShellSection(
                name=name,
                shape="Shell",
                material=mat_name,
                A=0.0,
                I33=0.0,
                I22=0.0,
                J=0.0,
                thickness=thickness,
                rebar_material=area_rebar_mat.get(name),
            )

        return sections

    def _get_groups(self) -> dict[str, Group]:
        groups = {}
        # Definitions
        defs = self._raw_tables.get("GROUPS 1 - DEFINITIONS", [])
        for g in defs:
            name = g.get("GroupName", "")
            if name:
                groups[name] = Group(name=name, color=g.get("Color"))
        # Assignments (simplified – you can expand)
        assigns = self._raw_tables.get("GROUPS 2 - ASSIGNMENTS", [])
        for a in assigns:
            gname = a.get("GroupName", "")
            if gname in groups:
                obj_type = a.get("ObjectType", "")
                obj_label = str(a.get("ObjectLabel", ""))
                if obj_type and obj_label:
                    groups[gname].objects.append(f"{obj_type}:{obj_label}")
        return groups

    def get_load_cases(self) -> dict[str, LoadCase]:
        """Build load cases from LOAD CASE DEFINITIONS and CASE-* tables."""
        loadcases: dict[str, LoadCase] = {}

        # ── 1. Parse LOAD CASE DEFINITIONS ──
        for rec in self._raw_tables.get("LOAD CASE DEFINITIONS", []):
            cname = rec.get("Case", "")
            if not cname:
                continue
            loadcases[cname] = LoadCase(
                case_name=cname,
                case_type=rec.get("Type", ""),
                design_type_option=rec.get("DesTypeOpt", "Prog Det"),
                design_type=rec.get("DesignType", ""),
                design_action_option=rec.get("DesActOpt", "Prog Det"),
                design_action=rec.get("DesignAct", ""),
                initial_condition=rec.get("InitialCond", "Zero"),
                modal_case=rec.get("ModalCase", ""),
                run_case=rec.get("RunCase", False) in (True, "True", "Yes", 1),
            )

        # ── 2. Parse CASE - RESPONSE SPECTRUM tables ──
        rs_general: dict[str, dict] = {}
        for rec in self._raw_tables.get("CASE - RESPONSE SPECTRUM 1 - GENERAL", []):
            cname = rec.get("Case", "")
            if cname:
                rs_general[cname] = {k: v for k, v in rec.items() if k != "Case"}

        rs_loads: dict[str, list] = {}
        for rec in self._raw_tables.get("CASE - RESPONSE SPECTRUM 2 - LOAD ASSIGNMENTS", []):
            cname = rec.get("Case", "")
            if cname:
                rs_loads.setdefault(cname, []).append({k: v for k, v in rec.items() if k != "Case"})

        # Merge response spectrum data into load cases
        for cname, general in rs_general.items():
            if cname not in loadcases:
                continue
            entry = dict(general)
            if cname in rs_loads:
                entry["LoadAssignments"] = rs_loads[cname]
            loadcases[cname].case_data["CASE - RESPONSE SPECTRUM"] = entry

        # ── 3. Parse all remaining CASE-* tables (MODAL, STATIC, etc.) ──
        handled_prefixes = ("CASE - RESPONSE SPECTRUM",)
        for table_name in self._raw_tables:
            if not table_name.startswith("CASE -"):
                continue
            # Skip tables already handled above
            if any(table_name.startswith(p) for p in handled_prefixes):
                continue
            for rec in self._raw_tables[table_name]:
                cname = rec.get("Case", "")
                if cname in loadcases:
                    # Store under the full table name, skipping the Case key
                    loadcases[cname].case_data[table_name] = {
                        k: v for k, v in rec.items() if k != "Case"
                    }

        return loadcases

    @staticmethod
    def _is_intentionally_ignored(table_name: str) -> bool:
        """Whether *table_name* is registered as deliberately not read.

        Consults :mod:`fea_toolkit.io.table_registry` so a family member the
        toolkit skips on purpose (e.g. ``AUTO WAVE 3 - ...``) is not reported
        as an oversight, while a genuinely new variant is.
        """
        from .table_registry import classify

        return classify(table_name) == "ignored"

    def _get_load_patterns(self) -> dict[str, LoadPattern]:
        patterns = {}
        for rec in self._raw_tables.get("LOAD PATTERN DEFINITIONS", []):
            name = rec.get("LoadPat", "")
            if name:
                patterns[name] = LoadPattern(
                    name=str(name),
                    pattern_type=rec.get("DesignType", ""),
                    self_weight_factor=rec.get("SelfWtMult", 0),
                )
        # Augment with data from AUTO* tables (e.g. AUTO SEISMIC, AUTO WIND).
        # Only members carrying a ``LoadPat`` column act as load-pattern
        # generators; the rest (e.g. ``AUTO WAVE 3 - ...``) are skipped — but
        # never silently: an AUTO table without a ``LoadPat`` column that is
        # not registered as deliberately ignored is logged, so a new variant
        # cannot disappear without trace.
        for table_name, records in self._raw_tables.items():
            if not table_name.startswith(self._AUTO_PREFIX):
                continue
            if not records or "LoadPat" not in records[0]:
                if not self._is_intentionally_ignored(table_name):
                    logging.warning(
                        "AUTO table %r has no LoadPat column and is not "
                        "registered as deliberately ignored — not parsed.",
                        table_name,
                    )
                continue
            for rec in records:
                lp_name = rec.get("LoadPat", "")
                if lp_name in patterns:
                    # Store the whole record under the full table name
                    patterns[lp_name].auto_data[table_name] = dict(rec)
        return patterns

    def _get_mass_sources(self) -> dict[str, MassSource]:
        """Parse MASS SOURCE table — each row defines one MassSource entry.

        Two table formats are supported:

        1. **Modern** (``MASS SOURCE``) — grouped by MassSource name, each
           row has Elements/Masses/Loads flags and LoadPat + Multiplier pairs.
           Multipliers for the same LoadPat within a group are summed.

        2. **Legacy** (``MASSES 1 - MASS SOURCE``) — simple ``MassFrom``
           field with value ``'Elements'``, ``'Masses'``, or ``'Loads'``.
           This is converted to a default ``MSSSRC1`` entry.
        """
        mass_sources: dict[str, MassSource] = {}

        # --- Legacy format: "MASSES 1 - MASS SOURCE" ---
        legacy = self._raw_tables.get("MASSES 1 - MASS SOURCE", [])
        if legacy:
            ms = MassSource(name="MSSSRC1", is_default=True)
            for rec in legacy:
                val = str(rec.get("MassFrom", ""))
                if val.lower() == "elements":
                    ms.elements = True
                elif val.lower() == "masses":
                    ms.masses = True
                elif val.lower() == "loads":
                    ms.loads = True
            mass_sources["MSSSRC1"] = ms
            return mass_sources

        # --- Modern format: "MASS SOURCE" ---
        raw = self._raw_tables.get("MASS SOURCE", [])
        if not raw:
            return mass_sources

        # Group rows by MassSource name
        groups: dict[str, list] = {}
        for rec in raw:
            key = rec.get("MassSource", "")
            if not key:
                continue
            groups.setdefault(key, []).append(rec)

        for name, rows in groups.items():
            first = rows[0]
            ms = MassSource(
                name=str(name),
                elements=self._to_bool(first.get("Elements", False)),
                masses=self._to_bool(first.get("Masses", False)),
                loads=self._to_bool(first.get("Loads", False)),
                is_default=self._to_bool(first.get("IsDefault", False)),
            )
            # Collect all LoadPat + Multiplier pairs — same LoadPat
            # appearing on multiple rows has its multipliers summed.
            load_pat = {}
            for rec in rows:
                lp = rec.get("LoadPat", "")
                mult = float(rec.get("Multiplier", 0))
                if lp:
                    load_pat[lp] = load_pat.get(lp, 0.0) + mult
            ms.load_pattern = load_pat
            mass_sources[name] = ms

        return mass_sources

    @staticmethod
    def _to_bool(val) -> bool:
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.lower() in ("yes", "true", "1")
        return bool(val)

    def _get_joint_loads(self) -> list[JointLoad]:
        loads = []
        for rec in self._raw_tables.get("JOINT LOADS - FORCE", []):
            loads.append(
                JointLoad(
                    pattern=rec.get("LoadPat", ""),
                    node_id=str(rec.get("Joint", "")),
                    # node_tag = self.model.nodes[node_id].tag,
                    fx=float(rec.get("F1", 0.0)),
                    fy=float(rec.get("F2", 0.0)),
                    fz=float(rec.get("F3", 0.0)),
                    mx=float(rec.get("M1", 0.0)),
                    my=float(rec.get("M2", 0.0)),
                    mz=float(rec.get("M3", 0.0)),
                )
            )
        return loads

    def _get_frame_distributed_loads(self) -> list[FrameDistributedLoad]:
        loads = []
        # Standard distributed loads
        for rec in self._raw_tables.get("FRAME LOADS - DISTRIBUTED", []):
            shape = "Uniform"
            val_a = rec.get("FOverLA", 0.0)
            val_b = rec.get("FOverLB", 0.0)
            if val_a != val_b:
                shape = (
                    "Linear"
                    if (rec.get("RelDistA", 0.0) == 0.0 and rec.get("RelDistB", 0.0) == 1.0)
                    else "Trapezoidal"
                )
            loads.append(
                FrameDistributedLoad(
                    pattern=rec.get("LoadPat", ""),
                    frame_id=str(rec.get("Frame", "")),
                    direction=rec.get("Dir", "Gravity"),
                    load_type=rec.get("Type", "Force"),
                    shape=shape,
                    val_a=float(val_a),
                    val_b=float(val_b),
                    rdist_a=float(rec.get("RelDistA", 0.0)),
                    rdist_b=float(rec.get("RelDistB", 0.0)),
                    dist_a=float(rec.get("AbsDistA", 0.0)),
                    dist_b=float(rec.get("AbsDistB", 0.0)),
                    coord_sys=rec.get("CoordSys", "GLOBAL"),
                )
            )

        # Open-structure wind loads (local coordinate directions)
        DIR_MAP = {1: "LocalX", 2: "LocalY", 3: "LocalZ"}
        for rec in self._raw_tables.get("FRAME LOADS - OPEN STRUCTURE WIND", []):
            dir_num = int(rec.get("Dir", 2))
            direction = DIR_MAP.get(dir_num, "LocalY")
            shape = "Uniform"
            val_a = rec.get("FOverLA", 0.0)
            val_b = rec.get("FOverLB", 0.0)
            if val_a != val_b:
                shape = (
                    "Linear"
                    if (rec.get("RelDistA", 0.0) == 0.0 and rec.get("RelDistB", 0.0) == 1.0)
                    else "Trapezoidal"
                )
            loads.append(
                FrameDistributedLoad(
                    pattern=rec.get("LoadCase", ""),
                    frame_id=str(rec.get("Frame", "")),
                    direction=direction,
                    load_type=rec.get("Type", "Force"),
                    shape=shape,
                    val_a=float(val_a),
                    val_b=float(val_b),
                    rdist_a=float(rec.get("RelDistA", 0.0)),
                    rdist_b=float(rec.get("RelDistB", 0.0)),
                    dist_a=float(rec.get("AbsDistA", 0.0)),
                    dist_b=float(rec.get("AbsDistB", 0.0)),
                    coord_sys="Local",
                )
            )
        return loads

    def _get_frame_gravity_loads(self) -> list[GravityLoad]:
        """Parse FRAME LOADS - GRAVITY table."""
        loads = []
        for rec in self._raw_tables.get("FRAME LOADS - GRAVITY", []):
            loads.append(
                GravityLoad(
                    pattern=str(rec.get("LoadPat", "")),
                    frame_id=str(rec.get("Frame", "")),
                    coord_sys=str(rec.get("CoordSys", "GLOBAL")),
                    multiplier_x=float(rec.get("MultiplierX", 0.0)),
                    multiplier_y=float(rec.get("MultiplierY", 0.0)),
                    multiplier_z=float(rec.get("MultiplierZ", 0.0)),
                )
            )
        return loads


# print('Loaded S2K Parser')
