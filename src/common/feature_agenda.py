from __future__ import annotations

import re
from typing import Any

_CLAUSE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+")
_TRANSITION_SPLIT_RE = re.compile(
    r"\b(?:first|next|then|again|finally|after that|subsequently)\b",
    re.IGNORECASE,
)


def build_feature_agenda(
    requirements: dict[str, Any] | None,
    action_history: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    description = _requirement_description(requirements)
    if not description:
        return None

    clauses = _extract_feature_clauses(description)
    if not clauses:
        return None

    completed_counts = _completed_feature_family_counts(action_history)
    consumed_counts = {key: 0 for key in completed_counts}
    items: list[dict[str, Any]] = []
    next_pending_phase: int | None = None

    for phase_index, clause in enumerate(clauses, start=1):
        action_family = _classify_clause_action_family(clause)
        if action_family is None:
            continue
        completed = consumed_counts[action_family] < completed_counts[action_family]
        if completed:
            consumed_counts[action_family] += 1
        status = "completed" if completed else "future"
        if not completed and next_pending_phase is None:
            status = "pending"
            next_pending_phase = phase_index
        items.append(
            {
                "phase": phase_index,
                "status": status,
                "action_family": action_family,
                "face_targets": _detect_clause_face_targets(clause),
                "summary": _summarize_clause(clause),
            }
        )

    if not items:
        return None

    summary = (
        f"{len(items)} ordered feature phase(s); "
        f"next pending phase={next_pending_phase if next_pending_phase is not None else 'none'}."
    )
    return {
        "summary": summary,
        "next_pending_phase": next_pending_phase,
        "items": items[:8],
    }


def next_pending_feature_face_targets(
    requirements: dict[str, Any] | None,
    action_history: list[dict[str, Any]] | None,
) -> list[str]:
    agenda = build_feature_agenda(requirements=requirements, action_history=action_history)
    if not isinstance(agenda, dict):
        return []
    items = agenda.get("items")
    if not isinstance(items, list):
        return []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("status") != "pending":
            continue
        face_targets = item.get("face_targets")
        if isinstance(face_targets, list):
            return [value for value in face_targets if isinstance(value, str)]
        return []
    return []


def next_pending_feature_summary(
    requirements: dict[str, Any] | None,
    action_history: list[dict[str, Any]] | None,
) -> str | None:
    agenda = build_feature_agenda(requirements=requirements, action_history=action_history)
    if not isinstance(agenda, dict):
        return None
    items = agenda.get("items")
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("status") != "pending":
            continue
        summary = item.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
        return None
    return None


def _requirement_description(requirements: dict[str, Any] | None) -> str:
    if not isinstance(requirements, dict):
        return ""
    description = str(requirements.get("description", "") or "").strip()
    return re.sub(r"\s+", " ", description)


def _extract_feature_clauses(description: str) -> list[str]:
    clauses: list[str] = []
    for sentence in _CLAUSE_SPLIT_RE.split(description):
        normalized = str(sentence).strip()
        if not normalized:
            continue
        fragments = _TRANSITION_SPLIT_RE.split(normalized)
        cleaned_fragments = [fragment.strip(" ,") for fragment in fragments if fragment.strip(" ,")]
        if cleaned_fragments:
            clauses.extend(cleaned_fragments)
        else:
            clauses.append(normalized)
    return clauses


def _classify_clause_action_family(clause: str) -> str | None:
    text = clause.lower()
    if not text:
        return None
    if "fillet" in text or "chamfer" in text or "bevel" in text:
        return "edge_finish"
    if any(token in text for token in ("pattern", "array", "evenly distributed", "distributed circle")):
        return "pattern_feature"
    subtractive_tokens = (
        "cut extrude",
        "cut-extrude",
        "cut extrusion",
        "cut through",
        "cut the",
        "perform a cut",
        "hole",
        "remove material",
        "subtract",
        "trim",
    )
    if any(token in text for token in subtractive_tokens):
        return "subtractive_solid"
    additive_tokens = (
        "extrude",
        "revolve",
        "loft",
        "sweep",
        "boss",
        "pad",
    )
    if any(token in text for token in additive_tokens):
        return "additive_solid"
    return None


def _detect_clause_face_targets(clause: str) -> list[str]:
    text = clause.lower()
    ordered: list[str] = []
    for token in ("top", "bottom", "front", "back", "left", "right", "side"):
        if token in text and token not in ordered:
            ordered.append(token)
    if "top view plane" in text or "xy plane" in text:
        ordered.append("top")
    if "front plane" in text or "front view plane" in text or "xz plane" in text:
        ordered.append("front")
    if "right plane" in text or "right view plane" in text or "yz plane" in text:
        ordered.append("right")
    deduped: list[str] = []
    seen: set[str] = set()
    for token in ordered:
        if token in seen:
            continue
        seen.add(token)
        deduped.append(token)
    return deduped[:3]


def _summarize_clause(clause: str) -> str:
    normalized = re.sub(r"\s+", " ", clause).strip(" .")
    if len(normalized) <= 120:
        return normalized
    return normalized[:117].rstrip() + "..."


def _completed_feature_family_counts(
    action_history: list[dict[str, Any]] | None,
) -> dict[str, int]:
    counts = {
        "additive_solid": 0,
        "subtractive_solid": 0,
        "pattern_feature": 0,
        "edge_finish": 0,
    }
    for item in action_history or []:
        if not isinstance(item, dict):
            continue
        action_type = str(item.get("action_type", "") or "").strip().lower()
        action_params = item.get("action_params")
        action_params = action_params if isinstance(action_params, dict) else {}
        if action_type in {"extrude", "revolve", "loft", "sweep"}:
            counts["additive_solid"] += 1
            continue
        if action_type in {"cut_extrude", "trim_solid", "sphere_recess"}:
            counts["subtractive_solid"] += 1
            continue
        if action_type == "hole":
            centers = action_params.get("centers")
            if isinstance(centers, list) and len(centers) > 1:
                counts["pattern_feature"] += 1
            else:
                counts["subtractive_solid"] += 1
            continue
        if action_type in {"pattern_linear", "pattern_circular"}:
            counts["pattern_feature"] += 1
            continue
        if action_type in {"fillet", "chamfer"}:
            counts["edge_finish"] += 1
    return counts
