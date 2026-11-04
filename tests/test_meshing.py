"""Reading an element's meshing settings, for the Inspector.

Qt-free and renderer-free.  These rows are what the Inspector appends to a
selected entity's own fields, so the lookup lives in one module
(:mod:`fea_toolkit.model.meshing`).
"""

from fea_toolkit.model.meshing import meshing_rows
from fea_toolkit.model.sap_data import AreaElement, FrameElement, Node, SAPModelData


def _model(
    *,
    areas=None,
    area_roles=None,
    floor_mesh_options=None,
    frames=None,
    frame_auto_mesh=None,
    frame_assignments=None,
    line_spring_assignments=None,
):
    areas = areas or {}
    frames = frames or {}
    return SAPModelData(
        nodes={},
        restraints={},
        materials={},
        sections={},
        frame_elements=frames,
        frame_assignments=frame_assignments if frame_assignments is not None else dict.fromkeys(frames, ""),
        area_elements=areas,
        area_assignments=dict.fromkeys(areas, ""),
        groups={},
        frame_auto_mesh=frame_auto_mesh or {},
        area_roles=area_roles or {},
        floor_mesh_options=floor_mesh_options or {},
        line_spring_assignments=line_spring_assignments or {},
    )


def test_floor_area_reports_role_mesh_option_and_size():
    md = _model(
        areas={"A1": AreaElement("A1", 1, ["1", "2", "3", "4"])},
        area_roles={"A1": "floor"},
        floor_mesh_options={
            "mesh_option": "General",
            "max_size": 0.25,
            "per_area": {"A1": {"mesh_option": "Default", "add_restraints": True}},
        },
    )
    assert meshing_rows(md, md.area_elements["A1"]) == [
        ("Role", "floor"),
        ("Mesh Option", "Default"),
        ("Add Restraints", "Yes"),
        ("Mesh Size", "0.25"),
    ]


def test_wall_area_uses_the_wall_mesh_table_and_size():
    md = _model(
        areas={"49": AreaElement("49", 1, ["392", "152", "3", "62"])},
        area_roles={"49": "wall"},
        floor_mesh_options={
            "wall_max_size": 0.25,
            "wall_per_area": {"49": {"mesh_option": "Auto Rectangular", "add_restraints": True}},
        },
    )
    assert meshing_rows(md, md.area_elements["49"]) == [
        ("Role", "wall"),
        ("Mesh Option", "Auto Rectangular"),
        ("Add Restraints", "Yes"),
        ("Mesh Size", "0.25"),
    ]


def test_opening_and_inactive_areas_flag_themselves():
    md = _model(
        areas={"A1": AreaElement("A1", 1, ["1", "2", "3"])},
        area_roles={"A1": "floor"},
        floor_mesh_options={"max_size": 0.5, "per_area": {"A1": {"mesh_option": "General"}}},
    )
    md.area_elements["A1"].opening = True
    md.area_elements["A1"].inactive = True
    rows = meshing_rows(md, md.area_elements["A1"])
    assert ("Opening", "Yes") in rows
    assert ("Meshed Away", "Yes") in rows


def test_null_area_flags_itself_as_geometric():
    md = _model(
        areas={"A1": AreaElement("A1", 1, ["1", "2", "3"])},
        area_roles={"A1": "floor"},
    )
    md.area_elements["A1"].null = True
    assert ("Null (Geometric)", "Yes") in meshing_rows(md, md.area_elements["A1"])


def test_area_reports_auto_edge_constraint():
    md = _model(
        areas={"A1": AreaElement("A1", 1, ["1", "2", "3", "4"])},
        area_roles={"A1": "floor"},
        floor_mesh_options={"auto_edge_constraints": {"A1": True}},
    )
    assert ("Auto Edge Constraint", "Yes") in meshing_rows(md, md.area_elements["A1"])


def test_node_reports_include_in_mesh():
    md = _model(floor_mesh_options={"include_in_mesh": {"1": True}})
    assert meshing_rows(md, Node("1", 1, 0.0, 0.0, 0.0)) == [("Include In Mesh", "Yes")]
    assert meshing_rows(md, Node("9", 9, 0.0, 0.0, 0.0)) == []


def test_frame_reports_its_auto_mesh_settings():
    md = _model(
        frames={"F1": FrameElement("F1", 1, "1", "2")},
        frame_assignments={"F1": "SEC"},
        frame_auto_mesh={
            "F1": {
                "Auto Mesh": "Yes",
                "At Intermediate Joints": "Yes",
                "At Intersections": "Yes",
                "Min Number?": "No",
                "Max Length?": "No",
            }
        },
    )
    assert meshing_rows(md, md.frame_elements["F1"]) == [
        ("Auto Mesh", "Yes"),
        ("Auto Mesh At Joints", "Yes"),
        ("Auto Mesh At Intersections", "Yes"),
    ]


def test_an_element_without_meshing_reports_nothing():
    md = _model(areas={"A1": AreaElement("A1", 1, ["1", "2", "3"])})
    assert meshing_rows(md, md.area_elements["A1"]) == []
    assert meshing_rows(None, md.area_elements["A1"]) == []
    assert meshing_rows(md, None) == []


# ── Non-structural frame role ─────────────────────────────────────


def test_assignment_free_frame_is_a_mesh_guide():
    md = _model(frames={"F1": FrameElement("F1", 1, "1", "2")})
    assert meshing_rows(md, md.frame_elements["F1"]) == [("Role", "mesh guide")]


def test_frame_with_a_section_has_no_non_structural_role():
    md = _model(
        frames={"F1": FrameElement("F1", 1, "1", "2")},
        frame_assignments={"F1": "SEC"},
    )
    assert meshing_rows(md, md.frame_elements["F1"]) == []


def test_frame_assigned_the_none_section_is_non_structural():
    md = _model(
        frames={"F1": FrameElement("F1", 1, "1", "2")},
        frame_assignments={"F1": "NONE"},
    )
    assert ("Role", "mesh guide") in meshing_rows(md, md.frame_elements["F1"])


def test_line_spring_frame_reports_the_line_spring_role():
    md = _model(
        frames={"F1": FrameElement("F1", 1, "1", "2")},
        line_spring_assignments={"F1": "LS1"},
    )
    assert ("Role", "line spring") in meshing_rows(md, md.frame_elements["F1"])


def test_null_area_boundary_frame_reports_the_patch_boundary_role():
    md = _model(
        areas={"P": AreaElement("P", 1, ["1", "2", "3", "4"])},
        frames={"F1": FrameElement("F1", 1, "1", "2")},
    )
    md.area_elements["P"].null = True
    assert ("Role", "patch boundary") in meshing_rows(md, md.frame_elements["F1"])


def test_a_diagonal_of_a_null_area_is_not_a_patch_boundary():
    md = _model(
        areas={"P": AreaElement("P", 1, ["1", "2", "3", "4"])},
        frames={"F1": FrameElement("F1", 1, "1", "3")},
    )
    md.area_elements["P"].null = True
    assert ("Role", "mesh guide") in meshing_rows(md, md.frame_elements["F1"])
