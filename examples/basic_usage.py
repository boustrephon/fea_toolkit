#!/usr/bin/env python
"""Example: Parse a SAP2000 .S2K file, enrich sections, and print summary.

Run this script from the project root (where the 'fea_toolkit/' folder lives):

    # Open a file chooser dialog
    $ python examples/basic_usage.py

    # Use the bundled sample file
    $ python examples/basic_usage.py --sample

    # Use a specific SAP2000 text file
    $ python examples/basic_usage.py /path/to/model.$2k

    # Parse only (skip OpenSees analysis)
    $ python examples/basic_usage.py /path/to/model.s2k --no-analysis

"""

import sys
import argparse
from pathlib import Path
import platform

# Add project root to Python path so that 'fea_toolkit' can be imported
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fea_toolkit import __version__, ops_version
from fea_toolkit.io.s2k_parser import SAP2000Parser
from fea_toolkit.model.sections import SectionLibrary
from fea_toolkit.opensees.builder import OpenSeesBuilder
from fea_toolkit.io.helper import mac_file_chooser, tkinter_file_chooser

# Get Operating System
os_name = platform.system()
# Get Chipset/Architecture
architecture = platform.machine()

print(f"Operating System: {os_name}")
print(f"Chipset Architecture: {architecture}")
print(f'FEA Toolkit Version: {__version__}')
print(f'OpenSees Version: {ops_version()}')

def pick_file() -> Path:
    """Open a native file chooser dialog appropriate for the platform."""
    if os_name == "Darwin" and architecture == "arm64":
        path = mac_file_chooser()
    else:
        path = tkinter_file_chooser()
    if path is None:
        sys.exit("No file selected.")
    return Path(path)

def main():
    parser = argparse.ArgumentParser(
        description="Parse a SAP2000 .s2k / .e2k file and optionally run an OpenSees analysis.",
    )
    parser.add_argument(
        "s2k_file", nargs="?", default=None,
        help="Path to the SAP2000 text file (.s2k, .$2k, .e2k).",
    )
    parser.add_argument(
        "--sample", action="store_true",
        help="Use the bundled sample file instead of a file chooser.",
    )
    parser.add_argument(
        "--no-analysis", action="store_true",
        help="Skip the OpenSees analysis step (only parse and enrich).",
    )
    args = parser.parse_args()
    ANALYSE = not args.no_analysis

    project_root = Path(__file__).parent.parent

    # Determine the SAP2000 file path
    if args.s2k_file:
        s2k_file = Path(args.s2k_file)
        if not s2k_file.exists():
            sys.exit(f"Error: file not found — {s2k_file}")
    elif args.sample:
        s2k_file = project_root / "tests" / "fixtures" / "sample.s2k"
        if not s2k_file.exists():
            sys.exit(f"Error: sample file not found at {s2k_file}")
    else:
        s2k_file = pick_file()

    section_db_path = project_root / "data" / "section_dict.pkl"

    # Check if files exist
    if not s2k_file.exists():
        print(f"Error: Sample file not found at {s2k_file}")
        print("Please create a sample.s2k file in tests/fixtures/")
        return
    if not section_db_path.exists():
        print(f"Error: Section database not found at {section_db_path}")
        print("Please place section_dict.pkl in the data/ folder at project root.")
        return

    # Parse the model
    print(f"\nParsing SAP2000 file: {s2k_file}")
    parser = SAP2000Parser(s2k_file)
    parser.parse()
    json_file = s2k_file.with_suffix(".json")
    parser.to_json(json_file)
    model_data = parser.get_model_data()
    print(f"Model units: {model_data.units}")
    print(f"Nodes: {len(model_data.nodes)}")
    print(f"Frames: {len(model_data.frame_elements)}")
    print(f"Sections: {len(model_data.sections)}")
    print(f"Load Patterns: {len(model_data.load_patterns)}")
    print(f"Node Loads: {len(model_data.joint_loads)}")
    print(f"Distributed Loads: {len(model_data.frame_dist_loads)}")

    if len(model_data.load_patterns) > 0:
        print('\nLoad Patterns:')
        _ = [print(pat) for pat in model_data.load_patterns.keys()]

    # Enrich sections if database available
    if section_db_path.exists():
        section_db = SectionLibrary(section_db_path, target_units=model_data.units['L'])
        for sec in model_data.sections.values():
            section_db.enrich_section(sec)
            if sec.Z33:
                print(f"  {sec.name}: Z33 = {sec.Z33:.2e} (in {model_data.units}³)")
            else:
                print(f"  {sec.name}: no manufacturer data found")
    else:
        print("Skipping section enrichment (no database)")

    # ── Build the OpenSees model ──────────────────────────────────────────
    config = {
        'element_type': 'elasticBeamColumn',
        'split_elements': True,
        'verbose': True,
    }
    builder = OpenSeesBuilder(model_data, config)
    builder.build()

    # Export split model data for inspection
    split_model_path = s2k_file.with_suffix(".split.json")
    builder.export_split_model(split_model_path)

    # Load totals are always computed after build() — print a summary
    unit_F = model_data.units.get('F', '?')
    unit_L = model_data.units.get('L', '?')
    print(f"\n── Applied load totals per pattern ({unit_F}) ──")
    for pname, totals in builder.load_totals.items():
        print(f"  {pname}:")
        print(f"    Forces:  Fx = {totals['fx']:>12.3f}  Fy = {totals['fy']:>12.3f}  Fz = {totals['fz']:>12.3f}")
        print(f"    Moments: Mx = {totals['mx']:>12.3f}  My = {totals['my']:>12.3f}  Mz = {totals['mz']:>12.3f}")

    # ── Run analysis and compare with reactions ───────────────────────────
    if ANALYSE:
        odb_tag = 0
        print('\n', 80*'=')
        print(f"── Running OpenSees static analysis (all patterns combined) (ODB tag {odb_tag}) ──")
        results = builder.run_static_analysis(odb_tag=odb_tag, extract_reactions=True)
        disp = results.get('nodal_displacements', {})

        # Unit scaling for display
        if unit_L == 'm':
            unit_L2, scale = 'mm', 1000.0
        elif unit_L == 'ft':
            unit_L2, scale = 'in', 12.0
        else:
            unit_L2, scale = unit_L, 1.0

        # ── Displacements ──
        print(f"\n── Displacements (first 5 nodes, {unit_L2}) ──")
        if isinstance(disp, dict) and disp:
            count = 0
            for node_tag, d in disp.items():
                if count >= 5:
                    break
                dx, dy, dz = d[0] * scale, d[1] * scale, d[2] * scale
                print(f"  Node {node_tag:>4}: dx = {dx:8.3f}  dy = {dy:8.3f}  dz = {dz:8.3f}")
                count += 1
        else:
            print("  (no displacement data)")

        # ── Equilibrium check: applied loads vs reactions ──
        summed_rx = results.get('summed_reactions')
        if summed_rx and hasattr(builder, 'load_totals'):
            print(f"\n── Equilibrium check ({unit_F}, {unit_F}·{unit_L}) ──")
            # Sum all applied loads across all patterns
            total_applied = {'fx': 0.0, 'fy': 0.0, 'fz': 0.0,
                             'mx': 0.0, 'my': 0.0, 'mz': 0.0}
            for totals in builder.load_totals.values():
                for k in total_applied:
                    total_applied[k] += totals[k]

            print(f"  {'':>15}  {'Fx':>12}  {'Fy':>12}  {'Fz':>12}")
            print(f"  {'Applied':>15}  {total_applied['fx']:12.3f}  {total_applied['fy']:12.3f}  {total_applied['fz']:12.3f}")
            print(f"  {'Reactions':>15}  {summed_rx['fx']:12.3f}  {summed_rx['fy']:12.3f}  {summed_rx['fz']:12.3f}")
            print(f"  {'Difference':>15}  {total_applied['fx'] + summed_rx['fx']:12.3f}"
                  f"  {total_applied['fy'] + summed_rx['fy']:12.3f}"
                  f"  {total_applied['fz'] + summed_rx['fz']:12.3f}")

        # ── Per-pattern equilibrium checks ──
        if hasattr(builder, 'load_totals') and len(builder.load_totals) > 1:
            print('\n', 80*'=')
            print(f"── Per-pattern equilibrium checks ({unit_F}) ──")
            for pname in builder.load_totals:
                print('\n', 80*'-')
                pat_results = builder.run_static_analysis(
                    extract_reactions=True,
                    pattern_scales={pname: 1.0},
                )
                pat_rx = pat_results.get('summed_reactions', {})
                pat_app = pat_results.get('load_totals', {}).get(pname, {})
                if pat_rx:
                    print(f"  {pname}:")
                    print(f"    Applied: Fx={pat_app.get('fx',0):12.3f}  "
                          f"Fy={pat_app.get('fy',0):12.3f}  "
                          f"Fz={pat_app.get('fz',0):12.3f}")
                    print(f"    Reactn:  Fx={pat_rx.get('fx',0):12.3f}  "
                          f"Fy={pat_rx.get('fy',0):12.3f}  "
                          f"Fz={pat_rx.get('fz',0):12.3f}")

        # ── Load combination example (1.2 DEAD + 1.6 SUPERDEAD) ──
        if hasattr(builder, 'load_totals') and len(builder.load_totals) > 1:
            print('\n', 80*'=')
            print(f"── Load combination: 1.2 DEAD + 1.6 SUPERDEAD ({unit_F}) ──")
            combo = {"DEAD": 1.2, "SUPERDEAD": 1.6}
            combo_results = builder.run_static_analysis(
                extract_reactions=True,
                pattern_scales=combo,
            )
            crx = combo_results.get('summed_reactions', {})
            ctots = combo_results.get('load_totals', {})
            total_app = {'fx': 0., 'fy': 0., 'fz': 0.}
            for t in ctots.values():
                for k in total_app:
                    total_app[k] += t.get(k, 0.)
            if crx:
                print(f"    Applied: Fx={total_app['fx']:12.3f}  "
                      f"Fy={total_app['fy']:12.3f}  "
                      f"Fz={total_app['fz']:12.3f}")
                print(f"    Reactn:  Fx={crx.get('fx',0):12.3f}  "
                      f"Fy={crx.get('fy',0):12.3f}  "
                      f"Fz={crx.get('fz',0):12.3f}")

        # ── Modal analysis ──
        if model_data.mass_sources:
            print('\n', 80*'=')
            print("── Modal analysis ──")
            # Rebuild (the static analysis above may have wiped the model)
            builder.build()
            builder.compute_seismic_masses(g=9.81)
            modal_results = builder.run_modal_analysis(num_modes=15, print_results=True)

            # Build a simple GB50011 spectrum and run response spectrum
            def gb50011_spectrum(T, A=0.16, Tg=0.35, zeta=0.05):
                """GB 50011-2010 design spectrum (returns Sa in g)."""
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

            periods = modal_results['periods']
            n = modal_results['num_modes']
            g = 9.81
            # Extend spectrum to cover the longest modal period
            T_max = max(periods[:n]) if periods and n > 0 else 6.0
            T_curve = [t * 0.1 for t in range(0, int(T_max / 0.1) + 2)]
            Sa_curve = [gb50011_spectrum(T, A=0.16, Tg=0.4, zeta=0.04) * g
                        for T in T_curve]

            try:
                rs_results = builder.run_response_spectrum_analysis(
                    num_modes=n,
                    modal_periods=periods[:n],
                    spectrum_periods=T_curve,
                    spectrum_accels=Sa_curve,
                    direction='X',
                    damping_ratio=0.04,
                    print_results=True,
                )
            except Exception as e:
                print(f"  Response spectrum analysis skipped: {e}")
        else:
            print("\n── No MASS SOURCE defined — skipping modal analysis. ──")

        print("\nResults keys:", results.keys())

if __name__ == "__main__":
    main()

    if False:
        import opstool.vis.pyvista as opsvis
        plotter = opsvis.plot_model(show_node_numbering=True, show_ele_numbering=True)
        plotter.show()
        plotter.close()

