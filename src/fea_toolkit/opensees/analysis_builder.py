"""Analysis builder — create OpenSees domain from a prepared ``MeshModel``.

The :class:`AnalysisBuilder` takes a :class:`~fea_toolkit.model.mesh_model.MeshModel`
(fully prepared topology from the :class:`~fea_toolkit.opensees.preprocessor.Preprocessor`)
and creates the OpenSees domain objects.  It handles all analysis execution
and result extraction — no topology mutations occur here.
"""

import contextlib
import logging
import math
from typing import TYPE_CHECKING, Any, ClassVar, Optional

import openseespy.opensees as ops

if TYPE_CHECKING:
    # pandas is not a required dependency — only imported at runtime inside
    # check_load_equilibrium().  The TYPE_CHECKING guard lets Ruff resolve
    # the "pd.DataFrame" return annotation statically without adding pandas
    # to the core dependencies.
    pass


from ..model.mesh_model import MeshModel

logger = logging.getLogger(__name__)


from ._constraints import ConstraintMixin
from ._elements import ElementMixin
from ._limit_state import LimitStateMixin
from ._loads import LoadMixin
from ._materials import MaterialMixin
from ._runners import RunnerMixin, _normalise_frame_response, _record_step
from ._sections import SectionMixin
from .releases import DEFAULT_RIGIDITY_FACTOR, DEFAULT_SOFTNESS_FACTOR

__all__ = [
    "AnalysisBuilder",
    "_normalise_frame_response",
    "_record_step",
    "run_modal",
    "run_review_analysis",
]


class AnalysisBuilder(
    RunnerMixin,
    ElementMixin,
    SectionMixin,
    MaterialMixin,
    LoadMixin,
    LimitStateMixin,
    ConstraintMixin,
):
    """Create and analyse an OpenSees model from a prepared MeshModel.

    Usage::

        builder = AnalysisBuilder(mesh_model, config)
        builder.build_domain()
        builder.create_loads({"DEAD": 1.0})
        results = builder.run_static_analysis()

    Args:
        mesh_model: Prepared topology from the Preprocessor.
        config: Flat builder-scoped configuration dict — the same keys
            accepted by :class:`~fea_toolkit.opensees.preprocessor.Preprocessor`
            and the analysis runners (see ``docs/builder_reference.md``).
    """

    # ── Solver defaults for pushover / nonlinear analysis ────────────
    PUSHOVER_SOLVER_DEFAULTS: ClassVar[dict] = {
        "solver_test_type": "NormDispIncr",
        "solver_test_tol": 1e-6,
        "solver_test_max_iter": 10,
        "solver_algorithm": "Newton",
        "solver_constraints": "Transformation",
        "solver_system": "BandGen",
        "gravity_num_substeps": 1,
    }

    # ── Fallback solver settings for RC softening (Gap 5) ────────────
    # When a step fails with the primary settings (e.g. the NC fiber-
    # rebuild gravity solve), retry with NormUnbalance + relaxed
    # tolerance + ModifiedNewton(-initial), then restore the primary
    # settings for subsequent steps.  NormUnbalance is safer than
    # NormDispIncr for forceBeamColumn elements carrying eleLoad member
    # loads — the displacement increment can be zero while the residual
    # is still large (Michael Scott, OpenSeesDigital 2026).
    #
    # The fallback test tolerance is computed at runtime, scaled off the
    # model's characteristic weight (total mass × g via g_from_units) so
    # it is unit-consistent.  An unscaled absolute tolerance (e.g. 1e-12)
    # is numerically unattainable for a full RC building with kN-m
    # residuals — the fallback would always burn its iteration budget and
    # never converge.
    PUSHOVER_FALLBACK_DEFAULTS: ClassVar[dict] = {
        "solver_test_type": "NormUnbalance",
        "solver_test_max_iter": 1000,
        "solver_algorithm": "ModifiedNewton",
    }

    # ── Pushover primary solver settings (P3 empirical finding) ───────
    # The pushover uses the general PUSHOVER_SOLVER_DEFAULTS (NormDispIncr
    # 1e-6 / 10 / Newton) pre-filled by _set_defaults().  An earlier
    # documented contract claimed "NormDispIncr 1e-4 / 20" (see the stale
    # comment in run_pushover_analysis), but that looser setting was never
    # actually effective (the .get(key, 1e-4) fallback cannot fire once the
    # general defaults pre-fill the config), and the 2026-08-24 empirical
    # pass (P3) showed it is NOT universally safe: 1e-4/20 breaks the Duong
    # flexure-only pushover (forceBeamColumn element state-determination
    # divergence) while 1e-6/10 converges every validated benchmark (V&E,
    # Duong, RC/steel/LayeredShell).  Looser tolerances (e.g. 2e-4/1000)
    # remain available as an explicit per-model opt-in — see
    # docs/pushover_analysis.md.

    # ── LayeredShell gravity substeps (auto-detection) ───────────────
    # RC models with LayeredShell walls (resolved via the Preprocessor's
    # ``shell_layers`` config) can fail the gravity stage with a NaN
    # stiffness shock if the full gravity combination is applied in a
    # single ``LoadControl`` step.  When any LayeredShell section is
    # present, the builder ramps gravity over this many substeps by
    # default.  Users may still override ``gravity_num_substeps`` in the
    # config dict — the explicit value wins.
    LAYERED_SHELL_GRAVITY_SUBSTEPS: ClassVar[int] = 10

    # ── Sparse solver auto-selection threshold ───────────────────────
    # ``BandGen`` (LAPACK DGBSV) stores the full band and becomes both
    # slow and numerically fragile as the model grows (wide RCM bandwidth
    # after shell meshing): the banded factorisation can stall Newton
    # iterations just above the tolerance or emit NaN, failing every
    # pushover/static step, while the sparse ``UmfPack`` solver converges
    # the identical model cleanly (Portwood Digital, "Stop Cargo Culting
    # BandGeneral and Plain Numberer", Feb 2026).  Above this node-count
    # threshold — and only when the user has NOT set ``solver_system``
    # explicitly — the builder defaults to ``UmfPack``.
    SPARSE_SOLVER_NODE_THRESHOLD: ClassVar[int] = 600

    def __init__(self, mesh_model: MeshModel, config: Optional[dict[str, Any]] = None):
        self.mesh_model = mesh_model
        self.units = mesh_model.units
        self.config = config or {}
        # Track whether the user explicitly configured gravity substeps so
        # _set_defaults() can auto-select a LayeredShell-safe count when
        # config omits it (see LAYERED_SHELL_GRAVITY_SUBSTEPS and
        # run_static_analysis).  An explicit value always wins.
        self._user_set_gravity_substeps = "gravity_num_substeps" in self.config
        # Same convention for the linear-equation solver: when the user has
        # not chosen one explicitly, _set_defaults() auto-selects a sparse
        # solver for large models (SPARSE_SOLVER_NODE_THRESHOLD).
        self._user_set_solver_system = "solver_system" in self.config
        self._set_defaults()

        # ── Elwood limit-state unit policy ──────────────────────────
        # The Elwood limitCurve equations are anchored to the kip-in-ksi
        # convention.  When limit-state columns are requested the whole
        # domain must run in kip-in-ksi: if the source mesh is in any other
        # system it is internally rescaled (the caller's mesh is left
        # untouched) unless ``limit_state_auto_convert_units`` is disabled.
        if self._limit_state_requested():
            self._ensure_limit_state_units()

        # Pushover step results (populated by run_pushover_analysis)
        self.pushover_step_results: list[dict[str, Any]] = []

        # Last static-analysis result dict (populated by run_static_analysis;
        # read by result-aware viewers, e.g. ModelViewer.overlay_deformed).
        self._last_static_results: Optional[dict[str, Any]] = None

        # Domain state (built during build_domain)
        self.frame_tag_map: dict[str, int] = {}
        self.material_tags: dict[str, int] = dict(self.mesh_model.material_tags)
        self.section_tags: dict[str, int] = dict(self.mesh_model.section_tags)
        self._shell_sec_tags: dict[str, int] = dict(self.mesh_model.shell_sec_tags)
        self._shell_sec_variants: dict[str, int] = dict(self.mesh_model.shell_sec_variants)
        self._frame_element_types: dict[str, str] = dict(self.mesh_model.frame_element_types)
        self._area_element_types: dict[str, str] = dict(self.mesh_model.area_element_types)
        self._offset_rigid_links: list[tuple] = list(self.mesh_model.offset_rigid_links)
        self._edge_constraint_method: Optional[str] = None
        # NOTE: mesh_model.edge_constraint_args is always [] today
        # (the Preprocessor stores detected pairs in detected_edge_pairs,
        # not constraint arguments).  The list is overwritten when
        # apply_edge_constraints() is first called at analysis time.
        self._saved_edge_constraints: list[tuple] = list(self.mesh_model.edge_constraint_args)
        self.edge_loads_from_areas: list = list(self.mesh_model.edge_loads_from_areas)
        self._base_z = self.mesh_model.base_z

        # Per-build tracking
        self._created_node_tags: set = set()
        self._next_variant_tag: int = (
            max(self.section_tags.values(), default=0) + 1 if self.section_tags else 1
        )
        self._rigid_link_elems: dict[str, int] = {}
        self._shell_tag_map: dict[
            str, int
        ] = {}  # area SAP ID → OpenSees element tag (reset at each build_domain())

        # Brace state
        self._brace_selection: Optional[set] = None

        # Mass tracking
        self.node_masses: dict[str, float] = {}
        # Per-source mass totals from compute_seismic_masses() —
        # {"elements": ..., "masses": ..., "loads": ...} in the model's
        # consistent mass unit.  Empty until masses have been computed.
        self.mass_components: dict[str, float] = {}
        # No hardcoded gravity constant: compute_seismic_masses()
        # overwrites this via g_from_units(units).  None until derived.
        self._mass_g: Optional[float] = None

        # Load totals
        self.load_totals: dict[str, float] = {}
        self._sw_load_totals: dict[str, dict[str, float]] = {}
        self._gravity_load_totals: dict[str, float] = {}
        self._joint_load_totals: dict[str, dict[str, float]] = {}

        # Model log
        self._model_log: Optional[Any] = None
        self._model_diagnostics: dict[str, Any] = {}

        # nD materials that were skipped (unknown type / unsupported)
        self._skipped_nd_materials: set[str] = set()
        self._skipped_shell_sec_names: set[str] = set()

        # Transf tags
        self._transf_tags: dict[int, int] = {}

    @property
    def model(self) -> MeshModel:
        """The frozen :class:`MeshModel` this builder was created from.

        The two-stage architecture keeps all topology (nodes, split frame
        elements, sections, assignments) in ``mesh_model``.  The plotting
        layer accesses that topology through ``.model`` — e.g.
        :func:`~fea_toolkit.plotting.plot_interactive_viewer` reads
        ``builder.model.frame_elements`` / ``.frame_assignments``.

        Returns:
            The :class:`MeshModel` passed to :meth:`__init__`.
        """
        return self.mesh_model

    def _set_defaults(self) -> None:
        """Set default configuration values."""
        defaults = {
            "element_type": "elasticBeamColumn",
            "num_int_pts": 3,
            "use_elastic_sections": True,
            "create_fiber_sections": False,
            "verbose": False,
            "geom_transf_type": "Linear",
            "beam_integration": "Lobatto",
            "simplify_distributed_loads": False,
            "constraint_method": "spring",
            "hinge_model": "fiber",  # Distributed plasticity by default
            # ── Member end releases / partial fixity ──
            # Honour SAP2000 frame end releases (and partial-fixity springs)
            # by inserting zero-length release elements at member ends.  On by
            # default — a release is a real property of the source model.
            "apply_releases": True,
            # ── SAP2000 BODY constraints (6-DOF rigid bodies) ──
            # Apply ``CONSTRAINT DEFINITIONS - BODY`` groups as
            # ``rigidLink('beam')`` MPCs.  A BODY constraint is a real model
            # property (a rigid body tie between joints), so it is on by
            # default — omitting it leaves those joints free and makes the
            # model softer.  Set False to disable.
            "apply_rigid_bodies": True,
            # Rigidity factor for the retained (non-released) DOFs on the
            # release element — must be much stiffer than the member it
            # terminates so the release does not soften the member.
            # (Canonical constant in fea_toolkit.opensees.releases.)
            "release_rigidity_factor": DEFAULT_RIGIDITY_FACTOR,
            # Softness factor for fully released DOFs (× member stiffness).
            # Keeps an otherwise-floating released DOF non-singular (e.g. a
            # pinned base whose only member is released); 0.0 = exact release.
            # (Canonical constant in fea_toolkit.opensees.releases.)
            "release_softness_factor": DEFAULT_SOFTNESS_FACTOR,
            # ── RC rebar material (fiber sections) ──
            # Config overrides in SI (Pa): user may override the yield
            # strength / elastic modulus / hardening of the Steel02 rebar
            # used in RC fiber sections.  None → use the section's
            # ``rebar_material`` from the S2K model, else the framework
            # rebar defaults (DEFAULT_FY_REBAR_PA / DEFAULT_E_S_PA).
            "rebar_Fy_override": None,
            "rebar_Es_override": None,
            "rebar_b": 0.01,
            "rebar_R0": 18.0,
            "rebar_cR1": 0.925,
            "rebar_cR2": 0.15,
            # ── FSAM uniaxial concrete (ConcreteCM) ──
            # ConcreteCM is required for FSAM (it implements
            # getCrackingStrain()).  Stress-valued keys are authored in
            # SI (Pa) and scaled to model units; strain/dimensionless
            # keys are passed through unchanged.
            "fsam_conc_fpc_override": None,
            "fsam_conc_ft_override": None,
            "fsam_conc_epcc": 0.002,
            "fsam_conc_rc": 5.0,
            "fsam_conc_xcrn": 0.0002,
            "fsam_conc_et": 0.0001,
            "fsam_conc_rt": 1.5,
            "fsam_conc_xcrp": 0.0001,
            # ── FSAM uniaxial steel (Steel02) ──
            "fsam_steel_Fy_override": None,
            "fsam_steel_Es_override": None,
            "fsam_steel_b": 0.01,
            "fsam_steel_R0": 18.0,
            "fsam_steel_cR1": 0.925,
            "fsam_steel_cR2": 0.15,
            # ── Pushover recording (opt-in) ──
            "record_pushover_steps": False,
            "pushover_record_selection": None,
            "pushover_record_shell_strains": False,
            # ── Confined concrete spalling-strain cap ──
            # Upper bound for the confined core ultimate (spalling)
            # strain when using the Mander confinement model.  The
            # Priestley (1996) formula can predict very large strains;
            # NZSEE C5 uses 0.05.  Mirrors ``ConfinementData.ecu_max``.
            "confined_ecu_max": 0.025,
            # ── Fiber concrete law (post-peak / P5) ─────────────────
            # Concrete01 (Kent-Scott-Park; no tension, flat post-crushing
            # plateau at ``core_residual_factor · f'c``) is the default and
            # reproduces the accepted benchmarks unchanged.
            #
            # ``"Concrete02"`` (Kent-Scott-Park + linear tension softening)
            # adds a *genuine* post-crushing descending branch and a
            # tension-stiffening branch, letting flexure-critical frames
            # shed strength after the peak instead of rising to the push
            # end.  The ``core_residual_factor`` lever (fraction of the
            # concrete strength retained at the crushing strain) applies
            # identically to both laws — lowering it from 0.2 makes the
            # core shed compressive stress as it crushes, producing the
            # post-peak descent.  Both knobs are off by default
            # (Concrete01 / 0.2), so existing models are unchanged.
            "concrete_material": "Concrete01",
            "core_residual_factor": 0.2,
            # Concrete02 post-peak unloading slope ratio (lambda).
            "concrete02_lambda": 0.1,
            # Concrete02 tension branch, authored in SI (Pa) and scaled to
            # model units.  None → ft = DEFAULT_FSAM_CONC_FT_PA (3 MPa),
            # Ets = ft / 0.001 (tension capacity gone at 1e-3 strain).
            "concrete02_ft_override": None,
            "concrete02_Ets_override": None,
            # ── Bond-slip end springs (P5 Phase B) ───────────────────
            # Zero-length Bond_SP01 slip-rotation springs at fiber member
            # ends, in series with the flexural fiber element (off by
            # default).  Bar slip at yield is authored in SI (m) and scaled
            # to model units; the other knobs are dimensionless or
            # multiples.  ``bond_slip_backbone`` may override the derived
            # moment-rotation backbone per member (model units).
            "bond_slip": False,
            "bond_slip_sy_m": 0.000254,  # 0.01 in — Zhao-Sritharan default
            "bond_slip_su_factor": 35.0,  # Su = 35 × Sy (Zhao-Sritharan)
            "bond_slip_mu_factor": 1.4,  # Mu = 1.4 × My
            "bond_slip_b": 0.5,  # strain-hardening ratio
            "bond_slip_R": 0.7,  # pinching factor
            "bond_slip_backbone": None,
            # ── Shear-flexible section aggregation (opt-in) ──
            # Wrap fiber sections in a SectionAggregator with an elastic
            # shear material (GA_v on Vy/Vz) so beam-column members gain
            # the Timoshenko transverse-shear flexibility that plain fiber
            # sections lack.  Off by default — existing models keep their
            # shear-rigid Euler-Bernoulli response unchanged.
            #
            # Accepts:
            #   False          — no aggregation (default)
            #   True/"elastic" — Elastic GA_v shear term only
            #   "nonlinear"    — trilinear simplified-MCFT backbone
            #                    (cracking → peak V_n → degrading → residual)
            #                    derived per section via shear_capacity.py.
            #
            # NOTE: section shear DOFs are only engaged by flexibility-
            # based elements — use ``fiber_element_type =
            # "forceBeamColumn"`` for the shear aggregation to take effect.
            # ``dispBeamColumn`` (Euler-Bernoulli, displacement-based)
            # never computes section shear deformation, so aggregation is
            # silently inert for it.
            "aggregate_shear": False,
            "shear_area_factor": 5.0 / 6.0,  # rectangular A_v = f·A
            # Explicit nonlinear shear backbone override for
            # ``aggregate_shear="nonlinear"``: a dict with keys ``v_cr``,
            # ``g_cr``, ``v_n``, ``g_n``, ``v_r``, ``g_r`` (model units),
            # applied to every aggregated section.  ``None`` (default)
            # auto-derives the backbone per section from the MCFT capacity
            # model in ``fea_toolkit.capacity.shear_capacity``.
            "shear_backbone": None,
            # Element type used by the fiber-section pushover rebuild
            # (rebuild_with_fiber_sections).  Defaults to dispBeamColumn.
            "fiber_element_type": "dispBeamColumn",
            # MPC-based rigid links (ops.rigidLink) for frame end offsets,
            # instead of very stiff elasticBeamColumn segments.  Avoids the
            # ill-conditioning of stiff elastic links under PDelta pushover
            # (those fail to converge at the gravity stage).
            "rigid_link_mpc": False,
            # ── Elwood & Moehle column limit states (Phase 3) ─────────
            # Zero-length LimitState shear + axial springs in series with
            # the selected columns, mirroring the PEER 2003/01 §8.2.2
            # series model.  Requires a kip-in-ksi domain (auto-converted
            # when ``limit_state_auto_convert_units`` is True).
            "limit_state_columns": None,  # list[str] of frame element IDs
            "column_gravity_loads": None,  # dict[str, float] P_g override
            "limit_state_params": None,  # dict[str, dict] per-column overrides
            "limit_state_pinch_x": 0.5,  # LimitState hysteresis
            "limit_state_pinch_y": 0.4,
            "limit_state_damage1": 0.0,
            "limit_state_damage2": 0.0,
            "limit_state_beta": 0.4,
            # Post-failure shear residual as a fraction of the 1%-drift
            # shear capacity V(0.01) (Elwood's Vr ~ 10 % of the peak).
            "limit_state_shear_residual_ratio": 0.10,
            # Soft axial catch-spring stiffness as a fraction of the axial
            # spring elastic slope (prevents singularity after axial
            # failure; Elwood's example uses ~ 0.02 % of 99·E·A/L).
            "limit_state_soft_axial_fraction": 2.0e-4,
            "limit_state_auto_convert_units": True,
        }
        # Merge solver defaults from the class constant
        defaults.update(self.PUSHOVER_SOLVER_DEFAULTS)
        for k, v in defaults.items():
            self.config.setdefault(k, v)

        # ── LayeredShell gravity substep auto-detection ──────────────
        # RC models with LayeredShell walls (populated by the Preprocessor
        # from the config ``shell_layers`` dict) can fail the gravity stage
        # with a NaN stiffness shock if the full gravity combination is
        # applied in a single ``LoadControl`` step.  When the mesh has any
        # LayeredShell section and the user did NOT explicitly set
        # ``gravity_num_substeps`` in the config, ramp gravity over
        # LAYERED_SHELL_GRAVITY_SUBSTEPS increments automatically so the
        # model behaves well out-of-the-box.  An explicit config value
        # always wins (``_user_set_gravity_substeps`` True).
        #
        # Note: ``getattr`` guards are used because some tests construct
        # the builder via ``AnalysisBuilder.__new__`` (bypassing
        # ``__init__``) and call ``_set_defaults()`` directly.  In that
        # path ``_user_set_gravity_substeps`` is absent — defaulting to
        # ``True`` disables auto-detection so the legacy behaviour is
        # unchanged for such callers.
        _user_set = getattr(self, "_user_set_gravity_substeps", True)
        _mesh = getattr(self, "mesh_model", None)
        if not _user_set and _mesh is not None and _mesh.layered_shell_sections:
            self.config["gravity_num_substeps"] = self.LAYERED_SHELL_GRAVITY_SUBSTEPS

        # ── Sparse solver auto-selection for large models ─────────────
        # ``BandGen`` is robust for small/medium models but breaks down on
        # large shell-meshed buildings (wide bandwidth → slow AND
        # numerically fragile factorisation; Newton stalls or emits NaN).
        # When the user has not explicitly chosen a solver and the mesh is
        # large, fall back to the sparse ``UmfPack`` direct solver — same
        # solution, dramatically faster and more stable.  An explicit
        # ``solver_system`` config value always wins.
        if not getattr(self, "_user_set_solver_system", True):
            _mesh = getattr(self, "mesh_model", None)
            if _mesh is not None and len(_mesh.nodes) >= self.SPARSE_SOLVER_NODE_THRESHOLD:
                self.config["solver_system"] = "UmfPack"

    # ═══════════════════════════════════════════════════════════════
    # Domain construction
    # ═══════════════════════════════════════════════════════════════

    def build_domain(
        self,
        config_overrides: Optional[dict[str, Any]] = None,
    ) -> "AnalysisBuilder":
        """Create the full OpenSees domain from the MeshModel.

        Creates nodes, restraints, materials, sections, frame elements,
        shell elements, lumped hinges, and rigid links.

        Args:
            config_overrides: Optional dict of config keys to temporarily
                override ``self.config`` for this build cycle.  Useful for
                pushover rebuilds that need fiber sections or different
                element types.  The overrides are reset after the build.

        Returns:
            ``self``, so the call can be chained — e.g.
            ``AnalysisBuilder(mesh, {}).build_domain().run_static_analysis()``.
        """
        # Apply temporary config overrides
        _saved_overrides: dict[str, Any] = {}
        if config_overrides:
            for k, v in config_overrides.items():
                _saved_overrides[k] = self.config.get(k)
                self.config[k] = v

        try:
            ops.wipe()
            # The domain has been reset — drop any cached static results so a
            # rebuilt domain cannot expose stale displacements.
            self._last_static_results = None
            self._edge_constraint_method = None
            self._rigid_link_elems = {}
            # Reset skipped-material/section sets so supported materials
            # and sections can recover across rebuilds.
            self._skipped_nd_materials = set()
            self._skipped_shell_sec_names = set()
            # Clear non-LayeredShell tags from shell section tag maps so
            # ElasticMembranePlateSection types are recreated with fresh tags
            # (they cannot be overwritten at the same tag).  LayeredShell
            # sections *can* be overwritten after ops.wipe(), so preserve
            # their tags for lookup-based stability across builds.
            _layered_names = set(self.mesh_model.layered_shell_sections.keys())
            for k in list(self._shell_sec_tags):
                if k not in _layered_names:
                    del self._shell_sec_tags[k]
            self._shell_sec_variants.clear()
            # Reset cached rigid section tag so it is recomputed fresh
            self._rigid_section_tag = None
            ops.model("basic", "-ndm", 3, "-ndf", 6)

            # Pre-compute frame tag map so shell elements can avoid clashing
            self._build_frame_tag_map()

            # Restore canonical hinge state before any nodes are created,
            # preventing stale *_hinge_* nodes from being recreated.
            self._restore_hinge_canonical_state()
            # Restore canonical bond-slip state (endpoints + *_bond_* nodes)
            # so repeated builds re-instrument the original elements.
            self._restore_bond_canonical_state()
            # Restore canonical brace state so repeated build_domain() /
            # rebuild_with_fiber_sections() always subdivide the original
            # (un‑subdivided) elements rather than already-subdivided ones.
            self._restore_brace_canonical_state()
            # Restore canonical release topology (endpoints + *_rel_* nodes)
            # so repeated builds re-instrument the original elements.
            self._restore_release_canonical_state()
            # Restore canonical limit-state column topology (control/anchor
            # nodes, re-pointed beams) so repeated builds instrument the
            # original elements rather than previously-instrumented ones.
            self._restore_limit_state_canonical_state()
            self._prepare_limit_state_columns()

            self._create_nodes()
            self._apply_restraints()
            self._create_nd_materials()
            self._create_materials()
            # FSAM must be created after uniaxial materials (it references
            # their tags), but before layered shell sections (which consume
            # the FSAM nD tag).
            self._create_fsam_materials()
            # SFI_MVLEM_3D wall elements consume the FSAM nD tags resolved
            # above; they must be created after FSAM materials but do not
            # depend on LayeredShell sections.
            self._create_wall_elements()
            self._create_layered_shell_sections()
            self._create_sections()
            self._create_shell_elements()
            self._create_lumped_hinges()
            self._create_bond_slip_springs()
            self._create_member_releases()
            self._create_elements()
            self._create_limit_state_columns()
            self._apply_rigid_diaphragms()
            self._apply_rigid_bodies()
            return self
        finally:
            # Restore any overridden config values
            for k, old_v in _saved_overrides.items():
                if old_v is None:
                    self.config.pop(k, None)
                else:
                    self.config[k] = old_v

    def rebuild_with_fiber_sections(
        self,
        brace_selection: Optional[set] = None,
        pushover_spring_scale: float = 1.0,
    ) -> None:
        """Rebuild the OpenSees domain with fiber sections for pushover.

        Calls :meth:`build_domain` with config overrides that enable fiber
        sections and dispBeamColumn elements.

        Args:
            brace_selection: Optional set of brace element IDs to
                subdivide with initial imperfection (Approach A).
                When provided, the builder stores the selection and
                enables ``subdivide_braces`` so that
                :meth:`_create_elements` will subdivide each brace
                into *brace_n_segments* segments with an initial
                sinusoidal imperfection.
            pushover_spring_scale: Scale factor for edge constraint
                spring stiffness on rebuild (default 1.0).

        Note:
            Braces are subdivided at domain creation time (in
            :meth:`_create_elements`), not deferred to analysis.
            The subdivided elements use ``PDelta`` geometric
            transformation by default, which is required for
            buckling to develop under compression.
        """
        overrides: dict[str, Any] = {
            "element_type": self.config.get("fiber_element_type", "dispBeamColumn"),
            "create_fiber_sections": True,
            "use_elastic_sections": False,
        }
        if brace_selection:
            overrides["geom_transf_type"] = "PDelta"
            overrides["subdivide_braces"] = True
            self._brace_selection = brace_selection

        self.build_domain(config_overrides=overrides)

        # Re-apply edge constraints if previously saved
        if self._saved_edge_constraints:
            self._reapply_edge_constraints(scale=pushover_spring_scale)


def run_modal(mesh_model, n_modes: int = 12, config: dict = None):
    """Run modal analysis through the two-stage path.

    Returns the same dict as :meth:`AnalysisBuilder.run_modal_analysis`.
    """
    from .analysis_builder import AnalysisBuilder

    if config is None:
        config = {"verbose": False}
    ab = AnalysisBuilder(mesh_model, config)
    ab.build_domain()
    ab.compute_seismic_masses()
    modal = ab.run_modal_analysis(num_modes=n_modes, print_results=False)
    shapes = ab.extract_mode_shapes(n_modes)
    return {"modal": modal, "shapes": shapes}


def _run_rs_pass(
    builder,
    md,
    modal: dict[str, Any],
    spec_cfg: Optional[dict[str, Any]] = None,
    num_modes: int = 12,
) -> dict[str, Any]:
    """Run the response-spectrum pass for each configured direction.

    Builds a GB 50011 demand spectrum (see
    :func:`fea_toolkit.spectrum._build_spectrum`), then runs a mode-by-mode
    RS analysis plus the CQC/SRSS nodal displacements for every requested
    direction.  The builder's eigen state must still be current — call this
    immediately after :meth:`AnalysisBuilder.run_modal_analysis`.

    Args:
        builder: A built ``AnalysisBuilder`` with a live eigen state.
        md: Parsed :class:`~fea_toolkit.model.sap_data.SAPModelData` (used
            for the roof-node lookup).
        modal: Modal result dict from ``builder.run_modal_analysis()``.
        spec_cfg: Spectrum configuration — the same keys as the report
            pipeline (:mod:`fea_toolkit.report`): ``level``,
            ``intensity``, ``site_class``, ``acceleration``, ``damping``,
            ``n_modes`` and ``directions``.  An empty dict yields the
            GB 50011 defaults (rare, intensity 7, site class II, 5 %).
        num_modes: Number of modes to include (clamped to the periods
            actually available).

    Returns:
        Dict with ``spectrum`` (the resolved descriptor), ``directions`` —
        a per-direction dict of CQC/SRSS base shear and overturning moment,
        the 6-DoF ``base_reactions_cqc``, the per-mode ``modal_base_shear``
        / ``modal_base_moment`` and the CQC/SRSS roof displacement — and
        ``nodal_displacements`` / ``nodal_displacements_direction`` (the
        CQC nodal displacement field of the first configured direction,
        for the single-direction unified ``rs/node_*`` block).
    """
    from ..spectrum import _build_spectrum, _interp_sa
    from ..utils import g_from_units

    cfg = dict(spec_cfg or {})
    # g follows the model's length unit so the spectral accelerations are
    # in the model's own unit system (e.g. mm/s² for a millimetre model).
    T_spec, Sa_spec, alpha_max, tg, zeta, label = _build_spectrum(cfg, g=g_from_units(md.units))

    periods = list(modal.get("periods", []))
    if not periods:
        raise RuntimeError("no modal periods available for the RS pass")
    eigenvalues = list(modal.get("eigenvalues", []))
    n_modes = max(1, min(int(cfg.get("n_modes") or num_modes), len(periods)))
    directions = list(cfg.get("directions") or ["X", "Y"])
    # Only X and Y are analysed.  ``_rs_export_payload`` writes ``rs_{dir}``
    # keys and :func:`~fea_toolkit.io.unified_writer.collect_rs_arrays` reads
    # only ``rs_x`` / ``rs_y``, so a ``Z`` (or any other) direction would be
    # computed and then silently dropped from the archive.  Reject it up front.
    unsupported = [d for d in directions if str(d).upper() not in ("X", "Y")]
    if unsupported:
        raise ValueError(
            f"unsupported response-spectrum direction(s) {unsupported!r} — "
            "only 'X' and 'Y' are supported"
        )

    def spectrum_func(T):
        """Return Sa(T), interpolated onto the built spectrum axis."""
        return float(_interp_sa(T, T_spec, Sa_spec))

    roof_tag = max(md.nodes.values(), key=lambda n: n.z).node_tag

    def _roof_disp(disp: dict) -> float:
        """Magnitude of the RS nodal displacement at the roof node."""
        d = disp.get(roof_tag)
        return math.hypot(d[0], d[1], d[2]) if d else 0.0

    out: dict[str, Any] = {}
    # The unified ``rs/node_*`` block is single-direction (the Rhino RS
    # deformed-shape overlay reads it without a direction argument), so the
    # CQC nodal displacements of the first configured direction are kept.
    nodal_disp: dict[int, tuple] = {}
    nodal_disp_direction = directions[0] if directions else ""
    # Element-level forces are likewise single-direction in the schema
    # (``rs/elem_*`` has no direction axis), and are opt-in because the
    # extraction adds a pass over every element per mode.  ``extraction``
    # selects between the per-mode loop (default) and the bulk recorder;
    # see ``_runner_rs`` for the measured trade-off.
    want_elem_forces = bool(cfg.get("element_forces"))
    elem_forces: Optional[dict[str, Any]] = None
    elem_forces_error: Optional[str] = None
    elem_combination = str(cfg.get("combination") or "cqc").lower()
    elem_extraction = cfg.get("element_extraction")
    for direction in directions:
        rs = builder.run_response_spectrum_analysis(
            num_modes=n_modes,
            modal_periods=periods,
            spectrum_periods=T_spec,
            spectrum_accels=Sa_spec,
            direction=direction,
            damping_ratio=zeta,
            print_results=False,
        )
        # Nodal displacements are a secondary (reported) quantity — keep the
        # base-shear result even when the eigen vectors are unavailable.
        roof_cqc = roof_srss = 0.0
        if eigenvalues:
            disp_cqc, disp_srss = builder.compute_rs_nodal_displacements(
                num_modes=n_modes,
                modal_periods=periods,
                eigenvalues=eigenvalues,
                spectrum_func=spectrum_func,
                direction=direction,
                damping_ratio=zeta,
                return_srss=True,
            )
            roof_cqc = _roof_disp(disp_cqc)
            roof_srss = _roof_disp(disp_srss)
            if direction == nodal_disp_direction:
                nodal_disp = disp_cqc
        if want_elem_forces and direction == nodal_disp_direction:
            # Per-element combined forces (local system) for the first
            # configured direction — mirrors the single-direction nodal block.
            try:
                elem_forces = dict(
                    builder.extract_element_rs_forces(
                        num_modes=n_modes,
                        modal_periods=periods,
                        spectrum_periods=T_spec,
                        spectrum_accels=Sa_spec,
                        direction=direction,
                        damping_ratio=zeta,
                        combination=elem_combination,
                        extraction=elem_extraction,
                        print_results=False,
                    )
                )
                elem_forces["direction"] = direction
            except Exception as exc:  # reported, never fatal to the review
                elem_forces = None
                elem_forces_error = f"{type(exc).__name__}: {exc}"
        out[direction] = {
            "base_shear_cqc": rs.get("base_shear_cqc", 0.0),
            "base_shear_srss": rs.get("base_shear_srss", 0.0),
            "base_moment_cqc": rs.get("base_moment_cqc", 0.0),
            "base_moment_srss": rs.get("base_moment_srss", 0.0),
            "base_reactions_cqc": dict(rs.get("base_reactions_cqc") or {}),
            "modal_base_shear": list(rs.get("modal_base_shear") or []),
            "modal_base_moment": list(rs.get("modal_base_moment") or []),
            "roof_disp_cqc": roof_cqc,
            "roof_disp_srss": roof_srss,
        }

    return {
        "spectrum": {
            "code": "GB50011",
            "label": label,
            "level": cfg.get("level", "rare"),
            "intensity": cfg.get("intensity", 7),
            "site_class": cfg.get("site_class", "II"),
            "acceleration": cfg.get("acceleration", 0.10),
            "damping": zeta,
            "alpha_max": alpha_max,
            "tg": tg,
            "n_modes": n_modes,
            "directions": directions,
        },
        "directions": out,
        # Modal combination rule used for the RS pass ('cqc' | 'srss'), from
        # ``spectrum.combination``.  Read by the per-mode base-shear table
        # footer so the reader can see which rule the combined values use.
        "combination": elem_combination,
        "nodal_displacements": nodal_disp,
        "nodal_displacements_direction": nodal_disp_direction,
        # Per-element combined forces (single direction, opt-in) and their
        # diagnostics.  ``None`` when ``spectrum.element_forces`` was not set.
        "element_forces": elem_forces,
        "element_forces_direction": nodal_disp_direction if elem_forces else "",
        "element_forces_error": elem_forces_error,
    }


def _rs_export_payload(
    response_spectrum: Optional[dict[str, Any]],
    periods: list[float],
) -> Optional[dict[str, dict]]:
    """Build the ``rs_results`` payload for the unified results writer.

    Converts a review ``response_spectrum`` block into the
    ``{"rs_x": ..., "rs_y": ...}`` shape
    :func:`~fea_toolkit.io.unified_writer.collect_rs_arrays` expects,
    attaching the modal periods each direction shares.  The period list is
    trimmed to the mode count actually used by the RS pass so ``rs/period``
    stays aligned with the per-mode ``rs/v_base_*`` arrays.

    Args:
        response_spectrum: The ``response_spectrum`` block of a review
            result (``None`` when the RS pass did not run or failed).
        periods: Modal periods from the analysis pass.

    Returns:
        ``{"rs_x": {...}, "rs_y": {...}}`` containing only the directions
        actually analysed, or ``None`` when there is nothing to export.
    """
    if not response_spectrum:
        return None
    n_modes = (response_spectrum.get("spectrum") or {}).get("n_modes")
    modal_periods = list(periods)
    if n_modes:
        modal_periods = modal_periods[: int(n_modes)]
    out: dict[str, dict] = {}
    for direction, data in (response_spectrum.get("directions") or {}).items():
        entry = dict(data)
        entry["modal_periods"] = modal_periods
        out[f"rs_{str(direction).lower()}"] = entry
    return out or None


def run_review_analysis(md, config: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Run a modal and linear-static analysis to confirm model behaviour.

    This is the optional analysis phase of the solver-free model review
    (:mod:`fea_toolkit.model.review`).  It builds an OpenSees domain via the
    normal Preprocessor → AnalysisBuilder pipeline and reports dynamic
    (periods, mass participation) and static (reactions, convergence)
    results.  Any failure — e.g. a singular stiffness matrix — is captured
    rather than raised, so a review of a broken model still completes with a
    diagnostic.

    Args:
        md: Parsed :class:`~fea_toolkit.model.sap_data.SAPModelData`.
        config: Optional builder config dict.  Recognised keys:
            ``num_modes``, ``load_verify``, ``wind_check``,
            ``response_spectrum`` (bool — run the GB 50011
            response-spectrum pass), ``spectrum`` (nested spectrum options:
            ``level``, ``intensity``, ``site_class``, ``acceleration``,
            ``damping``, ``n_modes``, ``directions``, ``element_forces`` —
            opt-in per-element forces for the ``rs/elem_*`` block — and
            ``combination`` — ``'cqc'`` (default) or ``'srss'``) and
            ``export_npz`` (output path for a unified geometry + modal +
            static NPZ via
            :func:`~fea_toolkit.io.npz_writer.write_results_npz`).

    Returns:
        Dict with ``ok``, ``periods``, ``mass_participation``, ``static``
        and ``error`` keys.  Each ``mass_participation`` entry carries the
        translational (``mx`` / ``my`` / ``mz``) **and** rotational
        (``rx`` / ``ry`` / ``rz``) mass-participation ratios plus the
        ``frequency`` — the 6-DOF presentation used by the report
        pipeline.  ``mass_source`` reports the summed seismic mass and
        weight (``total_mass`` / ``total_weight`` / ``gravity`` /
        ``n_nodes_with_mass``) from the model's MASS SOURCE, plus a
        ``components`` breakdown of that mass by source (``elements`` =
        material-density self-weight, ``masses`` = joint masses,
        ``loads`` = load-pattern mass).
        When ``config["load_verify"]`` or
        ``config["wind_check"]`` are set, ``load_verification`` (a list of
        per-pattern applied-vs-reaction records) and ``wind`` (structured
        wind-sanity data from :func:`~fea_toolkit.analysis.linear.
        wind_sanity_data`) are added respectively.  With
        ``config["response_spectrum"]`` set, ``response_spectrum`` carries
        the per-direction CQC/SRSS base shear, overturning moment, 6-DoF
        base reactions, per-mode shears and roof displacement, plus the
        resolved ``spectrum`` descriptor (or ``response_spectrum_error``
        holds the captured failure).  When
        ``config["export_npz"]`` is set, ``npz`` holds the written path
        (or ``npz_error`` holds the captured failure).
    """
    # Lazy import avoids a hard dependency cycle (preprocessor → model only)
    # and keeps the module importable in dependency-light environments.
    from .preprocessor import preprocess_model

    result: dict[str, Any] = {
        "ok": False,
        "periods": [],
        "mass_participation": [],
        "static": None,
        "load_verification": None,
        "wind": None,
        "response_spectrum": None,
        "response_spectrum_error": None,
        "npz": None,
        "npz_error": None,
        "mass_source": None,
        "error": None,
    }

    builder_config = dict(config or {})
    builder_config.setdefault("element_type", "elasticBeamColumn")
    builder_config.setdefault("verbose", False)

    try:
        mesh = preprocess_model(md, builder_config)

        # ── Optional richer checks (opt-in) ──────────────────────────
        # Each helper builds its own OpenSees domain, but build_domain()
        # starts with ops.wipe(), so the runs stay isolated.  Failures are
        # captured individually so they never abort the modal/static pass.
        if builder_config.get("load_verify"):
            # ``static_load_verification`` requires at least one load
            # pattern; skip cleanly rather than surfacing a spurious
            # KeyError from its DataFrame path.
            if md.load_patterns:
                try:
                    from ..analysis.linear import static_load_verification

                    df_lv = static_load_verification(md, mesh, builder_config)
                    result["load_verification"] = df_lv.to_dict("records")
                except Exception as exc:  # captured, never raised
                    result["load_verification_error"] = f"{type(exc).__name__}: {exc}"
            else:
                result["load_verification"] = []

        if builder_config.get("wind_check"):
            try:
                from ..analysis.linear import run_linear_cases, wind_sanity_data

                df_linear = run_linear_cases(md, mesh)
                result["wind"] = wind_sanity_data(md, df_linear)
            except Exception as exc:  # captured, never raised
                result["wind_error"] = f"{type(exc).__name__}: {exc}"

        builder = AnalysisBuilder(mesh, builder_config)
        builder.build_domain()
        node_masses = builder.compute_seismic_masses() or {}

        # ── Seismic mass totals (from the model's MASS SOURCE) ───────
        # The nodal masses are in the model's consistent mass unit; the
        # weight is mass × g (g_from_units, never a hardcoded 9.81).
        try:
            from ..utils import g_from_units

            total_mass = float(sum(node_masses.values()))
            gravity = g_from_units(md.units)
            src_name = next(
                (n for n, ms in md.mass_sources.items() if ms.is_default),
                next(iter(md.mass_sources), None),
            )
            src = md.mass_sources.get(src_name) if src_name else None
            _mc = getattr(builder, "mass_components", None) or {}
            result["mass_source"] = {
                "name": src_name,
                "from_elements": bool(src.elements) if src else False,
                "from_masses": bool(src.masses) if src else False,
                "from_loads": bool(src.loads) if src else False,
                "load_patterns": dict(src.load_pattern) if src else {},
                "total_mass": total_mass,
                "total_weight": total_mass * gravity,
                "gravity": gravity,
                "n_nodes_with_mass": sum(1 for m in node_masses.values() if m > 0),
                # Per-source breakdown: element self-weight, joint masses and
                # load-pattern mass (all in the model's consistent mass unit).
                "components": {
                    "elements": float(_mc.get("elements", 0.0)),
                    "masses": float(_mc.get("masses", 0.0)),
                    "loads": float(_mc.get("loads", 0.0)),
                },
            }
        except Exception as exc:  # captured, never raised
            result["mass_source_error"] = f"{type(exc).__name__}: {exc}"

        modal = builder.run_modal_analysis(
            num_modes=int(builder_config.get("num_modes", 12)),
            print_results=False,
        )
        periods = list(modal.get("periods", []))
        frequencies = list(modal.get("frequencies", []))
        props = modal.get("modal_props", {})

        def _ratio(key: str, idx: int) -> float:
            """Mass-participation ratio for mode *idx* (0.0 when absent)."""
            lst = props.get(key) or []
            return float(lst[idx]) if idx < len(lst) else 0.0

        result["periods"] = periods
        # 6-DOF participation — translational (Mx/My/Mz) plus rotational
        # (Rx/Ry/Rz) — matching the report pipeline's modal_table_enhanced().
        result["mass_participation"] = [
            {
                "mode": i + 1,
                "period": periods[i],
                "frequency": frequencies[i] if i < len(frequencies) else 0.0,
                "mx": _ratio("partiMassRatiosMX", i),
                "my": _ratio("partiMassRatiosMY", i),
                "mz": _ratio("partiMassRatiosMZ", i),
                "rx": _ratio("partiMassRatiosRMX", i),
                "ry": _ratio("partiMassRatiosRMY", i),
                "rz": _ratio("partiMassRatiosRMZ", i),
            }
            for i in range(len(periods))
        ]

        # Capture mode shapes immediately — ``ops.nodeEigenvector`` reads the
        # eigen state produced by run_modal_analysis, which the later static
        # pass would otherwise invalidate.
        mode_shapes = None
        if builder_config.get("export_npz"):
            try:
                # Request the number of converged modes actually returned
                # (``periods``), not the requested ``num_modes``, so a model
                # that converged fewer modes still exports a shape per period.
                mode_shapes = builder.extract_mode_shapes(len(periods))
            except Exception:  # keep modal results even if shapes fail
                mode_shapes = None

        # ── Optional response-spectrum pass (opt-in) ─────────────────
        # Must run while the modal eigen state is current — the static pass
        # below replaces the domain's displacement state.  Failures are
        # captured so a broken model still produces a review.
        if builder_config.get("response_spectrum"):
            try:
                result["response_spectrum"] = _run_rs_pass(
                    builder,
                    md,
                    modal,
                    builder_config.get("spectrum"),
                    int(builder_config.get("num_modes", 12)),
                )
            except Exception as exc:  # captured, never raised
                result["response_spectrum_error"] = f"{type(exc).__name__}: {exc}"

        # Apply gravity (DEAD) load patterns only.  When the model defines no
        # DEAD pattern, apply no loads rather than silently substituting every
        # load pattern, so the static pass reflects the model's actual gravity
        # loading; the (possibly empty) selection is reported via
        # ``patterns_applied``.
        gravity = [
            name for name, lp in md.load_patterns.items() if str(lp.pattern_type).lower() == "dead"
        ]
        applied_patterns = gravity
        if applied_patterns:
            builder.create_loads(pattern_scales=dict.fromkeys(applied_patterns, 1.0))

        static = builder.run_static_analysis(extract_reactions=True)
        node_reactions = static.get("reactions", {})
        summed = {"fx": 0.0, "fy": 0.0, "fz": 0.0, "mx": 0.0, "my": 0.0, "mz": 0.0}
        for rxn in node_reactions.values():
            for component in summed:
                summed[component] += rxn.get(component, 0.0)
        result["static"] = {
            "summed_reactions": summed,
            "n_supports": len(node_reactions),
            "n_nodes_with_displacement": len(static.get("nodal_displacements", {})),
            "patterns_applied": applied_patterns,
        }

        # ── Optional unified export (geometry + modal + static + RS) ──
        # Uses the canonical unified writer so the archive follows the
        # ``results_schema`` layout (including the ``rs/*`` block) and can
        # be read by :func:`~fea_toolkit.io.npz_reader.read_results_npz`.
        export_npz = builder_config.get("export_npz")
        if export_npz:
            try:
                from ..io.unified_writer import write_results

                case = applied_patterns[0] if applied_patterns else "GRAVITY"
                static_case: dict[str, Any] = {
                    "nodal_displacements": static.get("nodal_displacements", {}),
                }
                try:
                    elem_forces = builder.static_element_force_arrays()
                except Exception:  # element forces are optional
                    elem_forces = None
                if elem_forces:
                    static_case["element_forces"] = elem_forces
                result["npz"] = write_results(
                    str(export_npz),
                    model=md,
                    mesh_model=mesh,
                    static_results={case: static_case},
                    modal_result=modal,
                    mode_shapes=mode_shapes,
                    rs_results=_rs_export_payload(result.get("response_spectrum"), periods),
                    # Single-direction CQC displacement field for the
                    # ``rs/node_*`` block (Rhino RS deformed-shape overlay).
                    rs_nodal_displacements=(result.get("response_spectrum") or {}).get(
                        "nodal_displacements"
                    )
                    or None,
                    # Per-element local forces for the ``rs/elem_*`` block
                    # (opt-in via ``spectrum.element_forces``) — enables the
                    # per-element RS force diagrams from the archive.
                    rs_element_forces=(result.get("response_spectrum") or {}).get("element_forces")
                    or None,
                    fmt="npz",
                )
            except Exception as exc:  # captured, never raised
                result["npz_error"] = f"{type(exc).__name__}: {exc}"

        result["ok"] = True
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        with contextlib.suppress(Exception):
            ops.wipe()
    return result
