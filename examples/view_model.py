#!/usr/bin/env python
"""View a structural model or a saved results archive.

Given a SAP2000 ``.s2k`` file this script parses the model, runs the
requested analysis, and displays the matching visualisation.  Given a
saved ``.npz`` results archive it displays the archived data directly,
without re-running any analysis.

Usage::

    # Show the bare model geometry
    python examples/view_model.py /path/to/model.s2k --result mesh

    # Static analysis - deformed shape, then a force diagram
    python examples/view_model.py /path/to/model.s2k --result static --quantity Mz

    # Modal / response-spectrum / pushover / interactive
    python examples/view_model.py /path/to/model.s2k --result modal --mode 0
    python examples/view_model.py /path/to/model.s2k --result rs
    python examples/view_model.py /path/to/model.s2k --result pushover
    python examples/view_model.py /path/to/model.s2k --result interactive

    # Display a previously saved results archive (no solver run)
    python examples/view_model.py /path/to/results.npz --result static

    # Built-in sample (no external file needed)
    python examples/view_model.py --sample --result static
"""

import argparse
import sys
from pathlib import Path

import numpy as np

# Allow running from any directory (project root + src on the path).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fea_toolkit import __version__, ops_version
from fea_toolkit.io.s2k_parser import SAP2000Parser
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
from fea_toolkit.opensees.preprocessor import preprocess_model
from fea_toolkit.plotting import (
    plot_deformed_displacement_3d,
    plot_force_diagram,
    plot_interactive_viewer,
    plot_mesh,
    plot_mode_animation,
    plot_pushover_curve,
)
from fea_toolkit.spectrum import ResponseSpectrum
from fea_toolkit.utils import g_from_units

RESULT_TYPES = ("mesh", "static", "modal", "rs", "pushover", "interactive")


def build_builder(md, element_type="elasticBeamColumn"):
    """Preprocess and build the OpenSees domain, returning the builder."""
    config = {"element_type": element_type, "split_elements": True, "verbose": False}
    mesh_model = preprocess_model(md, config)
    builder = AnalysisBuilder(mesh_model, {"element_type": element_type, "verbose": False})
    builder.build_domain()
    return builder


def show_static(builder, md, quantity, scale):
    """Static analysis: deformed shape, then a 3D force diagram."""
    avail = list(md.load_patterns.keys())
    combo = dict.fromkeys(avail, 1.0) if avail else None
    results = builder.run_static_analysis(extract_reactions=True, pattern_scales=combo)
    disp = results.get("nodal_displacements", {})
    if not disp:
        print("No displacements produced - showing the bare mesh instead.")
        plot_mesh(builder)
        return
    plot_deformed_displacement_3d(builder, disp, scale=scale, show_undeformed=True)
    elem_forces = builder.extract_static_element_forces()
    plot_force_diagram(builder, elem_forces, quantity=quantity, mode="flag", dimension="3d")


def show_modal(builder, num_modes, mode):
    """Modal analysis with an animated mode shape."""
    builder.compute_seismic_masses()
    modal = builder.run_modal_analysis(num_modes=num_modes, print_results=True)
    n = modal["num_modes"]
    if n == 0:
        sys.exit("Error: no modes converged.")
    shapes = builder.extract_mode_shapes(n)
    plot_mode_animation(
        builder,
        shapes,
        mode=min(mode, n - 1),
        periods=modal["periods"],
        animate=True,
    )


def show_rs(builder, md, num_modes, alpha_max, tg, damping, scale):
    """Response-spectrum analysis (GB 50011) with the CQC deformed shape."""
    builder.compute_seismic_masses()
    modal = builder.run_modal_analysis(num_modes=num_modes, print_results=True)
    n = modal["num_modes"]
    if n == 0:
        sys.exit("Error: no modes converged.")
    periods = modal["periods"]

    spec = ResponseSpectrum.from_gb50011(
        alpha_max,
        tg,
        damping,
        g=g_from_units(md.units),
        T_max=max(periods[:n]) * 1.1 + 0.1,
        n_pts=200,
    )

    def spectrum_func(T: float) -> float:
        return float(np.interp(T, spec.T, spec.Sa))

    try:
        builder.run_response_spectrum_analysis(
            num_modes=n,
            modal_periods=periods[:n],
            spectrum_periods=spec.T,
            spectrum_accels=spec.Sa,
            direction="X",
            damping_ratio=damping,
            print_results=True,
        )
        disp = builder.compute_rs_nodal_displacements(
            num_modes=n,
            modal_periods=periods[:n],
            eigenvalues=modal["eigenvalues"],
            spectrum_func=spectrum_func,
            direction="X",
            damping_ratio=damping,
        )
    except Exception as e:
        # Degenerate models (few DOFs, near-zero-stiffness modes) can make
        # the CQC combination overflow.  Fall back to a mode-shape view
        # rather than aborting.
        print(f"\nResponse-spectrum analysis failed ({e}).")
        print("Falling back to the mode-shape view - try a smaller --num-modes.")
        shapes = builder.extract_mode_shapes(n)
        plot_mode_animation(builder, shapes, mode=0, periods=periods, animate=True)
        return

    plot_deformed_displacement_3d(builder, disp, scale=scale, show_undeformed=True)


def show_pushover(builder, md):
    """Pushover analysis and capacity curve (needs a nonlinear-capable model)."""
    patterns = list(md.load_patterns.keys())
    gravity = {"DEAD": 1.0} if "DEAD" in patterns else (dict.fromkeys(patterns, 1.0) or {})

    try:
        results = builder.run_pushover_analysis(
            gravity_patterns=gravity,
            lateral_load_type="uniform",
            lateral_direction="X",
            max_disp=0.5,
            num_steps=50,
            print_progress=True,
        )
    except Exception as e:
        sys.exit(f"Pushover failed (model may not be nonlinear-capable): {e}")

    bs = [abs(v) for v in results["base_shear"]]
    cd = results["control_disp"]
    print(f"\nPeak base shear: {max(bs):.1f}   Max displacement: {max(cd):.4f}")

    fig = plot_pushover_curve(results)
    if fig is not None:
        import matplotlib.pyplot as plt

        plt.show()


def show_interactive(builder, md):
    """Interactive widget viewer (radio buttons, sliders, click-to-inspect)."""
    avail = list(md.load_patterns.keys())
    combo = dict.fromkeys(avail, 1.0) if avail else None
    results = builder.run_static_analysis(extract_reactions=True, pattern_scales=combo)
    forces = builder.extract_static_element_forces()
    plot_interactive_viewer(builder, {"All": forces}, {"All": results})


def show_npz(path, result_type, quantity, mode):
    """Display a saved .npz archive directly (no solver run)."""
    data = np.load(path, allow_pickle=True)
    if result_type == "static":
        plot_force_diagram(str(path), quantity=quantity)
    elif result_type == "modal":
        plot_mode_animation(data, None, mode=mode)
    else:
        # mesh / rs / pushover / interactive - just show the archived geometry.
        plot_mesh(data, collapse_to_parents=True)


def run_s2k(md, args):
    """Run the requested analysis on a parsed model and display the result."""
    print(f"Model units: {md.units}")
    print(
        f"Nodes: {len(md.nodes)}; frames: {len(md.frame_elements)}; areas: {len(md.area_elements)}"
    )
    print(f"Load patterns: {list(md.load_patterns.keys())}")

    if args.result == "mesh":
        plot_mesh(md, collapse_to_parents=True)
        return

    builder = build_builder(md, args.element_type)

    if args.result == "static":
        show_static(builder, md, args.quantity, args.scale)
    elif args.result == "modal":
        show_modal(builder, args.num_modes, args.mode)
    elif args.result == "rs":
        show_rs(builder, md, args.num_modes, args.alpha_max, args.tg, args.damping, args.scale)
    elif args.result == "pushover":
        show_pushover(builder, md)
    elif args.result == "interactive":
        show_interactive(builder, md)


def main():
    parser = argparse.ArgumentParser(description="View a model or a saved results archive.")
    parser.add_argument("model_file", nargs="?", help="Path to a .s2k model or .npz archive.")
    parser.add_argument(
        "-r",
        "--result",
        choices=RESULT_TYPES,
        default="mesh",
        help="Analysis/result type to visualise (default: mesh).",
    )
    parser.add_argument(
        "--sample",
        action="store_true",
        help="Use the built-in cantilever sample model.",
    )
    parser.add_argument("--quantity", default="Mz", help="Force quantity (static/NPZ).")
    parser.add_argument("--mode", type=int, default=0, help="Mode index (modal).")
    parser.add_argument("--num-modes", type=int, default=12, help="Number of modes.")
    parser.add_argument("--scale", type=float, default=50.0, help="Deformation magnification.")
    parser.add_argument(
        "--element-type",
        default="elasticBeamColumn",
        help="OpenSees element type (e.g. elasticBeamColumn, forceBeamColumn).",
    )
    parser.add_argument("--alpha-max", type=float, default=0.16, help="GB 50011 alpha_max (rs).")
    parser.add_argument("--tg", type=float, default=0.40, help="GB 50011 Tg (rs).")
    parser.add_argument("--damping", type=float, default=0.05, help="Damping ratio (rs).")
    args = parser.parse_args()

    print(f"FEA Toolkit {__version__} / OpenSees {ops_version()}")

    if args.sample:
        from examples.sample_model import make_sample_model

        run_s2k(make_sample_model(), args)
        return

    if not args.model_file:
        sys.exit("Provide a .s2k file path, a .npz archive, or use --sample.")

    path = Path(args.model_file)
    if not path.exists():
        sys.exit(f"Error: file not found - {path}")

    if path.suffix.lower() == ".npz":
        show_npz(path, args.result, args.quantity, args.mode)
        return

    print(f"Loading: {path}")
    parser_s2k = SAP2000Parser(path)
    parser_s2k.parse()
    md = parser_s2k.get_model_data()
    run_s2k(md, args)


if __name__ == "__main__":
    main()
