from types import SimpleNamespace

from sub_agent_runtime.agent_loop_v2 import (
    _determine_turn_tool_policy,
    _latest_actionable_kernel_patch,
    _latest_validation_prefers_semantic_refresh,
)
from sub_agent_runtime.turn_state import RunState, ToolCallRecord, ToolCategory, ToolResultRecord, TurnRecord


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
