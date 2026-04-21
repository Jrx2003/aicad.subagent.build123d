from __future__ import annotations

from typing import Any

from sub_agent_runtime.turn_state import (
    RunState,
    ToolCallRecord,
    ToolCategory,
    TurnRecord,
)


_SKETCH_WINDOW_CONTINUATION_ACTIONS = {
    "create_sketch",
    "add_circle",
    "add_rectangle",
    "add_polygon",
    "add_slot",
    "add_ellipse",
    "add_path",
}


def _tool_call_apply_action_type(tool_call: ToolCallRecord) -> str | None:
    if tool_call.category != ToolCategory.WRITE or tool_call.name != "apply_cad_action":
        return None
    action_type = tool_call.arguments.get("action_type")
    if isinstance(action_type, str) and action_type.strip():
        return action_type.strip().lower()
    return None


def _latest_apply_action_type_from_turn(turn: TurnRecord | None) -> str | None:
    if turn is None:
        return None
    for tool_call in reversed(turn.tool_calls):
        action_type = _tool_call_apply_action_type(tool_call)
        if action_type:
            return action_type
    return None


def _turn_has_open_sketch_window_after_successful_apply(turn: TurnRecord | None) -> bool:
    action_type = _latest_apply_action_type_from_turn(turn)
    return bool(action_type and action_type in _SKETCH_WINDOW_CONTINUATION_ACTIONS)


def _latest_successful_apply_action_type_with_open_sketch_window(
    run_state: RunState,
) -> str | None:
    from sub_agent_runtime.orchestration.policy.shared import _has_tool_turn_since_round

    latest_successful_write_turn = run_state.latest_successful_write_turn
    if (
        latest_successful_write_turn is None
        or latest_successful_write_turn.write_tool_name != "apply_cad_action"
    ):
        return None
    action_type = _latest_apply_action_type_from_turn(latest_successful_write_turn)
    if action_type not in _SKETCH_WINDOW_CONTINUATION_ACTIONS:
        return None
    if _has_tool_turn_since_round(
        run_state,
        after_round=latest_successful_write_turn.round_no,
        tool_names={"validate_requirement"},
    ):
        return None
    return action_type


def _latest_successful_tool_payload(
    run_state: RunState,
    *,
    tool_name: str,
) -> dict[str, Any] | None:
    payload = run_state.evidence.latest_by_tool.get(tool_name)
    if isinstance(payload, dict):
        return payload
    for turn in reversed(run_state.turns):
        for result in reversed(turn.tool_results):
            if result.name == tool_name and result.success and isinstance(result.payload, dict):
                return result.payload
    return None


def _open_sketch_window_requires_code_escape(
    *,
    run_state: RunState,
    max_rounds: int,
) -> bool:
    latest_action_type = _latest_successful_apply_action_type_with_open_sketch_window(run_state)
    if latest_action_type is None:
        return False
    remaining_rounds = max(max_rounds - len(run_state.turns), 0)
    if remaining_rounds <= 0:
        return True
    query_sketch_payload = _latest_successful_tool_payload(
        run_state,
        tool_name="query_sketch",
    )
    sketch_state = (
        query_sketch_payload.get("sketch_state")
        if isinstance(query_sketch_payload, dict)
        else None
    )
    profile_refs = (
        sketch_state.get("profile_refs")
        if isinstance(sketch_state, dict)
        and isinstance(sketch_state.get("profile_refs"), list)
        else []
    )
    path_refs = (
        sketch_state.get("path_refs")
        if isinstance(sketch_state, dict)
        and isinstance(sketch_state.get("path_refs"), list)
        else []
    )
    min_write_steps_remaining = 0
    if latest_action_type == "create_sketch":
        min_write_steps_remaining = 2
    elif profile_refs:
        min_write_steps_remaining = 1
    elif path_refs:
        min_write_steps_remaining = 2
    if min_write_steps_remaining <= 0:
        return False
    if latest_action_type == "create_sketch" and not profile_refs and not path_refs:
        # An empty sketch window still needs at least one geometry-building write plus the
        # materialization write. When only two rounds remain, the bounded local lane is already
        # too tight to recover reliably, so reopen the whole-part escape instead of trapping the
        # model inside a sketch-only tail.
        return remaining_rounds <= min_write_steps_remaining
    return remaining_rounds < min_write_steps_remaining


def _preferred_sketch_window_tools(
    action_type: str,
    *,
    all_tool_names: list[str],
) -> list[str]:
    preferred_order = ["apply_cad_action", "query_sketch"]
    return [name for name in preferred_order if name in all_tool_names]


def _open_sketch_window_requires_apply_write_first(run_state: RunState) -> bool:
    latest_action_type = _latest_successful_apply_action_type_with_open_sketch_window(run_state)
    if latest_action_type != "create_sketch":
        return False
    query_sketch_payload = _latest_successful_tool_payload(
        run_state,
        tool_name="query_sketch",
    )
    sketch_state = (
        query_sketch_payload.get("sketch_state")
        if isinstance(query_sketch_payload, dict)
        else None
    )
    profile_refs = (
        sketch_state.get("profile_refs")
        if isinstance(sketch_state, dict)
        and isinstance(sketch_state.get("profile_refs"), list)
        else []
    )
    path_refs = (
        sketch_state.get("path_refs")
        if isinstance(sketch_state, dict)
        and isinstance(sketch_state.get("path_refs"), list)
        else []
    )
    return not profile_refs and not path_refs


def _feature_probe_recommends_local_finish(probe: dict[str, Any]) -> bool:
    if not isinstance(probe, dict):
        return False
    family_id = str(probe.get("family") or "").strip()
    recommended_next_tools = {
        str(item or "").strip().lower()
        for item in (probe.get("recommended_next_tools") or [])
        if str(item or "").strip()
    }
    if {"query_topology", "apply_cad_action"}.issubset(recommended_next_tools):
        return True
    return family_id == "named_face_local_edit" and "query_topology" in recommended_next_tools


def _latest_feature_probes_recommend_local_finish(run_state: RunState) -> bool:
    for turn in reversed(run_state.turns):
        for result in reversed(turn.tool_results):
            if result.name != "query_feature_probes" or not result.success:
                continue
            payload = result.payload if isinstance(result.payload, dict) else {}
            for probe in payload.get("probes") or []:
                if _feature_probe_recommends_local_finish(
                    probe if isinstance(probe, dict) else {}
                ):
                    return True
            return False
    return False


def _latest_feature_probes_recommend_apply_local_finish(run_state: RunState) -> bool:
    return bool(_latest_feature_probe_apply_local_finish_families(run_state))


def _latest_feature_probe_apply_local_finish_families(run_state: RunState) -> set[str]:
    families: set[str] = set()
    for turn in reversed(run_state.turns):
        for result in reversed(turn.tool_results):
            if result.name != "query_feature_probes" or not result.success:
                continue
            payload = result.payload if isinstance(result.payload, dict) else {}
            for probe in payload.get("probes") or []:
                if not isinstance(probe, dict):
                    continue
                family_id = str(probe.get("family") or probe.get("family_id") or "").strip()
                recommended_next_tools = {
                    str(item or "").strip().lower()
                    for item in (probe.get("recommended_next_tools") or [])
                    if str(item or "").strip()
                }
                if "apply_cad_action" in recommended_next_tools and family_id:
                    families.add(family_id)
            return families
    return families


def _successful_local_finish_semantic_refresh_needs_validation(
    run_state: RunState,
    *,
    write_round: int,
    all_tool_names: list[str],
) -> bool:
    from sub_agent_runtime.orchestration.policy import semantic_refresh, validation
    from sub_agent_runtime.orchestration.policy.shared import _has_tool_turn_since_round

    if "validate_requirement" not in all_tool_names:
        return False
    if validation._latest_validation_is_fresh_for_write(
        run_state,
        write_round=write_round,
    ):
        return False
    if _has_tool_turn_since_round(
        run_state,
        after_round=write_round,
        tool_names={"validate_requirement"},
    ):
        return False
    if not semantic_refresh._has_successful_semantic_refresh_since_round(
        run_state,
        after_round=write_round,
    ):
        return False
    return _latest_feature_probes_recommend_apply_local_finish(
        run_state
    ) or _latest_feature_probes_recommend_local_finish(run_state)


def _local_finish_validation_evidence_refresh_tools_for_turn(
    run_state: RunState,
    *,
    all_tool_names: list[str],
) -> list[str]:
    from sub_agent_runtime.orchestration.policy import semantic_refresh, validation

    read_refresh_tool_set = {
        "query_topology",
        "query_feature_probes",
        "query_geometry",
        "query_kernel_state",
        "execute_build123d_probe",
    }
    allowed_tool_names: list[str] = []

    def _append(raw_tool_name: Any) -> None:
        tool_name = str(raw_tool_name or "").strip()
        if (
            not tool_name
            or tool_name not in all_tool_names
            or tool_name not in read_refresh_tool_set
            or tool_name in allowed_tool_names
        ):
            return
        allowed_tool_names.append(tool_name)

    for tool_name in semantic_refresh._preferred_validation_assessment_tools_for_turn(
        run_state,
        all_tool_names=all_tool_names,
    ):
        _append(tool_name)

    latest_validation = (
        run_state.latest_validation if isinstance(run_state.latest_validation, dict) else {}
    )
    normalized_hints = {
        str(item or "").strip().lower()
        for field_name in ("repair_hints", "decision_hints")
        for item in (latest_validation.get(field_name) or [])
        if isinstance(item, str) and str(item).strip()
    }
    if any(
        token in hint
        for hint in normalized_hints
        for token in (
            "geometry/topology evidence",
            "inspect count or placement",
            "inspect_more_evidence",
        )
    ):
        _append("query_geometry")
        _append("query_topology")
        _append("query_feature_probes")
    if not allowed_tool_names and validation._latest_validation_prefers_topology_refresh(
        latest_validation
    ):
        _append("query_topology")
        _append("query_feature_probes")
    _append("query_kernel_state")
    return allowed_tool_names


def _local_finish_is_actionable_after_semantic_refresh(
    run_state: RunState,
    *,
    write_round: int,
    all_tool_names: list[str],
) -> bool:
    from sub_agent_runtime.orchestration.policy.shared import (
        _has_successful_tool_result_since_round,
    )

    if "apply_cad_action" not in all_tool_names or "query_topology" not in all_tool_names:
        return False
    if _local_finish_should_defer_to_actionable_rebuild_patch(run_state):
        return False
    if not _latest_feature_probes_recommend_local_finish(run_state):
        return False
    return _has_successful_tool_result_since_round(
        run_state,
        after_round=write_round,
        tool_names={"query_topology"},
    )


def _actionable_patch_family_ids(
    run_state: RunState,
    patch: dict[str, Any],
) -> set[str]:
    families: set[str] = set()
    feature_graph = run_state.feature_graph
    feature_instances = (
        getattr(feature_graph, "feature_instances", {})
        if feature_graph is not None
        else {}
    )

    direct_family_id = str(patch.get("family_id") or "").strip()
    if direct_family_id:
        families.add(direct_family_id)

    feature_instance_id = str(patch.get("feature_instance_id") or "").strip()
    if feature_instance_id:
        instance = feature_instances.get(feature_instance_id)
        family_id = str(getattr(instance, "family_id", "") or "").strip()
        if family_id:
            families.add(family_id)

    for instance_id in patch.get("feature_instance_ids") or []:
        normalized_instance_id = str(instance_id or "").strip()
        if not normalized_instance_id:
            continue
        instance = feature_instances.get(normalized_instance_id)
        family_id = str(getattr(instance, "family_id", "") or "").strip()
        if family_id:
            families.add(family_id)
    return families


def _has_actionable_topology_targeting_since_round(
    run_state: RunState,
    *,
    after_round: int,
) -> bool:
    for turn in reversed(run_state.turns):
        if turn.round_no <= after_round:
            continue
        for result in reversed(turn.tool_results):
            if result.name != "query_topology" or not result.success:
                continue
            payload = result.payload if isinstance(result.payload, dict) else {}
            matched_ref_ids = [
                str(ref_id).strip()
                for ref_id in (payload.get("matched_ref_ids") or [])
                if str(ref_id).strip()
            ]
            if matched_ref_ids:
                return True
            matched_ref_id_count = payload.get("matched_ref_id_count")
            if isinstance(matched_ref_id_count, int) and matched_ref_id_count > 0:
                return True
            for candidate_set in payload.get("candidate_sets") or []:
                if not isinstance(candidate_set, dict):
                    continue
                candidate_ref_ids = [
                    str(ref_id).strip()
                    for ref_id in (candidate_set.get("ref_ids") or [])
                    if str(ref_id).strip()
                ]
                if candidate_ref_ids:
                    return True
            return False
    return False


def _local_finish_should_force_apply_after_topology_targeting(
    run_state: RunState,
    *,
    write_round: int,
    all_tool_names: list[str],
) -> bool:
    if "apply_cad_action" not in all_tool_names:
        return False
    if _local_finish_should_defer_to_actionable_rebuild_patch(run_state):
        return False
    if not _latest_feature_probes_recommend_apply_local_finish(run_state):
        return False
    return _has_actionable_topology_targeting_since_round(
        run_state,
        after_round=write_round,
    )


def _local_finish_escape_is_available_after_topology_targeting(
    run_state: RunState,
    *,
    write_round: int,
    all_tool_names: list[str],
) -> bool:
    if "apply_cad_action" not in all_tool_names:
        return False
    if not _latest_feature_probes_recommend_apply_local_finish(run_state):
        return False
    return _has_actionable_topology_targeting_since_round(
        run_state,
        after_round=write_round,
    )


def _local_finish_should_defer_to_actionable_rebuild_patch(
    run_state: RunState,
) -> bool:
    from sub_agent_runtime.orchestration.policy.code_repair import (
        _latest_actionable_kernel_patch,
    )

    patch = _latest_actionable_kernel_patch(run_state)
    if not isinstance(patch, dict):
        return False
    repair_mode = str(patch.get("repair_mode") or "").strip()
    if repair_mode not in {"whole_part_rebuild", "subtree_rebuild"}:
        return False
    apply_local_finish_families = _latest_feature_probe_apply_local_finish_families(
        run_state
    )
    if apply_local_finish_families:
        patch_family_ids = _actionable_patch_family_ids(run_state, patch)
        if patch_family_ids and apply_local_finish_families.isdisjoint(patch_family_ids):
            return False
    return True


def _local_finish_contract_failure_should_retry_after_topology_refresh(
    run_state: RunState,
    *,
    previous_tool_failure_summary: dict[str, Any] | None,
    all_tool_names: list[str],
) -> bool:
    from sub_agent_runtime.orchestration.policy.shared import (
        _has_successful_tool_result_since_round,
        _has_tool_turn_since_round,
    )

    if "apply_cad_action" not in all_tool_names:
        return False
    if not isinstance(previous_tool_failure_summary, dict):
        return False
    latest_write_turn = run_state.latest_write_turn
    if latest_write_turn is None or latest_write_turn.write_tool_name != "apply_cad_action":
        return False
    failure_tool = str(previous_tool_failure_summary.get("tool") or "").strip()
    failure_kind = str(
        previous_tool_failure_summary.get("effective_failure_kind")
        or previous_tool_failure_summary.get("failure_kind")
        or ""
    ).strip()
    if failure_tool != "apply_cad_action" or failure_kind != "apply_cad_action_contract_failure":
        return False
    if not (
        _latest_feature_probes_recommend_apply_local_finish(run_state)
        or _latest_feature_probes_recommend_local_finish(run_state)
    ):
        return False
    if not _has_successful_tool_result_since_round(
        run_state,
        after_round=latest_write_turn.round_no,
        tool_names={"query_topology"},
    ):
        return False
    if not _has_actionable_topology_targeting_since_round(
        run_state,
        after_round=latest_write_turn.round_no,
    ):
        return False
    return not _has_tool_turn_since_round(
        run_state,
        after_round=latest_write_turn.round_no,
        tool_names={"apply_cad_action"},
    )


def _latest_topology_evidence_is_actionable(run_state: RunState) -> bool:
    topology_payload = run_state.evidence.latest_by_tool.get("query_topology")
    if not isinstance(topology_payload, dict):
        return False
    matched_ref_ids = [
        str(ref_id).strip()
        for ref_id in (topology_payload.get("matched_ref_ids") or [])
        if str(ref_id).strip()
    ]
    if matched_ref_ids:
        return True
    matched_ref_id_count = topology_payload.get("matched_ref_id_count")
    if isinstance(matched_ref_id_count, int) and matched_ref_id_count > 0:
        return True
    for candidate_set in topology_payload.get("candidate_sets") or []:
        if not isinstance(candidate_set, dict):
            continue
        candidate_ref_ids = [
            str(ref_id).strip()
            for ref_id in (candidate_set.get("ref_ids") or [])
            if str(ref_id).strip()
        ]
        if candidate_ref_ids:
            return True
    return False


def _local_finish_validation_evidence_gap_needs_read_refresh(
    run_state: RunState,
    *,
    write_round: int,
    all_tool_names: list[str],
) -> bool:
    from sub_agent_runtime.orchestration.policy.semantic_refresh import (
        _latest_validation_has_budget_skipped_hint,
    )
    from sub_agent_runtime.orchestration.policy.shared import _has_tool_turn_since_round
    from sub_agent_runtime.orchestration.policy.validation import (
        _is_successful_validation,
        _latest_validation_is_fresh_for_write,
        _latest_validation_round,
        _validation_has_evidence_gap,
        _validation_requests_localized_evidence_refresh,
    )

    if not any(
        name in all_tool_names
        for name in {
            "query_topology",
            "query_feature_probes",
            "query_geometry",
            "query_kernel_state",
            "execute_build123d_probe",
        }
    ):
        return False
    latest_validation = (
        run_state.latest_validation if isinstance(run_state.latest_validation, dict) else {}
    )
    if not _latest_validation_is_fresh_for_write(run_state, write_round=write_round):
        return False
    blockers = latest_validation.get("blockers")
    if isinstance(blockers, list) and blockers:
        return False
    if _is_successful_validation(latest_validation):
        return False
    if not _validation_has_evidence_gap(latest_validation):
        return False
    if not (
        _validation_requests_localized_evidence_refresh(latest_validation)
        or _latest_validation_has_budget_skipped_hint(latest_validation)
    ):
        return False
    latest_validation_round = _latest_validation_round(run_state)
    if latest_validation_round <= write_round:
        return False
    return not _has_tool_turn_since_round(
        run_state,
        after_round=latest_validation_round,
        tool_names={
            "query_topology",
            "query_feature_probes",
            "query_geometry",
            "query_kernel_state",
            "execute_build123d_probe",
        },
    )


def _local_finish_validation_evidence_gap_closure_tools_for_turn(
    run_state: RunState,
    *,
    all_tool_names: list[str],
) -> tuple[list[str], list[str]]:
    refresh_tools = _local_finish_validation_evidence_refresh_tools_for_turn(
        run_state,
        all_tool_names=all_tool_names,
    )
    allowed_tool_names = list(refresh_tools)

    for tool_name in ("validate_requirement", "finish_run"):
        if tool_name in all_tool_names and tool_name not in allowed_tool_names:
            allowed_tool_names.append(tool_name)

    preferred_tool_names: list[str] = []
    if refresh_tools:
        preferred_tool_names.append(refresh_tools[0])
    for tool_name in ("validate_requirement", "finish_run"):
        if tool_name in allowed_tool_names and tool_name not in preferred_tool_names:
            preferred_tool_names.append(tool_name)
    for tool_name in refresh_tools[1:]:
        if tool_name not in preferred_tool_names:
            preferred_tool_names.append(tool_name)
    return allowed_tool_names, preferred_tool_names


def _local_finish_contract_failure_should_retry_with_existing_topology_refs(
    run_state: RunState,
    *,
    previous_tool_failure_summary: dict[str, Any] | None,
    all_tool_names: list[str],
) -> bool:
    from sub_agent_runtime.orchestration.policy.shared import _has_tool_turn_since_round

    if "apply_cad_action" not in all_tool_names:
        return False
    if not isinstance(previous_tool_failure_summary, dict):
        return False
    latest_write_turn = run_state.latest_write_turn
    if latest_write_turn is None or latest_write_turn.write_tool_name != "apply_cad_action":
        return False
    failure_tool = str(previous_tool_failure_summary.get("tool") or "").strip()
    failure_kind = str(
        previous_tool_failure_summary.get("effective_failure_kind")
        or previous_tool_failure_summary.get("failure_kind")
        or ""
    ).strip()
    if failure_tool != "apply_cad_action" or failure_kind != "apply_cad_action_contract_failure":
        return False
    if not (
        _latest_feature_probes_recommend_apply_local_finish(run_state)
        or _latest_feature_probes_recommend_local_finish(run_state)
    ):
        return False
    topology_round = run_state.evidence.rounds_by_tool.get("query_topology")
    if not isinstance(topology_round, int) or topology_round <= 0:
        return False
    if topology_round >= latest_write_turn.round_no:
        return False
    if not _latest_topology_evidence_is_actionable(run_state):
        return False
    return not _has_tool_turn_since_round(
        run_state,
        after_round=latest_write_turn.round_no,
        tool_names={"apply_cad_action", "query_topology"},
    )
