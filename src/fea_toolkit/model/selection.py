"""Flexible selection/filter criteria for SAP2000 model elements."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple, TYPE_CHECKING, Union

if TYPE_CHECKING:
    from .mesh_model import MeshModel
    from .sap_data import Group, SAPModelData, FrameElement, AreaElement, Node
    from .sap_data import AreaUniformLoad, AreaGravityLoad
    from .stories import StoryLevel


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
    elevation_range:
        ``(z_min, z_max)`` tuple in model length units.  An element is
        included if its **mid-height Z** coordinate falls within
        ``[z_min, z_max]``.  For frame elements, mid-height = ``(z_i + z_j)
        / 2``.  For area elements, mid-height = centroid Z of all vertex
        nodes.  ``None`` (default) means no elevation filter.
    story:
        Filter by storey name(s) — e.g. ``["Roof", "Level 2"]``.  An
        element is included if its mid-height Z is within ``story_z_tolerance``
        of the named storey's elevation.  Requires ``storey_data`` to be
        passed to :meth:`resolve_to_mesh_sets`.  ``None`` (default) means
        no storey filter.

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

    Record frame elements between Z = 0 and Z = 3 m during pushover:

        >>> sel = Selection(
        ...     element_types=['Frame'],
        ...     elevation_range=(0.0, 3.0),
        ... )

    Record shear walls on a specific storey (requires storey_data):

        >>> from fea_toolkit.model.stories import identify_stories
        >>> stories = identify_stories(model, raw_tables)
        >>> sel = Selection(
        ...     element_types=['Area'],
        ...     sections=['Shear Wall'],
        ...     story=['Level 2'],
        ... )
        >>> frame_ids, area_ids = sel.resolve_to_mesh_sets(
        ...     mesh_model, storey_data=stories,
        ... )
    """

    element_types: Optional[List[str]] = None
    sections: Optional[List[str]] = None
    materials: Optional[List[str]] = None
    groups: Optional[List[str]] = None
    element_ids: Optional[List[str]] = None
    elevation_range: Optional[Tuple[float, float]] = None
    story: Optional[List[str]] = None

    def __post_init__(self) -> None:
        """Validate invariants after construction."""
        if (self.elevation_range is not None
                and self.elevation_range[0] > self.elevation_range[1]):
            raise ValueError(
                f"Invalid elevation_range {self.elevation_range}: "
                f"lower bound must not exceed upper bound"
            )

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
        self,
        model: Union["SAPModelData", "MeshModel"],
        sec_name: Optional[str],
    ) -> bool:
        """Check the material criterion against either model type.

        Both :class:`SAPModelData` and :class:`MeshModel` expose the same
        ``sections`` mapping, so the same lookup serves both.
        """
        if self.materials is None:
            return True
        if sec_name is None:
            return False
        sec = model.sections.get(sec_name)
        if sec is None:
            return False
        return sec.material in self.materials

    def _match_groups(
        self,
        model: Union["SAPModelData", "MeshModel"],
        etype: str,
        eid: str,
    ) -> bool:
        """Check the group membership criterion against either model type.

        Both :class:`SAPModelData` and :class:`MeshModel` expose the same
        ``groups`` mapping, so the same lookup serves both.
        """
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

    def _frame_matches(
        self,
        model: Union["SAPModelData", "MeshModel"],
        eid: str,
        story_elevations: Optional[Dict[str, float]] = None,
        story_z_tolerance: float = 0.5,
    ) -> bool:
        """Check all selection criteria against either model type.

        When ``story_elevations`` is supplied (e.g. from
        :func:`~fea_toolkit.model.stories.identify_stories`), the
        :attr:`elevation_range` and :attr:`story` filters are also applied
        using the frame's mid-height Z.
        """
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
        # Elevation and story filters (only if either is set)
        if self.elevation_range is not None or self.story is not None:
            z_mid = self._get_frame_z_mid(model, eid)
            if not self._match_z_filter(
                z_mid, story_elevations, story_z_tolerance
            ):
                return False
        return True

    def _area_matches(
        self,
        model: Union["SAPModelData", "MeshModel"],
        eid: str,
        story_elevations: Optional[Dict[str, float]] = None,
        story_z_tolerance: float = 0.5,
    ) -> bool:
        """Check all selection criteria against either model type.

        When ``story_elevations`` is supplied (e.g. from
        :func:`~fea_toolkit.model.stories.identify_stories`), the
        :attr:`elevation_range` and :attr:`story` filters are also applied
        using the area's centroid Z.
        """
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
        # Elevation and story filters (only if either is set)
        if self.elevation_range is not None or self.story is not None:
            z_mid = self._get_area_z_mid(model, eid)
            if not self._match_z_filter(
                z_mid, story_elevations, story_z_tolerance
            ):
                return False
        return True

    def _node_matches(
        self,
        model: Union["SAPModelData", "MeshModel"],
        eid: str,
    ) -> bool:
        if not self._match_element_type("Node"):
            return False
        if not self._match_id(eid):
            return False
        if not self._match_groups(model, "Joint", eid):
            return False
        # Nodes have no section/material, so those criteria are skipped
        return True

    # ── Public query methods ─────────────────────────────────────────────────

    def get_frame_ids(
        self, model: Union["SAPModelData", "MeshModel"]
    ) -> List[str]:
        """Return frame element IDs matching this selection.

        Both :class:`SAPModelData` and :class:`MeshModel` expose the same
        ``frame_elements`` mapping, so the same lookup serves both.
        """
        return [
            eid for eid in model.frame_elements
            if self._frame_matches(model, eid)
        ]

    def get_area_ids(
        self, model: Union["SAPModelData", "MeshModel"]
    ) -> List[str]:
        """Return area element IDs matching this selection.

        Both :class:`SAPModelData` and :class:`MeshModel` expose the same
        ``area_elements`` mapping, so the same lookup serves both.
        """
        return [
            eid for eid in model.area_elements
            if self._area_matches(model, eid)
        ]

    def get_node_ids(
        self, model: Union["SAPModelData", "MeshModel"]
    ) -> List[str]:
        """Return node IDs matching this selection.

        Both :class:`SAPModelData` and :class:`MeshModel` expose the same
        ``nodes`` mapping, so the same lookup serves both.
        """
        return [
            nid for nid in model.nodes
            if self._node_matches(model, nid)
        ]

    # ── Dict filters ─────────────────────────────────────────────────────────

    def filter_frames(
        self, model: Union["SAPModelData", "MeshModel"]
    ) -> Dict[str, "FrameElement"]:
        """Return filtered frame elements as ``{id: FrameElement}``.

        Both :class:`SAPModelData` and :class:`MeshModel` store
        :class:`FrameElement` objects, so the same lookup serves both.
        """
        return {
            eid: model.frame_elements[eid]
            for eid in self.get_frame_ids(model)
        }

    def filter_areas(
        self, model: Union["SAPModelData", "MeshModel"]
    ) -> Dict[str, "AreaElement"]:
        """Return filtered area elements as ``{id: AreaElement}``.

        Both :class:`SAPModelData` and :class:`MeshModel` store
        :class:`AreaElement` objects, so the same lookup serves both.
        """
        return {
            eid: model.area_elements[eid]
            for eid in self.get_area_ids(model)
        }

    def filter_nodes(
        self, model: Union["SAPModelData", "MeshModel"]
    ) -> Dict[str, "Node"]:
        """Return filtered nodes as ``{id: Node}``.

        Both :class:`SAPModelData` and :class:`MeshModel` store
        :class:`Node` objects, so the same lookup serves both.
        """
        return {
            nid: model.nodes[nid]
            for nid in self.get_node_ids(model)
        }

    # ── Load filters ─────────────────────────────────────────────────────────

    def filter_area_uniform_loads(
        self, model: Union["SAPModelData", "MeshModel"]
    ) -> List["AreaUniformLoad"]:
        """Return area uniform loads for areas matching this selection.

        Only checks membership (element type ``'Area'`` plus any
        section / material / group / id filters).  If the selection
        has ``element_types`` set, it must include ``'Area'``.

        Both :class:`SAPModelData` and :class:`MeshModel` expose the same
        ``area_uniform_loads`` list, so the same lookup serves both.
        """
        selected_ids: Set[str] = set(self.get_area_ids(model))
        return [
            ld for ld in model.area_uniform_loads
            if ld.area_id in selected_ids
        ]

    def filter_area_gravity_loads(
        self, model: Union["SAPModelData", "MeshModel"]
    ) -> List["AreaGravityLoad"]:
        """Return area gravity loads for areas matching this selection.

        Both :class:`SAPModelData` and :class:`MeshModel` expose the same
        ``area_gravity_loads`` list, so the same lookup serves both.
        """
        selected_ids: Set[str] = set(self.get_area_ids(model))
        return [
            ld for ld in model.area_gravity_loads
            if ld.area_id in selected_ids
        ]

    # ── Self-contained subset ────────────────────────────────────────────────

    def filter_model(self, model: "SAPModelData") -> "SAPModelData":
        """Create a new, self-contained ``SAPModelData`` for this selection.

        The returned model contains only the entities needed by the selected
        elements — their nodes, sections, materials, restraints, and loads.
        The original model is **not** modified.

        This is useful for:

        * **Plotting** — show only a structural subsystem with all its
          dependencies resolved.
        * **Export** — create a clean subset for exchange or debugging.
        * **Verification** — confirm the selection is self-consistent.

        Only **Frame** and **Area** selections are currently supported.
        A pure **Node** selection (no Frame or Area types) will return an
        empty model.

        Returns:
            A new ``SAPModelData`` instance containing only the entities
            required by this selection.
        """
        from .sap_data import SAPModelData

        # 1. Collect element IDs that match
        frame_ids = set(self.get_frame_ids(model))
        area_ids = set(self.get_area_ids(model))

        # 2. Collect referenced node IDs
        node_ids: Set[str] = set()
        for fid in frame_ids:
            fe = model.frame_elements.get(fid)
            if fe is not None:
                node_ids.add(fe.node_i)
                node_ids.add(fe.node_j)
        for aid in area_ids:
            ae = model.area_elements.get(aid)
            if ae is not None:
                node_ids.update(ae.node_ids)

        # 3. Collect section names referenced by selected elements
        sec_names: Set[str] = set()
        for fid in frame_ids:
            s = model.frame_assignments.get(fid)
            if s:
                sec_names.add(s)
        for aid in area_ids:
            s = model.area_assignments.get(aid)
            if s:
                sec_names.add(s)

        # 4. Collect material names from those sections
        mat_names: Set[str] = set()
        for sn in sec_names:
            sec = model.sections.get(sn)
            if sec is not None:
                mat_names.add(sec.material)

        # 5. Build filtered dicts
        subset = SAPModelData(
            # Nodes
            nodes={nid: model.nodes[nid] for nid in node_ids
                   if nid in model.nodes},
            # Restraints on those nodes
            restraints={nid: model.restraints[nid] for nid in node_ids
                        if nid in model.restraints},
            # Materials used by selected sections
            materials={mn: model.materials[mn] for mn in mat_names
                       if mn in model.materials},
            # Sections used by selected elements
            sections={sn: model.sections[sn] for sn in sec_names
                      if sn in model.sections},
            # Frame & area elements
            frame_elements={fid: model.frame_elements[fid] for fid in frame_ids
                            if fid in model.frame_elements},
            area_elements={aid: model.area_elements[aid] for aid in area_ids
                           if aid in model.area_elements},
            # Assignments
            frame_assignments={fid: model.frame_assignments[fid]
                               for fid in frame_ids
                               if fid in model.frame_assignments},
            area_assignments={aid: model.area_assignments[aid]
                              for aid in area_ids
                              if aid in model.area_assignments},
            # Auto-mesh for selected frames
            frame_auto_mesh={fid: model.frame_auto_mesh[fid]
                             for fid in frame_ids
                             if fid in model.frame_auto_mesh},
            # Groups — keep those that contain selected elements, with only
            # the matching references
            groups=self._filter_groups(model, frame_ids, area_ids, node_ids),
            # Load definitions — keep all (harmless)
            load_cases=model.load_cases,
            load_patterns=model.load_patterns,
            mass_sources=model.mass_sources,
            # Loads on selected elements / nodes
            joint_loads=[jl for jl in model.joint_loads
                         if jl.node_id in node_ids],
            frame_dist_loads=[ld for ld in model.frame_dist_loads
                              if ld.frame_id in frame_ids],
            frame_gravity_loads=[gl for gl in model.frame_gravity_loads
                                 if gl.frame_id in frame_ids],
            area_uniform_loads=self.filter_area_uniform_loads(model),
            area_gravity_loads=self.filter_area_gravity_loads(model),
            # Units
            units=dict(model.units),
        )
        return subset

    def _filter_groups(
        self,
        model: Union["SAPModelData", "MeshModel"],
        frame_ids: Set[str],
        area_ids: Set[str],
        node_ids: Set[str],
    ) -> Dict[str, "Group"]:
        """Return groups that have at least one selected element, pruned
        to only those references.

        Both :class:`SAPModelData` and :class:`MeshModel` expose the same
        ``groups`` mapping, so the same lookup serves both.
        """
        from .sap_data import Group
        result: Dict[str, Group] = {}
        for gname, grp in model.groups.items():
            kept: List[str] = []
            for obj in grp.objects:
                # Object references are "Frame:123", "Area:456", "Joint:1"
                if obj.startswith("Frame:"):
                    eid = obj.split(":", 1)[1]
                    if eid in frame_ids:
                        kept.append(obj)
                elif obj.startswith("Area:"):
                    eid = obj.split(":", 1)[1]
                    if eid in area_ids:
                        kept.append(obj)
                elif obj.startswith("Joint:"):
                    nid = obj.split(":", 1)[1]
                    if nid in node_ids:
                        kept.append(obj)
                else:
                    # Unknown type — keep it (conservative)
                    kept.append(obj)
            if kept:
                result[gname] = Group(
                    name=gname,
                    color=grp.color,
                    objects=kept,
                )
        return result

    # ── MeshModel resolution (for pushover per-step recording) ──────────────

    def resolve_to_mesh_sets(
        self,
        mesh_model: "MeshModel",
        storey_data: "Optional[List[StoryLevel]]" = None,
        story_z_tolerance: float = 0.5,
    ) -> Tuple[Set[str], Set[str]]:
        """Resolve this Selection against a ``MeshModel``.

        Returns the set of frame and area SAP2000 IDs that match all
        non-``None`` criteria in this selection.  This is used to determine
        which elements to record during pushover per-step analysis.

        Unlike :meth:`get_frame_ids` / :meth:`get_area_ids` which work on
        ``SAPModelData``, this method reads from a ``MeshModel`` (which has
        the same ``frame_assignments``, ``area_assignments``, ``sections``,
        ``materials``, and ``groups`` structures) and additionally supports
        the :attr:`elevation_range` and :attr:`story` filters.

        Args:
            mesh_model:
                The processed ``MeshModel`` to resolve against.
            storey_data:
                Output of :func:`~fea_toolkit.model.stories.identify_stories`,
                i.e. ``List[StoryLevel]``.  Required only when :attr:`story`
                is set; ignored otherwise.
            story_z_tolerance:
                Tolerance (in model length units) for matching an element's
                mid-height Z to a storey elevation from *storey_data*.
                Default 0.5 (half-metre in metre-based models).

        Returns:
            ``(record_frame_ids, record_area_ids)`` — two :class:`set` of
            SAP2000 element ID strings for frame and area elements matching
            this selection.

        Raises:
            ValueError:
                If :attr:`story` is set but ``storey_data`` is ``None``.

        Examples::

            # Select base-level columns for pushover recording
            sel = Selection(
                element_types=['Frame'],
                elevation_range=(0.0, 3.0),
            )
            frame_ids, area_ids = sel.resolve_to_mesh_sets(mesh_model)

            # Select shear walls on a specific storey
            stories = identify_stories(md, raw_tables)
            sel = Selection(
                element_types=['Area'],
                sections=['Shear Wall'],
                story=['Level 2'],
            )
            frame_ids, area_ids = sel.resolve_to_mesh_sets(
                mesh_model, storey_data=stories,
            )
        """
        # ── Build story name → elevation lookup ──
        story_elevations: Optional[Dict[str, float]] = None
        if self.story is not None:
            if storey_data is None:
                raise ValueError(
                    "story filter requires storey_data. "
                    "Call identify_stories(md, raw_tables) and pass the "
                    "result as storey_data to resolve_to_mesh_sets()."
                )
            story_elevations = {s.name: s.elevation for s in storey_data}

        frame_ids: Set[str] = set()
        area_ids: Set[str] = set()

        for eid, fe in mesh_model.frame_elements.items():
            if getattr(fe, 'inactive', False):
                continue
            if self._frame_matches(
                mesh_model, eid, story_elevations, story_z_tolerance,
            ):
                frame_ids.add(eid)

        for aid, ae in mesh_model.area_elements.items():
            if getattr(ae, 'inactive', False):
                continue
            if self._area_matches(
                mesh_model, aid, story_elevations, story_z_tolerance,
            ):
                area_ids.add(aid)

        return frame_ids, area_ids

    # ── MeshModel matching helpers ──────────────────────────────────────────

    def _match_z_filter(
        self,
        z_mid: Optional[float],
        story_elevations: Optional[Dict[str, float]] = None,
        story_z_tolerance: float = 0.5,
    ) -> bool:
        """Apply the elevation-range and storey filters to a resolved Z.

        The elevation and storey criteria are applied only when set
        (:meth:`_match_elevation` and :meth:`_match_story` each trivially
        pass when their filter is unset).  A ``None`` ``z_mid`` — element
        geometry unavailable — excludes the element from the selection.
        """
        if z_mid is None:
            return False
        if not self._match_elevation(z_mid):
            return False
        return self._match_story(z_mid, story_elevations, story_z_tolerance)

    def _match_elevation(self, z_mid: float) -> bool:
        """Check if a Z coordinate falls within *elevation_range*."""
        if self.elevation_range is None:
            return True
        z_min, z_max = self.elevation_range
        return z_min <= z_mid <= z_max

    def _match_story(
        self,
        z_mid: float,
        story_elevations: Optional[Dict[str, float]],
        story_z_tolerance: float,
    ) -> bool:
        """Check if a Z coordinate matches any named storey."""
        if self.story is None:
            return True
        if story_elevations is None:
            return False  # no elevation data to match against — exclude
        for story_name in self.story:
            elev = story_elevations.get(story_name)
            if elev is None:
                continue  # unknown story name — skip conservatively
            if abs(z_mid - elev) <= story_z_tolerance:
                return True
        return False

    @staticmethod
    def _get_frame_z_mid(
        model: Union["SAPModelData", "MeshModel"], eid: str
    ) -> Optional[float]:
        """Return the mid-height Z of a frame element, or None."""
        fe = model.frame_elements.get(eid)
        if fe is None:
            return None
        node_i = model.nodes.get(fe.node_i)
        node_j = model.nodes.get(fe.node_j)
        if node_i is None or node_j is None:
            return None
        return (node_i.z + node_j.z) / 2.0

    @staticmethod
    def _get_area_z_mid(
        model: Union["SAPModelData", "MeshModel"], aid: str
    ) -> Optional[float]:
        """Return the centroid Z of an area element, or None."""
        ae = model.area_elements.get(aid)
        if ae is None:
            return None
        z_vals = []
        for nid in ae.node_ids:
            nd = model.nodes.get(nid)
            if nd is not None:
                z_vals.append(nd.z)
        if not z_vals:
            return None
        return sum(z_vals) / len(z_vals)

    # ── Brace detection ──────────────────────────────────────────────────────

    @staticmethod
    def from_brace_sections(
        model: Union["SAPModelData", "MeshModel"]
    ) -> "Selection":
        """Create a ``Selection`` targeting brace‑type sections.

        Identifies frame elements whose section shape is one of the common
        brace profiles: ``Pipe``, ``Angle``, ``Double Angle``, ``Tee``,
        or ``Channel``.  This is the **section-type** signal used in
        element classification (see ``docs/element_classification.md``).

        The classification combines two independent signals:

        **1. Section type (this method)**
           Checks the Python dataclass type of each section in the model.
           Brace-shaped sections (Pipe, Angle, Double Angle, Tee, Channel)
           are candidates regardless of their orientation.

        **2. Geometry**
           A frame element whose chord angle from vertical exceeds ~20\u00b0
           is geometrically diagonal.  Handled by
           ``Preprocessor._classify_element_type()``.

        **Merge rule**
           A frame element is treated as a brace for pushover (Truss +
           Hysteretic) only if **both** conditions hold: its section is a
           brace shape AND it is geometrically diagonal.  This prevents
           horizontal pipes (e.g. handrails) or vertical tees from being
           misclassified as braces.

        Args:
            model: The parsed ``SAPModelData`` or ``MeshModel`` (both expose
                the same ``sections`` mapping).

        Returns:
            A ``Selection`` with ``element_types=['Frame']`` and
            ``sections`` populated from the model's brace-shape sections.
            Returns an empty Selection if no brace-shaped sections exist.
        """
        from .sap_data import (
            PipeSection, AngleSection, DoubleAngleSection,
            TeeSection, ChannelSection,
        )
        brace_shape_types = (
            PipeSection, AngleSection, DoubleAngleSection,
            TeeSection, ChannelSection,
        )
        brace_sec_names = [
            name for name, sec in model.sections.items()
            if isinstance(sec, brace_shape_types)
        ]
        if not brace_sec_names:
            # Return an empty selection — no braces to find
            return Selection(
                element_types=[],
                sections=[],
            )
        return Selection(
            element_types=['Frame'],
            sections=brace_sec_names,
        )