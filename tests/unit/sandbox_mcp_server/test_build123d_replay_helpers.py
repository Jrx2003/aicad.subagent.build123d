from __future__ import annotations

import importlib.util
from pathlib import Path


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
