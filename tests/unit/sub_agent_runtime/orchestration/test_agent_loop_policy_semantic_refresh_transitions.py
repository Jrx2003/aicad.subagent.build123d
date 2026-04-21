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
