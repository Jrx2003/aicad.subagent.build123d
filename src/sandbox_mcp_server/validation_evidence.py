from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from sandbox_mcp_server.contracts import ActionHistoryEntry, CADStateSnapshot


def _normalize_requirement_text(
    requirements: dict[str, Any] | None,
    requirement_text: str | None,
) -> str:
    if isinstance(requirement_text, str) and requirement_text.strip():
        return requirement_text.strip()
    if isinstance(requirements, dict):
        description = requirements.get("description")
        if isinstance(description, str) and description.strip():
            return description.strip()
    return ""


def _split_requirement_clauses(text: str) -> list[str]:
    if not text.strip():
        return []
    normalized = re.sub(r"\s+", " ", text.strip())
    raw_parts: list[str] = []
    current: list[str] = []
    paren_depth = 0
    bracket_depth = 0
    index = 0
    connective_tokens = (" and then ", " then ", " and ")

    def _flush_current() -> None:
        clause = "".join(current).strip(" .,:;")
        current.clear()
        if not clause:
            return
        clause = re.sub(
            r"^(?:(?:and|next|then|finally)\b[:, ]*)+",
            "",
            clause,
            flags=re.IGNORECASE,
        ).strip(" .,:;")
        if clause:
            raw_parts.append(clause)

    while index < len(normalized):
        char = normalized[index]
        if char == "(":
            paren_depth += 1
            current.append(char)
            index += 1
            continue
        if char == ")":
            current.append(char)
            paren_depth = max(0, paren_depth - 1)
            index += 1
            continue
        if char == "[":
            bracket_depth += 1
            current.append(char)
            index += 1
            continue
        if char == "]":
            current.append(char)
            bracket_depth = max(0, bracket_depth - 1)
            index += 1
            continue
        if paren_depth == 0 and bracket_depth == 0:
            if char in {";", ".", ","} and index + 1 < len(normalized) and normalized[index + 1] == " ":
                _flush_current()
                index += 2
                continue
            matched_connective = next(
                (token for token in connective_tokens if normalized.startswith(token, index)),
                None,
            )
            if matched_connective is not None:
                _flush_current()
                index += len(matched_connective)
                continue
        current.append(char)
        index += 1
    _flush_current()

    clauses: list[str] = []
    for part in raw_parts:
        clause = part.strip(" .,:;")
        if clause and clause not in clauses:
            clauses.append(clause)
    return clauses or [normalized]


def _as_float_list(values: Any) -> list[float]:
    if not isinstance(values, list):
        return []
    return [float(item) for item in values if isinstance(item, (int, float))]


def _dominant_axis_index(axis_direction: Any) -> int | None:
    if not isinstance(axis_direction, list) or len(axis_direction) < 3:
        return None
    components = [
        abs(float(component)) if isinstance(component, (int, float)) else 0.0
        for component in axis_direction[:3]
    ]
    axis_index = max(range(3), key=lambda idx: components[idx])
    if components[axis_index] < 0.9:
        return None
    return axis_index


class RequirementEvidenceBundle(BaseModel):
    """Family-neutral evidence extracted from the current session state."""

    model_config = ConfigDict(extra="forbid")

    requirement_text: str = Field(default="")
    requirement_clauses: list[str] = Field(default_factory=list)
    snapshot_step: int | None = Field(default=None)
    geometry_facts: dict[str, Any] = Field(default_factory=dict)
    topology_facts: dict[str, Any] = Field(default_factory=dict)
    process_facts: dict[str, Any] = Field(default_factory=dict)
    observation_tags: list[str] = Field(default_factory=list)
    decision_hints: list[str] = Field(default_factory=list)
    coverage_confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class RequirementEvidenceBuilder:
    """Build a compact, generic evidence bundle from the current Build123d state."""

    @classmethod
    def build(
        cls,
        *,
        snapshot: CADStateSnapshot,
        history: list[ActionHistoryEntry],
        requirements: dict[str, Any] | None,
        requirement_text: str | None,
    ) -> RequirementEvidenceBundle:
        text = _normalize_requirement_text(requirements, requirement_text)
        geometry_facts = cls._build_geometry_facts(snapshot)
        topology_facts = cls._build_topology_facts(snapshot)
        process_facts = cls._build_process_facts(history)
        observation_tags = cls._build_observation_tags(
            geometry_facts=geometry_facts,
            topology_facts=topology_facts,
            process_facts=process_facts,
        )
        decision_hints = cls._build_decision_hints(
            geometry_facts=geometry_facts,
            topology_facts=topology_facts,
        )
        return RequirementEvidenceBundle(
            requirement_text=text,
            requirement_clauses=_split_requirement_clauses(text),
            snapshot_step=snapshot.step,
            geometry_facts=geometry_facts,
            topology_facts=topology_facts,
            process_facts=process_facts,
            observation_tags=observation_tags,
            decision_hints=decision_hints,
        )

    @staticmethod
    def _build_geometry_facts(snapshot: CADStateSnapshot) -> dict[str, Any]:
        geometry = snapshot.geometry
        bbox = _as_float_list(geometry.bbox)
        bbox_min = _as_float_list(geometry.bbox_min)
        bbox_max = _as_float_list(geometry.bbox_max)
        axis_radii = RequirementEvidenceBuilder._collect_axisymmetric_radii(snapshot)
        axis_bands = RequirementEvidenceBuilder._collect_axisymmetric_bands(snapshot)
        return {
            "solids": int(geometry.solids or 0),
            "faces": int(geometry.faces or 0),
            "edges": int(geometry.edges or 0),
            "volume": float(geometry.volume or 0.0),
            "bbox": bbox,
            "bbox_min": bbox_min,
            "bbox_max": bbox_max,
            "surface_area": float(geometry.surface_area or 0.0),
            "features": list(snapshot.features or []),
            "issues": list(snapshot.issues or []),
            "warnings": list(snapshot.warnings or []),
            "blockers": list(snapshot.blockers or []),
            "through_axisymmetric_radii": axis_radii,
            "axisymmetric_bands": axis_bands,
            "max_planar_span": max(bbox) if bbox else 0.0,
            "min_planar_span": min(bbox) if bbox else 0.0,
        }

    @staticmethod
    def _collect_axisymmetric_radii(snapshot: CADStateSnapshot) -> list[float]:
        geometry_objects = snapshot.geometry_objects
        if geometry_objects is None:
            return []
        radii: list[float] = []
        seen: set[float] = set()
        for face in geometry_objects.faces:
            if face.geom_type.upper() != "CYLINDER":
                continue
            if not isinstance(face.radius, (int, float)) or face.radius <= 0:
                continue
            rounded = round(float(face.radius), 3)
            if rounded in seen:
                continue
            seen.add(rounded)
            radii.append(rounded)
        return sorted(radii)

    @staticmethod
    def _collect_axisymmetric_bands(snapshot: CADStateSnapshot) -> list[dict[str, Any]]:
        geometry_objects = snapshot.geometry_objects
        topology_index = snapshot.topology_index
        face_source = (
            geometry_objects.faces
            if geometry_objects is not None
            else (topology_index.faces if topology_index is not None else [])
        )
        merged: dict[tuple[int, float, float, float], dict[str, Any]] = {}
        for face in face_source:
            if str(getattr(face, "geom_type", "")).strip().upper() != "CYLINDER":
                continue
            radius = getattr(face, "radius", None)
            if not isinstance(radius, (int, float)) or float(radius) <= 0.0:
                continue
            axis_index = _dominant_axis_index(getattr(face, "axis_direction", None))
            if axis_index is None:
                continue
            bbox = getattr(face, "bbox", None)
            if bbox is None:
                continue
            bbox_bounds = (
                (getattr(bbox, "xmin", None), getattr(bbox, "xmax", None)),
                (getattr(bbox, "ymin", None), getattr(bbox, "ymax", None)),
                (getattr(bbox, "zmin", None), getattr(bbox, "zmax", None)),
            )
            axis_bounds = bbox_bounds[axis_index]
            if not all(isinstance(value, (int, float)) for value in axis_bounds):
                continue
            axial_min = round(float(axis_bounds[0]), 3)
            axial_max = round(float(axis_bounds[1]), 3)
            rounded_radius = round(float(radius), 3)
            key = (axis_index, rounded_radius, axial_min, axial_max)
            existing = merged.get(key)
            if existing is None:
                merged[key] = {
                    "axis": "XYZ"[axis_index],
                    "radius": rounded_radius,
                    "axial_range": [axial_min, axial_max],
                    "face_count": 1,
                }
                continue
            existing["face_count"] = int(existing.get("face_count", 1)) + 1
        return sorted(
            merged.values(),
            key=lambda item: (
                str(item.get("axis", "")),
                float((item.get("axial_range") or [0.0, 0.0])[0]),
                float((item.get("axial_range") or [0.0, 0.0])[1]),
                float(item.get("radius", 0.0)),
            ),
        )

    @staticmethod
    def _build_topology_facts(snapshot: CADStateSnapshot) -> dict[str, Any]:
        geometry_objects = snapshot.geometry_objects
        topology_index = snapshot.topology_index
        face_types: list[str] = []
        face_radii: list[float] = []
        face_summaries: list[dict[str, Any]] = []
        face_summary_index: dict[str, dict[str, Any]] = {}
        edge_radii: list[float] = []
        edge_summaries: list[dict[str, Any]] = []
        edge_summary_index: dict[str, dict[str, Any]] = {}

        def _record_face(face: Any) -> None:
            face_type = str(getattr(face, "geom_type", "") or "").strip().upper()
            if face_type:
                face_types.append(face_type)
            radius = getattr(face, "radius", None)
            rounded_radius = (
                round(float(radius), 3)
                if isinstance(radius, (int, float)) and radius > 0
                else None
            )
            if rounded_radius is not None:
                face_radii.append(rounded_radius)
            bbox = getattr(face, "bbox", None)
            if bbox is None:
                return
            summary = {
                "face_id": str(getattr(face, "face_id", "") or "").strip() or None,
                "geom_type": face_type,
                "radius": rounded_radius,
                "normal": _as_float_list(getattr(face, "normal", None)),
                "axis_origin": _as_float_list(getattr(face, "axis_origin", None)),
                "axis_direction": _as_float_list(getattr(face, "axis_direction", None)),
                "edge_count": len(getattr(face, "edge_refs", []) or []),
                "bbox": {
                    "xmin": round(float(getattr(bbox, "xmin", 0.0)), 3),
                    "xmax": round(float(getattr(bbox, "xmax", 0.0)), 3),
                    "ymin": round(float(getattr(bbox, "ymin", 0.0)), 3),
                    "ymax": round(float(getattr(bbox, "ymax", 0.0)), 3),
                    "zmin": round(float(getattr(bbox, "zmin", 0.0)), 3),
                    "zmax": round(float(getattr(bbox, "zmax", 0.0)), 3),
                },
            }
            face_id = summary.get("face_id")
            if not isinstance(face_id, str) or not face_id:
                face_summaries.append(summary)
                return
            existing = face_summary_index.get(face_id)
            if existing is None:
                face_summary_index[face_id] = summary
                face_summaries.append(summary)
                return
            existing["edge_count"] = max(
                int(existing.get("edge_count") or 0),
                int(summary.get("edge_count") or 0),
            )
            if existing.get("radius") is None and summary.get("radius") is not None:
                existing["radius"] = summary["radius"]
            for key in ("normal", "axis_origin", "axis_direction"):
                if not existing.get(key) and summary.get(key):
                    existing[key] = summary[key]
            if not existing.get("geom_type") and summary.get("geom_type"):
                existing["geom_type"] = summary["geom_type"]

        def _record_edge(edge: Any) -> None:
            edge_type = str(getattr(edge, "geom_type", "") or "").strip().upper()
            radius = getattr(edge, "radius", None)
            rounded_radius = (
                round(float(radius), 3)
                if isinstance(radius, (int, float)) and radius > 0
                else None
            )
            if rounded_radius is not None:
                edge_radii.append(rounded_radius)
            bbox = getattr(edge, "bbox", None)
            if bbox is None:
                return
            summary = {
                "edge_id": str(getattr(edge, "edge_id", "") or "").strip() or None,
                "geom_type": edge_type,
                "radius": rounded_radius,
                "center": _as_float_list(getattr(edge, "center", None)),
                "axis_origin": _as_float_list(getattr(edge, "axis_origin", None)),
                "axis_direction": _as_float_list(getattr(edge, "axis_direction", None)),
                "bbox": {
                    "xmin": round(float(getattr(bbox, "xmin", 0.0)), 3),
                    "xmax": round(float(getattr(bbox, "xmax", 0.0)), 3),
                    "ymin": round(float(getattr(bbox, "ymin", 0.0)), 3),
                    "ymax": round(float(getattr(bbox, "ymax", 0.0)), 3),
                    "zmin": round(float(getattr(bbox, "zmin", 0.0)), 3),
                    "zmax": round(float(getattr(bbox, "zmax", 0.0)), 3),
                },
            }
            edge_id = summary.get("edge_id")
            if not isinstance(edge_id, str) or not edge_id:
                edge_summaries.append(summary)
                return
            existing = edge_summary_index.get(edge_id)
            if existing is None:
                edge_summary_index[edge_id] = summary
                edge_summaries.append(summary)
                return
            if existing.get("radius") is None and summary.get("radius") is not None:
                existing["radius"] = summary["radius"]
            for key in ("center", "axis_origin", "axis_direction"):
                if not existing.get(key) and summary.get(key):
                    existing[key] = summary[key]
            if not existing.get("geom_type") and summary.get("geom_type"):
                existing["geom_type"] = summary["geom_type"]

        if geometry_objects is not None:
            for face in geometry_objects.faces:
                _record_face(face)
            for edge in geometry_objects.edges:
                _record_edge(edge)
        if topology_index is not None:
            for face in topology_index.faces:
                _record_face(face)
            for edge in topology_index.edges:
                _record_edge(edge)
        return {
            "has_geometry_objects": geometry_objects is not None,
            "has_topology_index": topology_index is not None,
            "face_geom_types": list(dict.fromkeys(face_types)),
            "face_radii": list(dict.fromkeys(face_radii)),
            "face_summaries": face_summaries,
            "edge_radii": list(dict.fromkeys(edge_radii)),
            "edge_summaries": edge_summaries,
            "topology_faces": len(topology_index.faces) if topology_index is not None else 0,
            "topology_edges": len(topology_index.edges) if topology_index is not None else 0,
        }

    @staticmethod
    def _build_process_facts(history: list[ActionHistoryEntry]) -> dict[str, Any]:
        action_types = [
            str(entry.action_type.value if hasattr(entry.action_type, "value") else entry.action_type)
            for entry in history
        ]
        lowered = [item.lower() for item in action_types]
        return {
            "history_steps": len(history),
            "action_types": action_types,
            "has_extrude": "extrude" in lowered,
            "has_cut": "cut_extrude" in lowered or "trim_solid" in lowered,
            "has_revolve": "revolve" in lowered,
            "has_sweep": "sweep" in lowered,
            "has_loft": "loft" in lowered,
            "has_fillet": "fillet" in lowered,
            "has_chamfer": "chamfer" in lowered,
            "has_hole": "hole" in lowered,
            "has_pattern": any(item.startswith("pattern_") for item in lowered),
        }

    @staticmethod
    def _build_observation_tags(
        *,
        geometry_facts: dict[str, Any],
        topology_facts: dict[str, Any],
        process_facts: dict[str, Any],
    ) -> list[str]:
        tags: list[str] = []
        if int(geometry_facts.get("solids") or 0) > 0:
            tags.append("geometry:solid_present")
        if topology_facts.get("has_geometry_objects"):
            tags.append("geometry:object_index_available")
        if topology_facts.get("has_topology_index"):
            tags.append("topology:index_available")
        if process_facts.get("has_sweep"):
            tags.append("process:sweep")
        if process_facts.get("has_revolve"):
            tags.append("process:revolve")
        if process_facts.get("has_hole"):
            tags.append("process:hole")
        return list(dict.fromkeys(tags))

    @staticmethod
    def _build_decision_hints(
        *,
        geometry_facts: dict[str, Any],
        topology_facts: dict[str, Any],
    ) -> list[str]:
        hints: list[str] = []
        if int(geometry_facts.get("solids") or 0) <= 0:
            hints.append("rebuild core solid before local feature validation")
        if not topology_facts.get("has_geometry_objects"):
            hints.append("query_geometry for richer face-level evidence")
        if not topology_facts.get("has_topology_index"):
            hints.append("query_topology for explicit anchor and relation evidence")
        return list(dict.fromkeys(hints))
