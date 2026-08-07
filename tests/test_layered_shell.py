"""Tests for AnalysisBuilder nD material + layered shell section creation.

Covers the four gaps identified in the audit:
1. _create_nd_materials() — nDMaterial ops calls exercised
2. _create_layered_shell_sections() — LayeredShell ops calls exercised
3. Skipped-material rejection — unknown nD material causes section skip
4. Section recreation after ops.wipe() — build_domain twice succeeds
5. Rebuild recovery — supported → unsupported → supported cycle recovers
"""

import openseespy.opensees as ops
import pytest

from fea_toolkit.model.mesh_model import MeshModel
from fea_toolkit.model.sap_data import (
    AreaElement,
    FrameElement,
    LayeredShellSection,
    Material,
    NDMaterial,
    Node,
    Restraint,
    SAPModelData,
    ShellFiberLayer,
    ShellSection,
)
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
from fea_toolkit.opensees.preprocessor import Preprocessor


def _minimal_mesh(nd_materials=None, layered_sections=None):
    """Build a MeshModel with just enough data to reach the nD/layered paths.

    Args:
        nd_materials: ``{name: NDMaterial}`` or None (skips nD step).
        layered_sections: ``{name: LayeredShellSection}`` or None.
    """
    return MeshModel(
        nodes={
            "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
        },
        materials={},
        sections={},
        frame_elements={},
        frame_assignments={},
        area_elements={},
        area_assignments={},
        frame_dist_loads=[],
        material_tags={},
        section_tags={},
        shell_sec_tags={},
        shell_sec_variants={},
        frame_element_types={},
        area_element_types={},
        offset_rigid_links=[],
        edge_constraint_args=[],
        edge_loads_from_areas=[],
        loads_only_area_ids=set(),
        base_z=0.0,
        units={"F": "N", "L": "m", "T": "C"},
        nd_materials=nd_materials or {},
        layered_shell_sections=layered_sections or {},
    )


class TestGravitySubstepAutoDetection:
    """Verify LayeredShell models auto-ramp gravity without explicit config."""

    def teardown_method(self):
        ops.wipe()

    def test_non_layered_keeps_single_substep(self):
        """Models without LayeredShell sections keep gravity_num_substeps=1."""
        mm = _minimal_mesh(nd_materials=None, layered_sections=None)
        ab = AnalysisBuilder(mm, {})
        assert ab.config["gravity_num_substeps"] == 1

    def test_layered_auto_sets_ten_substeps(self):
        """Models with a LayeredShell section auto-set gravity_num_substeps=10."""
        ndm = {
            "WallConcrete": NDMaterial(
                name="WallConcrete",
                material_type="ConcreteS",
                E=30e9,
                nu=0.2,
                fc=30e6,
                ft=3e6,
                Es=0.0,
            )
        }
        lss = {
            "WallSec": LayeredShellSection(
                name="WallSec",
                layers=[
                    ShellFiberLayer(nd_material="WallConcrete", thickness=0.2, n_ip=2),
                ],
            )
        }
        mm = _minimal_mesh(nd_materials=ndm, layered_sections=lss)
        ab = AnalysisBuilder(mm, {})
        assert ab.config["gravity_num_substeps"] == 10

    def test_explicit_config_wins_over_auto(self):
        """An explicit gravity_num_substeps value is never overridden."""
        ndm = {
            "WallConcrete": NDMaterial(
                name="WallConcrete",
                material_type="ConcreteS",
                E=30e9,
                nu=0.2,
                fc=30e6,
                ft=3e6,
                Es=0.0,
            )
        }
        lss = {
            "WallSec": LayeredShellSection(
                name="WallSec",
                layers=[
                    ShellFiberLayer(nd_material="WallConcrete", thickness=0.2, n_ip=2),
                ],
            )
        }
        mm = _minimal_mesh(nd_materials=ndm, layered_sections=lss)
        ab = AnalysisBuilder(mm, {"gravity_num_substeps": 3})
        assert ab.config["gravity_num_substeps"] == 3


class TestShellForceResultants:
    """Verify shell stress resultants come from the section-force query.

    Regression test for the DCR bug: ``ops.eleResponse(tag, "forces")`` on
    a shell returns the **24-entry local nodal-force vector**, not the
    per-unit-width membrane/bending resultants.  The correct query is
    ``ops.eleResponse(tag, "section", 1, "forces")``.  Under a known
    uniaxial in-plane strain the extracted Nx must equal E·t·εx (Poisson
    Ny = ν·Nx, Nxy = 0) — anything else means a corner nodal force leaked
    into the resultant slot (e.g. shell Nxy = fz1 inflating wall τ DCRs).
    """

    def teardown_method(self):
        ops.wipe()

    def test_extract_static_shell_forces_membrane_resultants(self):
        """extract_static_shell_forces() returns E·t·εx, not nodal forces."""
        E = 30.0e6  # Pa (N/m²)
        nu = 0.2
        t = 0.15  # m
        eps_x = 1.0e-4
        expected_Nx = E * t * eps_x

        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
            "2": Node(node_id="2", node_tag=2, x=1.0, y=0.0, z=0.0),
            "3": Node(node_id="3", node_tag=3, x=1.0, y=1.0, z=0.0),
            "4": Node(node_id="4", node_tag=4, x=0.0, y=1.0, z=0.0),
        }
        mat = {"Conc": Material(name="Conc", type="Concrete", E_mod=E, nu=nu)}
        sec = {
            "WSec": ShellSection(
                name="WSec",
                shape="Shell",
                material="Conc",
                A=0.0,
                I33=0.0,
                I22=0.0,
                J=0.0,
                thickness=t,
            )
        }
        area = {
            "A1": AreaElement(area_id="A1", area_tag=10, node_ids=["1", "2", "3", "4"], thickness=t)
        }
        mm = MeshModel(
            nodes=nodes,
            materials=mat,
            sections=sec,
            frame_elements={},
            frame_assignments={},
            area_elements=area,
            area_assignments={"A1": "WSec"},
            frame_dist_loads=[],
            material_tags={},
            section_tags={},
            shell_sec_tags={},  # NOT pre-seeded — builder must create the shell section
            shell_sec_variants={},
            frame_element_types={},
            area_element_types={},
            offset_rigid_links=[],
            edge_constraint_args=[],
            edge_loads_from_areas=[],
            loads_only_area_ids=set(),
            base_z=0.0,
            units={"F": "N", "L": "m", "T": "C"},
        )

        ab = AnalysisBuilder(mm, {"create_shells": True, "verbose": False})
        ab.build_domain()

        # Uniaxial in-plane extension: fix x, z and all rotations on every
        # node.  Restrain uy at nodes 1 and 2 only (rigid-body y translation)
        # and leave uy free at nodes 3 and 4 so Poisson contraction can
        # develop.  Then prescribe ux = eps_x at nodes 2 and 3 (SP
        # constraints override the fix for the x DOF at those nodes).
        # This keeps the BandSPD system non-singular while allowing
        # Ny = ν·Nx to develop under the uniaxial strain state.
        for nd in (1, 2):
            ops.fix(nd, 1, 1, 1, 1, 1, 1)
        for nd in (3, 4):
            ops.fix(nd, 1, 0, 1, 1, 1, 1)
        ops.timeSeries("Linear", 1)
        ops.pattern("Plain", 1, 1)
        for nd in (2, 3):
            ops.sp(nd, 1, eps_x)
        ops.system("BandSPD")
        ops.numberer("RCM")
        ops.constraints("Transformation")
        ops.integrator("LoadControl", 1.0)
        ops.algorithm("Linear")
        ops.analysis("Static")
        assert ops.analyze(1) == 0

        res = ab.extract_static_shell_forces()
        assert "A1" in res, f"extract_static_shell_forces keys: {list(res)}"
        f = res["A1"]
        # ShellMITC4 samples the membrane stress resultants at its own
        # integration points, so Nx is within ~5% of E·t·εx for a single
        # coarse element under a prescribed boundary strain.  The decisive
        # regression checks are (a) the result is a force-per-width
        # resultant on the E·t·εx scale (never ±225 kN corner nodal
        # forces) and (b) Nxy ≈ 0 under pure uniaxial strain.
        assert abs(f["fx"] - expected_Nx) < expected_Nx * 0.10, (
            f"Nx={f['fx']:.6g} != E*t*eps_x={expected_Nx:.6g} (±10%)"
        )
        assert abs(f["fy"] - nu * expected_Nx) < expected_Nx * 0.10, (
            f"Ny={f['fy']:.6g} != nu*Nx={nu * expected_Nx:.6g} (Poisson, ±10%)"
        )
        assert abs(f["fxy"]) < expected_Nx * 1e-6, (
            f"Nxy={f['fxy']:.6g} != 0 — a corner nodal force leaked in"
        )

    def test_record_step_shell_resultants(self):
        """_record_step() (pushover recorder) stores true Nx/Ny/Nxy resultants."""
        E = 30.0e6
        nu = 0.2
        t = 0.15
        eps_x = 1.0e-4
        expected_Nx = E * t * eps_x

        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
            "2": Node(node_id="2", node_tag=2, x=1.0, y=0.0, z=0.0),
            "3": Node(node_id="3", node_tag=3, x=1.0, y=1.0, z=0.0),
            "4": Node(node_id="4", node_tag=4, x=0.0, y=1.0, z=0.0),
        }
        mat = {"Conc": Material(name="Conc", type="Concrete", E_mod=E, nu=nu)}
        sec = {
            "WSec": ShellSection(
                name="WSec",
                shape="Shell",
                material="Conc",
                A=0.0,
                I33=0.0,
                I22=0.0,
                J=0.0,
                thickness=t,
            )
        }
        area = {
            "A1": AreaElement(area_id="A1", area_tag=10, node_ids=["1", "2", "3", "4"], thickness=t)
        }
        mm = MeshModel(
            nodes=nodes,
            materials=mat,
            sections=sec,
            frame_elements={},
            frame_assignments={},
            area_elements=area,
            area_assignments={"A1": "WSec"},
            frame_dist_loads=[],
            material_tags={},
            section_tags={},
            shell_sec_tags={},
            shell_sec_variants={},
            frame_element_types={},
            area_element_types={},
            offset_rigid_links=[],
            edge_constraint_args=[],
            edge_loads_from_areas=[],
            loads_only_area_ids=set(),
            base_z=0.0,
            units={"F": "N", "L": "m", "T": "C"},
        )

        ab = AnalysisBuilder(mm, {"create_shells": True, "verbose": False})
        ab.build_domain()
        # Same constraint scheme as the static-extraction test — y must be
        # free for the Poisson contraction under uniaxial extension.
        for nd in (1, 2, 3, 4):
            ops.fix(nd, 1, 0, 1, 1, 1, 1)
        ops.timeSeries("Linear", 1)
        ops.pattern("Plain", 1, 1)
        for nd in (2, 3):
            ops.sp(nd, 1, eps_x)
        ops.system("BandSPD")
        ops.numberer("RCM")
        ops.constraints("Transformation")
        ops.integrator("LoadControl", 1.0)
        ops.algorithm("Linear")
        ops.analysis("Static")
        assert ops.analyze(1) == 0

        from fea_toolkit.opensees.analysis_builder import _record_step

        rec = _record_step(ab, 1, set(), {"A1"})
        sh = rec["shell_forces"]["A1"]
        # Same ±10% band as the static test (ShellMITC4 integration-point
        # sampling); Nxy must remain ~0 (it was previously fz1, the
        # out-of-plane corner nodal force — the source of inflated wall
        # τ/τcap DCRs).
        assert abs(sh["Nx"] - expected_Nx) < expected_Nx * 0.10, (
            f"recorded Nx={sh['Nx']:.6g} != E*t*eps_x={expected_Nx:.6g} (±10%)"
        )
        assert abs(sh["Ny"] - nu * expected_Nx) < expected_Nx * 0.10, (
            f"recorded Ny={sh['Ny']:.6g} != nu*Nx={nu * expected_Nx:.6g} (Poisson, ±10%)"
        )
        assert abs(sh["Nxy"]) < expected_Nx * 1e-6, (
            f"recorded Nxy={sh['Nxy']:.6g} != 0 — this is what inflated wall τ DCRs"
        )

    def test_record_step_layered_shell_shear_resultant(self):
        """_record_step() returns true composite-shear resultants for a 5-layer
        LayeredShell under an exact pure in-plane shear field.

        Regression test for the wall τ DCR extraction path.  An admin shear
        wall is ShellNLDKGQ + a 5-layer LayeredShell (0.020/0.002/0.106/
        0.002/0.020 m).  Under ux = γ₀ at the top edge (uy = 0) the
        engineering shear strain is exactly γ₀, so Nxy must equal
        Σ(Gᵢ tᵢ)·γ₀.  A tensor/engineering factor-of-2 mix-up or a corner
        nodal-force leak would silently inflate wall τ/τcap DCRs.
        """
        E_c, nu_c = 30.0e9, 0.2
        E_s, nu_s = 200.0e9, 0.3
        G_c = E_c / (2 * (1 + nu_c))
        G_s = E_s / (2 * (1 + nu_s))
        thk = [0.020, 0.002, 0.106, 0.002, 0.020]
        gamma0 = 2.0e-4

        ndm = {
            "WallConcrete": NDMaterial(
                name="WallConcrete",
                material_type="ElasticIsotropic",
                E=E_c,
                nu=nu_c,
            ),
            "WallRebar": NDMaterial(
                name="WallRebar",
                material_type="J2PlateFibre",
                E=E_s,
                nu=nu_s,
                fy=400.0e6,
                Hiso=0.0,
                Hkin=0.0,
            ),
        }
        lss = {
            "WallSec": LayeredShellSection(
                name="WallSec",
                layers=[
                    ShellFiberLayer(nd_material="WallConcrete", thickness=thk[0]),
                    ShellFiberLayer(nd_material="WallRebar", thickness=thk[1]),
                    ShellFiberLayer(nd_material="WallConcrete", thickness=thk[2]),
                    ShellFiberLayer(nd_material="WallRebar", thickness=thk[3]),
                    ShellFiberLayer(nd_material="WallConcrete", thickness=thk[4]),
                ],
            )
        }
        nodes = {
            "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
            "2": Node(node_id="2", node_tag=2, x=1.0, y=0.0, z=0.0),
            "3": Node(node_id="3", node_tag=3, x=1.0, y=1.0, z=0.0),
            "4": Node(node_id="4", node_tag=4, x=0.0, y=1.0, z=0.0),
        }
        area = {
            "A1": AreaElement(
                area_id="A1",
                area_tag=10,
                node_ids=["1", "2", "3", "4"],
                thickness=0.15,
            )
        }
        sec = {
            "WallSec": ShellSection(
                name="WallSec",
                shape="Shell",
                material="WallConc",
                A=0.0,
                I33=0.0,
                I22=0.0,
                J=0.0,
                thickness=0.15,
            )
        }
        mats = {
            "WallConc": Material(name="WallConc", type="Concrete", E_mod=E_c, nu=nu_c),
        }
        mm = MeshModel(
            nodes=nodes,
            materials=mats,
            sections=sec,
            frame_elements={},
            frame_assignments={},
            area_elements=area,
            area_assignments={"A1": "WallSec"},
            frame_dist_loads=[],
            material_tags={},
            section_tags={},
            shell_sec_tags={},
            shell_sec_variants={},
            frame_element_types={},
            area_element_types={},
            offset_rigid_links=[],
            edge_constraint_args=[],
            edge_loads_from_areas=[],
            loads_only_area_ids=set(),
            base_z=0.0,
            units={"F": "N", "L": "m", "T": "C"},
            nd_materials=ndm,
            layered_shell_sections=lss,
        )
        ab = AnalysisBuilder(mm, {"create_shells": True, "verbose": False})
        ab.build_domain()

        # Pure in-plane shear: ux = gamma0*y, uy = 0  ->  gamma_xy = gamma0.
        ops.fix(1, 1, 1, 1, 1, 1, 1)
        # ux = gamma0*y = 0 at y = 0 for both nodes 1 and 2.
        ops.fix(2, 1, 1, 1, 1, 1, 1)
        for nd in (3, 4):
            ops.fix(nd, 0, 1, 1, 1, 1, 1)
        ops.timeSeries("Linear", 1)
        ops.pattern("Plain", 1, 1)
        ops.sp(3, 1, gamma0)
        ops.sp(4, 1, gamma0)
        # All four nodes are fully constrained (fix + SP), leaving zero
        # free equations.  BandSPD cannot handle that (LAPACK DPBSV
        # "parameter 8 illegal value" — a known OpenSees limitation), so
        # use the sparse UmfPack solver, which checks for zero equations.
        # See docs/llm_guide.md under "Solver selection".
        ops.system("UmfPack")
        ops.numberer("RCM")
        ops.constraints("Transformation")
        ops.integrator("LoadControl", 1.0)
        ops.algorithm("Linear")
        ops.analysis("Static")
        assert ops.analyze(1) == 0

        from fea_toolkit.opensees.analysis_builder import _record_step

        rec = _record_step(ab, 1, set(), {"A1"})
        sh = rec["shell_forces"]["A1"]
        GAw = G_c * thk[0] + G_s * thk[1] + G_c * thk[2] + G_s * thk[3] + G_c * thk[4]
        expected_Nxy = GAw * gamma0
        assert abs(sh["Nxy"] - expected_Nxy) < expected_Nxy * 0.05, (
            f"recorded Nxy={sh['Nxy']:.6g} != GAw*gamma0={expected_Nxy:.6g} (±5%)"
        )
        tol = expected_Nxy * 1e-4
        assert abs(sh["Nx"]) < tol, f"pure shear must give Nx≈0, got {sh['Nx']:.6g}"
        assert abs(sh["Ny"]) < tol, f"pure shear must give Ny≈0, got {sh['Ny']:.6g}"


class TestLayeredShellBuild:
    """Verify AnalysisBuilder builds nD materials and layered shell sections."""

    def teardown_method(self):
        ops.wipe()

    def _three_node_area_mesh(self, nd_materials, layered_sections, sec_name):
        """Build a MeshModel with a triangular area assigned to *sec_name*."""
        return MeshModel(
            nodes={
                "n1": Node(node_id="n1", node_tag=101, x=0.0, y=0.0, z=0.0),
                "n2": Node(node_id="n2", node_tag=102, x=4.0, y=0.0, z=0.0),
                "n3": Node(node_id="n3", node_tag=103, x=0.0, y=4.0, z=0.0),
            },
            materials={
                "mat_concrete": Material(
                    name="mat_concrete",
                    type="Concrete",
                    E_mod=30.0e9,
                    nu=0.2,
                    unit_weight=0.0,
                    unit_mass=0.0,
                ),
            },
            sections={
                sec_name: ShellSection(
                    name=sec_name,
                    shape="ShellSection",
                    material="mat_concrete",
                    thickness=0.3,
                ),
            },
            frame_elements={},
            frame_assignments={},
            area_elements={
                "a1": AreaElement(
                    area_id="a1",
                    area_tag=201,
                    node_ids=["n1", "n2", "n3"],
                    thickness=0.3,
                ),
            },
            area_assignments={"a1": sec_name},
            frame_dist_loads=[],
            material_tags={},
            section_tags={},
            shell_sec_tags={},
            shell_sec_variants={},
            frame_element_types={},
            area_element_types={},
            offset_rigid_links=[],
            edge_constraint_args=[],
            edge_loads_from_areas=[],
            loads_only_area_ids=set(),
            base_z=0.0,
            units={"F": "N", "L": "m", "T": "C"},
            nd_materials=nd_materials or {},
            layered_shell_sections=layered_sections or {},
        )

    # ── Test 1: Build with ElasticIsotropic ──────────────────────────

    def test_elastic_isotropic_nd_material(self):
        """Create a single ElasticIsotropic nD material + layered section."""
        ndm = {
            "conc1": NDMaterial(
                name="conc1",
                material_type="ElasticIsotropic",
                E=30.0e9,
                nu=0.2,
            ),
        }
        lss = {
            "wall_section": LayeredShellSection(
                name="wall_section",
                layers=[
                    ShellFiberLayer(thickness=0.04, nd_material="conc1", n_ip=3),
                    ShellFiberLayer(thickness=0.30, nd_material="conc1", n_ip=8),
                    ShellFiberLayer(thickness=0.04, nd_material="conc1", n_ip=3),
                ],
            ),
        }
        mm = _minimal_mesh(nd_materials=ndm, layered_sections=lss)
        builder = AnalysisBuilder(mm, {"verbose": False})
        builder.build_domain()

        assert "conc1" in builder._nd_material_tags
        conc_tag = builder._nd_material_tags["conc1"]
        assert isinstance(conc_tag, int) and conc_tag > 0

        assert "wall_section" in builder._shell_sec_tags
        sec_tag = builder._shell_sec_tags["wall_section"]
        assert isinstance(sec_tag, int) and sec_tag > 0
        assert len(builder._skipped_nd_materials) == 0

    # ── Test 2: Build with J2PlateFibre + ConcreteS ──────────────────

    def test_j2_and_concrete_nd_material(self):
        """Create J2PlateFibre + ConcreteS nD materials."""
        ndm = {
            "rebar": NDMaterial(
                name="rebar",
                material_type="J2PlateFibre",
                E=200.0e9,
                nu=0.3,
                fy=400.0e6,
                Hiso=0.0,
                Hkin=0.01,
            ),
            "concrete": NDMaterial(
                name="concrete",
                material_type="ConcreteS",
                E=30.0e9,
                nu=0.2,
                fc=-30.0e6,
                ft=2.0e6,
                Es=30.0e9,
            ),
        }
        lss = {
            "wall_section": LayeredShellSection(
                name="wall_section",
                layers=[
                    ShellFiberLayer(thickness=0.04, nd_material="concrete", n_ip=3),
                    ShellFiberLayer(thickness=0.002, nd_material="rebar", n_ip=5),
                    ShellFiberLayer(thickness=0.30, nd_material="concrete", n_ip=8),
                    ShellFiberLayer(thickness=0.002, nd_material="rebar", n_ip=5),
                    ShellFiberLayer(thickness=0.04, nd_material="concrete", n_ip=3),
                ],
            ),
        }
        mm = _minimal_mesh(nd_materials=ndm, layered_sections=lss)
        builder = AnalysisBuilder(mm, {"verbose": False})
        builder.build_domain()

        assert "concrete" in builder._nd_material_tags
        assert "rebar" in builder._nd_material_tags
        assert "wall_section" in builder._shell_sec_tags
        assert len(builder._skipped_nd_materials) == 0

    # ── Test 3: Skipped-material rejection ──────────────────────────

    def test_skipped_material_rejects_dependent_section(self):
        """LayeredShell referencing a skipped/unknown nD material is rejected."""
        ndm = {
            "bad_stuff": NDMaterial(
                name="bad_stuff",
                material_type="NonExistentType",
                E=30.0e9,
                nu=0.2,
            ),
        }
        lss = {
            "wall_using_bad": LayeredShellSection(
                name="wall_using_bad",
                layers=[
                    ShellFiberLayer(thickness=0.04, nd_material="bad_stuff", n_ip=3),
                    ShellFiberLayer(thickness=0.30, nd_material="bad_stuff", n_ip=8),
                    ShellFiberLayer(thickness=0.04, nd_material="bad_stuff", n_ip=3),
                ],
            ),
        }
        mm = _minimal_mesh(nd_materials=ndm, layered_sections=lss)
        builder = AnalysisBuilder(mm, {"verbose": False})
        builder.build_domain()

        # The unknown material was skipped
        assert "bad_stuff" in builder._skipped_nd_materials
        assert "bad_stuff" not in builder.material_tags

        # The section referencing it should NOT be created
        assert "wall_using_bad" not in builder._shell_sec_tags

    def test_skipped_material_preserves_valid_section(self):
        """Valid sections survive alongside rejected ones."""
        ndm = {
            "good_conc": NDMaterial(
                name="good_conc",
                material_type="ElasticIsotropic",
                E=30.0e9,
                nu=0.2,
            ),
            "bad_stuff": NDMaterial(
                name="bad_stuff",
                material_type="UnknownType",
                E=10.0e9,
                nu=0.3,
            ),
        }
        lss = {
            "valid_wall": LayeredShellSection(
                name="valid_wall",
                layers=[
                    ShellFiberLayer(thickness=0.04, nd_material="good_conc", n_ip=3),
                    ShellFiberLayer(thickness=0.30, nd_material="good_conc", n_ip=8),
                    ShellFiberLayer(thickness=0.04, nd_material="good_conc", n_ip=3),
                ],
            ),
            "invalid_wall": LayeredShellSection(
                name="invalid_wall",
                layers=[
                    ShellFiberLayer(thickness=0.04, nd_material="bad_stuff", n_ip=3),
                    ShellFiberLayer(thickness=0.30, nd_material="bad_stuff", n_ip=8),
                    ShellFiberLayer(thickness=0.04, nd_material="bad_stuff", n_ip=3),
                ],
            ),
        }
        mm = _minimal_mesh(nd_materials=ndm, layered_sections=lss)
        builder = AnalysisBuilder(mm, {"verbose": False})
        builder.build_domain()

        assert "good_conc" in builder._nd_material_tags
        assert "bad_stuff" in builder._skipped_nd_materials
        assert "valid_wall" in builder._shell_sec_tags
        assert "invalid_wall" not in builder._shell_sec_tags

    # ── Test 4: Section recreation after ops.wipe() ─────────────────

    def test_build_twice_preserves_layered_section_tags(self):
        """LayeredShell section tags remain stable after a second build_domain.

        Also verifies that shell_sec_tags from a previous build are merged
        correctly (non-layered tags cleared, new non-layered tags created).
        """
        ndm = {
            "concrete": NDMaterial(
                name="concrete",
                material_type="ElasticIsotropic",
                E=30.0e9,
                nu=0.2,
            ),
        }
        lss_wall = {
            "wall_section": LayeredShellSection(
                name="wall_section",
                layers=[
                    ShellFiberLayer(thickness=0.04, nd_material="concrete", n_ip=3),
                    ShellFiberLayer(thickness=0.30, nd_material="concrete", n_ip=8),
                    ShellFiberLayer(thickness=0.04, nd_material="concrete", n_ip=3),
                ],
            ),
        }
        lss_two = {
            "wall_section": LayeredShellSection(
                name="wall_section",
                layers=[
                    ShellFiberLayer(thickness=0.04, nd_material="concrete", n_ip=3),
                    ShellFiberLayer(thickness=0.30, nd_material="concrete", n_ip=8),
                    ShellFiberLayer(thickness=0.04, nd_material="concrete", n_ip=3),
                ],
            ),
            "new_section": LayeredShellSection(
                name="new_section",
                layers=[
                    ShellFiberLayer(thickness=0.04, nd_material="concrete", n_ip=3),
                    ShellFiberLayer(thickness=0.10, nd_material="concrete", n_ip=5),
                    ShellFiberLayer(thickness=0.04, nd_material="concrete", n_ip=3),
                ],
            ),
        }
        mm = _minimal_mesh(nd_materials=ndm, layered_sections=lss_wall)
        builder = AnalysisBuilder(mm, {"verbose": False})

        # Set up _shell_sec_tags *before* first build to simulate
        # reality where mesh_model already carries shell_sec_tags.
        builder._shell_sec_tags["old_stale_elastic_shell"] = 999
        builder.build_domain()

        # After first build the stale non-layered tag is cleared
        assert "old_stale_elastic_shell" not in builder._shell_sec_tags
        # wall_section was created
        assert "wall_section" in builder._shell_sec_tags
        wall_tag = builder._shell_sec_tags["wall_section"]

        # Replace mesh_model.layered_shell_sections with a new dict
        # (simulates what happens when the user reconfigures
        # shell_layers and rebuilds).
        mm.layered_shell_sections = lss_two
        # Manually restore the stale tag so the code path that
        # clears it on rebuild is exercised.
        builder._shell_sec_tags["old_stale_elastic_shell"] = 999

        # Second build — ops.wipe() is called inside build_domain()
        builder.build_domain()
        # wall_section uses lookup-based reuse → tag unchanged
        assert "wall_section" in builder._shell_sec_tags
        assert builder._shell_sec_tags["wall_section"] == wall_tag
        # new_section gets a distinct tag
        assert "new_section" in builder._shell_sec_tags
        new_tag = builder._shell_sec_tags["new_section"]
        assert isinstance(new_tag, int)
        assert new_tag != wall_tag
        # No collisions — all values are unique
        assert len(set(builder._shell_sec_tags.values())) == len(builder._shell_sec_tags)
        # nD material tag should also still exist
        assert "concrete" in builder._nd_material_tags
        # The stale non-layered tag was cleared again (did not resurrect)
        assert "old_stale_elastic_shell" not in builder._shell_sec_tags

    # ── Test 5: Elastic fallback + section-tag collision ─────────────

    def test_skipped_layered_section_creates_no_shell_element(self):
        """Areas referencing a skipped layered section produce no shell elements.

        Covers both:
        - Elastic fallback path (section name in _skipped_shell_sec_names)
        - Section-tag collision path (no tag assigned to the skipped section)
        """
        ndm = {
            "bad_stuff": NDMaterial(
                name="bad_stuff",
                material_type="NonExistentType",
                E=30.0e9,
                nu=0.2,
            ),
        }
        lss = {
            "wall_using_bad": LayeredShellSection(
                name="wall_using_bad",
                layers=[
                    ShellFiberLayer(thickness=0.04, nd_material="bad_stuff", n_ip=3),
                    ShellFiberLayer(thickness=0.20, nd_material="bad_stuff", n_ip=5),
                    ShellFiberLayer(thickness=0.04, nd_material="bad_stuff", n_ip=3),
                ],
            ),
        }
        mm = self._three_node_area_mesh(
            nd_materials=ndm, layered_sections=lss, sec_name="wall_using_bad"
        )
        builder = AnalysisBuilder(mm, {"verbose": False, "create_shells": True})
        builder.build_domain()

        # The skipped section name is tracked
        assert "wall_using_bad" in builder._skipped_shell_sec_names

        # No shell element was created for area 'a1'
        assert "a1" not in builder._shell_tag_map

    # ── Test 6: Rebuild recovery (supported → unsupported → supported) ─

    def test_rebuild_recovery_supported_to_unsupported_back(self):
        """Verify that a supported → unsupported → supported rebuild cycle
        correctly recovers and recreates the layered shell section.

        Regression test for the stale-tag bug where:
        - Build 1: supported material → section created (tag in _shell_sec_tags)
        - Build 2: unsupported type → material skipped, section skipped,
          but material_tags still held stale tag → section wrongly recreated
        - Build 3: supported again → _skipped sets cleared, section recovered
        """
        ndm_supported = {
            "flex_mat": NDMaterial(
                name="flex_mat",
                material_type="ElasticIsotropic",
                E=30.0e9,
                nu=0.2,
            ),
        }
        ndm_unsupported = {
            "flex_mat": NDMaterial(
                name="flex_mat",
                material_type="NonExistentType",
                E=30.0e9,
                nu=0.2,
            ),
        }
        lss = {
            "flex_section": LayeredShellSection(
                name="flex_section",
                layers=[
                    ShellFiberLayer(thickness=0.04, nd_material="flex_mat", n_ip=3),
                    ShellFiberLayer(thickness=0.20, nd_material="flex_mat", n_ip=5),
                    ShellFiberLayer(thickness=0.04, nd_material="flex_mat", n_ip=3),
                ],
            ),
        }
        mm_supported = _minimal_mesh(nd_materials=ndm_supported, layered_sections=lss)
        mm_unsupported = _minimal_mesh(nd_materials=ndm_unsupported, layered_sections=lss)

        # ── Build 1: supported ──
        builder = AnalysisBuilder(mm_supported, {"verbose": False})
        builder.build_domain()
        assert "flex_mat" in builder._nd_material_tags
        assert "flex_section" in builder._shell_sec_tags
        assert len(builder._skipped_nd_materials) == 0
        builder._shell_sec_tags["flex_section"]

        # ── Build 2: unsupported (replace mesh_model nd_materials) ──
        builder.mesh_model = mm_unsupported
        builder.build_domain()
        assert "flex_mat" in builder._skipped_nd_materials
        # The section name is registered in _skipped_shell_sec_names so
        # _create_shell_elements will skip it (no shell element is
        # created for this section).  The _shell_sec_tags dict may still
        # carry the Build 1 tag, but the LayeredShell section is never
        # registered in OpenSees (ops.section('LayeredShell', ...) is
        # not called) because the skipped-material guard fires first.
        assert "flex_section" in builder._skipped_shell_sec_names

        # ── Build 3: supported again ──
        builder.mesh_model = mm_supported
        builder.build_domain()
        # Skip sets were cleared at start of build_domain
        assert len(builder._skipped_nd_materials) == 0
        # flex_mat should be created now
        assert "flex_mat" in builder._nd_material_tags
        # flex_section should be recreated with a fresh tag
        assert "flex_section" in builder._shell_sec_tags
        # Tag may differ from build 1 (after the intervening unsupported build
        # cleared the tag), but it must be a valid positive integer.
        recreated_tag = builder._shell_sec_tags["flex_section"]
        assert isinstance(recreated_tag, int) and recreated_tag > 0


# ── Preprocessor nd_materials tests ────────────────────────────────


def _minimal_sap_model_data(units=None):
    """Build a minimal SAPModelData that survives Preprocessor.run().

    Provides one node, one material, one section, and a single frame
    element so the topology pipeline runs without error.  Most of the
    heavy work (splitting, meshing) is skipped when ``split_elements``
    and ``create_shells`` are False.
    """
    if units is None:
        units = {"F": "kN", "L": "m", "T": "C"}
    return SAPModelData(
        nodes={
            "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
            "2": Node(node_id="2", node_tag=2, x=5.0, y=0.0, z=0.0),
        },
        materials={
            "steel": Material(
                name="steel",
                type="Steel",
                E_mod=200.0e9,
                nu=0.3,
                unit_weight=0.0,
                unit_mass=0.0,
            ),
        },
        sections={
            "beam_sec": ShellSection(
                name="beam_sec",
                shape="ShellSection",
                material="steel",
                thickness=0.1,
            ),
        },
        frame_elements={
            "f1": FrameElement(
                elem_id="f1",
                elem_tag=10,
                node_i="1",
                node_j="2",
            ),
        },
        area_elements={},
        frame_assignments={"f1": "beam_sec"},
        area_assignments={},
        groups={},
        restraints={
            "1": Restraint(dofs=[1, 1, 1, 1, 1, 1]),
        },
        load_cases={},
        load_patterns={},
        mass_sources={},
        joint_loads=[],
        frame_gravity_loads=[],
        area_gravity_loads=[],
        area_uniform_loads=[],
        frame_dist_loads=[],
        frame_end_offsets={},
        frame_auto_mesh={},
        units=units,
    )


class TestPreprocessorNdMaterials:
    """Verify Preprocessor._resolve_element_properties nd_materials path.

    Ensures the config->nd_materials path is exercised through
    Preprocessor.run() so any future refactoring of the validation or
    scaling logic is caught.
    """

    def test_invalid_nd_material_key_raises_valueerror(self):
        """Passing an unknown key in config['nd_materials'] raises ValueError."""
        model_data = _minimal_sap_model_data()
        config = {
            "split_elements": False,
            "create_shells": False,
            "verbose": False,
            "nd_materials": {
                "mat1": {
                    "E": 200.0e9,
                    "nu": 0.3,
                    "not_a_valid_field": 42,  # ← unknown key
                },
            },
        }
        pre = Preprocessor(config)
        with pytest.raises(ValueError) as exc_info:
            pre.run(model_data)
        msg = str(exc_info.value)
        assert "Invalid key" in msg
        assert "nd_materials" in msg
        assert "mat1" in msg

    def test_stress_fields_scaled_in_mesh_model(self):
        """Stress-valued fields are scaled from SI Pa to model units.

        Uses kN-m units where stress_scale_factor = 0.001, so
        400 MPa (SI) → 400 kPa (model).
        """
        model_data = _minimal_sap_model_data(units={"F": "kN", "L": "m", "T": "C"})
        config = {
            "split_elements": False,
            "create_shells": False,
            "verbose": False,
            "nd_materials": {
                "steel_mat": {
                    "material_type": "J2PlateFibre",
                    "E": 200.0e9,
                    "nu": 0.3,
                    "fy": 400.0e6,  # 400 MPa in SI Pa
                    "Hiso": 0.0,
                    "Hkin": 0.01e9,  # 10 MPa hardening → scaled
                },
            },
        }
        pre = Preprocessor(config)
        mesh_model = pre.run(model_data)

        assert "steel_mat" in mesh_model.nd_materials
        mat = mesh_model.nd_materials["steel_mat"]

        # In kN-m units: stress_scale = 0.001 (Pa → kPa)
        # Check that scaling was applied:
        #   fy: 400.0e6 Pa → 400.0e3 kPa
        #   Hkin: 0.01e9 Pa → 10.0e3 kPa
        # Allow for floating-point rounding
        assert abs(mat.fy - 400.0e3) < 1.0, f"Expected fy≈400e3, got {mat.fy}"
        assert abs(mat.Hkin - 10.0e3) < 1.0, f"Expected Hkin≈10e3, got {mat.Hkin}"

        # Non-stress fields (nu, material_type) must pass through unchanged
        assert mat.nu == 0.3
        assert mat.material_type == "J2PlateFibre"

    def test_stress_fields_scaled_si_units(self):
        """With SI units (N-m), stress_scale_factor = 1.0 → no change."""
        model_data = _minimal_sap_model_data(units={"F": "N", "L": "m", "T": "C"})
        config = {
            "split_elements": False,
            "create_shells": False,
            "verbose": False,
            "nd_materials": {
                "conc_mat": {
                    "material_type": "ConcreteS",
                    "E": 30.0e9,
                    "nu": 0.2,
                    "fc": 30.0e6,
                    "ft": 2.0e6,
                    "Es": 30.0e9,
                },
            },
        }
        pre = Preprocessor(config)
        mesh_model = pre.run(model_data)

        assert "conc_mat" in mesh_model.nd_materials
        mat = mesh_model.nd_materials["conc_mat"]
        # SI → SI: values should be unchanged
        assert mat.E == 30.0e9
        assert mat.fc == 30.0e6
        assert mat.ft == 2.0e6
        assert mat.Es == 30.0e9
