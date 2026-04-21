from __future__ import annotations

import json
from typing import Any

from sub_agent_runtime.semantic_kernel.repair_packets import (
    describe_runtime_repair_packet_support,
)


def _failure_lint_ids(previous_tool_failure_summary: dict[str, Any] | None) -> set[str]:
    if not isinstance(previous_tool_failure_summary, dict):
        return set()
    lint_hits = previous_tool_failure_summary.get("lint_hits")
    if not isinstance(lint_hits, list):
        return set()
    return {
        str(item.get("rule_id") or "").strip()
        for item in lint_hits
        if isinstance(item, dict) and str(item.get("rule_id") or "").strip()
    }


def _failure_lint_hits_payload(
    previous_tool_failure_summary: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not isinstance(previous_tool_failure_summary, dict):
        return []
    lint_hits = previous_tool_failure_summary.get("lint_hits")
    if not isinstance(lint_hits, list):
        return []
    return [item for item in lint_hits if isinstance(item, dict)]


def _failure_repair_recipe_payload(
    previous_tool_failure_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(previous_tool_failure_summary, dict):
        return {}
    repair_recipe = previous_tool_failure_summary.get("repair_recipe")
    return repair_recipe if isinstance(repair_recipe, dict) else {}


def _previous_failure_requires_latest_topology_face_ref(
    previous_tool_failure_summary: dict[str, Any] | None,
) -> bool:
    if not isinstance(previous_tool_failure_summary, dict):
        return False
    error_text = str(previous_tool_failure_summary.get("error") or "").strip().lower()
    return (
        "create_sketch must use face_ref from latest query_topology during local_finish"
        in error_text
    )


def _previous_failure_used_candidate_set_label_as_reference(
    previous_tool_failure_summary: dict[str, Any] | None,
) -> bool:
    if not isinstance(previous_tool_failure_summary, dict):
        return False
    error_text = str(previous_tool_failure_summary.get("error") or "").strip().lower()
    return (
        "invalid_reference: malformed face_ref" in error_text
        and "candidate-set label" in error_text
    ) or (
        "invalid_reference: malformed edge_ref" in error_text
        and "candidate-set label" in error_text
    )


def _previous_failure_hit_detached_subtractive_builder_runtime_error(
    previous_tool_failure_summary: dict[str, Any] | None,
) -> bool:
    if not isinstance(previous_tool_failure_summary, dict):
        return False
    error_text = str(previous_tool_failure_summary.get("error") or "").strip().lower()
    return "nothing to subtract from" in error_text


def _latest_repair_packet_payload(
    domain_kernel_digest: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(domain_kernel_digest, dict):
        return {}
    recipe_id = str(domain_kernel_digest.get("latest_repair_packet_recipe_id") or "").strip()
    recipe_summary = str(
        domain_kernel_digest.get("latest_repair_packet_recipe_summary") or ""
    ).strip()
    family_id = str(domain_kernel_digest.get("latest_repair_packet_family_id") or "").strip()
    repair_mode = str(
        domain_kernel_digest.get("latest_repair_packet_repair_mode") or ""
    ).strip()
    skeleton_raw = domain_kernel_digest.get("latest_repair_packet_recipe_skeleton")
    skeleton = skeleton_raw if isinstance(skeleton_raw, dict) else {}
    target_anchor_summary_raw = domain_kernel_digest.get(
        "latest_repair_packet_target_anchor_summary"
    )
    target_anchor_summary = (
        target_anchor_summary_raw if isinstance(target_anchor_summary_raw, dict) else {}
    )
    host_frame_raw = domain_kernel_digest.get("latest_repair_packet_host_frame")
    host_frame = host_frame_raw if isinstance(host_frame_raw, dict) else {}
    if not any(
        (
            recipe_id,
            recipe_summary,
            family_id,
            repair_mode,
            skeleton,
            target_anchor_summary,
            host_frame,
        )
    ):
        return {}
    return {
        "recipe_id": recipe_id,
        "recipe_summary": recipe_summary,
        "family_id": family_id,
        "repair_mode": repair_mode,
        "recipe_skeleton": skeleton,
        "target_anchor_summary": target_anchor_summary,
        "host_frame": host_frame,
    }


def _coerce_xy_points(points: Any) -> list[list[float]]:
    if not isinstance(points, list):
        return []
    normalized: list[list[float]] = []
    for item in points:
        if (
            isinstance(item, (list, tuple))
            and len(item) >= 2
            and isinstance(item[0], (int, float))
            and isinstance(item[1], (int, float))
        ):
            normalized.append([float(item[0]), float(item[1])])
    return normalized


def _extract_local_center_preservation_summary(
    domain_kernel_digest: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(domain_kernel_digest, dict):
        return {}
    active_feature_instances = domain_kernel_digest.get("active_feature_instances")
    if not isinstance(active_feature_instances, list):
        return {}
    for item in active_feature_instances:
        if not isinstance(item, dict):
            continue
        parameter_bindings = (
            item.get("parameter_bindings")
            if isinstance(item.get("parameter_bindings"), dict)
            else {}
        )
        realized_centers = _coerce_xy_points(parameter_bindings.get("realized_centers"))
        expected_centers = _coerce_xy_points(parameter_bindings.get("expected_local_centers"))
        expected_count_raw = parameter_bindings.get("expected_local_center_count")
        expected_count = (
            int(expected_count_raw)
            if isinstance(expected_count_raw, (int, float))
            else (len(expected_centers) if expected_centers else None)
        )
        if not realized_centers or expected_count is None:
            continue
        if len(realized_centers) != expected_count:
            continue
        family_id = str(item.get("family_id") or "").strip()
        if not family_id:
            continue
        host_face = str(
            parameter_bindings.get("host_face")
            or item.get("host_ids", [""])[0]
            or ""
        ).strip()
        return {
            "family_id": family_id,
            "host_face": host_face,
            "expected_center_count": expected_count,
            "realized_centers": realized_centers,
            "source": "active_feature_instances",
        }
    return {}


def _repair_packet_skeleton_summary(
    recipe_skeleton: dict[str, Any] | None,
    *,
    host_frame: dict[str, Any] | None = None,
    target_anchor_summary: dict[str, Any] | None = None,
) -> str:
    if not isinstance(recipe_skeleton, dict):
        recipe_skeleton = {}
    if not isinstance(host_frame, dict):
        host_frame = {}
    if not isinstance(target_anchor_summary, dict):
        target_anchor_summary = {}
    fields: list[tuple[str, Any]] = []
    for key in (
        "mode",
        "host_face",
        "workplane_frame",
        "point_strategy",
        "center_source_key",
        "hole_call",
        "cutter_kind",
        "cutter_strategy",
        "profile_kind",
        "split_axis",
        "half_plane",
        "hole_axis",
        "sphere_center_z_strategy",
        "pad_strategy",
    ):
        value = recipe_skeleton.get(key)
        if value not in (None, "", [], {}):
            fields.append((key, value))
    frame_kind = host_frame.get("frame_kind")
    if frame_kind not in (None, "", [], {}):
        fields.append(("frame_kind", frame_kind))
    expected_local_centers = target_anchor_summary.get("expected_local_centers")
    if isinstance(expected_local_centers, list) and expected_local_centers:
        fields.append(("expected_local_centers", expected_local_centers[:4]))
    requested_centers = target_anchor_summary.get("requested_centers")
    if isinstance(requested_centers, list) and requested_centers:
        fields.append(("requested_centers", requested_centers[:4]))
    expected_half_profile_span = target_anchor_summary.get("expected_half_profile_span")
    if expected_half_profile_span not in (None, ""):
        fields.append(("expected_half_profile_span", expected_half_profile_span))
    expected_length = target_anchor_summary.get("expected_length")
    if expected_length not in (None, ""):
        fields.append(("expected_length", expected_length))
    if not fields:
        return ""
    return ", ".join(
        f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in fields[:8]
    )


def _repair_packet_runtime_skill(
    domain_kernel_digest: dict[str, Any] | None,
) -> dict[str, Any] | None:
    payload = _latest_repair_packet_payload(domain_kernel_digest)
    if not payload:
        return None
    recipe_id = str(payload.get("recipe_id") or "").strip()
    recipe_summary = str(payload.get("recipe_summary") or "").strip()
    family_id = str(payload.get("family_id") or "").strip()
    repair_mode = str(payload.get("repair_mode") or "").strip()
    recipe_skeleton = (
        payload.get("recipe_skeleton")
        if isinstance(payload.get("recipe_skeleton"), dict)
        else {}
    )
    target_anchor_summary = (
        payload.get("target_anchor_summary")
        if isinstance(payload.get("target_anchor_summary"), dict)
        else {}
    )
    host_frame = payload.get("host_frame") if isinstance(payload.get("host_frame"), dict) else {}
    support = describe_runtime_repair_packet_support(payload)
    runtime_supported = bool(support.get("runtime_supported"))
    if runtime_supported:
        guidance = [
            "The semantic kernel already surfaced a runtime-supported packet; prefer `execute_repair_packet` before falling back to a broader execute_build123d rewrite.",
        ]
    else:
        guidance = [
            "The semantic kernel already surfaced a descriptive-only packet; keep the next execute_build123d attempt on this recipe lane instead of inventing a different whole-part rewrite.",
        ]
    if family_id or recipe_id or repair_mode:
        identifiers = []
        if family_id:
            identifiers.append(f"family={family_id}")
        if recipe_id:
            identifiers.append(f"recipe={recipe_id}")
        if repair_mode:
            identifiers.append(f"repair_mode={repair_mode}")
        guidance.append("Current packet identifiers: " + ", ".join(identifiers) + ".")
    support_reason = str(support.get("support_reason") or "").strip()
    if runtime_supported:
        guidance.append(
            "This packet is executable by the runtime-owned repair lane right now, so only drop back to execute_build123d if packet execution fails or the packet contract misses."
        )
    elif support_reason == "missing_recipe_id":
        guidance.append(
            "This packet does not expose a concrete recipe_id yet, so treat it as descriptive guidance only and keep the repair on execute_build123d."
        )
    else:
        guidance.append(
            "This packet is descriptive-only for now; keep the next execute_build123d attempt aligned to the surfaced family recipe instead of switching lanes blindly."
        )
    if recipe_summary:
        guidance.append("Recipe summary: " + recipe_summary)
    skeleton_summary = _repair_packet_skeleton_summary(
        recipe_skeleton,
        host_frame=host_frame,
        target_anchor_summary=target_anchor_summary,
    )
    if skeleton_summary:
        guidance.append("Recipe skeleton: " + skeleton_summary + ".")
    center_source_key = str(recipe_skeleton.get("center_source_key") or "").strip()
    if center_source_key == "derive_from_requirement_or_validation":
        guidance.append(
            "The center layout is not fully grounded yet; derive it from the requirement/validation evidence or a topology read before cutting, instead of improvising manual cutter coordinates."
        )
    cutter_strategy = str(recipe_skeleton.get("cutter_strategy") or "").strip()
    helper_first_hole_call = (
        str(recipe_skeleton.get("hole_call") or "").strip() == "CounterSinkHole_or_Hole"
    )
    if helper_first_hole_call or (
        "avoid_manual_cone_cylinder_inside_active_builder" in cutter_strategy
    ):
        guidance.append(
            "Prefer the native hole helper contract on the target host face, and do not fall back to manual cone/cylinder cutters inside an active BuildPart unless the helper contract is provably insufficient."
        )
    return {
        "skill_id": "kernel_repair_packet_recipe",
        "when_relevant": "Use when the domain kernel digest already exposes a concrete repair packet recipe for the active family.",
        "guidance": guidance[:7],
    }


def _failure_repair_recipe_runtime_skill(
    previous_tool_failure_summary: dict[str, Any] | None,
) -> dict[str, Any] | None:
    payload = _failure_repair_recipe_payload(previous_tool_failure_summary)
    if not payload:
        return None
    recipe_id = str(payload.get("recipe_id") or "").strip()
    recipe_summary = str(payload.get("recipe_summary") or "").strip()
    repair_family = str(payload.get("repair_family") or "").strip()
    recipe_skeleton = (
        payload.get("recipe_skeleton")
        if isinstance(payload.get("recipe_skeleton"), dict)
        else {}
    )
    guidance = [
        "The previous write failure already exposed a concrete repair recipe; keep the next execute_build123d attempt on that lane instead of improvising a fresh whole-part rewrite."
    ]
    if recipe_id or repair_family:
        identifiers = []
        if recipe_id:
            identifiers.append(f"recipe={recipe_id}")
        if repair_family:
            identifiers.append(f"repair_family={repair_family}")
        guidance.append("Failure recipe identifiers: " + ", ".join(identifiers) + ".")
    if recipe_summary:
        guidance.append("Failure recipe summary: " + recipe_summary)
    skeleton_summary = _repair_packet_skeleton_summary(recipe_skeleton)
    if skeleton_summary:
        guidance.append("Failure recipe skeleton: " + skeleton_summary + ".")
    recipe_steps = recipe_skeleton.get("steps")
    if isinstance(recipe_steps, list) and recipe_steps:
        guidance.append(
            "Failure recipe steps: "
            + " | ".join(
                str(item).strip()
                for item in recipe_steps[:4]
                if isinstance(item, str) and str(item).strip()
            )
            + "."
        )
    return {
        "skill_id": "execute_build123d_failure_recipe_focus",
        "when_relevant": "Use when previous_tool_failure_summary already includes a concrete Build123d repair recipe from lint/runtime failure analysis.",
        "guidance": guidance[:5],
    }


def _failure_lint_runtime_skill(
    previous_tool_failure_summary: dict[str, Any] | None,
) -> dict[str, Any] | None:
    lint_hits = _failure_lint_hits_payload(previous_tool_failure_summary)
    if not lint_hits:
        return None
    lint_ids = {
        str(item.get("rule_id") or "").strip()
        for item in lint_hits
        if isinstance(item, dict) and str(item.get("rule_id") or "").strip()
    }
    structural_builder_lint_ids = {
        "invalid_build123d_contract.detached_subtractive_builder_without_host",
        "invalid_build123d_contract.active_builder_part_mutation",
        "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic",
        "invalid_build123d_contract.active_builder_temporary_primitive_transform_rebind",
        "invalid_build123d_api.nested_buildpart_part_transform",
    }
    guidance = [
        "The last execute_build123d failure already named concrete lint contracts; repair those exact contracts before changing geometry strategy."
    ]
    if lint_ids.intersection(structural_builder_lint_ids) and (
        lint_ids - structural_builder_lint_ids
    ):
        guidance.append(
            "When mixed lint hits include both feature-specific keyword/helper mistakes and active-builder violations, repair the builder-authority contract first; otherwise the next rewrite often stays non-executable even after syntax cleanup."
        )
    for item in lint_hits[:2]:
        rule_id = str(item.get("rule_id") or "").strip()
        repair_hint = str(item.get("repair_hint") or item.get("message") or "").strip()
        recommended_recipe_id = str(item.get("recommended_recipe_id") or "").strip()
        if rule_id:
            guidance.append(f"Lint contract: rule={rule_id}.")
        if recommended_recipe_id:
            guidance.append(
                "Preferred repair lane: "
                f"recommended_recipe_id={recommended_recipe_id}."
            )
        if repair_hint:
            guidance.append("Direct repair hint: " + repair_hint)
    if "invalid_build123d_contract.active_builder_temporary_primitive_transform_rebind" in lint_ids:
        guidance.append(
            "For active-builder transform rebind failures, choose one valid lane only: inside an active host, place the primitive correctly with `Locations(...)` at creation time instead of creating it first and relocating the Python variable afterward."
        )
        guidance.append(
            "If the solid truly needs detached rotation or placement, close that builder first and only then orient/place the detached solid with `Rot(...) * part` or `Pos(...) * Rot(...) * part` outside the active builder."
        )
    return {
        "skill_id": "execute_build123d_failure_lint_contract",
        "when_relevant": "Use when previous_tool_failure_summary exposes concrete lint hits with direct repair hints.",
        "guidance": guidance[:6],
    }


__all__ = [
    "_extract_local_center_preservation_summary",
    "_failure_lint_ids",
    "_failure_lint_runtime_skill",
    "_failure_repair_recipe_payload",
    "_failure_repair_recipe_runtime_skill",
    "_previous_failure_hit_detached_subtractive_builder_runtime_error",
    "_previous_failure_requires_latest_topology_face_ref",
    "_previous_failure_used_candidate_set_label_as_reference",
    "_repair_packet_runtime_skill",
]
