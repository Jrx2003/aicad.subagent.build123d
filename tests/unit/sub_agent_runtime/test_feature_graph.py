from sub_agent_runtime.feature_graph import (
    FeatureInstance,
    _explicit_anchor_hole_recipe_packet,
    build_domain_kernel_digest,
    initialize_domain_kernel_state,
    sync_domain_kernel_state,
    sync_domain_kernel_state_from_tool_result,
)


def test_domain_kernel_resolves_stale_explicit_anchor_instances_after_new_validation() -> None:
    requirements = {
        "description": (
            "Create a rectangular plate and place four countersunk holes at coordinates "
            "(25,15), (25,45), (75,15), (75,45)."
        )
    }
    graph = initialize_domain_kernel_state(requirements)

    graph = sync_domain_kernel_state(
        graph,
        requirements=requirements,
        latest_write_payload=None,
        latest_validation={
            "is_complete": False,
            "blockers": ["feature_hole_position_alignment"],
            "blocker_taxonomy": [
                {
                    "blocker_id": "feature_hole_position_alignment",
                    "normalized_blocker_id": "feature_hole_position_alignment",
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
                }
            ],
        },
        previous_error=None,
        reason="test:round_01_validation",
    )

    graph = sync_domain_kernel_state(
        graph,
        requirements=requirements,
        latest_write_payload=None,
        latest_validation={
            "is_complete": False,
            "blockers": ["head_diameter_12_0_millimeters"],
            "blocker_taxonomy": [
                {
                    "blocker_id": "head_diameter_12_0_millimeters",
                    "normalized_blocker_id": "head_diameter_12_0_millimeters",
                    "family_ids": ["general_geometry"],
                    "feature_ids": ["feature.core_geometry"],
                    "primary_feature_id": "feature.core_geometry",
                    "evidence_source": "validation",
                    "completeness_relevance": "core",
                    "severity": "blocking",
                    "recommended_repair_lane": "code_repair",
                }
            ],
        },
        previous_error=None,
        reason="test:round_02_validation",
    )

    digest = build_domain_kernel_digest(graph, max_nodes=32)
    active_instance_ids = {
        item["instance_id"]
        for item in digest.get("active_feature_instances", [])
        if isinstance(item, dict) and isinstance(item.get("instance_id"), str)
    }
    latest_patch_families = {
        item["family_id"]
        for item in digest.get("latest_patch_feature_instances", [])
        if isinstance(item, dict) and isinstance(item.get("family_id"), str)
    }

    assert "blocker.head_diameter_12_0_millimeters" in digest.get("blocked_node_ids", [])
    assert (
        "blocker.feature_hole_position_alignment"
        not in digest.get("blocked_node_ids", [])
    )
    assert (
        "instance.explicit_anchor_hole.feature_hole_position_alignment"
        not in active_instance_ids
    )
    assert latest_patch_families == {"explicit_anchor_hole"}


def test_domain_kernel_resolves_local_finish_placeholder_when_only_explicit_anchor_evidence_gap_remains() -> None:
    requirements = {
        "description": (
            "Create a rectangular electronics bracket sized 62mm x 40mm x 14mm with a top "
            "pocket, two mounting holes, and a front thumb notch. Finish the model with local "
            "edge fillets around the top opening and a countersink on the mounting face so that "
            "a topology-aware local finishing pass is useful."
        )
    }
    graph = initialize_domain_kernel_state(requirements)

    graph = sync_domain_kernel_state(
        graph,
        requirements=requirements,
        latest_write_payload=None,
        latest_validation={
            "is_complete": False,
            "blockers": ["feature_countersink"],
            "blocker_taxonomy": [
                {
                    "blocker_id": "feature_countersink",
                    "normalized_blocker_id": "feature_countersink",
                    "family_ids": ["explicit_anchor_hole", "named_face_local_edit"],
                    "feature_ids": [
                        "feature.explicit_anchor_hole",
                        "feature.named_face_local_edit",
                    ],
                    "primary_feature_id": "feature.explicit_anchor_hole",
                    "evidence_source": "validation",
                    "completeness_relevance": "core",
                    "severity": "blocking",
                    "recommended_repair_lane": "local_finish",
                }
            ],
        },
        previous_error=None,
        reason="test:round_01_validation",
    )

    graph = sync_domain_kernel_state(
        graph,
        requirements=requirements,
        latest_write_payload=None,
        latest_validation={
            "is_complete": False,
            "blockers": [],
            "insufficient_evidence": True,
            "clause_interpretations": [
                {
                    "status": "verified",
                    "family_binding": "explicit_anchor_hole",
                    "evidence": (
                        "found material hole/recess action; countersink_action=True, "
                        "hole_feature=True"
                    ),
                },
                {
                    "status": "insufficient_evidence",
                    "family_binding": "explicit_anchor_hole",
                    "evidence": (
                        "Feature-level evidence exists, but count or placement is still "
                        "under-specified."
                    ),
                },
            ],
        },
        previous_error=None,
        reason="test:round_02_validation",
    )

    digest = build_domain_kernel_digest(graph, max_nodes=32)
    active_instance_ids = {
        item["instance_id"]
        for item in digest.get("active_feature_instances", [])
        if isinstance(item, dict) and isinstance(item.get("instance_id"), str)
    }
    node_statuses = {
        node.node_id: node.status
        for node in graph.nodes.values()
        if node.kind == "feature"
    }

    assert "instance.named_face_local_edit.primary" not in active_instance_ids
    assert "feature.named_face_local_edit" not in digest.get("unsatisfied_feature_ids", [])
    assert "feature.explicit_anchor_hole" in digest.get("unsatisfied_feature_ids", [])
    assert node_statuses["feature.named_face_local_edit"] == "resolved"
    assert node_statuses["feature.explicit_anchor_hole"] == "active"


def test_validation_anchor_summary_does_not_copy_hole_layout_signals_into_named_face_local_edit() -> None:
    requirements = {
        "description": (
            "Create a bracket and add a centered rounded rectangle recess on the front face."
        )
    }
    graph = initialize_domain_kernel_state(requirements)

    validation_payload = {
        "success": True,
        "is_complete": False,
        "blockers": ["feature_local_anchor_count_alignment"],
        "core_checks": [
            {
                "check_id": "feature_local_anchor_count_alignment",
                "status": "fail",
                "blocking": True,
                "evidence": (
                    "required_center_count=1, realized_center_count=2, "
                    "realized_centers=[[-12.5, 0.0], [12.5, 0.0]], "
                    "countersink_action=True, hole_feature=True"
                ),
            }
        ],
        "blocker_taxonomy": [
            {
                "blocker_id": "feature_local_anchor_count_alignment",
                "family_ids": ["explicit_anchor_hole", "named_face_local_edit"],
                "feature_ids": [
                    "feature.explicit_anchor_hole",
                    "feature.named_face_local_edit",
                ],
                "primary_feature_id": "feature.explicit_anchor_hole",
                "evidence_source": "validation",
                "completeness_relevance": "core",
                "severity": "blocking",
                "recommended_repair_lane": "local_finish",
            }
        ],
    }

    graph, meta = sync_domain_kernel_state_from_tool_result(
        graph,
        tool_name="validate_requirement",
        payload=validation_payload,
        round_no=1,
    )

    binding = graph.bindings[str(meta["binding_id"])]
    feature_anchor_summary = binding.feature_anchor_summary
    signal_values_by_family = feature_anchor_summary["signal_values_by_family"]

    assert signal_values_by_family["explicit_anchor_hole"]["realized_centers"] == [
        [-12.5, 0.0],
        [12.5, 0.0],
    ]
    assert (
        signal_values_by_family["explicit_anchor_hole"]["expected_local_center_count"]
        == 1
    )
    named_face_signals = signal_values_by_family.get("named_face_local_edit", {})
    assert "realized_centers" not in named_face_signals
    assert "required_center_count" not in named_face_signals
    assert "countersink_action" not in named_face_signals
    assert "hole_feature" not in named_face_signals


def test_query_feature_probe_anchor_summary_preserves_count_only_explicit_anchor_metadata() -> None:
    requirements = {
        "description": (
            "Create a rectangular electronics bracket sized 62mm x 40mm x 14mm with a top "
            "pocket, two mounting holes, and a front thumb notch."
        )
    }
    graph = initialize_domain_kernel_state(requirements)

    probe_payload = {
        "success": True,
        "detected_families": ["explicit_anchor_hole"],
        "probes": [
            {
                "family": "explicit_anchor_hole",
                "success": False,
                "signals": {
                    "bbox": [62.0, 40.0, 14.0],
                    "realized_centers": [],
                },
                "anchor_summary": {
                    "bbox": [62.0, 40.0, 14.0],
                    "bbox_min": [-31.0, -20.0, -7.0],
                    "bbox_max": [31.0, 20.0, 7.0],
                    "expected_local_center_count": 2,
                    "realized_local_center_count": 0,
                },
                "blockers": ["feature_hole", "feature_countersink"],
                "family_binding": "explicit_anchor_hole",
                "required_evidence_kinds": ["geometry", "topology"],
            }
        ],
    }

    graph, meta = sync_domain_kernel_state_from_tool_result(
        graph,
        tool_name="query_feature_probes",
        payload=probe_payload,
        round_no=1,
    )

    binding = graph.bindings[str(meta["binding_id"])]
    explicit_signals = binding.feature_anchor_summary["signal_values_by_family"][
        "explicit_anchor_hole"
    ]

    assert explicit_signals["expected_local_center_count"] == 2
    assert explicit_signals["realized_local_center_count"] == 0
    assert explicit_signals["bbox"] == [62.0, 40.0, 14.0]


def test_domain_kernel_canonicalizes_general_geometry_local_finish_lane_to_code_repair() -> None:
    requirements = {
        "description": (
            "Create a two-part rounded clamshell storage enclosure with overall dimensions "
            "78mm x 56mm x 32mm."
        )
    }
    graph = initialize_domain_kernel_state(requirements)

    validation_payload = {
        "success": True,
        "is_complete": False,
        "blockers": ["keep_wall_thickness_near_2_4mm"],
        "blocker_taxonomy": [
            {
                "blocker_id": "keep_wall_thickness_near_2_4mm",
                "family_ids": ["general_geometry"],
                "feature_ids": ["feature.core_geometry"],
                "primary_feature_id": "feature.core_geometry",
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
    blocker_node = graph.nodes["blocker.keep_wall_thickness_near_2_4mm"]

    assert digest["latest_binding_repair_lane"] == "code_repair"
    assert blocker_node.attributes["recommended_repair_lane"] == "code_repair"


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
