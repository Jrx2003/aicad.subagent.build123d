from sub_agent_runtime.semantic_kernel import (
    DomainKernelState,
    FamilyRepairPacket,
    FeatureInstance,
    build_domain_kernel_digest,
    initialize_domain_kernel_state,
    sync_domain_kernel_state,
    sync_domain_kernel_state_from_tool_result,
)
from sub_agent_runtime.semantic_kernel.recipes import (
    _explicit_anchor_hole_recipe_packet,
    _repair_packet_priority,
    _replace_repair_packets_from_active_instances,
)

def test_domain_kernel_surfaces_fallback_explicit_anchor_hole_recipe_when_centers_are_missing() -> None:
    requirements = {
        "description": (
            "Create a rectangular electronics bracket with two mounting holes and a countersink "
            "on the mounting face so that a topology-aware local finishing pass is useful."
        )
    }
    graph = initialize_domain_kernel_state(requirements)

    write_payload = {
        "success": True,
        "session_state_persisted": True,
        "step_file": "model.step",
        "snapshot": {
            "geometry": {
                "solids": 1,
                "faces": 13,
                "edges": 30,
                "volume": 31033.09,
                "bbox": [40.0, 62.0, 14.0],
                "bbox_min": [-20.0, -31.0, -7.0],
                "bbox_max": [20.0, 31.0, 7.0],
            }
        },
    }
    graph, _ = sync_domain_kernel_state_from_tool_result(
        graph,
        tool_name="execute_build123d",
        payload=write_payload,
        round_no=1,
    )

    validation_payload = {
        "success": True,
        "is_complete": False,
        "blockers": [
            "feature_countersink",
            "two_mounting_holes",
            "a_countersink_on_the_mounting_face_so_that_a_topology_aware_local_finishing_pass_is_useful",
        ],
        "blocker_taxonomy": [
            {
                "blocker_id": "feature_countersink",
                "family_ids": ["explicit_anchor_hole", "named_face_local_edit"],
                "feature_ids": [
                    "feature.explicit_anchor_hole",
                    "feature.named_face_local_edit",
                ],
                "primary_feature_id": "feature.explicit_anchor_hole",
                "evidence_source": "validation",
                "completeness_relevance": "core",
                "severity": "blocking",
                "recommended_repair_lane": "code_repair",
            },
            {
                "blocker_id": "two_mounting_holes",
                "family_ids": ["explicit_anchor_hole", "named_face_local_edit"],
                "feature_ids": [
                    "feature.explicit_anchor_hole",
                    "feature.named_face_local_edit",
                ],
                "primary_feature_id": "feature.explicit_anchor_hole",
                "evidence_source": "validation",
                "completeness_relevance": "core",
                "severity": "blocking",
                "recommended_repair_lane": "code_repair",
            },
        ],
    }
    graph = sync_domain_kernel_state(
        graph,
        requirements=requirements,
        latest_write_payload=write_payload,
        latest_validation=validation_payload,
        previous_error=None,
        reason="test:explicit_anchor_fallback_recipe",
    )

    digest = build_domain_kernel_digest(graph, max_nodes=48)

    assert digest["latest_repair_packet_family_id"] == "explicit_anchor_hole"
    assert (
        digest["latest_repair_packet_recipe_id"]
        == "explicit_anchor_hole_helper_contract_fallback"
    )
    assert (
        digest["latest_repair_packet_recipe_skeleton"]["center_source_key"]
        == "derive_from_requirement_or_validation"
    )
    assert (
        digest["latest_repair_packet_recipe_skeleton"]["workplane_normal_strategy"]
        == "host_face_outward_normal"
    )
    assert (
        digest["latest_repair_packet_recipe_skeleton"]["center_frame_kind"]
        == "host_face_local_2d"
    )

def test_repair_packet_priority_prefers_recipe_backed_packet_over_recipe_less_stub() -> None:
    recipe_backed_packet = FamilyRepairPacket(
        packet_id="packet.explicit_anchor",
        family_id="explicit_anchor_hole",
        feature_instance_id="instance.explicit_anchor_hole.feature_countersink",
        repair_mode="subtree_rebuild",
        target_anchor_summary={"expected_local_centers": [[-10.0, -10.0], [10.0, 10.0]]},
        host_frame={"frame_kind": "host_face_local", "host_face": "top"},
        recipe_id="explicit_anchor_hole_helper_contract_fallback",
    )
    recipe_less_stub = FamilyRepairPacket(
        packet_id="packet.path_sweep",
        family_id="path_sweep",
        feature_instance_id="instance.path_sweep.feature_path_sweep_rail",
        repair_mode="subtree_rebuild",
        target_anchor_summary={"profile_closed": True},
        host_frame={"frame_kind": "global_curve_frame"},
        recipe_id=None,
    )

    assert _repair_packet_priority(recipe_backed_packet) < _repair_packet_priority(
        recipe_less_stub
    )

def test_replace_repair_packets_retains_multiple_prioritized_packets() -> None:
    graph = DomainKernelState(graph_id="graph.test_packets")
    active_instances = [
        FeatureInstance(
            instance_id="instance.path_sweep.feature_path_sweep_rail",
            family_id="path_sweep",
            primary_feature_id="feature.path_sweep",
            label="path sweep rail",
            status="blocked",
            anchor_keys=["rail_points"],
            parameter_bindings={"rail_points": [[0.0, 0.0], [20.0, 10.0]]},
            latest_repair_mode="subtree_rebuild",
            repair_intent="rebuild_hollow_path_sweep_result",
        ),
        FeatureInstance(
            instance_id="instance.explicit_anchor_hole.feature_countersink",
            family_id="explicit_anchor_hole",
            primary_feature_id="feature.explicit_anchor_hole",
            label="explicit anchor countersink",
            status="blocked",
            host_ids=["top"],
            blocker_ids=["feature_countersink"],
            anchor_keys=["bbox", "bbox_min", "bbox_max", "expected_local_centers"],
            parameter_bindings={
                "bbox": [62.0, 40.0, 14.0],
                "bbox_min": [-31.0, -20.0, -7.0],
                "bbox_max": [31.0, 20.0, 7.0],
                "expected_local_centers": [[8.0, 8.0], [54.0, 32.0]],
            },
            latest_repair_mode="subtree_rebuild",
            repair_intent="restore_explicit_anchor_countersink",
        ),
    ]

    _replace_repair_packets_from_active_instances(graph, active_instances)

    digest = build_domain_kernel_digest(graph, max_nodes=32)
    payload = graph.to_query_payload(include_bindings=True, max_bindings=8)
    repair_packets = payload.get("repair_packets") or []

    assert digest["repair_packet_count"] == 2
    assert digest["latest_repair_packet_family_id"] == "explicit_anchor_hole"
    assert digest["latest_repair_packet_recipe_id"] == "explicit_anchor_hole_centered_host_frame_array"
    assert [item["family_id"] for item in repair_packets] == [
        "explicit_anchor_hole",
        "path_sweep",
    ]

def test_explicit_anchor_recipe_packet_uses_top_level_bbox_when_geometry_summary_is_missing() -> None:
    feature_instance = FeatureInstance(
        instance_id="instance.explicit_anchor_hole.feature_countersink",
        family_id="explicit_anchor_hole",
        primary_feature_id="feature.explicit_anchor_hole",
        label="explicit anchor hole",
        status="blocked",
        summary="feature_countersink",
        host_ids=["body.primary"],
        blocker_ids=["feature_countersink"],
        anchor_keys=["bbox", "bbox_min", "bbox_max", "realized_centers"],
        parameter_bindings={
            "bbox": [62.0, 40.0, 14.0],
            "bbox_min": [-31.0, -20.0, -7.0],
            "bbox_max": [31.0, 20.0, 7.0],
            "expected_local_centers": [],
            "realized_centers": [[-23.0, 0.0], [23.0, 0.0]],
        },
        latest_repair_mode="subtree_rebuild",
        repair_intent="realign_local_feature_centers",
    )

    (
        host_frame,
        _target_anchor_summary,
        realized_anchor_summary,
        recipe_id,
        _recipe_summary,
        recipe_skeleton,
    ) = _explicit_anchor_hole_recipe_packet(feature_instance=feature_instance)

    assert host_frame["frame_kind"] == "centered_bbox_xy"
    assert host_frame["translation_from_corner_frame"] == [-31.0, -20.0]
    assert realized_anchor_summary["realized_centers"] == [[-23.0, 0.0], [23.0, 0.0]]
    assert recipe_id == "explicit_anchor_hole_helper_contract_fallback"
    assert recipe_skeleton["workplane_frame"] == "centered_bbox_xy"
    assert recipe_skeleton["workplane_normal_strategy"] == "host_face_outward_normal"
    assert recipe_skeleton["center_frame_kind"] == "host_face_local_2d"

def test_explicit_anchor_recipe_packet_preserves_expected_count_without_explicit_centers() -> None:
    feature_instance = FeatureInstance(
        instance_id="instance.explicit_anchor_hole.feature_hole",
        family_id="explicit_anchor_hole",
        primary_feature_id="feature.explicit_anchor_hole",
        label="explicit anchor hole",
        status="blocked",
        summary="feature_hole",
        host_ids=["body.primary"],
        blocker_ids=["feature_hole"],
        anchor_keys=["bbox", "expected_local_center_count"],
        parameter_bindings={
            "bbox": [62.0, 40.0, 14.0],
            "bbox_min": [-31.0, -20.0, -7.0],
            "bbox_max": [31.0, 20.0, 7.0],
            "expected_local_center_count": 2,
            "realized_center_count": 0,
        },
        latest_repair_mode="subtree_rebuild",
        repair_intent="restore_explicit_anchor_countersink",
    )

    (
        _host_frame,
        target_anchor_summary,
        realized_anchor_summary,
        recipe_id,
        _recipe_summary,
        recipe_skeleton,
    ) = _explicit_anchor_hole_recipe_packet(feature_instance=feature_instance)

    assert recipe_id == "explicit_anchor_hole_helper_contract_fallback"
    assert target_anchor_summary["expected_center_count"] == 2
    assert target_anchor_summary["recommended_center_count"] == 2
    assert recipe_skeleton["center_count_hint"] == 2
    assert recipe_skeleton["center_count_source"] == "requirement_or_validation"
    assert realized_anchor_summary["realized_center_count"] == 0
    assert recipe_skeleton["center_count_hint"] == 2
