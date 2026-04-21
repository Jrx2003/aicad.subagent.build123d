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
