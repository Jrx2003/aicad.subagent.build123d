from __future__ import annotations

import re

from sandbox_mcp_server.registry import infer_requirement_probe_families
from sub_agent_runtime.turn_state import RunState
from sub_agent_runtime.tooling.lint.recipes import (
    _requirement_mentions_explicit_cylindrical_slot,
    _requirement_mentions_half_shell_with_split_surface,
)


def _candidate_lint_family_ids(
    *,
    requirement_text: str,
    run_state: RunState | None,
) -> list[str]:
    families: list[str] = []
    lowered_requirement = requirement_text.lower()
    graph = getattr(run_state, "feature_graph", None)
    feature_instances = getattr(graph, "feature_instances", None)
    if isinstance(feature_instances, dict):
        for feature_instance in feature_instances.values():
            family_id = str(getattr(feature_instance, "family_id", "") or "").strip()
            if family_id and family_id not in families and family_id != "general_geometry":
                families.append(family_id)
    for inferred_family_id in infer_requirement_probe_families(
        requirement_text=requirement_text
    ):
        if inferred_family_id == "nested_hollow_section" and not any(
            token in lowered_requirement
            for token in (
                "shell",
                "shelled",
                "hollow enclosure",
                "enclosure",
                "housing",
                "casing",
                "clamshell",
                "lid",
                "base",
            )
        ):
            continue
        if (
            inferred_family_id
            and inferred_family_id not in families
            and inferred_family_id != "general_geometry"
        ):
            families.append(inferred_family_id)
    if (
        "sweep" in lowered_requirement
        and any(
            token in lowered_requirement
            for token in ("path", "rail", "profile sketch", "concentric", "reference plane", "tangent arc")
        )
        and "path_sweep" not in families
    ):
        families.append("path_sweep")
    if (
        any(token in lowered_requirement for token in ("countersink", "countersunk"))
        and "explicit_anchor_hole" not in families
    ):
        families.append("explicit_anchor_hole")
    if (
        "explicit_anchor_hole" not in families
        and _requirement_mentions_explicit_hole_anchors(lowered_requirement)
    ):
        families.append("explicit_anchor_hole")
    if (
        "four point" in lowered_requirement
        or "four points" in lowered_requirement
        or ("four" in lowered_requirement and "hole" in lowered_requirement)
    ) and "pattern_distribution" not in families:
        families.append("pattern_distribution")
    if _requirement_mentions_explicit_cylindrical_slot(lowered_requirement):
        if "slots" not in families:
            families.append("slots")
    if (
        "annular groove" in lowered_requirement
        or ("groove" in lowered_requirement and "revol" in lowered_requirement)
    ):
        if "annular_groove" not in families:
            families.append("annular_groove")
        if "axisymmetric_profile" not in families:
            families.append("axisymmetric_profile")
    if (
        (
            any(
                token in lowered_requirement
                for token in (
                    "hemisphere",
                    "hemispherical",
                    "spherical recess",
                    "spherical cavity",
                    "spherical depression",
                )
            )
            or ("sphere" in lowered_requirement and "recess" in lowered_requirement)
        )
        and "spherical_recess" not in families
    ):
        families.append("spherical_recess")
    if (
        any(token in lowered_requirement for token in ("recess", "pocket", "groove"))
        and any(
            token in lowered_requirement
            for token in (
                "top face",
                "top-face",
                "bottom face",
                "bottom-face",
                "front face",
                "front-face",
                "back face",
                "back-face",
                "left face",
                "left-face",
                "right face",
                "right-face",
            )
        )
        and "named_face_local_edit" not in families
    ):
        families.append("named_face_local_edit")
    if (
        any(token in lowered_requirement for token in ("shell", "shelled", "hollow enclosure"))
        and "nested_hollow_section" not in families
    ):
        families.append("nested_hollow_section")
    if _requirement_mentions_half_shell_with_split_surface(lowered_requirement):
        if "axisymmetric_profile" not in families:
            families.append("axisymmetric_profile")
    if (
        any(token in lowered_requirement for token in ("pattern", "quantity", "spacing"))
        and "pattern_distribution" not in families
    ):
        families.append("pattern_distribution")
    return families


def _requirement_mentions_explicit_hole_anchors(requirement_lower: str) -> bool:
    if not requirement_lower or "hole" not in requirement_lower:
        return False
    coordinate_tokens = (
        "coordinates (",
        "coordinate (",
        "centered at x",
        "centered at y",
        "centered at z",
        "at x =",
        "at y =",
        "at z =",
        "x =",
        "y =",
        "z =",
    )
    if any(token in requirement_lower for token in coordinate_tokens):
        return True
    return bool(
        re.search(
            r"\(\s*-?[0-9]+(?:\.[0-9]+)?\s*,\s*-?[0-9]+(?:\.[0-9]+)?(?:\s*,\s*-?[0-9]+(?:\.[0-9]+)?)?\s*\)",
            requirement_lower,
        )
    )


def _requirement_mentions_local_finish_fillet_tail(requirement_lower: str) -> bool:
    lowered = str(requirement_lower or "").lower()
    local_finish_tokens = (
        "local finish",
        "local finishing",
        "topology-aware",
        "topology aware",
        "later local finish",
        "later topology-aware",
        "opening rim",
        "rim edges",
        "target edge",
    )
    feature_tokens = (
        "fillet",
        "edge fillet",
        "chamfer",
        "edge chamfer",
    )
    return any(token in lowered for token in local_finish_tokens) and any(
        token in lowered for token in feature_tokens
    )


__all__ = [
    "_candidate_lint_family_ids",
    "_requirement_mentions_explicit_hole_anchors",
    "_requirement_mentions_local_finish_fillet_tail",
]
