"""Atomic file writes shared by the GUI controllers' JSON persistence.

Both :mod:`.settings` and :mod:`.view_state` write a JSON file beside the user
config and must never leave a half-written file behind if the process dies
mid-write.  The temp-file naming therefore lives here, in one place, rather
than being repeated per writer.
"""

import contextlib
import os
import tempfile
from pathlib import Path
from typing import Union

__all__ = ["atomic_write_text"]


def atomic_write_text(path: Union[str, Path], text: str) -> None:
    """Write *text* to *path* atomically.

    The content goes to a uniquely-named temp file in *path*'s directory and is
    then renamed over *path*, so the replacement is atomic on one filesystem and
    a crash leaves either the old file or the new one -- never a partial write.
    A temp file left over from a failed write or rename is removed.

    Args:
        path: Destination file (its parent directory is created if needed).
        text: The string content to write.

    Raises:
        OSError: If the directory cannot be created or the write/rename fails.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
