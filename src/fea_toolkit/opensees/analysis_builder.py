"""Analysis builder — create OpenSees domain from a prepared ``MeshModel``.

The :class:`AnalysisBuilder` takes a :class:`~fea_toolkit.model.mesh_model.MeshModel`
(fully prepared topology from the :class:`~fea_toolkit.opensees.preprocessor.Preprocessor`)
and creates the OpenSees domain objects.  It handles all analysis execution
and result extraction — no topology mutations occur here.
"""

import logging
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

__all__ = [
    "AnalysisBuilder",
    "_normalise_frame_response",
    "_record_step",
    "run_modal",
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

    def __init__(self, mesh_model: MeshModel, config: Optional[dict[str, Any]] = None):
        self.mesh_model = mesh_model
        self.units = mesh_model.units
        self.config = config or {}
        # Track whether the user explicitly configured gravity substeps so
        # _set_defaults() can auto-select a LayeredShell-safe count when
        # config omits it (see LAYERED_SHELL_GRAVITY_SUBSTEPS and
        # run_static_analysis).  An explicit value always wins.
        self._user_set_gravity_substeps = "gravity_num_substeps" in self.config
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

    # ═══════════════════════════════════════════════════════════════
    # Domain construction
    # ═══════════════════════════════════════════════════════════════

    def build_domain(
        self,
        config_overrides: Optional[dict[str, Any]] = None,
    ) -> None:
        """Create the full OpenSees domain from the MeshModel.

        Creates nodes, restraints, materials, sections, frame elements,
        shell elements, lumped hinges, and rigid links.

        Args:
            config_overrides: Optional dict of config keys to temporarily
                override ``self.config`` for this build cycle.  Useful for
                pushover rebuilds that need fiber sections or different
                element types.  The overrides are reset after the build.
        """
        # Apply temporary config overrides
        _saved_overrides: dict[str, Any] = {}
        if config_overrides:
            for k, v in config_overrides.items():
                _saved_overrides[k] = self.config.get(k)
                self.config[k] = v

        try:
            ops.wipe()
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
            self._create_elements()
            self._create_limit_state_columns()
            self._apply_rigid_diaphragms()
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
