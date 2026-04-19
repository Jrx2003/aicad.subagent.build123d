from types import SimpleNamespace

from sub_agent_runtime.context_manager import V2ContextManager
from sub_agent_runtime.skill_pack import build_runtime_skill_pack
from sub_agent_runtime.turn_state import (
    RunState,
    ToolCallRecord,
    ToolCategory,
    ToolResultRecord,
    TurnRecord,
    TurnToolPolicy,
)


def test_previous_tool_failure_summary_keeps_repair_recipe_steps_visible() -> None:
    repair_recipe = {
        "recipe_id": "explicit_anchor_hole_same_builder_subtract_recipe",
        "repair_family": "explicit_anchor_hole",
        "recipe_summary": (
            "For repeated countersunk hole layouts, keep the host in one BuildPart and stay on a "
            "supported host-face countersink lane instead of improvising nested cutters."
        ),
        "recipe_skeleton": {
            "mode": "subtree_rebuild_via_execute_build123d",
            "hole_call": "CounterSinkHole_or_Hole",
            "steps": [
                "with BuildPart() as part: build the host body first",
                "compute the full hole center set in the host-face coordinate frame before cutting",
                "prefer one CounterSinkHole helper-first pass on the actual host-face plane",
                "do not use nested with BuildPart() as cutter blocks inside the host builder",
            ],
        },
    }
    payload = {
        "failure_kind": "execute_build123d_api_lint_failure",
        "stderr": "execute_build123d preflight lint failed",
        "lint_hits": [
            {
                "rule_id": "invalid_build123d_contract.explicit_anchor_manual_cutter_requires_subtract_mode",
                "message": "manual cutter missing subtract mode",
                "repair_hint": "switch to a helper-first host-face countersink recipe",
                "recommended_recipe_id": "explicit_anchor_hole_same_builder_subtract_recipe",
            }
        ],
        "repair_recipe": repair_recipe,
    }
    turn = TurnRecord(
        round_no=1,
        decision_summary="broken countersink attempt",
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
                error="execute_build123d preflight lint failed",
                payload=payload,
            )
        ],
    )
    run_state = RunState(
        session_id="session-failure-summary",
        requirements={"description": "Create a countersunk bracket."},
        turns=[turn],
        latest_write_payload=payload,
    )

    context_manager = V2ContextManager()
    summary = context_manager.build_previous_tool_failure_summary(run_state)

    assert summary is not None
    assert summary["repair_recipe"]["recipe_id"] == "explicit_anchor_hole_same_builder_subtract_recipe"
    assert summary["repair_recipe"]["repair_family"] == "explicit_anchor_hole"
    assert (
        summary["repair_recipe"]["recipe_skeleton"]["steps"][1]
        == "compute the full hole center set in the host-face coordinate frame before cutting"
    )
    assert "__truncated_items__" not in str(summary["repair_recipe"]["recipe_skeleton"]["steps"])


def test_prompt_payload_keeps_topology_candidate_refs_visible_for_local_finish_retry() -> None:
    run_state = RunState(
        session_id="session-topology-targeting-summary",
        requirements={"description": "Create a bracket with a local fillet around the opening rim."},
    )
    run_state.latest_write_payload = {
        "failure_kind": "apply_cad_action_contract_failure",
        "summary": "Local fillet/chamfer should consume explicit edge_refs once query_topology has already returned targetable edge candidate sets.",
        "candidate_edge_set_labels": ["Opening Rim Edges"],
    }
    run_state.evidence.update(
        tool_name="query_topology",
        round_no=4,
        payload={
            "success": True,
            "matched_ref_ids": [
                "edge:1:E_opening_rim_1",
                "edge:1:E_opening_rim_2",
            ],
            "matched_ref_id_count": 2,
            "candidate_sets": [
                {
                    "candidate_id": "opening_rim_edges",
                    "label": "Opening Rim Edges",
                    "entity_type": "edge",
                    "ref_ids": [
                        "edge:1:E_opening_rim_1",
                        "edge:1:E_opening_rim_2",
                    ],
                    "semantic_host_roles": ["opening_rim"],
                }
            ],
            "topology_index": {
                "faces_total": 20,
                "edges_total": 52,
                "faces_truncated": False,
                "edges_truncated": True,
            },
        },
    )

    context_manager = V2ContextManager()
    payload = context_manager.build_prompt_payload(run_state=run_state, max_rounds=4)

    topology_summary = payload.get("topology_targeting_summary")
    assert isinstance(topology_summary, dict)
    assert topology_summary["matched_ref_ids"] == [
        "edge:1:E_opening_rim_1",
        "edge:1:E_opening_rim_2",
    ]
    assert topology_summary["candidate_sets"][0]["label"] == "Opening Rim Edges"
    assert topology_summary["candidate_sets"][0]["ref_ids"] == [
        "edge:1:E_opening_rim_1",
        "edge:1:E_opening_rim_2",
    ]
    assert payload["freshest_evidence"]["query_topology"]["targeting_summary"]["candidate_sets"][0]["ref_ids"] == [
        "edge:1:E_opening_rim_1",
        "edge:1:E_opening_rim_2",
    ]


def test_prompt_payload_adds_local_finish_contract_when_topology_refs_exist() -> None:
    run_state = RunState(
        session_id="session-local-finish-contract",
        requirements={"description": "Add two countersunk mounting holes on the bottom face."},
    )
    run_state.evidence.update(
        tool_name="query_topology",
        round_no=3,
        payload={
            "success": True,
            "matched_ref_ids": [
                "face:1:F_bottom_host",
                "edge:1:E_bottom_outer_1",
            ],
            "candidate_sets": [
                {
                    "candidate_id": "bottom_faces",
                    "label": "Bottom Faces",
                    "entity_type": "face",
                    "ref_ids": ["face:1:F_bottom_host"],
                    "semantic_host_roles": ["bottom_host"],
                }
            ],
        },
    )
    run_state.feature_graph = SimpleNamespace(
        to_query_payload=lambda **_: {
            "success": True,
            "nodes": [],
            "edges": [],
            "bindings": [],
            "active_feature_instances": [
                {
                    "instance_id": "instance.explicit_anchor_hole.feature_countersink",
                    "family_id": "explicit_anchor_hole",
                    "status": "blocked",
                }
            ],
        },
        nodes={
            "feature.explicit_anchor_hole": SimpleNamespace(
                node_id="feature.explicit_anchor_hole",
                kind="feature",
                status="blocked",
            )
        },
        edges={},
        bindings={},
        active_node_ids=["feature.explicit_anchor_hole"],
        revision_history=[],
        feature_instances={
            "instance.explicit_anchor_hole.feature_countersink": SimpleNamespace(
                family_id="explicit_anchor_hole",
                status="blocked",
            )
        },
    )

    context_manager = V2ContextManager()
    payload = context_manager.build_prompt_payload(
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

    contract = payload.get("local_finish_contract")
    assert isinstance(contract, dict)
    assert contract["must_consume_exact_topology_refs"] is True
    assert contract["preferred_face_refs"] == ["face:1:F_bottom_host"]
    assert contract["preferred_action_types"][:2] == ["hole", "countersink"]
    assert contract["preferred_edge_refs"] == ["edge:1:E_bottom_outer_1"]
    assert "face_ref" in " ".join(contract["instructions"])
    assert (
        "prefer a direct hole/countersink apply_cad_action on that exact face_ref before opening a new sketch window"
        in " ".join(contract["instructions"])
    )
    assert "do not spend apply_cad_action on get_history" in " ".join(contract["instructions"])
    assert contract["candidate_sets"][0]["label"] == "Bottom Faces"


def test_local_finish_contract_prefers_candidate_set_preferred_face_refs_over_raw_matched_refs() -> None:
    run_state = RunState(
        session_id="session-local-finish-preferred-face-refs",
        requirements={"description": "Add a face-attached local recess on the front face."},
    )
    run_state.evidence.update(
        tool_name="query_topology",
        round_no=3,
        payload={
            "success": True,
            "matched_ref_ids": [
                "face:1:F_front_cyl",
                "face:1:F_front_planar",
                "face:1:F_bottom_planar",
                "edge:1:E_front_edge_1",
            ],
            "candidate_sets": [
                {
                    "candidate_id": "front_faces",
                    "label": "Front Faces",
                    "entity_type": "face",
                    "preferred_ref_id": "face:1:F_front_planar",
                    "ref_ids": [
                        "face:1:F_front_planar",
                        "face:1:F_front_cyl",
                    ],
                },
                {
                    "candidate_id": "bottom_faces",
                    "label": "Bottom Faces",
                    "entity_type": "face",
                    "preferred_ref_id": "face:1:F_bottom_planar",
                    "ref_ids": ["face:1:F_bottom_planar"],
                },
            ],
        },
    )

    context_manager = V2ContextManager()
    payload = context_manager.build_prompt_payload(
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

    contract = payload.get("local_finish_contract")
    assert isinstance(contract, dict)
    assert contract["preferred_face_refs"][:2] == [
        "face:1:F_front_planar",
        "face:1:F_bottom_planar",
    ]
    assert contract["preferred_face_refs"][0] != "face:1:F_front_cyl"
    assert payload["topology_targeting_summary"]["candidate_sets"][0]["preferred_ref_id"] == "face:1:F_front_planar"


def test_build_messages_surfaces_human_readable_exact_topology_refs_for_local_finish() -> None:
    run_state = RunState(
        session_id="session-local-finish-focus-attachment",
        requirements={"description": "Add a front-face local recess with exact topology refs."},
    )
    run_state.evidence.update(
        tool_name="query_topology",
        round_no=3,
        payload={
            "success": True,
            "matched_ref_ids": [
                "face:1:F_front_planar",
                "face:1:F_front_cyl",
            ],
            "candidate_sets": [
                {
                    "candidate_id": "front_faces",
                    "label": "Front Faces",
                    "entity_type": "face",
                    "preferred_ref_id": "face:1:F_front_planar",
                    "ref_ids": [
                        "face:1:F_front_planar",
                        "face:1:F_front_cyl",
                    ],
                }
            ],
        },
    )

    messages = V2ContextManager().build_messages(
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
    combined = "\n".join(message.content for message in messages if message.role == "user")

    assert "Exact topology refs to consume now" in combined
    assert "face:1:F_front_planar" in combined
    assert "Front Faces" in combined


def test_build_messages_surfaces_direct_hole_before_sketch_guidance_for_exact_face_ref() -> None:
    attachment = V2ContextManager()._build_local_finish_focus_attachment(
        {
            "local_finish_contract": {
                "preferred_face_refs": ["face:1:F_bottom_host"],
                "preferred_edge_refs": [],
                "preferred_action_types": ["hole", "countersink", "counterbore"],
                "candidate_sets": [
                    {
                        "candidate_id": "bottom_faces",
                        "label": "Bottom Faces",
                        "entity_type": "face",
                        "preferred_ref_id": "face:1:F_bottom_host",
                        "ref_ids": ["face:1:F_bottom_host"],
                    }
                ],
            }
        }
    )
    assert attachment is not None
    combined = attachment.content

    assert "preferred_action_types: hole, countersink" in combined
    assert "before opening create_sketch(face_ref=...)" in combined


def test_build_messages_system_prompt_surfaces_core_build123d_contracts() -> None:
    run_state = RunState(
        session_id="session-system-prompt-contracts",
        requirements={
            "description": (
                "Create a two-part rounded clamshell enclosure with a pin hinge and rounded shell corners."
            )
        },
    )

    messages = V2ContextManager().build_messages(run_state=run_state)
    system_prompt = messages[0].content

    assert "Sketch primitives such as `Circle(...)`, `Ellipse(...)`, and `Rectangle(...)` belong inside `BuildSketch`" in system_prompt
    assert "do not pass `axis=` or `length=`" in system_prompt
    assert "Do not write `with Rot(...):` or `with Pos(...):`" in system_prompt
    assert "Do not import `ocp_vscode` or call `show(...)` / `show_object(...)`" in system_prompt
    assert "do not create a primitive and then relocate it with `Pos(...) * solid` or `Rot(...) * solid`" in system_prompt
    assert "Do not open a nested `BuildPart` cutter inside an active host and then mutate `host.part -= cutter.part`" in system_prompt
    assert "Do not open a detached builder whose first real operation is subtractive" in system_prompt
    assert "Do not mix `Circle(...)` with `BuildLine` + `make_face()` in the same `BuildSketch` to fake a semicircle" in system_prompt
    assert "front/back -> `Plane.XZ`" in system_prompt
    assert "back seam at `y = -depth/2` and the front opening/notch boundary at `y = +depth/2`" in system_prompt
    assert "pin/barrel axis usually runs along X/width; do not leave hinge barrels or hinge pins as default Z-axis cylinders" in system_prompt
    assert "do not drop an unrotated default `Cylinder(...)` directly onto `(x, hinge_y, split_z)`" in system_prompt
    assert "without a supported rotation/orientation lane that cylinder still runs along Z" in system_prompt
    assert "A plain `pin hinge` or `mechanical hinge` on a two-part lid/base enclosure does not by itself authorize extra detached hinge solids or a third physical part" in system_prompt
    assert "`extrude(amount=h)` grows one-sided from the active sketch plane" in system_prompt
    assert "do not assume `Locations((0, 0, center_z))` plus `extrude(amount=h)` creates a centered shell interval" in system_prompt
    assert "do not invent `Box(..., radius=...)`" in system_prompt
    assert "prefer that rounded footprint directly over a first-pass broad shell-edge fillet" in system_prompt
    assert "`RectangleRounded(width, depth, radius=...)` already uses the outer footprint spans" in system_prompt
    assert "do not shrink the requested outer width/depth to `width - 2*radius`" in system_prompt
    assert "use lowercase `scale(shape, by=(sx, sy, sz))`" in system_prompt
    assert "`Rot(...) * part` or `Pos(...) * Rot(...) * part`" in system_prompt
    assert "do not stop at a prose-only repair sketch" in system_prompt
    assert "call that tool directly instead of returning a code block or repair plan without a tool invocation" in system_prompt


def test_local_finish_contract_surfaces_preserved_local_centers_from_domain_kernel() -> None:
    context_manager = V2ContextManager()
    contract = context_manager._build_local_finish_contract(  # noqa: SLF001
        turn_tool_policy=TurnToolPolicy(
            round_no=4,
            policy_id="continue_local_finish_after_semantic_refresh",
            mode="local_finish",
            reason="Keep the local-finish lane active.",
            allowed_tool_names=["apply_cad_action"],
            preferred_tool_names=["apply_cad_action"],
        ),
        topology_targeting_summary={
            "matched_ref_ids": ["face:1:F_mount"],
            "candidate_sets": [
                {
                    "candidate_id": "mount_faces",
                    "label": "Mount Faces",
                    "entity_type": "face",
                    "ref_ids": ["face:1:F_mount"],
                }
            ],
        },
        domain_kernel_digest={
            "active_feature_instances": [
                {
                    "family_id": "explicit_anchor_hole",
                    "host_ids": ["body.primary"],
                    "parameter_bindings": {
                        "expected_local_center_count": 2,
                        "realized_centers": [[-23.0, -13.0], [-23.0, 13.0]],
                    },
                }
            ]
        },
    )

    assert isinstance(contract, dict)
    assert contract["preserve_existing_local_layout"]["expected_center_count"] == 2
    assert contract["preserve_existing_local_layout"]["realized_centers"] == [
        [-23.0, -13.0],
        [-23.0, 13.0],
    ]
    assert any("reuse that exact center set" in item for item in contract["instructions"])


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
