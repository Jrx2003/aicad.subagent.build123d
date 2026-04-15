from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class BlockerTaxonomy:
    blocker_id: str
    normalized_blocker_id: str
    family_ids: list[str]
    feature_ids: list[str]
    primary_feature_id: str
    evidence_source: str
    completeness_relevance: str
    severity: str
    recommended_repair_lane: str
    observation_tags: list[str] = field(default_factory=list)
    decision_hints: list[str] = field(default_factory=list)


_FAMILY_SPECS: dict[str, dict[str, object]] = {
    "core_geometry": {
        "feature_ids": ["feature.core_geometry"],
        "validation_check_ids": set(),
        "recommended_tools": ["query_geometry", "query_kernel_state"],
        "repair_lane": "probe_first",
    },
    "holes": {
        "feature_ids": ["feature.explicit_anchor_hole", "feature.named_face_local_edit"],
        "validation_check_ids": {
            "feature_hole",
            "feature_countersink",
            "feature_hole_position_alignment",
            "feature_local_anchor_alignment",
        },
        "recommended_tools": ["query_topology", "query_kernel_state"],
        "repair_lane": "probe_first",
    },
    "slots": {
        "feature_ids": ["feature.nested_hollow_section", "feature.named_face_local_edit"],
        "validation_check_ids": {
            "feature_notch_or_profile_cut",
            "feature_notch_profile_alignment",
            "feature_cylindrical_slot_alignment",
        },
        "recommended_tools": ["query_feature_probes", "query_kernel_state"],
        "repair_lane": "probe_first",
    },
    "grooves": {
        "feature_ids": ["feature.annular_groove", "feature.named_face_local_edit"],
        "validation_check_ids": {
            "feature_annular_groove",
            "feature_revolved_groove_result",
            "feature_target_face_subtractive_merge",
        },
        "recommended_tools": ["query_geometry", "query_kernel_state"],
        "repair_lane": "probe_first",
    },
    "annular_groove": {
        "feature_ids": ["feature.annular_groove", "feature.axisymmetric_profile"],
        "validation_check_ids": {
            "feature_annular_groove",
            "feature_revolved_groove_setup",
            "feature_revolved_groove_alignment",
            "feature_revolved_groove_result",
        },
        "recommended_tools": ["query_geometry", "execute_build123d_probe"],
        "repair_lane": "code_repair",
    },
    "nested_hollow_section": {
        "feature_ids": ["feature.nested_hollow_section"],
        "validation_check_ids": {
            "feature_inner_void",
            "feature_inner_void_cutout",
            "feature_notch_or_profile_cut",
        },
        "recommended_tools": ["query_geometry", "execute_build123d"],
        "repair_lane": "code_repair",
    },
    "explicit_anchor_hole": {
        "feature_ids": ["feature.explicit_anchor_hole", "feature.named_face_local_edit"],
        "validation_check_ids": {
            "feature_hole",
            "feature_countersink",
            "feature_hole_position_alignment",
            "feature_local_anchor_alignment",
        },
        "recommended_tools": ["query_topology", "execute_build123d"],
        "repair_lane": "code_repair",
    },
    "spherical_recess": {
        "feature_ids": ["feature.spherical_recess", "feature.pattern_distribution"],
        "validation_check_ids": {
            "feature_spherical_recess",
            "feature_profile_shape_alignment",
        },
        "recommended_tools": ["query_geometry", "execute_build123d_probe"],
        "repair_lane": "code_repair",
    },
    "pattern_distribution": {
        "feature_ids": ["feature.pattern_distribution"],
        "validation_check_ids": {
            "feature_pattern",
            "feature_pattern_seed",
            "feature_pattern_seed_alignment",
        },
        "recommended_tools": ["query_geometry", "execute_build123d_probe"],
        "repair_lane": "code_repair",
    },
    "orthogonal_union": {
        "feature_ids": ["feature.orthogonal_union"],
        "validation_check_ids": {
            "feature_multi_plane_additive_union",
            "feature_multi_plane_additive_specs",
            "feature_merged_body_result",
        },
        "recommended_tools": ["query_geometry", "execute_build123d"],
        "repair_lane": "code_repair",
    },
    "axisymmetric_profile": {
        "feature_ids": ["feature.axisymmetric_profile"],
        "validation_check_ids": {
            "feature_revolve_profile_setup",
            "feature_revolve_profile_shape",
            "feature_named_axis_axisymmetric_pose",
            "feature_named_plane_positive_extrude_span",
            "feature_half_shell_profile_envelope",
            "feature_merged_body_result",
        },
        "recommended_tools": ["query_kernel_state", "execute_build123d_probe"],
        "repair_lane": "code_repair",
    },
    "named_face_local_edit": {
        "feature_ids": ["feature.named_face_local_edit"],
        "validation_check_ids": {
            "feature_target_face_edit",
            "feature_target_face_subtractive_merge",
            "feature_target_face_additive_merge",
            "feature_fillet",
            "feature_chamfer",
        },
        "recommended_tools": ["query_topology", "apply_cad_action"],
        "repair_lane": "local_finish",
    },
    "path_sweep": {
        "feature_ids": ["feature.core_geometry"],
        "validation_check_ids": {
            "feature_path_sweep_rail",
            "feature_path_sweep_profile",
            "feature_path_sweep_frame",
            "feature_path_sweep_result",
            "path_disconnected",
            "missing_profile",
        },
        "recommended_tools": ["query_kernel_state", "execute_build123d_probe"],
        "repair_lane": "probe_first",
    },
}

_FAMILY_ALIASES = {
    "nested_profile_hollow_section": "nested_hollow_section",
    "hole": "holes",
    "recess": "named_face_local_edit",
    "pocket": "named_face_local_edit",
}

_SPECIAL_MULTI_FAMILY_BLOCKERS: dict[str, list[str]] = {
    "feature_annular_groove": ["annular_groove", "axisymmetric_profile"],
    "feature_revolved_groove_setup": ["annular_groove", "axisymmetric_profile"],
    "feature_revolved_groove_alignment": ["annular_groove", "axisymmetric_profile"],
    "feature_revolved_groove_result": ["annular_groove", "axisymmetric_profile"],
    "feature_hole": ["explicit_anchor_hole", "named_face_local_edit"],
    "feature_countersink": ["explicit_anchor_hole", "named_face_local_edit"],
    "feature_hole_position_alignment": ["explicit_anchor_hole", "named_face_local_edit"],
    "feature_local_anchor_alignment": ["explicit_anchor_hole", "named_face_local_edit"],
    "feature_pattern": ["pattern_distribution", "spherical_recess"],
    "feature_pattern_seed": ["pattern_distribution", "spherical_recess"],
    "feature_pattern_seed_alignment": ["pattern_distribution", "spherical_recess"],
    "feature_merged_body_result": ["orthogonal_union", "axisymmetric_profile"],
}


def normalize_probe_family_id(family_id: str) -> str:
    normalized = str(family_id or "").strip()
    if not normalized:
        return ""
    return _FAMILY_ALIASES.get(normalized, normalized)


def normalize_probe_family_ids(family_ids: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for raw_family_id in family_ids:
        normalized = normalize_probe_family_id(raw_family_id)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def probe_check_ids_for_family(family_id: str) -> tuple[str, ...]:
    normalized = normalize_probe_family_id(family_id)
    spec = _FAMILY_SPECS.get(normalized)
    if spec is None:
        return ()
    return tuple(sorted(spec.get("validation_check_ids", set())))


def recommended_probe_tools_for_family(family_id: str) -> list[str]:
    normalized = normalize_probe_family_id(family_id)
    spec = _FAMILY_SPECS.get(normalized)
    if spec is None:
        return ["query_snapshot", "query_geometry"]
    return [str(item) for item in spec.get("recommended_tools", []) if isinstance(item, str)]


def classify_blocker_taxonomy(
    blocker_id: str,
    *,
    evidence_source: str = "validation",
    completeness_relevance: str = "core",
) -> BlockerTaxonomy:
    normalized_blocker_id = str(blocker_id or "").strip()
    family_ids = _family_ids_for_blocker(normalized_blocker_id)
    feature_ids = _feature_ids_for_families(family_ids)
    primary_feature_id = feature_ids[0] if feature_ids else "feature.core_geometry"
    observation_tags = _observation_tags_for_blocker(
        normalized_blocker_id,
        family_ids=family_ids,
    )
    decision_hints = _decision_hints_for_blocker(
        normalized_blocker_id,
        family_ids=family_ids,
        observation_tags=observation_tags,
    )
    recommended_repair_lane = _recommended_repair_lane(normalized_blocker_id, family_ids)
    return BlockerTaxonomy(
        blocker_id=blocker_id,
        normalized_blocker_id=normalized_blocker_id,
        family_ids=family_ids,
        feature_ids=feature_ids,
        primary_feature_id=primary_feature_id,
        evidence_source=evidence_source,
        completeness_relevance=completeness_relevance,
        severity=_severity_for_completeness_relevance(completeness_relevance),
        recommended_repair_lane=recommended_repair_lane,
        observation_tags=observation_tags,
        decision_hints=decision_hints,
    )


def classify_blocker_taxonomy_many(
    blocker_ids: list[str],
    *,
    evidence_source: str = "validation",
    completeness_relevance: str = "core",
) -> list[BlockerTaxonomy]:
    return [
        classify_blocker_taxonomy(
            blocker_id,
            evidence_source=evidence_source,
            completeness_relevance=completeness_relevance,
        )
        for blocker_id in blocker_ids
        if isinstance(blocker_id, str) and blocker_id.strip()
    ]


def taxonomy_records_from_validation_payload(
    latest_validation: dict[str, Any] | None,
) -> list[BlockerTaxonomy]:
    if not isinstance(latest_validation, dict):
        return []
    raw_taxonomy = latest_validation.get("blocker_taxonomy")
    normalized: list[BlockerTaxonomy] = []
    if isinstance(raw_taxonomy, list):
        for item in raw_taxonomy:
            if not isinstance(item, dict):
                continue
            blocker_id = str(item.get("blocker_id") or "").strip()
            if not blocker_id:
                continue
            fallback_record = classify_blocker_taxonomy(
                blocker_id,
                evidence_source=str(item.get("evidence_source") or "validation").strip()
                or "validation",
                completeness_relevance=str(
                    item.get("completeness_relevance") or "core"
                ).strip()
                or "core",
            )
            family_ids = normalize_probe_family_ids(
                [
                    str(family_id).strip()
                    for family_id in (item.get("family_ids") or [])
                    if isinstance(family_id, str) and str(family_id).strip()
                ]
            )
            feature_ids = [
                str(feature_id).strip()
                for feature_id in (item.get("feature_ids") or [])
                if isinstance(feature_id, str) and str(feature_id).strip()
            ]
            if (
                (not family_ids or family_ids == ["general_geometry"])
                and fallback_record.family_ids
                and any(family_id != "general_geometry" for family_id in fallback_record.family_ids)
            ):
                family_ids = list(fallback_record.family_ids)
                feature_ids = list(fallback_record.feature_ids)
            using_fallback_specific_family = (
                family_ids == list(fallback_record.family_ids)
                and any(family_id != "general_geometry" for family_id in family_ids)
            )
            observation_tags = _normalize_str_list(item.get("observation_tags"))
            decision_hints = _normalize_str_list(item.get("decision_hints"))
            incoming_lane = str(item.get("recommended_repair_lane") or "").strip()
            recommended_repair_lane = incoming_lane or _recommended_repair_lane(
                blocker_id,
                family_ids,
            )
            if using_fallback_specific_family:
                recommended_repair_lane = fallback_record.recommended_repair_lane
            primary_feature_id = str(item.get("primary_feature_id") or "").strip()
            if not primary_feature_id and feature_ids:
                primary_feature_id = feature_ids[0]
            if using_fallback_specific_family and (
                not primary_feature_id or primary_feature_id == "feature.core_geometry"
            ):
                primary_feature_id = fallback_record.primary_feature_id
            normalized.append(
                BlockerTaxonomy(
                    blocker_id=blocker_id,
                    normalized_blocker_id=str(
                        item.get("normalized_blocker_id") or blocker_id
                    ).strip()
                    or blocker_id,
                    family_ids=family_ids or _family_ids_for_blocker(blocker_id),
                    feature_ids=feature_ids or _feature_ids_for_families(family_ids),
                    primary_feature_id=primary_feature_id or "feature.core_geometry",
                    evidence_source=str(item.get("evidence_source") or "validation").strip()
                    or "validation",
                    completeness_relevance=str(
                        item.get("completeness_relevance") or "core"
                    ).strip()
                    or "core",
                    severity=str(
                        item.get("severity")
                        or _severity_for_completeness_relevance(
                            str(item.get("completeness_relevance") or "core").strip()
                            or "core"
                        )
                    ).strip()
                    or _severity_for_completeness_relevance(
                        str(item.get("completeness_relevance") or "core").strip()
                        or "core"
                    ),
                    recommended_repair_lane=recommended_repair_lane,
                    observation_tags=observation_tags,
                    decision_hints=decision_hints,
                )
            )
    if normalized:
        return normalized
    blockers = latest_validation.get("blockers")
    blocker_ids = [item for item in blockers if isinstance(item, str)] if isinstance(blockers, list) else []
    if blocker_ids:
        return classify_blocker_taxonomy_many(
            blocker_ids,
            evidence_source="validation",
            completeness_relevance="core",
        )
    core_check_ids = _extract_failed_check_ids(latest_validation.get("core_checks"))
    diagnostic_check_ids = _extract_failed_check_ids(latest_validation.get("diagnostic_checks"))
    combined: list[BlockerTaxonomy] = []
    combined.extend(
        classify_blocker_taxonomy_many(
            core_check_ids,
            evidence_source="validation",
            completeness_relevance="core",
        )
    )
    combined.extend(
        classify_blocker_taxonomy_many(
            diagnostic_check_ids,
            evidence_source="validation",
            completeness_relevance="diagnostic",
        )
    )
    deduped: list[BlockerTaxonomy] = []
    seen: set[tuple[str, str]] = set()
    for record in combined:
        key = (record.blocker_id, record.completeness_relevance)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def taxonomy_family_ids_from_validation_payload(
    latest_validation: dict[str, Any] | None,
) -> list[str]:
    family_ids: list[str] = []
    seen: set[str] = set()
    for record in taxonomy_records_from_validation_payload(latest_validation):
        for family_id in record.family_ids:
            normalized = normalize_probe_family_id(family_id)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            family_ids.append(normalized)
    return family_ids


def taxonomy_repair_lanes_from_validation_payload(
    latest_validation: dict[str, Any] | None,
) -> list[str]:
    lanes: list[str] = []
    seen: set[str] = set()
    for record in taxonomy_records_from_validation_payload(latest_validation):
        lane = str(record.recommended_repair_lane or "").strip()
        if not lane or lane in seen:
            continue
        seen.add(lane)
        lanes.append(lane)
    return lanes


def _extract_failed_check_ids(raw_checks: Any) -> list[str]:
    failed: list[str] = []
    if not isinstance(raw_checks, list):
        return failed
    for raw_check in raw_checks:
        if not isinstance(raw_check, dict):
            continue
        status = str(raw_check.get("status") or "").strip().lower()
        if status not in {"fail", "failed", "error", "blocked"}:
            continue
        check_id = raw_check.get("check_id") or raw_check.get("name") or raw_check.get("code")
        if isinstance(check_id, str) and check_id.strip():
            failed.append(check_id.strip())
    return failed


def _family_ids_for_blocker(blocker_id: str) -> list[str]:
    if blocker_id == "feature_profile_shape_alignment":
        return ["general_geometry"]
    if blocker_id in _SPECIAL_MULTI_FAMILY_BLOCKERS:
        return list(_SPECIAL_MULTI_FAMILY_BLOCKERS[blocker_id])
    matched: list[str] = []
    for family_id, spec in _FAMILY_SPECS.items():
        validation_ids = spec.get("validation_check_ids", set())
        if blocker_id in validation_ids:
            matched.append(family_id)
    if matched:
        return matched
    lowered = blocker_id.lower()
    if _looks_like_hole_wizard_or_revolved_hole_clause(lowered):
        return ["explicit_anchor_hole", "named_face_local_edit"]
    if "annular" in lowered or "groove" in lowered or "revolve" in lowered:
        return ["annular_groove", "axisymmetric_profile"]
    if "inner_void" in lowered or "hollow" in lowered or "section" in lowered:
        return ["nested_hollow_section"]
    if "pattern" in lowered:
        return ["pattern_distribution", "spherical_recess"]
    if _looks_like_countersink_or_explicit_hole_dimension_blocker(lowered):
        return ["explicit_anchor_hole", "named_face_local_edit"]
    if "hole" in lowered or "anchor" in lowered:
        return ["explicit_anchor_hole", "named_face_local_edit"]
    if "union" in lowered or "orthogonal" in lowered or "merged_body" in lowered:
        return ["orthogonal_union"]
    if "fillet" in lowered or "chamfer" in lowered or "face" in lowered:
        return ["named_face_local_edit"]
    return ["general_geometry"]


def _looks_like_countersink_or_explicit_hole_dimension_blocker(lowered_blocker_id: str) -> bool:
    lowered = str(lowered_blocker_id or "").strip().lower()
    if not lowered:
        return False
    if lowered.startswith(("head_diameter_", "cone_angle_", "through_hole_diameter_")):
        return True
    if "countersink" in lowered or "counter_sink" in lowered:
        return True
    if "conical_recess" in lowered or "conical recess" in lowered:
        return True
    return False


def _looks_like_hole_wizard_or_revolved_hole_clause(lowered_blocker_id: str) -> bool:
    lowered = str(lowered_blocker_id or "").strip().lower()
    if not lowered:
        return False
    if "hole_wizard" in lowered or "hole wizard" in lowered:
        return True
    if "revolved_cut_tool" in lowered or "revolved cut tool" in lowered:
        return "hole" in lowered or "countersink" in lowered or "conical_recess" in lowered
    return False


def _feature_ids_for_families(family_ids: list[str]) -> list[str]:
    feature_ids: list[str] = []
    seen: set[str] = set()
    for family_id in family_ids:
        spec = _FAMILY_SPECS.get(family_id)
        if not isinstance(spec, dict):
            continue
        for feature_id in spec.get("feature_ids", []):
            normalized = str(feature_id or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            feature_ids.append(normalized)
    if not feature_ids:
        return ["feature.core_geometry"]
    return feature_ids


def _recommended_repair_lane(
    blocker_id: str,
    family_ids: list[str],
) -> str:
    if blocker_id in {"feature_fillet", "feature_chamfer"}:
        return "local_finish"
    if "path_sweep" in family_ids:
        return "probe_first"
    for family_id in family_ids:
        spec = _FAMILY_SPECS.get(family_id)
        if isinstance(spec, dict):
            lane = str(spec.get("repair_lane") or "").strip()
            if lane:
                return lane
    return "code_repair"


def _observation_tags_for_blocker(
    blocker_id: str,
    *,
    family_ids: list[str],
) -> list[str]:
    lowered = str(blocker_id or "").strip().lower()
    tags: list[str] = []
    if lowered == "feature_path_sweep_frame" or (
        lowered.endswith("_frame") and "path_sweep" in family_ids
    ):
        tags.extend(["insufficient_evidence", "path_frame_style"])
    return _normalize_str_list(tags)


def _decision_hints_for_blocker(
    blocker_id: str,
    *,
    family_ids: list[str],
    observation_tags: list[str],
) -> list[str]:
    hints: list[str] = []
    if "path_frame_style" in observation_tags or (
        str(blocker_id or "").strip().lower() == "feature_path_sweep_frame"
        and "path_sweep" in family_ids
    ):
        hints.extend(
            [
                "query_feature_probes",
                "refresh path-frame semantics before attempting more probe code",
            ]
        )
    return _normalize_str_list(hints)


def _normalize_str_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _severity_for_completeness_relevance(completeness_relevance: str) -> str:
    normalized = str(completeness_relevance or "").strip().lower()
    if normalized == "core":
        return "blocking"
    if normalized == "diagnostic":
        return "diagnostic"
    return "unknown"
