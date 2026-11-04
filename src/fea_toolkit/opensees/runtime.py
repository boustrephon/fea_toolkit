"""Locate an OpenSees interpreter for out-of-process analysis.

The "separate" Nuitka build excludes ``openseespy`` (``--nofollow-import-to``),
so the solve verbs cannot run analysis in-process.  They instead delegate to a
Python interpreter that *does* have OpenSeesPy installed — a "runtime
environment".  This module answers two questions for them:

* :func:`opensees_available` — can *this* interpreter import ``openseespy``?
* :func:`resolve_opensees_python` — which interpreter should run analysis
  out-of-process?

Resolution order (highest precedence first):

1. an explicit path handed in by the caller (the GUI's configured
   ``opensees.python`` setting),
2. the ``FEA_TOOLKIT_OPENSEES_PYTHON`` environment variable,
3. auto-detection — ``sys.executable`` when *this* interpreter can import
   ``openseespy`` (the source install and the licensed ``--with-opensees``
   build), else the first ``python3`` on ``PATH``, else ``sys.executable``.

The module imports only the standard library, so it is importable in the
"separate" build (which substitutes an :class:`ImportError` stub for
``openseespy``) without pulling in OpenSees or Qt.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from typing import Callable, Optional

__all__ = [
    "ENV_VAR",
    "opensees_available",
    "resolve_opensees_python",
    "run_python",
    "should_delegate",
]

#: Environment variable overriding the configured OpenSees interpreter.
ENV_VAR = "FEA_TOOLKIT_OPENSEES_PYTHON"


def opensees_available() -> bool:
    """Return True when ``openseespy`` can be imported in this interpreter.

    In the "separate" Nuitka build ``openseespy`` is excluded with
    ``--nofollow-import-to`` and Nuitka substitutes a stub that raises
    :class:`ImportError`; either that or an absent package means "not here".
    """
    try:
        return importlib.util.find_spec("openseespy") is not None
    except ImportError:
        return False


def resolve_opensees_python(explicit: Optional[str] = None) -> str:
    """Resolve the interpreter that should run analysis out-of-process.

    Args:
        explicit: A caller-supplied path (e.g. the GUI's configured
            ``opensees.python`` setting).  ``None`` or ``""`` falls through to
            the environment variable, then auto-detection.

    Returns:
        A path (or bare command name) usable as ``argv[0]`` for a subprocess.
    """
    if explicit:
        return explicit
    env = os.environ.get(ENV_VAR)
    if env:
        return env
    if opensees_available():
        return sys.executable
    for candidate in ("python3", "python"):
        found = shutil.which(candidate)
        if found:
            return found
    # Last resort: fall back to this interpreter and let the driver fail loudly
    # with an informative traceback rather than a bare FileNotFoundError.
    return sys.executable


def should_delegate() -> bool:
    """Return True when analysis must run out-of-process."""
    return not opensees_available()


def run_python(
    python: str,
    args: Sequence[str],
    *,
    log: Optional[Callable[[str], None]] = None,
    timeout: Optional[float] = None,
) -> tuple[int, str]:
    """Run ``python args`` as a subprocess, streaming stdout line-by-line.

    stdout and stderr are merged so the child cannot deadlock on a full stderr
    pipe, and so ordering is preserved.  Each line is handed to *log* (when
    given) and accumulated into the returned output.

    Cancellation is **not** wired here: a solve subprocess runs to completion
    and a caller's stop flag cannot interrupt it mid-case.  (Line-streaming
    cannot poll a flag while the child is silent, and the cooperative
    cancellation contract of the recipe loop does not extend across a process
    boundary.)

    Args:
        python: The interpreter path (as resolved by
            :func:`resolve_opensees_python`).
        args: The argument vector after the interpreter (e.g. ``["-m",
            "fea_toolkit.opensees.subprocess_driver", ...]``).
        log: Optional one-line sink; each output line is forwarded to it.
        timeout: Optional wall-clock timeout in seconds; on expiry the child is
            killed and :class:`subprocess.TimeoutExpired` is raised.

    Returns:
        ``(returncode, combined_output)``.

    Raises:
        subprocess.TimeoutExpired: If the child exceeds *timeout*.
    """
    cmd = [python, *args]
    lines: list[str] = []
    with subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    ) as proc:
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.rstrip("\n")
                lines.append(line)
                if log is not None:
                    log(line)
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise
    return proc.returncode, "\n".join(lines)
