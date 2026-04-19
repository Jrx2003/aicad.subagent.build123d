from types import SimpleNamespace

from sub_agent_runtime.agent_loop_v2 import (
    _determine_turn_tool_policy,
    _infer_runtime_failure_cluster,
    _local_finish_should_force_apply_after_topology_targeting,
    _latest_feature_probe_preferred_tools_for_turn,
    _payload_has_positive_session_backed_solid,
    _result_has_positive_session_backed_solid,
    _latest_actionable_kernel_patch,
    _semantic_refresh_allowed_tool_names_for_turn,
    _latest_validation_prefers_semantic_refresh,
    _should_auto_validate_after_post_write,
    _should_auto_validate_after_non_progress,
)
from sub_agent_runtime.context_manager import V2ContextManager
from sub_agent_runtime.turn_state import (
    RunState,
    ToolCallRecord,
    ToolCategory,
    ToolResultRecord,
    TurnRecord,
    build_feature_chain_budget_risk,
)


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


def test_infer_runtime_failure_cluster_ignores_read_stall_history_after_successful_validation() -> None:
    run_state = RunState(
        session_id="session-successful-validation",
        requirements={"description": "Create a topology-aware local finish case."},
    )
    for round_no in range(1, 9):
        run_state.turns.append(
            TurnRecord(
                round_no=round_no,
                decision_summary="inspect" if round_no <= 4 else "write",
                tool_calls=[
                    ToolCallRecord(
                        name="query_topology" if round_no <= 4 else "apply_cad_action",
                        category=ToolCategory.READ if round_no <= 4 else ToolCategory.WRITE,
                    )
                ],
            )
        )
    run_state.latest_validation = {"success": True, "is_complete": True, "blockers": []}

    assert _infer_runtime_failure_cluster(run_state) is None


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


def test_first_concrete_build123d_api_lint_failure_stays_on_code_repair_lane() -> None:
    run_state = RunState(
        session_id="session-first-api-lint",
        requirements={"description": "Create a two-part hollow enclosure."},
    )
    error_text = (
        "execute_build123d preflight lint failed | Keep the active builder authoritative"
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
                    success=False,
                    payload={"failure_kind": "execute_build123d_api_lint_failure"},
                    error=error_text,
                )
            ],
            error=error_text,
        )
    )
    run_state.latest_write_payload = {"failure_kind": "execute_build123d_api_lint_failure"}

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=2,
        max_rounds=5,
        all_tool_names=[
            "execute_build123d",
            "query_kernel_state",
            "query_feature_probes",
            "execute_build123d_probe",
        ],
        previous_tool_failure_summary={
            "tool": "execute_build123d",
            "same_tool_failure_count": 1,
            "effective_failure_kind": "execute_build123d_api_lint_failure",
        },
    )

    assert policy is not None
    assert policy.policy_id == "code_repair_after_first_concrete_code_failure"
    assert policy.mode == "code_first_repair"
    assert policy.allowed_tool_names == ["execute_build123d", "query_kernel_state"]
    assert "execute_build123d_probe" not in policy.allowed_tool_names


def test_first_concrete_build123d_api_lint_failure_can_refresh_topology_when_latest_feature_probe_requires_host_selection() -> None:
    run_state = RunState(
        session_id="session-first-api-lint-with-topology-refresh",
        requirements={"description": "Create a rounded enclosure with a front thumb notch and side pocket."},
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="successful whole-part host build",
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
                                "volume": 18000.0,
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
            round_no=2,
            decision_summary="feature probe refresh",
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
                        "detected_families": ["slots", "named_face_local_edit"],
                        "probes": [
                            {
                                "family": "slots",
                                "recommended_next_tools": [
                                    "query_topology",
                                    "query_feature_probes",
                                    "query_kernel_state",
                                ],
                                "required_evidence_kinds": ["geometry", "topology"],
                                "anchor_summary": {"requires_topology_host_ranking": True},
                                "grounding_blockers": [
                                    "feature_notch_or_profile_cut",
                                    "need_topology_host_selection",
                                ],
                            },
                            {
                                "family": "named_face_local_edit",
                                "recommended_next_tools": [
                                    "query_topology",
                                    "apply_cad_action",
                                ],
                                "success": True,
                            },
                        ],
                    },
                )
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=3,
            decision_summary="whole-part rewrite failed preflight",
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
                    payload={"failure_kind": "execute_build123d_api_lint_failure"},
                    error="execute_build123d preflight lint failed | Rot(...) is not a context manager",
                )
            ],
            error="execute_build123d preflight lint failed | Rot(...) is not a context manager",
        )
    )
    run_state.latest_write_payload = {
        "session_state_persisted": True,
        "snapshot": {
            "geometry": {
                "solids": 1,
                "volume": 18000.0,
                "bbox": [78.0, 56.0, 32.0],
            }
        },
        "failure_kind": "execute_build123d_api_lint_failure",
    }

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=4,
        max_rounds=8,
        all_tool_names=[
            "execute_build123d",
            "query_kernel_state",
            "query_feature_probes",
            "query_topology",
            "apply_cad_action",
            "execute_build123d_probe",
        ],
        previous_tool_failure_summary={
            "tool": "execute_build123d",
            "same_tool_failure_count": 1,
            "effective_failure_kind": "execute_build123d_api_lint_failure",
        },
    )

    assert policy is not None
    assert policy.policy_id == "topology_refresh_after_first_concrete_code_failure"
    assert policy.mode == "graph_refresh"
    assert policy.allowed_tool_names == [
        "query_kernel_state",
        "query_topology",
    ]
    assert policy.preferred_tool_names[0] == "query_topology"
    assert "execute_build123d" not in policy.allowed_tool_names


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


def test_semantic_refresh_policy_allows_query_topology_when_validation_requests_local_finish_evidence() -> None:
    run_state = RunState(
        session_id="session-topology-semantic-refresh",
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
        round_no=2,
        max_rounds=8,
        all_tool_names=[
            "execute_build123d",
            "query_topology",
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
    assert "query_topology" in policy.allowed_tool_names
    assert policy.preferred_tool_names[0] == "query_topology"
    assert "validate_requirement" not in policy.allowed_tool_names


def test_under_grounded_kernel_patch_yields_semantic_refresh_for_local_feature_evidence_gap() -> None:
    run_state = RunState(
        session_id="session-under-grounded-kernel-patch",
        requirements={"description": "Create a bracket with two mounting holes and a countersink."},
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
    run_state.feature_graph = SimpleNamespace(
        nodes={},
        repair_packets={"packet-1": _UnderGroundedPacket()},
        repair_patches={"patch-1": _UnderGroundedPatch()},
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
    run_state.latest_validation = {
        "success": True,
        "is_complete": False,
        "summary": "Hole family still needs localized evidence before completion.",
        "blockers": [
            "feature_countersink",
            "two_mounting_holes",
            "a_countersink_on_the_mounting_face_so_that_a_topology_aware_local_finishing_pass_is_useful",
        ],
        "insufficient_evidence": True,
        "coverage_confidence": 0.8,
        "decision_hints": [
            "repair the contradicted clause before finishing",
            "inspect count or placement with geometry/topology evidence",
            "fallback_to_evidence_first_clause_interpretation",
        ],
        "repair_hints": [
            "query_topology",
            "query_feature_probes",
        ],
        "observation_tags": [
            "geometry:solid_present",
            "topology:index_available",
            "insufficient_evidence",
            "validation:feature_alignment",
        ],
        "clause_interpretations": [
            {
                "clause_id": "two_mounting_holes",
                "status": "contradicted",
                "evidence": (
                    "countersink_action=False, snapshot_countersink_geometry=False, "
                    "hole_feature=True, cone_like_face_present=True"
                ),
            },
            {
                "clause_id": "a_countersink_on_the_mounting_face_so_that_a_topology_aware_local_finishing_pass_is_useful",
                "status": "contradicted",
                "evidence": (
                    "countersink_action=False, snapshot_countersink_geometry=False, "
                    "hole_feature=True, cone_like_face_present=True"
                ),
            },
        ],
    }
    run_state.add_agent_event(
        SimpleNamespace(  # type: ignore[arg-type]
            kind="validation_result",
            round_no=1,
            role="runtime",
            payload={
                "summary": "Hole family still needs localized evidence before completion.",
                "is_complete": False,
                "blockers": [
                    "feature_countersink",
                    "two_mounting_holes",
                    "a_countersink_on_the_mounting_face_so_that_a_topology_aware_local_finishing_pass_is_useful",
                ],
                "insufficient_evidence": True,
            },
        )
    )

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=2,
        max_rounds=8,
        all_tool_names=[
            "execute_build123d",
            "query_topology",
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
        == "semantic_refresh_before_under_grounded_kernel_patch_for_local_feature_gap"
    )
    assert "query_topology" in policy.allowed_tool_names
    assert "query_feature_probes" in policy.allowed_tool_names
    assert "execute_build123d" not in policy.allowed_tool_names


def test_under_grounded_kernel_patch_reopens_code_repair_after_topology_refresh_under_short_budget() -> None:
    run_state = RunState(
        session_id="session-under-grounded-kernel-patch-short-budget",
        requirements={"description": "Create a bracket with two mounting holes and a countersink."},
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
            decision_summary="refresh topology",
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
                    payload={"success": True},
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
    run_state.feature_graph = SimpleNamespace(
        nodes={},
        repair_packets={"packet-1": _UnderGroundedPacket()},
        repair_patches={"patch-1": _UnderGroundedPatch()},
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
    run_state.latest_validation = {
        "success": True,
        "is_complete": False,
        "summary": "Hole family still needs localized evidence before completion.",
        "blockers": [
            "feature_countersink",
            "two_mounting_holes",
            "a_countersink_on_the_mounting_face_so_that_a_topology_aware_local_finishing_pass_is_useful",
        ],
        "insufficient_evidence": True,
        "coverage_confidence": 0.8,
        "decision_hints": [
            "repair the contradicted clause before finishing",
            "inspect count or placement with geometry/topology evidence",
        ],
        "repair_hints": [
            "query_topology",
            "query_feature_probes",
        ],
        "observation_tags": [
            "geometry:solid_present",
            "topology:host_selection_needed",
        ],
        "clause_interpretations": [
            {
                "clause_id": "two_mounting_holes",
                "status": "contradicted",
                "family_binding": "explicit_anchor_hole",
                "grounding_gap_reasons": [
                    "local_host_target_not_grounded",
                ],
                "required_evidence_kinds": ["geometry", "topology"],
                "repair_hints": ["query_topology"],
            }
        ],
    }
    run_state.add_agent_event(
        SimpleNamespace(  # type: ignore[arg-type]
            kind="validation_result",
            round_no=1,
            role="runtime",
            payload={
                "summary": "Hole family still needs localized evidence before completion.",
                "is_complete": False,
                "blockers": [
                    "feature_countersink",
                    "two_mounting_holes",
                    "a_countersink_on_the_mounting_face_so_that_a_topology_aware_local_finishing_pass_is_useful",
                ],
                "insufficient_evidence": True,
            },
        )
    )

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=3,
        max_rounds=4,
        all_tool_names=[
            "execute_build123d",
            "query_topology",
            "query_kernel_state",
            "query_feature_probes",
            "execute_build123d_probe",
            "validate_requirement",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is not None
    assert policy.policy_id == "repair_after_topology_refresh_under_budget"
    assert policy.allowed_tool_names == ["execute_build123d"]
    assert policy.preferred_tool_names == ["execute_build123d"]
    assert "query_feature_probes" not in policy.allowed_tool_names


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


def test_successful_artifactless_probe_requires_kernel_refresh_before_more_probe_turns() -> None:
    run_state = RunState(
        session_id="session-artifactless-probe-closure",
        requirements={
            "description": (
                "Create a two-part rounded clamshell enclosure with a hollow base and lid."
            )
        },
    )
    for round_no, error_text in (
        (
            1,
            "execute_build123d preflight lint failed | Keep the active builder authoritative",
        ),
        (
            2,
            "Exit code: 1 | Traceback (most recent call last):",
        ),
    ):
        run_state.turns.append(
            TurnRecord(
                round_no=round_no,
                decision_summary="repair enclosure shell",
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
            decision_summary="probe enclosure shell recipe",
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
        max_rounds=5,
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
    assert policy.policy_id == "kernel_refresh_after_successful_artifactless_probe"
    assert policy.mode == "semantic_refresh"
    assert policy.allowed_tool_names == ["query_kernel_state"]
    assert "execute_build123d_probe" not in policy.allowed_tool_names


def test_successful_non_persisted_probe_after_existing_semantic_refresh_reopens_code_repair() -> None:
    run_state = RunState(
        session_id="session-stale-probe-refresh",
        requirements={
            "description": (
                "Create a two-part rounded clamshell enclosure with a hollow base and lid."
            )
        },
    )
    for round_no, error_text in (
        (
            1,
            "execute_build123d preflight lint failed | Keep the active builder authoritative",
        ),
        (
            2,
            "Exit code: 1 | Traceback (most recent call last):",
        ),
    ):
        run_state.turns.append(
            TurnRecord(
                round_no=round_no,
                decision_summary="repair enclosure shell",
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
            decision_summary="refresh family evidence",
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
                    payload={"success": True, "probes": []},
                    error=None,
                )
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=4,
            decision_summary="probe repaired shell recipe",
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
                        "session_state_persisted": False,
                        "probe_summary": {
                            "success": True,
                            "bbox": [78.0, 56.0, 32.0],
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
        round_no=5,
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
            "effective_failure_kind": "execute_build123d_runtime_failure",
        },
    )

    assert policy is not None
    assert policy.mode == "code_first_repair"
    assert "execute_build123d" in policy.allowed_tool_names
    assert "query_feature_probes" not in policy.allowed_tool_names


def test_successful_probe_evidence_store_closes_probe_chain_even_without_turn_result() -> None:
    run_state = RunState(
        session_id="session-probe-evidence-only",
        requirements={
            "description": (
                "Create a two-part rounded clamshell enclosure with a hollow base and lid."
            )
        },
    )
    for round_no, error_text in (
        (
            1,
            "execute_build123d preflight lint failed | Keep the active builder authoritative",
        ),
        (
            2,
            "Exit code: 1 | Traceback (most recent call last):",
        ),
    ):
        run_state.turns.append(
            TurnRecord(
                round_no=round_no,
                decision_summary="repair enclosure shell",
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
            decision_summary="probe repaired shell recipe",
            tool_calls=[
                ToolCallRecord(
                    name="execute_build123d_probe",
                    category=ToolCategory.READ,
                )
            ],
            tool_results=[],
        )
    )
    run_state.evidence.update(
        tool_name="execute_build123d_probe",
        payload={
            "success": True,
            "session_state_persisted": False,
            "probe_summary": {
                "success": True,
                "bbox": [78.0, 56.0, 32.0],
                "actionable": False,
                "actionable_family_ids": [],
            },
        },
        round_no=3,
    )
    run_state.latest_write_payload = {}

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=4,
        max_rounds=6,
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
    assert policy.policy_id == "kernel_refresh_after_successful_artifactless_probe"
    assert policy.mode == "semantic_refresh"
    assert policy.allowed_tool_names == ["query_kernel_state"]


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


def test_local_finish_lane_forces_apply_cad_action_after_topology_candidates_exist() -> None:
    run_state = RunState(
        session_id="session-force-local-finish-after-topology",
        requirements={
            "description": (
                "Create a bracket with a mounting-face countersink and local edge fillets around the top opening."
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
            decision_summary="refresh topology and feature evidence",
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
                        "matched_ref_ids": [
                            "face:1:F_top",
                            "edge:1:E_top_outer_1",
                        ],
                        "candidate_sets": [
                            {
                                "candidate_id": "top_outer_edges",
                                "label": "Top Outer Edges",
                                "entity_type": "edge",
                                "ref_ids": ["edge:1:E_top_outer_1"],
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
            round_no=1,
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
        round_no=3,
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
    assert policy.policy_id == "apply_local_finish_after_topology_targeting"
    assert policy.allowed_tool_names == ["apply_cad_action"]


def test_local_finish_lane_forces_apply_even_with_remaining_validation_blockers() -> None:
    run_state = RunState(
        session_id="session-force-local-finish-after-topology-with-blockers",
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
                                "volume": 18000.0,
                                "bbox": [78.0, 56.0, 25.3],
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
            decision_summary="refresh topology and feature evidence",
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
                        "matched_ref_ids": [
                            "face:front:mating",
                            "edge:front:notch_lip",
                        ],
                        "candidate_sets": [
                            {
                                "candidate_id": "front_mating_faces",
                                "label": "Front Faces",
                                "entity_type": "face",
                                "ref_ids": ["face:front:mating"],
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
                "solids": 2,
                "volume": 18000.0,
                "bbox": [78.0, 56.0, 25.3],
            }
        },
    }
    run_state.latest_validation = {
        "success": True,
        "is_complete": False,
        "summary": "Requirement validation has 3 blocker(s)",
        "blockers": [
            "add_four_corner_magnet_recesses_on_the_mating_faces",
            "a_front_thumb_notch_about_10mm_wide",
            "keep_wall_thickness_near_2_4mm",
        ],
        "insufficient_evidence": True,
        "coverage_confidence": 0.4,
        "repair_hints": [
            "inspect count or placement with geometry/topology evidence",
            "query_topology",
            "query_feature_probes",
        ],
        "decision_hints": [
            "inspect count or placement with geometry/topology evidence",
            "query_topology",
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
                    "add_four_corner_magnet_recesses_on_the_mating_faces",
                    "a_front_thumb_notch_about_10mm_wide",
                    "keep_wall_thickness_near_2_4mm",
                ],
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
            "query_kernel_state",
            "validate_requirement",
            "finish_run",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is not None
    assert policy.policy_id == "apply_local_finish_after_topology_targeting"
    assert policy.mode == "local_finish"
    assert policy.allowed_tool_names == ["apply_cad_action"]


def test_apply_contract_failure_keeps_local_finish_retry_lane_after_topology_refresh() -> None:
    run_state = RunState(
        session_id="session-local-finish-contract-retry",
        requirements={
            "description": (
                "Create a bracket with a mounting-face countersink and local edge fillets around the top opening."
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
            decision_summary="probe feature families and get topology",
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
                        "matched_ref_ids": ["edge:1:E_top_outer_1"],
                        "candidate_sets": [
                            {
                                "candidate_id": "opening_rim_edges",
                                "label": "Opening Rim Edges",
                                "entity_type": "edge",
                                "ref_ids": [
                                    "edge:1:E_top_outer_1",
                                    "edge:1:E_top_outer_2",
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
            decision_summary="try local fillet without explicit refs",
            tool_calls=[
                ToolCallRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    arguments={
                        "action_type": "fillet",
                        "action_params": {"edge_selector": "top_face_edges", "radius": 2.0},
                    },
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    success=False,
                    payload={
                        "failure_kind": "apply_cad_action_contract_failure",
                        "summary": (
                            "Local fillet/chamfer should consume explicit edge_refs once query_topology has already "
                            "returned targetable edge candidate sets."
                        ),
                        "candidate_edge_set_labels": ["Opening Rim Edges"],
                    },
                    error="apply_cad_action preflight failed: missing edge_refs for local fillet/chamfer",
                )
            ],
            error="apply_cad_action preflight failed: missing edge_refs for local fillet/chamfer",
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=4,
            decision_summary="refresh topology after the contract failure",
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
                        "matched_ref_ids": ["edge:1:E_top_outer_1", "edge:1:E_top_outer_2"],
                        "candidate_sets": [
                            {
                                "candidate_id": "opening_rim_edges",
                                "label": "Opening Rim Edges",
                                "entity_type": "edge",
                                "ref_ids": [
                                    "edge:1:E_top_outer_1",
                                    "edge:1:E_top_outer_2",
                                ],
                            }
                        ],
                    },
                )
            ],
        )
    )
    run_state.latest_validation = {
        "success": True,
        "is_complete": False,
        "summary": "Requirement validation has insufficient evidence",
        "blockers": [],
        "insufficient_evidence": True,
        "coverage_confidence": 0.45,
    }

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=5,
        max_rounds=8,
        all_tool_names=[
            "apply_cad_action",
            "query_topology",
            "query_kernel_state",
            "query_feature_probes",
            "execute_build123d",
            "execute_build123d_probe",
        ],
        previous_tool_failure_summary={
            "tool": "apply_cad_action",
            "failure_kind": "apply_cad_action_contract_failure",
            "effective_failure_kind": "apply_cad_action_contract_failure",
        },
    )

    assert policy is not None
    assert policy.policy_id == "retry_local_finish_after_topology_contract_repair"
    assert policy.mode == "local_finish"
    assert policy.allowed_tool_names == ["apply_cad_action"]


def test_apply_contract_failure_keeps_local_finish_retry_lane_with_existing_topology_refs() -> None:
    run_state = RunState(
        session_id="session-local-finish-contract-retry-existing-refs",
        requirements={
            "description": (
                "Create a bracket with a mounting-face countersink and local edge fillets around the top opening."
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
                                "volume": 12000.0,
                                "bbox": [62.0, 40.0, 14.0],
                            }
                        },
                    },
                )
            ],
        )
    )
    topology_payload = {
        "success": True,
        "matched_ref_ids": ["face:1:F_front_mounting", "edge:1:E_front_top_1"],
        "candidate_sets": [
            {
                "candidate_id": "front_faces",
                "label": "Front Faces",
                "entity_type": "face",
                "ref_ids": ["face:1:F_front_mounting"],
            },
            {
                "candidate_id": "front_top_edges",
                "label": "Front Top Edges",
                "entity_type": "edge",
                "ref_ids": ["edge:1:E_front_top_1", "edge:1:E_front_top_2"],
            },
        ],
    }
    run_state.turns.append(
        TurnRecord(
            round_no=2,
            decision_summary="probe feature families and get topology",
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
                    payload=topology_payload,
                ),
            ],
        )
    )
    run_state.evidence.update(
        tool_name="query_topology",
        payload=topology_payload,
        round_no=2,
    )
    run_state.turns.append(
        TurnRecord(
            round_no=3,
            decision_summary="try local hole with a broad face alias",
            tool_calls=[
                ToolCallRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    arguments={
                        "action_type": "hole",
                        "action_params": {
                            "face": "front",
                            "centers": [[-23.0, 0.0], [23.0, 0.0]],
                            "diameter": 5.0,
                            "depth": 15.0,
                        },
                    },
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    success=False,
                    payload={
                        "failure_kind": "apply_cad_action_contract_failure",
                        "summary": (
                            "A topology-targeted local face edit should consume exact face_ref once "
                            "query_topology has already returned actionable face candidates."
                        ),
                        "candidate_face_set_labels": ["Front Faces"],
                        "preferred_face_refs": ["face:1:F_front_mounting"],
                    },
                    error=(
                        "apply_cad_action preflight failed: hole must use face_ref from latest "
                        "query_topology instead of face='front' during local_finish"
                    ),
                )
            ],
            error=(
                "apply_cad_action preflight failed: hole must use face_ref from latest "
                "query_topology instead of face='front' during local_finish"
            ),
        )
    )
    run_state.latest_validation = {
        "success": True,
        "is_complete": False,
        "summary": "Requirement validation has insufficient evidence",
        "blockers": [],
        "insufficient_evidence": True,
        "coverage_confidence": 0.45,
    }

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=4,
        max_rounds=8,
        all_tool_names=[
            "apply_cad_action",
            "query_topology",
            "query_geometry",
            "query_kernel_state",
            "query_feature_probes",
            "execute_build123d",
            "execute_build123d_probe",
        ],
        previous_tool_failure_summary={
            "tool": "apply_cad_action",
            "failure_kind": "apply_cad_action_contract_failure",
            "effective_failure_kind": "apply_cad_action_contract_failure",
        },
    )

    assert policy is not None
    assert policy.policy_id == "retry_local_finish_with_existing_topology_refs"
    assert policy.mode == "local_finish"
    assert policy.allowed_tool_names == ["apply_cad_action"]


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
    assert policy.allowed_tool_names == ["apply_cad_action", "query_sketch"]
    assert policy.preferred_tool_names == ["apply_cad_action", "query_sketch"]


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
    assert "execute_build123d" in policy.allowed_tool_names
    assert "apply_cad_action" not in policy.allowed_tool_names
    assert policy.preferred_tool_names[0] == "execute_build123d"


def test_feature_probe_assessment_prefers_query_topology_when_latest_feature_probe_requires_host_selection() -> None:
    run_state = RunState(
        session_id="session-feature-probe-topology-closure",
        requirements={"description": "Create a rounded enclosure with a front thumb notch."},
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="initial enclosure build",
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
                                "volume": 18000.0,
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
                    payload={"success": True, "is_complete": False},
                )
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=3,
            decision_summary="refresh probe evidence",
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
                    payload={
                        "success": True,
                        "detected_families": ["slots", "nested_hollow_section"],
                        "probes": [
                            {
                                "family": "slots",
                                "recommended_next_tools": [
                                    "query_topology",
                                    "query_feature_probes",
                                    "query_kernel_state",
                                ],
                                "required_evidence_kinds": ["geometry", "topology"],
                                "anchor_summary": {"requires_topology_host_ranking": True},
                                "grounding_blockers": [
                                    "feature_notch_or_profile_cut",
                                    "need_topology_host_selection",
                                ],
                            }
                        ],
                    },
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
                "solids": 2,
                "volume": 18000.0,
                "bbox": [78.0, 56.0, 32.0],
            }
        },
    }
    run_state.latest_validation = {
        "success": True,
        "is_complete": False,
        "summary": "Requirement validation has insufficient evidence",
        "blockers": ["feature_notch_or_profile_cut"],
        "insufficient_evidence": True,
        "coverage_confidence": 0.45,
        "observation_tags": ["insufficient_evidence", "clause:notch"],
        "repair_hints": ["query_feature_probes", "query_kernel_state"],
        "blocker_taxonomy": [
            {
                "blocker_id": "feature_notch_or_profile_cut",
                "family_ids": ["slots", "nested_hollow_section"],
                "recommended_repair_lane": "probe_first",
                "decision_hints": ["query_feature_probes", "query_kernel_state"],
            }
        ],
    }
    all_tool_names = [
        "query_topology",
        "query_geometry",
        "query_kernel_state",
        "query_feature_probes",
        "execute_build123d_probe",
        "validate_requirement",
        "finish_run",
    ]

    preferred_tools = _latest_feature_probe_preferred_tools_for_turn(
        run_state,
        all_tool_names=all_tool_names,
    )
    allowed_tools = _semantic_refresh_allowed_tool_names_for_turn(
        run_state,
        all_tool_names=all_tool_names,
    )

    assert preferred_tools[0] == "query_topology"
    assert "query_topology" in allowed_tools


def test_repeated_failed_code_path_switches_to_local_finish_when_feature_probe_recommends_topology_edit() -> None:
    run_state = RunState(
        session_id="session-local-finish-after-probe",
        requirements={"description": "Create a bracket with a countersink that should be finished locally."},
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="failed code write",
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
                    error="Exit code: 1 | Traceback (most recent call last):",
                )
            ],
            error="Exit code: 1 | Traceback (most recent call last):",
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=2,
            decision_summary="family probe refresh",
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
                            "explicit_anchor_hole",
                            "named_face_local_edit",
                        ],
                        "probes": [
                            {
                                "family": "named_face_local_edit",
                                "summary": "target face exists; get topology refs before local edit",
                                "success": True,
                                "recommended_next_tools": [
                                    "query_topology",
                                    "apply_cad_action",
                                ],
                            }
                        ],
                    },
                )
            ],
        )
    )
    run_state.latest_write_payload = {
        "step_file": "model.step",
        "session_state_persisted": True,
        "snapshot": {
            "geometry": {
                "solids": 1,
                "volume": 19073.2,
                "bbox": [62.0, 40.0, 14.0],
            }
        },
    }
    run_state.latest_validation = {
        "success": True,
        "is_complete": False,
        "summary": "Requirement validation has 3 blocker(s)",
        "blockers": [
            "feature_countersink",
            "two_mounting_holes",
            "a_countersink_on_the_mounting_face_so_that_a_topology_aware_local_finishing_pass_is_useful",
        ],
        "insufficient_evidence": True,
        "coverage_confidence": 0.8,
        "repair_hints": ["query_topology", "query_feature_probes"],
        "decision_hints": ["query_topology"],
    }

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=3,
        max_rounds=8,
        all_tool_names=[
            "execute_build123d",
            "query_kernel_state",
            "query_feature_probes",
            "query_topology",
            "apply_cad_action",
        ],
        previous_tool_failure_summary={
            "tool": "execute_build123d",
            "same_tool_failure_count": 3,
        },
    )

    assert policy is not None
    assert policy.policy_id == "local_finish_after_actionable_feature_probe_refresh"
    assert policy.mode == "local_finish"
    assert policy.allowed_tool_names == [
        "query_kernel_state",
        "query_topology",
        "apply_cad_action",
    ]
    assert policy.preferred_tool_names == ["query_topology", "apply_cad_action"]


def test_actionable_feature_probe_refresh_forces_apply_after_topology_targeting() -> None:
    run_state = RunState(
        session_id="session-force-apply-after-actionable-feature-probe-refresh",
        requirements={
            "description": "Create a bracket with local topology-aware finishing after repeated code failures."
        },
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="initial whole-part write failed",
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
                    error="execute_build123d preflight lint failed",
                )
            ],
            error="execute_build123d preflight lint failed",
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=2,
            decision_summary="family probe refresh",
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
                        "detected_families": [
                            "explicit_anchor_hole",
                            "named_face_local_edit",
                        ],
                        "probes": [
                            {
                                "family": "named_face_local_edit",
                                "summary": "target face exists; get topology refs before local edit",
                                "success": True,
                                "recommended_next_tools": [
                                    "query_topology",
                                    "apply_cad_action",
                                ],
                            }
                        ],
                    },
                ),
                ToolResultRecord(
                    name="query_topology",
                    category=ToolCategory.READ,
                    success=True,
                    payload={
                        "matched_ref_ids": [
                            "face:1:F_mounting",
                            "edge:1:E_opening_rim",
                        ],
                        "candidate_sets": [
                            {
                                "candidate_id": "opening_rim_edges",
                                "ref_ids": ["edge:1:E_opening_rim"],
                            }
                        ],
                    },
                ),
            ],
        )
    )
    run_state.latest_write_payload = {
        "step_file": "model.step",
        "session_state_persisted": True,
        "snapshot": {
            "geometry": {
                "solids": 1,
                "volume": 19073.2,
                "bbox": [62.0, 40.0, 14.0],
            }
        },
    }
    run_state.latest_validation = {
        "success": True,
        "is_complete": False,
        "summary": "Requirement validation has 3 blocker(s)",
        "blockers": [
            "feature_countersink",
            "two_mounting_holes",
            "a_countersink_on_the_mounting_face_so_that_a_topology_aware_local_finishing_pass_is_useful",
        ],
        "insufficient_evidence": True,
        "coverage_confidence": 0.8,
        "repair_hints": ["query_topology", "query_feature_probes"],
        "decision_hints": ["query_topology"],
    }

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=3,
        max_rounds=8,
        all_tool_names=[
            "execute_build123d",
            "query_kernel_state",
            "query_feature_probes",
            "query_topology",
            "apply_cad_action",
        ],
        previous_tool_failure_summary={
            "tool": "execute_build123d",
            "same_tool_failure_count": 3,
        },
    )

    assert policy is not None
    assert policy.policy_id == "apply_local_finish_after_actionable_feature_probe_refresh"
    assert policy.mode == "local_finish"
    assert policy.allowed_tool_names == ["apply_cad_action"]
    assert policy.preferred_tool_names == ["apply_cad_action"]


def test_successful_host_write_keeps_local_finish_apply_even_with_unrelated_subtree_rebuild_patch() -> None:
    run_state = RunState(
        session_id="session-local-finish-not-blocked-by-unrelated-rebuild-patch",
        requirements={
            "description": (
                "Create a rectangular electronics bracket sized 62mm x 40mm x 14mm with a top pocket, "
                "two mounting holes, and a front thumb notch. Finish the model with local edge fillets "
                "around the top opening and a countersink on the mounting face so that a topology-aware "
                "local finishing pass is useful."
            )
        },
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="successful host build",
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
                                "volume": 19639.4,
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
            decision_summary="feature-probe and topology refresh",
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
                        "detected_families": [
                            "explicit_anchor_hole",
                            "named_face_local_edit",
                        ],
                        "probes": [
                            {
                                "family": "explicit_anchor_hole",
                                "summary": "explicit-anchor host remains under-grounded",
                                "success": False,
                                "recommended_next_tools": [
                                    "query_topology",
                                    "execute_build123d",
                                ],
                                "grounding_blockers": [
                                    "feature_countersink",
                                    "feature_hole",
                                ],
                            },
                            {
                                "family": "named_face_local_edit",
                                "summary": "local host is actionable after topology targeting",
                                "success": True,
                                "recommended_next_tools": [
                                    "query_topology",
                                    "apply_cad_action",
                                ],
                                "grounding_blockers": [],
                            },
                        ],
                    },
                ),
                ToolResultRecord(
                    name="query_topology",
                    category=ToolCategory.READ,
                    success=True,
                    payload={
                        "success": True,
                        "matched_ref_ids": [
                            "face:1:F_bottom_mounting",
                            "edge:1:E_opening_rim_1",
                        ],
                        "candidate_sets": [
                            {
                                "candidate_id": "bottom_faces",
                                "label": "Bottom Faces",
                                "entity_type": "face",
                                "ref_ids": ["face:1:F_bottom_mounting"],
                            },
                            {
                                "candidate_id": "opening_rim_edges",
                                "label": "Opening Rim Edges",
                                "entity_type": "edge",
                                "ref_ids": ["edge:1:E_opening_rim_1"],
                            },
                        ],
                    },
                ),
            ],
        )
    )
    run_state.latest_write_payload = {
        "step_file": "model.step",
        "session_state_persisted": True,
        "snapshot": {
            "geometry": {
                "solids": 1,
                "volume": 19639.4,
                "bbox": [62.0, 40.0, 14.0],
            }
        },
    }
    run_state.latest_validation = {
        "success": True,
        "is_complete": False,
        "summary": "Requirement validation has 4 blocker(s)",
        "blockers": [
            "feature_hole",
            "feature_countersink",
            "two_mounting_holes",
            "a_countersink_on_the_mounting_face_so_that_a_topology_aware_local_finishing_pass_is_useful",
        ],
        "insufficient_evidence": True,
        "coverage_confidence": 0.8,
        "repair_hints": ["query_topology", "query_feature_probes"],
        "decision_hints": ["query_topology"],
    }
    run_state.feature_graph = SimpleNamespace(
        nodes={},
        repair_packets={},
        repair_patches={"patch-1": _UnderGroundedPatch()},
        feature_instances={
            "instance.explicit_anchor_hole.feature_countersink": SimpleNamespace(
                family_id="explicit_anchor_hole",
                status="blocked",
            ),
            "instance.explicit_anchor_hole.two_mounting_holes": SimpleNamespace(
                family_id="explicit_anchor_hole",
                status="blocked",
            ),
            "instance.named_face_local_edit.primary": SimpleNamespace(
                family_id="named_face_local_edit",
                status="active",
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
            "apply_cad_action",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is not None
    assert policy.policy_id == "apply_local_finish_after_topology_targeting"
    assert policy.mode == "local_finish"
    assert policy.allowed_tool_names == ["apply_cad_action"]


def test_read_stall_after_fresh_topology_refresh_exits_to_local_finish() -> None:
    run_state = RunState(
        session_id="session-local-finish-after-read-stall",
        requirements={
            "description": "Create a bracket with a topology-aware countersink finishing pass."
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
            decision_summary="topology refresh",
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
                    payload={"candidate_sets": [{"candidate_id": "mounting_faces"}]},
                ),
                ToolResultRecord(
                    name="query_feature_probes",
                    category=ToolCategory.READ,
                    success=True,
                    payload={
                        "success": True,
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
            decision_summary="extra semantic read that stalled",
            tool_calls=[
                ToolCallRecord(
                    name="query_kernel_state",
                    category=ToolCategory.READ,
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="query_kernel_state",
                    category=ToolCategory.READ,
                    success=True,
                    payload={"active_feature_instances": []},
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

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=4,
        max_rounds=8,
        all_tool_names=[
            "apply_cad_action",
            "query_topology",
            "query_kernel_state",
            "query_feature_probes",
            "validate_requirement",
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
    assert policy.preferred_tool_names[:2] == [
        "apply_cad_action",
        "query_topology",
    ]


def test_feature_probe_topology_refresh_forces_query_topology_after_kernel_state_stall() -> None:
    run_state = RunState(
        session_id="session-force-query-topology-after-kernel-stall",
        requirements={
            "description": (
                "Create a snap clamshell enclosure with overall dimensions 72mm x 64mm x 26mm, "
                "a thumb notch, corner magnet slots, and a hollow shell."
            )
        },
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="repair write produced moderately close geometry",
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
                                "volume": 71000.0,
                                "bbox": [72.0, 64.0, 27.3],
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
            decision_summary="feature probe assessment after moderate whole-part write",
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
                            "general_geometry",
                            "slots",
                            "nested_hollow_section",
                            "core_geometry",
                        ],
                        "probes": [
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
                                    "solid_count": 3,
                                    "expected_part_count": 2,
                                    "bbox": [72.0, 64.0, 27.3],
                                    "expected_bbox": [72.0, 64.0, 26.0],
                                },
                            },
                            {
                                "family": "slots",
                                "success": False,
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
                                    "solid_count": 3,
                                    "expected_part_count": 2,
                                    "bbox": [72.0, 64.0, 27.3],
                                    "expected_bbox": [72.0, 64.0, 26.0],
                                    "requires_topology_host_ranking": True,
                                },
                            },
                        ],
                    },
                )
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=3,
            decision_summary="semantic refresh that stalled before topology",
            tool_calls=[
                ToolCallRecord(
                    name="query_kernel_state",
                    category=ToolCategory.READ,
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="query_kernel_state",
                    category=ToolCategory.READ,
                    success=True,
                    payload={"active_feature_instances": []},
                )
            ],
        )
    )
    run_state.latest_write_payload = {
        "session_state_persisted": True,
        "snapshot": {
            "geometry": {
                "solids": 3,
                "volume": 71000.0,
                "bbox": [72.0, 64.0, 27.3],
            }
        },
    }
    run_state.latest_validation = {
        "success": True,
        "is_complete": False,
        "summary": "Requirement validation has 5 blocker(s)",
        "blockers": [
            "feature_notch_or_profile_cut",
            "create_a_snap_clamshell_enclosure_with_overall_dimensions_72mm_x_64mm_x_26mm",
            "keep_wall_thickness_near_2_0mm",
            "corner_magnet_slots",
            "a_thumb_notch",
        ],
        "blocker_taxonomy": [
            {
                "blocker_id": "feature_notch_or_profile_cut",
                "family_ids": ["slots", "nested_hollow_section"],
                "decision_hints": ["query_topology"],
            },
            {
                "blocker_id": "create_a_snap_clamshell_enclosure_with_overall_dimensions_72mm_x_64mm_x_26mm",
                "family_ids": ["general_geometry"],
                "decision_hints": ["query_feature_probes", "query_geometry"],
            },
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
                    "create_a_snap_clamshell_enclosure_with_overall_dimensions_72mm_x_64mm_x_26mm",
                    "keep_wall_thickness_near_2_0mm",
                    "corner_magnet_slots",
                    "a_thumb_notch",
                ],
            },
        )
    )
    run_state.feature_graph = SimpleNamespace(
        nodes={},
        bindings={},
        repair_packets={},
        repair_patches={"patch-1": _GeneralGeometryWholePartPacket()},
        feature_instances={
            "instance.slots.front_notch": SimpleNamespace(
                family_id="slots",
                status="blocked",
            ),
            "instance.nested_hollow_section.primary": SimpleNamespace(
                family_id="nested_hollow_section",
                status="blocked",
            ),
            "instance.general_geometry.primary": SimpleNamespace(
                family_id="general_geometry",
                status="blocked",
            ),
        },
    )

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=4,
        max_rounds=8,
        all_tool_names=[
            "execute_build123d",
            "query_kernel_state",
            "query_feature_probes",
            "query_topology",
            "apply_cad_action",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is not None
    assert policy.policy_id == "force_query_topology_after_feature_probe_kernel_stall"
    assert policy.mode == "graph_refresh"
    assert policy.allowed_tool_names == ["query_topology"]
    assert policy.preferred_tool_names == ["query_topology"]


def test_read_stall_with_actionable_topology_targets_forces_apply_cad_action() -> None:
    run_state = RunState(
        session_id="session-force-apply-after-read-stall-topology-targets",
        requirements={
            "description": "Create a bracket with topology-aware local finishing after a read stall."
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
            decision_summary="topology refresh",
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
                    payload={
                        "matched_ref_ids": [
                            "face:1:F_top",
                            "edge:1:E_opening_rim",
                        ],
                        "candidate_sets": [
                            {
                                "candidate_id": "opening_rim_edges",
                                "ref_ids": ["edge:1:E_opening_rim"],
                            }
                        ],
                    },
                ),
                ToolResultRecord(
                    name="query_feature_probes",
                    category=ToolCategory.READ,
                    success=True,
                    payload={
                        "success": True,
                        "probes": [
                            {
                                "family": "named_face_local_edit",
                                "success": False,
                                "recommended_next_tools": [
                                    "query_topology",
                                    "apply_cad_action",
                                ],
                                "grounding_blockers": ["feature_fillet"],
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
            decision_summary="extra semantic read that stalled",
            tool_calls=[
                ToolCallRecord(
                    name="query_kernel_state",
                    category=ToolCategory.READ,
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="query_kernel_state",
                    category=ToolCategory.READ,
                    success=True,
                    payload={"active_feature_instances": []},
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
        "summary": "Requirement validation has 3 blocker(s)",
        "blockers": [
            "feature_hole",
            "feature_countersink",
            "feature_fillet",
        ],
        "insufficient_evidence": True,
        "coverage_confidence": 0.8,
        "repair_hints": ["query_topology", "query_feature_probes"],
        "decision_hints": ["query_topology"],
    }

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=4,
        max_rounds=8,
        all_tool_names=[
            "apply_cad_action",
            "query_topology",
            "query_kernel_state",
            "query_feature_probes",
            "validate_requirement",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is not None
    assert policy.policy_id == "apply_local_finish_after_topology_targeting_from_read_stall"
    assert policy.mode == "local_finish"
    assert policy.allowed_tool_names == ["apply_cad_action"]
    assert policy.preferred_tool_names == ["apply_cad_action"]


def test_read_stall_with_actionable_rebuild_patch_does_not_force_apply_cad_action() -> None:
    run_state = RunState(
        session_id="session-read-stall-rebuild-patch-no-forced-local-finish",
        requirements={
            "description": (
                "Create a rounded clamshell enclosure where overall body dimensions and local mating-face edits are still both incorrect."
            )
        },
    )
    run_state.feature_graph = SimpleNamespace(
        nodes={},
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
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="successful whole-part write but globally wrong geometry",
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
                                "volume": 20220.74,
                                "bbox": [78.0, 56.0, 66.0],
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
            decision_summary="topology refresh and feature probe assessment",
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
                    payload={
                        "matched_ref_ids": [
                            "face:1:F_top",
                            "edge:1:E_opening_rim",
                        ],
                        "candidate_sets": [
                            {
                                "candidate_id": "mating_faces",
                                "ref_ids": ["face:1:F_top"],
                            }
                        ],
                    },
                ),
                ToolResultRecord(
                    name="query_feature_probes",
                    category=ToolCategory.READ,
                    success=True,
                    payload={
                        "success": True,
                        "probes": [
                            {
                                "family": "named_face_local_edit",
                                "success": False,
                                "recommended_next_tools": [
                                    "query_topology",
                                    "apply_cad_action",
                                ],
                                "grounding_blockers": [
                                    "feature_target_face_subtractive_merge"
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
            decision_summary="extra semantic read after topology refresh",
            tool_calls=[
                ToolCallRecord(
                    name="query_kernel_state",
                    category=ToolCategory.READ,
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="query_kernel_state",
                    category=ToolCategory.READ,
                    success=True,
                    payload={"active_feature_instances": []},
                )
            ],
        )
    )
    run_state.latest_write_payload = {
        "session_state_persisted": True,
        "snapshot": {
            "geometry": {
                "solids": 2,
                "volume": 20220.74,
                "bbox": [78.0, 56.0, 66.0],
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
            "create_a_two_part_rounded_clamshell_storage_enclosure_with_overall_dimensions_78mm_x_56mm_x_32mm",
            "keep_wall_thickness_near_2_4mm",
        ],
        "insufficient_evidence": False,
        "coverage_confidence": 0.9,
    }

    should_force_apply = _local_finish_should_force_apply_after_topology_targeting(
        run_state,
        write_round=1,
        all_tool_names=[
            "execute_build123d",
            "apply_cad_action",
            "query_topology",
            "query_kernel_state",
            "query_feature_probes",
            "validate_requirement",
        ],
    )

    assert should_force_apply is False


def test_read_stall_with_actionable_rebuild_patch_keeps_local_finish_escape_available() -> None:
    run_state = RunState(
        session_id="session-read-stall-rebuild-patch-keeps-local-finish-escape",
        requirements={
            "description": (
                "Create a rounded clamshell enclosure where overall body dimensions and local mating-face edits are still both incorrect."
            )
        },
    )
    run_state.feature_graph = SimpleNamespace(
        nodes={},
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
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="successful whole-part write but globally wrong geometry",
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
                                "volume": 20220.74,
                                "bbox": [78.0, 56.0, 66.0],
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
            decision_summary="topology refresh and feature probe assessment",
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
                    payload={
                        "matched_ref_ids": [
                            "face:1:F_top",
                            "edge:1:E_opening_rim",
                        ],
                        "candidate_sets": [
                            {
                                "candidate_id": "mating_faces",
                                "ref_ids": ["face:1:F_top"],
                            }
                        ],
                    },
                ),
                ToolResultRecord(
                    name="query_feature_probes",
                    category=ToolCategory.READ,
                    success=True,
                    payload={
                        "success": True,
                        "probes": [
                            {
                                "family": "named_face_local_edit",
                                "success": False,
                                "recommended_next_tools": [
                                    "query_topology",
                                    "apply_cad_action",
                                ],
                                "grounding_blockers": [
                                    "feature_target_face_subtractive_merge"
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
            decision_summary="read stall wants semantic refresh but local topology refs already exist",
            tool_calls=[
                ToolCallRecord(
                    name="query_kernel_state",
                    category=ToolCategory.READ,
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="query_kernel_state",
                    category=ToolCategory.READ,
                    success=False,
                    payload={},
                )
            ],
        )
    )
    run_state.latest_write_payload = {
        "session_state_persisted": True,
        "snapshot": {
            "geometry": {
                "solids": 2,
                "volume": 20220.74,
                "bbox": [78.0, 56.0, 66.0],
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
            "create_a_two_part_rounded_clamshell_storage_enclosure_with_overall_dimensions_78mm_x_56mm_x_32mm",
            "keep_wall_thickness_near_2_4mm",
        ],
        "insufficient_evidence": False,
        "coverage_confidence": 0.9,
    }

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=4,
        max_rounds=8,
        all_tool_names=[
            "execute_build123d",
            "apply_cad_action",
            "query_kernel_state",
            "query_feature_probes",
            "query_topology",
            "execute_build123d_probe",
            "validate_requirement",
            "finish_run",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is not None
    assert policy.policy_id == "graph_refresh_with_local_finish_escape_after_read_stall"
    assert policy.mode == "graph_refresh"
    assert policy.allowed_tool_names == ["query_kernel_state", "apply_cad_action"]
    assert policy.preferred_tool_names == ["query_kernel_state", "apply_cad_action"]


def test_post_solid_semantic_admission_prefers_graph_refresh_before_code_escape_when_budget_allows() -> None:
    run_state = RunState(
        session_id="session-post-solid-semantic-admission-refresh",
        requirements={
            "description": "Create a housing that already has a stable base body but still needs two semantic feature families resolved."
        },
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="local finishing produced the first stable solid",
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
                                "volume": 12000.0,
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
                "volume": 12000.0,
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
            "feature.notch": SimpleNamespace(
                node_id="feature.notch",
                kind="feature",
                status="blocked",
            ),
            "feature.magnet_slots": SimpleNamespace(
                node_id="feature.magnet_slots",
                kind="feature",
                status="blocked",
            ),
        }
    )

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=2,
        max_rounds=4,
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
    assert policy.policy_id == "semantic_admission_after_first_stable_solid"
    assert policy.mode == "graph_refresh"
    assert policy.allowed_tool_names == [
        "query_kernel_state",
        "query_feature_probes",
        "execute_build123d_probe",
    ]
    assert policy.preferred_tool_names == [
        "query_kernel_state",
        "query_feature_probes",
    ]
    assert "execute_build123d" not in policy.allowed_tool_names


def test_post_solid_semantic_admission_reopens_code_escape_when_budget_is_tight() -> None:
    run_state = RunState(
        session_id="session-post-solid-semantic-admission-tight-budget",
        requirements={
            "description": "Create a housing that has a stable host body but too little budget for two remaining features."
        },
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="local finishing produced the first stable solid",
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
                                "volume": 12000.0,
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
                "volume": 12000.0,
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
            "feature.notch": SimpleNamespace(
                node_id="feature.notch",
                kind="feature",
                status="blocked",
            ),
            "feature.magnet_slots": SimpleNamespace(
                node_id="feature.magnet_slots",
                kind="feature",
                status="blocked",
            ),
        }
    )

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=2,
        max_rounds=3,
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
    assert policy.policy_id == "code_first_after_feature_budget_risk"
    assert policy.mode == "code_first_escape"
    assert "execute_build123d" in policy.allowed_tool_names


def test_successful_local_finish_semantic_refresh_stays_on_local_finish_lane() -> None:
    run_state = RunState(
        session_id="session-successful-local-finish-semantic-refresh",
        requirements={
            "description": "Create a bracket with topology-aware local holes and local edge finishing."
        },
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="whole-part build produced the stable host body",
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
            decision_summary="local hole and countersink succeeded on the target face",
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
    run_state.turns.append(
        TurnRecord(
            round_no=3,
            decision_summary="feature probes still recommend topology-aware local finishing",
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
                                "family": "named_face_local_edit",
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
            round_no=4,
            decision_summary="semantic refresh confirms local features remain unsatisfied",
            tool_calls=[
                ToolCallRecord(
                    name="query_kernel_state",
                    category=ToolCategory.READ,
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="query_kernel_state",
                    category=ToolCategory.READ,
                    success=True,
                    payload={"summary": "local finishing still required"},
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
        round_no=5,
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

    assert policy is not None
    assert policy.policy_id == "refresh_topology_for_continued_local_finish_after_semantic_refresh"
    assert policy.mode == "local_finish"
    assert policy.allowed_tool_names == [
        "apply_cad_action",
        "query_topology",
        "query_kernel_state",
    ]
    assert policy.preferred_tool_names == ["query_topology", "apply_cad_action"]
    assert "execute_build123d" not in policy.allowed_tool_names


def test_successful_local_finish_semantic_refresh_prefers_validation_when_latest_validation_is_stale() -> None:
    run_state = RunState(
        session_id="session-successful-local-finish-stale-validation",
        requirements={
            "description": "Create a bracket with topology-aware countersunk holes and local edge finishing."
        },
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="initial validation still reports local hole and fillet blockers",
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
                            "feature_hole",
                            "feature_countersink",
                            "feature_fillet",
                        ],
                        "decision_hints": ["query_topology"],
                    },
                )
            ],
        )
    )
    run_state.latest_validation = {
        "success": True,
        "is_complete": False,
        "summary": "Requirement validation has 3 blocker(s)",
        "blockers": [
            "feature_hole",
            "feature_countersink",
            "feature_fillet",
        ],
        "decision_hints": ["query_topology"],
        "coverage_confidence": 1.0,
    }
    run_state.turns.append(
        TurnRecord(
            round_no=2,
            decision_summary="local countersink hole succeeded on the target face",
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
    run_state.turns.append(
        TurnRecord(
            round_no=3,
            decision_summary="feature probes still recommend topology-aware local finishing",
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
            round_no=4,
            decision_summary="semantic refresh returned fresh topology after the successful local finish",
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
                        "matched_ref_ids": ["face:2:F_mount"],
                        "candidate_sets": [
                            {
                                "candidate_id": "top_faces",
                                "ref_ids": ["face:2:F_mount"],
                            }
                        ],
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
        }
    )

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=5,
        max_rounds=8,
        all_tool_names=[
            "apply_cad_action",
            "validate_requirement",
            "query_topology",
            "query_kernel_state",
            "query_feature_probes",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is not None
    assert policy.policy_id == "validate_after_local_finish_semantic_refresh"
    assert policy.mode == "validation_check"
    assert policy.allowed_tool_names == ["validate_requirement"]
    assert policy.preferred_tool_names == ["validate_requirement"]


def test_local_finish_validation_evidence_gap_prefers_read_refresh_before_more_local_writes() -> None:
    run_state = RunState(
        session_id="session-local-finish-validation-evidence-gap-refresh",
        requirements={
            "description": (
                "Create a bracket with countersunk mounting holes and local edge fillets around the top opening."
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
                                "volume": 19000.0,
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
            decision_summary="local finish writes countersunk holes onto the mounting face",
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
                                "volume": 18800.0,
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
            round_no=3,
            decision_summary="post-write semantic refresh gathers geometry and topology evidence",
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
                        "matched_ref_ids": ["face:2:F_mount", "edge:2:E_top_0"],
                        "candidate_sets": [
                            {
                                "candidate_id": "mating_faces",
                                "label": "Mating Faces",
                                "entity_type": "face",
                                "ref_ids": ["face:2:F_mount"],
                                "semantic_host_roles": ["mating_face", "inner_planar_host"],
                            },
                            {
                                "candidate_id": "top_edges",
                                "label": "Top Edges",
                                "entity_type": "edge",
                                "ref_ids": ["edge:2:E_top_0", "edge:2:E_top_1"],
                            },
                        ],
                    },
                ),
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=4,
            decision_summary="validation confirms blockers are gone but requests count/placement evidence",
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
                        "summary": "Requirement validation has insufficient evidence",
                        "blockers": [],
                        "insufficient_evidence": True,
                        "coverage_confidence": 0.8,
                        "decision_hints": [
                            "inspect count or placement with geometry/topology evidence",
                            "validation_llm_skipped:estimated_prompt_budget_exceeded:7522/7000",
                        ],
                        "repair_hints": ["query_topology", "query_feature_probes"],
                        "observation_tags": [
                            "insufficient_evidence",
                            "validation:llm_skipped",
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
                "volume": 18800.0,
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
        "coverage_confidence": 0.8,
        "decision_hints": [
            "inspect count or placement with geometry/topology evidence",
            "validation_llm_skipped:estimated_prompt_budget_exceeded:7522/7000",
        ],
        "repair_hints": ["query_topology", "query_feature_probes"],
        "observation_tags": ["insufficient_evidence", "validation:llm_skipped"],
    }
    run_state.add_agent_event(
        SimpleNamespace(  # type: ignore[arg-type]
            kind="validation_result",
            round_no=4,
            role="runtime",
            payload={
                "summary": "Requirement validation has insufficient evidence",
                "is_complete": False,
                "blockers": [],
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
        }
    )

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=7,
        max_rounds=8,
        all_tool_names=[
            "apply_cad_action",
            "query_topology",
            "query_geometry",
            "query_feature_probes",
            "query_kernel_state",
            "validate_requirement",
            "finish_run",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is not None
    assert policy.policy_id == "read_refresh_after_local_finish_validation_evidence_gap"
    assert policy.mode == "graph_refresh"
    assert "query_topology" in policy.allowed_tool_names
    assert "query_kernel_state" in policy.allowed_tool_names
    assert "apply_cad_action" not in policy.allowed_tool_names
    assert policy.preferred_tool_names[0] == "query_topology"


def test_last_round_local_finish_validation_evidence_gap_keeps_closure_validation_open() -> None:
    run_state = RunState(
        session_id="session-local-finish-validation-evidence-gap-last-round",
        requirements={
            "description": (
                "Create a bracket with countersunk mounting holes and local edge fillets around the top opening."
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
                                "volume": 19000.0,
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
            round_no=4,
            decision_summary="local finish writes countersunk holes onto the mounting face",
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
                                "volume": 18800.0,
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
            round_no=5,
            decision_summary="post-write semantic refresh gathers geometry and topology evidence",
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
                        "matched_ref_ids": ["face:2:F_mount", "edge:2:E_top_0"],
                        "candidate_sets": [
                            {
                                "candidate_id": "mating_faces",
                                "label": "Mating Faces",
                                "entity_type": "face",
                                "ref_ids": ["face:2:F_mount"],
                                "semantic_host_roles": ["mating_face", "inner_planar_host"],
                            },
                            {
                                "candidate_id": "top_edges",
                                "label": "Top Edges",
                                "entity_type": "edge",
                                "ref_ids": ["edge:2:E_top_0", "edge:2:E_top_1"],
                            },
                        ],
                    },
                ),
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=6,
            decision_summary="validation confirms blockers are gone but requests count/placement evidence",
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
                        "summary": "Requirement validation has insufficient evidence",
                        "blockers": [],
                        "insufficient_evidence": True,
                        "coverage_confidence": 0.8,
                        "decision_hints": [
                            "inspect count or placement with geometry/topology evidence",
                            "validation_llm_skipped:estimated_prompt_budget_exceeded:7522/7000",
                        ],
                        "repair_hints": ["query_topology", "query_feature_probes"],
                        "observation_tags": [
                            "insufficient_evidence",
                            "validation:llm_skipped",
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
                "volume": 18800.0,
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
        "coverage_confidence": 0.8,
        "decision_hints": [
            "inspect count or placement with geometry/topology evidence",
            "validation_llm_skipped:estimated_prompt_budget_exceeded:7522/7000",
        ],
        "repair_hints": ["query_topology", "query_feature_probes"],
        "observation_tags": ["insufficient_evidence", "validation:llm_skipped"],
    }
    run_state.add_agent_event(
        SimpleNamespace(  # type: ignore[arg-type]
            kind="validation_result",
            round_no=6,
            role="runtime",
            payload={
                "summary": "Requirement validation has insufficient evidence",
                "is_complete": False,
                "blockers": [],
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
        }
    )

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=5,
        max_rounds=5,
        all_tool_names=[
            "apply_cad_action",
            "query_topology",
            "query_geometry",
            "query_feature_probes",
            "query_kernel_state",
            "validate_requirement",
            "finish_run",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is not None
    assert (
        policy.policy_id
        == "closure_refresh_after_local_finish_validation_evidence_gap_under_budget"
    )
    assert policy.mode == "completion_judge"
    assert "query_topology" in policy.allowed_tool_names
    assert "query_geometry" in policy.allowed_tool_names
    assert "validate_requirement" in policy.allowed_tool_names
    assert "finish_run" in policy.allowed_tool_names
    assert "apply_cad_action" not in policy.allowed_tool_names
    assert policy.preferred_tool_names[:2] == ["query_topology", "validate_requirement"]


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
