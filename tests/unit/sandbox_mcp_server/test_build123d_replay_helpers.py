from __future__ import annotations

import importlib.util
from pathlib import Path
import hashlib


def _load_replay_helpers_module():
    module_path = (
        Path(__file__).resolve().parents[3]
        / "src"
        / "sandbox_mcp_server"
        / "build123d_replay_helpers.py"
    )
    spec = importlib.util.spec_from_file_location("build123d_replay_helpers", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _service_style_entity_id(prefix: str, parts: list[float]) -> str:
    normalized = "|".join(f"{float(part):.6f}" for part in parts)
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def test_apply_extrude_uses_build123d_plane_direction_compatibly() -> None:
    module = _load_replay_helpers_module()
    with module.BuildSketch(module.Plane.XY) as builder:
        module.Rectangle(40.0, 20.0)

    sketch_state = module._aicad_make_sketch_state(module.Plane.XY, plane_name="XY")
    sketch_state["profile"] = builder.sketch

    result, feature = module._aicad_apply_extrude(
        module.Part(),
        sketch_state,
        distance=10.0,
    )
    bbox = module._aicad_bbox(result)

    assert feature is not None
    assert bbox["xlen"] == 40.0
    assert bbox["ylen"] == 20.0
    assert bbox["zlen"] == 10.0


def test_face_entity_id_matches_query_side_contract() -> None:
    module = _load_replay_helpers_module()
    with module.BuildPart() as builder:
        module.Box(62.0, 40.0, 14.0)
    part = builder.part

    top_face = next(
        face
        for face in module._aicad_faces(part)
        if (
            (module._aicad_face_normal(face) or [0.0, 0.0, 0.0])[2] > 0.999
        )
    )
    bbox = module._aicad_bbox(top_face)
    center = module._aicad_shape_center(top_face)
    normal = module._aicad_face_normal(top_face)
    expected = _service_style_entity_id(
        "F",
        [
            module._aicad_shape_area(top_face),
            center[0],
            center[1],
            center[2],
            normal[0] if normal else 0.0,
            normal[1] if normal else 0.0,
            normal[2] if normal else 0.0,
            bbox["xlen"],
            bbox["ylen"],
            bbox["zlen"],
        ],
    )

    assert module._aicad_face_entity_id(top_face) == expected
    assert module._aicad_find_face_by_id(part, expected) is not None


def test_entity_id_normalizes_signed_zero() -> None:
    module = _load_replay_helpers_module()

    assert module._aicad_entity_id("F", [-0.0, 0.0, 1.0]) == module._aicad_entity_id(
        "F",
        [0.0, 0.0, 1.0],
    )


def test_result_has_positive_solid_accepts_normal_part() -> None:
    module = _load_replay_helpers_module()

    with module.BuildPart() as builder:
        module.Box(10.0, 10.0, 10.0)

    assert module._aicad_result_has_positive_solid(builder.part) is True


def test_aicad_as_part_preserves_positive_solid_for_solid_result() -> None:
    module = _load_replay_helpers_module()

    with module.BuildPart() as builder:
        module.Box(10.0, 10.0, 10.0)

    solid = builder.part.solids()[0]

    assert module._aicad_result_has_positive_solid(solid) is True
    assert module._aicad_result_has_positive_solid(module._aicad_as_part(solid)) is True


def test_apply_holes_accepts_solid_like_current_result() -> None:
    module = _load_replay_helpers_module()

    with module.BuildPart() as builder:
        module.Box(20.0, 20.0, 10.0)

    solid = builder.part.solids()[0]
    sketch_state = module._aicad_make_sketch_state(module.Plane.XY, plane_name="XY")

    result = module._aicad_apply_holes(
        solid,
        sketch_state,
        diameter=4.0,
        depth=5.0,
        points_raw=[[0.0, 0.0]],
        face_hint="top_faces",
    )

    assert module._aicad_result_has_positive_solid(result) is True
