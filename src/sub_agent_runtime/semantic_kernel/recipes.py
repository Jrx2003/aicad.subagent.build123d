from __future__ import annotations

from typing import Any, Iterable

from sub_agent_runtime.semantic_kernel.bindings import _sanitize_anchor_signal_value
from sub_agent_runtime.semantic_kernel.instances import _slugify
from sub_agent_runtime.semantic_kernel.models import (
    DomainKernelState,
    FamilyRepairPacket,
    FeatureInstance,
)

_MAX_ACTIVE_REPAIR_PACKETS = 4

def _family_repair_priority_rank(family_id: str) -> int:
    family_priority = {
        "path_sweep": 0,
        "axisymmetric_profile": 1,
        "orthogonal_union": 2,
        "nested_hollow_section": 2,
        "spherical_recess": 3,
        "pattern_distribution": 3,
        "explicit_anchor_hole": 4,
        "named_face_local_edit": 5,
        "general_geometry": 6,
    }
    return family_priority.get(str(family_id or "").strip(), 99)

def _repair_packet_priority(packet: FamilyRepairPacket) -> tuple[int, int, int, str]:
    specificity = 0
    if packet.target_anchor_summary:
        specificity += 3
    if packet.realized_anchor_summary:
        specificity += 2
    if packet.host_frame:
        specificity += 2
    if packet.recipe_id:
        specificity += 1
    blocker_rank = 3
    instance_id = str(packet.feature_instance_id or "").strip()
    if ".feature_hole_position_alignment" in instance_id or ".feature_local_anchor_alignment" in instance_id:
        blocker_rank = 0
    elif ".feature_hole_exact_center_set" in instance_id or ".feature_pattern_seed_alignment" in instance_id:
        blocker_rank = 1
    elif ".feature_countersink" in instance_id:
        blocker_rank = 2
    recipe_priority = 0 if str(packet.recipe_id or "").strip() else 1
    return (
        recipe_priority,
        _family_repair_priority_rank(packet.family_id),
        blocker_rank,
        -specificity,
        0 if packet.repair_mode == "local_edit" else 1,
        packet.packet_id,
    )

def _sorted_active_repair_packets(
    packets: Iterable[FamilyRepairPacket],
) -> list[FamilyRepairPacket]:
    return sorted(
        [
            packet
            for packet in packets
            if not bool(getattr(packet, "stale", False))
        ],
        key=_repair_packet_priority,
    )

def _coerce_xy_points(value: Any) -> list[list[float]]:
    points: list[list[float]] = []
    if not isinstance(value, list):
        return points
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        try:
            x = float(item[0])
            y = float(item[1])
        except (TypeError, ValueError):
            continue
        points.append([x, y])
    return points

def _build_centered_bbox_host_frame(
    *,
    host_ids: list[str],
    geometry_summary: dict[str, Any],
) -> dict[str, Any]:
    bbox = geometry_summary.get("bbox")
    bbox_min = geometry_summary.get("bbox_min")
    bbox_max = geometry_summary.get("bbox_max")
    if not (
        isinstance(bbox, list)
        and len(bbox) >= 2
        and isinstance(bbox_min, list)
        and len(bbox_min) >= 2
        and isinstance(bbox_max, list)
        and len(bbox_max) >= 2
    ):
        return {}
    try:
        width = float(bbox[0])
        depth = float(bbox[1])
        min_x = float(bbox_min[0])
        min_y = float(bbox_min[1])
        max_x = float(bbox_max[0])
        max_y = float(bbox_max[1])
    except (TypeError, ValueError):
        return {}
    if not (min_x < 0.0 < max_x and min_y < 0.0 < max_y):
        return {}
    return {
        "frame_kind": "centered_bbox_xy",
        "host_face": host_ids[0] if host_ids else "body.primary",
        "bbox": [width, depth],
        "bbox_min": [min_x, min_y],
        "bbox_max": [max_x, max_y],
        "origin": [0.0, 0.0],
        "translation_from_corner_frame": [-round(width / 2.0, 6), -round(depth / 2.0, 6)],
    }

def _repair_packet_geometry_summary(
    parameter_bindings: dict[str, Any],
) -> dict[str, Any]:
    geometry_summary = (
        parameter_bindings.get("geometry_summary")
        if isinstance(parameter_bindings.get("geometry_summary"), dict)
        else {}
    )
    bbox = geometry_summary.get("bbox")
    bbox_min = geometry_summary.get("bbox_min")
    bbox_max = geometry_summary.get("bbox_max")
    if (
        isinstance(bbox, list)
        and len(bbox) >= 2
        and isinstance(bbox_min, list)
        and len(bbox_min) >= 2
        and isinstance(bbox_max, list)
        and len(bbox_max) >= 2
    ):
        return geometry_summary
    fallback_bbox = parameter_bindings.get("bbox")
    fallback_bbox_min = parameter_bindings.get("bbox_min")
    fallback_bbox_max = parameter_bindings.get("bbox_max")
    if (
        isinstance(fallback_bbox, list)
        and len(fallback_bbox) >= 2
        and isinstance(fallback_bbox_min, list)
        and len(fallback_bbox_min) >= 2
        and isinstance(fallback_bbox_max, list)
        and len(fallback_bbox_max) >= 2
    ):
        synthesized = dict(geometry_summary)
        synthesized["bbox"] = fallback_bbox
        synthesized["bbox_min"] = fallback_bbox_min
        synthesized["bbox_max"] = fallback_bbox_max
        return synthesized
    return geometry_summary

def _normalize_points_for_host_frame(
    *,
    points: list[list[float]],
    geometry_summary: dict[str, Any],
) -> tuple[list[list[float]], bool]:
    bbox = geometry_summary.get("bbox")
    bbox_min = geometry_summary.get("bbox_min")
    bbox_max = geometry_summary.get("bbox_max")
    if not (
        points
        and isinstance(bbox, list)
        and len(bbox) >= 2
        and isinstance(bbox_min, list)
        and len(bbox_min) >= 2
        and isinstance(bbox_max, list)
        and len(bbox_max) >= 2
    ):
        return points, False
    try:
        width = float(bbox[0])
        depth = float(bbox[1])
        min_x = float(bbox_min[0])
        min_y = float(bbox_min[1])
        max_x = float(bbox_max[0])
        max_y = float(bbox_max[1])
    except (TypeError, ValueError):
        return points, False
    if not (min_x < 0.0 < max_x and min_y < 0.0 < max_y):
        return points, False
    half_width = width / 2.0
    half_depth = depth / 2.0
    requires_shift = any(
        point[0] > half_width + 1e-6 or point[1] > half_depth + 1e-6
        for point in points
    )
    if not requires_shift:
        return points, False
    normalized = [
        [round(point[0] - half_width, 6), round(point[1] - half_depth, 6)]
        for point in points
    ]
    return normalized, True

def _explicit_anchor_hole_recipe_packet(
    *,
    feature_instance: FeatureInstance,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str | None, str | None, dict[str, Any]]:
    parameter_bindings = (
        feature_instance.parameter_bindings
        if isinstance(feature_instance.parameter_bindings, dict)
        else {}
    )
    geometry_summary = _repair_packet_geometry_summary(parameter_bindings)
    expected_centers = _coerce_xy_points(parameter_bindings.get("expected_local_centers"))
    realized_centers = _coerce_xy_points(parameter_bindings.get("realized_centers"))
    expected_center_count_raw = parameter_bindings.get("expected_local_center_count")
    expected_center_count = (
        int(expected_center_count_raw)
        if isinstance(expected_center_count_raw, (int, float))
        else None
    )
    realized_center_count_raw = parameter_bindings.get("realized_center_count")
    realized_center_count = (
        int(realized_center_count_raw)
        if isinstance(realized_center_count_raw, (int, float))
        else (len(realized_centers) if realized_centers else None)
    )
    host_frame = _build_centered_bbox_host_frame(
        host_ids=feature_instance.host_ids,
        geometry_summary=geometry_summary,
    )
    host_face = str(
        parameter_bindings.get("host_face")
        or host_frame.get("host_face")
        or (feature_instance.host_ids[0] if feature_instance.host_ids else "top")
    ).strip() or "top"
    if host_frame:
        host_frame = {
            **host_frame,
            "host_face": host_face,
        }
    normalized_expected, normalization_applied = _normalize_points_for_host_frame(
        points=expected_centers,
        geometry_summary=geometry_summary,
    )
    recommended_center_count = (
        len(expected_centers)
        if expected_centers
        else (
            expected_center_count
            if expected_center_count is not None
            else realized_center_count
        )
    )
    target_anchor_summary = (
        {
            "requested_centers": expected_centers,
            "normalized_local_centers": normalized_expected,
            "normalization_applied": normalization_applied,
            "host_face": host_face,
            "recommended_center_count": recommended_center_count,
            "expected_center_count": recommended_center_count,
        }
        if expected_centers or recommended_center_count is not None
        else {}
    )
    realized_anchor_summary = (
        {
            "realized_centers": realized_centers,
            "host_face": host_face,
            "realized_center_count": realized_center_count,
        }
        if realized_centers or realized_center_count is not None
        else {}
    )
    recipe_id = None
    recipe_summary = None
    recipe_skeleton: dict[str, Any] = {}
    if expected_centers:
        recipe_id = (
            "explicit_anchor_hole_centered_host_frame_array"
            if normalization_applied
            else "explicit_anchor_hole_local_anchor_array"
        )
        recipe_summary = (
            "Select the host face workplane, push the normalized center set, and rebuild the "
            "hole array with the countersink recipe on that local frame."
        )
        recipe_skeleton = {
            "host_face": host_face,
            "workplane_frame": host_frame.get("frame_kind", "host_face_local"),
            "workplane_normal_strategy": "host_face_outward_normal",
            "center_frame_kind": "host_face_local_2d",
            "point_strategy": "pushPoints",
            "center_source_key": "normalized_local_centers"
            if normalization_applied
            else "requested_centers",
            "center_count_hint": len(expected_centers),
            "center_count_source": "requested_centers",
            "hole_call": (
                "cskHole"
                if any("countersink" in blocker for blocker in feature_instance.blocker_ids)
                else "hole"
            ),
        }
    elif (
        str(feature_instance.repair_intent or "").strip()
        == "restore_explicit_anchor_countersink"
        or any(
            isinstance(blocker, str)
            and ("countersink" in blocker or "hole" in blocker)
            for blocker in feature_instance.blocker_ids
        )
    ):
        recipe_id = "explicit_anchor_hole_helper_contract_fallback"
        recipe_summary = (
            "Keep the host body authoritative, bind helper-based hole/countersink creation "
            "to the actual host-face plane with the face's outward normal, preserve the "
            "intended planar center count, and avoid manual cone/cylinder cutters inside "
            "the active BuildPart when the center layout is not yet fully grounded."
        )
        recipe_skeleton = {
            "mode": "subtree_rebuild_via_execute_build123d",
            "host_face": host_face,
            "workplane_frame": host_frame.get("frame_kind", "host_face_local"),
            "workplane_normal_strategy": "host_face_outward_normal",
            "center_frame_kind": "host_face_local_2d",
            "center_source_key": "derive_from_requirement_or_validation",
            "center_count_hint": recommended_center_count,
            "center_count_source": (
                "realized_centers"
                if realized_centers
                else "requirement_or_validation"
            ),
            "hole_call": "CounterSinkHole_or_Hole",
            "helper_contract": (
                "CounterSinkHole(radius=..., counter_sink_radius=..., "
                "depth=..., counter_sink_angle=...)"
            ),
            "cutter_strategy": "avoid_manual_cone_cylinder_inside_active_builder",
        }
    return (
        host_frame,
        target_anchor_summary,
        realized_anchor_summary,
        recipe_id,
        recipe_summary,
        recipe_skeleton,
    )

def _spherical_recess_recipe_packet(
    *,
    feature_instance: FeatureInstance,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str | None, str | None, dict[str, Any]]:
    parameter_bindings = (
        feature_instance.parameter_bindings
        if isinstance(feature_instance.parameter_bindings, dict)
        else {}
    )
    geometry_summary = _repair_packet_geometry_summary(parameter_bindings)
    expected_centers = _coerce_xy_points(parameter_bindings.get("expected_local_centers"))
    realized_centers = _coerce_xy_points(parameter_bindings.get("realized_centers"))
    host_frame = _build_centered_bbox_host_frame(
        host_ids=feature_instance.host_ids,
        geometry_summary=geometry_summary,
    )
    host_face = str(
        parameter_bindings.get("host_face")
        or host_frame.get("host_face")
        or (feature_instance.host_ids[0] if feature_instance.host_ids else "top")
    ).strip() or "top"
    if host_frame:
        host_frame = {
            **host_frame,
            "host_face": host_face,
        }
    target_anchor_summary = {}
    if expected_centers:
        target_anchor_summary = {
            "expected_local_centers": expected_centers,
            "host_face": host_face,
        }
    realized_anchor_summary = {"realized_centers": realized_centers} if realized_centers else {}
    recipe_id = None
    recipe_summary = None
    recipe_skeleton: dict[str, Any] = {}
    if expected_centers:
        recipe_id = "spherical_recess_host_face_center_set"
        recipe_summary = (
            "Keep the host solid, place the full local center set on the requested host face plane, "
            "build one sphere cutter per center, union the cutters, and subtract them from the host body."
        )
        recipe_skeleton = {
            "mode": "subtree_rebuild_via_execute_build123d",
            "host_face": host_face,
            "workplane_frame": host_frame.get("frame_kind", "host_face_local"),
            "center_source_key": "expected_local_centers",
            "cutter_kind": "sphere_array_subtract",
            "sphere_center_z_strategy": "host_face_plane",
        }
    return (
        host_frame,
        target_anchor_summary,
        realized_anchor_summary,
        recipe_id,
        recipe_summary,
        recipe_skeleton,
    )

def _half_shell_profile_recipe_packet(
    *,
    feature_instance: FeatureInstance,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str | None, str | None, dict[str, Any]]:
    parameter_bindings = (
        feature_instance.parameter_bindings
        if isinstance(feature_instance.parameter_bindings, dict)
        else {}
    )
    split_axis = str(parameter_bindings.get("likely_split_axis") or "Y").strip().upper() or "Y"
    observed_bounds = parameter_bindings.get("likely_split_bounds")
    observed_spans = parameter_bindings.get("observed_spans")
    expected_half_profile_span = parameter_bindings.get("expected_half_profile_span")
    expected_length = parameter_bindings.get("expected_length")
    host_frame = {
        "frame_kind": "global_half_shell_split_frame",
        "split_axis": split_axis,
        "half_plane": "positive",
        "hole_center_frame": "global_xz",
    }
    if isinstance(observed_bounds, list) and len(observed_bounds) >= 2:
        host_frame["observed_split_bounds"] = observed_bounds
    target_anchor_summary: dict[str, Any] = {}
    if isinstance(expected_half_profile_span, (int, float)):
        target_anchor_summary["expected_half_profile_span"] = float(expected_half_profile_span)
    if isinstance(expected_length, (int, float)):
        target_anchor_summary["expected_length"] = float(expected_length)
    realized_anchor_summary: dict[str, Any] = {}
    if isinstance(observed_bounds, list) and len(observed_bounds) >= 2:
        realized_anchor_summary["observed_split_bounds"] = observed_bounds
    if isinstance(observed_spans, list) and len(observed_spans) >= 3:
        realized_anchor_summary["observed_spans"] = observed_spans
    recipe_id = None
    recipe_summary = None
    recipe_skeleton: dict[str, Any] = {}
    if (
        str(feature_instance.repair_intent or "").strip()
        == "rebuild_half_shell_profile_envelope"
        or "feature_half_shell_profile_envelope" in feature_instance.blocker_ids
    ):
        recipe_id = "half_shell_profile_global_xz_lug_hole_recipe"
        recipe_summary = (
            "Rebuild the half-shell as a positive-half-plane semi-annulus, merge the pad before "
            "the bore cut, then place the lug-hole cutters from global X/Z anchors so Y-direction "
            "holes do not drift when workplane offsets are ambiguous."
        )
        recipe_skeleton = {
            "mode": "subtree_rebuild_via_execute_build123d",
            "profile_kind": "semi_annulus_shell",
            "split_axis": split_axis,
            "half_plane": "positive",
            "pad_strategy": "merge_then_bore_cut",
            "hole_axis": "Y",
            "hole_center_frame": "global_xz",
            "cutter_kind": "y_axis_cylinder_array",
        }
    return (
        host_frame,
        target_anchor_summary,
        realized_anchor_summary,
        recipe_id,
        recipe_summary,
        recipe_skeleton,
    )

def _family_repair_packet_from_feature_instance(
    feature_instance: FeatureInstance,
) -> FamilyRepairPacket | None:
    family_id = str(feature_instance.family_id or "").strip()
    if not family_id:
        return None
    parameter_bindings = (
        feature_instance.parameter_bindings
        if isinstance(feature_instance.parameter_bindings, dict)
        else {}
    )
    parameter_keys = [
        key
        for key in parameter_bindings.keys()
        if isinstance(key, str) and key and key != "geometry_summary"
    ]
    host_frame: dict[str, Any] = {}
    target_anchor_summary: dict[str, Any] = {}
    realized_anchor_summary: dict[str, Any] = {}
    recipe_id: str | None = None
    recipe_summary: str | None = None
    recipe_skeleton: dict[str, Any] = {}
    if family_id == "spherical_recess":
        (
            host_frame,
            target_anchor_summary,
            realized_anchor_summary,
            recipe_id,
            recipe_summary,
            recipe_skeleton,
        ) = _spherical_recess_recipe_packet(feature_instance=feature_instance)
    if (
        family_id == "axisymmetric_profile"
        and str(feature_instance.repair_intent or "").strip()
        == "rebuild_half_shell_profile_envelope"
    ):
        (
            host_frame,
            target_anchor_summary,
            realized_anchor_summary,
            recipe_id,
            recipe_summary,
            recipe_skeleton,
        ) = _half_shell_profile_recipe_packet(feature_instance=feature_instance)
    if family_id in {"explicit_anchor_hole", "pattern_distribution"}:
        (
            host_frame,
            target_anchor_summary,
            realized_anchor_summary,
            recipe_id,
            recipe_summary,
            recipe_skeleton,
        ) = _explicit_anchor_hole_recipe_packet(feature_instance=feature_instance)
    if not (
        feature_instance.anchor_keys
        or parameter_keys
        or target_anchor_summary
        or realized_anchor_summary
        or recipe_id
    ):
        return None
    return FamilyRepairPacket(
        packet_id=f"repair_packet.{_slugify(feature_instance.instance_id)}",
        family_id=family_id,
        feature_instance_id=feature_instance.instance_id,
        repair_mode=feature_instance.latest_repair_mode or "subtree_rebuild",
        repair_intent=feature_instance.repair_intent,
        affected_host_ids=list(feature_instance.host_ids or ["body.primary"]),
        anchor_keys=list(feature_instance.anchor_keys),
        parameter_keys=parameter_keys,
        host_frame=host_frame,
        target_anchor_summary=target_anchor_summary,
        realized_anchor_summary=realized_anchor_summary,
        recipe_id=recipe_id,
        recipe_summary=recipe_summary,
        recipe_skeleton=recipe_skeleton,
        source_binding_id=feature_instance.linked_binding_ids[-1]
        if feature_instance.linked_binding_ids
        else None,
        source_blocker_ids=list(feature_instance.blocker_ids),
    )

def _replace_repair_packets_from_active_instances(
    graph: DomainKernelState,
    active_instances: list[FeatureInstance],
) -> None:
    packets = [
        packet
        for packet in (
            _family_repair_packet_from_feature_instance(feature_instance)
            for feature_instance in active_instances
        )
        if packet is not None
    ]
    if packets:
        prioritized_packets = _sorted_active_repair_packets(packets)[
            :_MAX_ACTIVE_REPAIR_PACKETS
        ]
        packets = list(reversed(prioritized_packets))
    graph.replace_repair_packets(packets)

def _feature_instance_digest(
    feature_instance: FeatureInstance,
    *,
    max_nodes: int,
) -> dict[str, Any]:
    return {
        "instance_id": feature_instance.instance_id,
        "family_id": feature_instance.family_id,
        "primary_feature_id": feature_instance.primary_feature_id,
        "status": feature_instance.status,
        "summary": feature_instance.summary,
        "host_ids": list(feature_instance.host_ids[:max_nodes]),
        "blocker_ids": list(feature_instance.blocker_ids[:max_nodes]),
        "anchor_keys": list(feature_instance.anchor_keys[:max_nodes]),
        "parameter_bindings": _compact_parameter_bindings(
            feature_instance.parameter_bindings
        ),
        "linked_binding_ids": list(feature_instance.linked_binding_ids[:max_nodes]),
        "latest_repair_mode": feature_instance.latest_repair_mode,
        "repair_intent": feature_instance.repair_intent,
    }

def _repair_priority_for_feature_instance(feature_instance: FeatureInstance) -> tuple[int, str]:
    family_id = str(feature_instance.family_id or "").strip()
    instance_id = str(feature_instance.instance_id or "").strip()
    blocker_priority = 99
    if family_id == "path_sweep":
        if ".feature_path_sweep_rail" in instance_id:
            blocker_priority = 0
        elif ".feature_path_sweep_profile" in instance_id:
            blocker_priority = 1
        elif ".feature_path_sweep_frame" in instance_id:
            blocker_priority = 2
        elif ".feature_path_sweep_result" in instance_id:
            blocker_priority = 3
    elif family_id == "axisymmetric_profile":
        if ".feature_half_shell_profile_envelope" in instance_id:
            blocker_priority = 0
        elif ".feature_named_plane_positive_extrude_span" in instance_id:
            blocker_priority = 1
    return (_family_repair_priority_rank(family_id), blocker_priority, instance_id)

def _compact_parameter_bindings(bindings: dict[str, Any]) -> dict[str, Any]:
    compacted: dict[str, Any] = {}
    for key, value in list(bindings.items())[:8]:
        if not isinstance(key, str) or not key.strip():
            continue
        compacted[key] = _sanitize_anchor_signal_value(value)
    return compacted
