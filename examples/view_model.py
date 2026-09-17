#!/usr/bin/env python
"""View a structural model or a saved results archive.

Given a SAP2000 ``.s2k`` file this script parses the model, runs the
requested analysis, and displays the matching visualisation.  Given a
saved ``.npz`` results archive it displays the archived data directly,
without re-running any analysis.

Usage::

    # Show the bare model geometry
    python examples/view_model.py /path/to/model.s2k --result mesh

    # Mesh view: highlight a section, restrict to a storey band, label frames
    python examples/view_model.py /path/to/model.s2k --result mesh \
        --highlight-section 2xR3 2xR4 --zlim 3.4 4.5 --labels

    # Mesh view: highlight the joints of a SAP2000 constraint group
    # (any type: BODY, DIAPHRAGM, EQUAL, ...) and label them with their
    # SAP joint labels
    python examples/view_model.py /path/to/model.s2k --result mesh \
        --highlight-constraint Fix --node-labels

    # Mesh view: overdraw a Selection (frames in yellow, or nodes as dots)
    python examples/view_model.py /path/to/model.s2k --result mesh \
        --select "type=Frame; section=2xR3,2xR4"
    python examples/view_model.py /path/to/model.s2k --result mesh \
        --select "type=Node; group=Deck" --select "id=10,11,12"

    # Mesh view: select the joints of a SAP2000 constraint group (any type —
    # BODY, DIAPHRAGM, EQUAL, ...) through the same Selection mechanism
    python examples/view_model.py /path/to/model.s2k --result mesh \
        --select "constraint=Fix"

    # Static analysis - deformed shape, then a force diagram
    python examples/view_model.py /path/to/model.s2k --result static --quantity Mz

    # Modal / response-spectrum / pushover / interactive
    python examples/view_model.py /path/to/model.s2k --result modal --mode 1
    python examples/view_model.py /path/to/model.s2k --result rs
    python examples/view_model.py /path/to/model.s2k --result pushover
    python examples/view_model.py /path/to/model.s2k --result interactive

    # Display a previously saved results archive (no solver run)
    python examples/view_model.py /path/to/results.npz --result static

    # Per-element response-spectrum forces from an archive that has them
    python examples/view_model.py /path/to/review.npz --result rs --quantity Mz
    python examples/view_model.py /path/to/review.npz --result rs --quantity Mz --dimension 3d

    # Built-in sample (no external file needed)
    python examples/view_model.py --sample --result static

    # Pre-parsed inputs instead of the .s2k text:
    #   raw-table JSON  - SAP2000Parser(...).parse().to_json("model.json")
    #   model-codec JSON - model_codec.model_to_json(md)
    python examples/view_model.py /path/to/model.json --result mesh

Every PyVista window this script opens is titled
``PyVista - <input file name>`` -- the base name only, e.g.
``PyVista - tower.s2k`` -- so a screen full of windows still says which
model or archive each one belongs to.  ``--title TITLE`` replaces that
caption outright::

    python examples/view_model.py /path/to/model.s2k -r mesh \
        --highlight-constraint Fix --title "Pipe rack - Fix body"
"""

import argparse
import sys
from pathlib import Path

import numpy as np

# Allow running from any directory (project root + src on the path).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fea_toolkit import __version__, ops_version
from fea_toolkit.io.model_loader import load_model_data
from fea_toolkit.model.mesh_model import MeshModel
from fea_toolkit.model.selection import SELECT_KEYS_HELP, Selection
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
from fea_toolkit.opensees.preprocessor import preprocess_model
from fea_toolkit.plotting import (
    mass_participation_ratios,
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


def window_title(path=None, override=None):
    """Return the PyVista window title for a model / archive input.

    Only the file's base name is used, so the title bar reads
    ``PyVista - tower.s2k`` rather than the whole path.  ``PyVista`` is the
    prefix PyVista itself gives its render windows, so the library name is
    kept and the input file appended to it.

    Args:
        path: Path to the loaded ``.s2k`` / ``.json`` / ``.npz`` file, or
            ``None`` for the built-in ``--sample`` model (which has no file;
            it is titled ``PyVista - sample``).  Ignored when *override* is
            given.
        override: Explicit ``--title`` value from the CLI.  When it is not
            ``None`` it is used **verbatim**, replacing the file-derived
            title.  An empty string is therefore honoured (it clears the
            caption) rather than being treated as "unset".

    Returns:
        The window title string.
    """
    if override is not None:
        return override
    return f"PyVista - {Path(path).name if path else 'sample'}"


def mode_index(args):
    """Convert the 1-based ``--mode`` CLI option to a 0-based index.

    Modes are presented 1-based to the user (mode 1 is the first mode), which
    matches both the ``Mode N`` label on the plot and the 1-based ``mode``
    field of the modal ``mass_participation`` records.

    Args:
        args: Parsed CLI namespace (uses ``mode``).

    Returns:
        The 0-based mode index expected by the plotting / analysis APIs.
    """
    if args.mode < 1:
        sys.exit(f"Error: --mode is 1-based (mode 1 is the first mode); got {args.mode}.")
    return args.mode - 1


def mesh_view_kwargs(args, section_names):
    """Translate the mesh-view CLI options into ``plot_mesh`` keyword arguments.

    Args:
        args: Parsed CLI namespace (uses ``zlim``, ``labels``, ``node_labels``
            and ``highlight_section``).
        section_names: Section names present in the model, used to validate
            ``--highlight-section`` and to build the colour map.

    Returns:
        Dict of extra ``plot_mesh`` keyword arguments (empty when no
        mesh-view option was requested).
    """
    kwargs = {}
    if args.zlim is not None:
        lo, hi = args.zlim
        kwargs["zlim"] = (lo, hi)
    if args.labels:
        kwargs["show_frame_labels"] = True
    if args.node_labels:
        kwargs["show_node_labels"] = True
    if args.highlight_section:
        wanted = set(args.highlight_section)
        available = set(section_names)
        missing = wanted - available
        if missing:
            print(f"Warning: section(s) not found in the model: {sorted(missing)}")
        matched = wanted & available
        if matched:
            # Matched sections red, every other section dimmed grey.
            kwargs["section_colors"] = {
                name: ("#ff2d2d" if name in matched else "#c8c8c8") for name in available
            }
            print(f"Highlighting {sorted(matched)} in red; other sections dimmed grey.")
        else:
            print("Warning: --highlight-section matched no sections; ignoring.")
    return kwargs


def selection_highlights(args):
    """Build the ``--select`` :class:`Selection` objects for the mesh view.

    Args:
        args: Parsed CLI namespace (uses ``select``).

    Returns:
        List of :class:`Selection` objects, or ``None`` when the option was
        not used (which :func:`plot_mesh` reads as "no overlay").
    """
    if not args.select:
        return None
    selections = []
    for expr in args.select:
        try:
            sel = Selection.from_string(expr)
        except ValueError as exc:
            sys.exit(f"Error: --select {expr!r}: {exc}")
        if (
            sel.constraints is not None
            and sel.element_types is not None
            and "Node" not in sel.element_types
        ):
            print(
                "Warning: --select: constraint= selects joints only, but the "
                f"element type(s) given ({', '.join(sel.element_types)}) exclude "
                "Node — the selection matches nothing."
            )
        # A Node-only Selection (explicit Node type, or a constraint criterion,
        # which only joints can satisfy) ignores the element-only criteria.
        if (sel.element_types == ["Node"] or sel.constraints is not None) and (
            sel.sections or sel.materials or sel.elevation_range
        ):
            # Selection matches nodes on type / id / group / constraint only, so
            # a node-only selection silently ignores every other criterion.
            print(
                "Warning: --select: a Node-only Selection (Node type / constraint=) "
                "is matched by type, id, group and constraint — its section / "
                "material / z criteria are ignored."
            )
        selections.append(sel)
    return selections


def constraint_node_colors(args, md):
    """Resolve ``--highlight-constraint`` names to a node-colour mapping.

    Looks each requested name up in the model's ``CONSTRAINT DEFINITIONS``
    tables and collects the joints assigned to it in ``JOINT CONSTRAINT
    ASSIGNMENTS`` (``md.constraint_assignments``).  The lookup is
    type-agnostic, so ``BODY`` rigid bodies, ``DIAPHRAGM``, ``EQUAL``,
    ``WELD``, ... groups all work.

    The highlighted joint set is resolved through
    :attr:`~fea_toolkit.model.selection.Selection.constraints` -- the same
    criterion ``--select`` uses -- so the red and yellow paths can never
    disagree about which joints belong to a group.  The per-name report below
    still reads the assignment table directly, because it counts *assigned*
    joints (including any the model dropped) rather than selected ones.

    Args:
        args: Parsed CLI namespace (uses ``highlight_constraint``).
        md: Parsed :class:`~fea_toolkit.model.sap_data.SAPModelData`.

    Returns:
        ``{joint_id: color}`` for :func:`plot_mesh`'s ``node_colors``
        argument -- empty when the option is unused or matched nothing.
    """
    if not args.highlight_constraint:
        return {}

    wanted = list(dict.fromkeys(args.highlight_constraint))
    definitions = getattr(md, "constraints", {}) or {}
    assignments = getattr(md, "constraint_assignments", {}) or {}
    nodes = getattr(md, "nodes", {}) or {}

    missing = [name for name in wanted if name not in definitions]
    if missing:
        print(f"Warning: constraint(s) not defined in the model: {missing}")

    known = [name for name in wanted if name in definitions]
    colors = {}
    if known:
        # Single source of truth for *which* joints belong to each group.
        for jid in Selection(constraints=known).get_node_ids(md):
            colors[jid] = "#ff2d2d"

    for name in known:
        ctype = getattr(definitions[name], "constraint_type", "?") or "?"
        assigned = [jid for jid, cname in assignments.items() if cname == name]
        present = [jid for jid in assigned if jid in nodes]
        print(
            f"Highlighting constraint '{name}' ({ctype}): "
            f"{len(present)} of {len(assigned)} assigned joint(s) present in the model."
        )

    if not colors:
        print("Warning: --highlight-constraint matched no joints; ignoring.")
    return colors


def _npz_section_names(data):
    """Return the set of section names present in a loaded NPZ data dict."""
    try:
        return {str(s) for s in data["frame_sec_name"]}
    except Exception:
        return set()


def build_builder(md, element_type="elasticBeamColumn"):
    """Preprocess and build the OpenSees domain, returning the builder."""
    config = {"element_type": element_type, "split_elements": True, "verbose": False}
    mesh_model = preprocess_model(md, config)
    builder = AnalysisBuilder(mesh_model, {"element_type": element_type, "verbose": False})
    builder.build_domain()
    return builder


def show_static(builder, md, quantity, scale, title=None):
    """Static analysis: deformed shape, then a 3D force diagram."""
    avail = list(md.load_patterns.keys())
    combo = dict.fromkeys(avail, 1.0) if avail else None
    results = builder.run_static_analysis(extract_reactions=True, pattern_scales=combo)
    disp = results.get("nodal_displacements", {})
    if not disp:
        print("No displacements produced - showing the bare mesh instead.")
        plot_mesh(builder, title=title)
        return
    plot_deformed_displacement_3d(builder, disp, scale=scale, show_undeformed=True, title=title)
    elem_forces = builder.extract_static_element_forces()
    plot_force_diagram(
        builder,
        elem_forces,
        quantity=quantity,
        mode="flag",
        dimension="3d",
        window_title=title,
    )


def show_modal(builder, num_modes, mode, mode_scale=5.0, title=None):
    """Modal analysis with an animated mode shape.

    *mode* is a **0-based** index; the CLI converts its 1-based ``--mode`` via
    :func:`mode_index`.

    The period and the six-DOF mass-participation ratios drawn on the plot are
    OpenSees ``modalProperties()`` results, read through
    :func:`~fea_toolkit.plotting.mass_participation_ratios` — they are not
    recomputed here.
    """
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
        participation=mass_participation_ratios(modal["modal_props"]),
        scale=mode_scale,
        animate=True,
        title=title,
    )


def show_rs(builder, md, num_modes, alpha_max, tg, damping, scale, mode_scale=5.0, title=None):
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
        plot_mode_animation(
            builder,
            shapes,
            mode=0,
            periods=periods,
            participation=mass_participation_ratios(modal["modal_props"]),
            scale=mode_scale,
            animate=True,
            title=title,
        )
        return

    plot_deformed_displacement_3d(builder, disp, scale=scale, show_undeformed=True, title=title)


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


def show_interactive(builder, md, title=None):
    """Interactive widget viewer (radio buttons, sliders, click-to-inspect)."""
    avail = list(md.load_patterns.keys())
    combo = dict.fromkeys(avail, 1.0) if avail else None
    results = builder.run_static_analysis(extract_reactions=True, pattern_scales=combo)
    forces = builder.extract_static_element_forces()
    plot_interactive_viewer(builder, {"All": forces}, {"All": results}, title=title)


def show_npz(path, args):
    """Display a saved .npz archive directly (no solver run).

    The window title is ``args.title`` when the CLI ``--title`` override was
    given, otherwise :func:`window_title` of the archive path.
    """
    data = np.load(path, allow_pickle=True)
    title = window_title(path, args.title)
    if args.result in ("static", "modal") and (
        args.zlim or args.labels or args.node_labels or args.highlight_section
    ):
        print(
            "Note: --zlim / --labels / --node-labels / --highlight-section "
            "apply to --result mesh only."
        )
    if args.highlight_constraint:
        print(
            "Note: --highlight-constraint needs a .s2k model — NPZ archives "
            "carry geometry and results, but not CONSTRAINT DEFINITIONS / "
            "JOINT CONSTRAINT ASSIGNMENTS."
        )
    if args.select:
        print(
            "Note: --select needs a .s2k model — a Selection is resolved "
            "against section / material / group / elevation data that an NPZ "
            "archive does not carry."
        )
    if args.result == "static":
        plot_force_diagram(
            str(path), quantity=args.quantity, dimension=args.dimension, window_title=title
        )
    elif args.result == "modal":
        plot_mode_animation(data, None, mode=mode_index(args), scale=args.mode_scale, title=title)
    elif args.result == "rs":
        # Per-element response-spectrum forces require the rs/elem_* block,
        # which only archives written with element-level RS forces carry.
        if "rs/elem_sap_id" not in data:
            print(
                "This archive has no element-level RS forces (no rs/elem_* block).\n"
                "Write one with rs_element_forces=... (or "
                "'model.review --response-spectrum --rs-element-forces --npz ...')."
            )
        else:
            fig = plot_force_diagram(
                str(path),
                quantity=args.quantity,
                kind="rs",
                dimension=args.dimension,
                window_title=title,
            )
            # The 2D RS renderer returns a Figure without displaying it; the
            # 3D path shows its own window and returns None.
            if fig is not None and hasattr(fig, "show"):
                import matplotlib.pyplot as plt

                plt.show()
    else:
        # mesh / pushover / interactive - just show the archived geometry.
        plot_mesh(
            data,
            collapse_to_parents=True,
            title=title,
            **mesh_view_kwargs(args, _npz_section_names(data)),
        )


def load_model(path):
    """Load a model from a ``.s2k``, raw-table JSON or model-codec JSON file.

    Thin CLI wrapper over :func:`fea_toolkit.io.load_model_data` that turns the
    loader's exceptions into a message + exit code.

    Args:
        path: Path to the model file.

    Returns:
        A ``SAPModelData``, or the ``MeshModel`` itself for a codec snapshot of
        a post-preprocessing model (mesh view only; see
        :func:`check_result_supported`).

    Raises:
        SystemExit: If the file cannot be read, or is not a recognised model.
    """
    try:
        return load_model_data(path)
    except (OSError, ValueError) as exc:
        sys.exit(f"Error: {exc}")


def check_result_supported(model, result):
    """Reject a ``MeshModel`` snapshot for results that need a parsed model.

    A model-codec snapshot of a post-preprocessing ``MeshModel`` is already
    meshed: it can be *viewed* (``--result mesh``) but carries no SAP2000
    input for the analyses to build from.

    Args:
        model: The loaded model (``SAPModelData`` or ``MeshModel``).
        result: The requested ``--result`` value.

    Raises:
        SystemExit: If *model* is a ``MeshModel`` and *result* is not ``mesh``.
    """
    if isinstance(model, MeshModel) and result != "mesh":
        sys.exit(
            f"Error: MeshModel snapshots support --result mesh only (got {result!r}). "
            "A MeshModel is already meshed, so it carries no input for an analysis — "
            "use the .s2k or a SAPModelData snapshot instead."
        )


def run_s2k(md, args, title=None):
    """Run the requested analysis on a parsed model and display the result.

    Args:
        md: Parsed :class:`~fea_toolkit.model.sap_data.SAPModelData` (or a
            ``MeshModel`` snapshot, for ``--result mesh``).
        args: Parsed CLI namespace.
        title: PyVista window title for the windows opened from this run
            (see :func:`window_title`); ``None`` keeps PyVista's default.
    """
    print(f"Model units: {md.units}")
    print(
        f"Nodes: {len(md.nodes)}; frames: {len(md.frame_elements)}; areas: {len(md.area_elements)}"
    )
    print(f"Load patterns: {list(md.load_patterns.keys())}")

    mesh_only = (
        args.zlim
        or args.labels
        or args.node_labels
        or args.highlight_section
        or args.highlight_constraint
        or args.select
    )
    if args.result != "mesh" and mesh_only:
        print(
            "Note: --zlim / --labels / --node-labels / --highlight-section / "
            "--highlight-constraint / --select apply to --result mesh only."
        )

    if args.result == "mesh":
        kwargs = mesh_view_kwargs(args, set(md.sections))
        node_colors = constraint_node_colors(args, md)
        if node_colors:
            kwargs["node_colors"] = node_colors
        plot_mesh(
            md,
            collapse_to_parents=True,
            title=title,
            highlight_selection=selection_highlights(args),
            **kwargs,
        )
        return

    builder = build_builder(md, args.element_type)

    if args.result == "static":
        show_static(builder, md, args.quantity, args.scale, title)
    elif args.result == "modal":
        show_modal(builder, args.num_modes, mode_index(args), args.mode_scale, title)
    elif args.result == "rs":
        show_rs(
            builder,
            md,
            args.num_modes,
            args.alpha_max,
            args.tg,
            args.damping,
            args.scale,
            args.mode_scale,
            title,
        )
    elif args.result == "pushover":
        show_pushover(builder, md)
    elif args.result == "interactive":
        show_interactive(builder, md, title)


def main():
    parser = argparse.ArgumentParser(
        description="View a model or a saved results archive.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  %(prog)s model.s2k                                  # whole model\n"
            "  %(prog)s model.s2k -r mesh --labels                 # mesh view, frame IDs\n"
            "  %(prog)s model.s2k -r mesh --zlim 3.4 4.5 \\\n"
            "      --highlight-section 2xR3 2xR4                   # highlight braces in a band\n"
            "  %(prog)s model.s2k -r mesh \\\n"
            "      --highlight-constraint Fix --node-labels        # SAP constraint joints\n"
            "  %(prog)s model.s2k -r mesh \\\n"
            '      --select "type=Frame; section=2xR3"            # Selection overlay\n'
            "  %(prog)s results.npz -r static --quantity Mz        # saved archive\n"
            "  %(prog)s --sample -r modal --mode 1                 # built-in sample\n"
        ),
    )
    parser.add_argument(
        "model_file",
        nargs="?",
        help=(
            "Path to a .s2k model, a parsed-model .json (raw tables from "
            "SAP2000Parser.to_json() or a model_codec snapshot) or a .npz archive."
        ),
    )
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
    parser.add_argument("--quantity", default="Mz", help="Force quantity (static/NPZ/rs).")
    parser.add_argument(
        "--dimension",
        choices=("2d", "3d"),
        default=None,
        help=(
            "Force-diagram view: '2d' (quantity vs elevation) or '3d' "
            "(per-element tubes/flags).  Default: 2D for --result rs, "
            "auto (3D when PyVista is available) for static."
        ),
    )
    parser.add_argument(
        "--title",
        default=None,
        metavar="TITLE",
        help=(
            "PyVista window title, replacing the default "
            "'PyVista - <input file name>' (base name only, e.g. "
            "'PyVista - tower.s2k'; --sample gives 'PyVista - sample').  "
            'Applies to every window opened; use --title "" to clear it.  '
            "Not applied to --result pushover, which draws a Matplotlib "
            "figure rather than a PyVista window."
        ),
    )
    parser.add_argument(
        "--mode",
        type=int,
        default=1,
        help="Mode number for --result modal, 1-based (default: 1 = first mode).",
    )
    parser.add_argument("--num-modes", type=int, default=12, help="Number of modes.")
    parser.add_argument("--scale", type=float, default=50.0, help="Deformation magnification.")
    parser.add_argument(
        "--mode-scale",
        type=float,
        default=5.0,
        help=(
            "Mode-shape exaggeration for --result modal, as a percentage of the "
            "model's largest dimension (default: 5).  Mode shapes are "
            "mass-normalised by OpenSees, so their magnitudes are not "
            "displacements and are normalised to unit peak before scaling."
        ),
    )
    parser.add_argument(
        "--element-type",
        default="elasticBeamColumn",
        help="OpenSees element type (e.g. elasticBeamColumn, forceBeamColumn).",
    )
    parser.add_argument("--alpha-max", type=float, default=0.16, help="GB 50011 alpha_max (rs).")
    parser.add_argument("--tg", type=float, default=0.40, help="GB 50011 Tg (rs).")
    parser.add_argument("--damping", type=float, default=0.05, help="Damping ratio (rs).")
    parser.add_argument(
        "--zlim",
        type=float,
        nargs=2,
        metavar=("LO", "HI"),
        default=None,
        help="Mesh view: only draw elements whose midpoint lies in this Z band.",
    )
    parser.add_argument(
        "--labels",
        action="store_true",
        help="Mesh view: label frame (element) IDs.",
    )
    parser.add_argument(
        "--node-labels",
        action="store_true",
        help=(
            "Mesh view: label nodes.  Joints highlighted with "
            "--highlight-constraint are labelled with their SAP joint label, "
            "other nodes with their OpenSees tag."
        ),
    )
    parser.add_argument(
        "--highlight-constraint",
        nargs="+",
        metavar="NAME",
        default=None,
        help=(
            "Mesh view: draw the joints assigned to these SAP2000 constraint "
            "group(s) in red (any type — BODY, DIAPHRAGM, EQUAL, ...), e.g. "
            "--highlight-constraint Fix.  The same joints are selectable "
            "through --select 'constraint=Fix' (drawn yellow); this flag "
            "is the red-ink convenience.  Needs a .s2k model; NPZ archives "
            "do not carry the constraint tables."
        ),
    )
    parser.add_argument(
        "--select",
        action="append",
        metavar="EXPR",
        default=None,
        help=(
            "Mesh view: overdraw the elements matched by a Selection in "
            "yellow — wide half-opaque lines over frames, large dots over "
            "nodes.  EXPR is 'KEY=VALUE[,VALUE ...][; KEY=VALUE ...]' with "
            "keys " + SELECT_KEYS_HELP + ", e.g. --select 'type=Frame; "
            "section=2xR3,2xR4', --select 'id=10,11,12', --select "
            "'constraint=Fix' (joints of a SAP2000 constraint group) or "
            "--select 'z=3.4:4.5'.  Repeat --select to overlay several "
            "selections.  Needs a .s2k model."
        ),
    )
    parser.add_argument(
        "--highlight-section",
        nargs="+",
        metavar="SECTION",
        default=None,
        help=(
            "Mesh view: draw frames of these section name(s) in red and dim the "
            "rest, e.g. --highlight-section 2xR3 2xR4."
        ),
    )
    args = parser.parse_args()

    if not args.model_file and not args.sample:
        # No input given - show the full options instead of a bare message.
        parser.print_help()
        sys.exit("\nNo model file given - provide a .s2k / .npz path, or use --sample.")

    print(f"FEA Toolkit {__version__} / OpenSees {ops_version()}")

    if args.sample:
        from examples.sample_model import make_sample_model

        run_s2k(make_sample_model(), args, window_title(None, args.title))
        return

    path = Path(args.model_file)
    if not path.exists():
        sys.exit(f"Error: file not found - {path}")

    if path.suffix.lower() == ".npz":
        show_npz(path, args)
        return

    print(f"Loading: {path}")
    model = load_model(path)
    check_result_supported(model, args.result)
    run_s2k(model, args, window_title(path, args.title))


if __name__ == "__main__":
    main()
