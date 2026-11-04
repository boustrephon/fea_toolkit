"""Nuitka entry point that launches the fea_toolkit Qt desktop GUI.

Compiled by ``scripts/build_nuitka.sh --gui``.  This is a plain *top-level*
script (absolute imports only), deliberately not a ``-m`` package entry:
compiling the ``fea_toolkit.gui`` subpackage with ``--python-flag=-m`` broke
relative imports (``from ..utils import ...``) in sibling subpackages such as
``io.report``, whereas a top-level script keeps the ``fea_toolkit`` package
context intact.
"""

from fea_toolkit.gui.app import main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
