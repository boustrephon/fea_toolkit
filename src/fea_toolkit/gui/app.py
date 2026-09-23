"""Entry point for the fea_toolkit desktop GUI (``fea-gui``).

Usage::

    fea-gui                    # opens a small built-in demo frame
    fea-gui path/to/model.s2k  # opens a SAP2000 model file

Milestone 1 scope was launch-and-render: the window embeds a PyVista viewport
and shows geometry.  The window itself (chrome, lazy Model Tree, inspector,
selection sync) now lives in :mod:`fea_toolkit.gui.main_window`; this module
owns the ``QApplication`` bootstrap and the application's identity, including
the macOS Application-menu naming workaround.  Milestone plan:
``docs/gui_roadmap.md`` section 9.6.
"""

import sys
from typing import Optional

#: macOS labels its Application menu from the *process bundle*, not from Qt:
#: for a plain interpreter that is the Python framework's ``CFBundleName``
#: ("Python"), so Qt's own items read "About Python" / "Quit Python".
#: :func:`rename_macos_application_menu` retitles them through AppKit -- the
#: only lever short of a ``.app`` bundle.  Probe evidence: ``docs/dev_notes.md``.
APP_NAME = "FEA Toolkit"

#: Internal organisation name; only used for ``QSettings`` paths (Milestone 8).
ORG_NAME = "fea_toolkit"


def configure_application() -> None:
    """Set the application identity on the ``QCoreApplication`` statics.

    Called *before* the ``QApplication`` exists, because Qt reads several of
    these once, when the platform plugin initialises; calling it afterwards
    too is harmless.
    """
    from qtpy.QtCore import QCoreApplication
    from qtpy.QtGui import QGuiApplication

    QCoreApplication.setApplicationName(APP_NAME)
    QCoreApplication.setOrganizationName(ORG_NAME)
    QGuiApplication.setApplicationDisplayName(APP_NAME)


def rename_macos_application_menu(name: str = APP_NAME) -> bool:
    """Retitle the macOS Application menu for *name* (best effort).

    Qt titles the Application menu -- its bold label and its ``About …`` /
    ``Hide …`` / ``Quit …`` items -- from ``qt_mac_applicationName()``, which
    resolves through the process bundle; a plain interpreter therefore reads
    "About Python" / "Quit Python".  Qt's API cannot change it, so the native
    ``NSMenu`` items are retitled directly.  The item titles are updated by
    this call (verified); whether macOS *renders* the retitled bold menu label
    is AppKit's business.

    PyObjC is optional and this is cosmetic: when it is missing, when not on
    macOS, or when no native menu exists yet, the Qt-provided titles stay.

    Args:
        name: Title to apply.

    Returns:
        ``True`` if the Application menu was found and retitled, else ``False``.
    """
    if sys.platform != "darwin":
        return False
    try:
        from AppKit import NSApp

        menu = NSApp.mainMenu() if NSApp is not None else None
    except Exception:
        return False  # PyObjC absent -- Qt's own titles remain
    if menu is None or menu.numberOfItems() == 0:
        return False

    app_item = menu.itemAtIndex_(0)
    submenu = app_item.submenu()
    if submenu is None:
        return False

    # The interpreter's own name (e.g. "Python") is what Qt stamped on the
    # items; it is also the submenu's current title, so read it before renaming.
    previous = submenu.title() or ""
    app_item.setTitle_(name)
    submenu.setTitle_(name)
    if previous:
        for item in submenu.itemArray():
            title = item.title()
            if title and previous in title:
                item.setTitle_(title.replace(previous, name))
    return True


def _demo_model():
    """Build a small built-in portal frame so ``fea-gui`` needs no input file.

    Self-contained on purpose: the installed package must not depend on the
    repository's ``examples/`` directory.

    Returns:
        A planar two-column, one-beam steel portal frame in a ``SAPModelData``.
    """
    from ..model.sap_data import (
        FrameElement,
        Material,
        Node,
        Restraint,
        SAPModelData,
        Section,
    )

    nodes = {
        "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
        "2": Node(node_id="2", node_tag=2, x=4.0, y=0.0, z=0.0),
        "3": Node(node_id="3", node_tag=3, x=0.0, y=0.0, z=3.0),
        "4": Node(node_id="4", node_tag=4, x=4.0, y=0.0, z=3.0),
    }
    frame_elements = {
        "1": FrameElement(elem_id="1", elem_tag=1, node_i="1", node_j="3"),
        "2": FrameElement(elem_id="2", elem_tag=2, node_i="2", node_j="4"),
        "3": FrameElement(elem_id="3", elem_tag=3, node_i="3", node_j="4"),
    }
    return SAPModelData(
        nodes=nodes,
        restraints={
            "1": Restraint([1, 1, 1, 1, 1, 1]),
            "2": Restraint([1, 1, 1, 1, 1, 1]),
        },
        materials={
            "Steel": Material(
                name="Steel",
                type="Steel",
                E_mod=2.0e11,
                G_mod=7.7e10,
                nu=0.3,
                unit_weight=7.85e4,
                Fy=2.5e8,
            )
        },
        sections={
            "SEC1": Section(
                name="SEC1",
                shape="I/Wide Flange",
                material="Steel",
                A=0.00509434,
                I33=8.3935e-5,
                I22=7.6559e-6,
                J=2.0e-6,
            )
        },
        frame_elements=frame_elements,
        area_elements={},
        frame_assignments={"1": "SEC1", "2": "SEC1", "3": "SEC1"},
        area_assignments={},
        groups={},
        frame_auto_mesh={},
    )


def _load_model(path: str):
    """Parse a SAP2000 model file into ``SAPModelData``."""
    from ..io.s2k_parser import SAP2000Parser

    return SAP2000Parser(path).parse().get_model_data()


def main(argv: Optional[list] = None) -> int:
    """Launch the desktop GUI.

    Args:
        argv: Argument vector; defaults to ``sys.argv``.  The first positional
            argument, if any, is treated as a model file path; with no path a
            small built-in demo frame is shown.

    Returns:
        The Qt application exit code.
    """
    from qtpy.QtWidgets import QApplication

    argv = list(sys.argv if argv is None else argv)
    model_path = argv[1] if len(argv) > 1 else None

    configure_application()

    app = QApplication.instance()
    if app is None:
        app = QApplication([argv[0] if argv else "fea-gui"])

    model = _load_model(model_path) if model_path else _demo_model()

    from .main_window import MainWindow

    window = MainWindow(model=model)
    window.resize(1200, 800)
    window.show()
    # Cosmetic and macOS-only: Qt labels the Application menu from the process
    # bundle ("About Python" / "Quit Python"), so retitle it once the native
    # menu exists.  A quiet no-op everywhere else.
    rename_macos_application_menu()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
