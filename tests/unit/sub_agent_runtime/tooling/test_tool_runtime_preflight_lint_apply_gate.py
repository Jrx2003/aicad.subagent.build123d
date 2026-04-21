import ast

from sub_agent_runtime.tooling.lint.families.planes import (
    _named_face_requirement_plane_groups,
)
from sub_agent_runtime.tooling.lint.families.structural import (
    _collect_numeric_assignment_env,
    _find_rectanglerounded_radius_bounds_hits,
)
from sub_agent_runtime.tooling.lint.plane_rules import (
    _find_named_face_plane_family_mismatch_hits,
)
from sub_agent_runtime.tooling.lint.preflight import (
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
