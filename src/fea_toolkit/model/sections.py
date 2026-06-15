# fea_toolkit/model/sections.py

import pickle
from pathlib import Path
from typing import Dict, Optional, Any, List

from .sap_data import Section


class SectionLibrary:
    """Load manufacturer section database and convert properties to model units.

    The conversion is based on the exponent of length for each property:
        - length¹  : dimensions, radii of gyration, offsets
        - length²  : area, shear areas
        - length³  : elastic/plastic section moduli
        - length⁴  : moments of inertia, torsional constant

    Usage:
        lib = SectionLibrary(Path("section_dict.pkl"), target_units='m')
        lib.enrich_section(my_section)   # my_section must be in target_units
    """

    # Mapping from property name to length exponent
    # Add more names as needed from your database
    _EXPONENT_MAP = {
        # exponent 1 (length)
        'B': 1, 'BF': 1, 'D': 1, 'TW': 1, 'TF': 1, 'TFT': 1, 'TFB': 1,
        'BFT': 1, 'BFB': 1, 'DIS': 1, 'R33': 1, 'R22': 1, 'OD': 1,
        'TDES': 1, 'Y': 1, 'X': 1, 't3': 1, 't2': 1, 'depth': 1, 'width': 1,
        'KDES': 1, 'tw': 1, 'tf': 1, 'tfb': 1, 't2b': 1, 'FilletRadius': 1,
        'CGOffset3': 1, 'CGOffset2': 1,
        # exponent 2 (area)
        'A': 2, 'AS2': 2, 'AS3': 2,
        # exponent 3 (section moduli)
        'Z33': 3, 'Z22': 3, 'S33POS': 3, 'S22POS': 3, 'S33NEG': 3, 'S22NEG': 3,
        # exponent 4 (moments of inertia, torsion constant)
        'I33': 4, 'I22': 4, 'J': 4, 'TorsConst': 4, 'Cw': 4,
    }

    # Conversion factor from inches to mm (for exponent 1)
    INCH_TO_MM = 25.4
    INCH_TO_CM = 2.54
    INCH_TO_M = 0.0254
    FOOT_TO_MM = 304.8
    FOOT_TO_CM = 30.48
    FOOT_TO_M = 0.3048

    def __init__(self, db_path: Path, target_units: str = 'mm'):
        """Load the pickle file and set target unit system.

        Args:
            db_path: Path to the section_dict.pkl file.
            target_units: Desired unit system for all properties ('mm' or 'in').
                          Must match the units of the SAP2000 model.
        """
        self.db_path = db_path
        self.target_units = target_units.lower()
        self._catalogues: Dict[str, Dict] = {}
        self._load()

    def _load(self) -> None:
        with open(self.db_path, 'rb') as f:
            self._catalogues = pickle.load(f)

    def list_catalogues(self) -> List[str]:
        return list(self._catalogues.keys())

    def get_section_properties(self, name: str) -> Optional[Dict[str, Any]]:
        """Return raw properties dict (with units info) for a section name."""
        for cat_name, cat_data in self._catalogues.items():
            sections_dict = cat_data.get('SECTIONS', cat_data)
            if name in sections_dict:
                props = sections_dict[name].copy()
                props['_catalogue'] = cat_name
                props['_length_units'] = cat_data.get('LENGTH_UNITS', 'm')
                return props
        return None

    def _convert_value(self, value: Any, from_units: str, exponent: int) -> Optional[float]:
        """Convert a numeric value based on length exponent."""
        if value is None:
            return None
        try:
            val = float(value)
        except (ValueError, TypeError):
            return None

        if from_units == self.target_units:
            return val

        # Determine conversion factor
        if from_units == 'in' and self.target_units == 'mm':
            factor = self.INCH_TO_MM ** exponent
        elif from_units == 'in' and self.target_units == 'm':
            factor = self.INCH_TO_M ** exponent
        elif from_units == 'mm' and self.target_units == 'in':
            factor = (1.0 / self.INCH_TO_MM) ** exponent
        elif from_units == 'm' and self.target_units == 'in':
            factor = (1.0 / self.INCH_TO_M) ** exponent
        else:
            factor = 1.0  # unknown units, no conversion
        return val * factor

    def enrich_section(self, section: Section) -> Section:
        """Add manufacturer data to a Section, converting units to match SAP2000 model.

        The section is modified in place and returned.
        """
        props = self.get_section_properties(section.name)
        if not props:
            return section

        from_units = props.pop('_length_units', 'mm')
        # Remove internal marker
        props.pop('_catalogue', None)

        # Process all known properties using the exponent map
        for prop_name, exponent in self._EXPONENT_MAP.items():
            if prop_name not in props:
                continue
            raw_value = props[prop_name]
            converted = self._convert_value(raw_value, from_units, exponent)
            if converted is None:
                continue

            # Map to Section fields (use same name where possible)
            # Some property names differ between database and Section dataclass
            if prop_name == 'D':
                section.depth = converted
            elif prop_name == 'BF' or prop_name == 'B':
                section.width = converted
            elif prop_name == 'TW':
                section.tw = converted
            elif prop_name == 'TF':
                section.tf = converted
            elif prop_name in ('Z33', 'Z22', 'I33', 'I22', 'J', 'A'):
                setattr(section, prop_name, converted)
            # Add more mappings as needed

        # Set manufacturer name
        section.manufacturer = props.get('_catalogue', 'unknown')
        return section

