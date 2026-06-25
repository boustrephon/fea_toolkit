#!/usr/bin/env python
"""Static analysis with force diagrams from a SAP2000 model.

Parses the model, builds the OpenSees model, runs a load combination,
extracts element forces, and plots 2D/3D force diagrams.

Usage::

    # Use a specific model
    python examples/static_analysis.py /path/to/model.s2k

    # Use the built‑in cantilever sample (no external file needed)
    python examples/static_analysis.py --sample
"""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # project root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fea_toolkit import __version__, ops_version
from fea_toolkit.io.s2k_parser import SAP2000Parser
from fea_toolkit.opensees.builder import OpenSeesBuilder


def main():
    parser = argparse.ArgumentParser(
        description="Parse a SAP2000 model, run static analysis, and plot force diagrams.",
    )
    parser.add_argument(
        "s2k_file", nargs="?",
        help="Path to the SAP2000 text file (.s2k, .$2k). Omit when using --sample.",
    )
    parser.add_argument(
        "--sample", action="store_true",
        help="Use the built‑in cantilever sample model (no external file needed).",
    )
    args = parser.parse_args()

    # Determine input source
    if args.sample:
        from examples.sample_model import make_sample_model
        md = make_sample_model()
        source_name = "built‑in cantilever sample"
        print(f"FEA Toolkit Version: {__version__}")
        print(f"OpenSees Version: {ops_version()}")
        print(f"Using: {source_name}\n")
    elif args.s2k_file:
        s2k_path = Path(args.s2k_file)
        if not s2k_path.exists():
            sys.exit(f"Error: file not found — {s2k_path}")
        print(f"FEA Toolkit Version: {__version__}")
        print(f"OpenSees Version: {ops_version()}")
        print(f"Loading: {s2k_path}\n")
        parser_s2k = SAP2000Parser(s2k_path)
        parser_s2k.parse()
        md = parser_s2k.get_model_data()
    else:
        sys.exit("Provide a .s2k file path or use --sample.")

    # Output directory for plots
    out = Path(__file__).parent / "output"
    out.mkdir(exist_ok=True)

    print(f"Model units: {md.units}")
    print(f"Nodes: {len(md.nodes)}")
    print(f"Frame elements: {len(md.frame_elements)}")
    print(f"Area elements: {len(md.area_elements)}")
    print(f"Sections: {len(md.sections)}")
    print(f"Load patterns: {list(md.load_patterns.keys())}")
    print(f"Frame distributed loads: {len(md.frame_dist_loads)}")
    print(f"Area uniform loads: {len(md.area_uniform_loads)}")
    print(f"Mass sources: {len(md.mass_sources)}")
    print(f"Frame gravity loads: {len(md.frame_gravity_loads)}")
    print(f"Area gravity loads: {len(md.area_gravity_loads)}")

    # Build
    builder = OpenSeesBuilder(md, {
        'element_type': 'elasticBeamColumn',
        'split_elements': True,
        'verbose': False,
    })
    builder.build()

    # Static analysis: combine available load patterns
    avail = list(md.load_patterns.keys())
    combo = {name: 1.0 for name in avail}
    print(f"\n── Static analysis: {combo} ──")
    results = builder.run_static_analysis(
        extract_reactions=True,
        pattern_scales=combo,
    )

    # Displacements
    disp = results.get('nodal_displacements', {})
    if disp:
        tags = sorted(disp.keys())
        print(f"\nDisplacements (first 5 nodes):")
        for tag in tags[:5]:
            dx, dy, dz = disp[tag]
            print(f"  Node {tag}: dx = {dx:+.6f}  dy = {dy:+.6f}  dz = {dz:+.6f}")

    # Equilibrium
    summed = results.get('summed_reactions')
    if summed:
        print(f"\nBase reactions (should balance applied loads):")
        print(f"  Fx: {summed['fx']:+.3f}  Fy={summed['fy']:+.3f}  Fz={summed['fz']:+.3f}")
        print(f"  Mx: {summed['mx']:+.3f}  My={summed['my']:+.3f}  Mz={summed['mz']:+.3f}")

    # Load totals
    if 'load_totals' in results:
        print(f"\nApplied load totals per pattern:")
        for pname, totals in results['load_totals'].items():
            print(f"  {pname}: Fx={totals['fx']:>12.3f}  Fy={totals['fy']:>12.3f}  Fz={totals['fz']:>12.3f}  "
                  f"Mx={totals['mx']:>10.3f}  My={totals['my']:>10.3f}  Mz={totals['mz']:>10.3f}")

    print(f"\nDone. {len(disp)} node displacements, {len(results.get('nodal_reactions', {}))} reactions.")

    # ── Extract element forces ──
    print(f"\n── Extracting element forces ──")
    elem_forces = builder.extract_static_element_forces()
    print(f"  Forces for {len(elem_forces)} elements")

    # ── 2D force diagrams ──
    try:
        from fea_toolkit.plotting import plot_static_force_diagram

        # Axial forces (Fz)
        fig_n = plot_static_force_diagram(
            builder, elem_forces, 'Fz',
            title=f'Axial force Fz — {combo}',
        )
        if fig_n:
            fig_n.savefig(out / 'static_axial.png', dpi=150)
            print(f"  Saved → {out / 'static_axial.png'}")

        # Shear in X (Fx)
        fig_vx = plot_static_force_diagram(
            builder, elem_forces, 'Fx',
            title=f'Shear Fx — {combo}',
        )
        if fig_vx:
            fig_vx.savefig(out / 'static_shear.png', dpi=150)
            print(f"  Saved → {out / 'static_shear.png'}")

        # Moment about Y (My)
        fig_m = plot_static_force_diagram(
            builder, elem_forces, 'My',
            title=f'Moment My — {combo}',
        )
        if fig_m:
            fig_m.savefig(out / 'static_moment_2d.png', dpi=150)
            print(f"  Saved → {out / 'static_moment_2d.png'}")

    except Exception as e:
        print(f"  2D plotting skipped: {e}")

    # ── 3D moment diagram (flags) ──
    try:
        from fea_toolkit.plotting import plot_static_moment_3d

        print("\n── 3D moment diagram (flags) ──")
        plot_static_moment_3d(
            builder, elem_forces, 'My', mode='flag',
        )
        # Also save a tube‑mode screenshot
        plotter_tube = plot_static_moment_3d(
            builder, elem_forces, 'My', mode='tube', notebook=True,
        )
        if plotter_tube is not None:
            plotter_tube.screenshot(str(out / 'static_moment_3d.png'))
            print(f"  Saved → {out / 'static_moment_3d.png'}")
    except Exception as e:
        print(f"  3D plotting skipped: {e}")

    print("\nDone.")


if __name__ == "__main__":
    main()
