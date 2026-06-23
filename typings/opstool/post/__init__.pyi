"""
Type stubs for opstool.post (post-processing module).
"""
from typing import Any, Optional


def CreateODB(odb_tag: int = 1) -> None:
    """Create an ODB (OpenSees Database) for storing analysis results.

    Args:
        odb_tag: ODB identifier tag (default 1).
    """
    ...

def save_model_data(odb_tag: int = 1) -> None:
    """Save the current model data to the ODB.

    Args:
        odb_tag: ODB identifier tag (default 1).
    """
    ...

def get_model_data(data_type: str = 'Nodal',
                   odb_tag: int = 1) -> Any:
    """Retrieve model data from the ODB.

    Args:
        data_type: Type of data to retrieve (e.g. ``'Nodal'``).
        odb_tag: ODB identifier tag (default 1).
    Returns:
        DataFrame or dict with requested data.
    """
    ...
