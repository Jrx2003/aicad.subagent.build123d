from __future__ import annotations

import json
import re
from typing import Any


def _requirements_text(requirements: dict[str, object]) -> str:
    description = requirements.get("description")
    if isinstance(description, str) and description.strip():
        return description
    return json.dumps(requirements, ensure_ascii=False)


def _detect_positive_extrude_plane(requirement_lower: str) -> tuple[str, str] | None:
    if "extrude" not in requirement_lower:
        return None
    plane_map = {
        "xy plane": "z",
        "yz plane": "x",
        "xz plane": "y",
    }
    for plane_name, axis_name in plane_map.items():
        if plane_name in requirement_lower:
            return plane_name.upper(), axis_name.upper()
    return None


def _detect_named_plane_bottom_aligned_box_pose(
    requirement_lower: str,
) -> tuple[str, str] | None:
    if "box" not in requirement_lower:
        return None
    plane_map = {
        "xy plane": ("XY", "Z"),
        "yz plane": ("YZ", "X"),
        "xz plane": ("XZ", "Y"),
    }
    for plane_token, (plane_name, axis_name) in plane_map.items():
        if plane_token not in requirement_lower:
            continue
        axis_lower = axis_name.lower()
        if re.search(
            rf"(?:bottom|base)[^.,;]{{0,32}}{axis_lower}\s*=\s*0(?:\.0+)?",
            requirement_lower,
            re.IGNORECASE,
        ):
            return plane_name, axis_name
    return None


def _centered_tuple_for_positive_span_axis(axis_name: str) -> str:
    axis = axis_name.strip().upper()
    centered_map = {
        "X": "(False, True, True)",
        "Y": "(True, False, True)",
        "Z": "(True, True, False)",
    }
    return centered_map.get(axis, "(True, True, True)")


def _requirement_requests_centered_plane_pose(requirement_lower: str) -> bool:
    if any(
        token in requirement_lower
        for token in (
            "symmetr",
            "midplane",
            "centered about",
            "about the xy plane",
            "about the yz plane",
            "about the xz plane",
        )
    ):
        return True
    return any(
        re.search(pattern, requirement_lower, re.IGNORECASE)
        for pattern in (
            r"center(?:ed)?\s+(?:on|about|around)\s+(?:the\s+)?(?:xy|yz|xz)\s+plane",
            r"extrud(?:e|ed|ing)(?: it)?[^.,;]{0,32}\s+symmetr",
            r"extrud(?:e|ed|ing)(?: it)?[^.,;]{0,32}\s+midplane",
        )
    )


def _requirement_mentions_regular_polygon_side_length(
    requirement_lower: str,
) -> bool:
    if "side length" not in requirement_lower:
        return False
    return any(
        token in requirement_lower
        for token in (
            "equilateral triangle",
            "regular polygon",
            "hexagon",
            "pentagon",
            "octagon",
            "nonagon",
            "decagon",
        )
    )


def _requirement_prefers_named_face_local_feature_sequence(
    requirement_lower: str,
) -> bool:
    if "select the" not in requirement_lower or " face" not in requirement_lower:
        return False
    if not any(
        token in requirement_lower
        for token in (
            "fillet",
            "chamfer",
            "cut-extrude",
            "cut extrude",
            "pocket",
            "blind hole",
            " hole",
            "slot",
            "notch",
        )
    ):
        return False
    return any(
        token in requirement_lower
        for token in (
            "rectangle",
            "box",
            "block",
            "extrude",
            "cylinder",
            "base",
        )
    )


def _requirement_suggests_local_finish_probe_family(
    requirement_lower: str,
) -> bool:
    local_finish_tokens = (
        "local finish",
        "local finishing",
        "topology-aware",
        "topology aware",
        "mounting face",
        "opening rim",
        "rim edges",
        "target face",
        "target edge",
    )
    feature_tokens = (
        "fillet",
        "chamfer",
        "countersink",
        "counterbore",
        "notch",
        "edge fillet",
        "edge chamfer",
    )
    return any(token in requirement_lower for token in local_finish_tokens) and any(
        token in requirement_lower for token in feature_tokens
    )


def _requirement_mentions_enclosure_host(requirement_lower: str) -> bool:
    if any(
        token in requirement_lower for token in ("enclosure", "housing", "clamshell")
    ):
        return True
    has_lid = "lid" in requirement_lower
    has_base = "base" in requirement_lower
    if has_lid and has_base:
        return True
    if "shell" in requirement_lower and any(
        token in requirement_lower for token in ("lid", "base", "hinge", "mating")
    ):
        return True
    if has_lid and any(
        token in requirement_lower for token in ("hinge", "mating", "magnet")
    ):
        return True
    if has_base and any(
        token in requirement_lower
        for token in ("lid", "hinge", "mating", "magnet", "clamshell", "enclosure")
    ):
        return True
    return False


def _requirement_mentions_multi_part_assembled_envelope(
    requirement_lower: str,
) -> bool:
    if not _requirement_mentions_enclosure_host(requirement_lower):
        return False
    has_multi_part_signal = any(
        token in requirement_lower
        for token in (
            "two-part",
            "two part",
            "separate parts",
            "lid and base",
            "top lid",
            "bottom base",
            "clamshell",
            "hinge",
            "mating",
        )
    ) or ("lid" in requirement_lower and "base" in requirement_lower)
    if not has_multi_part_signal:
        return False
    has_envelope_signal = any(
        token in requirement_lower
        for token in (
            "overall dimensions",
            "overall dimension",
            "overall bounding box",
            "outer bounding box",
            "outer dimensions",
            "overall size",
        )
    )
    return has_envelope_signal


def _requirement_prefers_living_hinge(requirement_lower: str) -> bool:
    if not requirement_lower:
        return False
    return "living hinge" in requirement_lower or "living-hinge" in requirement_lower


def _requirement_explicitly_requests_detached_hinge_hardware(
    requirement_lower: str,
) -> bool:
    if not requirement_lower:
        return False
    return any(
        token in requirement_lower
        for token in (
            "removable pin",
            "removable hinge pin",
            "detachable pin",
            "separate hinge part",
            "separate hinge parts",
            "detached hinge",
            "detached hinge hardware",
            "exposed hinge assembly",
            "external hinge assembly",
            "hinge assembly",
            "hinge barrel",
            "hinge barrels",
            "hinge pin",
            "hinge pins",
        )
    )


def _requirement_mentions_half_shell_with_split_surface(
    requirement_lower: str,
) -> bool:
    if not requirement_lower:
        return False
    half_shell_tokens = (
        "half-cylindrical",
        "half cylindrical",
        "half cylinder",
        "half a cylinder",
        "semi-cylindrical",
        "semi cylindrical",
        "semicylindrical",
        "half-shell",
        "half shell",
    )
    if not any(token in requirement_lower for token in half_shell_tokens):
        return False
    return any(
        token in requirement_lower
        for token in (
            "split surface",
            "split line",
            "semicircle",
            "semi-circle",
            "bearing housing",
            "bore",
            "lug",
            "flange",
        )
    )


def _requirement_mentions_shelled_host_with_named_face_feature(
    requirement_lower: str,
    *,
    semantics: Any,
) -> bool:
    if not requirement_lower:
        return False
    if not any(
        token in requirement_lower
        for token in ("shell", "shelled", "hollow enclosure", "hollow body")
    ):
        return False
    if not bool(getattr(semantics, "face_targets", ())):
        return False
    return bool(
        getattr(semantics, "mentions_hole", False)
        or getattr(semantics, "mentions_pattern", False)
        or getattr(semantics, "mentions_spherical_recess", False)
        or any(
            token in requirement_lower
            for token in ("recess", "pocket", "groove", "slot", "notch")
        )
    )


def _requirement_mentions_flange_boss_pattern_holes(requirement_lower: str) -> bool:
    if not requirement_lower:
        return False
    if "flange" not in requirement_lower or "boss" not in requirement_lower:
        return False
    if "hole" not in requirement_lower:
        return False
    pattern_tokens = (
        "circular array",
        "evenly distributed",
        "distributed circle",
        "bolt circle",
        "pitch circle",
        "pattern",
    )
    if not any(token in requirement_lower for token in pattern_tokens):
        return False
    return (
        "through the flange" in requirement_lower
        or "cut through the flange" in requirement_lower
    )


def _requirement_mentions_explicit_path_sweep(requirement_lower: str) -> bool:
    if not requirement_lower or "sweep" not in requirement_lower:
        return False
    explicit_sweep_tokens = (
        "execute the sweep command",
        "sweep along the",
        "sweep the annular profile",
        "sweep the profile along",
    )
    if not any(token in requirement_lower for token in explicit_sweep_tokens):
        return False
    has_rail = (
        "path sketch" in requirement_lower
        or "path" in requirement_lower
        or "rail" in requirement_lower
    )
    has_profile = (
        "profile sketch" in requirement_lower or "profile" in requirement_lower
    )
    return has_rail and has_profile


def _requirement_uses_named_plane_symmetric_union(requirement_lower: str) -> bool:
    plane_hits = sum(
        1
        for token in ("xy plane", "yz plane", "xz plane")
        if token in requirement_lower
    )
    if plane_hits < 2:
        return False
    if "symmetr" not in requirement_lower and "centered" not in requirement_lower:
        return False
    if "extrude" not in requirement_lower:
        return False
    return (
        "union" in requirement_lower
        or "orthogonal" in requirement_lower
        or "intersect at the origin" in requirement_lower
    )


def _detect_positive_extrude_bbox_mismatch(
    *,
    requirement_lower: str,
    latest_write_health: dict[str, Any] | None,
) -> tuple[str, str, tuple[float, float], tuple[float, float]] | None:
    if not isinstance(latest_write_health, dict):
        return None
    if (
        str(latest_write_health.get("tool") or "").strip().lower()
        != "execute_build123d"
    ):
        return None
    if any(
        token in requirement_lower
        for token in (
            "centered about",
            "symmetr",
            "midplane",
            "about the xy plane",
            "about the yz plane",
            "about the xz plane",
        )
    ):
        return None
    plane_spec = _extract_positive_extrude_spec(requirement_lower)
    if plane_spec is None:
        return None
    plane_name, axis_name, axis_index, distance = plane_spec
    geometry = latest_write_health.get("geometry")
    if not isinstance(geometry, dict):
        return None
    bbox_min = geometry.get("bbox_min")
    bbox_max = geometry.get("bbox_max")
    if not (
        isinstance(bbox_min, list)
        and isinstance(bbox_max, list)
        and len(bbox_min) >= 3
        and len(bbox_max) >= 3
    ):
        return None
    current_min = float(bbox_min[axis_index])
    current_max = float(bbox_max[axis_index])
    expected_range = (0.0, float(distance))
    current_range = (current_min, current_max)
    distance_tolerance = max(1e-3, abs(distance) * 0.08)
    lower_bound_matches = abs(current_min - expected_range[0]) <= distance_tolerance
    upper_bound_matches = abs(current_max - expected_range[1]) <= distance_tolerance
    if lower_bound_matches and upper_bound_matches:
        return None
    return plane_name, axis_name, expected_range, current_range


def _extract_positive_extrude_spec(
    requirement_lower: str,
) -> tuple[str, str, int, float] | None:
    plane_map = {
        "xy plane": ("XY PLANE", "Z", 2),
        "yz plane": ("YZ PLANE", "X", 0),
        "xz plane": ("XZ PLANE", "Y", 1),
    }
    matched_plane: tuple[str, str, int] | None = None
    for plane_token, plane_spec in plane_map.items():
        if plane_token in requirement_lower:
            matched_plane = plane_spec
            break
    if matched_plane is None:
        return None

    match = re.search(
        r"extrud(?:e|ed|ing)(?: it)?(?: [^.,;]{0,32})? by ([0-9]+(?:\.[0-9]+)?)",
        requirement_lower,
    )
    if match is None:
        match = re.search(
            r"extrud(?:e|ed|ing)(?: it)? ([0-9]+(?:\.[0-9]+)?)(?: millimeters?| mm)?(?: [^.,;]{0,24})? along (?:the )?[xyz]-axis",
            requirement_lower,
        )
    if match is None:
        return None
    distance = float(match.group(1))
    if distance <= 0.0:
        return None
    plane_name, axis_name, axis_index = matched_plane
    return plane_name, axis_name, axis_index, distance


def _detect_named_axis_axisymmetric_pose_mismatch(
    *,
    requirement_lower: str,
    latest_write_health: dict[str, Any] | None,
) -> tuple[str, tuple[str, str], tuple[float, float], tuple[float, float, float]] | None:
    if not isinstance(latest_write_health, dict):
        return None
    if (
        str(latest_write_health.get("tool") or "").strip().lower()
        != "execute_build123d"
    ):
        return None
    axis_spec = _extract_named_axis_axisymmetric_spec(requirement_lower)
    if axis_spec is None:
        return None
    axis_name, axis_index = axis_spec
    geometry = latest_write_health.get("geometry")
    if not isinstance(geometry, dict):
        return None
    bbox_min = geometry.get("bbox_min")
    bbox_max = geometry.get("bbox_max")
    center = geometry.get("center_of_mass")
    if not (
        isinstance(bbox_min, list)
        and isinstance(bbox_max, list)
        and len(bbox_min) >= 3
        and len(bbox_max) >= 3
    ):
        return None
    perpendicular_indices = [idx for idx in range(3) if idx != axis_index]
    perpendicular_names = tuple("XYZ"[idx] for idx in perpendicular_indices)
    radial_span = max(
        float(bbox_max[idx]) - float(bbox_min[idx]) for idx in perpendicular_indices
    )
    tolerance = max(1.0, abs(radial_span) * 0.08)
    bbox_offsets = tuple(
        abs((float(bbox_min[idx]) + float(bbox_max[idx])) / 2.0)
        for idx in perpendicular_indices
    )
    center_tuple = (
        tuple(float(center[idx]) for idx in range(3))
        if isinstance(center, list) and len(center) >= 3
        else (0.0, 0.0, 0.0)
    )
    center_offsets = tuple(abs(center_tuple[idx]) for idx in perpendicular_indices)
    if max((*bbox_offsets, *center_offsets)) <= tolerance:
        return None
    return axis_name, perpendicular_names, bbox_offsets, center_tuple


def _extract_named_axis_axisymmetric_spec(
    requirement_lower: str,
) -> tuple[str, int] | None:
    if not any(
        token in requirement_lower
        for token in ("revolve", "revolution", "rotational", "axisymmetric")
    ):
        return None
    match = re.search(
        r"(?:around|about)\s+(?:the\s+)?(?P<axis>[xyz])(?:\s*[- ]?\s*axis)\b",
        requirement_lower,
        re.IGNORECASE,
    )
    if match is None:
        return None
    axis_name = str(match.group("axis")).upper()
    axis_index_map = {"X": 0, "Y": 1, "Z": 2}
    return axis_name, axis_index_map[axis_name]


def _requirement_explicitly_prescribes_revolve_profile(requirement_lower: str) -> bool:
    if not any(
        token in requirement_lower
        for token in (
            "rotational addition",
            "revolved boss",
            "revolve",
            "rotate 360",
            "360 degrees",
        )
    ):
        return False
    has_axis_language = (
        "axis of rotation" in requirement_lower
        or "centerline" in requirement_lower
        or _extract_named_axis_axisymmetric_spec(requirement_lower) is not None
    )
    has_profile_language = (
        "closed profile" in requirement_lower
        or "close the profile" in requirement_lower
        or "close the sketch" in requirement_lower
        or "sketch plane" in requirement_lower
    )
    return has_axis_language and has_profile_language


def _requirement_suggests_mixed_nested_section(
    *,
    requirement_lower: str,
    blockers: set[str],
) -> bool:
    if "feature_inner_void_cutout" in blockers:
        return True
    if "extrude" not in requirement_lower:
        return False
    if "center" not in requirement_lower:
        return False
    shape_tokens = {
        token
        for token in ("circle", "square", "rectangle", "triangle", "hexagon", "polygon")
        if token in requirement_lower
    }
    if len(shape_tokens) < 2:
        return False
    return (
        "extrude the section" in requirement_lower
        or "frame" in requirement_lower
        or "hollow" in requirement_lower
        or "inner void" in requirement_lower
        or "cutout" in requirement_lower
    )


def _requirement_prefers_nested_regular_polygon_frame(
    *,
    requirement_lower: str,
    blockers: set[str],
) -> bool:
    if not _requirement_mentions_regular_polygon_side_length(requirement_lower):
        return False
    if "feature_inner_void_cutout" in blockers:
        return True
    if "extrude" not in requirement_lower:
        return False
    return any(
        token in requirement_lower
        for token in (
            "concentric",
            "centroid",
            "centroids coinciding",
            "frame-shaped region",
            "frame",
            "hollow",
            "inner void",
        )
    )


def _requirement_has_explicit_xy_coordinate_pair(requirement_text: str) -> bool:
    if not isinstance(requirement_text, str) or not requirement_text.strip():
        return False
    return bool(
        re.search(
            r"\(\s*[-+]?[0-9]+(?:\.[0-9]+)?\s*,\s*[-+]?[0-9]+(?:\.[0-9]+)?\s*\)",
            requirement_text,
        )
    )


def _requirement_mentions_directional_drilling(requirement_lower: str) -> bool:
    if not requirement_lower:
        return False
    return any(
        phrase in requirement_lower
        for phrase in (
            "in the x direction",
            "in the y direction",
            "in the z direction",
            "along the x direction",
            "along the y direction",
            "along the z direction",
            "drill in the x direction",
            "drill in the y direction",
            "drill in the z direction",
            "through-holes through the lugs in the y direction",
        )
    )


def _infer_centered_square_or_rectangular_array_centers(
    requirement_text: str | None,
) -> list[list[float]]:
    text = str(requirement_text or "").strip().lower()
    if not text or ("array" not in text and "pattern" not in text):
        return []
    if "center" not in text:
        return []

    explicit_xy_offset = re.search(
        r"each\s+\w+\s*'?s?\s+center\s+is\s+([0-9]+(?:\.[0-9]+)?)\s*mm?\s+from\s+the\s+center\s+in\s+the\s+x\s*/\s*y\s+direction",
        text,
        re.IGNORECASE,
    )
    if explicit_xy_offset is not None:
        try:
            offset = float(explicit_xy_offset.group(1))
        except Exception:
            offset = 0.0
        if offset > 0.0:
            return [
                [offset, offset],
                [offset, -offset],
                [-offset, offset],
                [-offset, -offset],
            ]

    square_side_match = re.search(
        r"square\s+array[^.]{0,80}?side\s+length(?:\s+of)?\s+([0-9]+(?:\.[0-9]+)?)",
        text,
        re.IGNORECASE,
    )
    if square_side_match is not None:
        try:
            side_length = float(square_side_match.group(1))
        except Exception:
            side_length = 0.0
        if side_length > 0.0:
            half = side_length / 2.0
            return [
                [half, half],
                [half, -half],
                [-half, half],
                [-half, -half],
            ]

    x_axis_pattern = re.search(
        r"x-axis[^.;]{0,120}?spacing\s+([0-9]+(?:\.[0-9]+)?)\s*mm?[^.;]{0,80}?quantity\s+([0-9]+)",
        text,
        re.IGNORECASE,
    )
    y_axis_pattern = re.search(
        r"y-axis[^.;]{0,120}?spacing\s+([0-9]+(?:\.[0-9]+)?)\s*mm?[^.;]{0,80}?quantity\s+([0-9]+)",
        text,
        re.IGNORECASE,
    )
    if x_axis_pattern is not None and y_axis_pattern is not None:
        try:
            x_spacing = float(x_axis_pattern.group(1))
            x_count = int(x_axis_pattern.group(2))
            y_spacing = float(y_axis_pattern.group(1))
            y_count = int(y_axis_pattern.group(2))
        except Exception:
            x_spacing = 0.0
            x_count = 0
            y_spacing = 0.0
            y_count = 0
        if (
            x_spacing > 0.0
            and y_spacing > 0.0
            and x_count > 1
            and y_count > 1
            and (x_count * y_count) <= 25
        ):
            x_mid = (x_count - 1) / 2.0
            y_mid = (y_count - 1) / 2.0
            x_positions = [
                round((index - x_mid) * x_spacing, 4) for index in range(x_count)
            ]
            y_positions = [
                round((index - y_mid) * y_spacing, 4) for index in range(y_count)
            ]
            return [
                [x_pos, y_pos] for x_pos in x_positions for y_pos in y_positions
            ]

    return []


__all__ = [name for name in globals() if not name.startswith("__")]
