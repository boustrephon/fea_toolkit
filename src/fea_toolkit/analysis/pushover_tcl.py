"""RC pushover via the Tcl/Xara backend (alternate to the OpenSeesPy path).

Orchestrates the ``use_tcl_fallback`` backend for reinforced-concrete
pushover:

1. Generate RC fiber Tcl via
   :func:`~fea_toolkit.opensees.recorder.export_mesh_model_to_tcl`.
2. Append :func:`~fea_toolkit.opensees.builder.pushover_tcl` commands with
   recorder output files.
3. Run via :class:`~fea_toolkit.opensees.recorder.XaraTclRunner`.
4. Parse recorder output files via
   :func:`~fea_toolkit.opensees.recorder.parse_pushover_results`.

Returns a plain ``(data, metadata)`` pair ready to be wrapped in an
:class:`~fea_toolkit.analysis.base.AnalysisResult` by
:func:`~fea_toolkit.analysis.pushover.run_pushover_analysis`.

Recorder output files (``*_disp.out``, ``*_bs.out``, ``*_reaction.out``)
are written alongside the Tcl script in the ``output/`` directory, which
is gitignored per project convention.
"""

from __future__ import annotations

import datetime
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

from fea_toolkit.opensees.builder import pushover_tcl
from fea_toolkit.opensees.recorder import (
    XaraTclRunner,
    export_mesh_model_to_tcl,
    parse_pushover_results,
)
from fea_toolkit.utils import g_from_units

if TYPE_CHECKING:
    from fea_toolkit.model.mesh_model import MeshModel


# ── Shared load helpers (Tcl path and future ReplayConcrete path) ──


def _find_control_node(mm: MeshModel) -> int:
    """Return the node tag of the topmost node (max Z)."""
    control_node = 1
    max_z = -1e12
    for nd in mm.nodes.values():
        if nd.z > max_z:
            max_z = nd.z
            control_node = nd.node_tag
    return control_node


def _build_lateral_loads(
    mm: MeshModel,
    lateral_load_type: str,
    dir_index: int,
    shapes: Optional[dict] = None,
) -> dict[int, tuple]:
    """Build nodal lateral load pattern (uniform / triangular / mode1).

    Parameters
    ----------
    mm : MeshModel
        Frozen topology from the Preprocessor.
    lateral_load_type : str
        One of ``'uniform'``, ``'triangular'``, ``'mode1'``.
    dir_index : int
        Lateral direction index: 0=X, 1=Y, 2=Z.
    shapes : dict, optional
        Mode-shape dict (mode index → {node_tag: (dx, dy, dz)}) used
        only by the ``'mode1'`` pattern.

    Returns
    -------
    dict
        ``{node_tag: (fx, fy, fz)}`` — normalized weights (not scaled
        by gravity or mass) for the Tcl ``pushover_tcl()`` helper.
    """
    lateral_loads: dict[int, tuple] = {}
    if lateral_load_type == "uniform":
        # Uniform: unit weights at all nodes
        for nd in mm.nodes.values():
            load = [0.0, 0.0, 0.0]
            load[dir_index] = 1.0
            lateral_loads[nd.node_tag] = tuple(load)
    elif lateral_load_type == "triangular":
        # Triangular: proportional to height above base
        heights = [nd.z for nd in mm.nodes.values()]
        min_z = min(heights) if heights else 0.0
        total_weight = 0.0
        for nd in mm.nodes.values():
            h = max(nd.z - min_z, 0.0)
            load = [0.0, 0.0, 0.0]
            load[dir_index] = h
            lateral_loads[nd.node_tag] = tuple(load)
            total_weight += h
        if total_weight > 1e-12:
            for tag, ld in lateral_loads.items():
                lateral_loads[tag] = tuple(v / total_weight for v in ld)
    elif lateral_load_type == "mode1" and shapes:
        # Mode 1 proportional: mode-shape component in lateral direction
        first_mode = shapes.get(1, shapes.get(0, {})) if shapes else {}
        total_weight = 0.0
        for nd in mm.nodes.values():
            mode_comp = first_mode.get(nd.node_tag, (1.0, 0.0, 0.0))
            w = abs(mode_comp[dir_index] if len(mode_comp) > dir_index else mode_comp[0])
            load = [0.0, 0.0, 0.0]
            load[dir_index] = w
            lateral_loads[nd.node_tag] = tuple(load)
            total_weight += w
        if total_weight > 0:
            for tag, ld in lateral_loads.items():
                lateral_loads[tag] = tuple(v / total_weight for v in ld)
    else:
        # Fallback: uniform in configured direction
        for nd in mm.nodes.values():
            load = [0.0, 0.0, 0.0]
            load[dir_index] = 1.0
            lateral_loads[nd.node_tag] = tuple(load)

    return lateral_loads


def _build_gravity_loads(mm: MeshModel) -> dict[int, tuple]:
    """Build nodal gravity loads from node masses and model-unit g.

    Uses :func:`~fea_toolkit.utils.g_from_units` for the unit-consistent
    gravitational acceleration — never hardcodes ``g``.
    """
    gravity_loads: dict[int, tuple] = {}
    g = g_from_units(mm.units)
    for nd in mm.nodes.values():
        mass_val = getattr(nd, "mass", None)
        if mass_val is None or mass_val <= 0.0:
            # Skip nodes without a valid mass rather than fabricating 1.0
            continue
        gravity_loads[nd.node_tag] = (0.0, 0.0, -mass_val * g)
    return gravity_loads


def _find_base_node_tags(mm: MeshModel) -> list[int]:
    """Return node tags of restrained (support) nodes."""
    base_node_tags: list[int] = []
    for nid, nd in mm.nodes.items():
        r = mm.restraints.get(nid)
        if r is not None and any(int(x) != 0 for x in r.dofs):
            base_node_tags.append(nd.node_tag)
    return base_node_tags or [1]


# ── Orchestration ──────────────────────────────────────────────────


def run_rc_pushover_tcl(
    mesh_model: MeshModel,
    modal_data: dict,
    config: Optional[dict] = None,
    *,
    lateral_load_type: str = "mode1",
    max_disp_val: float = 0.30,
    num_steps: int = 50,
) -> tuple[dict, dict]:
    """Run an RC pushover via the Tcl/Xara backend.

    Args:
        mesh_model: Frozen topology from the Preprocessor.
        modal_data: Modal result dict (a bare value is tolerated and
            wrapped in ``{"modal": ...}``).
        config: Merged RC config — user overrides over
            ``_PUSHOVER_RC_DEFAULTS`` with ``create_fiber_sections``
            forced on (see :func:`~fea_toolkit.analysis.pushover._build_rc_config`).
        lateral_load_type: One of ``'uniform'``, ``'triangular'``,
            ``'mode1'`` (default).
        max_disp_val: Maximum control displacement (model units).
        num_steps: Number of push increments.

    Returns:
        ``(data, metadata)`` — *data* is the parsed pushover result dict
        (an ``"error"`` key is set on failure) and *metadata* carries the
        ``tcl_path`` / ``output_dir`` (``None`` when cleaned up) plus the
        control settings for the caller's ``AnalysisResult``.
    """
    mm = mesh_model

    # ── Output directory ─────────────────────────────────────────
    out_dir = Path("output") / f"pushover_rc_{datetime.datetime.now():%Y%m%d_%H%M%S}"
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Control node and direction resolution ────────────────────
    control_node = _find_control_node(mm)

    # Determine direction index: X=0, Y=1, Z=2 (default X) for lateral loads
    dir_index = 0
    if (config or {}).get("direction") == "Y":
        dir_index = 1
    elif (config or {}).get("direction") == "Z":
        dir_index = 2

    # DOF for control node displacement (1=X, 2=Y, 3=Z) — follows dir_index+1
    control_dof = dir_index + 1

    # Build lateral load pattern from mode 1 shape or uniform
    if not isinstance(modal_data, dict):
        modal_data = {"modal": modal_data}
    modal_nested = modal_data.get("modal", modal_data)
    shapes = modal_nested.get("shapes", modal_nested.get("mode_shapes", {}))

    lateral_loads = _build_lateral_loads(mm, lateral_load_type, dir_index, shapes=shapes)

    # Gravity loads — use MeshModel's computed mass when available
    gravity_loads = _build_gravity_loads(mm)

    # RC config — already resolved by the dispatcher
    # (``_build_rc_config`` merges ``_PUSHOVER_RC_DEFAULTS`` over user
    # overrides and forces ``create_fiber_sections=True``).  Passed through
    # unchanged so the resolved values reach the Tcl exporter/runner.
    rc_config = config or {}

    output_prefix = "pushover_rc"

    # Determine base node tags from restrained nodes so the reaction
    # recorders monitor the actual supports (not an implicit node 1).
    base_node_tags = _find_base_node_tags(mm)

    # Generate Tcl suffix with recorder files — DOF matches direction.
    # ``output_prefix`` controls the recorder filenames so they match
    # the paths expected below (``pushover_rc_{disp,bs,reaction}.out``).
    tcl_suffix = pushover_tcl(
        control_node=control_node,
        dof=control_dof,
        max_disp=max_disp_val,
        num_steps=num_steps,
        lateral_loads=lateral_loads,
        gravity_loads=gravity_loads,
        adaptive=True,
        base_node_tags=base_node_tags,
        output_prefix=output_prefix,
    )

    # Write Tcl script to output directory
    tcl_path = str(out_dir / "model.tcl")
    export_mesh_model_to_tcl(
        mm,
        tcl_path,
        config=rc_config,
        tcl_suffix=tcl_suffix,
    )

    # Run via Xara — the runner sets cwd to the tcl file's directory,
    # so recorder output files are written alongside ``model.tcl``.
    runner = XaraTclRunner()
    ret, output = runner.run(tcl_path)

    # ── Validate return status ──
    if ret != 0:
        # Propagate failure without parsing outputs
        return (
            {
                "error": f"XaraTclRunner returned status {ret}",
                "output_raw": output,
                "output_dir": str(out_dir) if out_dir.exists() else None,
            },
            {
                "material_type": "rc",
                "lateral_load_type": lateral_load_type,
                "max_disp_val": max_disp_val,
                "num_steps": num_steps,
                "tcl_path": None,
                "output_dir": None,
                "config": config,
                "error": f"runner returned {ret}",
            },
        )

    # ── Parse recorder output files ─────────────────────────────
    disp_path = str(out_dir / f"{output_prefix}_disp.out")
    bs_path = str(out_dir / f"{output_prefix}_bs.out")
    # A single base node writes ``{prefix}_reaction.out``; multiple
    # base nodes write per-node ``{prefix}_reaction_{tag}.out`` files.
    # Pass the per-node paths to the parser so reactions are summed
    # across all bases (each file carries the push-direction reaction
    # for that base node, aggregated by matching recorded time steps).
    if len(base_node_tags) == 1:
        reaction_path: Optional[Union[str, list[str]]] = str(
            out_dir / f"{output_prefix}_reaction.out"
        )
    else:
        reaction_path = [
            str(out_dir / f"{output_prefix}_reaction_{tag}.out") for tag in base_node_tags
        ]

    def _safe_list(arr, default=None):
        """Convert optional array-like to list; return empty list if missing/empty."""
        if arr is None:
            return default if default is not None else []
        try:
            lst = arr.tolist()
            if lst is None:
                return default if default is not None else []
            return lst
        except (AttributeError, ValueError, TypeError):
            return default if default is not None else []

    def _safe_scalar(arr, default=0.0):
        """Extract first scalar from optional array-like; return default if missing/empty."""
        if arr is None:
            return default
        try:
            flat = arr.flatten()
            if flat.size == 0:
                return default
            return float(flat[0])
        except (AttributeError, ValueError, IndexError, TypeError):
            return default

    result = {}
    if os.path.exists(disp_path) and os.path.exists(bs_path):
        try:
            if reaction_path is None:
                reaction_arg = None
            elif isinstance(reaction_path, str):
                reaction_arg = reaction_path if os.path.exists(reaction_path) else None
            else:
                existing = [p for p in reaction_path if os.path.exists(p)]
                reaction_arg = list(existing) if existing else None
            parsed = parse_pushover_results(
                disp_path,
                bs_path,
                reaction_arg,
                dof=control_dof - 1,
            )
            result = {
                "control_disp": _safe_list(parsed.get("control_disp")),
                "base_shear": _safe_list(parsed.get("base_shear")),
                "step": _safe_list(parsed.get("step")),
                "base_rx": _safe_scalar(parsed.get("base_rx")),
                "base_ry": _safe_scalar(parsed.get("base_ry")),
                "base_rz": _safe_scalar(parsed.get("base_rz")),
                "output_raw": output,
                "output_dir": str(out_dir),
            }
            if "reaction_rx" in parsed:
                result["reaction_rx"] = _safe_list(parsed.get("reaction_rx"))
        except Exception as exc:
            result = {"error": str(exc), "output_raw": output}
    else:
        # Fallback: try stdout parsing as before
        import re

        rx = ry = rz = 0.0
        for line in output.splitlines():
            if "Base reactions:" in line:
                m = re.search(r"Rx\s*=\s*([-\d.e+]+)", line)
                if m:
                    rx = float(m.group(1))
                m = re.search(r"Ry\s*=\s*([-\d.e+]+)", line)
                if m:
                    ry = float(m.group(1))
                m = re.search(r"Rz\s*=\s*([-\d.e+]+)", line)
                if m:
                    rz = float(m.group(1))
        result = {
            "base_reactions": {"rx": rx, "ry": ry, "rz": rz},
            "output_raw": output,
            "output_dir": str(out_dir),
        }

    # Determine post-cleanup state for metadata
    out_dir_removed = False
    tcl_path_removed = False
    if not rc_config.get("keep_tcl", False) and not rc_config.get("keep_output", False):
        try:
            shutil.rmtree(str(out_dir), ignore_errors=True)
            out_dir_removed = True
            tcl_path_removed = True
        except OSError:
            pass

    metadata = {
        "material_type": "rc",
        "lateral_load_type": lateral_load_type,
        "max_disp_val": max_disp_val,
        "num_steps": num_steps,
        "tcl_path": None if tcl_path_removed else (tcl_path if os.path.exists(tcl_path) else None),
        "output_dir": None if out_dir_removed else (str(out_dir) if out_dir.exists() else None),
        "config": config,
    }
    return result, metadata
