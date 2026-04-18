from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from sandbox_mcp_server.contracts import (
    RequirementClauseInterpretation,
    RequirementClauseStatus,
)
from sandbox_mcp_server.validation_interpretation import (
    RequirementInterpretationSummary,
)


@dataclass(frozen=True, slots=True)
class ClauseGroundingRule:
    rule_id: str
    tokens: tuple[str, ...]
    required_evidence_kinds: tuple[str, ...] = ()
    family_binding: str | None = None
    precise_grounding_required: bool = False
    repair_hints: tuple[str, ...] = ()


_GROUNDING_RULES: tuple[ClauseGroundingRule, ...] = (
    ClauseGroundingRule(
        rule_id="explicit_anchor_hole_layout",
        tokens=("center", "offset", "pattern", "hole", "countersink", "counterbore", "aligned"),
        required_evidence_kinds=("geometry", "topology"),
        family_binding="explicit_anchor_hole",
        precise_grounding_required=True,
        repair_hints=("query_topology", "query_feature_probes"),
    ),
    ClauseGroundingRule(
        rule_id="half_shell_mating_and_hinge",
        tokens=("hinge", "mating", "lid", "base", "shell"),
        required_evidence_kinds=("topology",),
        family_binding="half_shell",
        repair_hints=("query_feature_probes", "query_kernel_state"),
    ),
    ClauseGroundingRule(
        rule_id="named_face_local_edit_feature",
        tokens=("recess", "pocket", "notch", "slot", "fillet", "chamfer"),
        required_evidence_kinds=("topology",),
        family_binding="named_face_local_edit",
        repair_hints=("query_topology", "query_kernel_state"),
    ),
    ClauseGroundingRule(
        rule_id="overall_dimensions",
        tokens=("overall", "bounding box", "size", "dimension"),
        required_evidence_kinds=("geometry",),
        repair_hints=("query_geometry",),
    ),
)

_GROUNDING_SOURCE_TAG_ALIASES: tuple[tuple[str, str], ...] = (
    ("geometry", "geometry"),
    ("topology", "topology"),
    ("history", "process"),
    ("process", "process"),
)

_GROUNDING_STRENGTH_RANK = {"none": 0, "weak": 1, "partial": 2, "strong": 3}
_HOLE_LAYOUT_TOKENS = (
    "center",
    "centered",
    "centred",
    "offset",
    "aligned",
    "pattern",
    "coordinate",
    "coordinates",
    "spacing",
    "pitch",
)
_HOLE_NOUN_TOKENS = ("hole", "holes", "countersink", "counterbore", "bore", "bores")
_LOCAL_FACE_FEATURE_TOKENS = ("recess", "pocket", "notch", "slot", "fillet", "chamfer")
_LOCAL_FACE_TARGET_TOKENS = (
    "face",
    "surface",
    "opening",
    "rim",
    "edge",
    "front",
    "back",
    "left",
    "right",
    "top",
    "bottom",
)


def attach_clause_grounding_surface(
    interpretation: RequirementInterpretationSummary,
) -> tuple[RequirementInterpretationSummary, dict[str, object]]:
    updated_clauses: list[RequirementClauseInterpretation] = []
    aggregate_sources: set[str] = set()
    aggregate_required: set[str] = set()
    aggregate_repair_hints: list[str] = []
    aggregate_family_bindings: set[str] = set()
    overclaim_guard: str | None = None
    strongest = "none"

    for clause in interpretation.clause_interpretations:
        clause_surface = _derive_clause_grounding_surface(clause)
        updated_clause = clause.model_copy(update=clause_surface)
        updated_clauses.append(updated_clause)

        aggregate_sources.update(clause_surface["grounding_sources"])
        aggregate_required.update(clause_surface["required_evidence_kinds"])
        aggregate_repair_hints.extend(clause_surface["repair_hints"])
        family_binding = clause_surface["family_binding"]
        if family_binding:
            aggregate_family_bindings.add(str(family_binding))
        clause_overclaim_guard = clause_surface["overclaim_guard"]
        if isinstance(clause_overclaim_guard, str) and clause_overclaim_guard and overclaim_guard is None:
            overclaim_guard = clause_overclaim_guard
        grounding_strength = str(clause_surface["grounding_strength"] or "none")
        if _GROUNDING_STRENGTH_RANK.get(grounding_strength, 0) > _GROUNDING_STRENGTH_RANK.get(strongest, 0):
            strongest = grounding_strength

    updated_interpretation = interpretation.model_copy(
        update={"clause_interpretations": updated_clauses}
    )
    return updated_interpretation, {
        "grounding_sources": sorted(aggregate_sources),
        "grounding_strength": strongest,
        "required_evidence_kinds": sorted(aggregate_required),
        "overclaim_guard": overclaim_guard,
        "repair_hints": list(dict.fromkeys(aggregate_repair_hints))[:8],
        "family_bindings": sorted(aggregate_family_bindings),
    }


def _derive_clause_grounding_surface(
    clause: RequirementClauseInterpretation,
) -> dict[str, object]:
    lowered_text = str(clause.clause_text or "").lower()
    lowered_evidence = str(clause.evidence or "").lower()
    observation_tags = _normalize_observation_tags(clause.observation_tags or [])

    grounding_sources = _infer_grounding_sources(
        lowered_evidence=lowered_evidence,
        observation_tags=observation_tags,
    )
    matched_rules = [
        rule
        for rule in _GROUNDING_RULES
        if _rule_matches_clause(
            rule,
            lowered_text=lowered_text,
            observation_tags=observation_tags,
        )
    ]
    required_evidence_kinds = _dedupe(
        kind
        for rule in matched_rules
        for kind in rule.required_evidence_kinds
    )
    repair_hints = _dedupe(
        [
            *(
                str(item).strip()
                for item in (clause.decision_hints or [])
                if isinstance(item, str) and str(item).strip()
            ),
            *(
                hint
                for rule in matched_rules
                for hint in rule.repair_hints
                if isinstance(hint, str) and hint.strip()
            ),
        ]
    )
    family_binding = _infer_family_binding(
        lowered_text=lowered_text,
        observation_tags=observation_tags,
        matched_rules=matched_rules,
    )
    precise_grounding_required = any(rule.precise_grounding_required for rule in matched_rules)
    overclaim_guard = None
    if precise_grounding_required and (
        clause.status == RequirementClauseStatus.INSUFFICIENT_EVIDENCE
        or (
            clause.status == RequirementClauseStatus.VERIFIED
            and len(grounding_sources) < 2
        )
    ):
        overclaim_guard = "geometry_grounding_required"

    if clause.status == RequirementClauseStatus.VERIFIED and len(grounding_sources) >= 2:
        grounding_strength = "strong"
    elif grounding_sources:
        grounding_strength = "partial"
    elif clause.status == RequirementClauseStatus.INSUFFICIENT_EVIDENCE:
        grounding_strength = "weak"
    else:
        grounding_strength = "none"

    return {
        "grounding_sources": grounding_sources,
        "grounding_strength": grounding_strength,
        "required_evidence_kinds": required_evidence_kinds,
        "overclaim_guard": overclaim_guard,
        "repair_hints": repair_hints,
        "family_binding": family_binding,
    }


def _normalize_observation_tags(tags: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    for tag in tags:
        if not isinstance(tag, str):
            continue
        value = tag.strip().lower()
        if value:
            normalized.append(value)
    return normalized


def _infer_grounding_sources(
    *,
    lowered_evidence: str,
    observation_tags: list[str],
) -> list[str]:
    grounding_sources: list[str] = []
    if "bbox" in lowered_evidence or "diameter" in lowered_evidence or "radius" in lowered_evidence:
        grounding_sources.append("geometry")
    if "face" in lowered_evidence or "edge" in lowered_evidence or "pattern" in lowered_evidence:
        grounding_sources.append("topology")
    if "history" in lowered_evidence or "step" in lowered_evidence:
        grounding_sources.append("process")
    for token, normalized_source in _GROUNDING_SOURCE_TAG_ALIASES:
        if any(token in tag for tag in observation_tags) and normalized_source not in grounding_sources:
            grounding_sources.append(normalized_source)
    return grounding_sources


def _infer_family_binding(
    *,
    lowered_text: str,
    observation_tags: list[str],
    matched_rules: list[ClauseGroundingRule],
) -> str | None:
    tagged_family_binding = next(
        (
            tag.split("family:", 1)[1]
            for tag in observation_tags
            if tag.startswith("family:") and tag.split("family:", 1)[1]
        ),
        None,
    )
    if tagged_family_binding:
        return tagged_family_binding
    if _looks_like_named_face_local_edit(
        lowered_text=lowered_text,
        observation_tags=observation_tags,
    ):
        return "named_face_local_edit"
    for rule in matched_rules:
        if rule.family_binding:
            return rule.family_binding
    if "half-shell" in lowered_text or "half shell" in lowered_text:
        return "half_shell"
    if "countersink" in lowered_text or "counterbore" in lowered_text or "hole" in lowered_text:
        return "explicit_anchor_hole"
    return None


def _rule_matches_clause(
    rule: ClauseGroundingRule,
    *,
    lowered_text: str,
    observation_tags: list[str],
) -> bool:
    if rule.rule_id == "explicit_anchor_hole_layout":
        return _looks_like_hole_layout_clause(lowered_text)
    if rule.rule_id == "named_face_local_edit_feature":
        return _looks_like_named_face_local_edit(
            lowered_text=lowered_text,
            observation_tags=observation_tags,
        )
    if rule.rule_id == "overall_dimensions":
        return any(
            token in lowered_text
            for token in ("overall", "bounding box", "outer bounding box", "outer dimensions")
        )
    return any(token in lowered_text for token in rule.tokens)


def _looks_like_hole_layout_clause(lowered_text: str) -> bool:
    return any(token in lowered_text for token in _HOLE_NOUN_TOKENS) and (
        any(token in lowered_text for token in _HOLE_LAYOUT_TOKENS)
        or "x =" in lowered_text
        or "y =" in lowered_text
        or "z =" in lowered_text
    )


def _looks_like_named_face_local_edit(
    *,
    lowered_text: str,
    observation_tags: list[str],
) -> bool:
    has_local_feature_tag = any(
        tag in observation_tags for tag in ("clause:local_feature", "clause:notch_like")
    )
    has_local_feature_token = any(token in lowered_text for token in _LOCAL_FACE_FEATURE_TOKENS)
    if not has_local_feature_tag and not has_local_feature_token:
        return False
    return any(token in lowered_text for token in _LOCAL_FACE_TARGET_TOKENS)


def _dedupe(items: Iterable[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item).strip()
        if not value or value in seen:
            continue
        deduped.append(value)
        seen.add(value)
    return deduped
