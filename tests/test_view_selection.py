"""The selection *lens* a view renders through — Qt-free.

``Selection``'s criteria and the exact-match resolution the *analysis* callers
use are tested in ``test_sections_selection.py``.  What is tested here is the
deliberately different **display** resolution — a joint selection brings the
members framing into it — plus the viewer and the registry that consume it.
"""

import pytest


def _chain_model():
    """The sample member extended into the chain 1-2-3-4, plus an orphan node.

    A chain is what makes one-hop expansion observable: selecting joint 2 must
    show members 1 and 2, never member 3 further along.
    """
    from examples.sample_model import make_sample_model
    from fea_toolkit.model.sap_data import FrameElement, Node

    md = make_sample_model()
    md.nodes["3"] = Node(node_id="3", node_tag=3, x=0.0, y=0.0, z=20.0)
    md.nodes["4"] = Node(node_id="4", node_tag=4, x=0.0, y=0.0, z=30.0)
    md.nodes["9"] = Node(node_id="9", node_tag=9, x=5.0, y=0.0, z=0.0)  # on nothing
    md.frame_elements["2"] = FrameElement(elem_id="2", elem_tag=2, node_i="2", node_j="3")
    md.frame_elements["3"] = FrameElement(elem_id="3", elem_tag=3, node_i="3", node_j="4")
    md.frame_assignments["2"] = "UB300"
    md.frame_assignments["3"] = "UB300"
    return md


class TestSelectionExpression:
    """``Selection.to_string`` is the inverse of ``from_string``."""

    def test_round_trips_through_the_expression_form(self):
        """A selection survives display → edit → re-parse unchanged."""
        from fea_toolkit.model.selection import Selection

        cases = [
            Selection(),
            Selection(element_types=["Frame", "Area"]),
            Selection(sections=["Roof slab"], elevation_range=(3.0, 6.0)),
            Selection(materials=["C30"], groups=["Walls"], constraints=["DIAPHRAGM"]),
            Selection(element_ids=["1", "2"], story=["Level 2"]),
        ]
        for selection in cases:
            assert Selection.from_string(selection.to_string()) == selection

    def test_uses_the_short_keys_in_a_readable_order(self):
        from fea_toolkit.model.selection import Selection

        selection = Selection(sections=["Roof slab"], elevation_range=(3.0, 6.0))

        assert selection.to_string() == "section=Roof slab z=3:6"

    def test_an_empty_selection_has_an_empty_expression(self):
        from fea_toolkit.model.selection import Selection

        assert Selection().to_string() == ""


class TestResolveConnected:
    """Display resolution: joints bring their members, one hop."""

    def test_a_joint_selection_brings_the_members_framing_into_it(self):
        """A lone marker is useless to look at; the connection is the point."""
        from fea_toolkit.model.selection import Selection

        frames, areas, nodes = Selection(
            element_types=["Node"], element_ids=["1"]
        ).resolve_connected(_chain_model())

        assert frames == {"1"}  # the member on joint 1 ...
        assert nodes == {"1", "2"}  # ... and its far end, so it draws complete
        assert areas == set()

    def test_the_expansion_is_one_hop_only(self):
        """Joint 2 shows members 1 and 2 — member 3 is two hops away."""
        from fea_toolkit.model.selection import Selection

        frames, _areas, nodes = Selection(
            element_types=["Node"], element_ids=["2"]
        ).resolve_connected(_chain_model())

        assert frames == {"1", "2"}
        assert nodes == {"1", "2", "3"}

    def test_an_element_filter_reports_the_joints_of_what_it_shows(self):
        """An element filter shows the joints of its members, not every node."""
        from fea_toolkit.model.selection import Selection

        frames, _areas, nodes = Selection(sections=["UB300"]).resolve_connected(_chain_model())

        assert frames == {"1", "2", "3"}
        assert nodes == {"1", "2", "3", "4"}
        assert "9" not in nodes  # the orphan joint is not part of the shown members

    def test_a_selection_matching_nothing_resolves_to_nothing(self):
        from fea_toolkit.model.selection import Selection

        assert Selection(sections=["NOT-A-SECTION"]).resolve_connected(_chain_model()) == (
            set(),
            set(),
            set(),
        )


@pytest.mark.needs_pyvista
class TestViewerAppliesTheLens:
    """``ModelViewer(selection=...)`` draws the resolved geometry only."""

    def test_the_viewer_draws_only_the_selected_geometry(self):
        from fea_toolkit.model.selection import Selection
        from fea_toolkit.plotting.viewer import ModelViewer

        viewer = ModelViewer(
            model_data=_chain_model(),
            selection=Selection(element_types=["Node"], element_ids=["2"]),
        )
        frames, _shells, nodes = viewer.geometry()

        assert sorted(frame.elem_id for frame in frames) == ["1", "2"]
        assert sorted(node.node_id for node in nodes) == ["1", "2", "3"]

    def test_without_a_selection_the_whole_model_is_drawn(self):
        from fea_toolkit.plotting.viewer import ModelViewer

        frames, _shells, nodes = ModelViewer(model_data=_chain_model()).geometry()

        assert sorted(frame.elem_id for frame in frames) == ["1", "2", "3"]
        assert sorted(node.node_id for node in nodes) == ["1", "2", "3", "4", "9"]


class TestDerivedViews:
    """A derived view is a lens on a lens: same geometry, narrower filter."""

    def test_a_derived_view_shares_its_parents_geometry(self):
        from fea_toolkit.gui.controllers.view_registry import ViewRegistry
        from fea_toolkit.model.selection import Selection

        registry = ViewRegistry()
        registry.add_geometry("processed", "Processed", _chain_model())
        derived = registry.add_derived(
            "processed:1",
            "Processed \u00b7 joint 2",
            "processed",
            Selection(element_types=["Node"], element_ids=["2"]),
        )

        assert derived is not None
        assert derived.parent == "processed"
        assert derived.selection is not None
        assert registry.source("processed:1") is registry.source("processed")
        assert registry.active.key == "processed:1"

    def test_a_derived_view_reports_the_filtered_counts(self):
        """The Inspector reports what the view shows, not the parent's totals."""
        from fea_toolkit.gui.controllers.view_registry import ViewRegistry
        from fea_toolkit.model.selection import Selection

        registry = ViewRegistry()
        registry.add_geometry("processed", "Processed", _chain_model())
        derived = registry.add_derived(
            "processed:1",
            "joint 2",
            "processed",
            Selection(element_types=["Node"], element_ids=["2"]),
        )

        assert derived.n_frames == 2
        assert derived.n_nodes == 3
        assert registry.get("processed").n_frames == 3  # the parent is untouched

    def test_an_unknown_parent_registers_nothing(self):
        from fea_toolkit.gui.controllers.view_registry import ViewRegistry

        registry = ViewRegistry()

        assert registry.add_derived("derived", "Derived", "no-such-parent", None) is None
        assert len(registry) == 0
