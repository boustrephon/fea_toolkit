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


class TestResolveErrors:
    def test_unsupported_source(self):
        import pytest

        with pytest.raises(TypeError, match="unsupported source type"):
            resolve_model_source(42)
