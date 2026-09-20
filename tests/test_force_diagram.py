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

    @pytest.mark.needs_pyvista
    def test_window_title_reaches_the_plotter(self):
        """``window_title`` is the VTK window caption; ``title`` stays in-plot.

        ``plot_force_diagram``'s ``title`` is drawn *inside* the plot
        (``add_text``), so the window caption needs its own keyword — this
        asserts which one actually reaches ``pyvista.Plotter()``.
        """
        from unittest.mock import patch

        import pyvista as pv

        from fea_toolkit.plotting.force_diagram import plot_force_diagram

        captured = {}
        real_init = pv.Plotter.__init__

        def _spy(self, *args, **kwargs):
            captured.update(kwargs)
            return real_init(self, *args, **kwargs)

        with patch.object(pv.Plotter, "__init__", _spy):
            pl = plot_force_diagram(
                _minimal_npz_dict(),
                quantity="My",
                dimension="3d",
                notebook=True,
                title="in-plot caption",
                window_title="PyVista - tower.s2k",
            )
        try:
            assert captured["title"] == "PyVista - tower.s2k"
        finally:
            pl.close()

    @pytest.mark.needs_pyvista
    def test_window_title_is_optional(self):
        """Omitting ``window_title`` passes ``None``, i.e. PyVista's default."""
        from unittest.mock import patch

        import pyvista as pv

        from fea_toolkit.plotting.force_diagram import plot_force_diagram

        captured = {}
        real_init = pv.Plotter.__init__

        def _spy(self, *args, **kwargs):
            captured.update(kwargs)
            return real_init(self, *args, **kwargs)

        with patch.object(pv.Plotter, "__init__", _spy):
            pl = plot_force_diagram(
                _minimal_npz_dict(), quantity="My", dimension="3d", notebook=True
            )
        try:
            assert captured["title"] is None
        finally:
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


# ============================================================================
# Two-sided (fork) pairing — response-spectrum combinations
# ============================================================================


def _forked_npz_dict(*, with_meta: bool = False) -> dict:
    """Minimal archive holding one two-sided combination as ``#1`` / ``#2``.

    ``#2`` is ``#1`` with the spectrum term negated, so the pair differs by
    twice the magnitude on every component.  With *with_meta* the explicit
    ``static_case_group`` / ``static_case_kind`` pairing arrays are written too.
    """
    data = _minimal_npz_dict()
    base = "COMB1_ULS 1.3GE+1.4QE(X)+0.3W(X)"
    names = ["DEAD", f"{base} #1", f"{base} #2"]
    data["static_case_labels"] = np.array(names)
    for name, my_i in (
        (names[0], [10.0, -5.0]),
        (names[1], [30.0, 15.0]),
        (names[2], [-10.0, -35.0]),
    ):
        data[f"static/{name}/my_i"] = np.array(my_i)
        data[f"static/{name}/my_j"] = np.array([-v for v in my_i])
    if with_meta:
        data["static_case_group"] = np.array([names[0], base, base])
        data["static_case_kind"] = np.array(["", "+QE", "-QE"])
    return data


class TestForkPairing:
    """A spectrum combination is two-sided, so the 2D profile draws both forks."""

    BASE = "COMB1_ULS 1.3GE+1.4QE(X)+0.3W(X)"

    def test_companion_resolved_from_the_label_convention(self):
        """``"<combo> #1"`` / ``"#2"`` pair with no metadata present."""
        from fea_toolkit.plotting.force_diagram import _companion_case

        data = _forked_npz_dict()
        assert _companion_case(data, f"{self.BASE} #1") == f"{self.BASE} #2"
        assert _companion_case(data, f"{self.BASE} #2") == f"{self.BASE} #1"

    def test_metadata_pairing_without_a_fork_label(self):
        """``static_case_group`` / ``static_case_kind`` pair renamed cases."""
        from fea_toolkit.plotting.force_diagram import _companion_case, _fork_legend

        data = _forked_npz_dict(with_meta=True)
        # Names the label convention cannot pair — only the metadata can.
        data["static_case_labels"] = np.array(["DEAD", "CQ", "CQ rev"])
        assert _companion_case(data, "CQ") == "CQ rev"
        assert _companion_case(data, "CQ rev") == "CQ"
        assert _fork_legend(data, "CQ").endswith("[+QE]")
        assert _fork_legend(data, "CQ rev").endswith("[-QE]")

    def test_plain_case_has_no_companion(self):
        from fea_toolkit.plotting.force_diagram import _companion_case, _fork_legend

        data = _forked_npz_dict()
        assert _companion_case(data, "DEAD") is None
        assert _fork_legend(data, "DEAD") == "DEAD"

    def test_ambiguous_group_is_left_unpaired(self):
        """Three variants of one group are not guessed at."""
        from fea_toolkit.plotting.force_diagram import _companion_case

        data = _forked_npz_dict()
        data["static_case_labels"] = np.array([f"{self.BASE} #{n}" for n in (1, 2, 3)])
        assert _companion_case(data, f"{self.BASE} #1") is None

    def test_both_sides_draws_two_curves(self):
        from fea_toolkit.plotting import plot_force_diagram

        data = _forked_npz_dict()
        # Local MY on a vertical member rotates to a global MX, so the summed
        # profile lives in MX — the values are the two ends of the pair.
        fig = plot_force_diagram(data, quantity="Mx", combo=f"{self.BASE} #1", dimension="2d")
        assert fig is not None
        curves = [ln for ln in fig.axes[0].lines if not ln.get_label().startswith("_")]
        assert len(curves) == 2
        assert curves[0].get_label().endswith("[+QE]")
        assert curves[1].get_label().endswith("[-QE]")
        # Both forks are drawn, and they are genuinely different data.
        assert list(curves[0].get_xdata()) == [15.0, 0.0]
        assert list(curves[1].get_xdata()) == [-35.0, 0.0]
        import matplotlib.pyplot as plt

        plt.close(fig)

    def test_both_sides_false_draws_one_curve(self):
        from fea_toolkit.plotting import plot_force_diagram

        data = _forked_npz_dict()
        fig = plot_force_diagram(
            data, quantity="Mx", combo=f"{self.BASE} #1", dimension="2d", both_sides=False
        )
        assert fig is not None
        curves = [ln for ln in fig.axes[0].lines if not ln.get_label().startswith("_")]
        assert len(curves) == 1
        assert list(curves[0].get_xdata()) == [15.0, 0.0]
        import matplotlib.pyplot as plt

        plt.close(fig)

    def test_by_storey_false_skips_the_group_extras(self):
        """The extras are storey profiles, so the per-element view has none."""
        from fea_toolkit.plotting.force_diagram import _resolve_source

        data = _forked_npz_dict()
        assert len(_resolve_source(data, combo=f"{self.BASE} #1", quantity="My").storey_extras) == 1
        per_element = _resolve_source(data, combo=f"{self.BASE} #1", quantity="My", by_storey=False)
        # The primary profile is still resolved (the caller ignores it) ...
        assert per_element.storey_series
        # ... but the other group members are not summed at all.
        assert per_element.storey_extras == []

    def test_group_member_without_a_profile_is_not_appended(self):
        """An empty group sum is omitted rather than drawn as a blank curve."""
        from fea_toolkit.plotting.force_diagram import _resolve_source

        data = _forked_npz_dict()
        # Strip the opposite fork's forces: it is still a case (the labels are
        # intact, so it is still grouped) but has no profile to sum.
        for key in [k for k in data if k.startswith(f"static/{self.BASE} #2/")]:
            del data[key]
        resolved = _resolve_source(data, combo=f"{self.BASE} #1", quantity="My")
        assert resolved.storey_series
        assert resolved.storey_extras == []


# ============================================================================
# Persisting the fork pairing (P20) — writer side + archive round-trip
# ============================================================================


def _nested_static_results() -> dict:
    """Two cases of one forked combination, in the writer's nested format."""
    return {
        "DEAD": {"element_forces": {"my_i": [10.0], "my_j": [-10.0]}},
        "CQ": {"element_forces": {"my_i": [30.0], "my_j": [-30.0]}},
        "CQ rev": {"element_forces": {"my_i": [-10.0], "my_j": [10.0]}},
    }


def _case_meta() -> dict:
    """``case_meta`` for :func:`_nested_static_results` — note the names do not fork."""
    return {
        "DEAD": {"group": "DEAD", "kind": ""},
        "CQ": {"group": "COMB9", "kind": "+QE"},
        "CQ rev": {"group": "COMB9", "kind": "-QE"},
    }


class TestForkMetadataRoundTrip:
    """``case_meta`` is persisted so the pairing survives without the labels."""

    def test_case_meta_arrays_align_and_default(self):
        from fea_toolkit.io.results_schema import case_meta_arrays

        # No metadata at all -> no arrays, so writers can update unconditionally
        # and an unannotated archive is identical to one written before P20.
        assert case_meta_arrays(["A"], None) == {}
        assert case_meta_arrays(["A"], {}) == {}
        out = case_meta_arrays(["A", "B", "C"], {"B": {"group": "G", "kind": "+QE"}})
        assert list(out["static_case_group"]) == ["", "G", ""]
        assert list(out["static_case_kind"]) == ["", "+QE", ""]

    def test_writer_metadata_pairs_renamed_cases(self):
        from fea_toolkit.io.unified_writer import collect_static_arrays
        from fea_toolkit.plotting.force_diagram import _case_pairs, _companion_case

        arrays = collect_static_arrays(_nested_static_results(), _case_meta())
        assert list(arrays["static_case_labels"]) == ["DEAD", "CQ", "CQ rev"]
        assert list(arrays["static_case_group"]) == ["DEAD", "COMB9", "COMB9"]
        assert list(arrays["static_case_kind"]) == ["", "+QE", "-QE"]
        # The names do not fork, so only the metadata can pair them.
        assert _case_pairs(arrays)["CQ"] == ("COMB9", 1)
        assert _companion_case(arrays, "CQ") == "CQ rev"
        assert _companion_case(arrays, "CQ rev") == "CQ"

    def test_archive_round_trip_pairs_from_metadata(self):
        import os
        import tempfile

        from fea_toolkit.io.npz_reader import read_results
        from fea_toolkit.io.unified_writer import collect_static_arrays
        from fea_toolkit.plotting.force_diagram import _companion_case

        arrays = collect_static_arrays(_nested_static_results(), _case_meta())
        with tempfile.NamedTemporaryFile(suffix=".npz", delete=False) as fh:
            path = fh.name
        np.savez(path, **arrays)
        try:
            data = read_results(path)
            assert "static_case_group" in data
            assert list(data["static_case_kind"]) == ["", "+QE", "-QE"]
            assert _companion_case(data, "CQ") == "CQ rev"
        finally:
            os.remove(path)

    def test_archive_without_metadata_pairs_by_label(self):
        from fea_toolkit.io.unified_writer import collect_static_arrays
        from fea_toolkit.plotting.force_diagram import _companion_case

        nested = {
            "DEAD": {"element_forces": {"my_i": [10.0]}},
            "COMB9 #1": {"element_forces": {"my_i": [30.0]}},
            "COMB9 #2": {"element_forces": {"my_i": [-10.0]}},
        }
        arrays = collect_static_arrays(nested)  # no case_meta
        assert "static_case_group" not in arrays
        assert "static_case_kind" not in arrays
        assert _companion_case(arrays, "COMB9 #1") == "COMB9 #2"

    def test_flat_results_get_metadata_for_the_unnamed_case(self):
        from fea_toolkit.io.unified_writer import collect_static_arrays

        arrays = collect_static_arrays({"fx_i": [1.0]}, {"1": {"group": "CASE1", "kind": ""}})
        assert list(arrays["static_case_labels"]) == ["1"]
        assert list(arrays["static_case_group"]) == ["CASE1"]

    def test_npz_writer_uses_the_same_collection(self):
        """``write_results_npz``'s collector accepts the same metadata."""
        from fea_toolkit.io.npz_writer import _collect_static

        arrays = _collect_static(_nested_static_results(), _case_meta())
        assert list(arrays["static_case_group"]) == ["DEAD", "COMB9", "COMB9"]
        assert list(arrays["static_case_kind"]) == ["", "+QE", "-QE"]
        # Without metadata the arrays are simply absent.
        assert "static_case_group" not in _collect_static(_nested_static_results())


# ============================================================================
# Family / coords metadata + multi-member grouping (P21)
# ============================================================================


def _multi_fork_results() -> dict:
    """A 2² fork — ``DEAD + RSX + RSY`` with all four sign corners present."""
    return {
        "DEAD": {"element_forces": {"my_i": [10.0]}},
        "COMB9 #1": {"element_forces": {"my_i": [110.0]}},
        "COMB9 #2": {"element_forces": {"my_i": [90.0]}},
        "COMB9 #3": {"element_forces": {"my_i": [-10.0]}},
        "COMB9 #4": {"element_forces": {"my_i": [-30.0]}},
    }


def _multi_fork_meta() -> dict:
    """``case_meta`` for :func:`_multi_fork_results` — coordinates, no ``kind``."""
    coords = {
        "COMB9 #1": "+RSX|+RSY",
        "COMB9 #2": "+RSX|-RSY",
        "COMB9 #3": "-RSX|+RSY",
        "COMB9 #4": "-RSX|-RSY",
    }
    meta = {"DEAD": {"group": "DEAD", "kind": "", "family": "single", "coords": ""}}
    meta.update(
        {
            name: {"group": "COMB9", "kind": "", "family": "fork", "coords": coord}
            for name, coord in coords.items()
        }
    )
    return meta


def _single_fork_definition() -> dict:
    """An external definition whose one magnitude reference is declared as such."""
    return {
        "COMB9": {
            "type": "Linear Add",
            "entries": [
                {"ref": "DEAD", "factor": 1.0},
                {"ref": "SPECIAL", "factor": 1.0, "magnitude": True},
            ],
        }
    }


class TestMultiMemberGrouping:
    """A 2ⁿ fork is grouped and labelled from its coordinates, not a #1/#2 pair."""

    def test_case_meta_arrays_write_family_and_coords(self):
        from fea_toolkit.io.results_schema import case_meta_arrays

        out = case_meta_arrays(["A", "B"], {"B": {"group": "G", "family": "fork", "coords": "+X"}})
        assert set(out) == {"static_case_group", "static_case_family", "static_case_coords"}
        assert list(out["static_case_family"]) == ["", "fork"]
        assert list(out["static_case_coords"]) == ["", "+X"]

    def test_fields_absent_from_the_metadata_are_not_written(self):
        from fea_toolkit.io.results_schema import case_meta_arrays

        # group/kind only — byte-identical to a pre-P21 archive.
        out = case_meta_arrays(["A"], {"A": {"group": "G", "kind": "+QE"}})
        assert set(out) == {"static_case_group", "static_case_kind"}

    def test_group_members_lists_every_corner(self):
        from fea_toolkit.io.unified_writer import collect_static_arrays
        from fea_toolkit.plotting.force_diagram import _companion_case, _group_members

        arrays = collect_static_arrays(_multi_fork_results(), _multi_fork_meta())
        assert _group_members(arrays, "COMB9 #1") == [
            "COMB9 #1",
            "COMB9 #2",
            "COMB9 #3",
            "COMB9 #4",
        ]
        assert _group_members(arrays, "DEAD") == ["DEAD"]
        # Sense pairing still refuses an ambiguous group; grouping does not.
        assert _companion_case(arrays, "COMB9 #1") is None

    def test_legend_uses_the_coordinates(self):
        from fea_toolkit.io.unified_writer import collect_static_arrays
        from fea_toolkit.plotting.force_diagram import _fork_legend

        arrays = collect_static_arrays(_multi_fork_results(), _multi_fork_meta())
        assert _fork_legend(arrays, "COMB9 #3") == "COMB9 [-RSX, +RSY]"
        assert _fork_legend(arrays, "DEAD") == "DEAD"

    def test_definition_supplies_grouping_without_archive_metadata(self):
        from fea_toolkit.plotting.force_diagram import _case_pairs, _group_members

        archive = {"static_case_labels": np.array(["COMB9 #1", "COMB9 #2"])}
        assert "static_case_group" not in archive
        definitions = _single_fork_definition()
        assert _case_pairs(archive, definitions) == {
            "COMB9 #1": ("COMB9", 1),
            "COMB9 #2": ("COMB9", -1),
        }
        assert _group_members(archive, "COMB9 #1", definitions) == ["COMB9 #1", "COMB9 #2"]

    def test_definition_pairs_read_the_magnitude_hint(self):
        from fea_toolkit.plotting.force_diagram import _definition_pairs

        assert _definition_pairs(_single_fork_definition()) == {
            "COMB9 #1": ("COMB9", 1),
            "COMB9 #2": ("COMB9", -1),
        }
        # Without the hint and without load_cases there is no magnitude, so the
        # definition contributes a single (unforked) variant.
        assert _definition_pairs(
            {"G": {"type": "Linear Add", "entries": [["DEAD", 1.0], ["SPECIAL", 1.0]]}}
        ) == {"G": ("G", 0)}

    def test_render_static_2d_draws_one_curve_per_group_member(self):
        import matplotlib.pyplot as plt

        from fea_toolkit.plotting.force_diagram import _render_static_2d

        def _series(value):
            return [
                {"elevation": 0.0, "forces": {"MY": value}},
                {"elevation": 3.0, "forces": {"MY": value / 2}},
            ]

        fig = _render_static_2d(
            [],
            "My",
            "kN",
            "m",
            True,
            None,
            (4, 3),
            storey_series=_series(10.0),
            storey_label="COMB9 [+RSX, +RSY]",
            storey_extras=[
                (_series(-10.0), "COMB9 [-RSX, +RSY]"),
                (_series(4.0), "COMB9 [-RSX, -RSY]"),
            ],
        )
        try:
            ax = fig.axes[0]
            # Three curves plus the vertical zero line.
            assert len(ax.get_lines()) == 4
            assert [t.get_text() for t in ax.get_legend().get_texts()] == [
                "COMB9 [+RSX, +RSY]",
                "COMB9 [-RSX, +RSY]",
                "COMB9 [-RSX, -RSY]",
            ]
        finally:
            plt.close(fig)

    def test_single_curve_draws_no_legend(self):
        import matplotlib.pyplot as plt

        from fea_toolkit.plotting.force_diagram import _render_static_2d

        series = [
            {"elevation": 0.0, "forces": {"MY": 10.0}},
            {"elevation": 3.0, "forces": {"MY": 5.0}},
        ]
        fig = _render_static_2d(
            [], "My", "kN", "m", True, None, (4, 3), storey_series=series, storey_label="COMB9"
        )
        try:
            ax = fig.axes[0]
            assert len(ax.get_lines()) == 2  # one curve + the zero line
            assert ax.get_legend() is None
        finally:
            plt.close(fig)
