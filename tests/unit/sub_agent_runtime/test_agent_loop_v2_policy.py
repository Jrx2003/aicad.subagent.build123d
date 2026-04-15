from types import SimpleNamespace

from sub_agent_runtime.agent_loop_v2 import (
    _determine_turn_tool_policy,
    _payload_has_positive_session_backed_solid,
    _result_has_positive_session_backed_solid,
    _latest_actionable_kernel_patch,
    _latest_validation_prefers_semantic_refresh,
    _should_auto_validate_after_post_write,
    _should_auto_validate_after_non_progress,
)
from sub_agent_runtime.context_manager import V2ContextManager
from sub_agent_runtime.turn_state import RunState, ToolCallRecord, ToolCategory, ToolResultRecord, TurnRecord


class _Packet:
    def __init__(self) -> None:
        self.stale = False
        self.repair_mode = "local_edit"
        self.feature_instance_id = "instance.named_face_local_edit.slot"
        self.family_id = "named_face_local_edit"
        self.anchor_keys = ["axis"]
        self.parameter_keys = ["radius"]
        self.repair_intent = "retarget_local_face_edit"

    def to_dict(self) -> dict[str, object]:
        return {
            "repair_mode": self.repair_mode,
            "feature_instance_id": self.feature_instance_id,
            "family_id": self.family_id,
        }


class _Patch:
    def __init__(self) -> None:
        self.stale = False
        self.repair_mode = "whole_part_rebuild"
        self.feature_instance_ids = [
            "instance.slots.slot_alignment",
            "instance.general_geometry.full_length",
        ]
        self.anchor_keys = ["axis"]
        self.parameter_keys = ["requested_dimensions"]
        self.repair_intent = "rebuild_global_cut"


def test_latest_actionable_kernel_patch_prefers_global_patch_over_local_packet() -> None:
    run_state = SimpleNamespace(
        feature_graph=SimpleNamespace(
            repair_packets={"packet-1": _Packet()},
            repair_patches={"patch-1": _Patch()},
            feature_instances={
                "instance.slots.slot_alignment": SimpleNamespace(
                    family_id="slots",
                    status="blocked",
                ),
                "instance.general_geometry.full_length": SimpleNamespace(
                    family_id="general_geometry",
                    status="blocked",
                ),
                "instance.named_face_local_edit.slot": SimpleNamespace(
                    family_id="named_face_local_edit",
                    status="blocked",
                ),
            },
        )
    )

    patch = _latest_actionable_kernel_patch(run_state)

    assert patch is not None
    assert patch["repair_mode"] == "whole_part_rebuild"
    assert patch["feature_instance_ids"] == [
        "instance.slots.slot_alignment",
        "instance.general_geometry.full_length",
    ]


def test_latest_validation_prefers_semantic_refresh_for_human_readable_insufficient_evidence_hint() -> None:
    assert _latest_validation_prefers_semantic_refresh(
        {
            "blockers": ["length_set_to_110_0_to_cover_the_entire_length"],
            "insufficient_evidence": True,
            "coverage_confidence": 0.2,
            "observation_tags": ["insufficient_evidence"],
            "decision_hints": [
                "inspect more geometry/topology evidence before completion",
                "repair the overall body dimensions",
            ],
        }
    )


def test_repeated_build123d_api_lint_failure_stays_on_code_repair_lane() -> None:
    run_state = RunState(
        session_id="session-api-lint",
        requirements={"description": "Create a block with a cylindrical slot."},
    )
    for round_no, error_text in (
        (
            1,
            "Exit code: 1 | Traceback | TypeError: unsupported operand type(s) for -: 'method' and 'Cylinder'",
        ),
        (
            2,
            "execute_build123d preflight lint failed | Use Build123d transforms on the shape itself",
        ),
    ):
        run_state.turns.append(
            TurnRecord(
                round_no=round_no,
                decision_summary="retry",
                tool_calls=[
                    ToolCallRecord(
                        name="execute_build123d",
                        category=ToolCategory.WRITE,
                    )
                ],
                tool_results=[
                    ToolResultRecord(
                        name="execute_build123d",
                        category=ToolCategory.WRITE,
                        success=False,
                        payload={},
                        error=error_text,
                    )
                ],
                error=error_text,
            )
        )
    run_state.latest_write_payload = {}

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=3,
        max_rounds=8,
        all_tool_names=[
            "execute_build123d",
            "query_kernel_state",
            "query_feature_probes",
            "execute_build123d_probe",
        ],
        previous_tool_failure_summary={
            "tool": "execute_build123d",
            "same_tool_failure_count": 2,
            "effective_failure_kind": "execute_build123d_api_lint_failure",
        },
    )

    assert policy is not None
    assert policy.policy_id == "code_repair_after_repeated_concrete_code_failure"
    assert "execute_build123d" in policy.allowed_tool_names
    assert policy.mode == "code_first_repair"


def test_fresh_insufficient_evidence_after_code_write_forces_semantic_refresh_lane() -> None:
    run_state = RunState(
        session_id="session-insufficient-evidence",
        requirements={"description": "Create a centered orthogonal cross."},
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="build cross",
            tool_calls=[
                ToolCallRecord(
                    name="execute_build123d",
                    category=ToolCategory.WRITE,
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="execute_build123d",
                    category=ToolCategory.WRITE,
                    success=True,
                    payload={
                        "session_state_persisted": True,
                        "snapshot": {
                            "geometry": {
                                "solids": 1,
                                "volume": 15000.0,
                                "bbox": [10.0, 80.0, 80.0],
                            }
                        },
                    },
                )
            ],
        )
    )
    run_state.latest_write_payload = {
        "session_state_persisted": True,
        "snapshot": {
            "geometry": {
                "solids": 1,
                "volume": 15000.0,
                "bbox": [10.0, 80.0, 80.0],
            }
        },
    }
    run_state.latest_validation = {
        "success": True,
        "is_complete": False,
        "summary": "Requirement validation has insufficient evidence",
        "blockers": [],
        "insufficient_evidence": True,
        "coverage_confidence": 0.2,
        "observation_tags": ["insufficient_evidence"],
    }
    run_state.add_agent_event(
        SimpleNamespace(  # type: ignore[arg-type]
            kind="validation_result",
            round_no=1,
            role="runtime",
            payload={
                "summary": "Requirement validation has insufficient evidence",
                "is_complete": False,
                "blockers": [],
            },
        )
    )

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=2,
        max_rounds=8,
        all_tool_names=[
            "execute_build123d",
            "query_geometry",
            "query_kernel_state",
            "query_feature_probes",
            "execute_build123d_probe",
            "validate_requirement",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is not None
    assert (
        policy.policy_id
        == "semantic_refresh_after_validation_assessment_gap_from_code_write"
    )
    assert "query_kernel_state" in policy.allowed_tool_names
    assert "query_feature_probes" in policy.allowed_tool_names
    assert "execute_build123d_probe" in policy.allowed_tool_names
    assert "query_geometry" not in policy.allowed_tool_names
    assert "validate_requirement" not in policy.allowed_tool_names


def test_last_round_successful_artifactless_probe_reopens_code_repair_lane() -> None:
    run_state = RunState(
        session_id="session-artifactless-last-round-probe-repair",
        requirements={
            "description": (
                "Use the Sweep feature to construct a hollow bent pipe from an L-shaped "
                "path and concentric circle profile."
            )
        },
    )
    for round_no, error_text in (
        (
            1,
            "execute_build123d preflight lint failed | Call path_builder.wire()",
        ),
        (
            2,
            "Exit code: 1 | Traceback (most recent call last):",
        ),
    ):
        run_state.turns.append(
            TurnRecord(
                round_no=round_no,
                decision_summary="repair sweep path",
                tool_calls=[
                    ToolCallRecord(
                        name="execute_build123d",
                        category=ToolCategory.WRITE,
                    )
                ],
                tool_results=[
                    ToolResultRecord(
                        name="execute_build123d",
                        category=ToolCategory.WRITE,
                        success=False,
                        payload={},
                        error=error_text,
                    )
                ],
                error=error_text,
            )
        )
    run_state.turns.append(
        TurnRecord(
            round_no=3,
            decision_summary="probe path sweep rail",
            tool_calls=[
                ToolCallRecord(
                    name="execute_build123d_probe",
                    category=ToolCategory.READ,
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="execute_build123d_probe",
                    category=ToolCategory.READ,
                    success=True,
                    payload={
                        "success": True,
                        "probe_summary": {
                            "success": True,
                            "actionable": False,
                            "actionable_family_ids": [],
                        },
                    },
                    error=None,
                )
            ],
        )
    )
    run_state.latest_write_payload = {}

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=4,
        max_rounds=4,
        all_tool_names=[
            "execute_build123d",
            "query_kernel_state",
            "query_feature_probes",
            "execute_build123d_probe",
        ],
        previous_tool_failure_summary={
            "tool": "execute_build123d",
            "same_tool_failure_count": 2,
            "effective_failure_kind": "execute_build123d_runtime_failure",
        },
    )

    assert policy is not None
    assert policy.policy_id == "code_repair_last_round_after_successful_probe"
    assert policy.mode == "code_first_repair"
    assert "execute_build123d" in policy.allowed_tool_names
    assert "query_kernel_state" in policy.allowed_tool_names


def test_signed_negative_volume_counts_as_material_solid_for_runtime() -> None:
    payload = {
        "session_state_persisted": True,
        "snapshot": {
            "geometry": {
                "solids": 1,
                "volume": -5448.317262277956,
                "bbox": [101.23105635617661, 130.0000002, 20.0000002],
                "bbox_min": [-10.0, -1e-7, -10.0],
                "bbox_max": [91.23105635617661, 130.0000001, 10.0000001],
            }
        },
    }
    result = ToolResultRecord(
        name="execute_build123d",
        category=ToolCategory.WRITE,
        success=True,
        payload=payload,
    )

    assert _payload_has_positive_session_backed_solid(payload)
    assert _result_has_positive_session_backed_solid(result)


def test_post_write_auto_validation_accepts_signed_negative_volume_material_solid() -> None:
    payload = {
        "success": True,
        "session_state_persisted": True,
        "snapshot": {
            "geometry": {
                "solids": 1,
                "volume": -5448.317262277956,
                "bbox": [101.23105635617661, 130.0000002, 20.0000002],
                "bbox_min": [-10.0, -1e-7, -10.0],
                "bbox_max": [91.23105635617661, 130.0000001, 10.0000001],
            }
        },
    }
    turn = TurnRecord(
        round_no=2,
        decision_summary="repair path sweep",
        tool_calls=[
            ToolCallRecord(
                name="execute_build123d",
                category=ToolCategory.WRITE,
            )
        ],
        tool_results=[
            ToolResultRecord(
                name="execute_build123d",
                category=ToolCategory.WRITE,
                success=True,
                payload=payload,
            )
        ],
    )
    run_state = RunState(
        session_id="session-negative-volume-auto-validate",
        requirements={"description": "Create a hollow bent pipe with a path sweep."},
        turns=[turn],
        latest_write_payload=payload,
        latest_step_file="model.step",
    )

    assert _should_auto_validate_after_post_write(
        run_state=run_state,
        turn=turn,
        round_no=2,
        max_rounds=4,
    )


def test_context_manager_does_not_mark_signed_negative_volume_material_solid_as_invalid() -> None:
    payload = {
        "success": True,
        "session_state_persisted": True,
        "snapshot": {
            "geometry": {
                "solids": 1,
                "faces": 9,
                "edges": 16,
                "volume": -5448.317262277956,
                "bbox": [101.23105635617661, 130.0000002, 20.0000002],
                "bbox_min": [-10.0, -1e-7, -10.0],
                "bbox_max": [91.23105635617661, 130.0000001, 10.0000001],
            }
        },
    }
    turn = TurnRecord(
        round_no=2,
        decision_summary="repair path sweep",
        tool_calls=[
            ToolCallRecord(
                name="execute_build123d",
                category=ToolCategory.WRITE,
            )
        ],
        tool_results=[
            ToolResultRecord(
                name="execute_build123d",
                category=ToolCategory.WRITE,
                success=True,
                payload=payload,
            )
        ],
    )
    run_state = RunState(
        session_id="session-negative-volume-health",
        requirements={"description": "Create a hollow bent pipe with a path sweep."},
        turns=[turn],
        latest_write_payload=payload,
        latest_step_file="model.step",
    )

    context_manager = V2ContextManager()
    latest_write_health = context_manager._build_latest_write_health(run_state)
    objective_health = context_manager._build_objective_health(
        run_state,
        round_budget={"remaining_rounds": 2},
    )

    assert latest_write_health is not None
    assert latest_write_health["flags"]["has_positive_volume"] is True
    assert "non_positive_volume" not in latest_write_health["invalid_signals"]
    assert objective_health["status"] != "repair_needed"


def test_repeated_validation_without_new_evidence_after_code_write_forces_semantic_refresh() -> None:
    run_state = RunState(
        session_id="session-validation-pingpong",
        requirements={"description": "Create a centered orthogonal cross."},
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="build cross",
            tool_calls=[
                ToolCallRecord(
                    name="execute_build123d",
                    category=ToolCategory.WRITE,
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="execute_build123d",
                    category=ToolCategory.WRITE,
                    success=True,
                    payload={
                        "session_state_persisted": True,
                        "snapshot": {
                            "geometry": {
                                "solids": 1,
                                "volume": 15000.0,
                                "bbox": [10.0, 80.0, 80.0],
                            }
                        },
                    },
                )
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=2,
            decision_summary="validate again",
            tool_calls=[
                ToolCallRecord(
                    name="validate_requirement",
                    category=ToolCategory.JUDGE,
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="validate_requirement",
                    category=ToolCategory.JUDGE,
                    success=True,
                    payload={
                        "success": True,
                        "is_complete": False,
                        "summary": "Requirement validation has insufficient evidence",
                        "blockers": [],
                        "insufficient_evidence": True,
                    },
                )
            ],
        )
    )
    run_state.latest_write_payload = {
        "session_state_persisted": True,
        "snapshot": {
            "geometry": {
                "solids": 1,
                "volume": 15000.0,
                "bbox": [10.0, 80.0, 80.0],
            }
        },
    }
    run_state.latest_validation = {
        "success": True,
        "is_complete": False,
        "summary": "Requirement validation has insufficient evidence",
        "blockers": [],
        "insufficient_evidence": True,
        "coverage_confidence": 0.2,
        "observation_tags": ["insufficient_evidence"],
    }
    run_state.add_agent_event(
        SimpleNamespace(  # type: ignore[arg-type]
            kind="validation_result",
            round_no=1,
            role="runtime",
            payload={
                "summary": "Requirement validation has insufficient evidence",
                "is_complete": False,
                "blockers": [],
            },
        )
    )
    run_state.add_agent_event(
        SimpleNamespace(  # type: ignore[arg-type]
            kind="validation_result",
            round_no=2,
            role="runtime",
            payload={
                "summary": "Requirement validation has insufficient evidence",
                "is_complete": False,
                "blockers": [],
            },
        )
    )

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=3,
        max_rounds=8,
        all_tool_names=[
            "execute_build123d",
            "query_kernel_state",
            "query_feature_probes",
            "execute_build123d_probe",
            "validate_requirement",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is not None
    assert (
        policy.policy_id
        == "semantic_refresh_after_repeated_validation_without_new_evidence"
    )
    assert "query_kernel_state" in policy.allowed_tool_names
    assert "validate_requirement" not in policy.allowed_tool_names


def test_semantic_refresh_after_code_write_forces_closure_validation_instead_of_generic_read() -> None:
    run_state = RunState(
        session_id="session-post-refresh-closure",
        requirements={"description": "Create a centered orthogonal cross."},
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="build cross",
            tool_calls=[
                ToolCallRecord(
                    name="execute_build123d",
                    category=ToolCategory.WRITE,
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="execute_build123d",
                    category=ToolCategory.WRITE,
                    success=True,
                    payload={
                        "session_state_persisted": True,
                        "snapshot": {
                            "geometry": {
                                "solids": 1,
                                "volume": 15000.0,
                                "bbox": [10.0, 80.0, 80.0],
                            }
                        },
                    },
                )
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=2,
            decision_summary="refresh semantic evidence",
            tool_calls=[
                ToolCallRecord(
                    name="query_feature_probes",
                    category=ToolCategory.READ,
                ),
                ToolCallRecord(
                    name="query_kernel_state",
                    category=ToolCategory.READ,
                ),
            ],
            tool_results=[
                ToolResultRecord(
                    name="query_feature_probes",
                    category=ToolCategory.READ,
                    success=True,
                    payload={"success": True, "summary": "Feature probes: 1/1 families satisfied"},
                ),
                ToolResultRecord(
                    name="query_kernel_state",
                    category=ToolCategory.READ,
                    success=True,
                    payload={"success": True},
                ),
            ],
        )
    )
    run_state.latest_write_payload = {
        "session_state_persisted": True,
        "snapshot": {
            "geometry": {
                "solids": 1,
                "volume": 15000.0,
                "bbox": [10.0, 80.0, 80.0],
            }
        },
    }
    run_state.latest_validation = {
        "success": True,
        "is_complete": False,
        "summary": "Requirement validation has insufficient evidence",
        "blockers": [],
        "insufficient_evidence": True,
        "coverage_confidence": 0.2,
        "observation_tags": ["insufficient_evidence"],
    }
    run_state.add_agent_event(
        SimpleNamespace(  # type: ignore[arg-type]
            kind="validation_result",
            round_no=1,
            role="runtime",
            payload={
                "summary": "Requirement validation has insufficient evidence",
                "is_complete": False,
                "blockers": [],
            },
        )
    )

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=3,
        max_rounds=8,
        all_tool_names=[
            "query_geometry",
            "query_kernel_state",
            "query_feature_probes",
            "execute_build123d_probe",
            "validate_requirement",
            "finish_run",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is not None
    assert (
        policy.policy_id
        == "closure_validation_after_semantic_refresh_from_code_write"
    )
    assert "validate_requirement" in policy.allowed_tool_names
    assert "query_geometry" not in policy.allowed_tool_names


def test_non_progress_auto_validation_skips_when_latest_turn_already_ran_validate_requirement() -> None:
    run_state = RunState(
        session_id="session-non-progress-validation-skip",
        requirements={"description": "Create a centered orthogonal cross."},
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="semantic refresh",
            tool_calls=[
                ToolCallRecord(
                    name="query_feature_probes",
                    category=ToolCategory.READ,
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="query_feature_probes",
                    category=ToolCategory.READ,
                    success=True,
                    payload={"success": True},
                )
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=2,
            decision_summary="close with validation",
            tool_calls=[
                ToolCallRecord(
                    name="validate_requirement",
                    category=ToolCategory.JUDGE,
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="validate_requirement",
                    category=ToolCategory.JUDGE,
                    success=True,
                    payload={
                        "success": True,
                        "is_complete": False,
                        "summary": "Requirement validation has insufficient evidence",
                        "blockers": [],
                    },
                )
            ],
        )
    )

    assert _should_auto_validate_after_non_progress(run_state) is False
