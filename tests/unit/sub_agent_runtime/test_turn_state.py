from sub_agent_runtime.turn_state import RunState, ToolCategory, ToolCallRecord, ToolResultRecord, TurnRecord


def test_run_state_counts_no_effect_apply_action_as_no_op() -> None:
    run_state = RunState(session_id="session-1", requirements={})
    run_state.turns.append(
        TurnRecord(
            round_no=1,
            decision_summary="attempted local cut",
            tool_calls=[
                ToolCallRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    arguments={"action_type": "cut_extrude"},
                    call_id="apply_cad_action:0",
                )
            ],
            tool_results=[
                ToolResultRecord(
                    name="apply_cad_action",
                    category=ToolCategory.WRITE,
                    success=False,
                    payload={
                        "error_message": (
                            "cut_extrude produced no geometry change on the current solid; "
                            "inspect the active sketch/profile or refresh the local target before retrying."
                        )
                    },
                    error=(
                        "cut_extrude produced no geometry change on the current solid; "
                        "inspect the active sketch/profile or refresh the local target before retrying."
                    ),
                )
            ],
            error="apply_cad_action failed",
        )
    )

    assert run_state.no_op_action_count == 1
