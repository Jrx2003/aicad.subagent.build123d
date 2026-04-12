from __future__ import annotations

import re
from typing import Any

from common.feature_agenda import (
    next_pending_feature_face_targets,
    next_pending_feature_summary,
)
from sandbox_mcp_server.registry import (
    analyze_requirement_semantics,
    requirement_requests_loft,
    requirement_requests_path_sweep,
)

_SKETCH_ACTIONS = {
    "create_sketch",
    "add_rectangle",
    "add_circle",
    "add_polygon",
    "add_path",
    "snapshot",
}
_SKETCH_EDIT_ACTIONS = _SKETCH_ACTIONS - {"snapshot"}
_SOLID_ACTIONS = {"extrude", "cut_extrude", "revolve", "loft", "sweep", "trim_solid"}
_TOPOLOGY_EDIT_ACTIONS = {
    "fillet",
    "chamfer",
    "hole",
    "sphere_recess",
    "pattern_linear",
    "pattern_circular",
}
_SUPPORTED_EXPECTED_CHANGE_IDS = {
    "current_query_snapshot",
    "current_query_sketch",
    "current_query_geometry",
    "current_query_topology",
    "current_validate_requirement",
    "current_render_view",
    "solid_should_exist",
    "state_mode_post_solid",
    "path_count_increase",
    "profile_count_increase",
    "target_blocker_reduction",
    "history_rewound",
}
_EXPECTED_CHANGE_ALIAS_MAP = {
    "solid_created": "solid_should_exist",
    "solid_exists": "solid_should_exist",
    "volume_positive": "solid_should_exist",
    "geometry_updated": "current_query_geometry",
    "geometry_changed": "current_query_geometry",
    "subtractive_feature_created": "current_query_geometry",
    "additive_feature_created": "current_query_geometry",
    "feature_created": "current_query_geometry",
    "sketch_updated": "current_query_sketch",
    "sketch_closed": "current_query_sketch",
    "profile_created": "current_query_sketch",
    "profile_closed": "current_query_sketch",
    "rail_created": "current_query_sketch",
    "path_created": "current_query_sketch",
    "topology_updated": "current_query_topology",
    "face_target_resolved": "current_query_topology",
    "edge_target_resolved": "current_query_topology",
    "validation_updated": "current_validate_requirement",
    "blocker_reduced": "target_blocker_reduction",
    "blocker_cleared": "target_blocker_reduction",
}

_SURFACE_POLICY_MAP: dict[str, dict[str, Any]] = {
    "pre_solid_base_sketch": {
        "allowed_actions": [
            "create_sketch",
            "add_rectangle",
            "add_circle",
            "add_polygon",
            "add_path",
            "extrude",
            "revolve",
            "loft",
            "sweep",
            "snapshot",
        ],
        "required_evidence": ["query_snapshot"],
        "preferred_inspection": ["query_snapshot", "query_sketch"],
        "rollback_scope": "current_sketch_window",
    },
    "path_rail": {
        "allowed_actions": ["create_sketch", "add_path", "snapshot"],
        "required_evidence": ["query_sketch"],
        "preferred_inspection": ["query_sketch", "render_view"],
        "rollback_scope": "current_path_window",
    },
    "path_profile": {
        "allowed_actions": [
            "create_sketch",
            "add_circle",
            "add_rectangle",
            "add_polygon",
            "sweep",
            "snapshot",
        ],
        "required_evidence": ["query_sketch"],
        "preferred_inspection": ["query_sketch", "validate_requirement"],
        "rollback_scope": "current_profile_window",
    },
    "loft_profile_stack": {
        "allowed_actions": [
            "create_sketch",
            "add_circle",
            "add_rectangle",
            "add_polygon",
            "loft",
            "snapshot",
        ],
        "required_evidence": ["query_sketch"],
        "preferred_inspection": ["query_sketch", "validate_requirement"],
        "rollback_scope": "current_profile_stack",
    },
    "face_edit_window": {
        "allowed_actions": [
            "create_sketch",
            "add_circle",
            "add_rectangle",
            "add_polygon",
            "cut_extrude",
            "extrude",
            "hole",
            "sphere_recess",
            "snapshot",
        ],
        "required_evidence": ["query_topology"],
        "preferred_inspection": ["query_topology", "validate_requirement", "render_view"],
        "rollback_scope": "last_topology_change",
    },
    "edge_feature_window": {
        "allowed_actions": ["fillet", "chamfer", "snapshot"],
        "required_evidence": ["query_topology"],
        "preferred_inspection": ["query_topology", "validate_requirement", "render_view"],
        "rollback_scope": "last_topology_change",
    },
    "pattern_window": {
        "allowed_actions": [
            "create_sketch",
            "add_circle",
            "add_rectangle",
            "add_polygon",
            "pattern_linear",
            "pattern_circular",
            "snapshot",
        ],
        "required_evidence": ["query_geometry"],
        "preferred_inspection": ["query_geometry", "validate_requirement", "render_view"],
        "rollback_scope": "latest_seed_feature",
    },
    "groove_window": {
        "allowed_actions": [
            "create_sketch",
            "add_rectangle",
            "add_circle",
            "add_polygon",
            "cut_extrude",
            "revolve",
            "trim_solid",
            "snapshot",
        ],
        "required_evidence": ["query_topology"],
        "preferred_inspection": ["query_topology", "validate_requirement", "render_view"],
        "rollback_scope": "last_topology_change",
    },
    "trim_window": {
        "allowed_actions": ["trim_solid", "snapshot"],
        "required_evidence": ["query_geometry"],
        "preferred_inspection": ["query_geometry", "validate_requirement", "render_view"],
        "rollback_scope": "last_topology_change",
    },
    "post_solid_finish_window": {
        "allowed_actions": [
            "create_sketch",
            "fillet",
            "chamfer",
            "hole",
            "sphere_recess",
            "pattern_linear",
            "pattern_circular",
            "trim_solid",
            "snapshot",
        ],
        "required_evidence": ["query_geometry"],
        "preferred_inspection": ["query_geometry", "validate_requirement", "render_view"],
        "rollback_scope": "last_topology_change",
    },
}


def _build_inspection_partitions(
    *,
    required_evidence: list[str] | None,
    preferred_inspection: list[str] | None,
) -> tuple[dict[str, list[str]], list[list[str]]]:
    ordered_tools: list[str] = []
    for raw_tools in (required_evidence or [], preferred_inspection or []):
        if not isinstance(raw_tools, str) and isinstance(raw_tools, list):
            for tool_name in raw_tools:
                if isinstance(tool_name, str) and tool_name not in ordered_tools:
                    ordered_tools.append(tool_name)

    partitions: dict[str, list[str]] = {
        "required_now": [
            tool for tool in (required_evidence or []) if isinstance(tool, str)
        ],
        "state_readback": [
            tool for tool in ordered_tools if tool in {"query_snapshot", "query_geometry"}
        ],
        "sketch_state": [
            tool for tool in ordered_tools if tool == "query_sketch"
        ],
        "topology_targeting": [
            tool for tool in ordered_tools if tool == "query_topology"
        ],
        "semantic_completion": [
            tool for tool in ordered_tools if tool == "validate_requirement"
        ],
        "visual_confirmation": [
            tool for tool in ordered_tools if tool == "render_view"
        ],
    }
    partitions = {key: value for key, value in partitions.items() if value}

    joint_request_groups: list[list[str]] = []

    def _add_joint_group(*tools: str) -> None:
        group = [tool for tool in tools if tool in ordered_tools]
        if len(group) < 2:
            return
        if group not in joint_request_groups:
            joint_request_groups.append(group)

    _add_joint_group("query_snapshot", "query_geometry")
    _add_joint_group("query_sketch", "validate_requirement")
    _add_joint_group("query_geometry", "validate_requirement")
    _add_joint_group("query_topology", "render_view")

    return partitions, joint_request_groups


def build_active_surface(
    *,
    requirements: dict[str, Any],
    action_history: list[dict[str, Any]] | None,
    evidence_status: dict[str, Any] | None,
    query_sketch: dict[str, Any] | None,
    query_topology: dict[str, Any] | None,
    requirement_validation: dict[str, Any] | None,
    relation_eval: dict[str, Any] | None,
    latest_unresolved_blockers: list[str] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    semantics = analyze_requirement_semantics(requirements)
    mixed_face_circle_cut_window = _requirement_prefers_mixed_face_circle_cut_window(
        requirements=requirements,
        semantics=semantics,
    )
    state_mode = _state_mode(evidence_status)
    blocker_codes = _normalize_string_list(latest_unresolved_blockers, limit=24)
    if not blocker_codes:
        blocker_codes = _collect_blockers(
            requirement_validation=requirement_validation,
            relation_eval=relation_eval,
            query_sketch=query_sketch,
        )
    latest_action_type = _latest_action_type(action_history)
    path_count = _path_count(query_sketch)
    profile_count = _profile_count(query_sketch)

    surface_type = "pre_solid_base_sketch"
    rationale = "No solid exists yet; continue from the current base sketch window."

    sweep_family_blockers = {
        "feature_path_sweep_rail",
        "feature_path_sweep_profile",
        "feature_path_sweep_frame",
        "feature_path_sweep_result",
        "eval:sweep_path_geometry",
        "eval:sweep_profile_section",
        "eval:sweep_result_annular_topology",
    }
    sweep_rail_phase_blockers = {
        "path_disconnected",
        "path_segment_sequence_mismatch",
    }
    loft_blockers = {
        "feature_loft_profile_stack",
        "feature_loft_result",
        "loft_requires_profile_stack_confirmation",
    }
    face_blockers = {
        "feature_target_face_edit",
        "feature_target_face_additive_merge",
        "feature_target_face_subtractive_merge",
    }
    edge_blockers = {"feature_fillet", "feature_chamfer"}
    pattern_blockers = {
        "feature_pattern_seed",
        "feature_pattern_seed_alignment",
        "feature_pattern",
    }
    groove_blockers = {
        "feature_annular_groove",
        "feature_revolved_groove_setup",
        "feature_revolved_groove_alignment",
        "feature_revolved_groove_result",
    }
    trim_blockers = {"feature_plane_trim"}
    sweep_family_requested = requirement_requests_path_sweep(
        requirements,
        requirement_text=semantics.normalized_text,
        semantics=semantics,
    )
    has_sweep_history = any(
        isinstance(item, dict)
        and str(item.get("action_type", "")).strip().lower() == "sweep"
        for item in (action_history or [])
    )

    if state_mode == "pre_solid":
        sweep_family_active = (
            sweep_family_requested
            or has_sweep_history
            or any(item in sweep_family_blockers for item in blocker_codes)
        )
        if sweep_family_active:
            if (
                path_count <= 0
                or latest_action_type == "add_path"
                or any(item in sweep_rail_phase_blockers for item in blocker_codes)
            ):
                surface_type = "path_rail"
                rationale = "The requirement family is sweep-like and the current rail evidence is incomplete or freshly edited."
            else:
                surface_type = "path_profile"
                rationale = "The rail exists; finish or repair the attached sweep profile before attempting the sweep result."
        elif _requirement_suggests_loft(requirements, blocker_codes):
            surface_type = "loft_profile_stack"
            rationale = "The requirement family is loft-like; the current profile stack is the active local work surface."
    else:
        if any(item in trim_blockers for item in blocker_codes):
            surface_type = "trim_window"
            rationale = "A trim-style blocker is active; focus on the current post-solid trim window."
        elif any(item in groove_blockers for item in blocker_codes) or semantics.mentions_revolved_groove_cut:
            surface_type = "groove_window"
            rationale = "A groove-style post-solid edit is active; keep the next round inside the local groove window."
        elif mixed_face_circle_cut_window and (
            any(item in face_blockers for item in blocker_codes)
            or any(item in pattern_blockers for item in blocker_codes)
            or latest_action_type in {"create_sketch", "add_circle", "cut_extrude", "hole"}
        ):
            surface_type = "face_edit_window"
            rationale = (
                "The requirement combines a central cut with a patterned circular face edit; "
                "keep the next round on the same target face, but direct hole actions remain valid when the requirement is explicitly hole-driven."
            )
        elif _prefer_face_edit_surface(
            latest_action_type=latest_action_type,
            blocker_codes=blocker_codes,
            semantics=semantics,
        ):
            surface_type = "face_edit_window"
            rationale = (
                "The current round is still inside a post-solid local sketch/edit window; "
                "finish the face-local edit before switching to downstream edge work."
            )
        elif any(item in pattern_blockers for item in blocker_codes):
            surface_type = "pattern_window"
            rationale = "The remaining blocker is pattern-specific; stay on the current seed/pattern window."
        elif (
            any(item in edge_blockers for item in blocker_codes)
            or semantics.mentions_fillet
            or semantics.mentions_chamfer
            or semantics.mentions_targeted_edge_feature
        ):
            surface_type = "edge_feature_window"
            rationale = "The next useful move is an edge-targeted local edit with current topology evidence."
        elif (
            any(item in face_blockers for item in blocker_codes)
            or semantics.mentions_face_edit
            or semantics.mentions_hole
            or semantics.mentions_spherical_recess
        ):
            surface_type = "face_edit_window"
            rationale = "The requirement is currently localized to a post-solid face edit window."
        elif semantics.mentions_pattern:
            surface_type = "pattern_window"
            rationale = "The remaining task is a repeated-feature layout; stay on the current seed/pattern window."
        else:
            surface_type = "post_solid_finish_window"
            rationale = "A solid already exists; keep the next round focused on one local post-solid finish window."

    target_ref_ids = _target_ref_ids_for_surface(
        requirements=requirements,
        action_history=action_history,
        query_sketch=query_sketch,
        query_topology=query_topology,
        state_mode=state_mode,
        surface_type=surface_type,
    )
    pending_feature_summary = next_pending_feature_summary(
        requirements=requirements,
        action_history=action_history,
    )
    if (
        state_mode == "post_solid"
        and surface_type == "face_edit_window"
        and isinstance(pending_feature_summary, str)
        and pending_feature_summary.strip()
    ):
        rationale = f"{rationale} Next pending feature: {pending_feature_summary}."

    active_surface = {
        "surface_id": f"{surface_type}:{_latest_step(evidence_status, action_history)}",
        "surface_type": surface_type,
        "state_mode": state_mode,
        "latest_action_type": latest_action_type,
        "target_ref_ids": target_ref_ids,
        "blocker_codes": blocker_codes[:12],
        "path_count": path_count,
        "profile_count": profile_count,
        "rationale": rationale,
    }
    base_policy = {
        **_SURFACE_POLICY_MAP.get(
            surface_type,
            _SURFACE_POLICY_MAP["post_solid_finish_window"],
        )
    }
    inspection_partitions, joint_request_groups = _build_inspection_partitions(
        required_evidence=base_policy.get("required_evidence"),
        preferred_inspection=base_policy.get("preferred_inspection"),
    )
    surface_policy = {
        **base_policy,
        "surface_type": surface_type,
        "state_mode": state_mode,
        "inspection_partitions": inspection_partitions,
        "joint_request_groups": joint_request_groups,
    }
    return active_surface, surface_policy


def infer_expected_outcome(
    *,
    active_surface: dict[str, Any] | None,
    evidence_status: dict[str, Any] | None,
    actions: list[dict[str, Any]] | None,
    inspection: dict[str, Any] | None,
    latest_unresolved_blockers: list[str] | None,
    query_sketch: dict[str, Any] | None,
) -> dict[str, Any] | None:
    action_list = [
        item for item in (actions or []) if isinstance(item, dict)
    ]
    inspection_payload = inspection if isinstance(inspection, dict) else None
    if not action_list and not inspection_payload:
        return None

    expected_changes: list[str] = []
    target_blockers = _normalize_string_list(latest_unresolved_blockers, limit=12)
    baseline = {
        "state_mode": _state_mode(evidence_status),
        "path_count": _path_count(query_sketch),
        "profile_count": _profile_count(query_sketch),
        "blockers": target_blockers,
        "latest_step": _latest_step(evidence_status, None),
    }

    for action in action_list:
        action_type = str(action.get("action_type", "")).strip().lower()
        action_params = (
            action.get("action_params")
            if isinstance(action.get("action_params"), dict)
            else {}
        )
        if action_type in _SKETCH_ACTIONS:
            expected_changes.append("current_query_sketch")
        if action_type == "add_path":
            expected_changes.append("path_count_increase")
        if action_type in {"add_circle", "add_rectangle", "add_polygon"}:
            expected_changes.append("profile_count_increase")
        if action_type in _SOLID_ACTIONS:
            expected_changes.extend(
                [
                    "current_query_geometry",
                    "solid_should_exist",
                    "state_mode_post_solid",
                    "current_validate_requirement",
                ]
            )
        if action_type in _TOPOLOGY_EDIT_ACTIONS or any(
            isinstance(action_params.get(field), str) and action_params.get(field)
            for field in ("face_ref", "path_ref")
        ) or bool(action_params.get("edge_refs")):
            expected_changes.append("current_query_topology")
        if target_blockers:
            expected_changes.append("target_blocker_reduction")
        if action_type == "rollback":
            expected_changes.append("history_rewound")

    if inspection_payload:
        for tool_name in (
            "query_snapshot",
            "query_sketch",
            "query_geometry",
            "query_topology",
            "validate_requirement",
            "render_view",
        ):
            if inspection_payload.get(tool_name) is True or isinstance(
                inspection_payload.get(tool_name), dict
            ):
                expected_changes.append(f"current_{tool_name}")

    expected_changes = list(dict.fromkeys(expected_changes))
    if not expected_changes:
        return None

    return {
        "source": "runtime_inferred",
        "surface_type": (
            str(active_surface.get("surface_type")).strip()
            if isinstance(active_surface, dict)
            else ""
        ),
        "summary": _expected_outcome_summary(
            active_surface=active_surface,
            expected_changes=expected_changes,
        ),
        "expected_changes": expected_changes,
        "target_blockers": target_blockers,
        "baseline": baseline,
    }


def build_outcome_delta(
    *,
    expected_outcome: dict[str, Any] | None,
    active_surface: dict[str, Any] | None,
    evidence_status: dict[str, Any] | None,
    latest_unresolved_blockers: list[str] | None,
    last_action_result: Any,
    query_sketch: dict[str, Any] | None,
    query_geometry: dict[str, Any] | None,
    query_topology: dict[str, Any] | None,
    requirement_validation: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(expected_outcome, dict):
        return None
    expected_changes = _normalize_string_list(
        expected_outcome.get("expected_changes"),
        limit=16,
    )
    if not expected_changes:
        return None

    baseline = (
        expected_outcome.get("baseline")
        if isinstance(expected_outcome.get("baseline"), dict)
        else {}
    )
    current_blockers = _normalize_string_list(latest_unresolved_blockers, limit=16)
    current_paths = _path_count(query_sketch)
    current_profiles = _profile_count(query_sketch)
    results: list[dict[str, Any]] = []
    achieved: list[str] = []
    missing: list[str] = []

    current_tools = (
        evidence_status.get("current")
        if isinstance(evidence_status, dict)
        and isinstance(evidence_status.get("current"), dict)
        else {}
    )
    state_mode = _state_mode(evidence_status)
    solid_exists = _solid_exists(query_geometry, last_action_result)
    baseline_blockers = _normalize_string_list(
        baseline.get("blockers"),
        limit=16,
    )

    for change in expected_changes:
        success = False
        observed: dict[str, Any] = {}
        if change == "current_query_snapshot":
            success = "query_snapshot" in current_tools
        elif change == "current_query_sketch":
            success = "query_sketch" in current_tools
            observed = {"path_count": current_paths, "profile_count": current_profiles}
        elif change == "current_query_geometry":
            success = "query_geometry" in current_tools
        elif change == "current_query_topology":
            success = "query_topology" in current_tools
        elif change == "current_validate_requirement":
            success = "validate_requirement" in current_tools
        elif change == "current_render_view":
            success = "render_view" in current_tools
        elif change == "solid_should_exist":
            success = solid_exists
            observed = {"solid_exists": solid_exists}
        elif change == "state_mode_post_solid":
            success = state_mode == "post_solid"
            observed = {"state_mode": state_mode}
        elif change == "path_count_increase":
            success = current_paths > int(baseline.get("path_count", 0) or 0)
            observed = {
                "before": baseline.get("path_count", 0),
                "after": current_paths,
            }
        elif change == "profile_count_increase":
            success = current_profiles > int(baseline.get("profile_count", 0) or 0)
            observed = {
                "before": baseline.get("profile_count", 0),
                "after": current_profiles,
            }
        elif change == "target_blocker_reduction":
            success = _blocker_reduction_succeeded(
                baseline_blockers=baseline_blockers,
                current_blockers=current_blockers,
            )
            observed = {
                "before": baseline_blockers,
                "after": current_blockers,
            }
        elif change == "history_rewound":
            success = bool(last_action_result) and not bool(
                getattr(last_action_result, "success", True)
            ) is False
        if success:
            achieved.append(change)
        else:
            missing.append(change)
        results.append(
            {
                "change": change,
                "achieved": success,
                "observed": observed,
            }
        )

    status = "satisfied" if not missing else "partial" if achieved else "missed"
    return {
        "surface_type": (
            str(active_surface.get("surface_type")).strip()
            if isinstance(active_surface, dict)
            else ""
        ),
        "status": status,
        "summary": (
            f"{len(achieved)}/{len(expected_changes)} expected change(s) achieved on the latest round."
        ),
        "expected_changes": expected_changes,
        "achieved_changes": achieved,
        "missing_changes": missing,
        "target_blockers_before": baseline_blockers,
        "current_blockers": current_blockers,
        "change_results": results,
    }


def normalize_expected_outcome_contract(
    *,
    expected_outcome: dict[str, Any] | None,
    inferred_expected_outcome: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(expected_outcome, dict):
        return inferred_expected_outcome

    normalized_expected_changes = _normalize_expected_change_list(
        expected_outcome.get("expected_changes")
    )
    if not normalized_expected_changes:
        return inferred_expected_outcome

    inferred_payload = (
        inferred_expected_outcome if isinstance(inferred_expected_outcome, dict) else {}
    )
    target_blockers = _normalize_string_list(
        expected_outcome.get("target_blockers"),
        limit=12,
    )
    if not target_blockers:
        target_blockers = _normalize_string_list(
            inferred_payload.get("target_blockers"),
            limit=12,
        )

    baseline: dict[str, Any] = {}
    if isinstance(inferred_payload.get("baseline"), dict):
        baseline = dict(inferred_payload["baseline"])
    elif isinstance(expected_outcome.get("baseline"), dict):
        baseline = dict(expected_outcome["baseline"])

    surface_type = str(expected_outcome.get("surface_type", "")).strip()
    if not surface_type:
        surface_type = str(inferred_payload.get("surface_type", "")).strip()

    summary = str(expected_outcome.get("summary", "")).strip()
    if not summary:
        summary = str(inferred_payload.get("summary", "")).strip()

    return {
        "source": "planner_normalized",
        "surface_type": surface_type,
        "summary": summary,
        "expected_changes": normalized_expected_changes,
        "target_blockers": target_blockers,
        "baseline": baseline,
    }


def _collect_blockers(
    *,
    requirement_validation: dict[str, Any] | None,
    relation_eval: dict[str, Any] | None,
    query_sketch: dict[str, Any] | None,
) -> list[str]:
    blockers: list[str] = []
    if isinstance(requirement_validation, dict):
        blockers.extend(
            _normalize_string_list(requirement_validation.get("blockers"), limit=16)
        )
    if isinstance(relation_eval, dict):
        blockers.extend(
            _normalize_string_list(relation_eval.get("blocking_eval_ids"), limit=16)
        )
    if (
        isinstance(query_sketch, dict)
        and isinstance(query_sketch.get("sketch_state"), dict)
    ):
        blockers.extend(
            _normalize_string_list(
                query_sketch["sketch_state"].get("issues"),
                limit=16,
            )
        )
    return list(dict.fromkeys(blockers))


def _prefer_face_edit_surface(
    *,
    latest_action_type: str,
    blocker_codes: list[str],
    semantics: Any,
) -> bool:
    if any(
        item in {
            "feature_target_face_edit",
            "feature_target_face_additive_merge",
            "feature_target_face_subtractive_merge",
        }
        for item in blocker_codes
    ):
        return True
    if latest_action_type not in _SKETCH_EDIT_ACTIONS:
        return False
    return bool(
        getattr(semantics, "mentions_face_edit", False)
        or getattr(semantics, "mentions_hole", False)
        or getattr(semantics, "mentions_spherical_recess", False)
    )


def _requirement_prefers_mixed_face_circle_cut_window(
    *,
    requirements: dict[str, Any],
    semantics: Any,
) -> bool:
    _ = requirements
    text = getattr(semantics, "normalized_text", "") if semantics is not None else ""
    if not isinstance(text, str) or not text:
        return False
    if not getattr(semantics, "mentions_hole", False):
        return False
    if not getattr(semantics, "mentions_pattern", False):
        return False

    explicit_cut_tokens = (
        "cut extrusion",
        "cut extrude",
        "extrude cut",
        "through the flange",
        "cut through the flange",
        "cut through flange",
        "through all",
        "through the entire solid",
        "construction circle",
        "construction line",
        "distributed circle",
        "pitch circle",
        "pcd",
        "circular array",
    )
    if not any(token in text for token in explicit_cut_tokens):
        return False

    has_central_cut_anchor = any(
        token in text
        for token in (
            "central circle",
            "central hole",
            "center circle",
            "centre circle",
            "center hole",
            "centre hole",
            "concentric circle",
        )
    )
    if not has_central_cut_anchor:
        return False

    diameter_values = {
        match.group(1)
        for match in re.finditer(r"diameter\s*(?:of)?\s*([0-9]+(?:\.[0-9]+)?)", text)
    }
    return len(diameter_values) >= 2


def _normalize_expected_change_list(value: Any) -> list[str]:
    items = value if isinstance(value, list) else []
    normalized: list[str] = []
    for raw_item in items:
        token = _canonical_expected_change(raw_item)
        if token:
            normalized.append(token)
    return list(dict.fromkeys(normalized))


def _canonical_expected_change(value: Any) -> str | None:
    if not isinstance(value, (str, int, float)):
        return None
    raw_token = str(value).strip().lower()
    if not raw_token:
        return None
    normalized_token = re.sub(r"[^a-z0-9]+", "_", raw_token).strip("_")
    if not normalized_token:
        return None
    if normalized_token in _SUPPORTED_EXPECTED_CHANGE_IDS:
        return normalized_token
    if normalized_token in _EXPECTED_CHANGE_ALIAS_MAP:
        return _EXPECTED_CHANGE_ALIAS_MAP[normalized_token]
    if "validate" in normalized_token:
        return "current_validate_requirement"
    if "render" in normalized_token or "view" in normalized_token:
        return "current_render_view"
    if (
        "topology" in normalized_token
        or "face" in normalized_token
        or "edge" in normalized_token
    ):
        return "current_query_topology"
    if (
        "sketch" in normalized_token
        or "profile" in normalized_token
        or "rail" in normalized_token
        or "path" in normalized_token
    ):
        return "current_query_sketch"
    if (
        "blocker" in normalized_token
        or "align" in normalized_token
        or "match" in normalized_token
        or "clear" in normalized_token
        or "reduce" in normalized_token
    ):
        return "target_blocker_reduction"
    if (
        "bbox" in normalized_token
        or "geometry" in normalized_token
        or "feature" in normalized_token
    ):
        return "current_query_geometry"
    if "solid" in normalized_token or "volume" in normalized_token:
        return "solid_should_exist"
    return None


def _target_ref_ids_for_surface(
    *,
    requirements: dict[str, Any],
    action_history: list[dict[str, Any]] | None,
    query_sketch: dict[str, Any] | None,
    query_topology: dict[str, Any] | None,
    state_mode: str,
    surface_type: str,
) -> list[str]:
    if state_mode == "pre_solid":
        refs: list[str] = []
        if (
            isinstance(query_sketch, dict)
            and isinstance(query_sketch.get("sketch_state"), dict)
        ):
            sketch_state = query_sketch["sketch_state"]
            refs.extend(_normalize_string_list(sketch_state.get("path_refs"), limit=4))
            refs.extend(
                _normalize_string_list(sketch_state.get("profile_refs"), limit=4)
            )
        return list(dict.fromkeys(refs))[:6]

    flange_surface_refs = _select_flange_surface_target_refs(
        requirements=requirements,
        action_history=action_history,
        query_topology=query_topology,
        surface_type=surface_type,
    )
    if flange_surface_refs:
        return flange_surface_refs[:6]

    topology_payload = query_topology if isinstance(query_topology, dict) else {}
    candidate_sets = (
        topology_payload.get("candidate_sets")
        if isinstance(topology_payload.get("candidate_sets"), list)
        else []
    )
    target_kind = "edge" if surface_type == "edge_feature_window" else "face"
    preferred_face_targets = (
        next_pending_feature_face_targets(
            requirements=requirements,
            action_history=action_history,
        )
        if target_kind == "face"
        else []
    )
    prioritized_refs: list[str] = []
    fallback_refs: list[str] = []
    refs: list[str] = []
    for item in candidate_sets:
        if not isinstance(item, dict):
            continue
        candidate_id = str(item.get("candidate_id", "")).strip().lower()
        if target_kind not in candidate_id:
            continue
        item_refs = _normalize_string_list(item.get("ref_ids"), limit=6)
        if preferred_face_targets and _candidate_id_matches_face_targets(
            candidate_id=candidate_id,
            face_targets=preferred_face_targets,
        ):
            prioritized_refs.extend(item_refs)
        else:
            fallback_refs.extend(item_refs)
    refs.extend(prioritized_refs)
    refs.extend(fallback_refs)
    if refs:
        return list(dict.fromkeys(refs))[:6]
    matched_ref_ids = _normalize_string_list(topology_payload.get("matched_ref_ids"), limit=6)
    return matched_ref_ids[:6]


def _candidate_id_matches_face_targets(
    *,
    candidate_id: str,
    face_targets: list[str],
) -> bool:
    normalized_targets = {
        str(item).strip().lower() for item in face_targets if isinstance(item, str)
    }
    if not normalized_targets:
        return False
    if "side" in normalized_targets and any(
        token in candidate_id for token in ("front_", "back_", "left_", "right_")
    ):
        return True
    return any(f"{target}_" in candidate_id for target in normalized_targets)


def _state_mode(evidence_status: dict[str, Any] | None) -> str:
    if isinstance(evidence_status, dict):
        state_mode = evidence_status.get("state_mode")
        if isinstance(state_mode, str) and state_mode.strip():
            return state_mode.strip()
    return "pre_solid"


def _latest_action_type(action_history: list[dict[str, Any]] | None) -> str | None:
    if not isinstance(action_history, list) or not action_history:
        return None
    latest = action_history[-1]
    if not isinstance(latest, dict):
        return None
    action_type = latest.get("action_type")
    if isinstance(action_type, str) and action_type.strip():
        return action_type.strip().lower()
    return None


def _latest_step(
    evidence_status: dict[str, Any] | None,
    action_history: list[dict[str, Any]] | None,
) -> int:
    if isinstance(evidence_status, dict):
        latest_step = evidence_status.get("latest_step")
        if isinstance(latest_step, int) and latest_step > 0:
            return latest_step
    if isinstance(action_history, list):
        for item in reversed(action_history):
            if not isinstance(item, dict):
                continue
            step = item.get("step")
            if isinstance(step, int) and step > 0:
                return step
    return 0


def _path_count(query_sketch: dict[str, Any] | None) -> int:
    if (
        not isinstance(query_sketch, dict)
        or not isinstance(query_sketch.get("sketch_state"), dict)
    ):
        return 0
    paths = query_sketch["sketch_state"].get("paths")
    return len(paths) if isinstance(paths, list) else 0


def _profile_count(query_sketch: dict[str, Any] | None) -> int:
    if (
        not isinstance(query_sketch, dict)
        or not isinstance(query_sketch.get("sketch_state"), dict)
    ):
        return 0
    profiles = query_sketch["sketch_state"].get("profiles")
    return len(profiles) if isinstance(profiles, list) else 0


def _solid_exists(query_geometry: dict[str, Any] | None, last_action_result: Any) -> bool:
    geometry = (
        query_geometry.get("geometry")
        if isinstance(query_geometry, dict)
        and isinstance(query_geometry.get("geometry"), dict)
        else {}
    )
    solids = geometry.get("solids")
    if isinstance(solids, (int, float)) and int(solids) > 0:
        return True
    snapshot = (
        getattr(last_action_result, "snapshot", None)
        if last_action_result is not None
        else None
    )
    if isinstance(snapshot, dict):
        geometry = snapshot.get("geometry")
        if isinstance(geometry, dict):
            solids = geometry.get("solids")
            if isinstance(solids, (int, float)) and int(solids) > 0:
                return True
    return False


def _requirement_suggests_loft(
    requirements: dict[str, Any],
    blocker_codes: list[str],
) -> bool:
    if requirement_requests_loft(requirements):
        return True
    return any(code.startswith("feature_loft_") for code in blocker_codes)


def _expected_outcome_summary(
    *,
    active_surface: dict[str, Any] | None,
    expected_changes: list[str],
) -> str:
    surface_type = (
        str(active_surface.get("surface_type")).strip()
        if isinstance(active_surface, dict)
        else "unknown_surface"
    )
    preview = ", ".join(expected_changes[:3])
    if len(expected_changes) > 3:
        preview = f"{preview}, ..."
    return f"Keep work on {surface_type}; expect {preview}."


def _blocker_reduction_succeeded(
    *,
    baseline_blockers: list[str],
    current_blockers: list[str],
) -> bool:
    if not baseline_blockers:
        return True
    baseline_set = set(baseline_blockers)
    current_set = set(current_blockers)
    return len(current_set & baseline_set) < len(baseline_set)


def _normalize_string_list(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        token = item.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
        if len(normalized) >= limit:
            break
    return normalized


def _select_flange_surface_target_refs(
    *,
    requirements: dict[str, Any] | None,
    action_history: list[dict[str, Any]] | None,
    query_topology: dict[str, Any] | None,
    surface_type: str,
) -> list[str]:
    if surface_type != "face_edit_window":
        return []
    flange_side = _requirement_flange_surface_side(requirements)
    if flange_side is None:
        return []
    face_records = _query_topology_face_records(query_topology)
    if not face_records:
        return []
    extents = _query_topology_face_extents(face_records)
    if extents is None:
        return []
    primary_axis = _infer_primary_axis_from_requirements(requirements) or _dominant_axis_from_extents(extents)
    sketch_plane = _infer_primary_profile_sketch_plane(action_history)
    if sketch_plane is None:
        sketch_plane = _preferred_profile_plane_for_requirements(requirements)
    vertical_axis = _profile_vertical_axis_for_sketch_plane(sketch_plane)
    if vertical_axis is None or vertical_axis == primary_axis:
        return []
    target_sign = "positive" if flange_side == "top" else "negative"
    primary_span = abs(float(extents[f"{primary_axis}_max"]) - float(extents[f"{primary_axis}_min"]))
    if primary_span <= 1e-6:
        return []
    orthogonal_axes = [
        axis for axis in ("X", "Y", "Z") if axis not in {primary_axis, vertical_axis}
    ]
    secondary_axis = orthogonal_axes[0] if orthogonal_axes else None
    threshold = max(primary_span * 0.8, primary_span - max(2.0, primary_span * 0.1))
    candidates: list[tuple[float, float, str]] = []
    for record in face_records:
        if str(record.get("geom_type", "")).strip().upper() != "PLANE":
            continue
        axis, sign = _face_record_axis_signature(record)
        if axis != vertical_axis or sign != target_sign:
            continue
        bbox = record.get("bbox")
        if not isinstance(bbox, dict):
            continue
        face_ref = record.get("face_ref")
        if not isinstance(face_ref, str) or not face_ref.strip():
            continue
        face_primary_span = _bbox_axis_length(bbox, primary_axis)
        if face_primary_span + 1e-6 < threshold:
            continue
        secondary_span = (
            _bbox_axis_length(bbox, secondary_axis) if secondary_axis is not None else 0.0
        )
        area_value = record.get("area")
        area = float(area_value) if isinstance(area_value, (int, float)) else 0.0
        candidates.append((secondary_span, area, face_ref.strip()))
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    return [ref for _secondary, _area, ref in candidates]


def _requirement_flange_surface_side(
    requirements: dict[str, Any] | None,
) -> str | None:
    semantics = analyze_requirement_semantics(requirements)
    text = semantics.normalized_text
    if not text or "flange" not in text or "length direction" not in text:
        return None
    segments = re.split(r"(?<=[.!?])\s+", text)
    for segment in segments:
        normalized = str(segment).strip().lower()
        if "flange" not in normalized:
            continue
        if "surface" not in normalized and "face" not in normalized:
            continue
        if "top" in normalized or "upper" in normalized:
            return "top"
        if "bottom" in normalized or "lower" in normalized:
            return "bottom"
    return None


def _query_topology_face_records(
    query_topology: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not isinstance(query_topology, dict):
        return []
    topology_window = query_topology.get("topology_window")
    if isinstance(topology_window, dict) and isinstance(
        topology_window.get("faces"), list
    ):
        return [
            item for item in topology_window.get("faces", []) if isinstance(item, dict)
        ]
    topology_index = query_topology.get("topology_index")
    if isinstance(topology_index, dict) and isinstance(topology_index.get("faces"), list):
        return [
            item for item in topology_index.get("faces", []) if isinstance(item, dict)
        ]
    return []


def _query_topology_face_extents(
    face_records: list[dict[str, Any]],
) -> dict[str, float] | None:
    mins: dict[str, list[float]] = {"X": [], "Y": [], "Z": []}
    maxs: dict[str, list[float]] = {"X": [], "Y": [], "Z": []}
    for record in face_records:
        bbox = record.get("bbox")
        if not isinstance(bbox, dict):
            continue
        for axis, min_key, max_key in (
            ("X", "xmin", "xmax"),
            ("Y", "ymin", "ymax"),
            ("Z", "zmin", "zmax"),
        ):
            min_value = bbox.get(min_key)
            max_value = bbox.get(max_key)
            if isinstance(min_value, (int, float)) and isinstance(max_value, (int, float)):
                mins[axis].append(float(min_value))
                maxs[axis].append(float(max_value))
    if not mins["X"] or not mins["Y"] or not mins["Z"]:
        return None
    return {
        "X_min": min(mins["X"]),
        "X_max": max(maxs["X"]),
        "Y_min": min(mins["Y"]),
        "Y_max": max(maxs["Y"]),
        "Z_min": min(mins["Z"]),
        "Z_max": max(maxs["Z"]),
    }


def _dominant_axis_from_extents(extents: dict[str, float]) -> str | None:
    spans = {
        axis: abs(float(extents[f"{axis}_max"]) - float(extents[f"{axis}_min"]))
        for axis in ("X", "Y", "Z")
    }
    return max(spans, key=spans.get) if spans else None


def _face_record_axis_signature(
    face_record: dict[str, Any],
) -> tuple[str | None, str | None]:
    normal = face_record.get("normal")
    if not isinstance(normal, list) or len(normal) < 3:
        return None, None
    try:
        components = [abs(float(normal[0])), abs(float(normal[1])), abs(float(normal[2]))]
        axis_index = components.index(max(components))
        axis = ("X", "Y", "Z")[axis_index]
        sign = "positive" if float(normal[axis_index]) >= 0.0 else "negative"
        return axis, sign
    except Exception:
        return None, None


def _bbox_axis_length(bbox: dict[str, Any], axis: str | None) -> float:
    if axis == "X":
        value = bbox.get("xlen")
    elif axis == "Y":
        value = bbox.get("ylen")
    elif axis == "Z":
        value = bbox.get("zlen")
    else:
        return 0.0
    return abs(float(value)) if isinstance(value, (int, float)) else 0.0


def _infer_primary_profile_sketch_plane(
    action_history: list[dict[str, Any]] | None,
) -> str | None:
    if not isinstance(action_history, list):
        return None
    latest_plane: str | None = None
    for action in action_history:
        if not isinstance(action, dict):
            continue
        action_type = str(action.get("action_type", "")).strip().lower()
        if action_type in {"extrude", "revolve", "loft", "sweep"}:
            return latest_plane
        if action_type != "create_sketch":
            continue
        params = action.get("action_params")
        action_params = params if isinstance(params, dict) else {}
        plane_raw = action_params.get("plane")
        if isinstance(plane_raw, str) and plane_raw.strip():
            latest_plane = plane_raw.strip().upper()
    return latest_plane


def _preferred_profile_plane_for_requirements(
    requirements: dict[str, Any] | None,
) -> str | None:
    axis = _infer_primary_axis_from_requirements(requirements)
    if axis == "X":
        return "YZ"
    if axis == "Y":
        return "XZ"
    if axis == "Z":
        return "XY"
    return None


def _infer_primary_axis_from_requirements(
    requirements: dict[str, Any] | None,
) -> str | None:
    semantics = analyze_requirement_semantics(requirements)
    text = semantics.normalized_text
    if not text:
        return None
    axis_match = re.search(
        r"along[^a-z0-9]{0,12}(?:the[^a-z0-9]{0,6})?([xyz])(?:[^a-z0-9]{0,6}(?:axis|-axis))?",
        text,
    )
    if axis_match is None:
        return None
    axis = axis_match.group(1).upper()
    if axis in {"X", "Y", "Z"}:
        return axis
    return None


def _profile_vertical_axis_for_sketch_plane(plane: str | None) -> str | None:
    if not isinstance(plane, str) or not plane.strip():
        return None
    plane_name = plane.strip().upper()
    if plane_name == "XY":
        return "Y"
    if plane_name in {"XZ", "YZ"}:
        return "Z"
    return None
