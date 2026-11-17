"""The GUI session — the document state the main window owns and displays.

The main window was holding its document state (the model store, the registered
views, the recipe, the named selections, the hidden-element set and the last
run) as bare attributes on the window itself.  Consolidating them on a single
:class:`Session` gives that state one owner, lets a whole run be saved and
reopened in one call (the planned ``to_bundle`` / ``from_bundle``) and — because
this module imports no Qt — lets that round-trip be unit-tested without the
``[gui]`` extra.

Like :mod:`fea_toolkit.gui.models.model_index` and
:mod:`fea_toolkit.gui.controllers.view_registry`, the session is Qt-free: it
holds the *data* the window renders, never a widget.
"""

from dataclasses import dataclass, field
from typing import Any

from ..workflow.recipe import Recipe
from .controllers.view_registry import ViewRegistry

__all__ = ["Session"]


@dataclass
class Session:
    """The document state the main window owns and displays.

    Attributes:
        store: The :class:`~fea_toolkit.io.model_store.ModelStore` (``None``
            until a model is opened).
        views: The registered views (geometry / results / table / figure).
        recipe: The workflow recipe being authored.
        named_selections: Named selections, keyed by name.
        hidden_elem_ids: Element ids hidden from the viewport.
        last_run: The last completed
            :class:`~fea_toolkit.workflow.recipe.RecipeRun`, retained so
            ``File ▸ Save project`` can serialise its results.
    """

    store: Any = None
    views: ViewRegistry = field(default_factory=ViewRegistry)
    recipe: Recipe = field(default_factory=Recipe)
    named_selections: dict[str, Any] = field(default_factory=dict)
    hidden_elem_ids: set[str] = field(default_factory=set)
    last_run: Any = None
