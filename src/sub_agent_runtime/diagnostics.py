from __future__ import annotations

from typing import Any

from common.blocker_taxonomy import (
    classify_blocker_taxonomy_many,
    taxonomy_records_from_validation_payload,
)
from sub_agent_runtime.turn_state import RunState


_RUNTIME_DIAGNOSTIC_CHECK_IDS = {
    "feature_target_face_edit",
    "feature_target_face_subtractive_merge",
    "feature_multi_plane_additive_union",
    "feature_multi_plane_additive_specs",
    "pre_solid_profile_shape_alignment",
}


def build_v2_diagnostics(run_state: RunState) -> dict[str, Any]:
    """Diagnostics stay inspectable without driving the main loop."""
    latest_turn = run_state.turns[-1] if run_state.turns else None
    return {
        "mode": "v2",
        "turn_count": len(run_state.turns),
        "latest_error": run_state.previous_error,
        "latest_write_tool": latest_turn.write_tool_name if latest_turn else None,
        "available_diagnostics": [
            "action_history",
            "tool_results",
            "artifact_index",
        ],
    }


def split_validation_feedback(payload: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split requirement validation into loop-safe core facts and diagnostics-only detail."""
    if not isinstance(payload, dict):
        return {}, {}
    raw_core_checks = payload.get("core_checks")
    raw_diagnostic_checks = payload.get("diagnostic_checks")
    if not isinstance(raw_core_checks, list) and not isinstance(raw_diagnostic_checks, list):
        checks = payload.get("checks")
        normalized_checks = checks if isinstance(checks, list) else []
        raw_core_checks = normalized_checks
        raw_diagnostic_checks = []

    core_checks = _summarize_validation_checks(raw_core_checks)
    diagnostic_checks = _summarize_validation_checks(raw_diagnostic_checks)
    retained_core_checks: list[dict[str, Any]] = []
    for item in core_checks:
        if _runtime_prefers_diagnostic_lane(item):
            diagnostic_checks.append(item)
        else:
            retained_core_checks.append(item)
    core_checks = retained_core_checks
    failed_core_checks = [
        item
        for item in core_checks
        if str(item.get("status") or "").lower() in {"fail", "failed", "error", "blocked"}
    ]
    failed_diagnostic_checks = [
        item
        for item in diagnostic_checks
        if str(item.get("status") or "").lower() in {"fail", "failed", "error", "blocked"}
    ]
    core_blockers = [
        str(item.get("name"))
        for item in failed_core_checks
        if isinstance(item.get("name"), str) and str(item.get("name")).strip()
    ]
    diagnostic_blockers = [
        str(item.get("name"))
        for item in failed_diagnostic_checks
        if isinstance(item.get("name"), str) and str(item.get("name")).strip()
    ]
    if not core_blockers and not diagnostic_blockers:
        taxonomy_core_blockers, taxonomy_diagnostic_blockers = _taxonomy_blockers_by_lane(payload)
        if taxonomy_core_blockers or taxonomy_diagnostic_blockers:
            core_blockers = taxonomy_core_blockers
            diagnostic_blockers = taxonomy_diagnostic_blockers
        else:
            core_blockers = [
                str(item).strip()
                for item in (payload.get("blockers") or [])
                if isinstance(item, str) and str(item).strip()
            ]
    observation_tags = [
        str(item).strip()
        for item in (payload.get("observation_tags") or [])
        if isinstance(item, str) and str(item).strip()
    ]
    has_insufficient_evidence = bool(payload.get("insufficient_evidence")) or (
        "insufficient_evidence" in observation_tags
    )
    if core_checks or diagnostic_checks:
        is_complete = len(core_blockers) == 0 and not has_insufficient_evidence
    else:
        is_complete = bool(payload.get("is_complete")) and not has_insufficient_evidence
    summary = payload.get("summary")
    if core_blockers:
        summary = f"Requirement validation has {len(core_blockers)} core blocker(s)"
    elif has_insufficient_evidence:
        summary = "Requirement validation has insufficient evidence"
    elif diagnostic_blockers:
        summary = "Requirement validation has diagnostic-only blockers"
    elif not isinstance(summary, str) or not summary.strip():
        if is_complete:
            summary = "Requirement validation passed"
        else:
            summary = "Requirement validation is incomplete"
    core = {
        "success": payload.get("success"),
        "is_complete": is_complete,
        "summary": summary,
        "blockers": core_blockers,
        "failed_checks": failed_core_checks[:8],
        "core_check_count": len(core_checks),
        "diagnostic_check_count": len(diagnostic_checks),
        "insufficient_evidence": has_insufficient_evidence,
    }
    core_taxonomy = _partition_blocker_taxonomy(
        payload,
        blocker_ids=core_blockers,
        completeness_relevance="core",
    )
    if core_taxonomy:
        core["blocker_taxonomy"] = core_taxonomy
    diagnostics = {
        "checks": diagnostic_checks[:16],
        "failed_checks": failed_diagnostic_checks[:16],
        "diagnostic_blockers": diagnostic_blockers,
        "raw_blockers": payload.get("blockers") if isinstance(payload.get("blockers"), list) else [],
        "relation_index": payload.get("relation_index"),
        "step": payload.get("step"),
        "session_id": payload.get("session_id"),
    }
    diagnostic_taxonomy = _partition_blocker_taxonomy(
        payload,
        blocker_ids=diagnostic_blockers,
        completeness_relevance="diagnostic",
    )
    if diagnostic_taxonomy:
        diagnostics["diagnostic_blocker_taxonomy"] = diagnostic_taxonomy
    return core, diagnostics


def build_runtime_validation_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize raw validation payload into the loop/core view used by runtime state sync."""
    if not isinstance(payload, dict):
        return {}
    core, diagnostics = split_validation_feedback(payload)
    normalized = dict(payload)
    normalized["is_complete"] = core.get("is_complete")
    normalized["summary"] = core.get("summary")
    normalized["blockers"] = list(core.get("blockers") or [])
    normalized["failed_checks"] = list(core.get("failed_checks") or [])
    normalized["core_check_count"] = core.get("core_check_count")
    normalized["diagnostic_check_count"] = core.get("diagnostic_check_count")
    normalized["insufficient_evidence"] = bool(core.get("insufficient_evidence"))
    normalized["core_checks"] = [
        {
            "check_id": item.get("name"),
            "status": item.get("status"),
            "message": item.get("message"),
            "evidence": item.get("evidence"),
            "label": item.get("label"),
        }
        for item in (core.get("failed_checks") or [])
        if isinstance(item, dict)
    ]
    normalized["diagnostic_checks"] = [
        {
            "check_id": item.get("name"),
            "status": item.get("status"),
            "message": item.get("message"),
            "evidence": item.get("evidence"),
            "label": item.get("label"),
        }
        for item in (diagnostics.get("failed_checks") or [])
        if isinstance(item, dict)
    ]
    if "blocker_taxonomy" in core:
        normalized["blocker_taxonomy"] = list(core.get("blocker_taxonomy") or [])
    else:
        normalized.pop("blocker_taxonomy", None)
    normalized["diagnostic_blockers"] = list(diagnostics.get("diagnostic_blockers") or [])
    if "diagnostic_blocker_taxonomy" in diagnostics:
        normalized["diagnostic_blocker_taxonomy"] = list(
            diagnostics.get("diagnostic_blocker_taxonomy") or []
        )
    else:
        normalized.pop("diagnostic_blocker_taxonomy", None)
    return normalized


def _summarize_validation_checks(raw_checks: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    if not isinstance(raw_checks, list):
        return normalized
    for raw_check in raw_checks:
        if not isinstance(raw_check, dict):
            continue
        normalized.append(
            {
                "name": raw_check.get("check_id") or raw_check.get("name") or raw_check.get("code"),
                "status": raw_check.get("status"),
                "message": raw_check.get("message") or raw_check.get("summary") or raw_check.get("evidence"),
                "evidence": raw_check.get("evidence") or raw_check.get("message") or raw_check.get("summary"),
                "label": raw_check.get("label"),
            }
        )
    return normalized


def _runtime_prefers_diagnostic_lane(check: dict[str, Any]) -> bool:
    check_name = str(check.get("name") or "").strip()
    return check_name in _RUNTIME_DIAGNOSTIC_CHECK_IDS


def _partition_blocker_taxonomy(
    payload: dict[str, Any],
    *,
    blocker_ids: list[str],
    completeness_relevance: str,
) -> list[dict[str, Any]]:
    normalized_blockers = [
        blocker_id.strip()
        for blocker_id in blocker_ids
        if isinstance(blocker_id, str) and blocker_id.strip()
    ]
    if not normalized_blockers:
        return []
    blocker_set = set(normalized_blockers)
    existing_records = taxonomy_records_from_validation_payload(payload)
    matched: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in existing_records:
        blocker_id = str(getattr(record, "blocker_id", "") or "").strip()
        if blocker_id not in blocker_set or blocker_id in seen:
            continue
        seen.add(blocker_id)
        matched.append(
            {
                "blocker_id": blocker_id,
                "normalized_blocker_id": str(
                    getattr(record, "normalized_blocker_id", "") or blocker_id
                ).strip()
                or blocker_id,
                "family_ids": [
                    str(item).strip()
                    for item in getattr(record, "family_ids", [])
                    if isinstance(item, str) and str(item).strip()
                ],
                "feature_ids": [
                    str(item).strip()
                    for item in getattr(record, "feature_ids", [])
                    if isinstance(item, str) and str(item).strip()
                ],
                "primary_feature_id": str(
                    getattr(record, "primary_feature_id", "") or "feature.core_geometry"
                ).strip()
                or "feature.core_geometry",
                "evidence_source": str(
                    getattr(record, "evidence_source", "") or "validation"
                ).strip()
                or "validation",
                "completeness_relevance": completeness_relevance,
                "severity": "blocking" if completeness_relevance == "core" else "diagnostic",
                "recommended_repair_lane": str(
                    getattr(record, "recommended_repair_lane", "") or "code_repair"
                ).strip()
                or "code_repair",
            }
        )
    missing = [blocker_id for blocker_id in normalized_blockers if blocker_id not in seen]
    if missing:
        matched.extend(
            _serialize_blocker_taxonomy(
                classify_blocker_taxonomy_many(
                    missing,
                    evidence_source="validation",
                    completeness_relevance=completeness_relevance,
                )
            )
        )
    return matched


def _taxonomy_blockers_by_lane(payload: dict[str, Any]) -> tuple[list[str], list[str]]:
    core_blockers: list[str] = []
    diagnostic_blockers: list[str] = []
    seen_core: set[str] = set()
    seen_diagnostic: set[str] = set()
    for record in taxonomy_records_from_validation_payload(payload):
        blocker_id = str(getattr(record, "blocker_id", "") or "").strip()
        if not blocker_id:
            continue
        lane = str(getattr(record, "completeness_relevance", "") or "core").strip().lower()
        if lane == "diagnostic":
            if blocker_id in seen_diagnostic:
                continue
            seen_diagnostic.add(blocker_id)
            diagnostic_blockers.append(blocker_id)
            continue
        if blocker_id in seen_core:
            continue
        seen_core.add(blocker_id)
        core_blockers.append(blocker_id)
    return core_blockers, diagnostic_blockers


def _serialize_blocker_taxonomy(records: Any) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    if not isinstance(records, list):
        return serialized
    for record in records:
        if not hasattr(record, "blocker_id"):
            continue
        blocker_id = str(getattr(record, "blocker_id", "") or "").strip()
        if not blocker_id:
            continue
        serialized.append(
            {
                "blocker_id": blocker_id,
                "normalized_blocker_id": str(
                    getattr(record, "normalized_blocker_id", "") or blocker_id
                ).strip()
                or blocker_id,
                "family_ids": [
                    str(item).strip()
                    for item in getattr(record, "family_ids", [])
                    if isinstance(item, str) and str(item).strip()
                ],
                "feature_ids": [
                    str(item).strip()
                    for item in getattr(record, "feature_ids", [])
                    if isinstance(item, str) and str(item).strip()
                ],
                "primary_feature_id": str(
                    getattr(record, "primary_feature_id", "") or "feature.core_geometry"
                ).strip()
                or "feature.core_geometry",
                "evidence_source": str(
                    getattr(record, "evidence_source", "") or "validation"
                ).strip()
                or "validation",
                "completeness_relevance": str(
                    getattr(record, "completeness_relevance", "") or "core"
                ).strip()
                or "core",
                "severity": str(getattr(record, "severity", "") or "").strip()
                or "unknown",
                "recommended_repair_lane": str(
                    getattr(record, "recommended_repair_lane", "") or "code_repair"
                ).strip()
                or "code_repair",
            }
        )
    return serialized
