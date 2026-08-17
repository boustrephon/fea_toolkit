"""Analysis builder — create OpenSees domain from a prepared ``MeshModel``.

The :class:`AnalysisBuilder` takes a :class:`~fea_toolkit.model.mesh_model.MeshModel`
(fully prepared topology from the :class:`~fea_toolkit.opensees.preprocessor.Preprocessor`)
and creates the OpenSees domain objects.  It handles all analysis execution
and result extraction — no topology mutations occur here.
"""

import copy
import logging
import math
from typing import TYPE_CHECKING, Any, ClassVar, Optional, Union

import numpy as np
import openseespy.opensees as ops

if TYPE_CHECKING:
    # pandas is not a required dependency — only imported at runtime inside
    # check_load_equilibrium().  The TYPE_CHECKING guard lets Ruff resolve
    # the "pd.DataFrame" return annotation statically without adding pandas
    # to the core dependencies.
    import pandas as pd

import contextlib

from ..model.geometry import get_local_axes, get_SAP_vecxz, polygon_area_3d
from ..model.mesh_model import MeshModel
from ..model.sap_data import (
    FrameElement,
    Node,
    ShellSection,
)
from ..model.tree_utils import collect_descendants
from ..utils import (
    DEFAULT_E_S_PA,
    DEFAULT_FSAM_CONC_EPCC,
    DEFAULT_FSAM_CONC_ET,
    DEFAULT_FSAM_CONC_FPC_PA,
    DEFAULT_FSAM_CONC_FT_PA,
    DEFAULT_FSAM_CONC_RC,
    DEFAULT_FSAM_CONC_RT,
    DEFAULT_FSAM_CONC_XCRN,
    DEFAULT_FSAM_CONC_XCRP,
    DEFAULT_FSAM_STEEL_B,
    DEFAULT_FSAM_STEEL_CR1,
    DEFAULT_FSAM_STEEL_CR2,
    DEFAULT_FSAM_STEEL_R0,
    DEFAULT_FY_REBAR_PA,
    RC_NO_TIE_CONFINEMENT_FACTOR,
    RC_NO_TIE_EPSC_FACTOR,
    cqc_combine,
    g_from_units,
    stress_scale_factor,
)

logger = logging.getLogger(__name__)


class AnalysisBuilder:
    """Create and analyse an OpenSees model from a prepared MeshModel.

    Usage::

        builder = AnalysisBuilder(mesh_model, config)
        builder.build_domain()
        builder.create_loads({"DEAD": 1.0})
        results = builder.run_static_analysis()

    Args:
        mesh_model: Prepared topology from the Preprocessor.
        config: Configuration dict (same keys as
            :class:`~fea_toolkit.opensees.builder.OpenSeesBuilder`).
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

        # Pushover step results (populated by run_pushover_analysis)
        self.pushover_step_results: list[dict[str, Any]] = []

        # Domain state (built during build_domain)
        self.frame_tag_map: dict[str, int] = {}
        self.material_tags: dict[str, int] = dict(mesh_model.material_tags)
        self.section_tags: dict[str, int] = dict(mesh_model.section_tags)
        self._shell_sec_tags: dict[str, int] = dict(mesh_model.shell_sec_tags)
        self._shell_sec_variants: dict[str, int] = dict(mesh_model.shell_sec_variants)
        self._frame_element_types: dict[str, str] = dict(mesh_model.frame_element_types)
        self._area_element_types: dict[str, str] = dict(mesh_model.area_element_types)
        self._offset_rigid_links: list[tuple] = list(mesh_model.offset_rigid_links)
        self._edge_constraint_method: Optional[str] = None
        # NOTE: mesh_model.edge_constraint_args is always [] today
        # (the Preprocessor stores detected pairs in detected_edge_pairs,
        # not constraint arguments).  The list is overwritten when
        # apply_edge_constraints() is first called at analysis time.
        self._saved_edge_constraints: list[tuple] = list(mesh_model.edge_constraint_args)
        self.edge_loads_from_areas: list = list(mesh_model.edge_loads_from_areas)
        self._base_z = mesh_model.base_z

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
            # model in ``fea_toolkit.analysis.shear_capacity``.
            "shear_backbone": None,
            # Element type used by the fiber-section pushover rebuild
            # (rebuild_with_fiber_sections).  Defaults to dispBeamColumn.
            "fiber_element_type": "dispBeamColumn",
            # MPC-based rigid links (ops.rigidLink) for frame end offsets,
            # instead of very stiff elasticBeamColumn segments.  Avoids the
            # ill-conditioning of stiff elastic links under PDelta pushover
            # (those fail to converge at the gravity stage).
            "rigid_link_mpc": False,
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

    # ── nD materials / layered shell sections ──────────────────────

    def _create_nd_materials(self) -> None:
        """Create nD (multi‑axial) materials for nonlinear layered shell analysis.

        Reads ``mesh_model.nd_materials`` (populated by the Preprocessor from
        the config's ``nd_materials`` dict) and creates the corresponding
        OpenSees ``nDMaterial`` objects.

        Supports the following OpenSees nD material types:

        * ``ElasticIsotropic`` — linear elastic 2D plane-stress
        * ``J2PlateFibre`` — J2 plasticity with isotropic/kinematic hardening
          (used for smeared rebar layers)
        * ``ConcreteS`` — concrete with compressive strength ``fc`` and tensile
          strength ``ft`` (fixed crack model)
        * ``PlateFromPlaneStress`` — wraps a plane-stress material into a
          plate bending formulation

        Material tags are assigned starting from a base offset above all
        existing material, section, and frame element tags to avoid clashes.

        This method is called during :meth:`build_domain` **before**
        :meth:`_create_materials` so that nD material tags are available
        when layered shell sections are created in
        :meth:`_create_layered_shell_sections`.

        .. note::

           When ``mesh_model.nd_materials`` is empty (no config set), this
           method is a no-op — existing behaviour is unchanged.
        """
        if not self.mesh_model.nd_materials:
            return

        import warnings as _w

        _max_mat = max(self.material_tags.values(), default=0)
        _max_sec = max(self.section_tags.values(), default=0)
        _max_frame = max(self.frame_tag_map.values(), default=0)
        _tag_base = max(_max_mat, _max_sec, _max_frame) + 1000
        # Also start above any tag already stored in the dedicated
        # _nd_material_tags namespace so newly created materials cannot
        # collide with tags from a previous build (e.g. after materials
        # were removed from the config).
        _nd_existing = getattr(self, "_nd_material_tags", {})
        if _nd_existing:
            _tag_base = max(_tag_base, max(_nd_existing.values()) + 1)
        tag = _tag_base

        created = 0
        for name, nd_mat in self.mesh_model.nd_materials.items():
            t = nd_mat.material_type

            # Reuse existing tag if already assigned for this name,
            # keeping tags stable across repeated build_domain calls.
            # Use dedicated _nd_material_tags namespace so nD material
            # names do not collide with uniaxial material names.
            if name in getattr(self, "_nd_material_tags", {}):
                current_tag = self._nd_material_tags[name]
                is_new = False
            else:
                current_tag = tag
                is_new = True

            if t == "ElasticIsotropic":
                ops.nDMaterial("ElasticIsotropic", current_tag, nd_mat.E, nd_mat.nu)
            elif t == "J2PlateFibre":
                ops.nDMaterial(
                    "J2PlateFibre",
                    current_tag,
                    nd_mat.E,
                    nd_mat.nu,
                    nd_mat.fy,
                    nd_mat.Hiso,
                    nd_mat.Hkin,
                )
            elif t == "ConcreteS":
                ops.nDMaterial(
                    "ConcreteS", current_tag, nd_mat.E, nd_mat.nu, nd_mat.fc, nd_mat.ft, nd_mat.Es
                )
            elif t == "PlateFromPlaneStress":
                # Two OpenSees objects are required to use the smeared-crack
                # concrete model in a LayeredShell section:
                #
                #   1. PlaneStressUserMaterial — the plane-stress damage /
                #      smeared-crack constitutive law (tension cracking,
                #      compression crushing, shear retention).
                #   2. PlateFromPlaneStress — wraps the plane-stress
                #      material into a plate (adds out-of-plane stiffness)
                #      so it can be stacked in ops.section('LayeredShell').
                #
                # The wrapper tag is what the layered-shell section
                # references; the base tag is created first and stays
                # one below the stored wrapper tag.
                if is_new:
                    ps_tag = current_tag
                    wrapper_tag = current_tag + 1
                else:
                    # Reusing a previously assigned tag for this name:
                    # the wrapper tag is the stored one and the base is
                    # one below.
                    wrapper_tag = current_tag
                    ps_tag = current_tag - 1
                ops.nDMaterial(
                    "PlaneStressUserMaterial",
                    ps_tag,
                    nd_mat.nstatevs,
                    nd_mat.nprops,
                    nd_mat.fc,
                    nd_mat.ft,
                    -abs(nd_mat.fcu),
                    -abs(nd_mat.epsc0),
                    -abs(nd_mat.epscu),
                    nd_mat.epstu,
                    nd_mat.stc,
                )
                eout = (
                    nd_mat.Eout
                    if nd_mat.Eout is not None
                    else (nd_mat.E / (2.0 * (1.0 + nd_mat.nu)) if nd_mat.nu else nd_mat.E / 2.6)
                )
                ops.nDMaterial("PlateFromPlaneStress", wrapper_tag, ps_tag, eout)

                if not hasattr(self, "_nd_material_tags"):
                    self._nd_material_tags = {}
                self._nd_material_tags[name] = wrapper_tag
                if is_new:
                    # Two tags consumed (base + wrapper).
                    tag += 2
                    while tag in self._nd_material_tags.values():
                        tag += 1
                    created += 1
                continue
            elif t == "FSAM":
                # FSAM creation is deferred to _create_fsam_materials(),
                # which runs after _create_materials() so the uniaxial
                # steel/concrete material tags exist to reference.
                continue
            else:
                _w.warn(
                    f"Unknown nDMaterial type '{t}' for '{name}' — skipping",
                    UserWarning,
                    stacklevel=2,
                )
                self._skipped_nd_materials.add(name)
                continue

            if not hasattr(self, "_nd_material_tags"):
                self._nd_material_tags = {}
            self._nd_material_tags[name] = current_tag
            if is_new:
                tag += 1
                # Advance past any occupied tags (safety net — new tags
                # normally start above all existing ones).
                while tag in self._nd_material_tags.values():
                    tag += 1
                created += 1

        if self.config.get("verbose", False):
            print(f"  Created {created} nD material(s)")

    def _create_fsam_materials(self) -> None:
        """Create FSAM (fixed-strut-angle model) nD materials.

        FSAM is a smeared fixed-strut-angle concrete model used with
        nonlinear shear walls (``SFI_MVLEM_3D`` / ``LayeredShell``
        sections).  Its OpenSees command references three **uniaxial**
        material tags for the steel and concrete laws::

            nDMaterial FSAM $tag $rho $sX $sY $conc $rouX $rouY $nu $alfadow

        where ``sX`` / ``sY`` / ``conc`` are uniaxial material tags
        resolved from the names in :attr:`NDMaterial.sx` /
        :attr:`NDMaterial.sy` / :attr:`NDMaterial.conc`.  Because those
        uniaxial materials are created by :meth:`_create_materials`, this
        method runs **after** it (see :meth:`build_domain`) and **before**
        :meth:`_create_layered_shell_sections`, which consumes the FSAM
        tag.

        The concrete uniaxial law must implement ``getCrackingStrain()``
        (e.g. ``ConcreteCM``); ``ConcreteS`` / ``Concrete01`` do not and
        will fail at runtime.

        The FSAM tag is stored in ``_nd_material_tags`` (keyed by the
        nD material name) so layered shell sections can reference it via
        :meth:`_create_layered_shell_sections`.
        """
        fsam_mats = {
            n: m for n, m in self.mesh_model.nd_materials.items() if m.material_type == "FSAM"
        }
        if not fsam_mats:
            return

        import warnings as _w

        _max_mat = max(self.material_tags.values(), default=0)
        _max_sec = max(self.section_tags.values(), default=0)
        _max_frame = max(self.frame_tag_map.values(), default=0)
        _tag_base = max(_max_mat, _max_sec, _max_frame) + 1000
        # Also start above any tag already stored in the dedicated
        # _nd_material_tags namespace so newly created materials cannot
        # collide with tags from a previous build.
        _nd_existing = getattr(self, "_nd_material_tags", {})
        if _nd_existing:
            _tag_base = max(_tag_base, max(_nd_existing.values()) + 1)
        tag = _tag_base

        if not hasattr(self, "_nd_material_tags"):
            self._nd_material_tags = {}
        created = 0
        for name, nd_mat in fsam_mats.items():
            # Reuse existing tag if already assigned for this name,
            # keeping tags stable across repeated build_domain calls.
            if name in self._nd_material_tags:
                current_tag = self._nd_material_tags[name]
            else:
                current_tag = tag
                tag += 1
                while tag in self._nd_material_tags.values():
                    tag += 1
                created += 1

            missing = sorted(
                n for n in (nd_mat.sx, nd_mat.sy, nd_mat.conc) if n not in self.material_tags
            )
            if missing:
                _w.warn(
                    f"FSAM material '{name}' references uniaxial material(s) "
                    f"{missing} not present in the model materials — skipping",
                    UserWarning,
                    stacklevel=2,
                )
                self._skipped_nd_materials.add(name)
                continue

            ops.nDMaterial(
                "FSAM",
                current_tag,
                nd_mat.density,
                self.material_tags[nd_mat.sx],
                self.material_tags[nd_mat.sy],
                self.material_tags[nd_mat.conc],
                nd_mat.rou_x,
                nd_mat.rou_y,
                nd_mat.nu,
                nd_mat.alfadow,
            )
            self._nd_material_tags[name] = current_tag

        if self.config.get("verbose", False):
            print(f"  Created {created} FSAM nD material(s)")

    def _create_mvlem3d_support_materials(self) -> None:
        """Create the MVLEM_3D shear-spring and interior-dummy materials.

        MVLEM_3D fibres reference a single horizontal shear spring plus
        (usually) a tiny-E elastic dummy steel for fibres without rebar.
        The framework materials are ordinary ``Material`` dataclasses, but
        the OpenSees domain needs ``ElasticPP`` for the shear spring (k,
        yield-strain) and a near-zero-stiffness ``Elastic`` for the dummy.

        Tag stability: the material names are already in
        ``self.material_tags`` (auto-assigned in ``_create_materials``);
        the correct law is emitted over the same tag, so repeated
        ``build_domain()`` calls reuse identical tags.
        """
        ssf = stress_scale_factor(self.mesh_model.units)
        for wall in self.mesh_model.wall_elements.values():
            if wall.material_type != "uniaxial":
                continue
            # ── Shear spring: ElasticPP (k, epsP) ────────────────
            if wall.shear_name:
                tag = self.material_tags.get(wall.shear_name)
                if tag is None:
                    continue
                shear_mat = self.mesh_model.materials.get(wall.shear_name)
                # k = 0.1 × G_cracked × A / h (same recipe as the probe)
                Ec = (shear_mat.E_mod if shear_mat else None) or 30.0e6 * ssf
                Gc = 0.4 * Ec
                # A = sum(width) × mean(thick); h = wall height (max z - min z)
                A = sum(wall.width) * (sum(wall.thick) / max(len(wall.thick), 1))
                zs = [
                    self.mesh_model.nodes[nid].z
                    for nid in wall.node_ids
                    if nid in self.mesh_model.nodes
                ]
                h = (max(zs) - min(zs)) if zs else 1.0
                k_shear = 0.1 * Gc * A / max(h, 1e-12)
                with contextlib.suppress(Exception):
                    ops.uniaxialMaterial("ElasticPP", tag, k_shear, 1.0e6)
            # ── Interior dummy steel: tiny-E Elastic ─────────────
            if wall.dummy_name:
                tag = self.material_tags.get(wall.dummy_name)
                if tag is None:
                    continue
                with contextlib.suppress(Exception):
                    ops.uniaxialMaterial("Elastic", tag, 1.0e-3)

    def _create_wall_elements(self) -> None:
        """Create wall macro-elements from MeshModel.wall_elements.

        Dispatches on each :class:`~fea_toolkit.model.mesh_model.WallElement`
        ``element_type`` / ``material_type``:

        * ``SFI_MVLEM_3D`` / ``E_SFI_MVLEM_3D`` — per-fibre FSAM nD
          materials::

              element <TYPE> eleTag iNode jNode kNode lNode m \\
                  -thick *t -width *w -mat *matTags <-CoR c> ...

        * ``MVLEM_3D`` — per-fibre uniaxial concrete + steel + shear::

              element MVLEM_3D eleTag iNode jNode kNode lNode m \\
                  -thick *t -width *w -rho *rho \\
                  -matConcrete *concTags -matSteel *steelTags \\
                  -matShear shearTag <-CoR c> ...

        Node IDs are resolved to tags via ``mesh_model.nodes``.  FSAM
        names resolve via ``_nd_material_tags`` (populated by
        :meth:`_create_fsam_materials`); uniaxial names via
        ``self.material_tags``.  This method runs **after**
        :meth:`_create_fsam_materials` and **before**
        :meth:`_create_shell_elements`.
        """
        if not self.mesh_model.wall_elements:
            return

        _nd_tags = getattr(self, "_nd_material_tags", {})
        created = 0
        for wall in self.mesh_model.wall_elements.values():
            elem_type = wall.element_type or wall.material_type

            # Resolve node IDs → tags
            node_tags = []
            skip = False
            for nid in wall.node_ids:
                node = self.mesh_model.nodes.get(nid)
                if node is None:
                    if self.config.get("verbose", False):
                        print(
                            f"  ⚠ Wall element '{wall.elem_id}': node "
                            f"'{nid}' not found in mesh — skipping"
                        )
                    skip = True
                    break
                node_tags.append(node.node_tag)
            if skip:
                continue

            if wall.material_type == "uniaxial":
                created += self._create_mvlem3d_wall(wall, node_tags, elem_type)
            else:
                created += self._create_fsam_wall(wall, node_tags, elem_type, _nd_tags)

        if self.config.get("verbose", False):
            print(f"  Created {created} wall element(s)")

    def _create_fsam_wall(self, wall, node_tags: list, elem_type: str, _nd_tags: dict) -> int:
        """Emit an SFI_MVLEM_3D / E_SFI_MVLEM_3D element (per-fibre FSAM)."""
        mat_tags = []
        for name in wall.fsam_material_names:
            tag = _nd_tags.get(name)
            if tag is None:
                print(
                    f"  ⚠ Wall element '{wall.elem_id}': FSAM nD material "
                    f"'{name}' not found in _nd_material_tags — skipping element"
                )
                return 0
            mat_tags.append(tag)

        args: list = [
            wall.elem_tag,
            *node_tags,
            wall.m,
            "-thick",
            *wall.thick,
            "-width",
            *wall.width,
            "-mat",
            *mat_tags,
            "-CoR",
            wall.CoR,
        ]
        if wall.ThickMod is not None:
            args.extend(["-ThickMod", wall.ThickMod])
        if wall.Poisson is not None:
            args.extend(["-Poisson", wall.Poisson])
        if wall.Density is not None:
            args.extend(["-Density", wall.Density])

        ops.element(elem_type, *args)
        if self.config.get("verbose", False):
            print(f"  {elem_type} tag={wall.elem_tag} nodes={node_tags} m={wall.m}")
        return 1

    def _create_mvlem3d_wall(self, wall, node_tags: list, elem_type: str) -> int:
        """Emit an MVLEM_3D element (per-fibre uniaxial concrete/steel + shear)."""

        def _resolve(names) -> Optional[list]:
            tags = []
            for name in names or []:
                tag = self.material_tags.get(name)
                if tag is None:
                    return None
                tags.append(tag)
            return tags

        conc_tags = _resolve(wall.concrete_names)
        steel_tags = _resolve(wall.steel_names)
        shear_tag = self.material_tags.get(wall.shear_name) if wall.shear_name else None
        rho = wall.rho or [2400.0] * wall.m
        if conc_tags is None or steel_tags is None or shear_tag is None:
            print(
                f"  ⚠ Wall element '{wall.elem_id}': missing uniaxial material "
                "tag (concrete/steel/shear) — skipping element"
            )
            return 0

        args: list = [
            wall.elem_tag,
            *node_tags,
            wall.m,
            "-thick",
            *wall.thick,
            "-width",
            *wall.width,
            "-rho",
            *rho,
            "-matConcrete",
            *conc_tags,
            "-matSteel",
            *steel_tags,
            "-matShear",
            shear_tag,
            "-CoR",
            wall.CoR,
        ]
        if wall.ThickMod is not None:
            args.extend(["-ThickMod", wall.ThickMod])
        if wall.Poisson is not None:
            args.extend(["-Poisson", wall.Poisson])
        if wall.Density is not None:
            args.extend(["-Density", wall.Density])

        ops.element(elem_type, *args)
        if self.config.get("verbose", False):
            print(f"  {elem_type} tag={wall.elem_tag} nodes={node_tags} m={wall.m}")
        return 1

    def _create_layered_shell_sections(self) -> None:
        """Create ``LayeredShell`` sections for nonlinear shell analysis.

        Reads ``mesh_model.layered_shell_sections`` (populated by the
        Preprocessor from the config's ``shell_layers`` dict) and calls
        ``ops.section('LayeredShell', tag, nLayer, matTag1, t1, ...)``
        for each section.

        Each layer's nD material must already exist in ``self.material_tags``
        (created by :meth:`_create_nd_materials`).  Section tags are stored
        in ``self._shell_sec_tags`` keyed by section name, making them
        available for :meth:`_create_shell_elements` to reference when
        creating shell elements with layered sections.

        .. important::

           This method always recreates the ``ops.section()`` domain
           object for every mapped section, even when the tag already
           appears in ``self._shell_sec_tags``.  This is necessary
           because :meth:`build_domain` calls ``ops.wipe()`` at the
           start of each build, which destroys all previously created
           section objects — they must be re-registered with the new
           domain.  Section tags remain stable across builds.

        The LayeredShell section is used with ShellNLDKGQ or ShellDKGQ
        elements for nonlinear shear wall analysis where through-thickness
        layering of concrete and rebar is needed.  Each layer is defined by:

        * ``matTag`` — reference to an nD material tag
        * ``thickness`` — layer thickness (same units as model)

        .. note::

           OpenSees ``LayeredShell`` section syntax takes only
           ``(matTag, thickness)`` pairs per layer — the ``nIP``
           argument found in the ``ShellFiberLayer`` dataclass is
           metadata for display purposes only.

        Typical wall cross-section stacking (outside → inside):

        1. Cover concrete (unconfined), e.g. 40 mm, ``ConcreteS``
        2. Smeared rebar, e.g. 2 mm, ``J2PlateFibre``
        3. Core concrete (confined), e.g. 300 mm, ``ConcreteS``
        4. Smeared rebar, e.g. 2 mm, ``J2PlateFibre``
        5. Cover concrete (unconfined), e.g. 40 mm, ``ConcreteS``

        .. note::

           When ``mesh_model.layered_shell_sections`` is empty, this method
           is a no-op — existing behaviour is unchanged.
        """
        if not self.mesh_model.layered_shell_sections:
            return

        _max_sec = max(self.section_tags.values(), default=0)
        _max_shell = max(self._shell_sec_tags.values(), default=0)
        next_tag = max(_max_sec, _max_shell) + 1

        created = 0
        for name, lss in self.mesh_model.layered_shell_sections.items():
            n_layers = len(lss.layers)
            if n_layers == 0:
                if self.config.get("verbose", False):
                    print(f"  ⚠ Layered section '{name}' has no layers — skipping")
                continue
            flat_args = []
            skip_section = False
            for layer in lss.layers:
                # Check skipped set first — this catches stale tags from
                # a previous build where a now-skipped material still has
                # a tag in self.material_tags.
                if layer.nd_material in self._skipped_nd_materials:
                    print(
                        f"  ⚠ nD material '{layer.nd_material}' for "
                        f"layered section '{name}' was skipped during "
                        f"material creation (unsupported type) — "
                        f"skipping section '{name}'"
                    )
                    skip_section = True
                    break
                mat_tag = self.material_tags.get(layer.nd_material)
                if mat_tag is None:
                    # Also check dedicated nD material tag namespace
                    nd_tags = getattr(self, "_nd_material_tags", {})
                    mat_tag = nd_tags.get(layer.nd_material)
                if mat_tag is None:
                    if self.config.get("verbose", False):
                        print(
                            f"  ⚠ nD material '{layer.nd_material}' not found "
                            f"for layered section '{name}' — skipping section"
                        )
                    skip_section = True
                    break
                # LayeredShell syntax: matTag, thickness only (nIP not accepted)
                flat_args.extend([mat_tag, layer.thickness])
            if skip_section:
                self._skipped_shell_sec_names.add(name)
                continue

            # Reuse pre-assigned tag if one exists (stable across builds)
            if name in self._shell_sec_tags:
                tag = self._shell_sec_tags[name]
            else:
                tag = next_tag
                self._shell_sec_tags[name] = tag
                next_tag += 1

            ops.section("LayeredShell", tag, n_layers, *flat_args)
            created += 1

        if self.config.get("verbose", False):
            print(f"  Created {created} layered shell section(s)")

    # ── Materials ────────────────────────────────────────────────

    def _create_materials(self) -> None:
        """Create OpenSees materials.

        Assigns material tags sequentially if not already populated in
        ``self.material_tags``.  Creates elastic materials for all
        referenced materials (needed for section creation), plus
        nonlinear materials for fiber sections and brace trusses.
        """
        # Auto-assign material tags
        next_tag = max(self.material_tags.values(), default=0) + 1 if self.material_tags else 1
        for mat_name, mat in self.mesh_model.materials.items():
            if mat_name not in self.material_tags:
                self.material_tags[mat_name] = next_tag
                next_tag += 1

        # Create uniaxial materials for all materials.
        #
        # Two creation modes are supported:
        #
        #   1. Elastic (default) — for regular frame/brace materials.
        #   2. FSAM uniaxial laws — when the material is referenced by an
        #      FSAM nD material (via NDMaterial.sx/sy/conc), the required
        #      ConcreteCM / Steel02 law is emitted instead so FSAM can
        #      resolve getCrackingStrain() at runtime.  This is opt-in per
        #      FSAM reference — non-FSAM materials stay Elastic.
        #
        # Determine which material names are used by brace-truss sections so
        # we can skip Elastic creation for them (the Hysteretic material
        # replaces the Elastic at a distinct tag, but creating both is wasteful).
        _brace_mat_names: set = set()
        if self.config.get("brace_truss"):
            from ..model.sap_data import (
                AngleSection,
                ChannelSection,
                DoubleAngleSection,
                PipeSection,
                TeeSection,
            )

            brace_sec_types = (
                PipeSection,
                AngleSection,
                DoubleAngleSection,
                TeeSection,
                ChannelSection,
            )
            explicit = self.config.get("brace_sections")
            for sec_name, sec in self.mesh_model.sections.items():
                if explicit is not None:
                    if sec_name not in explicit:
                        continue
                elif not isinstance(sec, brace_sec_types):
                    continue
                _brace_mat_names.add(sec.material)

        # ── FSAM-referenced uniaxial materials ─────────────────────
        # Collect the set of material names that any FSAM nD material
        # references as its steel (sx/sy) or concrete (conc) law.  These
        # receive ConcreteCM / Steel02 below instead of the generic
        # Elastic fallback, because FSAM requires getCrackingStrain().
        _fsam_refs_by_name: dict[str, set[str]] = {}
        for _nd in self.mesh_model.nd_materials.values():
            if _nd.material_type != "FSAM":
                continue
            for _rname in _nd.fsam_referenced_material_names():
                _fsam_refs_by_name.setdefault(_rname, set()).add(_nd.name)

        # MVLEM_3D wall elements reference uniaxial concrete + steel
        # per fibre (plus a shear spring / dummy steel handled in
        # _create_wall_uniaxial_materials).  The concrete must be a
        # genuine nonlinear law and the steel a Steel02, so route them
        # through the same ConcreteCM / Steel02 emission used by FSAM.
        #
        # NOTE (August 2026 calibration study, local/probe_mvlem_cm_ratio.py):
        # the MVLEM_3D axial softness under corner-node pre-load is a
        # geometric/kinematic property of the macro-element, NOT a
        # ConcreteCM material-tangent bug.  Verified: uz_cm ∝ 1/H
        # (uz·H = 0.08 m² constant), a pure-axial load produces lateral
        # drift, and scaling the ConcreteCM input Ec (26.8×) changes
        # uz_cm bit-for-bit not at all.  Concrete01 is unusable too (its
        # zero-tangent tension branch makes the macro-element singular).
        # Keep the default ConcreteCM — no material-calibration knob can
        # change the axial response (see docs/mvlem_wall_analysis.md §7.1).
        _mvlem01_only: set = set()
        self._wall_uniaxial_special_names: set = set()
        _mvlem_concrete_law = self.config.get("mvlem_3d_concrete_law", "ConcreteCM")
        for _wall in self.mesh_model.wall_elements.values():
            if _wall.material_type != "uniaxial":
                continue
            for _rname in _wall.concrete_names or []:
                _fsam_refs_by_name.setdefault(_rname, set()).add("<mvlem3d>")
            for _rname in _wall.steel_names or []:
                _fsam_refs_by_name.setdefault(_rname, set()).add("<mvlem3d>")
            if _wall.shear_name:
                self._wall_uniaxial_special_names.add(_wall.shear_name)
            if _wall.dummy_name:
                self._wall_uniaxial_special_names.add(_wall.dummy_name)
            if _mvlem_concrete_law == "Concrete01":
                _mvlem01_only.update(_wall.concrete_names or [])
        # Invert: only names that are referenced by at least one MVLEM_3D
        # wall (not by a genuine FSAM nD law) qualify for Concrete01.
        # A configured-but-unconsumed FSAM nD material does NOT force
        # ConcreteCM — only FSAM materials that are actually created
        # (referenced by a LayeredShell section layer or an
        # SFI_MVLEM_3D / E_SFI_MVLEM_3D wall element) participate, because
        # ConcreteCM is required for FSAM's getCrackingStrain() at runtime.
        _fsam_consumed: set = set()
        for _lss in self.mesh_model.layered_shell_sections.values():
            for _layer in _lss.layers:
                _fsam_consumed.add(_layer.nd_material)
        for _wall in self.mesh_model.wall_elements.values():
            if _wall.material_type == "uniaxial":
                continue
            _fsam_consumed.update(_wall.fsam_material_names or [])
        _mvlem01_and_fsam: set = set()
        for _nd in self.mesh_model.nd_materials.values():
            if _nd.material_type != "FSAM" or _nd.name not in _fsam_consumed:
                continue
            for _rname in _nd.fsam_referenced_material_names():
                if _rname in _mvlem01_only:
                    _mvlem01_and_fsam.add(_rname)
        _mvlem01_only -= _mvlem01_and_fsam
        self._mvlem01_only = _mvlem01_only
        self._mvlem_concrete_law = _mvlem_concrete_law

        ssf = stress_scale_factor(self.mesh_model.units)

        for mat_name, mat in self.mesh_model.materials.items():
            tag = self.material_tags.get(mat_name)
            if tag is None:
                continue
            E_mod = mat.E_mod or 200e9
            if mat_name in _brace_mat_names:
                continue  # will be created as Hysteretic in brace-truss section
            if mat_name in _fsam_refs_by_name:
                # FSAM concrete law (ConcreteCM) — must implement
                # getCrackingStrain().  Config keys are in SI (Pa) and
                # scaled to model units here.
                if mat.type and "concrete" in mat.type.lower():
                    fpc = self.config.get("fsam_conc_fpc_override")
                    fpc = (
                        fpc * ssf if fpc is not None else (mat.Fc or DEFAULT_FSAM_CONC_FPC_PA * ssf)
                    )
                    # Tension strength is a concrete property — always use
                    # the framework default (or explicit override) rather
                    # than mat.Fy, which is a steel yield strength.
                    ft = self.config.get("fsam_conc_ft_override")
                    ft = ft * ssf if ft is not None else DEFAULT_FSAM_CONC_FT_PA * ssf
                    # ConcreteCM uses the negative-compression convention:
                    # fpc, epcc, and xcrn must be NEGATIVE (compression
                    # stresses/strains are negative in the damaged-concrete
                    # uniaxial law).  Positive magnitudes break the FSAM
                    # damage-coefficient initialisation when an
                    # SFI_MVLEM_3D element consumes the material at
                    # domain-build time ("Damage Coefficient ErRoR !").
                    # Tension-side values (ft, et, rt, xcrp) stay positive.
                    epcc = -abs(float(self.config.get("fsam_conc_epcc", DEFAULT_FSAM_CONC_EPCC)))
                    xcrn = -abs(float(self.config.get("fsam_conc_xcrn", DEFAULT_FSAM_CONC_XCRN)))
                    # Opt-in Concrete01 for pure MVLEM_3D references:
                    # epsc0 = 2fc/Ec makes E0 = Ec exactly.  This is a
                    # documented dead-end for MVLEM_3D (Concrete01's
                    # zero-tangent tension branch makes the macro-element
                    # singular — the section goes singular whenever any
                    # fibre goes into tension; see
                    # docs/mvlem_wall_analysis.md §7.1 and
                    # local/probe_mvlem_cm_ratio.py).  Kept as an
                    # accepted-but-discouraged option.
                    if mat_name in getattr(self, "_mvlem01_only", set()):
                        epcc_v = -abs(
                            float(self.config.get("fsam_conc_epcc", DEFAULT_FSAM_CONC_EPCC))
                        )
                        fpc_v = -abs(fpc)
                        with contextlib.suppress(Exception):
                            ops.uniaxialMaterial(
                                "Concrete01",
                                tag,
                                fpc_v,
                                epcc_v,
                                0.2 * abs(fpc),
                                epcc_v * 8.0,
                            )
                        continue
                    # Default ConcreteCM emission.  The input Ec is
                    # intentionally NOT scaled: the calibration study
                    # (local/probe_mvlem_cm_ratio.py) proved the MVLEM_3D
                    # axial stiffness is independent of the concrete Ec
                    # (a 26.8× input scaling changed uz_cm not at all), so
                    # any mvlem_3d_ec_factor knob would be inert.
                    with contextlib.suppress(Exception):
                        ops.uniaxialMaterial(
                            "ConcreteCM",
                            tag,
                            -abs(fpc),
                            epcc,
                            E_mod,
                            float(self.config.get("fsam_conc_rc", DEFAULT_FSAM_CONC_RC)),
                            xcrn,
                            ft,
                            float(self.config.get("fsam_conc_et", DEFAULT_FSAM_CONC_ET)),
                            float(self.config.get("fsam_conc_rt", DEFAULT_FSAM_CONC_RT)),
                            float(self.config.get("fsam_conc_xcrp", DEFAULT_FSAM_CONC_XCRP)),
                        )
                    continue
                # FSAM steel law (Steel02) — Fy/Es resolve in priority
                # order: config override (SI Pa, scaled) → material Fy /
                # E_mod (already in model units) → framework defaults.
                Fy_fsam = self.config.get("fsam_steel_Fy_override")
                Es_fsam = self.config.get("fsam_steel_Es_override")
                if Fy_fsam is not None:
                    Fy_fsam = Fy_fsam * ssf
                else:
                    Fy_fsam = mat.Fy or DEFAULT_FY_REBAR_PA * ssf
                Es_fsam = Es_fsam * ssf if Es_fsam is not None else E_mod
                with contextlib.suppress(Exception):
                    ops.uniaxialMaterial(
                        "Steel02",
                        tag,
                        Fy_fsam,
                        Es_fsam,
                        float(self.config.get("fsam_steel_b", DEFAULT_FSAM_STEEL_B)),
                        float(self.config.get("fsam_steel_R0", DEFAULT_FSAM_STEEL_R0)),
                        float(self.config.get("fsam_steel_cR1", DEFAULT_FSAM_STEEL_CR1)),
                        float(self.config.get("fsam_steel_cR2", DEFAULT_FSAM_STEEL_CR2)),
                    )
                continue
            if mat_name in self._wall_uniaxial_special_names:
                # MVLEM_3D shear spring / interior-dummy steel — emitted by
                # _create_mvlem3d_support_materials() with the correct law
                # (ElasticPP shear spring, tiny-E Elastic dummy) rather than
                # the generic elastic fallback.
                continue
            # May already exist on rebuild — suppress the OpenSees error.
            with contextlib.suppress(Exception):
                ops.uniaxialMaterial("Elastic", tag, E_mod)

        # MVLEM_3D support materials (shear spring + interior dummy steel)
        self._create_mvlem3d_support_materials()

        # Fiber section materials
        if self.config.get("create_fiber_sections"):
            from ..model.sap_data import (
                AngleSection,
                ChannelSection,
                DoubleAngleSection,
                PipeSection,
                TeeSection,
            )

            for sec_name, sec in self.mesh_model.sections.items():
                mat_name = sec.material
                mat_tag = self.material_tags.get(mat_name)
                if mat_tag is None:
                    continue
                # Section-specific nonlinear materials created by
                # sec.to_fiber_patches(mat_tag, ...) in _create_single_section

        # Brace truss materials
        if self.config.get("brace_truss"):
            from ..model.sap_data import (
                AngleSection,
                ChannelSection,
                DoubleAngleSection,
                PipeSection,
                TeeSection,
            )

            brace_types = (
                PipeSection,
                AngleSection,
                DoubleAngleSection,
                TeeSection,
                ChannelSection,
            )
            self._truss_mat_tags: dict[str, int] = {}
            self._truss_areas: dict[str, float] = {}
            self._truss_Fy: dict[str, float] = {}
            self._truss_E: dict[str, float] = {}
            self._truss_mat_counter: int = 100000
            # Use tags beyond both material AND section tags to avoid clashes
            # with fiber-section materials created in _create_single_section
            # (which use mat_tag = section_tag).
            _existing = max(
                max(self.material_tags.values(), default=0),
                max(self.section_tags.values(), default=0),
            )
            truss_tag = _existing + 1

            explicit = self.config.get("brace_sections")
            for sec_name, sec in self.mesh_model.sections.items():
                if explicit is not None:
                    if sec_name not in explicit:
                        continue
                elif not isinstance(sec, brace_types):
                    continue
                area = getattr(sec, "A", 0.0) or 0.0
                if area < 1e-12:
                    continue
                mat = self.mesh_model.materials.get(sec.material)
                E_sec = mat.E_mod if mat else 200e9
                Fy = getattr(sec, "Fy", None) or getattr(mat, "Fy", 250e6) if mat else 250e6

                self._truss_mat_tags[sec_name] = truss_tag
                self._truss_areas[sec_name] = area
                # Hysteretic material creation deferred to _add_beam_column
                # where the actual element length is known for buckling calc.
                self._truss_Fy[sec_name] = Fy
                self._truss_E[sec_name] = E_sec
                truss_tag += 1

    # ── Sections ─────────────────────────────────────────────────

    def _create_sections(self) -> None:
        """Create OpenSees sections from MeshModel sections.

        Assigns section tags sequentially if they are not already
        populated in ``self.section_tags`` (from MeshModel).
        """
        if self.config["verbose"]:
            print("Creating sections...")

        # Ensure normal-section tags don't collide with layered-shell tags
        _max_sec = max(self.section_tags.values(), default=0)
        _max_shell = max(self._shell_sec_tags.values(), default=0)
        next_tag = (
            max(_max_sec, _max_shell) + 1 if (self.section_tags or self._shell_sec_tags) else 1
        )
        for sec_name, sec in self.mesh_model.sections.items():
            if sec_name not in self.section_tags:
                self.section_tags[sec_name] = next_tag
                next_tag += 1
            tag = self.section_tags[sec_name]
            self._create_single_section(sec, tag)

    def _create_single_section(self, sec, tag: int) -> None:
        """Create a single OpenSees section."""
        mods = getattr(sec, "modifiers", {}) or {}
        # ── Fiber section path (frame sections only) ────────────
        if self.config.get("create_fiber_sections"):
            from ..model.sap_data import ShellSection as _ShellSec

            if not isinstance(sec, _ShellSec):
                mat = self.mesh_model.materials.get(sec.material)
                if mat is None:
                    E_mod = 200e9
                    G_mod = 80e9
                else:
                    E_mod = mat.E_mod or 200e9
                    G_mod = mat.G_mod or (E_mod / 2.6)

                _A = getattr(sec, "A", 0.0) or 0.0
                _I33 = getattr(sec, "I33", 0.0) or 0.0
                _I22 = getattr(sec, "I22", 0.0) or 0.0
                _J = getattr(sec, "J", 0.0) or 0.0

                # Create material(s) appropriate for the section type
                # Use a continuously incrementing counter so that every
                # fiber material gets a unique tag (tag-based offsets
                # collide when e.g. concrete sec 1 uses 500001-500003
                # and steel sec 2 tries 500002).
                if not hasattr(self, "_next_fiber_mat_tag"):
                    _max_all = max(
                        max(self.material_tags.values(), default=0),
                        max(self.section_tags.values(), default=0),
                        max(self._shell_sec_tags.values(), default=0),
                    )
                    self._next_fiber_mat_tag = max(_max_all, 1000000) + 1
                mat_tag = self._next_fiber_mat_tag
                self._next_fiber_mat_tag += (
                    3 if (mat is not None and mat.type.lower() == "concrete") else 1
                )
                if mat is not None and mat.type.lower() == "concrete":
                    # Concrete section: to_fiber_patches() uses three tags:
                    #   mat_tag     → unconfined concrete  (Concrete01)
                    #   mat_tag + 1 → confined core        (Concrete01)
                    #   mat_tag + 2 → steel rebar          (Steel02)
                    Fc = getattr(mat, "Fc", 0.0) or 3.0e7
                    epsc = getattr(mat, "eFc", 0.0) or 0.002
                    # Unconfined cover concrete
                    ops.uniaxialMaterial("Concrete01", mat_tag, -Fc, -abs(epsc), -0.2 * Fc, -0.006)
                    # Confined core concrete — use Mander confinement when
                    # tie data is present on the section, else fall back
                    # to the conventional no-tie-data heuristic defined by
                    # RC_NO_TIE_CONFINEMENT_FACTOR / RC_NO_TIE_EPSC_FACTOR
                    # (shared with the Tcl export in builder.py).
                    Fc_core = Fc * RC_NO_TIE_CONFINEMENT_FACTOR
                    epsc_core = abs(epsc) * RC_NO_TIE_EPSC_FACTOR
                    ecu_core = 0.02
                    tie_fy = getattr(sec, "tie_fy", None) or 0.0
                    if tie_fy <= 0:
                        # Attempt to resolve tie_fy from the tie rebar
                        # material (RebarMatT), then the longitudinal
                        # rebar material (RebarMatL) as a fallback.
                        tie_mat_name = getattr(sec, "tie_rebar_mat", None) or getattr(
                            sec, "rebar_material", None
                        )
                        tie_mat = (
                            self.mesh_model.materials.get(tie_mat_name) if tie_mat_name else None
                        )
                        if tie_mat is not None:
                            tie_fy = getattr(tie_mat, "Fy", 0.0) or 0.0
                    confinement = None
                    fc_method = getattr(sec, "fiber_confinement", None)
                    if callable(fc_method):
                        try:
                            confinement = fc_method(Fc, tie_fy)
                        except Exception as e:
                            import warnings

                            warnings.warn(f"fiber_confinement failed for section '{sec.name}': {e}")
                            confinement = None
                    if confinement is not None:
                        Fc_core = confinement.get("fcc", Fc_core)
                        epsc_core = confinement.get("ecc", epsc_core)
                        ecu_core = confinement.get("ecu", 0.02)
                    # Cap the confined spalling strain (configurable), but
                    # never clamp it below the strain at confined peak —
                    # an ecu below epsc_core would give a degenerate
                    # Concrete01 curve.  Apply the cap first, then enforce
                    # epsc_core as the absolute lower bound so a cap set
                    # below epsc_core still yields a valid curve.
                    _ecu_max = float(self.config.get("confined_ecu_max", 0.025))
                    ecu_core = max(min(ecu_core, _ecu_max), epsc_core)
                    ops.uniaxialMaterial(
                        "Concrete01", mat_tag + 1, -Fc_core, -epsc_core, -0.2 * Fc_core, -ecu_core
                    )
                    # Steel rebar — resolve Fy/Es in priority order:
                    #   1) config override (SI Pa, scaled to model units)
                    #   2) section's SAP2000 rebar_material (RebarMatL) lookup
                    #   3) framework rebar defaults (DEFAULT_FY_REBAR_PA / E_S)
                    #      scaled to model units
                    ssf = stress_scale_factor(self.mesh_model.units)
                    Fy_rebar = self.config.get("rebar_Fy_override")
                    Es_rebar = self.config.get("rebar_Es_override")
                    if Fy_rebar is not None:
                        Fy_rebar = Fy_rebar * ssf
                    if Es_rebar is not None:
                        Es_rebar = Es_rebar * ssf
                    if Fy_rebar is None or Es_rebar is None:
                        rebar_mat_name = getattr(sec, "rebar_material", None)
                        rebar_mat = (
                            self.mesh_model.materials.get(rebar_mat_name)
                            if rebar_mat_name
                            else None
                        )
                        if rebar_mat is not None:
                            rm_Fy = getattr(rebar_mat, "Fy", 0.0) or 0.0
                            rm_Es = getattr(rebar_mat, "E_mod", 0.0) or 0.0
                            if rm_Fy > 0:
                                Fy_rebar = Fy_rebar if Fy_rebar is not None else rm_Fy
                            if rm_Es > 0:
                                Es_rebar = Es_rebar if Es_rebar is not None else rm_Es
                    if not Fy_rebar:
                        Fy_rebar = DEFAULT_FY_REBAR_PA * ssf
                    if not Es_rebar:
                        Es_rebar = DEFAULT_E_S_PA * ssf
                    ops.uniaxialMaterial(
                        "Steel02",
                        mat_tag + 2,
                        Fy_rebar,
                        Es_rebar,
                        float(self.config.get("rebar_b", 0.01)),
                        float(self.config.get("rebar_R0", 18.0)),
                        float(self.config.get("rebar_cR1", 0.925)),
                        float(self.config.get("rebar_cR2", 0.15)),
                    )
                else:
                    Fy = getattr(mat, "Fy", 0.0) or 2.5e8
                    ops.uniaxialMaterial("Steel01", mat_tag, Fy, E_mod, 0.01)

                # Create fiber section (after to_fiber_patches succeeds)
                try:
                    entries = sec.to_fiber_patches(mat_tag=mat_tag, nfy=8, nfz=4)
                except NotImplementedError:
                    # Fall back to elastic — no Fiber section was created,
                    # so no tag collision with the Elastic replacement.
                    import warnings

                    warnings.warn(
                        f"Section '{sec.name}' (tag {tag}) does not support fiber "
                        f"patches — using elastic section instead. "
                        f"This may indicate a mixed steel/RC model where some "
                        f"sections lack fiber conversion.",
                        UserWarning,
                        stacklevel=2,
                    )
                    ops.section("Elastic", tag, E_mod, _A, _I33, _I22, G_mod, _J)
                    return

                # When shear aggregation is enabled the fiber section must
                # live at an internal tag so the SectionAggregator can take
                # the public ``tag`` (elements keep referencing ``tag``).
                fiber_tag = tag
                if self.config.get("aggregate_shear"):
                    fiber_tag = self._next_fiber_mat_tag
                    self._next_fiber_mat_tag += 1
                ops.section("Fiber", fiber_tag, "-GJ", _J)
                for entry in entries:
                    if entry[0] in ("rect", "circ", "quad"):
                        ops.patch(*entry)
                    elif entry[0] == "straight":
                        ops.layer("straight", *entry[1:])
                    elif entry[0] == "circ_layer":
                        ops.layer("circ", *entry[1:])

                # ── Shear-flexible aggregation (opt-in) ───────────
                if self.config.get("aggregate_shear"):
                    # Section shear DOFs are only engaged by flexibility-
                    # based elements; Euler-Bernoulli (dispBeamColumn /
                    # elasticBeamColumn) elements never compute section
                    # shear deformation, so aggregation would be silently
                    # inert.  Warn once per build.  The relevant element
                    # type is the fibre rebuild type (fiber_element_type),
                    # not the base elastic element_type.
                    _etype = self.config.get("fiber_element_type", "") or self.config.get(
                        "element_type", ""
                    )
                    if _etype != "forceBeamColumn" and not getattr(
                        self, "_warned_shear_element", False
                    ):
                        import warnings

                        warnings.warn(
                            f"aggregate_shear is set but fiber_element_type={_etype!r} does not "
                            "engage section shear DOFs (Euler-Bernoulli element). The "
                            "shear aggregation will have no effect — set "
                            "fiber_element_type='forceBeamColumn' so the pushover rebuild "
                            "uses a flexibility-based element.",
                            UserWarning,
                            stacklevel=2,
                        )
                        self._warned_shear_element = True
                    self._wrap_fiber_section_with_shear(tag, fiber_tag, G_mod, _A, sec=sec)

                if self.config.get("verbose", False):
                    print(f"  Section {tag}: {sec.name} (Fiber, {len(entries)} patches)")
                return  # fiber path done

        # ── Elastic section path (including ShellSections) ──────
        mat = self.mesh_model.materials.get(sec.material)
        if mat is None:
            if self.config.get("verbose", False):
                print(
                    f"  ⚠ Section {sec.name}: material '{sec.material}' not found, using defaults"
                )
            E_mod = 200e9
            G_mod = 80e9
        else:
            E_mod = mat.E_mod
            if mat.G_mod and mat.G_mod > 0:
                G_mod = mat.G_mod
            else:
                G_mod = E_mod / (2 * (1 + mat.nu)) if mat.nu else E_mod / 2.6

        _A = getattr(sec, "A", 0.0) or 0.0
        _I33 = getattr(sec, "I33", 0.0) or 0.0
        _I22 = getattr(sec, "I22", 0.0) or 0.0
        _J = getattr(sec, "J", 0.0) or 0.0

        # Stiffness modifiers
        amod = mods.get("AMod", 1.0)
        i33mod = mods.get("I3Mod", 1.0)
        i22mod = mods.get("I2Mod", 1.0)
        jmod = mods.get("JMod", 1.0)

        if self.config.get("use_elastic_sections", True):
            ops.section(
                "Elastic", tag, E_mod, _A * amod, _I33 * i33mod, _I22 * i22mod, G_mod, _J * jmod
            )

    def _wrap_fiber_section_with_shear(
        self, agg_tag: int, fiber_tag: int, G_mod: float, A: float, sec=None
    ) -> None:
        """Wrap a fiber section in a SectionAggregator with a shear response.

        Plain fiber sections are shear-rigid (Euler–Bernoulli): the
        ``dispBeamColumn`` / ``forceBeamColumn`` elements carrying them have
        no transverse-shear deformation.  For RC frames with stocky members
        where shear deformations are non-negligible (e.g. the Vecchio &
        Emara 1992 and Duong et al. 2007 benchmarks), this method wraps the
        fiber section in a ``section Aggregator`` that adds a uniaxial
        material on the section's ``Vy``/``Vz`` DOFs:

        * ``aggregate_shear = "elastic"`` (or ``True``) — an ``Elastic``
          material with rigidity :math:`GA_v = G_{mod} \\cdot (f \\cdot A)`
          where ``f`` is the shear-area factor (5/6 for rectangles).
        * ``aggregate_shear = "nonlinear"`` — a trilinear ``Hysteretic``
          material built from the simplified-MCFT backbone
          (:func:`fea_toolkit.analysis.shear_capacity.shear_backbone`):
          cracking → peak ``V_n`` → degrading → residual.  If the backbone
          cannot be derived (missing section/material), the elastic term is
          used as a fallback.

        Args:
            agg_tag: OpenSees tag of the SectionAggregator — the tag the
                frame elements reference.
            fiber_tag: OpenSees tag of the base fiber section.
            G_mod: Shear modulus in model units.
            A: Gross area of the section (model units²).
            sec: The model ``Section`` — used only by the nonlinear path to
                derive the MCFT backbone from the section/material data.
        """
        Av = float(self.config.get("shear_area_factor", 5.0 / 6.0)) * (A or 0.0)
        if (G_mod or 0.0) <= 0.0 or Av <= 0.0:
            return  # nothing to aggregate (degenerate material/area)
        GAv = G_mod * Av
        sh_tag = self._next_fiber_mat_tag
        self._next_fiber_mat_tag += 1

        use_nonlinear = self.config.get("aggregate_shear") == "nonlinear"
        bb = None
        if use_nonlinear:
            bb = self._derive_shear_backbone(sec)
            if bb is None:
                use_nonlinear = False  # fall back to the elastic term

        if use_nonlinear:
            ops.uniaxialMaterial(
                "Hysteretic",
                sh_tag,
                bb["v_cr"],
                bb["g_cr"],
                bb["v_n"],
                bb["g_n"],
                bb["v_r"],
                bb["g_r"],
                -bb["v_cr"],
                -bb["g_cr"],
                -bb["v_n"],
                -bb["g_n"],
                -bb["v_r"],
                -bb["g_r"],
                1.0,
                1.0,
                0.0,
                0.0,
            )
        else:
            ops.uniaxialMaterial("Elastic", sh_tag, GAv)
        ops.section("Aggregator", agg_tag, sh_tag, "Vy", sh_tag, "Vz", "-section", fiber_tag)

    def _derive_shear_backbone(self, sec) -> Optional[dict]:
        """Derive (or read) the nonlinear shear backbone for a section.

        ``config["shear_backbone"]`` may be an explicit dict (applied to
        every aggregated section); otherwise the simplified-MCFT backbone
        is derived per section from the mesh model's material data.  Returns
        ``None`` when no backbone can be produced.
        """
        from ..analysis.shear_capacity import shear_backbone

        if sec is None:
            return None
        materials = self.mesh_model.materials
        concrete = materials.get(getattr(sec, "material", ""))
        if concrete is None:
            return None
        override = self.config.get("shear_backbone")
        if isinstance(override, dict):
            return dict(override)
        rebar = materials.get(getattr(sec, "rebar_material", "") or "")
        tie = materials.get(getattr(sec, "tie_rebar_mat", "") or "")
        return shear_backbone(
            sec,
            concrete,
            rebar=rebar,
            tie=tie,
            units=self.units,
        )

    # ── Shell elements ───────────────────────────────────────────

    def _create_shell_elements(self) -> None:
        """Create ShellMITC4 elements from MeshModel area elements."""
        if not self.config.get("create_shells", False):
            return

        if self.config["verbose"]:
            print("Creating shell elements...")

        # Merge with existing _shell_sec_tags (from _create_layered_shell_sections)
        # so LayeredShell section tags are preserved, not overwritten.
        _new_ss = dict(self.mesh_model.shell_sec_tags) if self.mesh_model.shell_sec_tags else {}
        for k, v in _new_ss.items():
            self._shell_sec_tags.setdefault(k, v)

        _new_variants = (
            dict(self.mesh_model.shell_sec_variants) if self.mesh_model.shell_sec_variants else {}
        )
        for k, v in _new_variants.items():
            self._shell_sec_variants.setdefault(k, v)

        # Seed next_sec_tag from both section_tags and _shell_sec_tags values
        _all_section_vals = set(self.section_tags.values())
        _all_section_vals.update(self._shell_sec_tags.values())
        _all_section_vals.update(self._shell_sec_variants.values())
        next_sec_tag = max(_all_section_vals, default=0) + 1 if _all_section_vals else 1

        shell_count = 0
        loads_only = self.mesh_model.loads_only_area_ids
        for aid, area in self.mesh_model.area_elements.items():
            # Skip loads-only areas — they contribute mass but not stiffness
            if aid in loads_only:
                continue
            if getattr(area, "inactive", False):
                continue

            nids = area.node_ids
            if len(nids) < 3:
                continue

            # Gather node tags
            node_tags = []
            skip = False
            for nid in nids:
                node = self.mesh_model.nodes.get(nid)
                if node is None:
                    skip = True
                    break
                node_tags.append(node.node_tag)
            if skip:
                continue

            sec_name = self.mesh_model.area_assignments.get(aid, "")
            if not sec_name or sec_name not in self.mesh_model.sections:
                continue

            sec = self.mesh_model.sections[sec_name]
            # Skip areas that reference a skipped layered shell section
            # (e.g. the nD material was unsupported) — do NOT create an
            # ElasticMembranePlateSection fallback for them.
            if sec_name in self._skipped_shell_sec_names:
                continue

            mat = self.mesh_model.materials.get(sec.material)
            if mat is None:
                continue

            # Determine section tag
            area_etype = self._area_element_types.get(aid)
            if area_etype and self._get_type_factor(area_etype) != 1.0:
                variant_key = f"{sec_name}__{area_etype}"
                if variant_key not in self._shell_sec_variants:
                    tag = next_sec_tag
                    next_sec_tag += 1
                    self._shell_sec_variants[variant_key] = tag
                    self._create_single_shell_section(sec, mat, tag, etype=area_etype)
                sec_tag = self._shell_sec_variants[variant_key]
            else:
                if sec_name not in self._shell_sec_tags:
                    tag = next_sec_tag
                    next_sec_tag += 1
                    self._shell_sec_tags[sec_name] = tag
                    self._create_single_shell_section(sec, mat, tag)
                sec_tag = self._shell_sec_tags[sec_name]

            # Determine element tag — avoid clashing with frame elements
            max_frame_tag = max(self.frame_tag_map.values(), default=0)
            max_rigid_tag = (
                max(
                    (r[3] for r in self._offset_rigid_links),
                    default=0,
                )
                if self._offset_rigid_links
                else 0
            )
            next_shell_tag = max(max_frame_tag, max_rigid_tag) + 1 + shell_count
            elem_tag = next_shell_tag

            # Use ShellNLDKGQ for areas with a LayeredShell section,
            # ShellMITC4 for all others (linear elastic).
            is_layered = sec_name in self.mesh_model.layered_shell_sections
            shell_type = "ShellNLDKGQ" if is_layered else "ShellMITC4"
            if len(node_tags) == 3:
                # Repeat last node tag for the 4th corner (Collapsed quad)
                ops.element(
                    shell_type,
                    elem_tag,
                    node_tags[0],
                    node_tags[1],
                    node_tags[2],
                    node_tags[2],
                    sec_tag,
                )
            else:
                ops.element(shell_type, elem_tag, *node_tags[:4], sec_tag)
            self._shell_tag_map[aid] = elem_tag
            shell_count += 1

        if self.config["verbose"]:
            print(f"  Created {shell_count} shell elements")

    def _create_single_shell_section(self, sec, mat, tag, etype=None):
        """Create a single ElasticMembranePlateSection in OpenSees."""
        E_mod = mat.E_mod or 200e9
        nu_val = mat.nu or 0.2
        factor = self._get_type_factor(etype) if etype else 1.0
        thickness = getattr(sec, "thickness", 0.0) or 1.0
        if factor != 1.0:
            E_mod *= factor
        ops.section("ElasticMembranePlateSection", tag, E_mod, nu_val, thickness)

    def _get_type_factor(self, etype: str) -> float:
        """Return stiffness reduction factor for a structural type."""
        factors = self.config.get("stiffness_factors", {})
        return factors.get(etype, 1.0)

    # ── Frame elements ───────────────────────────────────────────

    def _build_frame_tag_map(self) -> None:
        """Pre-compute frame element tags before creating elements.

        Ensures shell element tag assignment can avoid clashing with
        frame element tags.
        """
        elements = self.mesh_model.frame_elements
        next_tag = 1
        self.frame_tag_map = {}
        used_tags: set[int] = set()
        for eid, elem in elements.items():
            if getattr(elem, "inactive", False):
                continue
            if elem.elem_tag in used_tags:
                tag = next_tag
                next_tag += 1
            else:
                tag = elem.elem_tag if elem.elem_tag > 0 else next_tag
                next_tag = max(next_tag, tag + 1)
            used_tags.add(tag)
            self.frame_tag_map[eid] = tag

    def _create_elements(self) -> None:
        """Create OpenSees frame elements from MeshModel."""
        from ..model.geometry import subdivide_elements

        if self.config["verbose"]:
            print("Creating frame elements...")

        elements = self.mesh_model.frame_elements
        assignments = self.mesh_model.frame_assignments
        dist_loads = self.mesh_model.frame_dist_loads
        rigid_links: list[tuple] = []

        # Save canonical state on first brace subdivision so
        # _restore_brace_canonical_state() can restore it on repeated builds.
        # Use deep copies to prevent shared mutable state with the MeshModel.
        if (
            self.config.get("subdivide_braces")
            and self._brace_selection
            and not hasattr(self, "_brace_canonical")
        ):
            self._brace_canonical = {
                "frame_elements": copy.deepcopy(self.mesh_model.frame_elements),
                "frame_assignments": copy.deepcopy(self.mesh_model.frame_assignments),
                "nodes": copy.deepcopy(self.mesh_model.nodes),
                "frame_dist_loads": copy.deepcopy(self.mesh_model.frame_dist_loads),
            }

        # Brace subdivision (Approach A) — before element creation loop so
        # child sub-elements are processed by _add_beam_column below.
        if self.config.get("subdivide_braces") and self._brace_selection:
            n_seg = self.config.get("brace_n_segments", 4)
            imperf = self.config.get("brace_imperfection_ratio", 1.0 / 500.0)
            end_off = self.config.get("brace_end_offset", 0.0)
            nodes = self.mesh_model.nodes
            max_elem_tag = max((e.elem_tag for e in elements.values()), default=0)
            max_node_tag = max((nd.node_tag for nd in nodes.values()), default=0)
            try:
                max_ops_tag = max(ops.getEleTags(), default=0)
            except Exception:
                max_ops_tag = 0
            max_rigid_tag = max((r[3] for r in self._offset_rigid_links), default=0)
            next_tag = max(max_elem_tag, max_node_tag, max_ops_tag, max_rigid_tag) + 1
            elements, assignments, nodes, next_tag, rigid_links = subdivide_elements(
                elements,
                assignments,
                nodes,
                n_segments=n_seg,
                imperfection_ratio=imperf,
                brace_ids=self._brace_selection,
                end_offset=end_off,
                next_tag=next_tag,
            )
            self.mesh_model.frame_elements = elements
            self.mesh_model.frame_assignments = assignments
            self.mesh_model.nodes = nodes
            # Rebuild frame tag map so children get OpenSees tags
            self._build_frame_tag_map()
            # Create OpenSees nodes for subdivision / offset nodes
            for nd in nodes.values():
                if nd.node_tag not in self._created_node_tags:
                    ops.node(nd.node_tag, nd.x, nd.y, nd.z)
                    self._created_node_tags.add(nd.node_tag)
            # Redistribute distributed loads from subdivided braces to children
            # Each child gets a proportional share of the parent's load range.
            from ..model.sap_data import FrameDistributedLoad as _FDL

            new_dist_loads: list = []
            for ld in dist_loads:
                if ld.frame_id not in self._brace_selection:
                    new_dist_loads.append(ld)
                    continue
                # Parent was subdivided — distribute to each child
                parent = self.mesh_model.frame_elements.get(ld.frame_id)
                if parent is None or not hasattr(parent, "child_ids"):
                    new_dist_loads.append(ld)
                    continue
                total_len = ld.dist_b - ld.dist_a if ld.dist_b > ld.dist_a else 0.0
                n_child = len(parent.child_ids)
                for ci, child_id in enumerate(parent.child_ids):
                    child_start = ld.dist_a + total_len * (ci / n_child)
                    child_end = ld.dist_a + total_len * ((ci + 1) / n_child)
                    # Compute child-specific rdist values proportional to the
                    # child's segment within the parent's parametric range.
                    parent_rdist_range = ld.rdist_b - ld.rdist_a
                    child_rdist_a = ld.rdist_a + parent_rdist_range * (ci / n_child)
                    child_rdist_b = ld.rdist_a + parent_rdist_range * ((ci + 1) / n_child)
                    new_dist_loads.append(
                        _FDL(
                            pattern=ld.pattern,
                            frame_id=child_id,
                            direction=ld.direction,
                            load_type=ld.load_type,
                            shape=ld.shape,
                            val_a=ld.val_a,
                            val_b=ld.val_b,
                            rdist_a=child_rdist_a,
                            rdist_b=child_rdist_b,
                            dist_a=child_start,
                            dist_b=child_end,
                        )
                    )
            self.mesh_model.frame_dist_loads = new_dist_loads

        for eid, elem in elements.items():
            if getattr(elem, "inactive", False):
                continue

            tag = self.frame_tag_map.get(eid)
            if tag is None:
                continue

            self._add_beam_column(elem, tag, elements, assignments)

        # Rigid link section — created once and reused for both brace
        # subdivision links and frame-end offset links.
        if rigid_links or self._offset_rigid_links:
            all_sec_tags = set(self.section_tags.values())
            all_sec_tags.update(self._shell_sec_tags.values())
            all_sec_tags.update(self._shell_sec_variants.values())
            rigid_section_tag = max(all_sec_tags, default=0) + 1
            rigid_E = 2.0e14
            rigid_A = 1.0
            rigid_I = 1.0
            ops.section(
                "Elastic",
                rigid_section_tag,
                rigid_E,
                rigid_A,
                rigid_I,
                rigid_I,
                rigid_E / 2.6,
                rigid_I,
            )
            self._rigid_section_tag = rigid_section_tag

        # Rigid links from brace subdivision
        if rigid_links:
            for _link_id, _node_i_id, _node_j_id, link_tag in rigid_links:
                nd_i = self.mesh_model.nodes.get(_node_i_id)
                nd_j = self.mesh_model.nodes.get(_node_j_id)
                if nd_i is None or nd_j is None:
                    continue
                ni_tag = nd_i.node_tag
                nj_tag = nd_j.node_tag
                dx = float(nd_j.x - nd_i.x)
                dy = float(nd_j.y - nd_i.y)
                dz = float(nd_j.z - nd_i.z)
                vecxz = get_SAP_vecxz(np.array([dx, dy, dz]), 0.0)
                ops.geomTransf("Linear", link_tag, *vecxz)
                ops.element(
                    "elasticBeamColumn",
                    link_tag,
                    ni_tag,
                    nj_tag,
                    self._rigid_section_tag,
                    link_tag,
                    "-mass",
                    0.0,
                )
                self._rigid_link_elems[_link_id] = link_tag

        # Rigid links from frame end offsets
        # The Preprocessor returns (link_id, node_i, node_j, link_tag) tuples.
        # node_i and node_j are string node IDs — resolve to numeric tags.
        if self._offset_rigid_links:
            _mpc = self.config.get("rigid_link_mpc", False)
            if not _mpc and self._rigid_section_tag is None:
                all_sec_tags = set(self.section_tags.values())
                all_sec_tags.update(self._shell_sec_tags.values())
                all_sec_tags.update(self._shell_sec_variants.values())
                rigid_section_tag = max(all_sec_tags, default=0) + 1
                rigid_E = 2.0e14
                rigid_A = 1.0
                rigid_I = 1.0
                ops.section(
                    "Elastic",
                    rigid_section_tag,
                    rigid_E,
                    rigid_A,
                    rigid_I,
                    rigid_I,
                    rigid_E / 2.6,
                    rigid_I,
                )
                self._rigid_section_tag = rigid_section_tag
            for _link_id, _node_i_id, _node_j_id, link_tag in self._offset_rigid_links:
                nd_i = self.mesh_model.nodes.get(_node_i_id)
                nd_j = self.mesh_model.nodes.get(_node_j_id)
                if nd_i is None or nd_j is None:
                    continue
                ni_tag = nd_i.node_tag
                nj_tag = nd_j.node_tag
                if _mpc:
                    # MPC rigid link (ops.rigidLink "beam"): the original
                    # joint node is the master, the offset node the slave.
                    # Avoids the ill-conditioning of very stiff elastic
                    # links under PDelta (which fails at the gravity stage).
                    _off_i = _node_i_id.endswith(("_off_i", "_off_j"))
                    _off_j = _node_j_id.endswith(("_off_i", "_off_j"))
                    if _off_i and not _off_j:
                        _master_tag, _slave_tag = nj_tag, ni_tag
                    elif _off_j and not _off_i:
                        _master_tag, _slave_tag = ni_tag, nj_tag
                    else:  # defensive: neither id is an offset node
                        _master_tag, _slave_tag = ni_tag, nj_tag
                    ops.rigidLink("beam", _master_tag, _slave_tag)
                    self._rigid_link_elems[_link_id] = _slave_tag
                    continue
                # Compute vecxz for vertical/horizontal links (same convention as _add_beam_column)
                dx = float(nd_j.x - nd_i.x)
                dy = float(nd_j.y - nd_i.y)
                dz = float(nd_j.z - nd_i.z)
                vecxz = get_SAP_vecxz(np.array([dx, dy, dz]), 0.0)
                ops.geomTransf("Linear", link_tag, *vecxz)
                ops.element(
                    "elasticBeamColumn",
                    link_tag,
                    ni_tag,
                    nj_tag,
                    rigid_section_tag,
                    link_tag,
                    "-mass",
                    0.0,
                )
                self._rigid_link_elems[_link_id] = link_tag

        if self.config["verbose"]:
            n = len([e for e in elements.values() if not getattr(e, "inactive", False)])
            print(f"  Created {n} frame elements")

    def _add_beam_column(self, elem, tag, elements, assignments):
        """Add a single beam-column element to the OpenSees domain."""
        ni = self.mesh_model.nodes.get(elem.node_i)
        nj = self.mesh_model.nodes.get(elem.node_j)
        if ni is None or nj is None:
            return

        sec_name = assignments.get(elem.elem_id, "")

        # Determine section tag (check type-specific variant first)
        etype = self._frame_element_types.get(elem.elem_id)
        if etype:
            variant_key = f"{sec_name}__{etype}"
            if variant_key in self.section_tags:
                sec_tag = self.section_tags[variant_key]
            else:
                sec_tag = self.section_tags.get(sec_name, -1)
        else:
            sec_tag = self.section_tags.get(sec_name, -1)

        if sec_tag < 0:
            return

        # ── Brace truss elements ────────────────────────────────
        # When brace_truss is active, sections matching _truss_mat_tags
        # become Truss elements with Hysteretic material instead of
        # beam-column elements (matching the legacy Builder behaviour).
        if (
            self.config.get("brace_truss")
            and hasattr(self, "_truss_mat_tags")
            and sec_name in self._truss_mat_tags
        ):
            A = self._truss_areas[sec_name]
            Fy = self._truss_Fy[sec_name]
            E_sec = self._truss_E[sec_name]
            # Per-element Hysteretic material using actual element length
            # for Euler buckling — each brace gets its own buckling load.
            _L_brace = math.sqrt((nj.x - ni.x) ** 2 + (nj.y - ni.y) ** 2 + (nj.z - ni.z) ** 2)
            eps_y = Fy / E_sec
            s1p, e1p = Fy, eps_y
            s2p, e2p = Fy * 1.01, eps_y + 0.01
            s3p, e3p = Fy * 1.02, eps_y + 0.05
            _sec = self.mesh_model.sections.get(sec_name)
            _I_min = getattr(_sec, "I22", 0.0) or getattr(_sec, "I33", 0.0) or 1e-6
            _P_cr = (math.pi**2 * E_sec * _I_min) / (_L_brace**2)
            sig_cr = _P_cr / A if A > 0 else Fy * 0.3
            eps_cr = sig_cr / E_sec
            s1n, e1n = -sig_cr, -eps_cr
            s2n, e2n = -sig_cr * 0.2, -eps_cr - 0.01
            s3n, e3n = -sig_cr * 0.1, -eps_cr - 0.05
            mat_tag = self._truss_mat_counter
            self._truss_mat_counter += 1
            ops.uniaxialMaterial(
                "Hysteretic",
                mat_tag,
                s1p,
                e1p,
                s2p,
                e2p,
                s3p,
                e3p,
                s1n,
                e1n,
                s2n,
                e2n,
                s3n,
                e3n,
                1.0,
                1.0,
                0.0,
                0.0,
                0.0,
            )
            self.material_tags[f"truss_{sec_name}_{tag}"] = mat_tag
            ops.element("Truss", tag, ni.node_tag, nj.node_tag, A, mat_tag)
            return

        # Geometric transformation
        angle = getattr(elem, "angle", 0.0)
        vecxz = get_SAP_vecxz(np.array([nj.x - ni.x, nj.y - ni.y, nj.z - ni.z]), angle)
        transf_type = self.config.get("geom_transf_type", "Linear")
        transf_tag = tag
        ops.geomTransf(transf_type, transf_tag, *vecxz)
        self._transf_tags[tag] = transf_tag

        # Element
        elem_type = self.config["element_type"]
        n_ip = self.config.get("num_int_pts", 3)
        if elem_type == "elasticBeamColumn":
            ops.element(elem_type, tag, *[ni.node_tag, nj.node_tag], sec_tag, transf_tag)
        else:
            int_tag = tag + 10000
            if self.config.get("beam_integration", "Lobatto") == "Lobatto":
                ops.beamIntegration("Lobatto", int_tag, sec_tag, n_ip)
            else:
                # HingeRadau with explicit hinge lengths
                _L_hinge = math.sqrt((nj.x - ni.x) ** 2 + (nj.y - ni.y) ** 2 + (nj.z - ni.z) ** 2)
                _sec = self.mesh_model.sections.get(sec_name)
                if _sec is not None:
                    from fea_toolkit.model.checks import compute_hinge_length

                    Lp = compute_hinge_length(_sec, _L_hinge)
                else:
                    Lp = 0.1 * _L_hinge
                ops.beamIntegration("HingeRadau", int_tag, sec_tag, Lp, sec_tag, Lp, sec_tag)
            ops.element(elem_type, tag, *[ni.node_tag, nj.node_tag], transf_tag, int_tag)

    # ── Brace selection (Approach A) ─────────────────────────────

    def _restore_brace_canonical_state(self) -> None:
        """Restore canonical frame/assignment/node/dist-load state for brace
        subdivision, so repeated ``build_domain()`` or
        ``rebuild_with_fiber_sections()`` calls always subdivide the
        original (un‑subdivided) elements rather than already-subdivided
        ones.
        """
        if not hasattr(self, "_brace_canonical"):
            return
        snap = self._brace_canonical
        self.mesh_model.frame_elements = snap["frame_elements"]
        self.mesh_model.frame_assignments = snap["frame_assignments"]
        self.mesh_model.nodes = snap["nodes"]
        self.mesh_model.frame_dist_loads = snap["frame_dist_loads"]

    def _restore_hinge_canonical_state(self) -> None:
        """Restore canonical frame element endpoints and remove stale hinge nodes.

        Called at the start of :meth:`build_domain` (before
        :meth:`_create_nodes`) to prevent stale ``*_hinge_*`` nodes
        from a previous build cycle from being recreated.
        """
        if not hasattr(self, "_hinge_canonical_elements"):
            return
        # Remove any *_hinge_* nodes left from a previous build
        for nid in list(self.mesh_model.nodes.keys()):
            if nid.endswith(("_hinge_i", "_hinge_j")):
                del self.mesh_model.nodes[nid]
        # Restore canonical element endpoints and assignments
        for eid, elem in self.mesh_model.frame_elements.items():
            if eid in self._hinge_canonical_elements:
                ni, nj = self._hinge_canonical_elements[eid]
                elem.node_i = ni
                elem.node_j = nj
        self.mesh_model.frame_assignments = dict(self._hinge_canonical_assignments)

    def set_brace_selection(self, brace_ids: set, end_offset: float = 0.0) -> None:
        """Mark specific frame elements as braces for subdivision.

        Call **before** :meth:`build_domain` or
        :meth:`rebuild_with_fiber_sections`.  The elements identified by
        *brace_ids* will be subdivided into *brace_n_segments* segments
        with an initial imperfection (Approach A — subdivided element
        with initial geometric imperfection to capture buckling).

        Args:
            brace_ids: Set of frame element ID strings to treat as braces.
            end_offset: Distance from each working point to the gusset
                plate face (model length units).  Creates rigid link
                segments between the working point and the brace
                physical end.  Default 0.0 (no offset).
        """
        self._brace_selection = brace_ids
        self.config["subdivide_braces"] = True
        # Always clear first so a subsequent call with end_offset=0.0
        # does not retain a previous positive value.
        self.config.pop("brace_end_offset", None)
        if end_offset > 0:
            self.config["brace_end_offset"] = end_offset

    def check_brace_buckling(
        self,
        brace_ids: Optional[set] = None,
        K: float = 1.0,
        axial_demand: Optional[dict[str, float]] = None,
        print_results: bool = True,
    ) -> dict[str, dict[str, float]]:
        """Check selected braces against Euler buckling.

        Delegates to :func:`fea_toolkit.model.checks.check_brace_buckling`.

        Args:
            brace_ids: Set of element IDs to check.  Defaults to
                the stored ``_brace_selection``.
            K: Effective length factor (default 1.0).
            axial_demand: Optional ``{elem_id: axial_force_N}`` dict.
            print_results: If True, print a summary table.

        Returns:
            Dict of ``{elem_id: {P_cr, P_demand, ratio, slenderness, ...}}``.
        """
        from ..model.checks import check_brace_buckling as _check_buckling

        if brace_ids is None:
            brace_ids = self._brace_selection or set()
        return _check_buckling(self.mesh_model, brace_ids, K, axial_demand, print_results)

    # ── Lumped hinges ────────────────────────────────────────────

    def _create_lumped_hinges(self) -> None:
        """Replace frame elements with lumped plasticity hinges.

        Activated via ``config['hinge_model'] = 'lumped'``.

        Each frame element is split into::

            structural_node_i → hinge_i → elastic_mid → hinge_j → structural_node_j

        Coincident hinge nodes sit at the same coordinates.  Translation
        DOFs (1,2,3) are tied with ``equalDOF`` so only rotations (4,5,6)
        are released across the zero-length hinge elements.

        Hinge backbones use ``Hysteretic`` materials matched to ASCE 41
        rotation limits.
        """
        if self.config.get("hinge_model") != "lumped":
            return

        # ── Idempotency: preserve canonical state on first call ────────
        # Save canonical endpoints on first call; restoration is handled
        # by _restore_hinge_canonical_state() in build_domain().
        if not hasattr(self, "_hinge_canonical_elements"):
            self._hinge_canonical_elements = {
                eid: (elem.node_i, elem.node_j)
                for eid, elem in self.mesh_model.frame_elements.items()
                if not getattr(elem, "inactive", False)
            }
            self._hinge_canonical_assignments = dict(self.mesh_model.frame_assignments)

        elements = self.mesh_model.frame_elements
        assignments = self.mesh_model.frame_assignments

        next_node_tag = max((nd.node_tag for nd in self.mesh_model.nodes.values()), default=0) + 1
        # Consider existing OpenSees element tags (shells, rigid links already
        # created) and reserved offset-rigid-link tags to avoid collisions.
        try:
            max_ops_tag = max(ops.getEleTags(), default=0)
        except Exception:
            max_ops_tag = 0
        max_rigid_tag = max((r[3] for r in self._offset_rigid_links), default=0)
        next_tag = (
            max(
                max((e.elem_tag for e in elements.values() if not e.inactive), default=0),
                max_ops_tag,
                max_rigid_tag,
                max(self.frame_tag_map.values(), default=0),
            )
            + 1
        )
        # Separate counter for hinge section/material tags, seeded high
        # to avoid collision with existing tags.
        hinge_tag_base = (
            max((v for v in self.section_tags.values()), default=0) + len(self.section_tags) + 100
        )
        hinge_sec_tag = hinge_tag_base
        hinge_mat_tag = hinge_tag_base + len(self.section_tags) + 1

        new_elements: dict[str, FrameElement] = {}
        new_assignments: dict[str, str] = {}

        for eid, elem in list(elements.items()):
            if elem.inactive:
                new_elements[eid] = elem
                continue

            sec_name = assignments.get(eid) if assignments else None
            if not sec_name or sec_name not in self.section_tags:
                new_elements[eid] = elem
                continue

            ni = self.mesh_model.nodes.get(elem.node_i)
            nj = self.mesh_model.nodes.get(elem.node_j)
            if ni is None or nj is None:
                new_elements[eid] = elem
                continue

            L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
            if L < 1e-12:
                new_elements[eid] = elem
                continue

            # Type-specific section tag lookup
            etype = self._frame_element_types.get(eid)
            type_key = f"{sec_name}__{etype}" if etype else None
            if type_key and type_key in self.section_tags:
                self.section_tags[type_key]
            else:
                self.section_tags[sec_name]
            sec = self.mesh_model.sections.get(sec_name)
            if sec is None:
                new_elements[eid] = elem
                continue

            # --- Create coincident hinge nodes ---
            hinge_i_id = f"{eid}_hinge_i"
            hinge_j_id = f"{eid}_hinge_j"
            hinge_i_tag = next_node_tag
            next_node_tag += 1
            hinge_j_tag = next_node_tag
            next_node_tag += 1

            self.mesh_model.nodes[hinge_i_id] = Node(
                node_id=hinge_i_id,
                node_tag=hinge_i_tag,
                x=ni.x,
                y=ni.y,
                z=ni.z,
            )
            self.mesh_model.nodes[hinge_j_id] = Node(
                node_id=hinge_j_id,
                node_tag=hinge_j_tag,
                x=nj.x,
                y=nj.y,
                z=nj.z,
            )

            # Create OpenSees nodes for coincident hinge nodes
            ops.node(hinge_i_tag, ni.x, ni.y, ni.z)
            ops.node(hinge_j_tag, nj.x, nj.y, nj.z)
            self._created_node_tags.update([hinge_i_tag, hinge_j_tag])

            # Tie translation DOFs between structural and hinge nodes
            ops.equalDOF(ni.node_tag, hinge_i_tag, 1, 2, 3)
            ops.equalDOF(nj.node_tag, hinge_j_tag, 1, 2, 3)

            # --- Create Hysteretic hinge section ---
            mat = self.mesh_model.materials.get(sec.material)

            # Defensive defaults for nullable section values — initialised
            # before the concrete guard so they are guaranteed bound for
            # the hinge backbone computation below.
            Z33 = getattr(sec, "Z33", None) or 0.0
            Z22 = getattr(sec, "Z22", None) or 0.0
            I33 = getattr(sec, "I33", None) or 0.0
            I22 = getattr(sec, "I22", None) or 0.0
            A_val = getattr(sec, "A", None) or 0.0
            J_val = getattr(sec, "J", None) or 0.0
            Fy = mat.Fy if mat and mat.Fy and mat.Fy > 0 else 2.5e8
            E = mat.E_mod if mat and mat.E_mod > 0 else 2.0e11
            G = mat.G_mod if mat and mat.G_mod and mat.G_mod > 0 else 0.4 * E

            # ── Concrete guard ──────────────────────────────────────
            # Concrete sections fire a warning (reinforcement data not
            # available) but still fall through to create elastic hinges
            # using the defaults initialised above.
            if mat and mat.type and "concrete" in mat.type.lower():
                import warnings

                warnings.warn(
                    f"Lumped hinges for concrete sections require reinforcement "
                    f"data not available in generic Section/Material model. "
                    f"Section '{sec_name}', material '{sec.material}' — "
                    f"using elastic moment defaults.",
                )

            # Compute yield moments from section geometry
            if Z33 > 0:
                My = Fy * Z33
            elif I33 > 0 and A_val > 0:
                d_eff = 2.0 * math.sqrt(I33 / A_val)  # 2× radius of gyration
                My = Fy * (I33 / max(d_eff * 0.5, 1e-6))
            else:
                My = Fy * 1e-4  # Minimal fallback
            if Z22 > 0:
                My_weak = Fy * Z22
            elif I22 > 0 and A_val > 0:
                d_eff = 2.0 * math.sqrt(I22 / A_val)
                My_weak = Fy * (I22 / max(d_eff * 0.5, 1e-6))
            else:
                My_weak = Fy * 1e-4

            # ASCE 41 plastic hinge length for yield rotation scaling
            from ..model.checks import compute_asce41_hinge_length

            Lp = compute_asce41_hinge_length(self.mesh_model, sec_name, L)
            theta_y = (
                (My * Lp) / (max(6.0 * E * max(I33, 1e-12), 1e-12))
                if E * max(I33, 1e-12) > 0
                else 0.005
            )
            theta_y_weak = (
                (My_weak * Lp) / (max(6.0 * E * max(I22, 1e-12), 1e-12))
                if E * max(I22, 1e-12) > 0
                else 0.005
            )
            theta_cap = theta_y * 6.0
            theta_cap_weak = theta_y_weak * 6.0

            # Axial material (elastic)
            ops.uniaxialMaterial("Elastic", hinge_mat_tag, max(A_val, 1e-6) * E / L)
            # Strong-axis moment (Hysteretic backbone)
            ops.uniaxialMaterial(
                "Hysteretic",
                hinge_mat_tag + 1,
                My,
                theta_y,
                My * 1.1,
                theta_cap,
                -My,
                -theta_y,
                -My * 1.1,
                -theta_cap,
                1.0,
                1.0,
                0.0,
                0.0,
                0.0,
            )
            # Weak-axis moment
            ops.uniaxialMaterial(
                "Hysteretic",
                hinge_mat_tag + 2,
                My_weak,
                theta_y_weak,
                My_weak * 1.1,
                theta_cap_weak,
                -My_weak,
                -theta_y_weak,
                -My_weak * 1.1,
                -theta_cap_weak,
                1.0,
                1.0,
                0.0,
                0.0,
                0.0,
            )
            # Torsion (elastic — no inelastic torsion expected)
            ops.uniaxialMaterial(
                "Elastic", hinge_mat_tag + 3, G * max(J_val, 1e-6) / L if J_val else G * 1e-6 / L
            )

            ops.section(
                "Aggregator",
                hinge_sec_tag,
                hinge_mat_tag,
                "P",
                hinge_mat_tag + 1,
                "Mz",
                hinge_mat_tag + 2,
                "My",
                hinge_mat_tag + 3,
                "T",
            )
            hinge_sec_tag += 1
            hinge_mat_tag += 4

            # Get local axes for element orientation
            try:
                vx, _vy, vz = self._get_local_axes(elem)
                orient = (vx[0], vx[1], vx[2], vz[0], vz[1], vz[2])
            except Exception:
                orient = None

            # --- Create zero-length hinge elements ---
            hinge_i_elem_tag = next_tag
            next_tag += 1
            if orient:
                ops.element(
                    "zeroLengthSection",
                    hinge_i_elem_tag,
                    ni.node_tag,
                    hinge_i_tag,
                    hinge_sec_tag - 1,
                    "-orient",
                    orient[0],
                    orient[1],
                    orient[2],
                    orient[3],
                    orient[4],
                    orient[5],
                )
            else:
                ops.element(
                    "zeroLengthSection",
                    hinge_i_elem_tag,
                    ni.node_tag,
                    hinge_i_tag,
                    hinge_sec_tag - 1,
                )

            hinge_j_elem_tag = next_tag
            next_tag += 1
            if orient:
                ops.element(
                    "zeroLengthSection",
                    hinge_j_elem_tag,
                    hinge_j_tag,
                    nj.node_tag,
                    hinge_sec_tag - 1,
                    "-orient",
                    orient[0],
                    orient[1],
                    orient[2],
                    orient[3],
                    orient[4],
                    orient[5],
                )
            else:
                ops.element(
                    "zeroLengthSection",
                    hinge_j_elem_tag,
                    hinge_j_tag,
                    nj.node_tag,
                    hinge_sec_tag - 1,
                )

            # --- Shorten original element to span between hinge nodes ---
            elem.node_i = hinge_i_id
            elem.node_j = hinge_j_id
            new_elements[eid] = elem
            new_assignments[eid] = sec_name

        # Update collections
        self.mesh_model.frame_elements = new_elements
        self.mesh_model.frame_assignments = new_assignments

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
                load_total += (
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

    def run_static_analysis(
        self,
        extract_reactions: bool = True,
        pattern_scales: Optional[dict[str, float]] = None,
    ) -> dict[str, Any]:
        """Run static analysis on the current OpenSees domain.

        When *pattern_scales* is provided, the domain is rebuilt with
        only those load patterns active (matching the facade's behaviour).
        When *pattern_scales* is ``None`` (default), the existing domain
        is analysed as-is.

        Returns a dict with nodal_displacements, reactions, element_forces,
        and load_totals.
        """
        # Rebuild domain with new pattern scales if requested
        if pattern_scales is not None:
            self.build_domain()
            self._reapply_edge_constraints()
            self.create_loads(pattern_scales=pattern_scales)

        sol_cfg = self.config
        sd = self.PUSHOVER_SOLVER_DEFAULTS
        test_type = sol_cfg.get("solver_test_type", sd["solver_test_type"])
        test_tol = sol_cfg.get("solver_test_tol", sd["solver_test_tol"])
        test_iter = sol_cfg.get("solver_test_max_iter", sd["solver_test_max_iter"])
        algo = sol_cfg.get("solver_algorithm", sd["solver_algorithm"])
        n_sub = sol_cfg.get("gravity_num_substeps", sd["gravity_num_substeps"])

        cs = sol_cfg.get("solver_constraints", sd["solver_constraints"])
        if self._edge_constraint_method == "penalty":
            cs = "Penalty"
            ops.constraints("Penalty", 1.0e12, 1.0e12)
        else:
            ops.constraints(cs)
        ops.numberer("RCM")
        ops.system(sol_cfg.get("solver_system", sd["solver_system"]))
        ops.test(test_type, test_tol, test_iter)

        _algo_chain = [algo]
        if algo != "NewtonLineSearch":
            _algo_chain.append("NewtonLineSearch")
        if algo != "ModifiedNewton":
            _algo_chain.append(("ModifiedNewton", "-initial"))
        if algo != "KrylovNewton":
            _algo_chain.append("KrylovNewton")

        # ── Fallback settings (Gap 5) ────────────────────────────
        # If the primary chain fails (e.g. the fiber-rebuild gravity
        # solve returning NaN), retry the remaining substeps with
        # NormUnbalance + relaxed tolerance + ModifiedNewton(-initial).
        _fallback = sol_cfg.get("pushover_fallback_defaults", self.PUSHOVER_FALLBACK_DEFAULTS)
        fb_test_type = _fallback.get("solver_test_type", "NormUnbalance")
        # Units-aware fallback tolerance: scale off the model's
        # characteristic weight (total mass × g via g_from_units), which
        # has consistent force units.  An absolute unscaled tolerance
        # (e.g. 1e-12) is unattainable for full-building residuals.
        _g = g_from_units(self.units)
        _fb_total_mass = sum(self.node_masses.values()) if self.node_masses else 0.0
        if _fb_total_mass > 0:
            fb_test_tol = max(_fb_total_mass * _g * 1e-6, test_tol * 10.0)
        else:
            fb_test_tol = test_tol * 10.0
        fb_test_iter = max(_fallback.get("solver_test_max_iter", 1000), test_iter * 10)
        fb_algo = _fallback.get("solver_algorithm", "ModifiedNewton")

        # ── Configure the static analysis once ──────────────────
        # Do NOT re-create the integrator/analysis between algorithm
        # attempts.  A failed step rolls back to the last committed
        # state but the integrator's internal load factor remains at
        # the last *converged* increment.  Re-creating the analysis (as
        # historically done) resets that counter to 0, so a partially-
        # converged attempt (e.g. substeps 1-2 of n_sub=10 succeeded,
        # then substep 3 failed) forces the next algorithm to *unload*
        # from load factor 0.2 back to 0.1 — with forceBeamColumn fiber
        # sections this unloading path produces NaN.  Keeping the same
        # StaticAnalysis object and switching only the algorithm lets the
        # load factor continue monotonically 0.1 -> 0.2 -> ... -> 1.0.
        ops.integrator("LoadControl", 1.0 / n_sub)
        ops.analysis("Static")

        converged = 0
        ok = -1
        for attempt in _algo_chain:
            if isinstance(attempt, tuple):
                ops.algorithm(*attempt)
            elif attempt == "ModifiedNewton":
                ops.algorithm("ModifiedNewton", "-initial")
            else:
                ops.algorithm(attempt)
            ok = 0
            for s in range(converged, n_sub):
                ok = ops.analyze(1)
                if ok != 0:
                    break
                converged = s + 1
            if ok == 0:
                break

        if ok != 0:
            # Relaxed NormUnbalance + ModifiedNewton(-initial) fallback
            # pass, resuming from the last converged substep *without*
            # resetting the integrator (same monotonic-load-factor
            # reasoning as above).
            ops.test(fb_test_type, fb_test_tol, fb_test_iter)
            if fb_algo == "ModifiedNewton":
                ops.algorithm("ModifiedNewton", "-initial")
            else:
                ops.algorithm(fb_algo)
            ok = 0
            for s in range(converged, n_sub):
                ok = ops.analyze(1)
                if ok != 0:
                    break
                converged = s + 1

        if ok != 0:
            # Adaptive substepping (Gap 5): the RC fiber model can
            # still fail a fixed LoadControl step (e.g. 30% of gravity)
            # when a column softens between two converged states.  Halve
            # the load increment and continue monotonically from the last
            # converged state.  The analysis object stays alive — only
            # the integrator is swapped — so the load factor continues
            # 0.2 → 0.225 → 0.25 ... instead of unloading back toward 0
            # (which produced the original NaN).
            # Track the applied load factor as a float (each successful
            # ops.analyze(1) advances it by the current increment) rather
            # than remapping the integer converged step count.  Retry
            # passes derive their start index from this load factor, so
            # they never issue increments beyond gravity load factor 1.0.
            applied_load_factor = float(converged) / float(n_sub)
            half_n_sub = n_sub * 2
            half_inc = 1.0 / half_n_sub
            done_half = int(round(applied_load_factor * half_n_sub))
            done_half = min(max(done_half, 0), half_n_sub)
            ops.integrator("LoadControl", half_inc)
            ok = 0
            for s in range(done_half, half_n_sub):
                ok = ops.analyze(1)
                if ok != 0:
                    break
                applied_load_factor += half_inc
            if ok != 0:
                # Final fallback: quarter inc (only used when the model is
                # extremely soft near the target gravity combination).
                quad_n_sub = n_sub * 4
                quad_inc = 1.0 / quad_n_sub
                done_quad = int(round(applied_load_factor * quad_n_sub))
                done_quad = min(max(done_quad, 0), quad_n_sub)
                ops.integrator("LoadControl", quad_inc)
                ok = 0
                for s in range(done_quad, quad_n_sub):
                    ok = ops.analyze(1)
                    if ok != 0:
                        break
                    applied_load_factor += quad_inc

        if ok != 0:
            raise RuntimeError(
                f"Static analysis failed to converge after trying algorithms: {_algo_chain}"
            )

        # Extract results
        result: dict[str, Any] = {}

        # Nodal displacements
        result["nodal_displacements"] = {}
        for nd in self.mesh_model.nodes.values():
            try:
                disp = ops.nodeDisp(nd.node_tag)
                result["nodal_displacements"][nd.node_id] = list(disp)
            except Exception:
                continue

        # Reactions
        if extract_reactions:
            ops.reactions()
            result["reactions"] = {}
            for nid, restraint in self.mesh_model.restraints.items():
                nd = self.mesh_model.nodes.get(nid)
                if nd is None:
                    continue
                try:
                    rxn = ops.nodeReaction(nd.node_tag)
                    result["reactions"][nid] = {
                        "fx": rxn[0],
                        "fy": rxn[1],
                        "fz": rxn[2],
                        "mx": rxn[3],
                        "my": rxn[4],
                        "mz": rxn[5],
                    }
                except Exception:
                    continue

        # ── Gravity load/reaction sanity check ──────────────────
        if extract_reactions and (
            self._gravity_load_totals or self._joint_load_totals or self._sw_load_totals
        ):
            total_applied_fz = 0.0
            for totals in self._gravity_load_totals.values():
                total_applied_fz += totals.get("fz", 0.0)
            for totals in self._joint_load_totals.values():
                total_applied_fz += totals.get("fz", 0.0)
            for totals in self._sw_load_totals.values():
                total_applied_fz += totals.get("fz", 0.0)

            total_reaction_fz = 0.0
            for nid, restraint in self.mesh_model.restraints.items():
                # Full fixity only (6 DOFs all True)
                if not all(restraint.dofs):
                    continue
                rxn = result.get("reactions", {}).get(nid, {})
                total_reaction_fz += rxn.get("fz", 0.0)

            # Compare magnitudes using the established opposite-sign
            # convention: gravity loads are downward (negative Fz) while
            # reactions are upward (positive Fz).  The equilibrium delta
            # is the difference of the magnitudes, not the direct
            # subtraction of signed values (which double-counts).
            abs_applied = abs(total_applied_fz)
            abs_reaction = abs(total_reaction_fz)
            delta = abs(abs_applied - abs_reaction)
            tol = max(abs_applied * 0.01, 1e-6)

            if delta > tol and abs_applied > 1e-12:
                pct = (delta / abs_applied * 100) if abs_applied > 1e-12 else 0.0
                logger.warning(
                    "Gravity load/reaction mismatch: "
                    "applied fz=%.6e, "
                    "reaction fz=%.6e, "
                    "Δ=%.6e (%.1f%%)",
                    total_applied_fz,
                    total_reaction_fz,
                    delta,
                    pct,
                )

            result["load_reaction_check"] = {
                "applied_fz": total_applied_fz,
                "reaction_fz": total_reaction_fz,
                "delta": delta,
            }

        return result

    # ═══════════════════════════════════════════════════════════════
    # Mass
    # ═══════════════════════════════════════════════════════════════

    def compute_seismic_masses(self) -> dict[str, float]:
        """Compute lumped nodal masses from the model's MASS SOURCE entries.

        Gravitational acceleration is derived from the model's units via
        :func:`~fea_toolkit.utils.g_from_units` — the model unit system is
        the single source of truth (never a hardcoded 9.81).

        All mass contributions are lumped to nodes and assigned via
        ``ops.mass(node, m, m, m, 0, 0, 0)``.

        Returns:
            Dictionary mapping node ID → total lumped mass (tonnes).
        """
        g = g_from_units(self.mesh_model.units)

        mm = self.mesh_model
        elements = mm.frame_elements
        assignments = mm.frame_assignments
        dist_loads = mm.frame_dist_loads

        node_mass: dict[str, float] = {}

        mass_sources = getattr(mm, "mass_sources", {})
        if not mass_sources:
            # No MASS SOURCE definitions — fallback: element self-weight + DEAD
            self._mass_from_elements(mm, elements, assignments, node_mass, g)
            self._mass_from_dist_loads(mm, elements, dist_loads, node_mass, g, ["DEAD"])
        else:
            for ms in mass_sources.values():
                if ms.elements:
                    self._mass_from_elements(mm, elements, assignments, node_mass, g)

                if ms.loads and ms.load_pattern:
                    for lp_name, mult in ms.load_pattern.items():
                        if abs(mult) < 1e-12:
                            continue
                        self._mass_from_dist_loads(
                            mm, elements, dist_loads, node_mass, g, [lp_name], mult
                        )
                        self._mass_from_joint_loads(mm, node_mass, g, lp_name, mult)
                        self._mass_from_area_gravity(mm, node_mass, g, lp_name, mult)
                        self._mass_from_area_uniform(mm, node_mass, g, lp_name, mult)

        # Assign masses to OpenSees nodes
        for nid, m in node_mass.items():
            nd = mm.nodes.get(nid)
            if nd is None:
                continue
            tag = nd.node_tag
            if m > 0:
                ops.mass(tag, m, m, m, 0, 0, 0)
            else:
                ops.mass(tag, 1e-6, 1e-6, 1e-6, 0, 0, 0)

        self.node_masses = node_mass
        self._mass_g = g

        if self.config.get("verbose"):
            total = sum(node_mass.values())
            print(f"  Total seismic mass: {total:.2f} tonnes")
            print(f"  Total seismic weight: {total * g / 1000:.2f} MN")

        return node_mass

    def _query_nodal_masses(self) -> dict[int, float]:
        """Query lumped translational masses from the active OpenSees domain.

        Reads ``ops.nodeMass()`` directly so the returned dict is keyed by
        numeric OpenSees node tag — the same key space used by
        :meth:`extract_mode_shapes`.  This is the dict consumed by
        :func:`~fea_toolkit.model.csm.pushover_to_adrs` via the
        ``modal_results['nodal_masses']`` key; without it the ADRS
        conversion degenerates to ``Gamma = M_eff = 1.0``.

        Returns:
            ``{node_tag: mass}`` for every node in the active domain.
            Nodes with no applicable mass are included with ``0.0`` so
            the ADRS conversion sees the full node set.
        """
        masses: dict[int, float] = {}
        for tag in ops.getNodeTags():
            try:
                m = ops.nodeMass(int(tag))
                masses[int(tag)] = float(m[0]) if m else 0.0
            except Exception:
                masses[int(tag)] = 0.0
        return masses

    def _mass_from_elements(self, mm, elements, assignments, node_mass, g):
        """Add mass from element self-weight."""
        for eid, elem in elements.items():
            if getattr(elem, "inactive", False):
                continue
            sec_name = assignments.get(eid, "")
            if not sec_name:
                continue
            sec = mm.sections.get(sec_name)
            if sec is None:
                continue
            mat = mm.materials.get(sec.material)
            if mat is None or abs(mat.unit_weight) < 1e-12:
                continue
            ni = mm.nodes.get(elem.node_i)
            nj = mm.nodes.get(elem.node_j)
            if ni is None or nj is None:
                continue
            L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
            if L < 1e-12:
                continue
            weight = getattr(sec, "A", 0.0) * mat.unit_weight * L
            mass = weight / g
            node_mass[elem.node_i] = node_mass.get(elem.node_i, 0.0) + mass * 0.5
            node_mass[elem.node_j] = node_mass.get(elem.node_j, 0.0) + mass * 0.5

        # Area elements
        for aid, ae in mm.area_elements.items():
            if getattr(ae, "inactive", False):
                continue
            sec_name = mm.area_assignments.get(aid, "")
            if not sec_name:
                continue
            sec = mm.sections.get(sec_name)
            if sec is None:
                continue
            mat = mm.materials.get(sec.material)
            if mat is None or abs(mat.unit_weight) < 1e-12:
                continue
            thickness = getattr(ae, "thickness", 0.0) or 0.0
            if thickness < 1e-12:
                continue
            corner_pts = []
            for nid in ae.node_ids:
                nd = mm.nodes.get(nid)
                if nd is None:
                    break
                corner_pts.append((nd.x, nd.y, nd.z))
            if len(corner_pts) < 3:
                continue
            area_mag = polygon_area_3d(corner_pts)
            if area_mag < 1e-12:
                continue
            weight = area_mag * thickness * mat.unit_weight
            mass = weight / g
            n_c = len(ae.node_ids)
            for nid in ae.node_ids:
                node_mass[nid] = node_mass.get(nid, 0.0) + mass / n_c

    def _mass_from_dist_loads(
        self, mm, elements, dist_loads, node_mass, g, pattern_names, mult=1.0
    ):
        """Add mass from frame distributed loads in given patterns."""
        for ld in dist_loads or []:
            if ld.pattern not in pattern_names:
                continue
            elem = elements.get(ld.frame_id)
            if elem is None or getattr(elem, "inactive", False):
                continue
            ni = mm.nodes.get(elem.node_i)
            nj = mm.nodes.get(elem.node_j)
            if ni is None or nj is None:
                continue
            L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
            if L < 1e-12:
                continue
            load_len = ld.dist_b - ld.dist_a
            avg = (ld.val_a + ld.val_b) * 0.5
            total_force = avg * load_len * mult
            mass = total_force / g
            node_mass[elem.node_i] = node_mass.get(elem.node_i, 0.0) + mass * 0.5
            node_mass[elem.node_j] = node_mass.get(elem.node_j, 0.0) + mass * 0.5

    def _mass_from_joint_loads(self, mm, node_mass, g, lp_name, mult):
        """Add mass from joint loads in the given pattern."""
        for jl in getattr(mm, "joint_loads", []):
            if jl.pattern != lp_name:
                continue
            total_force = abs(jl.fz) * mult
            mass = total_force / g
            node_mass[jl.node_id] = node_mass.get(jl.node_id, 0.0) + mass

    def _mass_from_area_gravity(self, mm, node_mass, g, lp_name, mult):
        """Add mass from area gravity loads in the given pattern."""
        from ..model.tree_utils import collect_descendants

        for agl in getattr(mm, "area_gravity_loads", []):
            if agl.pattern != lp_name:
                continue
            ae = mm.area_elements.get(agl.area_id)
            if ae is None:
                continue
            if getattr(ae, "inactive", False):
                sub_ids = collect_descendants(agl.area_id, mm.area_elements)
                if not sub_ids:
                    continue
                for sub_id in sub_ids:
                    sub_elem = mm.area_elements.get(sub_id)
                    if sub_elem is None:
                        continue
                    sec_name = mm.area_assignments.get(sub_id, "")
                    if not sec_name:
                        continue
                    sec = mm.sections.get(sec_name)
                    if sec is None:
                        continue
                    mat = mm.materials.get(sec.material)
                    if mat is None or abs(mat.unit_weight) < 1e-12:
                        continue
                    thickness = getattr(sub_elem, "thickness", 0.0) or 0.0
                    if thickness < 1e-12:
                        continue
                    corner_pts = []
                    for nid in sub_elem.node_ids:
                        nd = mm.nodes.get(nid)
                        if nd is None:
                            break
                        corner_pts.append((nd.x, nd.y, nd.z))
                    if len(corner_pts) < 3:
                        continue
                    area_mag = polygon_area_3d(corner_pts)
                    if area_mag < 1e-12:
                        continue
                    sw_per_area = thickness * mat.unit_weight
                    total_fz = sw_per_area * area_mag * abs(agl.multiplier_z) * mult
                    mass = total_fz / g
                    n_c = len(sub_elem.node_ids)
                    for nid in sub_elem.node_ids:
                        node_mass[nid] = node_mass.get(nid, 0.0) + mass / n_c
                continue
            sec_name = mm.area_assignments.get(agl.area_id, "")
            if not sec_name:
                continue
            sec = mm.sections.get(sec_name)
            if sec is None:
                continue
            mat = mm.materials.get(sec.material)
            if mat is None or abs(mat.unit_weight) < 1e-12:
                continue
            thickness = getattr(ae, "thickness", 0.0) or 0.0
            if thickness < 1e-12:
                continue
            corner_pts = []
            for nid in ae.node_ids:
                nd = mm.nodes.get(nid)
                if nd is None:
                    break
                corner_pts.append((nd.x, nd.y, nd.z))
            if len(corner_pts) < 3:
                continue
            area_mag = polygon_area_3d(corner_pts)
            if area_mag < 1e-12:
                continue
            sw_per_area = thickness * mat.unit_weight
            total_fz = sw_per_area * area_mag * abs(agl.multiplier_z) * mult
            mass = total_fz / g
            n_c = len(ae.node_ids)
            for nid in ae.node_ids:
                node_mass[nid] = node_mass.get(nid, 0.0) + mass / n_c

    def _mass_from_area_uniform(self, mm, node_mass, g, lp_name, mult):
        """Add mass from area uniform loads in the given pattern."""
        from ..model.tree_utils import collect_descendants

        for aul in getattr(mm, "area_uniform_loads", []):
            if aul.pattern != lp_name:
                continue
            ae = mm.area_elements.get(aul.area_id)
            if ae is None:
                continue
            if getattr(ae, "inactive", False):
                sub_ids = collect_descendants(aul.area_id, mm.area_elements)
                if not sub_ids:
                    continue
                for sub_id in sub_ids:
                    sub_elem = mm.area_elements.get(sub_id)
                    if sub_elem is None:
                        continue
                    corner_pts = []
                    for nid in sub_elem.node_ids:
                        nd = mm.nodes.get(nid)
                        if nd is None:
                            break
                        corner_pts.append((nd.x, nd.y, nd.z))
                    if len(corner_pts) < 3:
                        continue
                    area_mag = polygon_area_3d(corner_pts)
                    if area_mag < 1e-12:
                        continue
                    pressure = abs(aul.value)
                    total_force = pressure * area_mag * mult
                    mass = total_force / g
                    n_c = len(sub_elem.node_ids)
                    for nid in sub_elem.node_ids:
                        node_mass[nid] = node_mass.get(nid, 0.0) + mass / n_c
                continue
            corner_pts = []
            for nid in ae.node_ids:
                nd = mm.nodes.get(nid)
                if nd is None:
                    break
                corner_pts.append((nd.x, nd.y, nd.z))
            if len(corner_pts) < 3:
                continue
            area_mag = polygon_area_3d(corner_pts)
            if area_mag < 1e-12:
                continue
            pressure = abs(aul.value)
            total_force = pressure * area_mag * mult
            mass = total_force / g
            n_c = len(ae.node_ids)
            for nid in ae.node_ids:
                node_mass[nid] = node_mass.get(nid, 0.0) + mass / n_c

    # ═══════════════════════════════════════════════════════════════
    # Modal and response-spectrum analysis
    # ═══════════════════════════════════════════════════════════════

    def run_modal_analysis(
        self, num_modes: int = 30, print_results: bool = True, eigen_solver: str = "default"
    ) -> dict[str, Any]:
        """Run eigenvalue / modal analysis and return results.

        Requires that seismic masses have been assigned (call
        :meth:`compute_seismic_masses` first) and the domain has been
        built via :meth:`build_domain`.

        Args:
            num_modes: Number of eigenvalues to solve for.
            print_results: If True, print a modal properties table.
            eigen_solver: Solver strategy.

                ``"default"``
                    ARPACK (fast), fallback to fullGenLapack.
                ``"fullGenLapack"``
                    Robust but slow for large models.
                ``"genBandArpack"``
                    Generalized banded ARPACK — requires a Ritz pre-step.
                ``"symmBandLapack"``
                    Symmetric banded Lapack solver.
                ``"ritz"``
                    Gravity pre-step then ARPACK.

        Returns:
            Dictionary with keys:

            * ``'eigenvalues'`` — list of eigenvalues (omega^2).
            * ``'periods'`` — list of natural periods (s).
            * ``'frequencies'`` — list of natural frequencies (Hz).
            * ``'modal_props'`` — the full ``ops.modalProperties()`` dict.
            * ``'num_modes'`` — number of converged modes.
            * ``'nodal_masses'`` — dict of nodal masses ``{tag: (mx, my, mz)}``
              in model units (tonnes for kN-m models).
        """
        if self.config.get("verbose"):
            print(f"Running modal analysis for {num_modes} modes...")

        # ── Ensure seismic masses are present ────────────────────
        _has_mass = False
        for t in ops.getNodeTags():
            try:
                m = ops.nodeMass(t)
                if sum(abs(x) for x in m) > 1e-12:
                    _has_mass = True
                    break
            except Exception:
                pass
        if not _has_mass:
            self.compute_seismic_masses()

        # ── Ritz / pre-load nudge ────────────────────────────────
        _needs_nudge = eigen_solver in ("genBandArpack", "ritz")
        if _needs_nudge:
            if self.config.get("verbose"):
                print("  Ritz pre-step (static gravity)...")
            # Run a self-weight gravity load step
            self.create_loads(pattern_scales={"Self weight": 1.0})
            try:
                if self._edge_constraint_method == "penalty":
                    ops.constraints("Penalty", 1.0e12, 1.0e12)
                else:
                    ops.constraints("Transformation")
                ops.numberer("RCM")
                ops.system(self.config.get("solver_system", "BandGen"))
                ops.test("NormDispIncr", 1e-3, 5, 0)
                _algorithms = ["Newton", "NewtonLineSearch", "ModifiedNewton", "KrylovNewton"]
                _ok = -1
                for _alg in _algorithms:
                    try:
                        ops.algorithm(_alg)
                    except Exception:
                        continue
                    ops.integrator("LoadControl", 1.0)
                    ops.analysis("Static")
                    _ok = ops.analyze(1)
                    if _ok == 0:
                        break
                if _ok != 0 and self.config.get("verbose"):
                    print("  ⚠ Ritz pre-step did not converge — continuing with zero initial state")
            except Exception:
                if self.config.get("verbose"):
                    print("  ⚠ Ritz pre-step failed — continuing")

        # ── Set constraint handler for eigen analysis ────────────
        try:
            if self._edge_constraint_method == "penalty":
                ops.constraints("Penalty", 1.0e12, 1.0e12)
            else:
                ops.constraints(self.config.get("solver_constraints", "Transformation"))
            ops.numberer("RCM")
            ops.system(self.config.get("solver_system", "BandGen"))
        except Exception:
            pass

        # ── Eigenvalue solver ────────────────────────────────────
        eigenvals_all = []
        _solver_map = {
            "genBandArpack": "-genBandArpack",
            "symmBandLapack": "-symmBandLapack",
            "fullGenLapack": "-fullGenLapack",
            "default": None,
        }
        solver_flag = _solver_map.get(eigen_solver)
        if solver_flag is not None:
            try:
                eigenvals_all = ops.eigen(solver_flag, num_modes)
            except Exception:
                eigenvals_all = []
            if not eigenvals_all:
                print(f"  ⚠ {eigen_solver} solver failed — falling back to ARPACK")
                try:
                    eigenvals_all = ops.eigen(num_modes)
                except Exception:
                    eigenvals_all = []
                if not eigenvals_all:
                    print("  ⚠ ARPACK also failed — falling back to fullGenLapack")
                    try:
                        eigenvals_all = ops.eigen("-fullGenLapack", num_modes)
                    except Exception:
                        eigenvals_all = []
        else:
            try:
                eigenvals_all = ops.eigen(num_modes)
            except Exception:
                eigenvals_all = []
            if not eigenvals_all:
                print("  ⚠ ARPACK solver failed — falling back to fullGenLapack")
                try:
                    eigenvals_all = ops.eigen("-fullGenLapack", num_modes)
                except Exception:
                    eigenvals_all = []

        eigenvals = [ev for ev in eigenvals_all if ev > 1e-12]
        n_modes = len(eigenvals)
        if n_modes < num_modes and self.config.get("verbose"):
            print(
                f"  Warning: only {n_modes} positive eigenvalues out of "
                f"{num_modes}.  Proceeding with {n_modes} modes."
            )

        periods = [2.0 * math.pi / math.sqrt(ev) for ev in eigenvals]
        frequencies = [math.sqrt(ev) / (2.0 * math.pi) for ev in eigenvals]

        try:
            modal_props = ops.modalProperties("-return", "-unorm")
        except Exception:
            modal_props = {}

        results = {
            "eigenvalues": eigenvals,
            "periods": periods,
            "frequencies": frequencies,
            "modal_props": modal_props,
            "num_modes": n_modes,
            "nodal_masses": self._query_nodal_masses(),
        }

        if print_results:
            print("\n===== MODAL ANALYSIS =====")
            if modal_props:
                try:
                    total_mass = modal_props.get("totalFreeMass", [0])[0]
                    print(f"Total translational mass (free DOFs): {total_mass:.2f} tonnes\n")
                    header = (
                        f"{'Mode':>5} {'Freq(Hz)':>10} {'Period(s)':>10} "
                        f"{'Mx(t)':>12} {'My(t)':>12} {'Mz(t)':>12} "
                        f"{'%X':>7} {'%Y':>7} {'%Z':>7}"
                    )
                    print(header)
                    print("-" * len(header))
                    for i in range(n_modes):
                        mx = modal_props.get("partiMassMX", [0] * n_modes)[i]
                        my = modal_props.get("partiMassMY", [0] * n_modes)[i]
                        mz = modal_props.get("partiMassMZ", [0] * n_modes)[i]
                        rx = modal_props.get("partiMassRatiosMX", [0] * n_modes)[i]
                        ry = modal_props.get("partiMassRatiosMY", [0] * n_modes)[i]
                        rz = modal_props.get("partiMassRatiosMZ", [0] * n_modes)[i]
                        print(
                            f"{i + 1:5d} {frequencies[i]:10.4f} "
                            f"{periods[i]:10.4f} {mx:12.2f} {my:12.2f} "
                            f"{mz:12.2f} {rx:6.2f}% {ry:6.2f}% {rz:6.2f}%"
                        )
                except Exception:
                    pass
            else:
                print(f"{'Mode':>5} {'Period(s)':>10} {'Freq(Hz)':>10}")
                print("-" * 30)
                for i in range(n_modes):
                    print(f"{i + 1:5d} {periods[i]:10.4f} {frequencies[i]:10.4f}")

        return results

    def run_response_spectrum_analysis(
        self,
        num_modes: int,
        modal_periods: list[float],
        spectrum_periods: list[float],
        spectrum_accels: list[float],
        direction: str = "X",
        damping_ratio: float = 0.05,
        T_rigid: Optional[float] = None,
        print_results: bool = True,
    ) -> dict[str, Any]:
        """Run a response‑spectrum analysis using CQC modal combination.

        Performs mode‑by‑mode RS analysis using OpenSees'
        ``responseSpectrumAnalysis``, then combines with CQC.

        **How base reactions are computed**

        ``ops.responseSpectrumAnalysis(... '-mode', mode)`` sets the
        modal displacement field for a single mode on the domain (via
        ``node->setTrialDisp()``).  After ``commitDomain()``, element
        internal forces are consistent with those displacements.  We then
        query ``ops.eleResponse(eid, 'forces')`` which returns **global
        element‑end forces** ``[Fx, Fy, Fz, Mx, My, Mz]`` at the I-end
        then J-end.

        For each base-connected element we extract the base-end forces
        and accumulate into a per-mode 6-DoF reaction vector.  The
        element-end moments include column bending directly, but the
        **axial force lever‑arm** (Fz from one column × distance to the
        centroid of another) is a structural‑level effect not present in
        individual element stiffness outputs.  We add it here:

        ``mx += Mx_direct + Fz·dy − Fy·dz``
        ``my += My_direct + Fx·dz − Fz·dx``

        using the **fixed geometric centroid** (bounding-box midpoint
        ``(min+max)/2`` of all nodes).  This fixed reference is the same
        as :func:`~fea_toolkit.utils.sum_reactions_with_overturning`
        uses for static lateral loads — ensuring consistent moment
        origins across all analysis types.

        .. note::
           A fixed reference point is **required** for CQC combination:
           if each mode used its own Fz-weighted centroid, the per-mode
           moments would reference different points and CQC would be
           physically invalid.

        Args:
            num_modes: Number of modes to include.
            modal_periods: Natural periods of each mode (s).
            spectrum_periods: Period axis of the response spectrum (s).
            spectrum_accels: Spectral acceleration values (m/s^2).
            direction: Excitation direction — ``'X'``, ``'Y'``, or ``'Z'``.
            damping_ratio: Damping ratio for CQC correlation.
            T_rigid: Rigid cut-off period (s). ``None`` = no cut-off.
            print_results: If True, print a summary table.

        Returns:
            Dictionary with:
            - ``modal_base_shear`` / ``modal_base_moment`` (scalar, backward compat)
            - ``base_shear_cqc`` / ``base_shear_srss`` / ``base_moment_cqc`` / ``base_moment_srss``
            - ``modal_periods``
            - ``modal_base_reactions`` (list of 6-DoF dicts per mode)
            - ``base_reactions_cqc`` / ``base_reactions_srss`` (6-DoF combined)
              where Mx/My include overturning from Fz × lever-arm about
              the fixed geometric centroid (bounding-box midpoint).
              This fixed reference ensures CQC validity across modes.
        """
        if self.config.get("verbose"):
            print(f"Running response spectrum analysis (dir={direction})...")

        num_modes = min(num_modes, len(modal_periods))
        if num_modes == 0:
            raise ValueError("No modal periods available for RS analysis")

        omega = [2.0 * math.pi / T if T > 0 else 0.0 for T in modal_periods]
        damp_ratios = [damping_ratio] * num_modes

        SPECTRUM_TS_TAG = 9999
        with contextlib.suppress(Exception):
            ops.remove("timeSeries", SPECTRUM_TS_TAG)
        ops.timeSeries(
            "Path", SPECTRUM_TS_TAG, "-time", *spectrum_periods, "-values", *spectrum_accels
        )

        modal_base_shear = []
        modal_base_moment = []
        dof = {"X": 1, "Y": 2, "Z": 3}[direction]

        dof_idx = {"X": 0, "Y": 1, "Z": 2}[direction]
        base_nodes = {
            nid
            for nid, r in self.mesh_model.restraints.items()
            if len(r.dofs) > dof_idx and r.dofs[dof_idx] == 1
        }

        elements = self.mesh_model.frame_elements
        base_elements = []
        for eid, elem in elements.items():
            if getattr(elem, "inactive", False):
                continue
            nd_i = self.mesh_model.nodes.get(elem.node_i)
            nd_j = self.mesh_model.nodes.get(elem.node_j)
            if nd_i is None or nd_j is None:
                continue
            # Use the actual OpenSees tag from the frame_tag_map so that
            # split-frame children are addressed by their correct tag.
            ops_tag = self.frame_tag_map.get(eid, elem.elem_tag)
            if elem.node_i in base_nodes and elem.node_j not in base_nodes:
                base_elements.append((ops_tag, "i"))
            elif elem.node_j in base_nodes and elem.node_i not in base_nodes:
                base_elements.append((ops_tag, "j"))

        # ── Pre-compute fixed reference point for overturning moment ──
        # Compute from base (support) nodes only — the centre of the base
        # footprint. This ensures a consistent reference across all modes
        # for valid CQC combination.  Same approach as
        # sum_reactions_with_overturning in utils.py.
        _base_nds = [
            self.mesh_model.nodes[nid] for nid in base_nodes if nid in self.mesh_model.nodes
        ]
        if _base_nds:
            _xs = [n.x for n in _base_nds]
            _ys = [n.y for n in _base_nds]
            _cx = (min(_xs) + max(_xs)) * 0.5
            _cy = (min(_ys) + max(_ys)) * 0.5
            _z_base = sum(n.z for n in _base_nds) / len(_base_nds)
        else:
            _cx = _cy = _z_base = 0.0

        # Pre-compute base-element node coordinates for lever-arm
        # Build a one-time tag-to-element index (ops tag → element)
        _elem_by_tag: dict = {}
        for _e in elements.values():
            _elem_by_tag[_e.elem_tag] = _e

        _base_elem_coords = []
        for eid, end in base_elements:
            elem = elements.get(str(eid)) or _elem_by_tag.get(eid)
            if elem is None:
                continue
            nid = elem.node_i if end == "i" else elem.node_j
            nd = self.mesh_model.nodes.get(nid)
            if nd is None:
                continue
            _base_elem_coords.append((eid, end, nd.x, nd.y, nd.z))

        modal_base_reactions = []
        for mode in range(1, num_modes + 1):
            ops.responseSpectrumAnalysis(SPECTRUM_TS_TAG, dof, "-mode", mode)

            rxn = {"fx": 0.0, "fy": 0.0, "fz": 0.0, "mx": 0.0, "my": 0.0, "mz": 0.0}
            for eid, end, nx, ny, nz in _base_elem_coords:
                try:
                    forces = ops.eleResponse(eid, "forces")
                except Exception:
                    continue
                if end == "i":
                    fx, fy, fz, mx, my, mz = (
                        forces[0],
                        forces[1],
                        forces[2],
                        forces[3],
                        forces[4],
                        forces[5],
                    )
                else:
                    fx, fy, fz, mx, my, mz = (
                        forces[6],
                        forces[7],
                        forces[8],
                        forces[9],
                        forces[10],
                        forces[11],
                    )

                rxn["fx"] += fx
                rxn["fy"] += fy
                rxn["fz"] += fz
                # Overturning: direct moment + force × lever-arm about fixed reference
                dx = nx - _cx
                dy = ny - _cy
                dz = nz - _z_base
                rxn["mx"] += mx + fz * dy - fy * dz
                rxn["my"] += my + fx * dz - fz * dx
                rxn["mz"] += mz + fy * dx - fx * dy

            modal_base_reactions.append(rxn)

        # ── CQC / SRSS per component ───────────────────────────
        dof_map = {"X": (0, 4), "Y": (1, 3), "Z": (2, 4)}
        #   X: shear=fx(idx 0), overturning=my(idx 4)
        #   Y: shear=fy(idx 1), overturning=mx(idx 3)  ← was mz before fix
        #   Z: shear=fz(idx 2), overturning=my(idx 4)
        f_idx, m_idx = dof_map[direction]
        comp_order = ["fx", "fy", "fz", "mx", "my", "mz"]

        # Keep scalar arrays for backward compat
        modal_base_shear = [r[comp_order[f_idx]] for r in modal_base_reactions]
        modal_base_moment = [r[comp_order[m_idx]] for r in modal_base_reactions]

        base_reactions_cqc = {}
        base_reactions_srss = {}
        for comp in comp_order:
            vals = [r[comp] for r in modal_base_reactions]
            base_reactions_cqc[comp] = cqc_combine(vals, omega, damp_ratios)
            base_reactions_srss[comp] = math.sqrt(sum(v * v for v in vals))

        base_shear_cqc = base_reactions_cqc[comp_order[f_idx]]
        base_shear_srss = base_reactions_srss[comp_order[f_idx]]
        base_moment_cqc = base_reactions_cqc[comp_order[m_idx]]
        base_moment_srss = base_reactions_srss[comp_order[m_idx]]

        result = {
            "modal_base_shear": modal_base_shear,
            "modal_base_moment": modal_base_moment,
            "base_shear_cqc": base_shear_cqc,
            "base_shear_srss": base_shear_srss,
            "base_moment_cqc": base_moment_cqc,
            "base_moment_srss": base_moment_srss,
            "modal_periods": modal_periods,
            # New: full 6-DoF base reactions per-mode and combined
            "modal_base_reactions": modal_base_reactions,
            "base_reactions_cqc": base_reactions_cqc,
            "base_reactions_srss": base_reactions_srss,
        }

        if print_results:
            print(f"\n===== RESPONSE SPECTRUM ({direction}) =====")
            print(f"{'Mode':>5} {'Period(s)':>10} {'Shear (kN)':>14} {'Moment (kN-m)':>16}")
            print("-" * 48)
            for i, (T, v, m) in enumerate(
                zip(modal_periods[:num_modes], modal_base_shear, modal_base_moment)
            ):
                print(f"{i + 1:5d} {T:10.4f} {v:14.2f} {m:16.2f}")
            print("-" * 48)
            print(f"{'CQC':>5} {'':>10} {base_shear_cqc:14.2f} {base_moment_cqc:16.2f}")
            print(f"{'SRSS':>5} {'':>10} {base_shear_srss:14.2f} {base_moment_srss:16.2f}")
            print()

        return result

    # =========================================================================
    # RS element forces (after run_response_spectrum_analysis)
    # =========================================================================
    def extract_element_rs_forces(
        self,
        num_modes: int,
        modal_periods: list[float],
        spectrum_periods: list[float],
        spectrum_accels: list[float],
        direction: str = "X",
        damping_ratio: float = 0.05,
        print_results: bool = True,
    ) -> dict[str, Any]:
        """Run RS analysis and return CQC‑combined element forces sorted by height.

        For each element this returns the CQC‑combined moments (My_i, My_j,
        Mz_i, Mz_j) and the corresponding shears derived from the moment
        gradient (Vy = dMz/dx, Vz = dMy/dx).

        Args:
            Same as :meth:`run_response_spectrum_analysis`.

        Returns:
            Dictionary with keys:

            * ``'element_results'`` — list of dicts sorted by elevation, each
              containing ``elem_id``, ``z_bot``, ``z_mid``, ``Vy_i``, ``Vy_j``,
              ``Vz_i``, ``Vz_j``, ``My_i``, ``My_j``, ``Mz_i``, ``Mz_j``.
            * ``'modal_periods'``, ``'omega'`` — for diagnostics.
        """
        if self.config.get("verbose"):
            print("Extracting element RS forces...")

        omega = [2.0 * math.pi / T if T > 0 else 0.0 for T in modal_periods]
        damp_ratios = [damping_ratio] * num_modes

        dof = {"X": 1, "Y": 2, "Z": 3}[direction]

        SPECTRUM_TS_TAG = 9999

        elements = self.mesh_model.frame_elements

        # Pre-compute element info + storage
        elem_data = {}
        for eid, elem in elements.items():
            if getattr(elem, "inactive", False):
                continue
            ni = self.mesh_model.nodes.get(elem.node_i)
            nj = self.mesh_model.nodes.get(elem.node_j)
            if ni is None or nj is None:
                continue
            z_i, z_j = ni.z, nj.z
            if z_i > z_j:
                z_i, z_j = z_j, z_i
            ops_tag = self.frame_tag_map.get(eid, elem.elem_tag)
            elem_data[eid] = {
                "tag": ops_tag,
                "elem_id": eid,
                "z_bot": z_i,
                "z_mid": (z_i + z_j) * 0.5,
                "My_i": [],
                "My_j": [],
                "Mz_i": [],
                "Mz_j": [],
            }

        # Mode-by-mode extraction
        for mode in range(1, num_modes + 1):
            ops.responseSpectrumAnalysis(SPECTRUM_TS_TAG, dof, "-mode", mode)
            for eid, ed in elem_data.items():
                try:
                    forces = ops.eleResponse(ed["tag"], "forces")
                except Exception:
                    forces = [0.0] * 12
                ed["My_i"].append(forces[4])
                ed["My_j"].append(forces[10])
                ed["Mz_i"].append(forces[5])
                ed["Mz_j"].append(forces[11])

        # CQC combine per element and compute shears
        element_results = []
        for eid, ed in elem_data.items():
            ne = len(ed["My_i"])
            n_use = min(ne, num_modes)
            o_use = omega[:n_use]
            d_use = damp_ratios[:n_use]

            My_i = cqc_combine(ed["My_i"][:n_use], o_use, d_use)
            My_j = cqc_combine(ed["My_j"][:n_use], o_use, d_use)
            Mz_i = cqc_combine(ed["Mz_i"][:n_use], o_use, d_use)
            Mz_j = cqc_combine(ed["Mz_j"][:n_use], o_use, d_use)

            # Element length
            elem = elements.get(eid)
            if elem:
                ni = self.mesh_model.nodes.get(elem.node_i)
                nj = self.mesh_model.nodes.get(elem.node_j)
                L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z) if ni and nj else 1.0
            else:
                L = 1.0

            # Shear from moment gradient
            Vy_i = (Mz_i - Mz_j) / L if L > 1e-12 else 0.0
            Vy_j = Vy_i
            Vz_i = (My_i - My_j) / L if L > 1e-12 else 0.0
            Vz_j = Vz_i

            element_results.append(
                {
                    "elem_id": ed["elem_id"],
                    "z_bot": ed["z_bot"],
                    "z_mid": ed["z_mid"],
                    "Vy_i": Vy_i,
                    "Vy_j": Vy_j,
                    "Vz_i": Vz_i,
                    "Vz_j": Vz_j,
                    "My_i": My_i,
                    "My_j": My_j,
                    "Mz_i": Mz_i,
                    "Mz_j": Mz_j,
                }
            )

        # Sort by height
        element_results.sort(key=lambda r: r["z_mid"])

        if print_results:
            print(
                f"\n===== RESPONSE SPECTRUM RESULTS ({direction} only, CQC) FOR ALL ELEMENTS ====="
            )
            header = (
                f"{'Elem':>30} {'Z_bot(m)':>10} {'Z_mid(m)':>10} {'End':>5} "
                f"{'Vy (kN)':>12} {'Vz (kN)':>12} {'My (kN-m)':>12} {'Mz (kN-m)':>12}"
            )
            print(header)
            print("-" * len(header))
            for r in element_results:
                eid_str = f"{r['elem_id']:30s}"
                print(
                    f"{eid_str} {r['z_bot']:10.2f} {r['z_mid']:10.2f} {'I':>5} "
                    f"{r['Vy_i']:12.2f} {r['Vz_i']:12.2f} {r['My_i']:12.2f} {r['Mz_i']:12.2f}"
                )
                print(
                    f"{eid_str} {r['z_bot']:10.2f} {r['z_mid']:10.2f} {'J':>5} "
                    f"{r['Vy_j']:12.2f} {r['Vz_j']:12.2f} {r['My_j']:12.2f} {r['Mz_j']:12.2f}"
                )

        return {
            "element_results": element_results,
            "modal_periods": modal_periods,
            "omega": omega,
        }

    # =========================================================================
    # RS nodal displacements (from mode‑shape combination)
    # =========================================================================
    def compute_rs_nodal_displacements(
        self,
        num_modes: int,
        modal_periods: list[float],
        eigenvalues: list[float],
        spectrum_func,
        direction: str = "X",
        damping_ratio: float = 0.05,
        return_srss: bool = False,
    ) -> Union[
        dict[int, tuple[float, float, float]],
        tuple[dict[int, tuple[float, float, float]], dict[int, tuple[float, float, float]]],
    ]:
        """Compute CQC‑ (and optionally SRSS‑) combined peak nodal
        displacements from RS analysis.

        Uses mode‑shape superposition rather than re‑running the RS analysis:

            u_m = Γ_m · φ_m · Sa_m / ω²_m

        then combined with CQC (and optionally SRSS).

        Args:
            num_modes: Number of modes.
            modal_periods: Natural periods of each mode (s).
            eigenvalues: Eigenvalues (ω²) from :meth:`run_modal_analysis`.
            spectrum_func: Callable ``f(T) → Sa`` in **m/s²**.
            direction: Excitation direction ``'X'``, ``'Y'``, or ``'Z'``.
            damping_ratio: Damping ratio for CQC correlation.
            return_srss: If True, return ``(cqc_result, srss_result)``
                as a tuple of two dicts.  If False (default), return
                only ``cqc_result`` for backward compatibility.

        Returns:
            Dict mapping ``node_tag`` → ``(dx, dy, dz)`` in model length
            units.  When ``return_srss=True``, returns a tuple of two
            such dicts: ``(cqc, srss)``.
        """
        dof = {"X": 1, "Y": 2, "Z": 3}[direction]
        dof_idx = dof - 1

        # Get participation factors from modalProperties
        try:
            mp = ops.modalProperties("-return", "-unorm")
        except Exception:
            mp = {}
        mass_key = (
            "partiMassMX"
            if direction == "X"
            else "partiMassMY"
            if direction == "Y"
            else "partiMassMZ"
        )
        eff_masses = mp.get(mass_key, [0.0] * num_modes)

        omega = [2.0 * math.pi / T if T > 0 else 0.0 for T in modal_periods]
        damp = [damping_ratio] * num_modes

        node_tags = list(ops.getNodeTags())

        per_mode = {tag: {d: [] for d in range(3)} for tag in node_tags}

        for m in range(num_modes):
            if eigenvalues[m] <= 1e-12 or omega[m] <= 1e-12:
                for tag in node_tags:
                    for d in range(3):
                        per_mode[tag][d].append(0.0)
                continue

            T = modal_periods[m]
            Sa = spectrum_func(T)
            Gamma = math.sqrt(abs(eff_masses[m])) if eff_masses[m] != 0 else 0.0
            factor = Gamma * Sa / (omega[m] ** 2)

            if abs(factor) < 1e-15:
                for tag in node_tags:
                    for d in range(3):
                        per_mode[tag][d].append(0.0)
                continue

            for tag in node_tags:
                phi = ops.nodeEigenvector(tag, m + 1, dof)
                per_mode[tag][dof_idx].append(phi * factor)
                for d in range(3):
                    if d != dof_idx:
                        per_mode[tag][d].append(0.0)

        cqc_result = {}
        srss_result = {}
        for tag in node_tags:
            cqc_vals = tuple(cqc_combine(per_mode[tag][d], omega, damp) for d in range(3))
            cqc_result[tag] = cqc_vals
            srss_vals = tuple(math.sqrt(sum(v * v for v in per_mode[tag][d])) for d in range(3))
            srss_result[tag] = srss_vals

        if return_srss:
            return cqc_result, srss_result
        return cqc_result

    def extract_mode_shapes(
        self, num_modes: int
    ) -> dict[int, dict[int, tuple[float, float, float]]]:
        """Extract mode shape displacements for each node and each mode.

        Must be called **after** :meth:`run_modal_analysis`.

        Args:
            num_modes: Number of modes to extract.

        Returns:
            ``{mode_index: {node_tag: (dx, dy, dz)}}`` where *mode_index*
            is 0‑based and displacements are raw eigenvector components.
        """
        node_tags = list(ops.getNodeTags())
        dof_map = {0: 1, 1: 2, 2: 3}
        shapes: dict[int, dict[int, tuple]] = {}
        for m in range(num_modes):
            mode_num = m + 1
            per_node: dict[int, tuple] = {}
            for tag in node_tags:
                dx = ops.nodeEigenvector(tag, mode_num, dof_map[0])
                dy = ops.nodeEigenvector(tag, mode_num, dof_map[1])
                dz = ops.nodeEigenvector(tag, mode_num, dof_map[2])
                per_node[tag] = (dx, dy, dz)
            shapes[m] = per_node
        return shapes

    def extract_static_element_forces(self) -> dict[int, dict[str, float]]:
        """Extract element end forces in the **local** coordinate system.

        Must be called **after** :meth:`run_static_analysis`.

        Returns:
            Dict mapping ``elem_tag`` → dict with keys ``'Fx'``, ``'Fy'``,
            ``'Fz'``, ``'Mx'``, ``'My'``, ``'Mz'`` (global forces at the
            I‑end of the element) and ``'Fx_j'``, ``'Fy_j'``, ``'Fz_j'``,
            ``'Mx_j'``, ``'My_j'``, ``'Mz_j'`` (J‑end).
        """
        elements = self.mesh_model.frame_elements
        results = {}
        for eid, elem in elements.items():
            if getattr(elem, "inactive", False):
                continue
            # Resolve the OpenSees element tag — may differ from elem.elem_tag
            # when the Preprocessor creates frame elements with deterministic
            # tags stored in frame_tag_map.
            tag = self.frame_tag_map.get(eid, elem.elem_tag)
            try:
                f = ops.eleResponse(tag, "localForces")
            except Exception:
                continue
            f = _normalise_frame_response(f)
            if f is None:
                # Empty or short unsupported response — skip this element
                # without aborting the extraction.
                continue
            f_i_local = np.array([f[0], f[1], f[2]])
            m_i_local = np.array([f[3], f[4], f[5]])
            f_j_local = np.array([f[6], f[7], f[8]])
            m_j_local = np.array([f[9], f[10], f[11]])

            results[tag] = {
                "Fx": f_i_local[0],
                "Fy": f_i_local[1],
                "Fz": f_i_local[2],
                "Mx": m_i_local[0],
                "My": m_i_local[1],
                "Mz": m_i_local[2],
                "Fx_j": f_j_local[0],
                "Fy_j": f_j_local[1],
                "Fz_j": f_j_local[2],
                "Mx_j": m_j_local[0],
                "My_j": m_j_local[1],
                "Mz_j": m_j_local[2],
            }
        return results

    def extract_static_shell_forces(self) -> dict[str, dict[str, Any]]:
        """Extract shell element forces after a static analysis.

        For each active (non-inactive, non-loads-only) area element,
        queries ``ops.eleResponse(tag, 'forces')`` and returns the
        local stress resultants (membrane + bending per unit width).

        ShellMITC4 returns 8 floats per element::

            [fx, fy, fxy, mx, my, mxy, ?, ?]

        The first six are the local force and moment resultants (per
        unit width).  The last two are element volume and thickness
        (not force resultants).

        Must be called **after** :meth:`run_static_analysis`.

        Returns
        -------
        dict
            ``{area_sap_id: {
                'elem_tag': int,
                'node_tags': list[int],
                'sec_name': str,
                'fx': float,   # membrane direct (force/width)
                'fy': float,   # membrane direct (force/width)
                'fxy': float,  # membrane shear (force/width)
                'mx': float,   # bending moment (moment/width)
                'my': float,   # bending moment (moment/width)
                'mxy': float,  # twisting moment (moment/width)
            }}``
        """
        results: dict[str, dict[str, Any]] = {}
        areas = self.mesh_model.area_elements
        loads_only = self.mesh_model.loads_only_area_ids
        for aid, area in areas.items():
            if aid in loads_only:
                continue
            if getattr(area, "inactive", False):
                continue
            elem_tag = self._shell_tag_map.get(aid)
            if elem_tag is None:
                continue
            try:
                f = ops.eleResponse(elem_tag, "section", 1, "forces")
            except Exception:
                continue
            # Shell section forces: [Nx, Ny, Nxy, Mx, My, Mxy, ?, ?]
            # (per-unit-width resultants — "forces" alone returns the raw
            # 24-entry local nodal-force vector for shells, not resultants.)
            results[aid] = {
                "elem_tag": elem_tag,
                "node_tags": [
                    nd.node_tag
                    for nd_id in area.node_ids
                    if (nd := self.mesh_model.nodes.get(nd_id)) is not None
                ],
                "sec_name": self.mesh_model.area_assignments.get(aid, ""),
                "fx": f[0],
                "fy": f[1],
                "fxy": f[2],
                "mx": f[3],
                "my": f[4],
                "mxy": f[5],
            }
        return results

    # ═══════════════════════════════════════════════════════════════
    # Utilities
    # ═══════════════════════════════════════════════════════════════

    def get_local_axes(self, elem: FrameElement) -> tuple[np.ndarray, ...]:
        """Compute local x, y, z unit vectors for a frame element.

        Uses ``get_SAP_vecxz`` from the geometry module (which handles
        the SAP2000 vecxz convention) combined with the element's
        section rotation angle.

        Args:
            elem: Frame element with ``node_i``, ``node_j``, and
                ``angle`` attributes.

        Returns:
            ``(vx, vy, vz)`` tuple of three unit vectors forming a
            right‑handed local coordinate system.

        Raises:
            ValueError: If either node cannot be resolved, or the
                element has zero length.
        """
        from ..model.geometry import get_local_axes

        ni = self.mesh_model.nodes.get(elem.node_i)
        nj = self.mesh_model.nodes.get(elem.node_j)
        if ni is None or nj is None:
            raise ValueError(f"Cannot resolve nodes for {elem.elem_id}")
        vx = np.array([nj.x - ni.x, nj.y - ni.y, nj.z - ni.z])
        return get_local_axes(vx, getattr(elem, "angle", 0.0))

    # ═══════════════════════════════════════════════════════════════
    # Load equilibrium check
    # ═══════════════════════════════════════════════════════════════

    def check_load_equilibrium(self) -> "pd.DataFrame":
        """Check equilibrium between applied loads and reactions.

        For each load pattern in the model, runs a static analysis
        with that pattern alone and compares the applied load totals
        (from :attr:`load_totals`) against the summed reactions.

        Reaction moments include the force × lever‑arm overturning
        contribution via
        :func:`~fea_toolkit.utils.sum_reactions_with_overturning`
        (same fixed centroid approach used for RS analysis in
        :meth:`run_response_spectrum_analysis`).

        Returns:
            A ``pandas.DataFrame`` with one row per pattern and
            columns for applied force, reaction force, and
            the equilibrium imbalance ``Δ = applied + reaction``
            (should be near zero for a correctly built model).
        """
        # TODO: Move `import pandas as pd` to module-level when the
        # optional dependency is declared in pyproject.toml (currently `pandas`
        # is not listed as a dependency, so the lazy import avoids breakage).
        import pandas as pd

        rows: list = []
        fu = self.mesh_model.units.get("F", "?")
        # Collect pattern names from the MeshModel's load_patterns dict
        # (matching the legacy Builder which iterates self.model.load_patterns).
        # Skip patterns with zero applied loads (e.g. SLX, SLY).
        _patterns = set()
        for pname, lp in self.mesh_model.load_patterns.items():
            # Check for any loads associated with this pattern
            has_loads = (
                lp.self_weight_factor > 0
                or any(ld.pattern == pname for ld in self.mesh_model.frame_dist_loads)
                or any(ld.pattern == pname for ld in self.mesh_model.joint_loads)
                or any(ld.pattern == pname for ld in self.mesh_model.area_gravity_loads)
                or any(ld.pattern == pname for ld in self.mesh_model.edge_loads_from_areas)
            )
            if has_loads:
                _patterns.add(pname)
        for pname in sorted(_patterns, key=str.casefold):
            result = self.run_static_analysis(
                extract_reactions=True,
                pattern_scales={pname: 1.0},
            )
            rxn = result.get("reactions", {})
            rx = sum(v["fx"] for v in rxn.values())
            ry = sum(v["fy"] for v in rxn.values())
            rz = sum(v["fz"] for v in rxn.values())

            rows.append(
                {
                    "Load Pattern": pname,
                    f"Reaction Fx ({fu})": round(rx, 1),
                    f"Reaction Fy ({fu})": round(ry, 1),
                    f"Reaction Fz ({fu})": round(rz, 1),
                }
            )

        return pd.DataFrame(rows)

    # ═══════════════════════════════════════════════════════════════
    # Export
    # ═══════════════════════════════════════════════════════════════

    def export_results(
        self,
        filepath: str,
        static_results: Optional[dict[str, Any]] = None,
        modal_result: Optional[dict[str, Any]] = None,
        mode_shapes: Optional[dict] = None,
        rs_results: Optional[dict[str, dict]] = None,
        rs_element_forces: Optional[dict[str, Any]] = None,
        rs_nodal_displacements: Optional[dict[int, tuple]] = None,
        fmt: str = "npz",
    ) -> str:
        """Export model geometry and analysis results to a unified file.

        Delegates to :func:`~fea_toolkit.io.unified_writer.write_results`
        using the builder's ``mesh_model`` and the provided results.

        Args:
            filepath: Output file path (``.npz`` or ``.h5``).
            static_results: Dict from :meth:`run_static_analysis`.
            modal_result: Dict from
                :meth:`~fea_toolkit.opensees.builder.OpenSeesBuilder.run_modal_analysis`.
            mode_shapes: Mode shape eigenvectors ``{mode_idx: {tag: (dx,dy,dz)}}``.
            rs_results: Response-spectrum results dict.
            rs_element_forces: Dict from :meth:`extract_element_rs_forces`.
            rs_nodal_displacements: Dict from
                :meth:`compute_rs_nodal_displacements`.
            fmt: ``"npz"`` (default) or ``"h5"``.

        Returns:
            Absolute path to the written file.
        """
        from ..io.unified_writer import write_results

        return write_results(
            path=filepath,
            mesh_model=self.mesh_model,
            static_results=static_results,
            modal_result=modal_result,
            mode_shapes=mode_shapes,
            rs_results=rs_results,
            rs_element_forces=rs_element_forces,
            rs_nodal_displacements=rs_nodal_displacements,
            fmt=fmt,
            config=self.config,
        )

    # ═══════════════════════════════════════════════════════════════
    # Pushover analysis
    # ═══════════════════════════════════════════════════════════════

    def run_pushover_analysis(
        self,
        gravity_patterns: dict[str, float],
        lateral_load_type: str = "uniform",
        lateral_pattern_name: Optional[str] = None,
        lateral_point_nodes: Optional[list[int]] = None,
        lateral_direction: str = "X",
        control_node_tag: Optional[int] = None,
        max_disp: float = 0.5,
        num_steps: int = 100,
        fundamental_period: Optional[float] = None,
        mode_shapes: Optional[dict] = None,
        mode_index: int = 0,
        node_mass_overrides: Optional[dict[str, float]] = None,
        print_progress: bool = True,
        record_element_forces: bool = False,
    ) -> dict[str, Any]:
        """Run a displacement‑controlled pushover analysis.

        **Two‑stage process:**

        1. **Gravity** — apply the specified gravity patterns via
           :meth:`run_static_analysis` with ``extract_reactions=True``.
        2. **Lateral push** — lock gravity, apply lateral loads, then
           push a control node in increments using
           ``DisplacementControl`` integration.

        Five lateral load types are supported:

        * ``'uniform'`` — mass‑proportional acceleration (uniform
          acceleration of the structure).
        * ``'triangular'`` — load proportional to :math:`m_i h_i^k`
          per ASCE 7 equivalent lateral force.
        * ``'mode1'`` — load proportional to the fundamental
          eigenvector :math:`\\mathbf{M} \\boldsymbol{\\phi}_1`
          (modal pushover).
        * ``'pattern'`` — read an existing SAP2000 load pattern
          (frame distributed loads) from the model data.
        * ``'point'`` — a unit point load at the node(s) given by
          *lateral_point_nodes* (default: the control node).  A single
          point load reproduces the Duong et al. (2007) and Vecchio &
          Emara (1992) test setups, which pushed the top beam with one
          actuator.

        Args:
            gravity_patterns: Dict mapping load pattern name → scale
                factor for gravity loads, e.g. ``{"DEAD": 1.0}``.
            lateral_load_type: ``'uniform'``, ``'triangular'``,
                ``'mode1'``, ``'pattern'``, or ``'point'``.
            lateral_pattern_name: SAP2000 load pattern name (required
                when *lateral_load_type* is ``'pattern'``).
            lateral_point_nodes: OpenSees node tags loaded by the
                ``'point'`` type (each receives a unit load in the push
                direction).  ``None`` → the control node only.
            lateral_direction: Push direction — ``'X'``, ``'Y'``, or
                ``'Z'``.
            control_node_tag: OpenSees node tag for displacement
                control.  ``None`` = auto‑select (highest unrestrained
                node in the push direction).
            max_disp: Target displacement at the control node (m).
            num_steps: Number of push steps.
            fundamental_period: Fundamental period (s) for
                ``'triangular'`` load exponent ``k``.  ``None`` uses
                the period of the first mode from the model.
            mode_shapes: Dict ``{mode_idx: {node_tag: (dx, dy, dz)}}``
                from :meth:`extract_mode_shapes`; required for
                ``'mode1'``.
            mode_index: Mode index (0‑based) for ``'mode1'``.
            node_mass_overrides: Optional dict mapping **node ID** →
                mass scale factor (multiplier) applied after seismic
                masses are computed.  Enables per‑storey masonry mass
                corrections (``factor = 1.0 + m_storey_extra/m_storey``)
                that change the mass distribution rather than a single
                global scale.  Node IDs match
                :attr:`node_masses` keys (string SAP IDs), not
                OpenSees tags.
            print_progress: Print a progress line per step.
            record_element_forces: When ``True``, capture the local end
                forces of every active frame element after each push step
                and expose them as ``results["element_forces_history"]``
                (list aligned with ``results["step"]``; index 0 is the
                post-gravity state).  Required by
                :func:`fea_toolkit.analysis.shear_capacity.report_shear_failure`.

        Returns:
            Dict with keys ``step``, ``control_disp``, ``base_shear``,
            ``status``, ``gravity_displacements``, ``control_node``,
            ``dof``, ``lateral_load_type`` and (when
            ``record_element_forces=True``) ``element_forces_history``.
        """
        valid_types = {"uniform", "triangular", "mode1", "pattern", "point"}
        if lateral_load_type not in valid_types:
            raise ValueError(
                f"Unknown lateral_load_type '{lateral_load_type}'. Choose from {valid_types}."
            )
        if lateral_load_type == "pattern" and not lateral_pattern_name:
            raise ValueError("lateral_pattern_name is required when lateral_load_type='pattern'")

        if self.config.get("verbose") or print_progress:
            print(
                f"Running pushover: {lateral_load_type} in "
                f"{lateral_direction}, {num_steps} steps, "
                f"max disp = {max_disp:.3f} m"
            )

        dof = {"X": 1, "Y": 2, "Z": 3}[lateral_direction]

        # ── Rebuild with fiber sections ──────────────────────────
        # Pushover always attempts fiber sections (nonlinear).  Check
        # whether any section overrides the base to_fiber_patches —
        # if none do, fall back to elastic sections.
        # Note: brace_truss is orthogonal — braces use Hysteretic truss
        # elements while beams/columns can still use fiber sections.
        _use_fiber = True
        for sec in self.mesh_model.sections.values():
            if isinstance(sec, ShellSection):
                continue
            try:
                sec.to_fiber_patches(mat_tag=1)
            except NotImplementedError:
                _use_fiber = False
                import warnings

                warnings.warn(
                    f"Section '{sec.name}' does not support fiber patches — "
                    f"falling back to elastic sections for all frame elements. "
                    f"Consider implementing to_fiber_patches() for mixed "
                    f"steel/RC models.",
                    UserWarning,
                    stacklevel=3,
                )
                break

        if not _use_fiber:
            overrides: dict[str, Any] = {
                "element_type": "elasticBeamColumn",
                "create_fiber_sections": False,
                "use_elastic_sections": True,
            }
            self.build_domain(config_overrides=overrides)
        else:
            self.rebuild_with_fiber_sections(
                brace_selection=self._brace_selection,
            )

        # ── Re-apply edge constraints ────────────────────────────
        _spring_scale = float(self.config.get("pushover_spring_scale", 1.0))
        if self._saved_edge_constraints and _spring_scale > 0:
            for args in self._saved_edge_constraints:
                coarse_edges, fine_nodes, coarse_elems, tolerance, k, verbose = args
                if _spring_scale != 1.0 and k is not None:
                    k = k * _spring_scale
                self.apply_edge_constraints(
                    coarse_edges=coarse_edges,
                    fine_nodes=fine_nodes,
                    coarse_elements=coarse_elems,
                    tolerance=tolerance,
                    penalty_stiffness=k,
                    verbose=verbose or self.config.get("verbose", False),
                )
            if self.config.get("verbose", False) or print_progress:
                n = len(self._saved_edge_constraints)
                print(f"  Re-applied edge constraints from {n} tear(s)")

        # ── Seismic masses (for lateral load shape) ──────────────
        try:
            self.compute_seismic_masses()
        except Exception:
            if self.config.get("verbose"):
                print("  compute_seismic_masses failed, using fallback masses")
            self._compute_fallback_masses()

        # ── Apply per-node mass overrides (masonry/storey scaling) ─
        if node_mass_overrides:
            for nid, factor in node_mass_overrides.items():
                if nid not in self.node_masses or factor <= 0:
                    continue
                scaled = self.node_masses[nid] * factor
                self.node_masses[nid] = scaled
                node = self.mesh_model.nodes.get(nid)
                if node is None:
                    continue
                # Re-issue: ops.mass() overwrites the previous value,
                # keeping the OpenSees model consistent with the scaled
                # Python-side masses (affects dynamic analysis + lateral
                # load shapes).
                with contextlib.suppress(Exception):
                    ops.mass(node.node_tag, scaled, scaled, scaled, 0.0, 0.0, 0.0)
            if self.config.get("verbose") or print_progress:
                print(
                    f"  Applied node_mass_overrides to "
                    f"{len([f for f in node_mass_overrides.values() if f > 0])} node(s)"
                )

        # ── Gravity analysis ─────────────────────────────────────
        # Create loads directly (domain was just rebuilt by
        # rebuild_with_fiber_sections above).  Avoid passing
        # pattern_scales to run_static_analysis, which would trigger
        # a second build_domain() without fiber overrides, replacing
        # dispBeamColumn elements with elasticBeamColumn.
        self.create_loads(pattern_scales=gravity_patterns)
        grav_results = self.run_static_analysis(
            extract_reactions=True,
        )
        grav_disp = grav_results.get("nodal_displacements", {})

        # ── Gravity diagnostic: reaction summary ────────────────
        # Report the total applied vs. reacted vertical load so the
        # user can verify the design gravity combination (λ=1.0) is
        # actually in place before lateral pushover begins.
        _lr_check = grav_results.get("load_reaction_check", {})
        if _lr_check:
            _applied = _lr_check.get("applied_fz", 0.0)
            _reaction = _lr_check.get("reaction_fz", 0.0)
            logger.info(
                "  Gravity reached λ=1.0 — applied Fz=%.3f, reacted Fz=%.3f, Δ=%.3f",
                _applied,
                _reaction,
                _lr_check.get("delta", 0.0),
            )
        if print_progress:
            _reac = grav_results.get("reactions", {})
            _sum_fz = sum(float(r.get("fz", 0.0)) for r in _reac.values())
            _n_full = sum(1 for r in self.mesh_model.restraints.values() if all(r.dofs))
            print(
                f"  Gravity converged — total vertical reaction = {_sum_fz:.1f} "
                f"({_n_full} fully-fixed base node(s))"
            )

        # ── Gravity diagnostic: concrete/rbar strain check ──────
        # After gravity converges, probe the extreme fibre strains at
        # the end sections of every fiber frame element.  Flags any
        # element whose concrete reaches crushing or rebar reaches
        # yield under the *design gravity load alone* — a useful
        # pre-pushover damage assessment.  Purely diagnostic (never
        # raises); wrapped so a query failure cannot abort the run.
        try:
            # threshold defaults — concrete crushing ~ -0.003
            # rebar yield ~ 0.0025 (typical εy ≈ 500 MPa / 200 GPa)
            _crush_eps = -0.0030
            _yield_eps = 0.0025
            _flagged: list[tuple[str, float]] = []
            _n_scanned = 0
            _assignments = self.mesh_model.frame_assignments or {}
            for eid, elem in self.mesh_model.frame_elements.items():
                tag = self.frame_tag_map.get(eid)
                if tag is None:
                    continue
                sec_name = _assignments.get(eid, "")
                if not sec_name:
                    continue
                _sec = self.mesh_model.sections.get(sec_name)
                if _sec is None:
                    continue
                try:
                    # first integration-point section deformation
                    sec_def = ops.eleResponse(int(tag), "section", 1, "deformation")
                except Exception:
                    continue
                if not sec_def or len(sec_def) < 3:
                    continue
                # axial strain eps0 + curvature about local z × h/2
                eps0 = float(sec_def[0])
                kz = float(sec_def[2])
                _n_scanned += 1
                half_depth = 0.5 * float(
                    getattr(_sec, "h", getattr(_sec, "depth", getattr(_sec, "t3", 0.0))) or 0.5
                )
                strain_upper = eps0 + kz * half_depth
                strain_lower = eps0 - kz * half_depth
                eps_max = max(strain_upper, strain_lower)
                eps_min = min(strain_upper, strain_lower)
                if eps_min < _crush_eps:
                    _flagged.append((str(eid), eps_min))
                elif eps_max > _yield_eps:
                    _flagged.append((str(eid), eps_max))
            if _flagged:
                logger.warning(
                    "  ⚠ Gravity-only damage check: %d / %d frame element(s) "
                    "exceed strain limits (crush < %.4f or yield > %.4f): %s",
                    len(_flagged),
                    _n_scanned,
                    _crush_eps,
                    _yield_eps,
                    _flagged[:8],
                )
            elif print_progress:
                print(
                    f"  Gravity-only damage check: 0 / {_n_scanned} "
                    f"frame element(s) exceed concrete crush / rebar yield strain"
                )
        except Exception:
            logger.debug("  Gravity damage check skipped", exc_info=True)

        # ── Control node auto‑select ─────────────────────────────
        if control_node_tag is None:
            candidate = None
            max_z = -1e12
            for nid, nd in self.mesh_model.nodes.items():
                restraint = self.mesh_model.restraints.get(nid)
                if restraint and len(restraint.dofs) > dof - 1 and restraint.dofs[dof - 1] == 1:
                    continue  # restrained in push direction
                try:
                    z = ops.nodeCoord(nd.node_tag)[2]
                except Exception:
                    continue
                if z > max_z:
                    max_z = z
                    candidate = nd.node_tag
            if candidate is not None:
                control_node_tag = candidate
            else:
                raise RuntimeError(
                    "Could not auto-select control node — no unrestrained nodes found"
                )

        if print_progress:
            print(f"  Control node = {control_node_tag}")

        # ── Record gravity control displacement ──────────────────
        try:
            grav_ctrl_disp = ops.nodeDisp(int(control_node_tag))[dof - 1]
        except Exception:
            grav_ctrl_disp = 0.0

        # ── Lock gravity ─────────────────────────────────────────
        ops.loadConst("-time", 0.0)

        # Find a free pattern tag
        _pat_tag = 9001
        try:
            existing = ops.getLoadPatternTags()
            if existing:
                _pat_tag = max(*existing, 9000) + 1
        except Exception:
            pass

        # ── Apply lateral loads ──────────────────────────────────
        if lateral_load_type == "pattern":
            # Use existing SAP2000 frame distributed loads projected
            # onto the push direction.
            dir_map = {"Gravity": (0, 0, -1), "X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}

            for ld in self.mesh_model.frame_dist_loads:
                if ld.pattern != lateral_pattern_name:
                    continue

                gx, gy, gz = dir_map.get(ld.direction, (0, 0, 0))
                elem = self.mesh_model.frame_elements.get(ld.frame_id)
                if elem is None or getattr(elem, "inactive", False):
                    continue
                ops_tag = self.frame_tag_map.get(ld.frame_id, elem.elem_tag)

                wa, wb = float(ld.val_a), float(ld.val_b)
                aL, bL = ld.rdist_a, ld.rdist_b

                nd_i = self.mesh_model.nodes.get(elem.node_i)
                nd_j = self.mesh_model.nodes.get(elem.node_j)
                if nd_i is None or nd_j is None:
                    continue
                axis = np.array([nd_j.x - nd_i.x, nd_j.y - nd_i.y, nd_j.z - nd_i.z])
                try:
                    vx, vy, vz = get_local_axes(axis, getattr(elem, "angle", 0.0))
                except Exception:
                    continue

                T = np.column_stack([vx, vy, vz])
                g_local = np.linalg.solve(T, np.array([gx, gy, gz]))
                wy_a = g_local[1] * wa
                wz_a = g_local[2] * wa
                wx_a = g_local[0] * wa
                wy_b = g_local[1] * wb
                wz_b = g_local[2] * wb
                wx_b = g_local[0] * wb

                if abs(wa) < 1e-12 and abs(wb) < 1e-12:
                    continue

                is_uniform = abs(wa - wb) < 1e-12
                if is_uniform and abs(aL) < 1e-12 and abs(bL - 1.0) < 1e-12:
                    ops.eleLoad("-ele", ops_tag, "-type", "-beamUniform", wy_a, wz_a, wx_a)
                elif is_uniform:
                    ops.eleLoad("-ele", ops_tag, "-type", "-beamUniform", wy_a, wz_a, wx_a, aL, bL)
                else:
                    for i in range(4):
                        span = bL - aL
                        seg_a = aL + i * span / 4
                        seg_b = aL + (i + 1) * span / 4
                        xi = (i + 0.5) / 4
                        ops.eleLoad(
                            "-ele",
                            ops_tag,
                            "-type",
                            "-beamUniform",
                            wy_a + (wy_b - wy_a) * xi,
                            wz_a + (wz_b - wz_a) * xi,
                            wx_a + (wx_b - wx_a) * xi,
                            seg_a,
                            seg_b,
                        )

            if print_progress:
                n = sum(
                    1
                    for ld in self.mesh_model.frame_dist_loads
                    if ld.pattern == lateral_pattern_name
                )
                print(
                    f"  Applied lateral loads from pattern '{lateral_pattern_name}' ({n} load(s))"
                )
        else:
            ops.timeSeries("Linear", _pat_tag)
            ops.pattern("Plain", _pat_tag, _pat_tag)

            if lateral_load_type == "uniform":
                node_loads = self._compute_uniform_lateral_loads(
                    direction=lateral_direction,
                    node_masses=self.node_masses,
                )
            elif lateral_load_type == "triangular":
                node_loads = self._compute_triangular_lateral_loads(
                    direction=lateral_direction,
                    node_masses=self.node_masses,
                    fundamental_period=fundamental_period,
                )
            elif lateral_load_type == "mode1":
                if mode_shapes is None:
                    raise ValueError("mode_shapes is required when lateral_load_type='mode1'")
                node_loads = self._compute_mode_shape_lateral_loads(
                    direction=lateral_direction,
                    node_masses=self.node_masses,
                    mode_shapes=mode_shapes,
                    mode_index=mode_index,
                )
            elif lateral_load_type == "point":
                _pts = lateral_point_nodes or [int(control_node_tag)]
                node_loads = {int(t): (1.0, 0.0, 0.0) for t in _pts}
            else:
                node_loads = {}

            for tag, (fx, fy, fz) in node_loads.items():
                ops.load(int(tag), fx, fy, fz, 0.0, 0.0, 0.0)

            n_loaded = len(node_loads)
            if print_progress:
                print(f"  Applied lateral loads ({lateral_load_type}) to {n_loaded} node(s)")

        # ── Displacement‑controlled push analysis setup ──────────
        disp_inc = max_disp / max(num_steps, 1)

        # Use looser tolerances matching v1 (builder.py) pushover —
        # NormDispIncr with 1e-4 tolerance, 20 iterations, energy
        # norm.  Tight tolerances (1e-6/10 iter) prevent convergence
        # for mode-shape-based pushover patterns.
        _algo = self.config.get("solver_algorithm", "Newton")
        _test_tol = self.config.get("solver_test_tol", 1e-4)
        _test_iter = self.config.get("solver_test_max_iter", 20)
        _system = self.config.get("solver_system", "BandGen")

        ops.wipeAnalysis()
        _cs = self.config.get("solver_constraints", "Transformation")
        if self._edge_constraint_method == "penalty":
            _cs = "Penalty"
            ops.constraints("Penalty", 1.0e12, 1.0e12)
        else:
            ops.constraints(_cs)
        ops.numberer("RCM")
        ops.system(_system)
        ops.test("NormDispIncr", _test_tol, _test_iter, 0, 2)

        ops.integrator("DisplacementControl", int(control_node_tag), dof, disp_inc)
        ops.analysis("Static")

        # ── Per-step recording setup (opt-in) ─────────────────────
        record = self.config.get("record_pushover_steps", False)
        record_sel = self.config.get("pushover_record_selection", None)
        record_frames: set[str] = set()
        record_areas: set[str] = set()
        record_node_tags: set[int] = set()
        if record:
            if record_sel is not None:
                # Pass storey data if available in config (for story-based Selection filtering)
                _storey_data = self.config.get("pushover_record_storey_data", None)
                record_frames, record_areas = record_sel.resolve_to_mesh_sets(
                    self.mesh_model,
                    storey_data=_storey_data,
                )
            else:
                record_frames = {
                    eid
                    for eid, fe in self.mesh_model.frame_elements.items()
                    if not getattr(fe, "inactive", False)
                }
                record_areas = {
                    aid
                    for aid, ae in self.mesh_model.area_elements.items()
                    if not getattr(ae, "inactive", False)
                }
            # Collect node tags from selected frames/areas only
            for eid in record_frames:
                fe = self.mesh_model.frame_elements.get(eid)
                if fe is None:
                    continue
                for nid in (fe.node_i, fe.node_j):
                    nd = self.mesh_model.nodes.get(nid)
                    if nd is not None:
                        record_node_tags.add(nd.node_tag)
            for aid in record_areas:
                ae = self.mesh_model.area_elements.get(aid)
                if ae is None:
                    continue
                for nid in ae.node_ids:
                    nd = self.mesh_model.nodes.get(nid)
                    if nd is not None:
                        record_node_tags.add(nd.node_tag)
            if print_progress and (record_frames or record_areas):
                print(
                    f"  Recording {len(record_frames)} frame(s) + "
                    f"{len(record_areas)} area(s) + "
                    f"{len(record_node_tags)} node(s) per step"
                )
        step_results: list[dict[str, Any]] = []
        element_forces_history: list[dict[int, dict[str, float]]] = []

        # ── Gravity state (step 0) ───────────────────────────────
        steps: list[int] = [0]
        ctrl_disps: list[float] = [0.0]
        base_shears: list[float] = [0.0]
        statuses: list[int] = [0]
        if record_element_forces:
            element_forces_history.append(self.extract_static_element_forces())

        try:
            ops.reactions()
            bs0 = 0.0
            for nid, nd in self.mesh_model.nodes.items():
                r = self.mesh_model.restraints.get(nid)
                if r and len(r.dofs) > dof - 1 and r.dofs[dof - 1] == 1:
                    try:
                        rxn = ops.nodeReaction(nd.node_tag, dof)
                        if isinstance(rxn, (list, tuple)):
                            rxn = rxn[0] if rxn else 0.0
                        bs0 += float(rxn)
                    except Exception:
                        pass
            # Sign convention: nodeReaction() returns the force the
            # ground exerts on the structure (Newton's 3rd law pair of
            # the applied lateral push).  Negate so base_shear records
            # the structure's lateral resistance, which is positive
            # when pushed in the positive DOF direction.
            base_shears[0] = -bs0
        except Exception:
            pass

        # ── Push loop with algorithm fallback chain ──────────────
        for step in range(1, num_steps + 1):
            _algo_chain: list = [_algo]
            if _algo != "NewtonLineSearch":
                _algo_chain.append("NewtonLineSearch")
            if _algo != "ModifiedNewton":
                _algo_chain.append(("ModifiedNewton", "-initial"))
            _algo_chain.append("KrylovNewton")

            ok = -1
            for attempt in _algo_chain:
                if isinstance(attempt, tuple):
                    ops.algorithm(attempt[0], attempt[1])
                else:
                    ops.algorithm(attempt)
                ok = ops.analyze(1)
                if ok == 0:
                    break

            # Per-step fallback (Gap 5): on failure, retry once with
            # relaxed NormUnbalance + ModifiedNewton(-initial), then
            # restore the primary test settings for subsequent steps.
            if ok != 0:
                _fallback = self.config.get(
                    "pushover_fallback_defaults", self.PUSHOVER_FALLBACK_DEFAULTS
                )
                # Units-aware fallback tolerance (see run_static_analysis).
                _g = g_from_units(self.units)
                _fb_total_mass = sum(self.node_masses.values()) if self.node_masses else 0.0
                if _fb_total_mass > 0:
                    _fb_tol = max(_fb_total_mass * _g * 1e-6, _test_tol * 10.0)
                else:
                    _fb_tol = _test_tol * 10.0
                ops.test(
                    _fallback.get("solver_test_type", "NormUnbalance"),
                    _fb_tol,
                    _fallback.get("solver_test_max_iter", 1000),
                )
                _fb_algo = _fallback.get("solver_algorithm", "ModifiedNewton")
                if _fb_algo == "ModifiedNewton":
                    ops.algorithm("ModifiedNewton", "-initial")
                else:
                    ops.algorithm(_fb_algo)
                ok = ops.analyze(1)
                # Restore primary settings for subsequent steps
                ops.test("NormDispIncr", _test_tol, _test_iter, 0, 2)

            statuses.append(ok)

            # Record control node displacement (relative to gravity)
            try:
                cd_total = ops.nodeDisp(int(control_node_tag))[dof - 1]
                cd = cd_total - grav_ctrl_disp
            except Exception:
                cd = 0.0
            ctrl_disps.append(cd)

            # Calculate base shear
            try:
                ops.reactions()
                bs = 0.0
                for nid, nd in self.mesh_model.nodes.items():
                    r = self.mesh_model.restraints.get(nid)
                    if r and len(r.dofs) > dof - 1 and r.dofs[dof - 1] == 1:
                        try:
                            rxn = ops.nodeReaction(nd.node_tag, dof)
                            if isinstance(rxn, (list, tuple)):
                                rxn = rxn[0] if rxn else 0.0
                            bs += float(rxn)
                        except Exception:
                            pass
            except Exception:
                bs = 0.0
            # Same sign convention as step 0: nodeReaction() is the
            # ground-on-structure force (Newton's 3rd law pair of the
            # applied lateral push).  Negate so base_shear is the
            # structure's lateral resistance (positive in push direction).
            base_shears.append(-bs)
            steps.append(step)

            # ── Per-step element-force recording (Phase 1 reporter) ──
            if record_element_forces and ok == 0:
                element_forces_history.append(self.extract_static_element_forces())

            # ── Per-step element recording ──────────────────────
            if record and ok == 0:
                step_data = _record_step(
                    self,
                    step,
                    record_frames,
                    record_areas,
                    node_tags=record_node_tags,
                )
                step_results.append(step_data)

            if print_progress:
                s = "✓" if ok == 0 else "✗"
                print(f"    Step {step:4d}/{num_steps}: u={cd:.6f} m  V={bs:.2f} kN  {s}")

            if ok != 0:
                if print_progress:
                    print(
                        f"    Push stopped — non-converged step (last algorithm: {_algo_chain[-1]})"
                    )
                break

        # Store step results on builder for downstream export
        self.pushover_step_results = step_results

        result = {
            "step": steps,
            "control_disp": ctrl_disps,
            "base_shear": base_shears,
            "status": statuses,
            "gravity_displacements": grav_disp,
            "control_node": control_node_tag,
            "dof": dof,
            "lateral_load_type": lateral_load_type,
            "element_forces_history": element_forces_history,
            "units": self.mesh_model.units,
        }
        if record:
            result["step_results"] = step_results

        return result

    # ═══════════════════════════════════════════════════════════════
    # Pushover helpers
    # ═══════════════════════════════════════════════════════════════

    def export_pushover_results(
        self,
        path: str,
        direction: str = "+X",
        pushover_results: Optional[dict[str, Any]] = None,
    ) -> str:
        """Export recorded pushover step results to NPZ.

        Args:
            path: Output .npz file path.
            direction: Push direction label, e.g. ``"+X"``, ``"+Y"``.
            pushover_results: Optional full result dict from
                :meth:`run_pushover_analysis`.  When provided, the
                global arrays (step, control_disp, base_shear) are
                included in the NPZ file alongside per-element forces.

        Returns:
            The path to the written .npz file.

        Raises:
            ValueError: If no step results have been recorded.
        """
        if not getattr(self, "pushover_step_results", None):
            raise ValueError(
                "No pushover step results to export. "
                "Ensure run_pushover_analysis() was called with "
                "record_pushover_steps=True in config."
            )
        from ..io.npz_writer import write_pushover_results_npz

        return write_pushover_results_npz(
            path,
            self.mesh_model,
            self.pushover_step_results,
            direction=direction,
            pushover_results=pushover_results,
        )

    def _compute_fallback_masses(self) -> dict[str, float]:
        """Compute nodal masses from element self‑weight when no MASS SOURCE.

        Used as a fallback when the model has no mass source definitions.
        Masses are used to define the shape of uniform/triangular pushover
        load patterns.
        """
        g = g_from_units(self.mesh_model.units)
        node_mass: dict[str, float] = {}

        for eid, elem in self.mesh_model.frame_elements.items():
            if getattr(elem, "inactive", False):
                continue
            sec_name = self.mesh_model.frame_assignments.get(eid)
            if not sec_name or sec_name not in self.mesh_model.sections:
                continue
            sec = self.mesh_model.sections[sec_name]
            mat = self.mesh_model.materials.get(sec.material)
            if mat is None or mat.unit_weight == 0:
                continue
            ni = self.mesh_model.nodes.get(elem.node_i)
            nj = self.mesh_model.nodes.get(elem.node_j)
            if ni is None or nj is None:
                continue
            L = math.hypot(nj.x - ni.x, nj.y - ni.y, nj.z - ni.z)
            if L < 1e-12:
                continue
            weight = sec.A * mat.unit_weight * L
            mass = weight / g
            node_mass[elem.node_i] = node_mass.get(elem.node_i, 0.0) + mass * 0.5
            node_mass[elem.node_j] = node_mass.get(elem.node_j, 0.0) + mass * 0.5

        return node_mass

    def _compute_uniform_lateral_loads(
        self,
        direction: str,
        node_masses: dict[str, float],
    ) -> dict[int, tuple[float, float, float]]:
        """Compute mass‑proportional lateral loads (uniform acceleration).

        Per ASCE 41 / ATC‑40 \"Uniform\" pattern — each node with mass
        receives a load proportional to its mass in the push direction.
        The absolute magnitude is irrelevant because ``DisplacementControl``
        scales the entire pattern to achieve the target displacement.

        Returns:
            ``{node_tag: (fx, fy, fz)}`` in global coordinates.
        """
        dof_idx = {"X": 0, "Y": 1, "Z": 2}.get(direction.upper(), 0)

        nodal_loads: dict[int, tuple[float, float, float]] = {}
        for nid, mass in node_masses.items():
            if mass <= 0:
                continue
            node = self.mesh_model.nodes.get(nid)
            if node is None:
                continue
            f = [0.0, 0.0, 0.0]
            f[dof_idx] = mass
            nodal_loads[node.node_tag] = (f[0], f[1], f[2])
        return nodal_loads

    def _compute_triangular_lateral_loads(
        self,
        direction: str,
        node_masses: dict[str, float],
        fundamental_period: Optional[float] = None,
    ) -> dict[int, tuple[float, float, float]]:
        """Compute triangular (ELF) lateral loads proportional to $m_i h_i^k$.

        Per ASCE 7 / ASCE 41:
        * $k = 1.0$ for $T \\le 0.5$ s
        * $k = 2.0$ for $T \\ge 2.5$ s
        * Linear interpolation for $0.5 < T < 2.5$ s

        Height $h_i$ is measured relative to the lowest node in the model.

        Returns:
            ``{node_tag: (fx, fy, fz)}`` in global coordinates.
        """
        dof_idx = {"X": 0, "Y": 1, "Z": 2}.get(direction.upper(), 0)

        # Find base elevation
        z_vals = [node.z for node in self.mesh_model.nodes.values()]
        z_min = min(z_vals) if z_vals else 0.0

        # Compute k exponent per ASCE 7
        if fundamental_period is None or fundamental_period <= 0.5:
            k = 1.0
        elif fundamental_period >= 2.5:
            k = 2.0
        else:
            k = 1.0 + (fundamental_period - 0.5) / 2.0

        nodal_loads: dict[int, tuple[float, float, float]] = {}
        for nid, mass in node_masses.items():
            if mass <= 0:
                continue
            node = self.mesh_model.nodes.get(nid)
            if node is None:
                continue
            h = max(node.z - z_min, 0.0)
            f_mag = mass * (h**k)
            if abs(f_mag) < 1e-12:
                continue
            f = [0.0, 0.0, 0.0]
            f[dof_idx] = f_mag
            nodal_loads[node.node_tag] = (f[0], f[1], f[2])
        return nodal_loads

    def _compute_mode_shape_lateral_loads(
        self,
        direction: str,
        node_masses: dict[str, float],
        mode_shapes: dict[int, dict[int, tuple[float, float, float]]],
        mode_index: int = 0,
    ) -> dict[int, tuple[float, float, float]]:
        """Compute mode‑shape‑proportional lateral loads $F_i = m_i \\cdot |\\phi_i|$.

        Each node receives a load proportional to its mass times the
        **absolute value** of the eigenvector component in the push
        direction.  Using absolute values ensures all loads act in
        the same direction — without this, nodes with opposite mode-
        shape signs would oppose the push, creating a near-self-
        equilibrating pattern that prevents convergence.

        The sign of the control-node mode shape is used to set the
        global direction (positive or negative push).

        Returns:
            ``{node_tag: (fx, fy, fz)}`` in global coordinates.
        """
        if mode_index not in mode_shapes:
            raise ValueError(f"Mode index {mode_index} not found in mode_shapes")

        mode = mode_shapes[mode_index]
        dof_idx = {"X": 0, "Y": 1, "Z": 2}.get(direction.upper(), 0)

        nodal_loads: dict[int, tuple[float, float, float]] = {}
        for nid, mass in node_masses.items():
            if mass <= 0:
                continue
            node = self.mesh_model.nodes.get(nid)
            if node is None:
                continue
            phi = mode.get(node.node_tag, (0.0, 0.0, 0.0))
            f_mag = mass * abs(phi[dof_idx])
            if abs(f_mag) < 1e-12:
                continue
            f = [0.0, 0.0, 0.0]
            f[dof_idx] = f_mag
            nodal_loads[node.node_tag] = (f[0], f[1], f[2])
        return nodal_loads

    # ═══════════════════════════════════════════════════════════════
    # Capacity Spectrum Method (CSM)
    # ═══════════════════════════════════════════════════════════════

    def pushover_to_adrs(
        self,
        pushover_results: dict[str, Any],
        modal_results: dict[str, Any],
        mode_shapes: dict[int, dict[int, tuple[float, float, float]]],
        direction: str = "X",
    ) -> dict[str, Any]:
        """Convert a pushover capacity curve to ADRS coordinates.

        Delegates to :func:`~fea_toolkit.model.csm.pushover_to_adrs`.

        Args:
            pushover_results: Output from :meth:`run_pushover_analysis`.
            modal_results: Output from :meth:`run_modal_analysis`.
            mode_shapes: Output from :meth:`extract_mode_shapes`.
            direction: Push direction (``'X'``, ``'Y'``, or ``'Z'``).

        Returns:
            Dict with ``'S_a'``, ``'S_d'``, ``'Gamma'``, ``'M_eff'``,
            ``'phi_control'`` — see :func:`~fea_toolkit.model.csm.pushover_to_adrs`.
        """
        from ..model.csm import pushover_to_adrs as _csm_pushover_to_adrs

        return _csm_pushover_to_adrs(
            pushover_results=pushover_results,
            modal_results=modal_results,
            mode_shapes=mode_shapes,
            direction=direction,
        )

    def compute_performance_point(
        self,
        pushover_results: dict[str, Any],
        modal_results: dict[str, Any],
        mode_shapes: dict[int, dict[int, tuple[float, float, float]]],
        spectrum_periods: list[float],
        spectrum_accels: list[float],
        direction: str = "X",
        damping_ratio: float = 0.05,
        max_iter: int = 50,
        tol: float = 0.01,
    ) -> dict[str, Any]:
        """Find the performance point using the Capacity Spectrum Method.

        Delegates to :func:`~fea_toolkit.model.csm.compute_performance_point`.

        Args:
            pushover_results: Output from :meth:`run_pushover_analysis`.
            modal_results: Output from :meth:`run_modal_analysis`.
            mode_shapes: Output from :meth:`extract_mode_shapes`.
            spectrum_periods: Periods (s) defining the elastic demand spectrum.
            spectrum_accels: Spectral accelerations (m/s²).
            direction: Push direction.
            damping_ratio: Elastic damping ratio (default 0.05).
            max_iter: Maximum iterations (default 50).
            tol: Convergence tolerance on S_d (default 0.01).

        Returns:
            Dict with ``'S_dp'``, ``'S_ap'``, ``'V_base'``, ``'D_roof'``,
            ``'T_eq'``, ``'mu'``, ``'converged'`` — see
            :func:`~fea_toolkit.model.csm.compute_performance_point`.
        """
        from ..model.csm import compute_performance_point as _csm_compute

        return _csm_compute(
            pushover_results=pushover_results,
            modal_results=modal_results,
            mode_shapes=mode_shapes,
            spectrum_periods=spectrum_periods,
            spectrum_accels=spectrum_accels,
            direction=direction,
            damping_ratio=damping_ratio,
            max_iter=max_iter,
            tol=tol,
        )


def _normalise_frame_response(f) -> Optional[list[float]]:
    """Normalise an ``ops.eleResponse(tag, 'localForces')`` response.

    Handles the variable-length responses returned by OpenSees element
    types:

    * **1 value** — ``Truss`` local axial force (tension-positive).
      Expanded to the standard 12-component row as
      ``[-P, 0, 0, 0, 0, 0, P, 0, 0, 0, 0, 0]`` — ``fx_i = -P``,
      ``fx_j = +P`` keeps the array dense and satisfies force
      equilibrium.
    * **6 values** — 3D ``Truss`` local end forces
      ``[fx_i, fy_i, fz_i, fx_j, fy_j, fz_j]`` with no moment
      components.  Expanded to the standard 12-component row with zero
      moments so the response is preserved rather than skipped.
    * **< 12 values (other than 1 or 6)** — unsupported; ``None`` is
      returned and the caller skips the element.
    * **>= 12 values** — the first 12 values are returned.

    This single helper replaces the ad-hoc inline normalisation that was
    previously duplicated in
    :meth:`AnalysisBuilder.extract_static_element_forces` and
    :func:`_record_step` — the two call sites disagreed on whether a
    6-value (3D truss) response was recordable, which silently dropped
    truss members from per-step pushover recording.

    Args:
        f: Raw response from ``ops.eleResponse`` (list or array-like).
           May be ``None`` when OpenSees fails to produce a response.

    Returns:
        List of 12 values, or ``None`` when the response length is not
        supported.
    """
    if f is None:
        # ``ops.eleResponse`` returns None on some failed queries —
        # treat identically to an unsupported-length response so the
        # caller's existing ``if f is None: continue`` paths skip the
        # element instead of crashing on ``len(None)``.
        return None
    if len(f) == 1:
        axial = float(f[0])
        return [-axial, 0.0, 0.0, 0.0, 0.0, 0.0, axial, 0.0, 0.0, 0.0, 0.0, 0.0]
    if len(f) == 6:
        return [f[0], f[1], f[2], 0.0, 0.0, 0.0, f[3], f[4], f[5], 0.0, 0.0, 0.0]
    if len(f) < 12:
        return None
    return list(f[:12])


def _record_step(
    builder: "AnalysisBuilder",
    step: int,
    frame_ids: set[str],
    area_ids: set[str],
    node_tags: Optional[set[int]] = None,
) -> dict[str, Any]:
    """Query ``ops.eleResponse()`` and ``ops.nodeDisp()`` at the current step.

    Args:
        builder: The ``AnalysisBuilder`` instance with active OpenSees domain.
        step: Current push step number.
        frame_ids: SAP2000 frame element IDs to record.
        area_ids: SAP2000 area element IDs to record.
        node_tags: Optional set of OpenSees node tags to record displacements
            for.  When ``None``, no displacement data is collected.

    Returns:
        Dict with keys:
        * ``"step"`` — int
        * ``"frame_forces"`` — ``{eid: {fx_i, fy_i, fz_i, mx_i, my_i, mz_i,
          fx_j, fy_j, fz_j, mx_j, my_j, mz_j}}``
        * ``"shell_forces"`` — ``{aid: {Nx, Ny, Nxy, Mx, My, Mxy}}``
        * ``"node_displacements"`` — ``{tag: (dx, dy, dz)}`` (when *node_tags* is provided)
    """
    data: dict[str, Any] = {"step": step}

    # ── Frame elements ──
    frame_forces: dict[str, dict[str, float]] = {}
    for eid in frame_ids:
        ops_tag = builder.frame_tag_map.get(eid)
        if ops_tag is None:
            continue
        try:
            f = ops.eleResponse(ops_tag, "localForces")  # 12 local values
        except Exception:
            continue
        f = _normalise_frame_response(f)
        if f is None:
            continue
        frame_forces[eid] = {
            "fx_i": f[0],
            "fy_i": f[1],
            "fz_i": f[2],
            "mx_i": f[3],
            "my_i": f[4],
            "mz_i": f[5],
            "fx_j": f[6],
            "fy_j": f[7],
            "fz_j": f[8],
            "mx_j": f[9],
            "my_j": f[10],
            "mz_j": f[11],
        }
    data["frame_forces"] = frame_forces

    # ── Shell elements (stress resultants) ──
    shell_forces: dict[str, dict[str, float]] = {}
    for aid in area_ids:
        ops_tag = builder._shell_tag_map.get(aid)
        if ops_tag is None:
            continue
        try:
            f = ops.eleResponse(ops_tag, "section", 1, "forces")  # Shell resultants
        except Exception:
            continue
        # Section forces return [Nx, Ny, Nxy, Mx, My, Mxy, ?, ?] — the
        # per-unit-width membrane/bending resultants.  (Plain "forces" on a
        # shell returns 24 local nodal forces, which must NOT be used here.)
        if len(f) >= 6:
            shell_forces[aid] = {
                "Nx": f[0],
                "Ny": f[1],
                "Nxy": f[2],
                "Mx": f[3],
                "My": f[4],
                "Mxy": f[5],
            }
    data["shell_forces"] = shell_forces

    # ── Node displacements ──
    if node_tags is not None:
        node_disp: dict[int, tuple[float, float, float]] = {}
        for tag in node_tags:
            try:
                d = ops.nodeDisp(tag)  # list: [dx, dy, dz, rx, ry, rz]
                node_disp[tag] = (float(d[0]), float(d[1]), float(d[2]))
            except Exception:
                continue
        data["node_displacements"] = node_disp

    return data


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
