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

def test_prepare_runtime_skills_payload_keeps_high_priority_enclosure_repairs_when_truncated() -> None:
    runtime_skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a two-part rounded clamshell enclosure with a hollow base and lid, "
                "overall dimensions 78mm x 56mm x 32mm, four corner magnet recesses, "
                "a front thumb notch, and one side plug pocket."
            )
        },
        latest_validation={},
        latest_write_health={"tool": "execute_build123d"},
        previous_tool_failure_summary={
            "tool": "execute_build123d",
            "lint_hits": [
                {
                    "rule_id": "invalid_build123d_context.transform_context_manager",
                    "message": "Rot is not a context manager",
                    "repair_hint": "Use Locations(...) instead of with Rot(...):",
                },
                {
                    "rule_id": "invalid_build123d_context.nested_subtractive_buildpart_inside_active_builder",
                    "message": "nested subtractive BuildPart",
                    "repair_hint": "Keep subtractive features in the same active BuildPart.",
                },
            ],
            "repair_recipe": {
                "recipe_id": "enclosure_whole_part_rebuild",
                "repair_family": "general_geometry",
                "recipe_summary": "Rebuild the enclosure shell first, then reapply local features.",
                "recipe_skeleton": {
                    "steps": [
                        "build the outer shell first",
                        "open the cavity with one builder-native subtractive recipe",
                        "add local enclosure features after the shell is valid",
                    ]
                },
            },
        },
    )

    assert len(runtime_skills) > 6

    prepared = V2ContextManager()._prepare_runtime_skills_payload(runtime_skills)
    prepared_ids = [
        str(item.get("skill_id"))
        for item in prepared
        if isinstance(item, dict) and isinstance(item.get("skill_id"), str)
    ]

    assert "execute_build123d_failure_lint_contract" in prepared_ids
    assert "multi_part_assembled_pose_bbox_contract" in prepared_ids
    assert "nested_hollow_section_builder_native_cavity" in prepared_ids
    assert "__truncated_skills__" in {next(iter(item.keys())) for item in prepared if isinstance(item, dict)}

def test_prepare_runtime_skills_payload_keeps_local_finish_retry_guidance_when_truncated() -> None:
    runtime_skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a two-part rounded clamshell enclosure and add local magnet recesses on the "
                "mating faces with exact topology refs after query_topology."
            )
        },
        latest_validation={
            "blockers": [
                "feature_target_face_subtractive_merge",
                "add_four_corner_magnet_recesses_on_the_mating_faces",
            ],
            "repair_hints": ["query_topology", "apply_cad_action"],
        },
        latest_write_health={"tool": "apply_cad_action"},
        previous_tool_failure_summary={
            "tool": "apply_cad_action",
            "error": (
                "apply_cad_action preflight failed: create_sketch must use face_ref from latest "
                "query_topology during local_finish"
            ),
            "failure_kind": "apply_cad_action_contract_failure",
            "effective_failure_kind": "apply_cad_action_contract_failure",
        },
    )

    runtime_skills.extend(
        [
            {
                "skill_id": f"filler_skill_{index}",
                "when_relevant": "filler",
                "guidance": ["filler"],
                "context_priority": 50 + index,
            }
            for index in range(6)
        ]
    )

    assert len(runtime_skills) > 6

    prepared = V2ContextManager()._prepare_runtime_skills_payload(runtime_skills)
    prepared_ids = [
        str(item.get("skill_id"))
        for item in prepared
        if isinstance(item, dict) and isinstance(item.get("skill_id"), str)
    ]

    assert "local_finish_retry_bind_latest_face_ref" in prepared_ids
    assert "local_finish_exact_face_ref_contract" in prepared_ids
    assert "__truncated_skills__" in {
        next(iter(item.keys())) for item in prepared if isinstance(item, dict)
    }

def test_prepare_runtime_skills_payload_keeps_detached_subtractive_builder_repair_when_truncated() -> None:
    runtime_skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a two-part rounded clamshell enclosure with overall dimensions 78mm x 56mm x 32mm, "
                "magnet recesses, a thumb notch, and a side pocket."
            )
        },
        latest_validation={},
        latest_write_health={"tool": "execute_build123d"},
        previous_tool_failure_summary={
            "tool": "execute_build123d",
            "error": (
                "Exit code: 1 | Traceback (most recent call last): RuntimeError: Nothing to subtract from"
            ),
        },
    )

    runtime_skills.extend(
        [
            {
                "skill_id": f"filler_skill_{index}",
                "when_relevant": "filler",
                "guidance": ["filler"],
                "context_priority": 60 + index,
            }
            for index in range(6)
        ]
    )

    prepared = V2ContextManager()._prepare_runtime_skills_payload(runtime_skills)
    prepared_ids = [
        str(item.get("skill_id"))
        for item in prepared
        if isinstance(item, dict) and isinstance(item.get("skill_id"), str)
    ]

    assert "multi_part_assembled_pose_bbox_contract" in prepared_ids
    assert "execute_build123d_detached_subtractive_builder_repair" in prepared_ids
    assert "__truncated_skills__" in {
        next(iter(item.keys())) for item in prepared if isinstance(item, dict)
    }

def test_prepare_runtime_skills_payload_keeps_code_first_local_finish_tail_contract_when_truncated() -> None:
    runtime_skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a rectangular service bracket sized 66mm x 42mm x 16mm with a shallow top "
                "pocket and two mounting holes on the bottom face. Add a centered rounded-rectangle "
                "recess on the front face sized about 12mm x 6mm and 2mm deep, plus small fillets "
                "around the top opening and countersinks on the mounting holes, so that a "
                "topology-aware local finishing pass on the front face is useful."
            )
        },
        latest_validation={},
        latest_write_health={"tool": "execute_build123d"},
    )

    runtime_skills.extend(
        [
            {
                "skill_id": f"filler_skill_{index}",
                "when_relevant": "filler",
                "guidance": ["filler"],
                "context_priority": 60 + index,
            }
            for index in range(8)
        ]
    )

    prepared = V2ContextManager()._prepare_runtime_skills_payload(runtime_skills)
    prepared_ids = [
        str(item.get("skill_id"))
        for item in prepared
        if isinstance(item, dict) and isinstance(item.get("skill_id"), str)
    ]

    assert "code_first_local_finish_tail_contract" in prepared_ids
    assert prepared_ids.index("code_first_local_finish_tail_contract") < prepared_ids.index(
        "local_finish_exact_face_ref_contract"
    )
    assert "__truncated_skills__" in {
        next(iter(item.keys())) for item in prepared if isinstance(item, dict)
    }

def test_prepare_runtime_skills_payload_keeps_enclosure_local_feature_contract_after_box_keyword_lint() -> None:
    runtime_skills = build_runtime_skill_pack(
        requirements={
            "description": (
                "Create a two-part rounded clamshell storage enclosure with overall dimensions "
                "78mm x 56mm x 32mm. Use a pin hinge at the back, keep wall thickness near 2.4mm, "
                "add four corner magnet recesses on the mating faces, a front thumb notch about "
                "10mm wide, two shallow organic top cavities, one bottom cable post, and one side plug pocket."
            )
        },
        latest_validation={},
        latest_write_health={"tool": "execute_build123d"},
        previous_tool_failure_summary={
            "tool": "execute_build123d",
            "lint_hits": [
                {
                    "rule_id": "invalid_build123d_keyword.box_radius_alias",
                    "message": "Box(...) does not accept a radius= keyword in Build123d.",
                }
            ],
            "repair_recipe": {
                "recipe_id": "build123d_box_keyword_contract",
                "recipe_summary": (
                    "Stay on native Box(length, width, height) keywords and use "
                    "RectangleRounded(...) + extrude(...) or explicit edge fillets for rounded corners."
                ),
            },
        },
    )

    prepared = V2ContextManager()._prepare_runtime_skills_payload(runtime_skills)
    prepared_ids = [
        str(item.get("skill_id"))
        for item in prepared
        if isinstance(item, dict) and isinstance(item.get("skill_id"), str)
    ]

    assert "execute_build123d_api_lint_repair_first" in prepared_ids
    assert "enclosure_local_feature_placement_contract" in prepared_ids

def test_compacted_prompt_payload_keeps_topology_targeting_and_local_finish_contract() -> None:
    run_state = RunState(
        session_id="session-compacted-local-finish-contract",
        requirements={"description": "Add countersunk mounting holes on the bottom face and finish the top opening edges."},
    )
    run_state.evidence.update(
        tool_name="query_topology",
        round_no=3,
        payload={
            "success": True,
            "session_id": "session-compacted-local-finish-contract",
            "step": 1,
            "matched_ref_ids": [
                "face:1:F_bottom_host",
                "edge:1:E_bottom_outer_1",
                "edge:1:E_bottom_outer_2",
            ],
            "candidate_sets": [
                {
                    "candidate_id": "bottom_faces",
                    "label": "Bottom Faces",
                    "entity_type": "face",
                    "ref_ids": ["face:1:F_bottom_host"],
                    "semantic_host_roles": ["bottom_host"],
                },
                {
                    "candidate_id": "bottom_outer_edges",
                    "label": "Bottom Outer Edges",
                    "entity_type": "edge",
                    "ref_ids": [
                        "edge:1:E_bottom_outer_1",
                        "edge:1:E_bottom_outer_2",
                    ],
                    "semantic_host_roles": ["bottom_host", "outer_edge"],
                },
            ],
            "topology_index": {
                "faces": [
                    {
                        "face_ref": f"face:1:F_{index}",
                        "edge_refs": [f"edge:1:E_{index}_{edge}" for edge in range(4)],
                        "adjacent_face_refs": [f"face:1:F_adj_{index}_{adj}" for adj in range(4)],
                    }
                    for index in range(18)
                ],
                "edges": [
                    {
                        "edge_ref": f"edge:1:E_{index}",
                        "adjacent_face_refs": [f"face:1:F_adj_{index}_{adj}" for adj in range(3)],
                    }
                    for index in range(24)
                ],
                "faces_total": 18,
                "edges_total": 24,
            },
        },
    )

    context_manager = V2ContextManager(soft_chars=1, hard_chars=2500)
    bundle = context_manager.build_prompt_bundle(
        run_state=run_state,
        turn_tool_policy=TurnToolPolicy(
            round_no=4,
            policy_id="apply_local_finish_after_topology_targeting_from_read_stall",
            mode="local_finish",
            reason="Consume topology refs with apply_cad_action.",
            allowed_tool_names=["apply_cad_action"],
            preferred_tool_names=["apply_cad_action"],
        ),
    )

    payload = bundle.payload
    assert isinstance(payload.get("topology_targeting_summary"), dict)
    assert payload["topology_targeting_summary"]["matched_ref_ids"][0] == "face:1:F_bottom_host"
    assert isinstance(payload.get("local_finish_contract"), dict)
    assert payload["local_finish_contract"]["preferred_face_refs"] == ["face:1:F_bottom_host"]
    freshest_topology = payload["freshest_evidence"]["query_topology"]
    assert isinstance(freshest_topology.get("targeting_summary"), dict)
    assert freshest_topology["targeting_summary"]["matched_ref_id_count"] == 3
