"""Ground motion record I/O and processing.

Provides readers for common ground motion formats (PEER NGA, simple
time-history CSV) and utility functions for scaling, baseline
correction, and spectral matching.

All record functions use the **canonical SI system** — acceleration in
m/s².  PEER ``.AT2`` files store acceleration in g; the values are
converted to m/s² at read time (see :func:`read_peer_record`).
"""

import pathlib
import urllib.parse
from dataclasses import dataclass, fields

import numpy as np

from ..utils import DEFAULT_GRAVITY_MS2

# ── Ground-motion manifest ───────────────────────────────────────


@dataclass
class GroundMotionManifest:
    """Provenance and fetch metadata for a single ground-motion record.

    A manifest is the repository-safe representation of a record: it records
    *where* a record came from, how to fetch it, and how it should be
    processed, without carrying the waveform itself.  Waveforms are fetched
    into a local ``data/`` mirror by the fetch helpers under ``scripts/`` and
    are never stored in the repository (see the *Ground-Motion Data & Sources*
    document).

    All fields are plain, JSON-friendly values, so a manifest list round-trips
    through :meth:`to_dict` / :meth:`from_dict`.
    """

    record_id: str
    provider: str
    event: str
    station: str
    component: str
    source_url: str
    licence: str = ""
    redistributable: bool = False
    scale_factor: float = 1.0
    rotation: str = "as_recorded"
    note: str = ""

    def to_dict(self) -> dict:
        """Return the manifest as a plain ``dict`` (JSON-serialisable).

        Returns:
            A ``dict`` mapping each field name to its value, suitable for
            ``json.dump`` and for :meth:`from_dict`.
        """
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, payload: dict) -> "GroundMotionManifest":
        """Rebuild a manifest from a :meth:`to_dict` payload.

        Args:
            payload: A ``dict`` whose keys are the manifest's field names,
                as produced by :meth:`to_dict`.

        Returns:
            A new :class:`GroundMotionManifest` carrying the values from
            *payload*.
        """
        return cls(**payload)


def load_manifest_registry(path: str) -> list[GroundMotionManifest]:
    """Load a JSON manifest registry into :class:`GroundMotionManifest` objects.

    The registry is a JSON array of :meth:`GroundMotionManifest.to_dict`
    payloads, written by :func:`save_manifest_registry`.

    Args:
        path: Path to the JSON registry file.

    Returns:
        List of manifests in file order.

    Raises:
        ValueError: If the file is not a JSON array.
    """
    import json

    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, list):
        raise ValueError(f"Manifest registry must be a JSON array: {path}")
    return [GroundMotionManifest.from_dict(item) for item in payload]


def save_manifest_registry(path: str, manifests: list[GroundMotionManifest]) -> None:
    """Write a list of manifests to a JSON registry file.

    Args:
        path: Destination path.
        manifests: Manifests to serialise, in order.
    """
    import contextlib
    import json
    import os
    import tempfile

    dest = os.fspath(path)
    directory = os.path.dirname(os.path.abspath(dest)) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".manifests-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump([m.to_dict() for m in manifests], f, indent=2)
            f.write("\n")
        # ``mkstemp`` creates the temp file owner-only (0600).  Before the
        # atomic ``replace`` installs it as the registry, restore the mode the
        # destination already carries, or fall back to the umask-derived default
        # when no destination exists, so a saved registry is not left readable
        # only by its owner.
        try:
            mode = os.stat(dest).st_mode & 0o777
        except OSError:
            umask = os.umask(0)
            os.umask(umask)
            mode = 0o666 & ~umask
        os.chmod(tmp, mode)
        os.replace(tmp, dest)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


# ── Fetch URL helpers ───────────────────────────────────────────

_DIRECT_SUFFIXES = (".at2", ".txt", ".csv")


def source_extension(url: str) -> str:
    """Return the destination file extension for a record's source URL.

    A URL whose path ends in a supported waveform suffix (``.at2``,
    ``.txt``, ``.csv``) keeps that suffix; any other URL — a portal page,
    a query-only link, or a path with no extension — falls back to ``.at2``
    so the fetched file always has a stable extension.
    """
    path = urllib.parse.urlparse(url).path
    suffix = pathlib.PurePosixPath(path).suffix.lower()
    return suffix if path.lower().endswith(_DIRECT_SUFFIXES) else ".at2"


def is_direct_url(url: str) -> bool:
    """Whether *url* points directly at a supported waveform file.

    The decision is made on the URL **path** only, so query parameters
    (e.g. ``?download=1``) do not defeat direct-download detection.
    """
    path = urllib.parse.urlparse(url).path
    return path.lower().endswith(_DIRECT_SUFFIXES)


# ── PEER NGA record reader ───────────────────────────────────────


def read_peer_record(path: str) -> tuple[np.ndarray, np.ndarray]:
    """Read a PEER NGA strong motion record.

    PEER NGA format (``.AT2``) has a header with metadata followed
    by acceleration values in g, one per line.  This function
    auto-detects the header length by searching for a line that
    contains ``NPTS`` or a data line.

    Args:
        path: Path to the ``.AT2`` file.

    Returns:
        ``(times, accel)`` tuple — time points (s) and acceleration values
        in **m/s²**.  PEER ``.AT2`` files store acceleration in g; values
        are converted to the module's canonical SI system at read time
        using :data:`fea_toolkit.utils.DEFAULT_GRAVITY_MS2` (9.80665).

    Raises:
        ValueError: If the file cannot be parsed.
    """
    import re

    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    # Skip header — search for the first numeric data line
    data_lines: list[str] = []
    dt = 0.005  # default time step (fallback if no DT header)
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Try to detect header metadata
        low = stripped.lower()
        if "npts" in low or "dt" in low:
            # Extract DT from header lines, handling NPTS+DT on same line
            # Only attempt fallback extraction when "dt" is explicitly present
            m_dt = re.search(r"(?:^|\s)DT\s*=\s*([\d.]+)", stripped, re.IGNORECASE)
            if not m_dt and "dt" in low:
                m_dt = re.search(r"([\d.]+)", stripped[stripped.lower().find("dt") :])
            if m_dt:
                dt = float(m_dt.group(1))
            continue  # skip recognized header metadata
        # Check if this line starts with a number (data)
        try:
            float(stripped.split()[0])
            data_lines.extend(stripped.split())
        except (ValueError, IndexError):
            pass  # non-header, non-numeric — ignore
    # If DT was not found in the header, the 0.005 fallback remains in effect.
    # This matches the PEER NGA database convention where a missing DT header
    # typically implies a 0.005 s time step.

    if not data_lines:
        raise ValueError(f"No numeric data found in PEER record: {path}")

    accel = np.array([float(v) for v in data_lines], dtype=np.float64)
    npts = len(accel)
    times = np.arange(npts, dtype=np.float64) * dt
    # PEER NGA .AT2 stores acceleration in g — convert to the module's
    # canonical SI system (m/s²) so every reader/processor shares one unit.
    accel = accel * DEFAULT_GRAVITY_MS2
    return times, accel


def read_time_history_csv(
    path: str, col_time: int = 0, col_accel: int = 1, skip_header: int = 1
) -> tuple[np.ndarray, np.ndarray]:
    """Read a simple CSV time history.

    Args:
        path: CSV file path.
        col_time: Zero-indexed column for time (s).
        col_accel: Zero-indexed column for acceleration (m/s²).
        skip_header: Number of header rows to skip.

    Returns:
        ``(times, accel)`` — time points (s) and acceleration values
        (m/s²).
    """
    data = np.loadtxt(path, delimiter=",", skiprows=skip_header, ndmin=2)
    return data[:, col_time], data[:, col_accel]


# ── Scaling ───────────────────────────────────────────────────────


def scale_to_pga(times: np.ndarray, accel: np.ndarray, target_pga: float) -> np.ndarray:
    """Scale accelerations to a target peak ground acceleration.

    Args:
        times: Time points (s) — accepted for API symmetry with other
            scaling helpers but not used in the computation.
        accel: Acceleration values (same units as *target_pga*).
        target_pga: Desired peak ground acceleration (same units as
            *accel*).

    Returns:
        Scaled acceleration record.
    """
    current_pga = np.max(np.abs(accel))
    if current_pga < 1e-12:
        return accel
    scale_factor = target_pga / current_pga
    return accel * scale_factor


def scale_to_target_sa(
    times: np.ndarray,
    accel: np.ndarray,
    target_sa: float,
    period: float,
    damping: float = 0.05,
) -> np.ndarray:
    """Scale accelerations so the spectral acceleration at *period*
    matches *target_sa* (approximate single-point matching).

    Args:
        times: Time points (s).
        accel: Acceleration values (m/s²).
        target_sa: Desired spectral acceleration at *period* (m/s²).
        period: Period (s).
        damping: Damping ratio for spectral acceleration.

    Returns:
        Scaled acceleration record.
    """
    # Compute response spectrum via Newmark-beta linear acceleration integration
    dt = times[1] - times[0] if len(times) > 1 else 0.005
    w = 2.0 * np.pi / period

    # Newmark linear acceleration coefficients (gamma=0.5, beta=1/6)
    gamma = 0.5
    beta = 1.0 / 6.0

    npts = len(accel)
    u = np.zeros(npts)
    v = np.zeros(npts)
    a = np.zeros(npts)
    # Initial acceleration from SDOF equation: m*a[0] + c*v[0] + k*u[0] = -m*accel[0]
    # with u[0]=v[0]=0 → a[0] = -accel[0]
    a[0] = -accel[0]

    # Effective stiffness: k_eff = k + gamma*c/(beta*dt) + 1/(beta*dt^2)  (per unit mass)
    # c = 2*damping*w, k = w^2
    c = 2.0 * damping * w
    k_eff = w**2 + (gamma * c) / (beta * dt) + 1.0 / (beta * dt**2)

    for i in range(1, npts):
        # Effective load per unit mass (standard Newmark formulation):
        # p_eff = -accel[i] + a_bar*u[i-1] + b_bar*v[i-1] + c_bar*a[i-1]
        # where a_bar = 1/(beta*dt^2), b_bar = gamma/(beta*dt), c_bar = 1/(beta*dt) - 1/(2*beta)
        # (consistent with k_eff = k + gamma*c/(beta*dt) + 1/(beta*dt^2))
        p_eff = (
            -accel[i]
            + (1.0 / (beta * dt**2)) * u[i - 1]
            + (gamma / (beta * dt)) * v[i - 1]
            + (1.0 / (beta * dt) - 1.0 / (2.0 * beta)) * a[i - 1]
        )
        u[i] = p_eff / k_eff
        # Newmark state update (gamma=0.5, beta=1/6 linear acceleration)
        # Standard formulas: v[i] = v[i-1] + dt*((1-gamma)*a[i-1] + gamma*a[i])
        #                    a[i] = (u[i]-u[i-1])/(beta*dt^2) - v[i-1]/(beta*dt) - (1/(2*beta)-1)*a[i-1]
        v[i] = v[i - 1] + dt * ((1.0 - gamma) * a[i - 1] + gamma * a[i])
        a[i] = (
            (u[i] - u[i - 1]) / (beta * dt**2)
            - v[i - 1] / (beta * dt)
            - (1.0 / (2.0 * beta) - 1.0) * a[i - 1]
        )

    current_sa = np.max(np.abs(u)) * w**2
    if current_sa < 1e-12:
        return accel
    return accel * (target_sa / current_sa)


# ── Baseline correction ───────────────────────────────────────────


def baseline_correct(times: np.ndarray, accel: np.ndarray, order: int = 3) -> np.ndarray:
    """Remove polynomial baseline drift from an acceleration record.

    Args:
        times: Time points (s).
        accel: Acceleration values.
        order: Polynomial order for baseline fit (default 3).

    Returns:
        Baseline-corrected acceleration.
    """
    coeffs = np.polyfit(times, accel, order)
    baseline = np.polyval(coeffs, times)
    return accel - baseline


# ── Record metadata ───────────────────────────────────────────────


def record_summary(times: np.ndarray, accel: np.ndarray) -> dict:
    """Return summary statistics for a ground motion record.

    ``accel`` must be in m/s² — the canonical unit of this module (see
    :func:`read_peer_record` / :func:`read_time_history_csv`).

    Returns:
        Dict with keys ``"duration"``, ``"npts"``, ``"dt"``, ``"pga"``,
        ``"pgv"`` (approximate), ``"ai"`` (Arias intensity), ``"cav"``
        (cumulative absolute velocity).
    """
    dt = times[1] - times[0] if len(times) > 1 else 0.005
    duration = times[-1] - times[0]
    pga = np.max(np.abs(accel))

    # Approximate PGV from cumulative trapezoidal integration
    vel = np.cumsum(accel) * dt
    pgv = np.max(np.abs(vel))

    # Arias intensity (integral of a² dt) — accel is in m/s², so the SI
    # gravitational constant normalises the squared-m/s² integrand.
    ai = (np.pi / (2.0 * DEFAULT_GRAVITY_MS2)) * np.trapezoid(accel**2, times)

    # Cumulative absolute velocity
    cav = np.trapezoid(np.abs(accel), times)

    return {
        "duration": float(duration),
        "npts": len(times),
        "dt": float(dt),
        "pga": float(pga),
        "pgv": float(pgv),
        "ai": float(ai),
        "cav": float(cav),
    }
