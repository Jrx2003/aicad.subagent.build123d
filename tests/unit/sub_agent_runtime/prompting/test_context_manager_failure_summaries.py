from types import SimpleNamespace

from sub_agent_runtime.prompting import V2ContextManager, build_runtime_skill_pack
from sub_agent_runtime.turn_state import (
    RunState,
    ToolCallRecord,
    ToolCategory,
    ToolResultRecord,
    TurnRecord,
    TurnToolPolicy,
)

def test_previous_tool_failure_summary_classifies_detached_subtractive_builder_runtime_error() -> None:
    payload = {
        "success": False,
        "error_message": "Exit code: 1",
        "stderr": (
            "Traceback (most recent call last):\n"
            "  File \"/app/aicad_runtime_main.py\", line 166, in <module>\n"
            "    extrude(amount=-4, mode=Mode.SUBTRACT)\n"
            "RuntimeError: Nothing to subtract from"
        ),
    }
    turn = TurnRecord(
        round_no=4,
        decision_summary="repair detached subtractive builder",
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
                error="Exit code: 1",
                payload=payload,
            )
        ],
        error="Exit code: 1",
    )
    run_state = RunState(
        session_id="session-detached-subtractive-summary",
        requirements={"description": "Create a clamshell enclosure with a front notch and side pocket."},
        turns=[turn],
        latest_write_payload=payload,
    )

    summary = V2ContextManager().build_previous_tool_failure_summary(run_state)

    assert summary is not None
    assert (
        summary["failure_kind"]
        == "execute_build123d_detached_subtractive_builder_failure"
    )
    assert summary["recovery_bias"] == "repair_detached_subtractive_builder_before_retry"
    assert summary["recommended_next_tools"] == ["execute_build123d", "query_kernel_state"]

def test_summarize_failure_lint_hits_deduplicates_repeated_rule_ids() -> None:
    summary = V2ContextManager()._summarize_failure_lint_hits(  # noqa: SLF001
        [
            {
                "rule_id": "invalid_build123d_contract.clamshell_hinge_unrotated_default_cylinder",
                "message": "Hinge cylinder stayed on Z.",
                "repair_hint": "Repair the unrotated hinge cylinder at line 55.",
                "layer": "write_surface",
                "category": "invalid_api_contract",
                "severity": "fatal",
                "recommended_recipe_id": "clamshell_host_local_cut_contract",
            },
            {
                "rule_id": "invalid_build123d_contract.clamshell_hinge_unrotated_default_cylinder",
                "message": "Hinge cylinder stayed on Z.",
                "repair_hint": "Repair the unrotated hinge cylinder at line 58.",
                "layer": "write_surface",
                "category": "invalid_api_contract",
                "severity": "fatal",
                "recommended_recipe_id": "clamshell_host_local_cut_contract",
            },
            {
                "rule_id": "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic",
                "message": "Temporary primitive arithmetic mutates the active host immediately.",
                "repair_hint": "Repair the temporary solid arithmetic at line 96.",
                "layer": "write_surface",
                "category": "invalid_api_contract",
                "severity": "fatal",
                "recommended_recipe_id": "clamshell_host_local_cut_contract",
            },
        ]
    )

    assert summary is not None
    assert len(summary) == 2
    assert summary[0]["rule_id"] == "invalid_build123d_contract.clamshell_hinge_unrotated_default_cylinder"
    assert summary[0]["occurrence_count"] == 2
    assert summary[1]["rule_id"] == "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic"
    assert "occurrence_count" not in summary[1]
