from __future__ import annotations

from sub_agent_runtime.feature_graph import DomainKernelState, FamilyRepairPacket, FeatureInstance
from sub_agent_runtime.tool_adapters import compile_runtime_repair_packet_execution
from sub_agent_runtime.turn_state import RunState


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
