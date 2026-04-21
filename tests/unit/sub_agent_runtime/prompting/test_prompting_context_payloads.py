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
    assert "action_params.face_ref" in " ".join(contract["instructions"])
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
