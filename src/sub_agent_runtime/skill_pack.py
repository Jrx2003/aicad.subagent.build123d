from __future__ import annotations

import json
import re
from typing import Any

from common.blocker_taxonomy import (
    taxonomy_family_ids_from_validation_payload,
    taxonomy_records_from_validation_payload,
)
from sandbox_mcp_server.registry import (
    analyze_requirement_semantics,
    infer_requirement_probe_families,
)


def _kernel_validation_assessment(
    domain_kernel_digest: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(domain_kernel_digest, dict):
        return {}
    assessment = domain_kernel_digest.get("latest_validation_assessment")
    return assessment if isinstance(assessment, dict) else {}


def _domain_kernel_active_family_ids(
    domain_kernel_digest: dict[str, Any] | None,
) -> set[str]:
    if not isinstance(domain_kernel_digest, dict):
        return set()
    active_feature_instances = domain_kernel_digest.get("active_feature_instances")
    if not isinstance(active_feature_instances, list):
        return set()
    return {
        str(item.get("family_id") or "").strip()
        for item in active_feature_instances
        if isinstance(item, dict) and str(item.get("family_id") or "").strip()
    }


def _validation_has_insufficient_evidence_guidance(
    latest_validation: dict[str, Any] | None,
    *,
    domain_kernel_digest: dict[str, Any] | None = None,
) -> bool:
    kernel_assessment = _kernel_validation_assessment(domain_kernel_digest)
    assessment_tags = {
        str(tag).strip().lower()
        for tag in (kernel_assessment.get("observation_tags") or [])
        if isinstance(tag, str) and str(tag).strip()
    }
    assessment_hints = {
        str(hint).strip().lower()
        for hint in (kernel_assessment.get("decision_hints") or [])
        if isinstance(hint, str) and str(hint).strip()
    }
    if (
        bool(kernel_assessment.get("insufficient_evidence"))
        or "insufficient_evidence" in assessment_tags
        or "inspect_more_evidence" in assessment_hints
    ):
        return True
    if not isinstance(latest_validation, dict):
        return False
    top_level_tags = {
        str(tag).strip().lower()
        for tag in (latest_validation.get("observation_tags") or [])
        if isinstance(tag, str) and str(tag).strip()
    }
    top_level_hints = {
        str(hint).strip().lower()
        for hint in (latest_validation.get("decision_hints") or [])
        if isinstance(hint, str) and str(hint).strip()
    }
    if (
        bool(latest_validation.get("insufficient_evidence"))
        or "insufficient_evidence" in top_level_tags
        or "inspect_more_evidence" in top_level_hints
    ):
        return True
    for record in taxonomy_records_from_validation_payload(latest_validation):
        observation_tags = {
            str(tag).strip().lower()
            for tag in getattr(record, "observation_tags", [])
            if isinstance(tag, str) and str(tag).strip()
        }
        decision_hints = {
            str(hint).strip().lower()
            for hint in getattr(record, "decision_hints", [])
            if isinstance(hint, str) and str(hint).strip()
        }
        if (
            "insufficient_evidence" in observation_tags
            or "inspect_more_evidence" in decision_hints
            or str(getattr(record, "recommended_repair_lane", "") or "").strip().lower()
            == "inspect_more_evidence"
        ):
            return True
    return False


def _failure_lint_ids(previous_tool_failure_summary: dict[str, Any] | None) -> set[str]:
    if not isinstance(previous_tool_failure_summary, dict):
        return set()
    lint_hits = previous_tool_failure_summary.get("lint_hits")
    if not isinstance(lint_hits, list):
        return set()
    return {
        str(item.get("rule_id") or "").strip()
        for item in lint_hits
        if isinstance(item, dict) and str(item.get("rule_id") or "").strip()
    }


def _failure_lint_hits_payload(
    previous_tool_failure_summary: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not isinstance(previous_tool_failure_summary, dict):
        return []
    lint_hits = previous_tool_failure_summary.get("lint_hits")
    if not isinstance(lint_hits, list):
        return []
    return [item for item in lint_hits if isinstance(item, dict)]


def _failure_repair_recipe_payload(
    previous_tool_failure_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(previous_tool_failure_summary, dict):
        return {}
    repair_recipe = previous_tool_failure_summary.get("repair_recipe")
    return repair_recipe if isinstance(repair_recipe, dict) else {}


def _previous_failure_requires_latest_topology_face_ref(
    previous_tool_failure_summary: dict[str, Any] | None,
) -> bool:
    if not isinstance(previous_tool_failure_summary, dict):
        return False
    error_text = str(previous_tool_failure_summary.get("error") or "").strip().lower()
    return (
        "create_sketch must use face_ref from latest query_topology during local_finish"
        in error_text
    )


def _previous_failure_used_candidate_set_label_as_reference(
    previous_tool_failure_summary: dict[str, Any] | None,
) -> bool:
    if not isinstance(previous_tool_failure_summary, dict):
        return False
    error_text = str(previous_tool_failure_summary.get("error") or "").strip().lower()
    return (
        "invalid_reference: malformed face_ref" in error_text
        and "candidate-set label" in error_text
    ) or (
        "invalid_reference: malformed edge_ref" in error_text
        and "candidate-set label" in error_text
    )


def _previous_failure_hit_detached_subtractive_builder_runtime_error(
    previous_tool_failure_summary: dict[str, Any] | None,
) -> bool:
    if not isinstance(previous_tool_failure_summary, dict):
        return False
    error_text = str(previous_tool_failure_summary.get("error") or "").strip().lower()
    return "nothing to subtract from" in error_text


def _latest_repair_packet_payload(
    domain_kernel_digest: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(domain_kernel_digest, dict):
        return {}
    recipe_id = str(domain_kernel_digest.get("latest_repair_packet_recipe_id") or "").strip()
    recipe_summary = str(
        domain_kernel_digest.get("latest_repair_packet_recipe_summary") or ""
    ).strip()
    family_id = str(domain_kernel_digest.get("latest_repair_packet_family_id") or "").strip()
    repair_mode = str(
        domain_kernel_digest.get("latest_repair_packet_repair_mode") or ""
    ).strip()
    skeleton_raw = domain_kernel_digest.get("latest_repair_packet_recipe_skeleton")
    skeleton = skeleton_raw if isinstance(skeleton_raw, dict) else {}
    target_anchor_summary_raw = domain_kernel_digest.get(
        "latest_repair_packet_target_anchor_summary"
    )
    target_anchor_summary = (
        target_anchor_summary_raw if isinstance(target_anchor_summary_raw, dict) else {}
    )
    host_frame_raw = domain_kernel_digest.get("latest_repair_packet_host_frame")
    host_frame = host_frame_raw if isinstance(host_frame_raw, dict) else {}
    if not any(
        (
            recipe_id,
            recipe_summary,
            family_id,
            repair_mode,
            skeleton,
            target_anchor_summary,
            host_frame,
        )
    ):
        return {}
    return {
        "recipe_id": recipe_id,
        "recipe_summary": recipe_summary,
        "family_id": family_id,
        "repair_mode": repair_mode,
        "recipe_skeleton": skeleton,
        "target_anchor_summary": target_anchor_summary,
        "host_frame": host_frame,
    }


def _coerce_xy_points(points: Any) -> list[list[float]]:
    if not isinstance(points, list):
        return []
    normalized: list[list[float]] = []
    for item in points:
        if (
            isinstance(item, (list, tuple))
            and len(item) >= 2
            and isinstance(item[0], (int, float))
            and isinstance(item[1], (int, float))
        ):
            normalized.append([float(item[0]), float(item[1])])
    return normalized


def _extract_local_center_preservation_summary(
    domain_kernel_digest: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(domain_kernel_digest, dict):
        return {}
    active_feature_instances = domain_kernel_digest.get("active_feature_instances")
    if not isinstance(active_feature_instances, list):
        return {}
    for item in active_feature_instances:
        if not isinstance(item, dict):
            continue
        parameter_bindings = (
            item.get("parameter_bindings")
            if isinstance(item.get("parameter_bindings"), dict)
            else {}
        )
        realized_centers = _coerce_xy_points(parameter_bindings.get("realized_centers"))
        expected_centers = _coerce_xy_points(parameter_bindings.get("expected_local_centers"))
        expected_count_raw = parameter_bindings.get("expected_local_center_count")
        expected_count = (
            int(expected_count_raw)
            if isinstance(expected_count_raw, (int, float))
            else (len(expected_centers) if expected_centers else None)
        )
        if not realized_centers or expected_count is None:
            continue
        if len(realized_centers) != expected_count:
            continue
        family_id = str(item.get("family_id") or "").strip()
        if not family_id:
            continue
        host_face = str(
            parameter_bindings.get("host_face")
            or item.get("host_ids", [""])[0]
            or ""
        ).strip()
        return {
            "family_id": family_id,
            "host_face": host_face,
            "expected_center_count": expected_count,
            "realized_centers": realized_centers,
            "source": "active_feature_instances",
        }
    return {}


def _repair_packet_skeleton_summary(
    recipe_skeleton: dict[str, Any] | None,
    *,
    host_frame: dict[str, Any] | None = None,
    target_anchor_summary: dict[str, Any] | None = None,
) -> str:
    if not isinstance(recipe_skeleton, dict):
        recipe_skeleton = {}
    if not isinstance(host_frame, dict):
        host_frame = {}
    if not isinstance(target_anchor_summary, dict):
        target_anchor_summary = {}
    fields: list[tuple[str, Any]] = []
    for key in (
        "mode",
        "host_face",
        "workplane_frame",
        "point_strategy",
        "center_source_key",
        "hole_call",
        "cutter_kind",
        "cutter_strategy",
        "profile_kind",
        "split_axis",
        "half_plane",
        "hole_axis",
        "sphere_center_z_strategy",
        "pad_strategy",
    ):
        value = recipe_skeleton.get(key)
        if value not in (None, "", [], {}):
            fields.append((key, value))
    frame_kind = host_frame.get("frame_kind")
    if frame_kind not in (None, "", [], {}):
        fields.append(("frame_kind", frame_kind))
    expected_local_centers = target_anchor_summary.get("expected_local_centers")
    if isinstance(expected_local_centers, list) and expected_local_centers:
        fields.append(("expected_local_centers", expected_local_centers[:4]))
    requested_centers = target_anchor_summary.get("requested_centers")
    if isinstance(requested_centers, list) and requested_centers:
        fields.append(("requested_centers", requested_centers[:4]))
    expected_half_profile_span = target_anchor_summary.get("expected_half_profile_span")
    if expected_half_profile_span not in (None, ""):
        fields.append(("expected_half_profile_span", expected_half_profile_span))
    expected_length = target_anchor_summary.get("expected_length")
    if expected_length not in (None, ""):
        fields.append(("expected_length", expected_length))
    if not fields:
        return ""
    return ", ".join(
        f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in fields[:8]
    )


def _repair_packet_runtime_skill(
    domain_kernel_digest: dict[str, Any] | None,
) -> dict[str, Any] | None:
    payload = _latest_repair_packet_payload(domain_kernel_digest)
    if not payload:
        return None
    recipe_id = str(payload.get("recipe_id") or "").strip()
    recipe_summary = str(payload.get("recipe_summary") or "").strip()
    family_id = str(payload.get("family_id") or "").strip()
    repair_mode = str(payload.get("repair_mode") or "").strip()
    recipe_skeleton = (
        payload.get("recipe_skeleton") if isinstance(payload.get("recipe_skeleton"), dict) else {}
    )
    target_anchor_summary = (
        payload.get("target_anchor_summary")
        if isinstance(payload.get("target_anchor_summary"), dict)
        else {}
    )
    host_frame = payload.get("host_frame") if isinstance(payload.get("host_frame"), dict) else {}
    guidance = [
        "The semantic kernel already surfaced a current repair packet; use this packet-aligned lane before inventing a different whole-part rewrite.",
    ]
    if family_id or recipe_id or repair_mode:
        identifiers = []
        if family_id:
            identifiers.append(f"family={family_id}")
        if recipe_id:
            identifiers.append(f"recipe={recipe_id}")
        if repair_mode:
            identifiers.append(f"repair_mode={repair_mode}")
        guidance.append("Current packet identifiers: " + ", ".join(identifiers) + ".")
    if recipe_summary:
        guidance.append("Recipe summary: " + recipe_summary)
    skeleton_summary = _repair_packet_skeleton_summary(
        recipe_skeleton,
        host_frame=host_frame,
        target_anchor_summary=target_anchor_summary,
    )
    if skeleton_summary:
        guidance.append("Recipe skeleton: " + skeleton_summary + ".")
    center_source_key = str(recipe_skeleton.get("center_source_key") or "").strip()
    if center_source_key == "derive_from_requirement_or_validation":
        guidance.append(
            "The center layout is not fully grounded yet; derive it from the requirement/validation evidence or a topology read before cutting, instead of improvising manual cutter coordinates."
        )
    cutter_strategy = str(recipe_skeleton.get("cutter_strategy") or "").strip()
    helper_first_hole_call = (
        str(recipe_skeleton.get("hole_call") or "").strip() == "CounterSinkHole_or_Hole"
    )
    if helper_first_hole_call or (
        "avoid_manual_cone_cylinder_inside_active_builder" in cutter_strategy
    ):
        guidance.append(
            "Prefer the native hole helper contract on the target host face, and do not fall back to manual cone/cylinder cutters inside an active BuildPart unless the helper contract is provably insufficient."
        )
    return {
        "skill_id": "kernel_repair_packet_recipe",
        "when_relevant": "Use when the domain kernel digest already exposes a concrete repair packet recipe for the active family.",
        "guidance": guidance[:6],
    }


def _failure_repair_recipe_runtime_skill(
    previous_tool_failure_summary: dict[str, Any] | None,
) -> dict[str, Any] | None:
    payload = _failure_repair_recipe_payload(previous_tool_failure_summary)
    if not payload:
        return None
    recipe_id = str(payload.get("recipe_id") or "").strip()
    recipe_summary = str(payload.get("recipe_summary") or "").strip()
    repair_family = str(payload.get("repair_family") or "").strip()
    recipe_skeleton = (
        payload.get("recipe_skeleton") if isinstance(payload.get("recipe_skeleton"), dict) else {}
    )
    guidance = [
        "The previous write failure already exposed a concrete repair recipe; keep the next execute_build123d attempt on that lane instead of improvising a fresh whole-part rewrite."
    ]
    if recipe_id or repair_family:
        identifiers = []
        if recipe_id:
            identifiers.append(f"recipe={recipe_id}")
        if repair_family:
            identifiers.append(f"repair_family={repair_family}")
        guidance.append("Failure recipe identifiers: " + ", ".join(identifiers) + ".")
    if recipe_summary:
        guidance.append("Failure recipe summary: " + recipe_summary)
    skeleton_summary = _repair_packet_skeleton_summary(recipe_skeleton)
    if skeleton_summary:
        guidance.append("Failure recipe skeleton: " + skeleton_summary + ".")
    recipe_steps = recipe_skeleton.get("steps")
    if isinstance(recipe_steps, list) and recipe_steps:
        guidance.append(
            "Failure recipe steps: "
            + " | ".join(
                str(item).strip()
                for item in recipe_steps[:4]
                if isinstance(item, str) and str(item).strip()
            )
            + "."
        )
    return {
        "skill_id": "execute_build123d_failure_recipe_focus",
        "when_relevant": "Use when previous_tool_failure_summary already includes a concrete Build123d repair recipe from lint/runtime failure analysis.",
        "guidance": guidance[:5],
    }


def _failure_lint_runtime_skill(
    previous_tool_failure_summary: dict[str, Any] | None,
) -> dict[str, Any] | None:
    lint_hits = _failure_lint_hits_payload(previous_tool_failure_summary)
    if not lint_hits:
        return None
    lint_ids = {
        str(item.get("rule_id") or "").strip()
        for item in lint_hits
        if isinstance(item, dict) and str(item.get("rule_id") or "").strip()
    }
    structural_builder_lint_ids = {
        "invalid_build123d_contract.detached_subtractive_builder_without_host",
        "invalid_build123d_contract.active_builder_part_mutation",
        "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic",
        "invalid_build123d_contract.active_builder_temporary_primitive_transform_rebind",
        "invalid_build123d_api.nested_buildpart_part_transform",
    }
    guidance = [
        "The last execute_build123d failure already named concrete lint contracts; repair those exact contracts before changing geometry strategy."
    ]
    if lint_ids.intersection(structural_builder_lint_ids) and (
        lint_ids - structural_builder_lint_ids
    ):
        guidance.append(
            "When mixed lint hits include both feature-specific keyword/helper mistakes and active-builder violations, repair the builder-authority contract first; otherwise the next rewrite often stays non-executable even after syntax cleanup."
        )
    for item in lint_hits[:2]:
        rule_id = str(item.get("rule_id") or "").strip()
        repair_hint = str(item.get("repair_hint") or item.get("message") or "").strip()
        recommended_recipe_id = str(item.get("recommended_recipe_id") or "").strip()
        if rule_id:
            guidance.append(f"Lint contract: rule={rule_id}.")
        if recommended_recipe_id:
            guidance.append(
                "Preferred repair lane: "
                f"recommended_recipe_id={recommended_recipe_id}."
            )
        if repair_hint:
            guidance.append("Direct repair hint: " + repair_hint)
    if "invalid_build123d_contract.active_builder_temporary_primitive_transform_rebind" in lint_ids:
        guidance.append(
            "For active-builder transform rebind failures, choose one valid lane only: inside an active host, place the primitive correctly with `Locations(...)` at creation time instead of creating it first and relocating the Python variable afterward."
        )
        guidance.append(
            "If the solid truly needs detached rotation or placement, close that builder first and only then orient/place the detached solid with `Rot(...) * part` or `Pos(...) * Rot(...) * part` outside the active builder."
        )
    return {
        "skill_id": "execute_build123d_failure_lint_contract",
        "when_relevant": "Use when previous_tool_failure_summary exposes concrete lint hits with direct repair hints.",
        "guidance": guidance[:6],
    }


def _skill_pack_prefers_code_first(
    *,
    requirement_lower: str,
    semantics: Any,
    latest_validation: dict[str, Any] | None,
    domain_kernel_digest: dict[str, Any] | None,
) -> bool:
    kernel_assessment = _kernel_validation_assessment(domain_kernel_digest)
    latest_patch_repair_mode = str(
        (domain_kernel_digest or {}).get("latest_patch_repair_mode") or ""
    ).strip()
    latest_packet_repair_mode = str(
        (domain_kernel_digest or {}).get("latest_repair_packet_repair_mode") or ""
    ).strip()
    if latest_patch_repair_mode in {"whole_part_rebuild", "subtree_rebuild"}:
        return True
    if latest_packet_repair_mode and latest_packet_repair_mode != "local_edit":
        return True
    if kernel_assessment and kernel_assessment.get("contradicted_clause_ids"):
        return True
    taxonomy_families = set(taxonomy_family_ids_from_validation_payload(latest_validation))
    if taxonomy_families.intersection(
        {
            "annular_groove",
            "axisymmetric_profile",
            "nested_hollow_section",
            "orthogonal_union",
            "path_sweep",
            "spherical_recess",
            "pattern_distribution",
        }
    ):
        return True
    return (
        bool(getattr(semantics, "mentions_revolved_groove_cut", False))
        or bool(getattr(semantics, "mentions_nested_profile_cutout", False))
        or bool(getattr(semantics, "mentions_profile_region_frame", False))
        or bool(getattr(semantics, "mentions_multi_plane_additive_union", False))
        or bool(getattr(semantics, "mentions_spherical_recess", False))
        or bool(getattr(semantics, "mentions_pattern", False))
        or _requirement_mentions_explicit_path_sweep(requirement_lower)
        or "hollow section" in requirement_lower
        or "inner void" in requirement_lower
        or "axisymmetric" in requirement_lower
        or "shaft" in requirement_lower
        or "stud" in requirement_lower
        or "union" in requirement_lower
        or "orthogonal" in requirement_lower
        or _requirement_mentions_half_shell_with_split_surface(requirement_lower)
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


def requirement_prefers_code_first_family(
    *,
    requirements: dict[str, Any],
    latest_validation: dict[str, Any] | None = None,
    domain_kernel_digest: dict[str, Any] | None = None,
) -> bool:
    requirement_text = _requirements_text(requirements)
    requirement_lower = requirement_text.lower()
    semantics = analyze_requirement_semantics(requirements, requirement_text)
    return _skill_pack_prefers_code_first(
        requirement_lower=requirement_lower,
        semantics=semantics,
        latest_validation=latest_validation,
        domain_kernel_digest=domain_kernel_digest,
    )


def recommended_feature_probe_families(
    *,
    requirements: dict[str, Any],
    latest_validation: dict[str, Any] | None = None,
    domain_kernel_digest: dict[str, Any] | None = None,
) -> list[str]:
    requirement_text = _requirements_text(requirements)
    requirement_lower = requirement_text.lower()
    semantics = analyze_requirement_semantics(requirements, requirement_text)
    blockers = {
        str(item)
        for item in (latest_validation or {}).get("blockers", [])
        if isinstance(item, str)
    }
    taxonomy_families = taxonomy_family_ids_from_validation_payload(latest_validation)
    taxonomy_present = bool(taxonomy_families)
    families: list[str] = []

    def _append(raw_family_id: Any) -> None:
        family_id = str(raw_family_id or "").strip()
        if family_id:
            families.append(family_id)

    active_feature_instances = (
        (domain_kernel_digest or {}).get("active_feature_instances")
        if isinstance(domain_kernel_digest, dict)
        else None
    )
    if isinstance(active_feature_instances, list):
        for item in active_feature_instances:
            if isinstance(item, dict):
                _append(item.get("family_id"))
    _append((domain_kernel_digest or {}).get("latest_repair_packet_family_id"))
    latest_patch_feature_instances = (
        (domain_kernel_digest or {}).get("latest_patch_feature_instances")
        if isinstance(domain_kernel_digest, dict)
        else None
    )
    if isinstance(latest_patch_feature_instances, list):
        for item in latest_patch_feature_instances:
            if isinstance(item, dict):
                _append(item.get("family_id"))

    families.extend(taxonomy_families)
    families.extend(
        infer_requirement_probe_families(
            requirements=requirements,
            requirement_text=requirement_text,
            semantics=semantics,
        )
    )
    if (
        bool(getattr(semantics, "mentions_revolved_groove_cut", False))
        or (
            not taxonomy_present
            and any("annular" in item or "revolve" in item for item in blockers)
        )
    ):
        families.extend(["annular_groove", "axisymmetric_profile"])
    if _requirement_suggests_mixed_nested_section(
        requirement_lower=requirement_lower,
        blockers=blockers,
    ):
        families.append("nested_hollow_section")
    if bool(getattr(semantics, "mentions_spherical_recess", False)):
        families.append("spherical_recess")
    if bool(getattr(semantics, "mentions_pattern", False)):
        families.append("pattern_distribution")
    if "path_sweep" in taxonomy_families or _requirement_mentions_explicit_path_sweep(
        requirement_lower
    ):
        families.append("path_sweep")
    if (
        _requirement_prefers_named_face_local_feature_sequence(requirement_lower)
        or _requirement_suggests_local_finish_probe_family(requirement_lower)
    ):
        families.append("named_face_local_edit")
    deduped: list[str] = []
    seen: set[str] = set()
    for family in families:
        if family in seen:
            continue
        seen.add(family)
        deduped.append(family)
    return deduped


def build_runtime_skill_pack(
    *,
    requirements: dict[str, Any],
    latest_validation: dict[str, Any] | None,
    latest_write_health: dict[str, Any] | None,
    previous_tool_failure_summary: dict[str, Any] | None = None,
    domain_kernel_digest: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    requirement_text = _requirements_text(requirements)
    requirement_lower = requirement_text.lower()
    semantics = analyze_requirement_semantics(requirements, requirement_text)
    blockers = {
        str(item)
        for item in (latest_validation or {}).get("blockers", [])
        if isinstance(item, str)
    }
    taxonomy_families = set(
        taxonomy_family_ids_from_validation_payload(latest_validation)
    )
    annular_blockers = {
        "feature_annular_groove",
        "feature_revolved_groove_setup",
        "feature_revolved_groove_alignment",
        "feature_revolved_groove_result",
    }
    annular_blockers_active = bool(blockers.intersection(annular_blockers))
    invalid_signals = {
        str(item)
        for item in (latest_write_health or {}).get("invalid_signals", [])
        if isinstance(item, str)
    }
    latest_tool = str((latest_write_health or {}).get("tool") or "").strip().lower()
    if not latest_tool:
        latest_tool = str(
            (previous_tool_failure_summary or {}).get("tool") or ""
        ).strip().lower()
    previous_failure_kind = str(
        (previous_tool_failure_summary or {}).get("effective_failure_kind")
        or (previous_tool_failure_summary or {}).get("failure_kind")
        or ""
    ).strip()
    previous_failure_lint_ids = _failure_lint_ids(previous_tool_failure_summary)
    same_tool_failure_count = int(
        (previous_tool_failure_summary or {}).get("same_tool_failure_count") or 0
    )
    domain_kernel_active_families = _domain_kernel_active_family_ids(domain_kernel_digest)
    latest_repair_packet_family_id = str(
        (domain_kernel_digest or {}).get("latest_repair_packet_family_id") or ""
    ).strip()
    latest_repair_packet_recipe_id = str(
        (domain_kernel_digest or {}).get("latest_repair_packet_recipe_id") or ""
    ).strip()
    local_center_preservation_summary = _extract_local_center_preservation_summary(
        domain_kernel_digest
    )
    insufficient_evidence_guidance = _validation_has_insufficient_evidence_guidance(
        latest_validation,
        domain_kernel_digest=domain_kernel_digest,
    )
    code_first_family = requirement_prefers_code_first_family(
        requirements=requirements,
        latest_validation=latest_validation,
        domain_kernel_digest=domain_kernel_digest,
    )

    skills: list[dict[str, Any]] = []
    repair_packet_skill = _repair_packet_runtime_skill(domain_kernel_digest)
    if repair_packet_skill is not None:
        skills.append(repair_packet_skill)
    failure_repair_skill = _failure_repair_recipe_runtime_skill(previous_tool_failure_summary)
    if failure_repair_skill is not None:
        skills.append(failure_repair_skill)
    failure_lint_skill = _failure_lint_runtime_skill(previous_tool_failure_summary)
    if failure_lint_skill is not None:
        skills.append(failure_lint_skill)

    skills.append(
        {
            "skill_id": "execute_build123d_minimal_script_hygiene",
            "when_relevant": "Use whenever you write or repair execute_build123d code.",
            "guidance": [
                "Keep execute_build123d scripts minimal and builder-first: use BuildPart for the host solid, BuildSketch for sections, BuildLine for rails, and assign the final geometry explicitly to result.",
                "Sketch primitives such as `Circle(...)`, `Ellipse(...)`, `Rectangle(...)`, and `RegularPolygon(...)` belong inside `BuildSketch`, not directly inside `BuildPart`.",
                "Do not write `with Rot(...):` or `with Pos(...):`, do not invent `Loc(...)`, and do not guess `Plane(...).moved(...)`; `Rot(...)` / `Pos(...)` are transforms, not context managers, and workplanes translate with `Plane.move(Location(...))`, `Plane.offset(...)`, or `Plane.shift_origin(...)`. Use `Location(...)` for location objects, `Locations(...)` for scoped placement, or multiply the transform with a detached solid.",
                "Do not import `ocp_vscode` or call `show(...)` / `show_object(...)`; sandbox execution must return geometry through `result = ...` only.",
                "Do not invent `Box(..., radius=...)`; if the body needs rounded plan corners, use `RectangleRounded(...)` + `extrude(...)` or create the box first and add explicit edge fillets.",
                "For rounded pillbox or rounded enclosure shells, prefer a `RectangleRounded(...)` footprint inside `BuildSketch(...)` and extrude from that stable profile instead of `Box(...)` plus a first-pass broad `edges().filter_by(Axis.Z)` fillet on the fresh shell host.",
                "If a detached helper or cutter needs anisotropic scaling, use lowercase `scale(shape, by=(sx, sy, sz))`; do not invent `Scale(...)` or `Scale.by(...)`.",
                "Keep primitive signatures literal: for boxes use `Box(length, width, height)` or the matching keyword names, and do not invent aliases such as `depth=`.",
                "Prefer short named constants plus explicit Plane, Axis, Pos, Rot, and Locations placement over clever inline helpers or implicit origin assumptions.",
                "Do not add print statements, f-strings, or temporary string-formatting diagnostics unless the runtime explicitly asks for them; they increase syntax-risk without improving the benchmark feedback loop.",
                "Remember that `Box(length, width, height)` is centered at the origin by default; on a centered box the top-face plane is at `+height/2`, not `+height`.",
                "If the requirement explicitly says to sketch on `Plane.XY` and extrude upward, preserve that sketch-plus-positive-extrude contract instead of silently swapping in a centered `Box(...)` whose base no longer sits on the named plane.",
                "If the requirement asks for separate parts such as a lid and base, keep those solids in a real assembly/closed coordinate frame unless the requirement explicitly asks for an exploded view. Do not move one part aside merely for visibility, because geometry reads and validation use the actual placed coordinates.",
                "If the requirement also gives an overall bounding box, keep every part, hinge barrel/pin, latch, and mating feature inside that assembled envelope. Do not stack the lid above the base or place a hinge outside the declared outer size unless the prompt explicitly asks for an exploded or out-of-envelope fixture view.",
                "For shelled bodies, stay on Build123d shell/offset semantics or an explicit inner-solid subtraction; do not invent a bare `shell(...)` helper.",
                "For boolean cuts, build explicit solid cutters and combine them with supported solid booleans or builder subtractive modes; do not invent bare `subtract(...)` or bare `rotate(...)` helpers.",
                "When filtering ShapeLists by axis direction, use `filter_by(Axis.X/Y/Z)` or an explicit predicate; there is no `filter_by_direction(...)` helper.",
                "For axis-parallel selection, do not call `edge.is_parallel(Axis.Y)` or similar guessed edge-instance helpers; filter the source ShapeList with `edges.filter_by(Axis.Y)` or use an explicit predicate.",
                "If you close a `BuildLine` wire and need a face from it, use lowercase `make_face()`; do not invent `MakeFace()`.",
                "Curve helpers such as `Polyline(...)`, `Line(...)`, `CenterArc(...)`, and `RadiusArc(...)` belong inside `BuildLine`, not directly inside `BuildSketch`.",
                "If a `BuildSketch` only contains wire geometry from `BuildLine`, call lowercase `make_face()` before `extrude(...)` or `revolve(...)`; otherwise the sketch can stay empty and fail with `sketch is None`.",
                "Inside `BuildSketch`, do not call the enclosing `BuildPart` alias's `vertices()` / `edges()` / `faces()`; build the 2D profile directly, then wait until solid creation is complete before selecting solid topology.",
                "For non-XY planar polygons that keep failing inside `BuildSketch`, prefer `Wire.make_polygon(...)` with explicit 3D vertices, then build a real `Face(...)` / `make_face(...)` result and extrude that face with `Solid.extrude(...)` instead of assuming the sketch builder will auto-promote the edges into a face.",
                "Use capitalized `Hole(...)`, not lowercase `hole(...)`.",
                "For Build123d revolves, use the supported `revolution_arc=` keyword or the default full revolve; do not invent `angle=` inside `revolve(...)`.",
                "For fillets, prefer `fillet(edge_list, radius=...)` on a selected ShapeList; if you use member-style `solid.fillet(...)`, do not mix a positional edge argument with `radius=`.",
                "If you choose `CounterSinkHole(...)`, keep the exact helper/keyword contract `CounterSinkHole(radius=..., counter_sink_radius=..., depth=..., counter_sink_angle=...)`; do not invent `CountersinkHole(...)`, `CounterSink(...)`, or `countersink_radius=` aliases.",
                "There is no `Workplanes(...)` helper in Build123d; use the target plane directly with `BuildSketch(plane)` or place the feature on that face/workplane with `Locations(...)`.",
                "`CounterSinkHole(...)` belongs in `BuildPart`, not `BuildSketch`; if the requirement names a top/front/side host face, place the hole tool on that actual face plane instead of leaving it on the default XY mid-plane.",
                "For explicit countersink arrays on a planar host face, prefer one `CounterSinkHole(...)` pass on the first attempt when the requirement already gives the through-hole diameter, head diameter, and cone angle; keep the exact helper contract and explicit host-face placement.",
                "`Plane.rotated(rotation, ordering=...)` only changes orientation; it does not relocate the workplane.",
                "The plane origin stays where it was after `Plane.rotated(...)`; if you need translation, use `Plane.offset(...)` along the plane normal or place the feature/cutter with `Pos(...)`.",
                "Do not write `Plane.XY * (x, y, z)` or similar tuple multiplication forms. Use `Locations((x, y, z))` for point placement, or `Plane.XY.offset(z)` / `Plane.XZ.offset(y)` / `Plane.YZ.offset(x)` for translated workplanes.",
                "Do not instantiate a detached `Cylinder(...)` cutter inside an active `BuildPart` and then do `result = part.part - cutter`; that primitive is already added to the builder. Build the host in one `BuildPart`, close it, then create the cutter outside the active builder before the explicit boolean.",
                "Every primitive constructor inside an active `BuildPart` mutates that host immediately. Do not use temporary solid arithmetic staging values there such as `outer_cyl = Cylinder(...)`, `inner_cyl = Cylinder(...)`, or `half_space_box = Box(...)` and then reuse them in later boolean/intersection expressions; close the host builder before doing explicit solid arithmetic, or encode the shape through one builder-native sketch/profile recipe.",
                "Inside an active `BuildPart`, do not expect `peg = Pos(...) * peg` or `peg = Rot(...) * peg` to move geometry that was already added to the host. Use `Locations(...)`, explicit local frames, or transform a detached solid only after the host builder closes.",
                "If you truly need a temporary staging solid inside an active `BuildPart`, create it with `mode=Mode.PRIVATE` so it stays out of the host until the later explicit boolean.",
                "Do not assign back into `part.part` while that `BuildPart` is still open. Avoid `part.part = ...`, `part.part += ...`, and `part.part -= ...` inside the active builder; keep adds/cuts builder-native, or close the builder first and then do detached booleans.",
                "Do not assign back into `part.solid`; inside `BuildPart` prefer builder-native subtraction, and if you need an explicit boolean after the builder, subtract from `part.part` instead.",
                "Do not open a nested `BuildPart()` cutter inside an active `BuildPart` and then mutate `part.part -= cutter.part`; repeated placements can collapse into one origin-centered boolean instead of preserving the intended feature locations.",
            ],
        }
    )

    if latest_tool == "execute_build123d" and previous_failure_lint_ids:
        skills.append(
            {
                "skill_id": "execute_build123d_api_lint_repair_first",
                "when_relevant": "Use when preflight lint already identified a concrete unsupported Build123d API or keyword surface.",
                "guidance": [
                    "Treat lint hits as authoritative repair targets; do not retry the same execute_build123d pattern unchanged.",
                    "If a repair_recipe is available in previous_tool_failure_summary, follow that recipe before opening new generic read turns.",
                    "Keep the next write materially simpler than the rejected one and stay on supported builder-first Build123d surfaces.",
                ],
            }
        )

    if latest_tool == "execute_build123d" and _previous_failure_hit_detached_subtractive_builder_runtime_error(
        previous_tool_failure_summary
    ):
        skills.append(
            {
                "skill_id": "execute_build123d_detached_subtractive_builder_repair",
                "when_relevant": "Use when the previous execute_build123d write failed with `Nothing to subtract from` or an equivalent subtract-without-host runtime error.",
                "guidance": [
                    "Treat `Nothing to subtract from` as a detached subtractive builder error: a subtractive primitive or `extrude(..., mode=Mode.SUBTRACT)` was opened in a builder that had no additive host yet.",
                    "Do not create a standalone `with BuildPart() as cutter:` block whose first primitive uses `mode=Mode.SUBTRACT`; detached cutter builders should create positive solids, not subtract from an empty host.",
                    "If the cut belongs to an existing host part, keep that subtractive primitive inside the authoritative host builder with `mode=Mode.SUBTRACT` and explicit `Locations(...)` / target plane placement.",
                    "If the cut truly needs a detached cutter, build the cutter as a positive solid first, close that builder, and only then subtract it with an explicit boolean such as `result = host.part - cutter.part` outside the active host builder.",
                    "For notch, pocket, cavity, or magnet-recess families, do not bounce between `part.part -= cutter.part` repairs until the subtractive host/cutter boundary is made explicit.",
                ],
            }
        )

    if _requirement_mentions_multi_part_assembled_envelope(requirement_lower) and (
        latest_tool == "execute_build123d" or not latest_tool
    ):
        skills.append(
            {
                "skill_id": "multi_part_assembled_pose_bbox_contract",
                "when_relevant": "Use when the requirement asks for multiple physical parts but still declares one overall assembled envelope.",
                "guidance": [
                    "Separate parts means separate solids in one assembled coordinate frame, not an exploded presentation layout.",
                    "If the requirement gives overall dimensions or an outer bounding box, treat that envelope as the closed/mating assembly pose and keep every part inside it unless an exploded view is explicitly requested.",
                    "Do not translate the lid above the base, move one half aside for visibility, or leave the hinge barrel floating outside the shell just to make the parts easier to see.",
                    "Remember that `Box(length, width, height)` is centered by default; when stacking base/lid shells or hinge bodies in one assembled pose, explicitly align or translate each part by half-height so the bottoms, split planes, and tops land where the requested envelope expects.",
                    "When a previous write already has the correct part count but one bbox axis is too large, suspect exploded placement first and rebuild the assembly pose before changing nominal dimensions.",
                    "If you need separate exportable parts, keep them as distinct solids or a Compound in shared assembled coordinates; do not satisfy part separation by physically spreading the solids apart.",
                ],
            }
        )

    if _requirement_mentions_multi_part_assembled_envelope(requirement_lower) and any(
        token in requirement_lower
        for token in ("clamshell", "lid", "base", "top lid", "bottom base", "hinge")
    ):
        living_hinge_requested = _requirement_prefers_living_hinge(requirement_lower)
        detached_hinge_hardware_requested = (
            _requirement_explicitly_requests_detached_hinge_hardware(requirement_lower)
        )
        skills.append(
            {
                "skill_id": "clamshell_split_axis_and_hinge_contract",
                "when_relevant": "Use when a lid/base or clamshell enclosure must stay inside one assembled envelope while still exposing separate physical parts and a hinge-style closure.",
                "guidance": [
                    "For lid/base or top-lid/bottom-base clamshells, both halves normally share the same outer width/depth footprint; do not halve the plan footprint just because there are two parts unless the prompt explicitly asks for left/right or front/back halves.",
                    "Use the requested overall envelope as the closed assembly pose and split the parts only along the closure axis or named mating plane, typically the thickness/height direction for lid/base shells.",
                    "For a centered closed envelope with overall `width x depth x height`, keep the split plane explicit: if the mating plane is `z = split_z`, place the base outer-envelope center at `split_z - base_height/2` and the lid outer-envelope center at `split_z + lid_height/2`; do not place both shells on the same side of that split plane or let both shells straddle it.",
                    "When using centered primitives such as `Box(length, width, height)`, explicitly align or translate the parts so the base occupies the lower interval and the lid occupies the upper interval of the shared envelope instead of both straddling the split plane; keep the lid in the mating pose rather than stacking or exploding it for visibility.",
                    "For a plain two-part lid/base target, the first geometry milestone is exactly two dominant shell solids in one shared assembled envelope, or one `Compound(...)` that contains exactly those two shell solids. Return the assembled default as `Compound([base.part, lid.part])` when lid/base are the only physical parts; do not keep adding hinge, magnet, notch, or pocket detail while the write is still producing four detached solids, one fused shell, or an obviously exploded pose.",
                    (
                        "If the requirement says `living hinge`, treat the hinge as an integrated host-owned thin back-edge strip or flexure bridge between lid and base; do not introduce detached hinge barrels, hinge pins, or extra hinge solids unless the prompt explicitly switches to a pin/mechanical/removable hinge."
                        if living_hinge_requested
                        else (
                            "If the requirement explicitly requests detachable hinge hardware such as a removable pin, separate hinge parts, or an exposed hinge assembly, detached hinge barrels/pins are allowed, but keep the default physical-part target at lid/base only and avoid inventing extra hinge solids beyond the requested hardware."
                            if detached_hinge_hardware_requested
                            else "A plain `pin hinge` or `mechanical hinge` still defaults to a two-part lid/base target: keep the hinge knuckles/barrels host-owned on lid/base and only detach the pin/hardware when the prompt explicitly asks for a removable pin, separate hinge parts, or an exposed hinge assembly."
                        )
                    ),
                    "For a living hinge, the back-edge seam coordinate belongs to the hinge strip itself, not to the whole shell envelope; do not translate the whole lid or base to the back seam coordinate just to make the hinge touch.",
                    "For a centered clamshell with depth on Y, the back-edge seam normally sits at `y = -depth/2` and the front opening boundary at `y = +depth/2`; keep those seam/boundary coordinates literal instead of inferring them from a rotated helper solid.",
                    "Build123d `extrude(amount=h)` grows one-sided from the active sketch plane; it does not automatically create a centered `[-h/2, +h/2]` shell interval around that plane.",
                    "For centered lid/base intervals, sketch on the real start face plane or translate the finished solid afterward; do not assume `Locations((0, 0, center_z))` plus `extrude(amount=h)` creates a centered shell interval by itself.",
                    "Remember that `Cylinder(...)` points along +Z by default. If a hinge barrel or pin must run along the back edge or shell width, rotate the detached cylinder onto that hinge axis before assembling it, and distinguish the hinge seam location from the hinge axis direction instead of assuming the long cylinder runs along the seam coordinate axis.",
                    "For a back-edge pin hinge on a centered clamshell, the seam stays on the back Y boundary while the hinge axis normally runs along X/width, so an unrotated default `Cylinder(...)` is not yet a valid hinge barrel or pin until that closed solid is rotated onto the width axis.",
                    "do not drop an unrotated default `Cylinder(...)` directly onto `(x, hinge_y, split_z)` or `(x, -depth/2, z)` inside lid/base builders and assume it became an X-axis hinge barrel or pin; without a supported rotation/orientation lane that cylinder still runs along Z.",
                    "When that back-edge hinge axis runs along X/width, keep Y fixed at the seam and derive hinge offsets or spans from width for X placement; do not reuse the seam Y coordinate as an X offset or size that X-axis hinge from depth just because the seam lives on Y.",
                    "Remember that `Cylinder(...)` is centered along its own axis by default. If the barrel should stay inside the enclosure envelope, do not center a long hinge cylinder on the seam coordinate unless that span direction is actually intended; do not let the hinge barrel or pin protrude outside the declared bounding box unless the prompt explicitly allows an exposed external hinge.",
                    "`RectangleRounded(width, depth, radius=...)` already uses the outer footprint spans when the requirement gives an overall rounded-rect shell envelope; do not rewrite the requested outer envelope as `width - 2*radius` / `depth - 2*radius` unless the requirement explicitly defines inner straight spans instead of the outer size.",
                    "If the prompt says separate parts and only names lid/base (or top/bottom shells) as the physical parts, treat that as a two-part target by default; fuse hinge barrels, cable posts, and other host-owned hardware into their real lid/base host unless detachable hinge hardware is explicitly requested.",
                    "Only keep hinge pins, hinge barrels, or other hinge hardware as detached shapes when the requirement explicitly calls for separate hinge parts, an exposed hinge assembly, or a removable pin; otherwise prefer `Compound([base.part, lid.part])` as the default assembled result shape list.",
                ],
            }
        )

    if latest_tool == "execute_build123d" and previous_failure_lint_ids.intersection(
        {
            "invalid_build123d_contract.detached_subtractive_builder_without_host",
            "invalid_build123d_contract.active_builder_temporary_primitive_arithmetic",
            "invalid_build123d_contract.active_builder_temporary_primitive_boolean_contract",
            "invalid_build123d_contract.active_builder_temporary_primitive_transform_rebind",
            "invalid_build123d_contract.active_builder_part_mutation",
            "invalid_build123d_api.nested_buildpart_part_transform",
        }
    ):
        skills.append(
            {
                "skill_id": "execute_build123d_active_builder_authority_repair",
                "when_relevant": "Use when lint says temporary solids or late part mutation broke the active BuildPart contract.",
                "guidance": [
                    "Inside an active BuildPart, do not create named solids like `outer = Box(...)`, `hinge = Cylinder(...)`, or `cutter = Box(...)` and then do later `outer - inner`, `hinge + boss`, `part.part -= cutter`, or other temporary-solid arithmetic.",
                    "Every primitive constructor inside the active builder mutates the host immediately; keep the host authoritative with builder-native `mode=Mode.ADD/SUBTRACT/INTERSECT`, or close the builder before detached solid arithmetic.",
                    "Do not open a nested `BuildPart(mode=Mode.SUBTRACT)` inside the host just to carve an inner cavity, notch, slot, or pocket; keep those subtractive primitives in the same authoritative host builder, or close the host builder first and subtract the detached cutter afterward.",
                    "If a staging solid is unavoidable, create it with `mode=Mode.PRIVATE` and only combine it after the host builder closes.",
                    "For enclosure/body/lid/base families, stabilize the outer envelope and inner cavity with one builder-native recipe first; do not bounce between detached `Box(...)` / `Cylinder(...)` temporaries while the builder is still open.",
                    "If the requirement asks for separate physical parts such as lid/base, body/cover, or clamp halves, open one closed `BuildPart` per physical part and only combine those detached results after each builder closes; do not host every physical part inside one shared active `BuildPart` just to get a first solid.",
                ],
            }
        )

    if (
        (latest_tool == "execute_build123d" or not latest_tool)
        and (
            "clamshell" in requirement_lower
            or ("lid" in requirement_lower and "base" in requirement_lower)
            or "lid and base" in requirement_lower
            or "top lid" in requirement_lower
            or "bottom base" in requirement_lower
        )
        and any(
            token in requirement_lower
            for token in (
                "magnet",
                "notch",
                "slot",
                "pocket",
                "cavity",
                "recess",
                "post",
                "hinge",
            )
        )
    ):
        living_hinge_requested = _requirement_prefers_living_hinge(requirement_lower)
        detached_hinge_hardware_requested = (
            _requirement_explicitly_requests_detached_hinge_hardware(requirement_lower)
        )
        skills.append(
            {
                "skill_id": "execute_build123d_clamshell_host_local_cut_contract",
                "when_relevant": "Use on clamshell lid/base first-write or repair turns when shell hosts, late local cuts, and detached hinge solids can easily get mixed together.",
                "guidance": [
                    "For clamshell lid/base shells, keep one authoritative `BuildPart` per shell host in the closed assembled pose; do not keep reopening the same lid/base alias later for late cuts.",
                    "For a centered closed envelope with overall `width x depth x height`, keep the split plane explicit: if the mating plane is `z = split_z`, place the base shell center at `split_z - base_height/2` and the lid shell center at `split_z + lid_height/2`; do not place both shells at positive Z or let both shells overlap the same split interval.",
                    (
                        "If the requirement says `living hinge`, keep the hinge as a host-owned thin hinge strip or flexure on the back edge and preserve the default two-shell target; do not create detached `hinge_barrel` / `hinge_pin` solids or a third/fourth hinge part unless the prompt explicitly requests a pin/mechanical/removable hinge."
                        if living_hinge_requested
                        else (
                            "If the requirement explicitly requests detachable hinge hardware such as a removable pin, separate hinge parts, or an exposed hinge assembly, detached hinge barrels or hinge pins are allowed, but keep the default shell target at lid/base only and avoid inventing extra hinge parts beyond the requested hardware."
                            if detached_hinge_hardware_requested
                            else "A plain `pin hinge` or `mechanical hinge` still defaults to a two-part lid/base target: keep the hinge knuckles/barrels host-owned on lid/base and only detach the pin/hardware when the prompt explicitly asks for a removable pin, separate hinge parts, or an exposed hinge assembly."
                        )
                    ),
                    "For a living hinge, the back-edge seam coordinate belongs to the hinge strip itself, not to the whole shell envelope; do not translate the whole lid or base to the back seam coordinate just to make the hinge touch.",
                    "Only when the prompt explicitly requests detached back-edge hinge barrels or hinge pins should you use that lane at all: separate the hinge seam location from the hinge axis direction, keep the seam at `y = ±depth/2`, do not reinterpret the back-edge hinge seam as a `Plane.YZ` sketch family, and never write `with Rot(...): Cylinder(...)`; build the cylinder positively first, close that builder, then orient the closed solid with `Rot(...) * hinge_barrel.part` or `Pos(...) * Rot(...) * hinge_barrel.part` in the final assembly lane.",
                    "For front/back clamshell-local features such as a thumb notch, front label recess, or mating-face pocket, the host is Y-normal, so start from `Plane.XZ.offset(±depth/2)` or `Plane(face)`; do not sketch that edit on `Plane.XY` or `Plane.YZ` and hope later offsets recover the host, and do not externalize that first pass into a detached subtractive alias such as `notch_cutter`.",
                    "do not drop an unrotated default `Cylinder(...)` directly onto `(x, hinge_y, split_z)` or `(x, -depth/2, z)` inside lid/base builders and assume it became an X-axis hinge barrel or pin; without a supported rotation/orientation lane that cylinder still runs along Z.",
                    "Build123d `extrude(amount=h)` grows one-sided from the active sketch plane; it does not automatically create a centered `[-h/2, +h/2]` shell interval around that plane.",
                    "For centered lid/base intervals, sketch on the real start face plane or translate the finished solid afterward; do not assume `Locations((0, 0, center_z))` plus `extrude(amount=h)` creates a centered shell interval by itself.",
                    "For centered clamshell coordinates, the back seam normally sits at `y = -depth/2` and the front opening/notch boundary at `y = +depth/2`; keep those front/back boundary coordinates literal instead of inferring them from a rotated helper or detached cutter center.",
                    "If a thumb notch or front label recess is externalized as a detached cutter such as `notch_cutter` or `label_recess`, that detached builder must stay positive or `mode=Mode.PRIVATE`; do not write `with BuildPart() as notch_cutter:` followed by `extrude(..., mode=Mode.SUBTRACT)` because a detached cutter builder has no host yet.",
                    "A safe back-edge detached hinge placement pattern is `Pos(0, ±depth/2, split_z) * (Rot(Y=90) * hinge_barrel.part)` after the hinge barrel builder closes; keep the Y seam coordinate explicit instead of rebuilding the hinge on `Plane.YZ`.",
                    "A default `Cylinder(...)` still runs along Z, so a detached pin/barrel on the back edge is only valid after its closed solid is rotated onto the intended hinge axis, usually X/width for a back-edge clamshell pin.",
                    "For detached back-edge hinge hardware whose axis runs along X/width, keep X placements and X spans derived from width while Y stays fixed at the seam; do not plug `hinge_y` into the X position or build an X-axis pin/barrel with a depth-derived span.",
                    "choose one axis-orientation lane for a detached hinge cylinder: either create the cylinder with one supported primitive rotation lane, or build it unrotated and orient the closed solid afterward, but do not stack `Cylinder(..., rotation=...)` and a second `Rot(...) * hinge_barrel.part` just to realize one hinge axis.",
                    "Finish host-owned local cuts such as magnet recesses, thumb notches, slots, and pockets inside that same shell host before that shell builder closes.",
                    "For top/bottom mating-face features, keep the XY-family host plane at the real face datum, for example `Plane.XY.offset(z_face)`, and only place local `(x, y)` coordinates on that plane.",
                    "If a local cut depends on sketch primitives such as `SlotOverall(...)`, `Rectangle(...)`, or `Circle(...)`, open `BuildSketch(target_plane)` first on the intended host plane, then extrude/subtract from that same shell host lane.",
                    "Do not try to rescue a wrong front/back host by combining `BuildSketch(Plane.XY)` or `BuildSketch(Plane.YZ)` with a guessed `Locations((x, y, z))`, `shift_origin(...)`, or extra rotations; keep the correct host plane authoritative from the start.",
                    "When detached hinge hardware is explicitly requested, treat hinge barrels, hinge pins, and other rotated hardware as detached positive solids after the shell hosts close, then assemble them in shared coordinates; do not blur them into the shell-cut lane, and do not write patterns like `hinge_barrel = Rot(...) * hinge_barrel` while the primitive still lives inside an active host builder because that only rebinds the Python variable, not the already-added host geometry.",
                    "Keep the seam location from the hinge axis direction: the back edge fixes the Y coordinate, while the hinge axis is a separate transform decision.",
                    "A safe detached hinge pattern is: create the barrel or pin positively in its own builder without `with Rot(...):`, close that builder, then orient the closed solid with `Rot(...) * hinge_barrel.part` or `Pos(...) * Rot(...) * hinge_barrel.part` in the final assembly lane, but only use this lane when detached hinge hardware is actually requested.",
                    "If a detached boolean is still unavoidable for one local cutter, build that cutter as a positive/private solid only after the shell host closes and subtract it once outside the active builder.",
                    "Do not reopen `with BuildPart() as lid:` or `with BuildPart() as base:` just to start subtractive mini-builders after the shell already exists.",
                ],
            }
        )

    if latest_tool == "execute_build123d" and previous_failure_lint_ids.intersection(
        {
            "invalid_build123d_contract.detached_subtractive_builder_without_host",
            "invalid_build123d_keyword.cylinder_axis",
        }
    ):
        skills.append(
            {
                "skill_id": "execute_build123d_detached_cylindrical_cutter_contract",
                "when_relevant": "Use when enclosure cuts or hinge/magnet-style cylindrical features failed because a detached cutter builder started subtractive or because Cylinder(...) was given an unsupported axis keyword.",
                "guidance": [
                    "When a notch, magnet recess, hinge barrel, drill, or similar cylindrical feature needs detached solid arithmetic, build that cutter or barrel as a normal positive/private solid first; do not start a detached builder with `mode=Mode.SUBTRACT`.",
                    "Do not write patterns such as `with BuildPart() as notch_cutter: Box(..., mode=Mode.SUBTRACT)` or `with BuildPart() as cutter: Cylinder(..., mode=Mode.SUBTRACT)` when that builder has no additive host yet.",
                    "For detached cylinders, do not pass `axis=Axis.X`, `axis=Axis.Y`, or `axis=Axis.Z` into `Cylinder(...)`; create the cylinder with `Cylinder(radius=..., height=...)`, then orient it with `Rot(...)` and place it with `Pos(...)` or `Locations(...)`.",
                    "If the cut belongs to the authoritative host part, keep the subtractive primitive inside that host builder with explicit placement and `mode=Mode.SUBTRACT`; only use detached boolean subtraction after the host builder closes.",
                ],
            }
        )

    if latest_tool == "execute_build123d" and previous_failure_lint_ids.issuperset(
        {
            "invalid_build123d_contract.detached_subtractive_builder_without_host",
            "invalid_build123d_context.transform_context_manager",
        }
    ):
        skills.append(
            {
                "skill_id": "execute_build123d_rotated_detached_cutter_contract",
                "when_relevant": "Use when the previous write mixed detached subtractive mini-builders with `with Rot(...):` placement while trying to realize rotated hinge barrels, pins, magnet recesses, or thumb-notch cutters.",
                "guidance": [
                    "Do not combine two invalid lanes in one repair: a detached `BuildPart` whose first real operation is subtractive, and a `with Rot(...):` block used as though Rot were a builder context manager.",
                    "For host-owned cuts such as magnet recesses, thumb notches, pockets, and shell-local cylindrical cuts, keep the subtractive primitive inside the authoritative host builder with explicit `Locations(...)` placement and `mode=Mode.SUBTRACT`.",
                    "For rotated detached solids such as hinge barrels or hinge pins, build the solid positively first, close that builder, and then orient it with `Rot(...) * solid` or `Pos(...) * Rot(...) * solid` outside the builder.",
                    "Do not write `with BuildPart() as cutter: Cylinder(..., mode=Mode.SUBTRACT)` and then try to rescue its orientation with `with Rot(...):`; choose one valid lane instead of mixing two invalid ones.",
                    "If a rotated cutter truly must be detached before the boolean, make it a positive/private solid first, rotate it outside the builder, and subtract it only after the authoritative host builder closes.",
                ],
            }
        )

    if (
        latest_tool == "execute_build123d"
        and "invalid_build123d_context.transform_context_manager"
        in previous_failure_lint_ids
        and (
            "clamshell" in requirement_lower
            or ("lid" in requirement_lower and "base" in requirement_lower)
            or "lid and base" in requirement_lower
            or "top lid" in requirement_lower
            or "bottom base" in requirement_lower
        )
        and any(
            token in requirement_lower
            for token in ("hinge", "notch", "label", "recess", "magnet", "pocket", "slot")
        )
    ):
        skills.append(
            {
                "skill_id": "execute_build123d_clamshell_transform_lane_contract",
                "when_relevant": "Use when a clamshell repair turn failed because front/back local cuts or detached hinge helpers were expressed with `with Rot(...):` instead of a valid host-native or detached-solid transform lane.",
                "guidance": [
                    "For a front-face thumb notch, front label recess, or similar clamshell-local cut, keep the edit host-native on the real shell host: open `BuildSketch(Plane.XZ.offset(±depth/2))` or `BuildSketch(Plane(face))` and extrude/subtract there instead of building a detached cutter under `with Rot(...):`.",
                    "If a cylindrical notch or recess cutter is clearer than a sketch, place that cutter directly inside the authoritative host with explicit `Locations(...)` and supported orientation data; do not wrap `Cylinder(...)` or `Box(...)` in `with Rot(...):`.",
                    "For detached hinge bars, hinge barrels, or hinge pins, build the solid positively first, close that builder, and only then orient it with `Rot(...) * part` or `Pos(...) * Rot(...) * part` outside the builder.",
                    "For detached back-edge hinge helpers, the back-edge seam coordinate stays on Y while the cylinder axis is chosen separately by transform; do not switch to `Plane.YZ` just because the hinge sits at the back edge.",
                    "A safe detached hinge lane is `Pos(0, ±depth/2, split_z) * (Rot(Y=90) * hinge_barrel.part)` after the hinge builder closes, instead of reopening a new sketch on `Plane.YZ`.",
                    "pick one axis lane for the detached hinge helper: either rely on one supported primitive rotation surface or one post-builder `Rot(...) * part` lane, but do not combine both just to force the same axis direction.",
                    "Do not mix two lanes in one workaround: host-owned front/back cuts belong inside the shell builder, while truly detached hinge helpers belong to a later detached-solid assembly lane.",
                ],
            }
        )

    previous_failure_repair_recipe = _failure_repair_recipe_payload(
        previous_tool_failure_summary
    )
    previous_failure_repair_recipe_id = str(
        previous_failure_repair_recipe.get("recipe_id") or ""
    ).strip()
    explicit_cylindrical_slot_recipe_lints = {
        "invalid_build123d_contract.active_builder_temporary_primitive_transform_rebind",
        "invalid_build123d_keyword.plane_normal_alias",
        "invalid_build123d_keyword.cylinder_axis",
    }
    if latest_tool == "execute_build123d" and (
        previous_failure_repair_recipe_id == "explicit_cylindrical_slot_boolean_safe_recipe"
        or previous_failure_lint_ids.issuperset(explicit_cylindrical_slot_recipe_lints)
    ):
        skills.append(
            {
                "skill_id": "execute_build123d_explicit_cylindrical_slot_recipe_contract",
                "when_relevant": "Use when the previous write already exposed the explicit cylindrical slot repair recipe: host-builder authority broke, Plane(...) used `normal=`, and Cylinder(...) used `axis=` while trying to realize notch or magnet-style cylindrical cuts.",
                "guidance": [
                    "Keep the host builder authoritative for shells, lids, bases, and other enclosure bodies; do not stage a host primitive inside the active builder and then try to relocate it afterward.",
                    "Do not create `lid_outer = Box(...)` and then relocate it with `Pos(...) * lid_outer` or `Rot(...) * lid_outer` while that primitive already belongs to the open host builder.",
                    "For local face sketches such as a thumb notch, use `Plane(origin=..., z_dir=...)`; do not pass `normal=` into `Plane(...)`.",
                    "For hinge barrels, magnet recesses, and other cylindrical cutters, do not pass `axis=` into `Cylinder(...)`; create a plain cylinder first, then orient it with `Rot(...)` and place it with `Pos(...)` or `Locations(...)`.",
                    "If the cylindrical cut can stay host-native, place it once inside the authoritative host with explicit `Locations(...)` and `mode=Mode.SUBTRACT`; otherwise close the host builder first, then build and subtract one detached cutter.",
                ],
            }
        )

    if latest_tool == "execute_build123d" and (
        "invalid_build123d_contract.compound_positional_children_contract"
        in previous_failure_lint_ids
    ):
        skills.append(
            {
                "skill_id": "execute_build123d_compound_children_contract",
                "when_relevant": "Use when the previous write tried to assemble detached lid/base/hinge results with `Compound(...)` positional varargs instead of one iterable child payload.",
                "guidance": [
                    "`Compound(...)` is not a variadic assembly constructor. After the first positional `obj`, later positional arguments bind to metadata such as `label` or `color`, not extra child parts.",
                    "Keep each physical part detached first, then return the assembly as `Compound([base_solid, lid_solid, hinge_solid])` or another explicit iterable/children form.",
                    "Do not write `Compound(base_solid, lid_solid, hinge_solid)` expecting it to collect three shapes.",
                    "If a single fused body is intended instead of a multi-part assembly, perform explicit booleans deliberately; do not rely on malformed `Compound(...)` positional calls as a union shortcut.",
                ],
            }
        )

    explicit_anchor_helper_lint_ids = {
        "invalid_build123d_contract.explicit_anchor_manual_cutter_requires_subtract_mode",
        "invalid_build123d_keyword.countersink_angle_alias",
        "invalid_build123d_keyword.countersink_diameter_alias",
        "invalid_build123d_keyword.countersink_depth_alias",
        "invalid_build123d_api.countersink_hole_helper_name",
    }
    explicit_anchor_helper_recipe_ids = {
        "explicit_anchor_hole_helper_contract_fallback",
    }
    explicit_anchor_helper_guidance_requested = (
        latest_tool == "execute_build123d"
        and (
            bool(previous_failure_lint_ids.intersection(explicit_anchor_helper_lint_ids))
            or (
                latest_repair_packet_family_id == "explicit_anchor_hole"
                and latest_repair_packet_recipe_id in explicit_anchor_helper_recipe_ids
            )
        )
    )
    if explicit_anchor_helper_guidance_requested:
        skills.append(
            {
                "skill_id": "execute_build123d_explicit_anchor_helper_first_repair",
                "when_relevant": "Use when explicit-anchor holes or countersinks are failing because the code drifted into manual cutters or guessed CounterSinkHole keywords.",
                "guidance": [
                    "For explicit planar countersink arrays, prefer one helper-first host-face recipe: use the actual host-face plane, enumerate the full center set once, and keep the exact `CounterSinkHole(radius=..., counter_sink_radius=..., depth=..., counter_sink_angle=...)` contract.",
                    "CounterSinkHole follows the active workplane normal. When repairing a bottom or side mounting face, bind it to the host-face plane with that face's outward normal; an inward-normal offset plane often leaves plain holes without visible cone faces.",
                    "Pass one 2D center set in the host-face local frame. Do not mix `Locations(Plane...)` with invented 3D tuples when the target is a planar hole array on one face.",
                    "If anchor or probe evidence already shows realized hole centers, preserve that count before changing the layout; do not casually add extra holes while the countersink geometry is still missing.",
                    "Do not keep retrying manual `Cylinder(...)` / `Cone(...)` cutters inside Locations unless helper-first placement is already proven insufficient for this family.",
                    "If a manual cutter fallback is truly required, every cutter created inside the active BuildPart must be explicitly subtractive with `mode=Mode.SUBTRACT`, or staged privately and subtracted after the host builder closes.",
                ],
            }
        )

    if insufficient_evidence_guidance:
        skills.append(
            {
                "skill_id": "insufficient_evidence_query_before_repair",
                "when_relevant": "Use when validation explicitly says evidence is insufficient or the blocker taxonomy carries evidence-gap hints.",
                "guidance": [
                    "Treat insufficient_evidence observation_tags as a stop signal for family-specific repair guidance.",
                    "Query more evidence first with query_feature_probes, query_geometry, or query_kernel_state before rewriting the geometry.",
                    "If the validation record already includes decision_hints such as inspect_more_evidence, follow that evidence-gathering lane before another repair attempt.",
                ],
            }
        )

    if code_first_family:
        skills.append(
            {
                "skill_id": "code_first_global_family_bias",
                "when_relevant": "Use when the requirement is global, family-driven, or likely to need several structured writes before a stable solid exists.",
                "guidance": [
                    "Prefer execute_build123d as the first write for whole-part or subtree construction.",
                    "Builder-first default: BuildPart for host solids, BuildSketch for section profiles, and BuildLine for rails before adding local finishing.",
                    "Use apply_cad_action only when a stable local face/edge/sketch anchor already exists and the local edit is clearly cheaper than a rebuild.",
                    "After a successful code write, prefer query_feature_probes or query_geometry before broad repeated topology/validation loops.",
                    "If standard read tools still leave one geometric-family question unresolved, execute_build123d_probe is the next diagnostic tool instead of another blind rewrite.",
                ],
            }
        )

    if "feature_target_face_additive_merge" in blockers:
        skills.append(
            {
                "skill_id": "whole_part_additive_features_must_merge_into_single_body",
                "when_relevant": "Use when a whole-part build added bosses/studs/pads on a target face but validation reports they did not stay merged into one solid.",
                "guidance": [
                    "Treat additive face features as part of the same body, not as separate result solids.",
                    "Prefer building repeated bosses/studs from the target face workplane of the base solid, or fuse them back into the base and verify the final solid count is 1 before finishing.",
                    "After a code-first rebuild with additive face features, check that the resulting snapshot has one merged solid rather than multiple disconnected solids.",
                ],
            }
        )

    positive_extrude_plane = _detect_positive_extrude_plane(requirement_lower)
    if (
        positive_extrude_plane is not None
        and not _requirement_requests_centered_plane_pose(requirement_lower)
    ):
        plane_name, axis_name = positive_extrude_plane
        skills.append(
            {
                "skill_id": "positive_extrude_from_named_plane_is_not_centered",
                "when_relevant": "Use when a requirement sketches on a named datum plane and then extrudes by a positive distance.",
                "guidance": [
                    f"If the requirement says sketch on the {plane_name} plane and extrude by a positive distance, the default solid spans positive {axis_name} from that plane rather than being centered about {axis_name}=0.",
                    "In Build123d, keep the sketch on the named Plane and extrude it in the positive normal direction rather than simulating the pose with a centered primitive by default.",
                    "If the requirement draws multiple closed section elements on that named plane and then says to extrude the section/profile, keep that section literal instead of replacing it with a default centered primitive.",
                    "For outer-circle plus inner-square/rectangle families, sketch or extrude from the named plane and subtract the inner profile through the same positive span instead of using a centered `Cylinder(...)` plus later compensation.",
                    "If you use a box primitive for convenience, place it explicitly with Plane/Pos so the requested lower bound stays on the named datum plane instead of drifting around the global origin.",
                    "Only use center-aligned or both-sides/midplane semantics when the requirement explicitly says centered, symmetric, or about the plane.",
                ],
            }
        )

    bottom_aligned_box_pose = _detect_named_plane_bottom_aligned_box_pose(
        requirement_lower
    )
    if (
        bottom_aligned_box_pose is not None
        and not _requirement_requests_centered_plane_pose(requirement_lower)
    ):
        plane_name, axis_name = bottom_aligned_box_pose
        skills.append(
            {
                "skill_id": "named_plane_box_bottom_pose_alignment",
                "when_relevant": "Use when a requirement selects a named datum plane, describes a box/base primitive, and only pins the normal-direction lower bound such as bottom on Z=0.",
                "guidance": [
                    f"If the requirement says select the {plane_name} plane and create a box/base with the bottom on {axis_name}=0, only the {axis_name} lower bound is pinned; do not also shift the in-plane footprint into the positive quadrant unless the prompt explicitly gives a corner/origin anchor.",
                    f"For this pose, prefer an explicit centered-in-plane placement such as `Pos(0, 0, height / 2) * Box(...)` or `with Locations(Pos(...)):` so the box keeps its X/Y footprint centered while the bottom stays on {axis_name}=0, instead of drifting into a centered=False-style positive-quadrant pose.",
                    "After the host solid is placed correctly, do later top-face or side-face local edits from the selected face workplane rather than compensating with ad-hoc whole-part translations.",
                ],
            }
        )

    positive_extrude_mismatch = _detect_positive_extrude_bbox_mismatch(
        requirement_lower=requirement_lower,
        latest_write_health=latest_write_health,
    )
    if positive_extrude_mismatch is not None:
        plane_name, axis_name, expected_range, current_range = positive_extrude_mismatch
        skills.append(
            {
                "skill_id": "positive_extrude_bbox_alignment_repair",
                "when_relevant": "Use when the current solid is centered about the datum plane but the requirement implies a positive extrusion from that plane.",
                "guidance": [
                    f"The current solid spans {axis_name}={current_range[0]:.3f}..{current_range[1]:.3f}, but a sketch on the {plane_name} plane extruded by the requested distance should span approximately {axis_name}={expected_range[0]:.3f}..{expected_range[1]:.3f}.",
                    "Center-rectangle wording applies to the sketch in the plane, not to centering the extrusion about the plane normal.",
                    "Repair by sketching on the named Plane and extruding in the positive normal direction; do not simulate the pose with a vague centered primitive.",
                    "If you keep a primitive-based whole-part rebuild, place the host explicitly with Plane/Pos before downstream cuts.",
                ],
            }
        )

    if _requirement_mentions_shelled_host_with_named_face_feature(
        requirement_lower,
        semantics=semantics,
    ):
        skills.append(
            {
                "skill_id": "shelled_host_preserves_named_feature_face",
                "when_relevant": "Use when a shelled body also needs a named-face recess, hole set, or other local feature on that same host.",
                "guidance": [
                    "If a shelled body will later receive a named-face local feature, do not open or remove that same target face while creating the shell.",
                    "When the opening face is unspecified, preserve the named feature face and open the opposite face by default.",
                    "For explicit inner-solid subtraction shells, keep the inner cutout extent and offset chosen so the target face still has material for the later edit.",
                    "For vague reference layouts on a shelled host, keep the recesses, holes, or reference pattern on surviving host material instead of placing them in the hollow void.",
                ],
            }
        )

    if _requirement_mentions_enclosure_host(requirement_lower) and (
        latest_tool == "execute_build123d" or not latest_tool
    ):
        skills.append(
            {
                "skill_id": "nested_hollow_section_builder_native_cavity",
                "when_relevant": "Use when a hollow enclosure/body/lid/base is being written or repaired with execute_build123d, including the very first whole-part write.",
                "guidance": [
                    "For hollow enclosure-style hosts, do not write `outer_box = Box(...)`, `inner_box = Box(...)`, then mutate `outer_box -= inner_box` inside the active `BuildPart`.",
                    "A safer first pass is: build the outer envelope in one active `BuildPart`, then place the inner cavity with `mode=Mode.SUBTRACT` in that same builder using `Locations(...)` or an explicit local frame so the opening direction stays literal.",
                    "Do not open a nested `BuildPart(mode=Mode.SUBTRACT)` inside that host just to cut the inner cavity or a repeated enclosure cutout; either keep the subtractive primitives in the same authoritative builder, or close the host and subtract the detached cavity afterward.",
                    "A good shell skeleton is `with BuildPart() as base: Box(...); with Locations((0, 0, wall)): Box(..., mode=Mode.SUBTRACT)` and only after the builder closes, if needed, `result = base.part`.",
                    "Do not replace that builder-native shell recipe with `base_block = Box(...)`, `inner = Box(...)`, `base_block - inner`, or `base.part = ...` while the host builder is still open.",
                    "If the cavity needs detached solid arithmetic, close the host builder first and only then compute `result = host.part - inner_cavity` outside the active builder.",
                    "If the enclosure is explicitly multi-part, model the lid and base in separate closed builders first, then combine them as detached solids or a `Compound(...)`; do not merge both physical parts into one shared host builder.",
                    "For lid/base/hinge assemblies with declared overall dimensions, keep the lid in its mating/closed pose and keep hinge geometry inside the same assembled outer envelope unless the prompt explicitly requests an exploded layout.",
                    "When topology evidence is needed, ask `query_topology` for enclosure-oriented candidate sets such as `shell_exterior_faces`, `shell_interior_faces`, `mating_faces`, and `split_plane_faces` instead of falling back to only `top_faces` / `outer_faces`.",
                    "Do not default to `fillet(host.edges().filter_by(GeomType.LINE), ...)` across the entire hollow enclosure. That broad edge set often pulls in hinge, notch, interior, or seam edges and fails; narrow to the intended exterior/opening edge family first, ideally with topology-guided host selection.",
                    "Treat `mating_faces` and `split_plane_faces` as the first semantic host candidates for lid/base seam, closure landing, and half-shell split decisions.",
                    "Stabilize the shell/body/lid/base envelope first; add magnets, thumb notches, hinge barrels, posts, and side pockets only after the hollow host is already valid.",
                ],
            }
        )

    if _requirement_mentions_enclosure_host(requirement_lower) and any(
        token in requirement_lower
        for token in (
            "magnet",
            "notch",
            "cavity",
            "pocket",
            "slot",
            "hinge",
            "post",
        )
    ):
        skills.append(
            {
                "skill_id": "enclosure_local_feature_placement_contract",
                "when_relevant": "Use on enclosure/clamshell first-write or repair turns that also place multiple local features such as magnets, cavities, pockets, notches, slots, hinges, or posts.",
                "guidance": [
                    "Stabilize the lid/base shell first, then place each local enclosure feature on its real physical host part with builder-native `Locations(...)` or a detached-solid transform that stays explicit end-to-end.",
                    "If the first whole-part write for a two-part enclosure produces extra skinny solids or tiny fragments, treat that as host/local-feature organization failure rather than success on part count; do not accept a four-solid or fused one-solid stop state just because some requested details appear visually plausible.",
                    "Do not immediately fillet every broad top/bottom shell edge set on a fresh shell/body first pass. That includes `edges().filter_by(Axis.Z)` and whole top/bottom `filter_by_position(Axis.Z, ...)` selections, because they often capture interior cavity rims or seam edges along with the intended exterior perimeter.",
                    "If the enclosure footprint itself is supposed to look rounded or pillbox-like, prefer building that rounded footprint directly with `RectangleRounded(...)` in `BuildSketch(...)` instead of relying on a broad first-pass shell-edge fillet to create the silhouette.",
                    "If a fresh-shell fillet fails with `command not done` or `Failed creating a fillet`, treat that as a geometry-stability signal: postpone the fillet until the host is valid, reduce the radius, or probe the largest safe value with `max_fillet(...)` before retrying.",
                    "Do not open a detached `BuildPart` whose first real operation is subtractive just to realize a magnet recess, thumb notch, plug pocket, slot, or similar enclosure-local cut; if the cut belongs to the shell host, keep it inside that authoritative host builder.",
                    "For repeated magnet recesses, thumb notches, plug pockets, posts, or similar enclosure-local features, keep the host builder authoritative and realize them with `Locations(...)` plus `mode=Mode.SUBTRACT/ADD` instead of mutating `part.part` or reopening ad-hoc nested builders.",
                    "Do not use `Loc(...)`, and do not open `with Rot(...):` or `with Pos(...):`; use `Location(...)` for a location object, `Locations(...)` for scoped builder placement, or apply `Pos(...) * Rot(...) * solid` after the host builder closes.",
                    "If a detached cavity proxy or organic cutter needs anisotropic scaling, use lowercase `scale(shape, by=...)` on the detached shape before the final boolean; do not invent `Scale.by(...)` or other capitalized scaling helpers.",
                    "`SlotOverall(...)`, `Rectangle(...)`, and `Circle(...)` belong inside `BuildSketch(...)` on the intended host plane; for thumb notches or slot-like cuts, use `with BuildSketch(target_plane): SlotOverall(...)` and then `extrude(..., mode=Mode.SUBTRACT)` from that sketch instead of `SlotOverall(..., mode=Mode.SUBTRACT)` inside `BuildPart`.",
                    "Map named enclosure faces to plane families by host normal before any local sketch or recess: `top/bottom -> Plane.XY`, `front/back -> Plane.XZ`, and `left/right -> Plane.YZ`. For a front label window, front notch, or front recess, `Plane.YZ` is a side-face family, not the front/back face family.",
                    "For lid/base assemblies, keep local features attached to their real physical host part and do not cut the lid, base, hinge barrel, and closure features through one shared active builder when the requirement expects separate parts.",
                    "If evidence shows one dominant enclosure solid plus a tiny extra solid, treat that as a detached feature fragment rather than a valid second physical part: rebuild so magnets, thumb notches, plug pockets, hinge cuts, and similar local features stay builder-native on the host, or subtract exactly once after the host builder closes.",
                ],
            }
        )

    if "feature_named_plane_positive_extrude_span" in blockers:
        skills.append(
            {
                "skill_id": "named_plane_positive_extrude_span_blocker_repair",
                "when_relevant": "Use when validation says the solid drifted into a centered pose instead of preserving a positive extrude span from the named plane.",
                "guidance": [
                    "Treat this as a pose bug, not as a generic local-feature bug: the base solid must preserve the datum-plane lower bound before later holes, pockets, or fillets are applied.",
                    "Build123d sketch-plus-extrude already gives the plane-anchored positive span when the sketch stays on the named Plane.",
                    "If a whole-part primitive is clearer, place it explicitly so the named-plane lower bound is preserved before later operations.",
                ],
            }
        )

    axisymmetric_axis_mismatch = _detect_named_axis_axisymmetric_pose_mismatch(
        requirement_lower=requirement_lower,
        latest_write_health=latest_write_health,
    )
    if axisymmetric_axis_mismatch is not None:
        axis_name, perpendicular_axes, bbox_offsets, current_center = axisymmetric_axis_mismatch
        skills.append(
            {
                "skill_id": "named_axis_axisymmetric_pose_alignment_repair",
                "when_relevant": "Use when a revolve / shaft / axisymmetric whole-part rebuild drifted away from the declared global rotation axis.",
                "guidance": [
                    f"The requirement declares the global {axis_name}-axis as the rotation axis, so the final solid should stay centered on that axis rather than drifting along {perpendicular_axes[0]} / {perpendicular_axes[1]}.",
                    f"The current radial bbox center offsets are {bbox_offsets[0]:.3f} and {bbox_offsets[1]:.3f}, and the current center is {current_center}; that indicates the whole part is translated off the named axis.",
                    f"When rebuilding with cylinders/cones for a {axis_name}-axis part, keep every primitive centered on {perpendicular_axes[0]}={0.0} and {perpendicular_axes[1]}={0.0}; only translate along {axis_name} unless the requirement explicitly asks for an offset axis.",
                    "Before finishing, verify that the final bbox straddles the declared rotation axis and that any cylindrical/conical faces use that same axis.",
                ],
            }
        )

    if (
        ("blind hole" in requirement_lower or "hole" in requirement_lower)
        and _requirement_has_explicit_xy_coordinate_pair(requirement_text)
    ):
        skills.append(
            {
                "skill_id": "explicit_face_local_anchor_coordinates",
                "when_relevant": "Use when the requirement gives explicit local coordinates for a face feature.",
                "guidance": [
                    "Treat named local coordinates like (30, 0) as explicit feature anchors, not as optional hints.",
                    "On an XY-aligned top face, local sketch X/Y normally align with global X/Y; place the feature explicitly with Locations((x, y)) or an equivalent Plane/Pos mapping before hole/cut calls.",
                    "When the prompt says to draw points with coordinates on a rectangular host face or plate surface, keep that sketch frame literal; those coordinates may be corner-based within the face sketch rather than already centered around the body origin.",
                    "Do not rely on the default workplane origin for explicit hole centers or local anchor features.",
                ],
            }
        )

    if _requirement_prefers_named_face_local_feature_sequence(requirement_lower):
        guidance = [
            "Even for simple base-solid plus named-face local-feature sequences, default the first write to execute_build123d so the host solid and local feature land in one fresh geometry revision.",
            "For symmetric base spans, keep extrusion-local semantics literal: `symmetrically by N` means a final span of `2N`, so use `extrude(N, both=True)` or an equivalent global primitive with axis span `2N`, not `extrude(2N, both=True)`.",
            "Use apply_cad_action only after a successful code build has established a stable host solid and the remaining work is a bounded local finish.",
            "For the final local fillet/chamfer step, prefer query_topology plus explicit edge_refs once the target solid exists.",
            "If the final fillet/chamfer remains inside whole-part execute_build123d code, use stable selector chains grounded on face orientation and axis direction; avoid lambda-based edge predicates or ad-hoc selector logic.",
            'For directional edge classes such as bottom edges parallel to Y, prefer supported chained selectors like `.edges("<Z").edges("|Y")` over unsupported boolean-expression selectors.',
            'Do not use ad-hoc boolean-expression selectors such as `"<Z and (|X > 29.9)"` inside whole-part code-path fillet/chamfer targeting.',
            "Treat repeated structured bootstrap turns as a cost signal; do not reopen them when a clean code rebuild is cheaper.",
        ]
        if previous_failure_kind in {
            "execute_build123d_timeout",
            "execute_build123d_chain_context_failure",
            "execute_build123d_selector_failure",
        }:
            guidance.append(
                "A recent execute_build123d failure already indicates that another blind whole-part retry is high-risk here; bias toward a materially simpler staged rebuild or a local finish only after the host solid is stable."
            )
        skills.append(
            {
                "skill_id": "named_face_local_feature_sequence",
                "when_relevant": "Use when the requirement reads like base solid first, then a named-face local feature, then a local edge finish.",
                "guidance": guidance,
            }
        )

    if (
        (latest_tool == "execute_build123d" or not latest_tool)
        and _requirement_suggests_local_finish_probe_family(requirement_lower)
    ):
        skills.append(
            {
                "skill_id": "code_first_local_finish_tail_contract",
                "when_relevant": "Use on code-first whole-part writes when the prompt already says a topology-aware local finishing pass should remain useful afterward.",
                "guidance": [
                    "Treat the first execute_build123d write as a host-stabilizing pass: build the bracket/body, top pocket, front recess, and the directly expressible hole features first, then leave the most topology-sensitive finishing tail for later if needed.",
                    "For countersinks on a named mounting/bottom/side/top host face, prefer one helper-first `CounterSinkHole(...)` pass on that actual host-face plane; keep the exact helper contract `CounterSinkHole(radius=..., counter_sink_radius=..., depth=..., counter_sink_angle=...)` and do not approximate countersinks with manual `Cylinder(...)` cutters on the first whole-part attempt.",
                    "For bottom or side mounting faces, the host-face plane must carry the real face normal, not just the right offset distance. `Plane.XY.offset(-height/2)` still keeps a +Z normal, so it often leaves plain cylindrical holes instead of visible countersink cones on the bottom face.",
                    "Build123d does not provide a `Workplanes(...)` helper; if you need a front/back/side host sketch, use the actual target plane directly with `BuildSketch(plane)` or place the feature with `Locations(...)` on that face plane.",
                    "Map named faces to plane families before opening the local sketch: `top/bottom -> Plane.XY`, `front/back -> Plane.XZ`, and `left/right -> Plane.YZ`. Then translate only along that plane family's normal axis to the real host-face datum.",
                    "For centered hosts, do not call `shift_origin((0, 0, 0))` on a named face plane or re-rotate an already-correct host plane just to hunt for the origin; keep the correct plane family authoritative from the start and place only the in-plane coordinates locally.",
                    "If the remaining fillet is only a local top-opening or rim finish and the edge set would still require guessed selectors, bounding-box heuristics, or broad `filter_by_position(...)` bands, postpone it to a later topology-guided local finish instead of forcing it into the first whole-part write.",
                    "Do not spend the first code path on broad whole-part fillet selectors such as `edges().filter_by(Axis.Z)` or broad top/bottom `filter_by_position(Axis.Z, ...)` bands when the prompt already frames those details as a later local-finishing tail.",
                    "Once a stable host exists, use `query_topology` plus `apply_cad_action` to finish the remaining exact face/edge-local detail instead of reopening another broad whole-part fillet or guessed host-face sketch.",
                ],
            }
        )

    local_finish_exact_ref_contract_requested = bool(
        _requirement_prefers_named_face_local_feature_sequence(requirement_lower)
        or _requirement_suggests_local_finish_probe_family(requirement_lower)
        or "feature_target_face_subtractive_merge" in blockers
        or "named_face_local_edit" in taxonomy_families
        or "named_face_local_edit" in domain_kernel_active_families
        or latest_repair_packet_family_id == "named_face_local_edit"
    )
    if local_finish_exact_ref_contract_requested:
        skills.append(
            {
                "skill_id": "local_finish_exact_face_ref_contract",
                "when_relevant": "Use when the remaining work is a topology-aware local face edit or a local-finish turn after query_topology.",
                "guidance": [
                    "Once query_topology has already returned actionable host-face candidates, the next local write must consume exact refs from that read surface instead of reopening a broad plane-based sketch.",
                    "For local face edits, use `face_ref='face:...'` from the latest query_topology candidate sets or matched_ref_ids. Do not fall back to `face='top'`, `face='bottom'`, or `plane='XY'` aliases once exact refs exist.",
                    "In local_finish mode, do not spend `apply_cad_action` on `get_history`, rollback, or other session-control escapes; spend it on the next topology-anchored local edit.",
                    "If the next local edit is already directly expressible on that host face, prefer a direct `apply_cad_action` hole/countersink step on that exact `face_ref` before opening `create_sketch(face_ref=...)`.",
                    "If the local step needs a host sketch, use `create_sketch(face_ref=...)` on the chosen host face, then add the circle/rectangle/polygon on that sketch. Do not start with a detached `create_sketch(plane=...)` inside a bounded local-finish turn.",
                    "After a successful `create_sketch(face_ref=...)`, spend the next local-finish turn adding the first profile or materializing cut with `apply_cad_action`; do not burn the next turn on `query_sketch` when the sketch was just opened and still has no geometry.",
                    "Keep bounded local finishing one write at a time: if the sketch edit needs create_sketch, profile creation, and cut/extrude, emit only the next apply_cad_action for the current turn and continue the sequence on later turns.",
                    "Treat planar-host requirements literally: when query_topology exposes planar host families such as `mating_faces`, `inner_planar_host`, or other planar face candidates, choose a planar `face_ref` first before opening the sketch.",
                    "If the latest topology evidence does not expose a suitable planar face, refresh query_topology or broaden the repair lane later; do not guess a broad plane alias while the turn is still constrained to local_finish.",
                    "In a local-finish turn, broad plane sketches and broad face aliases are contract failures, not acceptable first attempts.",
                ],
            }
        )
    if local_finish_exact_ref_contract_requested and local_center_preservation_summary:
        preserved_centers = local_center_preservation_summary.get("realized_centers") or []
        skills.append(
            {
                "skill_id": "local_finish_preserve_existing_local_centers",
                "when_relevant": "Use when a local-finish turn already has a stable host face and prior evidence has locked a valid local center layout.",
                "guidance": [
                    (
                        "Current preserved local centers from semantic evidence: "
                        f"{json.dumps(preserved_centers[:6], ensure_ascii=False)} "
                        f"(expected_count={local_center_preservation_summary.get('expected_center_count')})."
                    ),
                    "When the remaining work is local feature geometry on that same host face, reuse these exact local centers instead of inventing a new layout.",
                    "Do not add extra positions or re-spread the array while only repairing countersink, head geometry, or another host-face-local finishing detail.",
                    "If the current tool contract cannot safely express that reuse, prefer query_kernel_state or a bounded execute_build123d repair over guessed world-space coordinates.",
                ],
            }
        )
    if _previous_failure_requires_latest_topology_face_ref(previous_tool_failure_summary):
        skills.append(
            {
                "skill_id": "local_finish_retry_bind_latest_face_ref",
                "when_relevant": "Use when the previous local-finish attempt failed because create_sketch ignored the latest query_topology face_ref.",
                "guidance": [
                    "The previous local-finish attempt already failed on the exact-ref contract, so do not spend another retry on `plane='XY'`, `plane='XZ'`, `origin=[0,0,0]`, or other detached sketch aliases.",
                    "On the very next retry, copy one exact `face_ref='face:...'` from the latest query_topology matched_ref_ids or candidate_sets and keep the local edit bound to that same topology revision.",
                    "If query_topology already exposed host-role candidates such as `mating_face`, `inner_planar_host`, or `closure_landing`, choose from that planar host set first instead of reopening a broad workplane sketch.",
                    "The minimal recovery path is `create_sketch(face_ref=...)` on the chosen host face, then add the closed profile needed for the next local cut; do not retry a detached sketch first.",
                    "Only if no valid exact face_ref remains from the latest read should you spend one more turn on `query_topology`; otherwise the retry must stay on the exact-ref lane.",
                ],
            }
        )
    if _previous_failure_used_candidate_set_label_as_reference(previous_tool_failure_summary):
        skills.append(
            {
                "skill_id": "topology_candidate_set_label_is_not_exact_ref",
                "when_relevant": "Use when the previous local edit failed because a candidate-set label such as `mating_faces` or `opening_rim_edges` was passed as face_ref/edge_ref.",
                "guidance": [
                    "Candidate-set labels such as `mating_faces`, `outer_faces`, `top_faces`, or `opening_rim_edges` are summaries of multiple refs, not valid `face_ref` / `edge_ref` values by themselves.",
                    "On the retry, keep the chosen candidate set only as a selection source, then copy one concrete `face:...` or `edge:...` ref from that candidate set's `ref_ids` into `face_ref` / `edge_refs`.",
                    "Do not pass a host-role label or candidate-set id directly into `face_ref` or `edge_refs`; use the exact topology ref string from the latest query_topology result.",
                    "If the candidate set contains multiple plausible refs, pick one exact ref that matches the intended host role and stay on that same topology revision instead of falling back to a broad alias.",
                ],
            }
        )

    if _requirement_prefers_nested_regular_polygon_frame(
        requirement_lower=requirement_lower,
        blockers=blockers,
    ):
        skills.append(
            {
                "skill_id": "nested_regular_polygon_frame_code_first",
                "when_relevant": "Use when the requirement is a concentric regular-polygon or equilateral-triangle frame section that should extrude as one hollow profile.",
                "guidance": [
                    "For concentric regular-polygon frame sections, strongly prefer one same-workplane nested-profile extrude: draw the outer and inner loops on the same workplane or sketch and extrude the frame region directly in one whole-part write.",
                    "Keep the outer and inner regular polygons centroid-aligned and orientation-aligned so the frame region is defined by one nested profile, not by later 3D subtraction.",
                    "Do not start with `.cut()`, `.cutThruAll()`, or another 3D boolean misuse on the first pass; those APIs are the wrong tool before a stable solid exists and often create chain-context failures here.",
                    "If the first whole-part build fails, repair the nested 2D profile construction itself rather than mixing a sketch chain with solid booleans in the next retry.",
                ],
            }
        )

    if _requirement_mentions_regular_polygon_side_length(requirement_lower):
        skills.append(
            {
                "skill_id": "regular_polygon_side_length_build123d_semantics",
                "when_relevant": "Use when a requirement names a regular polygon or equilateral triangle by side length, especially in a whole-part code rebuild.",
                "guidance": [
                    "Build123d regular-polygon sizing should stay explicit: use the true side-length-aware helper/parameter instead of silently reusing a circumradius-like value.",
                    "If the requirement gives side length s for an n-sided regular polygon, convert it deliberately before sketching; do not treat the same numeric value as a radius by default.",
                    "For concentric regular-polygon frame sections, compute both outer and inner polygon sizes from the stated side lengths and keep their centroids coincident; do not halve the scale by passing the wrong sizing mode into the sketch primitive.",
                ],
            }
        )

    if (
        ("shaft" in requirement_lower or "stud" in requirement_lower or "axial direction" in requirement_lower)
        and "radius" in requirement_lower
        and "length" in requirement_lower
    ):
        skills.append(
            {
                "skill_id": "axisymmetric_segmented_primitives_preferred_over_revolve",
                "when_relevant": "Use when an axisymmetric part is described as axial segments with explicit radii and lengths.",
                "guidance": [
                    "For stepped shafts and studs described as consecutive radius/length segments, prefer coaxial cylinders or cones merged along the main axis instead of a handwritten revolve profile on the first attempt.",
                    "Even if the requirement mentions revolve, keep the first whole-part code build on the lower-risk coaxial primitive path unless a primitive-based build has already proven insufficient.",
                    "Do not use cylinder(..., centered=False) for a named-axis-centered solid, because that shifts the primitive off the declared rotation axis before any later translation.",
                    "Reserve revolve for genuinely non-piecewise profiles where the 2D contour is easier to express than the axial primitives.",
                    "Keep the radial center on the declared axis and translate only along the main axis unless the requirement explicitly asks for an offset axis.",
                    "After a primitive-based axisymmetric rebuild, verify that the final bbox stays centered on the declared axis and spans the full requested axial range before another rewrite.",
                ],
            }
        )

    if _requirement_mentions_half_shell_with_split_surface(requirement_lower):
        skills.append(
            {
                "skill_id": "half_shell_profile_from_semicircle_section",
                "when_relevant": "Use when the requirement is a split bearing housing or other half-cylindrical shell with a flat split surface.",
                "guidance": [
                "Build the base shell from one closed semicircular or semi-annular 2D section on the named profile plane, then extrude it along the housing length.",
                "Do not start from a full cylinder and split it later; that frequently preserves the wrong full-diameter envelope.",
                "If the radii are already explicit, prefer the lower-risk same-builder cylinder-subtract-then-intersect recipe on the first pass: create the outer cylinder, subtract the inner cylinder with `mode=Mode.SUBTRACT`, then intersect or trim to the required half-plane before downstream pad/lug edits.",
                "Do not guess `Circle(..., arc_size=180)` for the semicircular section. In Build123d, `Circle(...)` is always full-circle geometry.",
                "`Semicircle(...)` is not a Build123d helper; if you need a true half-profile, use `CenterArc(...)` or `RadiusArc(...)` inside `BuildLine`, close the split edge, and convert it with `make_face()`.",
                "Treat the split surface as the flat closing edge of the semicircle profile, and keep the shell, pad, and lugs in the same half-plane as that semicircular material instead of extending them past the split line.",
                "If a later repair needs topology evidence, prefer `split_plane_faces` and `mating_faces` from `query_topology` over generic top/bottom face guesses.",
                "For split-shell housings, the pad/lugs should widen the orthogonal axis along the split surface, not increase the split-axis depth beyond the outer radius.",
                "If the semicircle is drawn in the positive half-plane of the sketch, keep the pad/lugs in that same positive half-plane instead of mirroring them into the opposite half-plane.",
                "For explicit-radius half-shells with pads/lugs and downstream bore/hole edits, keep the first-pass whole-part order explicit: outer cylinder -> subtract inner cylinder -> intersect/trim to the half-plane -> add pad/lugs -> cut the bore -> drill the lug holes.",
                "Treat the bore/clearance cut as a subtractive operation on the combined shell/pad host after those bodies are merged.",
                "For split-shell housings, merge the pad/lugs, then run the bore cut on that combined host so the lugs remain outside the bore instead of being recreated afterward.",
                "Do not write `outer_cyl = Cylinder(...)`, `inner_cyl = Cylinder(...)`, or `half_space = Box(...)` inside the active half-shell `BuildPart` and then combine those temporary solids with `-` / `&` / `+`; that exact temporary-solid pattern is what the runtime preflight rejects.",
                "A safe first-pass half-shell skeleton is `Cylinder(outer_radius, length)` -> `Cylinder(inner_radius, length, mode=Mode.SUBTRACT)` -> `Box(..., mode=Mode.INTERSECT)` -> merge pad/lugs in the same builder -> subtract the bore -> place Y-axis lug-hole cutters with `mode=Mode.SUBTRACT)`.",
                    "Run that merged-host bore cut so it will leave side lugs outside the bore instead of recreating a full-width pad or cutting the lugs away.",
                    "When the bore and lug holes are subtractive, keep those cutters in the same active `BuildPart` with supported subtractive modes instead of nesting cutter parts or calling bare `subtract(...)` helpers.",
                    "For Y-direction lug holes at explicit X/Z anchors, a safe whole-part pattern is `with Locations((x, 0, z)): Cylinder(radius, extent, rotation=(90, 0, 0), mode=Mode.SUBTRACT)` after the host solid already exists.",
                    "On the first whole-part write, expect one radial bbox span to stay near the outer radius while the orthogonal axis shell-plus-lug span stays near or above the outer diameter.",
                ],
            }
        )

    if _requirement_mentions_flange_boss_pattern_holes(requirement_lower):
        skills.append(
            {
                "skill_id": "flange_boss_pattern_hole_host_thickness",
                "when_relevant": "Use when a flange hosts a bottom boss, a central through-hole, and a separate patterned hole set that should only cut the flange.",
                "guidance": [
                    "Treat the central through-hole and the patterned bolt-circle holes as different depth rules on the same part family.",
                    "If the requirement says the pattern holes cut through the flange, the patterned bolt-circle holes belong to the flange host thickness only, not the full flange-plus-boss stack.",
                    "Open the pattern on the flange host face or flange annular face, keep the hole centers on that flange host, and do not retarget the pattern to the boss face.",
                    "Use a bounded subtractive depth equal to the flange thickness for the patterned holes; do not use cutThruAll() when that would continue through the boss.",
                    "Reserve the full-stack cut for the explicitly named central through-hole when the requirement says it passes through the entire solid.",
                ],
            }
        )

    if _requirement_mentions_explicit_path_sweep(requirement_lower) or (
        "path_sweep" in taxonomy_families
    ) or blockers.intersection(
        {
            "feature_path_sweep_rail",
            "feature_path_sweep_profile",
            "feature_path_sweep_frame",
            "feature_path_sweep_result",
        }
    ):
        skills.append(
            {
                "skill_id": "path_sweep_wire_profile_frame_repair",
                "when_relevant": "Use when the requirement explicitly defines a path sweep with a separate endpoint-attached profile sketch.",
                "guidance": [
                    "Treat the sweep rail and the section profile as separate artifacts: build one open connected path wire first, then build one closed profile face for the sweep section.",
                    "Do not collapse the rail and the profile into one sketch window, and do not continue to sweep if the rail is disconnected or the profile is still open.",
                    "If an artifactless execute_build123d failure already had a successful execute_build123d_probe that exposed concrete rail/profile/frame geometry, prefer the next execute_build123d repair immediately; only insert query_kernel_state or query_feature_probes when the probe still leaves the endpoint frame or family binding ambiguous.",
                    "Preserve the path endpoint frame once the profile sketch attaches there; do not overwrite that path endpoint frame with a generic front/top/side plane guess.",
                    "For path-attached profiles, use BuildLine for the rail, BuildSketch on the explicit endpoint Plane for the section, and keep the frame_mode=normal_to_path_tangent or equivalent Frenet endpoint frame before placing the section loops.",
                    "For same-sketch annular sections, remember that the subtractive inner loop still yields one face with inner wires; prefer `sweep(profile.sketch, path=path_wire)` over splitting that one annular sketch into guessed outer/inner faces.",
                    "Do not index `profile.faces()[1]` or similar sorted-face expressions after building one annular sketch with `mode=Mode.SUBTRACT`; that pattern usually misreads the profile topology before the sweep even starts.",
                    "If you truly need separate outer/inner sweep solids after evidence from a failed annular sketch sweep, rebuild two independent closed section faces first and only then finish with one explicit solid boolean such as `result = outer_tube - inner_tube`.",
                    "When using direct solid sweep APIs, stay on the verified Build123d contract such as `Solid.sweep(section_face, path_wire)` or `Solid.sweep(section=..., path=...)`; do not invent keywords like `path_wire=` or `profile_plane=`.",
                    "If the requested world orientation keeps producing zero-volume or null-shape sweeps, rebuild the rail/profile in a stable local frame where the first sweep tangent aligns with the default sweep axis, then rotate/translate the finished solid back into the requested pose.",
                    "For hollow bent-pipe repairs, start with the lower-risk annular sketch sweep lane before escalating to separate outer/inner profiles on the endpoint frame.",
                    "When the path includes a tangent elbow with an explicit radius, prefer one connected rail built from stable line/arc members instead of guessing a midpoint-driven arc recipe.",
                    "If the requirement explicitly names a front/top/side view plane for the rail, keep the rail on that named front/top/side view plane instead of guessing a free 3D path frame first.",
                    "For explicit circular elbows with a named radius or quarter-turn, prefer `CenterArc(...)` with `start_angle=` and `arc_size=`; only reach for `TangentArc(...)` or `JernArc(...)` when the requirement truly gives a tangent-construction recipe and you are not guessing the elbow endpoint or turn center.",
                    "For Build123d angle parameters on those rail arcs, pass plain degree numbers directly; do not multiply them by `DEGREE` or `DEGREES`.",
                    "Keep the rail topologically connected: every downstream segment must start from the previous segment endpoint such as `arc @ 1`, not from the arc center or another guessed corner coordinate.",
                    "If `path.wire()` returns or implies multiple wires, repair the rail continuity first and only then debug the section/profile side of the sweep.",
                    "Do not repair this family with legacy Workplane-chain helpers or unsupported sweep shortcuts.",
                    "Keep execute_build123d_probe scripts measurement-oriented and minimal; avoid verbose debug print scaffolding that can fail before any geometry evidence is emitted.",
                ],
            }
        )

    if (
        "u-shaped" in requirement_lower
        or "u shape" in requirement_lower
        or "notch" in requirement_lower
        or "cut out a" in requirement_lower
    ) and "extrude" in requirement_lower:
        skills.append(
            {
                "skill_id": "requirement_driven_cross_section_profiles",
                "when_relevant": "Use when the requirement primarily defines a 2D profile on a named plane and then extrudes it.",
                "guidance": [
                    "Prefer one closed 2D cross-section on the named sketch plane, then extrude along the orthogonal axis.",
                    "If the requirement gives notch width, notch depth, wall height, or slot floor offsets, encode those directly in the profile-plane coordinates instead of approximating them with a later 3D subtractive box.",
                    "If a top-face slot is said to span the full part length and leave a U-shaped/channel section, treat that as a cross-section-first whole-profile build on the orthogonal plane rather than a box host plus a loosely aligned top-face cut.",
                    "Avoid top-face cutBlind box recipes for full-span channel sections unless you explicitly prove the local workplane is centered on the host and the slot truly occupies the requested full span.",
                    "When profile alignment matters more than feature history, a clean whole-profile rebuild is safer than another local boolean patch.",
                ],
            }
        )

    if (
        "union" in requirement_lower
        or "orthogonal" in requirement_lower
        or "two orthogonal" in requirement_lower
    ):
        skills.append(
            {
                "skill_id": "whole_part_union_from_global_axis_primitives",
                "when_relevant": "Use when the part is a small set of orthogonal bars/blocks that must intersect and union cleanly.",
                "guidance": [
                    "If an incremental sketch path is burning rounds, rebuild the whole part with a few global-axis solids and combine them directly.",
                    "For orthogonal unions, verify the required spans on each global axis after the rebuild instead of spending extra rounds inspecting stale sketch windows.",
                    "A whole-part write is preferred when the open sketch still needs multiple writes and the remaining round budget is tight.",
                ],
            }
        )

    if "feature_half_shell_profile_envelope" in blockers:
        skills.append(
            {
                "skill_id": "half_shell_profile_envelope_repair",
                "when_relevant": "Use when validation says the split-shell result kept a full-diameter body instead of a one-sided half-shell envelope.",
                "guidance": [
                    "Repair the base section itself instead of adding more inspection or trim stages.",
                    "Replace any full circle/full cylinder plus split workflow with a semicircle or semi-annulus closed along the split line, then extrude that section for the full housing length.",
                    "Keep the split surface flat and one-sided so the half-profile axis stays near the outer radius, not the full outer diameter.",
                    "Keep the pad and lugs in the same half-plane as the shell material so they widen the orthogonal axis instead of increasing split-axis depth.",
                    "If the current bbox grows because pad/lug material crossed the split line, move that pad/lug material back into the shell's half-plane rather than trimming the entire body.",
                    "After the envelope is corrected, run the bore/clearance cut through the merged shell-plus-pad body rather than only through one subfeature.",
                    "That bore/clearance cut through the merged shell-plus-pad body should leave only the outboard pad material as the two side lugs instead of a full-width bridge under the bore.",
                    "After the half-shell envelope is correct, rebuild the bottom pad/lugs and clearance cut as downstream features outside the bore.",
                ],
            }
        )

    if _requirement_uses_named_plane_symmetric_union(requirement_lower):
        skills.append(
            {
                "skill_id": "named_plane_profile_to_global_box_mapping",
                "when_relevant": "Use when a requirement defines two or more named-plane rectangles that are then extruded symmetrically and unioned.",
                "guidance": [
                    "For whole-part code rebuilds, convert each plane-local rectangle-plus-symmetric-extrude statement into one explicit global-axis box before the union.",
                    "Use these global box mappings: XY rectangle (w x h) with symmetric Z extrude d -> box(w, h, d); YZ rectangle (w x h) with symmetric X extrude d -> box(d, w, h); XZ rectangle (w x h) with symmetric Y extrude d -> box(w, d, h).",
                    "Prefer Build123d Box(global_x, global_y, global_z, align=(Align.CENTER, Align.CENTER, Align.CENTER)) for those whole-body primitives instead of re-encoding them through rotated sketch planes.",
                    "After the union, compare the final global bbox spans against the requirement before spending another read-only turn.",
                ],
            }
        )

    if _requirement_suggests_mixed_nested_section(
        requirement_lower=requirement_lower,
        blockers=blockers,
    ):
        skills.append(
            {
                "skill_id": "mixed_nested_profile_section_bootstrap",
                "when_relevant": "Use when the requirement defines two centered closed profiles where one lives inside the other before the first solid.",
                "guidance": [
                    "If the prompt says to draw multiple centered closed profiles and then extrude the section, treat that as a selected enclosed region, not as an automatic union of all wires.",
                    "The structured additive extrude tool is additive-only. Do not assume unsupported payloads such as mode=cut_hollow or shell-style extrude semantics will create the inner void for you.",
                    "For mixed-shape nested sections such as outer circle plus inner square/rectangle, prefer an explicit hollow/base-minus-inner construction if the tool surface cannot guarantee the intended interior region orientation.",
                    "Complete the whole pre-solid section before the first extrude. Do not extrude an empty sketch, and do not spend rounds validating before the hollow/frame intent is actually realized.",
                    "If a later groove, hole, or other local feature depends on that base, stabilize the hollow base first, then add the downstream feature in a second stage or a single whole-part code rebuild.",
                ],
            }
        )

    annular_requirement = (
        "annular groove" in requirement_lower
        or "revolved cut" in requirement_lower
        or ("groove" in requirement_lower and "revolve" in requirement_lower)
    )
    code_first_annular_path = annular_requirement and (
        code_first_family
        or latest_tool == "execute_build123d"
        or previous_failure_kind.startswith("execute_build123d")
    )

    if annular_requirement and not code_first_annular_path and latest_tool != "execute_build123d":
        skills.append(
            {
                "skill_id": "annular_groove_revolve_cut_recipe",
                "when_relevant": "Use when a local rectangular groove profile must be turned into a rotational subtractive cut with an explicit axis frame.",
                "guidance": [
                    "Use this recipe only when you are still on a structured local-tool path and the groove axis/workplane semantics are explicit.",
                    "Build the base solid first, then create a closed rectangular groove profile on an axis-containing plane such as XZ or YZ.",
                    "Use the rectangle so one dimension is radial depth and the other is axial height/location, then revolve it with a subtractive combine mode around the main axis instead of revolving a detached sheet and cutting later.",
                    "After the groove write, require solids>0 and an unchanged outer bbox before spending more read-only turns.",
                    "If repeated revolve-cut attempts keep failing, or if you are already repairing through execute_build123d, switch to a whole-part code rebuild and subtract an explicit annular groove band from the base solid instead of retrying another raw revolve.",
                ],
            }
        )

    if code_first_annular_path:
        skills.append(
            {
                "skill_id": "code_first_annular_band_subtraction",
                "when_relevant": "Use when an annular-groove requirement is on a code-first Build123d build or repair path.",
                "guidance": [
                    "Treat the base outer envelope as authoritative and realize the groove with one whole-part annular-band subtraction, not a raw sketch-plane revolve.",
                    "For the first whole-part code build, the default first-pass whole-part pattern should already be annular-band subtraction rather than a literal revolve-cut recreation.",
                    "Build the hollow base first, then construct an annular band on the same main axis using the outer radius, inner radius, and requested axial window, and subtract that band from the base.",
                    "For cylindrical parts, a typical pattern is: build the outer solid, subtract the inner void, then create a coaxial annular band whose axial span matches the requested groove height/window and cut it from the base.",
                    "There is no `Ring(...)` helper in Build123d; realize the groove band as an outer coaxial solid/profile minus the inner coaxial solid/profile instead of guessing a ring primitive.",
                    "Do not open a nested `BuildPart()` just to make the groove band while the host `BuildPart` is still active; either keep the groove subtraction in the same active `BuildPart` or close the host and subtract the annular groove band once.",
                    "Do not treat raw sketch-plane revolve as a co-equal repair recipe once execute_build123d is already the active repair path; only return to revolve if the axis/workplane semantics are explicitly proven and the band subtraction route is impossible.",
                    "After the repair write, verify the outer bbox stays stable while the local ring cut appears at the requested axial location.",
                ],
            }
        )

    if (
        "feature_hole_position_alignment" in blockers
        or "feature_local_anchor_alignment" in blockers
        or (
            "hole" in requirement_lower
            and (
                _requirement_has_explicit_xy_coordinate_pair(requirement_text)
                or _requirement_mentions_directional_drilling(requirement_lower)
            )
        )
    ):
        skills.append(
            {
                "skill_id": "positioned_holes_on_face_workplanes",
                "when_relevant": "Use for hole/recess features with explicit local coordinates or a stated drill direction, regardless of whether the next repair stays structured or switches to whole-part Build123d.",
                "guidance": [
                "On a face workplane, hole() at the workplane origin will place the feature at local (0, 0).",
                "For explicit coordinates, place the feature explicitly with Locations((x, y)), GridLocations(...), or a plane-local Pos transform instead of relying on implicit cursor state.",
                "Choose the workplane whose normal matches the requested drill direction: XY drills along Z, XZ drills along Y, and YZ drills along X.",
                "For XY-based top faces, local workplane X/Y usually match the requirement's X/Y coordinates directly; for XZ or YZ workplanes, remap the stated coordinates into that plane before drilling.",
                "If the requirement says the holes run in the Y direction and gives `x` plus `z` coordinates, use the XZ workplane so the local coordinates are `(x, z)` before drilling along Y.",
                "Use `Plane.offset(...)` only for plane-normal translation: `Plane.XY.offset(d)` shifts along Z, `Plane.XZ.offset(d)` shifts along Y, and `Plane.YZ.offset(d)` shifts along X.",
                "For Y-direction drilling on the XZ workplane, `Plane.XZ.offset(d)` shifts along Y, not Z, so do not encode a Z coordinate with `Plane.XZ.offset(z0)`.",
                "Map named faces to plane families by host normal before any local edit: `top/bottom -> Plane.XY`, `front/back -> Plane.XZ`, and `left/right -> Plane.YZ`. If the requirement says `front face`, `Plane.YZ...` is an X-normal side plane, not the front/back plane.",
                "If the named workplane already has the correct normal for the drill direction, keep it as-is instead of calling `Plane.rotated(...)` again; `Plane.rotated(rotation, ordering=...)` changes orientation only and leaves the origin unchanged.",
                "If the hole coordinates clearly come from a rectangular face sketch corner, prefer a corner-anchored host sketch/extrude or explicitly translate those coordinates into the centered host frame before cutting.",
                "If the host solid was created centered about the origin but the requirement's point coordinates came from a rectangular face sketch, translate those corner-based sketch coordinates into the centered host frame before placing the holes.",
                "If the host solid was created with a default centered `Box(length, width, height)`, its named faces sit at `+/- length/2`, `+/- width/2`, and `+/- height/2`; do not target a named top/front/side face by offsetting the workplane with the full span such as `Plane.XY.offset(height)`.",
                "If you still use `CounterSinkHole(...)`, keep the operation in `BuildPart` and include the host-face plane translation in the placement itself, for example `Locations((x, y, top_z), ...)` on a centered top face.",
                "For explicit planar countersink arrays, start with `CounterSinkHole(...)` on the actual host-face plane and keep the helper contract exact before escalating to custom cutters.",
                "Only fall back to an explicit same-builder cylinder+cone or revolved countersink recipe when the helper contract cannot express the host/placement semantics cleanly or when prior validation/evaluation evidence shows the helper result is dimensionally wrong for that family.",
                "When using manual `Cylinder(...)` and `Cone(...)` cutters for countersinks, the cutter placement must start from the host-face plane itself; declaring `top_face_plane` or `host_plane` is not enough by itself if the later `Locations(...)` or cutter transform still stays on the centered mid-plane.",
                "For repeated holes or countersinks, keep the cutters in the same active `BuildPart` with supported subtractive placement, or close the host builder before doing an explicit solid boolean; do not create a nested `BuildPart()` cutter at each location and mutate `part.part -= cutter.part` inside the loop.",
                "If the requirement names one center such as (30, 0), encode that coordinate explicitly in the Build123d geometry placement instead of relying on defaults.",
                ],
            }
        )

    centered_face_array_centers = _infer_centered_square_or_rectangular_array_centers(
        requirement_text
    )
    if len(centered_face_array_centers) >= 4 and bool(getattr(semantics, "mentions_pattern", False)):
        skills.append(
            {
                "skill_id": "explicit_centered_face_array_centers",
                "when_relevant": "Use when the requirement defines a centered square/rectangular face array by side length or per-axis offsets.",
                "guidance": [
                    "Treat the centered face-array layout as an explicit center set, not as a vague pattern hint.",
                    f"For this requirement the local centers are {centered_face_array_centers} (equivalently (±4, ±4) when the side length is 8 mm).",
                    "These centers are face-local coordinates around the host-face center/origin, not corner-based global offsets.",
                    "For default centered Rectangle(...) or Box(...) hosts, keep the centered array anchored around (0, 0) on that face unless the host was explicitly translated first.",
                    "On a face workplane, prefer one explicit pushPoints([...]) or rarray(...) layout over chained relative center(...) calls.",
                    "Do not derive later pattern members by repeatedly moving the current cursor with center(...); those moves are relative and often drift the full array into one quadrant.",
                    "After the write, the realized stud/hole/recess centers should still match the centered local layout before you consider the pattern complete.",
                ],
            }
        )

    if (
        bool(getattr(semantics, "mentions_spherical_recess", False))
        and bool(getattr(semantics, "mentions_pattern", False))
    ):
        skills.append(
            {
                "skill_id": "spherical_recess_pattern_code_first",
                "when_relevant": "Use when the requirement asks for repeated hemispherical/spherical recesses on a host face, especially on the first whole-part code build.",
                "guidance": [
                    "Treat this as a spherical-recess pattern family and prefer one whole-part sphere-subtraction build over a literal revolve recreation on the first pass.",
                    "Build the host solid first, identify the host face plane, and place the sphere centers on that host face plane when the prompt says the diameter edge coincides with the face.",
                    "For a hemispherical recess whose diameter edge lies on the top face, set `sphere_center_z = top_face_z`, not `top_face_z - radius`.",
                    "If the host comes from a default centered Rectangle(...) sketch or an origin-centered Box(...), the host-face center stays at local (0, 0); do not translate a centered pattern by (+width/2, +height/2).",
                    "Create spheres with Build123d solid primitives, place them at the explicit center set, then subtract them from the host body.",
                    "For the first pass, prefer one explicit builder recipe such as `with Locations((x, y, top_z), ...): Sphere(radius=..., mode=Mode.SUBTRACT)` inside the same `BuildPart`.",
                    "Enumerate the full repeated center set explicitly for centered 3x3 or linear-pattern layouts instead of deriving one seed recess and hoping later turns recover the array.",
                    "If the prompt mentions revolve, treat that as descriptive user intent for a hemispherical recess, not as a mandatory first-pass modeling recipe when direct sphere subtraction is lower risk.",
                    "Use only valid Build123d sphere-construction helpers; do not invent alternate top-level sphere constructors.",
                    "Do not subtract by mutating `part.solid`; stay in the builder with `mode=Mode.SUBTRACT`, or subtract from `part.part` only after the builder closes.",
                ],
            }
        )

    if (
        latest_tool == "execute_build123d"
        and bool(getattr(semantics, "mentions_spherical_recess", False))
        and bool(getattr(semantics, "mentions_pattern", False))
        and blockers.intersection(
            {
                "feature_hole",
                "feature_hole_position_alignment",
                "feature_local_anchor_alignment",
                "feature_profile_shape_alignment",
                "feature_pattern",
                "feature_pattern_seed_alignment",
            }
        )
    ):
        skills.append(
            {
                "skill_id": "spherical_recess_pattern_code_repair",
                "when_relevant": "Use when a whole-part code repair already produced a host solid for repeated hemispherical recesses but validation still reports profile/layout mismatches.",
                "guidance": [
                    "Treat this as a spherical-recess pattern family, not as an annular groove or generic revolve family.",
                    "Keep the recesses attached to the host face: when the prompt says the diameter edge coincides with the host face, sphere centers should lie on that host face plane rather than below it.",
                    "Use Build123d sphere primitives for the recess cutters.",
                    "For centered repeated layouts, derive and preserve the full center set explicitly instead of moving one seed sphere and hoping later turns recover the array.",
                    "After a repair write, prefer query_feature_probes or execute_build123d_probe before another blind rewrite so the next turn can distinguish shape success from layout failure.",
                ],
            }
        )

    if (
        latest_tool == "execute_build123d"
        and (
            "revolve" in requirement_lower
            or "shaft" in requirement_lower
            or "stud" in requirement_lower
            or "non_positive_volume" in invalid_signals
        )
    ):
        skills.append(
            {
                "skill_id": "revolve_requires_closed_area",
                "when_relevant": "Use when a revolve/extrude write produced a shell, zero volume, or flat bbox.",
                "guidance": [
                    "A successful revolve must start from a closed 2D area, not only a wire or open profile.",
                    "If the result has solids=1 but volume<=0, treat it as invalid and repair the profile definition before more inspection.",
                    "For stepped shafts, confirm the half-profile encloses area away from the rotation axis before revolving.",
                ],
            }
        )

    if _requirement_explicitly_prescribes_revolve_profile(requirement_lower):
        explicit_revolve_guidance = [
            "Keep the primary strategy on an explicit closed 2D profile revolve, not on a fallback primitive approximation.",
            "Build the closed 2D profile on the plane that contains the rotation axis, then revolve that closed area with an explicit rotation axis definition such as revolve(360, axisStart=..., axisEnd=...).",
            "When building that profile in Build123d, keep curve objects such as `Polyline(...)`, `Line(...)`, `CenterArc(...)`, and `RadiusArc(...)` inside `BuildLine`, then call `make_face()` before revolving.",
            "For Build123d `revolve(...)`, use the default 360-degree revolve or the supported `revolution_arc=` keyword; do not invent `angle=`.",
            "If a previous revolve produced a flat or zero-volume result, first repair the profile closure, workplane choice, and rotation axis before abandoning the revolve recipe.",
            "Treat the centerline/axis-of-rotation instructions as part of the required modeling semantics, not as optional commentary.",
        ]
        if (
            latest_tool == "execute_build123d"
            and "non_positive_volume" in invalid_signals
            and "flat_solid_bbox" in invalid_signals
        ):
            explicit_revolve_guidance.extend(
                [
                    "A repeated flat Build123d revolve means the current profile is still being treated like a wire or sheet instead of a closed area.",
                    "For the repair write, build a closed face explicitly from the profile and revolve that area into a solid instead of retrying minor point-order tweaks on the same wire-only revolve call.",
                    "Keep the coordinate axis that carries the height values aligned with the actual revolve axis: if height values are encoded on Y, revolve around global Y; if height values are encoded on Z for an XZ sketch, revolve around global Z.",
                    "Do not encode the profile with all points at z=0 and then revolve around global Z, or with all points at y=0 and then revolve around global Y; that recreates a planar sheet instead of a positive-volume solid.",
                    "If later code needs another builder stage after the solid revolve, carry the repaired solid forward as the explicit result instead of falling back to legacy workplane chaining.",
                ]
            )
        skills.append(
            {
                "skill_id": "explicit_revolve_profile_recipe",
                "when_relevant": "Use when the requirement explicitly prescribes a sketch plane, rotation axis/centerline, closed profile, and 360-degree revolve workflow.",
                "guidance": explicit_revolve_guidance,
            }
        )

    if (
        latest_tool == "execute_build123d"
        and "non_positive_volume" in invalid_signals
        and (
            "degenerate_bbox" in invalid_signals
            or "flat_solid_bbox" in invalid_signals
        )
        and (
            "revolve" in requirement_lower
            or "shaft" in requirement_lower
            or "stud" in requirement_lower
            or "axial direction" in requirement_lower
            or ("radius" in requirement_lower and "length" in requirement_lower)
        )
        and not _requirement_explicitly_prescribes_revolve_profile(requirement_lower)
    ):
        skills.append(
            {
                "skill_id": "axisymmetric_primitives_after_flat_revolve",
                "when_relevant": "Use when an axisymmetric part keeps producing a sheet-like zero-volume revolve result.",
                "guidance": [
                    "If one bbox axis stays near zero after execute_build123d, treat the result as a flat sheet/surface, not a usable solid.",
                    "For axisymmetric parts defined by radii along axial segments, rebuild with coaxial cylinders or cones merged along the target axis instead of retrying minor point-order variations of the same revolve profile.",
                    "Only keep the revolve strategy if you can clearly construct a positive-area profile that will produce a real solid around the requested axis.",
                    "After the repair write, verify solids>0, volume>0, and all three bbox spans are nonzero before spending more read-only turns.",
                ],
            }
        )

    if (
        latest_tool == "execute_build123d"
        and (
            "feature_multi_plane_additive_union" in blockers
            or "feature_multi_plane_additive_specs" in blockers
            or "union" in requirement_lower
            or "orthogonal" in requirement_lower
        )
    ):
        skills.append(
            {
                "skill_id": "global_axis_primitives_for_multi_body_union",
                "when_relevant": "Use when the part is easier to express as global-axis solids merged together.",
                "guidance": [
                    "For whole-part rebuilds, prefer Build123d Box(x, y, z, align=(...)) to express global-axis boxes directly.",
                    "Do not rely on YZ/XZ workplane intuition for global box dimensions; sketch/workplane orientation is safer for sketch ops than for whole-body primitive dimensions.",
                    "After a union target, compare bbox spans against the required axes before spending another read-only turn.",
                ],
            }
        )

    if (
        latest_tool == "execute_build123d"
        and "feature_cylindrical_slot_alignment" in blockers
    ):
        skills.append(
            {
                "skill_id": "clean_cylindrical_slot_boolean",
                "when_relevant": "Use when an explicit cutting-cylinder slot is aligned correctly in principle but still produces the wrong cylindrical-face topology.",
                "guidance": [
                    "When the requirement already defines a cutting cylinder, model the host block and one tool cylinder directly, then perform a single boolean difference.",
                    "In Build123d, prefer a single `Cylinder(radius, length, align=(Align.CENTER, Align.CENTER, Align.CENTER))` positioned with `Pos(...)` and `Rot(...)` so the requested axis and centerline are literal, instead of rebuilding the slot from stacked partial cuts or improvised profile fragments.",
                    "Do not write `Cylinder(..., axis=...)` in Build123d; create the cylinder first, then orient it with `Rot(...)`.",
                    "Do not build this cutter by sketching a circle on the YZ plane and extruding it both ways when validator is already reporting fragmented cylindrical wall faces; that repair pattern tends to preserve the same broken slot topology.",
                    "For an X-axis slot with centerline `(0, 0, z0)`, the default safe pattern is `cutter = Pos(0, 0, z0) * (Rot(Y=90) * Cylinder(...))`, then `result = host.part - cutter`.",
                    "Avoid repair writes that create extra cylindrical wall fragments on one side of the slot; the target should keep one clean cylindrical wall per side, or one continuous trough face when the topology stays connected.",
                    "After the cut, verify the cylindrical wall faces are minimal and symmetric rather than split into multiple same-side patches.",
                ],
            }
        )

    notch_profile_prompt = (
        "u-shaped" in requirement_lower
        or "u shape" in requirement_lower
        or "channel section" in requirement_lower
        or (
            "top face" in requirement_lower
            and "slot" in requirement_lower
            and any(
                token in requirement_lower
                for token in ("spans the full", "spans full", "full length")
            )
        )
    )
    if latest_tool == "execute_build123d" and (
        "feature_notch_profile_alignment" in blockers or notch_profile_prompt
    ):
        skills.append(
            {
                "skill_id": "cross_section_first_notch_profiles",
                "when_relevant": "Use when a U-shape / notch / slot profile exists but keeps failing profile-alignment validation.",
                "guidance": [
                    "Model the named 2D cross-section directly on the requirement's profile plane with `BuildSketch`, then extrude along the orthogonal axis.",
                    "For rectangular U-channels and centered notch sections, prefer one sketch containing an outer `Rectangle(...)` plus an inner `Rectangle(..., mode=Mode.SUBTRACT)` window instead of rebuilding the notch later with loosely aligned subtractive boxes.",
                    "Keep width, wall height, groove width, groove depth, and the floor offset in that same sketch/profile plane; then extrude the finished section with `extrude(amount=...)` on the orthogonal axis.",
                    "Prefer this same-sketch subtract recipe over ad-hoc `subtract(profile.sketch)`, partial `make_face(mode=Mode.SUBTRACT)` repairs, or ambiguous directional extrude retries.",
                    "If validator still says feature_notch_profile_alignment after a nominally successful write, rebuild the whole cross-section cleanly rather than spending more read-only turns on the same misoriented profile.",
                ],
            }
        )

    if (
        latest_tool == "execute_build123d"
        and "feature_fillet" in blockers
    ):
        skills.append(
            {
                "skill_id": "session_backed_local_edge_finishing",
                "when_relevant": "Use when a direct code rebuild succeeded but a local fillet/chamfer feature is still missing.",
                "guidance": [
                "A successful execute_build123d write already persisted authoritative session geometry for follow-on tools.",
                "Prefer query_topology to get fresh edge refs, then use apply_cad_action with fillet/chamfer and explicit edge_refs for the local finishing step.",
                "If query_topology already exposes a requirement-aligned edge candidate set such as bottom_outer_edges or y_parallel_bottom_outer_edges, consume those refs directly on the next write turn instead of spending another read-only round.",
                "For notch/opening/lip cleanup, prefer `opening_rim_edges` when that candidate set is available instead of guessing one more global rebuild.",
                "Do not default to reloading model.step inside another Build123d script for a small local finish unless the runtime explicitly exposes a state-import helper.",
            ],
        }
    )

    axis_selector = None
    if "parallel to the x axis" in requirement_lower:
        axis_selector = "|X"
    elif "parallel to the y axis" in requirement_lower:
        axis_selector = "|Y"
    elif "parallel to the z axis" in requirement_lower:
        axis_selector = "|Z"
    edge_scope = None
    if "bottom" in requirement_lower:
        edge_scope = "bottom"
    elif "top" in requirement_lower:
        edge_scope = "top"
    if (
        latest_tool == "execute_build123d"
        and blockers.intersection({"feature_fillet", "feature_chamfer"})
        and axis_selector is not None
        and edge_scope is not None
    ):
        skills.append(
            {
                "skill_id": "axis_constrained_local_edge_finish_selectors",
                "when_relevant": "Use when only a directional fillet/chamfer tail remains after a successful code-path rebuild.",
                "guidance": [
                    "Prefer one local apply_cad_action finish over another whole-part rewrite when the remaining blocker is only a directional fillet/chamfer.",
                    f"For this kind of requirement, use apply_cad_action with edge_scope='{edge_scope}' and edges_selector='{axis_selector}' before falling back to broader chained selectors in execute_build123d.",
                    "Use query_topology only if the selector is still ambiguous; do not spend the default next turn on another blind whole-part code retry.",
                    "Avoid inverted selector chains such as picking the correct axis but the wrong top/bottom side in Build123d code.",
                ],
            }
        )

    if (
        latest_tool == "execute_build123d"
        and previous_failure_kind in {
            "execute_build123d_chain_context_failure",
            "execute_build123d_selector_failure",
            "execute_build123d_timeout",
        }
        and _requirement_prefers_named_face_local_feature_sequence(requirement_lower)
    ):
        skills.append(
            {
                "skill_id": "recover_from_failed_whole_part_retry",
                "when_relevant": "Use when a whole-part code retry is failing on a requirement that already decomposes into bounded local edits.",
                "guidance": [
                    "Treat the failed whole-part code path as evidence about tool choice, not only as a syntax/modeling bug.",
                    "After execute_build123d timeout, broken solid-chain, or selector-based fillet failure, do not default to another end-to-end rewrite on the next turn.",
                    "Either rebuild only to the simpler pre-fillet solid or switch to a bounded local finishing step once a stable host solid and authoritative refs exist.",
                ],
            }
        )

    if annular_blockers_active:
        skills.append(
            {
                "skill_id": "axisymmetric_annular_groove_strategy",
                "when_relevant": "Use when annular/revolved groove intent is under-specified by local repairs and keeps failing semantic completion.",
                "guidance": [
                    "Treat the outer envelope as authoritative and realize the groove as a local rotational subtraction around the same main axis.",
                    "Prefer axisymmetric constructions whose groove depth and axial location can be read back from the final geometry, such as coaxial solid differences or a clearly anchored annular groove band on the main axis.",
                    "When execute_build123d is repairing a cylindrical part, the default whole-part pattern should be: build the base solid, build an annular groove band with outer_radius and inner_radius on the same axis, extrude that band to the requested axial window, then subtract it from the base.",
                    "Only use a raw sketch-plane revolve when you are confident about the local workplane coordinates and rotation axis semantics; otherwise treat revolve as a higher-risk fallback, not the default code-path repair.",
                    "After the repair write, verify that the outer bbox stays stable while the groove introduces the requested local rotational cut.",
                ],
            }
        )

    if (
        latest_tool == "execute_build123d"
        and code_first_family
        and same_tool_failure_count >= 2
    ):
        skills.append(
            {
                "skill_id": "failed_code_family_turn_must_probe_before_retry",
                "when_relevant": "Use when the same whole-part code path has already failed repeatedly on a family-driven geometry problem.",
                "guidance": [
                    "Do not spend the next turn on another equally broad execute_build123d rewrite.",
                    "The next turn should be a targeted diagnostic turn: query_feature_probes first, then query_geometry if needed for bbox/solid confirmation.",
                    "If those reads still leave one family-specific modeling question unresolved, use execute_build123d_probe for a one-off diagnostic script before the next whole-part rewrite.",
                    "Only issue another broad execute_build123d write after the probe turn gives a concrete repair target.",
                ],
            }
        )

    deduped: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for skill in skills:
        skill_id = str(skill.get("skill_id") or "").strip()
        if not skill_id or skill_id in seen_ids:
            continue
        seen_ids.add(skill_id)
        enriched_skill = dict(skill)
        enriched_skill["context_priority"] = _skill_priority(
            skill_id,
            latest_tool=latest_tool,
            annular_blockers_active=annular_blockers_active,
        )
        deduped.append(enriched_skill)

    deduped.sort(
        key=lambda skill: (
            int(skill.get("context_priority", 100)),
            str(skill.get("skill_id") or "").strip(),
        )
    )
    return deduped


def _skill_priority(
    skill_id: str,
    *,
    latest_tool: str,
    annular_blockers_active: bool,
) -> int:
    if latest_tool == "apply_cad_action":
        priorities = {
            "local_finish_retry_bind_latest_face_ref": 0,
            "topology_candidate_set_label_is_not_exact_ref": 1,
            "local_finish_exact_face_ref_contract": 2,
            "local_finish_preserve_existing_local_centers": 3,
            "named_face_local_feature_sequence": 4,
            "enclosure_local_feature_placement_contract": 5,
            "kernel_repair_packet_recipe": 6,
        }
        return priorities.get(skill_id, 20)
    if latest_tool == "execute_build123d" and annular_blockers_active:
        priorities = {
            "axisymmetric_annular_groove_strategy": 0,
            "mixed_nested_profile_section_bootstrap": 1,
            "revolve_requires_closed_area": 2,
            "annular_groove_revolve_cut_recipe": 5,
        }
        return priorities.get(skill_id, 10)
    general_priorities = {
        "execute_build123d_minimal_script_hygiene": 0,
        "execute_build123d_clamshell_host_local_cut_contract": 1,
        "code_first_local_finish_tail_contract": 2,
        "nested_hollow_section_builder_native_cavity": 3,
        "enclosure_local_feature_placement_contract": 4,
        "multi_part_assembled_pose_bbox_contract": 5,
        "clamshell_split_axis_and_hinge_contract": 6,
        "execute_build123d_failure_lint_contract": 7,
        "execute_build123d_api_lint_repair_first": 8,
        "execute_build123d_detached_subtractive_builder_repair": 9,
        "execute_build123d_rotated_detached_cutter_contract": 10,
        "execute_build123d_compound_children_contract": 11,
        "execute_build123d_explicit_cylindrical_slot_recipe_contract": 12,
        "execute_build123d_failure_recipe_focus": 13,
        "execute_build123d_active_builder_authority_repair": 14,
        "kernel_repair_packet_recipe": 15,
        "local_finish_exact_face_ref_contract": 16,
        "local_finish_preserve_existing_local_centers": 17,
        "spherical_recess_pattern_code_first": 18,
        "explicit_centered_face_array_centers": 19,
        "spherical_recess_pattern_code_repair": 20,
        "recover_from_failed_whole_part_retry": 21,
        "clean_cylindrical_slot_boolean": 22,
        "explicit_revolve_profile_recipe": 23,
        "axisymmetric_segmented_primitives_preferred_over_revolve": 24,
        "half_shell_profile_envelope_repair": 24,
        "half_shell_profile_from_semicircle_section": 25,
        "path_sweep_wire_profile_frame_repair": 26,
        "named_face_local_feature_sequence": 27,
        "flange_boss_pattern_hole_host_thickness": 28,
        "nested_regular_polygon_frame_code_first": 29,
        "named_axis_axisymmetric_pose_alignment_repair": 30,
        "regular_polygon_side_length_build123d_semantics": 31,
        "positive_extrude_from_named_plane_is_not_centered": 32,
        "positive_extrude_bbox_alignment_repair": 33,
        "whole_part_additive_features_must_merge_into_single_body": 34,
        "named_plane_profile_to_global_box_mapping": 35,
        "whole_part_union_from_global_axis_primitives": 36,
    }
    if skill_id in general_priorities:
        return general_priorities[skill_id]
    return 100


def _requirements_text(requirements: dict[str, Any]) -> str:
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
    has_rail = "path sketch" in requirement_lower or "path" in requirement_lower or "rail" in requirement_lower
    has_profile = "profile sketch" in requirement_lower or "profile" in requirement_lower
    return has_rail and has_profile


def _requirement_uses_named_plane_symmetric_union(requirement_lower: str) -> bool:
    plane_hits = sum(
        1 for token in ("xy plane", "yz plane", "xz plane") if token in requirement_lower
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
    if str(latest_write_health.get("tool") or "").strip().lower() != "execute_build123d":
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
    import re

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
    if str(latest_write_health.get("tool") or "").strip().lower() != "execute_build123d":
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
    import re

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
            x_positions = [round((index - x_mid) * x_spacing, 4) for index in range(x_count)]
            y_positions = [round((index - y_mid) * y_spacing, 4) for index in range(y_count)]
            return [[x_pos, y_pos] for x_pos in x_positions for y_pos in y_positions]

    return []
