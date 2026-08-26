"""Tests for :func:`fea_toolkit.model.source_resolver.resolve_model_source`."""

from examples.sample_model import make_rc_frame_model, make_sample_model
from fea_toolkit.io.stage_writer import write_model_stages
from fea_toolkit.model.source_resolver import resolve_model_source
from fea_toolkit.opensees.preprocessor import preprocess_model


class TestResolveFromModel:
    def test_sap_model_stage_inferred(self):
        md = make_sample_model()
        src = resolve_model_source(md)
        assert src.stage == "sap"
        assert len(src.nodes) == len(md.nodes)
        assert len(src.frame_elements) == len(md.frame_elements)

    def test_mesh_model_skips_inactive(self):
        md = make_rc_frame_model()
        mesh = preprocess_model(md, {"element_type": "elasticBeamColumn", "mesh_areas": True})
        # Force one inactive parent (simulating a split) and assert it is skipped.
        src = resolve_model_source(mesh)
        assert src.stage == "mesh"
        active = [e for e in mesh.frame_elements.values() if not getattr(e, "inactive", False)]
        assert len(src.frame_elements) == len(active)
        assert all(not getattr(e, "inactive", False) for e in src.frame_elements.values())

    def test_builder_source(self):
        from fea_toolkit.opensees.analysis_builder import AnalysisBuilder

        mesh = preprocess_model(make_rc_frame_model(), {"element_type": "elasticBeamColumn"})
        builder = AnalysisBuilder(mesh, config={})
        src = resolve_model_source(builder)
        assert src.stage == "mesh"
        assert len(src.nodes) == len(mesh.nodes)


class TestResolveFromStageFile:
    def test_from_dict_and_path(self, tmp_path):
        md = make_rc_frame_model()
        mesh = preprocess_model(md, {"element_type": "elasticBeamColumn", "mesh_areas": True})
        p = str(tmp_path / "m.h5")
        write_model_stages(p, sap=md, mesh=mesh, fmt="h5")

        src_path = resolve_model_source(p, stage="mesh")
        assert len(src_path.nodes) == len(mesh.nodes)
        assert len(src_path.frame_elements) == len(mesh.frame_elements)
        assert src_path.stage == "mesh"

    def test_sections_reconstructed_from_dict_blocks(self, tmp_path):
        from fea_toolkit.model.sap_data import Section

        md = make_rc_frame_model()
        mesh = preprocess_model(md, {"element_type": "elasticBeamColumn", "mesh_areas": True})
        p = str(tmp_path / "m.h5")
        write_model_stages(p, sap=md, mesh=mesh, fmt="h5")
        src = resolve_model_source(p, stage="sap")
        assert src.sections, "expected reconstructed sections"
        # Types must match the source sections exactly.
        assert {k: type(v) for k, v in src.sections.items()} == {
            k: type(v) for k, v in md.sections.items()
        }
        assert all(isinstance(s, Section) for s in src.sections.values())

    def test_units_reconstructed(self, tmp_path):
        md = make_sample_model()
        p = str(tmp_path / "m.h5")
        write_model_stages(p, sap=md, fmt="h5")
        src = resolve_model_source(p, stage="sap")
        assert src.units == md.units

    def test_tag_refs_translated_to_sap_ids(self):
        """The geometry arrays store node refs as OpenSees tags while the
        node dict is keyed by SAP ID — they differ when a mesh-created
        sub-node's ID is not its tag.  Frames/shells must resolve to the
        SAP IDs (regression: admin_v13 sub-nodes like '265_af_0_0_wi_1_0'
        with tag 1015 were previously connected to the wrong node)."""
        import numpy as np

        data = {
            "node_tag": np.array([1, 2, 1015]),
            "node_sap_id": np.array(["A", "B", "265_af_0_0_wi_1_0"]),
            "node_x": np.array([0.0, 5.0, 10.0]),
            "node_y": np.zeros(3),
            "node_z": np.zeros(3),
            "frame_sap_id": np.array(["F1", "F2"]),
            "frame_node_i": np.array([1, 1015]),
            "frame_node_j": np.array([2, 2]),
            "frame_elem_tag": np.array([10, 11]),
            "shell_sap_id": np.array(["S1"]),
            "shell_elem_tag": np.array([5]),
            "shell_node_ids_flat": np.array([1, 2, 1015, 1]),
            "shell_node_offsets": np.array([0, 4]),
            "shell_thickness": np.array([0.2]),
        }
        src = resolve_model_source(data, stage="mesh")
        assert src.nodes["265_af_0_0_wi_1_0"].node_tag == 1015
        # frame refs are translated tags -> SAP IDs
        assert src.frame_elements["F1"].node_i == "A"
        assert src.frame_elements["F1"].node_j == "B"
        assert src.frame_elements["F2"].node_i == "265_af_0_0_wi_1_0"
        # every frame/shell node ref resolves into the node dict
        assert all(
            f.node_i in src.nodes and f.node_j in src.nodes for f in src.frame_elements.values()
        )
        assert all(n in src.nodes for n in src.area_elements["S1"].node_ids)


class TestResolveErrors:
    def test_unsupported_source(self):
        import pytest

        with pytest.raises(TypeError, match="unsupported source type"):
            resolve_model_source(42)
