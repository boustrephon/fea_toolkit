"""Tests for the dataclass ⟷ JSON codec (``fea_toolkit.io.model_codec``)."""

import dataclasses

from examples.sample_model import make_rc_frame_model, make_sample_model
from fea_toolkit.io.model_codec import (
    BARE_LIST_TYPES,
    TUPLE_FIELDS,
    check_round_trip_types,
    dict_to_model,
    json_to_model,
    model_to_dict,
    model_to_json,
)
from fea_toolkit.model.mesh_model import MeshModel, WallElement
from fea_toolkit.model.sap_data import (
    AreaElement,
    FrameDistributedLoad,
    FrameElement,
    Group,
    ISection,
    Material,
    NDMaterial,
    Node,
    Restraint,
    SAPModelData,
)

# ═══════════════════════════════════════════════════════════════════
# Guard: every model field must be codec-safe
# ═══════════════════════════════════════════════════════════════════


class TestRoundTripGuard:
    def test_all_model_fields_are_codec_safe(self):
        """New fields on SAPModelData / MeshModel must be serialisable."""
        assert check_round_trip_types() == []


# ═══════════════════════════════════════════════════════════════════
# Basic round-trips
# ═══════════════════════════════════════════════════════════════════


class TestSAPRoundTrip:
    def test_sample_model_round_trip(self):
        md = make_sample_model()
        assert dict_to_model(model_to_dict(md)) == md

    def test_rc_frame_round_trip(self):
        md = make_rc_frame_model()
        assert json_to_model(model_to_json(md), cls=SAPModelData) == md

    def test_deterministic_json(self):
        md = make_sample_model()
        assert model_to_json(md) == model_to_json(md)


def _tricky_mesh() -> MeshModel:
    """A MeshModel exercising every non-trivial serialisation rule."""
    return MeshModel(
        nodes={"1": Node("1", 1, 0, 0, 0), "2": Node("2", 2, 5, 0, 0)},
        frame_elements={
            "F1": FrameElement(
                "F1", 1, "1", "2", child_ids=["F1-0"], t_locations=[0.4], cardinal_point=5
            )
        },
        area_elements={"A1": AreaElement("A1", 10, ["1", "2"], inactive=True, child_ids=["A1-0"])},
        frame_assignments={"F1": "UB300"},
        area_assignments={},
        frame_dist_loads=[],
        edge_loads_from_areas=[
            FrameDistributedLoad(
                "DEAD",
                "F1",
                "GRAV",
                "UNIFORM",
                "Lines",
                -25.0,
                -25.0,
                0.0,
                1.0,
                0.0,
                5.0,
                "GLOBAL",
            )
        ],
        wall_elements={
            "W1": WallElement(
                "W1", 1, ["1", "2", "3", "4"], 2, [0.2, 0.2], [2.0, 1.0], ["M1", "M2"]
            )
        },
        joint_loads=[],
        frame_gravity_loads=[],
        area_gravity_loads=[],
        area_uniform_loads=[],
        load_patterns={},
        mass_sources={},
        detected_edge_pairs=[("A1", "A2")],
        diaphragm_levels=[3.0],
        offset_rigid_links=[("l1", "1", "2", 99)],
        frame_element_types={"F1": "elasticBeamColumn"},
        area_element_types={},
        materials={"Steel": Material("Steel", "Steel", E_mod=2e11)},
        sections={
            "UB300": ISection(
                "UB300", "I/Wide Flange", "Steel", A=8e-3, depth=0.3, bf=0.15, tf=0.01, tw=0.006
            )
        },
        groups={"G1": Group("G1", "blue", ["Frame:F1"])},
        restraints={"1": Restraint([1, 1, 1, 1, 1, 1])},
        base_z=None,
        frame_tag_map={"F1": 1},
        material_tags={"Steel": 1},
        section_tags={"UB300": 1},
        shell_sec_tags={},
        shell_sec_variants={},
        units={"F": "kN", "L": "m", "T": "C"},
        model_name="tricky",
        edge_constraint_args=[],
        frame_element_properties={},
        area_element_properties={},
        nd_materials={"M1": NDMaterial(name="M1", material_type="PlaneStress")},
        layered_shell_sections={},
        diaphragm_components=[(3.0, ["n1", "n2"])],
        diaphragm_z_tolerance=0.01,
        loads_only_area_ids={"A9", "A10"},
        orphan_nodes={},
    )


class TestMeshRoundTrip:
    def test_tricky_mesh_round_trip(self):
        mesh = _tricky_mesh()
        mesh2 = dict_to_model(model_to_dict(mesh))
        assert mesh2 == mesh

    def test_tricky_mesh_json_round_trip(self):
        mesh = _tricky_mesh()
        mesh2 = json_to_model(model_to_json(mesh), cls=MeshModel)
        assert mesh2 == mesh

    def test_tuple_fields_are_retupled(self):
        mesh = _tricky_mesh()
        mesh2 = dict_to_model(model_to_dict(mesh))
        # JSON destroys tuples; the codec must restore them.
        assert all(isinstance(p, tuple) for p in mesh2.detected_edge_pairs)
        assert all(isinstance(x, tuple) for x in mesh2.offset_rigid_links)
        assert all(isinstance(d, tuple) for d in mesh2.diaphragm_components)
        # Nested list inside a diaphragm tuple survives.
        assert mesh2.diaphragm_components[0] == (3.0, ["n1", "n2"])

    def test_bare_list_typed_contents(self):
        mesh = _tricky_mesh()
        mesh2 = dict_to_model(model_to_dict(mesh))
        assert mesh2.edge_loads_from_areas == mesh.edge_loads_from_areas
        assert isinstance(mesh2.edge_loads_from_areas[0], FrameDistributedLoad)

    def test_sets_round_trip(self):
        mesh = _tricky_mesh()
        mesh2 = dict_to_model(model_to_dict(mesh))
        assert mesh2.loads_only_area_ids == {"A9", "A10"}
        assert isinstance(mesh2.loads_only_area_ids, set)

    def test_wall_element_round_trip(self):
        mesh = _tricky_mesh()
        mesh2 = dict_to_model(model_to_dict(mesh))
        assert mesh2.wall_elements == mesh.wall_elements

    def test_polymorphic_section_dispatch(self):
        """ISection must decode as ISection, not the base Section."""
        mesh = _tricky_mesh()
        mesh2 = dict_to_model(model_to_dict(mesh))
        assert isinstance(mesh2.sections["UB300"], ISection)

    def test_section_json_contains_type_discriminator(self):
        payload = model_to_dict(_tricky_mesh())
        sec = payload["sections"]["UB300"]
        assert sec["__type__"] == "ISection"

    def test_unknown_keys_ignored(self):
        """Forward-compatible: extra keys in the payload don't break reads."""
        data = model_to_dict(_tricky_mesh())
        data["future_field"] = {"anything": [1, 2, 3]}
        mesh2 = dict_to_model(data)
        assert mesh2 == _tricky_mesh()

    def test_rules_are_frozen(self):
        """If someone renames a field, the codec rules must be updated."""
        mesh_fields = {f.name for f in dataclasses.fields(MeshModel)}
        assert set(TUPLE_FIELDS) <= mesh_fields
        assert set(BARE_LIST_TYPES) <= mesh_fields
