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


def _branched_model():
    """The chain plus a branch framing into the *interior* of member 2.

    An interior joint is what the Preprocessor splits at, so this is the model
    that makes "an inactive parent is not drawn" observable.
    """
    from fea_toolkit.model.sap_data import FrameElement, Node

    md = _chain_model()
    md.nodes["5"] = Node(node_id="5", node_tag=5, x=0.0, y=0.0, z=15.0)  # on member 2
    md.frame_elements["4"] = FrameElement(elem_id="4", elem_tag=4, node_i="5", node_j="9")
    md.frame_assignments["4"] = "UB300"
    md.frame_auto_mesh = {"2": {"AtJoints": True}}
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
            Selection(exclude_sections=["COL"]),
            Selection(exclude_element_types=["Area"]),
            Selection(exclude_sections=["COL", "BEAM"]),
            Selection(element_types=["Area"], exclude_sections=["Roof slab"]),
            Selection(exclude_elevation_range=(0.0, 3.0)),
            Selection(sections=["NOT"], exclude_sections=["NOT"]),
        ]
        for selection in cases:
            assert Selection.from_string(selection.to_string()) == selection

    def test_uses_the_short_keys_in_a_readable_order(self):
        from fea_toolkit.model.selection import Selection

        selection = Selection(sections=["Roof slab"], elevation_range=(3.0, 6.0))

        assert selection.to_string() == "section=Roof slab z=3.0:6.0"

    def test_exclusion_is_written_with_NOT(self):
        from fea_toolkit.model.selection import Selection

        assert Selection(exclude_sections=["COL"]).to_string() == "NOT section=COL"
        assert (
            Selection(element_types=["Area"], exclude_sections=["Roof slab"]).to_string()
            == "type=Area NOT section=Roof slab"
        )

    def test_a_value_named_NOT_is_not_the_keyword(self):
        from fea_toolkit.model.selection import Selection

        assert Selection(sections=["NOT"]).to_string() == "section=NOT"
        assert Selection.from_string("section=NOT").sections == ["NOT"]
        assert Selection(exclude_sections=["NOT"]).to_string() == "NOT section=NOT"

    def test_values_with_delimiters_round_trip(self):
        """A comma, semicolon, quote or ``KEY=`` fragment in a value survives."""
        from fea_toolkit.model.selection import Selection

        cases = [
            Selection(sections=["S, 200"]),
            Selection(sections=["A; B"]),
            Selection(sections=["z=3"]),
            Selection(sections=['has "quotes" and \\ a slash']),
            Selection(sections=["S, 200", "plain"]),
            Selection(element_ids=["1 2", "3"]),
        ]
        for selection in cases:
            assert Selection.from_string(selection.to_string()) == selection

    def test_a_value_ending_in_NOT_with_a_following_clause_round_trips(self):
        """``COL NOT`` must be quoted so a later clause is not read as negation."""
        from fea_toolkit.model.selection import Selection

        selection = Selection(sections=["COL NOT"], materials=["C30"])

        assert selection.to_string() == 'section="COL NOT" material=C30'
        assert Selection.from_string(selection.to_string()) == selection

    def test_a_standalone_NOT_list_value_round_trips_with_a_following_clause(self):
        """A trailing ``NOT`` list item must be quoted so the next clause survives."""
        from fea_toolkit.model.selection import Selection

        selection = Selection(sections=["COL", "NOT"], materials=["C30"])

        assert selection.to_string() == 'section=COL, "NOT" material=C30'
        assert Selection.from_string(selection.to_string()) == selection

    def test_elevation_bounds_keep_their_precision(self):
        """:g would round the bound; the written form has to be exact."""
        from fea_toolkit.model.selection import Selection

        selection = Selection(elevation_range=(3.0000001, 6.0))

        assert selection.to_string() == "z=3.0000001:6.0"
        assert Selection.from_string(selection.to_string()).elevation_range == (3.0000001, 6.0)

    def test_an_empty_selection_has_an_empty_expression(self):
        from fea_toolkit.model.selection import Selection

        assert Selection().to_string() == ""

    def test_from_string_accepts_the_story_aliases(self):
        """``story`` is part of the grammar, so the round-trip covers every field."""
        from fea_toolkit.model.selection import Selection

        assert Selection.from_string("story=Level 2").story == ["Level 2"]
        assert Selection.from_string("stories=Level 1, Level 2").story == ["Level 1", "Level 2"]


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

    def test_a_joint_selection_brings_the_panels_touching_it(self):
        """Areas join the same one-hop expansion as frames."""
        from fea_toolkit.model.sap_data import AreaElement
        from fea_toolkit.model.selection import Selection

        md = _chain_model()
        md.area_elements["A1"] = AreaElement(area_id="A1", area_tag=101, node_ids=["3", "4", "9"])
        md.area_assignments["A1"] = "UB300"

        frames, areas, nodes = Selection(
            element_types=["Node"], element_ids=["3"]
        ).resolve_connected(md)

        assert areas == {"A1"}  # the panel on joint 3 ...
        assert nodes == {"2", "3", "4", "9"}  # ... drawn closed, plus member 2's far end
        assert frames == {"2", "3"}  # the members on joint 3 — never member 1 further up

    def test_an_excluded_type_is_not_reintroduced_by_expansion(self):
        """``exclude_element_types=['Area']`` keeps areas out even though their
        corners are retained nodes."""
        from fea_toolkit.model.sap_data import AreaElement
        from fea_toolkit.model.selection import Selection

        md = _chain_model()
        md.area_elements["A1"] = AreaElement(area_id="A1", area_tag=101, node_ids=["3", "4", "9"])
        md.area_assignments["A1"] = "UB300"

        frames, areas, nodes = Selection(exclude_element_types=["Area"]).resolve_connected(md)

        assert frames == {"1", "2", "3"}  # frames are not excluded
        assert areas == set()  # the excluded area is not reintroduced as connected
        assert nodes == {"1", "2", "3", "4", "9"}  # nodes are "not Area", so retained

    def test_inactive_split_parents_are_not_drawn(self):
        """A preprocessed model shows its active sub-elements, not the parent."""
        from fea_toolkit.model.selection import Selection
        from fea_toolkit.opensees.preprocessor import preprocess_model

        mesh = preprocess_model(_branched_model(), {"split_elements": True})
        frames, _areas, _nodes = Selection(sections=["UB300"]).resolve_connected(mesh)

        assert "2" not in frames  # the superseded parent
        assert {"2-0", "2-1"} <= frames  # its active sub-elements


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

    def test_a_collapsed_and_filtered_view_composes(self):
        """``collapse_to_parents`` and a selection are independent predicates."""
        from fea_toolkit.model.selection import Selection
        from fea_toolkit.opensees.preprocessor import preprocess_model
        from fea_toolkit.plotting.viewer import ModelViewer

        mesh = preprocess_model(_branched_model(), {"split_elements": True})
        viewer = ModelViewer(
            mesh_model=mesh,
            collapse_to_parents=True,
            selection=Selection(sections=["UB300"]),
        )
        frames, _shells, _nodes = viewer.geometry()

        # Collapsed: the unsplit members, so member 2 rather than its sub-elements.
        assert sorted(frame.elem_id for frame in frames) == ["1", "2", "3", "4"]

    def test_an_empty_selection_draws_the_members_and_their_joints(self):
        """An empty expression means "everything" — minus joints on nothing."""
        from fea_toolkit.model.selection import Selection
        from fea_toolkit.plotting.viewer import ModelViewer

        frames, _shells, nodes = ModelViewer(
            model_data=_chain_model(), selection=Selection()
        ).geometry()

        assert sorted(frame.elem_id for frame in frames) == ["1", "2", "3"]
        assert sorted(node.node_id for node in nodes) == ["1", "2", "3", "4"]  # not "9"


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

    def test_a_derived_view_can_be_registered_without_activating_it(self):
        """``activate=False`` keeps the user where they were."""
        from fea_toolkit.gui.controllers.view_registry import ViewRegistry
        from fea_toolkit.model.selection import Selection

        registry = ViewRegistry()
        registry.add_geometry("unprocessed", "Unprocessed", _chain_model())
        derived = registry.add_derived(
            "unprocessed:1",
            "joint 2",
            "unprocessed",
            Selection(element_types=["Node"], element_ids=["2"]),
            activate=False,
        )

        assert derived.active is False
        assert registry.active.key == "unprocessed"
