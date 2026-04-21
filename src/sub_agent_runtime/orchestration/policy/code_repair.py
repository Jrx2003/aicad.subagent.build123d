from __future__ import annotations

from collections import Counter
from typing import Any

from common.blocker_taxonomy import (
    taxonomy_family_ids_from_validation_payload,
    taxonomy_repair_lanes_from_validation_payload,
)
from sub_agent_runtime.prompting.skill_assembly import (
    recommended_feature_probe_families,
)
from sub_agent_runtime.semantic_kernel.repair_packets import (
    describe_runtime_repair_packet_support,
    select_preferred_repair_packet,
    supports_runtime_repair_packet,
)
from sub_agent_runtime.turn_state import (
    RunState,
    ToolCategory,
    ToolExecutionEvent,
    TurnToolPolicy,
)


_COARSE_KERNEL_PATCH_PARAMETER_KEYS = {
    "bbox",
    "bbox_min",
    "bbox_max",
    "bbox_min_span",
    "bbox_max_span",
    "anchor_summary",
}

_LOCAL_TOPOLOGY_SENSITIVE_FAMILIES = {
    "named_face_local_edit",
    "slots",
    "explicit_anchor_hole",
    "half_shell",
    "nested_hollow_section",
}

_LOCAL_TOPOLOGY_SENSITIVE_BLOCKER_IDS = {
    "feature_target_face_edit",
    "feature_target_face_subtractive_merge",
    "feature_notch_or_profile_cut",
    "feature_hole",
    "feature_counterbore",
    "feature_countersink",
}


def _runtime_repair_packet_observability_summary(run_state: RunState) -> dict[str, Any]:
    repair_packet_fallback_reasons: Counter[str] = Counter()
    repair_packet_exposed_count = 0
    repair_packet_supported_count = 0
    repair_packet_compile_success_count = 0
    repair_packet_compile_failure_count = 0
    repair_packet_fallback_count = 0
    execute_build123d_preflight_fail_count = 0
    for event in run_state.tool_execution_events:
        if event.tool_name == "execute_repair_packet":
            if event.phase == "repair_packet_exposed":
                repair_packet_exposed_count += 1
            elif event.phase == "repair_packet_supported":
                repair_packet_supported_count += 1
            elif event.phase == "repair_packet_compile_succeeded":
                repair_packet_compile_success_count += 1
            elif event.phase == "repair_packet_compile_failed":
                repair_packet_compile_failure_count += 1
            elif event.phase == "repair_packet_fallback":
                repair_packet_fallback_count += 1
                reason = str(event.detail.get("reason") or "").strip() or "unknown"
                repair_packet_fallback_reasons[reason] += 1
        elif (
            event.tool_name == "execute_build123d"
            and event.phase == "build123d_preflight_failed"
        ):
            execute_build123d_preflight_fail_count += 1
    return {
        "repair_packet_exposed_count": repair_packet_exposed_count,
        "repair_packet_supported_count": repair_packet_supported_count,
        "repair_packet_compile_success_count": repair_packet_compile_success_count,
        "repair_packet_compile_failure_count": repair_packet_compile_failure_count,
        "repair_packet_fallback_count": repair_packet_fallback_count,
        "repair_packet_fallback_reasons": dict(repair_packet_fallback_reasons),
        "execute_build123d_preflight_fail_count": execute_build123d_preflight_fail_count,
    }


def _infer_runtime_failure_cluster(run_state: RunState) -> str | None:
    from sub_agent_runtime.orchestration.policy.validation import _is_successful_validation

    last_error = str(run_state.previous_error or "").strip().lower()
    latest_validation = (
        run_state.latest_validation if isinstance(run_state.latest_validation, dict) else {}
    )
    if _is_successful_validation(latest_validation):
        return None
    blockers = latest_validation.get("blockers")
    blocker_list = (
        [item for item in blockers if isinstance(item, str)]
        if isinstance(blockers, list)
        else []
    )
    taxonomy_families = taxonomy_family_ids_from_validation_payload(latest_validation)
    taxonomy_repair_lanes = taxonomy_repair_lanes_from_validation_payload(
        latest_validation
    )
    if any(
        token in last_error
        for token in ("no step", "model.step", "step file", "step export")
    ):
        return "missing_step_gap"
    if run_state.feature_probe_count or run_state.probe_code_count:
        if taxonomy_families:
            return "code_path_family_gap"
        if any("annular" in item or "revolve" in item for item in blocker_list):
            return "code_path_family_gap"
    if run_state.inspection_only_rounds >= max(2, len(run_state.turns) // 2):
        return "read_stall_gap"
    if taxonomy_repair_lanes and set(taxonomy_repair_lanes) == {"probe_first"}:
        return "tool_gap"
    if blocker_list:
        return "tool_gap"
    if last_error:
        return "runtime_gap"
    return None


def _latest_failed_code_sequence_is_artifactless(run_state: RunState) -> bool:
    from sub_agent_runtime.orchestration.policy.validation import (
        _payload_has_positive_session_backed_solid,
    )

    payload = (
        run_state.latest_write_payload
        if isinstance(run_state.latest_write_payload, dict)
        else {}
    )
    if bool(run_state.latest_step_file):
        return False
    if _payload_has_step_artifact(payload):
        return False
    return not _payload_has_positive_session_backed_solid(payload)


def _payload_has_step_artifact(payload: dict[str, Any]) -> bool:
    step_file = str(payload.get("step_file") or "").strip()
    if step_file:
        return True
    output_files = payload.get("output_files")
    if isinstance(output_files, list):
        return any(
            isinstance(item, str) and item.lower().endswith(".step")
            for item in output_files
        )
    return False


def _filter_supported_round_tool_names(
    *,
    run_state: RunState,
    tool_names: set[str],
) -> set[str]:
    filtered = set(tool_names)
    if "execute_repair_packet" not in filtered:
        return filtered
    graph = run_state.feature_graph
    if graph is None:
        filtered.discard("execute_repair_packet")
        return filtered
    raw_packets = getattr(graph, "repair_packets", None)
    if not isinstance(raw_packets, dict) or not raw_packets:
        filtered.discard("execute_repair_packet")
        return filtered
    preferred_packet = select_preferred_repair_packet(raw_packets)
    preferred_packet_payload = (
        preferred_packet.to_dict()
        if preferred_packet is not None and hasattr(preferred_packet, "to_dict")
        else None
    )
    if not supports_runtime_repair_packet(preferred_packet_payload):
        filtered.discard("execute_repair_packet")
    return filtered


def _build_repair_packet_round_observability_events(
    *,
    run_state: RunState,
    round_no: int,
    allowed_tool_names: set[str],
) -> list[ToolExecutionEvent]:
    graph = run_state.feature_graph
    if graph is None:
        return []
    raw_packets = getattr(graph, "repair_packets", None)
    if not isinstance(raw_packets, dict) or not raw_packets:
        return []
    preferred_packet = select_preferred_repair_packet(raw_packets)
    if preferred_packet is None or not hasattr(preferred_packet, "to_dict"):
        return []
    packet_payload = preferred_packet.to_dict()
    support = describe_runtime_repair_packet_support(packet_payload)
    detail = {
        "packet_id": str(packet_payload.get("packet_id") or "").strip(),
        "family_id": str(packet_payload.get("family_id") or "").strip(),
        "recipe_id": str(packet_payload.get("recipe_id") or "").strip(),
        "support_reason": str(support.get("support_reason") or "").strip(),
    }
    events = [
        ToolExecutionEvent(
            round_no=round_no,
            tool_name="execute_repair_packet",
            phase="repair_packet_exposed",
            category=ToolCategory.WRITE,
            success=True,
            detail=detail,
        )
    ]
    runtime_supported = bool(support.get("runtime_supported"))
    if runtime_supported:
        events.append(
            ToolExecutionEvent(
                round_no=round_no,
                tool_name="execute_repair_packet",
                phase="repair_packet_supported",
                category=ToolCategory.WRITE,
                success=True,
                detail=detail,
            )
        )
    fallback_tool_names = [
        tool_name
        for tool_name in ("execute_repair_packet", "execute_build123d", "apply_cad_action")
        if tool_name in allowed_tool_names
    ]
    if runtime_supported and "execute_repair_packet" in allowed_tool_names:
        return events
    fallback_reason = (
        "turn_policy_disallowed_execute_repair_packet"
        if runtime_supported
        else str(support.get("support_reason") or "").strip() or "unsupported_recipe"
    )
    events.append(
        ToolExecutionEvent(
            round_no=round_no,
            tool_name="execute_repair_packet",
            phase="repair_packet_fallback",
            category=ToolCategory.WRITE,
            success=False,
            detail={
                **detail,
                "reason": fallback_reason,
                "fallback_tool_names": fallback_tool_names,
            },
        )
    )
    return events


def _blockers_prefer_probe_first_after_code_write(blockers: list[str]) -> bool:
    blocker_set = {item for item in blockers if isinstance(item, str)}
    if not blocker_set:
        return False
    return bool(
        blocker_set.intersection(
            {
                "feature_annular_groove",
                "feature_revolved_groove_setup",
                "feature_revolved_groove_alignment",
                "feature_revolved_groove_result",
                "feature_profile_shape_alignment",
                "feature_pattern",
                "feature_pattern_seed_alignment",
                "feature_hole_position_alignment",
                "feature_local_anchor_alignment",
            }
        )
    )


def _has_semantic_refresh_turn_since_failed_write(
    run_state: RunState,
    *,
    failed_write_round: int,
) -> bool:
    from sub_agent_runtime.orchestration.policy.shared import (
        _SEMANTIC_REFRESH_COMPLETION_TOOL_SET,
        _has_tool_turn_since_round,
    )

    return _has_tool_turn_since_round(
        run_state,
        after_round=failed_write_round,
        tool_names=_SEMANTIC_REFRESH_COMPLETION_TOOL_SET,
    )


def _has_probe_turn_since_failed_write(
    run_state: RunState,
    *,
    failed_write_round: int,
) -> bool:
    from sub_agent_runtime.orchestration.policy.shared import _has_tool_turn_since_round

    return _has_tool_turn_since_round(
        run_state,
        after_round=failed_write_round,
        tool_names={"execute_build123d_probe"},
    )


def _has_successful_probe_turn_since_failed_write(
    run_state: RunState,
    *,
    failed_write_round: int,
) -> bool:
    for turn in run_state.turns:
        if turn.round_no <= failed_write_round:
            continue
        for result in turn.tool_results:
            if result.name == "execute_build123d_probe" and result.success:
                return True
    probe_round = run_state.evidence.rounds_by_tool.get("execute_build123d_probe")
    probe_payload = run_state.evidence.latest_by_tool.get("execute_build123d_probe")
    if (
        isinstance(probe_round, int)
        and probe_round > failed_write_round
        and isinstance(probe_payload, dict)
        and bool(probe_payload.get("success"))
    ):
        return True
    return False


def _has_successful_non_persisted_probe_turn_since_failed_write(
    run_state: RunState,
    *,
    failed_write_round: int,
) -> bool:
    for turn in run_state.turns:
        if turn.round_no <= failed_write_round:
            continue
        for result in turn.tool_results:
            if result.name != "execute_build123d_probe" or not result.success:
                continue
            payload = result.payload if isinstance(result.payload, dict) else {}
            if not bool(payload.get("session_state_persisted", False)):
                return True
    probe_round = run_state.evidence.rounds_by_tool.get("execute_build123d_probe")
    probe_payload = run_state.evidence.latest_by_tool.get("execute_build123d_probe")
    return (
        isinstance(probe_round, int)
        and probe_round > failed_write_round
        and isinstance(probe_payload, dict)
        and bool(probe_payload.get("success"))
        and not bool(probe_payload.get("session_state_persisted", False))
    )


def _has_actionable_probe_turn_since_failed_write(
    run_state: RunState,
    *,
    failed_write_round: int,
) -> bool:
    for turn in run_state.turns:
        if turn.round_no <= failed_write_round:
            continue
        for result in turn.tool_results:
            if result.name != "execute_build123d_probe" or not result.success:
                continue
            payload = result.payload if isinstance(result.payload, dict) else {}
            probe_summary = (
                payload.get("probe_summary")
                if isinstance(payload.get("probe_summary"), dict)
                else {}
            )
            if bool(probe_summary.get("actionable")):
                return True
    return False


def _latest_actionable_kernel_patch(
    run_state: RunState,
) -> dict[str, Any] | None:
    graph = run_state.feature_graph
    if graph is None:
        return None
    raw_patches = getattr(graph, "repair_patches", None)
    if not isinstance(raw_patches, dict) or not raw_patches:
        raw_patches = {}
    feature_instances = getattr(graph, "feature_instances", {})
    if not isinstance(feature_instances, dict):
        feature_instances = {}

    blocked_family_ids = {
        str(getattr(feature_instance, "family_id", "") or "").strip()
        for feature_instance in feature_instances.values()
        if str(getattr(feature_instance, "status", "") or "").strip() == "blocked"
        and str(getattr(feature_instance, "family_id", "") or "").strip()
    }

    best_patch: dict[str, Any] | None = None
    for patch in reversed(list(raw_patches.values())):
        if bool(getattr(patch, "stale", False)):
            continue
        repair_mode = str(getattr(patch, "repair_mode", "") or "").strip()
        feature_instance_ids = [
            str(item).strip()
            for item in (getattr(patch, "feature_instance_ids", None) or [])
            if isinstance(item, str) and str(item).strip()
        ]
        anchor_keys = [
            str(item).strip()
            for item in (getattr(patch, "anchor_keys", None) or [])
            if isinstance(item, str) and str(item).strip()
        ]
        parameter_keys = [
            str(item).strip()
            for item in (getattr(patch, "parameter_keys", None) or [])
            if isinstance(item, str) and str(item).strip()
        ]
        repair_intent = str(getattr(patch, "repair_intent", "") or "").strip()
        if not repair_mode or not feature_instance_ids:
            continue
        if not (anchor_keys or parameter_keys):
            continue
        families: list[str] = []
        for instance_id in feature_instance_ids:
            feature_instance = feature_instances.get(instance_id)
            family_id = str(getattr(feature_instance, "family_id", "") or "").strip()
            if family_id and family_id not in families:
                families.append(family_id)
        best_patch = {
            "repair_mode": repair_mode,
            "feature_instance_ids": feature_instance_ids,
            "anchor_keys": anchor_keys,
            "parameter_keys": parameter_keys,
            "repair_intent": repair_intent,
            "families": families,
        }
        break

    best_packet: dict[str, Any] | None = None
    raw_packets = getattr(graph, "repair_packets", None)
    if isinstance(raw_packets, dict) and raw_packets:
        packet = select_preferred_repair_packet(raw_packets)
        if packet is not None:
            repair_mode = str(getattr(packet, "repair_mode", "") or "").strip()
            feature_instance_id = str(getattr(packet, "feature_instance_id", "") or "").strip()
            family_id = str(getattr(packet, "family_id", "") or "").strip()
            anchor_keys = [
                str(item).strip()
                for item in (getattr(packet, "anchor_keys", None) or [])
                if isinstance(item, str) and str(item).strip()
            ]
            parameter_keys = [
                str(item).strip()
                for item in (getattr(packet, "parameter_keys", None) or [])
                if isinstance(item, str) and str(item).strip()
            ]
            repair_intent = str(getattr(packet, "repair_intent", "") or "").strip()
            if repair_mode and feature_instance_id and (anchor_keys or parameter_keys):
                families = [family_id] if family_id else []
                packet_dict = (
                    packet.to_dict()
                    if hasattr(packet, "to_dict")
                    else {
                        "family_id": family_id,
                        "feature_instance_id": feature_instance_id,
                        "repair_mode": repair_mode,
                    }
                )
                best_packet = {
                    "repair_mode": repair_mode,
                    "feature_instance_ids": [feature_instance_id],
                    "anchor_keys": anchor_keys,
                    "parameter_keys": parameter_keys,
                    "repair_intent": repair_intent,
                    "families": families,
                    "repair_packet": packet_dict,
                }

    if best_packet is None:
        return best_patch
    if best_patch is None:
        return best_packet

    packet_mode = str(best_packet.get("repair_mode") or "").strip()
    patch_mode = str(best_patch.get("repair_mode") or "").strip()
    if (
        packet_mode == "local_edit"
        and patch_mode in {"whole_part_rebuild", "subtree_rebuild"}
        and (len(blocked_family_ids) > 1 or "general_geometry" in blocked_family_ids)
    ):
        return best_patch
    return best_packet


def _kernel_patch_is_under_grounded_for_local_feature_gap(
    patch: dict[str, Any] | None,
) -> bool:
    if not isinstance(patch, dict):
        return False
    anchor_keys = {
        str(item).strip()
        for item in (patch.get("anchor_keys") or [])
        if isinstance(item, str) and str(item).strip()
    }
    parameter_keys = {
        str(item).strip()
        for item in (patch.get("parameter_keys") or [])
        if isinstance(item, str) and str(item).strip()
    }
    repair_packet = (
        patch.get("repair_packet")
        if isinstance(patch.get("repair_packet"), dict)
        else {}
    )
    target_anchor_summary = (
        repair_packet.get("target_anchor_summary")
        if isinstance(repair_packet.get("target_anchor_summary"), dict)
        else {}
    )
    realized_anchor_summary = (
        repair_packet.get("realized_anchor_summary")
        if isinstance(repair_packet.get("realized_anchor_summary"), dict)
        else {}
    )
    recipe_skeleton = (
        repair_packet.get("recipe_skeleton")
        if isinstance(repair_packet.get("recipe_skeleton"), dict)
        else {}
    )
    grounding_blockers = {
        str(item).strip()
        for item in (
            repair_packet.get("grounding_blockers") or []
            if isinstance(repair_packet, dict)
            else []
        )
        if isinstance(item, str) and str(item).strip()
    }
    center_source_key = str(recipe_skeleton.get("center_source_key") or "").strip().lower()
    needs_external_anchor_grounding = (
        center_source_key.startswith("derive_from_requirement")
        or "validation" in center_source_key
        or bool(target_anchor_summary.get("requires_topology_host_ranking"))
        or "need_topology_host_selection" in grounding_blockers
    )
    coarse_only_parameters = bool(parameter_keys) and parameter_keys.issubset(
        _COARSE_KERNEL_PATCH_PARAMETER_KEYS
    )
    has_anchor_grounding = bool(anchor_keys) or bool(target_anchor_summary) or bool(realized_anchor_summary)
    return not has_anchor_grounding and (
        needs_external_anchor_grounding or coarse_only_parameters
    )


def _kernel_patch_should_yield_semantic_refresh(
    patch: dict[str, Any] | None,
    latest_validation: dict[str, Any] | None,
) -> bool:
    from sub_agent_runtime.orchestration.policy.validation import (
        _validation_requests_localized_evidence_refresh,
    )

    if not _validation_requests_localized_evidence_refresh(latest_validation):
        return False
    return _kernel_patch_is_under_grounded_for_local_feature_gap(patch)


def _kernel_patch_should_yield_feature_probe_assessment(
    patch: dict[str, Any] | None,
    latest_validation: dict[str, Any] | None,
    *,
    run_state: RunState | None = None,
) -> bool:
    if not isinstance(patch, dict) or not isinstance(latest_validation, dict):
        return False
    repair_packet = (
        patch.get("repair_packet")
        if isinstance(patch.get("repair_packet"), dict)
        else None
    )
    if supports_runtime_repair_packet(repair_packet):
        return False
    repair_mode = str(patch.get("repair_mode") or "").strip()
    if repair_mode not in {"whole_part_rebuild", "subtree_rebuild"}:
        return False
    families = {
        str(item).strip()
        for item in (patch.get("families") or [])
        if isinstance(item, str) and str(item).strip()
    }
    if run_state is not None:
        families.update(_blocked_feature_instance_family_ids(run_state))
    if not families.intersection(_LOCAL_TOPOLOGY_SENSITIVE_FAMILIES):
        return False
    blocker_ids = {
        str(item).strip()
        for item in (latest_validation.get("blockers") or [])
        if isinstance(item, str) and str(item).strip()
    }
    if blocker_ids.intersection(_LOCAL_TOPOLOGY_SENSITIVE_BLOCKER_IDS):
        return True
    for item in (latest_validation.get("blocker_taxonomy") or []):
        if not isinstance(item, dict):
            continue
        taxonomy_families = {
            str(family_id).strip()
            for family_id in (item.get("family_ids") or [])
            if isinstance(family_id, str) and str(family_id).strip()
        }
        if taxonomy_families.intersection(_LOCAL_TOPOLOGY_SENSITIVE_FAMILIES):
            return True
        decision_hints = {
            str(hint).strip().lower()
            for hint in (item.get("decision_hints") or [])
            if isinstance(hint, str) and str(hint).strip()
        }
        if decision_hints.intersection({"query_feature_probes", "query_topology"}):
            return True
    return False


def _blocked_feature_instance_family_ids(run_state: RunState | None) -> set[str]:
    if run_state is None or run_state.feature_graph is None:
        return set()
    feature_instances = getattr(run_state.feature_graph, "feature_instances", None)
    if not isinstance(feature_instances, dict):
        return set()
    families: set[str] = set()
    for feature_instance in feature_instances.values():
        status = str(getattr(feature_instance, "status", "") or "").strip()
        if status != "blocked":
            continue
        family_id = str(getattr(feature_instance, "family_id", "") or "").strip()
        if family_id:
            families.add(family_id)
    return families


def _preferred_probe_families_for_turn(run_state: RunState) -> list[str]:
    families: list[str] = []

    def _append(raw_family_id: Any) -> None:
        family_id = str(raw_family_id or "").strip()
        if not family_id or family_id in families:
            return
        families.append(family_id)

    graph = run_state.feature_graph
    if graph is not None:
        feature_probe_node = graph.nodes.get("evidence.feature_probes")
        if feature_probe_node is not None:
            detected_families = (
                feature_probe_node.attributes.get("detected_families")
                if isinstance(feature_probe_node.attributes, dict)
                else None
            )
            if isinstance(detected_families, list):
                for family_id in detected_families:
                    _append(family_id)
            if families:
                return families
        raw_packets = getattr(graph, "repair_packets", None)
        if isinstance(raw_packets, dict):
            for packet in reversed(list(raw_packets.values())):
                if bool(getattr(packet, "stale", False)):
                    continue
                _append(getattr(packet, "family_id", ""))
        raw_patches = getattr(graph, "repair_patches", None)
        feature_instances = (
            getattr(graph, "feature_instances", {})
            if isinstance(getattr(graph, "feature_instances", {}), dict)
            else {}
        )
        if isinstance(raw_patches, dict):
            for patch in reversed(list(raw_patches.values())):
                if bool(getattr(patch, "stale", False)):
                    continue
                for instance_id in getattr(patch, "feature_instance_ids", []) or []:
                    feature_instance = feature_instances.get(instance_id)
                    if feature_instance is None:
                        continue
                    _append(getattr(feature_instance, "family_id", ""))
        for feature_instance in feature_instances.values():
            status = str(getattr(feature_instance, "status", "") or "").strip()
            if status not in {"active", "blocked", "observed"}:
                continue
            _append(getattr(feature_instance, "family_id", ""))
        bindings = getattr(graph, "bindings", None)
        if isinstance(bindings, dict):
            for binding in reversed(list(bindings.values())):
                if bool(getattr(binding, "stale", False)):
                    continue
                for family_id in getattr(binding, "family_ids", []) or []:
                    _append(family_id)
                if families:
                    break
        for node_id in getattr(graph, "active_node_ids", []) or []:
            if not isinstance(node_id, str):
                continue
            if node_id.startswith("feature."):
                _append(node_id.split(".", 1)[1])
            elif node_id.startswith("feature:"):
                _append(node_id.split(":", 1)[1])

    latest_validation = run_state.latest_validation
    for family_id in taxonomy_family_ids_from_validation_payload(latest_validation):
        _append(family_id)

    for family_id in recommended_feature_probe_families(
        requirements=run_state.requirements,
        latest_validation=run_state.latest_validation,
    ):
        _append(family_id)
    if not families:
        families.append("general_geometry")
    return families


def _turn_policy_from_actionable_kernel_patch(
    *,
    round_no: int,
    all_tool_names: list[str],
    policy_id: str,
    reason: str,
    patch: dict[str, Any],
) -> TurnToolPolicy:
    repair_mode = str(patch.get("repair_mode") or "").strip() or "subtree_rebuild"
    families = [
        str(item).strip()
        for item in (patch.get("families") or [])
        if isinstance(item, str) and str(item).strip()
    ]
    repair_packet = patch.get("repair_packet")
    if repair_mode == "local_edit":
        allowed_tool_names = [
            name
            for name in all_tool_names
            if name in {"apply_cad_action", "query_topology"}
        ]
        blocked_tool_names = [
            name for name in all_tool_names if name not in set(allowed_tool_names)
        ]
        return TurnToolPolicy(
            round_no=round_no,
            policy_id=policy_id,
            mode="local_finish",
            reason=reason,
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=["apply_cad_action", "query_topology"],
            preferred_probe_families=families,
        )

    if (
        "execute_repair_packet" in all_tool_names
        and supports_runtime_repair_packet(repair_packet if isinstance(repair_packet, dict) else None)
    ):
        allowed_tool_names = ["execute_repair_packet"]
        blocked_tool_names = [
            name for name in all_tool_names if name not in set(allowed_tool_names)
        ]
        return TurnToolPolicy(
            round_no=round_no,
            policy_id=policy_id,
            mode="code_repair",
            reason=reason,
            allowed_tool_names=allowed_tool_names,
            blocked_tool_names=blocked_tool_names,
            preferred_tool_names=["execute_repair_packet"],
            preferred_probe_families=families,
        )

    allowed_tool_names = [
        name for name in all_tool_names if name == "execute_build123d"
    ]
    blocked_tool_names = [
        name for name in all_tool_names if name not in set(allowed_tool_names)
    ]
    return TurnToolPolicy(
        round_no=round_no,
        policy_id=policy_id,
        mode="code_repair",
        reason=reason,
        allowed_tool_names=allowed_tool_names,
        blocked_tool_names=blocked_tool_names,
        preferred_tool_names=["execute_build123d"],
        preferred_probe_families=families,
    )


def _short_budget_after_topology_refresh_requires_actionable_repair(
    *,
    run_state: RunState,
    write_round: int,
    max_rounds: int,
) -> bool:
    from sub_agent_runtime.orchestration.policy.shared import _has_tool_turn_since_round

    remaining_rounds = max(max_rounds - len(run_state.turns), 0)
    if remaining_rounds > 2:
        return False
    return _has_tool_turn_since_round(
        run_state,
        after_round=write_round,
        tool_names={"query_topology"},
    )


__all__ = [name for name in globals() if not name.startswith("__")]
