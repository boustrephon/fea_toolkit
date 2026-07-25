"""Ground motion record I/O and processing.

Provides readers for common ground motion formats (PEER NGA, simple
time-history CSV) and utility functions for scaling, baseline
correction, and spectral matching.
"""

from typing import Optional, Tuple

import numpy as np


# ── PEER NGA record reader ───────────────────────────────────────

def read_peer_record(path: str) -> Tuple[np.ndarray, np.ndarray]:
    """Read a PEER NGA strong motion record.

    PEER NGA format (``.AT2``) has a header with metadata followed
    by acceleration values in g, one per line.  This function
    auto-detects the header length by searching for a line that
    contains ``NPTS`` or a data line.

    Parameters
    ----------
    path : str
        Path to the ``.AT2`` file.

    Returns
    -------
    times : ndarray
        Time points (s).
    accel : ndarray
        Acceleration values (g).  Convert to m/s² by multiplying by
        ``9.81``.

    Raises
    ------
    ValueError
        If the file cannot be parsed.
    """
    import re

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    # Skip header — search for the first numeric data line
    data_lines: list[str] = []
    dt = 0.005  # default time step
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        # Try to detect header metadata
        low = stripped.lower()
        if "npts" in low:
            m = re.search(r"(\d+)", stripped)
        elif "dt" in low:
            m = re.search(r"([\d.]+)", stripped)
            if m:
                dt = float(m.group(1))
        # Check if this line starts with a number (data)
        try:
            float(stripped.split()[0])
            data_lines.extend(stripped.split())
        except (ValueError, IndexError):
            pass

    if not data_lines:
        raise ValueError(f"No numeric data found in PEER record: {path}")

    accel = np.array([float(v) for v in data_lines], dtype=np.float64)
    npts = len(accel)
    times = np.arange(npts, dtype=np.float64) * dt
    return times, accel


def read_time_history_csv(
    path: str, col_time: int = 0, col_accel: int = 1, skip_header: int = 1
) -> Tuple[np.ndarray, np.ndarray]:
    """Read a simple CSV time history.

    Parameters
    ----------
    path : str
        CSV file path.
    col_time : int
        Zero-indexed column for time (s).
    col_accel : int
        Zero-indexed column for acceleration (m/s²).
    skip_header : int
        Number of header rows to skip.

    Returns
    -------
    times : ndarray
        Time points (s).
    accel : ndarray
        Acceleration values (m/s²).
    """
    data = np.loadtxt(path, delimiter=",", skiprows=skip_header)
    if data.ndim == 1:
        data = data.reshape(-1, 1)
    return data[:, col_time], data[:, col_accel]


# ── Scaling ───────────────────────────────────────────────────────

def scale_to_pga(
    times: np.ndarray, accel: np.ndarray, target_pga: float
) -> np.ndarray:
    """Scale accelerations to a target peak ground acceleration.

    Parameters
    ----------
    times : ndarray
        Time points (s) — used to compute scaling factors only.
    accel : ndarray
        Acceleration values (same units as *target_pga*).
    target_pga : float
        Desired peak ground acceleration (same units as *accel*).

    Returns
    -------
    ndarray
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

    Parameters
    ----------
    times : ndarray
        Time points (s).
    accel : ndarray
        Acceleration values (m/s²).
    target_sa : float
        Desired spectral acceleration at *period* (m/s²).
    period : float
        Period (s).
    damping : float
        Damping ratio for spectral acceleration.

    Returns
    -------
    ndarray
        Scaled acceleration record.
    """
    from scipy.signal import find_peaks

    # Compute response spectrum via Duhamel integration
    dt = times[1] - times[0] if len(times) > 1 else 0.005
    w = 2.0 * np.pi / period
    wd = w * np.sqrt(1.0 - damping**2)

    # Newmark average acceleration integration
    npts = len(accel)
    u = np.zeros(npts)
    v = np.zeros(npts)
    a = np.zeros(npts)
    a[0] = -accel[0]  # initial acceleration

    for i in range(1, npts):
        # Predictor-corrector for SDOF response
        u[i] = u[i - 1] + dt * v[i - 1] + dt**2 * (0.5 - 0.25) * a[i - 1] + dt**2 * 0.25 * a[i - 1]
        v[i] = v[i - 1] + dt * (1.0 - 0.5) * a[i - 1] + dt * 0.5 * a[i]
        a[i] = (-accel[i] - 2.0 * damping * w * v[i] - w**2 * u[i])
        # This is approximate — for production use scipy.signal or OpenSees
    current_sa = np.max(np.abs(u)) * w**2
    if current_sa < 1e-12:
        return accel
    return accel * (target_sa / current_sa)


# ── Baseline correction ───────────────────────────────────────────

def baseline_correct(
    times: np.ndarray, accel: np.ndarray, order: int = 3
) -> np.ndarray:
    """Remove polynomial baseline drift from an acceleration record.

    Parameters
    ----------
    times : ndarray
        Time points (s).
    accel : ndarray
        Acceleration values.
    order : int
        Polynomial order for baseline fit (default 3).

    Returns
    -------
    ndarray
        Baseline-corrected acceleration.
    """
    coeffs = np.polyfit(times, accel, order)
    baseline = np.polyval(coeffs, times)
    return accel - baseline


# ── Record metadata ───────────────────────────────────────────────

def record_summary(times: np.ndarray, accel: np.ndarray) -> dict:
    """Return summary statistics for a ground motion record.

    Returns
    -------
    dict
        Keys: ``"duration"``, ``"npts"``, ``"dt"``, ``"pga"``,
        ``"pgv"`` (approximate), ``"ai"`` (Arias intensity),
        ``"cav"`` (cumulative absolute velocity).
    """
    dt = times[1] - times[0] if len(times) > 1 else 0.005
    duration = times[-1] - times[0]
    pga = np.max(np.abs(accel))

    # Approximate PGV from cumulative trapezoidal integration
    vel = np.cumsum(accel) * dt
    pgv = np.max(np.abs(vel))

    # Arias intensity (integral of a² dt)
    ai = (np.pi / (2.0 * 9.81)) * np.trapezoid(accel**2, times)

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