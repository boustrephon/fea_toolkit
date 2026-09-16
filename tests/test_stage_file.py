"""Tests for the self-describing stage file (``fea_toolkit.io.stage_writer`` /
``fea_toolkit.io.stage_reader``) — NPZ and HDF5 round-trips — plus the
unified-writer archive-schema collectors (``fea_toolkit.io.unified_writer``)."""

from pathlib import Path

import pytest

from examples.sample_model import make_rc_frame_model, make_sample_model
from fea_toolkit.io import (
    get_schema_version,
    read_dictionary_arrays,
    read_metadata,
    read_model_stages,
    read_stage_arrays,
)
from fea_toolkit.io.npz_reader import read_results
from fea_toolkit.io.s2k_parser import SAP2000Parser
from fea_toolkit.io.stage_writer import write_model_stages
from fea_toolkit.model.mesh_model import MeshModel
from fea_toolkit.model.sap_data import SAPModelData
from fea_toolkit.opensees.preprocessor import preprocess_model


def _h5py_available() -> bool:
    """Return ``True`` when the optional HDF5 dependency can be imported."""
    try:
        import h5py  # noqa: F401
    except ImportError:
        return False
    return True


_HAS_H5PY = _h5py_available()

#: The ``h5`` parametrisation skips — only that case — when h5py is absent, so
#: the ``npz`` variants and the solver-free collector tests still run.
_H5_FORMAT = pytest.param(
    "h5", marks=pytest.mark.skipif(not _HAS_H5PY, reason="h5py not installed")
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _parse(name: str) -> SAPModelData:
    """Parse a test fixture and return its model data."""
    parser = SAP2000Parser(FIXTURES_DIR / name)
    parser.parse()
    return parser.get_model_data()


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    md = make_rc_frame_model()
    config = {"element_type": "elasticBeamColumn", "mesh_areas": True}
    mesh = preprocess_model(md, config)
    return md, mesh, config


def _static_validation_fixture(n_node: int, n_frame: int, case: str = "DEAD") -> dict:
    """Build a minimal geometry + static skeleton for ``validate_arrays``.

    Returns a dict carrying clean geometry arrays, ``analysis_types =
    ['static']`` and ``static_case_labels``, but **without** any
    ``static/<case>/*`` payload arrays — callers add the ones under test.
    Node/frame counts are caller-controlled so the N_node ≠ N_frame case can
    be exercised.
    """
    import numpy as np

    data = {
        "node_tag": np.arange(1, n_node + 1, dtype=int),
        "node_sap_id": np.array([str(i) for i in range(1, n_node + 1)], dtype=str),
        "node_x": np.zeros(n_node),
        "node_y": np.zeros(n_node),
        "node_z": np.zeros(n_node),
        "frame_eid": np.arange(n_frame, dtype=int),
        "frame_sap_id": np.array([str(i + 1) for i in range(n_frame)], dtype=str),
        "frame_parent_sap_id": np.array([""] * n_frame, dtype=str),
        "frame_sec_name": np.array(["B1"] * n_frame, dtype=str),
        "frame_node_i": np.ones(n_frame, dtype=int),
        "frame_node_j": np.ones(n_frame, dtype=int),
        # One quad shell keeps the geometry block satisfied independently of
        # the frame count under test.
        "shell_eid": np.array([0], dtype=int),
        "shell_sap_id": np.array(["S1"], dtype=str),
        "shell_parent_sap_id": np.array([""], dtype=str),
        "shell_sec_name": np.array(["ST1"], dtype=str),
        "shell_node_1": np.array([1], dtype=int),
        "shell_node_2": np.array([1], dtype=int),
        "shell_node_3": np.array([1], dtype=int),
        "shell_node_4": np.array([1], dtype=int),
        "analysis_types": np.array(["static"], dtype=str),
        "static_case_labels": np.array([case], dtype=str),
    }
    return data


@pytest.mark.parametrize("fmt", ["npz", _H5_FORMAT])
class TestStageFile:
    def test_model_round_trip(self, prepared, tmp_path, fmt):
        md, mesh, config = prepared
        p = str(tmp_path / f"model.{fmt}")
        write_model_stages(p, sap=md, mesh=mesh, config=config, fmt=fmt)

        assert read_model_stages(p, "sap") == md
        mesh2, cfg = read_model_stages(p, "mesh", return_config=True)
        assert mesh2 == mesh
        assert cfg == config
        assert isinstance(mesh2, MeshModel)

    def test_geometry_arrays(self, prepared, tmp_path, fmt):
        md, mesh, config = prepared
        p = str(tmp_path / f"model.{fmt}")
        write_model_stages(p, sap=md, mesh=mesh, config=config, fmt=fmt)

        arr = read_stage_arrays(p, "mesh")
        assert len(arr["frame_sap_id"]) == len(mesh.frame_elements)
        # Ragged shell connectivity must be self-consistent.
        assert len(arr["shell_node_offsets"]) == len(arr["shell_eid"]) + 1
        assert arr["shell_node_offsets"][-1] == len(arr["shell_node_ids_flat"])
        # Node coordinates present.
        assert len(arr["node_x"]) == len(mesh.nodes)

    def test_dictionary_blocks(self, prepared, tmp_path, fmt):
        md, mesh, config = prepared
        p = str(tmp_path / f"model.{fmt}")
        write_model_stages(p, sap=md, mesh=mesh, config=config, fmt=fmt)

        blocks = read_dictionary_arrays(p, "sap")
        for name in (
            "sections_json",
            "materials_json",
            "groups_json",
            "restraints_json",
            "units_json",
            "frame_element_types_json",
        ):
            assert name in blocks, name
        # Sections carry the shape dimensions + type discriminator.
        for sec in blocks["sections_json"].values():
            assert "type" in sec

    def test_metadata_and_version(self, prepared, tmp_path, fmt):
        md, mesh, config = prepared
        p = str(tmp_path / f"model.{fmt}")
        write_model_stages(p, sap=md, mesh=mesh, config=config, fmt=fmt)

        meta = read_metadata(p)
        assert set(meta["stages"]) == {"sap", "mesh"}
        assert meta["units"]["L"] == md.units["L"]

        data = read_results(p)
        assert get_schema_version(data) >= 2

    def test_pushover_results_round_trip(self, prepared, tmp_path, fmt):
        """Pushover step arrays for multiple directions survive the round-trip."""
        from fea_toolkit.io import flatten_stage
        from fea_toolkit.rhino.results import (
            _load_deformed_arrays,
            _load_pushover_shell_quantities,
        )

        md, mesh, config = prepared
        fids = list(mesh.frame_elements)[:2]
        step_results = [
            {
                "step": s,
                "frame_forces": {
                    str(f): {
                        "fx": float(s),
                        "fy": 0.0,
                        "fz": 0.0,
                        "mx": 0.0,
                        "my": 0.0,
                        "mz": float(s) * 10,
                    }
                    for f in fids
                },
                "shell_forces": {
                    "S1": {
                        "Nx": float(s) * 5,
                        "Ny": 0.0,
                        "Nxy": 0.0,
                        "Mx": 0.0,
                        "My": 0.0,
                        "Mxy": 0.0,
                    }
                },
                "node_displacements": {
                    nd.node_tag: (0.0, 0.0, float(s) * 0.01) for nd in list(mesh.nodes.values())[:1]
                },
            }
            for s in (1, 2, 3)
        ]
        po_results = {
            "step": [0, 1, 2, 3],
            "control_disp": [0.0, 0.1, 0.2, 0.3],
            "base_shear": [0.0, 5.0, 10.0, 15.0],
        }

        p = str(tmp_path / f"model_po.{fmt}")
        write_model_stages(
            p,
            sap=md,
            mesh=mesh,
            config=config,
            fmt=fmt,
            static_results={"DEAD": {"nodal_displacements": {}, "element_forces": {}}},
            modal_result={"periods": [1.0, 0.5], "modal_props": {}},
            pushover_results={
                "+X": (step_results, po_results),
                "-Y": (step_results, po_results),
            },
        )

        data = read_results(p)
        assert "pushover/+X/shell_Nx" in data
        assert "pushover/-Y/shell_Nx" in data
        assert "pushover/+X/control_disp" in data
        assert list(data["analysis_types"]) == ["static", "modal", "pushover"]

        # Global arrays are aligned to the *recorded* (converged) steps.
        assert list(data["pushover/+X/control_disp"]) == [0.1, 0.2, 0.3]
        assert list(data["pushover/+X/step"]) == [1, 2, 3]

        # The Rhino results helpers read it back directly.
        values, _range = _load_pushover_shell_quantities(data, "Nx", direction="+X")
        assert values == {"S1": 15.0}  # last step
        assert _load_deformed_arrays(data, "pushover", direction="+X")[4] == "pushover/+X/step2"

        # flatten_stage exposes the geometry to legacy consumers.
        flat = flatten_stage(p, stage="mesh")
        assert "frame_sap_id" in flat
        assert "shell_parent_sap_id" in flat

    def test_response_spectrum_results_round_trip(self, prepared, tmp_path, fmt):
        """RS arrays are written under the canonical ``rs/*`` keys."""
        _md, mesh, _config = prepared
        p = str(tmp_path / f"rs.{fmt}")
        rs_x = {
            "modal_periods": [1.0, 0.5],
            "modal_base_shear": [120.0, 30.0],
            "base_shear_cqc": 130.0,
            "base_shear_srss": 125.0,
            "base_moment_cqc": 900.0,
            "base_moment_srss": 880.0,
            "roof_disp_cqc": 0.012,
            "roof_disp_srss": 0.013,
        }
        rs_y = dict(rs_x, modal_base_shear=[0.0, 200.0], base_shear_cqc=205.0)
        write_model_stages(p, mesh=mesh, rs_results={"rs_x": rs_x, "rs_y": rs_y}, fmt=fmt)

        data = read_results(p)
        assert "rs" in [str(t) for t in data["analysis_types"]]
        assert list(data["rs/period"]) == [1.0, 0.5]
        assert list(data["rs/v_base_x"]) == [120.0, 30.0]
        assert data["rs/v_cqc_y"][0] == 205.0
        assert data["rs/m_cqc_x"][0] == 900.0
        assert data["rs/roof_disp_srss_x"][0] == 0.013


class TestFormatParity:
    def test_same_payload_reads_back_identically(self, prepared, tmp_path):
        """The same payload must read back identically from npz and h5."""
        pytest.importorskip("h5py")
        md, mesh, config = prepared
        npz = str(tmp_path / "m.npz")
        h5 = str(tmp_path / "m.h5")
        write_model_stages(npz, sap=md, mesh=mesh, config=config, fmt="npz")
        write_model_stages(h5, sap=md, mesh=mesh, config=config, fmt="h5")
        assert read_model_stages(npz, "mesh") == read_model_stages(h5, "mesh")
        assert set(read_stage_arrays(npz, "mesh")) == set(read_stage_arrays(h5, "mesh"))


class TestStageFileValidation:
    def test_requires_a_stage(self, tmp_path):
        with pytest.raises(ValueError, match="at least one"):
            write_model_stages(str(tmp_path / "x.npz"))

    def test_validate_arrays_accepts_ragged_and_fixed_shells(self):
        """The schema validator accepts both fixed-quad and ragged shell layouts."""
        import numpy as np

        from fea_toolkit.io.results_schema import validate_arrays

        base = {
            "node_tag": np.array([1, 2, 3, 4], dtype=int),
            "node_sap_id": np.array(["1", "2", "3", "4"], dtype=str),
            "node_x": np.zeros(4),
            "node_y": np.zeros(4),
            "node_z": np.zeros(4),
            "frame_eid": np.array([0], dtype=int),
            "frame_sap_id": np.array(["1"], dtype=str),
            "frame_parent_sap_id": np.array([""], dtype=str),
            "frame_sec_name": np.array(["B1"], dtype=str),
            "frame_node_i": np.array([1], dtype=int),
            "frame_node_j": np.array([2], dtype=int),
            "shell_eid": np.array([0], dtype=int),
            "shell_sap_id": np.array(["S1"], dtype=str),
            "shell_parent_sap_id": np.array([""], dtype=str),
            "shell_sec_name": np.array(["ST1"], dtype=str),
            "analysis_types": np.array(["geometry"], dtype=str),
        }

        # Fixed-quad layout validates cleanly.
        fixed = dict(base)
        fixed.update(
            shell_node_1=np.array([1], dtype=int),
            shell_node_2=np.array([2], dtype=int),
            shell_node_3=np.array([3], dtype=int),
            shell_node_4=np.array([4], dtype=int),
        )
        assert validate_arrays(fixed) == []

        # Ragged layout validates cleanly and does not require the quads.
        ragged = dict(base)
        ragged.update(
            shell_node_ids_flat=np.array([1, 2, 3, 4], dtype=int),
            shell_node_offsets=np.array([0, 4], dtype=int),
        )
        assert validate_arrays(ragged) == []

        # Ragged without the offsets partner is reported.
        bad = dict(ragged)
        bad["shell_node_offsets"] = np.array([], dtype=int)
        msgs = validate_arrays(bad)
        assert any("shell_node_offsets" in m for m in msgs)

    def test_validate_arrays_static_displacement_dimension(self):
        """Nodal displacements are checked against N_node, element forces N_frame.

        Regression: every static array used to be validated against N_frame,
        which reported a false shape mismatch for ``node_dx``/``dy``/``dz`` on
        any model where the node and frame counts differ.
        """
        import numpy as np

        from fea_toolkit.io.results_schema import (
            STATIC_FORCE_ARRAYS,
            STATIC_NODAL_ARRAYS,
            validate_arrays,
        )

        n_node, n_frame = 4, 1  # deliberately different
        data = _static_validation_fixture(n_node, n_frame)
        for name in STATIC_NODAL_ARRAYS:
            data[f"static/DEAD/{name}"] = np.zeros(n_node)
        for name in STATIC_FORCE_ARRAYS:
            data[f"static/DEAD/{name}"] = np.zeros(n_frame)

        # No local arrays, N_node != N_frame → must still validate clean.
        assert validate_arrays(data) == []

        # A genuinely wrong-length displacement array is still reported.
        bad = dict(data)
        bad["static/DEAD/node_dx"] = np.zeros(n_frame)
        assert any(
            "static/DEAD/node_dx" in m and "Shape mismatch" in m for m in validate_arrays(bad)
        )

        # A missing global force array is still reported.
        missing = dict(data)
        missing.pop("static/DEAD/mz_j")
        assert any("static/DEAD/mz_j" in m for m in validate_arrays(missing))

    def test_validate_arrays_local_forces_optional(self):
        """Local-frame static forces are optional but validated when present."""
        import numpy as np

        from fea_toolkit.io.results_schema import (
            STATIC_FORCE_ARRAYS,
            STATIC_LOCAL_FORCE_ARRAYS,
            STATIC_NODAL_ARRAYS,
            validate_arrays,
        )

        n_node, n_frame = 2, 3
        data = _static_validation_fixture(n_node, n_frame)
        for name in STATIC_NODAL_ARRAYS:
            data[f"static/DEAD/{name}"] = np.zeros(n_node)
        for name in STATIC_FORCE_ARRAYS:
            data[f"static/DEAD/{name}"] = np.zeros(n_frame)

        # Absent local arrays are not reported as "missing" — the standard
        # writers record global-frame end forces only.
        assert not any("_local" in m for m in validate_arrays(data))

        # Present-and-correct local arrays validate clean.
        with_local = dict(data)
        for name in STATIC_LOCAL_FORCE_ARRAYS:
            with_local[f"static/DEAD/{name}"] = np.zeros(n_frame)
        assert validate_arrays(with_local) == []

        # Present-but-wrong-length local arrays are caught.
        bad_local = dict(with_local)
        bad_local["static/DEAD/mz_i_local"] = np.zeros(n_node)
        assert any("mz_i_local" in m for m in validate_arrays(bad_local))

    def test_validate_arrays_rs_element_and_node_blocks_optional(self):
        """Element/nodal RS arrays are optional; the combined block is required.

        The model review exports the ``rs/node_*`` block but does not compute
        ``rs/elem_*``; a per-mode-scalars-only producer writes neither.
        """
        import numpy as np

        from fea_toolkit.io.results_schema import RS_ARRAYS, validate_arrays

        n_node, n_mode = 2, 2
        data = _static_validation_fixture(n_node, 1)
        data["analysis_types"] = np.array(["rs"], dtype=str)
        data.pop("static_case_labels")
        # N_mode is resolved from modal/period, so the per-mode rs arrays
        # share its length.
        data["modal/period"] = np.zeros(n_mode)
        for key, (shape_desc, _dtype) in RS_ARRAYS.items():
            if key.startswith(("rs/elem_", "rs/node_")):
                continue  # the optional blocks — deliberately absent
            data[key] = np.zeros(n_mode if shape_desc else 1)

        assert validate_arrays(data) == []

        # A required combined RS array is still reported when absent.
        missing = dict(data)
        missing.pop("rs/v_cqc_x")
        assert any("rs/v_cqc_x" in m for m in validate_arrays(missing))

        # Present optional blocks are shape-checked.
        with_blocks = dict(data)
        with_blocks["rs/node_tag"] = np.arange(1, n_node + 1, dtype=int)
        with_blocks["rs/node_dx"] = np.zeros(n_node)
        with_blocks["rs/elem_sap_id"] = np.array(["1"], dtype=str)
        assert validate_arrays(with_blocks) == []

        bad_blocks = dict(with_blocks)
        bad_blocks["rs/node_dx"] = np.zeros(n_node + 1)
        assert any("rs/node_dx" in m for m in validate_arrays(bad_blocks))

    def test_bad_format(self, tmp_path):
        with pytest.raises(ValueError, match="Unsupported format"):
            write_model_stages(str(tmp_path / "x.json"), sap=make_sample_model(), fmt="json")

    def test_missing_stage_raises(self, tmp_path):
        pytest.importorskip("h5py")
        p = str(tmp_path / "m.h5")
        write_model_stages(p, sap=make_sample_model(), fmt="h5")
        with pytest.raises(ValueError, match="no model payload"):
            read_model_stages(p, "mesh")

    def test_model_json_opt_out(self, tmp_path):
        pytest.importorskip("h5py")
        p = str(tmp_path / "m.h5")
        write_model_stages(p, sap=make_sample_model(), mesh=None, model_json=False, fmt="h5")
        with pytest.raises(ValueError, match="no model payload"):
            read_model_stages(p, "sap")
        # Geometry arrays still available.
        assert len(read_stage_arrays(p, "sap")["node_x"]) == 2


# ═══════════════════════════════════════════════════════════════════
# Unified-writer schema coverage — solver-free
# ═══════════════════════════════════════════════════════════════════


class TestUnifiedWriterSchemaCoverage:
    """The unified collectors must emit every visualiser-consumed key.

    These guard the arrays that the legacy ``write_results_npz`` produced
    but the unified collectors originally omitted — losing them silently
    degrades mode-shape animation (row alignment) and parent-collapse
    visualisation to wrong geometry rather than failing loudly.
    """

    def test_geometry_arrays_cover_parent_collapse_keys(self):
        from fea_toolkit.io._serial import collect_geometry_arrays

        arrays = collect_geometry_arrays(_parse("sample.s2k"))
        # collapse_to_parents reads these (0 = no split parent).
        assert "frame_parent_node_i" in arrays
        assert "frame_parent_node_j" in arrays
        assert len(arrays["frame_parent_node_i"]) == len(arrays["frame_node_i"])

    def test_modal_arrays_cover_row_alignment_key(self):
        from fea_toolkit.io.unified_writer import collect_modal_arrays

        modal = {"periods": [1.0, 0.5], "modal_props": {}}
        shapes = {0: {3: (0.1, 0.0, 0.0), 1: (0.2, 0.0, 0.0)}}
        arrays = collect_modal_arrays(modal, mode_shapes=shapes)
        # mode_dx/y/z rows are in sorted-tag order while the geometry
        # node_tag array is not, so the alignment array must be written.
        assert list(arrays["modal/node_tag"]) == [1, 3]
        assert arrays["modal/mode_dx"].shape == (2, 2)

    def test_modal_arrays_cover_six_dof_participation(self):
        """All six participation ratios are archived, not just translations.

        The writer originally copied only ``partiMassRatiosMX/MY/MZ`` — it
        mirrored the console table, which printed just %X/%Y/%Z.  Six-DOF
        participation was therefore absent from *every* archive, and the
        mode-shape annotation had no RX/RY/RZ row to read.
        """
        from fea_toolkit.io.unified_writer import collect_modal_arrays

        modal = {
            "periods": [1.0, 0.5],
            "modal_props": {
                "partiMassRatiosMX": [10.0, 1.0],
                "partiMassRatiosMY": [20.0, 2.0],
                "partiMassRatiosMZ": [0.1, 0.2],
                "partiMassRatiosRMX": [3.0, 0.3],
                "partiMassRatiosRMY": [4.0, 0.4],
                "partiMassRatiosRMZ": [5.0, 0.5],
            },
        }
        arrays = collect_modal_arrays(modal)
        for key in (
            "modal/mx_ratio",
            "modal/my_ratio",
            "modal/mz_ratio",
            "modal/rx_ratio",
            "modal/ry_ratio",
            "modal/rz_ratio",
        ):
            assert key in arrays, key
        # OpenSees's own values, copied verbatim.
        assert list(arrays["modal/rx_ratio"]) == [3.0, 0.3]

    def test_npz_writer_modal_collector_stays_in_sync(self):
        """``npz_writer._collect_modal`` must not drift from the unified one.

        The two were verbatim copies and drifted: the rotational ratios were
        added to one and not the other, so archives written through the
        model-review path (``unified_writer.write_results``) silently lacked
        them.  ``npz_writer`` now delegates; this pins the two in step.
        """
        import numpy as np

        from fea_toolkit.io.npz_writer import _collect_modal
        from fea_toolkit.io.unified_writer import collect_modal_arrays

        modal = {"periods": [1.0], "modal_props": {"partiMassRatiosRMZ": [7.5]}}
        shapes = {0: {1: (0.1, 0.2, 0.3)}}
        unified = collect_modal_arrays(modal, mode_shapes=shapes)
        delegated = _collect_modal(modal, mode_shapes=shapes)
        assert set(unified) == set(delegated)
        for key, value in unified.items():
            assert np.array_equal(value, delegated[key]), key

    def test_npz_writer_rs_collector_stays_in_sync(self):
        """``npz_writer._collect_rs`` must not drift from the unified one.

        The two were separate implementations and drifted: only the legacy
        copy wrote the per-mode ``rs/sa_*`` / ``rs/eff_mass_*`` /
        ``rs/v_total_*`` keys, and only the unified copy wrote ``rs/period``
        plus the combined ``rs/v_srss_*`` / ``rs/m_*`` / ``rs/roof_disp_*``
        scalars.  ``npz_writer`` now delegates; this pins the two in step.
        """
        import numpy as np

        from fea_toolkit.io.npz_writer import _collect_rs
        from fea_toolkit.io.unified_writer import collect_rs_arrays

        rs = {
            "modal_periods": [1.0, 0.5],
            "modal_base_shear": [120.0, 30.0],
            "spectral_accels": [1.1, 2.2],
            "effective_masses": [500.0, 100.0],
            "base_shear_cqc": 130.0,
            "base_shear_srss": 125.0,
            "base_shear_total": 140.0,
            "base_moment_cqc": 900.0,
            "base_moment_srss": 880.0,
            "roof_disp_cqc": 0.012,
            "roof_disp_srss": 0.013,
        }
        rs_y = dict(rs, modal_base_shear=[0.0, 200.0], base_shear_cqc=205.0)
        unified = collect_rs_arrays(rs_x=rs, rs_y=rs_y)
        delegated = _collect_rs(rs, rs_y)
        assert set(unified) == set(delegated)
        for key, value in unified.items():
            assert np.array_equal(value, delegated[key]), key

    def test_collect_rs_arrays_carries_legacy_per_mode_keys(self):
        """The per-mode ``sa`` / effective-mass / total-shear keys survive.

        These three families existed only in ``npz_writer._collect_rs``;
        consolidating on one collector must not drop them from the archive.
        """
        from fea_toolkit.io.unified_writer import collect_rs_arrays

        arrays = collect_rs_arrays(
            rs_x={
                "modal_periods": [1.0, 0.5],
                "modal_base_shear": [120.0, 30.0],
                "spectral_accels": [1.1, 2.2],
                "effective_masses": [500.0, 100.0],
                "base_shear_total": 140.0,
            }
        )
        assert list(arrays["rs/sa_x"]) == [1.1, 2.2]
        assert list(arrays["rs/eff_mass_x"]) == [500.0, 100.0]
        assert arrays["rs/v_total_x"][0] == 140.0
        # A producer that omits them still yields the keys — empty per-mode
        # arrays and a zero total — rather than dropping the block.
        bare = collect_rs_arrays(rs_x={"modal_periods": [1.0]})
        assert list(bare["rs/sa_x"]) == []
        assert list(bare["rs/eff_mass_x"]) == []
        assert bare["rs/v_total_x"][0] == 0.0

    def test_npz_writer_rs_archive_validates(self, tmp_path):
        """``write_results_npz`` RS archives satisfy ``validate_arrays``.

        Before consolidation the legacy collector omitted ``rs/period`` and
        the combined moment/roof scalars, so every archive it wrote reported
        missing RS arrays.
        """
        from fea_toolkit.io.npz_writer import write_results_npz
        from fea_toolkit.io.results_schema import validate_npz

        md = make_sample_model()
        tags = sorted(node.node_tag for node in md.nodes.values())
        mode_shapes = {
            0: dict.fromkeys(tags, (0.010, 0.0, 0.0)),
            1: dict.fromkeys(tags, (0.020, 0.0, 0.0)),
        }
        rs_x = {
            "modal_periods": [1.0, 0.5],
            "modal_base_shear": [120.0, 30.0],
            "spectral_accels": [1.1, 2.2],
            "effective_masses": [500.0, 100.0],
            "base_shear_cqc": 130.0,
            "base_shear_srss": 125.0,
            "base_shear_total": 140.0,
            "base_moment_cqc": 900.0,
            "base_moment_srss": 880.0,
            "roof_disp_cqc": 0.012,
            "roof_disp_srss": 0.013,
        }
        rs_y = dict(rs_x, modal_base_shear=[0.0, 200.0], base_shear_cqc=205.0)
        p = str(tmp_path / "rs_archive.npz")
        write_results_npz(
            p,
            md,
            modal_result={"periods": [1.0, 0.5], "modal_props": {}},
            mode_shapes=mode_shapes,
            rs_results={"rs_x": rs_x, "rs_y": rs_y},
        )
        assert validate_npz(p) == []

    def test_collect_rs_arrays_writes_moment_and_roof(self):
        """The unified collector emits the extended canonical ``rs/*`` block."""
        from fea_toolkit.io.unified_writer import collect_rs_arrays

        arrays = collect_rs_arrays(
            rs_x={
                "modal_periods": [1.0, 0.5],
                "modal_base_shear": [100.0, 20.0],
                "base_shear_cqc": 110.0,
                "base_shear_srss": 105.0,
                "base_moment_cqc": 900.0,
                "base_moment_srss": 880.0,
                "roof_disp_cqc": 0.0123,
                "roof_disp_srss": 0.0130,
            },
            rs_y=None,
        )
        assert list(arrays["rs/period"]) == [1.0, 0.5]
        assert list(arrays["rs/v_base_x"]) == [100.0, 20.0]
        assert arrays["rs/v_cqc_x"][0] == 110.0
        assert arrays["rs/v_srss_x"][0] == 105.0
        assert arrays["rs/m_cqc_x"][0] == 900.0
        assert arrays["rs/m_srss_x"][0] == 880.0
        assert arrays["rs/roof_disp_cqc_x"][0] == 0.0123
        assert arrays["rs/roof_disp_srss_x"][0] == 0.0130
        # Only the X direction was supplied.
        assert "rs/v_cqc_y" not in arrays
        # A producer without moment/roof data (the scalar ``cqc_base_shear``
        # path) still yields the keys, defaulted to zero.
        bare = collect_rs_arrays(rs_x={"modal_periods": [1.0], "base_shear_cqc": 5.0})
        assert bare["rs/m_srss_x"][0] == 0.0
        assert bare["rs/roof_disp_cqc_x"][0] == 0.0

    def test_collect_rs_element_force_arrays_full_block(self):
        """The RS element block carries the full local set, labels and aliases."""
        from fea_toolkit.io.unified_writer import collect_rs_element_force_arrays

        rs_forces = {
            "combination": "srss",
            "direction": "X",
            "element_results": [
                {
                    "elem_id": "1",
                    "z_bot": 0.0,
                    "z_mid": 1.5,
                    "Fx_i": 1.0,
                    "Fy_i": 2.0,
                    "Fz_i": 3.0,
                    "Mx_i": 4.0,
                    "My_i": 5.0,
                    "Mz_i": 6.0,
                    "Fx_j": 7.0,
                    "Fy_j": 8.0,
                    "Fz_j": 9.0,
                    "Mx_j": 10.0,
                    "My_j": 11.0,
                    "Mz_j": 12.0,
                    # Deprecated aliases written alongside the canonical keys.
                    "Vy_i": 2.0,
                    "Vy_j": 8.0,
                    "Vz_i": 3.0,
                    "Vz_j": 9.0,
                }
            ],
        }
        arrays = collect_rs_element_force_arrays(rs_forces)
        # Full canonical set, lower-case keys mirroring static fx_i … mz_j.
        for key in (
            "rs/elem_fx_i",
            "rs/elem_fy_i",
            "rs/elem_fz_i",
            "rs/elem_mx_i",
            "rs/elem_my_i",
            "rs/elem_mz_i",
            "rs/elem_fx_j",
            "rs/elem_fy_j",
            "rs/elem_fz_j",
            "rs/elem_mx_j",
            "rs/elem_my_j",
            "rs/elem_mz_j",
        ):
            assert key in arrays, key
        assert arrays["rs/elem_mz_i"][0] == 6.0
        assert arrays["rs/elem_fx_j"][0] == 7.0
        # Self-describing labels.
        assert arrays["rs/elem_combination"][0] == "srss"
        assert arrays["rs/elem_direction"][0] == "X"
        # ── Deprecated aliases (DEPRECATED — delete with the 2D-only readers).
        assert arrays["rs/elem_Vy_i"][0] == 2.0
        assert arrays["rs/elem_My_i"][0] == 5.0
        # A producer without element forces writes nothing at all.
        assert collect_rs_element_force_arrays(None) == {}
