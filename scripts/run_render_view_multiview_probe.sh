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

echo "[render-multiview] run_dir: $RUN_DIR"

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


def _slug(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in text)


async def main() -> None:
    run_dir = Path(os.environ["AICAD_RUN_DIR"]).resolve()
    outputs_dir = run_dir / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    runner = McpSandboxRunner(
        command=settings.sandbox_mcp_server_command,
        args=settings.sandbox_mcp_server_args_list,
        cwd=settings.sandbox_mcp_server_cwd_effective,
        timeout_buffer_seconds=settings.sandbox_mcp_timeout_buffer_seconds,
    )

    session_id = "render-multiview-probe"
    actions = [
        {"action_type": "create_sketch", "action_params": {"plane": "XY"}},
        {"action_type": "add_rectangle", "action_params": {"width": 60, "height": 36}},
        {"action_type": "extrude", "action_params": {"distance": 14}},
        {"action_type": "hole", "action_params": {"diameter": 8, "position": [0, 0]}},
        {"action_type": "fillet", "action_params": {"radius": 2, "edge_scope": "all_outer"}},
    ]

    sequence_results = await runner.apply_action_sequence(
        actions=actions,
        session_id=session_id,
        timeout=120,
        include_artifact_content=True,
        clear_session=True,
    )
    last_result = sequence_results[-1] if sequence_results else None

    if last_result is None or not last_result.success:
        raise RuntimeError("action sequence failed; cannot run multiview render probe")

    geometry_result = await runner.query_geometry(
        session_id=session_id,
        include_solids=True,
        include_faces=True,
        include_edges=True,
        max_items_per_type=80,
        timeout=60,
    )

    target_entity_ids: list[str] = []
    focus_center: list[float] | None = None
    focus_span: float | None = None
    if isinstance(geometry_result.object_index, dict):
        faces = geometry_result.object_index.get("faces", [])
        if isinstance(faces, list):
            face_candidates: list[dict[str, Any]] = [
                item for item in faces if isinstance(item, dict)
            ]
            if face_candidates:
                face_candidates.sort(
                    key=lambda item: float(item.get("area", 0.0) or 0.0), reverse=True
                )
                picked = face_candidates[0]
                face_id = picked.get("face_id")
                if isinstance(face_id, str) and face_id:
                    target_entity_ids = [face_id]

                center = picked.get("center")
                if (
                    isinstance(center, list)
                    and len(center) >= 3
                    and all(isinstance(v, (int, float)) for v in center[:3])
                ):
                    focus_center = [float(center[0]), float(center[1]), float(center[2])]

                bbox = picked.get("bbox")
                if isinstance(bbox, dict):
                    spans = [bbox.get("xlen"), bbox.get("ylen"), bbox.get("zlen")]
                    numeric_spans = [
                        float(v) for v in spans if isinstance(v, (int, float)) and float(v) > 0
                    ]
                    if numeric_spans:
                        focus_span = max(2.0, max(numeric_spans) * 2.6)

    render_specs: list[dict[str, Any]] = [
        {
            "name": "iso_035_025_z100",
            "azimuth_deg": 35.0,
            "elevation_deg": 25.0,
            "zoom": 1.0,
            "style": "shaded",
        },
        {
            "name": "micro_043_018_z138",
            "azimuth_deg": 43.0,
            "elevation_deg": 18.0,
            "zoom": 1.38,
            "style": "shaded",
        },
        {
            "name": "front_002_011_z165",
            "azimuth_deg": 2.0,
            "elevation_deg": 11.0,
            "zoom": 1.65,
            "style": "wireframe",
        },
        {
            "name": "rear_183_014_z215",
            "azimuth_deg": 183.0,
            "elevation_deg": 14.0,
            "zoom": 2.15,
            "style": "shaded",
        },
        {
            "name": "high_271_071_z090",
            "azimuth_deg": 271.0,
            "elevation_deg": 71.0,
            "zoom": 0.90,
            "style": "wireframe",
        },
    ]
    if target_entity_ids and focus_center is not None and focus_span is not None:
        render_specs.append(
            {
                "name": "focus_face_126_022_z190",
                "azimuth_deg": 126.0,
                "elevation_deg": 22.0,
                "zoom": 1.9,
                "style": "shaded",
                "target_entity_ids": target_entity_ids,
                "focus_center": focus_center,
                "focus_span": focus_span,
                "focus_padding_ratio": 0.2,
            }
        )

    per_view: list[dict[str, Any]] = []
    for index, spec in enumerate(render_specs, start=1):
        render_result = await runner.render_view(
            session_id=session_id,
            azimuth_deg=float(spec["azimuth_deg"]),
            elevation_deg=float(spec["elevation_deg"]),
            zoom=float(spec["zoom"]),
            width_px=int(spec.get("width_px", 1200)),
            height_px=int(spec.get("height_px", 840)),
            style=str(spec.get("style", "shaded")),
            target_entity_ids=spec.get("target_entity_ids"),
            focus_center=spec.get("focus_center"),
            focus_span=spec.get("focus_span"),
            focus_padding_ratio=float(spec.get("focus_padding_ratio", 0.15)),
            include_artifact_content=True,
            timeout=120,
        )

        spec_slug = _slug(str(spec["name"]))
        _write_json(
            results_dir / "render_views" / f"{index:02d}_{spec_slug}.json",
            {
                "spec": spec,
                "result": asdict(render_result),
            },
        )

        for filename, content in render_result.output_file_contents.items():
            if filename.endswith(".png"):
                renamed = f"{index:02d}_{spec_slug}_{filename}"
                (outputs_dir / renamed).write_bytes(content)

        camera = (
            render_result.camera if isinstance(render_result.camera, dict) else {}
        )
        per_view.append(
            {
                "index": index,
                "name": spec["name"],
                "azimuth_deg": spec["azimuth_deg"],
                "elevation_deg": spec["elevation_deg"],
                "zoom": spec["zoom"],
                "success": render_result.success,
                "view_file": render_result.view_file,
                "error_message": render_result.error_message,
                "render_source": camera.get("render_source"),
                "render_fallback_used": camera.get("render_fallback_used"),
                "render_warning": camera.get("render_warning"),
            }
        )

    if last_result is not None:
        for filename, content in last_result.output_file_contents.items():
            (outputs_dir / filename).write_bytes(content)

    source_counts: dict[str, int] = {}
    success_count = 0
    for item in per_view:
        source = str(item.get("render_source") or "unknown")
        source_counts[source] = source_counts.get(source, 0) + 1
        if bool(item.get("success")):
            success_count += 1

    summary = {
        "session_id": session_id,
        "action_success": bool(last_result and last_result.success),
        "step_file": next(
            (
                f
                for f in (last_result.output_files if last_result else [])
                if f.endswith(".step")
            ),
            None,
        ),
        "render_total": len(per_view),
        "render_success_count": success_count,
        "render_source_counts": source_counts,
        "views": per_view,
        "target_entity_ids": target_entity_ids,
        "focus_center": focus_center,
        "focus_span": focus_span,
    }

    _write_json(
        run_dir / "run_manifest.json",
        {
            "run_dir": str(run_dir),
            "session_id": session_id,
            "actions": actions,
            "render_specs": render_specs,
            "provider": settings.llm_reasoning_provider,
            "model": settings.llm_reasoning_model,
        },
    )
    _write_json(
        results_dir / "action_sequence.json",
        [asdict(item) for item in sequence_results],
    )
    _write_json(results_dir / "query_geometry.json", asdict(geometry_result))
    _write_json(run_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=True, sort_keys=True))


asyncio.run(main())
PY

ln -sfn "$RUN_DIR" "$RUNS_ROOT/latest"
echo "[render-multiview] latest: $RUNS_ROOT/latest"
