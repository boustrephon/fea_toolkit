"""Optional Qt desktop GUI for fea_toolkit.

The GUI is an **optional** component: it requires the ``[gui]`` extra
(``PySide6`` + ``pyvistaqt`` + ``qtpy``) and Python **3.10 or newer**, because
``pyvistaqt`` declares ``requires-python >= 3.10``.  The core toolkit stays on
Python 3.9 (the Rhino 8 embedded-interpreter floor) and never imports this
subpackage, so neither the extra nor a newer interpreter is needed unless the
GUI is actually launched.

Install and run::

    pip install -e ".[gui]"
    fea-gui                  # or: fea-gui path/to/model.s2k

See ``docs/gui_roadmap.md`` for the design and ``docs/versioning.md`` for the
version policy.
"""

from typing import Optional

__all__ = ["launch_gui"]


def launch_gui(argv: Optional[list] = None) -> int:
    """Launch the desktop GUI.

    Qt is imported lazily, so importing :mod:`fea_toolkit.gui` performs no Qt
    import and never fails when the ``[gui]`` extra is absent.

    Args:
        argv: Optional argument vector passed through to the Qt application.

    Returns:
        The Qt application exit code.

    Raises:
        ImportError: If the ``[gui]`` extra is not installed, or the running
            interpreter is older than Python 3.10.
    """
    from .app import main

    return main(argv)
