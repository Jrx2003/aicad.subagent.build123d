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

echo "[relation-base-probe] run_dir: $RUN_DIR"

AICAD_RUN_DIR="$RUN_DIR" uv run python - <<'PY'
import asyncio
import json
import os
import shutil
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


def _extract_relation_types(relation_index: dict[str, Any] | None) -> list[str]:
    if not isinstance(relation_index, dict):
        return []
    relations_raw = relation_index.get("relations")
    relations = relations_raw if isinstance(relations_raw, list) else []
    relation_types = []
    for item in relations:
        if not isinstance(item, dict):
            continue
        relation_type = item.get("relation_type")
        if isinstance(relation_type, str) and relation_type:
            relation_types.append(relation_type)
    return sorted(dict.fromkeys(relation_types))


def _extract_group_types(relation_index: dict[str, Any] | None) -> list[str]:
    if not isinstance(relation_index, dict):
        return []
    groups_raw = relation_index.get("relation_groups")
    groups = groups_raw if isinstance(groups_raw, list) else []
    group_types = []
    for item in groups:
        if not isinstance(item, dict):
            continue
        group_type = item.get("group_type")
        if isinstance(group_type, str) and group_type:
            group_types.append(group_type)
    return sorted(dict.fromkeys(group_types))


async def _ensure_preview(
    *,
    case_dir: Path,
    last_result: Any,
    runner: McpSandboxRunner,
    session_id: str,
    step: int,
) -> Path | None:
    outputs_dir = case_dir / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    output_files = getattr(last_result, "output_file_contents", {})
    if isinstance(output_files, dict):
        preview_bytes = output_files.get("preview_iso.png")
        if isinstance(preview_bytes, bytes):
            preview_path = outputs_dir / "preview_iso.png"
            preview_path.write_bytes(preview_bytes)
            return preview_path

    render_result = await runner.render_view(
        session_id=session_id,
        step=step,
        azimuth_deg=36.0,
        elevation_deg=24.0,
        zoom=1.0,
        width_px=1024,
        height_px=768,
        style="shaded",
        include_artifact_content=True,
        timeout=120,
    )
    if not render_result.success:
        return None
    view_file = render_result.view_file
    if (
        isinstance(view_file, str)
        and isinstance(render_result.output_file_contents, dict)
        and isinstance(render_result.output_file_contents.get(view_file), bytes)
    ):
        preview_path = outputs_dir / "preview_iso.png"
        preview_path.write_bytes(render_result.output_file_contents[view_file])
        _write_json(case_dir / "results" / "render_view.json", asdict(render_result))
        return preview_path
    return None


async def _run_case(
    *,
    runner: McpSandboxRunner,
    root_dir: Path,
    case_name: str,
    session_id: str,
    actions: list[dict[str, Any]],
    sketch_step: int,
    topology_step: int,
) -> dict[str, Any]:
    case_dir = root_dir / "cases" / case_name
    results_dir = case_dir / "results"
    relation_dir = case_dir / "relation_base"
    results_dir.mkdir(parents=True, exist_ok=True)
    relation_dir.mkdir(parents=True, exist_ok=True)

    sequence_results = await runner.apply_action_sequence(
        actions=actions,
        session_id=session_id,
        timeout=120,
        include_artifact_content=True,
        clear_session=True,
    )
    if not sequence_results:
        raise RuntimeError(f"{case_name}: apply_action_sequence returned no results")
    last_result = sequence_results[-1]

    sketch_result = await runner.query_sketch(
        session_id=session_id,
        step=sketch_step,
        timeout=60,
    )
    topology_result = await runner.query_topology(
        session_id=session_id,
        step=topology_step,
        include_faces=True,
        include_edges=True,
        max_items_per_type=20,
        timeout=60,
    )

    _write_json(
        results_dir / "action_sequence.json",
        [asdict(item) for item in sequence_results],
    )
    _write_json(results_dir / "query_sketch.json", asdict(sketch_result))
    _write_json(results_dir / "query_topology.json", asdict(topology_result))
    _write_json(
        relation_dir / "query_sketch_relation_index.json",
        getattr(sketch_result, "relation_index", None),
    )
    _write_json(
        relation_dir / "query_topology_relation_index.json",
        getattr(topology_result, "relation_index", None),
    )

    preview_path = await _ensure_preview(
        case_dir=case_dir,
        last_result=last_result,
        runner=runner,
        session_id=session_id,
        step=topology_step,
    )

    return {
        "case_name": case_name,
        "session_id": session_id,
        "action_count": len(actions),
        "final_step": getattr(last_result, "step_file", None),
        "query_sketch_relation_types": _extract_relation_types(
            getattr(sketch_result, "relation_index", None)
        ),
        "query_sketch_group_types": _extract_group_types(
            getattr(sketch_result, "relation_index", None)
        ),
        "query_topology_relation_types": _extract_relation_types(
            getattr(topology_result, "relation_index", None)
        ),
        "query_topology_group_types": _extract_group_types(
            getattr(topology_result, "relation_index", None)
        ),
        "preview_iso_path": (
            str(preview_path.relative_to(root_dir)) if preview_path is not None else None
        ),
    }


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

    cases = [
        {
            "case_name": "washer_annulus",
            "session_id": "relation-base-washer",
            "sketch_step": 3,
            "topology_step": 4,
            "actions": [
                {"action_type": "create_sketch", "action_params": {"plane": "XY"}},
                {"action_type": "add_circle", "action_params": {"radius": 20.0, "center": [0.0, 0.0]}},
                {"action_type": "add_circle", "action_params": {"radius": 12.0, "center": [0.0, 0.0]}},
                {"action_type": "extrude", "action_params": {"distance": 10.0}},
            ],
        },
        {
            "case_name": "bent_pipe",
            "session_id": "relation-base-bent-pipe",
            "sketch_step": 5,
            "topology_step": 6,
            "actions": [
                {"action_type": "create_sketch", "action_params": {"plane": "XY"}},
                {
                    "action_type": "add_path",
                    "action_params": {
                        "start": [0.0, 0.0],
                        "segments": [
                            {"type": "line", "length": 40.0, "direction": "x"},
                            {"type": "tangent_arc", "radius": 20.0, "angle_degrees": 90.0},
                            {"type": "line", "length": 40.0, "direction": "y"},
                        ],
                    },
                },
                {
                    "action_type": "create_sketch",
                    "action_params": {
                        "plane": "YZ",
                        "path_ref": "path:2:P_1",
                        "path_endpoint": "end",
                        "frame_mode": "normal_to_path_tangent",
                    },
                },
                {"action_type": "add_circle", "action_params": {"radius": 10.0, "center": [0.0, 0.0]}},
                {"action_type": "add_circle", "action_params": {"radius": 8.0, "center": [0.0, 0.0]}},
                {"action_type": "sweep", "action_params": {"transition": "round"}},
            ],
        },
    ]

    manifest = {
        "run_dir": str(run_dir),
        "runner": "McpSandboxRunner",
        "provider": settings.llm_reasoning_provider,
        "model": settings.llm_reasoning_model,
        "cases": [
            {
                "case_name": case["case_name"],
                "session_id": case["session_id"],
                "action_count": len(case["actions"]),
                "sketch_step": case["sketch_step"],
                "topology_step": case["topology_step"],
            }
            for case in cases
        ],
    }
    _write_json(run_dir / "run_manifest.json", manifest)

    summaries = []
    top_level_preview: Path | None = None
    for case in cases:
        summary = await _run_case(
            runner=runner,
            root_dir=run_dir,
            case_name=case["case_name"],
            session_id=case["session_id"],
            actions=case["actions"],
            sketch_step=case["sketch_step"],
            topology_step=case["topology_step"],
        )
        summaries.append(summary)
        preview_rel = summary.get("preview_iso_path")
        if isinstance(preview_rel, str) and case["case_name"] == "bent_pipe":
            preview_path = run_dir / preview_rel
            if preview_path.exists():
                top_level_preview = preview_path

    if top_level_preview is None:
        for summary in summaries:
            preview_rel = summary.get("preview_iso_path")
            if isinstance(preview_rel, str):
                preview_path = run_dir / preview_rel
                if preview_path.exists():
                    top_level_preview = preview_path
                    break

    if top_level_preview is not None:
        shutil.copy2(top_level_preview, outputs_dir / "preview_iso.png")

    _write_json(
        run_dir / "summary.json",
        {
            "probe": "relation_base",
            "case_count": len(summaries),
            "cases": summaries,
            "top_level_preview_iso": (
                str((outputs_dir / "preview_iso.png").relative_to(run_dir))
                if (outputs_dir / "preview_iso.png").exists()
                else None
            ),
        },
    )


asyncio.run(main())
PY
