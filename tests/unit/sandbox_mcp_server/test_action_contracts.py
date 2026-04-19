import pytest

from sandbox.interface import SandboxResult
from sandbox_mcp_server.contracts import (
    ActionHistoryEntry,
    BoundingBox3D,
    CADActionInput,
    CADActionOutput,
    CADActionType,
    TopologyFaceEntity,
    TopologyObjectIndex,
)
from sandbox_mcp_server.registry import normalize_action_params
from sandbox_mcp_server.service import SandboxMCPService


class _DummyRunner:
    async def execute(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("not used in this test")


class _StaticSuccessRunner:
    async def execute(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return SandboxResult(
            success=True,
            stdout="",
            stderr="",
            output_files=[],
            output_file_contents={},
            error_message=None,
        )


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


@pytest.mark.asyncio
async def test_apply_cad_action_rejects_no_effect_cut_extrude(monkeypatch: pytest.MonkeyPatch) -> None:
    service = SandboxMCPService(runner=_StaticSuccessRunner())
    session_id = "no-effect-cut-extrude"
    service._session_manager.clear_session(session_id)

    base_snapshot = service._empty_snapshot()
    base_snapshot.geometry.solids = 1
    base_snapshot.geometry.faces = 48
    base_snapshot.geometry.edges = 120
    base_snapshot.geometry.volume = 22083.234552601418
    base_snapshot.geometry.bbox = [64.0, 48.3, 24.0]
    base_snapshot.geometry.center_of_mass = [0.0, 0.4, -2.4]
    base_snapshot.geometry.surface_area = 20550.881159458335
    base_snapshot.geometry.bbox_min = [-32.0, -24.0, -12.0]
    base_snapshot.geometry.bbox_max = [32.0, 24.3, 12.0]
    base_snapshot.topology_index = TopologyObjectIndex(
        faces=[
            TopologyFaceEntity(
                face_ref="face:1:F_front",
                face_id="F_front",
                step=1,
                area=416.0,
                center=[0.0, 24.0, 8.0],
                normal=[0.0, 1.0, 0.0],
                axis_origin=None,
                axis_direction=None,
                radius=None,
                geom_type="PLANE",
                bbox=_bbox().model_copy(
                    update={
                        "ymin": 24.0,
                        "ymax": 24.0,
                        "zmin": -12.0,
                        "zmax": 12.0,
                    }
                ),
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

    history = [
        ActionHistoryEntry(
            step=1,
            action_type=CADActionType.CREATE_SKETCH,
            action_params={"face_ref": "face:1:F_front", "plane": "YZ"},
            result_snapshot=base_snapshot.model_copy(deep=True),
            success=True,
        ),
        ActionHistoryEntry(
            step=2,
            action_type=CADActionType.ADD_CIRCLE,
            action_params={"radius": 3.5, "center": [0, 24], "name": "thumb_notch_profile"},
            result_snapshot=base_snapshot.model_copy(deep=True),
            success=True,
        ),
    ]
    for entry in history:
        service._session_manager.append_action(session_id, entry)

    monkeypatch.setattr(
        service,
        "_parse_snapshot",
        lambda _result: base_snapshot.model_copy(deep=True),
    )

    result: CADActionOutput = await service.apply_cad_action(
        CADActionInput(
            session_id=session_id,
            action_type=CADActionType.CUT_EXTRUDE,
            action_params={"depth": 12, "direction": "through"},
        )
    )

    assert result.success is False
    assert result.error_code.value == "execution_error"
    assert result.error_message is not None
    assert "no geometry change" in result.error_message
    assert "cut_extrude" in result.error_message
    assert len(result.action_history) == 2
    assert any("query_sketch" in suggestion for suggestion in result.suggestions)
