"""Rhino 3-D visualisation module for ``fea_toolkit``.

Exports ``SAPModelData`` (parsed from .S2K or JSON) into the active
Rhino document as lightweight geometry organised by section-based layers.

Two geometry representations are created:

* **Centreline** — points (joints), lines (frames), planar Breps (shells).
* **Extrusion** — lightweight ``Extrusion`` solids with section profiles
  (frames) or thickness offset (shells).

All objects carry FEA metadata as Rhino UserStrings for Grasshopper access.

Usage
-----
Inside Rhino (IronPython)::

    import sys
    sys.path.append(r'/path/to/fea_toolkit/src')

    from fea_toolkit.io.s2k_parser import SAP2000Parser
    from fea_toolkit.rhino.importer import RhinoImporter

    parser = SAP2000Parser.from_json('model.json')
    md = parser.get_model_data()

    importer = RhinoImporter(md)
    report = importer.run(create_centreline=True, create_extrusions=True)
    print(report)
"""

from .importer import RhinoImporter

__all__ = ["RhinoImporter"]

