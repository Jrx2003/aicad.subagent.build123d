from sandbox_mcp_server.contracts import (
    ActionHistoryEntry,
    BoundingBox3D,
    CADActionType,
    TopologyFaceEntity,
    TopologyObjectIndex,
)
from sandbox_mcp_server.registry import normalize_action_params
from sandbox_mcp_server.service import SandboxMCPService


class _DummyRunner:
    async def execute(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("not used in this test")


def _bbox() -> BoundingBox3D:
    return BoundingBox3D(
        xlen=20.0,
        ylen=20.0,
        zlen=0.0,
        xmin=-10.0,
        xmax=10.0,
        ymin=-10.0,
        ymax=10.0,
        zmin=0.0,
        zmax=0.0,
    )


def _history_with_face(*, face_ref: str, geom_type: str) -> list[ActionHistoryEntry]:
    service = SandboxMCPService(runner=_DummyRunner())
    snapshot = service._empty_snapshot()
    snapshot.topology_index = TopologyObjectIndex(
        faces=[
            TopologyFaceEntity(
                face_ref=face_ref,
                face_id=face_ref.split(":")[-1],
                step=1,
                area=100.0,
                center=[0.0, 0.0, 0.0],
                normal=[0.0, 0.0, 1.0],
                axis_origin=None,
                axis_direction=None,
                radius=None,
                geom_type=geom_type,
                bbox=_bbox(),
                parent_solid_id="S1",
                edge_refs=[],
                adjacent_face_refs=[],
            )
        ],
        edges=[],
        faces_total=1,
        edges_total=0,
        max_items_per_type=20,
    )
    return [
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.SNAPSHOT,
            action_params={},
            result_snapshot=snapshot,
            success=True,
        )
    ]


def test_hole_contract_requires_face_frame_or_face_attached_sketch() -> None:
    service = SandboxMCPService(runner=_DummyRunner())

    resolved, error, suggestions = service._validate_action_contract(
        action_type=CADActionType.HOLE,
        action_params={
            "diameter": 3.5,
            "depth": 10.0,
            "position": [-20.0, -12.0, -7.0],
        },
        action_history=[],
    )

    assert resolved["diameter"] == 3.5
    assert error is not None
    assert "face-attached local frame" in error
    assert "query_topology" in suggestions[0]
    assert "create_sketch(face_ref" in suggestions[1]


def test_hole_contract_allows_face_ref_without_prior_sketch() -> None:
    service = SandboxMCPService(runner=_DummyRunner())

    resolved, error, suggestions = service._validate_action_contract(
        action_type=CADActionType.HOLE,
        action_params={
            "diameter": 3.5,
            "depth": 10.0,
            "face_ref": "face:1:F_top",
            "position": [-20.0, -12.0],
        },
        action_history=[],
    )

    assert resolved["face_ref"] == "face:1:F_top"
    assert error is None
    assert suggestions == []


def test_hole_contract_allows_face_attached_sketch_context() -> None:
    service = SandboxMCPService(runner=_DummyRunner())
    history = [
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.CREATE_SKETCH,
            action_params={"face_ref": "face:1:F_top", "position": [0.0, 0.0]},
            result_snapshot=service._empty_snapshot(),
            success=True,
        )
    ]

    resolved, error, suggestions = service._validate_action_contract(
        action_type=CADActionType.HOLE,
        action_params={
            "diameter": 3.5,
            "depth": 10.0,
            "position": [-20.0, -12.0],
        },
        action_history=history,
    )

    assert resolved["position"] == [-20.0, -12.0]
    assert error is None
    assert suggestions == []


def test_create_sketch_reference_rejects_non_planar_face_ref() -> None:
    service = SandboxMCPService(runner=_DummyRunner())

    error = service._validate_action_references(
        action_type=CADActionType.CREATE_SKETCH,
        action_params={"face_ref": "face:1:F_curved"},
        action_history=_history_with_face(face_ref="face:1:F_curved", geom_type="CYLINDER"),
    )

    assert error is not None
    assert "not planar" in error
    assert "face:1:F_curved" in error


def test_create_sketch_reference_allows_planar_face_ref() -> None:
    service = SandboxMCPService(runner=_DummyRunner())

    error = service._validate_action_references(
        action_type=CADActionType.CREATE_SKETCH,
        action_params={"face_ref": "face:1:F_planar"},
        action_history=_history_with_face(face_ref="face:1:F_planar", geom_type="PLANE"),
    )

    assert error is None


def test_create_sketch_reference_rejects_candidate_set_label_as_face_ref() -> None:
    service = SandboxMCPService(runner=_DummyRunner())

    error = service._validate_action_references(
        action_type=CADActionType.CREATE_SKETCH,
        action_params={"face_ref": "mating_faces"},
        action_history=_history_with_face(face_ref="face:1:F_planar", geom_type="PLANE"),
    )

    assert error is not None
    assert "mating_faces" in error
    assert "candidate-set label" in error
    assert "face:<step>:<entity_id>" in error


def test_create_sketch_bootstraps_rectangle_profile_from_dimensions() -> None:
    service = SandboxMCPService(runner=_DummyRunner())

    code = service._action_to_code(
        CADActionType.CREATE_SKETCH,
        {
            "face_ref": "face:1:F_planar",
            "width": 18.0,
            "height": 10.0,
            "inner_width": 12.0,
            "inner_height": 6.0,
            "position": [2.0, -3.0],
        },
    )

    assert "_aicad_create_sketch_from_face" in code
    assert "_aicad_add_rectangle_to_sketch" in code
    assert "18.0, 10.0, (2.0, -3.0)" in code
    assert "inner_size=[12.0, 6.0]" in code


def test_create_sketch_bootstraps_circle_profile_from_diameter() -> None:
    service = SandboxMCPService(runner=_DummyRunner())

    code = service._action_to_code(
        CADActionType.CREATE_SKETCH,
        {
            "plane": "XY",
            "diameter": 8.0,
            "position": [1.5, 2.5],
        },
    )

    assert "_aicad_create_sketch(" in code
    assert "_aicad_add_circles_to_sketch" in code
    assert "_aicad_circle_points_raw = [[1.5, 2.5]]" in code
    assert "4.0, _aicad_circle_points" in code


def test_hole_codegen_normalizes_countersink_radius_to_diameter() -> None:
    service = SandboxMCPService(runner=_DummyRunner())

    code = service._action_to_code(
        CADActionType.HOLE,
        {
            "diameter": 5.0,
            "depth": 10.0,
            "face_ref": "face:1:F_top",
            "centers": [[-20.0, -12.0], [20.0, -12.0]],
            "countersink_radius": 4.5,
            "countersink_angle": 90.0,
        },
    )

    assert "_aicad_countersink_diameter = 9.0" in code
    assert "countersink_diameter=_aicad_countersink_diameter" in code


def test_registry_normalizes_hole_countersink_radius_to_canonical_diameter() -> None:
    normalized = normalize_action_params(
        CADActionType.HOLE,
        {
            "diameter": 5.0,
            "countersink_radius": 4.5,
            "countersink_angle": 90.0,
        },
    )

    assert normalized["countersink_diameter"] == 9.0
