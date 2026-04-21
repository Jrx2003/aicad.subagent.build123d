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

def test_objective_health_prefers_materializing_active_local_sketch_when_profile_ready() -> None:
    run_state = RunState(
        session_id="session-active-sketch-budget",
        requirements={
            "description": "Create a bracket with a countersunk local cut on a chosen face."
        },
    )
    apply_payload = {
        "success": True,
        "output_files": ["model.step", "geometry_info.json"],
        "snapshot": {
            "geometry": {
                "solids": 1,
                "faces": 19,
                "edges": 44,
                "volume": 19111.63,
                "bbox": [62.0, 40.0, 14.0],
            }
        },
        "action_history": [
            {
                "step": 3,
                "action_type": "create_sketch",
                "action_params": {"face_ref": "face:1:F_front"},
            }
        ],
    }
    run_state.turns.append(
        TurnRecord(
            round_no=3,
            decision_summary="open a face-attached local sketch",
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
                    payload=apply_payload,
                )
            ],
        )
    )
    run_state.turns.append(
        TurnRecord(
            round_no=4,
            decision_summary="inspect sketch before committing the material write",
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
                        "step": 3,
                        "sketch_state": {
                            "plane": "XY",
                            "profile_refs": ["profile:3:PR_1"],
                            "path_refs": [],
                        },
                    },
                )
            ],
        )
    )
    run_state.latest_write_payload = apply_payload
    run_state.latest_step_file = "model.step"
    run_state.evidence.update(
        tool_name="query_sketch",
        round_no=4,
        payload={
            "success": True,
            "step": 3,
            "sketch_state": {
                "plane": "XY",
                "profile_refs": ["profile:3:PR_1"],
                "path_refs": [],
            },
        },
    )

    context_manager = V2ContextManager()
    objective_health = context_manager._build_objective_health(
        run_state,
        round_budget={"remaining_rounds": 1},
    )

    assert objective_health["status"] == "budget_constrained"
    assert objective_health["recommended_bias"] == (
        "prefer_materializing_active_local_sketch_over_rebuild"
    )
    assert objective_health["recommended_next_tools"] == [
        "apply_cad_action",
        "query_sketch",
    ]
    assert "active_sketch_profile_ready_for_material_write" in objective_health["reasons"]
    assert "unfinished_sketch_window_under_round_budget" in objective_health["reasons"]

def test_objective_health_prefers_semantic_admission_before_code_escape_after_first_stable_local_finish() -> None:
    run_state = RunState(
        session_id="session-objective-health-semantic-admission",
        requirements={
            "description": "Create a housing with a stable local-finish solid but two unresolved semantic feature families."
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
        },
        to_query_payload=lambda **_: {
            "success": True,
            "nodes": [],
            "edges": [],
            "bindings": [],
            "revision_history": [],
            "active_node_ids": ["feature.notch", "feature.magnet_slots"],
            "blocked_node_ids": [],
            "completed_node_ids": ["feature.body"],
            "requirement_tags": [],
        },
        edges={},
        active_node_ids=["feature.notch", "feature.magnet_slots"],
    )

    context_manager = V2ContextManager()
    payload = context_manager.build_prompt_payload(run_state=run_state, max_rounds=4)

    objective_health = payload.get("objective_health")
    assert isinstance(objective_health, dict)
    assert objective_health["status"] == "semantic_admission_required"
    assert objective_health["recommended_bias"] == (
        "refresh_semantic_state_before_reopening_whole_part_write"
    )
    assert objective_health["recommended_next_tools"] == [
        "query_kernel_state",
        "query_feature_probes",
    ]
    assert "first_stable_solid_requires_semantic_admission" in objective_health["reasons"]
