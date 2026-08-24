"""Analysis builder — create OpenSees domain from a prepared ``MeshModel``.

The :class:`AnalysisBuilder` takes a :class:`~fea_toolkit.model.mesh_model.MeshModel`
(fully prepared topology from the :class:`~fea_toolkit.opensees.preprocessor.Preprocessor`)
and creates the OpenSees domain objects.  It handles all analysis execution
and result extraction — no topology mutations occur here.
"""

import copy
import logging
import math
from typing import TYPE_CHECKING, Any, ClassVar, Optional

import numpy as np
import openseespy.opensees as ops

if TYPE_CHECKING:
    # pandas is not a required dependency — only imported at runtime inside
    # check_load_equilibrium().  The TYPE_CHECKING guard lets Ruff resolve
    # the "pd.DataFrame" return annotation statically without adding pandas
    # to the core dependencies.
    pass


from ..model.geometry import get_local_axes, polygon_area_3d
from ..model.mesh_model import MeshModel
from ..model.sap_data import (
    FrameElement,
    Node,
    Restraint,
)
from ..model.tree_utils import collect_descendants

logger = logging.getLogger(__name__)


from ._elements import ElementMixin
from ._materials import MaterialMixin
from ._runners import RunnerMixin, _normalise_frame_response, _record_step
from ._sections import SectionMixin

__all__ = [
    "AnalysisBuilder",
    "_normalise_frame_response",
    "_record_step",
    "run_modal",
]


class AnalysisBuilder(RunnerMixin, ElementMixin, SectionMixin, MaterialMixin):
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
        self._mass_g: float = 9.81

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

    def _reapply_edge_constraints(self, scale: float = 1.0) -> None:
        """Re-apply saved edge constraints after a domain rebuild.

        Iterates ``self._saved_edge_constraints`` (populated when
        :meth:`apply_edge_constraints` was first called) and re-applies
        each saved batch.  Used after ``build_domain()`` wipes the
        OpenSees domain (e.g. during pushover fiber-section rebuild).
        """
        if not self._saved_edge_constraints:
            return
        for args in self._saved_edge_constraints:
            coarse_edges, fine_nodes, coarse_elems, tolerance, k, verbose = args
            if scale != 1.0 and k is not None:
                k = k * scale
            self.apply_edge_constraints(
                coarse_edges=coarse_edges,
                fine_nodes=fine_nodes,
                coarse_elements=coarse_elems,
                tolerance=tolerance,
                penalty_stiffness=k,
                verbose=verbose or self.config.get("verbose", False),
            )

    # ═══════════════════════════════════════════════════════════════
    # Edge constraint helpers
    # ═══════════════════════════════════════════════════════════════

    def _node_tag_from_id(self, node_id: str) -> Optional[int]:
        """Return numeric tag for a node, or None if not found."""
        node = self.mesh_model.nodes.get(node_id)
        if node:
            return node.node_tag
        return None

    def _get_shell_area_ids(self) -> set:
        """Return the set of area element IDs that became actual shell elements.

        When ``create_shells`` is ``False`` (no shells built), all areas
        are still returned to support diagnostic detection of unconnected
        edges before deciding whether to create shells.
        """
        return {
            aid
            for aid in self.mesh_model.area_elements
            if not getattr(self.mesh_model.area_elements[aid], "inactive", False)
        }

    # ═══════════════════════════════════════════════════════════════
    # Local axis utilities (used by visualisation)
    # ═══════════════════════════════════════════════════════════════

    def _get_local_axes(self, elem: FrameElement):
        """Return local (vx, vy, vz) unit vectors for a frame element.

        Parameters
        ----------
        elem : FrameElement
            Frame element from the MeshModel.

        Returns
        -------
        tuple of np.ndarray
            Local x, y, z unit vectors (3-element each).
        """
        node_i = self.mesh_model.nodes[elem.node_i]
        node_j = self.mesh_model.nodes[elem.node_j]
        coords_i = ops.nodeCoord(node_i.node_tag)
        coords_j = ops.nodeCoord(node_j.node_tag)
        vec_x = np.array(coords_j) - np.array(coords_i)
        return get_local_axes(vec_x, getattr(elem, "angle", 0.0))

    def _global_to_local(self, elem: FrameElement, vec: np.ndarray) -> np.ndarray:
        """Transform a vector from global to local coordinates.

        Parameters
        ----------
        elem : FrameElement
            Frame element defining the local coordinate system.
        vec : np.ndarray
            3-element vector in global coordinates.

        Returns
        -------
        np.ndarray
            3-element vector in local coordinates.
        """
        vx, vy, vz = self._get_local_axes(elem)
        T = np.vstack([vx, vy, vz])
        return T @ vec

    # ═══════════════════════════════════════════════════════════════
    # Edge constraint dispatcher
    # ═══════════════════════════════════════════════════════════════

    def apply_edge_constraints(
        self,
        coarse_edges: Optional[list[tuple[int, int]]] = None,
        fine_nodes: Optional[list[int]] = None,
        coarse_elements: Optional[list[int]] = None,
        tolerance: float = 1e-4,
        penalty_stiffness: Optional[float] = None,
        verbose: bool = True,
    ) -> int:
        """Apply edge constraints using the configured ``constraint_method``.

        Delegates to :meth:`apply_spring_edge_constraints` or
        :meth:`_apply_penalty_edge_constraints` based on
        ``self.config['constraint_method']``.

        * ``"spring"`` (default) — creates ``twoNodeLink`` spring elements.
        * ``"penalty"`` — uses ``equationConstraint`` MPCs + Penalty handler.

        See :meth:`apply_spring_edge_constraints` and
        :meth:`_apply_penalty_edge_constraints` for full parameter docs.
        """
        method = self.config.get("constraint_method", "spring")
        if method == "spring":
            return self.apply_spring_edge_constraints(
                coarse_edges=coarse_edges,
                fine_nodes=fine_nodes,
                coarse_elements=coarse_elements,
                tolerance=tolerance,
                penalty_stiffness=penalty_stiffness,
                verbose=verbose,
            )
        if method == "penalty":
            return self._apply_penalty_edge_constraints(
                coarse_edges=coarse_edges,
                fine_nodes=fine_nodes,
                coarse_elements=coarse_elements,
                tolerance=tolerance,
                verbose=verbose,
            )
        raise ValueError(f"Unknown constraint_method '{method}'. Choose 'spring' or 'penalty'.")

    # ═══════════════════════════════════════════════════════════════
    # Spring-based edge constraints (twoNodeLink)
    # ═══════════════════════════════════════════════════════════════

    def apply_spring_edge_constraints(
        self,
        coarse_edges: Optional[list[tuple[int, int]]] = None,
        fine_nodes: Optional[list[int]] = None,
        coarse_elements: Optional[list[int]] = None,
        tolerance: float = 1e-4,
        penalty_stiffness: Optional[float] = None,
        verbose: bool = True,
    ) -> int:
        """Tie slave nodes to master edges using stiff zero-length spring
        elements (``twoNodeLink``) instead of ``equationConstraint`` MPCs.

        Spring elements create a **flexible** connection whose stiffness
        is controlled by *penalty_stiffness*.  The auto-computed default
        targets ~100× the shell in-plane stiffness (E·t).

        Each slave node is tied to both ends of its nearest master edge
        via two spring elements with stiffness weighted by interpolation
        factors N₁, N₂ (proximity along the edge).

        Args:
            coarse_edges: Explicit master edge node pairs ``(n1, n2)``.
            fine_nodes: Slave node candidates.  ``None`` = auto-detect.
            coarse_elements: Auto-extract master edges from element tags.
            tolerance: Max perpendicular distance for slave detection.
            penalty_stiffness: Spring stiffness per DOF.  ``None`` = auto.
            verbose: Print progress.

        Returns:
            Number of ``twoNodeLink`` elements created (2 per slave-edge pair).
        """
        # ── Resolve master edges ──────────────────────────────────
        edge_set: set = set()
        if coarse_elements is not None:
            for etag in coarse_elements:
                try:
                    nodes = ops.eleNodes(int(etag))
                except Exception:
                    continue
                for j in range(len(nodes)):
                    n1, n2 = nodes[j], nodes[(j + 1) % len(nodes)]
                    edge_set.add((min(n1, n2), max(n1, n2)))
        if coarse_edges is not None:
            for n1, n2 in coarse_edges:
                t1 = self._node_tag_from_id(str(n1)) if not isinstance(n1, int) else n1
                t2 = self._node_tag_from_id(str(n2)) if not isinstance(n2, int) else n2
                if t1 is None:
                    t1 = int(n1)
                if t2 is None:
                    t2 = int(n2)
                edge_set.add((min(t1, t2), max(t1, t2)))
        if not edge_set:
            if verbose:
                print("No master edges — nothing to constrain.")
            return 0

        # ── Resolve slave nodes ───────────────────────────────────
        if fine_nodes is not None:
            slave_candidates = []
            for n in fine_nodes:
                tag = self._node_tag_from_id(str(n)) if not isinstance(n, int) else n
                slave_candidates.append(tag if tag is not None else int(n))
        else:
            shell_ids = self._get_shell_area_ids()
            all_nodes: set = set()
            for eid in shell_ids:
                for n_id in self.mesh_model.area_elements[eid].node_ids:
                    tag = self._node_tag_from_id(n_id)
                    if tag is not None:
                        all_nodes.add(tag)
            slave_candidates = sorted(all_nodes)

        # ── Auto stiffness ────────────────────────────────────────
        if penalty_stiffness is None:
            avg_Et = 0.0
            _count = 0
            for _aid in self.mesh_model.area_elements:
                if getattr(self.mesh_model.area_elements[_aid], "inactive", False):
                    continue
                _sec_name = self.mesh_model.area_assignments.get(_aid)
                if _sec_name:
                    _sec = self.mesh_model.sections.get(_sec_name)
                    if _sec and hasattr(_sec, "thickness") and _sec.thickness > 0:
                        _mat = self.mesh_model.materials.get(_sec.material)
                        if _mat and _mat.E_mod > 0:
                            avg_Et += _mat.E_mod * _sec.thickness
                            _count += 1
            if _count > 0:
                avg_Et /= _count
                penalty_stiffness = 100.0 * avg_Et
                if verbose:
                    print(
                        f"  Spring stiffness auto: E·t_avg = {avg_Et:.3e}, "
                        f"k = {penalty_stiffness:.3e}  "
                        f"(scanned {_count} shell element(s))"
                    )
            else:
                _E = 2e8  # KN/m² (200 GPa steel)
                _t = 0.15  # m (typical slab)
                penalty_stiffness = 100.0 * _E * _t
                if verbose:
                    print(f"  Spring stiffness auto: using fallback k = {penalty_stiffness:.3e}")

        # ── Find tags ─────────────────────────────────────────────
        max_elem = max(
            (
                e.elem_tag
                for e in self.mesh_model.frame_elements.values()
                if hasattr(e, "elem_tag") and e.elem_tag is not None
            ),
            default=0,
        )
        try:
            active = ops.getEleTags()
            if active:
                max_elem = max(max_elem, *active)
        except Exception:
            pass
        ele_tag = max_elem + 100_000
        mat_tag = ele_tag + 50_000

        # ── Apply springs ─────────────────────────────────────────
        count = 0
        for m1_id, m2_id in edge_set:
            try:
                c1 = np.array(ops.nodeCoord(m1_id))
                c2 = np.array(ops.nodeCoord(m2_id))
            except Exception:
                continue
            edge_vec = c2 - c1
            edge_len = float(np.linalg.norm(edge_vec))
            if edge_len < 1e-12:
                continue

            for s_id in slave_candidates:
                if s_id in (m1_id, m2_id):
                    continue
                try:
                    cs = np.array(ops.nodeCoord(s_id))
                except Exception:
                    continue
                cross = float(np.linalg.norm(np.cross(cs - c1, cs - c2)))
                if cross / max(edge_len, 1e-12) > tolerance:
                    continue
                proj = float(np.dot(cs - c1, edge_vec)) / edge_len
                if proj <= 0.0 or proj >= edge_len:
                    continue
                N2 = proj / edge_len
                N1 = 1.0 - N2

                for master, weight in ((m1_id, N1), (m2_id, N2)):
                    if weight < 1e-12:
                        continue
                    k = penalty_stiffness * weight
                    ops.uniaxialMaterial("Elastic", mat_tag, k)
                    ops.element(
                        "twoNodeLink",
                        ele_tag,
                        int(s_id),
                        int(master),
                        "-mat",
                        mat_tag,
                        mat_tag,
                        mat_tag,
                        mat_tag,
                        mat_tag,
                        mat_tag,
                        "-dir",
                        1,
                        2,
                        3,
                        4,
                        5,
                        6,
                    )
                    if verbose:
                        print(
                            f"  Spring constraint: node {s_id} → "
                            f"master {master}  (k={k:.2e}, w={weight:.3f})"
                        )
                    ele_tag += 1
                    mat_tag += 1
                    count += 1

        if count:
            self._edge_constraint_method = "spring"
            if coarse_edges is not None or coarse_elements is not None:
                # Save arguments so they can be re-applied after a
                # domain rebuild (pushover switches to fiber sections).
                # Single-entry list: edge constraints are applied as one
                # batch per analysis cycle.
                self._saved_edge_constraints = [
                    (
                        coarse_edges,
                        fine_nodes,
                        coarse_elements,
                        tolerance,
                        penalty_stiffness,
                        verbose,
                    )
                ]
            if verbose:
                print(f"Applied {count} spring element(s).")

        return count

    # ═══════════════════════════════════════════════════════════════
    # Penalty-based edge constraints (equationConstraint MPCs)
    # ═══════════════════════════════════════════════════════════════

    def _apply_penalty_edge_constraints(
        self,
        coarse_edges: Optional[list[tuple[int, int]]] = None,
        fine_nodes: Optional[list[int]] = None,
        coarse_elements: Optional[list[int]] = None,
        tolerance: float = 1e-4,
        verbose: bool = True,
    ) -> int:
        """Apply edge constraints using ``equationConstraint`` MPCs with
        the Penalty constraint handler.

        Unaligned slave nodes that lie on coarse-mesh edges are tied via
        ``ops.equationConstraint()`` with interpolation weights based on
        their position along the edge.  All six DOFs are constrained.

        The Penalty handler is required — ``Transformation`` cannot
        process ``equationConstraint`` MPCs.

        Args:
            coarse_edges: Explicit master edge node pairs.
            fine_nodes: Slave node IDs.  ``None`` = all shell nodes.
            coarse_elements: Auto-extract master edges from element tags.
            tolerance: Max perpendicular distance to consider a slave
                node "on the edge".
            verbose: Print progress messages.

        Returns:
            Number of multi-point constraints applied.
        """
        # ── Resolve master edges ────────────────────────────────────
        edge_set: set = set()
        if coarse_elements is not None:
            for etag in coarse_elements:
                try:
                    nodes = ops.eleNodes(int(etag))
                except Exception:
                    continue
                for j in range(len(nodes)):
                    n1 = nodes[j]
                    n2 = nodes[(j + 1) % len(nodes)]
                    edge_set.add((min(n1, n2), max(n1, n2)))
        if coarse_edges is not None:
            for n1, n2 in coarse_edges:
                t1 = self._node_tag_from_id(str(n1)) if not isinstance(n1, int) else n1
                t2 = self._node_tag_from_id(str(n2)) if not isinstance(n2, int) else n2
                if t1 is None:
                    t1 = int(n1)
                if t2 is None:
                    t2 = int(n2)
                edge_set.add((min(t1, t2), max(t1, t2)))
        if not edge_set:
            print("No master edges provided — nothing to constrain.")
            return 0

        # ── Resolve slave nodes ─────────────────────────────────────
        if fine_nodes is not None:
            slave_candidates = []
            for n in fine_nodes:
                tag = self._node_tag_from_id(str(n)) if not isinstance(n, int) else n
                if tag is None:
                    tag = int(n)
                slave_candidates.append(tag)
        else:
            shell_area_ids = self._get_shell_area_ids()
            all_nodes: set = set()
            for eid in shell_area_ids:
                elem = self.mesh_model.area_elements[eid]
                for n_id in elem.node_ids:
                    tag = self._node_tag_from_id(n_id)
                    if tag is not None:
                        all_nodes.add(tag)
            slave_candidates = sorted(all_nodes)

        # ── Apply constraints ───────────────────────────────────────
        count = 0
        for m1_id, m2_id in edge_set:
            try:
                c1 = np.array(ops.nodeCoord(m1_id))
                c2 = np.array(ops.nodeCoord(m2_id))
            except Exception:
                continue
            edge_vec = c2 - c1
            edge_len = np.linalg.norm(edge_vec)
            if edge_len < 1e-12:
                continue

            for s_id in slave_candidates:
                if s_id in (m1_id, m2_id):
                    continue
                try:
                    cs = np.array(ops.nodeCoord(s_id))
                except Exception:
                    continue

                cross_prod = np.cross(cs - c1, cs - c2)
                distance = np.linalg.norm(cross_prod) / edge_len
                if distance > tolerance:
                    continue

                proj = np.dot(cs - c1, edge_vec) / edge_len
                if 0.0 < proj < edge_len:
                    N2 = proj / edge_len
                    N1 = 1.0 - N2
                    for dof in range(1, 7):
                        ops.equationConstraint(
                            int(s_id),
                            dof,
                            1.0,
                            int(m1_id),
                            dof,
                            -N1,
                            int(m2_id),
                            dof,
                            -N2,
                        )
                    count += 1
                    if verbose:
                        print(
                            f"  Edge constraint: node {s_id} → "
                            f"edge ({m1_id}–{m2_id})  "
                            f"(N1={N1:.3f}, N2={N2:.3f})"
                        )

        if count:
            self._edge_constraint_method = "penalty"
            if coarse_edges is not None or coarse_elements is not None:
                self._saved_edge_constraints = [
                    (
                        coarse_edges,
                        fine_nodes,
                        coarse_elements,
                        tolerance,
                        None,
                        verbose,
                    )
                ]
            if verbose:
                print(f"Applied {count} edge constraint(s). Solver will use Penalty handler.")

        return count

    # ═══════════════════════════════════════════════════════════════
    # Diagnostic: detect unconnected edges
    # ═══════════════════════════════════════════════════════════════

    def detect_unconnected_edges(
        self,
        tolerance: float = 1e-4,
        include_frame_connections: bool = False,
    ) -> list[dict[str, Any]]:
        """Scan shell elements and report fine-mesh nodes that sit on
        coarse-mesh edges without being directly connected.

        This is a **diagnostic** tool — it identifies locations where
        SAP2000 would apply Auto Edge Constraints.  Use its output to
        build the mapping for :meth:`apply_edge_constraints`.

        Args:
            tolerance: Maximum perpendicular distance from a node to a
                line segment for it to be considered "on the edge".
            include_frame_connections: Also check whether frame element
                nodes align with shell edges.

        Returns:
            List of dicts with keys ``slave_node``, ``master_node_i``,
            ``master_node_j``, ``coords``, ``N1``, ``N2``, ``edge_length``,
            ``distance``.
        """
        reports: list[dict[str, Any]] = []

        shell_area_ids = self._get_shell_area_ids()
        if not shell_area_ids:
            return reports

        edge_set: set = set()
        for eid in shell_area_ids:
            elem = self.mesh_model.area_elements[eid]
            nodes = elem.node_ids
            for j in range(len(nodes)):
                t1 = self._node_tag_from_id(nodes[j])
                t2 = self._node_tag_from_id(nodes[(j + 1) % len(nodes)])
                if t1 is None or t2 is None:
                    continue
                edge_set.add((min(t1, t2), max(t1, t2)))
        all_edges = list(edge_set)

        if not all_edges:
            return reports

        shell_node_set: set = set()
        for eid in shell_area_ids:
            elem = self.mesh_model.area_elements[eid]
            for n_id in elem.node_ids:
                tag = self._node_tag_from_id(n_id)
                if tag is not None:
                    shell_node_set.add(tag)

        if include_frame_connections:
            for eid, elem in self.mesh_model.frame_elements.items():
                for n_id in (elem.node_i, elem.node_j):
                    tag = self._node_tag_from_id(n_id)
                    if tag is not None:
                        shell_node_set.add(tag)

        all_slave_nodes = sorted(shell_node_set)

        for m1_tag, m2_tag in all_edges:
            try:
                c1 = np.array(ops.nodeCoord(m1_tag))
                c2 = np.array(ops.nodeCoord(m2_tag))
            except Exception:
                continue

            edge_vec = c2 - c1
            edge_len = np.linalg.norm(edge_vec)
            if edge_len < 1e-12:
                continue

            for s_tag in all_slave_nodes:
                if s_tag in (m1_tag, m2_tag):
                    continue
                try:
                    cs = np.array(ops.nodeCoord(s_tag))
                except Exception:
                    continue

                cross_prod = np.cross(cs - c1, cs - c2)
                distance = np.linalg.norm(cross_prod) / edge_len

                if distance > tolerance:
                    continue

                proj = np.dot(cs - c1, edge_vec) / edge_len
                if 0.0 < proj < edge_len:
                    N2 = proj / edge_len
                    N1 = 1.0 - N2
                    reports.append(
                        {
                            "slave_node": s_tag,
                            "master_node_i": m1_tag,
                            "master_node_j": m2_tag,
                            "coords": tuple(cs),
                            "master_coords_i": tuple(c1),
                            "master_coords_j": tuple(c2),
                            "N1": round(N1, 6),
                            "N2": round(N2, 6),
                            "edge_length": round(edge_len, 6),
                            "distance": round(distance, 8),
                        }
                    )

        return reports

    def create_loads(
        self,
        pattern_scales: Optional[dict[str, float]] = None,
    ) -> None:
        """Create load patterns on the OpenSees domain.

        Args:
            pattern_scales: Dict mapping pattern name → scale factor.
                If provided, only these patterns are created.  If None,
                all patterns from the mesh model are applied.
        """
        self._create_loads(pattern_scales=pattern_scales)
        self._apply_rigid_diaphragms()

    # ── Node creation ────────────────────────────────────────────

    def _create_nodes(self) -> None:
        """Create OpenSees nodes from MeshModel nodes."""
        self._created_node_tags = set()
        for node in self.mesh_model.nodes.values():
            tag = node.node_tag
            ops.node(tag, node.x, node.y, node.z)
            self._created_node_tags.add(tag)

    def _apply_restraints(self) -> None:
        """Apply boundary conditions from MeshModel restraints.

        Also propagates restraints from area corner nodes to intermediate
        mesh-created nodes along each edge of subdivided shell areas.
        Without this, ``ShellMITC4`` elements at restrained edges would
        have unrestrained intermediate nodes, creating a rotational
        mechanism (singular stiffness matrix).
        """
        import numpy as np

        for node_id, restraint in self.mesh_model.restraints.items():
            nd = self.mesh_model.nodes.get(node_id)
            if nd is None:
                continue
            ops.fix(nd.node_tag, *restraint.dofs[:6])

        # ── Propagate edge restraints to mesh nodes ──────────────
        # For each subdivided area parent, check if both corner nodes
        # along an edge have restraints.  If so, intermediate mesh
        # nodes inherit the AND (more-restrictive) combination.
        for aid, elem in self.mesh_model.area_elements.items():
            if not getattr(elem, "inactive", False):
                continue  # only look at subdivided parents
            if len(elem.node_ids) != 4:
                continue
            corners = list(elem.node_ids)  # 4 corner SAP node IDs
            edges = [(0, 1), (1, 2), (3, 2), (0, 3)]
            for ci, cj in edges:
                nid_i = corners[ci]
                nid_j = corners[cj]
                ri = self.mesh_model.restraints.get(nid_i)
                rj = self.mesh_model.restraints.get(nid_j)
                if ri is None or rj is None:
                    continue
                # AND of restraint DOFs (more restricted wins)
                combined = [min(ri.dofs[d], rj.dofs[d]) for d in range(6)]
                if sum(combined) == 0:
                    continue
                nd_i = self.mesh_model.nodes.get(nid_i)
                nd_j = self.mesh_model.nodes.get(nid_j)
                if nd_i is None or nd_j is None:
                    continue
                p_i = np.array([nd_i.x, nd_i.y, nd_i.z])
                p_j = np.array([nd_j.x, nd_j.y, nd_j.z])
                edge_vec = p_j - p_i
                edge_len_sq = np.dot(edge_vec, edge_vec)
                if edge_len_sq < 1e-12:
                    continue

                mesh_prefix = f"{aid}_mesh_"
                for nd in list(self.mesh_model.nodes.values()):
                    if mesh_prefix not in nd.node_id:
                        continue
                    if nd.node_id in self.mesh_model.restraints:
                        # Already fixed by the explicit-restraint loop above.
                        # The Preprocessor propagates edge restraints into
                        # ``mesh_model.restraints`` (geometry's
                        # ``_propagate_edge_restraints``), so re-applying the
                        # AND combination here would double-constrain the DOF
                        # and make OpenSees reject the duplicate SP_Constraint.
                        continue
                    p = np.array([nd.x, nd.y, nd.z])
                    t = np.dot(p - p_i, edge_vec) / edge_len_sq
                    if t < 1e-6 or t > 1 - 1e-6:
                        continue
                    proj = p_i + t * edge_vec
                    if np.linalg.norm(p - proj) > 0.01:
                        continue
                    ops.fix(nd.node_tag, *combined)

    # ── Elwood & Moehle column limit states (Phase 3) ──────────────

    def _limit_state_requested(self) -> bool:
        """True when any ``limit_state_columns`` are configured."""
        return bool((self.config or {}).get("limit_state_columns"))

    def _ensure_limit_state_units(self) -> None:
        """Rescale the model to kip-in-ksi when limit-state columns need it.

        The Elwood ``limitCurve`` equations are hard-anchored to the
        kip-in-ksi convention (``f'c`` in psi, forces in kip, lengths in
        in).  When ``limit_state_columns`` is non-empty and the mesh is in
        any other system, :func:`convert_mesh_units` deep-copies the mesh
        to ``KIP_IN_UNITS`` (the caller's model is untouched) unless
        ``limit_state_auto_convert_units`` is False.  Wall nD / layered
        shell materials cannot be rescaled by ``convert_mesh_units`` — for
        such models the user must pre-convert and re-parse, or raise.
        """
        if not self.config.get("limit_state_auto_convert_units", True):
            units = self.mesh_model.units
            if units.get("L") == "in" and units.get("F") == "kip":
                return
            raise ValueError(
                "limit_state_columns require a kip-in-ksi model, but the "
                f"mesh is in {units} and 'limit_state_auto_convert_units' "
                "is False. Convert the model to L='in', F='kip' before "
                "running the analysis, or leave "
                "'limit_state_auto_convert_units' = True."
            )
        units = self.mesh_model.units
        if units.get("L") == "in" and units.get("F") == "kip":
            return
        if self.mesh_model.nd_materials or self.mesh_model.layered_shell_sections:
            raise ValueError(
                "limit-state columns require a kip-in-ksi domain, but this model is in "
                f"{units} and contains nd_materials/layered_shell_sections that "
                "convert_mesh_units cannot rescale. Re-parse/pre-process the model "
                "in kip-in-ksi, or set config 'limit_state_auto_convert_units' = False."
            )
        from ..model.units import KIP_IN_UNITS, convert_mesh_units

        converted = convert_mesh_units(self.mesh_model, KIP_IN_UNITS)
        self.mesh_model = converted
        self.units = converted.units
        logger.info("limit-state columns: mesh rescaled to kip-in-ksi units")

    def _restore_limit_state_canonical_state(self) -> None:
        """Restore the canonical column topology before a build cycle.

        Removes the ``*_limit_top`` / ``*_limit_anchor`` instrumentation
        nodes, restores re-pointed beam/column endpoints, restraints and
        joint loads, and clears the per-build emission plan.  Called at
        the start of :meth:`build_domain` (before node creation).
        """
        if not hasattr(self, "_limit_state_canonical"):
            self._limit_state_plan = None
            return
        snap = self._limit_state_canonical
        for nid in [
            k
            for k in list(self.mesh_model.nodes.keys())
            if k.endswith(("_limit_top", "_limit_anchor"))
        ]:
            del self.mesh_model.nodes[nid]
        for eid, elem in self.mesh_model.frame_elements.items():
            if eid in snap["frame_elements"]:
                elem.node_i, elem.node_j = snap["frame_elements"][eid]
        self.mesh_model.frame_assignments = dict(snap["frame_assignments"])
        self.mesh_model.restraints = dict(snap["restraints"])
        self.mesh_model.joint_loads = list(snap["joint_loads"])
        self._limit_state_plan = None

    @staticmethod
    def _is_gravity_pattern(pattern: Any) -> bool:
        """True for DEAD / SUPERDEAD / GRAVITY-type load patterns."""
        name = str(getattr(pattern, "name", "") or "").upper()
        ptype = str(getattr(pattern, "pattern_type", "") or "").upper()
        swf = float(getattr(pattern, "self_weight_factor", 0.0) or 0.0)
        return (
            ptype.startswith(("DEAD", "SUPER", "GRAV"))
            or name.startswith(("DEAD", "GRAV"))
            or swf > 0.0
        )

    def _top_node_id(self, elem: FrameElement) -> str:
        """ID of the higher-Z end of a frame element (gravity convention)."""
        ni = self.mesh_model.nodes.get(elem.node_i)
        nj = self.mesh_model.nodes.get(elem.node_j)
        if ni is None or nj is None:
            return elem.node_j
        return elem.node_j if nj.z >= ni.z else elem.node_i

    def _member_self_weight(self, eid: str) -> float:
        """Total self-weight of a frame element (model force, downward +)."""
        elem = self.mesh_model.frame_elements.get(eid)
        if elem is None or getattr(elem, "inactive", False):
            return 0.0
        sec_name = self.mesh_model.frame_assignments.get(eid)
        sec = self.mesh_model.sections.get(sec_name) if sec_name else None
        mat = self.mesh_model.materials.get(sec.material) if sec is not None else None
        if sec is None or mat is None:
            return 0.0
        a = float(getattr(sec, "A", 0.0) or 0.0)
        if a <= 0.0 or mat.unit_weight == 0.0:
            return 0.0
        ni = self.mesh_model.nodes.get(elem.node_i)
        nj = self.mesh_model.nodes.get(elem.node_j)
        if ni is None or nj is None:
            return 0.0
        L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
        return a * mat.unit_weight * L

    def _member_vertical_gravity_load(self, eid: str, grav_patterns: set) -> float:
        """Total vertical gravity load on a member (model force, downward +).

        Self-weight (with the pattern's ``self_weight_factor`` and explicit
        ``frame_gravity_loads`` multipliers) plus the vertical component of
        distributed loads in gravity patterns.  Positive = downward
        (compression at a supporting joint).
        """
        elem = self.mesh_model.frame_elements.get(eid)
        if elem is None or getattr(elem, "inactive", False):
            return 0.0
        sec_name = self.mesh_model.frame_assignments.get(eid)
        sec = self.mesh_model.sections.get(sec_name) if sec_name else None
        mat = self.mesh_model.materials.get(sec.material) if sec is not None else None
        if sec is None or mat is None:
            return 0.0
        a = float(getattr(sec, "A", 0.0) or 0.0)
        ni = self.mesh_model.nodes.get(elem.node_i)
        nj = self.mesh_model.nodes.get(elem.node_j)
        if ni is None or nj is None:
            return 0.0
        L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)

        total = 0.0
        # ── Self-weight (per gravity pattern, with its SWF) ──
        for pname in grav_patterns:
            pat = self.mesh_model.load_patterns.get(pname)
            if pat is None:
                continue
            swf = float(getattr(pat, "self_weight_factor", 0.0) or 0.0)
            if mat.unit_weight != 0.0 and a > 0.0 and swf != 0.0:
                total += a * mat.unit_weight * L * swf
            # Explicit frame gravity-load multipliers (vertical component)
            for gl in getattr(self.mesh_model, "frame_gravity_loads", []):
                if gl.pattern == pname and gl.frame_id == eid:
                    mz = float(getattr(gl, "multiplier_z", 0.0) or 0.0)
                    if mat.unit_weight != 0.0 and a > 0.0:
                        total += a * mat.unit_weight * L * mz

        # ── Distributed loads projected onto the global vertical axis ──
        for dl in self.mesh_model.frame_dist_loads:
            if dl.pattern not in grav_patterns or dl.frame_id != eid:
                continue
            gdir = self._load_global_direction(dl, elem)
            span = (
                float(getattr(dl, "rdist_b", 1.0) or 1.0)
                - float(getattr(dl, "rdist_a", 0.0) or 0.0)
            ) * L
            wavg = 0.5 * (
                float(getattr(dl, "val_a", 0.0) or 0.0) + float(getattr(dl, "val_b", 0.0) or 0.0)
            )
            # Positive vertical component (z_down = -1) contributes compression.
            total += -wavg * gdir[2] * span
        return total

    def _load_global_direction(self, dl: Any, elem: FrameElement) -> tuple:
        """Global direction vector of a distributed-load direction string."""
        direction = str(getattr(dl, "direction", "Gravity"))
        if direction == "Gravity":
            return (0.0, 0.0, -1.0)
        if direction == "X":
            return (1.0, 0.0, 0.0)
        if direction == "Y":
            return (0.0, 1.0, 0.0)
        if direction == "Z":
            return (0.0, 0.0, 1.0)
        # Local directions: compute from mesh geometry (ops domain not yet built)
        ni = self.mesh_model.nodes.get(elem.node_i)
        nj = self.mesh_model.nodes.get(elem.node_j)
        if ni is not None and nj is not None:
            vec_x = np.array([nj.x - ni.x, nj.y - ni.y, nj.z - ni.z])
            if np.linalg.norm(vec_x) > 1e-12:
                try:
                    vx, vy, vz = get_local_axes(vec_x, float(getattr(elem, "angle", 0.0) or 0.0))
                    if direction == "LocalX":
                        return tuple(vx)
                    if direction == "LocalY":
                        return tuple(vy)
                    if direction == "LocalZ":
                        return tuple(vz)
                except Exception:
                    pass
        return (0.0, 0.0, -1.0)

    @staticmethod
    def _element_is_vertical(elem: FrameElement, nodes: dict) -> bool:
        """True when the element is aligned with the global Z axis (a column).

        Only Z-aligned members qualify as "the column directly above" for
        the gravity-axial recursion in :meth:`_stack_gravity_axial`.  An
        *axis*-aligned check would also flag horizontal roof beams, whose
        self-weights and floor loads are already covered by
        :meth:`_member_vertical_gravity_load` — recursing into them
        double-counts the whole roof grid and inflates ``P_g``.
        """
        ni = nodes.get(elem.node_i)
        nj = nodes.get(elem.node_j)
        if ni is None or nj is None:
            return False
        dx = nj.x - ni.x
        dy = nj.y - ni.y
        dz = nj.z - ni.z
        L = math.hypot(dx, dy, dz)
        if L <= 0.0:
            return False
        return abs(dz) / L > 0.99

    def _stack_gravity_axial(self, eid: str, _visiting: Optional[set] = None) -> float:
        """Tributary gravity axial force at the top of a column (compression +).

        Recurses up the vertical column stack so multi-storey columns pick
        up the storeys above: at each joint the joint loads plus one half
        of every non-column member's vertical gravity load are summed, and
        the column above contributes its own self-weight and its top joint
        tributary.  A ``column_gravity_loads`` override always wins over
        this estimate (see :meth:`_derive_gravity_axial_loads`).
        """
        _visiting = set() if _visiting is None else _visiting
        if eid in _visiting:
            return 0.0
        _visiting = _visiting | {eid}
        elem = self.mesh_model.frame_elements.get(eid)
        if elem is None:
            return 0.0
        grav = {n for n, p in self.mesh_model.load_patterns.items() if self._is_gravity_pattern(p)}
        top_id = self._top_node_id(elem)
        nodes = self.mesh_model.nodes
        P = 0.0
        # Joint loads at the column top (vertical component, compression +)
        for jl in self.mesh_model.joint_loads:
            if jl.pattern in grav and jl.node_id == top_id:
                P += max(-float(getattr(jl, "fz", 0.0) or 0.0), 0.0)
        # Members framing into the top joint
        for eid2, elem2 in self.mesh_model.frame_elements.items():
            if eid2 == eid or getattr(elem2, "inactive", False):
                continue
            if top_id not in (elem2.node_i, elem2.node_j):
                continue
            if self._element_is_vertical(elem2, nodes):
                # Column directly above: its self-weight is carried at its
                # base (= this joint) plus the axial at its own top.
                P += self._member_self_weight(eid2) + self._stack_gravity_axial(eid2, _visiting)
            else:
                P += 0.5 * self._member_vertical_gravity_load(eid2, grav)
        return max(P, 0.0)

    def _derive_gravity_axial_loads(self, col_ids: list) -> dict[str, float]:
        """Per-column operating gravity axial load ``P_g`` (compression +).

        Explicit ``column_gravity_loads`` overrides win; otherwise the
        tributary estimate of :meth:`_stack_gravity_axial` is used.
        """
        overrides = self.config.get("column_gravity_loads") or {}
        result: dict[str, float] = {}
        for eid in col_ids:
            if eid in overrides:
                result[eid] = float(overrides[eid] or 0.0)
            else:
                result[eid] = self._stack_gravity_axial(eid)
        return result

    def _default_limit_state_shear_kdeg(self, sec: Any, concrete: Any, L: float) -> float:
        """Default shear degrading slope: 20 % of the cracked fixed-fixed
        flexural stiffness ``12*E_c*I_g/L^3`` (Elwood's ``kf`` proxy)."""
        ec = float(getattr(concrete, "E_mod", 0.0) or 0.0)
        b = float(getattr(sec, "bf", 0.0) or 0.0) or float(getattr(sec, "b", 0.0) or 0.0)
        h = float(getattr(sec, "depth", 0.0) or 0.0) or float(getattr(sec, "h", 0.0) or 0.0)
        if ec <= 0.0 or b <= 0.0 or h <= 0.0 or L <= 0.0:
            return 0.0
        ig = b * h**3 / 12.0
        return 0.2 * 12.0 * ec * ig / L**3

    def _prepare_limit_state_columns(self) -> None:
        """Topology + parameter planning for limit-state columns (Phase A).

        Runs inside :meth:`build_domain` **before** node/element creation
        so that the re-pointed beams and the control/anchor nodes are part
        of the regular domain build.  No OpenSees commands are emitted here
        — the OpenSees curves/materials/springs are created later by
        :meth:`_create_limit_state_columns`.
        """
        cols = self.config.get("limit_state_columns")
        if not cols:
            self._limit_state_plan = None
            return

        # ── Idempotency: canonical snapshot on the first call ──────
        if not hasattr(self, "_limit_state_canonical"):
            self._limit_state_canonical = {
                "frame_elements": {
                    eid: (elem.node_i, elem.node_j)
                    for eid, elem in self.mesh_model.frame_elements.items()
                    if not getattr(elem, "inactive", False)
                },
                "frame_assignments": dict(self.mesh_model.frame_assignments),
                "restraints": dict(self.mesh_model.restraints),
                # Shallow copies: ``_prepare_limit_state_columns`` re-points
                # the joint loads' node_id in place; restoring must undo it.
                "joint_loads": [copy.copy(jl) for jl in self.mesh_model.joint_loads],
            }

        from ..capacity.elwood_limit_state import (
            elwood_column_geometry,
            elwood_column_parameters,
            elwood_shear_limit_force,
        )
        from ..model.sap_data import ConcreteRectangularSection

        units = self.units
        params_all = self.config.get("limit_state_params") or {}
        p_g_map = self._derive_gravity_axial_loads(cols)
        nodes = self.mesh_model.nodes
        next_node_tag = max((nd.node_tag for nd in nodes.values()), default=0) + 1
        plan: list[dict] = []

        for eid in cols:
            elem = self.mesh_model.frame_elements.get(eid)
            if elem is None or getattr(elem, "inactive", False):
                logger.warning("limit-state: unknown/inactive frame element '%s' skipped", eid)
                continue
            sec_name = self.mesh_model.frame_assignments.get(eid)
            sec = self.mesh_model.sections.get(sec_name) if sec_name else None
            if not isinstance(sec, ConcreteRectangularSection):
                logger.warning(
                    "limit-state: frame element '%s' has no concrete rectangular section "
                    "(got %s) — skipped",
                    eid,
                    sec_name,
                )
                continue
            concrete = self.mesh_model.materials.get(sec.material)
            if concrete is None:
                logger.warning(
                    "limit-state: material '%s' missing for '%s' — skipped", sec.material, eid
                )
                continue
            if not str(getattr(concrete, "type", "") or "").lower().startswith("concrete"):
                logger.warning(
                    "limit-state: material '%s' of '%s' is not concrete — skipped",
                    sec.material,
                    eid,
                )
                continue

            # ── Geometry / column axis ──
            ni = nodes.get(elem.node_i)
            nj = nodes.get(elem.node_j)
            if ni is None or nj is None:
                logger.warning("limit-state: missing nodes for '%s' — skipped", eid)
                continue
            dx, dy, dz = nj.x - ni.x, nj.y - ni.y, nj.z - ni.z
            L = math.hypot(dx, dy, dz)
            if L <= 1e-9:
                logger.warning("limit-state: zero-length element '%s' — skipped", eid)
                continue
            axis = int(np.argmax([abs(dx), abs(dy), abs(dz)]))
            if max(abs(dx), abs(dy), abs(dz)) / L < 0.99:
                logger.warning(
                    "limit-state: element '%s' is not aligned with a global axis "
                    "(Elwood model assumes straight columns) — skipped",
                    eid,
                )
                continue
            axial_dof = axis + 1
            horiz = [i for i in (0, 1, 2) if i != axis]
            shear_dof = horiz[0] + 1
            # ``perpDirn`` is the direction OpenSees uses to measure the
            # distance between ndI/ndJ for the drift (1/oneOverL).  For a
            # vertical column that is the column-axis DOF — matching the
            # 2D Elwood example (column along Y -> perpDirn = 2).
            perp_dof = axial_dof

            # ── Tie rebar material ──
            tie_name = getattr(sec, "tie_rebar_mat", None) or getattr(sec, "rebar_material", None)
            tie = self.mesh_model.materials.get(tie_name) if tie_name else None

            # ── Operating gravity axial load (override wins) ──
            p_g = float(p_g_map.get(eid, 0.0) or 0.0)
            if p_g <= 0.0:
                fc = float(getattr(concrete, "Fc", 0.0) or 0.0)
                b_w = float(getattr(sec, "bf", 0.0) or 0.0) or float(getattr(sec, "b", 0.0) or 0.0)
                h_d = float(getattr(sec, "depth", 0.0) or 0.0) or float(
                    getattr(sec, "h", 0.0) or 0.0
                )
                if b_w > 0.0 and h_d > 0.0 and fc > 0.0:
                    p_g = 0.25 * b_w * h_d * fc  # PEER 2003/01 reference P_g
                    logger.warning(
                        "limit-state: no gravity axial load derived for '%s'; "
                        "using 0.25*A_g*f'c = %.3g (supply 'column_gravity_loads' to override)",
                        eid,
                        p_g,
                    )
                else:
                    logger.warning("limit-state: cannot derive P_g for '%s' — skipped", eid)
                    continue

            # ── Elwood parameters ──
            overrides = dict(params_all.get(eid, {}) or {})
            kwargs = dict(overrides)
            geom = elwood_column_geometry(
                sec,
                concrete,
                tie=tie,
                tie_legs=kwargs.get("tie_legs", 2),
                core_depth=kwargs.get("core_depth"),
            )
            try:
                v_ref = elwood_shear_limit_force(0.01, p_g, geom, units)
            except ValueError as exc:
                logger.warning(
                    "limit-state: degenerate geometry for '%s' - skipped (%s)",
                    eid,
                    exc,
                )
                continue
            kwargs.setdefault("kdeg_shear", self._default_limit_state_shear_kdeg(sec, concrete, L))
            # Post-failure shear residual as a fraction of the 1%-drift shear
            # capacity V(0.01) (Elwood's Vr ~ 10 % of the peak).  The config key
            # ``limit_state_shear_residual_ratio`` feeds ``fres_shear`` directly:
            # ``elwood_column_parameters`` only consults ``shear_residual_ratio``
            # when ``fres_shear`` is None, so passing both would make it dead.
            kwargs.setdefault(
                "fres_shear",
                float(self.config.get("limit_state_shear_residual_ratio", 0.10)) * v_ref,
            )
            params = elwood_column_parameters(sec, concrete, tie=tie, column_length=L, **kwargs)

            # ── Topology: control + anchor nodes at the column top ──
            top_id = self._top_node_id(elem)
            top_node = nodes[top_id]
            bottom_id = elem.node_j if top_id == elem.node_i else elem.node_i
            bottom_node = nodes.get(bottom_id, ni)
            control_id = f"{eid}_limit_top"
            anchor_id = f"{eid}_limit_anchor"
            control_tag = next_node_tag
            next_node_tag += 1
            anchor_tag = next_node_tag
            next_node_tag += 1
            nodes[control_id] = Node(control_id, control_tag, top_node.x, top_node.y, top_node.z)
            nodes[anchor_id] = Node(anchor_id, anchor_tag, top_node.x, top_node.y, top_node.z)
            self.mesh_model.restraints[anchor_id] = Restraint([1, 1, 1, 1, 1, 1])
            # Move the joint restraint onto the control node (the spring
            # rigid-ties the original top node through the zeroLength).
            if top_id in self.mesh_model.restraints:
                self.mesh_model.restraints[control_id] = self.mesh_model.restraints[top_id]
                del self.mesh_model.restraints[top_id]
            # Re-point beams above to the control node
            for eid2, elem2 in self.mesh_model.frame_elements.items():
                if eid2 == eid or getattr(elem2, "inactive", False):
                    continue
                if elem2.node_i == top_id:
                    elem2.node_i = control_id
                if elem2.node_j == top_id:
                    elem2.node_j = control_id
            # Re-point joint loads so gravity enters above the spring
            for jl in self.mesh_model.joint_loads:
                if jl.node_id == top_id:
                    jl.node_id = control_id

            plan.append(
                {
                    "eid": eid,
                    "elem_tag": self.frame_tag_map.get(eid),
                    "bottom_tag": bottom_node.node_tag,
                    "top_tag": top_node.node_tag,
                    "control_tag": control_tag,
                    "anchor_tag": anchor_tag,
                    "axis": axis,
                    "axial_dof": axial_dof,
                    "shear_dof": shear_dof,
                    "perp_dof": perp_dof,
                    "geometry": geom,
                    "params": params,
                    "p_g": p_g,
                }
            )

        self._limit_state_plan = plan
        if not plan:
            logger.warning("limit_state_columns configured but no valid concrete columns found")

    def _create_limit_state_columns(self) -> None:
        """Emit OpenSees limit-state curves/materials/springs (Phase B).

        Called after :meth:`_create_elements` (so the flexural element tags
        exist on the domain).  For each planned column:

        * ``limitCurve Shear`` — Elwood shear capacity surface (imperial).
        * ``limitCurve ThreePoint`` — axial surface (OpenSeesPy 3.8.0
          cannot construct ``limitCurve Axial``; ThreePoint with
          ``forType=2`` monitors the beam-column's axial force).
        * Two ``uniaxialMaterial LimitState`` laws (shear + axial).
        * A ``zeroLength`` spring between the column top and the new
          control node: shear on the first horizontal DOF, axial on the
          column-axis DOF, the remaining DOFs rigid-tied.
        * A soft axial ``zeroLength`` catch from the control node to a
          fixed anchor so gravity is still supported after axial failure.

        This mirrors the validated PEER 2003/01 §8.2.2 series model and the
        ``local/elwood_prototype.py`` topology (top spring, co-located
        control node).
        """
        plan = getattr(self, "_limit_state_plan", None)
        if not plan:
            return
        from ..capacity.elwood_limit_state import (
            elwood_limit_state_envelope,
            elwood_shear_limit_force,
            three_point_axial_surface,
        )

        units = self.units
        pinch_x = float(self.config.get("limit_state_pinch_x", 0.5))
        pinch_y = float(self.config.get("limit_state_pinch_y", 0.4))
        damage1 = float(self.config.get("limit_state_damage1", 0.0))
        damage2 = float(self.config.get("limit_state_damage2", 0.0))
        beta = float(self.config.get("limit_state_beta", 0.4))
        soft_fraction = float(self.config.get("limit_state_soft_axial_fraction", 2.0e-4))

        # ── Tag allocation (materials/curves are distinct namespaces) ──
        try:
            max_ops_mat = max(ops.getMaterialTags(), default=0)
        except Exception:
            max_ops_mat = 0
        mat_tag = max(max(self.material_tags.values(), default=0), max_ops_mat) + 1
        curve_tag = mat_tag + 10 * len(plan) + 10
        try:
            max_ops_ele = max(ops.getEleTags(), default=0)
        except Exception:
            max_ops_ele = 0
        spring_tag = (
            max(
                max_ops_ele,
                max(self.frame_tag_map.values(), default=0),
                max((r[3] for r in self._offset_rigid_links), default=0),
            )
            + 1
        )

        for col in plan:
            eid = col["eid"]
            elem_tag = col["elem_tag"]
            params = col["params"]
            geom = col["geometry"]
            p_g = col["p_g"]
            bottom_tag = col["bottom_tag"]
            top_tag = col["top_tag"]
            control_tag = col["control_tag"]
            anchor_tag = col["anchor_tag"]
            shear_dof = col["shear_dof"]
            perp_dof = col["perp_dof"]
            axial_dof = col["axial_dof"]

            # ── Imperial constants (domain is kip-in-ksi) ──
            b_in = float(geom.b)
            h_in = float(geom.h)
            d_in = float(geom.d)
            fc_psi = float(geom.fc) * 1000.0
            fsw_k = float(params.fsw)

            # ── Rigid elastic for tied DOFs ──
            rigid_tag = mat_tag
            mat_tag += 1
            ops.uniaxialMaterial("Elastic", rigid_tag, 9.9e9)

            # ── Shear limit curve ──
            shear_curve_tag = curve_tag
            curve_tag += 1
            ops.limitCurve(
                "Shear",
                shear_curve_tag,
                elem_tag,
                float(params.rho),
                fc_psi,
                b_in,
                h_in,
                d_in,
                fsw_k,
                float(params.kdeg_shear),
                float(params.fres_shear),
                2,  # defType = interstory drift (chord rotation)
                0,  # forType = spring force
                bottom_tag,
                control_tag,
                shear_dof,
                perp_dof,
                0.0,  # delta
            )

            # ── Axial three-point surface (limitCurve Axial workaround) ──
            axial_curve_tag = curve_tag
            curve_tag += 1
            pts = three_point_axial_surface(
                p_g,
                float(params.fsw),
                units,
                fres=float(params.fres_axial) if params.fres_axial else None,
            )
            (x1, y1), (x2, y2), (x3, y3) = pts
            ops.limitCurve(
                "ThreePoint",
                axial_curve_tag,
                elem_tag,
                x1,
                y1,
                x2,
                y2,
                x3,
                y3,
                float(params.kdeg_axial),
                float(params.fres_axial or 0.0),
                2,  # defType = interstory drift
                2,  # forType = axial force of the beam-column
                bottom_tag,
                control_tag,
                shear_dof,
                perp_dof,
            )

            # ── Shear LimitState material (elastic pre-failure backbone) ──
            shear_mat_tag = mat_tag
            mat_tag += 1
            v_ref = 2.0 * elwood_shear_limit_force(0.01, p_g, geom, units)
            v_backbone = [0.4 * v_ref, 0.7 * v_ref, v_ref]
            k_shear = float(params.shear_elastic_slope)
            sp = elwood_limit_state_envelope(v_backbone, k_shear)
            s_pos = [val for pair in sp for val in pair]
            s_neg = [-val for val in s_pos]
            ops.uniaxialMaterial(
                "LimitState",
                shear_mat_tag,
                *s_pos,
                *s_neg,
                pinch_x,
                pinch_y,
                damage1,
                damage2,
                beta,
                shear_curve_tag,
                2,
                0,  # trailing flag matches Elwood's example scripts
            )

            # ── Axial LimitState material ──
            axial_mat_tag = mat_tag
            mat_tag += 1
            p_backbone = [0.92 * p_g, p_g, 1.2 * p_g]
            k_ax = float(params.axial_elastic_slope)
            ap = elwood_limit_state_envelope(p_backbone, k_ax)
            a_pos = [val for pair in ap for val in pair]
            a_neg = [-val for val in a_pos]
            ops.uniaxialMaterial(
                "LimitState",
                axial_mat_tag,
                *a_pos,
                *a_neg,
                0.5,
                0.5,
                0.0,
                0.0,
                0.0,
                axial_curve_tag,
                2,
            )

            # ── Soft axial catch spring ──
            soft_tag = mat_tag
            mat_tag += 1
            soft_k = max(k_ax * soft_fraction, 1e-9)
            ops.uniaxialMaterial("Elastic", soft_tag, soft_k)

            # ── Top zeroLength spring: shear + axial + rigid ties ──
            mats_by_dof = {axial_dof: axial_mat_tag, shear_dof: shear_mat_tag}
            dirs = [1, 2, 3, 4, 5, 6]
            mats = [mats_by_dof.get(d, rigid_tag) for d in dirs]
            ops.element(
                "zeroLength",
                spring_tag,
                top_tag,
                control_tag,
                "-mat",
                *mats,
                "-dir",
                *dirs,
            )
            spring_tag += 1
            # Soft axial catch: control node → fixed anchor
            ops.element(
                "zeroLength",
                spring_tag,
                control_tag,
                anchor_tag,
                "-mat",
                soft_tag,
                "-dir",
                axial_dof,
            )
            spring_tag += 1

            # ── Register synthetic material tags so rebuilds don't reuse ──
            self.material_tags[f"limit_state_rigid_{eid}"] = rigid_tag
            self.material_tags[f"limit_state_shear_{eid}"] = shear_mat_tag
            self.material_tags[f"limit_state_axial_{eid}"] = axial_mat_tag
            self.material_tags[f"limit_state_soft_{eid}"] = soft_tag

    # ── Loads ────────────────────────────────────────────────────

    def _create_loads(
        self,
        pattern_scales: Optional[dict[str, float]] = None,
    ) -> None:
        """Create OpenSees load patterns from MeshModel data."""

        elements = self.mesh_model.frame_elements
        assignments = self.mesh_model.frame_assignments
        dist_loads = self.mesh_model.frame_dist_loads
        edge_loads = self.edge_loads_from_areas

        patterns_created: set = set()
        self.load_totals = {}
        self._sw_load_totals = {}
        self._gravity_load_totals = {}
        self._joint_load_totals = {}

        # ── Pre-compute frame + area self-weight per-node ────────
        # Stored as a list of (node_tag, fz) tuples; applied per-pattern
        # during the pattern loop below if the pattern's swf > 0.
        _sw_node_loads: list[tuple[int, float]] = []
        for eid, elem in elements.items():
            if getattr(elem, "inactive", False):
                continue
            sec_name = assignments.get(eid, "")
            sec = self.mesh_model.sections.get(sec_name)
            if sec is None:
                continue
            mat = self.mesh_model.materials.get(sec.material)
            if mat is None or mat.unit_weight == 0:
                continue
            _A = getattr(sec, "A", 0.0)
            if _A <= 0:
                continue
            ni = self.mesh_model.nodes.get(elem.node_i)
            nj = self.mesh_model.nodes.get(elem.node_j)
            if ni is None or nj is None:
                continue
            L = math.sqrt((nj.x - ni.x) ** 2 + (nj.y - ni.y) ** 2 + (nj.z - ni.z) ** 2)
            total_w = _A * mat.unit_weight * L
            nd_i = self.mesh_model.nodes.get(elem.node_i)
            nd_j = self.mesh_model.nodes.get(elem.node_j)
            if nd_i is not None:
                _sw_node_loads.append((nd_i.node_tag, -total_w * 0.5))
            if nd_j is not None:
                _sw_node_loads.append((nd_j.node_tag, -total_w * 0.5))

        # ── Area element self-weight ─────────────────────────────
        from ..model.sap_data import ShellSection as _ShellSec

        for aid, area in self.mesh_model.area_elements.items():
            if getattr(area, "inactive", False):
                continue
            sec_name = self.mesh_model.area_assignments.get(aid, "")
            sec = self.mesh_model.sections.get(sec_name)
            if sec is None or not isinstance(sec, _ShellSec):
                continue
            mat = self.mesh_model.materials.get(sec.material)
            if mat is None or mat.unit_weight == 0:
                continue
            t = getattr(sec, "thickness", 0.0)
            if t <= 0:
                continue
            poly = [self.mesh_model.nodes.get(nid) for nid in area.node_ids]
            poly = [nd for nd in poly if nd is not None]
            if len(poly) < 3:
                continue
            area_3d = 0.0
            v0 = np.array([poly[0].x, poly[0].y, poly[0].z])
            for k in range(1, len(poly) - 1):
                v1 = np.array([poly[k].x, poly[k].y, poly[k].z])
                v2 = np.array([poly[k + 1].x, poly[k + 1].y, poly[k + 1].z])
                area_3d += 0.5 * np.linalg.norm(np.cross(v1 - v0, v2 - v0))
            total_w = area_3d * t * mat.unit_weight
            n_corners = len(poly)
            for nd in poly:
                _sw_node_loads.append((nd.node_tag, -total_w / n_corners))

        # Pattern loop — deterministic tag generation
        all_patterns = set()
        for ld in dist_loads:
            all_patterns.add(ld.pattern)
        for ld in edge_loads:
            all_patterns.add(ld.pattern)
        for jl in getattr(self.mesh_model, "joint_loads", []):
            all_patterns.add(jl.pattern)
        for gl in getattr(self.mesh_model, "frame_gravity_loads", []):
            all_patterns.add(gl.pattern)
        for agl in getattr(self.mesh_model, "area_gravity_loads", []):
            all_patterns.add(agl.pattern)
        # Include patterns with self_weight_factor > 0 so their self-weight
        # can be activated even when they have no explicit load entries.
        for pn, lp in self.mesh_model.load_patterns.items():
            if abs(getattr(lp, "self_weight_factor", 0.0)) > 1e-12:
                all_patterns.add(pn)
        # Assign deterministic tags based on sorted pattern names
        _pat_tags = {pname: (1000 + i, 100 + i) for i, pname in enumerate(sorted(all_patterns))}

        for pname in sorted(all_patterns):
            if pattern_scales is not None and pname not in pattern_scales:
                continue
            scale = pattern_scales.get(pname, 1.0) if pattern_scales else 1.0

            ts_tag, ptag = _pat_tags.get(pname, (1000, 100))
            ops.timeSeries("Linear", ts_tag)
            ops.pattern("Plain", ptag, ts_tag)
            patterns_created.add(pname)

            load_total = 0.0

            # Frame distributed loads
            for ld in dist_loads:
                if ld.pattern != pname:
                    continue
                tag = self.frame_tag_map.get(ld.frame_id)
                if tag is None:
                    continue
                elem = elements.get(ld.frame_id)
                if elem is None or getattr(elem, "inactive", False):
                    continue
                ni = self.mesh_model.nodes.get(elem.node_i)
                nj = self.mesh_model.nodes.get(elem.node_j)
                if ni is None or nj is None:
                    continue

                wa = ld.val_a * scale
                wb = ld.val_b * scale
                aL = ld.rdist_a
                bL = ld.rdist_b

                vx, vy, vz = self.get_local_axes(elem)
                T = np.column_stack([vx, vy, vz])
                dir_map = {"Gravity": (0, 0, -1), "X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}
                gx, gy, gz = dir_map.get(ld.direction, (0, 0, 0))
                g_local = np.linalg.solve(T, np.array([gx, gy, gz]))
                wy_a = g_local[1] * wa
                wz_a = g_local[2] * wa
                wx_a = g_local[0] * wa
                wy_b = g_local[1] * wb
                wz_b = g_local[2] * wb
                wx_b = g_local[0] * wb

                is_uniform = abs(wa - wb) < 1e-12
                if is_uniform and abs(aL) < 1e-12 and abs(bL - 1.0) < 1e-12:
                    ops.eleLoad("-ele", tag, "-type", "-beamUniform", wy_a, wz_a, wx_a)
                elif is_uniform:
                    ops.eleLoad("-ele", tag, "-type", "-beamUniform", wy_a, wz_a, wx_a, aL, bL)
                else:
                    L_seg = bL - aL
                    for i in range(4):
                        seg_a = aL + i * L_seg / 4
                        seg_b = aL + (i + 1) * L_seg / 4
                        xi = (i + 0.5) / 4
                        ops.eleLoad(
                            "-ele",
                            tag,
                            "-type",
                            "-beamUniform",
                            wy_a + (wy_b - wy_a) * xi,
                            wz_a + (wz_b - wz_a) * xi,
                            wx_a + (wx_b - wx_a) * xi,
                            seg_a,
                            seg_b,
                        )

                load_total += abs(wa + wb) * 0.5 * abs(bL - aL)

            # Edge loads (from area-to-frame conversion)
            for ld in edge_loads:
                if ld.pattern != pname:
                    continue
                tag = self.frame_tag_map.get(ld.frame_id)
                if tag is None:
                    continue
                # Look up the unsplit (original) frame element to get
                # its local axes for projecting the global direction.
                elem = self.mesh_model.frame_elements.get(ld.frame_id)
                if elem is None or getattr(elem, "inactive", False):
                    continue
                try:
                    vx, vy, vz = self.get_local_axes(elem)
                except Exception:
                    continue
                # Determine the global direction vector
                if ld.direction == "Gravity":
                    gdir = np.array([0.0, 0.0, -1.0])
                elif ld.direction == "X":
                    gdir = np.array([1.0, 0.0, 0.0])
                elif ld.direction == "Y":
                    gdir = np.array([0.0, 1.0, 0.0])
                elif ld.direction == "Z":
                    gdir = np.array([0.0, 0.0, 1.0])
                elif ld.direction == "LocalX":
                    gdir = vx
                elif ld.direction == "LocalY":
                    gdir = vy
                elif ld.direction == "LocalZ":
                    gdir = vz
                else:
                    gdir = np.array([0.0, 0.0, -1.0])
                wa = ld.val_a * scale
                wb = ld.val_b * scale
                a_overL = max(0.0, min(1.0, ld.rdist_a))
                b_overL = max(0.0, min(1.0, ld.rdist_b))
                # Project global direction onto local axes
                wx_a = wa * np.dot(gdir, vx)
                wy_a = wa * np.dot(gdir, vy)
                wz_a = wa * np.dot(gdir, vz)
                wx_b = wb * np.dot(gdir, vx)
                wy_b = wb * np.dot(gdir, vy)
                wz_b = wb * np.dot(gdir, vz)
                # Apply using the same approach as the legacy Builder
                is_uniform = abs(wa - wb) < 1e-6
                is_full_span = abs(a_overL) < 1e-12 and abs(b_overL - 1.0) < 1e-12
                if is_uniform and is_full_span:
                    ops.eleLoad("-ele", tag, "-type", "-beamUniform", wy_a, wz_a, wx_a)
                elif is_uniform:
                    ops.eleLoad(
                        "-ele", tag, "-type", "-beamUniform", wy_a, wz_a, wx_a, a_overL, b_overL
                    )
                else:
                    # Non-uniform → decompose into partial-span segments
                    N = 4
                    span_frac = b_overL - a_overL
                    for i in range(N):
                        seg_a = a_overL + i * span_frac / N
                        seg_b = a_overL + (i + 1) * span_frac / N
                        xi = (i + 0.5) / N
                        wy_mid = wy_a + (wy_b - wy_a) * xi
                        wz_mid = wz_a + (wz_b - wz_a) * xi
                        wx_mid = wx_a + (wx_b - wx_a) * xi
                        ops.eleLoad(
                            "-ele",
                            tag,
                            "-type",
                            "-beamUniform",
                            wy_mid,
                            wz_mid,
                            wx_mid,
                            seg_a,
                            seg_b,
                        )
                load_total += abs(wa) * abs(b_overL - a_overL)

            # ── Self-weight for this pattern ────────────────────────
            # Apply if the pattern has self_weight_factor > 0 (e.g. DEAD swf=1).
            # Look up the pattern's swf from MeshModel load_patterns (passed
            # through from SAP2000 by the Preprocessor).
            _lp = self.mesh_model.load_patterns.get(pname)
            swf = getattr(_lp, "self_weight_factor", 0.0) if _lp else 0.0
            if abs(swf) > 1e-12:
                sw_scale = swf * scale
                sw_total = 0.0
                _sw_fz_total = 0.0
                for node_tag, fz in _sw_node_loads:
                    ops.load(node_tag, 0.0, 0.0, fz * sw_scale, 0.0, 0.0, 0.0)
                    sw_total += abs(fz * sw_scale)
                    _sw_fz_total += fz * sw_scale
                # Store per-pattern for check_self_weight_consistency
                if pname not in self._sw_load_totals:
                    self._sw_load_totals[pname] = dict.fromkeys(
                        ("fx", "fy", "fz", "mx", "my", "mz"), 0.0
                    )
                self._sw_load_totals[pname]["fz"] += _sw_fz_total
                load_total += sw_total

            # ── Joint loads (SAP2000 "JOINT LOADS - FORCE") ──────────
            # Point forces/moments at joints are applied as nodal loads.
            # Previously these were parsed and carried through the
            # Preprocessor but never emitted to the OpenSees domain
            # (Gap 4 discovery — the Vecchio & Emara benchmark's 700 kN
            # column loads were silently dropped).
            for jl in getattr(self.mesh_model, "joint_loads", []):
                if jl.pattern != pname:
                    continue
                node = self.mesh_model.nodes.get(jl.node_id)
                if node is None:
                    continue
                ops.load(
                    node.node_tag,
                    jl.fx * scale,
                    jl.fy * scale,
                    jl.fz * scale,
                    jl.mx * scale,
                    jl.my * scale,
                    jl.mz * scale,
                )
                load_total += scale * (
                    abs(jl.fx) + abs(jl.fy) + abs(jl.fz) + abs(jl.mx) + abs(jl.my) + abs(jl.mz)
                )
                if pname not in self._joint_load_totals:
                    self._joint_load_totals[pname] = dict.fromkeys(
                        ("fx", "fy", "fz", "mx", "my", "mz"), 0.0
                    )
                for _k in ("fx", "fy", "fz", "mx", "my", "mz"):
                    self._joint_load_totals[pname][_k] += getattr(jl, _k) * scale

            self.load_totals[pname] = load_total

        # ── Frame gravity loads (explicit multipliers on self-weight) ──
        for gl in getattr(self.mesh_model, "frame_gravity_loads", []):
            pname = gl.pattern
            if pattern_scales is not None and pname not in pattern_scales:
                continue
            scale = pattern_scales.get(pname, 1.0) if pattern_scales else 1.0
            if abs(scale) < 1e-12:
                continue
            # Create pattern if needed
            if pname not in patterns_created:
                ts_tag, ptag = _pat_tags.get(pname, (1000, 100))
                ops.timeSeries("Linear", ts_tag)
                ops.pattern("Plain", ptag, ts_tag)
                patterns_created.add(pname)
            elem = elements.get(gl.frame_id)
            if elem is None or getattr(elem, "inactive", False):
                continue
            sec_name = assignments.get(gl.frame_id, "")
            if not sec_name:
                continue
            sec = self.mesh_model.sections.get(sec_name)
            if sec is None:
                continue
            mat = self.mesh_model.materials.get(sec.material)
            if mat is None or abs(mat.unit_weight) < 1e-12:
                continue
            ni = self.mesh_model.nodes.get(elem.node_i)
            nj = self.mesh_model.nodes.get(elem.node_j)
            if ni is None or nj is None:
                continue
            L = math.sqrt((nj.x - ni.x) ** 2 + (nj.y - ni.y) ** 2 + (nj.z - ni.z) ** 2)
            if L < 1e-12:
                continue
            sw_per_len = getattr(sec, "A", 0.0) * mat.unit_weight
            fx = sw_per_len * L * gl.multiplier_x * scale * 0.5
            fy = sw_per_len * L * gl.multiplier_y * scale * 0.5
            fz = sw_per_len * L * gl.multiplier_z * scale * 0.5
            ops.load(ni.node_tag, fx, fy, fz, 0.0, 0.0, 0.0)
            ops.load(nj.node_tag, fx, fy, fz, 0.0, 0.0, 0.0)
            if pname not in self._gravity_load_totals:
                self._gravity_load_totals[pname] = {
                    "fx": 0.0,
                    "fy": 0.0,
                    "fz": 0.0,
                    "mx": 0.0,
                    "my": 0.0,
                    "mz": 0.0,
                }
            self._gravity_load_totals[pname]["fx"] += fx * 2
            self._gravity_load_totals[pname]["fy"] += fy * 2
            self._gravity_load_totals[pname]["fz"] += fz * 2
        # ── Area gravity loads (explicit multipliers) ────────────
        for agl in getattr(self.mesh_model, "area_gravity_loads", []):
            pname = agl.pattern
            if pattern_scales is not None and pname not in pattern_scales:
                continue
            scale = pattern_scales.get(pname, 1.0) if pattern_scales else 1.0
            if abs(scale) < 1e-12:
                continue
            if pname not in patterns_created:
                ts_tag, ptag = _pat_tags.get(pname, (1000, 100))
                ops.timeSeries("Linear", ts_tag)
                ops.pattern("Plain", ptag, ts_tag)
                patterns_created.add(pname)
            area_elem = self.mesh_model.area_elements.get(agl.area_id)
            if area_elem is None:
                continue
            if getattr(area_elem, "inactive", False):
                # Parent was split/meshed — apply to all leaf descendants
                sub_ids = collect_descendants(agl.area_id, self.mesh_model.area_elements)
                if not sub_ids:
                    continue
                for sub_id in sub_ids:
                    sub_elem = self.mesh_model.area_elements[sub_id]
                    sec_name = self.mesh_model.area_assignments.get(sub_id, "")
                    if not sec_name:
                        continue
                    sec = self.mesh_model.sections.get(sec_name)
                    if sec is None:
                        continue
                    mat = self.mesh_model.materials.get(sec.material)
                    if mat is None or abs(mat.unit_weight) < 1e-12:
                        continue
                    thickness = getattr(sub_elem, "thickness", 0.0) or 0.0
                    if thickness < 1e-12:
                        continue
                    corner_pts = []
                    for nid in sub_elem.node_ids:
                        nd = self.mesh_model.nodes.get(nid)
                        if nd is None:
                            break
                        corner_pts.append((nd.x, nd.y, nd.z))
                    if len(corner_pts) < 3:
                        continue
                    area_mag = polygon_area_3d(corner_pts)
                    if area_mag < 1e-12:
                        continue
                    sw_per_area = thickness * mat.unit_weight
                    tfx = sw_per_area * area_mag * agl.multiplier_x * scale
                    tfy = sw_per_area * area_mag * agl.multiplier_y * scale
                    tfz = sw_per_area * area_mag * agl.multiplier_z * scale
                    n_c = len(sub_elem.node_ids)
                    for nid in sub_elem.node_ids:
                        nd = self.mesh_model.nodes.get(nid)
                        if nd is not None:
                            ops.load(nd.node_tag, tfx / n_c, tfy / n_c, tfz / n_c, 0.0, 0.0, 0.0)
                continue
            # Active (unmeshed) area element
            sec_name = self.mesh_model.area_assignments.get(agl.area_id, "")
            if not sec_name:
                continue
            sec = self.mesh_model.sections.get(sec_name)
            if sec is None:
                continue
            mat = self.mesh_model.materials.get(sec.material)
            if mat is None or abs(mat.unit_weight) < 1e-12:
                continue
            thickness = getattr(area_elem, "thickness", 0.0) or 0.0
            if thickness < 1e-12:
                continue
            corner_pts = []
            for nid in area_elem.node_ids:
                nd = self.mesh_model.nodes.get(nid)
                if nd is None:
                    break
                corner_pts.append((nd.x, nd.y, nd.z))
            if len(corner_pts) < 3:
                continue
            area_mag = polygon_area_3d(corner_pts)
            if area_mag < 1e-12:
                continue
            sw_per_area = thickness * mat.unit_weight
            tfx = sw_per_area * area_mag * agl.multiplier_x * scale
            tfy = sw_per_area * area_mag * agl.multiplier_y * scale
            tfz = sw_per_area * area_mag * agl.multiplier_z * scale
            n_c = len(area_elem.node_ids)
            for nid in area_elem.node_ids:
                nd = self.mesh_model.nodes.get(nid)
                if nd is not None:
                    ops.load(nd.node_tag, tfx / n_c, tfy / n_c, tfz / n_c, 0.0, 0.0, 0.0)

    # ── Rigid diaphragms ─────────────────────────────────────────

    @staticmethod
    def _select_diaphragm_master(tags):
        """Select the node tag nearest the centroid of the given tags.

        Reads each node's coordinate from the OpenSees domain once and
        caches the ``(x, y)`` values, then returns the tag whose cached
        position is closest to the centroid of all cached points.  Used to
        pick a diaphragm master for both the per-group and per-elevation
        paths.

        Args:
            tags: Sequence of OpenSees node tags in the diaphragm group.

        Returns:
            The node tag whose cached ``(x, y)`` is nearest the centroid.
        """
        coords = {t: tuple(ops.nodeCoord(t)[:2]) for t in tags}
        xs = [c[0] for c in coords.values()]
        ys = [c[1] for c in coords.values()]
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)
        return min(
            tags,
            key=lambda t: (coords[t][0] - cx) ** 2 + (coords[t][1] - cy) ** 2,
        )

    def _apply_rigid_diaphragms(self) -> int:
        """Apply rigid diaphragm constraints at detected storey levels.

        Diaphragm definitions come from ``MeshModel``, which the Preprocessor
        populates from two sources:

        1. **S2K joint constraints** — Z-axis ``DIAPHRAGM`` constraints parsed
           from ``CONSTRAINT DEFINITIONS - DIAPHRAGM`` +
           ``JOINT CONSTRAINT ASSIGNMENTS`` (the canonical source for frame-only
           models with explicit diaphragm definitions).
        2. **Horizontal area elements** — fallback for models without explicit
           constraints.

        When explicit S2K constraints are present, the Preprocessor records
        them as ``mesh_model.diaphragm_components`` — one ``(mean_z, [node_id,
        ...])`` tuple per constraint.  This preserves the S2K constraint
        grouping so **independent diaphragms at the same elevation are not
        merged** (e.g. two building wings separated by a seismic gap).  The
        builder emits one ``rigidDiaphragm`` per group, picking the centroid
        node inside each group as its master.

        When no explicit constraints exist (area-only fallback), the builder
        falls back to per-elevation merging: all nodes near a detected ``z``
        are grouped into a single diaphragm.

        The ``rigid_diaphragms`` config is an optional tri-state override:

        * **absent** — apply constraints detected from the S2K file / area
          elements.  No config entry is required when the model declares its
          diaphragms.
        * ``False`` — explicitly **disable** all rigid diaphragms, even when
          levels are otherwise detected.
        * ``[z1, z2, ...]`` — override the detected levels with explicit
          ones.  When this list is given, per-group components are ignored
          and the per-elevation merge behaviour is used.
        """
        levels = self.mesh_model.diaphragm_levels
        config_val = self.config.get("rigid_diaphragms", None)
        if config_val is False:
            return 0  # explicit opt-out — skip even if levels were detected

        # Distinguish the two list forms:
        #   [z1, z2, ...]              → legacy explicit Z list (merge by elevation)
        #   [{name, nodes|selection}]  → explicit named groups (one
        #                                rigidDiaphragm per component, resolved by
        #                                the Preprocessor)
        if (
            isinstance(config_val, list)
            and config_val
            and any(isinstance(item, dict) for item in config_val)
            and not all(isinstance(item, dict) for item in config_val)
        ):
            raise ValueError(
                "rigid_diaphragms must be either an all-numeric legacy Z list "
                "([z1, z2, ...]) or an all-dict list of explicit named groups "
                "([{name, nodes/selection}, ...]) - mixed lists containing both "
                "dicts and non-dicts are not supported."
            )
        is_legacy_z_list = isinstance(config_val, list) and not (
            bool(config_val) and all(isinstance(item, dict) for item in config_val)
        )
        if is_legacy_z_list:
            levels = sorted(float(z) for z in config_val)
            existing_components = getattr(self.mesh_model, "diaphragm_components", [])
            if existing_components:
                logger.warning(
                    "rigid_diaphragms as a legacy [z1, z2, ...] list will merge %d "
                    "independent constraint group(s) into per-elevation diaphragms. "
                    "Use explicit group dicts ({name, nodes|selection}) to preserve "
                    "independent diaphragm identity at the same elevation.",
                    len(existing_components),
                )

        components = getattr(self.mesh_model, "diaphragm_components", [])
        # Per-group path is used whenever the Preprocessor recorded explicit
        # components (S2K constraint groups, explicit named groups, or forced
        # storey detection).  Only a legacy Z-list override forces the
        # per-elevation merge behaviour.
        use_groups = not is_legacy_z_list and bool(components)

        if not use_groups and not levels:
            return 0

        applied = 0

        # ── Per-group path: preserve S2K constraint identity ──────
        if use_groups:
            for _z, node_ids in components:
                tags = []
                for nid in node_ids:
                    nd = self.mesh_model.nodes.get(nid)
                    if nd is None:
                        continue
                    try:
                        ops.nodeCoord(nd.node_tag)
                        tags.append(nd.node_tag)
                    except Exception as exc:
                        raise RuntimeError(
                            f"Node tag {nd.node_tag} (id={nid}) from diaphragm "
                            f"component at z={_z:.3f} does not exist in the "
                            f"OpenSees domain. The node may have been removed "
                            f"during preprocessing."
                        ) from exc
                if len(tags) < 2:
                    continue

                master = self._select_diaphragm_master(tags)
                slaves = [t for t in tags if t != master]
                try:
                    ops.rigidDiaphragm(3, master, *slaves)
                    applied += 1
                except Exception as exc:
                    logger.warning(
                        "rigidDiaphragm failed for group at z=%.3f (master=%d, %d slaves): %s",
                        _z,
                        master,
                        len(slaves),
                        exc,
                    )
                    continue
            return applied

        # ── Per-elevation fallback: merge all nodes near each level ──
        z_tol = float(getattr(self.mesh_model, "diaphragm_z_tolerance", 0.01))
        for z in levels:
            tags_at_z = []
            for nid, nd in self.mesh_model.nodes.items():
                if abs(nd.z - float(z)) > z_tol:
                    continue
                try:
                    ops.nodeCoord(nd.node_tag)
                    tags_at_z.append(nd.node_tag)
                except Exception:
                    continue
            if len(tags_at_z) < 2:
                continue

            master = self._select_diaphragm_master(tags_at_z)
            slaves = [t for t in tags_at_z if t != master]
            try:
                ops.rigidDiaphragm(3, master, *slaves)
                applied += 1
            except Exception as exc:
                logger.warning(
                    "rigidDiaphragm failed for elevation z=%.3f (master=%d, %d slaves): %s",
                    float(z),
                    master,
                    len(slaves),
                    exc,
                )
                continue
        return applied

    # ═══════════════════════════════════════════════════════════════
    # Analysis methods
    # ═══════════════════════════════════════════════════════════════

    # ═══════════════════════════════════════════════════════════════
    # Mass
    # ═══════════════════════════════════════════════════════════════

    # ═══════════════════════════════════════════════════════════════
    # Modal and response-spectrum analysis
    # ═══════════════════════════════════════════════════════════════

    # =========================================================================
    # RS element forces (after run_response_spectrum_analysis)
    # =========================================================================

    # =========================================================================
    # RS nodal displacements (from mode‑shape combination)
    # =========================================================================

    # ═══════════════════════════════════════════════════════════════
    # Utilities
    # ═══════════════════════════════════════════════════════════════

    # ═══════════════════════════════════════════════════════════════
    # Load equilibrium check
    # ═══════════════════════════════════════════════════════════════

    # ═══════════════════════════════════════════════════════════════
    # Export
    # ═══════════════════════════════════════════════════════════════

    # ═══════════════════════════════════════════════════════════════
    # Pushover analysis
    # ═══════════════════════════════════════════════════════════════

    # ═══════════════════════════════════════════════════════════════
    # Pushover helpers
    # ═══════════════════════════════════════════════════════════════

    # ═══════════════════════════════════════════════════════════════
    # Capacity Spectrum Method (CSM)
    # ═══════════════════════════════════════════════════════════════


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
