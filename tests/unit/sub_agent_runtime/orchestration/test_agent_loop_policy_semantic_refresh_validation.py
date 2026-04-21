from types import SimpleNamespace

from sub_agent_runtime.orchestration.policy.shared import (
    _determine_turn_tool_policy,
    _filter_supported_round_tool_names,
    _infer_runtime_failure_cluster,
    _turn_has_successful_validation_completion,
    _local_finish_should_force_apply_after_topology_targeting,
    _latest_feature_probe_preferred_tools_for_turn,
    _payload_has_positive_session_backed_solid,
    _result_has_positive_session_backed_solid,
    _latest_actionable_kernel_patch,
    _semantic_refresh_allowed_tool_names_for_turn,
    _latest_validation_prefers_semantic_refresh,
    _should_auto_validate_after_post_write,
    _should_auto_validate_after_non_progress,
    _turn_policy_from_actionable_kernel_patch,
)
from sub_agent_runtime.prompting import V2ContextManager
from sub_agent_runtime.semantic_kernel import FamilyRepairPacket
from sub_agent_runtime.turn_state import (
    RunState,
    ToolCallRecord,
    ToolCategory,
    ToolResultRecord,
    TurnRecord,
    build_feature_chain_budget_risk,
)


class _LocalFeatureProbePatch:
    def __init__(self) -> None:
        self.stale = False
        self.repair_mode = "whole_part_rebuild"
        self.feature_instance_ids = [
            "instance.named_face_local_edit.magnet_recesses",
            "instance.slots.front_notch",
            "instance.general_geometry.wall_thickness",
        ]
        self.anchor_keys = ["bbox_min_span", "requested_thickness"]
        self.parameter_keys = [
            "bbox",
            "bbox_min_span",
            "bbox_max_span",
            "bbox_min",
            "bbox_max",
            "anchor_summary",
            "requested_thickness",
        ]
        self.repair_intent = "retarget_local_face_edit"


class _GeneralGeometryWholePartPacket:
    def __init__(self) -> None:
        self.stale = False
        self.repair_mode = "whole_part_rebuild"
        self.feature_instance_id = "instance.general_geometry.wall_thickness"
        self.family_id = "general_geometry"
        self.anchor_keys = [
            "bbox_min_span",
            "geometry_solids",
            "realized_bbox",
            "requested_dimensions",
            "requested_thickness",
        ]
        self.parameter_keys = [
            "requested_dimensions",
            "requested_thickness",
            "geometry_solids",
            "realized_bbox",
            "bbox_min_span",
            "bbox",
            "bbox_max_span",
            "bbox_min",
            "bbox_max",
            "anchor_summary",
        ]
        self.repair_intent = "rebuild_whole_part_geometry"

    def to_dict(self) -> dict[str, object]:
        return {
            "repair_mode": self.repair_mode,
            "feature_instance_id": self.feature_instance_id,
            "family_id": self.family_id,
            "anchor_keys": self.anchor_keys,
            "parameter_keys": self.parameter_keys,
            "repair_intent": self.repair_intent,
        }


class _UnderGroundedPacket:
    def __init__(self) -> None:
        self.stale = False
        self.repair_mode = "subtree_rebuild"
        self.feature_instance_id = "instance.explicit_anchor_hole.feature_countersink"
        self.family_id = "explicit_anchor_hole"
        self.anchor_keys = []
        self.parameter_keys = [
            "bbox",
            "bbox_min_span",
            "bbox_max_span",
            "bbox_min",
            "bbox_max",
            "anchor_summary",
        ]
        self.repair_intent = "restore_explicit_anchor_countersink"
        self.host_frame = {
            "frame_kind": "centered_bbox_xy",
            "host_face": "body.primary",
            "bbox": [62.0, 40.0],
        }
        self.target_anchor_summary = {}
        self.realized_anchor_summary = {}
        self.recipe_id = "explicit_anchor_hole_helper_contract_fallback"
        self.recipe_summary = (
            "Prefer helper-based countersink creation on the authoritative host body."
        )
        self.recipe_skeleton = {
            "mode": "subtree_rebuild_via_execute_build123d",
            "host_face": "body.primary",
            "workplane_frame": "centered_bbox_xy",
            "center_source_key": "derive_from_requirement_or_validation",
            "hole_call": "CounterSinkHole_or_Hole",
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "repair_mode": self.repair_mode,
            "feature_instance_id": self.feature_instance_id,
            "family_id": self.family_id,
            "anchor_keys": self.anchor_keys,
            "parameter_keys": self.parameter_keys,
            "repair_intent": self.repair_intent,
            "host_frame": self.host_frame,
            "target_anchor_summary": self.target_anchor_summary,
            "realized_anchor_summary": self.realized_anchor_summary,
            "recipe_id": self.recipe_id,
            "recipe_summary": self.recipe_summary,
            "recipe_skeleton": self.recipe_skeleton,
        }


class _UnderGroundedPatch:
    def __init__(self) -> None:
        self.stale = False
        self.repair_mode = "subtree_rebuild"
        self.feature_instance_ids = [
            "instance.explicit_anchor_hole.feature_countersink",
            "instance.explicit_anchor_hole.two_mounting_holes",
        ]
        self.anchor_keys = []
        self.parameter_keys = [
            "bbox",
            "bbox_min_span",
            "bbox_max_span",
            "bbox_min",
            "bbox_max",
            "anchor_summary",
        ]
        self.repair_intent = "restore_explicit_anchor_countersink"

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

def test_post_write_auto_validation_skips_open_sketch_window_after_create_sketch() -> None:
    payload = {
        "success": True,
        "output_files": ["model.step", "geometry_info.json"],
        "snapshot": {
            "geometry": {
                "solids": 1,
                "volume": 37822.99,
                "bbox": [78.0, 56.0, 32.0],
            }
        },
        "action_history": [
            {
                "step": 2,
                "action_type": "create_sketch",
                "action_params": {"face_ref": "face:2:F_front"},
            }
        ],
    }
    turn = TurnRecord(
        round_no=7,
        decision_summary="open front-face sketch for local notch",
        tool_calls=[
            ToolCallRecord(
                name="apply_cad_action",
                category=ToolCategory.WRITE,
                arguments={
                    "action_type": "create_sketch",
                    "action_params": {"face_ref": "face:2:F_front"},
                },
            )
        ],
        tool_results=[
            ToolResultRecord(
                name="apply_cad_action",
                category=ToolCategory.WRITE,
                success=True,
                payload=payload,
            )
        ],
    )
    run_state = RunState(
        session_id="session-skip-auto-validate-after-create-sketch",
        requirements={"description": "Create a rounded enclosure with a front thumb notch."},
        turns=[turn],
        latest_write_payload=payload,
        latest_step_file="model.step",
        latest_validation={
            "success": True,
            "is_complete": False,
            "blockers": ["feature_target_face_subtractive_merge"],
            "summary": "Front notch is still missing.",
        },
    )

    assert not _should_auto_validate_after_post_write(
        run_state=run_state,
        turn=turn,
        round_no=7,
        max_rounds=8,
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

def test_closure_policy_keeps_query_topology_open_when_local_finish_evidence_is_still_missing() -> None:
    run_state = RunState(
        session_id="session-topology-closure",
        requirements={"description": "Create a bracket with countersunk mounting holes."},
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="build bracket",
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
                                "volume": 12000.0,
                                "bbox": [62.0, 40.0, 14.0],
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
                    payload={"success": True},
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
                "volume": 12000.0,
                "bbox": [62.0, 40.0, 14.0],
            }
        },
    }
    run_state.latest_validation = {
        "success": True,
        "is_complete": False,
        "summary": "Requirement validation has insufficient evidence",
        "blockers": [],
        "insufficient_evidence": True,
        "coverage_confidence": 0.4,
        "observation_tags": ["insufficient_evidence", "clause:hole"],
        "repair_hints": ["query_topology", "query_feature_probes"],
        "blocker_taxonomy": [
            {
                "blocker_id": "feature_countersink",
                "family_ids": ["explicit_anchor_hole", "named_face_local_edit"],
                "recommended_repair_lane": "local_finish",
                "decision_hints": ["query_topology", "query_feature_probes"],
            }
        ],
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
            "query_topology",
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
    assert "query_topology" in policy.allowed_tool_names
    assert policy.preferred_tool_names[0] == "query_topology"
    assert "validate_requirement" in policy.allowed_tool_names

def test_semantic_refresh_with_fresh_topology_refs_promotes_local_finish_lane() -> None:
    run_state = RunState(
        session_id="session-local-finish-after-semantic-refresh",
        requirements={
            "description": "Create a bracket with a mounting-face countersink and local rim fillets."
        },
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="build bracket host",
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
                                "volume": 12000.0,
                                "bbox": [62.0, 40.0, 14.0],
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
            decision_summary="refresh topology and local-finish family evidence",
            tool_calls=[
                ToolCallRecord(
                    name="query_topology",
                    category=ToolCategory.READ,
                ),
                ToolCallRecord(
                    name="query_feature_probes",
                    category=ToolCategory.READ,
                ),
            ],
            tool_results=[
                ToolResultRecord(
                    name="query_topology",
                    category=ToolCategory.READ,
                    success=True,
                    payload={"candidate_sets": [{"candidate_id": "opening_rim_edges"}]},
                ),
                ToolResultRecord(
                    name="query_feature_probes",
                    category=ToolCategory.READ,
                    success=True,
                    payload={
                        "success": True,
                        "detected_families": [
                            "explicit_anchor_hole",
                            "named_face_local_edit",
                        ],
                        "probes": [
                            {
                                "family": "named_face_local_edit",
                                "success": True,
                                "recommended_next_tools": [
                                    "query_topology",
                                    "apply_cad_action",
                                ],
                            }
                        ],
                    },
                ),
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=3,
            decision_summary="validator still lacks final grounding",
            tool_calls=[
                ToolCallRecord(
                    name="validate_requirement",
                    category=ToolCategory.READ,
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="validate_requirement",
                    category=ToolCategory.READ,
                    success=True,
                    payload={"is_complete": False, "insufficient_evidence": True},
                )
            ],
        )
    )
    run_state.latest_write_payload = {
        "session_state_persisted": True,
        "snapshot": {
            "geometry": {
                "solids": 1,
                "volume": 12000.0,
                "bbox": [62.0, 40.0, 14.0],
            }
        },
    }
    run_state.latest_validation = {
        "success": True,
        "is_complete": False,
        "summary": "Need one local finish pass on the mounting face.",
        "blockers": [],
        "insufficient_evidence": True,
        "coverage_confidence": 0.45,
        "repair_hints": ["query_topology", "query_feature_probes"],
        "decision_hints": ["query_topology"],
    }
    run_state.add_agent_event(
        SimpleNamespace(  # type: ignore[arg-type]
            kind="validation_result",
            round_no=3,
            role="runtime",
            payload={
                "summary": "Need one local finish pass on the mounting face.",
                "is_complete": False,
                "blockers": [],
            },
        )
    )

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=4,
        max_rounds=8,
        all_tool_names=[
            "apply_cad_action",
            "query_topology",
            "query_kernel_state",
            "validate_requirement",
            "finish_run",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is not None
    assert policy.policy_id == "local_finish_after_read_stall_topology_refresh"
    assert policy.mode == "local_finish"
    assert policy.allowed_tool_names == [
        "apply_cad_action",
        "query_topology",
        "query_kernel_state",
    ]
    assert policy.preferred_tool_names[:2] == ["apply_cad_action", "query_topology"]

def test_budget_skipped_validation_after_semantic_refresh_keeps_followup_lane_open() -> None:
    run_state = RunState(
        session_id="session-budget-skipped-validation-followup",
        requirements={
            "description": (
                "Create a bracket with a front thumb notch and a topology-aware countersink on the mounting face."
            )
        },
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="build bracket host",
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
                                "volume": 18460.36,
                                "bbox": [62.0, 40.0, 14.0],
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
            decision_summary="refresh family evidence after budget-skipped validation",
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
                    payload={
                        "success": True,
                        "detected_families": [
                            "core_geometry",
                            "explicit_anchor_hole",
                            "named_face_local_edit",
                        ],
                        "probes": [
                            {
                                "family": "explicit_anchor_hole",
                                "success": True,
                                "recommended_next_tools": [
                                    "query_topology",
                                    "execute_build123d",
                                ],
                                "grounding_blockers": [
                                    "missing_expected_local_centers"
                                ],
                            },
                            {
                                "family": "named_face_local_edit",
                                "success": True,
                                "recommended_next_tools": [
                                    "query_topology",
                                    "apply_cad_action",
                                ],
                                "grounding_blockers": [],
                            },
                        ],
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
                "volume": 18460.36,
                "bbox": [62.0, 40.0, 14.0],
            }
        },
    }
    run_state.latest_validation = {
        "success": True,
        "is_complete": False,
        "summary": "Requirement validation has insufficient evidence",
        "blockers": [],
        "insufficient_evidence": True,
        "coverage_confidence": 0.6,
        "decision_hints": [
            "inspect count or placement with geometry/topology evidence",
            "validation_llm_skipped:estimated_prompt_budget_exceeded:7821/7000",
        ],
        "repair_hints": ["query_topology", "query_feature_probes"],
        "observation_tags": ["insufficient_evidence", "validation:llm_skipped"],
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
            "apply_cad_action",
            "query_topology",
            "query_feature_probes",
            "query_geometry",
            "query_kernel_state",
            "validate_requirement",
            "finish_run",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is not None
    assert (
        policy.policy_id
        == "followup_after_semantic_refresh_before_closure_validation_from_code_write"
    )
    assert "validate_requirement" not in policy.allowed_tool_names
    assert "finish_run" not in policy.allowed_tool_names
    assert policy.preferred_tool_names[:2] == ["query_topology", "apply_cad_action"]
