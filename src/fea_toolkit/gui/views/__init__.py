"""Qt view widgets for the fea_toolkit desktop GUI.

The widgets are exposed **lazily** (PEP 562), exactly like ``gui/models``:
importing this package -- for instance a non-Qt test reaching for
``gui.views.interactor`` -- must not drag Qt in (lazy-import policy,
``docs/gui_roadmap.md`` design rule 5).
"""

__all__ = [
    "MessageLog",
    "PickResult",
    "PropertyInspector",
    "QtMouseFilter",
    "ViewportInteraction",
    "install_mouse_filter",
]

_LAZY_EXPORTS = {
    "MessageLog": ".message_log",
    "PropertyInspector": ".property_inspector",
    "PickResult": ".interactor",
    "ViewportInteraction": ".interactor",
    "QtMouseFilter": ".qt_mouse",
    "install_mouse_filter": ".qt_mouse",
}


def __getattr__(name: str):
    """PEP 562 lazy attribute for the Qt-dependent views."""
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(module_name, __name__), name)
