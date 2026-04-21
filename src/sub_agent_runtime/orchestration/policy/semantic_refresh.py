from __future__ import annotations

from typing import Any

from common.blocker_taxonomy import classify_blocker_taxonomy_many

from sub_agent_runtime.turn_state import RunState


def _has_recent_semantic_refresh_before_round(
    run_state: RunState,
    *,
    before_round: int,
    lookback_rounds: int = 4,
) -> bool:
    from sub_agent_runtime.orchestration.policy.shared import (
        _SEMANTIC_REFRESH_COMPLETION_TOOL_SET,
    )

    threshold = max(before_round - lookback_rounds, 0)
    for turn in reversed(run_state.turns):
        if turn.round_no >= before_round:
            continue
        if turn.round_no <= threshold:
            break
        used_tool_names = {tool.name for tool in turn.tool_calls} | {
            result.name for result in turn.tool_results
        }
        if used_tool_names.intersection(_SEMANTIC_REFRESH_COMPLETION_TOOL_SET):
            return True
    return False


def _latest_actionable_semantic_refresh_since_failed_write(
    run_state: RunState,
    *,
    failed_write_round: int,
) -> dict[str, Any] | None:
    from sub_agent_runtime.orchestration.policy.local_finish import (
        _feature_probe_recommends_local_finish,
    )

    for turn in reversed(run_state.turns):
        if turn.round_no <= failed_write_round:
            continue
        for result in reversed(turn.tool_results):
            if result.name != "query_feature_probes" or not result.success:
                continue
            payload = result.payload if isinstance(result.payload, dict) else {}
            families = [
                str(family_id).strip()
                for family_id in (payload.get("detected_families") or [])
                if isinstance(family_id, str) and str(family_id).strip()
            ]
            local_finish_signaled = False
            for probe in payload.get("probes") or []:
                if not isinstance(probe, dict):
                    continue
                probe_family = str(probe.get("family") or "").strip()
                if probe_family and probe_family not in families:
                    families.append(probe_family)
                if _feature_probe_recommends_local_finish(probe):
                    local_finish_signaled = True
            if local_finish_signaled:
                return {
                    "repair_lane": "local_finish",
                    "families": families,
                    "round_no": turn.round_no,
                }
            probe_blockers = [
                str(blocker_id).strip()
                for probe in (payload.get("probes") or [])
                if isinstance(probe, dict)
                for blocker_id in (probe.get("blockers") or [])
                if isinstance(blocker_id, str) and str(blocker_id).strip()
            ]
            taxonomy = classify_blocker_taxonomy_many(
                probe_blockers,
                evidence_source="probe",
                completeness_relevance="diagnostic",
            )
            repair_lanes = {
                str(item.recommended_repair_lane or "").strip()
                for item in taxonomy
                if str(item.recommended_repair_lane or "").strip()
            }
            if not repair_lanes or repair_lanes == {"probe_first"}:
                continue
            for item in taxonomy:
                for family_id in item.family_ids:
                    if family_id and family_id not in families:
                        families.append(family_id)
            repair_lane = "code_repair"
            if repair_lanes == {"local_finish"}:
                repair_lane = "local_finish"
            return {
                "repair_lane": repair_lane,
                "families": families,
                "round_no": turn.round_no,
            }
    return None


def _semantic_refresh_allowed_tool_names_for_turn(
    run_state: RunState,
    *,
    all_tool_names: list[str],
) -> list[str]:
    from sub_agent_runtime.orchestration.policy.shared import (
        _SEMANTIC_REFRESH_REPAIR_TOOL_SET,
        _latest_feature_probes_prefer_topology_refresh,
    )
    from sub_agent_runtime.orchestration.policy.validation import (
        _latest_validation_prefers_topology_refresh,
    )

    allowed = [
        name for name in all_tool_names if name in _SEMANTIC_REFRESH_REPAIR_TOOL_SET
    ]
    if (
        (
            _latest_validation_prefers_topology_refresh(run_state.latest_validation)
            or _latest_feature_probes_prefer_topology_refresh(run_state)
        )
        and "query_topology" in all_tool_names
        and "query_topology" not in allowed
    ):
        allowed.append("query_topology")
    return allowed


def _preferred_validation_assessment_tools_for_turn(
    run_state: RunState,
    *,
    all_tool_names: list[str],
) -> list[str]:
    from sub_agent_runtime.orchestration.policy.shared import (
        _latest_feature_probe_preferred_tools_for_turn,
    )
    from sub_agent_runtime.orchestration.policy.validation import (
        _latest_validation_prefers_topology_refresh,
    )

    preferred_tools: list[str] = []

    def _append(raw_tool_name: Any) -> None:
        tool_name = str(raw_tool_name or "").strip()
        if not tool_name or tool_name in preferred_tools or tool_name not in all_tool_names:
            return
        preferred_tools.append(tool_name)

    graph = run_state.feature_graph
    assessment = (
        getattr(graph, "latest_validation_assessment", None)
        if graph is not None
        else None
    )
    for tool_name in _latest_feature_probe_preferred_tools_for_turn(
        run_state,
        all_tool_names=all_tool_names,
    ):
        _append(tool_name)
    if assessment is not None:
        for tool_name in getattr(assessment, "recommended_next_tools", []) or []:
            _append(tool_name)
    if not preferred_tools:
        latest_validation = run_state.latest_validation or {}
        for field_name in ("repair_hints", "decision_hints"):
            hints = latest_validation.get(field_name)
            if not isinstance(hints, list):
                continue
            for hint in hints:
                normalized = str(hint or "").strip().lower()
                if normalized == "inspect_more_evidence":
                    _append("query_feature_probes")
                    _append("query_kernel_state")
                elif normalized == "inspect count or placement with geometry/topology evidence":
                    _append("query_topology")
                    _append("query_feature_probes")
                else:
                    _append(normalized)
    if not preferred_tools and _latest_validation_prefers_topology_refresh(run_state.latest_validation):
        _append("query_topology")
        _append("query_feature_probes")
        _append("query_kernel_state")
    if not preferred_tools:
        _append("query_feature_probes")
        _append("query_kernel_state")
    return preferred_tools


def _latest_validation_has_budget_skipped_hint(
    latest_validation: dict[str, Any] | None,
) -> bool:
    if not isinstance(latest_validation, dict):
        return False
    for item in (latest_validation.get("decision_hints") or []):
        normalized = str(item or "").strip().lower()
        if normalized.startswith("validation_llm_skipped:"):
            return True
    return False


def _semantic_refresh_followup_should_preempt_closure_validation(
    run_state: RunState,
    *,
    write_round: int,
    all_tool_names: list[str],
) -> bool:
    from sub_agent_runtime.orchestration.policy.shared import (
        _post_semantic_refresh_followup_tools_for_turn,
    )
    from sub_agent_runtime.orchestration.policy.validation import (
        _validation_has_evidence_gap,
        _validation_requests_localized_evidence_refresh,
    )

    latest_validation = run_state.latest_validation
    if not _validation_has_evidence_gap(latest_validation):
        return False
    if not (
        _validation_requests_localized_evidence_refresh(latest_validation)
        or _latest_validation_has_budget_skipped_hint(latest_validation)
    ):
        return False
    followup_tools = _post_semantic_refresh_followup_tools_for_turn(
        run_state,
        write_round=write_round,
        all_tool_names=all_tool_names,
    )
    return any(tool_name != "query_kernel_state" for tool_name in followup_tools)


def _latest_feature_probes_prefer_topology_refresh(run_state: RunState) -> bool:
    for turn in reversed(run_state.turns):
        for result in reversed(turn.tool_results):
            if result.name != "query_feature_probes" or not result.success:
                continue
            payload = result.payload if isinstance(result.payload, dict) else {}
            if _feature_probe_payload_has_general_geometry_grounding_gap(
                payload
            ) and not _feature_probe_payload_allows_hybrid_topology_refresh(
                run_state,
                payload,
            ):
                return False
            for probe in payload.get("probes") or []:
                if not isinstance(probe, dict):
                    continue
                recommended_next_tools = {
                    str(item or "").strip().lower()
                    for item in (probe.get("recommended_next_tools") or [])
                    if str(item or "").strip()
                }
                if "query_topology" in recommended_next_tools:
                    return True
                anchor_summary = (
                    probe.get("anchor_summary")
                    if isinstance(probe.get("anchor_summary"), dict)
                    else {}
                )
                if bool(anchor_summary.get("requires_topology_host_ranking")):
                    return True
                grounding_blockers = {
                    str(item or "").strip()
                    for item in (probe.get("grounding_blockers") or [])
                    if str(item or "").strip()
                }
                if "need_topology_host_selection" in grounding_blockers:
                    return True
            return False
    return False


def _latest_feature_probes_allow_topology_refresh_despite_general_geometry_gap(
    run_state: RunState,
    *,
    after_round: int | None = None,
) -> bool:
    for turn in reversed(run_state.turns):
        if after_round is not None and turn.round_no <= after_round:
            continue
        for result in reversed(turn.tool_results):
            if result.name != "query_feature_probes" or not result.success:
                continue
            payload = result.payload if isinstance(result.payload, dict) else {}
            return _feature_probe_payload_allows_hybrid_topology_refresh(
                run_state,
                payload,
            )
    return False


def _latest_feature_probes_have_general_geometry_grounding_gap(
    run_state: RunState,
    *,
    after_round: int | None = None,
) -> bool:
    for turn in reversed(run_state.turns):
        if after_round is not None and turn.round_no <= after_round:
            continue
        for result in reversed(turn.tool_results):
            if result.name != "query_feature_probes" or not result.success:
                continue
            payload = result.payload if isinstance(result.payload, dict) else {}
            return _feature_probe_payload_has_general_geometry_grounding_gap(payload)
    return False


def _feature_probe_payload_has_general_geometry_grounding_gap(
    payload: dict[str, Any] | None,
) -> bool:
    if not isinstance(payload, dict):
        return False
    for probe in payload.get("probes") or []:
        if not isinstance(probe, dict):
            continue
        if str(probe.get("family") or "").strip() != "general_geometry":
            continue
        grounding_blockers = {
            str(item or "").strip()
            for item in (probe.get("grounding_blockers") or [])
            if str(item or "").strip()
        }
        if grounding_blockers and not bool(probe.get("success")):
            return True
        recommended_next_tools = {
            str(item or "").strip().lower()
            for item in (probe.get("recommended_next_tools") or [])
            if str(item or "").strip()
        }
        if {"query_geometry", "query_snapshot"}.intersection(recommended_next_tools) and not bool(
            probe.get("success")
        ):
            return True
    return False


def _feature_probe_payload_allows_hybrid_topology_refresh(
    run_state: RunState,
    payload: dict[str, Any] | None,
) -> bool:
    if not _feature_probe_payload_has_general_geometry_grounding_gap(payload):
        return False
    if not _feature_probe_payload_has_topology_refresh_signal(payload):
        return False
    return _latest_write_geometry_is_close_enough_for_topology_refresh(
        run_state,
        payload,
    )


def _feature_probe_payload_has_topology_refresh_signal(
    payload: dict[str, Any] | None,
) -> bool:
    if not isinstance(payload, dict):
        return False
    for probe in payload.get("probes") or []:
        if not isinstance(probe, dict):
            continue
        recommended_next_tools = {
            str(item or "").strip().lower()
            for item in (probe.get("recommended_next_tools") or [])
            if str(item or "").strip()
        }
        if "query_topology" in recommended_next_tools:
            return True
        anchor_summary = (
            probe.get("anchor_summary")
            if isinstance(probe.get("anchor_summary"), dict)
            else {}
        )
        if bool(anchor_summary.get("requires_topology_host_ranking")):
            return True
        grounding_blockers = {
            str(item or "").strip()
            for item in (probe.get("grounding_blockers") or [])
            if str(item or "").strip()
        }
        if "need_topology_host_selection" in grounding_blockers:
            return True
    return False


def _feature_probe_payload_has_non_general_topology_refresh_signal(
    payload: dict[str, Any] | None,
) -> bool:
    if not isinstance(payload, dict):
        return False
    for probe in payload.get("probes") or []:
        if not isinstance(probe, dict):
            continue
        family_id = str(
            probe.get("family")
            or probe.get("family_id")
            or ""
        ).strip()
        if not family_id or family_id == "general_geometry":
            continue
        recommended_next_tools = {
            str(item or "").strip().lower()
            for item in (probe.get("recommended_next_tools") or [])
            if str(item or "").strip()
        }
        if "query_topology" in recommended_next_tools:
            return True
        anchor_summary = (
            probe.get("anchor_summary")
            if isinstance(probe.get("anchor_summary"), dict)
            else {}
        )
        if bool(anchor_summary.get("requires_topology_host_ranking")):
            return True
        grounding_blockers = {
            str(item or "").strip()
            for item in (probe.get("grounding_blockers") or [])
            if str(item or "").strip()
        }
        if "need_topology_host_selection" in grounding_blockers:
            return True
    return False


def _latest_write_geometry_is_close_enough_for_topology_refresh(
    run_state: RunState,
    payload: dict[str, Any] | None,
) -> bool:
    from sub_agent_runtime.orchestration.policy.validation import (
        _payload_has_positive_session_backed_solid,
    )

    latest_write_payload = (
        run_state.latest_write_payload if isinstance(run_state.latest_write_payload, dict) else {}
    )
    if not _payload_has_positive_session_backed_solid(latest_write_payload):
        return False
    snapshot = (
        latest_write_payload.get("snapshot")
        if isinstance(latest_write_payload.get("snapshot"), dict)
        else {}
    )
    geometry = snapshot.get("geometry") if isinstance(snapshot.get("geometry"), dict) else {}
    actual_solids = int(geometry.get("solids", 0) or 0)
    actual_bbox_raw = geometry.get("bbox")
    actual_bbox = (
        [float(item or 0.0) for item in actual_bbox_raw[:3]]
        if isinstance(actual_bbox_raw, list) and len(actual_bbox_raw) >= 3
        else []
    )
    if not actual_bbox or any(value <= 0.0 for value in actual_bbox):
        return False
    expected_bbox: list[float] = []
    expected_part_count: int | None = None
    if isinstance(payload, dict):
        for probe in payload.get("probes") or []:
            if not isinstance(probe, dict):
                continue
            anchor_summary = (
                probe.get("anchor_summary")
                if isinstance(probe.get("anchor_summary"), dict)
                else {}
            )
            if not expected_bbox:
                expected_bbox_raw = anchor_summary.get("expected_bbox")
                if isinstance(expected_bbox_raw, list) and len(expected_bbox_raw) >= 3:
                    expected_bbox = [float(item or 0.0) for item in expected_bbox_raw[:3]]
            if expected_part_count is None:
                raw_expected_part_count = anchor_summary.get("expected_part_count")
                if isinstance(raw_expected_part_count, int):
                    expected_part_count = raw_expected_part_count
    if not expected_bbox and expected_part_count is None:
        return False
    max_bbox_rel_diff = 0.0
    if expected_bbox:
        max_bbox_rel_diff = max(
            abs(actual - expected) / max(abs(expected), 1.0)
            for actual, expected in zip(actual_bbox, expected_bbox, strict=False)
        )
        if max_bbox_rel_diff > 0.35:
            return False
    if (
        expected_part_count is not None
        and expected_part_count > 0
        and abs(actual_solids - expected_part_count) > 1
    ):
        solid_gap = abs(actual_solids - expected_part_count)
        if solid_gap > 2:
            return False
        if not expected_bbox or max_bbox_rel_diff > 0.12:
            return False
        if not _feature_probe_payload_has_non_general_topology_refresh_signal(payload):
            return False
    return True


def _latest_feature_probe_preferred_tools_for_turn(
    run_state: RunState,
    *,
    all_tool_names: list[str],
    after_round: int | None = None,
) -> list[str]:
    preferred_tools: list[str] = []

    def _append(raw_tool_name: Any) -> None:
        tool_name = str(raw_tool_name or "").strip()
        if (
            not tool_name
            or tool_name in preferred_tools
            or tool_name not in all_tool_names
        ):
            return
        preferred_tools.append(tool_name)

    for turn in reversed(run_state.turns):
        if after_round is not None and turn.round_no <= after_round:
            continue
        for result in reversed(turn.tool_results):
            if result.name != "query_feature_probes" or not result.success:
                continue
            payload = result.payload if isinstance(result.payload, dict) else {}
            for probe in payload.get("probes") or []:
                if not isinstance(probe, dict):
                    continue
                anchor_summary = (
                    probe.get("anchor_summary")
                    if isinstance(probe.get("anchor_summary"), dict)
                    else {}
                )
                grounding_blockers = {
                    str(item or "").strip()
                    for item in (probe.get("grounding_blockers") or [])
                    if str(item or "").strip()
                }
                if bool(anchor_summary.get("requires_topology_host_ranking")) or (
                    "need_topology_host_selection" in grounding_blockers
                ):
                    _append("query_topology")
                for tool_name in probe.get("recommended_next_tools") or []:
                    _append(tool_name)
            return preferred_tools
    return preferred_tools


def _post_semantic_refresh_followup_tools_for_turn(
    run_state: RunState,
    *,
    write_round: int,
    all_tool_names: list[str],
) -> list[str]:
    from sub_agent_runtime.orchestration.policy.local_finish import (
        _has_actionable_topology_targeting_since_round,
    )

    focused_followup_set = {
        "apply_cad_action",
        "query_topology",
        "query_feature_probes",
        "query_geometry",
        "query_kernel_state",
        "execute_build123d_probe",
    }
    preferred_tools = _latest_feature_probe_preferred_tools_for_turn(
        run_state,
        all_tool_names=all_tool_names,
        after_round=write_round,
    )
    followup_tools: list[str] = []
    for tool_name in preferred_tools:
        if tool_name not in focused_followup_set or tool_name in followup_tools:
            continue
        followup_tools.append(tool_name)
    if (
        "apply_cad_action" in followup_tools
        and "query_topology" in followup_tools
        and not _has_actionable_topology_targeting_since_round(
            run_state,
            after_round=write_round,
        )
    ):
        reordered = ["query_topology", "apply_cad_action"]
        followup_tools = reordered + [
            tool_name
            for tool_name in followup_tools
            if tool_name not in {"query_topology", "apply_cad_action"}
        ]
    if "query_kernel_state" in all_tool_names and "query_kernel_state" not in followup_tools:
        followup_tools.append("query_kernel_state")
    return followup_tools


from sub_agent_runtime.orchestration.policy.shared import (
    _has_successful_semantic_refresh_since_round,
)
from sub_agent_runtime.orchestration.policy.code_repair import (
    _has_semantic_refresh_turn_since_failed_write,
    _has_successful_non_persisted_probe_turn_since_failed_write,
    _has_successful_probe_turn_since_failed_write,
)
from sub_agent_runtime.orchestration.policy.validation import (
    _latest_validation_has_actionable_geometry_contradiction,
    _latest_validation_prefers_semantic_refresh,
)
