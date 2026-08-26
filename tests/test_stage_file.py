"""Tests for the self-describing stage file (``fea_toolkit.io.stage_writer`` /
``fea_toolkit.io.stage_reader``) — NPZ and HDF5 round-trips."""

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
from fea_toolkit.io.stage_writer import write_model_stages
from fea_toolkit.model.mesh_model import MeshModel
from fea_toolkit.opensees.preprocessor import preprocess_model

h5py = pytest.importorskip("h5py")


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    md = make_rc_frame_model()
    config = {"element_type": "elasticBeamColumn", "mesh_areas": True}
    mesh = preprocess_model(md, config)
    return md, mesh, config


@pytest.mark.parametrize("fmt", ["npz", "h5"])
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


class TestFormatParity:
    def test_same_payload_reads_back_identically(self, prepared, tmp_path):
        """The same payload must read back identically from npz and h5."""
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

    def test_bad_format(self, tmp_path):
        with pytest.raises(ValueError, match="Unsupported format"):
            write_model_stages(str(tmp_path / "x.json"), sap=make_sample_model(), fmt="json")

    def test_missing_stage_raises(self, tmp_path):
        p = str(tmp_path / "m.h5")
        write_model_stages(p, sap=make_sample_model(), fmt="h5")
        with pytest.raises(ValueError, match="no model payload"):
            read_model_stages(p, "mesh")

    def test_model_json_opt_out(self, tmp_path):
        p = str(tmp_path / "m.h5")
        write_model_stages(p, sap=make_sample_model(), mesh=None, model_json=False, fmt="h5")
        with pytest.raises(ValueError, match="no model payload"):
            read_model_stages(p, "sap")
        # Geometry arrays still available.
        assert len(read_stage_arrays(p, "sap")["node_x"]) == 2
