"""Run the desktop GUI with ``python -m fea_toolkit.gui``.

The same entry point as the ``fea-gui`` console script, for contexts where a
console script is not on ``PATH`` -- app launchers, scripts, or a virtualenv
used without activating it.
"""

from .app import main

if __name__ == "__main__":
    raise SystemExit(main())
