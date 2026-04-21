from __future__ import annotations

from typing import Any

from common.blocker_taxonomy import taxonomy_repair_lanes_from_validation_payload

from sub_agent_runtime.turn_state import (
    RunState,
    ToolCategory,
    ToolResultRecord,
    TurnRecord,
)


def _is_successful_validation(validation_core: dict[str, Any] | None) -> bool:
    if not isinstance(validation_core, dict):
        return False
    return bool(validation_core.get("success")) and bool(
        validation_core.get("is_complete")
    )


def _pick_step_file(output_files: list[str]) -> str | None:
    for filename in output_files:
        if filename.lower().endswith(".step"):
            return filename
    return None


def _pick_render_file(output_files: list[str]) -> str | None:
    for filename in output_files:
        lowered = filename.lower()
        if lowered.endswith(".png") or lowered.endswith(".jpg") or lowered.endswith(".jpeg"):
            return filename
    return None


def _should_auto_validate_after_non_progress(run_state: RunState) -> bool:
    if len(run_state.turns) < 2:
        return False
    recent = run_state.turns[-2:]
    if not all(turn.write_tool_name is None for turn in recent):
        return False
    latest_turn = recent[-1]
    if any(tool_call.name == "validate_requirement" for tool_call in latest_turn.tool_calls):
        return False
    if any(
        result.name == "validate_requirement" and result.success
        for result in latest_turn.tool_results
    ):
        return False
    return True


def _payload_has_positive_session_backed_solid(payload: dict[str, Any]) -> bool:
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
    geometry = snapshot.get("geometry") if isinstance(snapshot.get("geometry"), dict) else {}
    return (
        int(geometry.get("solids", 0) or 0) > 0
        and abs(float(geometry.get("volume", 0.0) or 0.0)) > 1e-6
        and bool(payload.get("session_state_persisted", False))
    )


def _result_has_positive_session_backed_solid(result: ToolResultRecord) -> bool:
    payload = result.payload if isinstance(result.payload, dict) else {}
    return _payload_has_positive_session_backed_solid(payload)


def _should_auto_validate_after_post_write(
    *,
    run_state: RunState,
    turn: TurnRecord,
    round_no: int,
    max_rounds: int,
) -> bool:
    from sub_agent_runtime.orchestration.policy.local_finish import (
        _turn_has_open_sketch_window_after_successful_apply,
    )

    write_results = [
        result
        for result in turn.tool_results
        if result.category == ToolCategory.WRITE and result.success
    ]
    if len(write_results) != 1:
        return False
    write_result = write_results[0]
    if write_result.name not in {
        "execute_build123d",
        "execute_repair_packet",
        "apply_cad_action",
    }:
        return False
    if (
        write_result.name == "apply_cad_action"
        and _turn_has_open_sketch_window_after_successful_apply(turn)
    ):
        return False
    latest_validation = run_state.latest_validation or {}
    prior_blockers = latest_validation.get("blockers")
    if not isinstance(prior_blockers, list) or not prior_blockers:
        for event in reversed(run_state.agent_events):
            if event.kind != "validation_result":
                continue
            if not isinstance(event.round_no, int) or event.round_no >= round_no:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            event_blockers = payload.get("blockers")
            if isinstance(event_blockers, list):
                prior_blockers = event_blockers
                break
    remaining_rounds = max(max_rounds - round_no, 0)
    prior_successful_positive_writes = 0
    for previous_turn in run_state.turns:
        if previous_turn is turn:
            continue
        for previous_result in previous_turn.tool_results:
            if (
                previous_result.category == ToolCategory.WRITE
                and previous_result.success
                and _result_has_positive_session_backed_solid(previous_result)
            ):
                prior_successful_positive_writes += 1
    has_positive_solid = _result_has_positive_session_backed_solid(write_result)
    no_prior_validation = not isinstance(run_state.latest_validation, dict)
    should_probe_first_code_write = (
        no_prior_validation
        and prior_successful_positive_writes == 0
        and has_positive_solid
    )
    should_close_existing_blockers = bool(prior_blockers) and has_positive_solid
    return (
        should_close_existing_blockers
        or remaining_rounds <= 1
        or should_probe_first_code_write
    )


def _validation_has_evidence_gap(latest_validation: dict[str, Any] | None) -> bool:
    if not isinstance(latest_validation, dict):
        return False
    if bool(latest_validation.get("insufficient_evidence")):
        return True
    observation_tags = {
        str(item).strip().lower()
        for item in (latest_validation.get("observation_tags") or [])
        if isinstance(item, str) and str(item).strip()
    }
    return "insufficient_evidence" in observation_tags


def _validation_requests_localized_evidence_refresh(
    latest_validation: dict[str, Any] | None,
) -> bool:
    if not _validation_has_evidence_gap(latest_validation):
        return False
    if not isinstance(latest_validation, dict):
        return False
    normalized_hints = {
        str(item).strip().lower()
        for field_name in ("repair_hints", "decision_hints")
        for item in (latest_validation.get(field_name) or [])
        if isinstance(item, str) and str(item).strip()
    }
    if "query_topology" in normalized_hints or "query_feature_probes" in normalized_hints:
        return True
    if any(
        token in hint
        for hint in normalized_hints
        for token in ("geometry/topology evidence", "inspect count or placement", "inspect_more_evidence")
    ):
        return True
    for item in (latest_validation.get("blocker_taxonomy") or []):
        if not isinstance(item, dict):
            continue
        if str(item.get("recommended_repair_lane") or "").strip() == "local_finish":
            return True
        decision_hints = {
            str(hint).strip().lower()
            for hint in (item.get("decision_hints") or [])
            if isinstance(hint, str) and str(hint).strip()
        }
        if "query_topology" in decision_hints or "query_feature_probes" in decision_hints:
            return True
    return False


def _latest_validation_prefers_topology_refresh(
    latest_validation: dict[str, Any] | None,
) -> bool:
    if not isinstance(latest_validation, dict):
        return False
    if "local_finish" in taxonomy_repair_lanes_from_validation_payload(latest_validation):
        return True
    for field_name in ("repair_hints", "decision_hints"):
        hints = latest_validation.get(field_name)
        if not isinstance(hints, list):
            continue
        for hint in hints:
            if str(hint or "").strip().lower() == "query_topology":
                return True
    return False


def _latest_validation_is_fresh_for_write(
    run_state: RunState,
    *,
    write_round: int,
) -> bool:
    latest_validation = (
        run_state.latest_validation if isinstance(run_state.latest_validation, dict) else {}
    )
    latest_validation_round = max(
        (
            int(event.round_no)
            for event in run_state.agent_events
            if event.kind == "validation_result"
            and isinstance(event.round_no, int)
        ),
        default=-1,
    )
    if latest_validation_round < write_round:
        return False
    blockers = latest_validation.get("blockers")
    if isinstance(blockers, list) and blockers:
        return True
    if latest_validation.get("is_complete") is False:
        return True
    if bool(latest_validation.get("insufficient_evidence")):
        return True
    return bool(latest_validation)


def _turn_has_successful_validation_completion(turn: TurnRecord | None) -> bool:
    if turn is None:
        return False
    for result in turn.tool_results:
        if result.name != "validate_requirement" or not result.success:
            continue
        if _is_successful_validation(result.payload):
            return True
    return False


def _latest_validation_round(run_state: RunState) -> int:
    return max(
        (
            int(event.round_no)
            for event in run_state.agent_events
            if event.kind == "validation_result"
            and isinstance(event.round_no, int)
        ),
        default=-1,
    )


def _blockers_are_local_structured_tail(blockers: list[str]) -> bool:
    blocker_set = {item for item in blockers if isinstance(item, str)}
    if not blocker_set:
        return False
    return blocker_set.issubset({"feature_fillet", "feature_chamfer"})


def _latest_validation_prefers_semantic_refresh(
    latest_validation: dict[str, Any] | None,
) -> bool:
    if not isinstance(latest_validation, dict):
        return False
    if _latest_validation_has_actionable_geometry_contradiction(
        latest_validation,
        min_coverage=0.4,
    ):
        return False
    if bool(latest_validation.get("insufficient_evidence")):
        return True
    observation_tags = {
        str(item).strip().lower()
        for item in (latest_validation.get("observation_tags") or [])
        if isinstance(item, str) and str(item).strip()
    }
    if "insufficient_evidence" in observation_tags:
        return True
    decision_hints = {
        str(item).strip().lower()
        for item in (latest_validation.get("decision_hints") or [])
        if isinstance(item, str) and str(item).strip()
    }
    if "inspect_more_evidence" in decision_hints:
        return True
    if any(
        "inspect more" in hint and any(token in hint for token in ("evidence", "geometry", "topology"))
        for hint in decision_hints
    ):
        return True
    if any(hint.startswith("no explicit evidence for clause:") for hint in decision_hints):
        return True
    coverage_confidence = latest_validation.get("coverage_confidence")
    return isinstance(coverage_confidence, (int, float)) and float(coverage_confidence) <= 0.25


def _latest_validation_has_actionable_geometry_contradiction(
    latest_validation: dict[str, Any] | None,
    *,
    min_coverage: float,
    require_nonempty_evidence: bool = True,
) -> bool:
    if not isinstance(latest_validation, dict):
        return False
    coverage_confidence = latest_validation.get("coverage_confidence")
    if not isinstance(coverage_confidence, (int, float)):
        return False
    if float(coverage_confidence) < float(min_coverage):
        return False
    clause_interpretations = latest_validation.get("clause_interpretations")
    if not isinstance(clause_interpretations, list):
        return False
    process_only_evidence_markers = (
        "no explicit evidence for clause:",
        "no cutting action observed",
        "no sweep action observed",
        "no revolve action observed",
        "no fillet action observed",
        "no chamfer action observed",
        "sketch-related evidence exists in the process history",
        "setup/process clause is not directly verifiable",
        "ui/navigation clause is not directly verifiable",
        "construction-constraint clause is not directly verifiable",
        "construction-method clause is not directly verifiable",
    )
    for clause in clause_interpretations:
        if not isinstance(clause, dict):
            continue
        status = str(clause.get("status") or "").strip().lower()
        if status != "contradicted":
            continue
        observation_tags = {
            str(item).strip().lower()
            for item in (clause.get("observation_tags") or [])
            if isinstance(item, str) and str(item).strip()
        }
        if "clause:process_setup" in observation_tags:
            continue
        evidence = str(clause.get("evidence") or "").strip().lower()
        if require_nonempty_evidence and not evidence:
            continue
        if evidence and any(marker in evidence for marker in process_only_evidence_markers):
            continue
        return True
    return False


def _latest_validation_has_actionable_single_blocker(
    latest_validation: dict[str, Any] | None,
) -> bool:
    if not isinstance(latest_validation, dict):
        return False
    blockers = [
        str(item).strip()
        for item in (latest_validation.get("blockers") or [])
        if isinstance(item, str) and str(item).strip()
    ]
    blocker_set = set(blockers)
    if len(blocker_set) != 1:
        return False
    if blocker_set.isdisjoint(
        {
            "feature_countersink",
            "feature_counterbore",
            "feature_hole",
            "feature_hole_exact_center_set",
            "feature_hole_position_alignment",
            "feature_local_anchor_alignment",
            "feature_target_face_additive_merge",
            "feature_target_face_subtractive_merge",
        }
    ):
        return False
    coverage_confidence = latest_validation.get("coverage_confidence")
    if not isinstance(coverage_confidence, (int, float)):
        return False
    if float(coverage_confidence) < 0.5:
        return False
    decision_hints = {
        str(item).strip().lower()
        for item in (latest_validation.get("decision_hints") or [])
        if isinstance(item, str) and str(item).strip()
    }
    if "inspect_more_evidence" in decision_hints:
        return False
    if any(hint.startswith("no explicit evidence for clause:") for hint in decision_hints):
        return False
    return True


def _has_repeated_validation_blockers_without_semantic_refresh(
    run_state: RunState,
    *,
    blockers: list[str],
    min_repeats: int = 2,
) -> bool:
    normalized_target = tuple(
        sorted(item for item in blockers if isinstance(item, str) and item.strip())
    )
    if not normalized_target:
        return False
    repeat_count = 0
    for event in reversed(run_state.agent_events):
        if event.kind != "validation_result":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        event_blockers = payload.get("blockers")
        normalized_event: tuple[str, ...] = ()
        if isinstance(event_blockers, list):
            normalized_event = tuple(
                sorted(
                    item
                    for item in event_blockers
                    if isinstance(item, str) and item.strip()
                )
            )
        else:
            summary_text = str(payload.get("summary") or "").strip().lower()
            is_incomplete = payload.get("is_complete") is False or "blocker" in summary_text
            if is_incomplete:
                normalized_event = normalized_target
        if not normalized_event:
            continue
        if normalized_event == normalized_target:
            repeat_count += 1
            if repeat_count >= min_repeats:
                return True
            continue
        break
    return False


def _has_repeated_validation_without_new_evidence_after_write(
    run_state: RunState,
    *,
    write_round: int,
    min_validations: int = 2,
) -> bool:
    from sub_agent_runtime.orchestration.policy.shared import _has_tool_turn_since_round

    if _has_tool_turn_since_round(
        run_state,
        after_round=write_round,
        tool_names={
            "query_kernel_state",
            "query_feature_probes",
            "execute_build123d_probe",
        },
    ):
        return False
    validation_rounds = {
        int(event.round_no)
        for event in run_state.agent_events
        if event.kind == "validation_result"
        and isinstance(event.round_no, int)
        and int(event.round_no) >= write_round
    }
    return len(validation_rounds) >= min_validations
