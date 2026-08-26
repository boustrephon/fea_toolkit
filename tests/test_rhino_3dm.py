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


class _FakeSDColor:
    """Minimal ``System.Drawing.Color`` stand-in.

    Carries the ``.R/.G/.B`` surface ``_applied_colour_matches`` reads plus
    the ``.rgb()`` helper the colouring tests assert on.  Returned by
    ``FakeSystemColor.FromArgb`` — the ``_as_sd_color_rgb`` path.
    """

    def __init__(self, r, g, b):
        self.R, self.G, self.B = int(r), int(g), int(b)

    def rgb(self):
        return (self.R, self.G, self.B)

    def __eq__(self, other):
        if isinstance(other, _FakeSDColor):
            return (self.R, self.G, self.B) == (other.R, other.G, other.B)
        if isinstance(other, (tuple, list)):
            return tuple(other) == (self.R, self.G, self.B)
        return NotImplemented

    def __hash__(self):
        return hash((self.R, self.G, self.B))

    def __repr__(self):
        return f"_FakeSDColor({self.R}, {self.G}, {self.B})"


class FakeSystemColor:
    @staticmethod
    def FromArgb(a1, a2, a3, a4=None):
        # Mirrors the real System.Drawing.Color overloads:
        #   FromArgb(int red, int green, int blue)
        #   FromArgb(int alpha, int red, int green, int blue)
        if a4 is None:
            return _FakeSDColor(a1, a2, a3)
        return _FakeSDColor(a2, a3, a4)

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

    def GetUserStrings(self):
        return self._strings

    def Duplicate(self):
        """Independent copy — mirrors ``ObjectAttributes.Duplicate()``.

        ``type(self)()`` preserves subclass semantics (e.g. the no-op
        colour setter of ``_NoOpObjectAttributes``) so the read-back
        verification exercises the same drop path as real Rhino.
        """
        new = type(self)()
        new.LayerIndex = self.LayerIndex
        new.ColorSource = self.ColorSource
        new.ObjectColor = self.ObjectColor
        new._strings = dict(self._strings)
        return new


class _NoOpObjectAttributes(FakeObjectAttributes):
    """Attributes whose colour assignment never sticks (no-op setter).

    Simulates a Rhino commit that silently ignores ``ObjectColor`` so the
    read-back verification in ``_colour_doc_objects`` can be tested.
    """

    def __init__(self):
        super().__init__()
        self._colour = None

    @property
    def ObjectColor(self):
        return self._colour

    @ObjectColor.setter
    def ObjectColor(self, value):
        self._colour = None  # commit is a no-op — the colour never sticks


class FakeLayer:
    def __init__(self):
        self.Name = ""
        self.ParentLayerId = None
        self.Color = None
        self.IsLocked = False
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


class _FakeGeometry:
    def Duplicate(self):
        return _FakeGeometry()


class FakeObject:
    def __init__(self, kind="Line"):
        self.kind = kind
        self.Attributes = FakeObjectAttributes()
        self.Layer = None
        self.IsDeleted = False
        self.IsLocked = False
        self.Id = None

    @property
    def Geometry(self):
        return getattr(self, "geom", None) or _FakeGeometry()

    def CommitChanges(self):
        pass


class _ReadOnlyAttrObject(FakeObject):
    """``FakeObject`` whose ``Attributes`` is read-only.

    Simulates Rhino 8 CPython dropping the in-place attribute setters so
    the Recreate (delete + re-add) fallback is exercised.
    """

    def __init__(self):
        self.kind = "Line"
        self.Layer = None
        self.IsDeleted = False
        self.IsLocked = False
        self.Id = None
        self._ro_attrs = FakeObjectAttributes()

    @property
    def Attributes(self):
        return self._ro_attrs

    @Attributes.setter
    def Attributes(self, value):
        raise AttributeError("read-only Attributes")


class FakeObjects:
    def __init__(self, doc):
        self._doc = doc
        self._items = []
        self._next_id = 0

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
        obj.Id = self._next_id
        self._next_id += 1
        self._items.append(obj)
        return obj.Id

    def AddLine(self, line, attrs=None):
        return self._add(attrs, "Line", line)

    def AddMesh(self, mesh, attrs=None):
        return self._add(attrs, "Mesh", mesh)

    def AddPoint(self, pt, attrs=None):
        return self._add(attrs, "Point", pt)

    def ModifyAttributes(self, guid, attrs, quiet=False):
        """Replace the attributes of the object with *guid* (mirrors the
        document API used by ``_colour_doc_objects``)."""
        for obj in self._items:
            if obj.Id == guid:
                obj.Attributes = attrs
                return True
        return False

    def Find(self, guid):
        for obj in self._items:
            if obj.Id == guid:
                return obj
        return None

    def Delete(self, guid, quiet=False):
        for i, obj in enumerate(self._items):
            if obj.Id == guid:
                del self._items[i]
                return True
        return False

    def Add(self, geom, attrs=None):
        return self._add(attrs, "Line", geom)


class FakeViews:
    def __init__(self):
        self.RedrawEnabled = True

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

    rhinoscriptsyntax = types.ModuleType("rhinoscriptsyntax")

    def _fake_get_user_text(guid, key=None):
        obj = doc.Objects.Find(guid)
        if obj is None:
            return {} if key is None else None
        strings = getattr(obj.Attributes, "_strings", {})
        if key is None:
            return dict(strings)
        return strings.get(key)

    rhinoscriptsyntax.GetUserText = _fake_get_user_text

    system = types.ModuleType("System")
    drawing = types.ModuleType("System.Drawing")
    drawing.Color = FakeSystemColor
    system.Drawing = drawing

    sys.modules["Rhino"] = Rhino
    sys.modules["Rhino.Display"] = display
    sys.modules["Rhino.DocObjects"] = docobjects
    sys.modules["Rhino.Geometry"] = geometry
    sys.modules["scriptcontext"] = scriptcontext
    sys.modules["rhinoscriptsyntax"] = rhinoscriptsyntax
    sys.modules["System"] = system
    sys.modules["System.Drawing"] = drawing
    return Rhino


_FAKE_MODULES = [
    "Rhino",
    "Rhino.Display",
    "Rhino.DocObjects",
    "Rhino.Geometry",
    "scriptcontext",
    "rhinoscriptsyntax",
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
        doc.Objects = _BrokenIndexObjects(doc.Objects)

        n = _colour_doc_objects(
            {"B1": 5.0},
            "SAP_FrameID",
            0.0,
            5.0,
            layer_filter="SAP2000/Mesh/*",
        )
        assert n == 1
        assert doc.Objects._items[0].Attributes.ColorSource == "ColorFromObject"

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

    def test_colour_commit_readback_filters_noop(self, rhino_env):
        """Objects whose colour commit does not stick are not counted."""
        doc = rhino_env
        attrs = _NoOpObjectAttributes()
        attrs.LayerIndex = create_or_get_layer("SAP2000/Mesh/Frames/CL/UB300")
        attrs.SetUserString("SAP_FrameID", "B1")
        doc.Objects.AddLine(None, attrs)
        # Block the Recreate rescue (Delete refused) so the dropped in-place
        # commit is genuinely un-recoverable and must be counted as failed.
        doc.Objects.Delete = lambda guid, quiet=False: False

        n = _colour_doc_objects(
            {"B1": 5.0},
            "SAP_FrameID",
            0.0,
            5.0,
            layer_filter="SAP2000/Mesh/*",
        )
        assert n == 0

    def test_recreate_fallback_when_inplace_rejected(self, rhino_env):
        """When every in-place attribute commit is dropped (as in Rhino 8
        CPython), the Recreate fallback (delete + re-add with the colour
        baked into fresh creation-time attributes) still colours the object."""
        doc = rhino_env
        idx = create_or_get_layer("SAP2000/Mesh/Frames/CL/UB300")
        attrs = FakeObjectAttributes()
        attrs.LayerIndex = idx
        attrs.SetUserString("SAP_FrameID", "B1")
        oid = doc.Objects.AddLine(None, attrs)

        # Replace the stored object with a read-only-Attributes variant and
        # make ModifyAttributes refuse — forcing the Recreate path.
        ro = _ReadOnlyAttrObject()
        ro._ro_attrs = doc.Objects._items[oid].Attributes
        ro.Id = oid
        doc.Objects._items[oid] = ro
        doc.Objects.ModifyAttributes = lambda guid, a, quiet=False: False

        n = _colour_doc_objects({"B1": 5.0}, "SAP_FrameID", 0.0, 5.0)
        assert n == 1
        assert any(
            o.Attributes.ObjectColor is not None and o.Attributes.ColorSource == "ColorFromObject"
            for o in doc.Objects._items
        )

    def test_recreate_preserves_user_strings(self, rhino_env):
        """The Recreate fallback re-stamps the full UserStrings snapshot
        (snapshot → fresh attributes), so the SAP_FrameID match key AND the
        SAP_Type / other metadata survive delete + re-add even when in-place
        attribute commits are dropped."""
        doc = rhino_env
        idx = create_or_get_layer("SAP2000/Mesh/Frames/CL/UB300")
        attrs = FakeObjectAttributes()
        attrs.LayerIndex = idx
        attrs.SetUserString("SAP_FrameID", "B1")
        attrs.SetUserString("SAP_Type", "Frame")
        attrs.SetUserString("SAP_Section", "UB300")
        doc.Objects.AddLine(None, attrs)
        doc.Objects.ModifyAttributes = lambda guid, a, quiet=False: False

        n = _colour_doc_objects({"B1": 5.0}, "SAP_FrameID", 0.0, 5.0)
        assert n == 1
        assert any(o.Attributes.GetUserString("SAP_FrameID") == "B1" for o in doc.Objects._items)
        # The snapshot must carry the full metadata set, not just the match
        # key — losing SAP_Type was the v15 regression (empty rs.GetUserText).
        assert any(
            o.Attributes.GetUserString("SAP_Type") == "Frame"
            and o.Attributes.GetUserString("SAP_Section") == "UB300"
            for o in doc.Objects._items
        )

    def test_as_sd_color_rgb_explicit(self, rhino_env):
        """``_as_sd_color_rgb`` builds the opaque colour via
        ``System.Drawing.Color.FromArgb`` — the explicit constructor that
        bypasses ``ColorRGBA``'s ambiguous int/float overloads (which clamp
        0-255 ints to 0-1 and silently produce white)."""
        from fea_toolkit.rhino.colour_from_npz import _as_sd_color_rgb

        c = _as_sd_color_rgb((254, 254, 255))
        assert (c.R, c.G, c.B) == (254, 254, 255)

    def test_recreate_colour_matches_exact_rgb(self, rhino_env):
        """The Recreate fallback must store the exact wanted RGB (no
        float-overload clamping to white) so the read-back verification
        accepts the object and the frame is counted as coloured."""
        doc = rhino_env
        idx = create_or_get_layer("SAP2000/Mesh/Frames/CL/UB300")
        attrs = FakeObjectAttributes()
        attrs.LayerIndex = idx
        attrs.SetUserString("SAP_FrameID", "B1")
        attrs.SetUserString("SAP_Type", "Frame")
        doc.Objects.AddLine(None, attrs)
        doc.Objects.ModifyAttributes = lambda guid, a, quiet=False: False

        n = _colour_doc_objects({"B1": 5.0}, "SAP_FrameID", 0.0, 5.0)
        assert n == 1  # +5.0 on [0, 5] → full red (255, 25, 0)
        assert any(
            o.Attributes.ObjectColor is not None
            and (o.Attributes.ObjectColor.R, o.Attributes.ObjectColor.G, o.Attributes.ObjectColor.B)
            == (255, 25, 0)
            for o in doc.Objects._items
        )

    def test_recreate_restamps_match_key_when_snapshot_empty(self, rhino_env):
        """Even when the full UserString snapshot fails, the Recreate fallback
        re-stamps the caller's match key (id_key/us), so the object stays
        identifiable after delete + re-add."""
        doc = rhino_env
        idx = create_or_get_layer("SAP2000/Mesh/Frames/CL/UB300")
        attrs = FakeObjectAttributes()
        attrs.LayerIndex = idx
        attrs.SetUserString("SAP_FrameID", "B1")
        doc.Objects.AddLine(None, attrs)
        doc.Objects.ModifyAttributes = lambda guid, a, quiet=False: False
        # Snapshot read fails → empty dict (mirrors v16's attrs-based
        # snapshot returning nothing for a genuinely unreadable object).
        attrs.GetUserStrings = dict

        n = _colour_doc_objects({"B1": 5.0}, "SAP_FrameID", 0.0, 5.0)
        assert n == 1
        assert any(o.Attributes.GetUserString("SAP_FrameID") == "B1" for o in doc.Objects._items)

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

    def test_colour_frames_stepped_scale(self, rhino_env):
        """``scale_mode='stepped'`` quantises frame colours to discrete bands."""
        from fea_toolkit.rhino.results import colour_frames_from_results

        doc = rhino_env
        for sap_id in ("B-A", "B-B", "B-C"):
            self._add_frame(doc, "SAP2000/Mesh/Frames/CL/UB300", sap_id)
        data = {
            "pushover/+X/frame_sap_id": np.array(["B-A", "B-B", "B-C"]),
            "pushover/+X/frame_mz_i": np.array([[-100.0, 20.0, 100.0]]),
        }
        n = colour_frames_from_results(
            data,
            quantity="Mz",
            direction="+X",
            scale_mode="stepped",
            n_steps=9,
            verbose=False,
        )
        assert n == 3
        # Extremes saturate; +20 sits on the 0.25 band (255,197,191) — not
        # the continuous tint (255,209,204) it would otherwise receive.
        assert doc.Objects._items[0].Attributes.ObjectColor.rgb() == (0, 25, 255)
        assert doc.Objects._items[1].Attributes.ObjectColor.rgb() == (255, 197, 191)
        assert doc.Objects._items[2].Attributes.ObjectColor.rgb() == (255, 25, 0)

    def test_colour_frames_percentile_clip(self, rhino_env):
        """``clip_pct`` clips the value range so mid-range values get a
        visible continuous tint instead of washing out to near-white."""
        from fea_toolkit.rhino.results import colour_frames_from_results

        doc = rhino_env
        for sap_id in ("B-100", "B-50", "B-10", "B10", "B50", "B100"):
            self._add_frame(doc, "SAP2000/Mesh/Frames/CL/UB300", sap_id)
        data = {
            "pushover/+X/frame_sap_id": np.array(["B-100", "B-50", "B-10", "B10", "B50", "B100"]),
            "pushover/+X/frame_mz_i": np.array([[-100.0, -50.0, -10.0, 10.0, 50.0, 100.0]]),
        }
        # Without clipping the range is [-100, 100]; +10 maps near-white.
        n = colour_frames_from_results(data, quantity="Mz", direction="+X", verbose=False)
        assert n == 6
        rgb_unclipped = doc.Objects._items[3].Attributes.ObjectColor.rgb()
        assert min(rgb_unclipped) >= 200
        # With 20% clipping the range is [-50, 50]; +10 now gets a tint.
        colour_frames_from_results(
            data,
            quantity="Mz",
            direction="+X",
            clip_pct=20.0,
            verbose=False,
        )
        rgb_clipped = doc.Objects._items[3].Attributes.ObjectColor.rgb()
        assert rgb_clipped != rgb_unclipped
        assert min(rgb_clipped) < min(rgb_unclipped)
        # Extremes still saturate: -100 → blue, +100 → red in both modes.
        assert doc.Objects._items[0].Attributes.ObjectColor.rgb() == (0, 25, 255)
        assert doc.Objects._items[5].Attributes.ObjectColor.rgb() == (255, 25, 0)


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


class TestSuppressRedraw:
    """``suppress_redraw`` must disable redraw for the batch, restore it on
    exit, and issue a single redraw — including when the batch raises."""

    def test_disables_and_restores(self, rhino_env):
        from fea_toolkit.rhino.layers import suppress_redraw

        assert rhino_env.Views.RedrawEnabled is True
        with suppress_redraw():
            assert rhino_env.Views.RedrawEnabled is False
        assert rhino_env.Views.RedrawEnabled is True

    def test_restores_on_exception(self, rhino_env):
        from fea_toolkit.rhino.layers import suppress_redraw

        assert rhino_env.Views.RedrawEnabled is True
        try:
            with suppress_redraw():
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        assert rhino_env.Views.RedrawEnabled is True

    def test_nested_batches_restore_correctly(self, rhino_env):
        from fea_toolkit.rhino.layers import suppress_redraw

        with suppress_redraw():
            assert rhino_env.Views.RedrawEnabled is False
            with suppress_redraw():
                assert rhino_env.Views.RedrawEnabled is False
            assert rhino_env.Views.RedrawEnabled is False
        assert rhino_env.Views.RedrawEnabled is True


class _BrokenIndexObjects:
    """Mirror of Rhino 8's ``ObjectTable`` as seen by pythonnet: ``Count``
    works but the indexer is unusable; only iteration (``IEnumerable``)
    yields objects.  The mutation APIs (``ModifyAttributes`` / ``Delete`` /
    ``Add`` / ``Find``) remain available — exactly like real Rhino, where
    only ``doc.Objects[i]`` is broken."""

    def __init__(self, delegate):
        self._delegate = delegate

    @property
    def Count(self):
        return self._delegate.Count

    @property
    def _items(self):
        return self._delegate._items

    def __getitem__(self, index):
        raise IndexError

    def __iter__(self):
        return iter(self._delegate._items)

    def ModifyAttributes(self, guid, attrs, quiet=False):
        return self._delegate.ModifyAttributes(guid, attrs, quiet)

    def Delete(self, guid, quiet=False):
        return self._delegate.Delete(guid, quiet)

    def Add(self, geom, attrs=None):
        return self._delegate.Add(geom, attrs)

    def Find(self, guid):
        return self._delegate.Find(guid)


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
