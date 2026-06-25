#!/usr/bin/env python
"""Example: Load a SAP2000 model and run a static analysis.

Usage::

    python examples/run_pumphouse.py /path/to/model.s2k
"""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fea_toolkit import __version__, ops_version
from fea_toolkit.io.s2k_parser import SAP2000Parser
from fea_toolkit.opensees.builder import OpenSeesBuilder


def main():
    parser = argparse.ArgumentParser(
        description="Parse a SAP2000 model and run a linear static analysis.",
    )
    parser.add_argument(
        "s2k_file",
        help="Path to the SAP2000 text file (.s2k, .$2k).",
    )
    args = parser.parse_args()

    s2k_path = Path(args.s2k_file)
    if not s2k_path.exists():
        sys.exit(f"Error: file not found — {s2k_path}")

    print(f"FEA Toolkit Version: {__version__}")
    print(f"OpenSees Version: {ops_version()}")
    print(f"Loading: {s2k_path}\n")

    # Parse
    parser = SAP2000Parser(s2k_path)
    parser.parse()
    md = parser.get_model_data()

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

    # Static analysis: 1.4 DEAD + 1.4 Wind +X
    combo = {"DEAD": 1.4, "Wind +X": 1.4}
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
            title='Axial Fz (KN) — 1.4 DEAD + 1.4 Wind +X',
        )
        if fig_n:
            fig_n.savefig('pumphouse_axial.png', dpi=150)
            print("  Saved → pumphouse_axial.png")

        # Shear in X (Fx)
        fig_vx = plot_static_force_diagram(
            builder, elem_forces, 'Fx',
            title='Shear Fx (KN) — 1.4 DEAD + 1.4 Wind +X',
        )
        if fig_vx:
            fig_vx.savefig('pumphouse_shear.png', dpi=150)
            print("  Saved → pumphouse_shear.png")

        # Moment about Y (My)
        fig_m = plot_static_force_diagram(
            builder, elem_forces, 'My',
            title='Moment My (KN·m) — 1.4 DEAD + 1.4 Wind +X',
        )
        if fig_m:
            fig_m.savefig('pumphouse_moment_2d.png', dpi=150)
            print("  Saved → pumphouse_moment_2d.png")

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
            plotter_tube.screenshot('pumphouse_moment_3d.png')
            print("  Saved → pumphouse_moment_3d.png")
    except Exception as e:
        print(f"  3D plotting skipped: {e}")

    print("\nDone.")


if __name__ == "__main__":
    main()
