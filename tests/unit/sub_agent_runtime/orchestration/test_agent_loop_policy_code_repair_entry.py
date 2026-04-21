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

def test_repeated_build123d_api_lint_failure_prefers_supported_repair_packet_when_available() -> None:
    run_state = RunState(
        session_id="session-api-lint-packet",
        requirements={"description": "Create a rectangular plate with centered countersunk holes."},
        feature_graph=SimpleNamespace(
            repair_packets={
                "packet-explicit-anchor-hole": FamilyRepairPacket(
                    packet_id="packet-explicit-anchor-hole",
                    family_id="explicit_anchor_hole",
                    feature_instance_id="instance.explicit_anchor_hole.primary",
                    repair_mode="subtree_rebuild",
                    anchor_keys=["normalized_local_centers", "host_face"],
                    parameter_keys=["geometry_summary", "requested_centers"],
                    recipe_id="explicit_anchor_hole_centered_host_frame_array",
                )
            },
            repair_patches={},
            feature_instances={},
            nodes={},
        ),
    )
    for round_no, error_text in (
        (
            1,
            "execute_build123d preflight lint failed | Manual countersink / through-hole cutters inside an active BuildPart placement must use mode=Mode.SUBTRACT",
        ),
        (
            2,
            "execute_build123d preflight lint failed | Move CounterSinkHole(...) back into the active BuildPart",
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
            "execute_repair_packet",
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
    assert policy.policy_id == "repair_packet_after_repeated_concrete_code_failure"
    assert policy.allowed_tool_names == ["execute_repair_packet"]
    assert policy.preferred_tool_names == ["execute_repair_packet"]
    assert policy.mode == "code_repair"

def test_successful_whole_part_write_with_local_topology_sensitive_blockers_prefers_feature_probe_assessment() -> None:
    run_state = RunState(
        session_id="session-local-feature-probe-assessment",
        requirements={"description": "Create a rounded clamshell enclosure with a front notch and magnet recesses."},
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="successful whole-part build",
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
                                "volume": 38957.2,
                                "bbox": [78.0, 57.8, 32.75],
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
            decision_summary="post-write validation",
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
                        "blockers": [
                            "feature_target_face_subtractive_merge",
                            "feature_notch_or_profile_cut",
                            "keep_wall_thickness_near_2_4mm",
                            "add_four_corner_magnet_recesses_on_the_mating_faces",
                        ],
                        "blocker_taxonomy": [
                            {
                                "blocker_id": "feature_target_face_subtractive_merge",
                                "family_ids": ["named_face_local_edit"],
                            },
                            {
                                "blocker_id": "feature_notch_or_profile_cut",
                                "family_ids": ["slots"],
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
                "volume": 38957.2,
                "bbox": [78.0, 57.8, 32.75],
            }
        },
    }
    run_state.latest_validation = {
        "success": True,
        "is_complete": False,
        "summary": "Requirement validation has 4 blocker(s)",
        "blockers": [
            "feature_target_face_subtractive_merge",
            "feature_notch_or_profile_cut",
            "keep_wall_thickness_near_2_4mm",
            "add_four_corner_magnet_recesses_on_the_mating_faces",
        ],
        "blocker_taxonomy": [
            {
                "blocker_id": "feature_target_face_subtractive_merge",
                "family_ids": ["named_face_local_edit"],
            },
            {
                "blocker_id": "feature_notch_or_profile_cut",
                "family_ids": ["slots"],
            },
        ],
    }
    run_state.add_agent_event(
        SimpleNamespace(  # type: ignore[arg-type]
            kind="validation_result",
            round_no=2,
            role="runtime",
            payload={
                "summary": "Requirement validation has 4 blocker(s)",
                "is_complete": False,
                "blockers": [
                    "feature_target_face_subtractive_merge",
                    "feature_notch_or_profile_cut",
                    "keep_wall_thickness_near_2_4mm",
                    "add_four_corner_magnet_recesses_on_the_mating_faces",
                ],
            },
        )
    )
    run_state.feature_graph = SimpleNamespace(
        nodes={},
        bindings={},
        repair_packets={},
        repair_patches={"patch-1": _LocalFeatureProbePatch()},
        feature_instances={
            "instance.named_face_local_edit.magnet_recesses": SimpleNamespace(
                family_id="named_face_local_edit",
                status="blocked",
            ),
            "instance.slots.front_notch": SimpleNamespace(
                family_id="slots",
                status="blocked",
            ),
            "instance.general_geometry.wall_thickness": SimpleNamespace(
                family_id="general_geometry",
                status="blocked",
            ),
        },
    )

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=3,
        max_rounds=8,
        all_tool_names=[
            "execute_build123d",
            "query_kernel_state",
            "query_feature_probes",
            "query_topology",
            "execute_build123d_probe",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is not None
    assert policy.policy_id == "feature_probe_assessment_before_actionable_kernel_patch_repair"
    assert policy.mode == "graph_refresh"
    assert "query_feature_probes" in policy.allowed_tool_names
    assert "execute_build123d" not in policy.allowed_tool_names
    assert "query_feature_probes" in policy.preferred_tool_names[:2]

def test_successful_whole_part_write_with_supported_repair_packet_prefers_execute_repair_packet() -> None:
    run_state = RunState(
        session_id="session-supported-packet-after-write",
        requirements={"description": "Create a rectangular plate with four countersunk holes."},
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="successful whole-part build",
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
                                "volume": 47660.7,
                                "bbox": [100.0, 60.0, 8.0],
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
            decision_summary="post-write validation",
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
                            "feature_hole_position_alignment",
                            "feature_hole_exact_center_set",
                            "feature_local_anchor_alignment",
                        ],
                        "blocker_taxonomy": [
                            {
                                "blocker_id": "feature_hole_position_alignment",
                                "family_ids": ["explicit_anchor_hole"],
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
                "volume": 47660.7,
                "bbox": [100.0, 60.0, 8.0],
            }
        },
    }
    run_state.latest_validation = {
        "success": True,
        "is_complete": False,
        "summary": "Requirement validation has 3 blocker(s)",
        "blockers": [
            "feature_hole_position_alignment",
            "feature_hole_exact_center_set",
            "feature_local_anchor_alignment",
        ],
        "blocker_taxonomy": [
            {
                "blocker_id": "feature_hole_position_alignment",
                "family_ids": ["explicit_anchor_hole"],
            }
        ],
    }
    run_state.add_agent_event(
        SimpleNamespace(  # type: ignore[arg-type]
            kind="validation_result",
            round_no=2,
            role="runtime",
            payload=run_state.latest_validation,
        )
    )
    run_state.feature_graph = SimpleNamespace(
        nodes={},
        bindings={},
        repair_packets={
            "packet-explicit-anchor-hole": FamilyRepairPacket(
                packet_id="packet-explicit-anchor-hole",
                family_id="explicit_anchor_hole",
                feature_instance_id="instance.explicit_anchor_hole.primary",
                repair_mode="subtree_rebuild",
                anchor_keys=["normalized_local_centers", "host_face"],
                parameter_keys=["geometry_summary", "requested_centers"],
                recipe_id="explicit_anchor_hole_centered_host_frame_array",
            )
        },
        repair_patches={},
        feature_instances={},
    )

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=3,
        max_rounds=8,
        all_tool_names=[
            "execute_build123d",
            "execute_repair_packet",
            "query_kernel_state",
            "query_feature_probes",
            "execute_build123d_probe",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is not None
    assert policy.policy_id == "repair_from_actionable_kernel_patch_after_validation_blocker"
    assert policy.allowed_tool_names == ["execute_repair_packet"]
    assert policy.preferred_tool_names == ["execute_repair_packet"]
    assert policy.mode == "code_repair"
