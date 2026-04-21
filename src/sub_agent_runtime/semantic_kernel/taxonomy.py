from __future__ import annotations

from typing import Any

from common.blocker_taxonomy import (
    classify_blocker_taxonomy_many,
    taxonomy_records_from_validation_payload,
)

from sub_agent_runtime.semantic_kernel.models import DomainKernelState

def _validation_family_status_hints(
    latest_validation: dict[str, Any] | None,
) -> dict[str, set[str]]:
    if not isinstance(latest_validation, dict):
        return {}
    hints: dict[str, set[str]] = {}
    for field_name in ("requirement_checks", "clause_interpretations"):
        items = latest_validation.get(field_name)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            family_id = str(item.get("family_binding") or item.get("family_id") or "").strip()
            if not family_id:
                continue
            status = str(item.get("status") or "").strip().lower()
            if status not in {"verified", "contradicted", "insufficient_evidence"}:
                continue
            hints.setdefault(family_id, set()).add(status)
    return hints

def _feature_node_family_ids(node_id: str) -> list[str]:
    normalized = str(node_id or "").strip()
    if not normalized.startswith("feature."):
        return []
    family_id = normalized.split(".", 1)[1].strip()
    if not family_id:
        return []
    return [family_id]

def _validation_uses_only_general_geometry_lane(
    *,
    blockers: list[str],
    taxonomy_by_blocker: dict[str, dict[str, Any]],
) -> bool:
    if not blockers:
        return True
    for blocker in blockers:
        record = taxonomy_by_blocker.get(blocker) or {}
        family_ids = [
            str(item).strip()
            for item in (record.get("family_ids") or [])
            if isinstance(item, str) and str(item).strip()
        ]
        feature_ids = [
            str(item).strip()
            for item in (record.get("feature_ids") or [])
            if isinstance(item, str) and str(item).strip()
        ]
        if any(family_id != "general_geometry" for family_id in family_ids):
            return False
        if any(feature_id != "feature.core_geometry" for feature_id in feature_ids):
            return False
    return True

def _validation_blockers(latest_validation: dict[str, Any] | None) -> list[str]:
    if not isinstance(latest_validation, dict):
        return []
    blockers = latest_validation.get("blockers")
    if isinstance(blockers, list):
        normalized = [item for item in blockers if isinstance(item, str)]
        if normalized:
            return normalized
    taxonomy = _validation_blocker_taxonomy(latest_validation)
    return [
        blocker_id
        for blocker_id in (
            item.get("blocker_id")
            for item in taxonomy
            if isinstance(item, dict)
        )
        if isinstance(blocker_id, str) and blocker_id.strip()
    ]

def _validation_blocker_taxonomy(
    latest_validation: dict[str, Any] | None,
    *,
    graph: DomainKernelState | None = None,
) -> list[dict[str, Any]]:
    taxonomy = [
        {
            "blocker_id": item.blocker_id,
            "normalized_blocker_id": item.normalized_blocker_id,
            "family_ids": item.family_ids,
            "feature_ids": item.feature_ids,
            "primary_feature_id": item.primary_feature_id,
            "recommended_repair_lane": item.recommended_repair_lane,
            "evidence_source": item.evidence_source,
            "completeness_relevance": item.completeness_relevance,
            "severity": item.severity,
        }
        for item in taxonomy_records_from_validation_payload(latest_validation)
        if str(getattr(item, "completeness_relevance", "") or "core").strip().lower()
        != "diagnostic"
    ]
    blockers = [
        blocker_id
        for blocker_id in (
            (latest_validation or {}).get("blockers")
            if isinstance((latest_validation or {}).get("blockers"), list)
            else []
        )
        if isinstance(blocker_id, str) and blocker_id.strip()
    ]
    if not blockers:
        blockers = [
            str(item.get("blocker_id")).strip()
            for item in taxonomy
            if isinstance(item, dict)
            and isinstance(item.get("blocker_id"), str)
            and str(item.get("blocker_id")).strip()
        ]
    return _contextualize_validation_blocker_taxonomy(
        blocker_taxonomy=taxonomy,
        blockers=blockers,
        graph=graph,
    )

def _contextualize_validation_blocker_taxonomy(
    *,
    blocker_taxonomy: list[dict[str, Any]],
    blockers: list[str],
    graph: DomainKernelState | None,
) -> list[dict[str, Any]]:
    if graph is None:
        return blocker_taxonomy
    requirement_feature_ids = {
        node.node_id
        for node in graph.nodes.values()
        if node.kind == "feature"
        and isinstance(node.node_id, str)
        and node.node_id.startswith("feature.")
    }
    if "feature.spherical_recess" not in requirement_feature_ids:
        return blocker_taxonomy
    taxonomy_by_blocker = {
        str(item.get("blocker_id")).strip(): item
        for item in blocker_taxonomy
        if isinstance(item, dict)
        and isinstance(item.get("blocker_id"), str)
        and str(item.get("blocker_id")).strip()
    }
    normalized_blockers = [
        blocker_id.strip()
        for blocker_id in blockers
        if isinstance(blocker_id, str) and blocker_id.strip()
    ]
    if not normalized_blockers:
        normalized_blockers = list(taxonomy_by_blocker.keys())
    contextualized: list[dict[str, Any]] = []
    for blocker_id in normalized_blockers:
        record = taxonomy_by_blocker.get(blocker_id)
        contextualized.append(
            _contextualize_blocker_taxonomy_record_for_graph(
                graph=graph,
                blocker_id=blocker_id,
                taxonomy_record=record,
            )
        )
    return contextualized or blocker_taxonomy

def _contextualize_blocker_taxonomy_record_for_graph(
    *,
    graph: DomainKernelState,
    blocker_id: str,
    taxonomy_record: dict[str, Any] | None,
) -> dict[str, Any]:
    requirement_feature_ids = {
        node.node_id
        for node in graph.nodes.values()
        if node.kind == "feature"
        and isinstance(node.node_id, str)
        and node.node_id.startswith("feature.")
    }
    requirement_family_ids = {
        feature_id.replace("feature.", "")
        for feature_id in requirement_feature_ids
    }
    blocker_id = str(blocker_id or "").strip()
    if not blocker_id:
        return dict(taxonomy_record or {})
    if not _should_prefer_spherical_recess_taxonomy(
        blocker_id=blocker_id,
        requirement_family_ids=requirement_family_ids,
    ):
        return dict(taxonomy_record or {})
    family_ids = ["spherical_recess"]
    if "pattern_distribution" in requirement_family_ids:
        family_ids.append("pattern_distribution")
    if blocker_id in {"feature_hole", "feature_local_anchor_alignment"}:
        family_ids.extend(["explicit_anchor_hole", "named_face_local_edit"])
    family_ids = list(dict.fromkeys(family_ids))
    feature_ids = _feature_ids_for_runtime_family_ids(family_ids)
    existing = dict(taxonomy_record or {})
    return {
        "blocker_id": blocker_id,
        "normalized_blocker_id": str(
            existing.get("normalized_blocker_id") or blocker_id
        ).strip()
        or blocker_id,
        "family_ids": family_ids,
        "feature_ids": feature_ids,
        "primary_feature_id": "feature.spherical_recess",
        "recommended_repair_lane": str(
            existing.get("recommended_repair_lane") or "code_repair"
        ).strip()
        or "code_repair",
        "evidence_source": str(existing.get("evidence_source") or "validation").strip()
        or "validation",
        "completeness_relevance": str(
            existing.get("completeness_relevance") or "core"
        ).strip()
        or "core",
        "severity": str(existing.get("severity") or "blocking").strip() or "blocking",
    }

def _should_prefer_spherical_recess_taxonomy(
    *,
    blocker_id: str,
    requirement_family_ids: set[str],
) -> bool:
    if "spherical_recess" not in requirement_family_ids:
        return False
    return blocker_id in {
        "feature_hole",
        "feature_local_anchor_alignment",
        "feature_profile_shape_alignment",
        "feature_pattern",
        "feature_pattern_seed",
        "feature_pattern_seed_alignment",
        "feature_spherical_recess",
    }

def _feature_ids_for_runtime_family_ids(family_ids: list[str]) -> list[str]:
    feature_ids: list[str] = []
    seen: set[str] = set()
    for family_id in family_ids:
        normalized = str(family_id or "").strip()
        if not normalized:
            continue
        candidates = [f"feature.{normalized}"]
        if normalized == "explicit_anchor_hole":
            candidates.append("feature.named_face_local_edit")
        for feature_id in candidates:
            if feature_id in seen:
                continue
            seen.add(feature_id)
            feature_ids.append(feature_id)
    return feature_ids or ["feature.core_geometry"]

def _feature_ids_from_taxonomy_record(taxonomy_record: dict[str, Any] | None) -> list[str]:
    if not isinstance(taxonomy_record, dict):
        return []
    return [
        feature_id
        for feature_id in (taxonomy_record.get("feature_ids") or [])
        if isinstance(feature_id, str) and feature_id.strip()
    ]

def _blocker_to_feature_ids(blocker: str) -> list[str]:
    if blocker == "feature_profile_shape_alignment":
        return ["feature.core_geometry"]
    taxonomy = classify_blocker_taxonomy_many([blocker])
    if taxonomy:
        feature_ids = [
            feature_id
            for feature_id in taxonomy[0].feature_ids
            if isinstance(feature_id, str) and feature_id.strip()
        ]
        if feature_ids:
            return feature_ids
    lowered = blocker.lower()
    if "polygon" in lowered or "triangle" in lowered or "hexagon" in lowered:
        return ["feature.regular_polygon_profile"]
    if "profile_shape" in lowered:
        return ["feature.core_geometry"]
    return ["feature.core_geometry"]

def _canonical_recommended_repair_lane(
    lane: str | None,
    *,
    family_ids: list[str] | None = None,
    primary_feature_id: str | None = None,
) -> str | None:
    lane_text = str(lane or "").strip()
    if not lane_text:
        return None
    if lane_text != "local_finish":
        return lane_text
    normalized_family_ids = [
        str(item).strip()
        for item in (family_ids or [])
        if isinstance(item, str) and str(item).strip()
    ]
    normalized_primary_feature_id = str(primary_feature_id or "").strip()
    if (
        "named_face_local_edit" in normalized_family_ids
        and normalized_primary_feature_id == "feature.named_face_local_edit"
    ):
        return "local_finish"
    if (
        not normalized_family_ids
        or "general_geometry" in normalized_family_ids
        or normalized_primary_feature_id == "feature.core_geometry"
    ):
        return "code_repair"
    return "probe_first"
