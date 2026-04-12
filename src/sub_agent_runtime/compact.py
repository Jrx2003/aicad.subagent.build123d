from __future__ import annotations

import json
from typing import Any

_DOMAIN_KERNEL_PRIORITY_KEYS = (
    "feature_instance_count",
    "active_feature_instances",
    "kernel_patch_count",
    "kernel_patch_kinds",
    "latest_patch_repair_mode",
    "latest_patch_feature_instance_ids",
    "latest_patch_affected_hosts",
    "latest_patch_anchor_keys",
    "latest_patch_parameter_keys",
    "latest_patch_feature_instances",
    "latest_patch_repair_intent",
)

_HARD_BUDGET_PRIORITY_SECTIONS = (
    "domain_kernel_digest",
    "turn_status",
    "round_budget",
    "evidence_status",
    "freshest_evidence",
    "fresh_write_pending_judgment",
    "freshness_source_round",
    "primary_write_mode",
    "objective_health",
    "latest_write_health",
    "previous_tool_failure_summary",
    "turn_tool_policy",
    "stall_summary",
)


def apply_turn_budget_with_report(
    payload: dict[str, Any],
    *,
    soft_chars: int = 20000,
    hard_chars: int = 35000,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Compact a context payload until it fits the hard budget, with a report."""
    rendered = render_json_length(payload)
    report = {
        "raw_chars": rendered,
        "final_chars": rendered,
        "was_compacted": False,
        "reason": None,
        "kept_sections": sorted(payload.keys()),
        "summarized_sections": [],
        "why_this_turn_is_compacted": None,
        "what_was_kept": sorted(payload.keys()),
        "what_was_summarized": [],
        "post_compact_messages": [],
    }
    if rendered <= soft_chars:
        return payload, report

    compacted = dict(payload)
    report["was_compacted"] = True
    report["reason"] = "soft_budget_exceeded"
    report["why_this_turn_is_compacted"] = (
        f"Rendered context reached {rendered} chars and exceeded the soft budget "
        f"of {soft_chars} chars."
    )

    if "diagnostics" in compacted:
        compacted["diagnostics"] = compact_jsonish(
            compacted["diagnostics"],
            max_depth=2,
            max_items=4,
            max_string_chars=120,
        )
        report["summarized_sections"].append("diagnostics")
        report["post_compact_messages"].append(
            "Diagnostics were summarized before dropping current-step evidence."
        )
    current_chars = render_json_length(compacted)
    if current_chars <= hard_chars:
        report["final_chars"] = current_chars
        report["what_was_summarized"] = list(report["summarized_sections"])
        return compacted, report

    if "recent_turns" in compacted:
        compacted["recent_turns"] = compact_jsonish(
            compacted["recent_turns"],
            max_depth=3,
            max_items=4,
            max_string_chars=120,
        )
        report["summarized_sections"].append("recent_turns")
        report["post_compact_messages"].append(
            "Older turn transcript was summarized to keep the most recent rounds verbatim."
        )
    current_chars = render_json_length(compacted)
    if current_chars <= hard_chars:
        report["final_chars"] = current_chars
        report["what_was_summarized"] = list(report["summarized_sections"])
        return compacted, report

    if "recent_public_transcript" in compacted:
        compacted["recent_public_transcript"] = compact_jsonish(
            compacted["recent_public_transcript"],
            max_depth=3,
            max_items=4,
            max_string_chars=120,
        )
        report["summarized_sections"].append("recent_public_transcript")
        report["post_compact_messages"].append(
            "Recent public transcript was summarized before trimming current-turn repair guidance."
        )
    current_chars = render_json_length(compacted)
    if current_chars <= hard_chars:
        report["final_chars"] = current_chars
        report["what_was_summarized"] = list(report["summarized_sections"])
        return compacted, report

    for section_name in ("tool_partitions", "artifact_index"):
        if section_name not in compacted:
            continue
        compacted[section_name] = compact_jsonish(
            compacted[section_name],
            max_depth=3,
            max_items=4,
            max_string_chars=120,
        )
        report["summarized_sections"].append(section_name)
        report["post_compact_messages"].append(
            f"{section_name} was summarized ahead of runtime skill notes to preserve current-turn repair guidance."
        )
        current_chars = render_json_length(compacted)
        if current_chars <= hard_chars:
            report["final_chars"] = current_chars
            report["what_was_summarized"] = list(report["summarized_sections"])
            return compacted, report

    evidence_key = (
        "freshest_evidence"
        if "freshest_evidence" in compacted
        else "current_evidence"
        if "current_evidence" in compacted
        else None
    )
    if evidence_key is not None:
        compacted[evidence_key] = compact_jsonish(
            compacted[evidence_key],
            max_depth=3,
            max_items=5,
            max_string_chars=120,
        )
        report["summarized_sections"].append(evidence_key)
        report["post_compact_messages"].append(
            f"{evidence_key} was compacted after diagnostics/history trimming was insufficient."
        )
    current_chars = render_json_length(compacted)
    if current_chars <= hard_chars:
        report["final_chars"] = current_chars
        report["what_was_summarized"] = list(report["summarized_sections"])
        return compacted, report

    report["reason"] = "hard_budget_exceeded"
    report["summarized_sections"].append("full_payload")
    report["post_compact_messages"].append(
        "Hard budget was exceeded, so the full payload was summarized as a last resort."
    )
    runtime_skills = compacted.get("runtime_skills")
    payload_without_runtime_skills = {
        key: value for key, value in compacted.items() if key != "runtime_skills"
    }
    final_payload = compact_jsonish(
        payload_without_runtime_skills,
        max_depth=3,
        max_items=5,
        max_string_chars=120,
    )
    if isinstance(final_payload, dict):
        final_payload = _restore_hard_budget_priority_sections(
            final_payload,
            payload_without_runtime_skills,
            hard_chars=hard_chars,
        )
    if runtime_skills not in (None, [], {}):
        candidate_payload = dict(final_payload)
        candidate_payload["runtime_skills"] = runtime_skills
        if render_json_length(candidate_payload) <= hard_chars:
            final_payload = candidate_payload
    report["final_chars"] = render_json_length(final_payload)
    report["what_was_summarized"] = list(report["summarized_sections"])
    return final_payload, report


def compact_jsonish(
    value: Any,
    *,
    max_depth: int = 4,
    max_items: int = 8,
    max_string_chars: int = 240,
) -> Any:
    """Lossy but inspectable compaction for planner-facing context."""
    if max_depth <= 0:
        if isinstance(value, (dict, list)):
            return "<compacted>"
        return _clip_string(value, max_string_chars=max_string_chars)

    if isinstance(value, dict):
        items = list(value.items())
        compacted: dict[str, Any] = {}
        for index, (key, item) in enumerate(items):
            if index >= max_items:
                compacted["__truncated_keys__"] = len(items) - max_items
                break
            compacted[str(key)] = compact_jsonish(
                item,
                max_depth=max_depth - 1,
                max_items=max_items,
                max_string_chars=max_string_chars,
            )
        return compacted

    if isinstance(value, list):
        compacted_list = [
            compact_jsonish(
                item,
                max_depth=max_depth - 1,
                max_items=max_items,
                max_string_chars=max_string_chars,
            )
            for item in value[:max_items]
        ]
        if len(value) > max_items:
            compacted_list.append({"__truncated_items__": len(value) - max_items})
        return compacted_list

    return _clip_string(value, max_string_chars=max_string_chars)


def render_json_length(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, indent=2))
    except Exception:
        return len(str(value))


def apply_turn_budget(
    payload: dict[str, Any],
    *,
    soft_chars: int = 20000,
    hard_chars: int = 35000,
) -> dict[str, Any]:
    """Compact a context payload until it fits the hard budget."""
    compacted, _report = apply_turn_budget_with_report(
        payload,
        soft_chars=soft_chars,
        hard_chars=hard_chars,
    )
    return compacted


def _restore_hard_budget_priority_sections(
    compacted: dict[str, Any],
    source_payload: dict[str, Any],
    *,
    hard_chars: int,
) -> dict[str, Any]:
    restored = dict(compacted)
    for section_name in _HARD_BUDGET_PRIORITY_SECTIONS:
        if section_name not in source_payload:
            continue
        section_value = _compact_priority_section(
            section_name,
            source_payload[section_name],
        )
        candidate_payload = dict(restored)
        candidate_payload[section_name] = section_value
        restored = candidate_payload

    return restored


def _compact_priority_section(section_name: str, value: Any) -> Any:
    if section_name == "domain_kernel_digest" and isinstance(value, dict):
        compacted = compact_jsonish(
            value,
            max_depth=3,
            max_items=8,
            max_string_chars=120,
        )
        if not isinstance(compacted, dict):
            return compacted
        for key in _DOMAIN_KERNEL_PRIORITY_KEYS:
            if key in value:
                compacted[key] = compact_jsonish(
                    value[key],
                    max_depth=3,
                    max_items=8,
                    max_string_chars=120,
                )
        return compacted
    return compact_jsonish(
        value,
        max_depth=3,
        max_items=8,
        max_string_chars=120,
    )


def _clip_string(value: Any, *, max_string_chars: int) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    if isinstance(value, str):
        if len(value) <= max_string_chars:
            return value
        return f"{value[:max_string_chars]}...[truncated {len(value) - max_string_chars} chars]"
    return value
