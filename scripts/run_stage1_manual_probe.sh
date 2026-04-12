#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI_AICAD_TEST_RUNS_ROOT="${AICAD_TEST_RUNS_ROOT-}"

if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  source "$ROOT_DIR/.env"
  set +a
fi

if [[ -n "$CLI_AICAD_TEST_RUNS_ROOT" ]]; then
  export AICAD_TEST_RUNS_ROOT="$CLI_AICAD_TEST_RUNS_ROOT"
fi

RUNS_ROOT="${AICAD_TEST_RUNS_ROOT:-$ROOT_DIR/test_runs}"
if [[ "$RUNS_ROOT" != /* ]]; then
  RUNS_ROOT="$ROOT_DIR/$RUNS_ROOT"
fi

RUN_ID="${1:-$(date +%Y%m%d_%H%M%S)}"
RUN_DIR="$RUNS_ROOT/$RUN_ID"
mkdir -p "$RUN_DIR"

echo "[stage1-manual] run_dir: $RUN_DIR"

AICAD_RUN_DIR="$RUN_DIR" uv run python - <<'PY'
import asyncio
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from common.config import settings
from sandbox.mcp_runner import McpSandboxRunner


def _json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__bytes__": len(value)}
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json_safe(payload), ensure_ascii=True, sort_keys=True, indent=2),
        encoding="utf-8",
    )


async def main() -> None:
    run_dir = Path(os.environ["AICAD_RUN_DIR"]).resolve()
    outputs_dir = run_dir / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)

    runner = McpSandboxRunner(
        command=settings.sandbox_mcp_server_command,
        args=settings.sandbox_mcp_server_args_list,
        cwd=settings.sandbox_mcp_server_cwd_effective,
        timeout_buffer_seconds=settings.sandbox_mcp_timeout_buffer_seconds,
    )

    session_id = "manual-stage1-session"
    actions = [
        {"action_type": "create_sketch", "action_params": {"plane": "XY"}},
        {"action_type": "add_rectangle", "action_params": {"width": 40, "height": 20}},
        {"action_type": "extrude", "action_params": {"distance": 10}},
        {
            "action_type": "fillet",
            "action_params": {"radius": 2, "edge_scope": "all_outer"},
        },
    ]

    sequence_results = await runner.apply_action_sequence(
        actions=actions,
        session_id=session_id,
        timeout=120,
        include_artifact_content=True,
        clear_session=True,
    )
    last_result = sequence_results[-1] if sequence_results else None

    snapshot_result = await runner.query_snapshot(
        session_id=session_id, include_history=False, timeout=60
    )
    geometry_result = await runner.query_geometry(
        session_id=session_id,
        include_solids=True,
        include_faces=True,
        include_edges=True,
        max_items_per_type=20,
        timeout=60,
    )
    topology_result = await runner.query_topology(
        session_id=session_id,
        include_faces=True,
        include_edges=True,
        max_items_per_type=20,
        timeout=60,
    )
    target_entity_ids: list[str] = []
    target_ref_ids: list[str] = []
    if geometry_result.object_index is not None:
        faces = geometry_result.object_index.get("faces", [])
        if isinstance(faces, list):
            for face in faces:
                if isinstance(face, dict):
                    face_id = face.get("face_id")
                    if isinstance(face_id, str) and face_id:
                        target_entity_ids = [face_id]
                        break
    if topology_result.topology_index is not None:
        faces = topology_result.topology_index.get("faces", [])
        if isinstance(faces, list):
            for face in faces:
                if isinstance(face, dict):
                    face_ref = face.get("face_ref")
                    if isinstance(face_ref, str) and face_ref:
                        target_ref_ids = [face_ref]
                        break

    filtered_geometry_result = await runner.query_geometry(
        session_id=session_id,
        include_solids=False,
        include_faces=True,
        include_edges=False,
        max_items_per_type=5,
        entity_ids=target_entity_ids,
        timeout=60,
    )
    focus_center: list[float] | None = None
    focus_span: float | None = None
    if (
        isinstance(filtered_geometry_result.object_index, dict)
        and isinstance(filtered_geometry_result.object_index.get("faces"), list)
        and filtered_geometry_result.object_index["faces"]
    ):
        first_face = filtered_geometry_result.object_index["faces"][0]
        if isinstance(first_face, dict):
            raw_center = first_face.get("center")
            raw_bbox = first_face.get("bbox")
            if (
                isinstance(raw_center, list)
                and len(raw_center) >= 3
                and all(isinstance(value, (int, float)) for value in raw_center[:3])
            ):
                focus_center = [
                    float(raw_center[0]),
                    float(raw_center[1]),
                    float(raw_center[2]),
                ]
            if isinstance(raw_bbox, dict):
                spans = [
                    raw_bbox.get("xlen"),
                    raw_bbox.get("ylen"),
                    raw_bbox.get("zlen"),
                ]
                numeric_spans = [
                    float(value)
                    for value in spans
                    if isinstance(value, (int, float)) and float(value) > 0
                ]
                if numeric_spans:
                    focus_span = max(2.0, max(numeric_spans) * 2.5)

    validate_result = await runner.validate_requirement(
        session_id=session_id,
        requirements={
            "dimensions": {"width": 40, "height": 20, "thickness": 10},
            "features": ["fillet"],
        },
        timeout=60,
    )
    render_result = await runner.render_view(
        session_id=session_id,
        azimuth_deg=120.0,
        elevation_deg=25.0,
        zoom=1.15,
        width_px=1024,
        height_px=768,
        style="wireframe",
        target_entity_ids=target_entity_ids,
        focus_center=focus_center,
        focus_span=focus_span,
        focus_padding_ratio=0.2,
        include_artifact_content=True,
        timeout=120,
    )

    _write_json(
        run_dir / "run_manifest.json",
        {
            "run_dir": str(run_dir),
            "session_id": session_id,
            "actions": actions,
            "provider": settings.llm_reasoning_provider,
            "model": settings.llm_reasoning_model,
        },
    )
    _write_json(
        run_dir / "results" / "action_sequence.json",
        [asdict(item) for item in sequence_results],
    )
    _write_json(run_dir / "results" / "query_snapshot.json", asdict(snapshot_result))
    _write_json(run_dir / "results" / "query_geometry.json", asdict(geometry_result))
    _write_json(run_dir / "results" / "query_topology.json", asdict(topology_result))
    _write_json(
        run_dir / "results" / "query_geometry_filtered.json",
        asdict(filtered_geometry_result),
    )
    _write_json(
        run_dir / "results" / "validate_requirement.json", asdict(validate_result)
    )
    _write_json(run_dir / "results" / "render_view.json", asdict(render_result))

    if last_result is not None:
        for filename, content in last_result.output_file_contents.items():
            (outputs_dir / filename).write_bytes(content)

    for filename, content in render_result.output_file_contents.items():
        (outputs_dir / filename).write_bytes(content)

    summary = {
        "action_success": bool(last_result and last_result.success),
        "snapshot_success": snapshot_result.success,
        "geometry_success": geometry_result.success,
        "validate_success": validate_result.success,
        "render_success": render_result.success,
        "step_file": next(
            (
                f
                for f in (last_result.output_files if last_result else [])
                if f.endswith(".step")
            ),
            None,
        ),
        "render_file": render_result.view_file,
        "render_focus_ids": render_result.focused_entity_ids,
        "topology_ref_ids": target_ref_ids,
        "render_focus_bbox": render_result.focus_bbox,
        "filtered_query_success": filtered_geometry_result.success,
        "filtered_query_ids": filtered_geometry_result.matched_entity_ids,
        "geometry_counts": geometry_result.geometry,
    }
    _write_json(run_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=True, sort_keys=True))


asyncio.run(main())
PY

ln -sfn "$RUN_DIR" "$RUNS_ROOT/latest"
echo "[stage1-manual] latest: $RUNS_ROOT/latest"
