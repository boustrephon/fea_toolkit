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
import subprocess
import sys
import types
from typing import Any

import numpy as np


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
            "system", "numberer", "constraints", "integrator",
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
        try:
            result = subprocess.run(
                [self._tclsh, tcl_path],
                capture_output=True, text=True, timeout=timeout,
                check=check,
            )
            if result.stderr:
                print(result.stderr, file=sys.stderr)
            return result.returncode, result.stdout
        except subprocess.TimeoutExpired:
            print(f"XaraTclRunner: script timed out after {timeout}s",
                  file=sys.stderr)
            raise

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
        # Common Homebrew / Miniforge locations
        for candidate in [
            "/Users/andrew/miniforge3/bin/tclsh8.6",
            "/opt/homebrew/bin/tclsh8.6",
            "/usr/local/bin/tclsh8.6",
        ]:
            if os.path.exists(candidate):
                return candidate
        return "tclsh8.6"  # last resort
