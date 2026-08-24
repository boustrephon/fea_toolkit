"""Test element property dataclasses and MeshModel field plumbing.

Tests cover dataclass defaults/fields for FrameElementProperties,
AreaElementProperties, NDMaterial, LayeredShellSection, and MeshModel
field acceptance. Does NOT test the Preprocessor's three-level
resolution (``_resolve_element_properties``) — see integration tests
in ``test_workflows.py`` or add dedicated tests there.
"""

import openseespy.opensees as ops
import pytest

import fea_toolkit.opensees._materials as mat_mod
from fea_toolkit.model.mesh_model import MeshModel, WallElement
from fea_toolkit.model.sap_data import (
    AreaElementProperties,
    FrameElementProperties,
    LayeredShellSection,
    Material,
    NDMaterial,
    Node,
    SAPModelData,
    ShellFiberLayer,
)
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
from fea_toolkit.opensees.builder import export_model_to_tcl
from fea_toolkit.opensees.recorder import RecordingOpenSees
from fea_toolkit.utils import (
    DEFAULT_RHO_MC_SI,
    g_from_units,
    mass_density_scale_factor,
)

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
        mat = NDMaterial(name="concrete", material_type="ElasticIsotropic", E=30e9, nu=0.2)
        tokens = mat.to_tcl(1).split()
        # Token structure: nDMaterial ElasticIsotropic <tag> <E> <nu>
        assert tokens[0] == "nDMaterial"  # command
        assert tokens[1] == "ElasticIsotropic"  # material type
        assert tokens[2] == "1"  # tag (integer)
        assert float(tokens[3]) == pytest.approx(30e9, rel=1e-12)  # E
        assert float(tokens[4]) == pytest.approx(0.2, rel=1e-12)  # nu
        assert len(tokens) == 5  # 5 tokens total

    def test_concrete_s(self):
        mat = NDMaterial(
            name="concrete_s", material_type="ConcreteS", E=30e9, nu=0.2, fc=30e6, ft=3e6, Es=200e9
        )
        tokens = mat.to_tcl(2).split()
        # nDMaterial ConcreteS <tag> <E> <nu> <fc> <ft> <Es>
        assert tokens[0] == "nDMaterial"
        assert tokens[1] == "ConcreteS"
        assert tokens[2] == "2"  # tag
        assert float(tokens[3]) == pytest.approx(30e9, rel=1e-12)  # E
        assert float(tokens[4]) == pytest.approx(0.2, rel=1e-12)  # nu
        assert float(tokens[5]) == pytest.approx(30e6, rel=1e-12)  # fc
        assert float(tokens[6]) == pytest.approx(3e6, rel=1e-12)  # ft
        assert float(tokens[7]) == pytest.approx(200e9, rel=1e-12)  # Es
        assert len(tokens) == 8

    def test_j2_plate_fibre(self):
        mat = NDMaterial(
            name="rebar",
            material_type="J2PlateFibre",
            E=200e9,
            nu=0.3,
            fy=400e6,
            Hiso=0.0,
            Hkin=0.5e9,
        )
        tokens = mat.to_tcl(3).split()
        # nDMaterial J2PlateFibre <tag> <E> <nu> <fy> <Hiso> <Hkin>
        assert tokens[0] == "nDMaterial"
        assert tokens[1] == "J2PlateFibre"
        assert tokens[2] == "3"  # tag
        assert float(tokens[3]) == pytest.approx(200e9, rel=1e-12)  # E
        assert float(tokens[4]) == pytest.approx(0.3, rel=1e-12)  # nu
        assert float(tokens[5]) == pytest.approx(400e6, rel=1e-12)  # fy
        assert float(tokens[6]) == pytest.approx(0.0, rel=1e-12)  # Hiso
        assert float(tokens[7]) == pytest.approx(0.5e9, rel=1e-12)  # Hkin
        assert len(tokens) == 8

    def test_plate_from_plane_stress(self):
        mat = NDMaterial(
            name="wall_ps",
            material_type="PlateFromPlaneStress",
            E=30e9,
            nu=0.2,
        )
        tokens = mat.to_tcl(5, wrapper_tag=3).split()
        # nDMaterial PlateFromPlaneStress <tag> <src> <eout>
        assert tokens[0] == "nDMaterial"
        assert tokens[1] == "PlateFromPlaneStress"
        assert tokens[2] == "5"  # tag
        assert tokens[3] == "3"  # src = wrapper_tag
        # eout = G = E / (2(1+nu)) when Eout is not set
        assert float(tokens[4]) == pytest.approx(30e9 / (2.0 * (1.0 + 0.2)), rel=1e-12)
        assert len(tokens) == 5

    def test_plate_from_plane_stress_eout_and_src(self):
        mat = NDMaterial(
            name="wall_ps",
            material_type="PlateFromPlaneStress",
            E=30e9,
            nu=0.2,
            Eout=12e9,
        )
        tcl = mat.to_tcl(5, wrapper_tag=3)
        assert float(tcl.split()[-1]) == pytest.approx(12e9, rel=1e-12)  # Eout override
        # Without wrapper_tag, src falls back to the material tag.
        assert mat.to_tcl(5).split()[3] == "5"

    def test_fsam_to_tcl(self):
        mat = NDMaterial(
            name="wall_fsam",
            material_type="FSAM",
            density=2400.0,
            sx="SteelX",
            sy="SteelY",
            conc="ConcreteCM",
            rou_x=0.004,
            rou_y=0.004,
            nu=0.2,
            alfadow=45.0,
        )
        mat_tags = {
            "SteelX": 1,
            "SteelY": 2,
            "ConcreteCM": 3,
        }
        tokens = mat.to_tcl(10, mat_tags=mat_tags).split()
        # Command: nDMaterial FSAM <tag> <rho> <sX> <sY> <conc> <rouX> <rouY> <nu> <alfadow>
        assert tokens[0] == "nDMaterial"
        assert tokens[1] == "FSAM"
        assert tokens[2] == "10"  # tag
        assert float(tokens[3]) == pytest.approx(2400.0, rel=1e-12)  # rho
        assert tokens[4] == "1"  # SteelX → tag 1
        assert tokens[5] == "2"  # SteelY → tag 2
        assert tokens[6] == "3"  # ConcreteCM → tag 3
        assert float(tokens[7]) == pytest.approx(0.004, rel=1e-12)  # rouX
        assert float(tokens[8]) == pytest.approx(0.004, rel=1e-12)  # rouY
        assert float(tokens[9]) == pytest.approx(0.2, rel=1e-12)  # nu
        assert float(tokens[10]) == pytest.approx(45.0, rel=1e-12)  # alfadow
        assert len(tokens) == 11

    def test_fsam_requires_mat_tags(self):
        mat = NDMaterial(
            name="wall_fsam",
            material_type="FSAM",
            density=2400.0,
            sx="SteelX",
            sy="SteelY",
            conc="ConcreteCM",
        )
        # Missing mat_tags entirely → LookupError
        with pytest.raises(LookupError, match="requires mat_tags"):
            mat.to_tcl(1)
        # Referenced material not present → LookupError listing the names
        with pytest.raises(LookupError, match="SteelX"):
            mat.to_tcl(1, mat_tags={"SteelY": 2, "ConcreteCM": 3})

    def test_fsam_rejects_unset_references(self):
        mat = NDMaterial(
            name="wall_fsam",
            material_type="FSAM",
            density=2400.0,
            sx="SteelX",
            sy="",  # unset
            conc="ConcreteCM",
        )
        # Empty reference names (the dataclass default) are unset fields —
        # reported per-field, not as missing tags.
        with pytest.raises(LookupError) as exc:
            mat.to_tcl(1, mat_tags={"SteelX": 1, "ConcreteCM": 3})
        msg = str(exc.value)
        assert "unset" in msg
        assert "sy" in msg
        assert "sx" not in msg  # set reference is not reported

    def test_fsam_rejects_multiple_unset_references(self):
        mat = NDMaterial(
            name="wall_fsam",
            material_type="FSAM",
            density=2400.0,
            sx="SteelX",
            sy="",
            conc="",
        )
        with pytest.raises(LookupError) as exc:
            mat.to_tcl(1, mat_tags={"SteelX": 1})
        msg = str(exc.value)
        assert "unset" in msg
        assert "sy" in msg
        assert "conc" in msg
        assert "sx" not in msg


class TestFSAMUniaxialDispatch:
    """Verify AnalysisBuilder emits ConcreteCM / Steel02 for FSAM-referenced
    uniaxial materials (required for getCrackingStrain), and Elastic for
    non-FSAM materials."""

    def _make_builder(self):
        """Build a minimal AnalysisBuilder with one concrete + one steel
        material (both FSAM-referenced) plus a non-FSAM steel material.

        The FSAM nD material is *consumed* by a LayeredShell section layer
        — only consumed FSAM materials force ConcreteCM / Steel02 for
        their referenced uniaxial laws (see the unconsumed-FSAM test).
        """
        # Model units: kN, m → stress in kN/m².  Fc/Fy and E_mod are authored
        # directly in model units (30 MPa → 30.0e3, 420 MPa → 420.0e3,
        # 250 MPa → 250.0e3 kN/m²; 30/200 GPa → 30.0e6/200.0e6 kN/m²) —
        # _create_materials consumes material Fc/Fy/E_mod unscaled, treating
        # them as already in model units.
        mm = MeshModel(
            nodes={},
            frame_elements={},
            frame_assignments={},
            area_elements={},
            area_assignments={},
            frame_dist_loads=[],
            materials={
                "WallConc": Material(name="WallConc", type="Concrete", Fc=30.0e3, E_mod=30.0e6),
                "WallSteel": Material(name="WallSteel", type="Rebar", Fy=420.0e3, E_mod=200.0e6),
                "FrameSteel": Material(name="FrameSteel", type="Steel", Fy=250.0e3, E_mod=200.0e6),
            },
            nd_materials={
                "wall_fsam": NDMaterial(
                    name="wall_fsam",
                    material_type="FSAM",
                    density=2400.0,
                    sx="WallSteel",
                    sy="WallSteel",
                    conc="WallConc",
                    rou_x=0.004,
                    rou_y=0.004,
                    nu=0.2,
                    alfadow=45.0,
                ),
            },
            layered_shell_sections={
                "wall_layer": LayeredShellSection(
                    name="wall_layer",
                    layers=[ShellFiberLayer(0.3, "wall_fsam")],
                ),
            },
            units={"F": "kN", "L": "m", "T": "C"},
        )
        return AnalysisBuilder(mm, {"verbose": False})

    def test_create_materials_fsam_laws(self):
        builder = self._make_builder()
        rec = RecordingOpenSees(ops)
        mat_mod.ops = rec
        try:
            builder._create_materials()
        finally:
            mat_mod.ops = ops
            ops.wipe()

        # Tags are auto-assigned: WallConc=1, WallSteel=2, FrameSteel=3.
        assert builder.material_tags["WallConc"] == 1
        assert builder.material_tags["WallSteel"] == 2
        assert builder.material_tags["FrameSteel"] == 3

        # FSAM-referenced materials must be emitted as ConcreteCM / Steel02
        # (only these implement getCrackingStrain() for FSAM); the non-FSAM
        # steel keeps the legacy Elastic path.
        laws = {
            args[1]: args[0] for name, args, _kwargs in rec.commands if name == "uniaxialMaterial"
        }
        assert laws[1] == "ConcreteCM"  # WallConc
        assert laws[2] == "Steel02"  # WallSteel
        assert laws[3] == "Elastic"  # FrameSteel

    def test_create_materials_unconsumed_fsam_uses_elastic(self):
        """A configured-but-unconsumed FSAM nD material does NOT force
        ConcreteCM/Steel02 for its referenced uniaxial laws — those keep
        the generic Elastic path (only *consumed* FSAM participates, i.e.
        FSAM referenced by a LayeredShell layer or wall element)."""
        builder = self._make_builder()
        # Drop the consuming LayeredShell section → the FSAM is unconsumed.
        builder.mesh_model.layered_shell_sections = {}
        rec = RecordingOpenSees(ops)
        mat_mod.ops = rec
        try:
            builder._create_materials()
        finally:
            mat_mod.ops = ops
            ops.wipe()

        laws = {
            args[1]: args[0] for name, args, _kwargs in rec.commands if name == "uniaxialMaterial"
        }
        assert laws[1] == "Elastic"  # WallConc — not forced to ConcreteCM
        assert laws[2] == "Elastic"  # WallSteel — not forced to Steel02
        assert laws[3] == "Elastic"  # FrameSteel

    def test_build_domain_unconsumed_fsam_skipped(self):
        """``build_domain()`` must not create a configured-but-unconsumed FSAM
        nD material.

        ``_create_fsam_materials()`` only creates FSAM materials actually
        consumed by a LayeredShell layer or an SFI/E_SFI wall element.  When
        ``_fsam_consumed`` exists but is empty (configured FSAM, no consumer),
        it must still return early — otherwise the FSAM nD material is
        created against the generic Elastic uniaxial laws, which lack
        ``getCrackingStrain()`` and crash the OpenSees FSAM constructor.
        """
        builder = self._make_builder()
        # Drop the consuming LayeredShell section → the FSAM is unconsumed.
        builder.mesh_model.layered_shell_sections = {}
        rec = RecordingOpenSees(ops)
        mat_mod.ops = rec
        try:
            builder.build_domain()
        finally:
            mat_mod.ops = ops
            ops.wipe()

        fsam_calls = [
            args
            for name, args, _kwargs in rec.commands
            if name == "nDMaterial" and args and args[0] == "FSAM"
        ]
        assert fsam_calls == []

    def test_fsam_laws_supported_by_wheel(self):
        """Fail loudly if the wheel lacks ConcreteCM / Steel02 support.

        ``_create_materials`` wraps every ``uniaxialMaterial`` call in
        ``contextlib.suppress``, so the emission assertions above would
        pass even on an OpenSeesPy build that cannot actually create
        these laws.  This raw, non-suppressed call raises (fails loudly)
        on a wheel that lacks ConcreteCM or Steel02.
        """
        try:
            ops.wipe()
            # Model units kN, m → 30 MPa = 30e3 kN/m², 420 MPa = 4.2e5 kN/m².
            # Production emits the negative-compression convention (fpc,
            # epcc, xcrn negative) — matching _create_materials and
            # test_mvlem_3d_supported_by_wheel in test_wall_pushover.py.
            ops.uniaxialMaterial(
                "ConcreteCM", 1, -30.0e3, -0.002, 30.0e6, 5.0, -0.0002, 3.0e3, 0.0001, 1.5, 0.0001
            )
            ops.uniaxialMaterial("Steel02", 2, 4.2e5, 200.0e6, 0.01)
        finally:
            ops.wipe()

    def test_create_materials_non_fsam_elastic(self):
        """Non-FSAM concrete/steel materials keep the legacy Elastic path."""
        mm = MeshModel(
            nodes={},
            frame_elements={},
            frame_assignments={},
            area_elements={},
            area_assignments={},
            frame_dist_loads=[],
            materials={
                "FrameConc": Material(name="FrameConc", type="Concrete", Fc=30.0e6, E_mod=30.0e9),
            },
            units={"F": "kN", "L": "m", "T": "C"},
        )
        builder = AnalysisBuilder(mm, {"verbose": False})
        rec = RecordingOpenSees(ops)
        mat_mod.ops = rec
        try:
            builder._create_materials()
        finally:
            mat_mod.ops = ops
            ops.wipe()
        # No FSAM nd_materials → all materials stay Elastic.
        assert builder.material_tags["FrameConc"] == 1
        laws = {
            args[1]: args[0] for name, args, _kwargs in rec.commands if name == "uniaxialMaterial"
        }
        assert laws[1] == "Elastic"


class TestTclExportFSAM:
    """Verify export_model_to_tcl emits ConcreteCM / Steel02 for
    FSAM-referenced materials in the generated Tcl file."""

    def test_export_fsam_materials(self, tmp_path):
        units = {"F": "kN", "L": "m", "T": "C"}
        md = SAPModelData(
            nodes={},
            restraints={},
            materials={
                "WallConc": Material(name="WallConc", type="Concrete", Fc=30.0e6, E_mod=30.0e9),
                "WallSteel": Material(name="WallSteel", type="Rebar", Fy=420.0e6, E_mod=200.0e9),
            },
            sections={},
            frame_elements={},
            area_elements={},
            frame_assignments={},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
            nd_materials={
                "wall_fsam": NDMaterial(
                    name="wall_fsam",
                    material_type="FSAM",
                    density=2400.0,
                    sx="WallSteel",
                    sy="WallSteel",
                    conc="WallConc",
                    rou_x=0.004,
                    rou_y=0.004,
                    nu=0.2,
                    alfadow=45.0,
                ),
            },
            units=units,
        )
        out = tmp_path / "fsam.tcl"
        export_model_to_tcl(md, str(out), config={"create_fiber_sections": False})
        text = out.read_text()

        # FSAM-referenced walls must use ConcreteCM / Steel02.
        assert "uniaxialMaterial ConcreteCM 1" in text
        assert "uniaxialMaterial Steel02 2" in text
        # The FSAM nD material references the uniaxial tags.
        assert "nDMaterial FSAM 3" in text
        assert "uniaxialMaterial Concrete01" not in text
        assert "uniaxialMaterial Steel01" not in text


class TestTclExportPlateFromPlaneStress:
    """Verify export_model_to_tcl emits the PlateFromPlaneStress wrapper
    with the derived out-of-plane shear modulus G = E / (2(1+nu))."""

    def _model(self, eout=None) -> SAPModelData:
        units = {"F": "kN", "L": "m", "T": "C"}
        return SAPModelData(
            nodes={},
            restraints={},
            materials={
                "WallConc": Material(name="WallConc", type="Concrete", Fc=30.0e6, E_mod=30.0e9),
            },
            sections={},
            frame_elements={},
            area_elements={},
            frame_assignments={},
            area_assignments={},
            groups={},
            frame_auto_mesh={},
            nd_materials={
                "wall_cs": NDMaterial(
                    name="wall_cs",
                    material_type="ConcreteS",
                    E=30.0e9,
                    nu=0.2,
                    fc=30.0e6,
                    ft=3.0e6,
                    Eout=eout,
                ),
            },
            units=units,
        )

    def _plate_line(self, tmp_path, md) -> str:
        out = tmp_path / "plate.tcl"
        export_model_to_tcl(md, str(out), config={"create_fiber_sections": False})
        return next(
            line
            for line in out.read_text().splitlines()
            if "nDMaterial PlateFromPlaneStress" in line
        )

    def test_export_derives_eout(self, tmp_path):
        line = self._plate_line(tmp_path, self._model())
        assert float(line.split()[-1]) == pytest.approx(30e9 / (2.0 * (1.0 + 0.2)), rel=1e-12)

    def test_export_honors_eout(self, tmp_path):
        line = self._plate_line(tmp_path, self._model(eout=12.0e9))
        assert float(line.split()[-1]) == pytest.approx(12.0e9, rel=1e-12)


class TestTclExportMVLEMRho:
    """Verify export_model_to_tcl emits a unit-aware ``-rho`` fallback for
    MVLEM_3D walls, matching ``AnalysisBuilder._create_mvlem3d_wall``."""

    def _wall_line(self, tmp_path, rho=None, unit_weight=24.0) -> str:
        mm = MeshModel(
            nodes={
                "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
                "2": Node(node_id="2", node_tag=2, x=4.0, y=0.0, z=0.0),
                "3": Node(node_id="3", node_tag=3, x=4.0, y=0.0, z=4.0),
                "4": Node(node_id="4", node_tag=4, x=0.0, y=0.0, z=4.0),
            },
            frame_elements={},
            frame_assignments={},
            area_elements={},
            area_assignments={},
            frame_dist_loads=[],
            materials={
                "concrete": Material(
                    name="concrete",
                    type="Concrete",
                    Fc=30.0e3,
                    E_mod=30.0e6,
                    unit_weight=unit_weight,
                ),
                "steel": Material(
                    name="steel",
                    type="Rebar",
                    Fy=420.0e3,
                    E_mod=200.0e6,
                ),
            },
            wall_elements={
                "W1": WallElement(
                    elem_id="W1",
                    elem_tag=10,
                    node_ids=["1", "2", "3", "4"],
                    m=2,
                    thick=[0.3, 0.3],
                    width=[2.0, 2.0],
                    fsam_material_names=[],
                    material_type="uniaxial",
                    concrete_names=["concrete", "concrete"],
                    steel_names=["steel", "steel"],
                    shear_name="concrete",
                    rho=rho,
                ),
            },
            units={"F": "kN", "L": "m", "T": "C"},
        )
        out = tmp_path / "wall_mvlem.tcl"
        export_model_to_tcl(mm, str(out), config={"create_fiber_sections": False})
        return next(line for line in out.read_text().splitlines() if "element MVLEM_3D" in line)

    @staticmethod
    def _rho_values(line: str) -> list[float]:
        return [
            float(v) for v in line.split("-rho")[1].split("-matConcrete", maxsplit=1)[0].split()
        ]

    def test_fallback_derives_from_concrete_unit_weight(self, tmp_path):
        line = self._wall_line(tmp_path, unit_weight=24.0)
        expect = 24.0 / g_from_units({"F": "kN", "L": "m", "T": "C"})
        assert all(abs(v - expect) < 1e-9 for v in self._rho_values(line))

    def test_explicit_rho_preserved(self, tmp_path):
        line = self._wall_line(tmp_path, rho=[2400.0, 2400.0])
        assert self._rho_values(line) == [2400.0, 2400.0]

    def test_fallback_scales_si_default_when_no_unit_weight(self, tmp_path):
        line = self._wall_line(tmp_path, unit_weight=0.0)
        expect = DEFAULT_RHO_MC_SI * mass_density_scale_factor({"F": "kN", "L": "m", "T": "C"})
        assert all(abs(v - expect) < 1e-9 for v in self._rho_values(line))


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
            "section",
            "LayeredShell",
            "100",
            "5",
            "1",
            "0.05",
            "3",
            "0.002",
            "2",
            "0.3",
            "3",
            "0.002",
            "1",
            "0.05",
        ]
        assert tokens == expected, f"Token mismatch\n  got:  {tokens}\n  want: {expected}"
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
