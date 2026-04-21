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

def test_last_round_after_successful_local_finish_semantic_refresh_reopens_repair_lane() -> None:
    run_state = RunState(
        session_id="session-local-finish-semantic-refresh-last-round-repair",
        requirements={
            "description": (
                "Create a service bracket with countersunk mounting holes and a front-face local recess."
            )
        },
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="first whole-part build succeeds",
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
                                "volume": 34827.4,
                                "bbox": [66.0, 42.0, 16.0],
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
            decision_summary="validation still reports countersink blockers",
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
                    payload={
                        "success": True,
                        "is_complete": False,
                        "summary": "Requirement validation has 3 blocker(s)",
                        "blockers": [
                            "feature_countersink",
                            "two_mounting_holes_on_the_bottom_face",
                            "countersinks_on_the_mounting_holes",
                        ],
                        "blocker_taxonomy": [
                            {
                                "blocker_id": "feature_countersink",
                                "family_ids": ["explicit_anchor_hole"],
                            }
                        ],
                    },
                )
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=3,
            decision_summary="feature probes still recommend topology-aware finishing",
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
                        "probes": [
                            {
                                "family": "explicit_anchor_hole",
                                "success": False,
                                "recommended_next_tools": [
                                    "query_topology",
                                    "apply_cad_action",
                                ],
                            },
                            {
                                "family": "named_face_local_edit",
                                "success": True,
                                "recommended_next_tools": [
                                    "query_topology",
                                    "apply_cad_action",
                                ],
                            },
                        ]
                    },
                )
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=4,
            decision_summary="local finish writes countersunk holes onto the mounting face",
            tool_calls=[
                ToolCallRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    arguments={
                        "action_type": "hole",
                        "action_params": {
                            "face_ref": "face:1:F_mount",
                            "hole_type": "countersink",
                        },
                    },
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    success=True,
                    payload={
                        "session_state_persisted": True,
                        "snapshot": {
                            "geometry": {
                                "solids": 1,
                                "volume": 33944.6,
                                "bbox": [66.0, 42.0, 16.0],
                            }
                        },
                    },
                )
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=5,
            decision_summary="semantic refresh returns fresh topology after the successful local finish",
            tool_calls=[
                ToolCallRecord(
                    name="query_topology",
                    category=ToolCategory.READ,
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="query_topology",
                    category=ToolCategory.READ,
                    success=True,
                    payload={
                        "matched_ref_ids": ["face:2:F_front"],
                        "candidate_sets": [
                            {
                                "candidate_id": "front_faces",
                                "ref_ids": ["face:2:F_front"],
                            }
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
                "volume": 33944.6,
                "bbox": [66.0, 42.0, 16.0],
            }
        },
    }
    run_state.latest_validation = {
        "success": True,
        "is_complete": False,
        "summary": "Requirement validation has 3 blocker(s)",
        "blockers": [
            "feature_countersink",
            "two_mounting_holes_on_the_bottom_face",
            "countersinks_on_the_mounting_holes",
        ],
        "blocker_taxonomy": [
            {
                "blocker_id": "feature_countersink",
                "family_ids": ["explicit_anchor_hole"],
            }
        ],
    }
    run_state.add_agent_event(
        SimpleNamespace(  # type: ignore[arg-type]
            kind="validation_result",
            round_no=2,
            role="runtime",
            payload={
                "summary": "Requirement validation has 3 blocker(s)",
                "is_complete": False,
                "blockers": [
                    "feature_countersink",
                    "two_mounting_holes_on_the_bottom_face",
                    "countersinks_on_the_mounting_holes",
                ],
            },
        )
    )
    run_state.feature_graph = SimpleNamespace(
        nodes={
            "feature.body": SimpleNamespace(
                node_id="feature.body",
                kind="feature",
                status="satisfied",
            ),
            "feature.explicit_anchor_hole": SimpleNamespace(
                node_id="feature.explicit_anchor_hole",
                kind="feature",
                status="blocked",
            ),
            "feature.named_face_local_edit": SimpleNamespace(
                node_id="feature.named_face_local_edit",
                kind="feature",
                status="blocked",
            ),
        },
        repair_patches={"patch-1": _UnderGroundedPatch()},
        repair_packets={},
        feature_instances={
            "instance.explicit_anchor_hole.feature_countersink": SimpleNamespace(
                family_id="explicit_anchor_hole",
                status="blocked",
            ),
            "instance.explicit_anchor_hole.two_mounting_holes": SimpleNamespace(
                family_id="explicit_anchor_hole",
                status="blocked",
            ),
        },
    )

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=6,
        max_rounds=6,
        all_tool_names=[
            "execute_build123d",
            "apply_cad_action",
            "validate_requirement",
            "query_topology",
            "query_kernel_state",
            "query_feature_probes",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is not None
    assert policy.policy_id == "repair_after_local_finish_semantic_refresh_under_budget"
    assert policy.mode == "code_repair"
    assert policy.allowed_tool_names == ["execute_build123d"]
    assert policy.preferred_tool_names == ["execute_build123d"]

def test_last_round_after_successful_local_finish_semantic_refresh_falls_back_to_code_escape_without_patch() -> None:
    run_state = RunState(
        session_id="session-local-finish-semantic-refresh-last-round-code-escape",
        requirements={
            "description": (
                "Create a service bracket with countersunk mounting holes and a front-face local recess."
            )
        },
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="first whole-part build succeeds",
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
                                "volume": 34827.4,
                                "bbox": [66.0, 42.0, 16.0],
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
            decision_summary="feature probes narrow the family",
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
                        "probes": [
                            {
                                "family": "explicit_anchor_hole",
                                "success": False,
                                "recommended_next_tools": [
                                    "query_topology",
                                    "apply_cad_action",
                                ],
                            }
                        ]
                    },
                )
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=3,
            decision_summary="validation still reports countersink blockers",
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
                    payload={
                        "success": True,
                        "is_complete": False,
                        "summary": "Requirement validation has 3 blocker(s)",
                        "blockers": [
                            "feature_local_anchor_count_alignment",
                            "two_mounting_holes_on_the_bottom_face",
                            "countersinks_on_the_mounting_holes",
                        ],
                        "blocker_taxonomy": [
                            {
                                "blocker_id": "two_mounting_holes_on_the_bottom_face",
                                "family_ids": ["explicit_anchor_hole"],
                            }
                        ],
                    },
                )
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=4,
            decision_summary="local finish writes the hole feature on the exact host face",
            tool_calls=[
                ToolCallRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    arguments={
                        "action_type": "hole",
                        "action_params": {
                            "face_ref": "face:1:F_mount",
                            "hole_type": "countersink",
                        },
                    },
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    success=True,
                    payload={
                        "session_state_persisted": True,
                        "snapshot": {
                            "geometry": {
                                "solids": 1,
                                "volume": 33944.6,
                                "bbox": [66.0, 42.0, 16.0],
                            }
                        },
                    },
                )
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=5,
            decision_summary="semantic refresh re-reads geometry and front-face topology",
            tool_calls=[
                ToolCallRecord(
                    name="query_geometry",
                    category=ToolCategory.READ,
                ),
                ToolCallRecord(
                    name="query_topology",
                    category=ToolCategory.READ,
                ),
            ],
            tool_results=[
                ToolResultRecord(
                    name="query_geometry",
                    category=ToolCategory.READ,
                    success=True,
                    payload={"success": True},
                ),
                ToolResultRecord(
                    name="query_topology",
                    category=ToolCategory.READ,
                    success=True,
                    payload={
                        "matched_ref_ids": ["face:2:F_front"],
                        "candidate_sets": [
                            {
                                "candidate_id": "front_faces",
                                "ref_ids": ["face:2:F_front"],
                            }
                        ],
                    },
                ),
            ],
        )
    )
    run_state.latest_write_payload = {
        "session_state_persisted": True,
        "snapshot": {
            "geometry": {
                "solids": 1,
                "volume": 33944.6,
                "bbox": [66.0, 42.0, 16.0],
            }
        },
    }
    run_state.latest_validation = {
        "success": True,
        "is_complete": False,
        "summary": "Requirement validation has 3 blocker(s)",
        "blockers": [
            "feature_local_anchor_count_alignment",
            "two_mounting_holes_on_the_bottom_face",
            "countersinks_on_the_mounting_holes",
        ],
        "blocker_taxonomy": [
            {
                "blocker_id": "two_mounting_holes_on_the_bottom_face",
                "family_ids": ["explicit_anchor_hole"],
            }
        ],
    }
    run_state.add_agent_event(
        SimpleNamespace(  # type: ignore[arg-type]
            kind="validation_result",
            round_no=3,
            role="runtime",
            payload={
                "summary": "Requirement validation has 3 blocker(s)",
                "is_complete": False,
                "blockers": [
                    "feature_local_anchor_count_alignment",
                    "two_mounting_holes_on_the_bottom_face",
                    "countersinks_on_the_mounting_holes",
                ],
            },
        )
    )

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=6,
        max_rounds=6,
        all_tool_names=[
            "execute_build123d",
            "apply_cad_action",
            "validate_requirement",
            "query_topology",
            "query_geometry",
            "query_kernel_state",
            "query_feature_probes",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is not None
    assert policy.policy_id == "code_escape_after_local_finish_semantic_refresh_under_budget"
    assert policy.mode == "code_repair"
    assert policy.allowed_tool_names == ["execute_build123d"]
    assert policy.preferred_tool_names == ["execute_build123d"]

def test_followup_local_finish_after_existing_whole_part_solid_does_not_retrigger_first_solid_code_escape() -> None:
    run_state = RunState(
        session_id="session-followup-local-finish-after-existing-whole-part-solid",
        requirements={
            "description": "Create a bracket with a stable host body, then continue topology-aware local finishing."
        },
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="whole-part build already produced a stable solid host",
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
            decision_summary="a later local finish also succeeds, but this is not the first stable solid",
            tool_calls=[
                ToolCallRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    success=True,
                    payload={
                        "session_state_persisted": True,
                        "snapshot": {
                            "geometry": {
                                "solids": 1,
                                "volume": 11880.0,
                                "bbox": [62.0, 40.0, 14.0],
                            }
                        },
                    },
                )
            ],
        )
    )
    run_state.feature_graph = SimpleNamespace(
        nodes={
            "feature.body": SimpleNamespace(
                node_id="feature.body",
                kind="feature",
                status="satisfied",
            ),
            "feature.local_holes": SimpleNamespace(
                node_id="feature.local_holes",
                kind="feature",
                status="blocked",
            ),
            "feature.edge_finish": SimpleNamespace(
                node_id="feature.edge_finish",
                kind="feature",
                status="blocked",
            ),
        }
    )

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=3,
        max_rounds=4,
        all_tool_names=[
            "execute_build123d",
            "apply_cad_action",
            "query_topology",
            "query_kernel_state",
            "query_feature_probes",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is None or policy.policy_id != "code_first_after_feature_budget_risk"

def test_failed_local_action_attempts_do_not_count_as_feature_budget_escape_writes() -> None:
    run_state = RunState(
        session_id="session-local-finish-budget-risk-failed-attempts",
        requirements={
            "description": "Create a bracket with a stable host, then continue topology-aware local finishing."
        },
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="whole-part build already produced a stable solid host",
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
    for round_no in (2, 3, 4):
        run_state.turns.append(
            TurnRecord(
                round_no=round_no,
                decision_summary="read evidence before local finishing",
                tool_calls=[
                    ToolCallRecord(
                        name="query_topology",
                        category=ToolCategory.READ,
                    )
                ],
                tool_results=[
                    ToolResultRecord(
                        name="query_topology",
                        category=ToolCategory.READ,
                        success=True,
                        payload={"candidate_sets": {"top_faces": {"ref_ids": ["face:1:F_top"]}}},
                    )
                ],
            )
        )
    for round_no, error_text in (
        (
            5,
            "apply_cad_action preflight failed: hole must use face_ref from latest query_topology instead of face='bottom' during local_finish",
        ),
        (
            6,
            "apply_cad_action preflight failed: modify_action is not allowed while the turn_tool_policy is in local_finish mode",
        ),
    ):
        run_state.turns.append(
            TurnRecord(
                round_no=round_no,
                decision_summary="failed local action attempt",
                tool_calls=[
                    ToolCallRecord(
                        name="apply_cad_action",
                        category=ToolCategory.WRITE,
                    )
                ],
                tool_results=[
                    ToolResultRecord(
                        name="apply_cad_action",
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
            round_no=7,
            decision_summary="local countersink finally succeeded on the target face",
            tool_calls=[
                ToolCallRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    success=True,
                    payload={
                        "session_state_persisted": True,
                        "snapshot": {
                            "geometry": {
                                "solids": 1,
                                "volume": 11880.0,
                                "bbox": [62.0, 40.0, 14.0],
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
                "volume": 11880.0,
                "bbox": [62.0, 40.0, 14.0],
            }
        },
    }
    run_state.feature_graph = SimpleNamespace(
        nodes={
            "feature.body": SimpleNamespace(
                node_id="feature.body",
                kind="feature",
                status="satisfied",
            ),
            "feature.local_holes": SimpleNamespace(
                node_id="feature.local_holes",
                kind="feature",
                status="blocked",
            ),
            "feature.edge_finish": SimpleNamespace(
                node_id="feature.edge_finish",
                kind="feature",
                status="blocked",
            ),
        }
    )

    risk = build_feature_chain_budget_risk(run_state, max_rounds=8)

    assert risk is None

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=8,
        max_rounds=8,
        all_tool_names=[
            "execute_build123d",
            "apply_cad_action",
            "query_topology",
            "query_kernel_state",
            "query_feature_probes",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is None or policy.policy_id != "code_first_after_feature_budget_risk"

def test_last_round_after_validation_evidence_gap_reopens_code_repair_lane() -> None:
    run_state = RunState(
        session_id="session-last-round-code-repair",
        requirements={"description": "Create a rounded clamshell enclosure with a thumb notch."},
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="initial build",
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
                                "volume": 39513.79,
                                "bbox": [78.0, 60.8, 91.2],
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
            decision_summary="read family probes",
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
            round_no=3,
            decision_summary="rebuild with correct bbox",
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
                                "volume": 37858.14,
                                "bbox": [78.0, 56.0, 32.0],
                            }
                        },
                    },
                )
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=4,
            decision_summary="post-write validation still has mixed blocker/evidence gap",
            tool_calls=[],
            tool_results=[],
        )
    )
    run_state.latest_write_payload = {
        "session_state_persisted": True,
        "snapshot": {
            "geometry": {
                "solids": 1,
                "volume": 37858.14,
                "bbox": [78.0, 56.0, 32.0],
            }
        },
    }
    run_state.latest_validation = {
        "success": True,
        "is_complete": False,
        "summary": "Requirement validation has 3 core blocker(s)",
        "blockers": [
            "feature_notch_or_profile_cut",
            "keep_wall_thickness_near_2_4mm",
            "a_front_thumb_notch_about_10mm_wide",
        ],
        "insufficient_evidence": True,
        "coverage_confidence": 0.25,
        "decision_hints": ["inspect more geometry/topology evidence before completion"],
        "observation_tags": ["insufficient_evidence"],
    }
    run_state.add_agent_event(
        SimpleNamespace(  # type: ignore[arg-type]
            kind="validation_result",
            round_no=4,
            role="runtime",
            payload={
                "summary": "Requirement validation has 3 core blocker(s)",
                "is_complete": False,
                "blockers": [
                    "feature_notch_or_profile_cut",
                    "keep_wall_thickness_near_2_4mm",
                    "a_front_thumb_notch_about_10mm_wide",
                ],
            },
        )
    )

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=5,
        max_rounds=5,
        all_tool_names=[
            "execute_build123d",
            "query_kernel_state",
            "query_feature_probes",
            "execute_build123d_probe",
            "validate_requirement",
            "finish_run",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is not None
    assert policy.policy_id == "code_repair_last_round_after_validation_evidence_gap"
    assert policy.allowed_tool_names == ["execute_build123d", "query_kernel_state"]
    assert policy.preferred_tool_names == ["execute_build123d", "query_kernel_state"]
    assert "validate_requirement" not in policy.allowed_tool_names

def test_last_round_code_repair_after_repeated_validation_blockers_disallows_kernel_refresh() -> None:
    run_state = RunState(
        session_id="session-last-round-code-repair-no-kernel-refresh",
        requirements={
            "description": "Create a two-part rounded clamshell enclosure with a front thumb notch."
        },
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="feature probe refresh before repair",
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
                        "detected_families": ["general_geometry", "slots"],
                        "probes": [
                            {
                                "family": "general_geometry",
                                "success": False,
                                "grounding_blockers": ["bbox_dimension_mismatch"],
                            }
                        ],
                    },
                )
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=2,
            decision_summary="whole-part repair write",
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
                                "solids": 2,
                                "volume": 21000.0,
                                "bbox": [78.0, 61.0, 28.0],
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
                "solids": 2,
                "volume": 21000.0,
                "bbox": [78.0, 61.0, 28.0],
            }
        },
    }
    run_state.latest_validation = {
        "success": True,
        "is_complete": False,
        "summary": "Requirement validation has 3 core blocker(s)",
        "blockers": [
            "feature_notch_or_profile_cut",
            "create_a_two_part_rounded_clamshell_storage_enclosure_with_overall_dimensions_78mm_x_56mm_x_32mm",
            "keep_wall_thickness_near_2_4mm",
        ],
    }
    run_state.add_agent_event(
        SimpleNamespace(  # type: ignore[arg-type]
            kind="validation_result",
            round_no=2,
            role="runtime",
            payload={
                "summary": "Requirement validation has 3 core blocker(s)",
                "is_complete": False,
                "blockers": [
                    "feature_notch_or_profile_cut",
                    "create_a_two_part_rounded_clamshell_storage_enclosure_with_overall_dimensions_78mm_x_56mm_x_32mm",
                    "keep_wall_thickness_near_2_4mm",
                ],
            },
        )
    )

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=3,
        max_rounds=3,
        all_tool_names=[
            "execute_build123d",
            "query_kernel_state",
            "query_feature_probes",
            "query_topology",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is not None
    assert policy.policy_id == "code_repair_under_budget_after_repeated_validation_blockers"
    assert policy.allowed_tool_names == ["execute_build123d"]
    assert policy.preferred_tool_names == ["execute_build123d"]

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
