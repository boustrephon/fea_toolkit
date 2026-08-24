"""Tests for the standalone load-transform helpers in ``opensees/_loads.py``.

Covers :func:`~fea_toolkit.opensees.global_to_local_distributed_load`:

- Zero-length elements (coincident end nodes) raise a clear ``ValueError``
  instead of producing NaN load components via a zero-length normalisation.
- Non-degenerate elements return finite local ``(wx, wy, wz)`` components.
- Z-aligned elements exercise the manual fallback axis (``local_x[2]`` is
  tested, so ``v`` becomes the X reference vector and ``np.cross(local_x, v)``
  is nonzero), keeping ``local_z`` / ``local_y`` finite.
"""

import numpy as np
import openseespy.opensees as ops
import pytest

from fea_toolkit.opensees import global_to_local_distributed_load


@pytest.fixture(autouse=True)
def _clean_domain():
    """Wipe the OpenSees domain before and after each test."""
    ops.wipe()
    yield
    ops.wipe()


def _build_beam(i_xyz, j_xyz, tag=1):
    """Create a minimal 3D elasticBeamColumn between two nodes."""
    ops.model("basic", "-ndm", 3, "-ndf", 6)
    ops.node(1, *i_xyz)
    ops.node(2, *j_xyz)
    ops.fix(1, 1, 1, 1, 1, 1, 1)
    axis = np.array(j_xyz) - np.array(i_xyz)
    length = float(np.linalg.norm(axis))
    # LinearCrdTransf3d needs a vecxz not parallel to the element axis.
    vecxz = (1.0, 0.0, 0.0) if length > 0.0 and abs(axis[2]) / length > 0.999 else (0.0, 0.0, 1.0)
    ops.geomTransf("Linear", 1, *vecxz)
    ops.section("Elastic", 1, 200.0e6, 0.01, 1.0e-5, 1.0e-5, 77.0e6, 1.0e-5)
    ops.element("elasticBeamColumn", tag, 1, 2, 1, 1)
    return tag


def _build_zero_length(tag=1):
    """Create a minimal ``zeroLength`` element between two coincident nodes.

    ``elasticBeamColumn`` rejects coincident nodes inside the C library
    (``ElasticBeam3d::setDomain`` errors without returning cleanly), so the
    zero-length path is exercised through OpenSees' canonical ``zeroLength``
    element, which explicitly permits coincident nodes.
    """
    ops.model("basic", "-ndm", 3, "-ndf", 6)
    ops.node(1, 0.0, 0.0, 0.0)
    ops.node(2, 0.0, 0.0, 0.0)
    ops.fix(1, 1, 1, 1, 1, 1, 1)
    ops.uniaxialMaterial("Elastic", 1, 1000.0)
    ops.element("zeroLength", tag, 1, 2, "-mat", 1, "-dir", 1)
    return tag


def test_zero_length_raises_value_error():
    """Coincident end nodes must raise instead of producing NaN components."""
    _build_zero_length()
    with pytest.raises(ValueError, match="zero"):
        global_to_local_distributed_load(1, [1.0, 0.0, 0.0])


def test_non_zero_length_returns_finite_components():
    """A normal X-aligned element returns finite local load components."""
    _build_beam((0.0, 0.0, 0.0), (3.0, 0.0, 0.0))
    wx, wy, wz = global_to_local_distributed_load(1, [1.0, 0.0, 0.0])
    assert np.isfinite(wx) and np.isfinite(wy) and np.isfinite(wz)


def test_z_aligned_fallback_axis_is_finite(monkeypatch):
    """Z-aligned elements must not hit a zero cross-product in the fallback.

    The fallback tests ``local_x[2]`` (the global-Z component): a Z-aligned
    element picks the X reference vector so ``np.cross(local_x, v)`` is
    nonzero and ``local_z`` / ``local_y`` stay finite.
    """
    _build_beam((0.0, 0.0, 0.0), (0.0, 0.0, 3.0))
    from fea_toolkit.opensees import _loads as loads_mod

    def _raise(*args, **kwargs):
        raise RuntimeError("no yaxis/zaxis response")

    monkeypatch.setattr(loads_mod.ops, "eleResponse", _raise)
    wx, wy, wz = global_to_local_distributed_load(1, [0.0, 0.0, -1.0])
    assert np.isfinite(wx) and np.isfinite(wy) and np.isfinite(wz)
    # Gravity on a vertical member must project onto the axial (local x) axis.
    assert abs(wx) > 0.0 or abs(wz) > 0.0
