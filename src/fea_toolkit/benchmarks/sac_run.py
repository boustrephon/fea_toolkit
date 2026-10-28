"""SAC A4 NLTHA matrix runner with content-addressed result caching.

Runs the ten Ohtori et al. (2004) benchmark cases for a SAC building (3/9/20)
against a chosen hinge formulation, extracts the benchmark peak-response
quantities (roof displacement, ``d_max`` drift ratio, ``xdd_max`` floor
acceleration, ``F_max`` whole-building base shear, ``N_d`` yielded hinges) and
caches each run's result as an NPZ keyed on the case parameters, the record
bytes and the benchmark-relevant source files — so a case is only re-executed
when one of its inputs changes.

The per-case cache NPZ is a **private, non-schema** format — flat scalar
metrics plus a few history arrays — not a unified results archive.  It is
read and written through the storage seam (``io.npz_reader`` /
``io.npz_writer``) as a stop-gap; a first-class ``transient`` block in the
unified results schema is tracked as **P43** in ``docs/_pending_work.md``.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..io.npz_reader import read_results_npz
from ..io.npz_writer import save_results_arrays
from . import sac
from .sac_response_data import CASE_ORDER

#: The four benchmark records live in the ground-motion mirror
#: (``data/ground_motion/<public|local>/<id>.csv``), extracted by
#: ``scripts/fetch_ground_motions.py`` — see ``ground_motion_data.md``.
_RECORD_EXT = ".csv"


def _repo_root() -> Path:
    """The repository root, derived from this file's location (never the CWD)."""
    return Path(__file__).resolve().parent.parent.parent.parent


def record_path(record_id: str) -> Path:
    """Resolve a benchmark record's on-disk CSV path in the public or local mirror.

    A record may live in either ``data/ground_motion/public`` (redistributable)
    or ``data/ground_motion/local``.  The committed manifest registry records
    each record's ``redistributable`` flag, so the preferred mirror is chosen
    the same way the fetch workflow (and the ``needs_ground_motion`` test gate)
    places records; both mirrors are then checked before defaulting to the
    preferred one, so a public-domain record such as ``ELCENTRO`` is found even
    when it was not placed under ``local``.

    Args:
        record_id: Benchmark record identifier (a key of
            :data:`~fea_toolkit.benchmarks.sac_response_data.CASE_ORDER`, e.g.
            ``"ELCENTRO"``).

    Returns:
        Absolute path to the record's CSV
        (``data/ground_motion/<sub>/<record_id>.csv``).
    """
    root = _repo_root() / "data" / "ground_motion"
    order = ["local", "public"]
    try:
        from ..io.ground_motion import load_manifest_registry

        registry = _repo_root() / "tests" / "fixtures" / "ground_motion_manifests.json"
        for manifest in load_manifest_registry(str(registry)):
            if manifest.record_id == record_id:
                preferred = "public" if manifest.redistributable else "local"
                order = [preferred, *[s for s in order if s != preferred]]
                break
    except (OSError, ValueError):
        pass
    for sub in order:
        path = root / sub / f"{record_id}{_RECORD_EXT}"
        if path.exists():
            return path
    return root / order[0] / f"{record_id}{_RECORD_EXT}"


#: Package subpackages whose source files seed the cache fingerprint.  A
#: change to any of them — including a rename or deletion — invalidates every
#: cached case (prevents stale results).  Top-level modules (``utils.py``,
#: ``_unit_scaling.py``, …) are hashed separately in :func:`_code_fingerprint`.
_CODE_DIRS = ("benchmarks", "opensees", "model", "io", "capacity", "analysis")


def _code_fingerprint() -> str:
    """``sha256`` over the benchmark-relevant sources and the OpenSeesPy version.

    Walks the ``benchmarks``, ``opensees``, ``model``, ``io``, ``capacity`` and
    ``analysis`` subpackages plus the top-level package modules (``utils.py``,
    ``_unit_scaling.py``, ``spectrum.py``, …), hashing each Python/Tcl file's
    stable package-relative path *and* its bytes (so a rename or a deleted file
    also invalidates the cache), then folds in the installed OpenSeesPy version
    so a solver upgrade re-runs every case.
    """
    pkg = _repo_root() / "src" / "fea_toolkit"
    h = hashlib.sha256()

    def _hash(path: Path) -> None:
        h.update(path.relative_to(pkg).as_posix().encode())
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")

    for dirname in _CODE_DIRS:
        paths = [*(pkg / dirname).rglob("*.py"), *(pkg / dirname).rglob("*.tcl")]
        for path in sorted(paths):
            _hash(path)
    for path in sorted(pkg.glob("*.py")):
        _hash(path)
    try:
        ops_version = importlib.metadata.version("openseespy")
    except importlib.metadata.PackageNotFoundError:
        ops_version = "unavailable"
    h.update(b"openseespy\0")
    h.update(ops_version.encode())
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
    """``sha256`` over the case inputs (parameters + record bytes + code).

    Args:
        building: SAC building number (3, 9 or 20).
        record_id: Benchmark record identifier.
        intensity: Ground-motion intensity scale factor.
        hinge_model: Concentrated-plasticity hinge formulation.
        backbone: Hinge backbone label (``"trilinear"``, ``"imk"``, …).
        dt: Integration time step (s).
        damping: Rayleigh damping ratio.

    Returns:
        Hex ``sha256`` digest over the case parameters, the record bytes and
        the benchmark-relevant source code.
    """
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


#: Node-ID suffixes the builder appends for coincident zero-length
#: instrumentation nodes (lumped hinges, bond-slip springs).  These carry no
#: floor mass and their coincident-node kinematics are ill-conditioned, so they
#: must never be chosen as a floor's representative node.
_NON_STRUCTURAL_NODE_SUFFIXES = ("_hinge_i", "_hinge_j", "_bond_i", "_bond_j")


def floor_levels(builder: Any) -> list[tuple[float, int]]:
    """Sorted ``(z, representative_node_tag)`` per distinct storey elevation.

    The representative node per elevation is the highest-tagged *structural*
    node there — the rigid floor diaphragms / rigid-axial beams tie the floor's
    X DOF, so any structural node at the elevation carries the floor's lateral
    kinematics.  Internal hinge / bond-slip nodes (``*_hinge_*``, ``*_bond_*``)
    are excluded: they are coincident with a structural node but carry no floor
    mass, and their coincident-node acceleration is ill-conditioned (which would
    corrupt ``xdd_max``).

    Args:
        builder: An analysis builder whose ``mesh_model.nodes`` are scanned for
            storey elevations.

    Returns:
        Sorted ``(elevation, representative_node_tag)`` pairs, one per storey.
    """
    levels: dict[float, int] = {}
    for nd in builder.mesh_model.nodes.values():
        if getattr(nd, "node_id", "").endswith(_NON_STRUCTURAL_NODE_SUFFIXES):
            continue
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

    ``res["displacements"]`` and ``res["accelerations"]`` are per-node history
    dicts of shape ``{node_tag: (converged_steps, 3) ndarray}``; a missing node
    falls back to zeros.  When ``transient_record_kinematics`` is disabled the
    ``res["accelerations"]`` dict is empty, so the reconstructed absolute
    acceleration is the ground acceleration alone and ``xdd_max`` reflects only
    ``accel``.

    Args:
        res: Transient result dict from ``run_transient_analysis``.
        accel: (Intensity-scaled) ground acceleration series sampled at the
            integration step.
        levels: Output of :func:`floor_levels` — ``(elevation, node_tag)`` pairs.

    Returns:
        Dict of peak-response metrics (``roof_disp``, ``d_max``, ``xdd_max``,
        ``F_max_frame``, ``F_max``, ``N_d``, ``peak_plastic_rotation``,
        ``max_dissipated_energy``, ``converged_steps``, ``num_steps``,
        ``period_1``, ``period_2``).
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
    if n:
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


def _elastic_rayleigh_periods(building: int) -> tuple[Optional[float], Optional[float]]:
    """Rayleigh anchor periods from the *elastic* model.

    The benchmark's Rayleigh ζ=2 % is anchored at mode 1 + mode 2 (3/9-story) or
    mode 5 (20-story).  The periods come from the elastic model — the lumped
    hinge model is ~3× larger (hinge nodes), so an eigen solve on it is
    impractically slow, and the benchmark's damping is an elastic property
    anyway.  ``None`` falls back to the runner's own 2-mode solve.

    The default (ARPACK) solver is used: it is exact for the elastic model
    (healthy, non-singular stiffness) and returns in milliseconds, whereas
    ``fullGenLapack`` is needlessly slow here.  :meth:`run_modal_analysis`
    probes solvability with a static self-weight step first (the "initial load"
    ARPACK needs) and falls back to ``fullGenLapack`` automatically if ARPACK
    fails, so no robustness is lost.
    """
    from openseespy.opensees import wipe

    num_modes = 5 if building == 20 else 2
    wipe()
    try:
        elastic = sac.build_builder(building=building, nonlinear=False)
        modal = elastic.run_modal_analysis(
            num_modes=num_modes, print_results=False, eigen_solver="default"
        )
        periods = list(modal.get("periods", []))
    finally:
        wipe()

    if len(periods) < num_modes:
        return None, None
    if building == 20:
        return float(periods[0]), float(periods[4])
    return float(periods[0]), float(periods[1])


def _cache_dir() -> Path:
    return _repo_root() / "docs" / "assets" / "sac_nltha"


def manifest_path() -> Path:
    """Path to the committed metrics manifest (the paper reads this JSON)."""
    return _cache_dir() / "sac_nltha_metrics.json"


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
        # Stop-gap (P43): this per-case cache is a private, non-schema NPZ
        # (flat scalar metrics + a few histories), not a unified results
        # archive.  It still reads through the storage seam so ``np.load``
        # stays confined to ``io/`` — see ``docs/_pending_work.md``.
        data = read_results_npz(str(out_path))
        if str(data["_fingerprint"].item()) == fingerprint:
            return _metrics_from_npz(data)

    from openseespy.opensees import wipe

    # Rayleigh anchor periods, computed on the (small) elastic model before the
    # nonlinear domain is built — the lumped hinge model is ~3× larger, so an
    # eigen solve on it is much slower.
    period_1, period_2 = _elastic_rayleigh_periods(building)

    wipe()
    try:
        builder = sac.build_builder(
            building=building,
            nonlinear=True,
            backbone=backbone,
            hinge_model=hinge_model,
            derive_column_axial_ratio=(backbone == "imk"),
        )
        levels = floor_levels(builder)
        tags = [t for _, t in levels]

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
    # Stop-gap (P43): write through the seam — see the read site in run_case().
    save_results_arrays(str(path), arrays)


def run_matrix(
    buildings: tuple[int, ...] = (3, 9, 20),
    *,
    hinge_model: str = "beam_with_hinges",
    backbone: str = "trilinear",
    force: bool = False,
) -> list[dict[str, Any]]:
    """Run every benchmark case (in ``CASE_ORDER``) for the given buildings.

    Args:
        buildings: SAC building numbers to run (default ``(3, 9, 20)``).
        hinge_model: Concentrated-plasticity hinge formulation.
        backbone: Hinge backbone label.
        force: When True, re-run cases even when a cached result matches.

    Returns:
        The list of metric dicts, one per building and case (cached cases are
        loaded, not re-run).
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

    Args:
        rows: List of metric dicts as returned by :func:`run_matrix`.
        path: Optional output path; defaults to :func:`manifest_path`.

    Returns:
        The path the manifest was written to.
    """
    out = Path(path) if path else manifest_path()
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
    "manifest_path",
    "record_path",
    "run_case",
    "run_matrix",
    "write_manifest",
]
