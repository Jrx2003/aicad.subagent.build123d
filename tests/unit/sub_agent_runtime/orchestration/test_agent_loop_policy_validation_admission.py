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

def test_successful_validation_after_local_finish_does_not_reopen_local_finish_read_stall_policy() -> None:
    run_state = RunState(
        session_id="session-local-finish-validated-complete",
        requirements={
            "description": (
                "Create a service bracket with countersunk mounting holes and a front-face local recess."
            )
        },
    )
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="whole-part build succeeds",
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
                            "geometry": {"solids": 1, "volume": 35000.0, "bbox": [66.0, 42.0, 16.0]}
                        },
                    },
                )
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=2,
            decision_summary="local finish succeeds with exact bottom face refs",
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
                            "geometry": {"solids": 1, "volume": 34700.0, "bbox": [66.0, 42.0, 16.0]}
                        },
                    },
                )
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=3,
            decision_summary="refresh topology after local finish",
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
                    payload={"success": True, "candidate_sets": [{"candidate_id": "bottom_faces"}]},
                )
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=4,
            decision_summary="validation confirms requirement complete",
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
                        "is_complete": True,
                        "blockers": [],
                        "summary": "Requirement validation passed",
                    },
                )
            ],
        )
    )
    run_state.latest_validation = {
        "success": True,
        "is_complete": True,
        "blockers": [],
        "summary": "Requirement validation passed",
    }

    policy = _determine_turn_tool_policy(
        run_state=run_state,
        round_no=5,
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
    assert policy.policy_id != "apply_local_finish_after_topology_targeting_from_read_stall"
    assert policy.allowed_tool_names == ["finish_run"]
    assert policy.preferred_tool_names == ["finish_run"]
