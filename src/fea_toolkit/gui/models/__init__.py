"""Qt item models for the fea_toolkit desktop GUI.

``model_index`` is deliberately Qt-free, and ``ModelTreeModel`` is exposed
lazily, so importing the Qt-free half costs no Qt import.
"""

from .model_index import TreeGroup, build_groups

__all__ = ["ModelTreeModel", "TreeGroup", "build_groups"]


def __getattr__(name: str):
    """PEP 562 lazy attribute for the Qt-dependent model."""
    if name == "ModelTreeModel":
        from .tree_model import ModelTreeModel

        return ModelTreeModel
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
