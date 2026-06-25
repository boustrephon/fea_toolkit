#!/usr/bin/env python
"""Modal analysis + response spectrum from a SAP2000 model.

Demonstrates:
  1. Parse a .s2k file into SAPModelData
  2. Build an OpenSees model
  3. Compute seismic masses from the MASS SOURCE table
  4. Run eigenvalue (modal) analysis
  5. Run CQC response-spectrum analysis with a GB 50011 spectrum
  6. Element-level RS forces and missing mass correction

Usage::

    # Use a specific model
    python examples/modal_rs_analysis.py /path/to/model.s2k

    # Use the built‑in cantilever sample (no external file needed)
    python examples/modal_rs_analysis.py --sample
"""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # project root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fea_toolkit import __version__, ops_version
from fea_toolkit.io.s2k_parser import SAP2000Parser
from fea_toolkit.opensees.builder import OpenSeesBuilder


def gb50011_spectrum(T, A=0.16, Tg=0.35, zeta=0.05):
    """GB 50011-2010 design spectrum — returns Sa in m/s²."""
    if T <= 0.0:
        return A * 9.81
    gamma = 0.9 + (0.05 - zeta) / (0.3 + 6.0 * zeta)
    eta1 = max(0.0, 0.02 + (0.05 - zeta) / (4.0 + 32.0 * zeta))
    eta2 = max(0.55, 1.0 + (0.05 - zeta) / (0.08 + 1.6 * zeta))
    if T <= 0.1:
        beta = 0.45 + (eta2 - 0.45) * 10.0 * T
    elif T <= Tg:
        beta = eta2
    elif T <= 5.0 * Tg:
        beta = eta2 * (Tg / T) ** gamma
    else:
        beta = eta2 * (0.2 ** gamma) - eta1 * (T - 5.0 * Tg)
    return max(0.0, A * beta) * 9.81


def main():
    parser = argparse.ArgumentParser(
        description="Modal + response spectrum analysis from a .s2k file.",
    )
    parser.add_argument(
        "s2k_file", nargs="?",
        help="Path to the SAP2000 text file (.s2k, .$2k). Omit when using --sample.",
    )
    parser.add_argument(
        "--sample", action="store_true",
        help="Use the built‑in cantilever sample model (no external file needed).",
    )
    parser.add_argument(
        "--num-modes", type=int, default=30,
        help="Number of modes for eigenvalue analysis (default 30).",
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

    print(f"Model units: {md.units}")
    print(f"Nodes: {len(md.nodes)}")
    print(f"Frames: {len(md.frame_elements)}")
    print(f"Mass sources: {len(md.mass_sources)}")
    for name, ms in md.mass_sources.items():
        print(f"  {name}: elements={ms.elements}, masses={ms.masses}, "
              f"loads={ms.loads}, load_pattern={ms.load_pattern}")

    if not md.mass_sources:
        sys.exit("Error: no MASS SOURCE defined in the model — cannot run modal analysis.")

    # ── 2. Build ─────────────────────────────────────────────────────────────
    config = {
        'element_type': 'elasticBeamColumn',
        'split_elements': True,
        'verbose': False,
    }
    builder = OpenSeesBuilder(md, config)
    builder.build()

    # ── 3. Seismic masses ────────────────────────────────────────────────────
    print("\n── Computing seismic masses (g=9.81) ──")
    node_masses = builder.compute_seismic_masses(g=9.81)
    total_mass = sum(node_masses.values())
    print(f"  Total seismic mass: {total_mass:.2f} tonnes")
    print(f"  Total seismic weight: {total_mass * 9.81 / 1000:.2f} MN")

    # ── 4. Modal analysis ────────────────────────────────────────────────────
    print(f"\n── Modal analysis ({args.num_modes} modes) ──")
    modal = builder.run_modal_analysis(num_modes=args.num_modes, print_results=True)

    periods = modal['periods']
    n = modal['num_modes']
    if n == 0:
        sys.exit("Error: no modes converged.")

    # ── 5. Build spectrum curve ──────────────────────────────────────────────
    g = 9.81
    T_max = max(periods[:n])
    dT = 0.01
    T_curve = [i * dT for i in range(0, int(T_max / dT) + 2)]
    Sa_curve = [gb50011_spectrum(T, A=0.16, Tg=0.4, zeta=0.04) for T in T_curve]

    print(f"\n── Response spectrum analysis (X direction, {n} modes) ──")
    rs = builder.run_response_spectrum_analysis(
        num_modes=n,
        modal_periods=periods[:n],
        spectrum_periods=T_curve,
        spectrum_accels=Sa_curve,
        direction='X',
        damping_ratio=0.04,
        print_results=True,
    )

    # ── 6. Element-level RS forces ───────────────────────────────────────────
    print(f"\n── Element-level RS forces ──")
    elem_rs = builder.extract_element_rs_forces(
        num_modes=n,
        modal_periods=periods[:n],
        spectrum_periods=T_curve,
        spectrum_accels=Sa_curve,
        direction='X',
        damping_ratio=0.04,
        print_results=True,
    )

    # ── 7. Missing mass correction ────────────────────────────────────────────
    print(f"\n── Missing mass (rigid response) correction ──")

    missing = builder.add_missing_mass_correction(
        rs_results=rs,
        modal_results=modal,
        spectrum_func=gb50011_spectrum,
        g=g,
        T_short=0.01,
    )

    print(f"  Residual mass X = {missing['residual_mass_X']:.1f} t")
    print(f"  Residual mass Y = {missing['residual_mass_Y']:.1f} t")
    print(f"  Missing Vx = {missing['V_missing_X']:,.2f} kN")
    print(f"  Missing My  = {missing['M_missing_YY']:,.2f} kN·m")

    V_total = rs['base_shear_cqc'] + missing['V_missing_X']
    M_total = rs['base_moment_cqc'] + missing['M_missing_YY']
    print(f"\n  Total base shear Vx = {rs['base_shear_cqc']:,.2f} (CQC) + "
          f"{missing['V_missing_X']:,.2f} (rigid) = {V_total:,.2f} kN")
    print(f"  Total base moment My = {rs['base_moment_cqc']:,.2f} (CQC) + "
          f"{missing['M_missing_YY']:,.2f} (rigid) = {M_total:,.2f} kN·m")

    # ── 8. Output directory ──────────────────────────────────────────────────
    out = Path(__file__).parent / "output"
    out.mkdir(exist_ok=True)

    # ── Force diagrams ───────────────────────────────────────────────────────
    try:
        from fea_toolkit.plotting import plot_force_diagram

        fig_m = plot_force_diagram(
            elem_rs['element_results'], 'My_i',
            title='My (CQC combined) — UX excitation',
        )
        if fig_m:
            fig_m.savefig(out / 'rs_moment_diagram.png', dpi=300)
            fig_m.savefig(out / 'rs_moment_diagram.svg')
            print(f"\n  Saved → rs_moment_diagram.png / .svg")

        fig_v = plot_force_diagram(
            elem_rs['element_results'], 'Vz_i',
            title='Vz (CQC combined) — UX excitation',
        )
        if fig_v:
            fig_v.savefig(out / 'rs_shear_diagram.png', dpi=300)
            fig_v.savefig(out / 'rs_shear_diagram.svg')
            print(f"  Saved → rs_shear_diagram.png / .svg")
    except Exception as e:
        print(f"\n  Plotting skipped: {e}")

    # ── RS deformed shape ────────────────────────────────────────────────────
    try:
        print(f"\n── RS deformed shape ──")
        disp = builder.compute_rs_nodal_displacements(
            num_modes=n,
            modal_periods=periods[:n],
            eigenvalues=modal['eigenvalues'],
            spectrum_func=gb50011_spectrum,
            direction='X',
            damping_ratio=0.04,
        )
        top_tag = max(disp.keys(), key=lambda t: disp[t][0])
        top_dx = disp[top_tag][0]
        print(f"  Max displacement: node {top_tag} dx = {top_dx:.4f} m")

        from fea_toolkit.plotting import plot_rs_deformed_3d
        plotter = plot_rs_deformed_3d(
            builder, disp, scale=max(50, int(0.5 / max(top_dx, 1e-6))),
            show_original=True,
        )
        if plotter is not None:
            plotter.screenshot(str(out / 'rs_deformed.png'), scale=2)
            print(f"  Saved → rs_deformed.png")
            try:
                plotter.save_graphic(str(out / 'rs_deformed.svg'), raster=False)
                print(f"  Saved → rs_deformed.svg")
            except Exception:
                pass
    except Exception as e:
        print(f"  Deformed shape skipped: {e}")

    print("\nDone.")


if __name__ == "__main__":
    main()
