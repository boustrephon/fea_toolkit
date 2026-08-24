"""Analysis-builder mixin: section creation."""

import logging
from typing import Optional

import openseespy.opensees as ops

from ..utils import (
    DEFAULT_E_S_PA,
    DEFAULT_FSAM_CONC_FT_PA,
    DEFAULT_FY_REBAR_PA,
    RC_NO_TIE_CONFINEMENT_FACTOR,
    RC_NO_TIE_EPSC_FACTOR,
    stress_scale_factor,
)

logger = logging.getLogger(__name__)


class SectionMixin:
    """Frame/shell section creation (incl. fiber sections with shear aggregation and layered-shell sections)."""

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
                    # Missing material reference — generic steel SI defaults
                    # scaled to model units (same convention as
                    # apply_material_defaults).
                    _ssf = stress_scale_factor(self.units)
                    E_mod = 200e9 * _ssf
                    G_mod = 80e9 * _ssf
                else:
                    E_mod = mat.E_mod or 200e9
                    G_mod = mat.shear_modulus(E_mod)

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
                    3
                    if (mat is not None and mat.type is not None and mat.type.lower() == "concrete")
                    else 1
                )
                if mat is not None and mat.type is not None and mat.type.lower() == "concrete":
                    # Concrete section: to_fiber_patches() uses three tags:
                    #   mat_tag     → unconfined concrete  (Concrete01)
                    #   mat_tag + 1 → confined core        (Concrete01)
                    #   mat_tag + 2 → steel rebar          (Steel02)
                    Fc = getattr(mat, "Fc", 0.0) or 3.0e7
                    epsc = getattr(mat, "eFc", 0.0) or 0.002
                    # Unconfined cover concrete
                    self._emit_fiber_concrete(
                        mat_tag,
                        Fc,
                        epsc,
                        float(self.config.get("core_residual_factor", 0.2)) * Fc,
                        0.006,
                    )
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
                    self._emit_fiber_concrete(
                        mat_tag + 1,
                        Fc_core,
                        epsc_core,
                        float(self.config.get("core_residual_factor", 0.2)) * Fc_core,
                        ecu_core,
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
            # Missing material reference — generic steel SI defaults scaled
            # to model units (same convention as apply_material_defaults).
            _ssf = stress_scale_factor(self.units)
            E_mod = 200e9 * _ssf
            G_mod = 80e9 * _ssf
        else:
            E_mod = mat.E_mod
            G_mod = mat.shear_modulus()

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

    def _emit_fiber_concrete(
        self, tag: int, Fc: float, epsc0: float, fpcu: float, epsU: float
    ) -> None:
        """Emit the fiber concrete law — ``Concrete01`` (default) or ``Concrete02``.

        Both laws share the Kent–Scott–Park compression backbone: peak
        ``Fc`` at ``epsc0``, descending to ``fpcu`` at ``epsU``.
        ``Concrete01`` has no tension and stays **flat** at ``fpcu`` past
        ``epsU``; ``Concrete02`` adds a genuinely descending post-crushing
        branch plus linear tension softening (``ft`` / ``Ets``), letting a
        flexure-critical frame shed strength after the peak instead of
        rising monotonically to the push end.

        Config:
            * ``concrete_material`` — ``"Concrete01"`` (default) or
              ``"Concrete02"``.
            * ``core_residual_factor`` — ``fpcu`` as a fraction of ``Fc``
              (default 0.2, matching the classic 0.2·f'c residual).
              Lowering it makes the concrete shed compressive stress as it
              crushes — the "core-residual reduction" lever that produces
              the post-peak descent.
            * ``concrete02_lambda`` — post-peak unloading slope ratio.
            * ``concrete02_ft_override`` / ``concrete02_Ets_override`` —
              Concrete02 tension branch, authored in SI (Pa) and scaled to
              model units.  ``None`` → ``ft`` = :data:`DEFAULT_FSAM_CONC_FT_PA`
              (3 MPa) and ``Ets`` = ``ft / 0.001``.

        Args:
            tag: OpenSees material tag.
            Fc: Compressive peak strength, model units (positive).
            epsc0: Strain at peak (positive magnitude).
            fpcu: Crushing (residual) stress, model units (positive).
            epsU: Ultimate/crushing strain (positive magnitude).
        """
        law = self.config.get("concrete_material", "Concrete01")
        if law == "Concrete02":
            lam = float(self.config.get("concrete02_lambda", 0.1))
            ssf = stress_scale_factor(self.mesh_model.units)
            ft = self.config.get("concrete02_ft_override")
            ft = DEFAULT_FSAM_CONC_FT_PA * ssf if ft is None else float(ft) * ssf
            ets = self.config.get("concrete02_Ets_override")
            ets = ft / 0.001 if ets is None else float(ets) * ssf
            ops.uniaxialMaterial(
                "Concrete02", tag, -Fc, -abs(epsc0), -fpcu, -abs(epsU), lam, ft, ets
            )
        else:
            ops.uniaxialMaterial("Concrete01", tag, -Fc, -abs(epsc0), -fpcu, -abs(epsU))

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
          (:func:`fea_toolkit.capacity.shear_capacity.shear_backbone`):
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
        from ..capacity.shear_capacity import shear_backbone

        # Explicit override wins for every aggregated section.
        override = self.config.get("shear_backbone")
        if isinstance(override, dict):
            return dict(override)
        if sec is None:
            return None
        materials = self.mesh_model.materials
        concrete = materials.get(getattr(sec, "material", ""))
        if concrete is None:
            return None
        rebar = materials.get(getattr(sec, "rebar_material", "") or "")
        tie = materials.get(getattr(sec, "tie_rebar_mat", "") or "")
        try:
            return shear_backbone(
                sec,
                concrete,
                rebar=rebar,
                tie=tie,
                units=self.units,
            )
        except ValueError:
            # Degenerate geometry (e.g. a section missing depth/bf) — no
            # backbone, the caller falls back to the elastic aggregator.
            return None

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
