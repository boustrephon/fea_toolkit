"""Parse SAP2000 .S2K text files into intermediate data model."""

import json
import re
from pathlib import Path
from typing import Dict, List, Any, Optional, Union  # noqa: F401
import numpy as np  # noqa: F401

from ..model.sap_data import (
    SAPModelData, Node, Restraint, Material, Section,
    FrameElement, AreaElement, Group, LoadCase, LoadPattern,
    MassSource, JointLoad, FrameDistributedLoad
)
# from ..model.geometry import get_SAP_vecxz

class SAP2000Parser:
    """Parse SAP2000 .S2K file and convert to SAPModelData.

    Usage:
        parser = SAP2000Parser("model.s2k")
        parser.parse()
        model_data = parser.get_model_data()

    The raw table data can be saved to JSON for later reuse:
        parser.to_json("model.json")
        parser2 = SAP2000Parser.from_json("model.json")
    """

    def __init__(self, file_path: Union[str, Path]):
        """Initialise parser with path to .S2K file."""
        self.file_path = Path(file_path)
        self._raw_tables: Dict[str, List[Dict[str, Any]]] = {}

    # -------------------------------------------------------------------------
    # Parsing (adapted from your parse_sap2000_table_file / parse_file)
    # -------------------------------------------------------------------------
    def parse(self) -> None:
        """Parse the .S2K file and store raw tables internally."""
        content = self._read_file_with_encodings(self.file_path)
        self._raw_tables = self._parse_sap2000_table_file(content)

    @staticmethod
    def _read_file_with_encodings(path: Path) -> str:
        """Try multiple encodings to read the file."""
        encodings = ['utf-8', 'cp1252', 'latin-1']
        for enc in encodings:
            try:
                return path.read_text(encoding=enc)
            except UnicodeDecodeError:
                continue
        # Fallback
        return path.read_text(encoding='utf-8', errors='replace')

    @staticmethod
    def _parse_sap2000_table_file(file_content: str) -> Dict[str, List[Dict[str, Any]]]:
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
            metadata_pattern = re.compile(r'^File\s+(.+?)\s+was saved on\s+(.+?)\s+at\s+(.+?)$')
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
                        date_parts = date_str.split('/')
                        if len(date_parts) == 3:
                            month = int(date_parts[0])
                            day = int(date_parts[1])
                            year = int(date_parts[2])
                            
                            # Convert 2-digit year to 4-digit (assuming 2000s)
                            if year < 100:
                                year = 2000 + year
                            
                            # Parse time (format like "11:55:03")
                            time_parts = time_str.split(':')
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
                metadata_record = {
                    "FileName": file_path,
                    "DateSaved": formatted_datetime
                }
                
                # Add METADATA table to result
                result["METADATA"] = [metadata_record]
            
            # Skip the first line for further parsing
            lines = lines[1:]
        
        current_table: Union[str, None] = None
        current_record: dict[str, object] = {}

        # Regular expressions for parsing
        table_regex = re.compile(r'^TABLE:\s+"([^"]+)"')
        kv_pair_regex = re.compile(r'(\w+)=("[^"]*"|[^\s]+)')
        
        for line in lines:
            line = line.strip()
            
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
            ends_with_continuation = line.endswith('_')
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
                        if value.upper() == 'YES':
                            value = True
                        elif value.upper() == 'NO':
                            value = False
                        elif value.upper() == 'NONE':
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
        with open(path, 'w') as f:
            json.dump(self._raw_tables, f, indent=2, default=str)

    @classmethod
    def from_json(cls, json_path: Union[str, Path]) -> "SAP2000Parser":
        """Create a parser instance pre‑loaded from a JSON file."""
        path = Path(json_path)
        parser = cls(path)  # temporary path, not used
        with open(path, 'r') as f:
            parser._raw_tables = json.load(f)
        return parser

    # -------------------------------------------------------------------------
    # Conversion to SAPModelData (extraction functions)
    # -------------------------------------------------------------------------
    def get_model_data(self) -> SAPModelData:
        """Convert raw parsed tables into SAPModelData."""
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
        load_patterns = self._get_load_patterns()
        mass_sources = self._get_mass_sources()
        joint_loads = self._get_joint_loads()
        frame_dist_loads = self._get_frame_distributed_loads()


        return SAPModelData(
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
            load_patterns=load_patterns,
            mass_sources=mass_sources,
            joint_loads=joint_loads,
            frame_dist_loads=frame_dist_loads,
            units=model_units,
        )

    def get_model_units(self) -> Dict[str, str]:
        """Extract the units used in the SAP2000 model.

        The unit string is found in the 'PROGRAM CONTROL' table, e.g. 'N, mm, C'.
        Returns:
            'mm' or 'in' (converted from 'm', 'cm', 'ft' if necessary).
            Defaults to 'mm' if not found or unrecognised.
        """
        program_control = self._raw_tables.get("PROGRAM CONTROL", [])
        if not program_control:
            return {'F': "N", 'L': "m", 'T': "C"}  # default

        # Look for the CurrUnits field
        first_record = program_control[0]
        units_str = first_record.get("CurrUnits", "")
        if not units_str:
            return {'F': "N",'L': "m", 'T': "C"}

        # Expected format: "Force, Length, Temperature"
        # Example: "N, mm, C" or "kN, m, C" or "kip, in, F"
        force, length, temp = [p.strip() for p in units_str.split(",")]
        
        return {'F': force,'L': length, 'T': temp}

    # ---------- Individual extraction methods (adapted from your SAP2OPS_v4.py) ----------
    def _get_all_nodes(self) -> Dict[str, Node]:
        nodes = {}
        tag = 1
        for joint in self._raw_tables.get('JOINT COORDINATES', []):
            nid = str(joint['Joint']) # keep as string
            special = joint.get('SpecialJt', False)
            if isinstance(special, str):
                special = special.lower() == 'yes'
            nodes[nid] = Node(
                node_id=nid,
                node_tag=tag,
                x=float(joint['XorR']),
                y=float(joint['Y']),
                z=float(joint['Z']),
                is_special=bool(special)
            )
            tag += 1
        return nodes

    def _get_all_restraints(self) -> Dict[str, Restraint]:
        restraints = {}
        for joint in self._raw_tables.get('JOINT RESTRAINT ASSIGNMENTS', []):
            nid = str(joint['Joint'])
            dofs = []
            for dof in ['U1', 'U2', 'U3', 'R1', 'R2', 'R3']:
                val = joint.get(dof, False)
                if isinstance(val, str):
                    val = val.lower() == 'true'
                dofs.append(1 if val else 0)
            restraints[nid] = Restraint(dofs=dofs)
        return restraints

    def _get_all_materials(self) -> Dict[str, Material]:
        """Extract all materials by merging every MATERIAL PROPERTIES table."""
        # Step 1: collect raw properties for each material from all relevant tables
        materials_data: Dict[str, Dict[str, Any]] = {}

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
                prop_dict = {k:v for k,v in zip(["E_mod", "G_mod", "nu", "unit_weight", "unit_mass"], [E_mod, G_mod, nu, unit_weight, unit_mass])}
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

                assert E_mod is not None and G_mod is not None and nu is not None
                assert unit_weight is not None and unit_mass is not None

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
                    extra=props   # store everything else (damping, acceptance criteria, etc.)
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


    def _get_frame_auto_mesh(self) -> Dict[str, Dict[str, Any]]:
        """Parse FRAME AUTO MESH ASSIGNMENTS table."""
        auto_mesh = {}
        for rec in self._raw_tables.get('FRAME AUTO MESH ASSIGNMENTS', []):
            frame_id = str(rec.get('Frame', '0'))
            if frame_id != '0':
                auto_mesh[frame_id] = {
                    'AutoMesh': rec.get('AutoMesh', False),
                    'AtJoints': rec.get('AtJoints', False),
                    'AtFrames': rec.get('AtFrames', False),
                    'NumSegments': rec.get('NumSegments', 0),
                    'MaxLength': rec.get('MaxLength', 0),
                    'MaxDegrees': rec.get('MaxDegrees', 0),
                }
        return auto_mesh

    def _get_frame_elements(self) -> Dict[str, FrameElement]:
        elements = {}
        tag = 1
        for f in self._raw_tables.get('CONNECTIVITY - FRAME', []):
            eid = str(f['Frame'])
            # Also get angle from FRAME LOCAL AXES table
            angle = 0.0
            for la in self._raw_tables.get('FRAME LOCAL AXES ASSIGNMENTS 1 - TYPICAL', []):
                if str(la.get('Frame')) == eid:
                    angle = float(la.get('Angle', 0))
                    break
            node_i = str(f['JointI'])
            node_j = str(f['JointJ'])
            # vecxz = get_SAP_vecxz(np.array([1, 0, 0]), angle)
            elements[eid] = FrameElement(
                elem_id=eid,
                elem_tag = tag,
                node_i=node_i,
                node_j=node_j,
                angle=angle
            )
            tag += 1
        return elements

    def _get_area_elements(self) -> Dict[str, AreaElement]:
        areas = {}
        tag = 1
        for a in self._raw_tables.get('CONNECTIVITY - AREA', []):
            aid = str(a.get('Area', 0))
            if not aid:
                continue
            # Collect joint IDs (1-4 or more)
            node_ids = []
            i = 1
            while True:
                joint_key = f'Joint{i}'
                if joint_key in a:
                    node_ids.append(str(a[joint_key]))
                    i += 1
                else:
                    break
            if len(node_ids) >= 3:
                areas[aid] = AreaElement(area_id=aid, area_tag=tag, node_ids=node_ids)
            tag += 1
        return areas

    def _get_frame_assignments(self) -> Dict[str, str]:
        assign = {}
        for a in self._raw_tables.get('FRAME SECTION ASSIGNMENTS', []):
            eid = str(a.get('Frame', '0'))
            sec = a.get('AnalSect', '')
            if eid != '0' and sec and sec != 'N.A.':
                assign[eid] = sec
        return assign

    def _get_area_assignments(self) -> Dict[str, str]:
        assign = {}
        for a in self._raw_tables.get('AREA SECTION ASSIGNMENTS', []):
            aid = str(a.get('Area', '0'))
            sec = a.get('Section', '')
            if aid != '0' and sec:
                assign[aid] = sec
        return assign

    def _get_sections_with_material_properties(self) -> Dict[str, Section]:
        """Combine section geometry from FRAME SECTION PROPERTIES with material data."""
        from ..model.sap_data import (
            ISection, ChannelSection, PipeSection, BoxSection,
            RectangularSection, CircularSection, AngleSection,
            DoubleAngleSection, TeeSection, GeneralSection,
            SDSection, ShellSection,
        )

        sections = {}
        for sec in self._raw_tables.get('FRAME SECTION PROPERTIES 01 - GENERAL', []):
            name = sec.get('SectionName', 'Unknown')
            shape: str = sec.get('Shape', 'Unknown')
            mat_name = sec.get('Material', 'Unknown')

            # Common derived properties
            common = dict(
                name=name,
                shape=shape,
                material=mat_name,
                A=float(sec.get('Area', 0)),
                I33=float(sec.get('I33', 0)),
                I22=float(sec.get('I22', 0)),
                J=float(sec.get('TorsConst', 0)),
                Z33=sec.get('Z33', None),
                Z22=sec.get('Z22', None),
            )
            if common['Z33'] is not None:
                common['Z33'] = float(common['Z33'])
            if common['Z22'] is not None:
                common['Z22'] = float(common['Z22'])

            # Shape‑specific dimensions (SAP2000 t3 = depth, t2 = width)
            t3 = float(sec.get('t3', 0))
            t2 = float(sec.get('t2', 0))
            tw_val = float(sec.get('tw', 0))
            tf_val = float(sec.get('tf', 0))

            sec_data: Section

            if shape in ("Shell",):
                sec_data = ShellSection(
                    **common, thickness=float(sec.get('thickness', 0))
                )
            elif shape in ("I/Wide Flange", "WIDE FLANGE", "Steel I/Wide Flange"):
                sec_data = ISection(
                    **common, depth=t3, bf=t2, tf=tf_val, tw=tw_val
                )
            elif shape in ("Channel", "CHANNEL", "Steel Channel", "Concrete Channel"):
                sec_data = ChannelSection(
                    **common, depth=t3, bf=t2, tf=tf_val, tw=tw_val
                )
            elif shape in ("Angle", "Steel Angle", "Concrete Angle"):
                sec_data = AngleSection(
                    **common, depth=t3, bf=t2, tf=tf_val, tw=tw_val
                )
            elif shape in ("Double Angle", "Steel Double Angle",
                           "Concrete Double Angle"):
                sec_data = DoubleAngleSection(
                    **common, depth=t3, bf=t2, tf=tf_val, tw=tw_val,
                    dis=float(sec.get('DIS', 0)),
                )
            elif shape in ("Tee",):
                sec_data = TeeSection(
                    **common, depth=t3, bf=t2, tf=tf_val, tw=tw_val
                )
            elif shape in ("Pipe", "PIPE", "Steel Pipe", "Concrete Pipe",
                           "Filled Steel Pipe"):
                sec_data = PipeSection(**common, od=t3, t=tw_val)
            elif shape in ("Box/Tube", "Steel Tube", "Concrete Tube",
                           "Tube", "TUBE", "Filled Steel Tube"):
                sec_data = BoxSection(
                    **common, depth=t3, bf=t2, tf=tf_val, tw=tw_val
                )
            elif shape in ("Rectangular", "Rectangle", "RECTANGLE",
                           "Steel Plate", "Concrete Rectangular"):
                sec_data = RectangularSection(**common, depth=t3, bf=t2)
            elif shape in ("Circle", "CIRCLE", "Steel Rod", "Steel Circle",
                           "Concrete Circle"):
                sec_data = CircularSection(**common, diameter=t3)
            elif shape == "SD Section":
                sec_data = SDSection(**common)
            else:
                sec_data = GeneralSection(**common)

            sections[name] = sec_data

        return sections

    def _get_groups(self) -> Dict[str, Group]:
        groups = {}
        # Definitions
        defs = self._raw_tables.get('GROUPS 1 - DEFINITIONS', [])
        for g in defs:
            name = g.get('GroupName', '')
            if name:
                groups[name] = Group(name=name, color=g.get('Color'))
        # Assignments (simplified – you can expand)
        assigns = self._raw_tables.get('GROUPS 2 - ASSIGNMENTS', [])
        for a in assigns:
            gname = a.get('GroupName', '')
            if gname in groups:
                obj_type = a.get('ObjectType', '')
                obj_label = str(a.get('ObjectLabel', ''))
                if obj_type and obj_label:
                    groups[gname].objects.append(f"{obj_type}:{obj_label}")
        return groups

    def get_load_cases(self)-> Dict[str, LoadCase]:
        loadcases = {}
        # TODO
        return loadcases

    def _get_load_patterns(self) -> Dict[str, LoadPattern]:
        patterns = {}
        for rec in self._raw_tables.get('LOAD PATTERN DEFINITIONS', []):
            name = rec.get('LoadPat', '')
            if name:
                patterns[name] = LoadPattern(
                    name = str(name),
                    pattern_type = rec.get('DesignType', ''),
                    self_weight_factor = rec.get('SelfWtMult', 0)
                    )
        # Also add default patterns if needed? We'll keep as is.
        return patterns

    def _get_mass_sources(self) -> Dict[str, MassSource]:
        """Parse MASS SOURCE table — each row defines one MassSource entry.

        Two table formats are supported:

        1. **Modern** (``MASS SOURCE``) — grouped by MassSource name, each
           row has Elements/Masses/Loads flags and LoadPat + Multiplier pairs.
           Multipliers for the same LoadPat within a group are summed.

        2. **Legacy** (``MASSES 1 - MASS SOURCE``) — simple ``MassFrom``
           field with value ``'Elements'``, ``'Masses'``, or ``'Loads'``.
           This is converted to a default ``MSSSRC1`` entry.
        """
        mass_sources: Dict[str, MassSource] = {}

        # --- Legacy format: "MASSES 1 - MASS SOURCE" ---
        legacy = self._raw_tables.get('MASSES 1 - MASS SOURCE', [])
        if legacy:
            ms = MassSource(name='MSSSRC1', is_default=True)
            for rec in legacy:
                val = str(rec.get('MassFrom', ''))
                if val.lower() == 'elements':
                    ms.elements = True
                elif val.lower() == 'masses':
                    ms.masses = True
                elif val.lower() == 'loads':
                    ms.loads = True
            mass_sources['MSSSRC1'] = ms
            return mass_sources

        # --- Modern format: "MASS SOURCE" ---
        raw = self._raw_tables.get('MASS SOURCE', [])
        if not raw:
            return mass_sources

        # Group rows by MassSource name
        groups: Dict[str, list] = {}
        for rec in raw:
            key = rec.get('MassSource', '')
            if not key:
                continue
            groups.setdefault(key, []).append(rec)

        for name, rows in groups.items():
            first = rows[0]
            ms = MassSource(
                name=str(name),
                elements=self._to_bool(first.get('Elements', False)),
                masses=self._to_bool(first.get('Masses', False)),
                loads=self._to_bool(first.get('Loads', False)),
                is_default=self._to_bool(first.get('IsDefault', False)),
            )
            # Collect all LoadPat + Multiplier pairs — same LoadPat
            # appearing on multiple rows has its multipliers summed.
            load_pat = {}
            for rec in rows:
                lp = rec.get('LoadPat', '')
                mult = float(rec.get('Multiplier', 0))
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
            return val.lower() in ('yes', 'true', '1')
        return bool(val)

    def _get_joint_loads(self) -> List[JointLoad]:
        loads = []
        for rec in self._raw_tables.get('JOINT LOADS - FORCE', []):
            loads.append(JointLoad(
                pattern=rec.get('LoadPat', ''),
                node_id=str(rec.get('Joint', '')),
                # node_tag = self.model.nodes[node_id].tag,
                fx=float(rec.get('F1', 0.0)),
                fy=float(rec.get('F2', 0.0)),
                fz=float(rec.get('F3', 0.0)),
                mx=float(rec.get('M1', 0.0)),
                my=float(rec.get('M2', 0.0)),
                mz=float(rec.get('M3', 0.0))
            ))
        return loads

    def _get_frame_distributed_loads(self) -> List[FrameDistributedLoad]:
        loads = []
        # Standard distributed loads
        for rec in self._raw_tables.get('FRAME LOADS - DISTRIBUTED', []):
            shape = 'Uniform'
            val_a = rec.get('FOverLA', 0.0)
            val_b = rec.get('FOverLB', 0.0)
            if val_a != val_b:
                shape = 'Linear' if (rec.get('RelDistA', 0.0) == 0.0 and rec.get('RelDistB', 0.0) == 1.0) else 'Trapezoidal'
            loads.append(FrameDistributedLoad(
                pattern=rec.get('LoadPat', ''),
                frame_id=str(rec.get('Frame', '')),
                direction=rec.get('Dir', 'Gravity'),
                load_type=rec.get('Type', 'Force'),
                shape=shape,
                val_a=float(val_a),
                val_b=float(val_b),
                rdist_a=float(rec.get('RelDistA', 0.0)),
                rdist_b=float(rec.get('RelDistB', 0.0)),
                dist_a=float(rec.get('AbsDistA', 0.0)),
                dist_b=float(rec.get('AbsDistB', 0.0)),
                coord_sys=rec.get('CoordSys', 'GLOBAL')
            ))

        # Open-structure wind loads (local coordinate directions)
        DIR_MAP = {1: 'LocalX', 2: 'LocalY', 3: 'LocalZ'}
        for rec in self._raw_tables.get('FRAME LOADS - OPEN STRUCTURE WIND', []):
            dir_num = int(rec.get('Dir', 2))
            direction = DIR_MAP.get(dir_num, 'LocalY')
            shape = 'Uniform'
            val_a = rec.get('FOverLA', 0.0)
            val_b = rec.get('FOverLB', 0.0)
            if val_a != val_b:
                shape = 'Linear' if (rec.get('RelDistA', 0.0) == 0.0
                                     and rec.get('RelDistB', 0.0) == 1.0) else 'Trapezoidal'
            loads.append(FrameDistributedLoad(
                pattern=rec.get('LoadCase', ''),
                frame_id=str(rec.get('Frame', '')),
                direction=direction,
                load_type=rec.get('Type', 'Force'),
                shape=shape,
                val_a=float(val_a),
                val_b=float(val_b),
                rdist_a=float(rec.get('RelDistA', 0.0)),
                rdist_b=float(rec.get('RelDistB', 0.0)),
                dist_a=float(rec.get('AbsDistA', 0.0)),
                dist_b=float(rec.get('AbsDistB', 0.0)),
                coord_sys='Local',
            ))
        return loads

print('Loaded S2K Parser')
