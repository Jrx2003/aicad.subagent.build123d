from __future__ import annotations

from sub_agent_runtime.semantic_kernel import DomainKernelState, FamilyRepairPacket, FeatureInstance
from sub_agent_runtime.tooling.adapters import (
    compile_runtime_repair_packet_execution,
    describe_runtime_repair_packet_support,
)
from sub_agent_runtime.turn_state import RunState

_EXPLICIT_COUNTERSINK_REQUIREMENT = (
    "Select the top reference plane, draw a 100.0x60.0 millimeter rectangle and extrude it "
    "by 8.0 millimeters. Select the plate surface, and use the sketch to draw four points "
    "with coordinates (25,15), (25,45), (75,15), and (75,45). Exit the sketch, and activate "
    'the Hole Wizard or the revolved cut tool. If using the Hole Wizard: select "Countersink," '
    "set the standard, head diameter 12.0 millimeters, cone angle 90 degrees, through-hole "
    "diameter 6.0 millimeters, and in the position tab, select the four points drawn earlier."
)

_EXPLICIT_HOLE_REQUIREMENT = (
    "Create a centered plate 100.0 by 60.0 by 8.0 millimeters and add two through holes with "
    "hole diameter 6.0 millimeters at local coordinates (-25, -15) and (25, 15) on the top face."
)

_HALF_SHELL_REQUIREMENT = (
    "Create a half-cylindrical shell by sketching an outer semicircle of radius 25.0 millimeters "
    "and an inner semicircle of radius 17.5 millimeters on the XY plane, closing the profile along "
    "the split line, and extruding it 40.0 millimeters. Add a bottom rectangular pad spanning x = "
    "-27.0 to 27.0 millimeters with a height of 8.0 millimeters, remove the inner 35.0 millimeter "
    "diameter clearance so the shell remains open above the split line and two side lugs remain "
    "outside the bore, and union this pad with the shell. At z = 20.0 millimeters, drill two 6.0 "
    "millimeter through-holes through the lugs in the Y direction, centered at x = -22.25 and x = "
    "22.25 millimeters."
)


def test_compile_runtime_repair_packet_execution_reports_structured_contract_miss() -> None:
    graph = DomainKernelState(graph_id="graph-1")
    graph.feature_instances["feature-1"] = FeatureInstance(
        instance_id="feature-1",
        family_id="spherical_recess",
        primary_feature_id="feature.core",
        label="spherical recess",
        parameter_bindings={},
    )
    graph.repair_packets["packet-1"] = FamilyRepairPacket(
        packet_id="packet-1",
        family_id="spherical_recess",
        feature_instance_id="feature-1",
        repair_mode="subtree_rebuild",
        recipe_id="spherical_recess_host_face_center_set",
        target_anchor_summary={},
        realized_anchor_summary={},
    )
    run_state = RunState(
        session_id="session-1",
        requirements={"description": "Add spherical recesses."},
        feature_graph=graph,
    )

    payload = compile_runtime_repair_packet_execution(run_state=run_state, requirement_text="demo")

    assert payload["ok"] is False
    assert payload["error"] == "repair_packet_contract_miss"
    assert payload["missing_anchor_keys"] == ["expected_local_centers"]
    assert payload["missing_parameters"] == ["geometry_summary"]
    assert payload["contract_error"] == "missing_required_packet_contract_inputs"
    assert payload["recipe_contract"]["recipe_id"] == "spherical_recess_host_face_center_set"
    assert payload["recipe_contract"]["fallback_lane"] == "execute_build123d"


def test_describe_runtime_repair_packet_support_reports_supported_recipe_contract() -> None:
    support = describe_runtime_repair_packet_support(
        {
            "family_id": "half_shell_profile",
            "recipe_id": "half_shell_profile_global_xz_lug_hole_recipe",
        }
    )

    assert support["runtime_supported"] is True
    assert support["support_reason"] == "supported_recipe"
    assert support["recipe_contract"]["compiler_kind"] == "half_shell_profile_global_xz_lug_hole"
    assert support["recipe_contract"]["recipe_id"] == "half_shell_profile_global_xz_lug_hole_recipe"


def test_describe_runtime_repair_packet_support_accepts_axisymmetric_half_shell_recipe() -> None:
    support = describe_runtime_repair_packet_support(
        {
            "family_id": "axisymmetric_profile",
            "recipe_id": "half_shell_profile_global_xz_lug_hole_recipe",
        }
    )

    assert support["runtime_supported"] is True
    assert support["support_reason"] == "supported_recipe"
    assert support["recipe_contract"]["recipe_id"] == "half_shell_profile_global_xz_lug_hole_recipe"


def test_describe_runtime_repair_packet_support_reports_supported_explicit_anchor_recipe() -> None:
    support = describe_runtime_repair_packet_support(
        {
            "family_id": "explicit_anchor_hole",
            "recipe_id": "explicit_anchor_hole_centered_host_frame_array",
        }
    )

    assert support["runtime_supported"] is True
    assert support["support_reason"] == "supported_recipe"
    assert (
        support["recipe_contract"]["recipe_id"]
        == "explicit_anchor_hole_centered_host_frame_array"
    )


def test_describe_runtime_repair_packet_support_reports_descriptive_only_recipe() -> None:
    support = describe_runtime_repair_packet_support(
        {
            "family_id": "explicit_anchor_hole",
            "recipe_id": "explicit_anchor_hole_helper_contract_fallback",
        }
    )

    assert support["runtime_supported"] is False
    assert support["support_reason"] == "unsupported_recipe"
    assert support["recipe_contract"] is None


def test_describe_runtime_repair_packet_support_reports_missing_recipe_id() -> None:
    support = describe_runtime_repair_packet_support(
        {
            "family_id": "path_sweep",
            "recipe_id": None,
        }
    )

    assert support["runtime_supported"] is False
    assert support["support_reason"] == "missing_recipe_id"
    assert support["recipe_contract"] is None


def test_compile_runtime_repair_packet_execution_compiles_explicit_anchor_centered_array_recipe() -> None:
    graph = DomainKernelState(graph_id="graph-explicit-centered")
    graph.feature_instances["feature-1"] = FeatureInstance(
        instance_id="feature-1",
        family_id="explicit_anchor_hole",
        primary_feature_id="feature.explicit_anchor_hole",
        label="explicit anchor hole",
        blocker_ids=["feature_countersink"],
        parameter_bindings={
            "geometry_summary": {"bbox": [100.0, 60.0, 8.0]},
            "expected_local_centers": [[25.0, 15.0], [25.0, 45.0], [75.0, 15.0], [75.0, 45.0]],
        },
    )
    graph.repair_packets["packet-1"] = FamilyRepairPacket(
        packet_id="packet-1",
        family_id="explicit_anchor_hole",
        feature_instance_id="feature-1",
        repair_mode="subtree_rebuild",
        recipe_id="explicit_anchor_hole_centered_host_frame_array",
        host_frame={"frame_kind": "centered_bbox_xy", "host_face": "top"},
        target_anchor_summary={
            "requested_centers": [[25.0, 15.0], [25.0, 45.0], [75.0, 15.0], [75.0, 45.0]],
            "normalized_local_centers": [[-25.0, -15.0], [-25.0, 15.0], [25.0, -15.0], [25.0, 15.0]],
            "host_face": "top",
        },
        recipe_skeleton={"hole_call": "cskHole"},
    )
    run_state = RunState(
        session_id="session-explicit-centered",
        requirements={"description": _EXPLICIT_COUNTERSINK_REQUIREMENT},
        feature_graph=graph,
    )

    payload = compile_runtime_repair_packet_execution(
        run_state=run_state,
        requirement_text=_EXPLICIT_COUNTERSINK_REQUIREMENT,
    )

    assert payload["ok"] is True
    assert payload["recipe_id"] == "explicit_anchor_hole_centered_host_frame_array"
    assert payload["compiled_parameters"]["bbox"] == [100.0, 60.0, 8.0]
    assert payload["compiled_parameters"]["expected_local_centers"] == [
        [-25.0, -15.0],
        [-25.0, 15.0],
        [25.0, -15.0],
        [25.0, 15.0],
    ]
    assert payload["compiled_parameters"]["hole_diameter"] == 6.0
    assert payload["compiled_parameters"]["counter_sink_diameter"] == 12.0
    assert payload["compiled_parameters"]["counter_sink_angle"] == 90.0
    assert "Cylinder(" in payload["code"]
    assert "Cone(" in payload["code"]


def test_compile_runtime_repair_packet_execution_compiles_axisymmetric_half_shell_recipe() -> None:
    graph = DomainKernelState(graph_id="graph-half-shell-axisymmetric")
    graph.feature_instances["feature-1"] = FeatureInstance(
        instance_id="feature-1",
        family_id="axisymmetric_profile",
        primary_feature_id="feature.axisymmetric_profile",
        label="half shell envelope",
        blocker_ids=["feature_half_shell_profile_envelope"],
        parameter_bindings={
            "expected_half_profile_span": 25.0,
            "expected_length": 40.0,
            "likely_split_axis": "Z",
            "likely_split_bounds": [0.0, 40.0],
        },
    )
    graph.repair_packets["packet-1"] = FamilyRepairPacket(
        packet_id="packet-1",
        family_id="axisymmetric_profile",
        feature_instance_id="feature-1",
        repair_mode="subtree_rebuild",
        recipe_id="half_shell_profile_global_xz_lug_hole_recipe",
        host_frame={
            "frame_kind": "global_half_shell_split_frame",
            "split_axis": "Z",
            "half_plane": "positive",
            "hole_center_frame": "global_xz",
        },
        target_anchor_summary={
            "expected_half_profile_span": 25.0,
            "expected_length": 40.0,
        },
        realized_anchor_summary={
            "observed_split_bounds": [0.0, 40.0],
        },
        recipe_skeleton={"profile_kind": "semi_annulus_shell"},
    )
    run_state = RunState(
        session_id="session-half-shell-axisymmetric",
        requirements={"description": _HALF_SHELL_REQUIREMENT},
        feature_graph=graph,
    )

    payload = compile_runtime_repair_packet_execution(
        run_state=run_state,
        requirement_text=_HALF_SHELL_REQUIREMENT,
    )

    assert payload["ok"] is True
    assert payload["recipe_id"] == "half_shell_profile_global_xz_lug_hole_recipe"
    assert payload["compiled_parameters"]["outer_radius"] == 25.0
    assert payload["compiled_parameters"]["inner_radius"] == 17.5
    assert payload["compiled_parameters"]["length"] == 40.0
    assert payload["compiled_parameters"]["hole_centers_xz"] == [[-22.25, 20.0], [22.25, 20.0]]
    assert "Cylinder(_aicad_outer_radius" in payload["code"]


def test_compile_runtime_repair_packet_execution_compiles_explicit_anchor_local_hole_array_recipe() -> None:
    graph = DomainKernelState(graph_id="graph-explicit-local")
    graph.feature_instances["feature-1"] = FeatureInstance(
        instance_id="feature-1",
        family_id="explicit_anchor_hole",
        primary_feature_id="feature.explicit_anchor_hole",
        label="explicit anchor hole",
        blocker_ids=["feature_hole"],
        parameter_bindings={
            "geometry_summary": {"bbox": [100.0, 60.0, 8.0]},
            "expected_local_centers": [[-25.0, -15.0], [25.0, 15.0]],
        },
    )
    graph.repair_packets["packet-1"] = FamilyRepairPacket(
        packet_id="packet-1",
        family_id="explicit_anchor_hole",
        feature_instance_id="feature-1",
        repair_mode="subtree_rebuild",
        recipe_id="explicit_anchor_hole_local_anchor_array",
        host_frame={"frame_kind": "host_face_local", "host_face": "top"},
        target_anchor_summary={
            "requested_centers": [[-25.0, -15.0], [25.0, 15.0]],
            "host_face": "top",
        },
        recipe_skeleton={"hole_call": "hole"},
    )
    run_state = RunState(
        session_id="session-explicit-local",
        requirements={"description": _EXPLICIT_HOLE_REQUIREMENT},
        feature_graph=graph,
    )

    payload = compile_runtime_repair_packet_execution(
        run_state=run_state,
        requirement_text=_EXPLICIT_HOLE_REQUIREMENT,
    )

    assert payload["ok"] is True
    assert payload["recipe_id"] == "explicit_anchor_hole_local_anchor_array"
    assert payload["compiled_parameters"]["expected_local_centers"] == [
        [-25.0, -15.0],
        [25.0, 15.0],
    ]
    assert payload["compiled_parameters"]["hole_diameter"] == 6.0
    assert payload["compiled_parameters"]["counter_sink_diameter"] is None
    assert "Cylinder(" in payload["code"]
    assert "Cone(" not in payload["code"]
