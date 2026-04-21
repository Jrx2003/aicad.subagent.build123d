from types import SimpleNamespace

from sub_agent_runtime.orchestration.policy.shared import (
    _filter_supported_round_tool_names,
    _infer_runtime_failure_cluster,
    _latest_actionable_kernel_patch,
    _latest_validation_prefers_semantic_refresh,
    _turn_has_successful_validation_completion,
    _turn_policy_from_actionable_kernel_patch,
)
from sub_agent_runtime.semantic_kernel import FamilyRepairPacket
from sub_agent_runtime.turn_state import (
    RunState,
    ToolCallRecord,
    ToolCategory,
    ToolResultRecord,
    TurnRecord,
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


def test_turn_policy_from_actionable_kernel_patch_prefers_execute_repair_packet_for_supported_packet() -> None:
    policy = _turn_policy_from_actionable_kernel_patch(
        round_no=4,
        all_tool_names=[
            "execute_build123d",
            "execute_repair_packet",
            "apply_cad_action",
            "query_topology",
        ],
        policy_id="repair_from_actionable_kernel_patch_after_validation_blocker",
        reason="kernel exposed a supported deterministic packet",
        patch={
            "repair_mode": "subtree_rebuild",
            "families": ["spherical_recess"],
            "repair_packet": {
                "family_id": "spherical_recess",
                "feature_instance_id": "instance.spherical_recess.primary",
                "recipe_id": "spherical_recess_host_face_center_set",
            },
        },
    )

    assert policy.allowed_tool_names == ["execute_repair_packet"]
    assert policy.preferred_tool_names == ["execute_repair_packet"]


def test_turn_policy_from_actionable_kernel_patch_falls_back_to_execute_build123d_for_descriptive_packet() -> None:
    policy = _turn_policy_from_actionable_kernel_patch(
        round_no=4,
        all_tool_names=[
            "execute_build123d",
            "execute_repair_packet",
            "apply_cad_action",
            "query_topology",
        ],
        policy_id="repair_from_actionable_kernel_patch_after_validation_blocker",
        reason="kernel exposed a descriptive-only packet",
        patch={
            "repair_mode": "subtree_rebuild",
            "families": ["explicit_anchor_hole"],
            "repair_packet": {
                "family_id": "explicit_anchor_hole",
                "feature_instance_id": "instance.explicit_anchor_hole.primary",
                "recipe_id": "explicit_anchor_hole_helper_contract_fallback",
            },
        },
    )

    assert policy.allowed_tool_names == ["execute_build123d"]
    assert policy.preferred_tool_names == ["execute_build123d"]


def test_turn_policy_from_actionable_kernel_patch_keeps_local_finish_precedence_even_with_supported_packet() -> None:
    policy = _turn_policy_from_actionable_kernel_patch(
        round_no=4,
        all_tool_names=[
            "execute_build123d",
            "execute_repair_packet",
            "apply_cad_action",
            "query_topology",
        ],
        policy_id="repair_from_actionable_kernel_patch_after_validation_blocker",
        reason="local edit remains the correct lane",
        patch={
            "repair_mode": "local_edit",
            "families": ["named_face_local_edit"],
            "repair_packet": {
                "family_id": "spherical_recess",
                "feature_instance_id": "instance.named_face_local_edit.primary",
                "recipe_id": "spherical_recess_host_face_center_set",
            },
        },
    )

    assert policy.allowed_tool_names == ["apply_cad_action", "query_topology"]
    assert policy.preferred_tool_names == ["apply_cad_action", "query_topology"]


def test_filter_supported_round_tool_names_keeps_execute_repair_packet_when_any_active_packet_is_supported() -> None:
    supported_packet = FamilyRepairPacket(
        packet_id="packet-supported",
        family_id="spherical_recess",
        feature_instance_id="instance.spherical_recess.primary",
        repair_mode="subtree_rebuild",
        recipe_id="spherical_recess_host_face_center_set",
    )
    descriptive_packet = FamilyRepairPacket(
        packet_id="packet-descriptive",
        family_id="explicit_anchor_hole",
        feature_instance_id="instance.explicit_anchor_hole.primary",
        repair_mode="subtree_rebuild",
        recipe_id="explicit_anchor_hole_helper_contract_fallback",
    )
    run_state = RunState(
        session_id="session-supported-packet-filter",
        requirements={"description": "repair packet routing"},
        feature_graph=SimpleNamespace(
            repair_packets={
                "packet-supported": supported_packet,
                "packet-descriptive": descriptive_packet,
            }
        ),
    )

    filtered = _filter_supported_round_tool_names(
        run_state=run_state,
        tool_names={"execute_build123d", "execute_repair_packet"},
    )

    assert filtered == {"execute_build123d", "execute_repair_packet"}


def test_latest_actionable_kernel_patch_prefers_supported_runtime_packet_over_newer_descriptive_packet() -> None:
    supported_packet = FamilyRepairPacket(
        packet_id="packet-supported",
        family_id="spherical_recess",
        feature_instance_id="instance.spherical_recess.primary",
        repair_mode="subtree_rebuild",
        anchor_keys=["expected_local_centers"],
        parameter_keys=["geometry_summary"],
        recipe_id="spherical_recess_host_face_center_set",
    )
    descriptive_packet = FamilyRepairPacket(
        packet_id="packet-descriptive",
        family_id="explicit_anchor_hole",
        feature_instance_id="instance.explicit_anchor_hole.primary",
        repair_mode="subtree_rebuild",
        anchor_keys=["bbox"],
        parameter_keys=["geometry_summary"],
        recipe_id="explicit_anchor_hole_helper_contract_fallback",
    )
    run_state = RunState(
        session_id="session-best-packet-selection",
        requirements={"description": "repair packet routing"},
        feature_graph=SimpleNamespace(
            repair_packets={
                "packet-supported": supported_packet,
                "packet-descriptive": descriptive_packet,
            },
            repair_patches={},
            feature_instances={},
        ),
    )

    patch = _latest_actionable_kernel_patch(run_state)

    assert patch is not None
    assert patch["repair_packet"]["recipe_id"] == "spherical_recess_host_face_center_set"


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


def test_turn_has_successful_validation_completion_detects_complete_validate_requirement_result() -> None:
    turn = TurnRecord(
        round_no=3,
        decision_summary="validate current state",
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

    assert _turn_has_successful_validation_completion(turn) is True
