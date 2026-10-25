"""SAC A4 NLTHA matrix runner with content-addressed result caching.

Runs the ten Ohtori et al. (2004) benchmark cases for a SAC building (3/9/20)
against a chosen hinge formulation, extracts the benchmark peak-response
quantities (roof displacement, ``d_max`` drift ratio, ``xdd_max`` floor
acceleration, ``F_max`` whole-building base shear, ``N_d`` yielded hinges) and
caches each run's result as an NPZ keyed on the case parameters, the record
bytes and the benchmark-relevant source files — so a case is only re-executed
when one of its inputs changes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

import numpy as np

from . import sac
from .sac_response_data import CASE_ORDER

#: The four benchmark records live in the non-redistributable local mirror
#: (``data/ground_motion/local/<id>.csv``), extracted by
#: ``scripts/fetch_ground_motions.py`` — see ``ground_motion_data.md``.
_RECORD_EXT = ".csv"


def _repo_root() -> Path:
    """The repository root, derived from this file's location (never the CWD)."""
    return Path(__file__).resolve().parent.parent.parent.parent


def record_path(record_id: str) -> Path:
    """Resolve a benchmark record's on-disk CSV path in the local mirror."""
    return _repo_root() / "data" / "ground_motion" / "local" / f"{record_id}{_RECORD_EXT}"


#: Benchmark-relevant source files whose bytes seed the cache fingerprint.  A
#: change to any of them invalidates every cached case (prevents stale results).
_CODE_FILES = (
    "benchmarks/sac.py",
    "benchmarks/sac_member_data.py",
    "benchmarks/sac_response_data.py",
    "opensees/_elements.py",
    "opensees/_runner_dynamic.py",
)


def _code_fingerprint() -> str:
    """``sha256`` of the benchmark-relevant source files."""
    h = hashlib.sha256()
    for rel in _CODE_FILES:
        h.update((_repo_root() / "src" / "fea_toolkit" / rel).read_bytes())
    return h.hexdigest()


def case_fingerprint(
    building: int,
    record_id: str,
    intensity: float,
    hinge_model: str,
    backbone: str,
    dt: float,
    damping: float,
) -> str:
    """``sha256`` over the case inputs (parameters + record bytes + code)."""
    payload = {
        "building": building,
        "record_id": record_id,
        "intensity": intensity,
        "hinge_model": hinge_model,
        "backbone": backbone,
        "dt": dt,
        "damping": damping,
        "code": _code_fingerprint(),
        "record_sha": hashlib.sha256(record_path(record_id).read_bytes()).hexdigest(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def floor_levels(builder: Any) -> list[tuple[float, int]]:
    """Sorted ``(z, representative_node_tag)`` per distinct storey elevation.

    The representative node per elevation is the highest-tagged node there — a
    deterministic choice (the rigid floor diaphragms / rigid-axial beams tie the
    floor's X DOF, so any node at the elevation carries the floor's lateral
    displacement).
    """
    levels: dict[float, int] = {}
    for nd in builder.mesh_model.nodes.values():
        z = round(float(nd.z), 6)
        levels[z] = max(levels.get(z, -1), int(nd.node_tag))
    return sorted(levels.items())


def compute_metrics(
    res: dict[str, Any],
    accel: np.ndarray,
    levels: list[tuple[float, int]],
) -> dict[str, Any]:
    """Extract the benchmark peak-response quantities from a transient result.

    ``accel`` is the (intensity-scaled) ground acceleration series the runner
    integrated, sampled at the integration step; ``levels`` is the output of
    :func:`floor_levels`.  The absolute floor acceleration is reconstructed as
    ``a_rel + ag`` (``nodeAccel`` returns the *relative* acceleration under
    ``UniformExcitation``), matching the runner's base-shear convention.
    """
    n = int(res.get("converged_steps", 0))
    zs = np.array([z for z, _ in levels], dtype=float)
    tags = [t for _, t in levels]

    # Per-level X displacement histories (n, nlev).
    u = np.column_stack(
        [np.asarray(res["displacements"].get(t, np.zeros((n, 3)))[:n, 0]) for t in tags]
    )

    # Ground acceleration at each converged step (sample index step + 1),
    # zero once that index is beyond the series — the runner's convention.
    ag = np.zeros(n)
    m = min(n, len(accel) - 1)
    ag[:m] = accel[1 : 1 + m]

    # Per-level absolute acceleration histories (n, nlev).
    xdd = np.column_stack(
        [np.asarray(res["accelerations"].get(t, np.zeros((n, 3)))[:n, 0]) + ag for t in tags]
    )

    roof_disp = float(np.max(np.abs(u[:, -1]))) if n else 0.0
    d_max = 0.0
    for i in range(1, u.shape[1]):
        h = zs[i] - zs[i - 1]
        if h > 0.0:
            d_max = max(d_max, float(np.max(np.abs(u[:, i] - u[:, i - 1])) / h))
    xdd_max = float(np.max(np.abs(xdd))) if n else 0.0

    return {
        "roof_disp": roof_disp,
        "d_max": d_max,
        "xdd_max": xdd_max,
        "F_max_frame": float(res.get("peak_base_shear", 0.0)),
        "F_max": 2.0 * float(res.get("peak_base_shear", 0.0)),
        "N_d": int(res.get("num_yielded_hinges", 0)),
        "peak_plastic_rotation": float(res.get("peak_plastic_rotation", 0.0)),
        "max_dissipated_energy": float(res.get("max_dissipated_energy", 0.0)),
        "converged_steps": n,
        "num_steps": int(len(accel)),
        "period_1": float(res.get("period_1", 0.0) or 0.0),
        "period_2": float(res.get("period_2", 0.0) or 0.0),
    }


def _rayleigh_periods(builder: Any, building: int) -> tuple[Optional[float], Optional[float]]:
    """Rayleigh anchor periods — mode 1 + mode 2 (3/9-story) or mode 5 (20-story)."""
    if building != 20:
        return None, None  # the runner's default 2-mode solve suffices
    modal = builder.run_modal_analysis(
        num_modes=5, print_results=False, eigen_solver="fullGenLapack"
    )
    periods = list(modal.get("periods", []))
    if len(periods) < 5:
        return None, None
    return float(periods[0]), float(periods[4])


def _cache_dir() -> Path:
    return _repo_root() / "docs" / "assets" / "sac_nltha"


def _case_path(
    building: int, record_id: str, intensity: float, hinge_model: str, backbone: str
) -> Path:
    return _cache_dir() / f"sac_b{building}_{record_id}_{intensity:g}_{hinge_model}_{backbone}.npz"


def run_case(
    building: int,
    record_id: str,
    intensity: float,
    *,
    hinge_model: str = "beam_with_hinges",
    backbone: str = "trilinear",
    dt: float = sac.SAC_DT_CAL,
    damping: float = 0.02,
    force: bool = False,
    cache: bool = True,
) -> dict[str, Any]:
    """Run (or load from cache) one benchmark case and return its metrics.

    The result is cached as an NPZ whose stored fingerprint is compared to the
    freshly computed one — so a cached case is reused only when none of its
    inputs changed.

    Returns:
        A dict of peak-response metrics (see :func:`compute_metrics`) plus the
        ``_fingerprint`` / ``_building`` / ``_record`` / ``_intensity`` /
        ``_hinge_model`` / ``_backbone`` provenance keys.
    """
    accel_raw, dt = sac.load_benchmark_record(str(record_path(record_id)), dt)
    accel = np.asarray(accel_raw, dtype=float) * intensity

    fingerprint = case_fingerprint(
        building, record_id, intensity, hinge_model, backbone, dt, damping
    )
    out_path = _case_path(building, record_id, intensity, hinge_model, backbone)

    if not force and out_path.exists():
        with np.load(out_path, allow_pickle=False) as data:
            if str(data["_fingerprint"].item()) == fingerprint:
                return _metrics_from_npz(data)

    from openseespy.opensees import wipe

    wipe()
    try:
        builder = sac.build_builder(
            building=building, nonlinear=True, backbone=backbone, hinge_model=hinge_model
        )
        levels = floor_levels(builder)
        tags = [t for _, t in levels]
        period_1, period_2 = _rayleigh_periods(builder, building)

        # Record only the representative floor nodes (limit memory) but keep
        # their velocity/acceleration so ``xdd_max`` can be reconstructed.
        builder.config["transient_record_node_tags"] = tags
        builder.config["transient_record_kinematics"] = True
        res = builder.run_transient_analysis(
            accel,
            dt,
            direction="X",
            damping_ratio=damping,
            period_1=period_1,
            period_2=period_2,
        )
    finally:
        wipe()

    metrics = compute_metrics(res, accel, levels)
    converged = metrics["converged_steps"] == metrics["num_steps"]
    metrics["_converged"] = bool(converged)
    metrics["_fingerprint"] = fingerprint
    metrics["_building"] = building
    metrics["_record"] = record_id
    metrics["_intensity"] = intensity
    metrics["_hinge_model"] = hinge_model
    metrics["_backbone"] = backbone

    # Only cache fully-converged runs — a case that broke early (the 9/20-story
    # nonlinear models do) must not be persisted as if it were a valid result.
    if cache and converged:
        _cache_dir().mkdir(parents=True, exist_ok=True)
        _save_npz(out_path, metrics, res, levels)

    return metrics


def _metrics_from_npz(data: Any) -> dict[str, Any]:
    """Rebuild the metrics dict (the scalar keys) from a loaded NPZ."""
    scalar_keys = [
        "roof_disp",
        "d_max",
        "xdd_max",
        "F_max_frame",
        "F_max",
        "N_d",
        "peak_plastic_rotation",
        "max_dissipated_energy",
        "converged_steps",
        "num_steps",
        "period_1",
        "period_2",
    ]
    metrics: dict[str, Any] = {k: float(data[k].item()) for k in scalar_keys if k in data}
    metrics["N_d"] = int(data["N_d"].item())
    metrics["converged_steps"] = int(data["converged_steps"].item())
    metrics["num_steps"] = int(data["num_steps"].item())
    metrics["_converged"] = metrics["converged_steps"] == metrics["num_steps"]
    metrics["_fingerprint"] = str(data["_fingerprint"].item())
    metrics["_building"] = int(data["_building"].item())
    metrics["_record"] = str(data["_record"].item())
    metrics["_intensity"] = float(data["_intensity"].item())
    metrics["_hinge_model"] = str(data["_hinge_model"].item())
    metrics["_backbone"] = str(data["_backbone"].item())
    return metrics


def _save_npz(
    path: Path, metrics: dict[str, Any], res: dict[str, Any], levels: list[tuple[float, int]]
) -> None:
    """Write a case NPZ: the scalar metrics plus the roof history for figures."""
    roof_tag = levels[-1][1]
    n = int(metrics["converged_steps"])
    arrays: dict[str, Any] = {}
    for k, v in metrics.items():
        if isinstance(v, (str, int)):
            arrays[k] = np.array(v)
        else:
            arrays[k] = np.array(float(v))
    arrays["times"] = np.asarray(res.get("times", np.zeros(n)))[:n]
    arrays["roof_disp_hist"] = np.asarray(res["displacements"].get(roof_tag, np.zeros((n, 3))))[
        :n, 0
    ]
    arrays["base_shear"] = np.asarray(res.get("base_shear", np.zeros(n)))[:n]
    arrays["roof_tag"] = np.array(roof_tag)
    np.savez(path, **arrays)


def run_matrix(
    buildings: tuple[int, ...] = (3, 9, 20),
    *,
    hinge_model: str = "beam_with_hinges",
    backbone: str = "trilinear",
    force: bool = False,
) -> list[dict[str, Any]]:
    """Run every benchmark case (in ``CASE_ORDER``) for the given buildings.

    Returns the list of metric dicts (cached cases are loaded, not re-run).
    """
    rows: list[dict[str, Any]] = []
    for building in buildings:
        for record_id, intensity in CASE_ORDER:
            rows.append(
                run_case(
                    building,
                    record_id,
                    intensity,
                    hinge_model=hinge_model,
                    backbone=backbone,
                    force=force,
                )
            )
    return rows


def write_manifest(rows: list[dict[str, Any]], path: Optional[str] = None) -> Path:
    """Write the committed metrics manifest (the paper reads this JSON).

    Drops the per-case fingerprint (a cache detail) and the histories; the
    scalar metrics and provenance round-trip as plain JSON.
    """
    out = Path(path) if path else _cache_dir() / "sac_nltha_metrics.json"
    scalar_keys = [
        "roof_disp",
        "d_max",
        "xdd_max",
        "F_max_frame",
        "F_max",
        "N_d",
        "peak_plastic_rotation",
        "max_dissipated_energy",
        "converged_steps",
        "num_steps",
        "period_1",
        "period_2",
    ]
    payload = []
    for m in rows:
        payload.append(
            {
                "building": m.get("_building"),
                "record": m.get("_record"),
                "intensity": m.get("_intensity"),
                "hinge_model": m.get("_hinge_model"),
                "backbone": m.get("_backbone"),
                "converged": bool(m.get("_converged")),
                **{k: m.get(k) for k in scalar_keys},
            }
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out


__all__ = [
    "case_fingerprint",
    "compute_metrics",
    "floor_levels",
    "record_path",
    "run_case",
    "run_matrix",
    "write_manifest",
]
