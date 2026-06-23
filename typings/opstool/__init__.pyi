"""
Type stubs for opstool (C extension).

Provides function signatures for the post-processing submodule used in this project.
"""
from typing import Any
from . import post as post

def __getattr__(name: str) -> Any: ...
