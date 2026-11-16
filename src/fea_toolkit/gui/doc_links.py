"""Documentation / DOI URL helpers shared by the GUI.

The MkDocs site serves each ``docs/*.md`` as a ``<name>/`` directory URL, so a
doc filename resolves to ``DOCS_URL + stem + "/"``.  A DOI resolves to
``https://doi.org/<doi>``.  Both are used by :mod:`fea_toolkit.gui.main_window`
(the Help menu) and :mod:`fea_toolkit.gui.views.verb_help_dialog` (the per-verb
Documentation / Citations links), so the spelling of a link lives here once.
"""

from __future__ import annotations

__all__ = ["DOCS_URL", "doc_url", "doi_url"]

#: Base URL of the published documentation site (MkDocs).
DOCS_URL = "https://boustrephon.github.io/fea_toolkit/"


def doc_url(filename: str) -> str:
    """Resolve a ``docs/*.md`` filename to its MkDocs page URL."""
    stem = filename.removesuffix(".md")
    return f"{DOCS_URL}{stem}/"


def doi_url(doi: str) -> str:
    """Resolve a DOI to its ``doi.org`` URL."""
    return f"https://doi.org/{doi}"
