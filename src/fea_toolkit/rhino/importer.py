"""Rhino import — lightweight Extrusion version using ``rg``.

Creates true lightweight ``Extrusion`` objects (via
``rg.Extrusion.Create()``) instead of Brep polysurfaces.

Usage
-----
Inside Rhino::

    import sys
    sys.path.append(r'/path/to/fea_toolkit/src')

    from fea_toolkit.io.s2k_parser import SAP2000Parser
    from fea_toolkit.rhino.importer import RhinoImporter

    parser = SAP2000Parser('/path/to/model.s2k')
    parser.parse()
    md = parser.get_model_data()

    importer = RhinoImporter(md)
    report = importer.run()
    print(report)
"""

import copy
import typing as t

from ..model.sap_data import SAPModelData
from .colors import RESTRAINT_COLORS
from .geometry import (
    create_frame_extrusions,
    create_frame_lines,
    create_joint_points,
    create_shell_breps,
    create_shell_extrusions,
)
from .groups import create_sap_groups, create_selection_groups
from .layers import (
    create_frame_layers,
    create_joints_layer,
    create_root_layer,
    create_shell_layers,
)

__all__ = ["RhinoImporter"]


class RhinoImporter:
    """Export model stages into Rhino using lightweight Extrusions.

    Accepts any source supported by
    :func:`fea_toolkit.model.source_resolver.resolve_model_source`:
    ``SAPModelData``, ``MeshModel``, ``AnalysisBuilder``, or a stage-file
    path/dict (``.h5`` / ``.npz``).

    Args:
        model_data: A ``SAPModelData``, ``MeshModel``, ``AnalysisBuilder``,
            a stage-file path, or a stage-file dict.
        stage: Pipeline stage label (``sap`` / ``mesh``).  ``None`` →
            inferred from the source type.
    """

    def __init__(self, model_data, stage: t.Optional[str] = None):
        from ..model.source_resolver import resolve_model_source

        self.stage = stage
        self.md = resolve_model_source(model_data, stage=stage)
        self.stage = self.md.stage
        # Keep the original object when it is a raw SAPModelData so the
        # legacy ``create_meshed`` path can still run.
        self._raw_sap_model = model_data if isinstance(model_data, SAPModelData) else None
        self._ensure_rhino()

    @classmethod
    def from_mesh_model(cls, mesh_model):
        """Create a RhinoImporter from a :class:`~fea_toolkit.model.mesh_model.MeshModel`.

        The MeshModel's prepared topology (split frames, meshed shells,
        subdivided areas) is used directly — no need to build an
        ``AnalysisBuilder`` first.

        Usage::

            from fea_toolkit.model.mesh_model import MeshModel
            from fea_toolkit.rhino import RhinoImporter

            mesh = MeshModel(...)  # or from Preprocessor.run()
            importer = RhinoImporter.from_mesh_model(mesh)
            importer.run()
        """
        return cls(mesh_model)

    @staticmethod
    def _ensure_rhino():
        try:
            import scriptcontext as sc  # noqa: F401
        except ImportError:
            raise RuntimeError(
                "RhinoImporter requires Rhino 8. The Rhino API is not available in standard Python."
            ) from None

    def run(
        self,
        create_centreline: bool = True,
        create_extrusions: bool = True,
        color_code_joints: bool = True,
        create_groups: bool = True,
        create_meshed: bool = False,
        verbose: bool = True,
    ) -> dict[str, t.Any]:
        """Execute the full import sequence.

        Args:
            create_centreline: Points / lines / planar Breps.
            create_extrusions: Lightweight ``Extrusion`` solids.
            color_code_joints: Colour joints by restraint type.
            create_groups: Rhino groups from SAP groups.
            create_meshed: If True, also import meshed geometry (areas
                sub‑divided, frames split at joints) under a
                ``SAP2000/Meshed`` layer tree.
            verbose: Print progress.

        Returns:
            Dict with counts per geometry type.
        """
        results: dict[str, t.Any] = {
            "joints": 0,
            "frame_centrelines": 0,
            "shell_centrelines": 0,
            "frame_extrusions": 0,
            "shell_extrusions": 0,
            "sap_groups": 0,
            "meshed_frame_centrelines": 0,
            "meshed_shell_centrelines": 0,
            "meshed_frame_extrusions": 0,
            "meshed_shell_extrusions": 0,
        }

        # 1. Layer tree
        if verbose:
            print("Creating layer structure...")
        root_idx = create_root_layer()
        joint_layer = create_joints_layer(root_idx)

        frame_section_props: dict[str, dict] = {}
        shell_section_props: dict[str, dict] = {}
        for sname, sec in self.md.sections.items():
            props = {"Material": sec.material, "Shape": sec.shape}
            if hasattr(sec, "thickness"):
                props["Thickness"] = str(getattr(sec, "thickness", 0))
                shell_section_props[sname] = props
            else:
                frame_section_props[sname] = props

        frame_layers = create_frame_layers(root_idx, frame_section_props)
        shell_layers = create_shell_layers(root_idx, shell_section_props)

        joint_obj_ids: list[str] = []
        frame_obj_ids: list[str] = []
        shell_obj_ids: list[str] = []

        # 2. Joints
        if verbose:
            print("Creating joint points...")
        n_joints, joint_obj_ids = create_joint_points(self.md, joint_layer, stage=self.stage)
        results["joints"] = n_joints

        # 3. Centreline
        if create_centreline:
            if self.md.frame_elements:
                if verbose:
                    print("Creating frame centreline lines...")
                results["frame_centrelines"] = create_frame_lines(
                    self.md, frame_layers.centreline, stage=self.stage
                )
            if self.md.area_elements:
                if verbose:
                    print("Creating shell centreline Breps...")
                results["shell_centrelines"] = create_shell_breps(
                    self.md, shell_layers.centreline, stage=self.stage
                )

        # 4. Extrusions (lightweight Extrusion objects)
        if create_extrusions:
            if self.md.frame_elements:
                if verbose:
                    print("Creating frame extrusion solids...")
                results["frame_extrusions"] = create_frame_extrusions(
                    self.md, frame_layers.extrusion
                )
            if self.md.area_elements:
                if verbose:
                    print("Creating shell extrusion solids...")
                results["shell_extrusions"] = create_shell_extrusions(
                    self.md, shell_layers.extrusion
                )

        # 5. Meshed geometry (areas sub‑divided, frames split)
        # NOTE: the Preprocessor is the only sanctioned topology mutator
        # (§3.1/.clinerules).  The legacy in-importer meshing below is kept
        # only for raw ``SAPModelData`` inputs; stage-file / MeshModel
        # sources are imported directly.
        if create_meshed:
            if self._raw_sap_model is None:
                if verbose:
                    print(
                        "  create_meshed ignored: the source is already a MeshModel / "
                        "stage file. Import that stage directly (e.g. stage='mesh')."
                    )
            else:
                if verbose:
                    print("Pre-processing meshed model...")

                try:
                    from ..model.geometry import (
                        mesh_area_elements,
                        split_areas_at_frame_edges,
                        split_elements,
                    )

                    md_mesh = copy.deepcopy(self._raw_sap_model)

                    max_tag = 0
                    for nd in md_mesh.nodes.values():
                        max_tag = max(max_tag, nd.node_tag)
                    next_tag = max_tag + 1

                    dist_loads = getattr(md_mesh, "frame_dist_loads", [])
                    frame_auto_mesh = getattr(md_mesh, "frame_auto_mesh", {})
                    area_mesh = getattr(md_mesh, "area_mesh", {})

                    md_mesh.area_elements, md_mesh.area_assignments, md_mesh.nodes, next_tag = (
                        mesh_area_elements(
                            md_mesh.area_elements,
                            md_mesh.area_assignments,
                            md_mesh.nodes,
                            area_mesh,
                            next_tag=next_tag,
                        )
                    )

                    md_mesh.frame_elements, md_mesh.frame_assignments, _ = split_elements(
                        md_mesh.nodes,
                        md_mesh.frame_elements,
                        md_mesh.frame_assignments,
                        dist_loads,
                        frame_auto_mesh,
                    )

                    # Remap frame_end_offsets to split children
                    if hasattr(md_mesh, "frame_end_offsets") and md_mesh.frame_end_offsets:
                        new_offsets = {}
                        for eid, elem in md_mesh.frame_elements.items():
                            if getattr(elem, "inactive", False):
                                continue
                            parent = getattr(elem, "parent_id", None)
                            if parent and parent in md_mesh.frame_end_offsets:
                                parent_elem = md_mesh.frame_elements.get(parent)
                                if parent_elem and getattr(parent_elem, "child_ids", None):
                                    children = parent_elem.child_ids
                                    orig = md_mesh.frame_end_offsets[parent]
                                    if eid == children[0] or eid == children[-1]:
                                        new_offsets[eid] = orig
                            elif eid in md_mesh.frame_end_offsets:
                                new_offsets[eid] = md_mesh.frame_end_offsets[eid]
                        md_mesh.frame_end_offsets = new_offsets

                    md_mesh.area_elements, md_mesh.area_assignments, md_mesh.nodes, next_tag = (
                        split_areas_at_frame_edges(
                            md_mesh.area_elements,
                            md_mesh.area_assignments,
                            md_mesh.nodes,
                            md_mesh.frame_elements,
                            next_tag=next_tag,
                        )
                    )

                    if verbose:
                        print(
                            f"  Meshed: {len(md_mesh.area_elements)} shells, "
                            f"{len(md_mesh.frame_elements)} frames"
                        )

                except Exception as exc:
                    if verbose:
                        print(f"  Meshing skipped ({exc})")
                    md_mesh = None

            if md_mesh is not None:
                meshed_root = create_root_layer(name="Meshed", parent=root_idx)
                meshed_frame_layers = create_frame_layers(
                    meshed_root, frame_section_props, prefix="Meshed/"
                )
                meshed_shell_layers = create_shell_layers(
                    meshed_root, shell_section_props, prefix="Meshed/"
                )

                if create_centreline:
                    if md_mesh.frame_elements:
                        if verbose:
                            print("Creating meshed frame centreline lines...")
                        results["meshed_frame_centrelines"] = create_frame_lines(
                            md_mesh, meshed_frame_layers.centreline
                        )
                    if md_mesh.area_elements:
                        if verbose:
                            print("Creating meshed shell centreline Breps...")
                        results["meshed_shell_centrelines"] = create_shell_breps(
                            md_mesh, meshed_shell_layers.centreline
                        )

                if create_extrusions:
                    if md_mesh.frame_elements:
                        if verbose:
                            print("Creating meshed frame extrusions...")
                        results["meshed_frame_extrusions"] = create_frame_extrusions(
                            md_mesh, meshed_frame_layers.extrusion
                        )
                    if md_mesh.area_elements:
                        if verbose:
                            print("Creating meshed shell extrusions...")
                        results["meshed_shell_extrusions"] = create_shell_extrusions(
                            md_mesh, meshed_shell_layers.extrusion
                        )

        # 6. Groups
        if create_groups:
            if verbose:
                print("Creating selection groups...")
            create_selection_groups()
            if self.md.groups:
                if verbose:
                    print("Creating SAP2000 groups...")
                results["sap_groups"] = create_sap_groups(
                    self.md, joint_obj_ids, frame_obj_ids, shell_obj_ids
                )

        # 7. Joint colour coding
        if color_code_joints and joint_obj_ids:
            if verbose:
                print("Color-coding joints by restraint type...")
            self._color_code_joints(joint_obj_ids)

        if verbose:
            print("\nImport complete.")
            for key, val in results.items():
                print(f"  {key}: {val}")

        return results

    def _color_code_joints(self, joint_object_ids: list[str]) -> None:
        """Colour joints by restraint type."""
        import Rhino.DocObjects as rd
        import scriptcontext as sc

        doc = sc.doc

        for obj_id in joint_object_ids:
            try:
                obj = doc.Objects.Find(obj_id)
                if obj is None:
                    continue
                attrs = obj.Attributes

                constraint = attrs.GetUserString("SAP_Constraint")
                restraints = attrs.GetUserString("SAP_Restraints")

                color_key = "free"
                if constraint and constraint != "":
                    color_key = "constrained"
                elif restraints:
                    rlist = [r.strip() for r in restraints.split(",")]
                    if len(rlist) >= 6:
                        color_key = "fully_fixed"
                    elif all(dof in rlist for dof in ["U1", "U2", "U3"]):
                        color_key = "pinned"
                    else:
                        color_key = "roller"

                joint_color = RESTRAINT_COLORS.get(color_key)
                if joint_color is None:
                    continue
                attrs.ObjectColor = joint_color
                attrs.ColorSource = rd.ObjectColorSource.ColorFromObject
                obj.CommitChanges()
            except Exception:
                continue
