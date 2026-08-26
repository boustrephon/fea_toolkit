"""Rhino-behaviour tests via a fake Rhino API + optional rhino3dm round-trip.

The real Rhino 8 API is only importable inside Rhinoceros, so these tests
inject a minimal fake ``Rhino`` / ``scriptcontext`` / ``System.Drawing``
into ``sys.modules`` and exercise the layer-tree builders and the object
colouring helper against it — no new required dependency.  A ``rhino3dm``
write/read round-trip validates the nested-layer pattern in an actual
``.3dm`` file and is skipped when ``rhino3dm`` is not installed.
"""

import sys
import types

import numpy as np
import pytest

from fea_toolkit.rhino.colour_from_npz import _colour_doc_objects
from fea_toolkit.rhino.layers import (
    create_frame_layers,
    create_joints_layer,
    create_or_get_layer,
    create_root_layer,
    create_shell_layers,
)
from fea_toolkit.rhino.results import create_deformed_geometry

# ── Minimal fake Rhino API ───────────────────────────────────────────────


class FakeColorRGBA:
    def __init__(self, r, g, b, a=255):
        self.R, self.G, self.B, self.A = int(r), int(g), int(b), int(a)

    def rgb(self):
        return (self.R, self.G, self.B)


class FakeLine:
    """Fake ``Rhino.Geometry.Line`` — stores the two endpoints."""

    def __init__(self, p0, p1):
        self.p0 = p0
        self.p1 = p1


class FakePoint3d:
    """Fake ``Rhino.Geometry.Point3d``."""

    def __init__(self, x, y, z):
        self.X, self.Y, self.Z = float(x), float(y), float(z)

    def __eq__(self, other):
        return isinstance(other, FakePoint3d) and (self.X, self.Y, self.Z) == (
            other.X,
            other.Y,
            other.Z,
        )

    def __hash__(self):
        return hash((self.X, self.Y, self.Z))

    def __repr__(self):
        return f"FakePoint3d({self.X}, {self.Y}, {self.Z})"


class FakeSystemColor:
    @staticmethod
    def FromArgb(r, g, b, a=None):
        return (int(r), int(g), int(b))

    LightGray = (192, 192, 192)


class FakeObjectAttributes:
    def __init__(self):
        self.ObjectColor = None
        self.ColorSource = None
        self.LayerIndex = -1
        self._strings = {}

    def SetUserString(self, key, value):
        self._strings[key] = value

    def GetUserString(self, key):
        return self._strings.get(key)


class FakeLayer:
    def __init__(self):
        self.Name = ""
        self.ParentLayerId = None
        self.Color = None
        self._table = None

    @property
    def Id(self):
        return self._table._layers.index(self)

    @property
    def FullPath(self):
        parts = [self.Name]
        parent_id = self.ParentLayerId
        while parent_id is not None:
            parent = self._table[parent_id]
            parts.insert(0, parent.Name)
            parent_id = parent.ParentLayerId
        return "/".join(parts)


class FakeLayerTable:
    def __init__(self):
        self._layers = []

    def __len__(self):
        return len(self._layers)

    @property
    def Count(self):
        return len(self._layers)

    def __getitem__(self, idx):
        return self._layers[idx]

    def Add(self, layer):
        layer._table = self
        self._layers.append(layer)
        return len(self._layers) - 1

    def Find(self, path, ignore_case=False):
        for i, layer in enumerate(self._layers):
            if layer.FullPath == path:
                return i
        return -1


class FakeObject:
    def __init__(self, kind="Line"):
        self.kind = kind
        self.Attributes = FakeObjectAttributes()
        self.Layer = None
        self.IsDeleted = False
        self.IsLocked = False

    def CommitChanges(self):
        pass


class FakeObjects:
    def __init__(self, doc):
        self._doc = doc
        self._items = []

    @property
    def Count(self):
        return len(self._items)

    def __getitem__(self, i):
        return self._items[i]

    def _add(self, attrs, kind, geom=None):
        obj = FakeObject(kind=kind)
        obj.Attributes = attrs or FakeObjectAttributes()
        obj.geom = geom
        idx = getattr(obj.Attributes, "LayerIndex", -1)
        obj.Layer = self._doc.Layers[idx] if idx is not None and idx >= 0 else None
        self._items.append(obj)
        return len(self._items) - 1

    def AddLine(self, line, attrs=None):
        return self._add(attrs, "Line", line)

    def AddMesh(self, mesh, attrs=None):
        return self._add(attrs, "Mesh", mesh)

    def AddPoint(self, pt, attrs=None):
        return self._add(attrs, "Point", pt)


class FakeViews:
    def Redraw(self):
        pass


class FakeDoc:
    def __init__(self):
        self.Layers = FakeLayerTable()
        self.Objects = FakeObjects(self)
        self.Views = FakeViews()


def _install_fake_rhino(doc):
    """Install fake ``Rhino`` / ``scriptcontext`` / ``System.Drawing`` modules."""
    Rhino = types.ModuleType("Rhino")

    display = types.ModuleType("Rhino.Display")
    display.ColorRGBA = FakeColorRGBA
    Rhino.Display = display

    docobjects = types.ModuleType("Rhino.DocObjects")
    docobjects.Layer = FakeLayer
    docobjects.ObjectAttributes = FakeObjectAttributes
    docobjects.ObjectColorSource = types.SimpleNamespace(ColorFromObject="ColorFromObject")
    Rhino.DocObjects = docobjects

    geometry = types.ModuleType("Rhino.Geometry")
    geometry.Point3d = FakePoint3d
    geometry.Line = FakeLine
    geometry.Mesh = object
    Rhino.Geometry = geometry

    scriptcontext = types.ModuleType("scriptcontext")
    scriptcontext.doc = doc

    system = types.ModuleType("System")
    drawing = types.ModuleType("System.Drawing")
    drawing.Color = FakeSystemColor
    system.Drawing = drawing

    sys.modules["Rhino"] = Rhino
    sys.modules["Rhino.Display"] = display
    sys.modules["Rhino.DocObjects"] = docobjects
    sys.modules["Rhino.Geometry"] = geometry
    sys.modules["scriptcontext"] = scriptcontext
    sys.modules["System"] = system
    sys.modules["System.Drawing"] = drawing
    return Rhino


_FAKE_MODULES = [
    "Rhino",
    "Rhino.Display",
    "Rhino.DocObjects",
    "Rhino.Geometry",
    "scriptcontext",
    "System",
    "System.Drawing",
]


@pytest.fixture
def rhino_env():
    """Install the fake Rhino API and yield the fake document."""
    from fea_toolkit.rhino.colors import _Color as _cached_color

    doc = FakeDoc()
    saved = {name: sys.modules.pop(name, None) for name in _FAKE_MODULES}
    _install_fake_rhino(doc)
    try:
        yield doc
    finally:
        # Restore the lazy `System.Drawing.Color` cache so later tests do
        # not see the fake `Color` object.
        from fea_toolkit.rhino import colors as _colors_module

        _colors_module._Color = _cached_color
        for name in _FAKE_MODULES:
            sys.modules.pop(name, None)
            if saved[name] is not None:
                sys.modules[name] = saved[name]


# ── Layer namespacing ────────────────────────────────────────────────────


class TestLayerNamespacing:
    def test_nested_root_layer(self, rhino_env):
        idx = create_root_layer(name="SAP2000/SAP")
        assert idx == 1
        assert rhino_env.Layers[0].Name == "SAP2000"
        assert rhino_env.Layers[1].FullPath == "SAP2000/SAP"
        # Idempotent — re-creating returns the same index.
        assert create_root_layer(name="SAP2000/SAP") == idx

    def test_joints_layer_under_stage_root(self, rhino_env):
        root = create_root_layer(name="SAP2000/Mesh")
        joints = create_joints_layer(root, root_name="SAP2000/Mesh")
        assert rhino_env.Layers[joints].FullPath == "SAP2000/Mesh/Joints"

    def test_frame_layers_namespaced(self, rhino_env):
        root = create_root_layer(name="SAP2000/SAP")
        layers = create_frame_layers(
            root, {"UB300": {"Material": "STEEL"}}, root_name="SAP2000/SAP"
        )
        paths = {
            rhino_env.Layers[i].FullPath
            for i in list(layers.centreline.values()) + list(layers.extrusion.values())
        }
        assert "SAP2000/SAP/Frames/Centreline/UB300" in paths
        assert "SAP2000/SAP/Frames/Extrusion/UB300" in paths

    def test_shell_layers_namespaced(self, rhino_env):
        root = create_root_layer(name="SAP2000/Mesh")
        layers = create_shell_layers(
            root, {"SHELL": {"Material": "CONC"}}, root_name="SAP2000/Mesh"
        )
        paths = {
            rhino_env.Layers[i].FullPath
            for i in list(layers.centreline.values()) + list(layers.extrusion.values())
        }
        assert "SAP2000/Mesh/Shells/Centreline/SHELL" in paths

    def test_meshed_subtree(self, rhino_env):
        root = create_root_layer(name="SAP2000/SAP")
        meshed = create_root_layer(name="SAP2000/SAP/Meshed", parent=root)
        layers = create_frame_layers(
            meshed, {"UB300": {}}, prefix="Meshed/", root_name="SAP2000/SAP"
        )
        paths = {rhino_env.Layers[i].FullPath for i in layers.centreline.values()}
        assert "SAP2000/SAP/Meshed/Frames/Centreline/UB300" in paths

    def test_flat_root_legacy_default(self, rhino_env):
        root = create_root_layer()
        joints = create_joints_layer(root)
        assert rhino_env.Layers[0].FullPath == "SAP2000"
        assert rhino_env.Layers[joints].FullPath == "SAP2000/Joints"


# ── Object colouring via _colour_doc_objects ─────────────────────────────


class TestColourDocObjects:
    def _add_frame(self, doc, layer_path, sap_id):
        idx = create_or_get_layer(layer_path)
        attrs = FakeObjectAttributes()
        attrs.LayerIndex = idx
        attrs.SetUserString("SAP_FrameID", sap_id)
        return doc.Objects.AddLine(None, attrs)

    def test_positive_red_negative_blue(self, rhino_env):
        doc = rhino_env
        self._add_frame(doc, "SAP2000/Mesh/Frames/CL/UB300", "B1")
        self._add_frame(doc, "SAP2000/Mesh/Frames/CL/UB300", "B2")

        n = _colour_doc_objects(
            {"B1": 10.0, "B2": -10.0},
            "SAP_FrameID",
            -10.0,
            10.0,
            layer_filter="SAP2000/Mesh/Frames/*",
        )
        assert n == 2
        assert doc.Objects[0].Attributes.ObjectColor.rgb() == (255, 25, 0)
        assert doc.Objects[1].Attributes.ObjectColor.rgb() == (0, 25, 255)
        assert doc.Objects[0].Attributes.ColorSource == "ColorFromObject"

    def test_zero_maps_white(self, rhino_env):
        doc = rhino_env
        self._add_frame(doc, "SAP2000/Mesh/Frames/CL/UB300", "B1")
        n = _colour_doc_objects(
            {"B1": 0.0},
            "SAP_FrameID",
            -1.0,
            1.0,
            layer_filter="SAP2000/Mesh/*",
        )
        assert n == 1
        # Diverging scale midpoint is white (light tint), NOT mid-grey —
        # otherwise near-zero results are indistinguishable from uncoloured
        # layer-coloured objects.
        assert doc.Objects[0].Attributes.ObjectColor.rgb() == (255, 255, 255)

    def test_layer_filter_excludes_other_layers(self, rhino_env):
        doc = rhino_env
        self._add_frame(doc, "SAP2000/Mesh/Frames/CL/UB300", "B1")
        self._add_frame(doc, "SAP2000/SAP/Frames/CL/UB300", "B1")
        n = _colour_doc_objects(
            {"B1": 5.0},
            "SAP_FrameID",
            0.0,
            5.0,
            layer_filter="SAP2000/Mesh/*",
        )
        assert n == 1
        assert doc.Objects[0].Attributes.ObjectColor is not None
        assert doc.Objects[1].Attributes.ObjectColor is None

    def test_locked_object_skipped(self, rhino_env):
        doc = rhino_env
        obj = self._add_frame(doc, "SAP2000/Mesh/Frames/CL/UB300", "B1")
        doc.Objects[obj].IsLocked = True
        n = _colour_doc_objects(
            {"B1": 5.0},
            "SAP_FrameID",
            0.0,
            5.0,
            layer_filter="SAP2000/Mesh/*",
        )
        assert n == 0

    def test_unmatched_id_untouched(self, rhino_env):
        doc = rhino_env
        self._add_frame(doc, "SAP2000/Mesh/Frames/CL/UB300", "B-OTHER")
        n = _colour_doc_objects(
            {"B1": 5.0},
            "SAP_FrameID",
            0.0,
            5.0,
            layer_filter="SAP2000/Mesh/*",
        )
        assert n == 0
        assert doc.Objects[0].Attributes.ObjectColor is None

    def test_rhino_8_broken_indexer_uses_iteration(self, rhino_env):
        """Rhino 8 CPython regression: ``doc.Objects[i]`` always raises
        IndexError (pythonnet routes it to the abstract
        ``_collections_abc.Sequence.__getitem__``).  Colouring must iterate
        the ObjectTable instead of ``range(Count)`` + indexing.
        """
        doc = rhino_env
        self._add_frame(doc, "SAP2000/Mesh/Frames/CL/UB300", "B1")
        items = list(doc.Objects._items)
        doc.Objects = _BrokenIndexObjects(items)

        n = _colour_doc_objects(
            {"B1": 5.0},
            "SAP_FrameID",
            0.0,
            5.0,
            layer_filter="SAP2000/Mesh/*",
        )
        assert n == 1
        assert items[0].Attributes.ColorSource == "ColorFromObject"

    def test_layer_filter_without_obj_layer_attr(self, rhino_env):
        """Rhino 8 CPython regression: ``ExtrusionObject`` has no ``Layer``
        attribute (AttributeError).  The layer filter must resolve the
        path via ``Attributes.LayerIndex`` + ``doc.Layers`` instead."""
        doc = rhino_env
        idx = self._add_frame(doc, "SAP2000/Mesh/Frames/CL/UB300", "B1")
        # Simulate the Rhino 8 ExtrusionObject: strip the ``Layer`` attr,
        # keep ``Attributes.LayerIndex`` intact.
        del doc.Objects._items[idx].Layer

        n = _colour_doc_objects(
            {"B1": 5.0},
            "SAP_FrameID",
            0.0,
            5.0,
            layer_filter="SAP2000/Mesh/*",
        )
        assert n == 1
        assert doc.Objects._items[idx].Attributes.ColorSource == "ColorFromObject"

    def test_layer_filter_handles_rhino_separator(self, rhino_env):
        """Rhino 8's ``Layer.FullPath`` uses ``::`` while the filter API
        uses ``/`` — colouring must normalise both sides before matching."""
        doc = rhino_env
        layer = types.SimpleNamespace(FullPath="SAP2000::Mesh::Frames::CL::UB300")
        doc.Layers._layers.append(layer)
        lidx = len(doc.Layers._layers) - 1
        attrs = FakeObjectAttributes()
        attrs.LayerIndex = lidx
        attrs.SetUserString("SAP_FrameID", "B1")
        doc.Objects.AddLine(None, attrs)

        n = _colour_doc_objects(
            {"B1": 5.0},
            "SAP_FrameID",
            0.0,
            5.0,
            layer_filter="SAP2000/Mesh/*",
        )
        assert n == 1
        # The same object must NOT match a filter outside its layer tree.
        n2 = _colour_doc_objects(
            {"B1": 5.0},
            "SAP_FrameID",
            0.0,
            5.0,
            layer_filter="SAP2000/SAP/*",
        )
        assert n2 == 0

    def test_colour_frames_from_pushover(self, rhino_env):
        """Frame colouring can source from pushover per-step forces
        (``pushover/{direction}/frame_{q}_i``), matching SAP_FrameID."""
        from fea_toolkit.rhino.results import colour_frames_from_results

        doc = rhino_env
        self._add_frame(doc, "SAP2000/Mesh/Frames/CL/UB300", "B1-0")
        self._add_frame(doc, "SAP2000/Mesh/Frames/CL/UB300", "B1-1")
        data = {
            "pushover/+X/frame_sap_id": np.array(["B1-0", "B1-1"]),
            "pushover/+X/frame_mz_i": np.array([[1.0, -2.0], [1.5, -2.5]]),
        }
        n = colour_frames_from_results(data, quantity="Mz", direction="+X", verbose=False)
        assert n == 2
        assert doc.Objects._items[0].Attributes.ColorSource == "ColorFromObject"


# ── Deformed-shape overlay construction ──────────────────────────────────


class TestDeformedGeometry:
    def test_static_frame_line_tag_mapping(self, rhino_env):
        """Deformed overlays must map the 1-based frame endpoint node TAGS
        to geometry rows via ``node_tag`` (rows are dict-ordered, not
        tag-ordered) and pair the tag-sorted displacement arrays with the
        right rows.  Row 0 here is tag 2, row 1 is tag 1."""
        data = {
            "node_x": np.array([10.0, 0.0]),  # row 0 = tag 2, row 1 = tag 1
            "node_y": np.array([0.0, 0.0]),
            "node_z": np.array([0.0, 0.0]),
            "node_tag": np.array([2, 1]),
            "frame_sap_id": np.array(["1"]),
            "frame_node_i": np.array([1]),  # tags, not rows
            "frame_node_j": np.array([2]),
            "static_case_labels": np.array(["DEAD"]),
            "static/DEAD/node_dx": np.array([0.5, 0.0]),  # tag 1, tag 2
            "static/DEAD/node_dy": np.array([0.0, 0.0]),
            "static/DEAD/node_dz": np.array([0.0, 0.0]),
        }
        n = create_deformed_geometry(data, source_type="static", case="DEAD", scale=1.0)
        assert n == 1
        line = rhino_env.Objects._items[0].geom
        # tag 1 -> row 1 (base x=0.0) + dx=0.5; tag 2 -> row 0 (base x=10.0) + 0.0
        assert (line.p0.X, line.p0.Y, line.p0.Z) == (0.5, 0.0, 0.0)
        assert (line.p1.X, line.p1.Y, line.p1.Z) == (10.0, 0.0, 0.0)
        assert rhino_env.Objects._items[0].Attributes.GetUserString("SAP_FrameID") == "1"


class TestFindLayer:
    def test_rhino_separator_not_duplicated(self, rhino_env):
        """Real Rhino layers report ``FullPath`` with ``::`` separators, so
        ``LayerTable.Find`` misses ``/``-paths.  ``create_or_get_layer``
        must still find an existing layer instead of duplicating it."""
        doc = rhino_env
        existing = types.SimpleNamespace(
            Name="UB300",
            FullPath="SAP2000::Mesh::Frames::CL::UB300",
            ParentLayerId=None,
        )
        doc.Layers._layers.append(existing)
        n_before = len(doc.Layers._layers)

        from fea_toolkit.rhino.layers import create_or_get_layer

        got = create_or_get_layer("SAP2000/Mesh/Frames/CL/UB300")
        assert got == n_before - 1
        assert len(doc.Layers._layers) == n_before  # found, not duplicated


class _BrokenIndexObjects:
    """Mirror of Rhino 8's ``ObjectTable`` as seen by pythonnet: ``Count``
    works but the indexer is unusable; only iteration (``IEnumerable``)
    yields objects."""

    def __init__(self, items):
        self._items = items

    @property
    def Count(self):
        return len(self._items)

    def __getitem__(self, index):
        raise IndexError

    def __iter__(self):
        return iter(self._items)


# ── Optional rhino3dm .3dm round-trip ────────────────────────────────────


class TestRhino3dmRoundTrip:
    def test_nested_layers_write_read(self, tmp_path):
        """Nested stage layers survive a real .3dm write/read (skipped w/o rhino3dm).

        Uses the rhino3dm 8.x API verified against the shipped type stubs:
        ``Layer()`` takes no constructor args (set ``Name`` after), the
        layer table method is ``Add`` (capital A) and the file writer is
        ``Write(path, version)``.
        """
        rhino3dm = pytest.importorskip("rhino3dm")
        path = str(tmp_path / "layers.3dm")

        model = rhino3dm.File3dm()
        root = rhino3dm.Layer()
        root.Name = "SAP2000"
        root_idx = model.Layers.Add(root)
        sap = rhino3dm.Layer()
        sap.Name = "SAP"
        sap.ParentLayerId = model.Layers[root_idx].Id
        model.Layers.Add(sap)
        model.Write(path, 6)

        loaded = rhino3dm.File3dm.Read(path)
        by_name = {layer.Name: layer for layer in loaded.Layers}
        assert {"SAP2000", "SAP"} <= set(by_name)
        # Nesting survives the round-trip: SAP's parent is the SAP2000 root.
        assert by_name["SAP"].ParentLayerId == by_name["SAP2000"].Id
        names = {layer.Name for layer in loaded.Layers}
        assert {"SAP2000", "SAP"} <= names
