"""Flexible selection/filter criteria for SAP2000 model elements."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from .sap_data import SAPModelData, FrameElement, AreaElement, Node
    from .sap_data import AreaUniformLoad, AreaGravityLoad


@dataclass
class Selection:
    """Flexible criteria for selecting elements from a SAP2000 model.

    **Logic rules**

    *AND across criteria* — every non-``None`` field narrows the selection
    further.  An element must satisfy **all** of them to be included:

        Selection(element_types=['Area'], sections=['Roof slab'])
        # → element must be an Area AND have section "Roof slab"

    *OR within a list* — multiple values in the same field are alternatives.
    An element matching **any** of them passes that criterion:

        Selection(element_types=['Frame', 'Area'])
        # → element can be a Frame OR an Area (or both)

        Selection(sections=['Roof slab', 'Floor slab'])
        # → element section can be "Roof slab" OR "Floor slab"

    *Type-specific behaviour*

    - **Frame** and **Area** elements check ``section`` and ``material``
      criteria via their respective assignment maps
      (:attr:`SAPModelData.frame_assignments` /
      :attr:`SAPModelData.area_assignments`).
    - **Node** elements ignore ``section`` and ``material`` (they have
      none).  Only ``element_types``, ``groups``, and ``element_ids`` apply.
    - **Group** membership is tested against :class:`Group` objects, which
      store references like ``"Frame:123"``, ``"Area:456"``, ``"Joint:1"``.
    - When ``element_types`` is ``None`` (default), **all** element types
      are eligible — use this to filter by section / material / group alone
      regardless of type.

    Parameters
    ----------
    element_types:
        Filter by element type(s) — ``'Frame'``, ``'Area'``, ``'Node'``.
        ``None`` means all types are eligible.
    sections:
        Filter by section/property name(s).  Applies to **Frame** and
        **Area** elements (checks :attr:`SAPModelData.frame_assignments`
        / :attr:`SAPModelData.area_assignments`).  ``None`` means all.
    materials:
        Filter by material name(s).  An element matches if its assigned
        section's material is in this list.  ``None`` means all.
    groups:
        Filter by group name(s).  An element matches if it belongs to at
        least one of the named groups.  ``None`` means all.
    element_ids:
        Filter by specific element ID(s).  ``None`` means all.

    Examples
    --------
    Select all frame members in a lateral-resisting group:

        >>> sel = Selection(element_types=['Frame'], groups=['Moment Frame'])
        >>> frame_ids = sel.get_frame_ids(model)

    Select all areas made of a specific material:

        >>> sel = Selection(
        ...     element_types=['Area'],
        ...     materials=['C30/37'],
        ... )
        >>> areas = sel.filter_areas(model)

    Select areas with specific slab sections and inspect their loads:

        >>> sel = Selection(
        ...     element_types=['Area'],
        ...     sections=['Slab 200mm', 'Roof 150mm'],
        ... )
        >>> uni = sel.filter_area_uniform_loads(model)
        >>> grav = sel.filter_area_gravity_loads(model)

    Use in the builder to control which area loads become edge loads:

        >>> builder.build(selection=sel)
        >>> len(builder.edge_loads_from_areas)
        0   # no uniform loads on those sections
    """

    element_types: Optional[List[str]] = None
    sections: Optional[List[str]] = None
    materials: Optional[List[str]] = None
    groups: Optional[List[str]] = None
    element_ids: Optional[List[str]] = None

    # ── helpers ──────────────────────────────────────────────────────────────

    def _match_element_type(self, etype: str) -> bool:
        if self.element_types is None:
            return True
        return etype in self.element_types

    def _match_section(self, sec_name: Optional[str]) -> bool:
        if self.sections is None:
            return True
        if sec_name is None:
            return False
        return sec_name in self.sections

    def _match_material(
        self, model: "SAPModelData", sec_name: Optional[str]
    ) -> bool:
        if self.materials is None:
            return True
        if sec_name is None:
            return False
        sec = model.sections.get(sec_name)
        if sec is None:
            return False
        return sec.material in self.materials

    def _match_groups(
        self, model: "SAPModelData", etype: str, eid: str
    ) -> bool:
        if self.groups is None:
            return True
        # Groups store references as "Frame:123", "Area:456", "Joint:1"
        prefix = etype + ":"
        for gname in self.groups:
            grp = model.groups.get(gname)
            if grp is None:
                continue
            if f"{prefix}{eid}" in grp.objects:
                return True
        return False

    def _match_id(self, eid: str) -> bool:
        if self.element_ids is None:
            return True
        return eid in self.element_ids

    def _frame_matches(self, model: "SAPModelData", eid: str) -> bool:
        if not self._match_element_type("Frame"):
            return False
        if not self._match_id(eid):
            return False
        sec_name = model.frame_assignments.get(eid)
        if not self._match_section(sec_name):
            return False
        if not self._match_material(model, sec_name):
            return False
        if not self._match_groups(model, "Frame", eid):
            return False
        return True

    def _area_matches(self, model: "SAPModelData", eid: str) -> bool:
        if not self._match_element_type("Area"):
            return False
        if not self._match_id(eid):
            return False
        sec_name = model.area_assignments.get(eid)
        if not self._match_section(sec_name):
            return False
        if not self._match_material(model, sec_name):
            return False
        if not self._match_groups(model, "Area", eid):
            return False
        return True

    def _node_matches(self, model: "SAPModelData", eid: str) -> bool:
        if not self._match_element_type("Node"):
            return False
        if not self._match_id(eid):
            return False
        if not self._match_groups(model, "Joint", eid):
            return False
        # Nodes have no section/material, so those criteria are skipped
        return True

    # ── Public query methods ─────────────────────────────────────────────────

    def get_frame_ids(self, model: "SAPModelData") -> List[str]:
        """Return frame element IDs matching this selection."""
        return [
            eid for eid in model.frame_elements
            if self._frame_matches(model, eid)
        ]

    def get_area_ids(self, model: "SAPModelData") -> List[str]:
        """Return area element IDs matching this selection."""
        return [
            eid for eid in model.area_elements
            if self._area_matches(model, eid)
        ]

    def get_node_ids(self, model: "SAPModelData") -> List[str]:
        """Return node IDs matching this selection."""
        return [
            nid for nid in model.nodes
            if self._node_matches(model, nid)
        ]

    # ── Dict filters ─────────────────────────────────────────────────────────

    def filter_frames(
        self, model: "SAPModelData"
    ) -> Dict[str, "FrameElement"]:
        """Return filtered frame elements as ``{id: FrameElement}``."""
        return {
            eid: model.frame_elements[eid]
            for eid in self.get_frame_ids(model)
        }

    def filter_areas(
        self, model: "SAPModelData"
    ) -> Dict[str, "AreaElement"]:
        """Return filtered area elements as ``{id: AreaElement}``."""
        return {
            eid: model.area_elements[eid]
            for eid in self.get_area_ids(model)
        }

    def filter_nodes(
        self, model: "SAPModelData"
    ) -> Dict[str, "Node"]:
        """Return filtered nodes as ``{id: Node}``."""
        return {
            nid: model.nodes[nid]
            for nid in self.get_node_ids(model)
        }

    # ── Load filters ─────────────────────────────────────────────────────────

    def filter_area_uniform_loads(
        self, model: "SAPModelData"
    ) -> List["AreaUniformLoad"]:
        """Return area uniform loads for areas matching this selection.

        Only checks membership (element type ``'Area'`` plus any
        section / material / group / id filters).  If the selection
        has ``element_types`` set, it must include ``'Area'``.
        """
        selected_ids: Set[str] = set(self.get_area_ids(model))
        return [
            ld for ld in model.area_uniform_loads
            if ld.area_id in selected_ids
        ]

    def filter_area_gravity_loads(
        self, model: "SAPModelData"
    ) -> List["AreaGravityLoad"]:
        """Return area gravity loads for areas matching this selection."""
        selected_ids: Set[str] = set(self.get_area_ids(model))
        return [
            ld for ld in model.area_gravity_loads
            if ld.area_id in selected_ids
        ]
