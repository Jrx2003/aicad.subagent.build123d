from sub_agent_runtime.feature_graph import (
    build_domain_kernel_digest,
    initialize_domain_kernel_state,
    sync_domain_kernel_state,
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
