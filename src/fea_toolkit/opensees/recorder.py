"""Recording proxy for OpenSeesPy commands.

Provides :class:`RecordingOpenSees` — a drop-in replacement for the
``openseespy.opensees`` module that records every command for later
export as a standalone Python script or Tcl script.

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

Without the swap, the builder works exactly as before — *zero* impact.
"""

from __future__ import annotations

import copy
import keyword
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

    def save_as_tcl(self, path: str) -> None:
        """Save recorded commands as a Tcl script for standalone OpenSees.

        Parameters
        ----------
        path:
            File path to write to.
        """
        lines = [
            "# OpenSees Tcl script -- auto-generated by RecordingOpenSees",
            "wipe",
        ]
        for cmd_name, args, kwargs in self._commands:
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
