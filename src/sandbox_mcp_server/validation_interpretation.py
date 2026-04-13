from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from sandbox_mcp_server.contracts import (
    RequirementCheck,
    RequirementCheckStatus,
    RequirementClauseInterpretation,
    RequirementClauseStatus,
)
from sandbox_mcp_server.validation_evidence import (
    RequirementEvidenceBundle,
    _normalize_requirement_text,
    _split_requirement_clauses,
)


class RequirementInterpretationSummary(BaseModel):
    """Evidence-first interpretation summary for a validation request."""

    model_config = ConfigDict(extra="forbid")

    clause_interpretations: list[RequirementClauseInterpretation] = Field(
        default_factory=list
    )
    legacy_checks: list[RequirementCheck] = Field(default_factory=list)
    coverage_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    insufficient_evidence: list[str] = Field(default_factory=list)
    observation_tags: list[str] = Field(default_factory=list)
    decision_hints: list[str] = Field(default_factory=list)


_BODY_SHAPE_TOKENS = (
    "plate",
    "block",
    "box",
    "rectangular",
    "square",
    "base",
    "baseplate",
    "prism",
    "cube",
    "slab",
    "enclosure",
    "housing",
    "casing",
    "shell",
    "case",
    "washer",
    "disk",
    "disc",
    "cylinder",
    "ring",
)

_PROCESS_SETUP_PREFIXES = (
    "initialize the modeling environment",
    "create a new part",
    "create a new part file",
    "set the units to ",
    "establish a global coordinate system",
    "with the default xy plane as the base sketch plane",
    "with the default xz plane as the base sketch plane",
    "with the default yz plane as the base sketch plane",
    "the x-axis pointing ",
    "the y-axis pointing ",
    "the z-axis pointing ",
    "draw a half-sectional view",
    "draw a half sectional view",
    "revolve it around the ",
    "select the ",
    "create a sketch",
    "draw the center point",
    "use it as a reference to create an auxiliary plane",
    "on the auxiliary plane",
    "on the same top-face sketch",
    "on the same top face sketch",
    "exit the sketch",
    "close the profile",
    "after completing the sketch",
    "after closing the profile",
    "use the revolved boss command",
    "completing the construction of",
)

_SEQUENCE_ONLY_CLAUSES = {
    "first",
    "second",
    "third",
    "fourth",
    "fifth",
    "next",
    "then",
    "finally",
    "lastly",
}

_SPECIFICITY_TOKENS = (
    "corner",
    "corners",
    "centered",
    "centred",
    "center ",
    "centers ",
    "coordinate",
    "position",
    "spacing",
    "side length",
    "coincides with the center",
    "row",
    "column",
    "grid",
    "left",
    "right",
    "top",
    "bottom",
    "front",
    "back",
    "through the lugs",
    "pitch circle",
)


def _shape_token_describes_pattern_layout(text: str) -> bool:
    return bool(
        re.search(
            r"\b(square|rectangular)\s+(array|pattern|grid)\b",
            text,
            flags=re.IGNORECASE,
        )
    )


def build_interpretation_summary_from_clauses(
    clauses: list[RequirementClauseInterpretation],
    *,
    bundle: RequirementEvidenceBundle,
) -> RequirementInterpretationSummary:
    applicable = [
        clause
        for clause in clauses
        if clause.status != RequirementClauseStatus.NOT_APPLICABLE
    ]
    resolved = [
        clause
        for clause in applicable
        if clause.status
        in {
            RequirementClauseStatus.VERIFIED,
            RequirementClauseStatus.CONTRADICTED,
        }
    ]
    insufficient = [
        clause.clause_id
        for clause in applicable
        if clause.status == RequirementClauseStatus.INSUFFICIENT_EVIDENCE
    ]
    coverage_confidence = (
        float(len(resolved) / len(applicable))
        if applicable
        else 1.0
    )
    observation_tags = list(
        dict.fromkeys(
            [
                *bundle.observation_tags,
                *(
                    tag
                    for clause in clauses
                    for tag in clause.observation_tags
                    if isinstance(tag, str) and tag.strip()
                ),
            ]
        )
    )
    decision_hints = list(
        dict.fromkeys(
            [
                *bundle.decision_hints,
                *(
                    hint
                    for clause in clauses
                    for hint in clause.decision_hints
                    if isinstance(hint, str) and hint.strip()
                ),
            ]
        )
    )
    return RequirementInterpretationSummary(
        clause_interpretations=clauses,
        legacy_checks=_project_clause_checks(clauses),
        coverage_confidence=max(0.0, min(1.0, coverage_confidence)),
        insufficient_evidence=insufficient,
        observation_tags=observation_tags,
        decision_hints=decision_hints,
    )


def interpret_requirement_clauses(
    *,
    bundle: RequirementEvidenceBundle,
    requirements: dict[str, Any] | None,
    requirement_text: str | None,
    supplemental_checks: list[RequirementCheck] | None = None,
) -> RequirementInterpretationSummary:
    text = _normalize_requirement_text(requirements, requirement_text) or bundle.requirement_text
    clauses = bundle.requirement_clauses or _split_requirement_clauses(text)
    check_index = _index_supplemental_checks(supplemental_checks or [])
    interpretations = [
        _interpret_clause(
            clause_text=clause_text,
            index=index,
            bundle=bundle,
            check_index=check_index,
        )
        for index, clause_text in enumerate(clauses, start=1)
    ]
    return build_interpretation_summary_from_clauses(interpretations, bundle=bundle)


def _index_supplemental_checks(
    checks: list[RequirementCheck],
) -> dict[str, RequirementCheck]:
    return {
        check.check_id: check
        for check in checks
        if isinstance(check.check_id, str) and check.check_id.strip()
    }


def _slugify_clause(clause_text: str, index: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", clause_text.lower()).strip("_")
    return slug or f"clause_{index}"


def _interpret_clause(
    *,
    clause_text: str,
    index: int,
    bundle: RequirementEvidenceBundle,
    check_index: dict[str, RequirementCheck],
) -> RequirementClauseInterpretation:
    text = str(clause_text or "").strip()
    lowered = text.lower()
    clause_id = _slugify_clause(text, index)
    clause_tags = _classify_clause_tags(lowered)

    if _is_operation_only_clause(lowered):
        return RequirementClauseInterpretation(
            clause_id=clause_id,
            clause_text=text,
            status=RequirementClauseStatus.NOT_APPLICABLE,
            evidence="process/setup clause does not directly constrain the final artifact",
            observation_tags=clause_tags,
            decision_hints=[],
        )

    feature_grounded_result = _interpret_feature_grounded_clause(
        clause_id=clause_id,
        clause_text=text,
        clause_tags=clause_tags,
        bundle=bundle,
        check_index=check_index,
    )
    if feature_grounded_result is not None:
        return feature_grounded_result

    dimension_result = _interpret_dimension_clause(
        clause_id=clause_id,
        clause_text=text,
        clause_tags=clause_tags,
        bundle=bundle,
        check_index=check_index,
    )
    if dimension_result is not None:
        return dimension_result

    feature_result = _interpret_feature_clause(
        clause_id=clause_id,
        clause_text=text,
        clause_tags=clause_tags,
        check_index=check_index,
        bundle=bundle,
    )
    if feature_result is not None:
        return feature_result

    return RequirementClauseInterpretation(
        clause_id=clause_id,
        clause_text=text,
        status=RequirementClauseStatus.INSUFFICIENT_EVIDENCE,
        evidence="No geometry-grounded clause interpreter matched this requirement yet.",
        observation_tags=clause_tags + ["insufficient_evidence"],
        decision_hints=["inspect more geometry/topology evidence before completion"],
    )


def _is_operation_only_clause(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if normalized in _SEQUENCE_ONLY_CLAUSES:
        return True
    if any(normalized.startswith(prefix) for prefix in _PROCESS_SETUP_PREFIXES):
        return True
    if re.match(
        r"^to create an? .*(?:revolved|axisymmetric|rotational) structure$",
        normalized,
        flags=re.IGNORECASE,
    ):
        return True
    if re.match(
        r"^completing the .+ construction$",
        normalized,
        flags=re.IGNORECASE,
    ):
        return True
    if re.search(
        r"\bperform\s+a\s+360(?:\.\d+)?(?:°|\s*degree)?\s+revolution\b",
        normalized,
        flags=re.IGNORECASE,
    ):
        return True
    return False


def _classify_clause_tags(text: str) -> list[str]:
    tags: list[str] = []
    if _is_operation_only_clause(text):
        tags.append("clause:process_setup")
    if any(token in text for token in _BODY_SHAPE_TOKENS) and not _shape_token_describes_pattern_layout(text):
        tags.append("clause:body_shape")
    if "hole" in text or "bore" in text or "countersink" in text:
        tags.append("clause:hole")
    if any(token in text for token in ("slot", "u-slot", "u slot", "notch", "channel")):
        tags.append("clause:notch_like")
    if "groove" in text or "recess" in text or "pocket" in text:
        tags.append("clause:local_feature")
    if "pattern" in text or "array" in text or "grid" in text:
        tags.append("clause:pattern")
    if "fillet" in text:
        tags.append("clause:fillet")
    if "chamfer" in text:
        tags.append("clause:chamfer")
    if "sweep" in text:
        tags.append("clause:sweep")
    if "revolve" in text or "washer" in text or "cylinder" in text or "ring" in text:
        tags.append("clause:axisymmetric_body")
    if "thick" in text or "thickness" in text:
        tags.append("clause:thickness")
    if re.search(r"\b[xyz]\s*=\s*-?\d", text):
        tags.append("clause:coordinate")
    if any(token in text for token in ("direction", "top face", "bottom face", "left", "right")):
        tags.append("clause:local_feature")
    return list(dict.fromkeys(tags))


def _extract_measurements(text: str) -> list[float]:
    return [
        float(match)
        for match in re.findall(
            r"(-?\d+(?:\.\d+)?)\s*(?:mm|millimeter|millimeters)?",
            text,
            flags=re.IGNORECASE,
        )
    ]


def _close_enough(lhs: float, rhs: float) -> bool:
    tolerance = max(1.0, abs(rhs) * 0.05)
    return abs(float(lhs) - float(rhs)) <= tolerance


def _extract_coordinate_pair(text: str) -> tuple[float, float] | None:
    match = re.search(
        r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)",
        str(text or ""),
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return float(match.group(1)), float(match.group(2))


def _extract_evidence_float(evidence: Any, field_name: str) -> float | None:
    match = re.search(
        rf"{re.escape(field_name)}=\s*(-?\d+(?:\.\d+)?)",
        str(evidence or ""),
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return float(match.group(1))


def _extract_evidence_float_list(evidence: Any, field_name: str) -> list[float] | None:
    match = re.search(
        rf"{re.escape(field_name)}=\s*\[([^\]]+)\]",
        str(evidence or ""),
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    values = re.findall(r"-?\d+(?:\.\d+)?", match.group(1))
    if not values:
        return None
    return [float(value) for value in values]


def _extract_evidence_token(evidence: Any, field_name: str) -> str | None:
    match = re.search(
        rf"{re.escape(field_name)}=\s*([A-Za-z0-9_:.+-]+)",
        str(evidence or ""),
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return str(match.group(1)).strip()


def _topology_face_summaries(bundle: RequirementEvidenceBundle) -> list[dict[str, Any]]:
    face_summaries = bundle.topology_facts.get("face_summaries")
    if not isinstance(face_summaries, list):
        return []
    return [item for item in face_summaries if isinstance(item, dict)]


def _axis_name_index(axis_name: str | None) -> int | None:
    axis = str(axis_name or "").strip().upper()
    if axis not in {"X", "Y", "Z"}:
        return None
    return {"X": 0, "Y": 1, "Z": 2}[axis]


def _find_planar_polygon_pocket_depth(
    bundle: RequirementEvidenceBundle,
    *,
    host_plane: str,
    edge_count: int,
) -> float | None:
    bbox_min = [
        float(value)
        for value in (bundle.geometry_facts.get("bbox_min") or [])
        if isinstance(value, (int, float))
    ]
    bbox_max = [
        float(value)
        for value in (bundle.geometry_facts.get("bbox_max") or [])
        if isinstance(value, (int, float))
    ]
    if len(bbox_min) < 3 or len(bbox_max) < 3:
        return None
    host_axis = str(host_plane or "").strip().lower()
    if host_axis not in {"top", "bottom"}:
        return None
    host_z = bbox_max[2] if host_axis == "top" else bbox_min[2]
    candidate_depths: list[float] = []
    for face in _topology_face_summaries(bundle):
        if str(face.get("geom_type") or "").strip().upper() != "PLANE":
            continue
        if int(face.get("edge_count") or 0) != int(edge_count):
            continue
        normal = face.get("normal") or []
        if not (
            isinstance(normal, list)
            and len(normal) >= 3
            and isinstance(normal[2], (int, float))
            and abs(float(normal[2])) >= 0.9
        ):
            continue
        face_bbox = face.get("bbox") or {}
        if not isinstance(face_bbox, dict):
            continue
        zmin = face_bbox.get("zmin")
        zmax = face_bbox.get("zmax")
        if not isinstance(zmin, (int, float)) or not isinstance(zmax, (int, float)):
            continue
        face_z = (float(zmin) + float(zmax)) / 2.0
        if host_axis == "top" and face_z < host_z - 1e-3:
            candidate_depths.append(round(host_z - face_z, 3))
        if host_axis == "bottom" and face_z > host_z + 1e-3:
            candidate_depths.append(round(face_z - host_z, 3))
    if not candidate_depths:
        return None
    return min(candidate_depths)


def _requirement_mentions_polygon_vertex_list(bundle: RequirementEvidenceBundle) -> bool:
    lowered = str(bundle.requirement_text or "").lower()
    if "vertices at" not in lowered:
        return False
    return any(
        token in lowered
        for token in ("triangle", "triangular", "polygon", "pocket", "profile")
    )


def _fillet_check_matches_named_edge_clause(
    clause_text: str,
    fillet_check: RequirementCheck | None,
) -> bool:
    if fillet_check is None:
        return False
    lowered = str(clause_text or "").lower()
    evidence = str(fillet_check.evidence or fillet_check.check_id or "").lower()
    if "bottom" in lowered and "bottom" not in evidence:
        return False
    if "outer" in lowered and "outer" not in evidence:
        return False
    if "parallel to the y" in lowered and "y_parallel" not in evidence:
        return False
    if "parallel to the x" in lowered and "x_parallel" not in evidence:
        return False
    measurements = _extract_measurements(lowered)
    if measurements:
        match = re.search(r"radius\s*=\s*(-?\d+(?:\.\d+)?)", evidence)
        if match is None or not _close_enough(float(match.group(1)), measurements[-1]):
            return False
    return True


def _interpret_symmetric_rectangle_sketch_clause(
    *,
    clause_id: str,
    clause_text: str,
    clause_tags: list[str],
    bundle: RequirementEvidenceBundle,
) -> RequirementClauseInterpretation | None:
    lowered = clause_text.lower()
    if "rectangle" not in lowered:
        return None
    measurements = _extract_measurements(lowered)
    if len(measurements) < 2:
        return None
    plane_match = re.search(r"\b(xy|xz|yz)\s+plane\b", lowered)
    if plane_match is None:
        return None
    requirement_lower = str(bundle.requirement_text or "").lower()
    if "symmetr" not in requirement_lower and "centered about" not in requirement_lower:
        return None
    bbox = [
        float(value)
        for value in (bundle.geometry_facts.get("bbox") or [])
        if isinstance(value, (int, float))
    ]
    if len(bbox) < 3:
        return None
    plane_token = plane_match.group(1).upper()
    plane_dims = {
        "XY": [bbox[0], bbox[1]],
        "XZ": [bbox[0], bbox[2]],
        "YZ": [bbox[1], bbox[2]],
    }.get(plane_token)
    if plane_dims is None:
        return None
    remaining = list(plane_dims)
    evidence_parts: list[str] = []
    for measurement in measurements[:2]:
        match_index = next(
            (
                idx
                for idx, dimension in enumerate(remaining)
                if _close_enough(dimension, measurement)
            ),
            None,
        )
        if match_index is None:
            return None
        evidence_parts.append(f"matched_plane_dimension={measurement}")
        remaining.pop(match_index)
    return RequirementClauseInterpretation(
        clause_id=clause_id,
        clause_text=clause_text,
        status=RequirementClauseStatus.VERIFIED,
        evidence=f"{', '.join(evidence_parts)}, plane={plane_token}",
        observation_tags=clause_tags + ["geometry:bbox"],
        decision_hints=[],
    )


def _interpret_polygon_vertex_tuple_clause(
    *,
    clause_id: str,
    clause_text: str,
    clause_tags: list[str],
    bundle: RequirementEvidenceBundle,
    check_index: dict[str, RequirementCheck],
) -> RequirementClauseInterpretation | None:
    if not _is_coordinate_tuple_clause(clause_text):
        return None
    if not _requirement_mentions_polygon_vertex_list(bundle):
        return None
    if (
        _first_passed_check(
            check_index,
            "pre_solid_profile_shape_alignment",
            "feature_profile_shape_alignment",
        )
        is None
    ):
        return None
    return RequirementClauseInterpretation(
        clause_id=clause_id,
        clause_text=clause_text,
        status=RequirementClauseStatus.NOT_APPLICABLE,
        evidence="vertex-list coordinate clause is consumed by the enclosing polygon/profile clause",
        observation_tags=clause_tags + ["validation:polygon_vertex_list"],
        decision_hints=[],
    )


def _interpret_polygon_pocket_clause(
    *,
    clause_id: str,
    clause_text: str,
    clause_tags: list[str],
    bundle: RequirementEvidenceBundle,
    check_index: dict[str, RequirementCheck],
) -> RequirementClauseInterpretation | None:
    lowered = clause_text.lower()
    if not any(token in lowered for token in ("triangle", "triangular", "polygon")):
        return None
    profile_shape = _first_passed_check(
        check_index,
        "feature_profile_shape_alignment",
        "pre_solid_profile_shape_alignment",
    )
    target_face_edit = _first_passed_check(check_index, "feature_target_face_edit")
    target_face_merge = _first_passed_check(
        check_index, "feature_target_face_subtractive_merge"
    )
    if profile_shape is None or target_face_edit is None or target_face_merge is None:
        return None
    if any(token in lowered for token in ("cut-extrude", "cut extrude", "downward")):
        measurements = _extract_measurements(lowered)
        if measurements:
            pocket_depth = _find_planar_polygon_pocket_depth(
                bundle,
                host_plane="top",
                edge_count=3,
            )
            if pocket_depth is not None and _close_enough(pocket_depth, measurements[-1]):
                combined = _combine_check_evidence(
                    target_face_edit,
                    target_face_merge,
                    profile_shape,
                )
                evidence = f"matched_polygon_pocket_depth={measurements[-1]}"
                if combined:
                    evidence = f"{evidence}; {combined}"
                return RequirementClauseInterpretation(
                    clause_id=clause_id,
                    clause_text=clause_text,
                    status=RequirementClauseStatus.VERIFIED,
                    evidence=evidence,
                    observation_tags=clause_tags + ["validation:feature_alignment"],
                    decision_hints=[],
                )
        return None
    if any(token in lowered for token in ("pocket", "top surface", "top face")):
        evidence = _combine_check_evidence(
            target_face_edit,
            target_face_merge,
            profile_shape,
        )
        return RequirementClauseInterpretation(
            clause_id=clause_id,
            clause_text=clause_text,
            status=RequirementClauseStatus.VERIFIED,
            evidence=evidence or str(profile_shape.evidence or profile_shape.check_id),
            observation_tags=clause_tags + ["validation:feature_alignment"],
            decision_hints=[],
        )
    return None


def _interpret_named_fillet_clause(
    *,
    clause_id: str,
    clause_text: str,
    clause_tags: list[str],
    check_index: dict[str, RequirementCheck],
) -> RequirementClauseInterpretation | None:
    if "fillet" not in clause_text.lower():
        return None
    fillet_check = _first_passed_check(check_index, "feature_fillet")
    if not _fillet_check_matches_named_edge_clause(clause_text, fillet_check):
        return None
    return RequirementClauseInterpretation(
        clause_id=clause_id,
        clause_text=clause_text,
        status=RequirementClauseStatus.VERIFIED,
        evidence=str(fillet_check.evidence or fillet_check.check_id),
        observation_tags=clause_tags + ["validation:feature_alignment"],
        decision_hints=[],
    )


def _axisymmetric_bands(bundle: RequirementEvidenceBundle) -> list[dict[str, Any]]:
    bands = bundle.geometry_facts.get("axisymmetric_bands")
    if not isinstance(bands, list):
        return []
    normalized: list[dict[str, Any]] = []
    for band in bands:
        if not isinstance(band, dict):
            continue
        axis = str(band.get("axis", "")).upper()
        radius = band.get("radius")
        axial_range = band.get("axial_range")
        if axis not in {"X", "Y", "Z"}:
            continue
        if not isinstance(radius, (int, float)):
            continue
        if not (
            isinstance(axial_range, list)
            and len(axial_range) >= 2
            and all(isinstance(item, (int, float)) for item in axial_range[:2])
        ):
            continue
        normalized.append(
            {
                "axis": axis,
                "radius": float(radius),
                "axial_range": [float(axial_range[0]), float(axial_range[1])],
                "face_count": int(band.get("face_count", 1) or 1),
            }
        )
    return normalized


def _bundle_mentions_axisymmetric_profile(bundle: RequirementEvidenceBundle) -> bool:
    lowered = bundle.requirement_text.lower()
    return bool(
        _axisymmetric_bands(bundle)
        and any(
            token in lowered
            for token in (
                "revolve",
                "revolved",
                "axisymmetric",
                "axis of rotation",
                "axial direction",
                "shaft",
                "stud",
                "disk",
                "disc",
                "boss",
                "end cap",
                "pitch circle",
                "radius",
            )
        )
    )


def _dominant_axisymmetric_axis(bundle: RequirementEvidenceBundle) -> str | None:
    counts: dict[str, int] = {}
    for band in _axisymmetric_bands(bundle):
        axis = str(band.get("axis", "")).upper()
        counts[axis] = counts.get(axis, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda item: item[1])[0]


def _dominant_cylindrical_axis(bundle: RequirementEvidenceBundle) -> str | None:
    counts: dict[str, int] = {}
    for face in _topology_face_summaries(bundle):
        if str(face.get("geom_type") or "").strip().upper() != "CYLINDER":
            continue
        axis_direction = face.get("axis_direction") or []
        if not (
            isinstance(axis_direction, list)
            and len(axis_direction) >= 3
            and all(isinstance(item, (int, float)) for item in axis_direction[:3])
        ):
            continue
        dominant_index = max(range(3), key=lambda idx: abs(float(axis_direction[idx])))
        if abs(float(axis_direction[dominant_index])) < 0.9:
            continue
        axis_name = "XYZ"[dominant_index]
        counts[axis_name] = counts.get(axis_name, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda item: item[1])[0]


def _body_axis_bounds(
    bundle: RequirementEvidenceBundle,
    axis_name: str | None,
) -> tuple[float, float] | None:
    axis_index = _axis_name_index(axis_name)
    if axis_index is None:
        return None
    bbox_min = bundle.geometry_facts.get("bbox_min") or []
    bbox_max = bundle.geometry_facts.get("bbox_max") or []
    if not (
        isinstance(bbox_min, list)
        and isinstance(bbox_max, list)
        and len(bbox_min) >= 3
        and len(bbox_max) >= 3
        and isinstance(bbox_min[axis_index], (int, float))
        and isinstance(bbox_max[axis_index], (int, float))
    ):
        return None
    return float(bbox_min[axis_index]), float(bbox_max[axis_index])


def _find_through_cylindrical_faces(
    bundle: RequirementEvidenceBundle,
    *,
    axis_name: str | None = None,
) -> list[dict[str, Any]]:
    selected_axis = axis_name or _dominant_axisymmetric_axis(bundle) or _dominant_cylindrical_axis(bundle)
    axis_index = _axis_name_index(selected_axis)
    body_bounds = _body_axis_bounds(bundle, selected_axis)
    if axis_index is None or body_bounds is None:
        return []
    body_min, body_max = body_bounds
    span = body_max - body_min
    tolerance = max(1.0, abs(span) * 0.08)
    matches: list[dict[str, Any]] = []
    for face in _topology_face_summaries(bundle):
        if str(face.get("geom_type") or "").strip().upper() != "CYLINDER":
            continue
        axis_direction = face.get("axis_direction") or []
        if not (
            isinstance(axis_direction, list)
            and len(axis_direction) >= 3
            and isinstance(axis_direction[axis_index], (int, float))
            and abs(float(axis_direction[axis_index])) >= 0.9
        ):
            continue
        face_bbox = face.get("bbox") or {}
        if not isinstance(face_bbox, dict):
            continue
        axis_min = face_bbox.get(("xmin", "ymin", "zmin")[axis_index])
        axis_max = face_bbox.get(("xmax", "ymax", "zmax")[axis_index])
        if not isinstance(axis_min, (int, float)) or not isinstance(axis_max, (int, float)):
            continue
        if abs(float(axis_min) - body_min) <= tolerance and abs(float(axis_max) - body_max) <= tolerance:
            matches.append(face)
    return matches


def _axisymmetric_origin_pose(bundle: RequirementEvidenceBundle) -> dict[str, Any] | None:
    axis_name = _dominant_axisymmetric_axis(bundle)
    axis_index = _axis_name_index(axis_name)
    if axis_index is None:
        return None
    bbox_min = [
        float(value)
        for value in (bundle.geometry_facts.get("bbox_min") or [])
        if isinstance(value, (int, float))
    ]
    bbox_max = [
        float(value)
        for value in (bundle.geometry_facts.get("bbox_max") or [])
        if isinstance(value, (int, float))
    ]
    if len(bbox_min) < 3 or len(bbox_max) < 3:
        return None
    perpendicular_indices = [idx for idx in range(3) if idx != axis_index]
    if not perpendicular_indices:
        return None
    radial_span = max(
        float(bbox_max[idx]) - float(bbox_min[idx]) for idx in perpendicular_indices
    )
    tolerance = max(1.0, abs(radial_span) * 0.08)
    bbox_center_offsets = [
        abs((float(bbox_min[idx]) + float(bbox_max[idx])) / 2.0)
        for idx in perpendicular_indices
    ]
    if any(offset > tolerance for offset in bbox_center_offsets):
        return None
    axis_origin_offsets: list[float] = []
    for face in _topology_face_summaries(bundle):
        axis_direction = face.get("axis_direction") or []
        if not (
            isinstance(axis_direction, list)
            and len(axis_direction) >= 3
            and isinstance(axis_direction[axis_index], (int, float))
            and abs(float(axis_direction[axis_index])) >= 0.9
        ):
            continue
        axis_origin = face.get("axis_origin") or []
        if not (
            isinstance(axis_origin, list)
            and len(axis_origin) >= 3
            and all(isinstance(item, (int, float)) for item in axis_origin[:3])
        ):
            continue
        axis_origin_offsets.append(
            max(abs(float(axis_origin[idx])) for idx in perpendicular_indices)
        )
    if axis_origin_offsets and any(offset > tolerance for offset in axis_origin_offsets):
        return None
    return {
        "axis": axis_name,
        "tolerance": round(tolerance, 3),
        "bbox_center_offsets": [round(value, 3) for value in bbox_center_offsets],
        "axis_origin_offsets": [round(value, 3) for value in axis_origin_offsets],
    }


def _outer_axisymmetric_segments(
    bundle: RequirementEvidenceBundle,
    *,
    axis_name: str | None = None,
) -> list[dict[str, Any]]:
    selected_axis = axis_name or _dominant_axisymmetric_axis(bundle)
    if selected_axis is None:
        return []
    grouped: dict[tuple[float, float], dict[str, Any]] = {}
    for band in _axisymmetric_bands(bundle):
        if str(band.get("axis", "")).upper() != selected_axis:
            continue
        axial_range = band.get("axial_range") or [0.0, 0.0]
        if len(axial_range) < 2:
            continue
        axial_min = float(axial_range[0])
        axial_max = float(axial_range[1])
        key = (round(axial_min, 3), round(axial_max, 3))
        radius = float(band.get("radius", 0.0))
        existing = grouped.get(key)
        if existing is None or radius > float(existing.get("radius", 0.0)):
            grouped[key] = {
                "axis": selected_axis,
                "radius": radius,
                "axial_range": [axial_min, axial_max],
            }
    return sorted(
        grouped.values(),
        key=lambda item: (
            float((item.get("axial_range") or [0.0, 0.0])[0]),
            float((item.get("axial_range") or [0.0, 0.0])[1]),
            float(item.get("radius", 0.0)),
        ),
    )


def _extract_axisymmetric_radius_length_segments(text: str) -> list[tuple[float, float]]:
    matches = re.findall(
        r"(?:radius|r)\s*([0-9]+(?:\.\d+)?)\s*(?:millimeters?|mm)?\s*\(\s*length\s*([0-9]+(?:\.\d+)?)",
        str(text or ""),
        flags=re.IGNORECASE,
    )
    return [(float(radius), float(length)) for radius, length in matches]


def _interpret_axisymmetric_segment_clause(
    *,
    clause_id: str,
    clause_text: str,
    clause_tags: list[str],
    bundle: RequirementEvidenceBundle,
) -> RequirementClauseInterpretation | None:
    lowered = clause_text.lower()
    if not _bundle_mentions_axisymmetric_profile(bundle):
        return None
    if "axial direction" not in lowered or "radius" not in lowered or "length" not in lowered:
        return None
    expected_segments = _extract_axisymmetric_radius_length_segments(lowered)
    if len(expected_segments) < 2:
        return None
    axis_name = _dominant_axisymmetric_axis(bundle)
    observed_segments = [
        (
            round(float(segment["radius"]), 3),
            round(
                float(segment["axial_range"][1]) - float(segment["axial_range"][0]),
                3,
            ),
        )
        for segment in _outer_axisymmetric_segments(bundle, axis_name=axis_name)
    ]
    if len(observed_segments) != len(expected_segments):
        return RequirementClauseInterpretation(
            clause_id=clause_id,
            clause_text=clause_text,
            status=RequirementClauseStatus.CONTRADICTED,
            evidence=(
                f"expected_axisymmetric_segments={expected_segments}, "
                f"observed_axisymmetric_segments={observed_segments}"
            ),
            observation_tags=clause_tags + ["geometry:axisymmetric_band"],
            decision_hints=["repair the axisymmetric segment radii or axial spans"],
        )
    if all(
        _close_enough(observed_radius, expected_radius)
        and _close_enough(observed_length, expected_length)
        for (observed_radius, observed_length), (expected_radius, expected_length) in zip(
            observed_segments,
            expected_segments,
        )
    ):
        return RequirementClauseInterpretation(
            clause_id=clause_id,
            clause_text=clause_text,
            status=RequirementClauseStatus.VERIFIED,
            evidence=(
                f"matched_axisymmetric_segments={observed_segments}, axis={axis_name or '<unknown>'}"
            ),
            observation_tags=clause_tags + ["geometry:axisymmetric_band"],
            decision_hints=[],
        )
    return RequirementClauseInterpretation(
        clause_id=clause_id,
        clause_text=clause_text,
        status=RequirementClauseStatus.CONTRADICTED,
        evidence=(
            f"expected_axisymmetric_segments={expected_segments}, "
            f"observed_axisymmetric_segments={observed_segments}"
        ),
        observation_tags=clause_tags + ["geometry:axisymmetric_band"],
        decision_hints=["repair the axisymmetric segment radii or axial spans"],
    )


def _interpret_axisymmetric_profile_point_clause(
    *,
    clause_id: str,
    clause_text: str,
    clause_tags: list[str],
    bundle: RequirementEvidenceBundle,
) -> RequirementClauseInterpretation | None:
    lowered = clause_text.lower()
    if not _bundle_mentions_axisymmetric_profile(bundle):
        return None
    if not any(
        token in lowered
        for token in (
            "start from point",
            "corresponding to",
            "base thickness",
            "total height",
            "vertically upward",
            "vertically downward",
            "horizontally outward",
            "horizontally inward",
        )
    ):
        return None
    pair = _extract_coordinate_pair(clause_text)
    if pair is None:
        return None
    radius_value = abs(float(pair[0]))
    axial_value = float(pair[1])
    axis_name = _dominant_axisymmetric_axis(bundle)
    for band in _axisymmetric_bands(bundle):
        if axis_name is not None and str(band.get("axis", "")).upper() != axis_name:
            continue
        band_radius = float(band.get("radius", 0.0))
        axial_range = band.get("axial_range") or [0.0, 0.0]
        if len(axial_range) < 2:
            continue
        axial_min = float(axial_range[0])
        axial_max = float(axial_range[1])
        if not _close_enough(band_radius, radius_value):
            continue
        if axial_min - 1.0 <= axial_value <= axial_max + 1.0:
            return RequirementClauseInterpretation(
                clause_id=clause_id,
                clause_text=clause_text,
                status=RequirementClauseStatus.VERIFIED,
                evidence=(
                    f"matched_axisymmetric_point={[round(radius_value, 3), round(axial_value, 3)]}, "
                    f"observed_band_radius={round(band_radius, 3)}, "
                    f"observed_axial_range={[round(axial_min, 3), round(axial_max, 3)]}, "
                    f"axis={band.get('axis')}"
                ),
                observation_tags=clause_tags + ["geometry:axisymmetric_band"],
                decision_hints=[],
            )
    return None


def _interpret_axisymmetric_rotation_axis_clause(
    *,
    clause_id: str,
    clause_text: str,
    clause_tags: list[str],
    bundle: RequirementEvidenceBundle,
) -> RequirementClauseInterpretation | None:
    lowered = clause_text.lower()
    if not _bundle_mentions_axisymmetric_profile(bundle):
        return None
    if "axis of rotation" not in lowered and "center axis" not in lowered:
        return None
    pose = _axisymmetric_origin_pose(bundle)
    if pose is None:
        return None
    axis_name = str(pose.get("axis") or "")
    if "vertical" in lowered and axis_name != "Z":
        return None
    if "horizontal" in lowered and axis_name == "Z":
        return None
    evidence = (
        f"matched_axisymmetric_rotation_axis={axis_name}, "
        f"bbox_center_offsets={pose.get('bbox_center_offsets')}, "
        f"axis_origin_offsets={pose.get('axis_origin_offsets') or ['unavailable']}, "
        f"tolerance={pose.get('tolerance')}"
    )
    return RequirementClauseInterpretation(
        clause_id=clause_id,
        clause_text=clause_text,
        status=RequirementClauseStatus.VERIFIED,
        evidence=evidence,
        observation_tags=clause_tags + ["geometry:axisymmetric_band"],
        decision_hints=[],
    )


def _interpret_hole_through_thickness_clause(
    *,
    clause_id: str,
    clause_text: str,
    clause_tags: list[str],
    bundle: RequirementEvidenceBundle,
    check_index: dict[str, RequirementCheck],
) -> RequirementClauseInterpretation | None:
    lowered = clause_text.lower()
    if "clause:hole" not in clause_tags:
        return None
    if "through" not in lowered or "thickness" not in lowered:
        return None
    hole_feature = _first_passed_check(
        check_index,
        "feature_hole",
        "feature_hole_position_alignment",
        "feature_hole_exact_center_set",
    )
    hole_alignment = _first_passed_check(
        check_index,
        "feature_hole_position_alignment",
        "feature_hole_exact_center_set",
        "feature_local_anchor_alignment",
    )
    if hole_feature is None or hole_alignment is None:
        return None
    pattern = _first_passed_check(check_index, "feature_pattern")
    if "bolt" in lowered and pattern is None:
        return None
    axis_name = _dominant_axisymmetric_axis(bundle) or _dominant_cylindrical_axis(bundle)
    through_faces = _find_through_cylindrical_faces(bundle, axis_name=axis_name)
    if not through_faces:
        return None
    body_bounds = _body_axis_bounds(bundle, axis_name)
    radii = sorted(
        {
            round(float(face.get("radius")), 3)
            for face in through_faces
            if isinstance(face.get("radius"), (int, float))
        }
    )
    evidence_parts = [
        f"through_hole_face_count={len(through_faces)}",
        f"through_hole_radii={radii}",
    ]
    if body_bounds is not None:
        evidence_parts.append(
            f"body_axis_span={[round(body_bounds[0], 3), round(body_bounds[1], 3)]}"
        )
    combined = _combine_check_evidence(hole_alignment, hole_feature, pattern)
    evidence = ", ".join(evidence_parts[:3])
    if combined:
        evidence = f"{evidence}; {combined}"
    return RequirementClauseInterpretation(
        clause_id=clause_id,
        clause_text=clause_text,
        status=RequirementClauseStatus.VERIFIED,
        evidence=evidence,
        observation_tags=clause_tags + ["geometry:through_hole_span", "validation:feature_alignment"],
        decision_hints=[],
    )


def _interpret_plane_anchored_axisymmetric_band_clause(
    *,
    clause_id: str,
    clause_text: str,
    clause_tags: list[str],
    bundle: RequirementEvidenceBundle,
) -> RequirementClauseInterpretation | None:
    lowered = clause_text.lower()
    if not _axisymmetric_bands(bundle):
        return None
    if "extrud" not in lowered or "diameter" not in lowered:
        return None
    if not any(token in lowered for token in ("disk", "disc", "end cap", "boss", "cylinder")):
        return None
    direction = "downward" if "downward" in lowered else ("upward" if "upward" in lowered else None)
    if direction is None:
        return None
    measurements = _extract_measurements(lowered)
    if len(measurements) < 2:
        return None
    target_diameter = float(measurements[0])
    target_thickness = float(measurements[-1])
    target_radius = target_diameter / 2.0
    axis_name = _dominant_axisymmetric_axis(bundle)
    for band in _axisymmetric_bands(bundle):
        if axis_name is not None and str(band.get("axis", "")).upper() != axis_name:
            continue
        band_radius = float(band.get("radius", 0.0))
        axial_range = band.get("axial_range") or [0.0, 0.0]
        if len(axial_range) < 2:
            continue
        axial_min = float(axial_range[0])
        axial_max = float(axial_range[1])
        thickness = axial_max - axial_min
        anchor_matches = (
            abs(axial_max) <= 1.0 if direction == "downward" else abs(axial_min) <= 1.0
        )
        if (
            _close_enough(band_radius, target_radius)
            and _close_enough(thickness, target_thickness)
            and anchor_matches
        ):
            return RequirementClauseInterpretation(
                clause_id=clause_id,
                clause_text=clause_text,
                status=RequirementClauseStatus.VERIFIED,
                evidence=(
                    f"matched_axisymmetric_band_radius={round(band_radius, 3)}, "
                    f"matched_axisymmetric_band_thickness={round(thickness, 3)}, "
                    f"observed_axial_range={[round(axial_min, 3), round(axial_max, 3)]}, "
                    f"axis={band.get('axis')}, direction={direction}"
                ),
                observation_tags=clause_tags + ["geometry:axisymmetric_band"],
                decision_hints=[],
            )
    return None


def _interpret_symmetric_extrude_clause(
    *,
    clause_id: str,
    clause_text: str,
    clause_tags: list[str],
    bundle: RequirementEvidenceBundle,
) -> RequirementClauseInterpretation | None:
    lowered = clause_text.lower()
    if "extrude" not in lowered or "symmetrically" not in lowered:
        return None
    plane_match = re.search(r"\b(xy|xz|yz)\s+plane\b", lowered, flags=re.IGNORECASE)
    half_span_match = re.search(
        r"symmetrically(?: [^.,;]{0,40})? by\s*([0-9]+(?:\.\d+)?)",
        lowered,
        flags=re.IGNORECASE,
    )
    if plane_match is None or half_span_match is None:
        return None
    bbox = bundle.geometry_facts.get("bbox") or []
    bbox_min = bundle.geometry_facts.get("bbox_min") or []
    bbox_max = bundle.geometry_facts.get("bbox_max") or []
    if not (
        isinstance(bbox, list)
        and isinstance(bbox_min, list)
        and isinstance(bbox_max, list)
        and len(bbox) >= 3
        and len(bbox_min) >= 3
        and len(bbox_max) >= 3
    ):
        return None
    axis_index = {"XY": 2, "XZ": 1, "YZ": 0}[str(plane_match.group(1)).upper()]
    half_span = float(half_span_match.group(1))
    observed_min = float(bbox_min[axis_index])
    observed_max = float(bbox_max[axis_index])
    if not (_close_enough(observed_min, -half_span) and _close_enough(observed_max, half_span)):
        return None
    dims_match = re.search(
        r"([0-9]+(?:\.\d+)?)\s*x\s*([0-9]+(?:\.\d+)?)\s*x\s*([0-9]+(?:\.\d+)?)",
        lowered,
        flags=re.IGNORECASE,
    )
    if dims_match is not None:
        expected_dims = sorted(float(dims_match.group(index)) for index in range(1, 4))
        observed_dims = sorted(float(value) for value in bbox[:3])
        if not all(
            _close_enough(observed, expected)
            for observed, expected in zip(observed_dims, expected_dims)
        ):
            return RequirementClauseInterpretation(
                clause_id=clause_id,
                clause_text=clause_text,
                status=RequirementClauseStatus.CONTRADICTED,
                evidence=(
                    f"expected_bbox={expected_dims}, observed_bbox={observed_dims}, "
                    f"observed_axis_range={[round(observed_min, 3), round(observed_max, 3)]}"
                ),
                observation_tags=clause_tags + ["geometry:bbox"],
                decision_hints=["repair the symmetric span or overall body dimensions"],
            )
    return RequirementClauseInterpretation(
        clause_id=clause_id,
        clause_text=clause_text,
        status=RequirementClauseStatus.VERIFIED,
        evidence=(
            f"matched_symmetric_axis_range={[round(observed_min, 3), round(observed_max, 3)]}, "
            f"half_span={round(half_span, 3)}"
        ),
        observation_tags=clause_tags + ["geometry:bbox"],
        decision_hints=[],
    )


def _first_passed_check(
    check_index: dict[str, RequirementCheck],
    *check_ids: str,
) -> RequirementCheck | None:
    for check_id in check_ids:
        check = check_index.get(check_id)
        if check is not None and check.status == RequirementCheckStatus.PASS:
            return check
    return None


def _first_failed_check(
    check_index: dict[str, RequirementCheck],
    *check_ids: str,
) -> RequirementCheck | None:
    for check_id in check_ids:
        check = check_index.get(check_id)
        if check is not None and check.status == RequirementCheckStatus.FAIL:
            return check
    return None


def _combine_check_evidence(*checks: RequirementCheck | None) -> str:
    evidence_parts: list[str] = []
    for check in checks:
        if check is None:
            continue
        detail = str(check.evidence or check.check_id or "").strip()
        if detail and detail not in evidence_parts:
            evidence_parts.append(detail)
    return "; ".join(evidence_parts)


def _interpret_mixed_nested_profile_clause(
    *,
    clause_id: str,
    clause_text: str,
    clause_tags: list[str],
    check_index: dict[str, RequirementCheck],
) -> RequirementClauseInterpretation | None:
    lowered = clause_text.lower()
    inner_void = _first_passed_check(check_index, "feature_inner_void_cutout")
    profile_shape = _first_passed_check(
        check_index,
        "feature_profile_shape_alignment",
        "pre_solid_profile_shape_alignment",
    )
    if inner_void is None or profile_shape is None:
        return None
    measurements = _extract_measurements(lowered)
    if not measurements:
        return None

    inner_void_evidence = str(inner_void.evidence or "")
    if "circle" in lowered and "diameter" in lowered:
        outer_diameter = _extract_evidence_float(inner_void_evidence, "outer_diameter")
        if outer_diameter is not None and _close_enough(outer_diameter, measurements[0]):
            evidence = _combine_check_evidence(inner_void, profile_shape)
            return RequirementClauseInterpretation(
                clause_id=clause_id,
                clause_text=clause_text,
                status=RequirementClauseStatus.VERIFIED,
                evidence=evidence or str(inner_void.evidence or inner_void.check_id),
                observation_tags=clause_tags + ["validation:feature_alignment"],
                decision_hints=[],
            )

    if "square" in lowered or "rectangle" in lowered:
        inner_dims = _extract_evidence_float_list(inner_void_evidence, "inner_dims")
        if inner_dims is None or len(inner_dims) < 2:
            return None
        expected_dims = (
            [measurements[0], measurements[0]]
            if "square" in lowered and measurements
            else measurements[:2]
        )
        if len(expected_dims) < 2:
            return None
        observed_dims = sorted(float(value) for value in inner_dims[:2])
        requested_dims = sorted(float(value) for value in expected_dims[:2])
        if all(
            _close_enough(observed, expected)
            for observed, expected in zip(observed_dims, requested_dims)
        ):
            evidence = _combine_check_evidence(inner_void, profile_shape)
            return RequirementClauseInterpretation(
                clause_id=clause_id,
                clause_text=clause_text,
                status=RequirementClauseStatus.VERIFIED,
                evidence=evidence or str(inner_void.evidence or inner_void.check_id),
                observation_tags=clause_tags + ["validation:feature_alignment"],
                decision_hints=[],
            )
    return None


def _primary_bbox_axis_index(bundle: RequirementEvidenceBundle) -> int | None:
    axis_name = _dominant_axisymmetric_axis(bundle)
    if axis_name is not None:
        axis_index = _axis_name_index(axis_name)
        if axis_index is not None:
            return axis_index
    bbox = bundle.geometry_facts.get("bbox") or []
    if not (
        isinstance(bbox, list)
        and len(bbox) >= 3
        and all(isinstance(value, (int, float)) for value in bbox[:3])
    ):
        return None
    return max(range(3), key=lambda idx: abs(float(bbox[idx])))


def _annular_height_matches_requirement(
    *,
    bundle: RequirementEvidenceBundle,
    axial_window: list[float],
    height_match_mode: str | None,
    target_height: float,
) -> bool:
    if len(axial_window) < 2:
        return False
    axis_min = float(axial_window[0])
    axis_max = float(axial_window[1])
    anchor_mode = str(height_match_mode or "world_space:center").strip().lower()
    if ":" in anchor_mode:
        frame_mode, anchor_name = anchor_mode.split(":", 1)
    else:
        frame_mode, anchor_name = "world_space", anchor_mode

    window_min = axis_min
    window_max = axis_max
    if frame_mode == "bbox_min_normalized":
        axis_index = _primary_bbox_axis_index(bundle)
        bbox_min = bundle.geometry_facts.get("bbox_min") or []
        if (
            axis_index is not None
            and isinstance(bbox_min, list)
            and len(bbox_min) >= 3
            and isinstance(bbox_min[axis_index], (int, float))
        ):
            body_min = float(bbox_min[axis_index])
            window_min -= body_min
            window_max -= body_min
    if anchor_name == "top_edge":
        return _close_enough(window_max, target_height)
    if anchor_name == "bottom_edge":
        return _close_enough(window_min, target_height)
    return _close_enough((window_min + window_max) / 2.0, target_height)


def _interpret_named_plane_positive_extrude_clause(
    *,
    clause_id: str,
    clause_text: str,
    clause_tags: list[str],
    check_index: dict[str, RequirementCheck],
) -> RequirementClauseInterpretation | None:
    lowered = clause_text.lower()
    if "extrud" not in lowered:
        return None
    measurements = _extract_measurements(lowered)
    if not measurements:
        return None
    positive_extrude_check = _first_passed_check(
        check_index, "feature_named_plane_positive_extrude_span"
    ) or _first_failed_check(check_index, "feature_named_plane_positive_extrude_span")
    if positive_extrude_check is None:
        return None
    required_extent = _extract_evidence_float(
        positive_extrude_check.evidence, "required_minimum_extent"
    )
    if required_extent is None or not _close_enough(required_extent, measurements[-1]):
        return None
    status = (
        RequirementClauseStatus.VERIFIED
        if positive_extrude_check.status == RequirementCheckStatus.PASS
        else RequirementClauseStatus.CONTRADICTED
    )
    return RequirementClauseInterpretation(
        clause_id=clause_id,
        clause_text=clause_text,
        status=status,
        evidence=str(positive_extrude_check.evidence or positive_extrude_check.check_id),
        observation_tags=clause_tags + ["validation:feature_alignment"],
        decision_hints=[]
        if status == RequirementClauseStatus.VERIFIED
        else ["repair the datum-plane span before downstream features"],
    )


def _interpret_annular_groove_local_clause(
    *,
    clause_id: str,
    clause_text: str,
    clause_tags: list[str],
    bundle: RequirementEvidenceBundle,
    check_index: dict[str, RequirementCheck],
) -> RequirementClauseInterpretation | None:
    lowered = clause_text.lower()
    groove_check = _first_passed_check(
        check_index,
        "feature_revolved_groove_alignment",
        "feature_annular_groove",
        "feature_revolved_groove_result",
    )
    if groove_check is None:
        return None

    groove_evidence = str(groove_check.evidence or "")
    groove_dims = _extract_evidence_float_list(groove_evidence, "groove_dims")
    axial_window = _extract_evidence_float_list(groove_evidence, "axial_window")
    height_match_mode = _extract_evidence_token(groove_evidence, "height_match_mode")
    measurements = _extract_measurements(lowered)

    if "rectangle" in lowered and groove_dims is not None and len(measurements) >= 2:
        observed_dims = sorted(float(value) for value in groove_dims[:2])
        requested_dims = sorted(float(value) for value in measurements[:2])
        if all(
            _close_enough(observed, expected)
            for observed, expected in zip(observed_dims, requested_dims)
        ):
            return RequirementClauseInterpretation(
                clause_id=clause_id,
                clause_text=clause_text,
                status=RequirementClauseStatus.VERIFIED,
                evidence=groove_evidence,
                observation_tags=clause_tags + ["validation:feature_alignment"],
                decision_hints=[],
            )

    if "height" in lowered and axial_window is not None and measurements:
        target_height = float(measurements[-1])
        if _annular_height_matches_requirement(
            bundle=bundle,
            axial_window=axial_window,
            height_match_mode=height_match_mode,
            target_height=target_height,
        ):
            return RequirementClauseInterpretation(
                clause_id=clause_id,
                clause_text=clause_text,
                status=RequirementClauseStatus.VERIFIED,
                evidence=groove_evidence,
                observation_tags=clause_tags + ["validation:feature_alignment"],
                decision_hints=[],
            )
    return None


def _is_coordinate_tuple_clause(text: str) -> bool:
    normalized = str(text or "").strip()
    return bool(
        re.fullmatch(
            r"\(?\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?(?:\s*,\s*-?\d+(?:\.\d+)?)?\s*\)?",
            normalized,
        )
    )


def _interpret_feature_grounded_clause(
    *,
    clause_id: str,
    clause_text: str,
    clause_tags: list[str],
    bundle: RequirementEvidenceBundle,
    check_index: dict[str, RequirementCheck],
) -> RequirementClauseInterpretation | None:
    lowered = clause_text.lower()
    slot_alignment = _first_passed_check(
        check_index,
        "feature_cylindrical_slot_alignment",
    )
    notch_or_slot = _first_passed_check(
        check_index,
        "feature_notch_or_profile_cut",
    )
    profile_shape = _first_passed_check(
        check_index,
        "feature_profile_shape_alignment",
    )
    target_face_merge = _first_passed_check(
        check_index,
        "feature_target_face_subtractive_merge",
    )
    hole_alignment = _first_passed_check(
        check_index,
        "feature_hole_position_alignment",
        "feature_hole_exact_center_set",
        "feature_local_anchor_alignment",
    )
    hole_feature = _first_passed_check(
        check_index,
        "feature_hole",
        "feature_countersink",
    )
    local_anchor = _first_passed_check(
        check_index,
        "feature_local_anchor_alignment",
    )
    pattern = _first_passed_check(
        check_index,
        "feature_pattern",
    )
    base_positive_extrude = _first_passed_check(
        check_index,
        "feature_named_plane_positive_extrude_span",
    )
    mixed_nested_profile = _interpret_mixed_nested_profile_clause(
        clause_id=clause_id,
        clause_text=clause_text,
        clause_tags=clause_tags,
        check_index=check_index,
    )
    if mixed_nested_profile is not None:
        return mixed_nested_profile

    positive_extrude_clause = _interpret_named_plane_positive_extrude_clause(
        clause_id=clause_id,
        clause_text=clause_text,
        clause_tags=clause_tags,
        check_index=check_index,
    )
    if positive_extrude_clause is not None:
        return positive_extrude_clause

    annular_groove_local = _interpret_annular_groove_local_clause(
        clause_id=clause_id,
        clause_text=clause_text,
        clause_tags=clause_tags,
        bundle=bundle,
        check_index=check_index,
    )
    if annular_groove_local is not None:
        return annular_groove_local

    polygon_vertex_tuple = _interpret_polygon_vertex_tuple_clause(
        clause_id=clause_id,
        clause_text=clause_text,
        clause_tags=clause_tags,
        bundle=bundle,
        check_index=check_index,
    )
    if polygon_vertex_tuple is not None:
        return polygon_vertex_tuple

    polygon_pocket = _interpret_polygon_pocket_clause(
        clause_id=clause_id,
        clause_text=clause_text,
        clause_tags=clause_tags,
        bundle=bundle,
        check_index=check_index,
    )
    if polygon_pocket is not None:
        return polygon_pocket

    axisymmetric_rotation_axis = _interpret_axisymmetric_rotation_axis_clause(
        clause_id=clause_id,
        clause_text=clause_text,
        clause_tags=clause_tags,
        bundle=bundle,
    )
    if axisymmetric_rotation_axis is not None:
        return axisymmetric_rotation_axis

    through_hole_clause = _interpret_hole_through_thickness_clause(
        clause_id=clause_id,
        clause_text=clause_text,
        clause_tags=clause_tags,
        bundle=bundle,
        check_index=check_index,
    )
    if through_hole_clause is not None:
        return through_hole_clause

    named_fillet = _interpret_named_fillet_clause(
        clause_id=clause_id,
        clause_text=clause_text,
        clause_tags=clause_tags,
        check_index=check_index,
    )
    if named_fillet is not None:
        return named_fillet

    if slot_alignment is not None:
        if any(
            token in lowered
            for token in (
                "cutting cylinder",
                "centerline",
                "cover the entire length",
                "axis along the x-axis",
                "axis along the y-axis",
                "axis along the z-axis",
            )
        ):
            return RequirementClauseInterpretation(
                clause_id=clause_id,
                clause_text=clause_text,
                status=RequirementClauseStatus.VERIFIED,
                evidence=str(slot_alignment.evidence or slot_alignment.check_id),
                observation_tags=clause_tags + ["validation:feature_alignment"],
                decision_hints=[],
            )
        if any(
            token in lowered
            for token in (
                "boolean difference",
                "target body",
                "tool body",
            )
        ):
            evidence = _combine_check_evidence(slot_alignment, notch_or_slot)
            return RequirementClauseInterpretation(
                clause_id=clause_id,
                clause_text=clause_text,
                status=RequirementClauseStatus.VERIFIED,
                evidence=evidence or "validated by cylindrical-slot boolean result",
                observation_tags=clause_tags + ["validation:feature_alignment"],
                decision_hints=[],
            )
        if "semicircular slot" in lowered:
            evidence = _combine_check_evidence(
                slot_alignment,
                notch_or_slot,
                profile_shape,
            )
            return RequirementClauseInterpretation(
                clause_id=clause_id,
                clause_text=clause_text,
                status=RequirementClauseStatus.VERIFIED,
                evidence=evidence or "validated by cylindrical slot geometry",
                observation_tags=clause_tags + ["validation:feature_alignment"],
                decision_hints=[],
            )

    if "semicircle" in lowered and profile_shape is not None:
        evidence = _combine_check_evidence(profile_shape, target_face_merge, local_anchor)
        return RequirementClauseInterpretation(
            clause_id=clause_id,
            clause_text=clause_text,
            status=RequirementClauseStatus.VERIFIED,
            evidence=evidence or str(profile_shape.evidence or profile_shape.check_id),
            observation_tags=clause_tags + ["validation:feature_alignment"],
            decision_hints=[],
        )

    if "linear pattern command" in lowered and pattern is not None:
        evidence = _combine_check_evidence(pattern, local_anchor)
        return RequirementClauseInterpretation(
            clause_id=clause_id,
            clause_text=clause_text,
            status=RequirementClauseStatus.VERIFIED,
            evidence=evidence or str(pattern.evidence or pattern.check_id),
            observation_tags=clause_tags + ["validation:feature_alignment"],
            decision_hints=[],
        )

    if (
        base_positive_extrude is not None
        and "extrude" in lowered
        and "clause:body_shape" in clause_tags
        and any(
            token in lowered
            for token in (
                "create the base",
                "create the block",
                "create the plate",
                "create the body",
                "create the prism",
                "create the box",
            )
        )
    ):
        return RequirementClauseInterpretation(
            clause_id=clause_id,
            clause_text=clause_text,
            status=RequirementClauseStatus.VERIFIED,
            evidence=str(base_positive_extrude.evidence or base_positive_extrude.check_id),
            observation_tags=clause_tags + ["validation:feature_alignment"],
            decision_hints=[],
        )

    if any(
        token in lowered
        for token in (
            "direction 1",
            "direction 2",
            "spacing ",
            "quantity ",
            "center the pattern",
            "symmetrically centered",
        )
    ) and local_anchor is not None:
        evidence = _combine_check_evidence(local_anchor, pattern)
        return RequirementClauseInterpretation(
            clause_id=clause_id,
            clause_text=clause_text,
            status=RequirementClauseStatus.VERIFIED,
            evidence=evidence or str(local_anchor.evidence or local_anchor.check_id),
            observation_tags=clause_tags + ["validation:feature_alignment"],
            decision_hints=[],
        )

    if (
        hole_alignment is not None
        and hole_feature is not None
        and (
            _is_coordinate_tuple_clause(clause_text)
            or "face-sketch coordinates" in lowered
            or "face sketch coordinates" in lowered
            or "already-centered offsets" in lowered
            or "already centered offsets" in lowered
            or lowered in {"on the top face", "on the bottom face", "on the left face", "on the right face", "on the front face", "on the back face"}
        )
    ):
        evidence = _combine_check_evidence(hole_alignment, hole_feature)
        return RequirementClauseInterpretation(
            clause_id=clause_id,
            clause_text=clause_text,
            status=RequirementClauseStatus.VERIFIED,
            evidence=evidence or str(hole_alignment.evidence or hole_alignment.check_id),
            observation_tags=clause_tags + ["validation:feature_alignment"],
            decision_hints=[],
        )

    return None


def _interpret_dimension_clause(
    *,
    clause_id: str,
    clause_text: str,
    clause_tags: list[str],
    bundle: RequirementEvidenceBundle,
    check_index: dict[str, RequirementCheck],
) -> RequirementClauseInterpretation | None:
    lowered = clause_text.lower()
    bbox = [
        float(value)
        for value in (bundle.geometry_facts.get("bbox") or [])
        if isinstance(value, (int, float))
    ]
    if not bbox:
        return None
    measurements = _extract_measurements(lowered)
    if not measurements:
        return None

    symmetric_rectangle_result = _interpret_symmetric_rectangle_sketch_clause(
        clause_id=clause_id,
        clause_text=clause_text,
        clause_tags=clause_tags,
        bundle=bundle,
    )
    if symmetric_rectangle_result is not None:
        return symmetric_rectangle_result

    evidence_parts: list[str] = []
    matched = 0
    dimension_word_pattern = re.compile(
        r"\b(?:wide|long|length|tall|height|overall|diameter)\b",
        re.IGNORECASE,
    )
    body_like_dimension_clause = any(
        tag in clause_tags
        for tag in ("clause:body_shape", "clause:thickness", "clause:axisymmetric_body")
    )
    precise_grounding_check_ids = (
        "feature_local_anchor_alignment",
        "feature_pattern",
        "feature_pattern_seed_alignment",
        "feature_hole_position_alignment",
        "feature_hole_exact_center_set",
    )
    if _clause_requires_precise_grounding(lowered):
        precise_pass = _first_passed_check(check_index, *precise_grounding_check_ids)
        if precise_pass is not None:
            return RequirementClauseInterpretation(
                clause_id=clause_id,
                clause_text=clause_text,
                status=RequirementClauseStatus.VERIFIED,
                evidence=str(precise_pass.evidence or precise_pass.check_id),
                observation_tags=clause_tags + ["validation:feature_alignment"],
                decision_hints=[],
            )
        if any(check_id in check_index for check_id in precise_grounding_check_ids):
            precise_fail = _first_failed_check(check_index, *precise_grounding_check_ids)
            if precise_fail is not None:
                return RequirementClauseInterpretation(
                    clause_id=clause_id,
                    clause_text=clause_text,
                    status=RequirementClauseStatus.CONTRADICTED,
                    evidence=str(precise_fail.evidence or precise_fail.check_id),
                    observation_tags=clause_tags + ["validation:feature_alignment"],
                    decision_hints=["repair the contradicted local feature layout"],
                )
            return RequirementClauseInterpretation(
                clause_id=clause_id,
                clause_text=clause_text,
                status=RequirementClauseStatus.INSUFFICIENT_EVIDENCE,
                evidence="Local feature dimensions need direct alignment/pattern evidence instead of whole-body bbox grounding.",
                observation_tags=clause_tags + ["insufficient_evidence"],
                decision_hints=["inspect local alignment or pattern evidence before completion"],
            )

    if (
        "cover the entire length" in lowered
        and (slot_alignment := _first_passed_check(check_index, "feature_cylindrical_slot_alignment"))
        is not None
    ):
        return RequirementClauseInterpretation(
            clause_id=clause_id,
            clause_text=clause_text,
            status=RequirementClauseStatus.VERIFIED,
            evidence=str(slot_alignment.evidence or slot_alignment.check_id),
            observation_tags=clause_tags + ["validation:feature_alignment"],
            decision_hints=[],
        )

    axisymmetric_segment_result = _interpret_axisymmetric_segment_clause(
        clause_id=clause_id,
        clause_text=clause_text,
        clause_tags=clause_tags,
        bundle=bundle,
    )
    if axisymmetric_segment_result is not None:
        return axisymmetric_segment_result

    symmetric_extrude_result = _interpret_symmetric_extrude_clause(
        clause_id=clause_id,
        clause_text=clause_text,
        clause_tags=clause_tags,
        bundle=bundle,
    )
    if symmetric_extrude_result is not None:
        return symmetric_extrude_result

    axisymmetric_point_result = _interpret_axisymmetric_profile_point_clause(
        clause_id=clause_id,
        clause_text=clause_text,
        clause_tags=clause_tags,
        bundle=bundle,
    )
    if axisymmetric_point_result is not None:
        return axisymmetric_point_result

    plane_anchored_axisymmetric_result = _interpret_plane_anchored_axisymmetric_band_clause(
        clause_id=clause_id,
        clause_text=clause_text,
        clause_tags=clause_tags,
        bundle=bundle,
    )
    if plane_anchored_axisymmetric_result is not None:
        return plane_anchored_axisymmetric_result

    if (
        len(measurements) >= 2
        and "rectangle" in lowered
        and re.search(r"\b(?:xy|xz|yz)\s+plane\b", lowered)
        and (
            base_positive_extrude := _first_passed_check(
                check_index,
                "feature_named_plane_positive_extrude_span",
            )
        )
        is not None
    ):
        remaining_dims = list(bbox)
        evidence_parts: list[str] = []
        for measurement in measurements:
            match_index = next(
                (
                    idx
                    for idx, dimension in enumerate(remaining_dims)
                    if _close_enough(dimension, measurement)
                ),
                None,
            )
            if match_index is None:
                break
            evidence_parts.append(f"matched_dimension={measurement}")
            remaining_dims.pop(match_index)
        if len(evidence_parts) == len(measurements):
            supporting_evidence = str(
                base_positive_extrude.evidence or base_positive_extrude.check_id
            )
            evidence = ", ".join(evidence_parts)
            if supporting_evidence:
                evidence = f"{evidence}; {supporting_evidence}"
            return RequirementClauseInterpretation(
                clause_id=clause_id,
                clause_text=clause_text,
                status=RequirementClauseStatus.VERIFIED,
                evidence=evidence,
                observation_tags=clause_tags + ["geometry:bbox"],
                decision_hints=[],
            )

    if "circle" in lowered and "diameter" in lowered and len(measurements) == 1:
        observed_face_diameters = sorted(
            {
                round(float(radius) * 2.0, 3)
                for radius in (bundle.topology_facts.get("face_radii") or [])
                if isinstance(radius, (int, float)) and float(radius) > 1e-6
            }
        )
        target = measurements[0]
        matched_diameter = next(
            (
                realized
                for realized in observed_face_diameters
                if _close_enough(realized, target)
            ),
            None,
        )
        if matched_diameter is not None:
            evidence_checks = _combine_check_evidence(
                _first_passed_check(
                    check_index,
                    "feature_target_face_additive_merge",
                    "feature_target_face_edit",
                ),
                _first_passed_check(
                    check_index,
                    "feature_local_anchor_alignment",
                    "feature_pattern",
                ),
                _first_passed_check(
                    check_index,
                    "feature_profile_shape_alignment",
                ),
            )
            evidence_parts = [
                f"matched_circle_diameter={target}",
                f"observed_face_diameters={observed_face_diameters}",
            ]
            if evidence_checks:
                evidence_parts.append(evidence_checks)
            return RequirementClauseInterpretation(
                clause_id=clause_id,
                clause_text=clause_text,
                status=RequirementClauseStatus.VERIFIED,
                evidence=", ".join(evidence_parts[:2])
                if len(evidence_parts) == 2
                else f"{evidence_parts[0]}, {evidence_parts[1]}; {evidence_parts[2]}",
                observation_tags=clause_tags + ["geometry:face_radius"],
                decision_hints=[],
            )

    if "outer diameter" in lowered:
        outer = max(bbox[:2]) if len(bbox) >= 2 else max(bbox)
        target = measurements[0]
        if _close_enough(outer, target):
            return RequirementClauseInterpretation(
                clause_id=clause_id,
                clause_text=clause_text,
                status=RequirementClauseStatus.VERIFIED,
                evidence=f"matched_outer_diameter={target}, bbox_outer_diameter={round(outer, 3)}",
                observation_tags=clause_tags + ["geometry:bbox"],
                decision_hints=[],
            )
        return RequirementClauseInterpretation(
            clause_id=clause_id,
            clause_text=clause_text,
            status=RequirementClauseStatus.CONTRADICTED,
            evidence=f"requested_outer_diameter={target}, bbox_outer_diameter={round(outer, 3)}",
            observation_tags=clause_tags + ["geometry:bbox"],
            decision_hints=["repair the primary profile dimensions"],
        )

    if "inner diameter" in lowered:
        target = measurements[0]
        radii = [
            float(value)
            for value in (bundle.geometry_facts.get("through_axisymmetric_radii") or [])
            if isinstance(value, (int, float))
        ]
        for radius in radii:
            realized = float(radius) * 2.0
            if _close_enough(realized, target):
                return RequirementClauseInterpretation(
                    clause_id=clause_id,
                    clause_text=clause_text,
                    status=RequirementClauseStatus.VERIFIED,
                    evidence=f"matched_inner_diameter={target}, realized_inner_diameter={round(realized, 3)}",
                    observation_tags=clause_tags + ["geometry:axisymmetric_radius"],
                    decision_hints=[],
                )
        return RequirementClauseInterpretation(
            clause_id=clause_id,
            clause_text=clause_text,
            status=RequirementClauseStatus.INSUFFICIENT_EVIDENCE,
            evidence="No explicit internal cylindrical radius was available to ground the inner diameter clause.",
            observation_tags=clause_tags + ["insufficient_evidence"],
            decision_hints=["query_geometry or topology for explicit internal radius evidence"],
        )

    if "thickness" in lowered or "thick" in lowered:
        target = measurements[-1]
        realized = min(bbox)
        status = (
            RequirementClauseStatus.VERIFIED
            if _close_enough(realized, target)
            else RequirementClauseStatus.CONTRADICTED
        )
        evidence = (
            f"matched_thickness={target}, bbox_min_span={round(realized, 3)}"
            if status == RequirementClauseStatus.VERIFIED
            else f"requested_thickness={target}, bbox_min_span={round(realized, 3)}"
        )
        return RequirementClauseInterpretation(
            clause_id=clause_id,
            clause_text=clause_text,
            status=status,
            evidence=evidence,
            observation_tags=clause_tags + ["geometry:bbox"],
            decision_hints=[] if status == RequirementClauseStatus.VERIFIED else ["repair the target thickness"],
        )

    single_dimension_check_ids: list[str] = []
    if "width" in lowered or "wide" in lowered:
        single_dimension_check_ids.append("dimension_width")
    if "height" in lowered or "tall" in lowered:
        single_dimension_check_ids.append("dimension_height")
    if "length" in lowered or re.search(r"\blong\b", lowered):
        single_dimension_check_ids.append("dimension_length")
    if "depth" in lowered:
        single_dimension_check_ids.append("dimension_depth")
    if "diameter" in lowered and "outer diameter" not in lowered and "inner diameter" not in lowered:
        single_dimension_check_ids.append("dimension_diameter")
    if single_dimension_check_ids:
        passed_check = _first_passed_check(check_index, *single_dimension_check_ids)
        if passed_check is not None:
            return RequirementClauseInterpretation(
                clause_id=clause_id,
                clause_text=clause_text,
                status=RequirementClauseStatus.VERIFIED,
                evidence=str(passed_check.evidence or passed_check.check_id),
                observation_tags=clause_tags + ["validation:dimension_check"],
                decision_hints=[],
            )
        failed_check = _first_failed_check(check_index, *single_dimension_check_ids)
        if failed_check is not None:
            return RequirementClauseInterpretation(
                clause_id=clause_id,
                clause_text=clause_text,
                status=RequirementClauseStatus.CONTRADICTED,
                evidence=str(failed_check.evidence or failed_check.check_id),
                observation_tags=clause_tags + ["validation:dimension_check"],
                decision_hints=["repair the requested dimension"],
            )

    if (
        len(measurements) == 1
        and not body_like_dimension_clause
        and "overall" not in lowered
    ):
        return None

    has_bbox_dimension_phrase = bool(dimension_word_pattern.search(lowered)) or "by" in lowered
    if len(measurements) >= 2 and "clause:body_shape" in clause_tags:
        has_bbox_dimension_phrase = True
    if has_bbox_dimension_phrase:
        remaining_dims = list(bbox)
        for measurement in measurements:
            match_index = next(
                (
                    idx
                    for idx, dimension in enumerate(remaining_dims)
                    if _close_enough(dimension, measurement)
                ),
                None,
            )
            if match_index is None:
                return RequirementClauseInterpretation(
                    clause_id=clause_id,
                    clause_text=clause_text,
                    status=RequirementClauseStatus.CONTRADICTED,
                    evidence=(
                        f"requested_dimensions={measurements}, realized_bbox={[round(item, 3) for item in bbox]}"
                    ),
                    observation_tags=clause_tags + ["geometry:bbox"],
                    decision_hints=["repair the overall body dimensions"],
                )
            matched += 1
            evidence_parts.append(f"matched_dimension={measurement}")
            remaining_dims.pop(match_index)
        if matched:
            return RequirementClauseInterpretation(
                clause_id=clause_id,
                clause_text=clause_text,
                status=RequirementClauseStatus.VERIFIED,
                evidence=", ".join(evidence_parts) or "bbox dimensions matched",
                observation_tags=clause_tags + ["geometry:bbox"],
                decision_hints=[],
            )
    return None


def _interpret_feature_clause(
    *,
    clause_id: str,
    clause_text: str,
    clause_tags: list[str],
    check_index: dict[str, RequirementCheck],
    bundle: RequirementEvidenceBundle,
) -> RequirementClauseInterpretation | None:
    lowered = clause_text.lower()
    rule_sets: list[tuple[tuple[str, ...], list[str]]] = []
    if "clause:hole" in clause_tags:
        rule_sets.append(
            (
                ("feature_hole", "feature_countersink", "feature_hole_position_alignment", "feature_hole_exact_center_set", "feature_local_anchor_alignment"),
                ["hole", "countersink"],
            )
        )
    if "clause:notch_like" in clause_tags:
        rule_sets.append(
            (
                ("feature_notch_or_profile_cut", "feature_notch_profile_alignment", "feature_cylindrical_slot_alignment"),
                ["slot", "notch", "channel"],
            )
        )
    if "clause:local_feature" in clause_tags and "clause:hole" not in clause_tags:
        rule_sets.append(
            (
                ("feature_annular_groove", "feature_revolved_groove_result", "feature_target_face_subtractive_merge", "feature_spherical_recess"),
                ["groove", "recess", "pocket"],
            )
        )
    if "clause:pattern" in clause_tags:
        rule_sets.append((("feature_pattern", "feature_pattern_seed_alignment"), ["pattern", "array", "grid"]))
    if "clause:fillet" in clause_tags:
        rule_sets.append((("feature_fillet",), ["fillet"]))
    if "clause:chamfer" in clause_tags:
        rule_sets.append((("feature_chamfer",), ["chamfer"]))
    if "clause:sweep" in clause_tags:
        rule_sets.append(
            (
                ("feature_path_sweep_rail", "feature_path_sweep_profile", "feature_path_sweep_frame", "feature_path_sweep_result", "path_disconnected", "missing_profile"),
                ["sweep"],
            )
        )
    if "union" in lowered or "merge" in lowered or "pad" in lowered:
        rule_sets.append(
            (
                ("feature_multi_plane_additive_union", "feature_multi_plane_additive_specs", "feature_merged_body_result", "feature_target_face_additive_merge"),
                ["union", "merge", "pad"],
            )
        )

    for check_ids, _tokens in rule_sets:
        matched_checks = [check_index[check_id] for check_id in check_ids if check_id in check_index]
        if not matched_checks:
            continue
        failed = [check for check in matched_checks if check.status == RequirementCheckStatus.FAIL]
        passed = [check for check in matched_checks if check.status == RequirementCheckStatus.PASS]
        if failed:
            return RequirementClauseInterpretation(
                clause_id=clause_id,
                clause_text=clause_text,
                status=RequirementClauseStatus.CONTRADICTED,
                evidence="; ".join(check.evidence or check.check_id for check in failed[:3]),
                observation_tags=clause_tags + ["validation:legacy_fail"],
                decision_hints=["repair the contradicted clause before finishing"],
            )
        if passed:
            if _clause_requires_precise_grounding(lowered):
                return RequirementClauseInterpretation(
                    clause_id=clause_id,
                    clause_text=clause_text,
                    status=RequirementClauseStatus.INSUFFICIENT_EVIDENCE,
                    evidence="Feature-level evidence exists, but count or placement is still under-specified.",
                    observation_tags=clause_tags + ["insufficient_evidence"],
                    decision_hints=["inspect count or placement with geometry/topology evidence"],
                )
            return RequirementClauseInterpretation(
                clause_id=clause_id,
                clause_text=clause_text,
                status=RequirementClauseStatus.VERIFIED,
                evidence="; ".join(check.evidence or check.check_id for check in passed[:2]),
                observation_tags=clause_tags + ["validation:legacy_pass"],
                decision_hints=[],
            )

    if "rounded" in lowered and int(bundle.geometry_facts.get("solids") or 0) > 0:
        return RequirementClauseInterpretation(
            clause_id=clause_id,
            clause_text=clause_text,
            status=RequirementClauseStatus.INSUFFICIENT_EVIDENCE,
            evidence="High-level roundedness needs geometry-grounded or adjudicated evidence.",
            observation_tags=clause_tags + ["insufficient_evidence"],
            decision_hints=["allow high-level semantic adjudication after geometry checks settle"],
        )
    return None


def _clause_requires_precise_grounding(text: str) -> bool:
    if re.search(r"\b[xyz]\s*=\s*-?\d", text):
        return True
    if (
        any(token in text for token in ("array", "pattern", "grid"))
        and any(token in text for token in ("center", "centers", "spacing", "side length", "pitch"))
    ):
        return True
    if any(token in text for token in _SPECIFICITY_TOKENS):
        return True
    return any(token in text for token in ("through the", "top face", "bottom face"))


def _project_clause_checks(
    clauses: list[RequirementClauseInterpretation],
) -> list[RequirementCheck]:
    projected: list[RequirementCheck] = []
    for clause in clauses:
        if clause.status == RequirementClauseStatus.CONTRADICTED:
            projected.append(
                RequirementCheck(
                    check_id=clause.clause_id,
                    label=clause.clause_text,
                    status=RequirementCheckStatus.FAIL,
                    blocking=True,
                    evidence=clause.evidence,
                )
            )
            continue
        if clause.status == RequirementClauseStatus.INSUFFICIENT_EVIDENCE and _should_project_unknown_clause(
            clause
        ):
            projected.append(
                RequirementCheck(
                    check_id=clause.clause_id,
                    label=clause.clause_text,
                    status=RequirementCheckStatus.UNKNOWN,
                    blocking=False,
                    evidence=clause.evidence,
                )
            )
    return projected


def _should_project_unknown_clause(clause: RequirementClauseInterpretation) -> bool:
    text = clause.clause_text.lower()
    if len(text.split()) <= 2:
        return False
    if re.search(r"\d", text):
        return False
    return True
