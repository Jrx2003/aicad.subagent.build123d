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

def test_domain_kernel_preserves_named_face_local_finish_lane() -> None:
    requirements = {
        "description": "Create a bracket and finish a local subtractive edit on the target face."
    }
    graph = initialize_domain_kernel_state(requirements)

    validation_payload = {
        "success": True,
        "is_complete": False,
        "blockers": ["feature_target_face_subtractive_merge"],
        "blocker_taxonomy": [
            {
                "blocker_id": "feature_target_face_subtractive_merge",
                "family_ids": ["named_face_local_edit"],
                "feature_ids": ["feature.named_face_local_edit"],
                "primary_feature_id": "feature.named_face_local_edit",
                "evidence_source": "validation",
                "completeness_relevance": "core",
                "severity": "blocking",
                "recommended_repair_lane": "local_finish",
            }
        ],
    }

    graph, _ = sync_domain_kernel_state_from_tool_result(
        graph,
        tool_name="validate_requirement",
        payload=validation_payload,
        round_no=1,
    )

    digest = build_domain_kernel_digest(graph, max_nodes=32)
    blocker_node = graph.nodes["blocker.feature_target_face_subtractive_merge"]

    assert digest["latest_binding_repair_lane"] == "local_finish"
    assert blocker_node.attributes["recommended_repair_lane"] == "local_finish"

def test_domain_kernel_refreshes_feature_instance_bbox_from_latest_write_after_validation() -> None:
    requirements = {
        "description": (
            "Create a two-part rounded clamshell storage enclosure with overall dimensions "
            "78mm x 56mm x 32mm, keep wall thickness near 2.4mm, and add a front thumb notch."
        )
    }
    graph = initialize_domain_kernel_state(requirements)

    old_write = {
        "success": True,
        "session_state_persisted": True,
        "step_file": "model.step",
        "snapshot": {
            "geometry": {
                "solids": 1,
                "faces": 50,
                "edges": 109,
                "volume": 39513.79,
                "bbox": [78.0, 60.8, 91.2],
                "bbox_min": [-39.0, -32.8, -54.6],
                "bbox_max": [39.0, 28.0, 36.6],
            }
        },
    }
    graph, _ = sync_domain_kernel_state_from_tool_result(
        graph,
        tool_name="execute_build123d",
        payload=old_write,
        round_no=1,
    )

    old_probe = {
        "success": True,
        "detected_families": ["general_geometry", "slots"],
        "probes": [
            {
                "family": "general_geometry",
                "success": True,
                "signals": {
                    "bbox": [78.0, 60.8, 91.2],
                    "bbox_min": [-39.0, -32.8, -54.6],
                    "bbox_max": [39.0, 28.0, 36.6],
                    "bbox_min_span": 60.8,
                    "bbox_max_span": 91.2,
                },
                "anchor_summary": {
                    "bbox": [78.0, 60.8, 91.2],
                    "bbox_min": [-39.0, -32.8, -54.6],
                    "bbox_max": [39.0, 28.0, 36.6],
                    "bbox_min_span": 60.8,
                    "bbox_max_span": 91.2,
                },
                "family_binding": "general_geometry",
                "required_evidence_kinds": ["geometry"],
            },
            {
                "family": "slots",
                "success": False,
                "signals": {
                    "bbox": [78.0, 60.8, 91.2],
                    "bbox_min": [-39.0, -32.8, -54.6],
                    "bbox_max": [39.0, 28.0, 36.6],
                    "bbox_min_span": 60.8,
                    "bbox_max_span": 91.2,
                },
                "anchor_summary": {
                    "bbox": [78.0, 60.8, 91.2],
                    "bbox_min": [-39.0, -32.8, -54.6],
                    "bbox_max": [39.0, 28.0, 36.6],
                    "bbox_min_span": 60.8,
                    "bbox_max_span": 91.2,
                    "requires_topology_host_ranking": True,
                },
                "family_binding": "slots",
                "required_evidence_kinds": ["geometry", "topology"],
            },
        ],
    }
    graph, _ = sync_domain_kernel_state_from_tool_result(
        graph,
        tool_name="query_feature_probes",
        payload=old_probe,
        round_no=2,
    )

    old_validation = {
        "success": True,
        "is_complete": False,
        "blockers": [
            "feature_notch_or_profile_cut",
            "keep_wall_thickness_near_2_4mm",
        ],
        "checks": [
            {
                "check_id": "feature_notch_or_profile_cut",
                "status": "fail",
                "evidence": "no complex base profile or local subtractive notch window found",
            },
            {
                "check_id": "keep_wall_thickness_near_2_4mm",
                "status": "fail",
                "evidence": "requested_thickness=2.4, bbox_min_span=60.8",
            },
        ],
        "blocker_taxonomy": [
            {
                "blocker_id": "feature_notch_or_profile_cut",
                "family_ids": ["slots"],
                "feature_ids": ["feature.nested_hollow_section"],
                "primary_feature_id": "feature.nested_hollow_section",
                "evidence_source": "validation",
                "completeness_relevance": "core",
                "severity": "blocking",
                "recommended_repair_lane": "subtree_rebuild",
            },
            {
                "blocker_id": "keep_wall_thickness_near_2_4mm",
                "family_ids": ["general_geometry"],
                "feature_ids": ["feature.core_geometry"],
                "primary_feature_id": "feature.core_geometry",
                "evidence_source": "validation",
                "completeness_relevance": "core",
                "severity": "blocking",
                "recommended_repair_lane": "whole_part_rebuild",
            },
        ],
    }
    graph, _ = sync_domain_kernel_state_from_tool_result(
        graph,
        tool_name="validate_requirement",
        payload=old_validation,
        round_no=3,
    )

    new_write = {
        "success": True,
        "session_state_persisted": True,
        "step_file": "model.step",
        "snapshot": {
            "geometry": {
                "solids": 1,
                "faces": 35,
                "edges": 87,
                "volume": 37858.14,
                "bbox": [78.0, 56.0, 32.0],
                "bbox_min": [-39.0, -28.0, 0.0],
                "bbox_max": [39.0, 28.0, 32.0],
            }
        },
    }
    graph, _ = sync_domain_kernel_state_from_tool_result(
        graph,
        tool_name="execute_build123d",
        payload=new_write,
        round_no=4,
        fallback_latest_validation=old_validation,
    )

    new_validation = {
        "success": True,
        "is_complete": False,
        "blockers": [
            "feature_notch_or_profile_cut",
            "keep_wall_thickness_near_2_4mm",
        ],
        "checks": [
            {
                "check_id": "feature_notch_or_profile_cut",
                "status": "fail",
                "evidence": "no complex base profile or local subtractive notch window found",
            },
            {
                "check_id": "keep_wall_thickness_near_2_4mm",
                "status": "fail",
                "evidence": "requested_thickness=2.4, bbox_min_span=32.0",
            },
        ],
        "blocker_taxonomy": old_validation["blocker_taxonomy"],
        "coverage_confidence": 0.25,
        "insufficient_evidence": True,
        "decision_hints": ["inspect more geometry/topology evidence before completion"],
        "observation_tags": ["insufficient_evidence"],
    }
    graph, _ = sync_domain_kernel_state_from_tool_result(
        graph,
        tool_name="validate_requirement",
        payload=new_validation,
        round_no=5,
    )

    digest = build_domain_kernel_digest(graph, max_nodes=32)
    active_instances = {
        item["instance_id"]: item
        for item in digest.get("active_feature_instances", [])
        if isinstance(item, dict) and isinstance(item.get("instance_id"), str)
    }

    slot_instance = active_instances["instance.slots.feature_notch_or_profile_cut"]
    general_instance = active_instances["instance.general_geometry.keep_wall_thickness_near_2_4mm"]

    assert slot_instance["parameter_bindings"]["bbox"] == [78.0, 56.0, 32.0]
    assert general_instance["parameter_bindings"]["bbox"] == [78.0, 56.0, 32.0]
    assert (
        general_instance["parameter_bindings"]["anchor_summary"]["bbox"]
        == [78.0, 56.0, 32.0]
    )
    assert general_instance["parameter_bindings"]["bbox_min_span"] == 32.0
    assert "realized_bbox" not in general_instance["parameter_bindings"]
    assert "realized_bbox" not in general_instance["parameter_bindings"]["anchor_summary"]

def test_domain_kernel_keeps_latest_meaningful_geometry_after_failed_write() -> None:
    requirements = {
        "description": (
            "Create a two-part rounded clamshell storage enclosure with overall dimensions "
            "78mm x 56mm x 32mm, keep wall thickness near 2.4mm, and add a front thumb notch."
        )
    }
    graph = initialize_domain_kernel_state(requirements)

    successful_write = {
        "success": True,
        "session_state_persisted": True,
        "step_file": "model.step",
        "snapshot": {
            "geometry": {
                "solids": 1,
                "faces": 35,
                "edges": 87,
                "volume": 37858.14,
                "bbox": [78.0, 56.0, 32.0],
                "bbox_min": [-39.0, -28.0, 0.0],
                "bbox_max": [39.0, 28.0, 32.0],
            }
        },
    }
    graph, _ = sync_domain_kernel_state_from_tool_result(
        graph,
        tool_name="execute_build123d",
        payload=successful_write,
        round_no=1,
    )

    validation = {
        "success": True,
        "is_complete": False,
        "blockers": ["keep_wall_thickness_near_2_4mm"],
        "checks": [
            {
                "check_id": "keep_wall_thickness_near_2_4mm",
                "status": "fail",
                "evidence": "requested_thickness=2.4, bbox_min_span=32.0",
            }
        ],
        "blocker_taxonomy": [
            {
                "blocker_id": "keep_wall_thickness_near_2_4mm",
                "family_ids": ["general_geometry"],
                "feature_ids": ["feature.core_geometry"],
                "primary_feature_id": "feature.core_geometry",
                "evidence_source": "validation",
                "completeness_relevance": "core",
                "severity": "blocking",
                "recommended_repair_lane": "whole_part_rebuild",
            }
        ],
    }
    graph, _ = sync_domain_kernel_state_from_tool_result(
        graph,
        tool_name="validate_requirement",
        payload=validation,
        round_no=2,
    )

    failed_write = {
        "success": False,
        "session_state_persisted": False,
        "step_file": None,
        "snapshot": None,
        "error": "execute_build123d preflight lint failed",
        "failure_kind": "execute_build123d_api_lint_failure",
    }
    graph, _ = sync_domain_kernel_state_from_tool_result(
        graph,
        tool_name="execute_build123d",
        payload=failed_write,
        round_no=3,
        fallback_latest_validation=validation,
    )

    digest = build_domain_kernel_digest(graph, max_nodes=32)
    active_instances = {
        item["instance_id"]: item
        for item in digest.get("active_feature_instances", [])
        if isinstance(item, dict) and isinstance(item.get("instance_id"), str)
    }

    general_instance = active_instances["instance.general_geometry.keep_wall_thickness_near_2_4mm"]
    geometry_summary = general_instance["parameter_bindings"]["geometry_summary"]

    assert geometry_summary["bbox"] == [78.0, 56.0, 32.0]
    assert geometry_summary["persisted"] is True
    assert geometry_summary["step_file"] == "model.step"

def test_domain_kernel_refreshes_active_general_geometry_instances_when_later_blockers_shift() -> None:
    requirements = {
        "description": (
            "Create a snap clamshell enclosure with overall dimensions 72mm x 64mm x 26mm "
            "and keep wall thickness near 2.0mm."
        )
    }
    graph = initialize_domain_kernel_state(requirements)

    first_write = {
        "success": True,
        "session_state_persisted": True,
        "step_file": "model.step",
        "snapshot": {
            "geometry": {
                "solids": 3,
                "faces": 42,
                "edges": 110,
                "volume": 28123.0,
                "bbox": [72.0, 101.0, 26.0],
                "bbox_min": [-36.0, -69.0, -13.0],
                "bbox_max": [36.0, 32.0, 13.0],
            }
        },
    }
    graph, _ = sync_domain_kernel_state_from_tool_result(
        graph,
        tool_name="execute_build123d",
        payload=first_write,
        round_no=1,
    )

    initial_validation = {
        "success": True,
        "is_complete": False,
        "blockers": ["create_a_snap_clamshell_enclosure_with_overall_dimensions_72mm_x_64mm_x_26mm"],
        "checks": [
            {
                "check_id": "create_a_snap_clamshell_enclosure_with_overall_dimensions_72mm_x_64mm_x_26mm",
                "status": "fail",
                "evidence": "bbox=[72.0,101.0,26.0] exceeds requested depth envelope",
            }
        ],
        "blocker_taxonomy": [
            {
                "blocker_id": "create_a_snap_clamshell_enclosure_with_overall_dimensions_72mm_x_64mm_x_26mm",
                "family_ids": ["general_geometry"],
                "feature_ids": ["feature.core_geometry"],
                "primary_feature_id": "feature.core_geometry",
                "evidence_source": "validation",
                "completeness_relevance": "core",
                "severity": "blocking",
                "recommended_repair_lane": "whole_part_rebuild",
            }
        ],
    }
    graph, _ = sync_domain_kernel_state_from_tool_result(
        graph,
        tool_name="validate_requirement",
        payload=initial_validation,
        round_no=2,
    )

    repaired_write = {
        "success": True,
        "session_state_persisted": True,
        "step_file": "model.step",
        "snapshot": {
            "geometry": {
                "solids": 4,
                "faces": 48,
                "edges": 124,
                "volume": 26501.0,
                "bbox": [72.0, 64.0, 26.0],
                "bbox_min": [-36.0, -32.0, 0.0],
                "bbox_max": [36.0, 32.0, 26.0],
            }
        },
    }
    graph, _ = sync_domain_kernel_state_from_tool_result(
        graph,
        tool_name="execute_build123d",
        payload=repaired_write,
        round_no=3,
    )

    later_validation = {
        "success": True,
        "is_complete": False,
        "blockers": ["keep_wall_thickness_near_2_0mm"],
        "checks": [
            {
                "check_id": "keep_wall_thickness_near_2_0mm",
                "status": "fail",
                "evidence": "requested_thickness=2.0, bbox_min_span=26.0",
            }
        ],
        "blocker_taxonomy": [
            {
                "blocker_id": "keep_wall_thickness_near_2_0mm",
                "family_ids": ["general_geometry"],
                "feature_ids": ["feature.core_geometry"],
                "primary_feature_id": "feature.core_geometry",
                "evidence_source": "validation",
                "completeness_relevance": "core",
                "severity": "blocking",
                "recommended_repair_lane": "whole_part_rebuild",
            }
        ],
    }
    graph, _ = sync_domain_kernel_state_from_tool_result(
        graph,
        tool_name="validate_requirement",
        payload=later_validation,
        round_no=4,
    )

    digest = build_domain_kernel_digest(graph, max_nodes=32)
    active_instances = {
        item["instance_id"]: item
        for item in digest.get("active_feature_instances", [])
        if isinstance(item, dict) and isinstance(item.get("instance_id"), str)
    }

    overall_dims_instance = active_instances[
        "instance.general_geometry.create_a_snap_clamshell_enclosure_with_overall_dimensions_72mm_x_64mm_x_26mm"
    ]
    wall_instance = active_instances["instance.general_geometry.keep_wall_thickness_near_2_0mm"]

    assert overall_dims_instance["parameter_bindings"]["bbox"] == [72.0, 64.0, 26.0]
    assert overall_dims_instance["parameter_bindings"]["anchor_summary"]["bbox"] == [72.0, 64.0, 26.0]
    assert wall_instance["parameter_bindings"]["bbox"] == [72.0, 64.0, 26.0]

def test_domain_kernel_refreshes_non_blocking_general_geometry_instances_with_latest_geometry() -> None:
    requirements = {
        "description": (
            "Create a snap clamshell enclosure with overall dimensions 72mm x 64mm x 26mm "
            "and keep wall thickness near 2.0mm."
        )
    }
    graph = initialize_domain_kernel_state(requirements)

    graph.upsert_feature_instance(
        FeatureInstance(
            instance_id=(
                "instance.general_geometry."
                "create_a_snap_clamshell_enclosure_with_overall_dimensions_72mm_x_64mm_x_26mm"
            ),
            family_id="general_geometry",
            primary_feature_id="feature.core_geometry",
            label="core geometry",
            status="active",
            summary=(
                "create_a_snap_clamshell_enclosure_with_overall_dimensions_72mm_x_64mm_x_26mm"
            ),
            host_ids=["body.primary"],
            anchor_keys=[
                "anchor_summary",
                "bbox",
                "bbox_min",
                "bbox_max",
                "bbox_min_span",
                "bbox_max_span",
                "requested_dimensions",
                "requested_thickness",
            ],
            parameter_bindings={
                "requested_dimensions": [72.0, 64.0, 26.0],
                "requested_thickness": 2.0,
                "anchor_summary": {
                    "expected_bbox": [72.0, 64.0, 26.0],
                    "bbox": [72.0, 101.0, 26.0],
                    "bbox_min": [-36.0, -69.0, -13.0],
                    "bbox_max": [36.0, 32.0, 13.0],
                    "bbox_min_span": 26.0,
                    "bbox_max_span": 101.0,
                },
                "bbox": [72.0, 101.0, 26.0],
                "bbox_min": [-36.0, -69.0, -13.0],
                "bbox_max": [36.0, 32.0, 13.0],
                "bbox_min_span": 26.0,
                "bbox_max_span": 101.0,
            },
        )
    )

    refreshed_write = {
        "success": True,
        "session_state_persisted": True,
        "step_file": "model.step",
        "snapshot": {
            "geometry": {
                "solids": 4,
                "faces": 41,
                "edges": 86,
                "volume": 38911.71,
                "bbox": [72.0, 64.0, 26.0],
                "bbox_min": [-36.0, -32.0, 0.0],
                "bbox_max": [36.0, 32.0, 26.0],
            }
        },
    }
    graph, _ = sync_domain_kernel_state_from_tool_result(
        graph,
        tool_name="execute_build123d",
        payload=refreshed_write,
        round_no=1,
    )

    narrowed_validation = {
        "success": True,
        "is_complete": False,
        "blockers": ["keep_wall_thickness_near_2_0mm"],
        "blocker_taxonomy": [
            {
                "blocker_id": "keep_wall_thickness_near_2_0mm",
                "family_ids": ["general_geometry"],
                "feature_ids": ["feature.core_geometry"],
                "primary_feature_id": "feature.core_geometry",
                "evidence_source": "validation",
                "completeness_relevance": "core",
                "severity": "blocking",
                "recommended_repair_lane": "whole_part_rebuild",
            },
        ],
    }
    graph, _ = sync_domain_kernel_state_from_tool_result(
        graph,
        tool_name="validate_requirement",
        payload=narrowed_validation,
        round_no=2,
    )

    digest = build_domain_kernel_digest(graph, max_nodes=32)
    active_instances = {
        item["instance_id"]: item
        for item in digest.get("active_feature_instances", [])
        if isinstance(item, dict) and isinstance(item.get("instance_id"), str)
    }

    dimension_instance = active_instances[
        "instance.general_geometry.create_a_snap_clamshell_enclosure_with_overall_dimensions_72mm_x_64mm_x_26mm"
    ]
    wall_instance = active_instances["instance.general_geometry.keep_wall_thickness_near_2_0mm"]

    assert dimension_instance["parameter_bindings"]["bbox"] == [72.0, 64.0, 26.0]
    assert (
        dimension_instance["parameter_bindings"]["anchor_summary"]["bbox"]
        == [72.0, 64.0, 26.0]
    )
    assert wall_instance["parameter_bindings"]["bbox"] == [72.0, 64.0, 26.0]
