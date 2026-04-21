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

def test_successful_create_sketch_keeps_next_turn_in_sketch_lane() -> None:
    run_state = RunState(
        session_id="session-open-sketch-lane-after-success",
        requirements={"description": "Create a rounded enclosure with a front thumb notch."},
    )
    host_payload = {
        "session_state_persisted": True,
        "snapshot": {
            "geometry": {
                "solids": 1,
                "volume": 37822.99,
                "bbox": [78.0, 56.0, 32.0],
            }
        },
    }
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="build host enclosure",
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
                    payload=host_payload,
                )
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=2,
            decision_summary="probe front face targets",
            tool_calls=[
                ToolCallRecord(
                    name="query_feature_probes",
                    category=ToolCategory.READ,
                ),
                ToolCallRecord(
                    name="query_topology",
                    category=ToolCategory.READ,
                ),
            ],
            tool_results=[
                ToolResultRecord(
                    name="query_feature_probes",
                    category=ToolCategory.READ,
                    success=True,
                    payload={
                        "success": True,
                        "probes": [
                            {
                                "family_id": "named_face_local_edit",
                                "recommended_next_tools": [
                                    "query_topology",
                                    "apply_cad_action",
                                ],
                                "grounding_blockers": [],
                            }
                        ],
                    },
                ),
                ToolResultRecord(
                    name="query_topology",
                    category=ToolCategory.READ,
                    success=True,
                    payload={
                        "success": True,
                        "matched_ref_ids": ["face:1:F_front"],
                        "candidate_sets": [
                            {
                                "candidate_id": "front_faces",
                                "label": "Front Faces",
                                "entity_type": "face",
                                "ref_ids": ["face:1:F_front"],
                            }
                        ],
                    },
                ),
            ],
        )
    )
    create_sketch_payload = {
        "success": True,
        "output_files": ["model.step", "geometry_info.json"],
        "snapshot": host_payload["snapshot"],
        "action_history": [
            {
                "step": 2,
                "action_type": "create_sketch",
                "action_params": {"face_ref": "face:1:F_front"},
            }
        ],
    }
    run_state.turns.append(
        TurnRecord(
            round_no=3,
            decision_summary="open face-attached sketch for local notch",
            tool_calls=[
                ToolCallRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    arguments={
                        "action_type": "create_sketch",
                        "action_params": {"face_ref": "face:1:F_front"},
                    },
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    success=True,
                    payload=create_sketch_payload,
                )
            ],
        )
    )

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=4,
        max_rounds=8,
        all_tool_names=[
            "apply_cad_action",
            "query_sketch",
            "query_topology",
            "query_kernel_state",
            "query_feature_probes",
            "validate_requirement",
            "execute_build123d",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is not None
    assert policy.policy_id == "continue_open_sketch_window_after_apply_action"
    assert policy.mode == "local_finish"
    assert policy.allowed_tool_names == ["apply_cad_action"]
    assert policy.preferred_tool_names == ["apply_cad_action"]

def test_open_sketch_window_after_empty_query_sketch_still_prefers_apply_only() -> None:
    host_payload = {
        "success": True,
        "session_state_persisted": True,
        "snapshot": {
            "geometry": {
                "solids": 1,
                "faces": 12,
                "edges": 28,
                "volume": 16000.0,
                "bbox": [62.0, 40.0, 14.0],
            }
        },
    }
    run_state = RunState(
        session_id="session-open-sketch-empty-query",
        requirements={
            "description": "Create a bracket with a topology-aware front-face notch."
        },
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="initial whole-part build",
            tool_calls=[ToolCallRecord(name="execute_build123d", category=ToolCategory.WRITE)],
            tool_results=[
                ToolResultRecord(
                    name="execute_build123d",
                    category=ToolCategory.WRITE,
                    success=True,
                    payload=host_payload,
                )
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=2,
            decision_summary="pick front face",
            tool_calls=[ToolCallRecord(name="query_topology", category=ToolCategory.READ)],
            tool_results=[
                ToolResultRecord(
                    name="query_topology",
                    category=ToolCategory.READ,
                    success=True,
                    payload={
                        "success": True,
                        "matched_ref_ids": ["face:1:F_front"],
                        "candidate_sets": [
                            {
                                "candidate_id": "front_faces",
                                "label": "Front Faces",
                                "entity_type": "face",
                                "ref_ids": ["face:1:F_front"],
                            }
                        ],
                    },
                )
            ],
        )
    )
    create_sketch_payload = {
        "success": True,
        "output_files": ["model.step", "geometry_info.json"],
        "snapshot": host_payload["snapshot"],
        "action_history": [
            {
                "step": 2,
                "action_type": "create_sketch",
                "action_params": {"face_ref": "face:1:F_front"},
            }
        ],
    }
    run_state.turns.append(
        TurnRecord(
            round_no=3,
            decision_summary="open topology-anchored sketch",
            tool_calls=[
                ToolCallRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    arguments={
                        "action_type": "create_sketch",
                        "action_params": {"face_ref": "face:1:F_front"},
                    },
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    success=True,
                    payload=create_sketch_payload,
                )
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=4,
            decision_summary="inspected empty sketch despite create_sketch",
            tool_calls=[ToolCallRecord(name="query_sketch", category=ToolCategory.READ)],
            tool_results=[
                ToolResultRecord(
                    name="query_sketch",
                    category=ToolCategory.READ,
                    success=True,
                    payload={
                        "success": True,
                        "step": 2,
                        "sketch_state": {
                            "plane": "XY",
                            "profile_refs": [],
                            "path_refs": [],
                        },
                    },
                )
            ],
        )
    )

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=5,
        max_rounds=8,
        all_tool_names=[
            "apply_cad_action",
            "query_sketch",
            "query_topology",
            "query_kernel_state",
            "query_feature_probes",
            "validate_requirement",
            "execute_build123d",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is not None
    assert policy.policy_id == "continue_open_sketch_window_after_apply_action"
    assert policy.mode == "local_finish"
    assert policy.allowed_tool_names == ["apply_cad_action"]
    assert policy.preferred_tool_names == ["apply_cad_action"]

def test_open_sketch_window_persists_after_query_sketch_when_profile_is_ready() -> None:
    host_payload = {
        "success": True,
        "session_state_persisted": True,
        "snapshot": {
            "geometry": {
                "solids": 1,
                "faces": 12,
                "edges": 28,
                "volume": 16000.0,
                "bbox": [62.0, 40.0, 14.0],
            }
        },
    }
    run_state = RunState(
        session_id="session-open-sketch-after-query",
        requirements={
            "description": "Create a bracket with a topology-aware front-face notch."
        },
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="initial whole-part build",
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
                    payload=host_payload,
                )
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=2,
            decision_summary="pick front face",
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
                        "success": True,
                        "matched_ref_ids": ["face:1:F_front"],
                        "candidate_sets": [
                            {
                                "candidate_id": "front_faces",
                                "label": "Front Faces",
                                "entity_type": "face",
                                "ref_ids": ["face:1:F_front"],
                            }
                        ],
                    },
                )
            ],
        )
    )
    create_sketch_payload = {
        "success": True,
        "output_files": ["model.step", "geometry_info.json"],
        "snapshot": host_payload["snapshot"],
        "action_history": [
            {
                "step": 2,
                "action_type": "create_sketch",
                "action_params": {"face_ref": "face:1:F_front"},
            }
        ],
    }
    run_state.turns.append(
        TurnRecord(
            round_no=3,
            decision_summary="open topology-anchored sketch",
            tool_calls=[
                ToolCallRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    arguments={
                        "action_type": "create_sketch",
                        "action_params": {"face_ref": "face:1:F_front"},
                    },
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    success=True,
                    payload=create_sketch_payload,
                )
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=4,
            decision_summary="inspect sketch before materializing the cut",
            tool_calls=[
                ToolCallRecord(
                    name="query_sketch",
                    category=ToolCategory.READ,
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="query_sketch",
                    category=ToolCategory.READ,
                    success=True,
                    payload={
                        "success": True,
                        "step": 2,
                        "sketch_state": {
                            "plane": "XY",
                            "profile_refs": ["profile:2:PR_1"],
                            "path_refs": [],
                        },
                    },
                )
            ],
        )
    )

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=5,
        max_rounds=8,
        all_tool_names=[
            "apply_cad_action",
            "query_sketch",
            "query_topology",
            "query_kernel_state",
            "query_feature_probes",
            "validate_requirement",
            "execute_build123d",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is not None
    assert policy.policy_id == "continue_open_sketch_window_after_apply_action"
    assert policy.mode == "local_finish"
    assert policy.allowed_tool_names == ["apply_cad_action", "query_sketch"]
    assert policy.preferred_tool_names == ["apply_cad_action", "query_sketch"]

def test_open_sketch_window_with_fresh_profile_prefers_apply_over_query_sketch() -> None:
    host_payload = {
        "success": True,
        "session_state_persisted": True,
        "snapshot": {
            "geometry": {
                "solids": 1,
                "faces": 12,
                "edges": 28,
                "volume": 16000.0,
                "bbox": [62.0, 40.0, 14.0],
            }
        },
    }
    run_state = RunState(
        session_id="session-open-sketch-fresh-profile",
        requirements={
            "description": "Create a bracket with a topology-aware front-face notch."
        },
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="initial whole-part build",
            tool_calls=[ToolCallRecord(name="execute_build123d", category=ToolCategory.WRITE)],
            tool_results=[
                ToolResultRecord(
                    name="execute_build123d",
                    category=ToolCategory.WRITE,
                    success=True,
                    payload=host_payload,
                )
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=2,
            decision_summary="pick front face",
            tool_calls=[ToolCallRecord(name="query_topology", category=ToolCategory.READ)],
            tool_results=[
                ToolResultRecord(
                    name="query_topology",
                    category=ToolCategory.READ,
                    success=True,
                    payload={
                        "success": True,
                        "matched_ref_ids": ["face:1:F_front"],
                        "candidate_sets": [
                            {
                                "candidate_id": "front_faces",
                                "label": "Front Faces",
                                "entity_type": "face",
                                "preferred_ref_id": "face:1:F_front",
                                "ref_ids": ["face:1:F_front"],
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
            decision_summary="open topology-anchored sketch",
            tool_calls=[
                ToolCallRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    arguments={
                        "action_type": "create_sketch",
                        "action_params": {"face_ref": "face:1:F_front"},
                    },
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    success=True,
                    payload={
                        "success": True,
                        "output_files": ["model.step", "geometry_info.json"],
                        "snapshot": host_payload["snapshot"],
                        "action_history": [
                            {
                                "step": 2,
                                "action_type": "create_sketch",
                                "action_params": {"face_ref": "face:1:F_front"},
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
            decision_summary="add rectangle profile to open sketch",
            tool_calls=[
                ToolCallRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    arguments={
                        "action_type": "add_rectangle",
                        "action_params": {"width": 7.0, "height": 4.0, "centered": True},
                    },
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    success=True,
                    payload={
                        "success": True,
                        "output_files": ["model.step", "geometry_info.json"],
                        "snapshot": host_payload["snapshot"],
                        "action_history": [
                            {
                                "step": 3,
                                "action_type": "add_rectangle",
                                "action_params": {"width": 7.0, "height": 4.0, "centered": True},
                            }
                        ],
                    },
                )
            ],
        )
    )

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=5,
        max_rounds=8,
        all_tool_names=[
            "apply_cad_action",
            "query_sketch",
            "query_topology",
            "query_kernel_state",
            "query_feature_probes",
            "validate_requirement",
            "execute_build123d",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is not None
    assert policy.policy_id == "continue_open_sketch_window_after_apply_action"
    assert policy.mode == "local_finish"
    assert policy.allowed_tool_names == ["apply_cad_action", "query_sketch"]
    assert policy.preferred_tool_names == ["apply_cad_action", "query_sketch"]

def test_open_sketch_window_under_critical_budget_exits_to_code_first_escape() -> None:
    host_payload = {
        "success": True,
        "session_state_persisted": True,
        "snapshot": {
            "geometry": {
                "solids": 1,
                "faces": 12,
                "edges": 28,
                "volume": 16000.0,
                "bbox": [62.0, 40.0, 14.0],
            }
        },
    }
    run_state = RunState(
        session_id="session-open-sketch-critical-budget",
        requirements={
            "description": "Create a bracket with a front-face recess and remaining hole features."
        },
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="initial whole-part build",
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
                    payload=host_payload,
                )
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=2,
            decision_summary="pick front face",
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
                        "success": True,
                        "matched_ref_ids": ["face:1:F_front"],
                        "candidate_sets": [
                            {
                                "candidate_id": "front_faces",
                                "label": "Front Faces",
                                "entity_type": "face",
                                "preferred_ref_id": "face:1:F_front",
                                "ref_ids": ["face:1:F_front"],
                            }
                        ],
                    },
                )
            ],
        )
    )
    create_sketch_payload = {
        "success": True,
        "output_files": ["model.step", "geometry_info.json"],
        "snapshot": host_payload["snapshot"],
        "action_history": [
            {
                "step": 2,
                "action_type": "create_sketch",
                "action_params": {"face_ref": "face:1:F_front"},
            }
        ],
    }
    run_state.turns.append(
        TurnRecord(
            round_no=3,
            decision_summary="open topology-anchored sketch",
            tool_calls=[
                ToolCallRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    arguments={
                        "action_type": "create_sketch",
                        "action_params": {"face_ref": "face:1:F_front"},
                    },
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    success=True,
                    payload=create_sketch_payload,
                )
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=4,
            decision_summary="inspect open sketch but it is still empty",
            tool_calls=[
                ToolCallRecord(
                    name="query_sketch",
                    category=ToolCategory.READ,
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="query_sketch",
                    category=ToolCategory.READ,
                    success=True,
                    payload={
                        "success": True,
                        "step": 2,
                        "sketch_state": {
                            "plane": "XY",
                            "profile_refs": [],
                            "path_refs": [],
                        },
                    },
                )
            ],
        )
    )

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=5,
        max_rounds=5,
        all_tool_names=[
            "apply_cad_action",
            "query_sketch",
            "query_topology",
            "query_kernel_state",
            "query_feature_probes",
            "validate_requirement",
            "execute_build123d",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is not None
    assert policy.policy_id == "code_escape_after_open_sketch_window_under_budget"
    assert policy.mode == "code_first"

def test_open_sketch_window_with_empty_sketch_and_two_rounds_left_exits_to_code_first_escape() -> None:
    host_payload = {
        "success": True,
        "session_state_persisted": True,
        "snapshot": {
            "geometry": {
                "solids": 1,
                "faces": 12,
                "edges": 28,
                "volume": 16000.0,
                "bbox": [62.0, 40.0, 14.0],
            }
        },
    }
    run_state = RunState(
        session_id="session-open-sketch-two-rounds-left",
        requirements={
            "description": "Create a bracket with a front-face recess and remaining notch detail."
        },
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="initial whole-part build",
            tool_calls=[ToolCallRecord(name="execute_build123d", category=ToolCategory.WRITE)],
            tool_results=[
                ToolResultRecord(
                    name="execute_build123d",
                    category=ToolCategory.WRITE,
                    success=True,
                    payload=host_payload,
                )
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=2,
            decision_summary="pick front face",
            tool_calls=[ToolCallRecord(name="query_topology", category=ToolCategory.READ)],
            tool_results=[
                ToolResultRecord(
                    name="query_topology",
                    category=ToolCategory.READ,
                    success=True,
                    payload={
                        "success": True,
                        "matched_ref_ids": ["face:1:F_front"],
                        "candidate_sets": [
                            {
                                "candidate_id": "front_faces",
                                "label": "Front Faces",
                                "entity_type": "face",
                                "preferred_ref_id": "face:1:F_front",
                                "ref_ids": ["face:1:F_front"],
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
            decision_summary="open topology-anchored sketch",
            tool_calls=[
                ToolCallRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    arguments={
                        "action_type": "create_sketch",
                        "action_params": {"face_ref": "face:1:F_front"},
                    },
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    success=True,
                    payload={
                        "success": True,
                        "output_files": ["model.step", "geometry_info.json"],
                        "snapshot": host_payload["snapshot"],
                        "action_history": [
                            {
                                "step": 2,
                                "action_type": "create_sketch",
                                "action_params": {"face_ref": "face:1:F_front"},
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
            decision_summary="refresh semantic state",
            tool_calls=[ToolCallRecord(name="query_feature_probes", category=ToolCategory.READ)],
            tool_results=[
                ToolResultRecord(
                    name="query_feature_probes",
                    category=ToolCategory.READ,
                    success=True,
                    payload={"success": True, "probes": []},
                )
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=5,
            decision_summary="refresh kernel state",
            tool_calls=[ToolCallRecord(name="query_kernel_state", category=ToolCategory.READ)],
            tool_results=[
                ToolResultRecord(
                    name="query_kernel_state",
                    category=ToolCategory.READ,
                    success=True,
                    payload={"success": True},
                )
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=6,
            decision_summary="inspect empty sketch",
            tool_calls=[ToolCallRecord(name="query_sketch", category=ToolCategory.READ)],
            tool_results=[
                ToolResultRecord(
                    name="query_sketch",
                    category=ToolCategory.READ,
                    success=True,
                    payload={
                        "success": True,
                        "step": 2,
                        "sketch_state": {
                            "plane": "XY",
                            "profile_refs": [],
                            "path_refs": [],
                        },
                    },
                )
            ],
        )
    )

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=7,
        max_rounds=8,
        all_tool_names=[
            "apply_cad_action",
            "query_sketch",
            "query_topology",
            "query_kernel_state",
            "query_feature_probes",
            "validate_requirement",
            "execute_build123d",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is not None
    assert policy.policy_id == "code_escape_after_open_sketch_window_under_budget"
    assert policy.mode == "code_first"
    assert "execute_build123d" in policy.allowed_tool_names
    assert "apply_cad_action" not in policy.allowed_tool_names
    assert policy.preferred_tool_names[0] == "execute_build123d"
