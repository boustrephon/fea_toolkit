"""Test element property dataclasses and MeshModel field plumbing.

Tests cover dataclass defaults/fields for FrameElementProperties,
AreaElementProperties, NDMaterial, LayeredShellSection, and MeshModel
field acceptance. Does NOT test the Preprocessor's three-level
resolution (``_resolve_element_properties``) — see integration tests
in ``test_workflows.py`` or add dedicated tests there.
"""

import copy
from dataclasses import dataclass

import pytest

from fea_toolkit.model.sap_data import (
    FrameElementProperties, AreaElementProperties,
    NDMaterial, ShellFiberLayer, LayeredShellSection,
    ShellSection, FrameElement, AreaElement,
    Material, Node,
)
from fea_toolkit.model.mesh_model import MeshModel


# ═══════════════════════════════════════════════════════════════════
# FrameElementProperties
# ═══════════════════════════════════════════════════════════════════

class TestFrameElementProperties:
    """Verify FrameElementProperties dataclass fields and defaults."""

    def test_defaults(self):
        props = FrameElementProperties()
        assert props.element_type == "elasticBeamColumn"
        assert props.material_strategy == "elastic"
        assert props.integration_type is None
        assert props.num_integration_points == 0
        assert props.hinge_params is None

    def test_fiber_steel_with_hinges(self):
        props = FrameElementProperties(
            element_type="nonlinearBeamColumn",
            material_strategy="fiber_steel",
            integration_type="HingeRadau",
            num_integration_points=4,
            hinge_params={"lpI": 0.1, "lpJ": 0.1},
        )
        assert props.element_type == "nonlinearBeamColumn"
        assert props.integration_type == "HingeRadau"
        assert props.hinge_params == {"lpI": 0.1, "lpJ": 0.1}

    def test_truss_brace(self):
        props = FrameElementProperties(
            element_type="truss",
            material_strategy="steel02",
        )
        assert props.element_type == "truss"
        # Integration is irrelevant for trusses
        assert props.integration_type is None


# ═══════════════════════════════════════════════════════════════════
# AreaElementProperties
# ═══════════════════════════════════════════════════════════════════

class TestAreaElementProperties:
    """Verify AreaElementProperties dataclass fields and defaults."""

    def test_defaults(self):
        props = AreaElementProperties()
        assert props.element_type == "ShellMITC4"
        assert props.material_strategy == "elastic"
        assert props.thickness is None
        assert props.nd_material_names == []
        assert props.layer_stack == []

    def test_layered_shell(self):
        props = AreaElementProperties(
            element_type="ShellNLDKGQ",
            material_strategy="layered_rc",
            thickness=0.4,
            layer_stack=[
                ShellFiberLayer(0.05, "conc_unconfined", 3),
                ShellFiberLayer(0.30, "conc_confined", 8),
                ShellFiberLayer(0.05, "conc_unconfined", 3),
            ],
        )
        assert props.element_type == "ShellNLDKGQ"
        assert len(props.layer_stack) == 3
        assert props.layer_stack[1].thickness == 0.30

    def test_loads_only(self):
        props = AreaElementProperties(
            element_type=None,
            material_strategy="elastic",
        )
        assert props.element_type is None


# ═══════════════════════════════════════════════════════════════════
# NDMaterial / ShellFiberLayer / LayeredShellSection
# ═══════════════════════════════════════════════════════════════════

class TestNDMaterial:
    """Verify NDMaterial dataclass and to_tcl method."""

    def test_elastic_isotropic(self):
        mat = NDMaterial(name="concrete", material_type="ElasticIsotropic",
                         E=30e9, nu=0.2)
        tokens = mat.to_tcl(1).split()
        # Token structure: nDMaterial ElasticIsotropic <tag> <E> <nu>
        assert tokens[0] == "nDMaterial"          # command
        assert tokens[1] == "ElasticIsotropic"     # material type
        assert tokens[2] == "1"                    # tag (integer)
        assert float(tokens[3]) == pytest.approx(30e9, rel=1e-12)  # E
        assert float(tokens[4]) == pytest.approx(0.2, rel=1e-12)   # nu
        assert len(tokens) == 5                    # 5 tokens total

    def test_concrete_s(self):
        mat = NDMaterial(name="concrete_s", material_type="ConcreteS",
                         E=30e9, nu=0.2, fc=30e6, ft=3e6, Es=200e9)
        tcl = mat.to_tcl(2)
        assert "ConcreteS" in tcl
        assert "3e+07" in tcl  # fc

    def test_j2_plate_fibre(self):
        mat = NDMaterial(name="rebar", material_type="J2PlateFibre",
                         E=200e9, nu=0.3, fy=400e6, Hiso=0.0, Hkin=0.5e9)
        tcl = mat.to_tcl(3)
        assert "J2PlateFibre" in tcl


class TestLayeredShellSection:
    """Verify LayeredShellSection dataclass and to_tcl method."""

    def test_to_tcl(self):
        layers = [
            ShellFiberLayer(0.05, "conc_unconfined", 3),
            ShellFiberLayer(0.002, "rebar_smeared", 2),
            ShellFiberLayer(0.30, "conc_confined", 8),
            ShellFiberLayer(0.002, "rebar_smeared", 2),
            ShellFiberLayer(0.05, "conc_unconfined", 3),
        ]
        sec = LayeredShellSection(name="Wall400", layers=layers)
        mat_tags = {"conc_unconfined": 1, "conc_confined": 2, "rebar_smeared": 3}
        tcl = sec.to_tcl(100, mat_tags)

        # Parse the Tcl command into whitespace-delimited tokens.
        tokens = tcl.split()
        # Expected token sequence:
        #   section LayeredShell <tag> <nLayers>
        #   <matTag> <thickness>  (×5 layers — no nIP; nIP is metadata only)
        expected = [
            "section", "LayeredShell", "100", "5",
            "1", "0.05",
            "3", "0.002",
            "2", "0.3",
            "3", "0.002",
            "1", "0.05",
        ]
        assert tokens == expected, (
            f"Token mismatch\n  got:  {tokens}\n  want: {expected}"
        )
        # Also verify total token count.
        assert len(tokens) == 4 + 5 * 2  # 4 header + 5 layers × 2 tokens each

    def test_missing_material(self):
        layers = [ShellFiberLayer(0.1, "missing_mat")]
        sec = LayeredShellSection(name="Bad", layers=layers)
        with pytest.raises(KeyError):
            sec.to_tcl(10, {"other": 1})


# ═══════════════════════════════════════════════════════════════════
# MeshModel — verify new fields exist and are populated by Preprocessor
# ═══════════════════════════════════════════════════════════════════

class TestMeshModelNewFields:
    """Verify MeshModel accepts the new fields."""

    def test_new_fields_defaults(self):
        mm = MeshModel(
            nodes={},
            frame_elements={},
            frame_assignments={},
            area_elements={},
            area_assignments={},
            frame_dist_loads=[],
        )
        assert mm.frame_element_properties == {}
        assert mm.area_element_properties == {}
        assert mm.nd_materials == {}
        assert mm.layered_shell_sections == {}
        assert mm.diaphragm_components == []

    def test_new_fields_populated(self):
        fep = {"FRAME-1": FrameElementProperties(element_type="truss")}
        aep = {"AREA-1": AreaElementProperties(element_type=None)}
        ndm = {"concrete": NDMaterial(name="concrete")}
        lss = {"Wall400": LayeredShellSection(name="Wall400", layers=[])}
        mm = MeshModel(
            nodes={},
            frame_elements={},
            frame_assignments={},
            area_elements={},
            area_assignments={},
            frame_dist_loads=[],
            frame_element_properties=fep,
            area_element_properties=aep,
            nd_materials=ndm,
            layered_shell_sections=lss,
            diaphragm_components=[(3.0, ["master", "slave1", "slave2"])],
        )
        assert mm.frame_element_properties["FRAME-1"].element_type == "truss"
        assert mm.area_element_properties["AREA-1"].element_type is None
        assert mm.nd_materials["concrete"].material_type == "ElasticIsotropic"
        assert mm.diaphragm_components[0][0] == 3.0


# ═══════════════════════════════════════════════════════════════════
# Top-level module import verification
# ═══════════════════════════════════════════════════════════════════

def test_imports():
    """Verify all new public types are importable from sap_data."""
    assert FrameElementProperties is not None
    assert AreaElementProperties is not None
    assert NDMaterial is not None
    assert ShellFiberLayer is not None
    assert LayeredShellSection is not None
