"""Locate an OpenSees interpreter for out-of-process analysis.

The "separate" Nuitka build excludes ``openseespy`` (``--nofollow-import-to``),
so the solve verbs cannot run analysis in-process.  They instead delegate to a
Python interpreter that *does* have OpenSeesPy installed — a "runtime
environment".  This module answers two questions for them:

* :func:`opensees_available` — can *this* interpreter import ``openseespy``?
* :func:`resolve_opensees_python` — which interpreter should run analysis
  out-of-process?
* :func:`describe_opensees_runtime` — a one-line summary of where OpenSeesPy
  will run and its version, for the GUI message log.

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

import importlib
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from typing import Callable, Optional

__all__ = [
    "ENV_VAR",
    "describe_opensees_runtime",
    "opensees_available",
    "opensees_version",
    "opensees_version_of",
    "resolve_opensees_python",
    "run_python",
    "should_delegate",
]

#: Environment variable overriding the configured OpenSees interpreter.
ENV_VAR = "FEA_TOOLKIT_OPENSEES_PYTHON"

#: Wall-clock limit for the one-shot version probe in :func:`opensees_version_of`.
_VERSION_TIMEOUT = 20.0


def opensees_available() -> bool:
    """Return True when ``openseespy`` can actually be imported in this interpreter.

    ``find_spec`` is **not** enough: the "separate" Nuitka build embeds an
    ``openseespy`` stub (``--nofollow-import-to``) whose spec resolves but whose
    import raises :class:`ImportError` ("Module … actively excluded from Nuitka
    compilation"), so an actual import is the only honest test.  Either the
    excluded stub or an absent package means "not here".
    """
    try:
        importlib.import_module("openseespy")
    except ImportError:
        return False
    return True


def _normalise_python(path: str) -> str:
    """Resolve a virtualenv *directory* to the interpreter inside it.

    A user may point the setting at the environment itself (``.../venv``) rather
    than its interpreter; this resolves it to ``.../venv/bin/python`` (or
    ``.../venv/Scripts/python.exe`` on Windows).  Non-directory values — a bare
    command name (``python3``) or a full executable path — pass through
    unchanged.
    """
    if os.path.isdir(path):
        for candidate in (
            os.path.join(path, "bin", "python"),
            os.path.join(path, "Scripts", "python.exe"),
        ):
            if os.path.isfile(candidate):
                return candidate
    return path


def resolve_opensees_python(explicit: Optional[str] = None) -> str:
    """Resolve the interpreter that should run analysis out-of-process.

    Args:
        explicit: A caller-supplied path (e.g. the GUI's configured
            ``opensees.python`` setting) — an interpreter path or a virtualenv
            directory.  ``None`` or ``""`` falls through to the environment
            variable, then auto-detection.

    Returns:
        A path (or bare command name) usable as ``argv[0]`` for a subprocess.
    """
    if explicit:
        return _normalise_python(explicit)
    env = os.environ.get(ENV_VAR)
    if env:
        return _normalise_python(env)
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


def opensees_version() -> str:
    """Return the installed ``openseespy`` package version, or ``""`` when absent.

    Read from distribution metadata (``importlib.metadata``), which needs no
    ``openseespy`` import — so this is cheap and safe in the "separate" build
    (where the package is absent) and in the source install alike.
    """
    import importlib.metadata

    try:
        return importlib.metadata.version("openseespy")
    except importlib.metadata.PackageNotFoundError:
        return ""


def opensees_version_of(python: str) -> str:
    """Return the OpenSeesPy package version of the *python* interpreter, or ``""``.

    Probes ``python -c "import importlib.metadata as m; print(m.version('openseespy'))"``
    — a metadata read, not an ``openseespy`` import, so it stays fast and cannot
    trigger the excluded-module stub.  A missing interpreter, a non-zero exit or
    a timeout yield ``""``, so the caller falls back to reporting just the path.
    """
    try:
        code, output = run_python(
            python,
            ["-c", "import importlib.metadata as m; print(m.version('openseespy'))"],
            timeout=_VERSION_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if code != 0:
        return ""
    for raw_line in reversed(output.splitlines()):
        stripped = raw_line.strip()
        if stripped:
            return stripped
    return ""


def describe_opensees_runtime(explicit: Optional[str] = None) -> str:
    """Return a one-line summary of where OpenSeesPy will run, for a message log.

    Args:
        explicit: The configured interpreter (the GUI's ``opensees.python``
            setting); ``None``/``""`` falls through to the environment variable
            then auto-detection.

    Returns:
        A single sentence — "OpenSeesPy <version> available in-process." when
        bundled, otherwise "OpenSeesPy not bundled — analyses delegate to
        <interpreter> (OpenSeesPy <version>)." (the version is omitted when the
        probe fails).
    """
    if opensees_available():
        version = opensees_version()
        if version:
            return f"OpenSeesPy {version} available in-process."
        return "OpenSeesPy available in-process."
    python = resolve_opensees_python(explicit)
    version = opensees_version_of(python)
    if version:
        return f"OpenSeesPy not bundled — analyses delegate to {python} (OpenSeesPy {version})."
    return f"OpenSeesPy not bundled — analyses delegate to {python}."
