import numpy as np

# ── Flag diagram geometry (pure NumPy, no renderer dependency) ────────


# ── Flag geometry tolerances ─────────────────────────────────────────────
# Relative (never absolute) so the toolkit stays unit-agnostic (§4.6): a
# pinned end's floating-point residual is ~1e-16 of the peak end force,
# while a genuinely small moment (1 % of peak) must never be snapped.
_FLAG_ZERO_SNAP_TOL = 1e-9  # fraction of max(|Fi|, |Fj|)
_FLAG_COINCIDE_TOL = 1e-9  # fraction of member length


def _snap_flag_noise(Fi: float, Fj: float, zero_tol: float) -> tuple[float, float]:
    """Snap numerically-negligible end forces to exact zero.

    An end force below ``zero_tol * max(|Fi|, |Fj|)`` is treated as an
    exact ``0.0``.  This is what turns a pinned base's ~1e-16 moment
    residual into a real zero, so flag geometry cannot degenerate at that
    end (a noise-level offset otherwise collapses two polygon corners onto
    the same point — a face Rhino's ``AddMesh`` silently rejects).

    Args:
        Fi, Fj: End-force/moment values (original, un-negated).
        zero_tol: Relative fraction of the larger magnitude treated as zero.

    Returns:
        ``(Fi, Fj)`` with noise ends snapped to ``0.0``.
    """
    ref = max(abs(Fi), abs(Fj))
    if ref == 0.0:
        return 0.0, 0.0
    if abs(Fi) < zero_tol * ref:
        Fi = 0.0
    if abs(Fj) < zero_tol * ref:
        Fj = 0.0
    return Fi, Fj


def _dedupe_flag_vertices(vertices, length: float, coincide_tol: float):
    """Drop vertices that coincide (bitwise or within a relative tolerance).

    A polygon that survives with fewer than 3 distinct vertices has zero
    area — it cannot be drawn and must never be emitted to a renderer.

    Args:
        vertices: Polygon corner points in perimeter order.
        length: Member length used to scale ``coincide_tol``.
        coincide_tol: Fraction of ``length`` below which two corners are
            treated as the same point.

    Returns:
        List of distinct vertices in original order.
    """
    out = []
    for v in vertices:
        v_arr = np.asarray(v, dtype=float)
        dup = any(
            (
                np.array_equal(v_arr, w)
                or (length > 0.0 and np.linalg.norm(v_arr - w) <= coincide_tol * length)
            )
            for w in out
        )
        if not dup:
            out.append(v_arr)
    return out


def compute_flag_parts(pt1, pt2, vn, Fi, Fj, scale, zero_tol=None):
    """Yield ``(vertices, col_val)`` for each part of a flag diagram element.

    End-force noise is snapped to exact zero first (see
    :func:`_snap_flag_noise`), so a pinned end's floating-point residual can
    never push a flag into a branch that builds a degenerate polygon.  As
    defence in depth, no part with coincident vertices is ever yielded.

    Parameters
    ----------
    pt1, pt2 : array-like of length 3
        I-end and J-end node coordinates.
    vn : array-like of length 3
        Unit vector for positive flag offset direction.
    Fi, Fj : float
        Force/moment values (original, un-negated).
    scale : float
        Scale factor (display units per force/moment unit).
    zero_tol : float or None
        Relative zero-snap fraction (see :func:`_snap_flag_noise`).
        ``None`` → :data:`_FLAG_ZERO_SNAP_TOL`.

    Yields
    ------
    vertices : list of ndarray
        Corner points in perimeter order (4 for a quad, 3 for a triangle).
    col_val : float
        Signed value for colour mapping (positive → red, negative → blue).
    """
    pt1 = np.asarray(pt1, dtype=float)
    pt2 = np.asarray(pt2, dtype=float)
    vn = np.asarray(vn, dtype=float)

    if zero_tol is None:
        zero_tol = _FLAG_ZERO_SNAP_TOL

    # Snap end-force noise to exact zero BEFORE the branch decision, so the
    # trapezoid branch can never be reached with a noise-level end (which
    # would collapse two quad corners onto the same point).
    Fi, Fj = _snap_flag_noise(Fi, Fj, zero_tol)
    if Fi == 0.0 and Fj == 0.0:
        return

    axis = pt2 - pt1
    length = float(np.linalg.norm(axis))
    if length < 1e-12:
        return

    off_i = vn * Fi * scale  # I-end: +vn for positive Fi
    off_j = -vn * Fj * scale  # J-end: -vn for positive Fj (baked-in negation)

    if Fi * Fj < 0.0:
        # Trapezoid: [pt1, pt2, pt2+off_j, pt1+off_i]
        col_val = Fi if abs(Fi) >= abs(Fj) else Fj
        verts = _dedupe_flag_vertices(
            [pt1, pt2, pt2 + off_j, pt1 + off_i], length, _FLAG_COINCIDE_TOL
        )
        if len(verts) >= 3:
            yield verts, col_val
    else:
        # Zero-crossing: split at vcp = vx · Fi / (Fi + Fj)
        if Fi == 0.0:
            p_zero = pt1
        elif Fj == 0.0:
            p_zero = pt2
        else:
            p_zero = pt1 + axis * (Fi / (Fi + Fj))
        if Fi != 0.0:
            verts = _dedupe_flag_vertices([pt1, p_zero, pt1 + off_i], length, _FLAG_COINCIDE_TOL)
            if len(verts) >= 3:
                yield verts, Fi
        if Fj != 0.0:
            verts = _dedupe_flag_vertices([p_zero, pt2, pt2 + off_j], length, _FLAG_COINCIDE_TOL)
            if len(verts) >= 3:
                yield verts, Fj
