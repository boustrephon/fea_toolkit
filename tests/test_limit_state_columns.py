"""Integration: Elwood & Moehle column limit-state instrumentation (Phase 3).

Exercises the Phase-3 builder path end to end on the genuinely-3D RC frame
(``make_rc_frame_3d``, kN-m kPa model):

* ``_ensure_limit_state_units`` — automatic rescale of the builder's mesh to
  the kip-in-ksi domain the ``limitCurve`` equations require (the caller's
  mesh is untouched), with an opt-out flag.
* ``_prepare_limit_state_columns`` — topology planning (control/anchor nodes,
  re-pointed roof beams, anchor restraint) and the tributary gravity axial
  load ``P_g`` (explicit ``column_gravity_loads`` overrides win).
* ``_create_limit_state_columns`` — OpenSees emission of the ``limitCurve``
  Shear/ThreePoint surfaces, the ``LimitState`` materials and the two
  ``zeroLength`` springs.
* Repeated ``build_domain()`` idempotency (canonical-state restore).
* A gravity **static analysis** through the emitted springs (CenterCol
  solver recipe), including a realistic-tributary ``P_g`` regression guard.

The physics itself (drift equations, parameter extraction, ThreePoint fit) is
covered separately in ``tests/test_elwood_limit_state.py``.  The prototype
reference is ``local/elwood_prototype.py`` (PEER 2003/01, validated command
forms for OpenSeesPy 3.8.0).
"""

from typing import Optional

import openseespy.opensees as ops
import pytest

from examples.sample_model import make_rc_frame_3d
from fea_toolkit.analysis.elwood_limit_state import elwood_shear_limit_force
from fea_toolkit.opensees.analysis_builder import AnalysisBuilder
from fea_toolkit.opensees.preprocessor import preprocess_model

_CFG = {"element_type": "elasticBeamColumn", "verbose": False, "create_shells": False}

# 21 frame elements (9 columns + 12 beams) + 2 limit-state zeroLength springs.
_N_FRAME_ELEMS = 21
_N_DOMAIN_ELEMS = _N_FRAME_ELEMS + 2


def _limit_state_builder(cfg_extra: Optional[dict] = None) -> tuple[AnalysisBuilder, object]:
    """Preprocessed rc3d frame wrapped in an AnalysisBuilder with limit states.

    Adds tie data to the ``COL`` section (the sample model has none) so the
    Elwood parameters are non-degenerate: 8 mm ties @ 150 mm, with ``f_yt``
    resolved from the ``Rebar`` material (413.685 MPa -> ~60 ksi after the
    kip-in rescale).
    """
    cfg = dict(_CFG)
    if cfg_extra:
        cfg.update(cfg_extra)
    mesh_model = preprocess_model(make_rc_frame_3d(), cfg)
    sec = mesh_model.sections["COL"]
    sec.tie_diameter = 0.008  # m
    sec.tie_spacing = 0.15  # m
    return AnalysisBuilder(mesh_model, cfg), mesh_model


@pytest.fixture
def limit_state_builder():
    builder, mesh_model = _limit_state_builder({"limit_state_columns": ["1"]})
    yield builder, mesh_model
    ops.wipe()


@pytest.fixture(autouse=True)
def _wipe_opensees_after_each_test():
    """Guarantee ``ops.wipe()`` after every test in this module.

    Several tests (``test_auto_convert_opt_out``, the planning overrides,
    ``TestLimitStateAnalysis``) build the domain through ``_limit_state_builder``
    directly and never request the ``limit_state_builder`` fixture, so the
    committed OpenSees global state would otherwise leak into the next test.
    The ``yield`` defers the wipe until after the test body.
    """
    yield
    ops.wipe()


# ═════════════════════════════════════════════════════════════════════
# Unit auto-conversion (kip-in-ksi enabler)
# ═════════════════════════════════════════════════════════════════════


class TestUnitAutoConversion:
    def test_builder_mesh_rescaled_callers_mesh_untouched(self, limit_state_builder):
        builder, mesh_model = limit_state_builder
        # The caller's mesh stays in kN-m...
        assert mesh_model.units.get("L") == "m"
        assert mesh_model.units.get("F") == "KN"
        # ...while the builder's internal mesh was deep-copied to kip-in.
        assert builder.mesh_model.units.get("L") == "in"
        assert builder.mesh_model.units.get("F") == "kip"
        # Coordinates actually rescaled (3 m storey -> inches).
        top = builder.mesh_model.nodes["10"]
        assert top.z == pytest.approx(3.0 / 0.0254, rel=1e-3)

    def test_converted_domain_builds(self, limit_state_builder):
        builder, _ = limit_state_builder
        builder.build_domain()  # must succeed after the rescale
        assert builder._limit_state_plan

    def test_auto_convert_opt_out(self):
        # Opting out of the automatic rescale with a non-kip-in mesh is a
        # misconfiguration: the Elwood limitCurve equations are
        # hard-anchored to kip-in-ksi, so the builder refuses to proceed
        # instead of silently building an incompatible domain.
        with pytest.raises(ValueError, match="kip-in"):
            _limit_state_builder(
                {
                    "limit_state_columns": ["1"],
                    "limit_state_auto_convert_units": False,
                }
            )


# ═════════════════════════════════════════════════════════════════════
# Topology planning + gravity axial loads
# ═════════════════════════════════════════════════════════════════════


class TestLimitStatePlanning:
    def test_plan_populated_for_vertical_rc_column(self, limit_state_builder):
        builder, _ = limit_state_builder
        builder.build_domain()
        plan = builder._limit_state_plan
        assert len(plan) == 1
        col = plan[0]
        assert col["eid"] == "1"
        assert col["axis"] == 2  # Z-aligned column
        assert col["axial_dof"] == 3
        assert col["shear_dof"] == 1
        # Control/anchor nodes are the first free tags (model uses 1..18).
        assert col["control_tag"] == 19
        assert col["anchor_tag"] == 20
        # Tie data present -> non-degenerate Elwood parameters.
        assert col["params"].fsw > 0.0
        assert col["params"].rho > 0.0
        assert col["params"].shear_elastic_slope > 0.0
        assert col["params"].axial_elastic_slope > 0.0

    def test_topology_instrumentation(self, limit_state_builder):
        builder, mesh_model = limit_state_builder
        builder.build_domain()
        mesh = builder.mesh_model
        assert "1_limit_top" in mesh.nodes
        assert "1_limit_anchor" in mesh.nodes
        # Anchor is fully fixed; the caller's mesh has none of this.
        assert "1_limit_anchor" in mesh.restraints
        assert "1_limit_top" not in mesh_model.nodes
        # The two roof beams framing into column 1 (x-beam 10-11 and
        # y-beam 10-13) now frame into the control node (Elwood series
        # model: beams above the spring).
        beam10, beam16 = mesh.frame_elements["10"], mesh.frame_elements["16"]
        assert "1_limit_top" in (beam10.node_i, beam10.node_j)
        assert "1_limit_top" in (beam16.node_i, beam16.node_j)

    def test_gravity_axial_load_derivation(self, limit_state_builder):
        builder, _ = limit_state_builder
        # Pre-build tributary estimate (kip, after the rescale).
        p_g = builder._derive_gravity_axial_loads(["1"])["1"]
        assert p_g > 0.0
        builder.build_domain()
        assert builder._limit_state_plan[0]["p_g"] == pytest.approx(p_g, rel=1e-6)

    def test_column_gravity_load_override_wins(self):
        builder, _ = _limit_state_builder(
            {"limit_state_columns": ["1"], "column_gravity_loads": {"1": 42.0}}
        )
        assert builder._derive_gravity_axial_loads(["1"])["1"] == pytest.approx(42.0)
        builder.build_domain()
        assert builder._limit_state_plan[0]["p_g"] == pytest.approx(42.0)

    def test_shear_residual_ratio_config_is_live(self):
        """``limit_state_shear_residual_ratio`` actually drives ``params.fres_shear``.

        Regression guard: the key was previously dead because ``fres_shear``
        was always set to a fixed ``0.10 * V(0.01)`` before
        ``elwood_column_parameters``, which only consults
        ``shear_residual_ratio`` when ``fres_shear`` is ``None``.
        """
        builder, _ = _limit_state_builder(
            {
                "limit_state_columns": ["1"],
                "limit_state_shear_residual_ratio": 0.25,
            }
        )
        builder.build_domain()
        col = builder._limit_state_plan[0]
        v_ref = elwood_shear_limit_force(0.01, col["p_g"], col["geometry"], builder.units)
        assert col["params"].fres_shear == pytest.approx(0.25 * v_ref)


# ═════════════════════════════════════════════════════════════════════
# OpenSees emission + rebuild idempotency
# ═════════════════════════════════════════════════════════════════════


class TestLimitStateEmission:
    def test_domain_contains_limit_state_objects(self, limit_state_builder):
        builder, _ = limit_state_builder
        builder.build_domain()
        # Synthetic material tags registered by the builder.
        for key in (
            "limit_state_rigid_1",
            "limit_state_shear_1",
            "limit_state_axial_1",
            "limit_state_soft_1",
        ):
            assert builder.material_tags.get(key, 0) > 0
        # 21 frame elements + 2 zeroLength springs on the domain.
        assert len(set(ops.getEleTags())) == _N_DOMAIN_ELEMS

    def test_rebuild_is_idempotent(self, limit_state_builder):
        builder, _ = limit_state_builder
        builder.build_domain()
        first = builder._limit_state_plan
        assert len(set(ops.getEleTags())) == _N_DOMAIN_ELEMS

        builder.build_domain()
        second = builder._limit_state_plan
        assert len(second) == 1
        # Canonical restore removed the old instrumentation, so the
        # rebuild keeps exactly one control + one anchor node pair...
        limit_ids = sorted(
            nid for nid in builder.mesh_model.nodes if nid.endswith(("_limit_top", "_limit_anchor"))
        )
        assert limit_ids == ["1_limit_anchor", "1_limit_top"]
        # ...and deterministically reuses the same node tags.
        assert second[0]["control_tag"] == first[0]["control_tag"]
        assert second[0]["anchor_tag"] == first[0]["anchor_tag"]
        assert second[0]["p_g"] == pytest.approx(first[0]["p_g"])
        # The domain is rebuilt from scratch (no element accumulation).
        assert len(set(ops.getEleTags())) == _N_DOMAIN_ELEMS
        # Re-pointed beams still frame into the control node.
        beam10 = builder.mesh_model.frame_elements["10"]
        assert "1_limit_top" in (beam10.node_i, beam10.node_j)


# ═════════════════════════════════════════════════════════════════════
# Analysis through the LimitState springs (gravity stage)
# ═════════════════════════════════════════════════════════════════════


class TestLimitStateAnalysis:
    def test_gravity_static_converges_and_balances(self):
        """Gravity runs end-to-end through the emitted LimitState springs.

        Uses the documented CenterCol solver recipe (Penalty / ProfileSPD /
        NormDispIncr + ramped gravity) — the LimitState material is
        convergence-sensitive and the toolkit's default
        Transformation/BandGen chain can otherwise diverge to the
        documented ``Norm = NaN`` gravity failure (see
        ``docs/shear_failure_modelling.md`` Phase 3).
        """
        builder, _ = _limit_state_builder(
            {
                "limit_state_columns": [str(i) for i in range(1, 10)],
                "solver_constraints": "Penalty",
                "solver_system": "ProfileSPD",
                "solver_test_type": "NormDispIncr",
                "solver_test_tol": 1e-4,
                "gravity_num_substeps": 5,
            }
        )
        res = builder.run_static_analysis(pattern_scales={"DEAD": 1.0}, extract_reactions=True)
        # Total vertical reactions balance the kip-in gravity combination
        # (9 columns + 12 roof beams: ~270 kip).
        reac = res["reactions"]
        rz = sum(float(v["fz"]) for v in reac.values())
        assert 250.0 < rz < 300.0
        # Every planned column sees a realistic operating axial load.
        for col in builder._limit_state_plan:
            assert 18.0 < col["p_g"] < 50.0

    def test_gravity_axial_load_derivation_is_realistic(self):
        """P_g is the tributary gravity axial load, not a runaway sum.

        Regression guard for the ``Norm = NaN`` gravity failure: the
        ``_stack_gravity_axial`` recursion must only treat Z-aligned
        members as "columns above".  An *axis*-aligned check also flags
        the horizontal roof beams, inflating P_g ~430x; that inflated
        axial load collapses the shear limit surface to zero at 1% drift,
        giving the LimitState material a degenerate zero backbone
        (0/0 tangent -> NaN).
        """
        builder, _ = _limit_state_builder({"limit_state_columns": ["1"]})
        p_g = builder._derive_gravity_axial_loads(["1"])["1"]
        # Two roof beams frame into column 1; each contributes half of
        # (self-weight + 20 kN/m floor load) = 21.36 kip in the kip-in domain.
        assert p_g == pytest.approx(21.36, rel=0.1)
        # ...and the plan picks it up so the emitted backbones are sane.
        builder.build_domain()
        assert builder._limit_state_plan[0]["p_g"] == pytest.approx(p_g, rel=1e-6)
