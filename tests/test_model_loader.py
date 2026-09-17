"""Tests for ``fea_toolkit.io.model_loader`` — model-file dispatch.

Covers the three recognised inputs (SAP2000 text, raw-table JSON, model-codec
JSON), the validation that makes an unrecognised payload a loud error rather
than a silently empty model, and the package-level export.
"""

import json
from pathlib import Path

import pytest

from fea_toolkit.io import load_model_data
from fea_toolkit.model.mesh_model import MeshModel
from fea_toolkit.model.sap_data import (
    FrameElement,
    ISection,
    Node,
    SAPModelData,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _sample_model():
    """Small SAPModelData for codec round-trips."""
    return SAPModelData(
        nodes={
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 4.0, 0.0, 0.0),
        },
        restraints={},
        materials={},
        sections={
            "COL": ISection(
                name="COL",
                shape="W16x31",
                material="Steel",
                A=0.2,
                I33=0.02,
                I22=0.02,
                J=0.0,
                depth=0.2,
                bf=0.2,
                tf=0.02,
                tw=0.02,
            )
        },
        frame_elements={"1": FrameElement("1", 1, "1", "2")},
        area_elements={},
        frame_assignments={"1": "COL"},
        area_assignments={},
        groups={},
        frame_auto_mesh={},
    )


def _mesh_snapshot():
    """Post-preprocessing MeshModel — codec-serialisable, not analysable."""
    return MeshModel(
        nodes={"1": Node("1", 1, 0.0, 0.0, 0.0)},
        frame_elements={"1": FrameElement("1", 1, "1", "1")},
        frame_assignments={},
        area_elements={},
        area_assignments={},
        frame_dist_loads=[],
    )


class TestTextAndTableInputs:
    """The SAP2000 text export and the parser's raw-table JSON cache."""

    def test_s2k_text_export(self):
        md = load_model_data(FIXTURES / "clean_model.s2k")
        assert isinstance(md, SAPModelData)
        assert md.nodes and md.frame_elements

    def test_raw_table_json_cache(self):
        """A ``SAP2000Parser.to_json()`` cache rebuilds the same model."""
        md = load_model_data(FIXTURES / "sample.json")
        assert isinstance(md, SAPModelData)
        assert md.nodes and md.frame_elements

    def test_unknown_suffix_is_rejected(self, tmp_path):
        """An unrecognised extension must not fall through to a text parse."""
        path = tmp_path / "model.txt"
        path.write_text('TABLE:  "JOINT COORDINATES"\n', encoding="utf-8")
        with pytest.raises(ValueError, match="unsupported model file"):
            load_model_data(path)

    def test_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_model_data(tmp_path / "nope.s2k")


class TestCodecJsonInputs:
    """Model-codec snapshots (``model_codec.model_to_json``)."""

    def test_sap_model_snapshot_round_trips(self, tmp_path):
        from fea_toolkit.io.model_codec import model_to_json

        original = _sample_model()
        path = tmp_path / "model.json"
        path.write_text(model_to_json(original), encoding="utf-8")

        loaded = load_model_data(path)
        assert isinstance(loaded, SAPModelData)
        assert loaded == original

    def test_mesh_snapshot_loads_as_a_mesh_model(self, tmp_path):
        from fea_toolkit.io.model_codec import model_to_json

        path = tmp_path / "mesh.json"
        path.write_text(model_to_json(_mesh_snapshot()), encoding="utf-8")

        assert isinstance(load_model_data(path), MeshModel)

    def test_newer_schema_version_is_rejected(self, tmp_path):
        """A snapshot from a newer build fails loudly, not silently."""
        from fea_toolkit.io.model_codec import SCHEMA_KEY, model_to_dict

        payload = model_to_dict(_sample_model())
        payload[SCHEMA_KEY] = 999
        path = tmp_path / "future.json"
        path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(ValueError, match="upgrade fea_toolkit"):
            load_model_data(path)

    def test_malformed_json(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")

        with pytest.raises(ValueError, match="not valid JSON"):
            load_model_data(path)

    def test_unrecognised_json(self, tmp_path):
        """A JSON dict that is neither snapshot nor tables is an error."""
        path = tmp_path / "bad.json"
        path.write_text('{"a": 1}', encoding="utf-8")

        with pytest.raises(ValueError, match="neither a model snapshot"):
            load_model_data(path)

    def test_empty_json(self, tmp_path):
        path = tmp_path / "empty.json"
        path.write_text("{}", encoding="utf-8")

        with pytest.raises(ValueError, match="neither a model snapshot"):
            load_model_data(path)
