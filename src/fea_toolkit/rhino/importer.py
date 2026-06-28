"""
Main ``RhinoImporter`` — orchestrates the export of ``SAPModelData``
into the Rhino document.

The importer creates two geometry representations:

* **Centreline** — points for joints, lines for frames, planar Breps for shells.
* **Extrusion** — lightweight ``Extrusion`` solids using section profiles
  (frames) or thickness offset (shells).

Both are organised in a hierarchical layer tree under ``SAP2000``:

.. code::

    SAP2000
    ├── Joints
    ├── Frames
    │   ├── Centreline / {Section}
    │   └── Extrusion / {Section}
    └── Shells
        ├── Centreline / {Section}
        └── Extrusion / {Section}

Usage
-----

Inside Rhino (IronPython)::

    import sys
    sys.path.append(r'/path/to/fea_toolkit/src')
    from fea_toolkit.io.s2k_parser import SAP2000Parser
    from fea_toolkit.rhino.importer import RhinoImporter

    parser = SAP2000Parser.from_json('model.json')
    md = parser.get_model_data()

    importer = RhinoImporter(md)
    report = importer.run(
        create_centreline=True,
        create_extrusions=True,
        color_code_joints=True,
        create_groups=True,
    )
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
from .geometry import (
    create_joint_points, create_frame_lines, create_shell_breps,
    create_frame_extrusions, create_shell_extrusions,
)
from .groups import create_sap_groups, create_selection_groups
from ..io.s2k_parser import SAP2000Parser


# ── Re-export for convenience ────────────────────────────────────────────
__all__ = ["RhinoImporter"]


class RhinoImporter:
    """Export a parsed SAP2000 model into the active Rhino document.

    Args:
        model_data: A ``SAPModelData`` instance (from ``SAP2000Parser``).
    """

    def __init__(self, model_data: SAPModelData):
        self.md = model_data
        self._ensure_rhino()

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------
    @staticmethod
    def _ensure_rhino():
        """Check that we are running inside Rhino."""
        try:
            import scriptcontext as sc  # noqa: F401
        except ImportError:
            raise RuntimeError(
                "RhinoImporter requires Rhino 8 (IronPython). "
                "The Rhino API is not available in standard Python."
            )

    # -----------------------------------------------------------------
    # Main entry point
    # -----------------------------------------------------------------
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
            create_centreline: Create points / lines / planar Breps.
            create_extrusions: Create 3‑D extrusion solids.
            color_code_joints: Colour joint points by restraint type.
            create_groups: Create Rhino groups from SAP2000 groups
                plus selection groups (All Frames / All Shells / All Joints).
            verbose: Print progress to the Rhino command line.

        Returns:
            Dict with keys ``joints``, ``frame_centrelines``,
            ``shell_centrelines``, ``frame_extrusions``,
            ``shell_extrusions``, ``sap_groups``.
        """
        results: t.Dict[str, t.Any] = {
            "joints": 0,
            "frame_centrelines": 0,
            "shell_centrelines": 0,
            "frame_extrusions": 0,
            "shell_extrusions": 0,
            "sap_groups": 0,
        }

        # ── 1. Create layer tree ──────────────────────────────────────
        if verbose:
            print("Creating layer structure...")

        root_idx = create_root_layer()
        joint_layer = create_joints_layer(root_idx)

        # Collect section names with their raw props for colour info.
        # We extract from the model's section dict.
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

        # Collect object IDs for groups later
        joint_obj_ids: t.List[str] = []
        frame_obj_ids: t.List[str] = []
        shell_obj_ids: t.List[str] = []

        # ── 2. Joints (always created) ───────────────────────────────
        if verbose:
            print("Creating joint points...")
        n_joints, joint_obj_ids = create_joint_points(self.md, joint_layer)
        results["joints"] = n_joints

        # ── 3. Centreline geometry ───────────────────────────────────
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
                # Capture shell obj IDs during centreline creation
                # (create_shell_breps returns count, we need IDs)
                results["shell_centrelines"] = create_shell_breps(
                    self.md, shell_layers.centreline
                )

        # ── 4. Extrusion geometry ────────────────────────────────────
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

        # ── 5. Groups ────────────────────────────────────────────────
        if create_groups:
            # Selection groups (SAP_All_Frames / Shells / Joints) always
            # created when there are objects in the document.
            if verbose:
                print("Creating selection groups...")
            create_selection_groups()

            # SAP2000 groups only if the model has definitions
            if self.md.groups:
                if verbose:
                    print("Creating SAP2000 groups...")
                results["sap_groups"] = create_sap_groups(
                    self.md, joint_obj_ids, frame_obj_ids, shell_obj_ids
                )

        # ── 6. Color-code joints ────────────────────────────────────
        if color_code_joints and joint_obj_ids:
            if verbose:
                print("Color-coding joints by restraint type...")
            self._color_code_joints(joint_obj_ids)

        # ── 7. Finalise ──────────────────────────────────────────────
        if verbose:
            print("\nImport complete.")
            for key, val in results.items():
                print("  {}: {}".format(key, val))

        return results

    # -----------------------------------------------------------------
    # Joint colour coding
    # -----------------------------------------------------------------
    def _color_code_joints(self, joint_object_ids: t.List[str]) -> None:
        """Colour joint points by restraint type.

        Colours are defined in ``RESTRAINT_COLORS``:
            fully_fixed → Red
            pinned      → Blue
            roller      → Green
            free        → LightGray
            constrained → Purple
        """
        import scriptcontext as sc
        import Rhino.DocObjects as rd

        doc = sc.doc

        for obj_id in joint_object_ids:
            try:
                obj = doc.Objects.Find(obj_id)
                if obj is None:
                    continue
                attrs = obj.Attributes

                # Skip objects that belong to a SAP group (groups handle colour)
                if attrs.GroupCount > 0:
                    continue

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
