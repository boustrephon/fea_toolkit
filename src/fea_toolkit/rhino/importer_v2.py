"""
``RhinoImporterV2`` — lightweight Extrusion version using ``rg``.

Same public API as ``RhinoImporter`` but uses ``geometry_v2`` which
creates true lightweight ``Extrusion`` objects (via
``rg.Extrusion.Create()``) instead of Brep polysurfaces.

Usage
-----
Inside Rhino::

    import sys
    sys.path.append(r'/path/to/fea_toolkit/src')

    from fea_toolkit.io.s2k_parser import SAP2000Parser
    from fea_toolkit.rhino.importer_v2 import RhinoImporterV2

    parser = SAP2000Parser('/path/to/model.s2k')
    parser.parse()
    md = parser.get_model_data()

    importer = RhinoImporterV2(md)
    report = importer.run()
    print(report)
"""

import typing as t

from ..model.sap_data import SAPModelData
from .colors import RESTRAINT_COLORS, get_sap2000_color
from .layers import (
    create_root_layer, create_joints_layer,
    create_frame_layers, create_shell_layers,
    FrameLayerSet, ShellLayerSet,
)
from .geometry_v2 import (
    create_joint_points, create_frame_lines, create_shell_breps,
    create_frame_extrusions, create_shell_extrusions,
)
from .groups import create_sap_groups, create_selection_groups


__all__ = ["RhinoImporterV2"]


class RhinoImporterV2:
    """Export ``SAPModelData`` into Rhino using lightweight Extrusions.

    Args:
        model_data: A ``SAPModelData`` instance.
    """

    def __init__(self, model_data: SAPModelData):
        self.md = model_data
        self._ensure_rhino()

    @staticmethod
    def _ensure_rhino():
        try:
            import scriptcontext as sc  # noqa: F401
        except ImportError:
            raise RuntimeError(
                "RhinoImporterV2 requires Rhino 8. "
                "The Rhino API is not available in standard Python."
            )

    def run(
        self,
        create_centreline: bool = True,
        create_extrusions: bool = True,
        color_code_joints: bool = True,
        create_groups: bool = True,
        verbose: bool = True,
    ) -> t.Dict[str, t.Any]:
        """Execute the full import sequence.

        Args:
            create_centreline: Points / lines / planar Breps.
            create_extrusions: Lightweight ``Extrusion`` solids.
            color_code_joints: Colour joints by restraint type.
            create_groups: Rhino groups from SAP groups.
            verbose: Print progress.

        Returns:
            Dict with counts per geometry type.
        """
        results: t.Dict[str, t.Any] = {
            "joints": 0,
            "frame_centrelines": 0,
            "shell_centrelines": 0,
            "frame_extrusions": 0,
            "shell_extrusions": 0,
            "sap_groups": 0,
        }

        # 1. Layer tree
        if verbose:
            print("Creating layer structure...")
        root_idx = create_root_layer()
        joint_layer = create_joints_layer(root_idx)

        frame_section_props: t.Dict[str, dict] = {}
        shell_section_props: t.Dict[str, dict] = {}
        for sname, sec in self.md.sections.items():
            props = {"Material": sec.material, "Shape": sec.shape}
            if hasattr(sec, "thickness"):
                props["Thickness"] = getattr(sec, "thickness", 0)
                shell_section_props[sname] = props
            else:
                frame_section_props[sname] = props

        frame_layers = create_frame_layers(root_idx, frame_section_props)
        shell_layers = create_shell_layers(root_idx, shell_section_props)

        joint_obj_ids: t.List[str] = []
        frame_obj_ids: t.List[str] = []
        shell_obj_ids: t.List[str] = []

        # 2. Joints
        if verbose:
            print("Creating joint points...")
        n_joints, joint_obj_ids = create_joint_points(self.md, joint_layer)
        results["joints"] = n_joints

        # 3. Centreline
        if create_centreline:
            if self.md.frame_elements:
                if verbose:
                    print("Creating frame centreline lines...")
                results["frame_centrelines"] = create_frame_lines(
                    self.md, frame_layers.centreline
                )
            if self.md.area_elements:
                if verbose:
                    print("Creating shell centreline Breps...")
                results["shell_centrelines"] = create_shell_breps(
                    self.md, shell_layers.centreline
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

        # 5. Groups
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

        # 6. Joint colour coding
        if color_code_joints and joint_obj_ids:
            if verbose:
                print("Color-coding joints by restraint type...")
            self._color_code_joints(joint_obj_ids)

        if verbose:
            print("\nImport complete.")
            for key, val in results.items():
                print("  {}: {}".format(key, val))

        return results

    def _color_code_joints(self, joint_object_ids: t.List[str]) -> None:
        """Colour joints by restraint type."""
        import scriptcontext as sc
        import Rhino.DocObjects as rd
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
