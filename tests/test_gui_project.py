"""Tests for the Qt-free project-bundle module (:mod:`fea_toolkit.gui.project`).

The module is exercised without the ``[gui]`` extra and without OpenSees: the
payload-mapping helpers are pure, and the write/read round-trip uses synthetic
results arrays and JSON payloads (no model stage).
"""

import json
from typing import Optional

import numpy as np

from fea_toolkit.analysis.base import AnalysisResult
from fea_toolkit.gui.controllers.view_registry import FIGURE, GEOMETRY, RESULTS, TABLE, View
from fea_toolkit.gui.project import (
    build_manifest,
    collect_project_results,
    detect_model_format,
    figure_identity,
    figures_payload,
    is_project,
    mode_shape_arrays,
    pushover_displacement_arrays,
    read_project,
    rs_displacement_arrays,
    tables_payload,
    views_payload,
    write_project,
    zip_project,
)
from fea_toolkit.workflow.steps import CASES, MODAL, PUSHOVER, RS, StepResult, Table

# ── Helpers ──────────────────────────────────────────────────────────


def _modal_step() -> StepResult:
    return StepResult(
        kind=MODAL,
        label="Modal",
        payload=AnalysisResult(
            name="Modal",
            analysis_type="ModalAnalysis",
            data={
                "modal": {
                    "periods": [1.0, 0.5],
                    "frequencies": [1.0, 2.0],
                    "modal_props": {"partiMassRatiosMX": [0.8, 0.1]},
                },
                "shapes": {},
            },
        ),
    )


def _rs_step(direction: str, node_displacements: Optional[dict] = None) -> StepResult:
    data = {
        "rs_result": {
            "base_shear_cqc": 120.0,
            "base_shear_srss": 130.0,
            "base_moment_cqc": 140.0,
            "base_moment_srss": 150.0,
            "base_shear_total": 160.0,
        },
        "direction": direction,
    }
    if node_displacements is not None:
        data["node_displacements"] = node_displacements
    return StepResult(
        kind=RS,
        label=f"Response spectrum ({direction})",
        payload=AnalysisResult(
            name="ResponseSpectrum",
            analysis_type="ResponseSpectrumAnalysis",
            data=data,
        ),
    )


def _pushover_step() -> StepResult:
    return StepResult(
        kind=PUSHOVER,
        label="Pushover",
        payload=AnalysisResult(
            name="Pushover",
            analysis_type="PushoverAnalysis",
            data={
                "+X": {
                    "adrs": {"S_a": [0.0, 1.0], "S_d": [0.0, 0.1]},
                    "pp": {"S_dp": 0.1, "S_ap": 1.0, "S_dy": 0.05, "S_ay": 0.9},
                    "spectrum": {"T": [0.0, 1.0], "Sa": [1.0, 1.0]},
                },
            },
        ),
    )


def _views() -> list:
    return [
        View(key="meshed", name="Meshed", kind=GEOMETRY, source="Preprocessor"),
        View(key="results:DEAD", name="DEAD", kind=RESULTS, source="Recipe: Static · DEAD"),
        View(key="table:modal", name="Modal", kind=TABLE, source="Recipe: Modal"),
        View(
            key="figure:pushover-plusx-uniform",
            name="Pushover — +X · uniform",
            kind=FIGURE,
            source="Recipe: Pushover",
        ),
    ]


# ── figure_identity ──────────────────────────────────────────────────


def test_figure_identity_maps_directions():
    assert figure_identity("Pushover", "+X") == ("figure:pushover-plusx", "Pushover — +X")
    assert figure_identity("Pushover", "-Y") == ("figure:pushover-minusy", "Pushover — -Y")
    assert figure_identity("Pushover", "+X", "uniform") == (
        "figure:pushover-plusx-uniform",
        "Pushover — +X · uniform",
    )


# ── collect_project_results ──────────────────────────────────────────


def test_collect_project_results_maps_each_kind():
    static_arrays = {"node_tag": np.array([1, 2]), "schema_version": np.array([3])}
    results = [
        StepResult(kind=CASES, label="Static cases", payload=static_arrays),
        _modal_step(),
        _rs_step("X"),
        _rs_step("Y"),
        _pushover_step(),
    ]
    collected = collect_project_results(results)

    assert collected["static_arrays"] == static_arrays
    assert collected["modal"]["periods"] == [1.0, 0.5]
    assert collected["mode_shapes"] == {}
    assert set(collected["rs"]) == {"rs_x", "rs_y"}
    assert collected["rs"]["rs_x"]["base_shear_cqc"] == 120.0
    assert ("+X", "uniform") in collected["pushover"]
    assert collected["pushover"][("+X", "uniform")]["adrs"]["S_a"] == [0.0, 1.0]


def test_collect_project_results_captures_rs_nodal_displacements():
    field = {1: (0.1, 0.2, 0.3), 2: (0.4, 0.5, 0.6)}
    collected = collect_project_results([_rs_step("X", field)])
    assert collected["rs_nodal_displacements"] == {"X": field}
    # A direction that recorded no field leaves the slot empty.
    assert collect_project_results([_rs_step("Y")])["rs_nodal_displacements"] == {}


def test_rs_displacement_arrays_build_static_shaped_cases():
    """Option C: RS fields become ``static/RS-<dir>/node_*`` cases over the geometry."""
    from examples.sample_model import make_sample_model

    mesh = make_sample_model()
    node_tags = [node.node_tag for node in mesh.nodes.values()]
    field = dict.fromkeys(node_tags, (0.001, 0.0, 0.0))

    arrays = rs_displacement_arrays(mesh, {"X": field, "Y": field})

    assert list(arrays["static_case_labels"]) == ["RS-X", "RS-Y"]
    for direction in ("X", "Y"):
        for dof in ("dx", "dy", "dz"):
            key = f"static/RS-{direction}/node_{dof}"
            assert key in arrays
            assert len(arrays[key]) == len(node_tags)
    # The X field's dx component is the seeded magnitude.
    assert arrays["static/RS-X/node_dx"][0] == 0.001


def test_rs_directions_round_trip_through_the_bundle(tmp_path):
    """Option C: RS displacement cases survive a write/read project round-trip."""
    from examples.sample_model import make_sample_model

    mesh = make_sample_model()
    node_tags = [node.node_tag for node in mesh.nodes.values()]
    field = dict.fromkeys(node_tags, (0.001, 0.0, 0.0))
    arrays = rs_displacement_arrays(mesh, {"X": field, "Y": field})

    manifest = build_manifest(
        name="RS",
        source_label="rs.s2k",
        model_file="model.npz",
        units={"F": "N"},
        recipe=None,
        views=[],
        active_view=None,
        camera=None,
        hidden_elem_ids=[],
        display={},
    )
    write_project(
        tmp_path / "RS.fea",
        manifest=manifest,
        sap=None,
        mesh=None,
        results_arrays=arrays,
        figures=figures_payload({}),
        tables=tables_payload({}),
        model_fmt="npz",
    )

    bundle = read_project(tmp_path / "RS.fea")
    assert "RS-X" in bundle["results"].cases()
    assert "RS-Y" in bundle["results"].cases()
    assert bundle["results"].has_displacements("RS-X")
    assert bundle["results"].has_displacements("RS-Y")


def test_mode_shape_arrays_build_static_shaped_cases():
    """Modal shapes become ``static/Mode-<i>/node_*`` cases over the geometry."""
    from examples.sample_model import make_sample_model

    mesh = make_sample_model()
    node_tags = [node.node_tag for node in mesh.nodes.values()]
    shapes = {
        0: dict.fromkeys(node_tags, (0.001, 0.0, 0.0)),
        1: dict.fromkeys(node_tags, (0.0, 0.002, 0.0)),
    }

    arrays = mode_shape_arrays(mesh, shapes)

    assert list(arrays["static_case_labels"]) == ["Mode-1", "Mode-2"]
    assert len(arrays["static/Mode-1/node_dx"]) == len(node_tags)
    assert arrays["static/Mode-1/node_dx"][0] == 0.001
    assert arrays["static/Mode-2/node_dy"][0] == 0.002


def test_mode_shapes_round_trip_through_the_bundle(tmp_path):
    """Mode shapes persist as results cases that survive a write/read round-trip."""
    from examples.sample_model import make_sample_model

    mesh = make_sample_model()
    node_tags = [node.node_tag for node in mesh.nodes.values()]
    shapes = {0: dict.fromkeys(node_tags, (0.001, 0.0, 0.0))}
    arrays = mode_shape_arrays(mesh, shapes)

    manifest = build_manifest(
        name="Modal",
        source_label="modal.s2k",
        model_file="model.npz",
        units={"F": "N"},
        recipe=None,
        views=[],
        active_view=None,
        camera=None,
        hidden_elem_ids=[],
        display={},
    )
    write_project(
        tmp_path / "Modal.fea",
        manifest=manifest,
        sap=None,
        mesh=None,
        results_arrays=arrays,
        figures=figures_payload({}),
        tables=tables_payload({}),
        model_fmt="npz",
    )

    bundle = read_project(tmp_path / "Modal.fea")
    assert "Mode-1" in bundle["results"].cases()
    assert bundle["results"].has_displacements("Mode-1")


def test_pushover_displacement_arrays_build_static_shaped_cases():
    """Pushover directions become ``static/Pushover<direction>/node_*`` cases."""
    from examples.sample_model import make_sample_model

    mesh = make_sample_model()
    node_tags = [node.node_tag for node in mesh.nodes.values()]
    field = dict.fromkeys(node_tags, (0.003, 0.0, 0.0))

    arrays = pushover_displacement_arrays(
        mesh, {("+X", "uniform"): field, ("-X", "uniform"): field}
    )

    assert list(arrays["static_case_labels"]) == ["Pushover+X-uniform", "Pushover-X-uniform"]
    assert len(arrays["static/Pushover+X-uniform/node_dx"]) == len(node_tags)
    assert arrays["static/Pushover+X-uniform/node_dx"][0] == 0.003


def test_pushover_step_results_round_trip_through_the_bundle(tmp_path):
    """Per-step pushover history and per-direction cases survive a round-trip."""
    from examples.sample_model import make_sample_model
    from fea_toolkit.io.npz_writer import collect_pushover_arrays

    mesh = make_sample_model()
    node_tags = [node.node_tag for node in mesh.nodes.values()]
    final_field = dict.fromkeys(node_tags, (0.005, 0.0, 0.0))
    step_results = [
        {
            "step": 1,
            "frame_forces": {},
            "shell_forces": {},
            "node_displacements": dict.fromkeys(node_tags, (0.002, 0.0, 0.0)),
        },
        {
            "step": 2,
            "frame_forces": {},
            "shell_forces": {},
            "node_displacements": final_field,
        },
    ]
    po_results = {
        "step": [0, 1, 2],
        "control_disp": [0.0, 0.1, 0.2],
        "base_shear": [0.0, 5.0, 10.0],
    }

    arrays = dict(pushover_displacement_arrays(mesh, {("+X", "uniform"): final_field}))
    arrays.update(
        collect_pushover_arrays(mesh, step_results, direction="+X", pushover_results=po_results)
    )

    manifest = build_manifest(
        name="Pushover",
        source_label="po.s2k",
        model_file="model.npz",
        units={"F": "N"},
        recipe=None,
        views=[],
        active_view=None,
        camera=None,
        hidden_elem_ids=[],
        display={},
    )
    write_project(
        tmp_path / "Pushover.fea",
        manifest=manifest,
        sap=None,
        mesh=None,
        results_arrays=arrays,
        figures=figures_payload({}),
        tables=tables_payload({}),
        model_fmt="npz",
    )

    bundle = read_project(tmp_path / "Pushover.fea")
    assert "Pushover+X-uniform" in bundle["results"].cases()
    assert bundle["results"].has_displacements("Pushover+X-uniform")
    raw = bundle["results"].raw()
    assert "pushover/+X/uniform/node_disp_x" in raw
    assert raw["pushover/+X/uniform/node_disp_x"].shape[0] == 2


def test_collect_project_results_empty_run():
    assert collect_project_results([]) == {
        "static_arrays": {},
        "modal": None,
        "mode_shapes": None,
        "rs": {},
        "rs_nodal_displacements": {},
        "pushover": {},
        "pushover_results": {},
        "pushover_step_results": {},
    }


# ── figures / tables payloads ────────────────────────────────────────


def test_figures_payload_is_json_native():
    pushover = {
        ("+X", "uniform"): {
            "adrs": {"S_a": [0.0], "S_d": [0.0]},
            "pp": {"S_dp": 1.0},
            "spectrum": {"T": [0.0], "Sa": [1.0]},
        }
    }
    entry = figures_payload(pushover)["figure:pushover-plusx-uniform"]
    assert entry["kind"] == "capacity_spectrum"
    assert entry["adrs"] == {"S_a": [0.0], "S_d": [0.0]}
    json.dumps(entry)  # no numpy scalars may leak through


def test_tables_payload_round_trips():
    table = Table(title="Modal", columns=("Mode", "Period"), rows=(("1", "1.0"), ("2", "0.5")))
    assert tables_payload({"table:modal": table})["table:modal"] == {
        "title": "Modal",
        "columns": ["Mode", "Period"],
        "rows": [["1", "1.0"], ["2", "0.5"]],
    }


# ── views_payload / build_manifest ───────────────────────────────────


def test_views_payload_records_pointers():
    payload = views_payload(_views())
    by_key = {entry["key"]: entry for entry in payload}
    assert by_key["results:DEAD"]["case"] == "DEAD"
    assert by_key["table:modal"]["table_key"] == "table:modal"
    assert by_key["figure:pushover-plusx-uniform"]["figure_key"] == "figure:pushover-plusx-uniform"
    assert [entry["order"] for entry in payload] == [0, 1, 2, 3]


def test_build_manifest_shape():
    manifest = build_manifest(
        name="Admin",
        source_label="Admin.s2k",
        model_file="model.h5",
        units={"F": "N", "L": "m", "T": "C"},
        recipe=None,
        views=_views(),
        active_view="results:DEAD",
        camera=None,
        hidden_elem_ids=["A-1"],
        display={"shell_opacity": 0.6},
    )
    assert manifest["schema_version"] == 1
    assert manifest["project"]["model_file"] == "model.h5"
    assert manifest["active_view"] == "results:DEAD"
    assert manifest["viewport"]["hidden_elem_ids"] == ["A-1"]
    assert len(manifest["views"]) == 4


# ── write / read round-trip ──────────────────────────────────────────


def _write_small_project(tmp_path) -> dict:
    collected = collect_project_results([_modal_step(), _rs_step("X"), _pushover_step()])
    collected["static_arrays"] = {"node_tag": np.array([1, 2]), "schema_version": np.array([3])}

    from fea_toolkit.io.unified_writer import collect_modal_arrays, collect_rs_arrays

    arrays = dict(collected["static_arrays"])
    arrays.update(collect_modal_arrays(collected["modal"], collected["mode_shapes"]))
    arrays.update(collect_rs_arrays(collected["rs"].get("rs_x"), collected["rs"].get("rs_y")))

    figures = figures_payload(collected["pushover"])
    tables = tables_payload(
        {"table:modal": Table(title="Modal", columns=("Mode",), rows=(("1",),))}
    )
    manifest = build_manifest(
        name="Admin",
        source_label="Admin.s2k",
        model_file="model.npz",
        units={"F": "N"},
        recipe=None,
        views=_views(),
        active_view="figure:pushover-plusx-uniform",
        camera=[[1.0, 2.0, 3.0], [0.0, 0.0, 0.0], [0.0, 0.0, 1.0]],
        hidden_elem_ids=["A-1"],
        display={"shell_opacity": 0.6},
    )
    notes = write_project(
        tmp_path / "Admin.fea",
        manifest=manifest,
        sap=None,
        mesh=None,
        results_arrays=arrays,
        figures=figures,
        tables=tables,
        model_fmt="npz",
    )
    return manifest, notes


def test_write_read_roundtrip(tmp_path):
    _manifest, notes = _write_small_project(tmp_path)
    assert notes == []

    bundle = read_project(tmp_path / "Admin.fea")
    assert bundle["manifest"]["active_view"] == "figure:pushover-plusx-uniform"
    assert bundle["sap"] is None and bundle["mesh"] is None  # no model stage written
    assert bundle["results"] is not None
    assert "DEAD" not in bundle["results"].cases()  # only synthetic arrays, no cases

    assert bundle["tables"]["table:modal"].title == "Modal"
    assert "figure:pushover-plusx-uniform" in bundle["figures"]
    assert bundle["figures"]["figure:pushover-plusx-uniform"]["adrs"]["S_a"] == [0.0, 1.0]


def test_zip_project_roundtrip(tmp_path):
    _write_small_project(tmp_path)
    directory = tmp_path / "Admin.fea"
    archive = zip_project(directory)
    assert archive.endswith(".zip")

    bundle = read_project(archive)
    assert bundle["manifest"]["active_view"] == "figure:pushover-plusx-uniform"


def test_is_project(tmp_path):
    _write_small_project(tmp_path)
    directory = tmp_path / "Admin.fea"
    assert is_project(directory)
    assert is_project(zip_project(directory))
    assert not is_project(tmp_path)


def test_detect_model_format_is_h5_or_npz():
    assert detect_model_format() in ("h5", "npz")
