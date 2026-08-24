"""Analysis-builder mixin: material creation."""

import contextlib
import logging

import openseespy.opensees as ops

from ..utils import (
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
    stress_scale_factor,
)

logger = logging.getLogger(__name__)


class MaterialMixin:
    """Uniaxial and nD material creation (incl. the PlaneStressUserMaterial pair and FSAM/MVLEM support materials)."""

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
                    nd_mat.Eout if nd_mat.Eout is not None else nd_mat.E / (2.0 * (1.0 + nd_mat.nu))
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

        Only FSAM materials that are actually *consumed* by the domain —
        referenced by a
        :class:`~fea_toolkit.model.sap_data.LayeredShellSection` layer
        or an SFI/E_SFI wall element — are created.  A
        configured-but-unconsumed FSAM nD material is skipped
        (``_create_materials`` stashes the consumed-name set in
        ``_fsam_consumed``, which may be empty); creating it would
        reference the generic Elastic uniaxial laws, which lack
        ``getCrackingStrain()`` and crash the OpenSees FSAM constructor.
        """
        fsam_mats = {
            n: m for n, m in self.mesh_model.nd_materials.items() if m.material_type == "FSAM"
        }
        if not fsam_mats:
            return
        # Only FSAM materials actually *consumed* (referenced by a
        # LayeredShell section layer or an SFI/E_SFI wall element) are
        # created.  Unconsumed FSAM would reference the generic Elastic
        # uniaxial laws — which lack getCrackingStrain() — and crash the
        # OpenSees FSAM constructor ("failed to get cracking strain").
        # ``_fsam_consumed`` is always stashed by ``_create_materials()``
        # (possibly empty when no FSAM is consumed) — distinguish
        # "exists but empty" from "absent" so a configured-but-unconsumed
        # FSAM is also skipped.
        _fsam_consumed = getattr(self, "_fsam_consumed", None)
        if _fsam_consumed is not None:
            fsam_mats = {n: m for n, m in fsam_mats.items() if n in _fsam_consumed}
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
            created += 1

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
        # Collect the set of FSAM nD materials that are actually
        # *consumed* by the domain — referenced by a LayeredShell
        # section layer or an SFI_MVLEM_3D / E_SFI_MVLEM_3D wall
        # element.  A configured-but-unconsumed FSAM nD material does
        # NOT force ConcreteCM/Steel02 for its referenced laws; only
        # consumed FSAM materials participate, because ConcreteCM is
        # required for FSAM's getCrackingStrain() at runtime.
        _fsam_consumed: set = set()
        for _lss in self.mesh_model.layered_shell_sections.values():
            for _layer in _lss.layers:
                _fsam_consumed.add(_layer.nd_material)
        for _wall in self.mesh_model.wall_elements.values():
            if _wall.material_type == "uniaxial":
                continue
            _fsam_consumed.update(_wall.fsam_material_names or [])
        # Stash for _create_fsam_materials(), which only creates consumed
        # FSAM materials (unconsumed FSAM would reference Elastic uniaxial
        # laws and crash the OpenSees FSAM constructor).
        self._fsam_consumed = _fsam_consumed

        # Collect the set of material names that any *consumed* FSAM nD
        # material references as its steel (sx/sy) or concrete (conc)
        # law.  These receive ConcreteCM / Steel02 below instead of the
        # generic Elastic fallback, because FSAM requires
        # getCrackingStrain().
        _fsam_refs_by_name: dict[str, set[str]] = {}
        for _nd in self.mesh_model.nd_materials.values():
            if _nd.material_type != "FSAM":
                continue
            if _nd.name not in _fsam_consumed:
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

            # Sections used by explicitly-selected brace elements (custom
            # BRB, diagonal I-section) may not be recognised brace shapes —
            # register truss data for them so the per-element override in
            # _add_beam_column can resolve area/Fy/E.
            if self._brace_selection:
                _assignments = self.mesh_model.frame_assignments
                for _eid in self._brace_selection:
                    _sec_name = _assignments.get(_eid)
                    if not _sec_name or _sec_name in self._truss_mat_tags:
                        continue
                    _sec = self.mesh_model.sections.get(_sec_name)
                    if _sec is None:
                        continue
                    _area = getattr(_sec, "A", 0.0) or 0.0
                    if _area < 1e-12:
                        continue
                    _mat = self.mesh_model.materials.get(_sec.material)
                    _E = _mat.E_mod if _mat else 200e9
                    _Fy = getattr(_sec, "Fy", None) or getattr(_mat, "Fy", 250e6) if _mat else 250e6
                    self._truss_mat_tags[_sec_name] = truss_tag
                    self._truss_areas[_sec_name] = _area
                    self._truss_Fy[_sec_name] = _Fy
                    self._truss_E[_sec_name] = _E
                    truss_tag += 1
