from common.blocker_taxonomy import (
    classify_blocker_taxonomy,
    normalize_probe_family_ids,
    probe_check_ids_for_family,
    taxonomy_records_from_validation_payload,
)


def test_probe_family_aliases_normalize_intuitive_hole_and_recess_labels() -> None:
    assert normalize_probe_family_ids(["hole", "recess", "pattern_distribution"]) == [
        "holes",
        "named_face_local_edit",
        "pattern_distribution",
    ]


def test_recess_alias_uses_face_local_edit_checks_including_subtractive_merge() -> None:
    check_ids = set(probe_check_ids_for_family("recess"))

    assert "feature_target_face_edit" in check_ids
    assert "feature_target_face_subtractive_merge" in check_ids


def test_countersink_dimension_blockers_classify_as_explicit_anchor_hole() -> None:
    record = classify_blocker_taxonomy("head_diameter_12_0_millimeters")

    assert record.family_ids == ["explicit_anchor_hole", "named_face_local_edit"]
    assert record.primary_feature_id == "feature.explicit_anchor_hole"


def test_validation_taxonomy_overrides_generic_family_for_countersink_dimension_blocker() -> None:
    records = taxonomy_records_from_validation_payload(
        {
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
            ]
        }
    )

    assert len(records) == 1
    assert records[0].family_ids == ["explicit_anchor_hole", "named_face_local_edit"]
    assert records[0].feature_ids == [
        "feature.explicit_anchor_hole",
        "feature.named_face_local_edit",
    ]
    assert records[0].primary_feature_id == "feature.explicit_anchor_hole"


def test_hole_wizard_revolved_cut_clause_stays_in_explicit_anchor_hole_family() -> None:
    record = classify_blocker_taxonomy("activate_the_hole_wizard_or_the_revolved_cut_tool")

    assert record.family_ids == ["explicit_anchor_hole", "named_face_local_edit"]
    assert record.primary_feature_id == "feature.explicit_anchor_hole"
