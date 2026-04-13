from common.blocker_taxonomy import normalize_probe_family_ids, probe_check_ids_for_family


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
