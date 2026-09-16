"""Tests for fea_toolkit._flags.compute_flag_parts — force-flag geometry.

Covers the trapezoidal / zero-crossing decomposition and the floating-point
noise guards that keep degenerate polygons out of the flag renderer.
"""

import numpy as np

from fea_toolkit.utils import compute_flag_parts


def test_flag_trapezoid_opposite_signs():
    """Fi*Fj < 0 → single quad trapezoid."""
    pt1 = np.array([0.0, 0.0, 0.0])
    pt2 = np.array([5.0, 0.0, 0.0])
    vn = np.array([0.0, 0.0, 1.0])
    parts = list(compute_flag_parts(pt1, pt2, vn, Fi=10.0, Fj=-5.0, scale=0.1))
    assert len(parts) == 1
    verts, col_val = parts[0]
    assert len(verts) == 4
    np.testing.assert_array_almost_equal(verts[0], pt1)
    np.testing.assert_array_almost_equal(verts[1], pt2)
    np.testing.assert_array_almost_equal(verts[2], pt2 + np.array([0, 0, 0.5]))
    np.testing.assert_array_almost_equal(verts[3], pt1 + np.array([0, 0, 1.0]))
    assert col_val == 10.0


def test_flag_zero_crossing_same_sign():
    """Fi*Fj > 0 → two triangles crossing at zero."""
    pt1 = np.array([0.0, 0.0, 0.0])
    pt2 = np.array([5.0, 0.0, 0.0])
    vn = np.array([0.0, 1.0, 0.0])
    parts = list(compute_flag_parts(pt1, pt2, vn, Fi=10.0, Fj=5.0, scale=0.1))
    assert len(parts) == 2
    vcp = np.array([5.0 * 10.0 / 15.0, 0, 0])
    v1, c1 = parts[0]
    assert len(v1) == 3
    np.testing.assert_array_almost_equal(v1[0], pt1)
    np.testing.assert_array_almost_equal(v1[1], pt1 + vcp)
    np.testing.assert_array_almost_equal(v1[2], pt1 + np.array([0, 1.0, 0]))
    assert c1 == 10.0
    v2, c2 = parts[1]
    assert len(v2) == 3
    np.testing.assert_array_almost_equal(v2[0], pt1 + vcp)
    np.testing.assert_array_almost_equal(v2[1], pt2)
    np.testing.assert_array_almost_equal(v2[2], pt2 + np.array([0, -0.5, 0]))
    assert c2 == 5.0


def test_flag_both_negative_zero_crossing():
    """Both negative → two triangles."""
    pt1 = np.array([0.0, 0.0, 0.0])
    pt2 = np.array([4.0, 0.0, 0.0])
    vn = np.array([0.0, 0.0, 1.0])
    parts = list(compute_flag_parts(pt1, pt2, vn, Fi=-40.0, Fj=-10.0, scale=0.1))
    assert len(parts) == 2
    v1, c1 = parts[0]
    np.testing.assert_array_almost_equal(v1[2], pt1 + np.array([0, 0, -4]))
    assert c1 == -40.0
    v2, c2 = parts[1]
    np.testing.assert_array_almost_equal(v2[2], pt2 + np.array([0, 0, 1]))
    assert c2 == -10.0


def test_flag_trapezoid_left_negative_right_positive():
    """Fi<0, Fj>0 → trapezoid, both offsets in -vn."""
    pt1 = np.array([0.0, 0.0, 0.0])
    pt2 = np.array([3.0, 0.0, 0.0])
    vn = np.array([0.0, 1.0, 0.0])
    parts = list(compute_flag_parts(pt1, pt2, vn, Fi=-8.0, Fj=4.0, scale=0.5))
    assert len(parts) == 1
    verts, col_val = parts[0]
    assert len(verts) == 4
    np.testing.assert_array_almost_equal(verts[3], pt1 + np.array([0, -4, 0]))
    np.testing.assert_array_almost_equal(verts[2], pt2 + np.array([0, -2, 0]))
    assert col_val == -8.0


def test_flag_zero_at_one_end():
    """Fi=0, Fj non-zero → single triangle."""
    pt1 = np.array([0.0, 0.0, 0.0])
    pt2 = np.array([2.0, 0.0, 0.0])
    vn = np.array([0.0, 0.0, 1.0])
    parts = list(compute_flag_parts(pt1, pt2, vn, Fi=0.0, Fj=10.0, scale=0.1))
    assert len(parts) == 1
    verts, col_val = parts[0]
    assert len(verts) == 3
    np.testing.assert_array_almost_equal(verts[0], pt1)
    np.testing.assert_array_almost_equal(verts[2], pt2 + np.array([0, 0, -1.0]))
    assert col_val == 10.0


def test_flag_zero_at_both_ends():
    """Fi=0, Fj=0 → no parts yielded."""
    pt1 = np.array([0.0, 0.0, 0.0])
    pt2 = np.array([2.0, 0.0, 0.0])
    vn = np.array([0.0, 1.0, 0.0])
    parts = list(compute_flag_parts(pt1, pt2, vn, Fi=0.0, Fj=0.0, scale=1.0))
    assert len(parts) == 0


def test_flag_3d_diagonal_member():
    """Non-axis-aligned member — basic geometry test."""
    pt1 = np.array([0.0, 0.0, 0.0])
    pt2 = np.array([3.0, 4.0, 0.0])
    vn = np.array([0.0, 0.0, 1.0])
    parts = list(compute_flag_parts(pt1, pt2, vn, Fi=10.0, Fj=-10.0, scale=0.5))
    assert len(parts) == 1
    verts, _col_val = parts[0]
    assert len(verts) == 4
    np.testing.assert_array_almost_equal(verts[0], pt1)
    np.testing.assert_array_almost_equal(verts[1], pt2)
    np.testing.assert_array_almost_equal(verts[2], pt2 + np.array([0, 0, 5]))
    np.testing.assert_array_almost_equal(verts[3], pt1 + np.array([0, 0, 5]))


# ── compute_flag_parts: floating-point noise at a pinned end ─────────────
# Regression (Project A +X pushover, SAP 179/244/253): a pinned-base
# column's Mz_i is ~1e-16 of the peak, which passed the old absolute
# 1e-12 guard, forced the opposite-sign (trapezoid) branch and produced a
# quad with two bit-identical corners.  At x=10.79 the noise offset is
# below half an ULP, so ``pt1 + off_i == pt1`` exactly — Rhino's AddMesh
# silently rejected the resulting face.  All values below are the real
# Project A numbers (auto scale factor = model_height*0.2/max|Mz|).
_PROJECT_A_FLAG = {
    "pt1": np.array([10.79, -4.95, -5.32]),
    "pt2": np.array([10.79, -4.95, -0.32]),
    "vn": np.array([1.0, 0.0, 0.0]),
    "scale": 4.525114e-03,
}


def test_flag_noise_at_i_end_yields_clean_triangle():
    """Mz_i = 1.8474e-13, Mz_j = -201.44 → one clean triangle, not a
    degenerate quad with coincident corners."""
    parts = list(
        compute_flag_parts(
            _PROJECT_A_FLAG["pt1"],
            _PROJECT_A_FLAG["pt2"],
            _PROJECT_A_FLAG["vn"],
            Fi=1.8474e-13,
            Fj=-201.44,
            scale=_PROJECT_A_FLAG["scale"],
        )
    )
    assert len(parts) == 1
    verts, col_val = parts[0]
    assert len(verts) == 3
    assert col_val == -201.44
    # I-end corner sits exactly on the axis (the moment is truly zero there)
    np.testing.assert_array_almost_equal(verts[0], _PROJECT_A_FLAG["pt1"])
    # no bit-identical or coincident vertices
    assert len(set(map(tuple, verts))) == 3


def test_flag_noise_at_j_end_yields_clean_triangle():
    """Mirror family (SAP 22): Mz_i = -1.6146, Mz_j = 5.1442e-10."""
    parts = list(
        compute_flag_parts(
            _PROJECT_A_FLAG["pt1"],
            _PROJECT_A_FLAG["pt2"],
            _PROJECT_A_FLAG["vn"],
            Fi=-1.6146,
            Fj=5.1442e-10,
            scale=_PROJECT_A_FLAG["scale"],
        )
    )
    assert len(parts) == 1
    verts, _col_val = parts[0]
    assert len(verts) == 3
    # J-end sits exactly on the axis (its moment is truly zero)
    np.testing.assert_array_almost_equal(verts[1], _PROJECT_A_FLAG["pt2"])
    assert len(set(map(tuple, verts))) == 3


def test_flag_same_sign_noise_does_not_emit_collapsed_part():
    """SAP 33 family: Mz_i = 12.552, Mz_j = 1.3297e-10 → only the real
    part survives (no zero-area sliver triangle)."""
    parts = list(
        compute_flag_parts(
            _PROJECT_A_FLAG["pt1"],
            _PROJECT_A_FLAG["pt2"],
            _PROJECT_A_FLAG["vn"],
            Fi=12.552,
            Fj=1.3297e-10,
            scale=_PROJECT_A_FLAG["scale"],
        )
    )
    assert len(parts) == 1
    verts, col_val = parts[0]
    assert len(verts) == 3
    assert col_val == 12.552


def test_flag_no_noise_regression_at_large_site_coordinates():
    """The FP collapse is coordinate-dependent: translate the model to
    x ≈ 1e6 (site coordinates) and the residual is far below the local
    ULP, yet the flag must still be a clean, non-degenerate triangle."""
    pt1 = np.array([1.0e6 + 10.79, -4.95, -5.32])
    pt2 = np.array([1.0e6 + 10.79, -4.95, -0.32])
    parts = list(
        compute_flag_parts(
            pt1,
            pt2,
            _PROJECT_A_FLAG["vn"],
            Fi=1.8474e-13,
            Fj=-201.44,
            scale=_PROJECT_A_FLAG["scale"],
        )
    )
    assert len(parts) == 1
    verts, _col_val = parts[0]
    assert len(verts) == 3
    assert len(set(map(tuple, verts))) == 3


def test_flag_no_part_ever_has_coincident_vertices():
    """Property: for any input, no yielded polygon contains two vertices
    closer than the drawable floor (1e-9 × member length)."""
    cases = [
        (10.0, -5.0),
        (10.0, 5.0),
        (-40.0, -10.0),
        (-8.0, 4.0),
        (0.0, 10.0),
        (10.0, -10.0),
        (1.8474e-13, -201.44),
        (-1.6146, 5.1442e-10),
        (12.552, 1.3297e-10),
        (0.0, 0.0),
    ]
    length = np.linalg.norm(_PROJECT_A_FLAG["pt2"] - _PROJECT_A_FLAG["pt1"])
    for Fi, Fj in cases:
        parts = list(
            compute_flag_parts(
                _PROJECT_A_FLAG["pt1"],
                _PROJECT_A_FLAG["pt2"],
                _PROJECT_A_FLAG["vn"],
                Fi=Fi,
                Fj=Fj,
                scale=_PROJECT_A_FLAG["scale"],
            )
        )
        for verts, _cv in parts:
            assert len(verts) >= 3
            for m in range(len(verts)):
                for n in range(m + 1, len(verts)):
                    dist = np.linalg.norm(np.asarray(verts[m]) - np.asarray(verts[n]))
                    assert dist > 1e-9 * length, (
                        f"degenerate polygon for Fi={Fi}, Fj={Fj}: "
                        f"verts[{m}] and verts[{n}] are {dist:.3e} apart"
                    )


def test_flag_relative_snap_keeps_genuinely_small_moments():
    """SAP 458 (Mz ≈ 2.7 / 8.0 kN·m, ~1% of peak) must NOT be snapped —
    it is a real, drawable sliver, not noise."""
    parts = list(
        compute_flag_parts(
            np.array([0.0, 0.0, 0.0]),
            np.array([10.025, 0.0, 0.0]),
            np.array([0.0, 0.0, 1.0]),
            Fi=2.69621,
            Fj=7.968195,
            scale=_PROJECT_A_FLAG["scale"],
        )
    )
    assert len(parts) == 2
    for verts, _cv in parts:
        assert len(verts) == 3
