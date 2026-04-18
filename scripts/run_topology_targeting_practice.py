from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

from common.config import settings
from sandbox.mcp_runner import McpSandboxRunner
from sub_agent_runtime.practice_runner import render_generated_previews_async


def _json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__bytes__": len(value)}
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="python", exclude_none=False))
    if hasattr(value, "__dataclass_fields__"):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json_safe(payload), ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _pick_candidate_ref_ids(payload: dict[str, Any], *, entity_type: str, label_token: str) -> list[str]:
    for candidate in payload.get("candidate_sets") or []:
        if not isinstance(candidate, dict):
            continue
        label = str(candidate.get("label") or "").lower()
        candidate_entity_type = str(candidate.get("entity_type") or "").lower()
        if candidate_entity_type == entity_type.lower() and label_token.lower() in label:
            return [
                str(item).strip()
                for item in (candidate.get("ref_ids") or [])
                if isinstance(item, str) and str(item).strip()
            ]
    return []


def _pick_candidate_entity_ids(payload: dict[str, Any], *, entity_type: str, label_token: str) -> list[str]:
    for candidate in payload.get("candidate_sets") or []:
        if not isinstance(candidate, dict):
            continue
        label = str(candidate.get("label") or "").lower()
        candidate_entity_type = str(candidate.get("entity_type") or "").lower()
        if candidate_entity_type == entity_type.lower() and label_token.lower() in label:
            return [
                str(item).strip()
                for item in (candidate.get("entity_ids") or [])
                if isinstance(item, str) and str(item).strip()
            ]
    return []


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a manual topology-targeting capability practice case."
    )
    parser.add_argument("--run-dir", type=str, required=True)
    args = parser.parse_args()

    case_dir = Path(args.run_dir).expanduser().resolve()
    session_id = "practice-topology-targeting"
    prompt_text = (
        "Build a 62mm x 40mm x 14mm bracket body, then use topology-aware local edits to "
        "target the top face for a centered circular cut, then inspect the top outer edges."
    )

    for subdir in ("actions", "queries", "outputs", "evaluation", "trace"):
        (case_dir / subdir).mkdir(parents=True, exist_ok=True)
    (case_dir / "prompt.txt").write_text(prompt_text + "\n", encoding="utf-8")
    _write_json(
        case_dir / "practice_case.json",
        {
            "case_id": case_dir.name,
            "mode": "manual_topology_capability_probe",
            "prompt": prompt_text,
            "expected_capabilities": [
                "query_topology",
                "face_ref_local_cut",
                "edge_refs_local_targeting",
                "render_view",
            ],
        },
    )

    runner = McpSandboxRunner(
        command=settings.sandbox_mcp_server_command,
        args=settings.sandbox_mcp_server_args_list,
        cwd=settings.sandbox_mcp_server_cwd_effective,
        timeout_buffer_seconds=settings.sandbox_mcp_timeout_buffer_seconds,
    )
    try:
        build_code = "\n".join(
            [
                "from build123d import *",
                "",
                "with BuildPart() as part:",
                "    Box(62, 40, 14)",
                "result = part.part",
                "show_object(result)",
            ]
        )
        build_result = await runner.execute(
            code=build_code,
            timeout=120,
            requirement_text=prompt_text,
            session_id=session_id,
        )
        _write_json(case_dir / "actions" / "round_01_execute_build123d.json", build_result)
        for filename, content in build_result.output_file_contents.items():
            (case_dir / "outputs" / filename).write_bytes(content)

        topology_initial = await runner.query_topology(
            session_id=session_id,
            include_faces=True,
            include_edges=True,
            selection_hints=["top_faces", "top_outer_edges"],
            requirement_text=prompt_text,
            timeout=60,
        )
        _write_json(case_dir / "queries" / "round_02_query_topology_initial.json", topology_initial)
        topology_initial_payload = _json_safe(topology_initial)
        top_edge_refs = _pick_candidate_ref_ids(
            topology_initial_payload,
            entity_type="edge",
            label_token="top",
        )
        top_face_refs = _pick_candidate_ref_ids(
            topology_initial_payload,
            entity_type="face",
            label_token="top",
        )
        top_face_entity_ids = _pick_candidate_entity_ids(
            topology_initial_payload,
            entity_type="face",
            label_token="top",
        )

        create_sketch_result = await runner.apply_cad_action(
            action_type="create_sketch",
            action_params={
                "face_ref": top_face_refs[0] if top_face_refs else None,
                "position": [0.0, 0.0],
            },
            session_id=session_id,
            timeout=120,
            include_artifact_content=True,
        )
        _write_json(
            case_dir / "actions" / "round_03_apply_cad_action_create_sketch.json",
            create_sketch_result,
        )
        for filename, content in create_sketch_result.output_file_contents.items():
            (case_dir / "outputs" / filename).write_bytes(content)

        add_circle_result = await runner.apply_cad_action(
            action_type="add_circle",
            action_params={
                "radius": 3.0,
                "position": [0.0, 0.0],
            },
            session_id=session_id,
            timeout=120,
            include_artifact_content=True,
        )
        _write_json(
            case_dir / "actions" / "round_04_apply_cad_action_add_circle.json",
            add_circle_result,
        )
        for filename, content in add_circle_result.output_file_contents.items():
            (case_dir / "outputs" / filename).write_bytes(content)

        cut_result = await runner.apply_cad_action(
            action_type="cut_extrude",
            action_params={
                "distance": 14.0,
            },
            session_id=session_id,
            timeout=120,
            include_artifact_content=True,
        )
        _write_json(
            case_dir / "actions" / "round_05_apply_cad_action_cut_extrude.json",
            cut_result,
        )
        for filename, content in cut_result.output_file_contents.items():
            (case_dir / "outputs" / filename).write_bytes(content)

        topology_after_cut = await runner.query_topology(
            session_id=session_id,
            include_faces=True,
            include_edges=True,
            selection_hints=["top_faces", "top_outer_edges"],
            requirement_text=prompt_text,
            timeout=60,
        )
        _write_json(case_dir / "queries" / "round_06_query_topology_after_cut.json", topology_after_cut)
        topology_after_cut_payload = _json_safe(topology_after_cut)
        top_face_ref_after_cut = (
            _pick_candidate_ref_ids(
                topology_after_cut_payload,
                entity_type="face",
                label_token="top",
            )
            or top_face_refs
        )
        top_face_entities = (
            _pick_candidate_entity_ids(
                topology_after_cut_payload,
                entity_type="face",
                label_token="top",
            )
            or top_face_entity_ids
        )
        top_edge_refs_after_cut = (
            _pick_candidate_ref_ids(
                topology_after_cut_payload,
                entity_type="edge",
                label_token="top",
            )
            or top_edge_refs
        )

        chamfer_result = await runner.apply_cad_action(
            action_type="chamfer",
            action_params={"distance": 0.8, "edge_refs": top_edge_refs_after_cut[:4]},
            session_id=session_id,
            timeout=120,
            include_artifact_content=True,
        )
        _write_json(case_dir / "actions" / "round_07_apply_cad_action_chamfer.json", chamfer_result)
        for filename, content in chamfer_result.output_file_contents.items():
            (case_dir / "outputs" / filename).write_bytes(content)

        geometry_after_hole = await runner.query_geometry(
            session_id=session_id,
            include_solids=True,
            include_faces=True,
            include_edges=True,
            max_items_per_type=20,
            timeout=60,
        )
        _write_json(
            case_dir / "queries" / "round_08_query_geometry_after_local_edits.json",
            geometry_after_hole,
        )

        render_iso = await runner.render_view(
            session_id=session_id,
            azimuth_deg=35.0,
            elevation_deg=25.0,
            zoom=1.1,
            width_px=1024,
            height_px=768,
            target_entity_ids=top_face_entities[:1],
            include_artifact_content=True,
            timeout=120,
        )
        _write_json(case_dir / "queries" / "round_09_render_view_iso.json", render_iso)
        for filename, content in render_iso.output_file_contents.items():
            (case_dir / "outputs" / filename).write_bytes(content)

        validate_result = await runner.validate_requirement(
            session_id=session_id,
            requirements={
                "description": prompt_text,
            },
            requirement_text=prompt_text,
            timeout=60,
        )
        _write_json(case_dir / "queries" / "round_10_validate_requirement.json", validate_result)

        final_step_candidates = sorted((case_dir / "outputs").glob("*.step"))
        final_step_path = final_step_candidates[-1] if final_step_candidates else None
        preview_payload = {}
        if final_step_path is not None:
            preview_payload = await render_generated_previews_async(
                step_path=final_step_path,
                evaluation_dir=case_dir / "evaluation",
            )

        summary = {
            "case_id": case_dir.name,
            "session_id": session_id,
            "build_success": bool(build_result.success),
            "create_sketch_success": bool(create_sketch_result.success),
            "add_circle_success": bool(add_circle_result.success),
            "cut_extrude_success": bool(cut_result.success),
            "topology_query_after_cut_success": bool(topology_after_cut.success),
            "chamfer_success": bool(chamfer_result.success),
            "validate_success": bool(validate_result.success),
            "validation_complete": bool(getattr(validate_result, "is_complete", False)),
            "selected_edge_refs": top_edge_refs[:4],
            "selected_face_ref": top_face_refs[0] if top_face_refs else None,
            "selected_edge_refs_after_cut": top_edge_refs_after_cut[:4],
            "selected_face_ref_after_cut": (
                top_face_ref_after_cut[0] if top_face_ref_after_cut else None
            ),
            "matched_face_entity_ids": top_face_entities[:1],
            "preview_payload": preview_payload,
        }
        _write_json(case_dir / "summary.json", summary)
        _write_json(
            case_dir / "practice_analysis.json",
            {
                "case_id": case_dir.name,
                "status": (
                    "complete"
                    if summary["build_success"] and summary["cut_extrude_success"]
                    else "open_with_error"
                ),
                "prompt": prompt_text,
                "query_topology_initial": topology_initial_payload,
                "query_topology_after_cut": topology_after_cut_payload,
                "selected_edge_refs": top_edge_refs[:4],
                "selected_face_ref": top_face_refs[0] if top_face_refs else None,
                "build_result": _json_safe(build_result),
                "create_sketch_result": _json_safe(create_sketch_result),
                "add_circle_result": _json_safe(add_circle_result),
                "cut_result": _json_safe(cut_result),
                "selected_edge_refs_after_cut": top_edge_refs_after_cut[:4],
                "selected_face_ref_after_cut": (
                    top_face_ref_after_cut[0] if top_face_ref_after_cut else None
                ),
                "chamfer_result": _json_safe(chamfer_result),
                "validate_result": _json_safe(validate_result),
                "preview_payload": preview_payload,
            },
        )
    finally:
        await runner.aclose()


if __name__ == "__main__":
    asyncio.run(main())
