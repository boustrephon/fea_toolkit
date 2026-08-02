"""Recording proxy for OpenSeesPy commands.

Provides :class:`RecordingOpenSees` — a drop-in replacement for the
``openseespy.opensees`` module that records every command for later
export as a standalone Python script or Tcl script.

Also provides :class:`XaraTclRunner` — a subprocess-based executor
for running exported Tcl scripts via Xara's standalone ``tclsh8.6``
interpreter, bypassing the Tcl 8.6 / Tcl 9 version conflict that
occurs when using ``opensees.tcl.Interpreter`` under Python.

Usage
-----
Swap the builder module's ``ops`` binding before calling ``build()``::

    from fea_toolkit.opensees.recorder import RecordingOpenSees
    import fea_toolkit.opensees.builder as builder_mod
    import openseespy.opensees as _real_ops

    rec = RecordingOpenSees(_real_ops)
    builder_mod.ops = rec

    builder.build()                      # ← every ops.* call is recorded

    rec.save_as_python("model.py")       # standalone OpenSeesPy script
    rec.save_as_tcl("model.tcl")         # for standalone OpenSees (Tcl)

To run the exported Tcl via Xara's native engine::

    from fea_toolkit.opensees.recorder import XaraTclRunner

    rec.save_as_xara_tcl("model.tcl")
    runner = XaraTclRunner()
    ret, stdout = runner.run("model.tcl")
"""

from __future__ import annotations

import copy
import keyword
import os
import re
import subprocess
import sys
import types
from typing import TYPE_CHECKING, Any, Dict, Optional

import numpy as np

if TYPE_CHECKING:
    from ..model.mesh_model import MeshModel

from ..utils import (
    DEFAULT_EPS_C,
    DEFAULT_EPS_CC,
    DEFAULT_G_MOD_FRAC,
    DEFAULT_FC_PA,
    DEFAULT_FY_REBAR_PA,
    DEFAULT_FY_STEEL_PA,
    DEFAULT_E_S_PA,
    RC_NO_TIE_CONFINEMENT_FACTOR,
    RC_NO_TIE_EPSC_FACTOR,
    stress_scale_factor,
)


def _py_val(v: Any) -> str:
    """Convert a Python value to a clean literal string for code generation.

    Handles NumPy scalars and arrays so the output does not require ``numpy``
    to be imported.
    """
    if isinstance(v, (np.floating, float)):
        return f"{float(v):.15g}"
    if isinstance(v, (np.integer, int, bool)):
        return str(int(v))
    if isinstance(v, np.ndarray):
        return "[" + ", ".join(_py_val(x) for x in v) + "]"
    return repr(v)


def _tcl_parts(v: Any) -> list[str]:
    """Convert a Python value to one or more Tcl literal tokens.

    Simple scalars return a single-element list.  Iterables (lists, tuples,
    ndarrays) are flattened recursively so ``[1, 2, 3]`` becomes three
    tokens.  Strings containing whitespace are braced for Tcl safety.
    """
    if isinstance(v, (np.floating, float)):
        return [f"{float(v):.15g}"]
    if isinstance(v, (np.integer, int, bool)):
        return [str(int(v))]
    if isinstance(v, (list, tuple)):
        result: list[str] = []
        for item in v:
            result.extend(_tcl_parts(item))
        return result
    if isinstance(v, np.ndarray):
        return _tcl_parts(list(v.flat))
    if isinstance(v, str):
        # Brace strings containing whitespace so Tcl reads them as one token.
        if " " in v or "\t" in v:
            return [f"{{{v}}}"]
        return [v]
    return [str(v)]


class RecordingOpenSees(types.ModuleType):
    """Module-compatible proxy that records every OpenSeesPy call.

    Acts as a drop-in for ``import openseespy.opensees as ops``.  Every
    call is captured as a ``(name, args, kwargs)`` tuple and can be
    exported later as a standalone Python or Tcl script.

    Parameters
    ----------
    wrapped:
        The real ``openseespy.opensees`` module to forward calls to.
    """

    def __init__(self, wrapped: types.ModuleType) -> None:
        name = getattr(wrapped, "__name__", "openseespy.opensees")
        super().__init__(name)
        # Copy module identity attributes so Python treats us like a module.
        self.__file__ = getattr(wrapped, "__file__", None)
        self.__path__ = getattr(wrapped, "__path__", [])
        self.__package__ = getattr(wrapped, "__package__", None)
        self.__loader__ = getattr(wrapped, "__loader__", None)
        self.__spec__ = getattr(wrapped, "__spec__", None)

        object.__setattr__(self, "_wrapped", wrapped)
        object.__setattr__(self, "_commands", [])

    # ── Intercept calls ───────────────────────────────────────────────

    def __getattr__(self, name: str) -> Any:
        # Let Python's import machinery access its own internals without
        # wrapping them.
        if name.startswith("_"):
            raise AttributeError(name)

        attr = getattr(self._wrapped, name)
        if not callable(attr):
            return attr

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Snapshot args/kwargs at call time so later mutations don't
            # affect what save_as_python() / save_as_tcl() replay.
            self._commands.append(
                (name, copy.deepcopy(args), copy.deepcopy(kwargs))
            )
            return attr(*args, **kwargs)

        return wrapper

    # ── Access recorded commands ──────────────────────────────────────

    @property
    def commands(self) -> list[tuple[str, tuple, dict]]:
        """Return the captured ``(name, args, kwargs)`` tuples."""
        return list(self._commands)

    def clear(self) -> None:
        """Discard all recorded commands."""
        object.__setattr__(self, "_commands", [])

    # ── Export formats ────────────────────────────────────────────────

    def save_as_python(self, path: str, func_name: str = "build_model") -> None:
        """Save recorded commands as a standalone Python script.

        The generated script imports ``openseespy.opensees`` and defines a
        single function that replays all commands in order.  It can be run
        directly or imported.

        Parameters
        ----------
        path:
            File path to write to.
        func_name:
            Name of the generated function (default ``"build_model"``).

        Raises
        ------
        ValueError
            If *func_name* is not a valid Python identifier or is a
            reserved keyword.
        """
        if not isinstance(func_name, str) or not func_name.isidentifier():
            raise ValueError(
                f"func_name={func_name!r} is not a valid Python identifier"
            )
        if keyword.iskeyword(func_name):
            raise ValueError(
                f"func_name={func_name!r} is a Python keyword and cannot "
                f"be used as a function name"
            )

        lines = [
            '#!/usr/bin/env python',
            '"""Auto-generated OpenSeesPy model -- created by RecordingOpenSees."""',
            '',
            "import openseespy.opensees as ops",
            "",
            "",
            f"def {func_name}():",
        ]
        for cmd_name, args, kwargs in self._commands:
            arg_str = ", ".join(_py_val(a) for a in args)
            kwarg_str = ", ".join(
                f"{k}={_py_val(v)}" for k, v in kwargs.items()
            )
            all_args = arg_str
            if kwarg_str:
                all_args += ", " + kwarg_str
            lines.append(f"    ops.{cmd_name}({all_args})")

        lines.extend(
            [
                "",
                "",
                'if __name__ == "__main__":',
                f"    {func_name}()",
                '    print("Model built successfully.")',
            ]
        )

        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")

    def save_as_tcl(self, path: str, ndm: int = 3, ndf: int = 6) -> None:
        """Save recorded commands as a Tcl script for standalone OpenSees.

        The generated script begins with ``wipe`` followed by a
        ``model Basic`` command matching the recorded model
        dimensionality (or the *ndm* / *ndf* fallbacks).

        Parameters
        ----------
        path:
            File path to write to.
        ndm:
            Number of spatial dimensions (fallback if no ``model``
            command was recorded).
        ndf:
            Number of DOFs per node (fallback if no ``model``
            command was recorded).
        """
        # Detect model dimensionality from recorded commands
        model_ndm, model_ndf = ndm, ndf
        for cmd_name, args, _ in self._commands:
            if cmd_name == "model":
                # model('basic', '-ndm', N, '-ndf', N)
                for i, a in enumerate(args):
                    if a == "-ndm" and i + 1 < len(args):
                        model_ndm = int(args[i + 1])
                    if a == "-ndf" and i + 1 < len(args):
                        model_ndf = int(args[i + 1])
                break

        lines = [
            "# OpenSees Tcl script -- auto-generated by RecordingOpenSees",
            "wipe",
            f"model Basic -ndm {model_ndm} -ndf {model_ndf}",
        ]
        for cmd_name, args, kwargs in self._commands:
            if cmd_name in ("wipe", "model"):
                continue
            parts = [cmd_name]
            for a in args:
                parts.extend(_tcl_parts(a))
            for k, v in kwargs.items():
                parts.append(f"-{k}")
                parts.extend(_tcl_parts(v))
            lines.append(" ".join(parts))
        lines.append("wipe")
        lines.append("exit")

        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")

    def save_as_xara_tcl(
        self, path: str, lib_path: str = "",
        ndm: int = 3, ndf: int = 6,
    ) -> None:
        """Save recorded commands as a Tcl script for Xara/OpenSeesRT.

        The generated script includes the ``load`` preamble needed by
        Xara's native ``libOpenSeesRT.dylib``.  Run it with::

            tclsh8.6 model.tcl

        or use :class:`XaraTclRunner`.

        Args:
            path: File path to write to.
            lib_path: Path to ``libOpenSeesRT.dylib``.  Auto-detected
                from the ``opensees`` package if empty.
            ndm: Number of spatial dimensions (default 3).
            ndf: Number of DOFs per node (default 6).
        """
        if not lib_path:
            try:
                import opensees
                lib_dir = os.path.dirname(opensees.__file__)
                found = False
                for candidate in (
                    os.path.join(lib_dir, "libOpenSeesRT.dylib"),
                    os.path.join(lib_dir, "libOpenSeesRT.so"),
                ):
                    if os.path.exists(candidate):
                        lib_path = candidate
                        found = True
                        break
                if not found:
                    lib_path = "libOpenSeesRT.dylib"  # hope runtime PATH resolves it
            except ImportError:
                lib_path = "libOpenSeesRT.dylib"  # hope it's on the dynamic loader path

        lines = [
            "# Xara/OpenSeesRT Tcl script -- auto-generated by RecordingOpenSees",
            f"load {{{lib_path}}}",
            f"model Basic -ndm {ndm} -ndf {ndf}",
        ]

        # Commands filtered: skip query-only calls; preserve stateful ones.
        _skip = {
            "wipe", "model", "wipeAnalysis",
            # Pure query calls (no Tcl model-building effect)
            "nodeCoord", "getNodeTags", "getEleTags", "eleNodes",
            "nodeDisp", "nodeEigenvector", "nodeReaction", "nodeMass",
            "eleResponse", "modalProperties",
            "responseSpectrumAnalysis",
            "eigen", "analyze",
            # Analysis/solver setup (emitted separately by exported analysis)
            # Note: "constraints" is NOT skipped because equationConstraint
            # MPCs require a specific handler (Penalty).  Without it, the
            # Tcl defaults to Transformation and MPCs are silently ignored.
            "system", "numberer", "integrator",
            "algorithm", "test", "analysis", "recorder",
        }
        for cmd_name, args, kwargs in self._commands:
            if cmd_name in _skip:
                continue
            parts = [cmd_name]
            for a in args:
                parts.extend(_tcl_parts(a))
            for k, v in kwargs.items():
                parts.append(f"-{k}")
                parts.extend(_tcl_parts(v))
            lines.append(" ".join(parts))

        lines.append("wipe")
        lines.append("exit")

        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")


class XaraTclRunner:
    """Run a Tcl model script via Xara's standalone ``tclsh8.6``.

    This bypasses Python's ``tkinter`` (linked to Tcl 9) and uses the
    standalone Tcl 8.6 interpreter directly, avoiding the version
    mismatch that prevents ``opensees.tcl.Interpreter`` from loading
    ``libOpenSeesRT.dylib``.

    Usage::

        runner = XaraTclRunner()
        exit_code, stdout = runner.run("model.tcl")
        data = XaraTclRunner.read_recorder("displacement.out")
    """

    def __init__(self, tclsh_path: str = "tclsh8.6"):
        self._tclsh = tclsh_path

    def run(self, tcl_path: str, timeout: float = 300.0,
            check: bool = False) -> tuple[int, str]:
        """Execute a Tcl script via the standalone interpreter.

        Prints stdout/stderr in real-time as the script runs (avoids
        buffering issues with ``capture_output=True``).

        Args:
            tcl_path: Path to the ``.tcl`` file to execute.
            timeout: Maximum wall-clock time in seconds.
            check: If True, raise :class:`subprocess.CalledProcessError`
                on non-zero exit code.

        Returns:
            ``(exit_code, stdout_text)`` tuple.

        Raises:
            subprocess.TimeoutExpired: If execution exceeds *timeout*.
            subprocess.CalledProcessError: If *check* is True and exit
                code is non-zero.
        """
        tcl_dir = os.path.dirname(os.path.abspath(tcl_path))
        stdout_buf: list[str] = []

        with subprocess.Popen(
            [self._tclsh, tcl_path],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, cwd=tcl_dir,
        ) as proc:
            try:
                # Read combined stdout+stderr line-by-line in real time
                for line in proc.stdout:
                    line = line.rstrip("\n")
                    print(line)
                    stdout_buf.append(line)

                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                print(f"XaraTclRunner: script timed out after {timeout}s",
                      file=sys.stderr)
                raise

        returncode = proc.returncode
        stdout_text = "\n".join(stdout_buf)

        if check and returncode != 0:
            raise subprocess.CalledProcessError(
                returncode, [self._tclsh, tcl_path],
                stdout_text, "")

        return returncode, stdout_text

    @staticmethod
    def read_recorder(file_path: str) -> np.ndarray:
        """Read a standard OpenSees recorder output file.

        Recorder files are space-delimited with one row per recorded
        step and one column per recorded DOF (or channel).  Always
        returns a 2D array, even for single-row files.

        Args:
            file_path: Path to the recorder output file.

        Returns:
            ``(n_steps, n_channels)`` NumPy array.
        """
        data = np.loadtxt(file_path)
        if data.ndim == 1:
            data = data.reshape(1, -1)
        return data

    @staticmethod
    def which_tclsh() -> str:
        """Locate ``tclsh8.6`` on the system PATH or common locations."""
        # Try PATH first
        for candidate in ["tclsh8.6", "tclsh"]:
            try:
                result = subprocess.run(
                    ["which", candidate],
                    capture_output=True, text=True,
                )
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout.strip()
            except FileNotFoundError:
                continue
        # Common Homebrew / Miniforge / custom build locations
        for candidate in [
            "/opt/homebrew/bin/tclsh8.6",
            "/usr/local/bin/tclsh8.6",
        ]:
            if os.path.exists(candidate):
                return candidate
        # Custom paths can be configured via XARA_TCLSH env var
        env_tclsh = os.environ.get("XARA_TCLSH")
        if env_tclsh and os.path.exists(env_tclsh):
            return env_tclsh
        return "tclsh8.6"  # last resort


# ════════════════════════════════════════════════════════════════════
# MeshModel-aware Tcl export
# ════════════════════════════════════════════════════════════════════


def export_mesh_model_to_tcl(
    mesh_model: "MeshModel",
    path: str,
    lib_path: str = "",
    ndm: int = 3,
    ndf: int = 6,
    tcl_prefix: str = "",
    tcl_suffix: str = "",
    config: Optional[dict] = None,
) -> None:
    """Export a ``MeshModel`` directly to a standalone Tcl script.

    This is a MeshModel-aware alternative to
    :func:`export_model_to_tcl` that works with the
    two-stage pipeline's pre-computed ``MeshModel``.  Unlike the
    SAPModelData path, the topology is already split, merged, and
    tagged — no Preprocessor re-run is needed.

    When *config* is ``None`` or ``{"create_fiber_sections": False}``,
    all frame elements are exported as ``elasticBeamColumn`` with
    ``section Elastic``.

    When *config* has ``{"create_fiber_sections": True}``, sections
    that support fiber conversion (via ``to_fiber_patches()``) are
    emitted as ``section Fiber`` blocks with ``patch`` commands, and
    frame elements reference them via ``forceBeamColumn`` +
    ``beamIntegration Lobatto``.  Elastic sections are used for
    sections that do not support fiber conversion.

    Args:
        mesh_model: Pre-processed mesh model to export.
        path: Output ``.tcl`` file path.
        lib_path: Path to ``libOpenSeesRT.dylib``.  Auto-detected
            from the installed ``opensees`` package if not provided.
        ndm: Spatial dimensions (default 3).
        ndf: DOFs per node (default 6).
        tcl_prefix: Tcl commands inserted after the model preamble
            (e.g. for nDMaterial definitions before sections).
        tcl_suffix: Tcl commands appended before ``wipe``
            (e.g. for analysis, recorders, results output).
        config: Builder config dict.  When
            ``config["create_fiber_sections"]`` is ``True``, fiber
            sections are auto-generated for supported section types.
    """
    import math
    from ..model.mesh_model import MeshModel

    if config is None:
        config = {}

    # ── Resolve libOpenSeesRT path ───────────────────────────────
    if not lib_path:
        try:
            import opensees as _xara_ops
            _lib_dir = os.path.dirname(_xara_ops.__file__)
            for ext in (".dylib", ".so"):
                cand = os.path.join(_lib_dir, f"libOpenSeesRT{ext}")
                if os.path.exists(cand):
                    lib_path = cand
                    break
        except ImportError:
            lib_path = "libOpenSeesRT.dylib"
        if not lib_path:
            lib_path = "libOpenSeesRT.dylib"

    lines: list[str] = [
        "# Xara/OpenSeesRT Tcl script -- exported by export_mesh_model_to_tcl",
        f"load {{{lib_path}}}",
        f"model Basic -ndm {ndm} -ndf {ndf}",
    ]

    # ── Tag maps ─────────────────────────────────────────────────
    # Material and section tags are pre-computed by the Preprocessor.
    # We use the same tag scheme as export_model_to_tcl:
    #   materials: 1..M, sections: M+1..M+S
    mat_tags: dict[str, int] = dict(mesh_model.material_tags)
    sec_tags: dict[str, int] = dict(mesh_model.section_tags)

    # If tag maps are empty (e.g. from a non-Preprocessor source),
    # fall back to sequential assignment.
    if not mat_tags:
        for i, mn in enumerate(mesh_model.materials, start=1):
            mat_tags[mn] = i
    if not sec_tags:
        mat_count = max(len(mesh_model.materials), 1)
        sec_offset = mat_count + 1
        for i, sn in enumerate(mesh_model.sections, start=sec_offset):
            sec_tags[sn] = i

    # ── Determine which nodes are referenced by exported elements ──
    export_shells = config.get("export_shells", True) if config else True
    _exported_node_tags: set[int] = set()
    for eid, elem in mesh_model.frame_elements.items():
        if getattr(elem, "inactive", False):
            continue
        ni = mesh_model.nodes.get(elem.node_i)
        nj = mesh_model.nodes.get(elem.node_j)
        if ni: _exported_node_tags.add(ni.node_tag)
        if nj: _exported_node_tags.add(nj.node_tag)
    if export_shells:
        for aid, aelem in mesh_model.area_elements.items():
            if getattr(aelem, "inactive", False):
                continue
            for nid in aelem.node_ids:
                nd = mesh_model.nodes.get(nid)
                if nd:
                    _exported_node_tags.add(nd.node_tag)
    # Also include restrained nodes even if no element — they may be supports
    for nid, r in mesh_model.restraints.items():
        nd = mesh_model.nodes.get(nid)
        if nd:
            _exported_node_tags.add(nd.node_tag)

    lines.append("")
    lines.append(f'puts "-> Building domain: {len(_exported_node_tags)} nodes..."')
    lines.append("flush stdout")

    # ── Nodes ────────────────────────────────────────────────────
    lines.append("")
    lines.append("# ── Nodes ──")
    for nd in mesh_model.nodes.values():
        if nd.node_tag not in _exported_node_tags:
            continue
        lines.append(f"node {nd.node_tag} {nd.x:g} {nd.y:g} {nd.z:g}")

    # ── Restraints ───────────────────────────────────────────────
    if mesh_model.restraints:
        lines.append("")
        lines.append("# ── Restraints ──")
        for nid, r in mesh_model.restraints.items():
            nd = mesh_model.nodes.get(nid)
            if nd is None:
                continue
            tags = " ".join(str(int(x)) for x in r.dofs)
            lines.append(f"fix {nd.node_tag} {tags}")

    # ── Determine which materials are used by fiber sections ────
    # These will get nonlinear materials later — skip Elastic here.
    _fiber_mat_names: set[str] = set()
    if config.get("create_fiber_sections", False):
        for sec_name, sec in mesh_model.sections.items():
            try:
                sec.to_fiber_patches(mat_tag=1)
                _fiber_mat_names.add(sec.material)
            except NotImplementedError:
                pass

    lines.append(f'puts "-> Restraints created, defining materials..."')
    lines.append("flush stdout")

    # ── Materials ────────────────────────────────────────────────
    if mesh_model.materials:
        lines.append("")
        lines.append("# ── Materials ──")
        _stress_factor = stress_scale_factor(mesh_model.units)
        for mat_name, mat in mesh_model.materials.items():
            tag = mat_tags.get(mat_name)
            if tag is None:
                continue
            # Skip Elastic if this material will be replaced by nonlinear fiber materials
            if mat_name in _fiber_mat_names:
                continue
            if mat.type and "concrete" in mat.type.lower():
                Fc = (mat.Fc if mat.Fc and mat.Fc > 0
                      else DEFAULT_FC_PA * _stress_factor)
                epsc = (mat.eFc if mat.eFc and mat.eFc > 0 else DEFAULT_EPS_C)
                Fu = 0.2 * Fc
                epsu = 0.006
                lines.append(
                    f"uniaxialMaterial Concrete01 {tag} "
                    f"{-Fc:g} {-epsc:g} {-Fu:g} {-epsu:g}"
                )
            else:
                E_mod = (mat.E_mod if mat.E_mod and mat.E_mod > 0
                         else DEFAULT_E_S_PA * _stress_factor)
                Fy = (mat.Fy if mat.Fy and mat.Fy > 0 else DEFAULT_FY_STEEL_PA * _stress_factor)
                lines.append(
                    f"uniaxialMaterial Steel01 {tag} "
                    f"{Fy:g} {E_mod:g} 0.01"
                )

    # ── Determine which sections are actually used by frame elements ──
    _assigned_to_frames: set[str] = set()
    for eid, elem in mesh_model.frame_elements.items():
        if getattr(elem, "inactive", False):
            continue
        sn = mesh_model.frame_assignments.get(eid, "")
        if sn:
            _assigned_to_frames.add(sn)

    lines.append(f'puts "-> Materials defined, creating frame sections..."')
    lines.append("flush stdout")

    # ── Frame sections ───────────────────────────────────────────
    if mesh_model.sections:
        lines.append("")
        lines.append("# ── Frame sections ──")

        # Detect fiber-capable sections that are actually used
        fiber_sec_names: set[str] = set()
        if config.get("create_fiber_sections", False):
            for sec_name, sec in mesh_model.sections.items():
                if sec_name not in _assigned_to_frames:
                    continue  # skip unused sections
                try:
                    sec.to_fiber_patches(mat_tag=1)
                    fiber_sec_names.add(sec_name)
                except NotImplementedError:
                    pass

        # ── Track which (material, is_rc) groups have emitted nonlinear mats ──
        # _rc_mat_tags[mat_name] = (concrete_unconf, concrete_conf, rebar_tag)
        _rc_mat_tags: dict[str, tuple[int, int, int]] = {}
        _next_mat_tag = max(mat_tags.values(), default=0) + 1

        for sec_name, sec in mesh_model.sections.items():
            tag = sec_tags.get(sec_name)
            if tag is None:
                continue

            if sec_name in fiber_sec_names:
                # ── Fiber section ──
                mat = mesh_model.materials.get(sec.material)

                from ..model.sap_data import (
                    ConcreteRectangularSection,
                    ConcreteCircularSection,
                    RectangularSection,
                )
                is_rc = isinstance(sec, (
                    ConcreteRectangularSection,
                    ConcreteCircularSection,
                    RectangularSection,
                ))

                if is_rc:
                    # Emit RC fiber materials ONCE per material, not per section
                    if sec.material not in _rc_mat_tags:
                        concrete_unconf = _next_mat_tag
                        concrete_conf = _next_mat_tag + 1
                        rebar_tag = _next_mat_tag + 2
                        _next_mat_tag += 3
                        _rc_mat_tags[sec.material] = (concrete_unconf, concrete_conf, rebar_tag)

                        # Concrete fallback strengths are authored in SI
                        # (Pa) and scaled to model units via _ssf — never
                        # hand-converted literals (see the material-
                        # property convention in .clinerules §4.6).
                        _ssf = stress_scale_factor(mesh_model.units)
                        _fc_pa = DEFAULT_FC_PA * _ssf
                        # No-tie-data confined-core heuristic — use the
                        # shared RC_NO_TIE_* factors (same as builder.py
                        # and AnalysisBuilder) so all Tcl/OpenSees paths
                        # emit identical Concrete01 parameters.
                        if mat is not None:
                            Fc = mat.Fc if mat.Fc and mat.Fc > 0 else _fc_pa
                            epsc = 0.002
                            fcc = (mat.eFc
                                   if mat.eFc and mat.eFc > 0
                                   else Fc * RC_NO_TIE_CONFINEMENT_FACTOR)
                            epscc = epsc * RC_NO_TIE_EPSC_FACTOR
                        else:
                            Fc = _fc_pa
                            epsc = 0.002
                            fcc = RC_NO_TIE_CONFINEMENT_FACTOR * _fc_pa
                            epscc = epsc * RC_NO_TIE_EPSC_FACTOR

                        # ── Rebar material (Steel02) ──────────────────
                        # Priority: config override (Pa, scaled to model
                        # units) → section.rebar_material lookup (model
                        # units, used as-is) → framework defaults (Pa,
                        # scaled to model units).
                        Fy_rebar = config.get("rebar_Fy_override")
                        Es_rebar = config.get("rebar_Es_override")
                        if Fy_rebar is not None:
                            Fy_rebar = Fy_rebar * _ssf
                        if Es_rebar is not None:
                            Es_rebar = Es_rebar * _ssf
                        if Fy_rebar is None or Es_rebar is None:
                            rebar_mat = None
                            rm_name = getattr(sec, "rebar_material", None)
                            if rm_name:
                                rebar_mat = mesh_model.materials.get(rm_name)
                            if rebar_mat is not None:
                                rm_Fy = getattr(rebar_mat, "Fy", 0.0) or 0.0
                                rm_Es = getattr(rebar_mat, "E_mod", 0.0) or 0.0
                                if Fy_rebar is None and rm_Fy > 0:
                                    Fy_rebar = rm_Fy
                                if Es_rebar is None and rm_Es > 0:
                                    Es_rebar = rm_Es
                        if not Fy_rebar:
                            Fy_rebar = DEFAULT_FY_REBAR_PA * _ssf
                        if not Es_rebar:
                            Es_rebar = DEFAULT_E_S_PA * _ssf

                        lines.append(
                            f"uniaxialMaterial Concrete01 {concrete_unconf} "
                            f"{-Fc:g} {-epsc:g} {-0.2*Fc:g} {-0.006:g}"
                        )
                        lines.append(
                            f"uniaxialMaterial Concrete01 {concrete_conf} "
                            f"{-fcc:g} {-epscc:g} {-0.2*fcc:g} {-0.02:g}"
                        )
                        lines.append(
                            f"uniaxialMaterial Steel02 {rebar_tag} "
                            f"{Fy_rebar:g} {Es_rebar:g} 0.01 18.5 0.925 0.15"
                        )
                    else:
                        concrete_unconf, concrete_conf, rebar_tag = _rc_mat_tags[sec.material]
                    fiber_mat_tag = concrete_unconf
                else:
                    # Steel fiber section — emit Steel01 if not already done
                    if sec.material not in _rc_mat_tags:
                        tag_for_steel = mat_tags.get(sec.material, 1)
                        _rc_mat_tags[sec.material] = (tag_for_steel, 0, 0)
                        if mat is not None and hasattr(mat, "type") and mat.type and mat.type.lower() == "steel":
                            Fy = mat.Fy if mat.Fy and mat.Fy > 0 else 2.5e8
                            E_mod = mat.E_mod if mat.E_mod > 0 else 2.0e11
                        else:
                            Fy = 2.5e8
                            E_mod = 2.0e11
                        lines.append(
                            f"uniaxialMaterial Steel01 {tag_for_steel} "
                            f"{Fy:g} {E_mod:g} 0.01"
                        )
                    fiber_mat_tag = mat_tags.get(sec.material, 1)

                # Build fiber patches
                patches = sec.to_fiber_patches(mat_tag=fiber_mat_tag)
                # GJ torsional rigidity for 3D fiber sections (Xara requirement)
                _E_mod = mat.E_mod if mat and mat.E_mod is not None and mat.E_mod > 0 else 2.0e11
                _G = mat.G_mod if mat and mat.G_mod and mat.G_mod > 0 else 0.4 * _E_mod
                gj = _G * sec.J
                lines.append(f"section Fiber {tag} -GJ {gj:g} {{")
                for entry in patches:
                    if entry[0] == "rect":
                        # patch rect $matTag $numSubdivY $numSubdivZ $yI $zI $yJ $zJ
                        lines.append(
                            f"  patch rect {entry[1]} {entry[2]} {entry[3]} "
                            f"{entry[4]:g} {entry[5]:g} {entry[6]:g} {entry[7]:g}"
                        )
                    elif entry[0] == "circ":
                        lines.append(
                            f"  patch circ {entry[1]} {entry[2]} {entry[3]} "
                            f"{entry[4]:g} {entry[5]:g} {entry[6]:g} {entry[7]:g}"
                        )
                    elif entry[0] == "straight":
                        # layer straight $matTag $numBars $area $yStart $zStart $yEnd $zEnd
                        parts = [str(x) for x in entry[1:]]
                        lines.append(f"  layer straight {' '.join(parts)}")
                    elif entry[0] == "circ_layer":
                        parts = [str(x) for x in entry[1:]]
                        lines.append(f"  layer circ {' '.join(parts)}")
                lines.append("}")
            else:
                # ── Elastic section (skip shell sections — handled below) ──
                if hasattr(sec, 'shape') and sec.shape == 'Shell':
                    continue
                E_mod = 2.0e11
                mat = mesh_model.materials.get(sec.material)
                if mat and mat.E_mod and mat.E_mod > 0:
                    E_mod = mat.E_mod
                G = (mat.G_mod if mat and mat.G_mod and mat.G_mod > 0
                     else 0.4 * E_mod)
                lines.append(
                    f"section Elastic {tag} "
                    f"{E_mod:g} {sec.A:g} {sec.I33:g} {sec.I22:g} "
                    f"{G:g} {sec.J:g}"
                )

    # ── tcl_prefix (user-supplied, before elements) ──────────────
    if tcl_prefix:
        lines.append("")
        lines.append("# ── User tcl_prefix ──")
        lines.append(tcl_prefix)

    lines.append(f'puts "-> Frame sections created, creating shell sections..."')
    lines.append("flush stdout")

    # ── Shell sections ───────────────────────────────────────────
    if mesh_model.area_elements and config.get("export_shells", True):
        lines.append("")
        lines.append("# ── Shell sections & area elements ──")

        # Assign shell section tags (sequential after frame sections)
        shell_sec_tags: dict[str, int] = {}
        _next_tag = max(sec_tags.values(), default=0) + 1
        for _aid, elem in mesh_model.area_elements.items():
            if getattr(elem, "inactive", False):
                continue
            sec_name = mesh_model.area_assignments.get(_aid, "")
            if not sec_name or sec_name in shell_sec_tags:
                continue
            shell_sec_tags[sec_name] = _next_tag
            _next_tag += 1
            # Emit ElasticMembranePlateSection
            area_sec = mesh_model.sections.get(sec_name)
            if area_sec is not None:
                E_mod = 2.0e11
                mat = mesh_model.materials.get(area_sec.material)
                if mat and mat.E_mod and mat.E_mod > 0:
                    E_mod = mat.E_mod
                nu = mat.nu if mat and mat.nu and mat.nu > 0 else 0.2
                t = getattr(area_sec, "thickness", getattr(area_sec, "t", 0.2))
                lines.append(
                    f"section ElasticMembranePlateSection "
                    f"{shell_sec_tags[sec_name]} {E_mod:g} {nu:g} {t:g}"
                )
            else:
                lines.append(
                    f"section ElasticMembranePlateSection "
                    f"{shell_sec_tags[sec_name]} 2.0e11 0.2 0.2"
                )

        # Area elements — use offset tag to avoid colliding with frame elements
        _shell_elem_tag_offset = 100_000
        for aid, elem in mesh_model.area_elements.items():
            if getattr(elem, "inactive", False):
                continue
            n_tags = []
            for nid in elem.node_ids:
                nd = mesh_model.nodes.get(nid)
                if nd is not None:
                    n_tags.append(str(nd.node_tag))
            if len(n_tags) < 3:
                continue
            stag = shell_sec_tags.get(
                mesh_model.area_assignments.get(aid, ""), 1
            )
            el_tag = _shell_elem_tag_offset + elem.area_tag
            nn = len(n_tags)
            if nn == 4:
                lines.append(
                    f"element ShellDKGQ {el_tag} "
                    f"{' '.join(n_tags)} {stag}"
                )
            elif nn == 3:
                lines.append(
                    f"element ShellDKGT {el_tag} "
                    f"{' '.join(n_tags)} {stag}"
                )
    else:
        lines.append("")
        lines.append("# ── Shell elements omitted (export_shells=False) ──")
        lines.append("puts \"-> Shell elements omitted (export_shells=False)\"")

    lines.append(f'puts "-> Shell sections created, creating frame elements..."')
    lines.append("flush stdout")

    # ── Frame elements ───────────────────────────────────────────
    if mesh_model.frame_elements:
        lines.append("")
        lines.append("# ── Frame elements ──")

        # Build frame tag map
        frame_tag_map: dict[str, int] = {}
        _created_transf_tags: set[int] = set()
        next_tag = 1
        used_tags: set[int] = set()
        for eid, elem in mesh_model.frame_elements.items():
            if getattr(elem, "inactive", False):
                continue
            if elem.elem_tag in used_tags:
                tag = next_tag
                next_tag += 1
            else:
                tag = elem.elem_tag if elem.elem_tag > 0 else next_tag
                next_tag = max(next_tag, tag + 1)
            used_tags.add(tag)
            frame_tag_map[eid] = tag

        for eid, elem in mesh_model.frame_elements.items():
            if getattr(elem, "inactive", False):
                continue
            sec_name = mesh_model.frame_assignments.get(eid, "")
            if not sec_name:
                continue
            ni = mesh_model.nodes.get(elem.node_i)
            nj = mesh_model.nodes.get(elem.node_j)
            if ni is None or nj is None:
                continue

            el_tag = frame_tag_map.get(eid, elem.elem_tag)
            sec_tag = sec_tags.get(sec_name, 1)

            # Geometric transformation
            dx = nj.x - ni.x
            dy = nj.y - ni.y
            dz = nj.z - ni.z
            if abs(dx) < 1e-12 and abs(dy) < 1e-12:
                vecxz = "1 0 0"
            else:
                vecxz = "0 0 1"
            # Determine root element for geomTransf: split children share
            # the parent's orientation, so they share the parent's transf_tag.
            # Build id→tag lookup for finding the parent's numeric tag.
            _parent_id = getattr(elem, 'parent_id', None)
            if _parent_id is not None and _parent_id in frame_tag_map:
                _root_el_tag = frame_tag_map[_parent_id]
            else:
                _root_el_tag = el_tag
            _transf_base = 20000
            transf_tag = _transf_base + _root_el_tag
            # Avoid redundant geomTransf for split children sharing same parent
            if transf_tag not in _created_transf_tags:
                _created_transf_tags.add(transf_tag)
                transf_type = config.get("geom_transf_type", "Linear") if config else "Linear"
                lines.append(f"geomTransf {transf_type} {transf_tag} {vecxz}")

            if config.get("create_fiber_sections", False) and sec_name in fiber_sec_names:
                # Nonlinear beam-column with fiber section
                int_tag = 10000 + el_tag
                n_int_pts = config.get("num_int_pts", 5) if config else 5
                elem_type = config.get("element_type", "forceBeamColumn") if config else "forceBeamColumn"

                if elem_type == "dispBeamColumn":
                    # dispBeamColumn uses inline Lobatto syntax (required for Xara/OpenSeesRT)
                    lines.append(
                        f"element dispBeamColumn {el_tag} "
                        f"{ni.node_tag} {nj.node_tag} {transf_tag} "
                        f"\"Lobatto {sec_tag} {n_int_pts}\""
                    )
                elif config.get("beam_integration", "Lobatto") == "HingeRadau":
                    # HingeRadau: 2 Gauss-Radau at each hinge over 4*Lp, 2 interior
                    dx = nj.x - ni.x; dy = nj.y - ni.y; dz = nj.z - ni.z
                    elem_len = math.sqrt(dx*dx + dy*dy + dz*dz)
                    Lp = max(0.05 * elem_len, 0.1)
                    lines.append(
                        f"beamIntegration HingeRadau {int_tag} {sec_tag} {Lp:.4f} {sec_tag} {Lp:.4f} {sec_tag}"
                    )
                    lines.append(
                        f"element forceBeamColumn {el_tag} "
                        f"{ni.node_tag} {nj.node_tag} {transf_tag} {int_tag}"
                    )
                else:
                    # Default (Lobatto): beamIntegration + forceBeamColumn
                    lines.append(
                        f"beamIntegration Lobatto {int_tag} {sec_tag} {n_int_pts}"
                    )
                    lines.append(
                        f"element forceBeamColumn {el_tag} "
                        f"{ni.node_tag} {nj.node_tag} {transf_tag} {int_tag}"
                    )
            else:
                lines.append(
                    f"element elasticBeamColumn {el_tag} "
                    f"{ni.node_tag} {nj.node_tag} {sec_tag} {transf_tag}"
                )

    lines.append(f'puts "-> Domain building complete ({len(mesh_model.nodes)} nodes, {len(mesh_model.frame_elements)} frames, {len(mesh_model.area_elements)} shells)"')
    lines.append("flush stdout")

    # ── tcl_suffix (user-supplied, before wipe) ──────────────────
    if tcl_suffix:
        lines.append("")
        lines.append("# ── User tcl_suffix ──")
        lines.append(tcl_suffix)

    # ── Final ────────────────────────────────────────────────────
    lines.append("")
    lines.append('puts "Xara/OpenSeesRT script completed successfully"')
    lines.append("wipe")
    lines.append("exit")

    # ── Write file ───────────────────────────────────────────────
    with open(path, "w") as f:
        f.write("\n".join(lines))
        f.write("\n")


def parse_pushover_results(
    disp_path: str,
    bs_path: str,
    reaction_path: Optional[str] = None,
) -> Dict[str, np.ndarray]:
    """Parse pushover Tcl recorder output files into numpy arrays.

    Reads the output files generated by :func:`pushover_tcl`:

    - **disp_path**: ``{output_prefix}_disp.out`` — one line per step
      with ``time`` and ``disp`` columns.  The control node displacement
      array is the second column.
    - **bs_path**: ``{output_prefix}_bs.out`` — a single line with
      three values ``rx ry rz`` representing the total base reactions.
    - **reaction_path**: ``{output_prefix}_reaction.out`` — optional,
      one line per step with time and the three reaction components
      at the base node.

    Returns
    -------
    dict
        Keys:
        - ``"control_disp"`` — 1-D ndarray of control node displacement
          at each converged step (from *disp_path*).
        - ``"base_shear"`` — 1-D ndarray of base shear (Rx) at each
          step.  If *disp_path* has N rows and *bs_path* has a single
          scalar, the single value is broadcast to match *control_disp*.
        - ``"step"`` — 1-D ndarray of step indices (1, 2, ..., N).
        - ``"base_rx"``, ``"base_ry"``, ``"base_rz"`` — scalar final
          base reaction components (from *bs_path*).

    Raises
    ------
    FileNotFoundError
        If *disp_path* or *bs_path* does not exist.
    ValueError
        If the files cannot be parsed as numeric data.
    """
    # ── Displacement ─────────────────────────────────────────────
    disp_data = np.genfromtxt(disp_path, invalid_raise=False)
    if disp_data.ndim == 1:
        # Single column of displacements (no time column)
        control_disp = disp_data
    elif disp_data.ndim >= 2:
        # First column is time, second is displacement — validate shape
        if disp_data.shape[1] < 2:
            raise ValueError(
                f"Displacement file {disp_path} has {disp_data.shape[1]} "
                f"column(s); expected at least 2 (time + disp)."
            )
        control_disp = disp_data[:, 1]
    else:
        raise ValueError(f"Cannot parse displacement file: {disp_path}")

    n_steps = len(control_disp)

    # ── Base shear ───────────────────────────────────────────────
    bs_data = np.genfromtxt(bs_path, invalid_raise=False)
    if bs_data.ndim == 0:
        # Single scalar value
        base_shear = np.full(n_steps, float(bs_data))
    elif bs_data.ndim == 1 and len(bs_data) == 3:
        # Three components: rx, ry, rz — use first (push direction)
        base_shear = np.full(n_steps, float(bs_data[0]))
    elif bs_data.ndim == 1:
        # One value per step
        base_shear = np.asarray(bs_data, dtype=float)
    else:
        # Multi-row: one per step
        base_shear = np.asarray(bs_data, dtype=float)

    # Broadcast if needed — only for non-scalar data
    if hasattr(base_shear, '__len__') and len(base_shear) != n_steps and n_steps > 0:
        # Fallback: use the first value if shapes mismatch
        base_shear_val = float(bs_data.flat[0]) if hasattr(bs_data, 'flat') else float(bs_data)
        base_shear = np.full(n_steps, base_shear_val)

    # ── Step indices ─────────────────────────────────────────────
    step = np.arange(1, n_steps + 1, dtype=int)

    result: Dict[str, np.ndarray] = {
        "control_disp": control_disp,
        "base_shear": base_shear,
        "step": step,
    }

    # ── Final base reactions (single scalar from bs_path) ────────
    if bs_data.ndim == 1 and len(bs_data) == 3:
        result["base_rx"] = np.array([bs_data[0]])
        result["base_ry"] = np.array([bs_data[1]])
        result["base_rz"] = np.array([bs_data[2]])
    elif bs_data.ndim == 0:
        result["base_rx"] = np.array([float(bs_data)])

    # ── Optional: per-step reaction file ─────────────────────────
    if reaction_path and os.path.exists(reaction_path):
        try:
            rx_data = np.genfromtxt(reaction_path, invalid_raise=False)
            if rx_data.ndim >= 2:
                result["reaction_rx"] = rx_data[:, 1]
                result["reaction_ry"] = rx_data[:, 2] if rx_data.shape[1] >= 3 else rx_data[:, 1]
                result["reaction_rz"] = rx_data[:, 3] if rx_data.shape[1] >= 4 else rx_data[:, 1]
        except Exception:
            pass

    return result
