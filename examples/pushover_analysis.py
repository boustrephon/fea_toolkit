#!/usr/bin/env python
"""Non‑linear pushover analysis using fiber sections.

Produces (both PNG and SVG):

* ``examples/output/pushover_curve.png`` / ``.svg`` — capacity curve.
* ``examples/output/pushover_deformed.png`` / ``.svg`` — 3D deformed
  shape at peak displacement.
* ``examples/output/pushover_mode0.png`` / ``.svg`` …
  ``examples/output/pushover_mode2.png`` / ``.svg`` — first three mode
  shapes.

Usage::

    # Use a specific model
    python examples/pushover_analysis.py /path/to/model.s2k

    # Use the built‑in cantilever sample (no external file needed)
    python examples/pushover_analysis.py --sample
"""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # project root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fea_toolkit import __version__, ops_version
from fea_toolkit.io.s2k_parser import SAP2000Parser
from fea_toolkit.opensees.builder import OpenSeesBuilder
from fea_toolkit.plotting import (
    plot_pushover_curve,
    plot_mode_3d,
    plot_model_3d,
)


def main():
    parser = argparse.ArgumentParser(
        description="Non‑linear pushover analysis using fiber sections.",
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

    # Output directory for plots (gitignored)
    out = Path(__file__).parent / "output"
    out.mkdir(exist_ok=True)

    print(f"Model units: {md.units}")
    print(f"Nodes: {len(md.nodes)}")
    print(f"Frame elements: {len(md.frame_elements)}")
    print(f"Area elements: {len(md.area_elements)}")
    print(f"Frame sections: {len(md.sections)}")
    for name, sec in md.sections.items():
        from fea_toolkit.model.sap_data import ISection, PipeSection
        if isinstance(sec, ISection):
            dims = f"{sec.depth:.3f}x{sec.bf:.3f}x{sec.tf:.4f}x{sec.tw:.4f}  (d x bf x tf x tw)"
        elif isinstance(sec, PipeSection):
            dims = f"OD={sec.od:.3f}  t={sec.t:.4f}"
        else:
            dims = ""
        print(f"    {name}: {type(sec).__name__}  {dims}")
    print(f"Load patterns: {list(md.load_patterns.keys())}")

    # ── 2. Modal analysis (elastic) ──────────────────────────────────────────
    print("\n── Modal analysis ──")
    b_elastic = OpenSeesBuilder(md, {
        'element_type': 'elasticBeamColumn',
        'split_elements': True,
        'verbose': False,
    })
    b_elastic.build()
    b_elastic.check_self_weight_consistency()
    b_elastic.compute_seismic_masses()
    modal = b_elastic.run_modal_analysis(num_modes=6, print_results=True)
    n_modes = modal['num_modes']

    # Extract mode shapes for visualisation
    shapes = b_elastic.extract_mode_shapes(n_modes)

    for m in range(min(3, n_modes)):
        import pyvista as pv
        pv.OFF_SCREEN = True

        # PNG: consistent font at 2× resolution
        pl_png = plot_mode_3d(
            b_elastic, shapes, mode=m, scale=15.0,
            periods=modal['periods'], font_size=22,
            animate=False, notebook=True,
        )
        if pl_png is not None:
            pl_png.screenshot(str(out / f"pushover_mode{m}.png"), scale=2)
            pl_png.close()
            print(f"  Saved → {out / f'pushover_mode{m}.png'}  (2×, 22pt)")

        # SVG: large window for high-res output, same font
        pl_svg = plot_mode_3d(
            b_elastic, shapes, mode=m, scale=15.0,
            periods=modal['periods'], font_size=22,
            animate=False, notebook=True,
            window_size=[3840, 2880],
        )
        if pl_svg is not None:
            try:
                pl_svg.save_graphic(str(out / f"pushover_mode{m}.svg"), raster=False)
                print(f"  Saved → {out / f'pushover_mode{m}.svg'}  (22pt, 3840×2880)")
            except Exception:
                print(f"  Note: vector SVG unavailable for mode {m}")
            pl_svg.close()

    # ── 3. Non‑linear pushover ───────────────────────────────────────────────
    print("\n── Pushover analysis (fiber sections, forceBeamColumn) ──")
    b_push = OpenSeesBuilder(md, {
        'element_type': 'elasticBeamColumn',
        'split_elements': True,
        'verbose': False,
    })

    results = b_push.run_pushover_analysis(
        gravity_patterns={'DEAD': 1.0, 'DEAD SDL': 1.0, 'LL': 1.0},
        lateral_load_type='uniform',
        lateral_direction='X',
        max_disp=0.5,
        num_steps=50,
        print_progress=True,
    )

    # ── 4. Capacity curve ────────────────────────────────────────────────────
    fig = plot_pushover_curve(
        results,
        title='BPPS Pumphouse — Non-linear Pushover (DEAD + SDL + Uniform)',
    )
    if fig is not None:
        fig.savefig(out / 'pushover_curve.png', dpi=300)
        print(f"\n  Saved → {out / 'pushover_curve.png'}  (300 dpi)")
        fig.savefig(out / 'pushover_curve.svg')
        print(f"  Saved → {out / 'pushover_curve.svg'}")

    # Print summary
    bs = [abs(v) for v in results['base_shear']]
    cd = results['control_disp']
    print(f"\n  Control node: {results['control_node']}, DOF={results['dof']} (X)")
    print(f"  Steps completed: {len(results['control_disp']) - 1}")
    print(f"  Peak base shear: {max(bs):.1f} kN")
    print(f"  Max displacement: {max(cd):.4f} m")
    if len(bs) > 10:
        # Stiffness over first 5 steps (excluding gravity-only step 0)
        i1, i2 = 1, 6
        k_init = (bs[i2] - bs[i1]) / (cd[i2] - cd[i1]) if cd[i2] != cd[i1] else 0
        # Stiffness over last 5 steps
        j1, j2 = -6, -1
        k_final = (bs[j2] - bs[j1]) / (cd[j2] - cd[j1]) if cd[j2] != cd[j1] else 0
        print(f"  Initial stiffness (steps 1-5): {k_init:.0f} kN/m")
        print(f"  Final stiffness (last 5 steps): {k_final:.0f} kN/m")
        if k_init > 0:
            print(f"  Stiffness degradation: {(1 - k_final / k_init) * 100:.0f}%")

    # ── 5. Deformed shape at peak displacement ────────────────────────────────
    print("\n── 3D deformed shape at peak ──")
    # Rebuild the pushover to get the final state for visualisation
    # (run it again with fewer steps but capture final displacements)
    import numpy as np
    try:
        import pyvista as pv
        pv.OFF_SCREEN = True

        # Extract nodal displacements at the last push step
        # Re-run and capture via ops.nodeDisp after the final step
        b_viz = OpenSeesBuilder(md, {
            'element_type': 'elasticBeamColumn',
            'split_elements': True,
            'verbose': False,
        })
        b_viz.run_pushover_analysis(
            gravity_patterns={'DEAD': 1.0, 'DEAD SDL': 1.0, 'LL': 1.0},
            lateral_load_type='uniform',
            lateral_direction='X',
            max_disp=0.5,
            num_steps=50,
            print_progress=False,
        )

        import openseespy.opensees as ops

        # Model extents for annotation placement
        all_z = [n.z for n in b_viz.model.nodes.values()]
        z_min, z_max = min(all_z), max(all_z)
        z_mid = (z_min + z_max) * 0.5

        # Build deformed mesh
        elements = (b_viz.split_elements if b_viz.split_elements
                    else b_viz.model.frame_elements)
        segments = []
        for eid, elem in elements.items():
            if getattr(elem, 'inactive', False):
                continue
            ni = b_viz.model.nodes.get(elem.node_i)
            nj = b_viz.model.nodes.get(elem.node_j)
            if ni is None or nj is None:
                continue
            try:
                di = ops.nodeDisp(ni.node_tag)
                dj = ops.nodeDisp(nj.node_tag)
            except Exception:
                di, dj = (0, 0, 0), (0, 0, 0)
            p1 = np.array([ni.x + di[0], ni.y + di[1], ni.z + di[2]])
            p2 = np.array([nj.x + dj[0], nj.y + dj[1], nj.z + dj[2]])
            segments.append((p1, p2))

        # Determine max displacement magnitude for annotation
        max_d = 0.0
        for eid, elem in elements.items():
            if getattr(elem, 'inactive', False):
                continue
            for nid in (elem.node_i, elem.node_j):
                nd = b_viz.model.nodes.get(nid)
                if nd is None:
                    continue
                try:
                    d = ops.nodeDisp(nd.node_tag)
                    mag = np.linalg.norm(d[:3])
                    if mag > max_d:
                        max_d = mag
                except Exception:
                    pass

        # ── Compute annotation positions (model‑relative) ──
        all_x = [n.x for n in b_viz.model.nodes.values()]
        all_y = [n.y for n in b_viz.model.nodes.values()]
        cx = (min(all_x) + max(all_x)) * 0.5
        cy = (min(all_y) + max(all_y)) * 0.5
        z_top = z_max
        model_height = z_max - min(all_z)
        model_width = max(all_x) - min(all_x)
        load_type = results.get('lateral_load_type', 'uniform')

        # ── Arrow geometry (much thicker stems for visibility) ──
        grav_shaft_r = model_height * 0.06    # 6% of model height
        grav_tip_r = model_height * 0.10
        grav_tip_l = model_height * 0.25
        grav_start = [cx, cy, z_top + model_height * 0.3]
        grav_dir = [0, 0, -model_height * 1.5]
        grav_mid = [cx, cy, z_top - model_height * 0.6]   # label near mid-shaft

        cn_info = None  # (pos, tag)
        push_geo = None  # (start, dir, shaft_r, tip_r, tip_l, label_pos)
        try:
            cn_tag = results['control_node']
            cn_node = [n for n in b_viz.model.nodes.values() if n.node_tag == cn_tag][0]
            cn_info = ([cn_node.x, cn_node.y, cn_node.z], cn_tag)
            ps = [cn_node.x - model_width * 0.2, cn_node.y, cn_node.z]
            pd = [model_width * 1.05, 0, 0]
            push_geo = (ps, pd,
                        model_width * 0.05,     # shaft radius
                        model_width * 0.08,     # tip radius
                        model_width * 0.20,     # tip length
                        [ps[0] + pd[0] * 0.4, ps[1], ps[2]])  # label pos
        except Exception:
            pass

        # ── Helper: renders one output ──
        def _render_deformed(plotter, corner_font, label_font,
                             png_path=None, png_scale=1, svg_path=None):
            """Add meshes, arrows, point-labels, and corner text."""
            # Undeformed (grey)
            for eid, elem in elements.items():
                if getattr(elem, 'inactive', False):
                    continue
                ni = b_viz.model.nodes.get(elem.node_i)
                nj = b_viz.model.nodes.get(elem.node_j)
                if ni is None or nj is None:
                    continue
                p1 = np.array([ni.x, ni.y, ni.z])
                p2 = np.array([nj.x, nj.y, nj.z])
                n = max(2, int(np.linalg.norm(p2 - p1) * 2))
                poly = pv.lines_from_points(np.linspace(p1, p2, n))
                plotter.add_mesh(poly, color='lightgrey', line_width=2, opacity=0.3)
            # Deformed (red)
            for p1, p2 in segments:
                n = max(2, int(np.linalg.norm(p2 - p1) * 2))
                poly = pv.lines_from_points(np.linspace(p1, p2, n))
                plotter.add_mesh(poly, color='#c44e52', line_width=5)
            # ── Gravity arrow + point label ──
            grav_arrow = pv.Arrow(start=grav_start, direction=grav_dir,
                                  tip_length=grav_tip_l,
                                  tip_radius=grav_tip_r,
                                  shaft_radius=grav_shaft_r)
            plotter.add_mesh(grav_arrow, color='green')
            plotter.add_point_labels(
                [grav_mid],
                ["Gravity loads  (DEAD+SDL+LL)"],
                point_size=0, font_size=label_font,
                text_color='green', shape=None,
                always_visible=True,
            )
            # ── Push arrow + point label + corner text ──
            if push_geo is not None:
                ps, pd, shr, tir, til, lp = push_geo
                push_arrow = pv.Arrow(start=ps, direction=pd,
                                      tip_length=til, tip_radius=tir,
                                      shaft_radius=shr)
                plotter.add_mesh(push_arrow, color='blue')
                plotter.add_point_labels(
                    [lp],
                    [f"Lateral push  ({load_type})  →X"],
                    point_size=0, font_size=label_font,
                    text_color='blue', shape=None,
                    always_visible=True,
                )
            # ── Corner text summary ──
            plotter.add_text("Gravity loads: DEAD + SDL + LL",
                             position='upper_left',
                             font_size=corner_font, color='green')
            plotter.add_text(f"Lateral push: {load_type}  (→X)",
                             position='upper_right',
                             font_size=corner_font, color='blue')
            # ── Control node marker ──
            if cn_info is not None:
                cn_pos, cn_tag = cn_info
                sphere = pv.Sphere(radius=model_height * 0.025, center=cn_pos)
                plotter.add_mesh(sphere, color='gold', specular=0.5)
                plotter.add_point_labels(
                    [cn_pos],
                    [f"Control node {cn_tag}"],
                    point_size=0, font_size=label_font,
                    text_color='gold', shape=None,
                    always_visible=True,
                )
            # ── Peak displacement text ──
            plotter.add_text(f"Peak displacement = {max_d:.3f} m",
                             position='lower_edge',
                             font_size=corner_font)
            from fea_toolkit.plotting.viz import _set_isometric_view
            _set_isometric_view(plotter)
            # Export
            if png_path:
                plotter.screenshot(png_path, scale=png_scale)
            if svg_path:
                try:
                    plotter.save_graphic(svg_path, raster=False)
                except Exception:
                    print(f"  Note: vector SVG unavailable for {svg_path}")

        # ── PNG: 2× resolution, larger corner text, same label size ──
        p = pv.Plotter(notebook=True, off_screen=True)
        _render_deformed(p, corner_font=22, label_font=22,
                         png_path=str(out / 'pushover_deformed.png'), png_scale=2)
        p.close()
        print(f"  Saved → {out / 'pushover_deformed.png'}  (2×, 22pt)")

        # ── SVG: large window for high-res output, corner text at ⅔ size (15pt) ──
        s = pv.Plotter(notebook=True, off_screen=True,
                       window_size=[3840, 2880])
        _render_deformed(s, corner_font=15, label_font=22,
                         svg_path=str(out / 'pushover_deformed.svg'))
        s.close()
        print(f"  Saved → {out / 'pushover_deformed.svg'}  (corner=15pt, labels=22pt, 3840×2880)")

    except ImportError:
        print("  Skipping 3D deformed shape (pyvista not available)")

    print("\nDone.")


if __name__ == "__main__":
    main()
