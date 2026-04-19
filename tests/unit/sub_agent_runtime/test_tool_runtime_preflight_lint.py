import ast

from sub_agent_runtime.tool_runtime import (
    _collect_numeric_assignment_env,
    _find_named_face_plane_family_mismatch_hits,
    _find_rectanglerounded_radius_bounds_hits,
    _named_face_requirement_plane_groups,
    _preflight_gate_apply_cad_action,
    _preflight_lint_execute_build123d,
)
from types import SimpleNamespace

from sub_agent_runtime.turn_state import RunState, TurnToolPolicy


_CYLINDRICAL_SLOT_REQUIREMENT = (
    "Create a new part with units in millimeters. Draw a center rectangle 100.0×50.0 "
    "in the XY plane and extrude it by 20.0 to form a block. Create a cutting cylinder: "
    "radius 12.0, axis along the X-axis, cylinder centerline placed at (0,0,8.0), "
    "length set to 110.0 to cover the entire length. Perform a Boolean difference: "
    "the block as the target body and the cylinder as the tool body, resulting in "
    "a semicircular slot on the top surface."
)

_SPHERICAL_RECESS_PATTERN_REQUIREMENT = (
    "Draw a 50.0x50.0mm square in the XY plane and extrude it by 15.0mm to create "
    "the base. Select the top face as the reference and create a sketch for "
    "positioning the center of the recess. Draw the center point and use it as a "
    "reference to create an auxiliary plane perpendicular to the top face. On the "
    "auxiliary plane, draw a semicircle with a radius of 5.0mm (the diameter edge "
    "coincides with the top face) and use the revolve cut command to generate the "
    "first hemispherical recess. Then use the linear pattern command, with "
    "direction 1 along the X-axis, spacing 15.0mm, and quantity 3; direction 2 "
    "along the Y-axis, spacing 15.0mm, and quantity 3. Select \"Center the pattern\" "
    "or pre-calculate the starting position to ensure that the nine holes are "
    "completely symmetrically centered on the 50x50 face, completing the "
    "construction of the shock absorber pad."
)

_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT = (
    "Select the top plane, draw a 100.0x60.0 millimeter rectangle, and extrude it "
    "by 8.0 millimeters. Use the sketch to draw four points with coordinates "
    "(25,15), (25,45), (75,15), and (75,45). Create countersunk through-holes at "
    "those four points with a through-hole diameter of 6.0 millimeters and a 90 "
    "degree countersink."
)

_HALF_SHELL_DIRECTIONAL_HOLE_REQUIREMENT = (
    "Create a half-cylindrical shell by sketching an outer semicircle of radius "
    "25.0 millimeters and an inner semicircle of radius 17.5 millimeters on the XY "
    "plane, closing the profile along the split line, and extruding it 40.0 "
    "millimeters. Add a bottom rectangular pad spanning x = -27.0 to 27.0 "
    "millimeters with a height of 8.0 millimeters, remove the inner 35.0 "
    "millimeter diameter clearance so the shell remains open above the split line "
    "and two side lugs remain outside the bore, and union this pad with the shell. "
    "At z = 20.0 millimeters, drill two 6.0 millimeter through-holes through the "
    "lugs in the Y direction, centered at x = -22.25 and x = 22.25 millimeters."
)

_ANNULAR_GROOVE_REQUIREMENT = (
    "Select the XY plane, draw a circle with a diameter of 50.0 mm and a square with "
    "a side length of 25.0 mm centered. Extrude the section by 60.0 mm. Select the "
    "front view plane, at a height of 30.0 mm, draw a 5.0 mm x 2.0 mm rectangle "
    "aligned with the edge, and use a revolved cut to create an annular groove."
)


def test_preflight_lint_rejects_bare_subtract_and_surfaces_slot_recipe() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(10, 10, 10)\n"
            "    cutter = Cylinder(radius=2, height=12)\n"
            "    subtract(cutter)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_CYLINDRICAL_SLOT_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.bare_subtract_helper" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "explicit_cylindrical_slot_boolean_safe_recipe"


def test_preflight_lint_rejects_bare_rotate_helper() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "solid = Cylinder(radius=2, height=12)\n"
            "rotate(axis=Axis.Y, angle=90)\n"
            "result = solid\n"
        ),
        session_id="test-session",
        requirement_text="make a rotated cylinder",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.bare_rotate_helper" in rule_ids


def test_preflight_lint_rejects_display_only_debug_helpers() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "from ocp_vscode import show\n"
            "with BuildPart() as part:\n"
            "    Box(10, 10, 10)\n"
            "result = part.part\n"
            "show(result)\n"
        ),
        session_id="test-session",
        requirement_text="create a simple block",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_runtime.display_only_helper_import" in rule_ids


def test_apply_cad_action_preflight_rejects_rollback_escape_under_local_finish_policy() -> None:
    run_state = RunState(
        session_id="session-local-finish-rollback-escape",
        requirements={"description": "Create a bracket with topology-aware local finishing."},
    )
    run_state.add_turn_tool_policy(
        TurnToolPolicy(
            round_no=7,
            policy_id="apply_local_finish_after_topology_targeting",
            mode="local_finish",
            reason="Consume the resolved topology refs with a local finishing step.",
            allowed_tool_names=["apply_cad_action"],
            preferred_tool_names=["apply_cad_action"],
        )
    )

    payload = _preflight_gate_apply_cad_action(
        action_type="rollback",
        action_params={"to_step": 0},
        run_state=run_state,
    )

    assert payload is not None
    assert payload["failure_kind"] == "apply_cad_action_contract_failure"
    assert "rollback" in payload["error_message"]


def test_apply_cad_action_preflight_allows_topology_anchored_create_sketch_under_local_finish_policy() -> None:
    run_state = RunState(
        session_id="session-local-finish-create-sketch",
        requirements={"description": "Create a bracket with topology-aware local finishing."},
    )
    run_state.add_turn_tool_policy(
        TurnToolPolicy(
            round_no=7,
            policy_id="apply_local_finish_after_topology_targeting",
            mode="local_finish",
            reason="Consume the resolved topology refs with a local finishing step.",
            allowed_tool_names=["apply_cad_action"],
            preferred_tool_names=["apply_cad_action"],
        )
    )

    payload = _preflight_gate_apply_cad_action(
        action_type="create_sketch",
        action_params={"face_ref": "face:front:mating"},
        run_state=run_state,
    )

    assert payload is None


def test_apply_cad_action_preflight_rejects_broad_face_alias_for_hole_under_local_finish_policy() -> None:
    run_state = RunState(
        session_id="session-local-finish-hole-face-alias",
        requirements={"description": "Create a bracket with topology-aware local finishing."},
    )
    run_state.add_turn_tool_policy(
        TurnToolPolicy(
            round_no=7,
            policy_id="apply_local_finish_after_topology_targeting",
            mode="local_finish",
            reason="Consume the resolved topology refs with a local finishing step.",
            allowed_tool_names=["apply_cad_action"],
            preferred_tool_names=["apply_cad_action"],
        )
    )
    run_state.evidence.update(
        tool_name="query_topology",
        payload={
            "matched_ref_ids": [
                "face:1:F_front_mounting",
                "face:1:F_front_mounting_2",
                "edge:1:E_front_top_0",
            ],
            "candidate_sets": [
                {
                    "candidate_id": "front_faces",
                    "label": "Front Faces",
                    "entity_type": "face",
                    "ref_ids": [
                        "face:1:F_front_mounting",
                        "face:1:F_front_mounting_2",
                    ],
                },
                {
                    "candidate_id": "front_top_edges",
                    "label": "Front Top Edges",
                    "entity_type": "edge",
                    "ref_ids": ["edge:1:E_front_top_0", "edge:1:E_front_top_1"],
                },
            ],
        },
        round_no=6,
    )

    payload = _preflight_gate_apply_cad_action(
        action_type="hole",
        action_params={
            "face": "front",
            "centers": [[-23.0, 0.0], [23.0, 0.0]],
            "diameter": 5.0,
            "depth": 15.0,
            "countersink_diameter": 9.0,
            "countersink_angle": 90.0,
        },
        run_state=run_state,
    )

    assert payload is not None
    assert payload["failure_kind"] == "apply_cad_action_contract_failure"
    assert "face_ref" in payload["error_message"]
    assert payload["preferred_face_refs"] == [
        "face:1:F_front_mounting",
        "face:1:F_front_mounting_2",
    ]
    assert payload["candidate_face_set_labels"] == ["Front Faces"]


def test_apply_cad_action_preflight_normalizes_exact_face_ref_passed_via_face_field() -> None:
    run_state = RunState(
        session_id="session-local-finish-hole-face-ref-alias",
        requirements={"description": "Create a bracket with topology-aware local finishing."},
    )
    run_state.add_turn_tool_policy(
        TurnToolPolicy(
            round_no=7,
            policy_id="apply_local_finish_after_topology_targeting",
            mode="local_finish",
            reason="Consume the resolved topology refs with a local finishing step.",
            allowed_tool_names=["apply_cad_action"],
            preferred_tool_names=["apply_cad_action"],
        )
    )
    run_state.evidence.update(
        tool_name="query_topology",
        payload={
            "matched_ref_ids": [
                "face:1:F_front_mounting",
                "edge:1:E_front_top_0",
            ],
            "candidate_sets": [
                {
                    "candidate_id": "front_faces",
                    "label": "Front Faces",
                    "entity_type": "face",
                    "ref_ids": ["face:1:F_front_mounting"],
                }
            ],
        },
        round_no=6,
    )
    action_params = {
        "face": "face:1:F_front_mounting",
        "centers": [[-23.0, 0.0], [23.0, 0.0]],
        "diameter": 5.0,
        "depth": 15.0,
        "countersink_diameter": 9.0,
        "countersink_angle": 90.0,
    }

    payload = _preflight_gate_apply_cad_action(
        action_type="hole",
        action_params=action_params,
        run_state=run_state,
    )

    assert payload is None
    assert action_params["face_ref"] == "face:1:F_front_mounting"
    assert "face" not in action_params


def test_apply_cad_action_preflight_normalizes_exact_edge_refs_passed_via_edges_field() -> None:
    run_state = RunState(
        session_id="session-local-finish-edge-refs-alias",
        requirements={"description": "Create a bracket with topology-aware local finishing."},
    )
    run_state.add_turn_tool_policy(
        TurnToolPolicy(
            round_no=7,
            policy_id="continue_local_finish_after_semantic_refresh",
            mode="local_finish",
            reason="Consume the resolved topology refs with a local finishing step.",
            allowed_tool_names=["apply_cad_action"],
            preferred_tool_names=["apply_cad_action"],
        )
    )
    run_state.evidence.update(
        tool_name="query_topology",
        payload={
            "candidate_sets": [
                {
                    "candidate_id": "top_outer_edges",
                    "label": "Top Outer Edges",
                    "entity_type": "edge",
                    "ref_ids": [
                        "edge:2:E_a",
                        "edge:2:E_b",
                        "edge:2:E_c",
                    ],
                }
            ],
        },
        round_no=6,
    )
    action_params = {
        "edges": ["edge:2:E_a", "edge:2:E_b"],
        "radius": 1.5,
    }

    payload = _preflight_gate_apply_cad_action(
        action_type="fillet",
        action_params=action_params,
        run_state=run_state,
    )

    assert payload is None
    assert action_params["edge_refs"] == ["edge:2:E_a", "edge:2:E_b"]
    assert "edges" not in action_params


def test_apply_cad_action_preflight_resolves_unambiguous_broad_face_alias_to_single_candidate_ref() -> None:
    run_state = RunState(
        session_id="session-local-finish-hole-bottom-alias",
        requirements={"description": "Create a bracket with topology-aware local finishing."},
    )
    run_state.add_turn_tool_policy(
        TurnToolPolicy(
            round_no=7,
            policy_id="apply_local_finish_after_topology_targeting",
            mode="local_finish",
            reason="Consume the resolved topology refs with a local finishing step.",
            allowed_tool_names=["apply_cad_action"],
            preferred_tool_names=["apply_cad_action"],
        )
    )
    run_state.evidence.update(
        tool_name="query_topology",
        payload={
            "matched_ref_ids": [
                "face:1:F_bottom_mounting",
                "face:1:F_top_outer",
                "face:1:F_top_inner",
            ],
            "candidate_sets": [
                {
                    "candidate_id": "bottom_faces",
                    "label": "Bottom Faces",
                    "entity_type": "face",
                    "ref_ids": ["face:1:F_bottom_mounting"],
                },
                {
                    "candidate_id": "top_faces",
                    "label": "Top Faces",
                    "entity_type": "face",
                    "ref_ids": ["face:1:F_top_outer", "face:1:F_top_inner"],
                },
            ],
        },
        round_no=6,
    )
    action_params = {
        "face": "bottom",
        "hole_type": "countersink",
        "diameter": 3.5,
        "depth": 10.0,
        "countersink_diameter": 6.5,
        "countersink_angle": 90.0,
    }

    payload = _preflight_gate_apply_cad_action(
        action_type="hole",
        action_params=action_params,
        run_state=run_state,
    )

    assert payload is None
    assert action_params["face_ref"] == "face:1:F_bottom_mounting"
    assert "face" not in action_params


def test_apply_cad_action_preflight_normalizes_exact_face_reference_alias() -> None:
    run_state = RunState(
        session_id="session-local-finish-face-reference-alias",
        requirements={"description": "Create a bracket with topology-aware local finishing."},
    )
    run_state.add_turn_tool_policy(
        TurnToolPolicy(
            round_no=7,
            policy_id="continue_local_finish_after_semantic_refresh",
            mode="local_finish",
            reason="Consume the resolved topology refs with a local finishing step.",
            allowed_tool_names=["apply_cad_action"],
            preferred_tool_names=["apply_cad_action"],
        )
    )
    run_state.evidence.update(
        tool_name="query_topology",
        payload={
            "candidate_sets": [
                {
                    "candidate_id": "bottom_faces",
                    "label": "Bottom Faces",
                    "entity_type": "face",
                    "ref_ids": ["face:3:F_bottom_mounting"],
                }
            ],
        },
        round_no=6,
    )
    action_params = {
        "face_reference": "face:3:F_bottom_mounting",
        "hole_type": "countersink",
        "diameter": 3.5,
        "depth": 10.0,
    }

    payload = _preflight_gate_apply_cad_action(
        action_type="hole",
        action_params=action_params,
        run_state=run_state,
    )

    assert payload is None
    assert action_params["face_ref"] == "face:3:F_bottom_mounting"
    assert "face_reference" not in action_params


def test_apply_cad_action_preflight_normalizes_unique_candidate_face_ref_under_local_finish() -> None:
    run_state = RunState(
        session_id="session-local-finish-candidate-face-ref",
        requirements={"description": "Apply a topology-aware local hole on the mating face."},
    )
    run_state.add_turn_tool_policy(
        TurnToolPolicy(
            round_no=7,
            policy_id="apply_local_finish_after_topology_targeting",
            mode="local_finish",
            reason="Consume the resolved topology refs with a local finishing step.",
            allowed_tool_names=["apply_cad_action"],
            preferred_tool_names=["apply_cad_action"],
        )
    )
    run_state.evidence.update(
        tool_name="query_topology",
        payload={
            "matched_ref_ids": ["face:1:F_mating"],
            "candidate_sets": [
                {
                    "candidate_id": "mating_faces",
                    "label": "Mating Faces",
                    "entity_type": "face",
                    "ref_ids": ["face:1:F_mating"],
                }
            ],
        },
        round_no=6,
    )
    action_params = {
        "face_ref": "candidate:mating_faces",
        "diameter": 3.5,
        "depth": 10.0,
        "position": [0.0, 0.0],
    }

    payload = _preflight_gate_apply_cad_action(
        action_type="hole",
        action_params=action_params,
        run_state=run_state,
    )

    assert payload is None
    assert action_params["face_ref"] == "face:1:F_mating"


def test_apply_cad_action_preflight_rejects_ambiguous_candidate_face_ref_under_local_finish() -> None:
    run_state = RunState(
        session_id="session-local-finish-ambiguous-candidate-face-ref",
        requirements={"description": "Apply a topology-aware local hole on one top face."},
    )
    run_state.add_turn_tool_policy(
        TurnToolPolicy(
            round_no=7,
            policy_id="apply_local_finish_after_topology_targeting",
            mode="local_finish",
            reason="Consume the resolved topology refs with a local finishing step.",
            allowed_tool_names=["apply_cad_action"],
            preferred_tool_names=["apply_cad_action"],
        )
    )
    run_state.evidence.update(
        tool_name="query_topology",
        payload={
            "matched_ref_ids": [
                "face:1:F_top_a",
                "face:1:F_top_b",
            ],
            "candidate_sets": [
                {
                    "candidate_id": "top_faces",
                    "label": "Top Faces",
                    "entity_type": "face",
                    "ref_ids": ["face:1:F_top_a", "face:1:F_top_b"],
                }
            ],
        },
        round_no=6,
    )
    action_params = {
        "face_ref": "candidate:top_faces",
        "diameter": 3.5,
        "depth": 10.0,
        "position": [0.0, 0.0],
    }

    payload = _preflight_gate_apply_cad_action(
        action_type="hole",
        action_params=action_params,
        run_state=run_state,
    )

    assert payload is not None
    assert payload["failure_kind"] == "apply_cad_action_contract_failure"
    assert "candidate face set" in payload["error_message"]
    assert payload["preferred_face_refs"] == ["face:1:F_top_a", "face:1:F_top_b"]


def test_apply_cad_action_preflight_prefers_candidate_set_preferred_face_refs() -> None:
    run_state = RunState(
        session_id="session-local-finish-preferred-face-order",
        requirements={"description": "Apply a topology-aware local face edit on the planar front face."},
    )
    run_state.add_turn_tool_policy(
        TurnToolPolicy(
            round_no=7,
            policy_id="apply_local_finish_after_topology_targeting",
            mode="local_finish",
            reason="Consume the resolved topology refs with a local finishing step.",
            allowed_tool_names=["apply_cad_action"],
            preferred_tool_names=["apply_cad_action"],
        )
    )
    run_state.evidence.update(
        tool_name="query_topology",
        payload={
            "matched_ref_ids": [
                "face:1:F_front_cyl",
                "face:1:F_front_planar",
                "face:1:F_bottom_planar",
            ],
            "candidate_sets": [
                {
                    "candidate_id": "front_faces",
                    "label": "Front Faces",
                    "entity_type": "face",
                    "preferred_ref_id": "face:1:F_front_planar",
                    "ref_ids": ["face:1:F_front_planar", "face:1:F_front_cyl"],
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
        round_no=6,
    )

    payload = _preflight_gate_apply_cad_action(
        action_type="create_sketch",
        action_params={"plane": "XY"},
        run_state=run_state,
    )

    assert payload is not None
    assert payload["failure_kind"] == "apply_cad_action_contract_failure"
    assert payload["preferred_face_refs"][:2] == [
        "face:1:F_front_planar",
        "face:1:F_bottom_planar",
    ]
    assert payload["preferred_face_refs"][0] != "face:1:F_front_cyl"


def test_apply_cad_action_preflight_normalizes_exact_target_edges_alias() -> None:
    run_state = RunState(
        session_id="session-local-finish-target-edges-alias",
        requirements={"description": "Create a bracket with topology-aware local finishing."},
    )
    run_state.add_turn_tool_policy(
        TurnToolPolicy(
            round_no=7,
            policy_id="continue_local_finish_after_semantic_refresh",
            mode="local_finish",
            reason="Consume the resolved topology refs with a local finishing step.",
            allowed_tool_names=["apply_cad_action"],
            preferred_tool_names=["apply_cad_action"],
        )
    )
    run_state.evidence.update(
        tool_name="query_topology",
        payload={
            "candidate_sets": [
                {
                    "candidate_id": "top_outer_edges",
                    "label": "Top Outer Edges",
                    "entity_type": "edge",
                    "ref_ids": ["edge:3:E_a", "edge:3:E_b"],
                }
            ],
        },
        round_no=6,
    )
    action_params = {
        "target_edges": ["edge:3:E_a", "edge:3:E_b"],
        "radius": 1.5,
    }

    payload = _preflight_gate_apply_cad_action(
        action_type="fillet",
        action_params=action_params,
        run_state=run_state,
    )

    assert payload is None
    assert action_params["edge_refs"] == ["edge:3:E_a", "edge:3:E_b"]
    assert "target_edges" not in action_params


def test_apply_cad_action_preflight_rejects_cut_extrude_without_active_profile_sketch() -> None:
    run_state = RunState(
        session_id="session-cut-extrude-without-profile",
        requirements={"description": "Cut a face-local rectangular pocket after opening a sketch."},
    )
    run_state.action_history = [
        {
            "step": 1,
            "action_type": "create_sketch",
            "action_params": {"face_ref": "face:1:F_top"},
        }
    ]
    run_state.evidence.update(
        tool_name="query_sketch",
        payload={
            "sketch_state": {
                "plane": "face:1:F_top",
                "profile_refs": [],
                "path_refs": [],
            }
        },
        round_no=2,
    )

    payload = _preflight_gate_apply_cad_action(
        action_type="cut_extrude",
        action_params={"distance": 6.0, "through_all": False},
        run_state=run_state,
    )

    assert payload is not None
    assert payload["failure_kind"] == "apply_cad_action_contract_failure"
    assert "cut_extrude" in payload["error_message"]
    assert "active profile sketch" in payload["error_message"]


def test_apply_cad_action_preflight_allows_cut_extrude_with_active_profile_sketch() -> None:
    run_state = RunState(
        session_id="session-cut-extrude-with-profile",
        requirements={"description": "Cut a face-local rectangular pocket after completing a sketch."},
    )
    run_state.action_history = [
        {
            "step": 1,
            "action_type": "add_rectangle",
            "action_params": {"width": 18.0, "height": 10.0},
        }
    ]
    run_state.evidence.update(
        tool_name="query_sketch",
        payload={
            "sketch_state": {
                "plane": "face:1:F_top",
                "profile_refs": ["profile:1:P_rect"],
                "path_refs": [],
            }
        },
        round_no=2,
    )

    payload = _preflight_gate_apply_cad_action(
        action_type="cut_extrude",
        action_params={"distance": 6.0, "through_all": False},
        run_state=run_state,
    )

    assert payload is None


def test_preflight_lint_rejects_buildpart_topology_access_inside_buildsketch() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.XY):\n"
            "        Rectangle(40, 20)\n"
            "        fillet(*part.vertices(), radius=4)\n"
            "    extrude(amount=10)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a rounded rectangular enclosure body.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_context.buildpart_topology_access_inside_buildsketch" in rule_ids
    assert (
        payload["repair_recipe"]["recipe_id"]
        == "build123d_buildsketch_builder_boundary_contract"
    )


def test_preflight_lint_rejects_sketch_primitive_inside_buildpart_without_buildsketch() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(78, 56, 12)\n"
            "    with Locations((0, 0, 6)):\n"
            "        Ellipse(12, 18)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a rounded enclosure with an organic top cavity.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_context.sketch_primitive_requires_buildsketch" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_sketch_primitive_builder_contract"


def test_preflight_lint_rejects_transform_helper_as_context_manager() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(40, 20, 8)\n"
            "    with Rot(90, 0, 0):\n"
            "        Cylinder(2, 50, mode=Mode.SUBTRACT)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a bracket and add a rotated cylindrical cut.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_context.transform_context_manager" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_transform_placement_contract"


def test_preflight_lint_rejects_detached_subtractive_builder_without_host() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as magnet_slots:\n"
            "    with Locations((20, 12, 4), (-20, 12, 4)):\n"
            "        Cylinder(radius=3.0, height=2.5, mode=Mode.SUBTRACT)\n"
            "result = magnet_slots.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a two-part clamshell enclosure with four corner magnet recesses and a "
            "front thumb notch."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.detached_subtractive_builder_without_host" in rule_ids
    assert (
        payload["repair_recipe"]["recipe_id"]
        == "detached_subtractive_builder_without_host_contract"
    )


def test_preflight_lint_marks_clamshell_hinge_requirements_as_half_shell_family() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as magnet_slots:\n"
            "    with Locations((20, 12, 4), (-20, 12, 4)):\n"
            "        Cylinder(radius=3.0, height=2.5, mode=Mode.SUBTRACT)\n"
            "result = magnet_slots.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a two-part clamshell enclosure with a top lid, bottom base, pin hinge, "
            "corner magnet recesses, and a thumb notch."
        ),
        run_state=None,
    )

    assert payload is not None
    assert "half_shell" in payload["candidate_family_ids"]


def test_preflight_lint_allows_same_builder_subtract_when_host_exists_first() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as base:\n"
            "    Box(40, 30, 12)\n"
            "    with Locations((12, 8, 6), (-12, 8, 6)):\n"
            "        Cylinder(radius=3.0, height=2.5, mode=Mode.SUBTRACT)\n"
            "result = base.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a base block with two magnet recesses near the top face.",
        run_state=None,
    )

    assert payload is None or (
        "invalid_build123d_contract.detached_subtractive_builder_without_host"
        not in {item["rule_id"] for item in payload["lint_hits"]}
    )


def test_preflight_lint_rejects_compound_positional_children_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as base:\n"
            "    Box(40, 30, 10)\n"
            "with BuildPart() as lid:\n"
            "    Box(40, 30, 10)\n"
            "result = Compound(base.part, lid.part)\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a two-part clamshell enclosure with separate lid and base parts in one assembled pose."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.compound_positional_children_contract" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_compound_children_contract"


def test_preflight_lint_rejects_case_drift_local_symbol_before_container_runtime() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "mag_z = 4.0\n"
            "with BuildPart() as part:\n"
            "    Box(40, 20, 8)\n"
            "    with Locations((0, 0, mag_Z)):\n"
            "        Cylinder(2, 10, mode=Mode.SUBTRACT)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a box with one cylindrical recess.",
        run_state=None,
    )

    assert payload is not None
    hits = payload["lint_hits"]
    rule_ids = {item["rule_id"] for item in hits}
    assert "invalid_build123d_identifier.case_drift_local_symbol" in rule_ids
    repair_hints = "\n".join(str(item.get("repair_hint") or "") for item in hits)
    assert "`mag_Z`" in repair_hints
    assert "`mag_z`" in repair_hints


def test_preflight_lint_does_not_mistake_build123d_pos_for_local_case_drift() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "pos = (0, 0, 0)\n"
            "with BuildPart() as part:\n"
            "    Box(20, 20, 8)\n"
            "result = part.part.moved(Pos(0, 0, 4))\n"
        ),
        session_id="test-session",
        requirement_text="Move the box upward after it is built.",
        run_state=None,
    )

    if payload is None:
        return
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_identifier.case_drift_local_symbol" not in rule_ids


def test_preflight_lint_rejects_ellipse_major_minor_radius_aliases() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(78, 56, 12)\n"
            "    with BuildSketch(Plane.XY.offset(9)):\n"
            "        Ellipse(major_radius=14, minor_radius=10)\n"
            "    extrude(amount=2, mode=Mode.SUBTRACT)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a rounded enclosure and cut one shallow organic top cavity.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.ellipse_major_radius_alias" in rule_ids
    assert "invalid_build123d_keyword.ellipse_minor_radius_alias" in rule_ids


def test_preflight_lint_rejects_filter_by_direction_and_surfaces_axis_filter_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(60, 40, 30)\n"
            "    bottom_edges = part.edges().filter_by_position(Axis.Z, -15.1, -14.9).filter_by_direction(Axis.Y)\n"
            "    fillet(bottom_edges, radius=1)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="fillet the bottom outer edges parallel to the Y axis",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.shapelist_filter_by_direction" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_shapelist_axis_filter_contract"


def test_preflight_lint_rejects_edge_is_parallel_axis_and_reuses_axis_filter_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(60, 40, 30)\n"
            "    bottom_edges = part.edges().filter_by_position(Axis.Z, -15.1, -14.9)\n"
            "    y_parallel_edges = [edge for edge in bottom_edges if edge.is_parallel(Axis.Y)]\n"
            "    fillet(y_parallel_edges, radius=1)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="fillet the bottom outer edges parallel to the Y axis",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.edge_is_parallel_axis" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_shapelist_axis_filter_contract"


def test_preflight_lint_rejects_filter_by_position_keyword_band_aliases() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(60, 40, 30)\n"
            "    top_edges = part.edges().filter_by(Axis.Z).filter_by_position(Axis.Z, ZMin=10, ZMax=15)\n"
            "    fillet(top_edges, radius=1)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="fillet the top edges parallel to the Z direction band",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.filter_by_position_keyword_band" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_shapelist_axis_filter_contract"


def test_preflight_lint_rejects_filter_by_position_plane_axis_argument() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(60, 40, 30)\n"
            "    top_edges = part.edges().filter_by_position(Plane.XY, 14.9, 15.1)\n"
            "    fillet(top_edges, radius=1)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="fillet the top opening edges after selecting the top Z band",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.filter_by_position_plane_axis" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_shapelist_axis_filter_contract"


def test_preflight_lint_rejects_member_fillet_radius_keyword_conflict() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(60, 40, 30)\n"
            "solid = part.part\n"
            "first_edge = solid.edges()[0]\n"
            "solid = solid.fillet(first_edge, radius=2.4)\n"
            "result = solid\n"
        ),
        session_id="test-session",
        requirement_text="Create a rounded block with softened outer edges.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.member_fillet_radius_keyword_conflict" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_fillet_member_contract"


def test_preflight_lint_rejects_global_fillet_helper_with_host_shape_argument() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "base = Box(60, 40, 30)\n"
            "top_edges = base.edges()\n"
            "rounded = fillet(base, top_edges, 1.5)\n"
            "result = rounded\n"
        ),
        session_id="test-session",
        requirement_text="Create a rounded block with softened top edges.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.global_fillet_helper_argument_contract" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_fillet_member_contract"


def test_preflight_lint_rejects_active_builder_cutter_primitive_for_explicit_slot_boolean() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.XY):\n"
            "        Rectangle(100.0, 50.0)\n"
            "    extrude(amount=20.0)\n"
            "    cutter = Cylinder(radius=12.0, height=110.0)\n"
            "    cutter = cutter.rotate(Axis.Y, 90).translate((0, 0, 8.0))\n"
            "    result = part.part - cutter\n"
            "result = result\n"
        ),
        session_id="test-session",
        requirement_text=_CYLINDRICAL_SLOT_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.active_builder_cutter_primitive_boolean" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "explicit_cylindrical_slot_boolean_safe_recipe"


def test_preflight_lint_rejects_makeface_helper_case_and_surfaces_make_face_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.XY):\n"
            "        with BuildLine():\n"
            "            Line((0, 0), (10, 0))\n"
            "            Line((10, 0), (0, 10))\n"
            "            Line((0, 10), (0, 0))\n"
            "        MakeFace()\n"
            "    extrude(amount=5)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="create a triangular prism",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.makeface_helper_case" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_make_face_helper_contract"


def test_preflight_lint_rejects_curve_helpers_directly_inside_buildsketch() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.XZ):\n"
            "        Polyline([(10, 0), (25, 0), (25, 15)], close=True)\n"
            "    revolve(axis=Axis.Z)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Select the front plane, draw a stepped closed profile, and revolve it 360 degrees."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_context.curve_requires_buildline" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_revolve_profile_contract"


def test_preflight_lint_rejects_buildsketch_wire_profile_without_make_face() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.XZ):\n"
            "        with BuildLine() as profile:\n"
            "            Line((0, 0), (80, 0))\n"
            "            Line((80, 0), (80, 5))\n"
            "            Line((80, 5), (0, 30))\n"
            "            Line((0, 30), (0, 0))\n"
            "    extrude(amount=40)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="Draw a trapezoidal profile on the front plane and extrude it into a wedge.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.buildsketch_wire_requires_make_face" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_make_face_helper_contract"


def test_preflight_lint_rejects_revolve_angle_keyword_alias() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.XZ):\n"
            "        with BuildLine():\n"
            "            Line((10, 0), (25, 0))\n"
            "            Line((25, 0), (10, 20))\n"
            "            Line((10, 20), (10, 0))\n"
            "        make_face()\n"
            "    revolve(axis=Axis.Z, angle=360)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Select the front plane, draw a closed profile, and revolve it 360 degrees."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.revolve_angle_alias" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_revolve_profile_contract"


def test_preflight_lint_rejects_circle_arc_size_and_surfaces_arc_profile_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.XZ):\n"
            "        Circle(20, arc_size=180)\n"
            "    extrude(amount=30)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a half-cylindrical shell with a flat split line and extrude it along the length."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.circle_arc_size" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_arc_profile_contract"


def test_preflight_lint_rejects_circle_plus_make_face_trim_pattern_and_surfaces_arc_profile_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.YZ):\n"
            "        Circle(radius=3)\n"
            "        with BuildLine():\n"
            "            Line((-3, 0), (3, 0))\n"
            "        make_face()\n"
            "    extrude(amount=20, both=True)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a rounded thumb notch with a semicircular profile on the front face."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.circle_make_face_trim_profile" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_arc_profile_contract"


def test_preflight_lint_rejects_center_arc_arc_angle_alias_and_surfaces_arc_profile_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildLine() as path:\n"
            "        Line((0, 0), (50, 0))\n"
            "        CenterArc((50, 30), radius=30, start_angle=-90, arc_angle=90)\n"
            "        Line((80, 30), (80, 80))\n"
            "result = None\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Use the Sweep feature to construct a hollow bent pipe from an L-shaped path with a tangent arc."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.center_arc_arc_angle_alias" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_path_sweep_contract"


def test_preflight_lint_rejects_center_arc_end_angle_alias_and_surfaces_arc_profile_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildLine() as profile:\n"
            "        CenterArc((0, 0), 20, start_angle=-90, end_angle=180)\n"
            "    result = None\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a half-cylindrical shell with a semicircular arc profile and a flat split edge."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.center_arc_end_angle_alias" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_arc_profile_contract"


def test_preflight_lint_rejects_symbolic_degree_constants_and_surfaces_arc_profile_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildLine() as profile:\n"
            "    CenterArc((0, 0), 20, -90 * DEGREES, 180 * DEGREE)\n"
            "result = None\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a half-cylindrical shell with a semicircular arc profile and a flat split edge."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.symbolic_degree_constant" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_arc_profile_contract"


def test_preflight_lint_prefers_path_sweep_recipe_when_arc_keyword_error_appears_inside_path_sweep() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildLine() as path_builder:\n"
            "    Line((0, 0, 0), (50, 0, 0))\n"
            "    CenterArc((50, 30, 0), 30, start_angle=-90, arc_angle=90)\n"
            "    Line((80, 30, 0), (80, 80, 0))\n"
            "path_wire = path_builder.wire\n"
            "with BuildSketch(Plane.YZ) as profile:\n"
            "    Circle(10)\n"
            "    Circle(8, mode=Mode.SUBTRACT)\n"
            "with BuildPart() as pipe:\n"
            "    sweep(profile.sketch, path=path_wire)\n"
            "result = pipe.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Use the Sweep feature to construct a hollow bent pipe by sweeping an annular profile along an L-shaped path with a tangent arc."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.center_arc_arc_angle_alias" in rule_ids
    assert "invalid_build123d_contract.builder_method_reference_assignment" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_path_sweep_contract"


def test_preflight_lint_rejects_symbolic_degree_constants_and_prefers_path_sweep_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildLine() as path:\n"
            "    Line((0, 0, 0), (50, 0, 0))\n"
            "    CenterArc((50, 30, 0), 30, -90 * DEGREES, 90 * DEGREE)\n"
            "    Line((80, 30, 0), (80, 80, 0))\n"
            "path_wire = path.wire()\n"
            "with BuildSketch(Plane.YZ) as profile:\n"
            "    Circle(10)\n"
            "    Circle(8, mode=Mode.SUBTRACT)\n"
            "with BuildPart() as pipe:\n"
            "    sweep(profile.sketch, path=path_wire)\n"
            "result = pipe.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Use the Sweep feature to construct a hollow bent pipe by sweeping an annular profile along an L-shaped path with a 90-degree tangent arc of radius 30mm."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.symbolic_degree_constant" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_path_sweep_contract"


def test_preflight_lint_rejects_tangent_arc_helpers_for_explicit_radius_path_sweep() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildLine() as path:\n"
            "    l1 = Line((0, 0, 0), (50, 0, 0))\n"
            "    arc = TangentArc(l1 @ 1, (50, 80, 0), tangent=(0, 1, 0))\n"
            "    l2 = Line(arc @ 1, (0, 80, 0))\n"
            "path_wire = path.wire()\n"
            "with BuildSketch(Plane.YZ) as profile:\n"
            "    Circle(10)\n"
            "    Circle(8, mode=Mode.SUBTRACT)\n"
            "with BuildPart() as pipe:\n"
            "    sweep(profile.sketch, path=path_wire)\n"
            "result = pipe.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Use the Sweep feature to construct a hollow bent pipe. First draw a path with a "
            "50mm straight line, a 90-degree tangent arc with a radius of 30mm, and another "
            "50mm straight line, then sweep an annular profile along that path."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.explicit_radius_arc_prefers_center_arc" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_path_sweep_contract"


def test_preflight_lint_rejects_sweep_path_wire_method_reference_and_surfaces_path_sweep_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildLine() as path:\n"
            "    Line((0, 0), (50, 0))\n"
            "with BuildSketch(Plane.YZ) as profile:\n"
            "    Circle(10)\n"
            "    Circle(8, mode=Mode.SUBTRACT)\n"
            "with BuildPart() as pipe:\n"
            "    sweep(profile.sketch, path=path.wire)\n"
            "result = pipe.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Use the Sweep feature to construct a hollow bent pipe by sweeping an annular profile along a path."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.sweep_path_wire_method_reference" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_path_sweep_contract"


def test_preflight_lint_rejects_sweep_path_line_alias_and_surfaces_path_sweep_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildLine() as path:\n"
            "    Line((0, 0), (50, 0))\n"
            "    Line((50, 0), (80, 30))\n"
            "with BuildSketch(Plane.YZ) as profile:\n"
            "    Circle(10)\n"
            "with BuildPart() as pipe:\n"
            "    sweep(profile.sketch, path=path.line)\n"
            "result = pipe.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Use the Sweep feature to construct a hollow bent pipe by sweeping a profile along an L-shaped path."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.sweep_path_line_alias" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_path_sweep_contract"


def test_preflight_lint_rejects_center_arc_without_start_angle_and_keeps_path_sweep_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildLine() as path:\n"
            "    Line((0, 0), (50, 0))\n"
            "    CenterArc((50, 30), 30, arc_size=90)\n"
            "result = None\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Use the Sweep feature to construct a bent pipe with a tangent arc path."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.center_arc_missing_start_angle" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_path_sweep_contract"


def test_preflight_lint_rejects_sweep_section_keyword_alias_and_surfaces_path_sweep_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildLine() as path:\n"
            "    Line((0, 0), (50, 0))\n"
            "with BuildSketch(Plane.YZ) as profile:\n"
            "    Circle(10)\n"
            "    Circle(8, mode=Mode.SUBTRACT)\n"
            "with BuildPart() as pipe:\n"
            "    sweep(section=profile.sketch, path=path.line)\n"
            "result = pipe.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Use the Sweep feature to construct a hollow bent pipe by sweeping an annular profile along a path."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.sweep_section_alias" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_path_sweep_contract"


def test_preflight_lint_rejects_sweep_profile_face_method_reference_and_surfaces_path_sweep_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildLine() as path:\n"
            "    Line((0, 0), (50, 0))\n"
            "with BuildSketch(Plane.YZ) as profile:\n"
            "    Circle(10)\n"
            "    Circle(8, mode=Mode.SUBTRACT)\n"
            "with BuildPart() as pipe:\n"
            "    sweep(profile.face, path=path.wire())\n"
            "result = pipe.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Use the Sweep feature to construct a hollow bent pipe by sweeping an annular profile along a path."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.sweep_profile_face_method_reference" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_path_sweep_contract"


def test_preflight_lint_rejects_solid_sweep_invalid_keywords_and_surfaces_path_sweep_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildLine() as path:\n"
            "    Line((0, 0, 0), (50, 0, 0))\n"
            "    CenterArc((50, 30, 0), 30, start_angle=-90, arc_size=90)\n"
            "    Line((80, 30, 0), (80, 80, 0))\n"
            "profile_plane = Plane(origin=(80, 80, 0), z_dir=(0, 1, 0))\n"
            "with BuildSketch(profile_plane) as profile:\n"
            "    Circle(10)\n"
            "outer_pipe = Solid.sweep(profile.face(), path_wire=path.wire(), profile_plane=profile_plane)\n"
            "result = outer_pipe\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Use the Sweep feature to construct a hollow bent pipe by sweeping a profile along an L-shaped path."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.solid_sweep_unsupported_keyword" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_path_sweep_contract"


def test_preflight_lint_rejects_annular_profile_face_splitting_and_surfaces_path_sweep_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildLine() as path:\n"
            "    Line((0, 0, 0), (50, 0, 0))\n"
            "    CenterArc((50, 30, 0), 30, start_angle=-90, arc_size=90)\n"
            "    Line((80, 30, 0), (80, 80, 0))\n"
            "profile_plane = Plane(origin=(80, 80, 0), z_dir=(0, 1, 0))\n"
            "with BuildSketch(profile_plane) as profile:\n"
            "    Circle(10)\n"
            "    Circle(8, mode=Mode.SUBTRACT)\n"
            "outer_face = profile.faces().sort_by(Axis.Z, reverse=True)[0]\n"
            "inner_face = profile.faces().sort_by(Axis.Z, reverse=True)[1]\n"
            "result = None\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Use the Sweep feature to construct a hollow bent pipe by sweeping an annular profile along a path."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.annular_profile_face_splitting" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_path_sweep_contract"


def test_preflight_lint_rejects_annular_profile_face_extraction_before_sweep_and_surfaces_path_sweep_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildLine() as path:\n"
            "    Line((0, 0, 0), (50, 0, 0))\n"
            "    CenterArc((50, 30, 0), 30, start_angle=-90, arc_size=90)\n"
            "    Line((80, 30, 0), (80, 80, 0))\n"
            "profile_plane = Plane(origin=(80, 80, 0), z_dir=(0, 1, 0))\n"
            "with BuildSketch(profile_plane) as profile_builder:\n"
            "    Circle(10)\n"
            "    Circle(8, mode=Mode.SUBTRACT)\n"
            "profile_face = profile_builder.face()\n"
            "with BuildPart() as pipe:\n"
            "    sweep(profile_face, path=path.wire())\n"
            "result = pipe.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Use the Sweep feature to construct a hollow bent pipe by sweeping an annular profile along a path."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.annular_profile_face_extraction" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_path_sweep_contract"


def test_preflight_lint_rejects_vector_component_indexing_in_path_sweep_and_surfaces_path_sweep_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildLine() as path:\n"
            "    l1 = Line((0, 0), (50, 0))\n"
            "    arc = CenterArc((50, 30), 30, start_angle=-90, arc_size=90)\n"
            "    arc_end = arc @ 1\n"
            "    l2 = Line(arc_end, (arc_end[0], arc_end[1] + 50))\n"
            "profile_plane = Plane.YZ.offset(0)\n"
            "with BuildSketch(profile_plane) as profile:\n"
            "    Circle(10)\n"
            "    Circle(8, mode=Mode.SUBTRACT)\n"
            "with BuildPart() as pipe:\n"
            "    sweep(profile.sketch, path=path.wire())\n"
            "result = pipe.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Use the Sweep feature to construct a hollow bent pipe by sweeping an annular profile along a path."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.vector_component_indexing" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_path_sweep_contract"


def test_preflight_lint_rejects_lowercase_vector_component_attribute_access() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as base_part:\n"
            "    Box(78, 56, 16)\n"
            "base_outer_edges = [e for e in base_part.edges() if abs(e.center().z - 8) < 0.1]\n"
            "fillet(base_outer_edges, radius=2)\n"
            "result = base_part.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a rounded enclosure body and fillet the top outer edges.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.vector_lowercase_component_attribute" in rule_ids
    assert (
        payload["repair_recipe"]["recipe_id"]
        == "build123d_vector_component_attribute_contract"
    )


def test_preflight_lint_rejects_topology_geometry_attribute_access() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as lid:\n"
            "    Box(78, 56, 14)\n"
            "edges_to_fillet = [e for e in lid.part.edges() if e.geometry is not None]\n"
            "fillet(edges_to_fillet, radius=3.0)\n"
            "result = lid.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a rounded enclosure lid and fillet the top outer edges.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.topology_geometry_attribute" in rule_ids
    assert (
        payload["repair_recipe"]["recipe_id"]
        == "build123d_topology_geometry_attribute_contract"
    )


def test_preflight_lint_rejects_broad_shell_axis_fillet_on_fresh_enclosure_host() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as base_part:\n"
            "    with BuildSketch():\n"
            "        RectangleRounded(78, 56, 8)\n"
            "    extrude(amount=20)\n"
            "    with BuildSketch(Plane.XY.offset(2.4)):\n"
            "        RectangleRounded(73.2, 51.2, 5.6)\n"
            "    extrude(amount=17.6, mode=Mode.SUBTRACT)\n"
            "    fillet(base_part.edges().filter_by(Axis.Z), 2.0)\n"
            "result = base_part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a two-part rounded clamshell storage enclosure with lid and base, "
            "corner magnet recesses, and a front thumb notch."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert (
        "invalid_build123d_contract.broad_shell_axis_fillet_on_fresh_host"
        in rule_ids
    )
    assert payload["repair_recipe"]["recipe_id"] == "shell_edge_fillet_postpone_contract"


def test_preflight_lint_rejects_broad_shell_axis_fillet_when_selector_is_stored_first() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as shell:\n"
            "    with BuildSketch():\n"
            "        RectangleRounded(78, 56, 8)\n"
            "    extrude(amount=14)\n"
            "    with BuildSketch(Plane.XY.offset(2.4)):\n"
            "        RectangleRounded(73.2, 51.2, 5.6)\n"
            "    extrude(amount=11.6, mode=Mode.SUBTRACT)\n"
            "    edges_to_fillet = shell.edges().filter_by(Axis.Z)\n"
            "    fillet(edges_to_fillet, 3.0)\n"
            "result = shell.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a rounded enclosure shell for a clamshell lid with a thumb notch and "
            "magnet recesses."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert (
        "invalid_build123d_contract.broad_shell_axis_fillet_on_fresh_host"
        in rule_ids
    )
    assert payload["repair_recipe"]["recipe_id"] == "shell_edge_fillet_postpone_contract"


def test_preflight_lint_rejects_broad_fillet_when_requirement_marks_it_as_local_finish_tail() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as bracket:\n"
            "    Box(66, 42, 16)\n"
            "    with BuildSketch(Plane.XY.offset(8)):\n"
            "        Rectangle(60, 36)\n"
            "    extrude(amount=-2.5, mode=Mode.SUBTRACT)\n"
            "    top_edges = bracket.part.edges().filter_by(Axis.Z).filter_by(lambda e: e.center().Z > 7.9)\n"
            "    fillet(top_edges, radius=1.0)\n"
            "result = bracket.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a rectangular service bracket sized 66mm x 42mm x 16mm with a shallow top "
            "pocket and two mounting holes on the bottom face. Add a centered rounded-rectangle "
            "recess on the front face and leave the small edge fillet for a later topology-aware "
            "local finish."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert (
        "invalid_build123d_contract.broad_local_finish_tail_fillet_on_first_write"
        in rule_ids
    )
    assert payload["repair_recipe"]["recipe_id"] == "local_finish_fillet_postpone_contract"


def test_preflight_lint_rejects_plane_normal_keyword_alias_and_surfaces_path_sweep_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildLine() as path:\n"
            "    l1 = Line((0, 0, 0), (50, 0, 0))\n"
            "profile_plane = Plane(path.line @ 0, normal=l1 % 0)\n"
            "with BuildSketch(profile_plane) as profile:\n"
            "    Circle(10)\n"
            "    Circle(8, mode=Mode.SUBTRACT)\n"
            "with BuildPart() as pipe:\n"
            "    sweep(profile.sketch, path=path.wire())\n"
            "result = pipe.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Use the Sweep feature to construct a hollow bent pipe by sweeping an annular profile along a path."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.plane_normal_alias" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_path_sweep_contract"


def test_preflight_lint_rejects_builder_method_reference_assignment_and_surfaces_generic_builder_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildLine() as path_builder:\n"
            "    Line((0, 0), (50, 0))\n"
            "path_wire = path_builder.wire\n"
            "with BuildSketch(Plane.YZ) as profile_builder:\n"
            "    Circle(10)\n"
            "profile_face = profile_builder.face\n"
            "result = None\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Build a wire and a circular profile, then continue modeling from those builders."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.builder_method_reference_assignment" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_builder_method_reference_contract"


def test_preflight_lint_rejects_semicircle_helper_and_surfaces_arc_profile_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.XZ):\n"
            "        Semicircle(radius=20)\n"
            "    extrude(amount=30)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a half-cylindrical shell with a flat split line and extrude it along the length."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.semicircle_helper_name" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_arc_profile_contract"


def test_preflight_lint_rejects_ring_helper_and_surfaces_annular_band_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.XY):\n"
            "        Circle(25)\n"
            "    extrude(amount=60)\n"
            "    with BuildSketch(Plane.XY.offset(29)):\n"
            "        Ring(25, 20)\n"
            "    extrude(amount=2, mode=Mode.SUBTRACT)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_ANNULAR_GROOVE_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.ring_helper_name" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "annular_groove_same_builder_band_subtract_recipe"


def test_preflight_lint_rejects_bare_shell_helper_and_surfaces_shell_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(80, 60, 40)\n"
            "shell(part.faces(), 3)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="model a shelled enclosure body",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.bare_shell_helper" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_shell_offset_contract"


def test_preflight_lint_rejects_offset_opening_keyword_and_surfaces_shell_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(80, 60, 40)\n"
            "    offset(amount=-2.4, opening=part.faces().sort_by(Axis.Z)[-1])\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="model a shelled enclosure body",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.offset_opening_singular" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_shell_offset_contract"


def test_preflight_lint_ignores_shell_like_comment_text() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(80, 60, 40)\n"
            "    # Create a hollow shell (open top) by subtracting an inner box later\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="model a shelled enclosure body",
        run_state=None,
    )

    assert payload is None


def test_preflight_lint_shell_recess_requirement_does_not_misclassify_as_spherical_recess() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(80, 60, 30)\n"
            "shell(part.faces(), 3)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a shelled block with a shallow top-face recess and a reference hole pattern.",
        run_state=None,
    )

    assert payload is not None
    assert "spherical_recess" not in payload["candidate_family_ids"]
    assert "pattern_distribution" in payload["candidate_family_ids"]


def test_preflight_lint_rejects_pos_lowercase_axis_keyword() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "solid = Box(10, 10, 10)\n"
            "result = solid.located(Pos(z=30))\n"
        ),
        session_id="test-session",
        requirement_text="move the block upward",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.pos_lowercase_axis_keyword" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_pos_keyword_contract"


def test_preflight_lint_rejects_plane_rotated_origin_guess() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(40, 30, 20)\n"
            "    with BuildSketch(Plane.XZ.offset(0).rotated((90, 0, 0), (0, 0, 0))):\n"
            "        Circle(2)\n"
            "    extrude(amount=50, both=True, mode=Mode.SUBTRACT)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Drill two through-holes in the Y direction at explicit x and z coordinates."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.plane_rotated_origin_guess" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_plane_rotation_contract"


def test_preflight_lint_rejects_plane_rotated_origin_guess_with_coordinate_expressions() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "width = 40\n"
            "with BuildPart() as part:\n"
            "    Box(40, width, 20)\n"
            "    with Locations(Plane.XY.offset(0).rotated((90, 0, 0), (0, width/2, 0))):\n"
            "        Cylinder(radius=3, height=6, mode=Mode.SUBTRACT)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Cut a front thumb notch using a translated placement, not a guessed plane-rotation origin."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.plane_rotated_origin_guess" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_plane_rotation_contract"


def test_preflight_lint_rejects_plane_rotate_shape_method_guess() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "width = 66.0\n"
            "with BuildPart() as part:\n"
            "    Box(66, 42, 16)\n"
            "    with BuildSketch(Plane.YZ.offset(width/2).rotate((0, 0, 0), (1, 0, 0), 90)):\n"
            "        RectangleRounded(12, 6, radius=1.5)\n"
            "    extrude(amount=-2, mode=Mode.SUBTRACT)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a front-face rounded recess on a service bracket.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.plane_rotate_shape_method_guess" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_plane_rotation_contract"


def test_preflight_lint_rejects_plane_located_shape_method_guess() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "wall = 2.4\n"
            "with BuildPart() as part:\n"
            "    Box(40, 30, 20)\n"
            "    with BuildSketch(Plane.XY.located((0, 0, wall))):\n"
            "        Rectangle(20, 10)\n"
            "    extrude(amount=5, mode=Mode.SUBTRACT)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a hollowed host with an inner sketch on an offset XY workplane.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.plane_located_shape_method_guess" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_plane_translation_contract"


def test_preflight_lint_rejects_plane_moved_shape_method_guess() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "hinge_y = -20\n"
            "hinge_z = 8\n"
            "with BuildPart() as part:\n"
            "    Box(40, 30, 20)\n"
            "with BuildPart() as hinge:\n"
            "    with BuildSketch(Plane.YZ.moved(Location((0, hinge_y, hinge_z)))):\n"
            "        Circle(3)\n"
            "    extrude(amount=10, both=True)\n"
            "result = part.part + hinge.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a rounded shell with one detached hinge barrel at the back.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.plane_moved_shape_method_guess" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_plane_translation_contract"


def test_preflight_lint_rejects_face_plane_shift_origin_global_coordinate_guess() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(40, 30, 20)\n"
            "    front_face = part.faces().sort_by(Axis.Y)[-1]\n"
            "    with BuildSketch(Plane(front_face).shift_origin((0, 0, 7))):\n"
            "        Rectangle(12, 4)\n"
            "    extrude(amount=4, mode=Mode.SUBTRACT)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a front thumb notch on the host face with a local sketch anchored to the face."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.face_plane_shift_origin_global_coordinate_guess" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_face_plane_shift_origin_contract"


def test_preflight_lint_rejects_directional_drill_plane_offset_coordinate_mixup() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "HOLE_Z = 15\n"
            "with BuildPart() as part:\n"
            "    Box(40, 20, 20)\n"
            "    with BuildSketch(Plane.XZ.offset(HOLE_Z)):\n"
            "        with Locations((-10, 0), (10, 0)):\n"
            "            Circle(2)\n"
            "    extrude(amount=30, both=True, mode=Mode.SUBTRACT)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Drill two through-holes in the Y direction at x = -10 mm and x = 10 mm, both at z = 15 mm."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.directional_drill_plane_offset_coordinate_mixup" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "directional_drill_workplane_coordinate_contract"


def test_preflight_lint_allows_zero_offset_xz_workplane_for_y_direction_drill() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(40, 20, 20)\n"
            "    with BuildSketch(Plane.XZ.offset(0)):\n"
            "        with Locations((-10, 15), (10, 15)):\n"
            "            Circle(2)\n"
            "    extrude(amount=30, both=True, mode=Mode.SUBTRACT)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Drill two through-holes in the Y direction at x = -10 mm and x = 10 mm, both at z = 15 mm."
        ),
        run_state=None,
    )

    assert payload is None


def test_preflight_lint_allows_valid_build123d_countersink_helper() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(20, 20, 8)\n"
            "    with Locations((0, 0, 4)):\n"
            "        CounterSinkHole(radius=3, counter_sink_radius=5, depth=8)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is None


def test_preflight_lint_rejects_countersinkhole_inside_buildsketch_context() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(20, 20, 8)\n"
            "    with BuildSketch(Plane.XY.offset(4)):\n"
            "        CounterSinkHole(radius=3, counter_sink_radius=5, depth=8)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_context.countersinkhole_requires_buildpart" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "explicit_anchor_hole_countersink_array_safe_recipe"


def test_preflight_lint_rejects_invalid_countersink_helper_name_and_keyword_alias() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(20, 20, 8)\n"
            "    with Locations((0, 0, 4)):\n"
            "        CountersinkHole(radius=3, countersink_radius=5, depth=8)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.countersink_helper_name" in rule_ids
    assert "invalid_build123d_keyword.countersink_radius_alias" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "explicit_anchor_hole_countersink_array_safe_recipe"
    helper_hint = next(
        item["repair_hint"]
        for item in payload["lint_hits"]
        if item["rule_id"] == "invalid_build123d_api.countersink_helper_name"
    )
    assert "the exact name is `CounterSinkHole(...)`" in helper_hint
    assert "prefer one `CounterSinkHole(...)` pass first" in helper_hint
    assert "Only fall back to an explicit same-builder cone/cylinder or revolved countersink recipe" in helper_hint


def test_preflight_lint_rejects_lowercase_countersink_hole_helper_guess() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(20, 20, 8)\n"
            "    with Locations((0, 0, 4)):\n"
            "        countersink_hole(diameter=6, countersink_diameter=12)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.countersink_helper_name" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "explicit_anchor_hole_countersink_array_safe_recipe"


def test_preflight_lint_rejects_bare_countersink_helper_guess() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(20, 20, 8)\n"
            "    with Locations((0, 0, 4)):\n"
            "        Hole(radius=3, depth=8)\n"
            "        Countersink(radius=6, angle=90, depth=3)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.countersink_helper_name" in rule_ids
    helper_hint = next(
        item["repair_hint"]
        for item in payload["lint_hits"]
        if item["rule_id"] == "invalid_build123d_api.countersink_helper_name"
    )
    assert "CounterSinkHole(...)" in helper_hint
    assert "Countersink(...)" in helper_hint


def test_preflight_lint_does_not_flag_plain_countersink_angle_variable_assignment() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "countersink_angle = 90.0\n"
            "with BuildPart() as part:\n"
            "    Box(20, 20, 8)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is None


def test_preflight_lint_rejects_angle_keyword_alias_inside_countersink_helper() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(20, 20, 8)\n"
            "    with Locations((0, 0, 4)):\n"
            "        CounterSinkHole(radius=3, counter_sink_radius=5, angle=90)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.countersink_angle_alias" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "explicit_anchor_hole_countersink_array_safe_recipe"


def test_preflight_lint_rejects_depth_keyword_alias_inside_countersink_helper() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(20, 20, 8)\n"
            "    with Locations((0, 0, 4)):\n"
            "        CounterSinkHole(radius=3, counter_sink_radius=5, counter_sink_angle=90, counter_sink_depth=2, depth=8)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.countersink_depth_alias" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "explicit_anchor_hole_countersink_array_safe_recipe"


def test_preflight_lint_rejects_diameter_style_countersink_helper_aliases() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(100, 60, 8)\n"
            "    with Locations((-25, -15, 4), (-25, 15, 4), (25, -15, 4), (25, 15, 4)):\n"
            "        CounterSinkHole(head_diameter=12, thru_diameter=6, cone_angle=90, depth=8)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.countersink_head_diameter_alias" in rule_ids
    assert "invalid_build123d_keyword.countersink_through_diameter_alias" in rule_ids
    assert "invalid_build123d_keyword.countersink_angle_alias" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "explicit_anchor_hole_countersink_array_safe_recipe"


def test_preflight_lint_rejects_radius_style_countersink_helper_aliases() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(100, 60, 8)\n"
            "    with Locations((25, 15, 4)):\n"
            "        CounterSinkHole(radius=3, head_radius=6, angle=90)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.countersink_head_diameter_alias" in rule_ids
    assert "invalid_build123d_keyword.countersink_angle_alias" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "explicit_anchor_hole_countersink_array_safe_recipe"


def test_preflight_lint_prefers_explicit_anchor_hole_recipe_over_generic_make_face_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(100, 60, 8)\n"
            "    with BuildSketch(Plane.XY.offset(4)):\n"
            "        with BuildLine():\n"
            "            Line((0, 0), (3, 0))\n"
            "            Line((3, 0), (3, 3))\n"
            "            Line((3, 3), (0, 3))\n"
            "            Line((0, 3), (0, 0))\n"
            "    with Locations((25, 15, 4)):\n"
            "        CounterSinkHole(radius=3, counter_sink_radius=6, angle=90, depth=8)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.countersink_angle_alias" in rule_ids
    assert "invalid_build123d_contract.buildsketch_wire_requires_make_face" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "explicit_anchor_hole_countersink_array_safe_recipe"


def test_preflight_lint_rejects_cone_radius_aliases_for_manual_countersink_cutters() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(20, 20, 8)\n"
            "    with Locations((0, 0, 4)):\n"
            "        Cone(lower_radius=6, upper_radius=3, height=3, mode=Mode.SUBTRACT)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.cone_radius_alias" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "explicit_anchor_hole_countersink_array_safe_recipe"


def test_preflight_lint_rejects_countersink_short_helper_name() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(20, 20, 8)\n"
            "    with Locations((0, 0, 4)):\n"
            "        CounterSink(radius=3, counter_sink_radius=5, depth=8)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.countersink_helper_name" in rule_ids


def test_preflight_lint_rejects_workplanes_helper_guess_for_face_local_holes() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(20, 20, 8)\n"
            "    with Workplanes(Plane.XY.offset(4)):\n"
            "        Hole(radius=3, depth=8)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.workplanes_helper_name" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "explicit_anchor_hole_countersink_array_safe_recipe"


def test_preflight_lint_rejects_lowercase_hole_helper_guess() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(20, 20, 8)\n"
            "    with Locations((0, 0, 4)):\n"
            "        hole(radius=3, depth=8)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.lowercase_hole_helper_name" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "explicit_anchor_hole_countersink_array_safe_recipe"


def test_preflight_lint_rejects_execute_build123d_cut_extrude_helper_guess() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(40, 30, 10)\n"
            "cut_extrude(amount=6)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_CYLINDRICAL_SLOT_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "legacy_api.cut_extrude_helper" in rule_ids


def test_preflight_lint_routes_cut_extrude_helper_guess_for_explicit_anchor_holes_to_hole_recipe() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(100, 60, 8)\n"
            "    with BuildSketch(Plane.XY.offset(4)):\n"
            "        Circle(3)\n"
            "cut_extrude(amount=8)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "legacy_api.cut_extrude_helper" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "explicit_anchor_hole_countersink_array_safe_recipe"


def test_preflight_lint_rejects_unsupported_cylinder_axis_keyword() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "result = Cylinder(radius=2, height=12, axis=Axis.X)\n"
        ),
        session_id="test-session",
        requirement_text="make a rotated cylinder",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.cylinder_axis" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_cylinder_axis_transform_contract"
    repair_steps = "\n".join(payload["repair_recipe"]["recipe_skeleton"]["steps"])
    assert "do not create a cylinder inside an active `BuildPart`" in repair_steps


def test_preflight_lint_rejects_unsupported_cylinder_taper_keyword() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "result = Cylinder(radius=3, height=6, taper=45)\n"
        ),
        session_id="test-session",
        requirement_text="make a countersunk cutter",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.cylinder_taper" in rule_ids


def test_preflight_lint_rejects_unsupported_cylinder_length_keyword() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "result = Cylinder(radius=3, length=25)\n"
        ),
        session_id="test-session",
        requirement_text="make a long cylindrical cutter",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.cylinder_length_alias" in rule_ids


def test_preflight_lint_rejects_unsupported_box_depth_keyword_and_surfaces_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(width=80, depth=60, height=40)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="model a compact enclosure body",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.box_depth_alias" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_box_keyword_contract"


def test_preflight_lint_rejects_unsupported_box_radius_keyword_and_surfaces_contract() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(80, 60, 20, radius=6)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="model a rounded enclosure shell",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.box_radius_alias" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_box_keyword_contract"


def test_preflight_lint_rejects_regular_polygon_sides_alias() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.XY):\n"
            "        RegularPolygon(radius=20.0, sides=3, major_radius=True)\n"
            "    extrude(amount=10.0)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Select the XY plane. Draw an equilateral triangle and extrude it by 10.0 millimeters."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.regular_polygon_sides_alias" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_regular_polygon_keyword_contract"


def test_preflight_lint_rejects_centered_box_for_plane_anchored_positive_extrude_requirement() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(length=100.0, width=50.0, height=20.0)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Draw a center rectangle 100.0x50.0 in the XY plane and extrude it by 20.0 "
            "to form a block."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert (
        "invalid_build123d_contract.centered_box_breaks_plane_anchored_positive_extrude"
        in rule_ids
    )
    assert payload["repair_recipe"]["recipe_id"] == "build123d_plane_anchored_extrude_contract"


def test_preflight_lint_rejects_full_span_face_plane_offset_on_centered_box() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "width = 66.0\n"
            "depth = 42.0\n"
            "height = 16.0\n"
            "with BuildPart() as part:\n"
            "    Box(width, depth, height)\n"
            "    with BuildSketch(Plane.XY.offset(height)):\n"
            "        Rectangle(width - 6.0, depth - 6.0)\n"
            "    extrude(amount=-3.0, mode=Mode.SUBTRACT)\n"
            "    with Locations(Plane.YZ.offset(width)):\n"
            "        with Locations((0, height / 2)):\n"
            "            CounterSinkHole(radius=2.5, counter_sink_radius=5.0, depth=height, counter_sink_angle=82)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a rectangular bracket body, cut a shallow top pocket from the top face, "
            "and add countersunk holes on the front face of the centered block."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.centered_box_face_plane_full_span_offset" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "explicit_anchor_hole_countersink_array_safe_recipe"


def test_preflight_lint_allows_half_span_face_plane_offset_on_centered_box() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "width = 66.0\n"
            "depth = 42.0\n"
            "height = 16.0\n"
            "with BuildPart() as part:\n"
            "    Box(width, depth, height)\n"
            "    with BuildSketch(Plane.XY.offset(height / 2)):\n"
            "        Rectangle(width - 6.0, depth - 6.0)\n"
            "    extrude(amount=-3.0, mode=Mode.SUBTRACT)\n"
            "    with Locations(Plane.YZ.offset(width / 2)):\n"
            "        pass\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a rectangular bracket body, cut a shallow top pocket from the top face, "
            "and add a front-face feature on the centered block."
        ),
        run_state=None,
    )

    rule_ids = {item["rule_id"] for item in (payload or {}).get("lint_hits", [])}
    assert "invalid_build123d_contract.centered_box_face_plane_full_span_offset" not in rule_ids


def test_preflight_lint_rejects_named_front_face_plane_family_mismatch() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "width = 66.0\n"
            "depth = 42.0\n"
            "height = 16.0\n"
            "with BuildPart() as part:\n"
            "    Box(width, depth, height)\n"
            "    with BuildSketch(Plane.XY.offset(height / 2)):\n"
            "        Rectangle(width - 6.0, depth - 6.0)\n"
            "    extrude(amount=-3.0, mode=Mode.SUBTRACT)\n"
            "    with BuildSketch(Plane.YZ.offset(-width / 2).shift_origin((0, 0, 0))):\n"
            "        RectangleRounded(12.0, 6.0, radius=1.0)\n"
            "    extrude(amount=2.0, mode=Mode.SUBTRACT)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a rectangular bracket body, cut a shallow top pocket from the top face, "
            "and add a centered rounded-rectangle recess on the front face."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.named_face_plane_family_mismatch" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_named_face_plane_family_contract"


def test_preflight_lint_allows_named_front_face_xz_plane_family() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "width = 66.0\n"
            "depth = 42.0\n"
            "height = 16.0\n"
            "with BuildPart() as part:\n"
            "    Box(width, depth, height)\n"
            "    with BuildSketch(Plane.XY.offset(height / 2)):\n"
            "        Rectangle(width - 6.0, depth - 6.0)\n"
            "    extrude(amount=-3.0, mode=Mode.SUBTRACT)\n"
            "    with BuildSketch(Plane.XZ.offset(-depth / 2)):\n"
            "        RectangleRounded(12.0, 6.0, radius=1.0)\n"
            "    extrude(amount=2.0, mode=Mode.SUBTRACT)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a rectangular bracket body, cut a shallow top pocket from the top face, "
            "and add a centered rounded-rectangle recess on the front face."
        ),
        run_state=None,
    )

    rule_ids = {item["rule_id"] for item in (payload or {}).get("lint_hits", [])}
    assert "invalid_build123d_contract.named_face_plane_family_mismatch" not in rule_ids


def test_preflight_lint_ignores_bare_xy_host_profiles_when_front_face_local_edit_uses_xz() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "width = 64.0\n"
            "depth = 48.0\n"
            "base_height = 12.0\n"
            "lid_height = 10.0\n"
            "with BuildPart() as base:\n"
            "    with BuildSketch(Plane.XY):\n"
            "        RectangleRounded(width, depth, radius=8.0)\n"
            "    extrude(amount=base_height)\n"
            "with BuildPart() as lid:\n"
            "    with BuildSketch(Plane.XY):\n"
            "        RectangleRounded(width, depth, radius=8.0)\n"
            "    extrude(amount=lid_height)\n"
            "with BuildPart() as label:\n"
            "    with BuildSketch(Plane.XZ.offset(-depth / 2)):\n"
            "        RectangleRounded(40.0, 12.0, radius=2.0)\n"
            "    extrude(amount=1.0)\n"
            "result = Compound(children=[base.part, lid.part, label.part])\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a two-part rounded pillbox enclosure and add a shallow rounded label window on the front face."
        ),
        run_state=None,
    )

    rule_ids = {item["rule_id"] for item in (payload or {}).get("lint_hits", [])}
    assert "invalid_build123d_contract.named_face_plane_family_mismatch" not in rule_ids


def test_named_face_plane_family_mismatch_only_hits_local_front_face_sketches_not_shell_profiles() -> None:
    code = (
        "from build123d import *\n"
        "width = 64.0\n"
        "depth = 48.0\n"
        "height_per_part = 12.0\n"
        "wall = 2.2\n"
        "with BuildPart() as base:\n"
        "    with BuildSketch(Plane.XY):\n"
        "        RectangleRounded(width, depth, 8.0)\n"
        "    extrude(amount=height_per_part)\n"
        "    with Locations((0, 0, wall)):\n"
        "        with BuildSketch(Plane.XY):\n"
        "            RectangleRounded(width - 2*wall, depth - 2*wall, 6.0)\n"
        "        extrude(amount=height_per_part - wall, mode=Mode.SUBTRACT)\n"
        "    with BuildSketch(Plane.YZ.offset(depth / 2)):\n"
        "        RectangleRounded(36.0, 14.0, 3.0)\n"
        "    extrude(amount=1.2, mode=Mode.SUBTRACT)\n"
        "with BuildPart() as lid:\n"
        "    with BuildSketch(Plane.XY):\n"
        "        RectangleRounded(width, depth, 8.0)\n"
        "    extrude(amount=height_per_part)\n"
        "    with Locations((0, 0, wall)):\n"
        "        with BuildSketch(Plane.XY):\n"
        "            RectangleRounded(width - 2*wall, depth - 2*wall, 6.0)\n"
        "        extrude(amount=height_per_part - wall, mode=Mode.SUBTRACT)\n"
        "    with BuildSketch(Plane.YZ.offset(depth / 2)):\n"
        "        SlotOverall(7.0, height_per_part * 0.6, rotation=90)\n"
        "    extrude(amount=3.0, mode=Mode.SUBTRACT)\n"
        "result = base.part + lid.part\n"
    )

    hits = _find_named_face_plane_family_mismatch_hits(
        ast.parse(code),
        requirement_lower=(
            "create a two-part rounded pillbox enclosure, add a shallow label window on the front face, "
            "and add a thumb notch on the front face."
        ),
    )

    assert len(hits) == 2
    assert {item["plane_name"] for item in hits} == {"YZ"}


def test_named_face_plane_family_mismatch_ignores_alias_derived_xy_host_profiles() -> None:
    code = (
        "from build123d import *\n"
        "width = 64.0\n"
        "depth = 48.0\n"
        "base_height = 14.0\n"
        "wall = 2.2\n"
        "with BuildPart() as base:\n"
        "    with BuildSketch(Plane.XY):\n"
        "        RectangleRounded(width, depth, radius=8.0)\n"
        "    extrude(amount=base_height)\n"
        "    inner_w = width - 2 * wall\n"
        "    inner_d = depth - 2 * wall\n"
        "    inner_r = max(8.0 - wall, 2.0)\n"
        "    with BuildSketch(Plane.XY.offset(wall)):\n"
        "        RectangleRounded(inner_w, inner_d, radius=inner_r)\n"
        "    extrude(amount=base_height - wall, mode=Mode.SUBTRACT)\n"
        "    with BuildSketch(Plane.YZ.offset(-depth / 2)):\n"
        "        RectangleRounded(40.0, 12.0, radius=2.0)\n"
        "    extrude(amount=1.0, mode=Mode.SUBTRACT)\n"
        "result = base.part\n"
    )

    hits = _find_named_face_plane_family_mismatch_hits(
        ast.parse(code),
        requirement_lower=(
            "create a rounded clamshell base and add a shallow label recess on the front face."
        ),
    )

    assert len(hits) == 1
    assert hits[0]["plane_name"] == "YZ"


def test_named_face_plane_family_mismatch_ignores_host_profile_aliases_derived_via_wall_thick_names() -> None:
    code = (
        "from build123d import *\n"
        "width = 64.0\n"
        "depth = 48.0\n"
        "base_height = 14.0\n"
        "lid_height = 10.0\n"
        "wall_thick = 2.2\n"
        "with BuildPart() as lid:\n"
        "    with Locations((0, 0, base_height)):\n"
        "        with BuildSketch(Plane.XY):\n"
        "            RectangleRounded(width, depth, radius=8.0)\n"
        "        extrude(amount=lid_height)\n"
        "        inner_w = width - 2 * wall_thick\n"
        "        inner_d = depth - 2 * wall_thick\n"
        "        inner_r = max(8.0 - wall_thick, 2.0)\n"
        "        with BuildSketch(Plane.XY.offset(base_height + lid_height - wall_thick)):\n"
        "            RectangleRounded(inner_w, inner_d, radius=inner_r)\n"
        "        extrude(amount=lid_height, mode=Mode.SUBTRACT)\n"
        "    with BuildSketch(Plane.YZ.offset(-depth / 2)):\n"
        "        RectangleRounded(12.0, 40.0, radius=2.0)\n"
        "    extrude(amount=1.0, mode=Mode.SUBTRACT)\n"
        "result = lid.part\n"
    )

    hits = _find_named_face_plane_family_mismatch_hits(
        ast.parse(code),
        requirement_lower=(
            "create a two-part rounded pillbox enclosure with a front face label window recess, "
            "a front thumb notch, and smooth lid/base shells."
        ),
    )

    assert len(hits) == 1
    assert hits[0]["line_no"] == 18
    assert hits[0]["plane_name"] == "YZ"


def test_named_face_requirement_plane_groups_include_mating_faces_as_top_bottom() -> None:
    groups = _named_face_requirement_plane_groups(
        "create a pillbox enclosure, add magnet recesses on the mating faces near the front corners, "
        "and add a front face label recess."
    )

    assert groups == {"front_back", "top_bottom"}


def test_named_face_plane_family_mismatch_allows_xy_mating_face_edits_when_requirement_mentions_front_face_and_mating_faces() -> None:
    code = (
        "from build123d import *\n"
        "width = 64.0\n"
        "depth = 48.0\n"
        "base_height = 12.0\n"
        "magnet_d = 1.5\n"
        "with BuildPart() as base:\n"
        "    with BuildSketch(Plane.XY):\n"
        "        RectangleRounded(width, depth, radius=6.0)\n"
        "    extrude(amount=base_height)\n"
        "    with BuildSketch(Plane.XY.offset(base_height - magnet_d)):\n"
        "        with Locations((20, depth/2 - 8), (-20, depth/2 - 8)):\n"
        "            Circle(3.0)\n"
        "    extrude(amount=magnet_d, mode=Mode.SUBTRACT)\n"
        "    with BuildSketch(Plane.XZ.offset(depth / 2)):\n"
        "        Circle(3.5)\n"
        "    extrude(amount=3.0, mode=Mode.SUBTRACT)\n"
        "result = base.part\n"
    )

    hits = _find_named_face_plane_family_mismatch_hits(
        ast.parse(code),
        requirement_lower=(
            "create a pillbox enclosure, add magnet recesses on the mating faces near the front corners, "
            "and add a front face label recess."
        ),
    )

    assert hits == []


def test_preflight_lint_rejects_rectanglerounded_radius_that_exceeds_half_of_height() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "label_w = 30.0\n"
            "label_h = 12.0\n"
            "label_r = 8.0\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.XY):\n"
            "        RectangleRounded(label_w, label_h, label_r)\n"
            "    extrude(amount=2.0)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a small rounded label plaque.",
        run_state=None,
    )

    rule_ids = {hit["rule_id"] for hit in payload["lint_hits"]}

    assert "invalid_build123d_contract.rectanglerounded_radius_bounds" in rule_ids


def test_numeric_assignment_env_converges_when_same_name_is_reassigned_in_multiple_loops() -> None:
    code = (
        "from build123d import *\n"
        "width = 64.0\n"
        "depth = 48.0\n"
        "wall = 2.2\n"
        "for x_sign in [-1, 1]:\n"
        "    magnet_z = depth / 2 - wall\n"
        "for x_sign in [-1, 1]:\n"
        "    magnet_z = wall / 2\n"
        "with BuildPart() as part:\n"
        "    with BuildSketch(Plane.XY):\n"
        "        RectangleRounded(width, depth, radius=8.0)\n"
        "    extrude(amount=4.0)\n"
        "result = part.part\n"
    )

    tree = ast.parse(code)
    env = _collect_numeric_assignment_env(tree)
    hits = _find_rectanglerounded_radius_bounds_hits(tree)

    assert env["width"] == 64.0
    assert env["depth"] == 48.0
    assert env["magnet_z"] == 1.1
    assert hits == []


def test_preflight_lint_ignores_locations_wrapped_xy_host_profiles_when_front_face_local_edit_uses_xz() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "width = 64.0\n"
            "depth = 48.0\n"
            "base_height = 12.0\n"
            "lid_height = 10.0\n"
            "wall = 2.2\n"
            "with BuildPart() as lid:\n"
            "    with Locations((0, 0, base_height)):\n"
            "        with BuildSketch(Plane.XY):\n"
            "            RectangleRounded(width, depth, radius=8.0)\n"
            "        extrude(amount=lid_height)\n"
            "        with BuildSketch(Plane.XY.offset(wall)):\n"
            "            RectangleRounded(width - 2*wall, depth - 2*wall, radius=6.0)\n"
            "        extrude(amount=lid_height - wall, mode=Mode.SUBTRACT)\n"
            "        with BuildSketch(Plane.XZ.offset(-depth / 2)):\n"
            "            RectangleRounded(30.0, 12.0, radius=2.0)\n"
            "        extrude(amount=1.0, mode=Mode.SUBTRACT)\n"
            "result = lid.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a two-part rounded pillbox enclosure and add a shallow rounded label window on the front face."
        ),
        run_state=None,
    )

    rule_ids = {item["rule_id"] for item in (payload or {}).get("lint_hits", [])}
    assert "invalid_build123d_contract.named_face_plane_family_mismatch" not in rule_ids


def test_named_face_plane_family_mismatch_ignores_detached_positive_axisymmetric_hinge_builder() -> None:
    code = (
        "from build123d import *\n"
        "width = 64.0\n"
        "depth = 48.0\n"
        "height = 24.0\n"
        "with BuildPart() as base:\n"
        "    with BuildSketch(Plane.XY):\n"
        "        RectangleRounded(width, depth, radius=8.0)\n"
        "    extrude(amount=height/2)\n"
        "with BuildPart() as hinge:\n"
        "    with BuildSketch(Plane.YZ.offset(-20.0)):\n"
        "        Circle(2.5)\n"
        "    extrude(amount=40.0)\n"
        "result = base.part + hinge.part\n"
    )

    hits = _find_named_face_plane_family_mismatch_hits(
        ast.parse(code),
        requirement_lower=(
            "create a two-part rounded pillbox enclosure with a living hinge at the back and "
            "a shallow rounded recess on the front face."
        ),
    )

    assert hits == []


def test_named_face_plane_family_mismatch_still_hits_detached_front_face_label_builder() -> None:
    code = (
        "from build123d import *\n"
        "width = 64.0\n"
        "depth = 48.0\n"
        "height = 24.0\n"
        "with BuildPart() as base:\n"
        "    with BuildSketch(Plane.XY):\n"
        "        RectangleRounded(width, depth, radius=8.0)\n"
        "    extrude(amount=height/2)\n"
        "with BuildPart() as label:\n"
        "    with BuildSketch(Plane.YZ.offset(-depth/2)):\n"
        "        RectangleRounded(18.0, 8.0, radius=2.0)\n"
        "    extrude(amount=1.2)\n"
        "result = base.part + label.part\n"
    )

    hits = _find_named_face_plane_family_mismatch_hits(
        ast.parse(code),
        requirement_lower=(
            "create a two-part rounded pillbox enclosure with a living hinge at the back and "
            "a shallow rounded recess on the front face."
        ),
    )

    assert len(hits) == 1
    assert hits[0]["plane_name"] == "YZ"


def test_named_face_plane_family_mismatch_ignores_placeholder_builder_without_materializing_ops() -> None:
    code = (
        "from build123d import *\n"
        "width = 64.0\n"
        "depth = 48.0\n"
        "base_height = 13.0\n"
        "label_depth = 1.0\n"
        "with BuildPart() as base:\n"
        "    with BuildSketch(Plane.XY):\n"
        "        RectangleRounded(width, depth, radius=8.0)\n"
        "    extrude(amount=base_height)\n"
        "with BuildPart() as label_cut:\n"
        "    with BuildSketch(Plane.YZ.offset(width/2)) as sk:\n"
        "        pass\n"
        "with BuildPart() as label_recess:\n"
        "    with BuildSketch(Plane.XZ.offset(depth/2)):\n"
        "        RectangleRounded(30.0, 12.0, radius=3.0)\n"
        "    extrude(amount=label_depth)\n"
        "result = base.part - label_recess.part\n"
    )

    hits = _find_named_face_plane_family_mismatch_hits(
        ast.parse(code),
        requirement_lower=(
            "create a two-part rounded pillbox enclosure with a living hinge at the back and "
            "a shallow rounded recess on the front face."
        ),
    )

    assert hits == []


def test_preflight_lint_routes_bottom_face_plane_family_mismatch_to_explicit_anchor_recipe() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "width = 66.0\n"
            "depth = 42.0\n"
            "height = 16.0\n"
            "with BuildPart() as part:\n"
            "    Box(width, depth, height)\n"
            "    with Locations(Plane.YZ.offset(width / 2)):\n"
            "        with GridLocations(x_spacing=50.0, y_spacing=0, x_count=2, y_count=1):\n"
            "            CounterSinkHole(radius=2.5, counter_sink_radius=4.5, depth=8.0, counter_sink_angle=90.0)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a rectangular bracket body with two countersunk mounting holes on the bottom face."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.named_face_plane_family_mismatch" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "explicit_anchor_hole_countersink_array_safe_recipe"


def test_preflight_lint_rejects_extrude_direction_keyword_alias() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.XY):\n"
            "        Rectangle(100, 50)\n"
            "    extrude(amount=20, direction=(0, 0, 1))\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="extrude a rectangle upward",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.extrude_direction_alias" in rule_ids


def test_preflight_lint_rejects_rectangle_length_keyword_alias() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.XY):\n"
            "        Rectangle(width=80, length=40)\n"
            "    extrude(amount=12)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="extrude a rectangular plate from a centered sketch",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.rectangle_length_alias" in rule_ids


def test_preflight_lint_rejects_slot_center_point_radius_alias() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.XY):\n"
            "        SlotCenterPoint(center=(0, 0), point=(12, 0), height=6, radius=3)\n"
            "    extrude(amount=4)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="extrude a slot-shaped profile",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.slot_center_point_radius_alias" in rule_ids


def test_preflight_lint_does_not_misclassify_unrelated_radius_names_as_slot_center_point_radius_alias() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "hole_radius = 2.0\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.XY):\n"
            "        SlotCenterPoint((0, 0), (12, 0), 6)\n"
            "    extrude(amount=4)\n"
            "    with Locations((0, 0, 2)):\n"
            "        Hole(radius=hole_radius, depth=4)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="extrude a slot-shaped profile with a separate hole radius variable",
        run_state=None,
    )

    if payload is None:
        return
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.slot_center_point_radius_alias" not in rule_ids


def test_preflight_lint_rejects_slot_center_point_center_point_alias() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(54, 34, 12)\n"
            "    with BuildSketch(Plane.XY):\n"
            "        SlotCenterPoint(center_point=(0, 0), point=(12, 0), height=6)\n"
            "    extrude(amount=2)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a bracket with a rounded slot profile centered on the top face."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.slot_center_point_center_alias" in rule_ids


def test_preflight_lint_rejects_slot_center_to_center_center_to_center_alias() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildSketch():\n"
            "    SlotCenterToCenter(center_to_center=18, height=6)\n"
        ),
        session_id="test-session",
        requirement_text="Create a rounded slot cutout with a center-to-center span.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.slot_center_to_center_alias" in rule_ids


def test_preflight_lint_rejects_slot_center_to_center_width_alias() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildSketch():\n"
            "    SlotCenterToCenter(center_separation=18, width=6)\n"
        ),
        session_id="test-session",
        requirement_text="Create a rounded slot cutout with a 6mm slot width.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.slot_center_to_center_alias" in rule_ids


def test_preflight_lint_rejects_python_indentation_error() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as host:\n"
            "    Box(10, 10, 10)\n"
            " cutter = Cylinder(radius=2, height=12)\n"
            "result = host.part - cutter\n"
        ),
        session_id="test-session",
        requirement_text="make a simple boolean cut",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "python_syntax.invalid_script" in rule_ids
    assert payload["failure_kind"] == "execute_build123d_python_syntax_failure"


def test_preflight_lint_rejects_buildpart_solid_method_arithmetic_and_surfaces_recess_recipe() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with BuildSketch(Plane.XY):\n"
            "        Rectangle(50, 50)\n"
            "    extrude(amount=15)\n"
            "    sphere = Sphere(radius=5)\n"
            "    part.solid = part.solid - sphere\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_SPHERICAL_RECESS_PATTERN_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.buildpart_solid_method_arithmetic" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "spherical_recess_pattern_builder_subtract_recipe"


def test_preflight_lint_rejects_nested_buildpart_cutter_arithmetic_for_hole_arrays() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "plate_length = 100.0\n"
            "plate_width = 60.0\n"
            "plate_height = 8.0\n"
            "through_hole_dia = 6.0\n"
            "countersink_dia = 12.0\n"
            "cone_depth = countersink_dia / 2\n"
            "hole_positions = [(25, 15), (25, 45), (75, 15), (75, 45)]\n"
            "with BuildPart() as part:\n"
            "    Box(plate_length, plate_width, plate_height)\n"
            "    for pos in hole_positions:\n"
            "        with Locations((pos[0] - plate_length/2, pos[1] - plate_width/2, plate_height/2)):\n"
            "            with BuildPart() as countersink:\n"
            "                Cone(bottom_radius=countersink_dia/2, top_radius=through_hole_dia/2, height=cone_depth)\n"
            "            part.part -= countersink.part\n"
            "            with BuildPart() as through_hole:\n"
            "                Cylinder(radius=through_hole_dia/2, height=plate_height + 0.2)\n"
            "            part.part -= through_hole.part\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.nested_buildpart_cutter_part_arithmetic" in rule_ids
    assert (
        payload["repair_recipe"]["recipe_id"]
        == "explicit_anchor_hole_same_builder_subtract_recipe"
    )
    recipe_steps = "\n".join(payload["repair_recipe"]["recipe_skeleton"]["steps"])
    assert "prefer one `CounterSinkHole(...)` pass first" in recipe_steps
    assert "Only fall back to an explicit same-builder cylinder+cone or revolved countersink recipe" in recipe_steps


def test_preflight_lint_surfaces_annular_groove_same_builder_recipe_for_nested_groove_band() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Cylinder(radius=25, height=60)\n"
            "    with BuildPart() as groove_band:\n"
            "        Cylinder(radius=25, height=2)\n"
            "    part.part -= groove_band.part\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_ANNULAR_GROOVE_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.nested_buildpart_cutter_part_arithmetic" in rule_ids
    assert (
        payload["repair_recipe"]["recipe_id"]
        == "annular_groove_same_builder_band_subtract_recipe"
    )


def test_preflight_lint_rejects_nested_buildpart_part_transform_inside_active_builder() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as lid:\n"
            "    Box(78, 56, 16)\n"
            "    with BuildPart() as notch_cut:\n"
            "        Box(10, 4, 4)\n"
            "    moved_notch = notch_cut.part.move(Location((0, 24, 6)))\n"
            "    lid.part = lid.part - moved_notch\n"
            "result = lid.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a two-part rounded clamshell enclosure with a hollow lid, a front "
            "thumb notch, and smooth printable outer walls."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.nested_buildpart_part_transform" in rule_ids
    assert (
        payload["repair_recipe"]["recipe_id"]
        == "nested_hollow_section_same_builder_subtract_contract"
    )


def test_preflight_lint_rejects_temporary_primitive_arithmetic_inside_active_buildpart() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    outer_cyl = Cylinder(radius=25, height=40)\n"
            "    inner_cyl = Cylinder(radius=17.5, height=40)\n"
            "    full_shell = outer_cyl - inner_cyl\n"
            "    half_space_box = Box(200, 100, 200)\n"
            "    half_space_box = Pos(0, 50, 0) * half_space_box\n"
            "    half_shell = full_shell & half_space_box\n"
            "    add(half_shell)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a half-cylindrical shell bearing housing with a flat split surface, "
            "merge a bottom pad with two lugs, cut the bore, and drill two through-holes "
            "through the lugs in the Y direction."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "half_shell_semi_profile_extrude_contract"


def test_preflight_lint_prefers_nested_hollow_section_same_builder_subtract_recipe() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as body:\n"
            "    outer_box = Box(78, 56, 18)\n"
            "    inner_box = Box(73.2, 51.2, 15.8)\n"
            "    inner_box = Pos(0, 0, 1.2) * inner_box\n"
            "    outer_box -= inner_box\n"
            "result = body.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a hollow enclosure base body with a front notch and hinge features.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic" in rule_ids
    assert (
        payload["repair_recipe"]["recipe_id"]
        == "nested_hollow_section_same_builder_subtract_contract"
    )


def test_preflight_lint_surfaces_active_builder_transform_rebind_for_temporary_primitives() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as hinge:\n"
            "    hinge_cyl = Cylinder(radius=4, height=20)\n"
            "    hinge_cyl = Rot(0, 90, 0) * hinge_cyl\n"
            "result = hinge.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a hinge barrel rotated onto the X axis.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert (
        "invalid_build123d_contract.active_builder_temporary_primitive_transform_rebind"
        in rule_ids
    )
    assert (
        payload["repair_recipe"]["recipe_id"]
        == "active_builder_temporary_primitive_transform_contract"
    )


def test_preflight_lint_rejects_active_builder_part_mutation_inside_buildpart() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as base:\n"
            "    outer_base = Box(78, 56, 18)\n"
            "    with Locations((0, 0, 2.4)):\n"
            "        inner_base = Box(73.2, 51.2, 15.61)\n"
            "    base.part = outer_base.part - inner_base.part\n"
            "result = base.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a hollow enclosure shell with wall thickness 2.4 mm.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.active_builder_part_mutation" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "active_builder_part_mutation_contract"


def test_preflight_lint_rejects_active_builder_part_mutation_for_direct_transformed_primitive_assignment() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as base:\n"
            "    Box(72, 64, 13)\n"
            "    cutter = Pos(24, 24, 1.0) * Cylinder(radius=2.0, height=2.0)\n"
            "    base.part = base.part - cutter\n"
            "result = base.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a two-part rounded clamshell enclosure with a hollow lid and base, "
            "a pin hinge, corner magnet slots, and a front thumb notch."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.active_builder_part_mutation" in rule_ids
    assert "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic" in rule_ids
    assert (
        "invalid_build123d_contract.active_builder_temporary_primitive_transform_rebind"
        in rule_ids
    )
    assert payload["repair_recipe"]["recipe_id"] == "clamshell_host_local_cut_contract"


def test_preflight_lint_rejects_plane_tuple_multiplication_for_locations() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as base:\n"
            "    Box(78, 56, 18)\n"
            "    with Locations(Plane.XY * (0, 0, 18)):\n"
            "        with BuildSketch():\n"
            "            Circle(3)\n"
            "        extrude(amount=-2, mode=Mode.SUBTRACT)\n"
            "result = base.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a hollow enclosure shell with one top-face magnet recess.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.plane_tuple_multiplication" in rule_ids
    assert (
        payload["repair_recipe"]["recipe_id"]
        == "build123d_plane_tuple_multiplication_contract"
    )


def test_preflight_lint_rejects_loc_helper_alias() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as base:\n"
            "    Box(20, 20, 8)\n"
            "moved = base.part.move(Loc((0, 0, 4)))\n"
            "result = moved\n"
        ),
        session_id="test-session",
        requirement_text="Create a box and move it upward.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.loc_helper_name" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_location_helper_contract"


def test_preflight_lint_rejects_capitalized_scale_helper_guess() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "earphone = Sphere(10)\n"
            "earphone = Scale.by((1.2, 0.8, 0.4)) * earphone\n"
            "result = earphone\n"
        ),
        session_id="test-session",
        requirement_text="Create a softly flattened organic earphone cavity proxy by scaling a detached solid.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.scale_helper_case" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_scale_helper_contract"


def test_preflight_lint_rejects_bare_move_helper_guess() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as lid:\n"
            "    Box(20, 20, 4)\n"
            "move(lid.part, (0, 0, 6))\n"
            "result = lid.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a lid and move it upward.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.bare_move_helper" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_location_helper_contract"


def test_preflight_lint_prefers_shell_recipe_over_loc_helper_when_builder_arithmetic_is_also_present() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as body:\n"
            "    outer_box = Box(78, 56, 18)\n"
            "    inner_box = Box(73.2, 51.2, 15.6)\n"
            "    inner_box = Pos(0, 0, 1.2) * inner_box\n"
            "    outer_box -= inner_box\n"
            "moved = body.part.move(Loc((0, 0, 2)))\n"
            "result = moved\n"
        ),
        session_id="test-session",
        requirement_text="Create a hollow enclosure base body with a front notch and hinge features.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic" in rule_ids
    assert "invalid_build123d_api.loc_helper_name" in rule_ids
    assert (
        payload["repair_recipe"]["recipe_id"]
        == "nested_hollow_section_same_builder_subtract_contract"
    )


def test_preflight_lint_rejects_capitalized_split_helper_guess_for_clamshell_split() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as shell:\n"
            "    Box(40, 30, 20)\n"
            "result = Split(shell.part, Plane.XY)\n"
        ),
        session_id="test-session",
        requirement_text="Create a two-part clamshell enclosure with a lid and base.",
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_api.split_helper_case" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "build123d_split_function_contract"


def test_preflight_lint_surfaces_explicit_anchor_hole_recipe_for_temporary_countersink_primitive_arithmetic() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(100, 60, 8, align=(Align.MIN, Align.MIN, Align.MIN))\n"
            "    cone = Cone(bottom_radius=3, top_radius=6, height=3)\n"
            "    cone = Pos(25, 15, 8) * cone\n"
            "    part.part = part.part - cone\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic" in rule_ids
    assert (
        payload["repair_recipe"]["recipe_id"]
        == "explicit_anchor_hole_same_builder_subtract_recipe"
    )


def test_preflight_lint_rejects_nested_subtractive_buildpart_inside_location_array() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "plate_length = 100.0\n"
            "plate_width = 60.0\n"
            "plate_height = 8.0\n"
            "hole_diameter = 6.0\n"
            "countersink_diameter = 12.0\n"
            "countersink_depth = (countersink_diameter - hole_diameter) / 2.0\n"
            "hole_positions = [(-25.0, -15.0), (-25.0, 15.0), (25.0, -15.0), (25.0, 15.0)]\n"
            "with BuildPart() as part:\n"
            "    Box(length=plate_length, width=plate_width, height=plate_height)\n"
            "    for pos in hole_positions:\n"
            "        with Locations((pos[0], pos[1], plate_height / 2)):\n"
            "            with BuildPart(mode=Mode.SUBTRACT) as countersink:\n"
            "                Cone(bottom_radius=countersink_diameter / 2, top_radius=hole_diameter / 2, height=countersink_depth, align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
            "            with BuildPart(mode=Mode.SUBTRACT) as through_hole:\n"
            "                Cylinder(radius=hole_diameter / 2, height=plate_height, align=(Align.CENTER, Align.CENTER, Align.CENTER))\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert (
        "invalid_build123d_context.nested_subtractive_buildpart_inside_active_builder"
        in rule_ids
    )
    assert (
        payload["repair_recipe"]["recipe_id"]
        == "explicit_anchor_hole_same_builder_subtract_recipe"
    )
    repair_steps = "\n".join(payload["repair_recipe"]["recipe_skeleton"]["steps"])
    assert "prefer one `CounterSinkHole(...)` pass first" in repair_steps


def test_preflight_lint_rejects_non_subtractive_manual_countersink_cutters_inside_locations() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as plate:\n"
            "    Box(100, 60, 8)\n"
            "    hole_positions = [(-25, -15), (-25, 15), (25, -15), (25, 15)]\n"
            "    for pos in hole_positions:\n"
            "        with Locations((pos[0], pos[1], 4)):\n"
            "            Cone(3, 6, 3)\n"
            "            Cylinder(3, 10)\n"
            "result = plate.part\n"
        ),
        session_id="test-session",
        requirement_text=_COUNTERSUNK_HOLE_ARRAY_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert (
        "invalid_build123d_contract.explicit_anchor_manual_cutter_requires_subtract_mode"
        in rule_ids
    )
    assert (
        payload["repair_recipe"]["recipe_id"]
        == "explicit_anchor_hole_same_builder_subtract_recipe"
    )


def test_preflight_lint_does_not_treat_origin_host_cylinders_as_manual_hole_cutters() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    with Locations((0, 0, 0)):\n"
            "        Cylinder(radius=25.0, height=40.0)\n"
            "    with Locations((0, 0, 0)):\n"
            "        Cylinder(radius=17.5, height=40.0, mode=Mode.SUBTRACT)\n"
            "    with Locations((22.25, -50.0, 20.0)):\n"
            "        Cylinder(radius=3.0, height=100.0, axis=Axis.Y, mode=Mode.SUBTRACT)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_HALF_SHELL_DIRECTIONAL_HOLE_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.cylinder_axis" in rule_ids
    assert (
        "invalid_build123d_contract.explicit_anchor_manual_cutter_requires_subtract_mode"
        not in rule_ids
    )


def test_preflight_lint_prefers_half_shell_profile_recipe_for_half_shell_temporary_primitive_arithmetic() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    outer_cyl = Cylinder(radius=25.0, height=40.0)\n"
            "    inner_cyl = Cylinder(radius=17.5, height=40.0)\n"
            "    result = outer_cyl - inner_cyl\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_HALF_SHELL_DIRECTIONAL_HOLE_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "half_shell_semi_profile_extrude_contract"


def test_preflight_lint_prefers_directional_hole_recipe_for_explicit_anchor_cylinder_axis() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(54.0, 25.0, 40.0)\n"
            "    with Locations((-22.25, -50.0, 20.0), (22.25, -50.0, 20.0)):\n"
            "        Cylinder(radius=3.0, height=100.0, axis=Axis.Y, mode=Mode.SUBTRACT)\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text=_HALF_SHELL_DIRECTIONAL_HOLE_REQUIREMENT,
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.cylinder_axis" in rule_ids
    assert (
        payload["repair_recipe"]["recipe_id"]
        == "explicit_anchor_directional_hole_cylinder_contract"
    )


def test_preflight_lint_prefers_structural_builder_contract_over_cylinder_axis_fix_when_lints_are_mixed() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as base:\n"
            "    Box(78, 56, 18)\n"
            "    slot_cutter = Cylinder(radius=3.0, height=20.0, axis=Axis.X)\n"
            "    base.part = base.part - slot_cutter\n"
            "result = base.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a two-part rounded clamshell enclosure with a hollow base and lid, "
            "a front thumb notch, one cylindrical side slot, and a side plug pocket."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_keyword.cylinder_axis" in rule_ids
    assert "invalid_build123d_contract.active_builder_part_mutation" in rule_ids
    assert (
        "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic"
        in rule_ids
    )
    assert payload["repair_recipe"]["recipe_id"] == "active_builder_part_mutation_contract"
    repair_steps = "\n".join(payload["repair_recipe"]["recipe_skeleton"]["steps"])
    assert "close the host builder first" in repair_steps


def test_preflight_lint_prefers_clamshell_host_local_cut_recipe_for_half_shell_shells() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as lid:\n"
            "    lid_outer = Box(72, 64, 13)\n"
            "    lid_outer = Pos(0, 0, 6.5) * lid_outer\n"
            "    with Locations((0, 28, 0)):\n"
            "        SlotOverall(10, 4, mode=Mode.SUBTRACT)\n"
            "result = lid.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a two-part rounded clamshell enclosure with a hollow lid and base, "
            "a pin hinge, corner magnet slots, and a front thumb notch."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.active_builder_temporary_primitive_transform_rebind" in rule_ids
    assert "invalid_build123d_context.sketch_primitive_requires_buildsketch" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "clamshell_host_local_cut_contract"
    repair_steps = "\n".join(payload["repair_recipe"]["recipe_skeleton"]["steps"])
    assert "separate positive solids after the shell hosts close" in repair_steps


def test_preflight_lint_keeps_clamshell_contract_priority_over_nested_hollow_slot_recipe() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as base:\n"
            "    base_outer = Box(72, 64, 13)\n"
            "    base_outer = Pos(0, 0, 6.5) * base_outer\n"
            "    base.part = base_outer\n"
            "    mag_cyl = Cylinder(radius=2.0, height=2.0)\n"
            "    mag_cyl = Pos(24, 24, 1.0) * mag_cyl\n"
            "    base.part = base.part - mag_cyl\n"
            "result = base.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a two-part rounded clamshell enclosure with a hollow lid and base, "
            "a pin hinge, corner magnet slots, and a front thumb notch."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.active_builder_part_mutation" in rule_ids
    assert "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic" in rule_ids
    assert "invalid_build123d_contract.active_builder_temporary_primitive_transform_rebind" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "clamshell_host_local_cut_contract"


def test_preflight_lint_keeps_clamshell_contract_priority_when_slots_family_comes_from_kernel() -> None:
    run_state = RunState(
        session_id="test-session",
        requirements={},
        feature_graph=SimpleNamespace(
            feature_instances={
                "instance.slots.feature_notch_or_profile_cut": SimpleNamespace(
                    family_id="slots"
                )
            }
        ),
    )
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as enclosure:\n"
            "    with BuildPart() as base:\n"
            "        base_outer = Box(72, 64, 13)\n"
            "        base_outer = Pos(0, 0, 6.5) * base_outer\n"
            "        base.part = base_outer\n"
            "        mag_cyl = Cylinder(radius=2.0, height=2.0)\n"
            "        mag_cyl = Pos(24, 24, 1.0) * mag_cyl\n"
            "        base.part = base.part - mag_cyl\n"
            "result = enclosure.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a snap clamshell enclosure with overall dimensions 72mm x 64mm x 26mm. "
            "Use a pin hinge, keep wall thickness near 2.0mm, include two-part lid/base "
            "separation, corner magnet slots, and a thumb notch. The outer form should remain "
            "smooth and printable."
        ),
        run_state=run_state,
    )

    assert payload is not None
    assert "slots" in payload["candidate_family_ids"]
    assert "half_shell" in payload["candidate_family_ids"]
    assert payload["repair_recipe"]["recipe_id"] == "clamshell_host_local_cut_contract"
    repair_steps = "\n".join(payload["repair_recipe"]["recipe_skeleton"]["steps"])
    assert "plain `pin hinge` or `mechanical hinge` still defaults to a two-part lid/base target" in repair_steps
    assert "only detach the pin/hardware when the prompt explicitly asks for a removable pin, separate hinge parts, or an exposed hinge assembly" in repair_steps
    assert "`extrude(amount=h)` grows one-sided from the active sketch plane" in repair_steps
    assert "do not assume `Locations((0, 0, center_z))` plus `extrude(amount=h)` creates a centered shell interval" in repair_steps


def test_preflight_lint_prefers_clamshell_host_local_cut_recipe_when_nested_subtractive_builder_is_present() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as base:\n"
            "    Box(78, 56, 18)\n"
            "    with Locations((30, 0, 9)):\n"
            "        with BuildPart(mode=Mode.SUBTRACT) as pocket:\n"
            "            Box(12, 8, 4)\n"
            "moved = base.part.move(Loc((0, 0, 0)))\n"
            "result = moved\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a two-part rounded clamshell enclosure with a hollow base and lid, "
            "one side plug pocket, a front thumb notch, and a pin hinge."
        ),
        run_state=None,
    )

    assert payload is not None
    assert "half_shell" in payload["candidate_family_ids"]
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert (
        "invalid_build123d_context.nested_subtractive_buildpart_inside_active_builder"
        in rule_ids
    )
    assert "invalid_build123d_api.loc_helper_name" in rule_ids
    assert (
        payload["repair_recipe"]["recipe_id"]
        == "clamshell_host_local_cut_contract"
    )


def test_preflight_lint_prefers_clamshell_host_local_cut_recipe_for_named_face_plane_family_mismatch() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "width = 64.0\n"
            "depth = 48.0\n"
            "height = 24.0\n"
            "wall = 2.2\n"
            "with BuildPart() as base:\n"
            "    with BuildSketch(Plane.XY):\n"
            "        RectangleRounded(width, depth, radius=8.0)\n"
            "    extrude(amount=height / 2)\n"
            "    with BuildSketch(Plane.XY.offset(wall)):\n"
            "        RectangleRounded(width - 2 * wall, depth - 2 * wall, radius=6.0)\n"
            "    extrude(amount=height / 2 - wall, mode=Mode.SUBTRACT)\n"
            "    with BuildSketch(Plane.YZ.offset(-width / 2).shift_origin((0, 0, 0))):\n"
            "        RectangleRounded(18.0, 8.0, radius=2.0)\n"
            "    extrude(amount=1.2, mode=Mode.SUBTRACT)\n"
            "result = base.part\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a two-part rounded clamshell enclosure with a hollow lid and base, "
            "a living hinge, corner magnet recesses, and a rounded label recess on the front face."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.named_face_plane_family_mismatch" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "clamshell_host_local_cut_contract"
    repair_steps = "\n".join(payload["repair_recipe"]["recipe_skeleton"]["steps"])
    assert "`Plane.XZ.offset(±depth/2)`" in repair_steps
    assert "wrong host plane" in repair_steps
    assert "if the requirement says `living hinge`" in repair_steps
    assert "do not create detached `hinge_barrel` or `hinge_pin` solids" in repair_steps
    assert "do not translate the whole lid or base to the back seam coordinate" in repair_steps
    assert "hinge seam location from the hinge axis direction" in repair_steps
    assert "do not reinterpret the back-edge hinge seam as a `Plane.YZ` sketch family" in repair_steps
    assert "choose one axis-orientation lane for a detached hinge cylinder" in repair_steps


def test_preflight_lint_rejects_unrotated_clamshell_hinge_cylinder_axis_mismatch() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "width = 72.0\n"
            "depth = 64.0\n"
            "split_z = 0.0\n"
            "hinge_y = -depth / 2\n"
            "with BuildPart() as base:\n"
            "    Box(width, depth, 13)\n"
            "    with Locations((0, hinge_y, split_z)):\n"
            "        Cylinder(radius=2.0, height=12.0)\n"
            "with BuildPart() as lid:\n"
            "    Box(width, depth, 13)\n"
            "    with Locations((12, hinge_y, split_z)):\n"
            "        Cylinder(radius=2.0, height=12.0)\n"
            "result = Compound([base.part, lid.part])\n"
        ),
        session_id="test-session",
        requirement_text=(
            "Create a snap clamshell enclosure with overall dimensions 72mm x 64mm x 26mm. "
            "Use a pin hinge, keep wall thickness near 2.0mm, include two-part lid/base "
            "separation, corner magnet slots, and a thumb notch. The outer form should remain "
            "smooth and printable."
        ),
        run_state=None,
    )

    assert payload is not None
    rule_ids = {item["rule_id"] for item in payload["lint_hits"]}
    assert "invalid_build123d_contract.clamshell_hinge_unrotated_default_cylinder" in rule_ids
    assert payload["repair_recipe"]["recipe_id"] == "clamshell_host_local_cut_contract"
    repair_steps = "\n".join(payload["repair_recipe"]["recipe_skeleton"]["steps"])
    assert "do not drop an unrotated default `Cylinder(...)` directly onto `(x, hinge_y, split_z)`" in repair_steps
    assert "without a supported rotation/orientation lane that cylinder still runs along Z" in repair_steps


def test_preflight_lint_allows_mode_private_temporary_primitive_boolean_inside_active_buildpart() -> None:
    payload = _preflight_lint_execute_build123d(
        code=(
            "from build123d import *\n"
            "with BuildPart() as part:\n"
            "    Box(80, 60, 40)\n"
            "    inner_box = Box(74, 54, 37, mode=Mode.PRIVATE)\n"
            "    part.part = part.part - inner_box\n"
            "result = part.part\n"
        ),
        session_id="test-session",
        requirement_text="Create a shelled block with a shallow top-face recess and a reference hole pattern.",
        run_state=None,
    )

    assert payload is None
