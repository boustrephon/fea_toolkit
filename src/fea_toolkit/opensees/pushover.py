"""
Pushover analysis orchestration — multi-direction, multi-pattern.
"""

from typing import Optional

import numpy as np


def run_pushover_4dir(
    mesh_model,
    modal_result: dict,
    gravity_patterns: dict[str, float] = None,
    lateral_load_type: str = "uniform",
    max_disp_val: float = 0.30,
    num_steps: int = 50,
    tg: float = 0.25,
    alpha_max_rare: float = 0.50,
    g: float = 9.81,
    zeta: float = 0.05,
    verbose: bool = False,
    brace_type: str = "truss",
    brace_sections: Optional[list] = None,
    rs_modal_base_shear: Optional[dict[str, list[float]]] = None,
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
    g : float
        Gravitational acceleration (m/s²).
    zeta : float
        Damping ratio.
    verbose : bool
        Print progress.
    brace_type : str
        ``'truss'`` (Hysteretic) or ``'beam'`` (subdivided).
    brace_sections : list, optional
        Section names to treat as braces.
    rs_modal_base_shear : dict, optional
        Per-direction RS base shear for mode validation
        ``{"X": [...], "Y": [...]}``.

    Returns
    -------
    dict
        ``{direction: {"results": ..., "adrs": ..., "pp": ..., ...}}``.
    """
    from fea_toolkit.model.csm import check_modal_pushover_mode
    from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
    from fea_toolkit.spectrum import _gb50011_spectrum

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

    gamma = 0.9 + (0.05 - zeta) / (0.3 + 6.0 * zeta)
    eta_1 = max(0.0, 0.02 + (0.05 - zeta) / (4.0 + 32.0 * zeta))
    eta_2 = max(0.55, 1.0 + (0.05 - zeta) / (0.08 + 1.6 * zeta))

    roof_node = max(mesh_model.nodes.values(), key=lambda n: n.z)
    roof_tag = roof_node.node_tag

    T_spec = np.linspace(0.0, 6.0, 200).tolist()
    Sa_spec = _gb50011_spectrum(
        T_spec, alpha_max_rare, tg, gamma=gamma, eta1=eta_1, eta2=eta_2, g=g
    ).tolist()
    roof_tag = roof_node.node_tag

    T_spec = np.linspace(0.0, 6.0, 200).tolist()
    Sa_spec = _gb50011_spectrum(
        T_spec, alpha_max_rare, tg, gamma=gamma, eta1=eta_1, eta2=eta_2, g=g
    ).tolist()

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
        try:
            mp = modal.get("modal_props", {})
            dir_key = "partiMassRatiosMX" if cfg["dir"] == "X" else "partiMassRatiosMY"
            ratios = mp.get(dir_key, [])
            if ratios:
                best_mode_idx = int(np.argmax(np.abs(ratios)))
        except Exception:
            pass

        ab = AnalysisBuilder(mesh_model, builder_cfg)

        results = ab.run_pushover_analysis(
            gravity_patterns=gravity_patterns,
            lateral_load_type=lateral_load_type,
            lateral_direction=cfg["dir"],
            control_node_tag=roof_tag,
            max_disp=cfg["disp"],
            num_steps=num_steps,
            mode_shapes=shapes if lateral_load_type == "mode1" else None,
            mode_index=best_mode_idx,
            print_progress=False,
        )

        adrs = ab.pushover_to_adrs(results, modal, shapes, direction=cfg["dir"], g=g)
        pp = ab.compute_performance_point(
            results,
            modal,
            shapes,
            T_spec,
            Sa_spec,
            direction=cfg["dir"],
            g=g,
        )

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
