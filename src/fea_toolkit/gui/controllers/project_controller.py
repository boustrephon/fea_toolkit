"""Project save / load orchestration — Qt-free.

The window keeps the file dialogs and the viewport re-attach; this controller
owns the session↔archive round-trip: building a bundle from a
:class:`~fea_toolkit.gui.session.Session` and writing it, and reading an archive
back into a fresh ``Session``.  It is a thin orchestration layer over the
already-Qt-free :mod:`fea_toolkit.gui.session` and :mod:`fea_toolkit.gui.project`
modules, so the window's File ▸ Save/Open project handlers no longer thread the
bundle themselves — and the round-trip is unit-testable without a running GUI.
"""

from pathlib import Path
from typing import Any, Optional

from ..session import Session

__all__ = ["ProjectController"]


class ProjectController:
    """Coordinate a session's serialisation to and from a project archive."""

    def save(
        self,
        session: Session,
        path: str | Path,
        *,
        name: str,
        source_label: str,
        camera: Any = None,
        display: Optional[dict] = None,
    ) -> list[str]:
        """Write *session* to the project archive at *path*, returning notes.

        Args:
            session: The session to serialise.
            path: The output ``.zip`` archive.
            name: Project display name.
            source_label: Original model path (informational only).
            camera: The viewport camera position, or ``None``.
            display: The display preferences dict, or ``None``.

        Returns:
            Non-fatal notes to log; ``[]`` when everything wrote silently.
        """
        from ..project import write_project_zip

        bundle = session.to_bundle(
            name=name,
            source_label=source_label,
            camera=camera,
            display=display,
        )
        return write_project_zip(Path(path), **bundle)

    def load(self, path: str | Path) -> tuple[Session, dict]:
        """Rebuild a session (and its manifest) from the archive at *path*.

        Args:
            path: A project ``.zip`` archive, or a legacy directory / ``.fea``.

        Returns:
            ``(session, bundle)`` — the rebuilt session plus the raw bundle the
            caller may inspect (e.g. to refuse a project with no model stage).

        Raises:
            Any error :func:`~fea_toolkit.gui.project.read_project` or
            :meth:`~fea_toolkit.gui.session.Session.from_bundle` raises — the
            window's open handler catches and reports it.
        """
        from ..project import read_project

        bundle = read_project(path)
        return Session.from_bundle(bundle), bundle

    @staticmethod
    def is_project(path: str | Path) -> bool:
        """``True`` when *path* is a recognised project archive or directory."""
        from ..project import is_project

        return is_project(path)

    @staticmethod
    def member_summary(path: str | Path) -> list[str]:
        """One human-readable line per member of the project archive at *path*."""
        from ..project import project_member_summary

        return project_member_summary(path)
