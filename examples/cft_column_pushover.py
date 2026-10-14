#!/usr/bin/env python
"""Concrete-filled steel pipe (CFT / CHS) cantilever — pushover example.

Builds a programmatic concrete-filled circular pipe column (CHS) and runs a
displacement-controlled pushover through the two-stage
``Preprocessor -> AnalysisBuilder`` pipeline, then plots the capacity curve.

The model is a single fixed-base cantilever column:

* steel tube  Q355  (Fy = 355 MPa)
* concrete fill  C40  (fc = 40 MPa) — modelled **unconfined**; the CFT
  confinement model is still deferred (see ``docs/cft_confinement_plan.md``)
* axial pre-load applied as a top-node joint load (DEAD pattern), with the
  seismic mass derived from that load (``MassSource`` loads=True)
* the column bends in the X–Z plane, so the top node's out-of-plane DOFs
  (UY, RX, RZ) are restrained — a planar 3D model, per the toolkit convention

This exercises the role-based fiber contract end to end:
``ConcreteFilledPipeSection.fiber_roles()`` -> ``["steel", "concrete", "rebar"]``
with the steel pipe as an annular ``circ`` patch and the concrete core as a
solid ``circ`` patch, assembled into an OpenSees ``Fiber`` section.

Output (gitignored):

* ``examples/output/cft_column_pushover.png`` — base shear vs. displacement.

Usage::

    python examples/cft_column_pushover.py
    python examples/cft_column_pushover.py --od 0.4 --t 0.012 --height 4.0
    python examples/cft_column_pushover.py --lateral uniform   # uniform | point | mode1
    python examples/cft_column_pushover.py --no-plot

See also ``docs/pushover_analysis.md`` and ``docs/cft_confinement_plan.md``.
"""

import argparse
import sys
from pathlib import Path

# Make `fea_toolkit` importable when running from anywhere.
sys.path.insert(0, str(Path(__file__).parent.parent))  # project root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import openseespy.opensees as ops

from fea_toolkit import __version__, ops_version
from fea_toolkit.model.sap_data import (
    ConcreteFilledPipeSection,
    FrameElement,
    JointLoad,
    LoadPattern,
    MassSource,
    Material,
    Node,
    Restraint,
    SAPModelData,
)
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
from fea_toolkit.opensees.preprocessor import preprocess_model
from fea_toolkit.plotting import plot_pushover_curve


# ── Scenario data (kN-m model units; stresses authored in kPa) ─────────────
def make_cfp_cantilever(
    od: float = 0.4,
    t: float = 0.012,
    height: float = 4.0,
    axial_load_kN: float = 1000.0,
) -> SAPModelData:
    """Build a fixed-base CHS concrete-filled pipe cantilever (kN-m units).

    Args:
        od: Outer diameter of the pipe (m).
        t: Pipe wall thickness (m).
        height: Column height (m).
        axial_load_kN: Axial pre-load applied at the top node (kN, downward).

    Returns:
        SAPModelData ready for ``preprocess_model`` + ``AnalysisBuilder``.
    """
    units = {"F": "KN", "L": "m", "T": "C"}

    nodes = {
        "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
        "2": Node(node_id="2", node_tag=2, x=0.0, y=0.0, z=height),
    }
    restraints = {
        "1": Restraint([1, 1, 1, 1, 1, 1]),  # base: fully fixed
        # Top node: in-plane DOFs (UX, UZ, RY) free; out-of-plane (UY, RX, RZ)
        # restrained so the column bends in the X-Z plane.  A single 3D
        # cantilever otherwise has a free torsion/out-of-plane mode (the fiber
        # section carries GJ=0), which makes the stiffness matrix singular.
        "2": Restraint([0, 1, 0, 1, 0, 1]),
    }

    materials = {
        "Q355": Material(
            name="Q355",
            type="Steel",
            E_mod=206e6,  # kPa
            G_mod=79.23e6,
            nu=0.3,
            unit_weight=77.0,  # kN/m³
            Fy=355e3,  # kPa
        ),
        "C40": Material(
            name="C40",
            type="Concrete",
            E_mod=32.5e6,  # kPa (nominal short-term)
            nu=0.2,
            unit_weight=24.0,  # kN/m³
            Fc=40e3,  # kPa (40 MPa, unconfined)
        ),
    }

    sections = {
        "CFP": ConcreteFilledPipeSection(
            name="CFP",
            shape="Filled Steel Pipe",
            material="Q355",
            od=od,
            t=t,
            fill_material="C40",
        ),
    }

    frame_elements = {"1": FrameElement(elem_id="1", elem_tag=1, node_i="1", node_j="2")}
    frame_assignments = {"1": "CFP"}

    load_patterns = {
        "DEAD": LoadPattern(name="DEAD", pattern_type="Dead", self_weight_factor=0.0),
    }
    # Axial pre-load at the top node (downward), used for gravity and to seed
    # the seismic mass (MassSource loads=True maps downward loads to +mass).
    joint_loads = [JointLoad(pattern="DEAD", node_id="2", fz=-axial_load_kN)]

    mass_sources = {
        "MSSSRC1": MassSource(
            name="MSSSRC1",
            elements=True,
            masses=False,
            loads=True,
            load_pattern={"DEAD": 1.0},
        ),
    }

    md = SAPModelData(
        nodes=nodes,
        restraints=restraints,
        materials=materials,
        sections=sections,
        frame_elements=frame_elements,
        area_elements={},
        frame_assignments=frame_assignments,
        area_assignments={},
        groups={},
        frame_auto_mesh={},
        load_patterns=load_patterns,
        joint_loads=joint_loads,
        mass_sources=mass_sources,
        units=units,
    )
    # Fill derived properties (e.g. G_mod for the concrete) from SI defaults.
    md.apply_material_defaults()
    return md


def run_pushover(
    md: SAPModelData,
    max_disp: float,
    num_steps: int,
    lateral: str,
) -> dict:
    """Run the two-stage gravity + lateral pushover on a prepared model."""
    # Topology preprocessing stays elastic; the builder enables fiber sections
    # and runs the pushover rebuild on a nonlinear (dispBeamColumn) element.
    # forceBeamColumn is deliberately not used: the CFT fiber section carries
    # zero torsional rigidity (GJ = 0), so forceBeamColumn cannot invert its
    # flexibility matrix and fails the gravity stage.
    cfg = {"element_type": "elasticBeamColumn", "split_elements": True, "verbose": False}
    mm = preprocess_model(md, cfg)
    builder = AnalysisBuilder(
        mm,
        {
            "element_type": "dispBeamColumn",
            "create_fiber_sections": True,
            "fiber_element_type": "dispBeamColumn",
            "verbose": False,
        },
    )
    builder.build_domain()
    return builder.run_pushover_analysis(
        gravity_patterns={"DEAD": 1.0},
        lateral_load_type=lateral,
        lateral_direction="X",
        control_node_tag=None,  # auto-select the top (free) node
        max_disp=max_disp,
        num_steps=num_steps,
        print_progress=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Concrete-filled steel pipe (CHS) cantilever pushover.",
    )
    parser.add_argument("--od", type=float, default=0.4, help="Pipe outer diameter (m).")
    parser.add_argument("--t", type=float, default=0.012, help="Pipe wall thickness (m).")
    parser.add_argument("--height", type=float, default=4.0, help="Column height (m).")
    parser.add_argument("--axial", type=float, default=1000.0, help="Axial pre-load (kN).")
    parser.add_argument(
        "--lateral",
        choices=("uniform", "point", "mode1"),
        default="uniform",
        help="Lateral load shape for the pushover.",
    )
    parser.add_argument(
        "--max-drift",
        type=float,
        default=0.05,
        help="Target drift at the control node (fraction of height).",
    )
    parser.add_argument("--num-steps", type=int, default=100, help="Number of push steps.")
    parser.add_argument("--no-plot", action="store_true", help="Skip the capacity-curve PNG.")
    args = parser.parse_args()

    print(f"FEA Toolkit Version: {__version__}")
    print(f"OpenSees Version: {ops_version()}\n")

    md = make_cfp_cantilever(
        od=args.od,
        t=args.t,
        height=args.height,
        axial_load_kN=args.axial,
    )
    sec = md.sections["CFP"]
    print(f"Column: {args.height:.1f} m cantilever")
    print(f"Section: CHS OD={sec.od:.3f} m  t={sec.t:.4f} m")
    print(f"Fill: {sec.fill_material} (unconfined)  |  Tube: {sec.material}")
    print(f"Axial pre-load: {args.axial:.0f} kN\n")

    max_disp = args.max_drift * args.height
    results = run_pushover(
        md,
        max_disp=max_disp,
        num_steps=args.num_steps,
        lateral=args.lateral,
    )

    # ── Summary ──
    disp = results["control_disp"]
    shear = [abs(v) for v in results["base_shear"]]
    print("\n── Pushover summary ──")
    print(f"  Lateral load type: {results['lateral_load_type']}")
    print(f"  Control node: {results['control_node']} (DOF {results['dof']})")
    print(f"  Peak base shear: {max(shear):.1f} kN")
    print(f"  Max displacement: {max(disp):.4f} m ({max(disp) / args.height * 100:.1f}% drift)")
    print(f"  Status: {results['status']}")

    if not args.no_plot:
        fig = plot_pushover_curve(
            results,
            title=f"CFT (CHS) cantilever pushover — OD {sec.od:.3f} m, t {sec.t:.4f} m",
        )
        if fig is not None:
            out = Path(__file__).parent / "output"
            out.mkdir(exist_ok=True)
            fig.savefig(out / "cft_column_pushover.png", dpi=300)
            print(f"\n  Saved → {out / 'cft_column_pushover.png'}  (300 dpi)")

    ops.wipe()


if __name__ == "__main__":
    main()
