"""Tests for ``fea_toolkit.plotting.force_diagram`` — the unified force
diagram dispatcher.

Covers source resolution (builder / in-memory dict / NPZ path), static vs
CQC-RS dispatch, and 2D vs 3D selection.
"""

import numpy as np
import pytest

# ============================================================================
# Shared synthetic data
# ============================================================================


def _minimal_npz_dict() -> dict:
    """Minimal NPZ-style dict with static frame forces + kN-m metadata."""
    return {
        "node_tag": np.array([1, 2, 3]),
        "node_sap_id": np.array(["1", "2", "3"]),
        "node_x": np.array([0.0, 4.0, 4.0]),
        "node_y": np.array([0.0, 0.0, 0.0]),
        "node_z": np.array([0.0, 0.0, 3.0]),
        "frame_eid": np.array([0, 1]),
        "frame_sap_id": np.array(["1", "2"]),
        "frame_node_i": np.array([1, 2]),
        "frame_node_j": np.array([2, 3]),
        "frame_sec_name": np.array(["COL", "BEAM"]),
        "frame_parent_sap_id": np.array(["", ""]),
        "analysis_types": np.array(["static"]),
        "static_case_labels": np.array(["DEAD"]),
        "force_unit": np.array(["kN"]),
        "length_unit": np.array(["m"]),
        "forces_coordinate_system": np.array(["local"]),
        "static/DEAD/my_i": np.array([10.0, -5.0]),
        "static/DEAD/my_j": np.array([-10.0, 5.0]),
    }


# ============================================================================
# Force-diagram unified dispatcher
# ============================================================================


def _minimal_rs_npz_dict() -> dict:
    """Minimal NPZ-style dict carrying the flat ``rs/elem_*`` force block.

    Derived from :func:`_minimal_npz_dict`'s geometry so the RS reader and the
    geometry resolver can both be exercised; the static payload is replaced by
    the response-spectrum element block.
    """
    data = _minimal_npz_dict()
    data.pop("static/DEAD/my_i")
    data.pop("static/DEAD/my_j")
    data["analysis_types"] = np.array(["rs"])
    data.pop("static_case_labels")
    data["rs/elem_sap_id"] = np.array(["1", "2"])
    data["rs/elem_z_bot"] = np.array([0.0, 0.0])
    data["rs/elem_z_mid"] = np.array([0.0, 1.5])
    data["rs/elem_combination"] = np.array(["cqc"])
    data["rs/elem_direction"] = np.array(["X"])
    data["rs/elem_fy_i"] = np.array([2.0, 4.0])
    data["rs/elem_fy_j"] = np.array([-2.0, -4.0])
    data["rs/elem_mz_i"] = np.array([6.0, 12.0])
    data["rs/elem_mz_j"] = np.array([-6.0, -12.0])
    # Deprecated aliases, as written by collect_rs_element_force_arrays.
    # Delete with them — see docs/deprecation_plan.md ("Scheduled: per-element
    # RS alias keys"); the canonical replacements are rs/elem_fy_i, rs/elem_my_i.
    data["rs/elem_Vy_i"] = np.array([2.0, 4.0])
    data["rs/elem_My_i"] = np.array([0.0, 0.0])
    return data


# ============================================================================
# Force-diagram unified dispatcher
# ============================================================================


class TestForceDiagramUnified:
    """Table-driven coverage for the unified ``plot_force_diagram``."""

    def test_resolve_source_npz(self):
        from fea_toolkit.plotting.force_diagram import _resolve_source

        data = _resolve_source(_minimal_npz_dict())
        assert data is not None
        assert data.kind == "static"
        assert data.quantity == "My"
        assert data.force_unit == "kN"
        assert data.length_unit == "m"
        assert len(data.series) == 2
        # frame 0 spans nodes 1-2 (z 0..0), frame 1 spans nodes 2-3 (z 0..3)
        assert data.series[0]["z_mid"] == pytest.approx(0.0)
        assert data.series[1]["z_mid"] == pytest.approx(1.5)
        # canonical uppercase force keys, incl. local variants
        assert data.series[0]["forces"]["MY"] == pytest.approx(10.0)
        assert data.series[0]["forces"]["MY_j"] == pytest.approx(-10.0)
        assert data.series[0]["forces"]["MY_i_local"] == pytest.approx(10.0)

    def test_extract_npz_rs_forces(self):
        """``_extract_npz_rs_forces`` rebuilds records from the rs/elem_* block."""
        from fea_toolkit.plotting.viz_forces import _extract_npz_rs_forces

        data = _minimal_rs_npz_dict()
        records = _extract_npz_rs_forces(data)
        assert len(records) == 2
        assert records[0]["elem_id"] == "1"
        assert records[0]["z_mid"] == pytest.approx(0.0)
        # Canonical components are reconstructed from the lower-case NPZ keys.
        assert records[0]["Mz_i"] == pytest.approx(6.0)
        assert records[1]["Mz_i"] == pytest.approx(12.0)
        assert records[0]["Fy_i"] == pytest.approx(2.0)
        # Components absent from the archive are omitted, not zero-padded.
        assert "Fx_i" not in records[0]
        # An archive without the block yields no records.
        assert _extract_npz_rs_forces(_minimal_npz_dict()) == []

    def test_resolve_source_rs_element_block(self):
        """An rs/elem_* archive resolves to an RS diagram (series + force_map)."""
        from fea_toolkit.plotting.force_diagram import _resolve_source

        # Auto-detected from the presence of the rs/elem_* block (no kind pin).
        data = _resolve_source(_minimal_rs_npz_dict(), None, None, False, None, "Mz")
        assert data.kind == "rs"
        assert data.force_unit == "kN"
        assert data.length_unit == "m"
        assert len(data.series) == 2
        assert data.series[0]["forces"]["MZ"] == pytest.approx(6.0)
        assert data.series[0]["forces"]["MZ_j"] == pytest.approx(-6.0)
        # Geometry came along, so the per-element 3D view is available: the
        # force map is keyed by frame index and exposes local variant keys
        # (the stored RS forces are already local).
        assert len(data.force_map) == 2
        assert data.force_map[0]["MZ_i_local"] == pytest.approx(6.0)
        assert data.frames  # geometry present for the 3D renderer

    def test_resolve_source_rs_explicit_kind_on_dict(self):
        """A pinned kind='rs' also reads the rs/elem_* block from a dict."""
        from fea_toolkit.plotting.force_diagram import _resolve_source

        data = _resolve_source(_minimal_rs_npz_dict(), None, None, False, "rs", "Fy")
        assert data.kind == "rs"
        assert len(data.series) == 2
        assert data.series[0]["forces"]["FY"] == pytest.approx(2.0)

    def test_resolve_source_rs_list(self):
        from fea_toolkit.plotting.force_diagram import _resolve_source

        records = [
            {"z_mid": 1.0, "My_i": 100.0, "My_j": -50.0},
            {"z_mid": 3.0, "My_i": 200.0, "My_j": -100.0},
        ]
        data = _resolve_source(records, kind="rs", quantity="My")
        assert data.kind == "rs"
        assert len(data.series) == 2
        assert data.series[0]["forces"]["MY"] == pytest.approx(100.0)
        assert data.series[0]["forces"]["MY_j"] == pytest.approx(-50.0)
        assert data.series[0]["z_mid"] == pytest.approx(1.0)

    def test_resolve_source_rs_dict_unwraps_units(self):
        from fea_toolkit.plotting.force_diagram import _resolve_source

        full = {
            "element_results": [{"z_mid": 2.0, "My_i": 5.0}],
            "units": {"F": "kN", "L": "m"},
        }
        data = _resolve_source(full, kind="rs", quantity="My")
        assert data.kind == "rs"
        assert data.force_unit == "kN"
        assert data.series[0]["forces"]["MY"] == pytest.approx(5.0)

    def test_dispatcher_static_2d(self):
        from fea_toolkit.plotting.force_diagram import plot_force_diagram

        fig = plot_force_diagram(_minimal_npz_dict(), quantity="My", dimension="2d", figsize=(6, 4))
        assert fig is not None
        import matplotlib.pyplot as plt

        plt.close(fig)

    @pytest.mark.needs_pyvista
    def test_dispatcher_static_3d_notebook(self):
        from fea_toolkit.plotting.force_diagram import plot_force_diagram

        pl = plot_force_diagram(_minimal_npz_dict(), quantity="My", dimension="3d", notebook=True)
        assert pl is not None
        pl.close()

    def test_dispatcher_rs_2d(self):
        from fea_toolkit.plotting.force_diagram import plot_force_diagram

        records = [
            {"z_mid": 1.0, "My_i": 100.0, "My_j": -50.0},
            {"z_mid": 3.0, "My_i": 200.0, "My_j": -100.0},
        ]
        fig = plot_force_diagram(records, quantity="My_i", kind="rs", figsize=(6, 4))
        assert fig is not None
        import matplotlib.pyplot as plt

        plt.close(fig)

    @pytest.mark.needs_pyvista
    def test_dispatcher_dimension_inference_3d(self):
        """With PyVista available + geometry present, 3D is inferred."""
        from fea_toolkit.plotting.force_diagram import plot_force_diagram

        pl = plot_force_diagram(_minimal_npz_dict(), quantity="My", notebook=True)
        assert pl is not None
        pl.close()

    def test_quantity_key_styles_equivalent(self):
        """'My_i' and 'My' resolve to the same series data."""
        from fea_toolkit.plotting.force_diagram import _resolve_source

        d1 = _resolve_source(_minimal_npz_dict(), quantity="My")
        d2 = _resolve_source(_minimal_npz_dict(), quantity="My_i")
        assert d1.series == d2.series
        assert d1.quantity == d2.quantity == "My"

    def test_shear_alias_vz_accepted_for_static(self):
        """'Vz'/'Vz_i' shear aliases plot the local Fz shear on static 2D."""
        import matplotlib.pyplot as plt

        from fea_toolkit.plotting.force_diagram import plot_force_diagram

        npz = _minimal_npz_dict()
        npz["static/DEAD/fz_i"] = np.array([12.0, -6.0])
        npz["static/DEAD/fz_j"] = np.array([-12.0, 6.0])
        for quantity in ("Vz", "Vz_i"):
            fig = plot_force_diagram(npz, quantity=quantity, dimension="2d", figsize=(6, 4))
            assert fig is not None, f"{quantity} rejected by the quantity gate"
            assert len(fig.axes[0].lines) > 0, f"{quantity} produced no lines"
            plt.close(fig)

    def test_unit_propagation_from_metadata(self):
        from fea_toolkit.plotting.force_diagram import _resolve_source

        npz = _minimal_npz_dict()
        npz["force_unit"] = np.array(["N"])
        npz["length_unit"] = np.array(["mm"])
        data = _resolve_source(npz, quantity="My")
        assert data.force_unit == "N"
        assert data.length_unit == "mm"

    def test_invalid_quantity_returns_none(self):
        from fea_toolkit.plotting.force_diagram import plot_force_diagram

        assert plot_force_diagram(_minimal_npz_dict(), quantity="ZZ", dimension="2d") is None

    @pytest.mark.needs_pyvista
    def test_npz_path_sources(self):
        """plot_force_diagram accepts NPZ paths for 2D and 3D rendering."""
        import os
        import tempfile

        from fea_toolkit.plotting import plot_force_diagram

        with tempfile.NamedTemporaryFile(suffix=".npz", delete=False) as f:
            path = f.name
        np.savez(path, **_minimal_npz_dict())
        try:
            fig = plot_force_diagram(path, quantity="My", kind="static", dimension="2d")
            assert fig is not None
            import matplotlib.pyplot as plt

            plt.close(fig)

            pl = plot_force_diagram(
                path, quantity="My", kind="static", dimension="3d", notebook=True
            )
            assert pl is not None
            pl.close()
        finally:
            os.remove(path)
