"""Copy-on-write contract for the Preprocessor's model handling.

:meth:`~fea_toolkit.opensees.preprocessor.Preprocessor.run` must leave the
caller's :class:`~fea_toolkit.model.sap_data.SAPModelData` untouched, and
must achieve that *without duplicating* the model: it shares the nodes and
elements it does not change, and ``dataclasses.replace``-s the objects it
does change.

The tests here pin that contract.  It replaced the previous
``copy.deepcopy(model_data)`` fence, which duplicated the whole graph — the
peak-memory spike, and the allocation burst behind the GC-on-worker-thread
crash documented in ``docs/dev_notes.md`` (*The macOS GUI segfault*).
Background: ``docs/dev_notes.md`` → *Copy-on-write replaces the model
deepcopy*.
"""

import copy

from fea_toolkit.model.geometry import mesh_area_elements
from fea_toolkit.model.sap_data import (
    AreaElement,
    AreaMesh,
    FrameElement,
    FrameEndOffset,
    Group,
    Material,
    Node,
    Restraint,
    SAPModelData,
    Section,
    apply_material_defaults,
)
from fea_toolkit.opensees.preprocessor import Preprocessor, _copy_for_preprocessing

# ============================================================================
# Fixtures
# ============================================================================


def _steel() -> Material:
    """A fully-populated material (so ``apply_material_defaults`` is a no-op)."""
    return Material(
        name="Steel",
        type="Steel",
        E_mod=2.0e11,
        G_mod=7.7e10,
        nu=0.3,
        unit_weight=7.85e4,
        Fy=2.5e8,
    )


def _crossing_frames_model() -> SAPModelData:
    """Two perpendicular frames crossing at (5, 0, 0), plus an offset frame.

    The crossing pair makes ``split_elements`` fire (``AtFrames`` in the
    auto-mesh table), and the end offset on ``C`` exercises
    ``apply_frame_end_offsets`` — the two paths that used to mark a parent
    inactive (and rewrite its connectivity) in place.
    """
    nodes = {
        sid: Node(node_id=sid, node_tag=i, x=xyz[0], y=xyz[1], z=xyz[2])
        for i, (sid, xyz) in enumerate(
            [
                ("1", (0.0, 0.0, 0.0)),
                ("2", (10.0, 0.0, 0.0)),
                ("3", (5.0, -5.0, 0.0)),
                ("4", (5.0, 5.0, 0.0)),
                ("5", (0.0, 0.0, -5.0)),
                ("6", (0.0, 0.0, -10.0)),
            ],
            start=1,
        )
    }
    frame_elements = {
        "A": FrameElement(elem_id="A", elem_tag=10, node_i="1", node_j="2"),
        "B": FrameElement(elem_id="B", elem_tag=11, node_i="3", node_j="4"),
        "C": FrameElement(elem_id="C", elem_tag=12, node_i="5", node_j="6"),
    }
    return SAPModelData(
        nodes=nodes,
        restraints={"5": Restraint([1, 1, 1, 1, 1, 1])},
        materials={"Steel": _steel()},
        sections={"S1": Section(name="S1", shape="General", material="Steel")},
        frame_elements=frame_elements,
        area_elements={},
        frame_assignments={"A": "S1", "B": "S1", "C": "S1"},
        area_assignments={},
        groups={"G": Group(name="G")},
        frame_auto_mesh={"A": {"AtFrames": True}, "B": {"AtFrames": True}},
        frame_end_offsets={"C": FrameEndOffset(end_i=0.5, end_j=0.5)},
    )


def _meshed_slab_model() -> SAPModelData:
    """A single 10x10 quad slab with an auto-mesh assignment.

    Meshing marks the parent area inactive and appends to its
    ``child_ids`` — the area-element equivalent of the frame path.
    """
    corners = [("1", (0.0, 0.0)), ("2", (10.0, 0.0)), ("3", (10.0, 10.0)), ("4", (0.0, 10.0))]
    nodes = {
        sid: Node(node_id=sid, node_tag=i, x=xy[0], y=xy[1], z=0.0)
        for i, (sid, xy) in enumerate(corners, start=1)
    }
    return SAPModelData(
        nodes=nodes,
        restraints={},
        materials={"Concrete": Material(name="Concrete", type="Concrete", E_mod=2.5e10, nu=0.2)},
        sections={"Slab": Section(name="Slab", shape="Shell", material="Concrete")},
        frame_elements={},
        area_elements={
            "S1": AreaElement(
                area_id="S1",
                area_tag=100,
                node_ids=["1", "2", "3", "4"],
                thickness=0.2,
            )
        },
        frame_assignments={},
        area_assignments={"S1": "Slab"},
        groups={"G": Group(name="G", objects=["Area:S1"])},
        frame_auto_mesh={},
        area_mesh={"S1": AreaMesh(auto_mesh=True, max_size=5.0)},
    )


# ============================================================================
# The source model is never mutated
# ============================================================================


class TestSourceModelIsUntouched:
    """``run()`` must not write to anything reachable from the source model."""

    def test_frame_splitting_and_offsets_leave_the_source_untouched(self):
        """Frames split and offset — but only on the preprocessor's own copies."""
        model = _crossing_frames_model()
        snapshot = copy.deepcopy(model)

        mesh = Preprocessor({"split_elements": True}).run(model)

        # The pipeline really did mutate something…
        assert mesh.frame_elements["A"].inactive
        assert mesh.frame_elements["A"].child_ids
        # …including rewriting connectivity for the end offset…
        assert mesh.frame_elements["C"].node_i != "5"
        # …but the source model is unchanged.
        assert model.frame_elements["A"].inactive is False
        assert model.frame_elements["A"].child_ids == []
        assert model.frame_elements["C"].node_i == "5"
        assert model == snapshot

    def test_area_meshing_leaves_the_source_untouched(self):
        """Area meshing marks the parent inactive on a copy only."""
        model = _meshed_slab_model()
        snapshot = copy.deepcopy(model)

        mesh = Preprocessor({"create_shells": True, "split_elements": False}).run(model)

        # The parent was meshed and deactivated in the output model…
        assert mesh.area_elements["S1"].inactive
        assert mesh.area_elements["S1"].child_ids
        assert len(mesh.area_elements) > 1
        # …while the source model's area is untouched.
        assert model.area_elements["S1"].inactive is False
        assert model.area_elements["S1"].child_ids == []
        assert model == snapshot

    def test_group_membership_leaves_the_source_untouched(self):
        """New sub-areas join the group without touching the source group."""
        model = _meshed_slab_model()
        snapshot = copy.deepcopy(model)

        mesh = Preprocessor({"create_shells": True, "split_elements": False}).run(model)

        assert mesh.groups["G"].objects == ["Area:S1"]
        assert model == snapshot


# ============================================================================
# The copy helper — why groups and materials are copied one level deeper
# ============================================================================


class TestCopyForPreprocessing:
    """``_copy_for_preprocessing`` owns the containers, shares the objects."""

    def test_containers_are_new_and_objects_are_shared(self):
        model = _crossing_frames_model()
        clone = _copy_for_preprocessing(model)

        assert clone is not model
        assert clone.nodes is not model.nodes
        assert clone.frame_elements is not model.frame_elements
        assert clone.materials is not model.materials
        # …but the objects inside are the very same ones — no second graph.
        assert clone.nodes["1"] is model.nodes["1"]
        assert clone.frame_elements["A"] is model.frame_elements["A"]
        assert clone.sections["S1"] is model.sections["S1"]

    def test_groups_get_their_own_objects_list(self):
        """Area meshing appends to ``Group.objects``, so groups are copied."""
        model = _meshed_slab_model()

        clone = _copy_for_preprocessing(model)
        clone.groups["G"].objects.append("Area:NEW")

        assert clone.groups["G"] is not model.groups["G"]
        assert model.groups["G"].objects == ["Area:S1"]

    def test_materials_are_copied_so_inplace_defaults_do_not_leak(self):
        """``apply_material_defaults`` writes in place — on the copy only."""
        model = _meshed_slab_model()
        model.materials["Plain"] = Material(name="Plain", type="Steel")
        before = model.materials["Plain"].E_mod

        clone = _copy_for_preprocessing(model)
        apply_material_defaults(clone.materials, clone.units)

        assert clone.materials["Plain"] is not model.materials["Plain"]
        assert clone.materials["Plain"].E_mod > 0
        assert model.materials["Plain"].E_mod == before


# ============================================================================
# Copy-on-write: share what is untouched, replace what changes
# ============================================================================


class TestObjectsAreSharedNotCopied:
    """The output model shares the source's untouched objects."""

    def test_untouched_nodes_and_elements_are_the_same_objects(self):
        """Nothing splits here, so no second copy of the graph may exist."""
        model = _crossing_frames_model()
        model.frame_end_offsets = {}

        mesh = Preprocessor({"split_elements": False}).run(model)

        assert mesh.frame_elements["A"] is model.frame_elements["A"]
        assert mesh.nodes["1"] is model.nodes["1"]
        # The MeshModel's dicts are its own, even though the objects are shared.
        assert mesh.frame_elements is not model.frame_elements
        assert mesh.nodes is not model.nodes

    def test_the_model_graph_is_never_deepcopied(self, monkeypatch):
        """Pin the copy-on-write fence.

        A ``copy.deepcopy(model_data)`` regression would put the entire model
        graph back on the worker thread (see the dev note) and double peak
        memory.  The pipeline may still deep-copy small, derived objects
        (e.g. per-group element-property dicts), so the assertion is
        narrowly about the model itself.
        """
        seen: list[str] = []
        real_deepcopy = copy.deepcopy

        def spy(obj, memo=None):
            seen.append(type(obj).__name__)
            return real_deepcopy(obj, memo)

        monkeypatch.setattr(copy, "deepcopy", spy)
        Preprocessor({"split_elements": True}).run(_crossing_frames_model())

        assert "SAPModelData" not in seen


# ============================================================================
# The geometry helpers the preprocessor relies on
# ============================================================================


class TestGeometryHelpersDoNotMutateTheirInputs:
    """The helpers may extend the dicts they are given, never the objects."""

    def test_mesh_area_elements_leaves_the_input_area_untouched(self):
        """The parent handed in must come back unmarked, as a replaced copy."""
        model = _meshed_slab_model()
        parent = model.area_elements["S1"]

        areas, _assignments, _nodes, _tag = mesh_area_elements(
            model.area_elements,
            model.area_assignments,
            model.nodes,
            {"S1": AreaMesh(auto_mesh=True, max_size=5.0)},
            next_tag=200,
        )

        assert len(areas) > 1, "the slab should have been meshed"
        assert areas["S1"] is not parent, "the parent must be a replaced copy"
        assert parent.inactive is False
        assert parent.child_ids == []
        assert areas["S1"].inactive is True
        assert areas["S1"].child_ids
