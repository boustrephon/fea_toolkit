#!/usr/bin/env python
"""Example: Modal + response spectrum analysis from a SAP2000 JSON file.

Usage::

    python examples/modal_usage.py /path/to/model.json
"""

import sys
import argparse
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fea_toolkit import __version__, ops_version
from fea_toolkit.io.s2k_parser import SAP2000Parser
from fea_toolkit.opensees.builder import OpenSeesBuilder


def main():
    parser = argparse.ArgumentParser(
        description="Modal + response spectrum analysis from a SAP2000 JSON file.",
    )
    parser.add_argument(
        "json_file",
        help="Path to the SAP2000 JSON file.",
    )
    parser.add_argument(
        "--num-modes", type=int, default=30,
        help="Number of modes for eigenvalue analysis (default 30).",
    )
    args = parser.parse_args()

    json_path = Path(args.json_file)
    if not json_path.exists():
        sys.exit(f"Error: file not found — {json_path}")

    print(f"FEA Toolkit Version: {__version__}")
    print(f"OpenSees Version: {ops_version()}")
    print(f"Loading: {json_path}")

    # Load via parser's from_json to get model_data
    parser = SAP2000Parser.from_json(json_path)
    model_data = parser.get_model_data()

    print(f"Model units: {model_data.units}")
    print(f"Nodes: {len(model_data.nodes)}")
    print(f"Frames: {len(model_data.frame_elements)}")
    print(f"Mass sources: {len(model_data.mass_sources)}")
    for name, ms in model_data.mass_sources.items():
        print(f"  {name}: elements={ms.elements}, masses={ms.masses}, "
              f"loads={ms.loads}, load_pattern={ms.load_pattern}")

    # Build the OpenSees model
    config = {
        'element_type': 'elasticBeamColumn',
        'split_elements': True,
        'verbose': False,
    }
    builder = OpenSeesBuilder(model_data, config)
    builder.build()

    # ── Seismic masses ──
    print("\n── Computing seismic masses (g=9.81) ──")
    node_masses = builder.compute_seismic_masses(g=9.81)
    total_mass = sum(node_masses.values())
    print(f"  Total seismic mass: {total_mass:.2f} tonnes")
    print(f"  Total seismic weight: {total_mass * 9.81 / 1000:.2f} MN")

    # ── Modal analysis ──
    print(f"\n── Modal analysis ({args.num_modes} modes) ──")
    modal = builder.run_modal_analysis(num_modes=args.num_modes, print_results=True)

    periods = modal['periods']
    n = modal['num_modes']

    # ── Response spectrum (GB 50011-2010) ──
    def gb50011_spectrum(T, A=0.16, Tg=0.35, zeta=0.05):
        """GB 50011-2010 design spectrum — returns Sa in g."""
        if T <= 0.0:
            return A
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
        return max(0.0, A * beta)

    # Extend spectrum to cover the longest modal period
    T_max = max(periods[:n]) if periods and n > 0 else 6.0
    dT = 0.01
    T_curve = [i * dT for i in range(0, int(T_max / dT) + 2)]
    g = 9.81
    Sa_curve = [gb50011_spectrum(T, A=0.16, Tg=0.4, zeta=0.04) * g
                for T in T_curve]

    print(f"\n── Response spectrum analysis (X direction) ──")
    rs = builder.run_response_spectrum_analysis(
        num_modes=n,
        modal_periods=periods[:n],
        spectrum_periods=T_curve,
        spectrum_accels=Sa_curve,
        direction='X',
        damping_ratio=0.04,
        print_results=True,
    )

    # ── Element-by-element RS forces ──
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

    # ── Missing mass correction ──
    print(f"\n── Missing mass (rigid response) correction ──")
    # Build spectrum function that returns Sa in m/s²
    def rs_func(T):
        return gb50011_spectrum(T, A=0.16, Tg=0.4, zeta=0.04) * g

    missing = builder.add_missing_mass_correction(
        rs_results=rs,
        modal_results=modal,
        spectrum_func=rs_func,
        g=g,
        T_short=0.01,
    )

    print(f"  Residual mass X = {missing['residual_mass_X']:.1f} t")
    print(f"  Residual mass Y = {missing['residual_mass_Y']:.1f} t")
    print(f"  Sa(T={missing['T_short']:.2f}s) = {missing['Sa_short']:.4f} m/s²")
    print(f"  Centre of mass height = {missing['h_cm']:.2f} m")
    print(f"  Missing Vx = {missing['V_missing_X']:,.2f} kN")
    print(f"  Missing My  = {missing['M_missing_YY']:,.2f} kN·m")

    V_total = rs['base_shear_cqc'] + missing['V_missing_X']
    M_total = rs['base_moment_cqc'] + missing['M_missing_YY']
    print(f"\n  Total base shear Vx = {rs['base_shear_cqc']:,.2f} (CQC) + "
          f"{missing['V_missing_X']:,.2f} (rigid) = {V_total:,.2f} kN")
    print(f"  Total base moment My = {rs['base_moment_cqc']:,.2f} (CQC) + "
          f"{missing['M_missing_YY']:,.2f} (rigid) = {M_total:,.2f} kN·m")

    # ── Plotting demo ──
    try:
        from fea_toolkit.plotting import plot_force_diagram
        # Moment diagram
        fig_m = plot_force_diagram(
            elem_rs['element_results'], 'My_i',
            title='My (CQC combined) — UX excitation',
        )
        if fig_m:
            fig_m.savefig('chimney_moment_diagram.png', dpi=150)
            print(f"\n  Saved moment diagram → chimney_moment_diagram.png")

        # Shear diagram
        fig_v = plot_force_diagram(
            elem_rs['element_results'], 'Vz_i',
            title='Vz (CQC combined) — UX excitation',
        )
        if fig_v:
            fig_v.savefig('chimney_shear_diagram.png', dpi=150)
            print(f"  Saved shear diagram  → chimney_shear_diagram.png")
    except Exception as e:
        print(f"\n  Plotting skipped: {e}")

    # ── RS deformed shape ──
    try:
        print(f"\n── RS deformed shape ──")
        disp = builder.compute_rs_nodal_displacements(
            num_modes=n,
            modal_periods=periods[:n],
            eigenvalues=modal['eigenvalues'],
            spectrum_func=rs_func,
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
            plotter.screenshot('chimney_rs_deformed.png')
            print(f"  Saved deformed shape → chimney_rs_deformed.png")
    except Exception as e:
        print(f"  RS deformed shape skipped: {e}")

    print("\nDone.")


if __name__ == "__main__":
    main()
