"""Tests for :mod:`fea_toolkit.model.mesh_model` dataclass validation.

``WallElement.__post_init__`` validates per-fibre list lengths (against
``m``) before any OpenSees command generation, so a mismatched list fails
fast with an error that names the wall element and the offending field.
"""

import pytest

from fea_toolkit.model.mesh_model import WallElement


def _valid_wall(**overrides) -> WallElement:
    """Return a valid 3-fibre SFI_MVLEM_3D WallElement with optional overrides."""
    params = {
        "elem_id": "W1",
        "elem_tag": 30,
        "node_ids": ["1", "2", "4", "3"],
        "m": 3,
        "thick": [0.2, 0.2, 0.2],
        "width": [1.0, 1.0, 1.0],
        "fsam_material_names": ["FSAM_bdry", "FSAM_core", "FSAM_bdry"],
    }
    params.update(overrides)
    return WallElement(**params)


class TestWallElementPerFiberValidation:
    def test_valid_fsam_wall_accepted(self):
        wall = _valid_wall()
        assert len(wall.thick) == wall.m
        assert len(wall.width) == wall.m
        assert len(wall.fsam_material_names) == wall.m

    def test_thick_length_mismatch_raises(self):
        with pytest.raises(ValueError, match=r"'thick'.*m=3"):
            _valid_wall(thick=[0.2, 0.2])

    def test_width_length_mismatch_raises(self):
        with pytest.raises(ValueError, match=r"'width'"):
            _valid_wall(width=[1.0] * 4)

    def test_fsam_material_names_length_mismatch_raises(self):
        with pytest.raises(ValueError, match=r"'fsam_material_names'"):
            _valid_wall(fsam_material_names=["FSAM_bdry"])

    def test_error_message_identifies_wall_element_and_field(self):
        with pytest.raises(ValueError, match=r"WallElement 'W1'.*'thick'"):
            _valid_wall(thick=[0.2])

    def test_uniaxial_wall_empty_fsam_list_accepted(self):
        # MVLEM_3D deliberately carries an empty fsam_material_names list
        # (FSAM materials are not applicable); the per-fibre uniaxial
        # lists still must match m.
        wall = _valid_wall(
            material_type="uniaxial",
            fsam_material_names=[],
            concrete_names=["concrete"] * 3,
            steel_names=["steel"] * 3,
            rho=[2400.0] * 3,
        )
        assert wall.fsam_material_names == []

    def test_uniaxial_concrete_names_mismatch_raises(self):
        with pytest.raises(ValueError, match=r"'concrete_names'.*m=3"):
            _valid_wall(
                material_type="uniaxial",
                fsam_material_names=[],
                concrete_names=["concrete", "concrete"],
                steel_names=["steel"] * 3,
                rho=[2400.0] * 3,
            )

    def test_uniaxial_steel_names_mismatch_raises(self):
        with pytest.raises(ValueError, match=r"'steel_names'"):
            _valid_wall(
                material_type="uniaxial",
                fsam_material_names=[],
                concrete_names=["concrete"] * 3,
                steel_names=["steel"] * 5,
                rho=[2400.0] * 3,
            )

    def test_uniaxial_rho_mismatch_raises(self):
        with pytest.raises(ValueError, match=r"'rho'"):
            _valid_wall(
                material_type="uniaxial",
                fsam_material_names=[],
                concrete_names=["concrete"] * 3,
                steel_names=["steel"] * 3,
                rho=[2400.0],
            )

    def test_optional_lists_none_accepted(self):
        # concrete_names / steel_names / rho are None on FSAM walls.
        wall = _valid_wall()
        assert wall.concrete_names is None
        assert wall.steel_names is None
        assert wall.rho is None

    def test_zero_m_raises(self):
        with pytest.raises(ValueError, match=r"m must be at least 1, got 0"):
            _valid_wall(m=0, thick=[], width=[], fsam_material_names=[])

    def test_negative_m_raises(self):
        with pytest.raises(ValueError, match=r"m must be at least 1, got -1"):
            _valid_wall(m=-1, thick=[], width=[], fsam_material_names=[])
