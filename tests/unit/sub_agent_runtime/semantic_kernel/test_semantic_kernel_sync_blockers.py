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
