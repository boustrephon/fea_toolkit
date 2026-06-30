"""Tests for mesh quality checks and optional Gmsh remeshing.

The checks tests have **no** external dependencies (pure NumPy) and always
run.  The remeshing tests require ``gmsh`` (``pip install fea_toolkit[mesh-remesh]``)
and are skipped when it is not installed.

Run with::

    pytest tests/test_mesh.py -v
"""

import pytest

from fea_toolkit.model.sap_data import Node, AreaElement, AreaMesh


# ========================================================================
# Fixtures
# ========================================================================


@pytest.fixture
def quad_nodes():
    return {
        "1": Node("1", 1, 0.0, 0.0, 0.0),
        "2": Node("2", 2, 6.0, 0.0, 0.0),
        "3": Node("3", 3, 6.0, 8.0, 0.0),
        "4": Node("4", 4, 0.0, 8.0, 0.0),
    }


@pytest.fixture
def quad_area(quad_nodes):
    return {
        "1": AreaElement("1", 10, list(quad_nodes.keys()), thickness=0.2),
    }


@pytest.fixture
def skewed_quad():
    """A warped quad with one vertex lifted out of plane (non-coplanar)."""
    nodes = {
        "1": Node("1", 1, 0.0, 0.0, 0.0),
        "2": Node("2", 2, 6.0, 0.0, 0.0),
        "3": Node("3", 3, 6.0, 8.0, 2.0),  # lifted → non-coplanar
        "4": Node("4", 4, 0.0, 8.0, 0.0),
    }
    areas = {"A": AreaElement("A", 20, ["1", "2", "3", "4"], thickness=0.2)}
    return areas, nodes


# ========================================================================
# Mesh quality checks (COMPAS — optional)
# ========================================================================


class TestMeshChecks:
    """Mesh quality diagnostics (``fea_toolkit.mesh.checks``)."""

    def test_aspect_ratios_flat_quad(self, quad_area, quad_nodes):
        from fea_toolkit.mesh.checks import aspect_ratios
        ar = aspect_ratios(quad_area, quad_nodes)
        assert "1" in ar
        # 6×8 rectangle → ratio = 8/6 ≈ 1.33
        assert ar["1"] == pytest.approx(1.3333, abs=1e-3)

    def test_aspect_ratios_empty(self):
        from fea_toolkit.mesh.checks import aspect_ratios
        assert aspect_ratios({}, {}) == {}

    def test_skew_flat_quad(self, quad_area, quad_nodes):
        from fea_toolkit.mesh.checks import skew
        sk = skew(quad_area, quad_nodes)
        assert "1" in sk
        assert sk["1"] == pytest.approx(0.0, abs=1e-12)  # perfect rectangle

    def test_skew_warped_quad(self, skewed_quad):
        from fea_toolkit.mesh.checks import skew
        areas, nodes = skewed_quad
        sk = skew(areas, nodes)
        assert "A" in sk
        assert sk["A"] > 0  # warped → non-zero skew

    def test_flatness_planar(self, quad_area, quad_nodes):
        from fea_toolkit.mesh.checks import flatness
        fl = flatness(quad_area, quad_nodes)
        assert "1" in fl
        assert fl["1"] == pytest.approx(0.0, abs=1e-12)  # perfectly planar

    def test_flatness_warped(self, skewed_quad):
        from fea_toolkit.mesh.checks import flatness
        areas, nodes = skewed_quad
        fl = flatness(areas, nodes)
        assert "A" in fl
        assert fl["A"] > 0  # warped → non-zero flatness

    def test_report_passes_good_quad(self, quad_area, quad_nodes):
        from fea_toolkit.mesh.checks import report
        r = report(quad_area, quad_nodes)
        assert r["n_elements"] == 1
        assert r["passed"] is True
        assert len(r["warnings"]) == 0

    def test_report_warns_skewed(self, skewed_quad):
        from fea_toolkit.mesh.checks import report
        areas, nodes = skewed_quad
        r = report(areas, nodes)
        # Use a very low threshold to force a warning
        r2 = report(areas, nodes, skew_warn=1.0)
        assert len(r2["warnings"]) >= 1
        assert r2["passed"] is False

    def test_report_non_quad_skipped(self, quad_area, quad_nodes):
        """Triangular area elements are silently skipped."""
        from fea_toolkit.mesh.checks import report
        tri = {
            "T": AreaElement("T", 99, ["1", "2", "3"], thickness=0.1),
        }
        r = report(tri, quad_nodes)
        assert r["n_elements"] == 0
        assert r["passed"] is True


def test_modules_importable_without_compas():
    """The checks module is importable without compas."""
    from fea_toolkit.mesh import checks as chk
    assert hasattr(chk, "aspect_ratios")
    assert hasattr(chk, "report")

def test_flatness_planar_rhomboid():
    """A planar rhomboid must have zero flatness."""
    from fea_toolkit.model.sap_data import Node, AreaElement
    from fea_toolkit.mesh.checks import flatness
    # Rhomboid: (0,0,0) (4,0,0) (6,4,0) (2,4,0) — planar, equal edges
    nodes = {
        "1": Node("1", 1, 0, 0, 0),
        "2": Node("2", 2, 4, 0, 0),
        "3": Node("3", 3, 6, 4, 0),
        "4": Node("4", 4, 2, 4, 0),
    }
    areas = {"R": AreaElement("R", 10, ["1","2","3","4"], thickness=0.1)}
    fl = flatness(areas, nodes)
    assert fl["R"] == pytest.approx(0.0, abs=1e-12), f"planar rhomboid should be flat, got {fl['R']}"

def test_flatness_non_planar_detected():
    """A warped (non-coplanar) quad must have non-zero flatness."""
    from fea_toolkit.model.sap_data import Node, AreaElement
    from fea_toolkit.mesh.checks import flatness
    # One corner lifted out of plane
    nodes = {
        "1": Node("1", 1, 0, 0, 0),
        "2": Node("2", 2, 4, 0, 0),
        "3": Node("3", 3, 4, 4, 0.5),  # lifted
        "4": Node("4", 4, 0, 4, 0),
    }
    areas = {"W": AreaElement("W", 10, ["1","2","3","4"], thickness=0.1)}
    fl = flatness(areas, nodes)
    assert fl["W"] > 0, f"warped quad should be non-planar, got {fl['W']}"



# ========================================================================
# Gmsh remeshing (Gmsh — optional)
# ========================================================================


def _requires_gmsh():
    try:
        import gmsh  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _requires_gmsh(), reason="gmsh not installed")
class TestMeshRemesh:
    """Constrained remeshing (``fea_toolkit.mesh.remesh``)."""

    def test_remesh_basic_quad(self, quad_area, quad_nodes):
        """Remesh a simple 6×8 quad into finer quads."""
        from fea_toolkit.mesh.remesh import remesh_areas
        mesh = {"1": AreaMesh(auto_mesh=True, max_size=2.0)}
        areas, assign, nodes, ntag = remesh_areas(
            quad_area, {}, quad_nodes, mesh,
            target_length=2.0, recombine=True, verbose=False,
        )
        # Original should be inactive
        assert areas["1"].inactive is True
        # Sub-elements created
        sub_ids = [aid for aid in areas if aid != "1"]
        assert len(sub_ids) >= 1
        # Section assignment preserved (empty in this test)
        assert all(assign.get(sid) is None for sid in sub_ids)

    def test_remesh_preserves_thickness(self, quad_area, quad_nodes):
        """Sub-elements inherit parent thickness."""
        from fea_toolkit.mesh.remesh import remesh_areas
        mesh = {"1": AreaMesh(auto_mesh=True, max_size=2.0)}
        areas, *_ = remesh_areas(
            quad_area, {}, quad_nodes, mesh,
            target_length=2.0, recombine=True, verbose=False,
        )
        for aid in areas:
            if aid != "1":
                assert areas[aid].thickness == pytest.approx(0.2)

    def test_remesh_no_mesh_skipped(self, quad_area, quad_nodes):
        """Areas with auto_mesh=False are not touched."""
        from fea_toolkit.mesh.remesh import remesh_areas
        mesh = {"1": AreaMesh(auto_mesh=False, max_size=2.0)}
        areas, assign, nodes, ntag = remesh_areas(
            quad_area, {}, quad_nodes, mesh,
            target_length=2.0, recombine=True, verbose=False,
        )
        assert areas["1"].inactive is False  # not modified

    def test_remesh_preserves_section(self, quad_area, quad_nodes):
        """Section assignments propagate to sub-elements."""
        from fea_toolkit.mesh.remesh import remesh_areas
        assign = {"1": "Slab200"}
        mesh = {"1": AreaMesh(auto_mesh=True, max_size=2.0)}
        _, out_assign, *_ = remesh_areas(
            quad_area, assign, quad_nodes, mesh,
            target_length=2.0, recombine=True, verbose=False,
        )
        sub_ids = [aid for aid in out_assign if aid != "1"]
        assert all(out_assign[sid] == "Slab200" for sid in sub_ids)


def test_constrain_line_builds_dict(quad_nodes):
    """constrain_line() populates the constraints dict."""
    from fea_toolkit.mesh.remesh import constrain_line
    constraints = {}
    constrain_line("A1", "F1", "1", "2", quad_nodes, constraints)
    assert "A1" in constraints
    fid, na, nb = constraints["A1"][0]
    assert fid == "F1"


def test_constrain_line_multiple_frames(quad_nodes):
    """Multiple frame edges on the same area are collected."""
    from fea_toolkit.mesh.remesh import constrain_line
    constraints = {}
    constrain_line("A1", "F1", "1", "2", quad_nodes, constraints)
    constrain_line("A1", "F2", "2", "3", quad_nodes, constraints)
    assert len(constraints["A1"]) == 2


def test_remesh_raises_without_gmsh():
    """A clear ImportError is raised when gmsh is missing."""
    import fea_toolkit.mesh.remesh as rm
    original = rm._check_gmsh
    try:
        rm._check_gmsh = lambda: False
        with pytest.raises(ImportError, match="gmsh is required"):
            rm.remesh_areas({}, {}, {}, {})
    finally:
        rm._check_gmsh = original
