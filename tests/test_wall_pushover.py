"""Integration tests: LayeredShell + SFI_MVLEM_3D RC wall pushover paths.

Exercises the two supported nonlinear RC wall discretisations through the
full Preprocessor → AnalysisBuilder pipeline on the same wall geometry:

* **LayeredShell** — ShellNLDKGQ quad with through-thickness layers using the elastic core material.
* **SFI_MVLEM_3D** — single macro-element with ``m`` macro-fibers, each
  carrying its own FSAM nD material.

Both paths share the same 3.0 m × 4.0 m × 0.3 m wall, 30 MPa concrete,
420 MPa rebar, units kN / m / C.

Wall geometry (X-Z plane — matches the canonical wall layout):
    node 1 = (0, 0, 0)   node 2 = (4, 0, 0)   ← base (fixed)
    node 4 = (0, 0, 3)   node 3 = (4, 0, 3)   ← top (Y-restrained)
    Lateral push is in X at the two top nodes.
    Top nodes are restrained in Y (out-of-plane) to enforce the X-Z
    plane orientation.
"""

import openseespy.opensees as ops
import pytest

from fea_toolkit.model.sap_data import (
    AreaElement,
    JointLoad,
    LoadPattern,
    Material,
    Node,
    Restraint,
    SAPModelData,
    ShellSection,
)
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
from fea_toolkit.opensees.preprocessor import Preprocessor


def _wall_model_data() -> SAPModelData:
    """Build the shared 4 m × 3 m RC wall SAPModelData.

    Material strengths/moduli are authored in **model units** (kN, m):
    this is the direct-construction exception documented in
    ``.clinerules`` §4.6 (SI Pa values may be converted exactly once —
    here they are expressed directly in kN/m² = kPa-equivalent units).

    Concrete: 30 MPa = 30e3 kN/m², E = 30 GPa = 30e6 kN/m²
    Rebar:    420 MPa = 4.2e5 kN/m², E = 200 GPa = 2e8 kN/m²
    """
    return SAPModelData(
        nodes={
            "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
            "2": Node(node_id="2", node_tag=2, x=4.0, y=0.0, z=0.0),
            "3": Node(node_id="3", node_tag=3, x=4.0, y=0.0, z=3.0),
            "4": Node(node_id="4", node_tag=4, x=0.0, y=0.0, z=3.0),
        },
        materials={
            "concrete": Material(
                name="concrete",
                type="Concrete",
                E_mod=30.0e6,  # kN/m² (30 GPa)
                nu=0.2,
                Fc=30.0e3,  # kN/m² (30 MPa)
                unit_weight=24.0,  # kN/m³
            ),
            "steel": Material(
                name="steel",
                type="Rebar",
                E_mod=200.0e6,  # kN/m² (200 GPa)
                nu=0.3,
                Fy=420.0e3,  # kN/m² (420 MPa)
                unit_weight=78.5,  # kN/m³
            ),
        },
        sections={
            "WALL_SEC": ShellSection(
                name="WALL_SEC",
                shape="ShellSection",
                material="concrete",
                thickness=0.3,
            ),
        },
        frame_elements={},
        area_elements={
            "A1": AreaElement(
                area_id="A1",
                area_tag=100,
                node_ids=["1", "2", "3", "4"],
                thickness=0.3,
            ),
        },
        frame_assignments={},
        area_assignments={"A1": "WALL_SEC"},
        groups={},
        # Base fully fixed; top nodes Y-restrained (out-of-plane) to
        # enforce the X-Z plane orientation — canonical wall layout.
        restraints={
            "1": Restraint(dofs=[1, 1, 1, 1, 1, 1]),
            "2": Restraint(dofs=[1, 1, 1, 1, 1, 1]),
            "3": Restraint(dofs=[0, 1, 0, 0, 0, 0]),
            "4": Restraint(dofs=[0, 1, 0, 0, 0, 0]),
        },
        load_cases={},
        load_patterns={
            "Lateral": LoadPattern(name="Lateral", pattern_type="OTHER"),
        },
        mass_sources={},
        joint_loads=[
            JointLoad(pattern="Lateral", node_id="3", fx=50.0),
            JointLoad(pattern="Lateral", node_id="4", fx=50.0),
        ],
        frame_gravity_loads=[],
        area_gravity_loads=[],
        area_uniform_loads=[],
        frame_dist_loads=[],
        frame_end_offsets={},
        frame_auto_mesh={},
        units={"F": "kN", "L": "m", "T": "C"},
    )


def _fsam_nd_config() -> dict:
    """FSAM nD material configs authored mostly in SI (Pa) — framework scales.

    Two FSAM variants mirror the reinforcement distribution validated in
    ``local/probe_mvlem_sfi.py``: boundary fibers carry 2.5 % smeared
    reinforcement (concentrated boundary steel), interior fibers 0.4 %.
    A uniform-0.4 % wall is nearly singular under pure lateral push
    (no out-of-plane stabilisation), so the boundary-enriched layout is
    what the SFI_MVLEM_3D element needs to converge.
    """
    return {
        "nd_materials": {
            "FSAM_bdry": {
                "material_type": "FSAM",
                "density": 2400.0,
                "sx": "steel",
                "sy": "steel",
                "conc": "concrete",
                "rou_x": 0.025,
                "rou_y": 0.025,
                "nu": 0.2,
                "alfadow": 45.0,
            },
            "FSAM_core": {
                "material_type": "FSAM",
                "density": 2400.0,
                "sx": "steel",
                "sy": "steel",
                "conc": "concrete",
                "rou_x": 0.004,
                "rou_y": 0.004,
                "nu": 0.2,
                "alfadow": 45.0,
            },
        },
    }


def _base_config() -> dict:
    cfg = dict(_fsam_nd_config())
    cfg.update(
        {
            "create_shells": True,
            "verbose": False,
            # Plain constraint handler + 10 LoadControl substeps match the
            # SFI_MVLEM_3D probe settings; Transformation + single-step
            # fails for a free-standing 3D macro-element wall.
            "solver_constraints": "Plain",
            "gravity_num_substeps": 10,
        }
    )
    return cfg


def _layered_shell_config() -> dict:
    """LayeredShell path: FSAM + 5-layer core stack on the wall."""
    cfg = _base_config()
    cfg.update(
        {
            # Extra nD material for the through-thickness layers (SI Pa).
            "nd_materials": dict(
                cfg["nd_materials"],
                core={"material_type": "ElasticIsotropic", "E": 30.0e9, "nu": 0.2},
            ),
            "shell_layers": {
                "WALL_LAYERS": {
                    "selector": {"element_ids": ["A1"], "sections": ["WALL_SEC"]},
                    "layers": [
                        {"thickness": 0.05, "nd_material": "core"},
                        {"thickness": 0.05, "nd_material": "core"},
                        {"thickness": 0.10, "nd_material": "core"},
                        {"thickness": 0.05, "nd_material": "core"},
                        {"thickness": 0.05, "nd_material": "core"},
                    ],
                },
            },
        }
    )
    return cfg


def _sfi_mvlem_config() -> dict:
    """SFI_MVLEM_3D path: 5 macro-fibers, CoR 0.4.

    Fiber materials: boundary fibers (0 and 4) use ``FSAM_bdry`` (2.5 %
    smeared reinforcement), interior fibers (1-3) use ``FSAM_core``
    (0.4 %) — matching the converged probe layout.
    """
    cfg = _base_config()
    cfg["element_strategies"] = {
        "wall": {
            "element_type": "SFI_MVLEM_3D",
            "n_fibers": 5,
            "CoR": 0.4,
            "fsam_materials": [
                "FSAM_bdry",  # fiber 0 (left boundary)
                "FSAM_core",
                "FSAM_core",
                "FSAM_core",
                "FSAM_bdry",  # fiber 4 (right boundary)
            ],
        },
    }
    return cfg


def _run_pushover(mesh_model, config, apply_loads=True):
    """Build the domain, apply the 100 kN lateral top loads, run static.

    The 100 kN is split 50/50 onto the two top nodes (3 and 4).  Loads
    are applied via a dedicated plain pattern because ``_create_loads``
    records ``JointLoad`` pattern membership but does not emit
    ``ops.load`` for joint loads.  Returns ``(builder, results)``.
    """
    builder = AnalysisBuilder(mesh_model, config)
    builder.build_domain()
    if apply_loads:
        ops.timeSeries("Linear", 5001)
        ops.pattern("Plain", 5001, 5001)
        ops.load(3, 50.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        ops.load(4, 50.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        results = builder.run_static_analysis()
    else:
        results = {}
    return builder, results


def _sum_rx(results) -> float:
    """Sum x-reactions at the fixed base nodes."""
    rx = 0.0
    for nid in ("1", "2"):
        rx += results["reactions"][nid]["fx"]
    return rx


class TestLayeredShellWallPushover:
    """Preprocessor → AnalysisBuilder LayeredShell + ShellNLDKGQ wall path."""

    def teardown_method(self):
        ops.wipe()

    def test_build_domain_creates_layered_wall(self):
        """build_domain succeeds and creates FSAM, LayeredShell, ShellNLDKGQ."""
        md = _wall_model_data()
        mm = Preprocessor(_layered_shell_config()).run(md)
        assert "FSAM_bdry" in mm.nd_materials
        assert "FSAM_core" in mm.nd_materials
        assert "WALL_SEC" in mm.layered_shell_sections
        assert mm.area_elements["A1"].inactive is False
        assert len(mm.wall_elements) == 0

        _, _ = _run_pushover(mm, _layered_shell_config(), apply_loads=False)
        ele_tags = list(ops.getEleTags())
        assert ele_tags, "Expected at least one shell element"
        # ShellNLDKGQ (nonlinear layered) rather than ShellMITC4.
        # Verify one quad element exists (4 nodes).
        assert any(len(ops.eleNodes(t)) == 4 for t in ele_tags)

    def test_100kn_pushover_reaction(self):
        """LayeredShell wall carries 100 kN and reacts −100 kN in X."""
        md = _wall_model_data()
        mm = Preprocessor(_layered_shell_config()).run(md)
        __, results = _run_pushover(mm, _layered_shell_config())
        rx = _sum_rx(results)
        assert abs(rx + 100.0) < 1.0, f"rx={rx} expected ≈ −100"

    def test_build_twice_stable(self):
        """Repeated build_domain() on the LayeredShell path is stable."""
        mm = Preprocessor(_layered_shell_config()).run(_wall_model_data())
        __, _ = _run_pushover(mm, _layered_shell_config(), apply_loads=False)
        n1 = len(ops.getEleTags())
        b2 = AnalysisBuilder(mm, _layered_shell_config())
        b2.build_domain()
        n2 = len(ops.getEleTags())
        assert n1 == n2


class TestSFIMVLEM3DWallPushover:
    """Preprocessor → AnalysisBuilder SFI_MVLEM_3D wall path."""

    def teardown_method(self):
        ops.wipe()

    def test_preprocessor_generates_wall_element(self):
        """SFI_MVLEM_3D config produces a single WallElement with 5 fibers."""
        md = _wall_model_data()
        mm = Preprocessor(_sfi_mvlem_config()).run(md)
        assert len(mm.wall_elements) == 1
        wall = mm.wall_elements["W1"]
        assert wall.m == 5
        assert wall.CoR == 0.4
        assert abs(sum(wall.width) - 4.0) < 1e-6
        # Boundary fibers use the 2.5%-reinforced FSAM, interior 0.4%.
        assert len(wall.fsam_material_names) == 5
        assert wall.fsam_material_names[0] == "FSAM_bdry"
        assert wall.fsam_material_names[-1] == "FSAM_bdry"
        assert all(n == "FSAM_core" for n in wall.fsam_material_names[1:-1])
        # OpenSees quad order: [bottom-L, bottom-R, top-L, top-R]
        assert wall.node_ids == ["1", "2", "4", "3"]
        # The source area is marked inactive so shells are skipped
        assert mm.area_elements["A1"].inactive is True

    def test_wall_generation_respects_slab_z_tolerance(self):
        """Wall-element generation honours the configured ``slab_z_tolerance``.

        A near-horizontal area (corner Z-span 0.015) is classified as a slab
        under the default 0.02 tolerance — no macro-element — and as a wall
        under a 0.01 override — macro-element generated.  This mirrors
        ``_classify_element_type`` and the stored ``area_element_types``
        role so wall-element generation stays consistent with them.
        """
        md = _wall_model_data()
        # Squash the top nodes so the area is near-horizontal (0.015 spread).
        md.nodes["3"].z = 0.015
        md.nodes["4"].z = 0.015

        # Default tolerance 0.02 > 0.015 spread → slab → no macro-element.
        mm_default = Preprocessor(_sfi_mvlem_config()).run(md)
        assert len(mm_default.wall_elements) == 0
        assert mm_default.area_element_types["A1"] == "slab"

        # Tightened tolerance 0.01 < 0.015 spread → wall → macro-element.
        tight = _sfi_mvlem_config()
        tight["slab_z_tolerance"] = 0.01
        mm_tight = Preprocessor(tight).run(md)
        assert len(mm_tight.wall_elements) == 1
        assert mm_tight.area_element_types["A1"] == "wall"

    def test_build_domain_creates_sfi_mvlem_3d(self):
        """build_domain() emits exactly one SFI_MVLEM_3D element."""
        mm = Preprocessor(_sfi_mvlem_config()).run(_wall_model_data())
        builder = AnalysisBuilder(mm, _sfi_mvlem_config())
        builder.build_domain()
        tags = list(ops.getEleTags())
        assert builder.mesh_model.wall_elements["W1"].elem_tag in tags

    @pytest.mark.xfail(
        reason=(
            "SFI_MVLEM_3D diverges at the first lateral step through the "
            "Preprocessor→AnalysisBuilder pipeline.  Root cause is an "
            "upstream element bug (SFI_MVLEM_3D.cpp block-diagonal tangent "
            "with discarded D01/D02 coupling — see "
            "Xara/models/SFI-MVLEM_Walls/sfi_mvlem_stiffness_fix.md, which "
            "fixes only the 2D SFI_MVLEM), producing a singular stiffness "
            "matrix (`U(i,i)=0,i=5`).  Verified byte-identical materials "
            "(Tcl export), identical divergence with crossed AND clean CCW "
            "node order, and identical failure in every openseespy wheel "
            "(3.8.0.0, 3.7.1.2, local build) — the element never converges, "
            "in any orientation or via direct ops calls."
        ),
        strict=False,
    )
    def test_100kn_pushover_reaction(self):
        """SFI_MVLEM_3D wall carries 100 kN and reacts −100 kN in X."""
        mm = Preprocessor(_sfi_mvlem_config()).run(_wall_model_data())
        __, results = _run_pushover(mm, _sfi_mvlem_config())
        rx = _sum_rx(results)
        assert abs(rx + 100.0) < 1.0, f"rx={rx} expected ≈ −100"

    def test_build_twice_tag_stable(self):
        """Repeated build_domain() keeps the SFI_MVLEM_3D tag stable."""
        mm = Preprocessor(_sfi_mvlem_config()).run(_wall_model_data())
        b1 = AnalysisBuilder(mm, _sfi_mvlem_config())
        b1.build_domain()
        tag1 = mm.wall_elements["W1"].elem_tag
        n1 = len(ops.getEleTags())
        b2 = AnalysisBuilder(mm, _sfi_mvlem_config())
        b2.build_domain()
        n2 = len(ops.getEleTags())
        assert b2.mesh_model.wall_elements["W1"].elem_tag == tag1
        assert n1 == n2


class TestBothWallApproaches:
    """Cross-approach comparison on the same wall geometry."""

    def teardown_method(self):
        ops.wipe()

    @pytest.mark.xfail(
        reason=(
            "SFI_MVLEM_3D diverges at the first lateral step through the "
            "Preprocessor→AnalysisBuilder pipeline.  Root cause is an "
            "upstream element bug (SFI_MVLEM_3D.cpp block-diagonal tangent "
            "with discarded D01/D02 coupling — see "
            "Xara/models/SFI-MVLEM_Walls/sfi_mvlem_stiffness_fix.md, which "
            "fixes only the 2D SFI_MVLEM), producing a singular stiffness "
            "matrix (`U(i,i)=0,i=5`).  Verified byte-identical materials "
            "(Tcl export), identical divergence with crossed AND clean CCW "
            "node order, and identical failure in every openseespy wheel "
            "(3.8.0.0, 3.7.1.2, local build) — the element never converges, "
            "in any orientation or via direct ops calls."
        ),
        strict=False,
    )
    def test_both_approaches_physically_consistent(self):
        """Both wall approaches carry the same 100 kN and drift in +X.

        A quantitative displacement agreement is NOT expected: the
        LayeredShell path here stacks 100 % elastic layers (no rebar
        smearing) and is far stiffer than the SFI_MVLEM_3D macro-element
        whose FSAM concrete softens severely under monotonic lateral
        push.  The convergent physics both models produce — identical
        shears and positive top drift in the push direction — is what
        this test locks in.  Per-path reaction equality is covered by
        the dedicated 100 kN tests.
        """
        # LayeredShell
        mm_l = Preprocessor(_layered_shell_config()).run(_wall_model_data())
        _, res_l = _run_pushover(mm_l, _layered_shell_config())
        dx_l = res_l["nodal_displacements"]["3"][0]
        rx_l = _sum_rx(res_l)

        ops.wipe()

        # SFI_MVLEM_3D
        mm_s = Preprocessor(_sfi_mvlem_config()).run(_wall_model_data())
        _, res_s = _run_pushover(mm_s, _sfi_mvlem_config())
        dx_s = res_s["nodal_displacements"]["3"][0]
        rx_s = _sum_rx(res_s)

        # Both paths carry the full 100 kN in X.
        assert abs(rx_l + 100.0) < 1.0, f"LayeredShell rx={rx_l}"
        assert abs(rx_s + 100.0) < 1.0, f"SFI_MVLEM_3D rx={rx_s}"
        # Both top nodes displace positively (in +X push direction) and
        # non-trivially (rigid-body-free).
        assert dx_l > 1e-8, f"LayeredShell top drift unexpectedly zero/negative: {dx_l}"
        assert dx_s > 1e-8, f"SFI_MVLEM_3D top drift unexpectedly zero/negative: {dx_s}"
        # The nonlinear macro-wall is expected to be far more flexible than
        # the purely-elastic layered stack — assert the expected ordering.
        assert dx_s > dx_l, (
            f"Expected SFI_MVLEM_3D drift ({dx_s:.6e}) to exceed elastic "
            f"LayeredShell drift ({dx_l:.6e})"
        )

    def test_tcl_export_contains_sfi_mvlem(self, tmp_path):
        """Tcl export emits the SFI_MVLEM_3D element command."""
        from fea_toolkit.opensees.builder import export_model_to_tcl

        mm = Preprocessor(_sfi_mvlem_config()).run(_wall_model_data())
        out = tmp_path / "wall_sfi.tcl"
        # export_model_to_tcl accepts MeshModel at runtime (docstring states
        # "Also accepts MeshModel"); the annotation is the narrower
        # SAPModelData type — ignore for this documented use.
        export_model_to_tcl(mm, str(out))  # type: ignore[arg-type]
        text = out.read_text()
        assert "element SFI_MVLEM_3D" in text
        assert str(mm.wall_elements["W1"].elem_tag) in text
        # The 5-fiber thick/width/mat lists are present
        assert "-thick" in text and "-width" in text and "-mat" in text


# ── MVLEM_3D (uniaxial) wall path ──────────────────────────────────


def _mvlem_3d_model_data() -> SAPModelData:
    """Wall model data plus the MVLEM_3D shear-spring and dummy-steel materials.

    ``dummy`` is the tiny-E elastic interior steel (boundary fibres get the
    real ``steel``); ``shear`` carries an ``E_mod`` so the builder computes
    the ElasticPP shear-spring stiffness ``k = 0.1·G·A/h`` (same recipe as
    ``local/probe_mvlem_sfi.py``).  Model units kN/m — direct-construction
    exception of ``.clinerules`` §4.6.
    """
    md = _wall_model_data()
    md.materials["dummy"] = Material(
        name="dummy",
        type="Rebar",
        E_mod=200.0e6,
        nu=0.3,
        Fy=420.0e3,
        unit_weight=0.0,
    )
    md.materials["shear"] = Material(
        name="shear",
        type="Concrete",
        E_mod=30.0e6,
        nu=0.2,
        Fc=30.0e3,
        unit_weight=0.0,
    )
    return md


def _mvlem_3d_config() -> dict:
    """MVLEM_3D path: 5 macro-fibres, uniaxial concrete/steel + shear spring.

    Boundary fibers (0 and 4) carry the real ``steel`` (Steel02) and
    interior fibres the ``dummy`` (tiny-E Elastic) — mirroring the converged
    ``local/probe_mvlem_3d.py`` layout.  ``shear`` is the single horizontal
    ElasticPP spring; ``density`` feeds the per-fibre ``-rho`` list.
    """
    cfg = _base_config()
    cfg["element_strategies"] = {
        "wall": {
            "element_type": "MVLEM_3D",
            "material_type": "uniaxial",
            "n_fibers": 5,
            "CoR": 0.4,
            "concrete_material": "concrete",
            "steel_material": "steel",
            "dummy_material": "dummy",
            "shear_material": "shear",
            "density": 2400.0,
            "boundary_fibers": 1,
        },
    }
    return cfg


def _mvlem_3d_concrete01_config() -> dict:
    """MVLEM_3D with opt-in ``Concrete01`` concrete law (documented dead-end).

    Kept only to preserve the negative-result record: Concrete01's
    zero-tangent tension branch makes the MVLEM_3D macro-element singular
    whenever any fibre goes into tension — the model never converges, even
    on pure axial 7200 kN (see local/probe_mvlem_cm_ratio.py and
    docs/mvlem_wall_analysis.md §7.1).  The corresponding tests are
    ``xfail(run=False)``.
    """
    cfg = _mvlem_3d_config()
    cfg["mvlem_3d_concrete_law"] = "Concrete01"
    return cfg


def _apply_axial(mesh_model, axial_kN: float):
    """Apply a total vertical load at the top nodes via a plain pattern.

    Returns the top node tags (the MVLEM_3D wall source area is inactive,
    so gravity has no area-load carrier in these test models).
    """
    top_z = max(nd.z for nd in mesh_model.nodes.values())
    top_tags = [nd.node_tag for nd in mesh_model.nodes.values() if abs(nd.z - top_z) < 1e-9]
    ops.timeSeries("Linear", 9000)
    ops.pattern("Plain", 9000, 9000)
    n = len(top_tags)
    for t in top_tags:
        ops.load(t, 0.0, 0.0, -axial_kN / n, 0.0, 0.0, 0.0)
    return top_tags


class TestMVLEM3DWallPushover:
    """Preprocessor → AnalysisBuilder MVLEM_3D (uniaxial) wall path."""

    def teardown_method(self):
        ops.wipe()

    def test_preprocessor_generates_wall_element(self):
        """MVLEM_3D config produces a single WallElement with uniaxial fields."""
        md = _mvlem_3d_model_data()
        mm = Preprocessor(_mvlem_3d_config()).run(md)
        assert len(mm.wall_elements) == 1
        wall = mm.wall_elements["W1"]
        assert wall.m == 5
        assert wall.CoR == 0.4
        assert wall.material_type == "uniaxial"
        assert wall.element_type == "MVLEM_3D"
        assert abs(sum(wall.width) - 4.0) < 1e-6
        # Concrete: every fibre; steel: real rebar at boundaries, dummy interior
        assert wall.concrete_names == ["concrete"] * 5
        assert wall.steel_names[0] == "steel"
        assert wall.steel_names[-1] == "steel"
        assert all(n == "dummy" for n in wall.steel_names[1:-1])
        assert wall.shear_name == "shear"
        assert wall.rho == [2400.0] * 5
        assert wall.node_ids == ["1", "2", "4", "3"]
        assert mm.area_elements["A1"].inactive is True

    def test_build_domain_creates_mvlem_3d(self):
        """build_domain() emits exactly one MVLEM_3D element."""
        mm = Preprocessor(_mvlem_3d_config()).run(_mvlem_3d_model_data())
        builder = AnalysisBuilder(mm, _mvlem_3d_config())
        builder.build_domain()
        tags = list(ops.getEleTags())
        assert mm.wall_elements["W1"].elem_tag in tags

    def test_100kn_pushover_reaction(self):
        """MVLEM_3D wall carries 100 kN and reacts −100 kN in X (converges)."""
        md = _mvlem_3d_model_data()
        mm = Preprocessor(_mvlem_3d_config()).run(md)
        __, results = _run_pushover(mm, _mvlem_3d_config())
        rx = _sum_rx(results)
        assert abs(rx + 100.0) < 1.0, f"rx={rx} expected ≈ −100"

    def test_build_twice_tag_stable(self):
        """Repeated build_domain() keeps the MVLEM_3D tag stable."""
        mm = Preprocessor(_mvlem_3d_config()).run(_mvlem_3d_model_data())
        b1 = AnalysisBuilder(mm, _mvlem_3d_config())
        b1.build_domain()
        tag1 = mm.wall_elements["W1"].elem_tag
        n1 = len(ops.getEleTags())
        b2 = AnalysisBuilder(mm, _mvlem_3d_config())
        b2.build_domain()
        n2 = len(ops.getEleTags())
        assert b2.mesh_model.wall_elements["W1"].elem_tag == tag1
        assert n1 == n2

    def test_tcl_export_contains_mvlem_3d(self, tmp_path):
        """Tcl export emits the MVLEM_3D element command."""
        from fea_toolkit.opensees.builder import export_model_to_tcl

        mm = Preprocessor(_mvlem_3d_config()).run(_mvlem_3d_model_data())
        out = tmp_path / "wall_mvlem.tcl"
        # export_model_to_tcl accepts MeshModel at runtime (docstring states
        # "Also accepts MeshModel"); the annotation is the narrower
        # SAPModelData type — ignore for this documented use.
        export_model_to_tcl(mm, str(out))  # type: ignore[arg-type]
        text = out.read_text()
        assert "element MVLEM_3D" in text
        assert str(mm.wall_elements["W1"].elem_tag) in text
        # The 5-fiber thick/width/rho/concrete/steel/shear lists are present
        assert "-thick" in text and "-width" in text and "-rho" in text
        assert "-matConcrete" in text and "-matSteel" in text and "-matShear" in text

    def test_mvlem_3d_supported_by_wheel(self):
        """Fail loudly if the wheel lacks MVLEM_3D element support.

        The Tcl export only serialises topology/metadata — it would emit an
        ``element MVLEM_3D`` command even on a wheel that cannot parse it.
        This raw, non-suppressed call either raises or leaves the element
        out of the domain, so the assertion below fails loudly on a wheel
        without MVLEM_3D support.
        """
        try:
            ops.wipe()
            ops.model("basic", "-ndm", 3, "-ndf", 6)
            # Wall in the X-Z plane (same layout as local/probe_mvlem_3d.py).
            ops.node(1, 0.0, 0.0, 0.0)
            ops.node(2, 4.0, 0.0, 0.0)
            ops.node(3, 0.0, 0.0, 3.0)
            ops.node(4, 4.0, 0.0, 3.0)
            # Model units kN, m → 30 MPa = 30e3 kN/m², 420 MPa = 4.2e5 kN/m².
            ops.uniaxialMaterial(
                "ConcreteCM",
                1,
                -30.0e3,
                -0.002,
                30.0e6,
                5.0,
                -0.0002,
                3.0e3,
                0.0001,
                1.5,
                0.0001,
            )
            ops.uniaxialMaterial("Steel02", 2, 4.2e5, 200.0e6, 0.01)
            # Shear spring: k = 0.1·Gc·A/h with Gc = 0.4·Ec (kN/m).
            ops.uniaxialMaterial("ElasticPP", 3, 0.1 * 0.4 * 30.0e6 * 0.3 * 4.0 / 3.0, 1.0e6)
            ops.uniaxialMaterial("Elastic", 4, 1.0e-3)
            ops.element(
                "MVLEM_3D",
                1,
                1,
                2,
                3,
                4,
                5,
                "-thick",
                *([0.3] * 5),
                "-width",
                *([0.8] * 5),
                "-rho",
                *([2400.0] * 5),
                "-matConcrete",
                *([1] * 5),
                "-matSteel",
                2,
                4,
                4,
                4,
                2,
                "-matShear",
                3,
                "-CoR",
                0.4,
            )
            assert 1 in ops.getEleTags(), "MVLEM_3D element not created"
        finally:
            ops.wipe()

    @pytest.mark.xfail(
        reason=(
            "Concrete01 zero-tangent tension branch is singular for "
            "MVLEM_3D — the macro-element never converges even on pure "
            "axial 7200 kN (KrylovNewton+NewtonLineSearch+ModifiedNewton "
            "all fail at ~35-40 % load in local/probe_mvlem_cm_ratio.py). "
            "The MVLEM_3D axial softness is geometric (uz·H ≈ const, "
            "Ec-independent, verified: scaling ConcreteCM's Ec 26.8× "
            "changes uz_cm not at all), so no concrete-law calibration can "
            "fix it; see docs/mvlem_wall_analysis.md §7.1."
        ),
        strict=False,
        run=False,
    )
    def test_concrete01_variant_builds_and_carries_100kn(self):
        """MVLEM_3D(Concrete01) carries the 100 kN push under axial preload.

        Concrete01 has no tension branch, so the lateral push must follow
        the protocol-order: gravity (7200 kN preload keeps every fibre in
        compression) then lateral.  This mirrors the comparison example's
        gravity-then-push sequence.
        """
        P_axial = 0.2 * 30.0e3 * (4.0 * 0.3)  # 7200 kN (= 0.2·fc·Ag)
        md = _mvlem_3d_model_data()
        mm = Preprocessor(_mvlem_3d_concrete01_config()).run(md)
        b = AnalysisBuilder(mm, _mvlem_3d_concrete01_config())
        b.build_domain()
        _apply_axial(mm, P_axial)
        b.run_static_analysis(extract_reactions=True)
        ops.loadConst("-time", 0.0)
        ops.wipeAnalysis()
        ops.timeSeries("Linear", 5001)
        ops.pattern("Plain", 5001, 5001)
        ops.load(3, 50.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        ops.load(4, 50.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        results = b.run_static_analysis()
        rx = _sum_rx(results)
        assert abs(rx + 100.0) < 1.0, f"rx={rx} expected ≈ −100"

    @pytest.mark.xfail(
        reason=(
            "Concrete01 zero-tangent tension branch is singular for "
            "MVLEM_3D — even pure-axial 7200 kN does not converge (see "
            "local/probe_mvlem_cm_ratio.py).  The premise '~37x-soft "
            "ConcreteCM initial tangent' was disproved: scaling the "
            "ConcreteCM input Ec by 26.8× leaves uz_cm bit-for-bit "
            "unchanged, and uz·H ≈ 0.08 m² is constant across heights — "
            "the MVLEM_3D axial softness is a kinematic/geometric property "
            "of the macro-element, not a material bug; see "
            "docs/mvlem_wall_analysis.md §7.1."
        ),
        strict=False,
        run=False,
    )
    def test_concrete01_axial_compression_matches_elastic_shell(self):
        """Concrete01 fixes ConcreteCM's ~37x-soft axial crushing.

        Apply the uniform protocol pre-load P = 0.20·fc·Ag = 7200 kN at
        the top and compare top-node Z settlement.

        ConcreteCM's crushed initial tangent in OpenSeesPy 3.8.0.0 makes
        the MVLEM_3D axial settlement ~37x the elastic value; Concrete01
        (E0 = Ec exact) collapses uz to the elastic LayeredShell value.

        Note: lateral drift at 100 kN is *not* used as the matched-
        stiffness metric — it is dominated by the MVLEM_3D shear spring
        (k = 0.1·G·A/h), identical for both concrete laws.
        """
        P_axial = 0.2 * 30.0e3 * (4.0 * 0.3)  # 7200 kN (= 0.2·fc·Ag)

        def _axial_settlement(config, model_data) -> tuple[float, float]:
            mm = Preprocessor(config).run(model_data)
            b = AnalysisBuilder(mm, config)
            b.build_domain()
            _apply_axial(mm, P_axial)
            res = b.run_static_analysis(extract_reactions=True)
            uz = res["nodal_displacements"]["3"][2]
            fz = sum(rx["fz"] for rx in res["reactions"].values())
            return uz, fz

        ops.wipe()
        uz_01, fz_01 = _axial_settlement(_mvlem_3d_concrete01_config(), _mvlem_3d_model_data())
        ops.wipe()
        uz_cm, fz_cm = _axial_settlement(_mvlem_3d_config(), _mvlem_3d_model_data())
        ops.wipe()
        uz_shell, fz_shell = _axial_settlement(_layered_shell_config(), _wall_model_data())

        # All three react the full 7200 kN axial pre-load.
        assert abs(fz_01 - P_axial) < 1.0, f"Concrete01 axial reaction {fz_01}"
        assert abs(fz_cm - P_axial) < 1.0, f"ConcreteCM axial reaction {fz_cm}"
        assert abs(fz_shell - P_axial) < 1.0, f"LayeredShell axial reaction {fz_shell}"

        # ConcreteCM's soft tangent crushes the wall (settlement ~37x the
        # elastic value); Concrete01 must collapse the gap by > 5x.
        assert abs(uz_cm) > abs(uz_01) * 5.0, (
            f"ConcreteCM settlement {uz_cm:.6e} should far exceed Concrete01 {uz_01:.6e}"
        )
        # Concrete01 settlement within 2x of the elastic shell reference.
        assert abs(uz_01) < abs(uz_shell) * 2.0 + 1e-9, (
            f"Concrete01 settlement {uz_01:.6e} should be near elastic shell {uz_shell:.6e}"
        )
