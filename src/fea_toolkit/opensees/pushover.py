"""
Pushover analysis orchestration — multi-direction, multi-pattern.
"""

import logging
from typing import Optional

import numpy as np

from ..spectrum import ResponseSpectrum
from ..utils import g_from_units

logger = logging.getLogger(__name__)


def _select_control_node(mesh_model, control_node_tag=None, control_node_id=None):
    """Pick the pushover control node.

    An explicit ``control_node_tag`` (OpenSees integer tag) or
    ``control_node_id`` (SAP string label) wins when given.  Otherwise the
    default is the node nearest the plan centroid of the highest
    *significant* floor level — a naive ``max(z)`` selection can land on an
    isolated rooftop appendage (e.g. a penthouse holding < 25 % of the node
    count of the floor below), whose local flexibility distorts the
    capacity curve and the CSM/ADRS conversion.

    Returns:
        The chosen :class:`~fea_toolkit.model.sap_data.Node`.
    """
    nodes = list(mesh_model.nodes.values())
    if not nodes:
        raise ValueError("cannot select control node — model has no nodes")

    if control_node_tag is not None:
        for n in nodes:
            if n.node_tag == int(control_node_tag):
                return n
        raise ValueError(f"control_node_tag {control_node_tag} not found in the mesh model")
    if control_node_id is not None:
        n = mesh_model.nodes.get(str(control_node_id))
        if n is None:
            raise ValueError(f"control_node_id {control_node_id} not found in the mesh model")
        return n

    levels: dict[float, list] = {}
    for n in nodes:
        levels.setdefault(round(n.z, 3), []).append(n)
    ordered = sorted(levels.items(), key=lambda kv: -kv[0])
    max_count = max(len(v) for v in levels.values())
    chosen = None
    for _z, level_nodes in ordered:
        # Skip appendage levels (very few nodes relative to the busiest
        # floor).  The guard `>= max(4, ...)` keeps tiny flat models safe.
        if len(level_nodes) >= max(4, 0.25 * max_count):
            chosen = level_nodes
            break
    if chosen is None:
        chosen = ordered[0][1]
    cx = sum(n.x for n in chosen) / len(chosen)
    cy = sum(n.y for n in chosen) / len(chosen)
    return min(chosen, key=lambda n: (n.x - cx) ** 2 + (n.y - cy) ** 2)


def run_pushover_4dir(
    mesh_model,
    modal_result: dict,
    gravity_patterns: Optional[dict[str, float]] = None,
    lateral_load_type: str = "uniform",
    max_disp_val: float = 0.30,
    num_steps: int = 50,
    tg: float = 0.25,
    alpha_max_rare: float = 0.50,
    zeta: float = 0.05,
    verbose: bool = False,
    brace_type: str = "truss",
    brace_sections: Optional[list] = None,
    rs_modal_base_shear: Optional[dict[str, list[float]]] = None,
    spectrum: Optional[ResponseSpectrum] = None,
    bilinearize_method: str = "composite",
    bilinearize_config: Optional[dict] = None,
    control_node_tag: Optional[int] = None,
    control_node_id: Optional[str] = None,
) -> dict:
    """Run pushover in all 4 directions with CSM (two-stage path).

    The topology work (Preprocessor) is already done — each direction
    creates a lightweight ``AnalysisBuilder`` from the shared
    *mesh_model*.

    Parameters
    ----------
    mesh_model : MeshModel
        Shared MeshModel from Preprocessor.
    modal_result : dict
        Dict with ``'modal'`` and ``'shapes'`` keys.
    gravity_patterns : dict, optional
        Dict mapping pattern name → scale factor.
    lateral_load_type : str
        One of ``'uniform'``, ``'triangular'``, ``'mode1'``.
    max_disp_val : float
        Maximum control displacement magnitude (m).
    num_steps : int
        Number of push increments.
    tg : float
        Characteristic period for GB 50011 spectrum (s).
    alpha_max_rare : float
        Seismic influence coefficient (rare event).
    zeta : float
        Damping ratio.
    spectrum : ResponseSpectrum, optional
        Pre-computed demand spectrum (T/Sa).  When ``None`` a GB 50011
        rare-event spectrum is built from *tg* / *alpha_max_rare*.
    bilinearize_method : str
        Bilinearisation method for the CSM performance point:
        ``'composite'`` (default), ``'stiffness_change'``,
        ``'equal_energy'``, or ``'rc'`` / ``'de_luca_10pct'`` (the
        De Luca 10 %-secant rule for curved RC backbones).
    bilinearize_config : dict, optional
        Optional dict passed to the bilinearisation function.
    verbose : bool
        Print progress.
    brace_type : str
        ``'truss'`` (Hysteretic) or ``'beam'`` (subdivided).
    brace_sections : list, optional
        Section names to treat as braces.
    rs_modal_base_shear : dict, optional
        Per-direction RS base shear for mode validation
        ``{"X": [...], "Y": [...]}``.
    control_node_tag : int, optional
        OpenSees node tag to use as the displacement-control node.  When
        ``None`` the node nearest the plan centroid of the highest
        significant floor level is selected (a naive ``max(z)`` can pick an
        isolated rooftop appendage).
    control_node_id : str, optional
        SAP2000 node ID (string label) for the control node, alternative
        to *control_node_tag*.

    Returns
    -------
    dict
        ``{direction: {"results": ..., "adrs": ..., "pp": ..., ...}}``.
    """
    from fea_toolkit.model.csm import check_modal_pushover_mode
    from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

    if gravity_patterns is None:
        gravity_patterns = {"DEAD": 1.0, "DEAD SDL": 1.0, "LL": 0.5}

    modal = modal_result["modal"]
    shapes = modal_result["shapes"]

    dirs = ["+X", "-X", "+Y", "-Y"]
    dir_cfg = {
        "+X": {"dir": "X", "disp": max_disp_val},
        "-X": {"dir": "X", "disp": -max_disp_val},
        "+Y": {"dir": "Y", "disp": max_disp_val},
        "-Y": {"dir": "Y", "disp": -max_disp_val},
    }

    # Derive gravity acceleration from the model's unit system.
    g = g_from_units(mesh_model.units)

    roof_node = _select_control_node(
        mesh_model, control_node_tag=control_node_tag, control_node_id=control_node_id
    )
    roof_tag = roof_node.node_tag

    if spectrum is None:
        spectrum = ResponseSpectrum.from_gb50011(
            alpha_max=alpha_max_rare,
            tg=tg,
            zeta=zeta,
            g=g,
            description="GB 50011 rare fallback",
        )
    T_spec = spectrum.T
    Sa_spec = spectrum.Sa

    if brace_type == "truss":
        builder_cfg = {
            "element_type": "elasticBeamColumn",
            "split_elements": True,
            "brace_truss": True,
            "verbose": False,
        }
        if brace_sections is not None:
            builder_cfg["brace_sections"] = brace_sections
    else:
        builder_cfg = {
            "element_type": "dispBeamColumn",
            "beam_integration": "Lobatto",
            "num_int_pts": 3,
            "split_elements": False,
            "subdivide_braces": True,
            "brace_n_segments": 4,
            "brace_imperfection_ratio": 0.001,
            "verbose": False,
        }

    all_out = {}
    for label in dirs:
        cfg = dir_cfg[label]
        if verbose:
            print(f"  Pushover {label} (brace_type={brace_type}) ...")

        # Determine best mode for this push direction
        best_mode_idx = 0
        # Initialise before the try block so the later mode1 validation
        # can safely reference ``ratios`` even when modal-property
        # extraction fails (mirrors pushover_rc_openseespy).
        ratios = []
        try:
            mp = modal.get("modal_props", {})
            dir_key = "partiMassRatiosMX" if cfg["dir"] == "X" else "partiMassRatiosMY"
            ratios = mp.get(dir_key, [])
            if ratios:
                best_mode_idx = int(np.argmax(np.abs(ratios)))
        except Exception:
            pass

        ab = AnalysisBuilder(mesh_model, builder_cfg)

        try:
            results = ab.run_pushover_analysis(
                gravity_patterns=gravity_patterns,
                lateral_load_type=lateral_load_type,
                lateral_direction=cfg["dir"],
                control_node_tag=roof_tag,
                max_disp=cfg["disp"],
                num_steps=num_steps,
                mode_shapes=shapes if lateral_load_type == "mode1" else None,
                mode_index=best_mode_idx,
                print_progress=verbose,
            )
        except RuntimeError as e:
            logger.warning("Pushover %s skipped — gravity analysis failed: %s", label, e)
            continue

        # A degenerate capacity curve (e.g. gravity produced too few valid
        # steps) must skip the direction, not abort the whole 4-direction
        # run — mirrors the gravity-failure skip above.
        try:
            adrs = ab.pushover_to_adrs(results, modal, shapes, direction=cfg["dir"])
            pp = ab.compute_performance_point(
                results,
                modal,
                shapes,
                T_spec,
                Sa_spec,
                direction=cfg["dir"],
                bilinearize_method=bilinearize_method,
                bilinearize_config=bilinearize_config,
            )
        except ValueError as e:
            logger.warning("Pushover %s skipped — no valid capacity spectrum: %s", label, e)
            continue

        # Validate mode selection against RS
        rs_warning = None
        if lateral_load_type == "mode1" and rs_modal_base_shear is not None:
            rs_list = rs_modal_base_shear.get(cfg["dir"])
            if rs_list:
                _, _rs_dom, rs_warning = check_modal_pushover_mode(
                    cfg["dir"],
                    ratios or [],
                    rs_list,
                )
                if rs_warning and verbose:
                    print(f"    {rs_warning}")

        all_out[label] = {
            "results": results,
            "adrs": adrs,
            "pp": pp,
            "mode_index": best_mode_idx,
            "rs_warning": rs_warning,
        }

    return all_out


def pushover_rc_openseespy(
    mesh_model,
    modal_result: dict,
    *,
    directions: str = "4dir",
    gravity_patterns: Optional[dict[str, float]] = None,
    lateral_load_type: str = "uniform",
    max_disp: float = 0.30,
    num_steps: int = 50,
    tg: float = 0.25,
    alpha_max_rare: float = 0.50,
    zeta: float = 0.05,
    config: Optional[dict] = None,
    verbose: bool = False,
    rs_modal_base_shear: Optional[dict[str, list[float]]] = None,
    spectrum: Optional[ResponseSpectrum] = None,
    node_mass_overrides: Optional[dict[str, float]] = None,
    return_builders: bool = False,
    control_node_tag: Optional[int] = None,
    control_node_id: Optional[str] = None,
) -> dict:
    """Run RC pushover in one or all 4 directions (OpenSeesPy path).

    Mirrors :func:`run_pushover_4dir` but uses the reinforced-concrete
    builder defaults (``_PUSHOVER_RC_DEFAULTS``) — force-based fiber
    frame elements and optional nonlinear shell walls.  Each direction
    creates a lightweight ``AnalysisBuilder`` from the shared
    *mesh_model*.

    Parameters
    ----------
    mesh_model : MeshModel
        Shared MeshModel from Preprocessor (frozen topology).
    modal_result : dict
        Dict with ``'modal'`` and ``'shapes'`` keys.
    directions : str
        One of ``'4dir'`` (default), ``'+X'``, ``'-X'``, ``'+Y'``,
        ``'-Y'``.  ``'4dir'`` runs all four; a single label runs only
        that direction.
    gravity_patterns : dict, optional
        Dict mapping pattern name → scale factor.
    lateral_load_type : str
        One of ``'uniform'``, ``'triangular'``, ``'mode1'``.
    max_disp : float
        Maximum control displacement magnitude (m).
    num_steps : int
        Number of push increments.
    tg : float
        Characteristic period for GB 50011 spectrum (s).
    alpha_max_rare : float
        Seismic influence coefficient (rare event).
    zeta : float
        Damping ratio.
    spectrum : ResponseSpectrum, optional
        Pre-computed demand spectrum (T/Sa).  When ``None`` a GB 50011
        rare-event spectrum is built from *tg* / *alpha_max_rare*.
    config : dict, optional
        RC builder config overrides merged over
        ``_PUSHOVER_RC_DEFAULTS`` — e.g. ``beam_integration``,
        ``nd_materials``, ``shell_layers``, ``rebar_Fy_override``.
        CSM-level option: ``bilinearize_method`` (default
        ``'composite'``; ``'rc'`` / ``'de_luca_10pct'`` selects the
        De Luca 10 %-secant rule for curved RC backbones) and
        ``bilinearize_config`` (dict passed to the bilinearisation
        function).
    verbose : bool
        Print progress.
    rs_modal_base_shear : dict, optional
        Per-direction RS base shear for mode validation
        ``{"X": [...], "Y": [...]}``.
    node_mass_overrides : dict, optional
        Mapping of node ID (SAP string label) → mass scale factor.
        Applied after computed seismic masses are assigned, allowing
        per-storey masonry/mass adjustments.
    return_builders : bool, optional
        When ``True``, each per-direction output dict also contains a
        ``"builder"`` key holding the ``AnalysisBuilder`` instance so
        callers can export recorded per-step results with
        ``AnalysisBuilder.export_pushover_results()``.
    control_node_tag : int, optional
        OpenSees node tag to use as the displacement-control node.  When
        ``None`` the node nearest the plan centroid of the highest
        significant floor level is selected (a naive ``max(z)`` can pick an
        isolated rooftop appendage).
    control_node_id : str, optional
        SAP2000 node ID (string label) for the control node, alternative
        to *control_node_tag*.

    Returns
    -------
    dict
        ``{direction: {"results": ..., "adrs": ..., "pp": ..., ...}}``.
        When *return_builders* is ``True``, each value additionally
        contains ``"builder"``.
    """
    from fea_toolkit.model.csm import check_modal_pushover_mode
    from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

    from ..analysis.base import _PUSHOVER_RC_DEFAULTS

    if gravity_patterns is None:
        gravity_patterns = {"DEAD": 1.0, "DEAD SDL": 1.0, "LL": 0.5}

    modal = modal_result["modal"]
    shapes = modal_result["shapes"]

    dirs = ["+X", "-X", "+Y", "-Y"]
    if directions == "4dir":
        dir_labels = dirs
    else:
        if directions not in dirs:
            raise ValueError(f"directions must be '4dir' or one of {dirs}")
        dir_labels = [directions]

    dir_cfg = {
        "+X": {"dir": "X", "disp": max_disp},
        "-X": {"dir": "X", "disp": -max_disp},
        "+Y": {"dir": "Y", "disp": max_disp},
        "-Y": {"dir": "Y", "disp": -max_disp},
    }

    # Derive gravity acceleration from the model's unit system.
    g = g_from_units(mesh_model.units)

    roof_node = _select_control_node(
        mesh_model, control_node_tag=control_node_tag, control_node_id=control_node_id
    )
    roof_tag = roof_node.node_tag

    if spectrum is None:
        spectrum = ResponseSpectrum.from_gb50011(
            alpha_max=alpha_max_rare,
            tg=tg,
            zeta=zeta,
            g=g,
            description="GB 50011 rare fallback",
        )
    T_spec = spectrum.T
    Sa_spec = spectrum.Sa

    # RC-specific builder config: framework defaults + user overrides.
    builder_cfg = dict(_PUSHOVER_RC_DEFAULTS)
    if config is not None:
        builder_cfg.update(config)

    all_out = {}
    for label in dir_labels:
        cfg = dir_cfg[label]
        if verbose:
            print(f"  Pushover {label} (RC, element_type={builder_cfg['element_type']}) ...")

        # Determine best mode for this push direction
        best_mode_idx = 0
        ratios = []
        try:
            mp = modal.get("modal_props", {})
            dir_key = "partiMassRatiosMX" if cfg["dir"] == "X" else "partiMassRatiosMY"
            ratios = mp.get(dir_key, [])
            if ratios:
                best_mode_idx = int(np.argmax(np.abs(ratios)))
        except Exception:
            pass

        ab = AnalysisBuilder(mesh_model, builder_cfg)

        try:
            results = ab.run_pushover_analysis(
                gravity_patterns=gravity_patterns,
                lateral_load_type=lateral_load_type,
                lateral_direction=cfg["dir"],
                control_node_tag=roof_tag,
                max_disp=cfg["disp"],
                num_steps=num_steps,
                mode_shapes=shapes if lateral_load_type == "mode1" else None,
                mode_index=best_mode_idx,
                print_progress=verbose,
                node_mass_overrides=node_mass_overrides,
            )
        except RuntimeError as e:
            logger.warning("Pushover %s skipped — gravity analysis failed: %s", label, e)
            continue

        # A degenerate capacity curve (e.g. gravity produced too few valid
        # steps) must skip the direction, not abort the whole 4-direction
        # run — mirrors the gravity-failure skip above.
        try:
            adrs = ab.pushover_to_adrs(results, modal, shapes, direction=cfg["dir"])
            pp = ab.compute_performance_point(
                results,
                modal,
                shapes,
                T_spec,
                Sa_spec,
                direction=cfg["dir"],
                bilinearize_method=(config or {}).get("bilinearize_method", "composite"),
                bilinearize_config=(config or {}).get("bilinearize_config"),
            )
        except ValueError as e:
            logger.warning("Pushover %s skipped — no valid capacity spectrum: %s", label, e)
            continue

        # Validate mode selection against RS
        rs_warning = None
        if lateral_load_type == "mode1" and rs_modal_base_shear is not None:
            rs_list = rs_modal_base_shear.get(cfg["dir"])
            if rs_list:
                _, _rs_dom, rs_warning = check_modal_pushover_mode(
                    cfg["dir"],
                    ratios or [],
                    rs_list,
                )
                if rs_warning and verbose:
                    print(f"    {rs_warning}")

        all_out[label] = {
            "results": results,
            "adrs": adrs,
            "pp": pp,
            "mode_index": best_mode_idx,
            "rs_warning": rs_warning,
        }
        if return_builders:
            all_out[label]["builder"] = ab

    return all_out
