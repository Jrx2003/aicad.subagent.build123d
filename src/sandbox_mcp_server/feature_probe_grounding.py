from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class FeatureProbeGroundingRule:
    family: str
    family_binding: str | None
    required_evidence_kinds: tuple[str, ...]


_FEATURE_PROBE_GROUNDING_RULES: dict[str, FeatureProbeGroundingRule] = {
    "explicit_anchor_hole": FeatureProbeGroundingRule(
        family="explicit_anchor_hole",
        family_binding="explicit_anchor_hole",
        required_evidence_kinds=("geometry", "topology"),
    ),
    "spherical_recess": FeatureProbeGroundingRule(
        family="spherical_recess",
        family_binding="spherical_recess",
        required_evidence_kinds=("geometry", "topology"),
    ),
    "half_shell": FeatureProbeGroundingRule(
        family="half_shell",
        family_binding="half_shell",
        required_evidence_kinds=("geometry", "topology"),
    ),
    "nested_hollow_section": FeatureProbeGroundingRule(
        family="nested_hollow_section",
        family_binding="nested_hollow_section",
        required_evidence_kinds=("geometry", "topology"),
    ),
    "slots": FeatureProbeGroundingRule(
        family="slots",
        family_binding="slots",
        required_evidence_kinds=("geometry", "topology"),
    ),
    "named_face_local_edit": FeatureProbeGroundingRule(
        family="named_face_local_edit",
        family_binding="named_face_local_edit",
        required_evidence_kinds=("topology",),
    ),
    "core_geometry": FeatureProbeGroundingRule(
        family="core_geometry",
        family_binding="core_geometry",
        required_evidence_kinds=("geometry",),
    ),
    "general_geometry": FeatureProbeGroundingRule(
        family="general_geometry",
        family_binding="general_geometry",
        required_evidence_kinds=("geometry",),
    ),
}


def build_feature_probe_grounding(
    *,
    family: str,
    signals: dict[str, Any],
    blockers: list[str],
) -> dict[str, Any]:
    rule = _FEATURE_PROBE_GROUNDING_RULES.get(family)
    required_evidence_kinds = list(rule.required_evidence_kinds) if rule else []
    family_binding = rule.family_binding if rule else family
    anchor_summary = _build_anchor_summary(family=family, signals=signals)
    grounding_blockers = _build_grounding_blockers(
        family=family,
        blockers=blockers,
        signals=signals,
    )
    return {
        "family_binding": family_binding,
        "required_evidence_kinds": required_evidence_kinds,
        "anchor_summary": anchor_summary,
        "grounding_blockers": grounding_blockers,
    }


def recommended_next_tools_for_feature_probe_grounding(
    *,
    base_tools: list[str],
    required_evidence_kinds: list[str],
    anchor_summary: dict[str, Any],
    grounding_blockers: list[str],
) -> list[str]:
    recommended: list[str] = []

    def _append(raw_tool_name: Any) -> None:
        tool_name = str(raw_tool_name or "").strip()
        if tool_name and tool_name not in recommended:
            recommended.append(tool_name)

    if _grounding_prefers_topology_host_selection(
        required_evidence_kinds=required_evidence_kinds,
        anchor_summary=anchor_summary,
        grounding_blockers=grounding_blockers,
    ):
        _append("query_topology")

    for tool_name in base_tools:
        _append(tool_name)
    return recommended


def _build_anchor_summary(*, family: str, signals: dict[str, Any]) -> dict[str, Any]:
    summary = _build_common_anchor_summary(signals)
    if family in {"slots", "nested_hollow_section", "half_shell"}:
        if family in {"slots", "half_shell"}:
            summary["requires_topology_host_ranking"] = True
        if "prefers_explicit_inner_void_cut" in signals:
            summary["prefers_explicit_inner_void_cut"] = bool(
                signals.get("prefers_explicit_inner_void_cut")
            )
    if family == "half_shell":
        hinge_like_cylinder_count = signals.get("hinge_like_cylinder_count")
        if isinstance(hinge_like_cylinder_count, (int, float)):
            summary["hinge_like_cylinder_count"] = int(hinge_like_cylinder_count)
        hinge_axis = signals.get("hinge_like_axis")
        if isinstance(hinge_axis, str) and hinge_axis.strip():
            summary["hinge_like_axis"] = hinge_axis.strip()
        boundary_count = signals.get("boundary_cylindrical_face_count")
        if isinstance(boundary_count, (int, float)):
            summary["boundary_cylindrical_face_count"] = int(boundary_count)
        hinge_face_ids = signals.get("hinge_like_face_ids")
        if isinstance(hinge_face_ids, list) and hinge_face_ids:
            summary["hinge_like_face_ids"] = [
                str(item).strip()
                for item in hinge_face_ids[:3]
                if str(item).strip()
            ]
    if family == "named_face_local_edit":
        requested_face_targets = signals.get("requested_face_targets")
        if isinstance(requested_face_targets, list) and requested_face_targets:
            summary["requested_face_targets"] = [
                str(item).strip()
                for item in requested_face_targets
                if str(item).strip()
            ]
        requested_side_face_targets = signals.get("requested_side_face_targets")
        if isinstance(requested_side_face_targets, list) and requested_side_face_targets:
            summary["requested_side_face_targets"] = [
                str(item).strip()
                for item in requested_side_face_targets
                if str(item).strip()
            ]
        if "specific_side_target_grounded" in signals:
            summary["specific_side_target_grounded"] = bool(
                signals.get("specific_side_target_grounded")
            )
    if family != "explicit_anchor_hole":
        return summary
    expected_centers = _as_point_list(signals.get("expected_local_centers"))
    realized_centers = _as_point_list(signals.get("realized_centers"))
    expected_center_count_raw = signals.get("expected_local_center_count")
    expected_center_count = (
        int(expected_center_count_raw)
        if isinstance(expected_center_count_raw, (int, float))
        else len(expected_centers)
    )
    summary.update(
        {
            "expected_local_center_count": expected_center_count,
            "realized_local_center_count": len(realized_centers),
        }
    )
    if isinstance(signals.get("normalized_local_centers"), list):
        summary["normalized_local_centers"] = signals.get("normalized_local_centers")
    if isinstance(signals.get("host_frame_translation_from_corner"), list):
        summary["host_frame_translation_from_corner"] = signals.get(
            "host_frame_translation_from_corner"
        )
    if isinstance(signals.get("host_frame_dimensions"), list):
        summary["host_frame_dimensions"] = signals.get("host_frame_dimensions")
    return summary


def _build_grounding_blockers(
    *,
    family: str,
    blockers: list[str],
    signals: dict[str, Any],
) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for blocker in blockers:
        value = str(blocker).strip()
        if value and value not in seen:
            deduped.append(value)
            seen.add(value)

    if family == "explicit_anchor_hole":
        expected_centers = _as_point_list(signals.get("expected_local_centers"))
        expected_count_raw = signals.get("expected_local_center_count")
        expected_count = (
            int(expected_count_raw)
            if isinstance(expected_count_raw, (int, float))
            else len(expected_centers)
        )
        realized_count = len(_as_point_list(signals.get("realized_centers")))
        if expected_count <= 0:
            _append_unique(deduped, seen, "missing_expected_local_centers")
        if realized_count <= 0:
            _append_unique(deduped, seen, "missing_realized_local_centers")
        elif 0 < realized_count < expected_count:
            _append_unique(deduped, seen, "center_layout_not_fully_realized")
        elif expected_count > 0 and realized_count > expected_count:
            _append_unique(deduped, seen, "center_count_mismatch")
    if family in {"slots", "nested_hollow_section", "half_shell"}:
        if not _as_float_triplet(signals.get("bbox")):
            _append_unique(deduped, seen, "missing_bbox_geometry")
    if family == "named_face_local_edit":
        requested_side_face_targets = signals.get("requested_side_face_targets")
        if (
            isinstance(requested_side_face_targets, list)
            and requested_side_face_targets
            and not bool(signals.get("specific_side_target_grounded"))
        ):
            _append_unique(deduped, seen, "local_host_target_not_grounded")
    if family == "half_shell" and bool(signals.get("hinge_requested")):
        hinge_like_cylinder_count = signals.get("hinge_like_cylinder_count")
        if not isinstance(hinge_like_cylinder_count, (int, float)) or int(
            hinge_like_cylinder_count
        ) <= 0:
            _append_unique(deduped, seen, "missing_hinge_like_cylindrical_evidence")
    if family == "slots" and deduped:
        _append_unique(deduped, seen, "need_topology_host_selection")
    return deduped


def _build_common_anchor_summary(signals: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    solids = signals.get("solids")
    if isinstance(solids, (int, float)):
        summary["solid_count"] = int(solids)
    expected_part_count = signals.get("expected_part_count")
    if isinstance(expected_part_count, (int, float)):
        summary["expected_part_count"] = int(expected_part_count)
    bbox = _as_float_triplet(signals.get("bbox"))
    if bbox:
        summary["bbox"] = bbox
        summary["bbox_min_span"] = round(min(bbox), 6)
        summary["bbox_max_span"] = round(max(bbox), 6)
    expected_bbox = _as_float_triplet(signals.get("expected_bbox"))
    if expected_bbox:
        summary["expected_bbox"] = expected_bbox
    bbox_min = _as_float_triplet(signals.get("bbox_min"))
    if bbox_min:
        summary["bbox_min"] = bbox_min
    bbox_max = _as_float_triplet(signals.get("bbox_max"))
    if bbox_max:
        summary["bbox_max"] = bbox_max
    dominant_fraction = signals.get("dominant_solid_volume_fraction")
    if isinstance(dominant_fraction, (int, float)):
        summary["dominant_solid_volume_fraction"] = round(float(dominant_fraction), 6)
    secondary_solid_count = signals.get("secondary_solid_count")
    if isinstance(secondary_solid_count, (int, float)):
        summary["secondary_solid_count"] = int(secondary_solid_count)
    detached_fragment_count = signals.get("suspected_detached_fragment_count")
    if isinstance(detached_fragment_count, (int, float)):
        summary["suspected_detached_fragment_count"] = int(detached_fragment_count)
    detached_fragment_ids = signals.get("suspected_detached_fragment_solid_ids")
    if isinstance(detached_fragment_ids, list) and detached_fragment_ids:
        summary["suspected_detached_fragment_solid_ids"] = [
            str(item).strip()
            for item in detached_fragment_ids[:4]
            if str(item).strip()
        ]
    detached_fragment_fractions = signals.get(
        "suspected_detached_fragment_volume_fractions"
    )
    if isinstance(detached_fragment_fractions, list) and detached_fragment_fractions:
        summary["suspected_detached_fragment_volume_fractions"] = [
            round(float(item), 6)
            for item in detached_fragment_fractions[:4]
            if isinstance(item, (int, float))
        ]
    return summary


def _append_unique(values: list[str], seen: set[str], item: str) -> None:
    value = str(item).strip()
    if value and value not in seen:
        values.append(value)
        seen.add(value)


def _grounding_prefers_topology_host_selection(
    *,
    required_evidence_kinds: list[str],
    anchor_summary: dict[str, Any],
    grounding_blockers: list[str],
) -> bool:
    if "topology" not in {
        str(kind or "").strip().lower() for kind in required_evidence_kinds
    }:
        return False
    if bool(anchor_summary.get("requires_topology_host_ranking")):
        return True
    return "need_topology_host_selection" in {
        str(item or "").strip() for item in grounding_blockers
    }


def _as_point_list(value: Any) -> list[list[float]]:
    if not isinstance(value, list):
        return []
    points: list[list[float]] = []
    for item in value:
        if (
            isinstance(item, list)
            and len(item) >= 2
            and all(isinstance(coord, (int, float)) for coord in item[:2])
        ):
            points.append([float(item[0]), float(item[1])])
    return points


def _as_float_triplet(value: Any) -> list[float]:
    if not isinstance(value, list) or len(value) < 3:
        return []
    triplet = value[:3]
    if not all(isinstance(item, (int, float)) for item in triplet):
        return []
    return [round(float(item), 6) for item in triplet]
