from sub_agent_runtime.orchestration.policy import (
    code_repair,
    local_finish,
    semantic_refresh,
    validation,
)


def test_local_finish_policy_helpers_live_in_local_finish_module() -> None:
    assert (
        local_finish._latest_feature_probes_recommend_local_finish.__module__
        == local_finish.__name__
    )
    assert (
        local_finish._latest_apply_action_type_from_turn.__module__
        == local_finish.__name__
    )
    assert (
        local_finish._turn_has_open_sketch_window_after_successful_apply.__module__
        == local_finish.__name__
    )
    assert (
        local_finish._latest_successful_apply_action_type_with_open_sketch_window.__module__
        == local_finish.__name__
    )
    assert (
        local_finish._latest_successful_tool_payload.__module__
        == local_finish.__name__
    )
    assert (
        local_finish._open_sketch_window_requires_code_escape.__module__
        == local_finish.__name__
    )
    assert (
        local_finish._preferred_sketch_window_tools.__module__
        == local_finish.__name__
    )
    assert (
        local_finish._open_sketch_window_requires_apply_write_first.__module__
        == local_finish.__name__
    )
    assert (
        local_finish._local_finish_validation_evidence_refresh_tools_for_turn.__module__
        == local_finish.__name__
    )
    assert (
        local_finish._successful_local_finish_semantic_refresh_needs_validation.__module__
        == local_finish.__name__
    )
    assert (
        local_finish._local_finish_should_force_apply_after_topology_targeting.__module__
        == local_finish.__name__
    )
    assert (
        local_finish._local_finish_validation_evidence_gap_needs_read_refresh.__module__
        == local_finish.__name__
    )


def test_semantic_refresh_policy_helpers_live_in_semantic_refresh_module() -> None:
    assert (
        semantic_refresh._latest_actionable_semantic_refresh_since_failed_write.__module__
        == semantic_refresh.__name__
    )
    assert (
        semantic_refresh._has_recent_semantic_refresh_before_round.__module__
        == semantic_refresh.__name__
    )
    assert (
        semantic_refresh._semantic_refresh_allowed_tool_names_for_turn.__module__
        == semantic_refresh.__name__
    )
    assert (
        semantic_refresh._semantic_refresh_followup_should_preempt_closure_validation.__module__
        == semantic_refresh.__name__
    )
    assert (
        semantic_refresh._latest_feature_probes_prefer_topology_refresh.__module__
        == semantic_refresh.__name__
    )
    assert (
        semantic_refresh._latest_feature_probe_preferred_tools_for_turn.__module__
        == semantic_refresh.__name__
    )


def test_validation_policy_helpers_live_in_validation_module() -> None:
    assert validation._is_successful_validation.__module__ == validation.__name__
    assert validation._pick_step_file.__module__ == validation.__name__
    assert validation._pick_render_file.__module__ == validation.__name__
    assert (
        validation._should_auto_validate_after_non_progress.__module__
        == validation.__name__
    )
    assert (
        validation._should_auto_validate_after_post_write.__module__
        == validation.__name__
    )
    assert (
        validation._result_has_positive_session_backed_solid.__module__
        == validation.__name__
    )
    assert (
        validation._payload_has_positive_session_backed_solid.__module__
        == validation.__name__
    )
    assert (
        validation._validation_has_evidence_gap.__module__
        == validation.__name__
    )
    assert (
        validation._latest_validation_prefers_topology_refresh.__module__
        == validation.__name__
    )
    assert (
        validation._turn_has_successful_validation_completion.__module__
        == validation.__name__
    )
    assert (
        validation._latest_validation_prefers_semantic_refresh.__module__
        == validation.__name__
    )
    assert (
        validation._has_repeated_validation_without_new_evidence_after_write.__module__
        == validation.__name__
    )


def test_code_repair_policy_helpers_live_in_code_repair_module() -> None:
    assert (
        code_repair._latest_actionable_kernel_patch.__module__
        == code_repair.__name__
    )
    assert (
        code_repair._runtime_repair_packet_observability_summary.__module__
        == code_repair.__name__
    )
    assert (
        code_repair._infer_runtime_failure_cluster.__module__
        == code_repair.__name__
    )
    assert (
        code_repair._latest_failed_code_sequence_is_artifactless.__module__
        == code_repair.__name__
    )
    assert (
        code_repair._payload_has_step_artifact.__module__
        == code_repair.__name__
    )
    assert (
        code_repair._filter_supported_round_tool_names.__module__
        == code_repair.__name__
    )
    assert (
        code_repair._build_repair_packet_round_observability_events.__module__
        == code_repair.__name__
    )
    assert (
        code_repair._blockers_prefer_probe_first_after_code_write.__module__
        == code_repair.__name__
    )
    assert (
        code_repair._preferred_probe_families_for_turn.__module__
        == code_repair.__name__
    )
    assert (
        code_repair._turn_policy_from_actionable_kernel_patch.__module__
        == code_repair.__name__
    )
    assert (
        code_repair._kernel_patch_should_yield_semantic_refresh.__module__
        == code_repair.__name__
    )
    assert (
        code_repair._kernel_patch_should_yield_feature_probe_assessment.__module__
        == code_repair.__name__
    )
