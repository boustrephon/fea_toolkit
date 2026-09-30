"""The GUI runs the Preprocessor and shows the resulting topology (P24).

Qt-dependent (``needs_gui``).  The preprocessor itself is exercised by the model
and OpenSees suites; what is tested here is the GUI wiring around it — the
worker, the displayed ``MeshModel``, and the tree/inspector/toggle that follow.

The model below is a **T-junction** whose beam asks for splitting at joints
(``frame_auto_mesh``: SAP2000's auto-mesh flags).  Splitting is opt-in per
element, so a model with no such request is legitimately unchanged.
"""

import pytest

from tests.fixtures.gui_preprocess import run_preprocess

pytestmark = pytest.mark.needs_gui


@pytest.fixture(scope="module")
def qapp():
    """Provide the single process-wide ``QApplication`` Qt requires."""
    from qtpy.QtWidgets import QApplication

    yield QApplication.instance() or QApplication(["pytest-fea-gui"])


def _t_junction_model(*, auto_split: bool = True):
    """A beam (1-2) crossed by a column framing into its midpoint (node 3).

    Args:
        auto_split: Whether the beam requests ``AtJoints`` splitting, which is
            what makes the Preprocessor subdivide it.
    """
    from fea_toolkit.model.sap_data import (
        FrameElement,
        Material,
        Node,
        Restraint,
        SAPModelData,
        Section,
    )

    return SAPModelData(
        nodes={
            "1": Node(node_id="1", node_tag=1, x=0.0, y=0.0, z=0.0),
            "2": Node(node_id="2", node_tag=2, x=10.0, y=0.0, z=0.0),
            "3": Node(node_id="3", node_tag=3, x=5.0, y=0.0, z=0.0),
            "4": Node(node_id="4", node_tag=4, x=5.0, y=0.0, z=-3.0),
        },
        restraints={"4": Restraint([1, 1, 1, 1, 1, 1])},
        materials={
            "Steel": Material(
                name="Steel",
                type="Steel",
                E_mod=2.0e11,
                G_mod=7.7e10,
                nu=0.3,
                unit_weight=7.85e4,
                Fy=2.5e8,
            )
        },
        sections={
            "SEC1": Section(
                name="SEC1",
                shape="I/Wide Flange",
                material="Steel",
                A=0.005,
                I33=8.4e-5,
                I22=7.7e-6,
                J=2.0e-6,
            )
        },
        frame_elements={
            "1": FrameElement(elem_id="1", elem_tag=1, node_i="1", node_j="2"),
            "2": FrameElement(elem_id="2", elem_tag=2, node_i="3", node_j="4"),
        },
        area_elements={},
        frame_assignments={"1": "SEC1", "2": "SEC1"},
        area_assignments={},
        groups={},
        frame_auto_mesh={"1": {"AtJoints": True}} if auto_split else {},
    )


@pytest.fixture()
def window(qapp, monkeypatch, tmp_path):
    """A ``MainWindow`` showing the T-junction model."""
    from fea_toolkit.gui.controllers.interaction import CONFIG_ENV_VAR
    from fea_toolkit.gui.main_window import MainWindow

    monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "absent.json"))
    win = MainWindow(model=_t_junction_model())
    win.resize(900, 700)
    win.show()
    yield win
    win.close()


def _inspector_rows(window) -> dict:
    """The inspector's ``{field name: value text}`` for the current selection."""
    table = window._inspector._table
    return {table.item(row, 0).text(): table.item(row, 1).text() for row in range(table.rowCount())}


def test_the_actions_need_a_parsed_source_model(qapp, monkeypatch, tmp_path):
    """With nothing to preprocess, the Model-menu actions stay off."""
    from fea_toolkit.gui.controllers.interaction import CONFIG_ENV_VAR
    from fea_toolkit.gui.main_window import MainWindow

    monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "absent.json"))
    win = MainWindow(model=_t_junction_model())
    win._remember_source(object())  # a model the Preprocessor cannot consume
    try:
        assert win._store is None
        win._set_model_actions_enabled(False)
        assert win._actions["model.mesh"].isEnabled() is False
        win._mesh_preset(
            {"split_elements": True, "create_shells": False},
            "Splitting elements at joints",
        )
        assert "Open a SAP2000 model" in win._message_log.toPlainText()
    finally:
        win.close()


def test_split_elements_preprocesses_and_displays_the_model(window):
    """Model ▸ Split elements swaps the parsed model for the ``MeshModel``."""
    from fea_toolkit.model.mesh_model import MeshModel

    assert window._actions["model.mesh"].isEnabled() is True

    run_preprocess(window, "model.split")

    assert isinstance(window._model, MeshModel)
    elements = window._model.frame_elements
    # The beam splits at the joint lying on it; the column, which was already
    # framed into that joint, is untouched.  The superseded parent is kept.
    assert sorted(elements) == ["1", "1-0", "1-1", "2"]
    assert sorted(e for e, elem in elements.items() if elem.parent_id) == ["1-0", "1-1"]
    assert elements["1"].inactive is True
    assert elements["1"].child_ids == ["1-0", "1-1"]


def test_the_log_summarises_the_result(window):
    """The Message Log says what the preprocessor did, in user terms."""
    run_preprocess(window, "model.split")

    log = window._message_log.toPlainText()
    assert "Splitting elements at joints" in log
    assert "Preprocessed: 4 frame elements (2 split sub-elements, 1 superseded parents)" in log


def test_a_model_that_requests_no_splitting_says_so(qapp, monkeypatch, tmp_path):
    """No auto-mesh flags means nothing to split -- not a silent no-op."""
    from fea_toolkit.gui.controllers.interaction import CONFIG_ENV_VAR
    from fea_toolkit.gui.main_window import MainWindow

    monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "absent.json"))
    win = MainWindow(model=_t_junction_model(auto_split=False))
    try:
        run_preprocess(win, "model.split")
        assert "no element requested splitting" in win._message_log.toPlainText()
        assert len(win._model.frame_elements) == 2
    finally:
        win.close()


def test_the_inspector_surfaces_the_parent_child_topology(window):
    """Split sub-elements get real rows, and their topology is browsable."""
    run_preprocess(window, "model.split")

    index = window._tree_model.index_for("frame_elements", "1-0")
    assert index is not None
    assert index.isValid()

    assert window._select_entity_in_tree("frame_elements", "1-0") is True
    child = _inspector_rows(window)
    assert {"parent_id", "child_ids", "inactive"} <= set(child)
    assert child["parent_id"] == "1"

    assert window._select_entity_in_tree("frame_elements", "1") is True
    parent = _inspector_rows(window)
    assert parent["inactive"].lower() == "true"
    assert "1-0" in parent["child_ids"]
    assert "1-1" in parent["child_ids"]


def _camera_flat(window):
    """The viewport camera as a flat float array.

    PyVista hands back a ``CameraPosition`` (position, focal point, view up), so
    flatten it rather than guessing its nesting depth.
    """
    import numpy as np

    def _flatten(value):
        try:
            return [float(x) for x in value]
        except TypeError:
            flat = []
            for item in value:
                flat.extend(_flatten(item))
            return flat

    return np.array(_flatten(window._interactor.camera_position))


def test_preprocessing_keeps_the_camera(window):
    """The view must not jump when the model is preprocessed."""
    import numpy as np

    camera = _camera_flat(window)

    run_preprocess(window, "model.split")

    assert np.allclose(_camera_flat(window), camera, rtol=1e-6, atol=1e-9)


# ── Split then Mesh areas: the second action must actually mesh ────────


def _slab_model():
    """A single four-node area — enough to see meshing happen."""
    from fea_toolkit.model.sap_data import (
        AreaElement,
        Material,
        Node,
        Restraint,
        SAPModelData,
        ShellSection,
    )

    return SAPModelData(
        nodes={
            "1": Node("1", 1, 0.0, 0.0, 0.0),
            "2": Node("2", 2, 4.0, 0.0, 0.0),
            "3": Node("3", 3, 4.0, 4.0, 0.0),
            "4": Node("4", 4, 0.0, 4.0, 0.0),
        },
        restraints={"1": Restraint([1, 1, 1, 1, 1, 1])},
        materials={"C30": Material(name="C30", type="Concrete", E_mod=3.0e10, unit_weight=25.0)},
        sections={"SLAB": ShellSection(name="SLAB", shape="Shell", material="C30", thickness=0.2)},
        frame_elements={},
        area_elements={"A1": AreaElement("A1", 1, ["1", "2", "3", "4"])},
        frame_assignments={},
        area_assignments={"A1": "SLAB"},
        groups={},
        frame_auto_mesh={},
    )


@pytest.fixture()
def slab_window(qapp, monkeypatch, tmp_path):
    """A ``MainWindow`` showing a single area element."""
    from fea_toolkit.gui.controllers.interaction import CONFIG_ENV_VAR
    from fea_toolkit.gui.main_window import MainWindow

    monkeypatch.setenv(CONFIG_ENV_VAR, str(tmp_path / "absent.json"))
    win = MainWindow(model=_slab_model())
    win.resize(900, 700)
    win.show()
    yield win
    win.close()


def test_mesh_areas_after_split_really_meshes(slab_window):
    """Regression: ``Mesh areas`` must not silently reuse the ``Split`` result.

    ``InMemoryModelStore.mesh()`` short-circuited on the model
    ``set_preprocessed()`` had stored, so the second config was ignored and the
    second action returned the first's model.  On a real building that meant a
    split-only mesh was labelled "Meshed" and analysed with un-meshed areas — a
    mechanism, so every case failed with ``matrix singular``.
    """
    run_preprocess(slab_window, "model.split")
    assert not slab_window._model.area_element_types  # split only: no shells

    run_preprocess(slab_window, "model.mesh")
    assert slab_window._model.area_element_types  # the config was honoured
    assert slab_window._store.preprocessed() is slab_window._model
    assert "Meshed" in slab_window._message_log.toPlainText()
