from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import sub_agent_runtime.tooling.execution as tool_runtime_module
from sub_agent_runtime.semantic_kernel import DomainKernelState, FamilyRepairPacket, FeatureInstance
from sub_agent_runtime.tooling import ToolRuntime
from sub_agent_runtime.turn_state import (
    RunState,
    ToolCategory,
    ToolResultRecord,
    TurnToolPolicy,
)

_EXPLICIT_COUNTERSINK_REQUIREMENT = (
    "Select the top reference plane, draw a 100.0x60.0 millimeter rectangle and extrude it "
    "by 8.0 millimeters. Select the plate surface, and use the sketch to draw four points "
    "with coordinates (25,15), (25,45), (75,15), and (75,45). Exit the sketch, and activate "
    'the Hole Wizard or the revolved cut tool. If using the Hole Wizard: select "Countersink," '
    "set the standard, head diameter 12.0 millimeters, cone angle 90 degrees, through-hole "
    "diameter 6.0 millimeters, and in the position tab, select the four points drawn earlier."
)


@pytest.mark.asyncio

async def test_execute_repair_packet_executes_explicit_anchor_packet_and_enriches_payload() -> None:
    captured: dict[str, object] = {}

    class FakeSandbox:
        async def execute(self, **kwargs: object):
            captured.update(kwargs)
            return SimpleNamespace(
                success=True,
                error_code=None,
                error_message=None,
                stdout="",
                stderr="",
                output_files=["model.step"],
                output_file_contents={},
                evaluation={"mode": "none", "status": "not_requested", "summary": "n/a"},
                session_id=str(kwargs.get("session_id")),
                step=1,
                step_file="model.step",
                snapshot={"geometry": {"solids": 1, "bbox": [100.0, 60.0, 8.0]}},
                session_state_persisted=True,
            )

    graph = DomainKernelState(graph_id="graph-explicit-anchor-runtime")
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
        session_id="session-explicit-anchor-runtime",
        requirements={"description": _EXPLICIT_COUNTERSINK_REQUIREMENT},
        feature_graph=graph,
    )
    tool_runtime = ToolRuntime(sandbox=FakeSandbox())

    batch = await tool_runtime.execute_tool_calls(
        tool_calls=[
            SimpleNamespace(
                name="execute_repair_packet",
                arguments={},
                id="execute_repair_packet:0",
            )
        ],
        session_id="session-explicit-anchor-runtime",
        requirements={"description": _EXPLICIT_COUNTERSINK_REQUIREMENT},
        requirement_text=_EXPLICIT_COUNTERSINK_REQUIREMENT,
        sandbox_timeout=30,
        round_no=2,
        run_state=run_state,
    )

    assert len(batch.tool_results) == 1
    result = batch.tool_results[0]
    assert result.success is True
    assert result.payload["compiled_from_repair_packet"] is True
    assert result.payload["repair_packet_compile_success"] is True
    assert result.payload["family_id"] == "explicit_anchor_hole"
    assert result.payload["recipe_id"] == "explicit_anchor_hole_centered_host_frame_array"
    assert result.payload["compiled_parameters"]["hole_diameter"] == 6.0
    assert result.payload["compiled_parameters"]["counter_sink_diameter"] == 12.0
    assert "Cylinder(" in str(captured["code"])
    assert "Cone(" in str(captured["code"])
