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

def test_feature_probe_assessment_uses_blocked_local_families_when_latest_packet_is_general_geometry() -> None:
    run_state = RunState(
        session_id="session-general-geometry-packet-local-family",
        requirements={
            "description": (
                "Create a rounded clamshell enclosure with a front notch, wall-thickness control, "
                "and corner magnet recesses."
            )
        },
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
                                "volume": 31422.45,
                                "bbox": [72.0, 101.5, 26.0],
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
                "volume": 31422.45,
                "bbox": [72.0, 101.5, 26.0],
            }
        },
    }
    run_state.latest_validation = {
        "success": True,
        "is_complete": False,
        "summary": "Requirement validation has 5 blocker(s)",
        "blockers": [
            "feature_notch_or_profile_cut",
            "keep_wall_thickness_near_2_0mm",
            "corner_magnet_slots",
            "a_thumb_notch",
            "include_two_part_lid_base_separation",
        ],
        "blocker_taxonomy": [
            {
                "blocker_id": "feature_notch_or_profile_cut",
                "family_ids": ["slots"],
            }
        ],
    }
    run_state.add_agent_event(
        SimpleNamespace(  # type: ignore[arg-type]
            kind="validation_result",
            round_no=1,
            role="runtime",
            payload={
                "summary": "Requirement validation has 5 blocker(s)",
                "is_complete": False,
                "blockers": [
                    "feature_notch_or_profile_cut",
                    "keep_wall_thickness_near_2_0mm",
                    "corner_magnet_slots",
                    "a_thumb_notch",
                    "include_two_part_lid_base_separation",
                ],
            },
        )
    )
    run_state.feature_graph = SimpleNamespace(
        nodes={},
        bindings={},
        repair_packets={"packet-1": _GeneralGeometryWholePartPacket()},
        repair_patches={},
        feature_instances={
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
        round_no=2,
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

def test_repeated_validation_blockers_with_general_geometry_packet_reopen_feature_probe_assessment() -> None:
    run_state = RunState(
        session_id="session-repeated-general-geometry-packet-local-family",
        requirements={
            "description": (
                "Create a two-part rounded enclosure with a front notch and magnet landing features."
            )
        },
    )
    for round_no, bbox in ((1, [72.0, 94.0, 26.0]), (2, [72.0, 101.5, 26.0])):
        run_state.turns.append(
            TurnRecord(
                round_no=round_no,
                decision_summary="whole-part rebuild",
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
                                    "volume": 31422.45,
                                    "bbox": bbox,
                                }
                            },
                        },
                    )
                ],
            )
        )
        run_state.add_agent_event(
            SimpleNamespace(  # type: ignore[arg-type]
                kind="validation_result",
                round_no=round_no,
                role="runtime",
                payload={
                    "summary": "Requirement validation has 5 blocker(s)",
                    "is_complete": False,
                    "blockers": [
                        "feature_notch_or_profile_cut",
                        "keep_wall_thickness_near_2_0mm",
                        "corner_magnet_slots",
                        "a_thumb_notch",
                        "include_two_part_lid_base_separation",
                    ],
                },
            )
        )
    run_state.latest_write_payload = {
        "session_state_persisted": True,
        "snapshot": {
            "geometry": {
                "solids": 1,
                "volume": 31422.45,
                "bbox": [72.0, 101.5, 26.0],
            }
        },
    }
    run_state.latest_validation = {
        "success": True,
        "is_complete": False,
        "summary": "Requirement validation has 5 blocker(s)",
        "blockers": [
            "feature_notch_or_profile_cut",
            "keep_wall_thickness_near_2_0mm",
            "corner_magnet_slots",
            "a_thumb_notch",
            "include_two_part_lid_base_separation",
        ],
        "blocker_taxonomy": [
            {
                "blocker_id": "feature_notch_or_profile_cut",
                "family_ids": ["slots"],
            }
        ],
    }
    run_state.feature_graph = SimpleNamespace(
        nodes={},
        bindings={},
        repair_packets={"packet-1": _GeneralGeometryWholePartPacket()},
        repair_patches={},
        feature_instances={
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
    assert (
        policy.policy_id
        == "feature_probe_assessment_after_repeated_validation_blocker_from_code_write"
    )
    assert policy.mode == "graph_refresh"
    assert "query_feature_probes" in policy.allowed_tool_names
    assert "execute_build123d" not in policy.allowed_tool_names
    assert "query_feature_probes" in policy.preferred_tool_names[:2]

def test_feature_probe_assessment_escalates_to_topology_refresh_before_repeating_probe() -> None:
    run_state = RunState(
        session_id="session-escalate-feature-probe-to-topology",
        requirements={
            "description": (
                "Create a rounded clamshell enclosure with corner magnet recesses and a front thumb notch."
            )
        },
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="build enclosure host",
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
                                "volume": 37308.74,
                                "bbox": [78.0, 56.0, 24.0],
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
            decision_summary="feature probe assessment",
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
                            "named_face_local_edit",
                            "slots",
                            "nested_hollow_section",
                        ],
                        "probes": [
                            {
                                "family": "named_face_local_edit",
                                "recommended_next_tools": [
                                    "query_topology",
                                    "apply_cad_action",
                                ],
                                "grounding_blockers": [
                                    "feature_target_face_subtractive_merge"
                                ],
                            },
                            {
                                "family": "slots",
                                "recommended_next_tools": [
                                    "query_topology",
                                    "query_feature_probes",
                                    "query_kernel_state",
                                ],
                                "grounding_blockers": [
                                    "feature_notch_or_profile_cut",
                                    "need_topology_host_selection",
                                ],
                                "anchor_summary": {
                                    "requires_topology_host_ranking": True,
                                },
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
                "solids": 2,
                "volume": 37308.74,
                "bbox": [78.0, 56.0, 24.0],
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
            round_no=1,
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
            "apply_cad_action",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is not None
    assert policy.policy_id == "topology_refresh_after_feature_probe_assessment_from_code_write"
    assert policy.mode == "graph_refresh"
    assert policy.allowed_tool_names == [
        "query_kernel_state",
        "query_topology",
    ]
    assert policy.preferred_tool_names == [
        "query_topology",
        "query_kernel_state",
    ]

def test_feature_probe_topology_refresh_can_preempt_code_repair_when_geometry_gap_is_moderate_and_topology_host_selection_is_needed() -> None:
    run_state = RunState(
        session_id="session-feature-probe-whole-part-gap",
        requirements={
            "description": (
                "Create a two-part rounded clamshell enclosure with overall dimensions 78mm x 56mm x 32mm, "
                "four corner magnet recesses, and a front thumb notch."
            )
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
                    payload={
                        "session_state_persisted": True,
                        "snapshot": {
                            "geometry": {
                                "solids": 3,
                                "volume": 20222.41,
                                "bbox": [78.0, 58.5, 35.5],
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
            decision_summary="feature probe assessment after first write",
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
                            "named_face_local_edit",
                            "general_geometry",
                            "slots",
                        ],
                        "probes": [
                            {
                                "family": "named_face_local_edit",
                                "recommended_next_tools": [
                                    "query_topology",
                                    "apply_cad_action",
                                ],
                                "grounding_blockers": [
                                    "feature_target_face_subtractive_merge"
                                ],
                            },
                            {
                                "family": "general_geometry",
                                "success": False,
                                "recommended_next_tools": [
                                    "query_snapshot",
                                    "query_geometry",
                                ],
                                "grounding_blockers": [
                                    "unexpected_part_count_for_requirement",
                                    "bbox_dimension_mismatch",
                                ],
                                "anchor_summary": {
                                    "solid_count": 3,
                                    "expected_part_count": 2,
                                    "expected_bbox": [78.0, 56.0, 32.0],
                                },
                            },
                            {
                                "family": "slots",
                                "recommended_next_tools": [
                                    "query_topology",
                                    "query_feature_probes",
                                    "query_kernel_state",
                                ],
                                "grounding_blockers": [
                                    "feature_notch_or_profile_cut",
                                    "need_topology_host_selection",
                                ],
                                "anchor_summary": {
                                    "requires_topology_host_ranking": True,
                                },
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
                "solids": 3,
                "volume": 20222.41,
                "bbox": [78.0, 58.5, 35.5],
            }
        },
    }
    run_state.latest_validation = {
        "success": True,
        "is_complete": False,
        "summary": "Requirement validation has 4 core blocker(s)",
        "blockers": [
            "feature_notch_or_profile_cut",
            "create_a_two_part_rounded_clamshell_storage_enclosure_with_overall_dimensions_78mm_x_56mm_x_32mm",
            "keep_wall_thickness_near_2_4mm",
            "add_four_corner_magnet_recesses_on_the_mating_faces",
        ],
        "blocker_taxonomy": [
            {
                "blocker_id": "feature_notch_or_profile_cut",
                "family_ids": ["slots"],
            },
            {
                "blocker_id": "create_a_two_part_rounded_clamshell_storage_enclosure_with_overall_dimensions_78mm_x_56mm_x_32mm",
                "family_ids": ["general_geometry"],
            },
        ],
    }
    run_state.add_agent_event(
        SimpleNamespace(  # type: ignore[arg-type]
            kind="validation_result",
            round_no=1,
            role="runtime",
            payload={
                "summary": "Requirement validation has 4 core blocker(s)",
                "is_complete": False,
                "blockers": [
                    "feature_notch_or_profile_cut",
                    "create_a_two_part_rounded_clamshell_storage_enclosure_with_overall_dimensions_78mm_x_56mm_x_32mm",
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
            "apply_cad_action",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is not None
    assert policy.policy_id == "topology_refresh_after_feature_probe_assessment_from_code_write"
    assert policy.mode == "graph_refresh"
    assert policy.allowed_tool_names == [
        "query_kernel_state",
        "query_topology",
    ]
    assert policy.preferred_tool_names == [
        "query_topology",
        "query_kernel_state",
    ]

def test_feature_probe_assessment_reopens_code_repair_on_last_round() -> None:
    run_state = RunState(
        session_id="session-feature-probe-last-round-repair",
        requirements={
            "description": (
                "Create a two-part rounded clamshell enclosure with overall dimensions 78mm x 56mm x 32mm, "
                "four corner magnet recesses, and a front thumb notch."
            )
        },
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="initial enclosure write",
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
                                "volume": 37308.74,
                                "bbox": [78.0, 56.0, 24.0],
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
            decision_summary="inspect localized blockers",
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
                        "probes": [
                            {
                                "family": "named_face_local_edit",
                                "recommended_next_tools": [
                                    "query_topology",
                                    "apply_cad_action",
                                ],
                                "grounding_blockers": [
                                    "feature_target_face_subtractive_merge"
                                ],
                            },
                            {
                                "family": "slots",
                                "recommended_next_tools": [
                                    "query_topology",
                                    "query_feature_probes",
                                    "query_kernel_state",
                                ],
                                "grounding_blockers": [
                                    "feature_notch_or_profile_cut",
                                    "need_topology_host_selection",
                                ],
                                "anchor_summary": {
                                    "requires_topology_host_ranking": True,
                                },
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
                "solids": 2,
                "volume": 37308.74,
                "bbox": [78.0, 56.0, 24.0],
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
            round_no=1,
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
        },
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
            "execute_build123d_probe",
            "apply_cad_action",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is not None
    assert policy.policy_id == "repair_last_round_after_feature_probe_assessment"
    assert policy.mode == "code_repair"
    assert policy.allowed_tool_names == ["execute_build123d"]
    assert policy.preferred_tool_names == ["execute_build123d"]

def test_feature_probe_general_geometry_gap_stays_on_code_repair_when_bbox_is_far_from_expected_envelope() -> None:
    run_state = RunState(
        session_id="session-feature-probe-whole-part-gap-severe",
        requirements={
            "description": (
                "Create a two-part rounded clamshell enclosure with overall dimensions 78mm x 56mm x 32mm, "
                "four corner magnet recesses, and a front thumb notch."
            )
        },
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="initial whole-part build with exploded pose",
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
                                "volume": 20222.41,
                                "bbox": [78.0, 56.0, 70.0],
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
            decision_summary="feature probe assessment after exploded layout",
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
                            "named_face_local_edit",
                            "general_geometry",
                            "slots",
                        ],
                        "probes": [
                            {
                                "family": "named_face_local_edit",
                                "recommended_next_tools": [
                                    "query_topology",
                                    "apply_cad_action",
                                ],
                                "grounding_blockers": [
                                    "feature_target_face_subtractive_merge"
                                ],
                            },
                            {
                                "family": "general_geometry",
                                "success": False,
                                "recommended_next_tools": [
                                    "query_snapshot",
                                    "query_geometry",
                                ],
                                "grounding_blockers": [
                                    "bbox_dimension_mismatch",
                                ],
                                "anchor_summary": {
                                    "solid_count": 2,
                                    "expected_part_count": 2,
                                    "expected_bbox": [78.0, 56.0, 32.0],
                                },
                            },
                            {
                                "family": "slots",
                                "recommended_next_tools": [
                                    "query_topology",
                                    "query_feature_probes",
                                    "query_kernel_state",
                                ],
                                "grounding_blockers": [
                                    "feature_notch_or_profile_cut",
                                    "need_topology_host_selection",
                                ],
                                "anchor_summary": {
                                    "requires_topology_host_ranking": True,
                                },
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
                "solids": 2,
                "volume": 20222.41,
                "bbox": [78.0, 56.0, 70.0],
            }
        },
    }
    run_state.latest_validation = {
        "success": True,
        "is_complete": False,
        "summary": "Requirement validation has 4 core blocker(s)",
        "blockers": [
            "feature_notch_or_profile_cut",
            "create_a_two_part_rounded_clamshell_storage_enclosure_with_overall_dimensions_78mm_x_56mm_x_32mm",
            "keep_wall_thickness_near_2_4mm",
            "add_four_corner_magnet_recesses_on_the_mating_faces",
        ],
        "blocker_taxonomy": [
            {
                "blocker_id": "feature_notch_or_profile_cut",
                "family_ids": ["slots"],
            },
            {
                "blocker_id": "create_a_two_part_rounded_clamshell_storage_enclosure_with_overall_dimensions_78mm_x_56mm_x_32mm",
                "family_ids": ["general_geometry"],
            },
        ],
    }
    run_state.add_agent_event(
        SimpleNamespace(  # type: ignore[arg-type]
            kind="validation_result",
            round_no=1,
            role="runtime",
            payload={
                "summary": "Requirement validation has 4 core blocker(s)",
                "is_complete": False,
                "blockers": [
                    "feature_notch_or_profile_cut",
                    "create_a_two_part_rounded_clamshell_storage_enclosure_with_overall_dimensions_78mm_x_56mm_x_32mm",
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
            "apply_cad_action",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is not None
    assert policy.policy_id == "code_repair_after_feature_probe_detected_whole_part_geometry_gap"
    assert policy.mode == "code_repair"
    assert policy.allowed_tool_names == ["execute_build123d"]

def test_feature_probe_topology_refresh_allows_fragmented_two_part_shell_when_bbox_is_close() -> None:
    run_state = RunState(
        session_id="session-feature-probe-fragmented-two-part-shell",
        requirements={
            "description": (
                "Create a two-part rounded clamshell enclosure with overall dimensions 78mm x 56mm x 32mm, "
                "four corner magnet recesses, and a front thumb notch."
            )
        },
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="initial whole-part build with fragmented shell geometry",
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
                                "solids": 4,
                                "volume": 43175.62,
                                "bbox": [80.0, 56.0, 32.0],
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
            decision_summary="feature probe assessment after fragmented shell write",
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
                            "named_face_local_edit",
                            "slots",
                            "nested_hollow_section",
                            "general_geometry",
                            "core_geometry",
                        ],
                        "probes": [
                            {
                                "family": "named_face_local_edit",
                                "recommended_next_tools": [
                                    "query_topology",
                                    "apply_cad_action",
                                ],
                                "grounding_blockers": [
                                    "feature_target_face_subtractive_merge"
                                ],
                                "anchor_summary": {
                                    "solid_count": 4,
                                    "expected_part_count": 2,
                                    "bbox": [80.0, 56.0, 32.0],
                                    "expected_bbox": [78.0, 56.0, 32.0],
                                },
                            },
                            {
                                "family": "slots",
                                "recommended_next_tools": [
                                    "query_topology",
                                    "query_feature_probes",
                                    "query_kernel_state",
                                ],
                                "grounding_blockers": [
                                    "feature_notch_or_profile_cut",
                                    "need_topology_host_selection",
                                ],
                                "anchor_summary": {
                                    "solid_count": 4,
                                    "expected_part_count": 2,
                                    "bbox": [80.0, 56.0, 32.0],
                                    "expected_bbox": [78.0, 56.0, 32.0],
                                    "requires_topology_host_ranking": True,
                                },
                            },
                            {
                                "family": "general_geometry",
                                "success": False,
                                "recommended_next_tools": [
                                    "query_snapshot",
                                    "query_geometry",
                                ],
                                "grounding_blockers": [
                                    "unexpected_part_count_for_requirement",
                                ],
                                "anchor_summary": {
                                    "solid_count": 4,
                                    "expected_part_count": 2,
                                    "bbox": [80.0, 56.0, 32.0],
                                    "expected_bbox": [78.0, 56.0, 32.0],
                                },
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
                "solids": 4,
                "volume": 43175.62,
                "bbox": [80.0, 56.0, 32.0],
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
            {
                "blocker_id": "keep_wall_thickness_near_2_4mm",
                "family_ids": ["general_geometry"],
            },
        ],
    }
    run_state.add_agent_event(
        SimpleNamespace(  # type: ignore[arg-type]
            kind="validation_result",
            round_no=1,
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
            "apply_cad_action",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is not None
    assert policy.policy_id == "topology_refresh_after_feature_probe_assessment_from_code_write"
    assert policy.mode == "graph_refresh"
    assert policy.allowed_tool_names == [
        "query_kernel_state",
        "query_topology",
    ]
    assert policy.preferred_tool_names == [
        "query_topology",
        "query_kernel_state",
    ]
