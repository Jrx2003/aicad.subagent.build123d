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

def test_successful_local_finish_under_short_budget_prefers_semantic_refresh_before_code_escape() -> None:
    run_state = RunState(
        session_id="session-local-finish-semantic-refresh-before-budget-escape",
        requirements={
            "description": (
                "Create a rounded pillbox enclosure with a front thumb notch and a shallow front-face label recess."
            )
        },
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="build pillbox host",
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
                                "volume": 21914.7,
                                "bbox": [64.0, 48.0, 24.0],
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
            decision_summary="refresh feature probes and topology for front-face local work",
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
    run_state.turns.append(
        TurnRecord(
            round_no=3,
            decision_summary="open front-face sketch",
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
                        "session_state_persisted": True,
                        "snapshot": {
                            "geometry": {
                                "solids": 1,
                                "volume": 21914.7,
                                "bbox": [64.0, 48.0, 24.0],
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
            decision_summary="add thumb-notch profile",
            tool_calls=[
                ToolCallRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    arguments={
                        "action_type": "add_circle",
                        "action_params": {"center": [0, 0], "radius": 3.5},
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
                                "volume": 21914.7,
                                "bbox": [64.0, 48.0, 24.0],
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
            decision_summary="materialize thumb-notch cut",
            tool_calls=[
                ToolCallRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    arguments={
                        "action_type": "cut_extrude",
                        "action_params": {"depth": 5.0, "direction": "through"},
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
                                "volume": 21821.68,
                                "bbox": [64.0, 48.0, 24.0],
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
                "volume": 21821.68,
                "bbox": [64.0, 48.0, 24.0],
            }
        },
    }
    run_state.latest_validation = {
        "success": True,
        "is_complete": False,
        "summary": "Requirement validation has 2 blocker(s)",
        "blockers": [
            "feature_notch_or_profile_cut",
            "feature_label_window_recess",
        ],
        "insufficient_evidence": True,
        "coverage_confidence": 0.45,
        "repair_hints": ["query_topology", "query_feature_probes"],
        "decision_hints": ["query_topology"],
    }
    run_state.add_agent_event(
        SimpleNamespace(  # type: ignore[arg-type]
            kind="validation_result",
            round_no=2,
            role="runtime",
            payload={
                "summary": "Requirement validation has 2 blocker(s)",
                "is_complete": False,
                "blockers": [
                    "feature_notch_or_profile_cut",
                    "feature_label_window_recess",
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
            "feature.named_face_local_edit.thumb_notch": SimpleNamespace(
                node_id="feature.named_face_local_edit.thumb_notch",
                kind="feature",
                status="blocked",
            ),
            "feature.named_face_local_edit.label_recess": SimpleNamespace(
                node_id="feature.named_face_local_edit.label_recess",
                kind="feature",
                status="blocked",
            ),
        }
    )

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=6,
        max_rounds=7,
        all_tool_names=[
            "execute_build123d",
            "apply_cad_action",
            "query_topology",
            "query_kernel_state",
            "query_feature_probes",
            "validate_requirement",
        ],
        previous_tool_failure_summary=None,
    )

    assert policy is not None
    assert policy.policy_id == "semantic_refresh_after_successful_local_finish"
    assert policy.mode == "graph_refresh"
    assert "execute_build123d" not in policy.allowed_tool_names
    assert "query_feature_probes" in policy.allowed_tool_names
    assert "query_kernel_state" in policy.allowed_tool_names

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
