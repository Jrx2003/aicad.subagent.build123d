from __future__ import annotations

import ast
import hashlib
import json
import re
from typing import Any

from common.blocker_taxonomy import (
    classify_blocker_taxonomy_many,
    taxonomy_records_from_validation_payload,
)

from sub_agent_runtime.semantic_kernel.bootstrap import _stable_hash
from sub_agent_runtime.semantic_kernel.models import DomainKernelState, KernelBinding
from sub_agent_runtime.semantic_kernel.taxonomy import (
    _canonical_recommended_repair_lane,
    _validation_blocker_taxonomy,
)

def _host_ids_from_binding(binding: KernelBinding | None, *, family_id: str | None = None) -> list[str]:
    if binding is None:
        return []
    feature_anchor_summary = (
        binding.feature_anchor_summary if isinstance(binding.feature_anchor_summary, dict) else {}
    )
    family_signal_values = _family_signal_values_from_binding(binding, family_id=family_id)
    host_face = family_signal_values.get("host_face")
    host_faces = family_signal_values.get("host_faces")
    if not host_face and not host_faces:
        host_face = feature_anchor_summary.get("host_face")
        host_faces = feature_anchor_summary.get("host_faces")
    host_ids: list[str] = []
    if isinstance(host_face, str) and host_face.strip():
        host_ids.append(host_face.strip())
    if isinstance(host_faces, list):
        host_ids.extend(
            str(item).strip() for item in host_faces if isinstance(item, str) and str(item).strip()
        )
    if not host_ids:
        host_ids.append("body.primary")
    return list(dict.fromkeys(host_ids))

def _anchor_keys_from_binding(binding: KernelBinding | None, *, family_id: str) -> list[str]:
    if binding is None:
        return []
    family_signal_values = _family_signal_values_from_binding(binding, family_id=family_id)
    if family_signal_values:
        return sorted(
            {
                str(key).strip()
                for key in family_signal_values.keys()
                if isinstance(key, str) and str(key).strip()
            }
        )
    feature_anchor_summary = (
        binding.feature_anchor_summary if isinstance(binding.feature_anchor_summary, dict) else {}
    )
    anchor_signal_keys_by_family = feature_anchor_summary.get("anchor_signal_keys_by_family")
    if isinstance(anchor_signal_keys_by_family, dict):
        family_keys = anchor_signal_keys_by_family.get(family_id)
        if isinstance(family_keys, list):
            return [
                str(item).strip()
                for item in family_keys
                if isinstance(item, str) and str(item).strip()
            ]
    return []

def _family_signal_values_from_binding(
    binding: KernelBinding | None,
    *,
    family_id: str | None,
) -> dict[str, Any]:
    if binding is None:
        return {}
    feature_anchor_summary = (
        binding.feature_anchor_summary if isinstance(binding.feature_anchor_summary, dict) else {}
    )
    signal_values_by_family = feature_anchor_summary.get("signal_values_by_family")
    if not isinstance(signal_values_by_family, dict):
        return {}
    family_key = str(family_id or "").strip()
    family_values = signal_values_by_family.get(family_key)
    if not isinstance(family_values, dict):
        return {}
    return {
        str(key): value
        for key, value in family_values.items()
        if isinstance(key, str) and str(key).strip()
    }

def _sanitize_anchor_signal_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_sanitize_anchor_signal_value(item) for item in value[:24]]
    if isinstance(value, dict):
        return {
            str(key): _sanitize_anchor_signal_value(item)
            for key, item in list(value.items())[:16]
            if isinstance(key, str) and str(key).strip()
        }
    return str(value)

def _geometry_anchor_overrides_from_execution_binding(
    binding: KernelBinding | None,
) -> dict[str, Any]:
    if binding is None or not _has_meaningful_geometry_summary(binding.geometry_summary):
        return {}
    geometry_summary = binding.geometry_summary
    overrides: dict[str, Any] = {}

    bbox = geometry_summary.get("bbox")
    if isinstance(bbox, list) and bbox:
        overrides["bbox"] = _sanitize_anchor_signal_value(bbox)
        numeric_bbox = [
            float(value)
            for value in bbox
            if isinstance(value, (int, float))
        ]
        if numeric_bbox:
            overrides["bbox_min_span"] = min(numeric_bbox)
            overrides["bbox_max_span"] = max(numeric_bbox)

    bbox_min = geometry_summary.get("bbox_min")
    if isinstance(bbox_min, list) and bbox_min:
        overrides["bbox_min"] = _sanitize_anchor_signal_value(bbox_min)

    bbox_max = geometry_summary.get("bbox_max")
    if isinstance(bbox_max, list) and bbox_max:
        overrides["bbox_max"] = _sanitize_anchor_signal_value(bbox_max)

    return overrides

def _has_meaningful_geometry_summary(summary: dict[str, Any] | None) -> bool:
    if not isinstance(summary, dict) or not summary:
        return False
    for key in ("bbox", "bbox_min", "bbox_max"):
        value = summary.get(key)
        if isinstance(value, list) and value:
            return True
    for key in ("solids", "faces", "edges", "volume"):
        value = summary.get(key)
        if isinstance(value, (int, float)) and float(value) > 0:
            return True
    return bool(summary.get("persisted")) and bool(summary.get("step_file"))

def _extract_geometry_summary(payload: dict[str, Any]) -> dict[str, Any]:
    snapshot = payload.get("snapshot")
    if isinstance(snapshot, dict):
        geometry = snapshot.get("geometry")
        if isinstance(geometry, dict):
            return {
                "solids": int(geometry.get("solids", 0) or 0),
                "faces": int(geometry.get("faces", 0) or 0),
                "edges": int(geometry.get("edges", 0) or 0),
                "volume": float(geometry.get("volume", 0.0) or 0.0),
                "bbox": geometry.get("bbox"),
                "bbox_min": geometry.get("bbox_min"),
                "bbox_max": geometry.get("bbox_max"),
                "step_file": payload.get("step_file"),
                "persisted": bool(payload.get("session_state_persisted", False)),
            }
    probe_summary = payload.get("probe_summary")
    if isinstance(probe_summary, dict) and probe_summary:
        return {
            "solids": int(probe_summary.get("solids", 0) or 0),
            "faces": int(probe_summary.get("faces", 0) or 0),
            "edges": int(probe_summary.get("edges", 0) or 0),
            "volume": float(probe_summary.get("volume", 0.0) or 0.0),
            "bbox": probe_summary.get("bbox"),
            "bbox_min": probe_summary.get("bbox_min"),
            "bbox_max": probe_summary.get("bbox_max"),
            "step_file": payload.get("step_file"),
            "persisted": bool(payload.get("session_state_persisted", False)),
        }
    return {
        "step_file": payload.get("step_file"),
        "persisted": bool(payload.get("session_state_persisted", False)),
    }

def _extract_feature_anchor_summary(payload: dict[str, Any]) -> dict[str, Any]:
    probes = payload.get("probes")
    if not isinstance(probes, list) or not probes:
        return {}
    successful_families: list[str] = []
    family_bindings: dict[str, str] = {}
    anchor_signal_keys_by_family: dict[str, list[str]] = {}
    signal_values_by_family: dict[str, dict[str, Any]] = {}
    grounding_blockers_by_family: dict[str, list[str]] = {}
    required_evidence_kinds_by_family: dict[str, list[str]] = {}
    for item in probes:
        if not isinstance(item, dict):
            continue
        family = str(item.get("family") or "").strip()
        if not family:
            continue
        if bool(item.get("success")) and family not in successful_families:
            successful_families.append(family)
        family_binding = str(item.get("family_binding") or "").strip()
        if family_binding:
            family_bindings[family] = family_binding
        required_evidence_kinds = [
            str(kind).strip()
            for kind in (item.get("required_evidence_kinds") or [])
            if isinstance(kind, str) and str(kind).strip()
        ]
        if required_evidence_kinds:
            required_evidence_kinds_by_family[family] = required_evidence_kinds
        grounding_blockers = [
            str(blocker).strip()
            for blocker in (item.get("grounding_blockers") or [])
            if isinstance(blocker, str) and str(blocker).strip()
        ]
        if grounding_blockers:
            grounding_blockers_by_family[family] = grounding_blockers
        signals = item.get("signals")
        if not isinstance(signals, dict):
            signals = {}
        anchor_keys = sorted(
            {
                str(key)
                for key in signals.keys()
                if isinstance(key, str)
                and any(
                    token in key.lower()
                    for token in (
                        "anchor",
                        "axis",
                        "bbox",
                        "center",
                        "diameter",
                        "host",
                        "plane",
                        "radius",
                        "span",
                    )
                )
            }
        )
        anchor_summary = item.get("anchor_summary")
        anchor_payload: dict[str, Any] = (
            {
                key: _sanitize_anchor_signal_value(signals.get(key))
                for key in anchor_keys
                if key in signals
            }
            if anchor_keys
            else {}
        )
        if isinstance(anchor_summary, dict) and anchor_summary:
            for key in (
                "expected_local_center_count",
                "realized_local_center_count",
                "bbox",
                "bbox_min",
                "bbox_max",
                "bbox_min_span",
                "bbox_max_span",
                "host_face",
            ):
                if key in anchor_summary and key not in anchor_payload:
                    anchor_payload[key] = _sanitize_anchor_signal_value(
                        anchor_summary.get(key)
                    )
            anchor_payload["anchor_summary"] = {
                str(key): _sanitize_anchor_signal_value(value)
                for key, value in anchor_summary.items()
                if isinstance(key, str)
            }
        if anchor_payload:
            if anchor_keys:
                anchor_signal_keys_by_family[family] = anchor_keys
            signal_values_by_family[family] = anchor_payload
    return {
        "probe_count": sum(1 for item in probes if isinstance(item, dict)),
        "successful_probe_count": len(successful_families),
        "successful_families": successful_families,
        "family_bindings": family_bindings,
        "anchor_signal_keys_by_family": anchor_signal_keys_by_family,
        "signal_values_by_family": signal_values_by_family,
        "grounding_blockers_by_family": grounding_blockers_by_family,
        "required_evidence_kinds_by_family": required_evidence_kinds_by_family,
    }

def _extract_validation_feature_anchor_summary(payload: dict[str, Any]) -> dict[str, Any]:
    taxonomy_records = [
        item
        for item in taxonomy_records_from_validation_payload(payload)
        if str(getattr(item, "completeness_relevance", "") or "core").strip().lower()
        != "diagnostic"
    ]
    checks_by_id: dict[str, dict[str, Any]] = {}
    for item in (payload.get("core_checks") or payload.get("checks") or []):
        if not isinstance(item, dict):
            continue
        check_id = str(item.get("check_id") or "").strip()
        if not check_id:
            continue
        checks_by_id[check_id] = item

    anchor_signal_keys_by_family: dict[str, list[str]] = {}
    signal_values_by_family: dict[str, dict[str, Any]] = {}

    for taxonomy in taxonomy_records:
        blocker_id = str(getattr(taxonomy, "blocker_id", "") or "").strip()
        if not blocker_id:
            continue
        check_payload = checks_by_id.get(blocker_id)
        if not isinstance(check_payload, dict):
            continue
        evidence = str(check_payload.get("evidence") or check_payload.get("message") or "").strip()
        if not evidence:
            continue
        raw_signals = _extract_structured_signals_from_evidence_text(evidence)
        if not raw_signals:
            continue
        for family_id in (
            family_id
            for family_id in getattr(taxonomy, "family_ids", [])
            if isinstance(family_id, str) and family_id.strip()
        ):
            family_signals = _normalize_validation_signals_for_family(
                family_id,
                raw_signals,
            )
            if not family_signals:
                continue
            existing = signal_values_by_family.setdefault(family_id, {})
            existing.update(family_signals)
            anchor_signal_keys_by_family[family_id] = sorted(existing.keys())

    if not signal_values_by_family:
        return {}
    return {
        "anchor_signal_keys_by_family": anchor_signal_keys_by_family,
        "signal_values_by_family": signal_values_by_family,
    }

def _extract_execute_probe_feature_anchor_summary(payload: dict[str, Any]) -> dict[str, Any]:
    probe_summary = payload.get("probe_summary")
    if not isinstance(probe_summary, dict):
        return {}
    signal_values_by_family = probe_summary.get("signal_values_by_family")
    anchor_signal_keys_by_family = probe_summary.get("anchor_signal_keys_by_family")
    if not isinstance(signal_values_by_family, dict):
        return {}
    normalized_signal_values: dict[str, dict[str, Any]] = {}
    normalized_anchor_keys: dict[str, list[str]] = {}
    for family_id, signals in signal_values_by_family.items():
        if not isinstance(family_id, str) or not family_id.strip() or not isinstance(signals, dict):
            continue
        family_key = family_id.strip()
        normalized_signal_values[family_key] = {
            str(key): _sanitize_anchor_signal_value(value)
            for key, value in signals.items()
            if isinstance(key, str) and str(key).strip()
        }
        family_keys = (
            anchor_signal_keys_by_family.get(family_key)
            if isinstance(anchor_signal_keys_by_family, dict)
            else None
        )
        if isinstance(family_keys, list):
            normalized_anchor_keys[family_key] = [
                str(item).strip()
                for item in family_keys
                if isinstance(item, str) and str(item).strip()
            ]
        else:
            normalized_anchor_keys[family_key] = sorted(normalized_signal_values[family_key].keys())
    if not normalized_signal_values:
        return {}
    return {
        "anchor_signal_keys_by_family": normalized_anchor_keys,
        "signal_values_by_family": normalized_signal_values,
    }

def _classify_failed_execute_build123d_binding(
    *,
    payload: dict[str, Any],
    feature_family_ids: list[str],
) -> dict[str, Any]:
    if bool(payload.get("success", True)):
        return {}
    combined = "\n".join(
        part.strip().lower()
        for part in (
            payload.get("stderr"),
            payload.get("stdout"),
            payload.get("error"),
            payload.get("error_message"),
        )
        if isinstance(part, str) and part.strip()
    )
    if not combined:
        return {}
    if "path_sweep" in feature_family_ids:
        if (
            "disconnectedwire" in combined
            or "brepbuilderapi_disconnectedwire" in combined
            or (
                "unexpected keyword argument 'startangle'" in combined
                and "makecircle" in combined
            )
            or ("gc_makearcofcircle::value() - no result" in combined and "makethreepointarc" in combined)
        ):
            return {
                "family_ids": ["path_sweep"],
                "blocker_ids": ["feature_path_sweep_rail"],
                "primary_feature_ids": ["feature.path_sweep"],
                "recommended_repair_lane": "subtree_rebuild",
            }
        if "zero norm" in combined and "plane" in combined:
            return {
                "family_ids": ["path_sweep"],
                "blocker_ids": ["feature_path_sweep_frame"],
                "primary_feature_ids": ["feature.path_sweep"],
                "recommended_repair_lane": "subtree_rebuild",
            }
        if "no pending wires present" in combined or "closed profile" in combined:
            return {
                "family_ids": ["path_sweep"],
                "blocker_ids": ["feature_path_sweep_profile"],
                "primary_feature_ids": ["feature.path_sweep"],
                "recommended_repair_lane": "subtree_rebuild",
            }
    return {}

def _extract_structured_signals_from_evidence_text(evidence: str) -> dict[str, Any]:
    if not evidence:
        return {}
    parsed: dict[str, Any] = {}
    index = 0
    length = len(evidence)
    key_pattern = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
    while index < length:
        while index < length and evidence[index] in {" ", "\t", "\n", ","}:
            index += 1
        if index >= length:
            break
        key_match = key_pattern.match(evidence, index)
        if key_match is None:
            index += 1
            continue
        key = str(key_match.group(0) or "").strip()
        cursor = key_match.end()
        while cursor < length and evidence[cursor].isspace():
            cursor += 1
        if cursor >= length or evidence[cursor] != "=":
            index = key_match.end()
            continue
        cursor += 1
        value_start = cursor
        bracket_depth = 0
        active_quote: str | None = None
        while cursor < length:
            char = evidence[cursor]
            if active_quote is not None:
                if char == active_quote and evidence[cursor - 1] != "\\":
                    active_quote = None
                cursor += 1
                continue
            if char in {"'", '"'}:
                active_quote = char
                cursor += 1
                continue
            if char in {"[", "{", "("}:
                bracket_depth += 1
            elif char in {"]", "}", ")"} and bracket_depth > 0:
                bracket_depth -= 1
            elif char == "," and bracket_depth == 0:
                lookahead = cursor + 1
                while lookahead < length and evidence[lookahead].isspace():
                    lookahead += 1
                next_key = key_pattern.match(evidence, lookahead)
                if next_key is not None:
                    after_key = next_key.end()
                    while after_key < length and evidence[after_key].isspace():
                        after_key += 1
                    if after_key < length and evidence[after_key] == "=":
                        break
            cursor += 1
        raw_value = str(evidence[value_start:cursor]).strip()
        if key and raw_value:
            parsed[key] = _parse_structured_signal_value(raw_value)
        index = cursor + 1 if cursor < length and evidence[cursor] == "," else cursor
    return parsed

def _parse_structured_signal_value(raw_value: str) -> Any:
    value = raw_value.strip()
    if not value:
        return value
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return value
    return value

def _normalize_validation_signals_for_family(
    family_id: str,
    raw_signals: dict[str, Any],
) -> dict[str, Any]:
    signals = {
        str(key): _sanitize_anchor_signal_value(value)
        for key, value in raw_signals.items()
        if isinstance(key, str) and str(key).strip()
    }
    if family_id == "explicit_anchor_hole":
        normalized: dict[str, Any] = {}
        if "required_centers" in signals:
            normalized["expected_local_centers"] = signals["required_centers"]
        required_center_count = signals.get("required_center_count")
        if isinstance(required_center_count, (int, float)):
            normalized["expected_local_center_count"] = int(required_center_count)
        elif isinstance(signals.get("expected_local_center_count"), (int, float)):
            normalized["expected_local_center_count"] = int(
                signals["expected_local_center_count"]
            )
        if "realized_centers" in signals:
            normalized["realized_centers"] = signals["realized_centers"]
        elif "actual_snapshot_centers" in signals:
            normalized["realized_centers"] = signals["actual_snapshot_centers"]
        realized_center_count = signals.get("realized_center_count")
        if isinstance(realized_center_count, (int, float)):
            normalized["realized_center_count"] = int(realized_center_count)
        if "host_face" in signals:
            normalized["host_face"] = signals["host_face"]
        if "bbox" in signals:
            normalized["bbox"] = signals["bbox"]
        return normalized
    if family_id == "spherical_recess":
        normalized = {}
        if "required_centers" in signals:
            normalized["expected_local_centers"] = signals["required_centers"]
        if "realized_centers" in signals:
            normalized["realized_centers"] = signals["realized_centers"]
        elif "actual_snapshot_centers" in signals:
            normalized["realized_centers"] = signals["actual_snapshot_centers"]
        if "host_face" in signals:
            normalized["host_face"] = signals["host_face"]
        if "bbox" in signals:
            normalized["bbox"] = signals["bbox"]
        if "required_shapes" in signals:
            normalized["required_shapes"] = signals["required_shapes"]
        return normalized
    if family_id == "named_face_local_edit":
        return {
            key: value
            for key, value in signals.items()
            if key
            not in {
                "required_center_count",
                "realized_center_count",
                "required_centers",
                "realized_centers",
                "actual_snapshot_centers",
                "countersink_action",
                "hole_feature",
                "cone_like_face_present",
                "snapshot_countersink_geometry",
            }
        }
    return signals

def _merge_feature_anchor_summaries(
    preferred: dict[str, Any],
    fallback: dict[str, Any],
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for key in ("probe_count", "successful_probe_count", "successful_families"):
        if key in preferred:
            merged[key] = preferred[key]
        elif key in fallback:
            merged[key] = fallback[key]

    merged_keys_by_family: dict[str, list[str]] = {}
    merged_values_by_family: dict[str, dict[str, Any]] = {}
    for source in (fallback, preferred):
        keys_by_family = source.get("anchor_signal_keys_by_family")
        if isinstance(keys_by_family, dict):
            for family_id, keys in keys_by_family.items():
                if not isinstance(family_id, str) or not family_id.strip() or not isinstance(keys, list):
                    continue
                merged_keys_by_family.setdefault(family_id, [])
                merged_keys_by_family[family_id].extend(
                    str(item).strip()
                    for item in keys
                    if isinstance(item, str) and str(item).strip()
                )
        values_by_family = source.get("signal_values_by_family")
        if isinstance(values_by_family, dict):
            for family_id, values in values_by_family.items():
                if not isinstance(family_id, str) or not family_id.strip() or not isinstance(values, dict):
                    continue
                merged_values_by_family.setdefault(family_id, {})
                merged_values_by_family[family_id].update(values)
    if merged_keys_by_family:
        merged["anchor_signal_keys_by_family"] = {
            family_id: sorted(dict.fromkeys(keys))
            for family_id, keys in merged_keys_by_family.items()
        }
    if merged_values_by_family:
        merged["signal_values_by_family"] = merged_values_by_family
    return merged

def _contextualize_feature_anchor_summary_for_graph(
    *,
    graph: DomainKernelState,
    feature_anchor_summary: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(feature_anchor_summary, dict) or not feature_anchor_summary:
        return feature_anchor_summary
    requirement_feature_ids = {
        node.node_id
        for node in graph.nodes.values()
        if node.kind == "feature"
        and isinstance(node.node_id, str)
        and node.node_id.startswith("feature.")
    }
    if "feature.spherical_recess" not in requirement_feature_ids:
        return feature_anchor_summary
    signal_values_by_family = feature_anchor_summary.get("signal_values_by_family")
    anchor_signal_keys_by_family = feature_anchor_summary.get("anchor_signal_keys_by_family")
    if not isinstance(signal_values_by_family, dict):
        return feature_anchor_summary
    signal_values = {
        str(family_id): dict(values)
        for family_id, values in signal_values_by_family.items()
        if isinstance(family_id, str) and family_id.strip() and isinstance(values, dict)
    }
    anchor_keys = {
        str(family_id): [
            str(item).strip()
            for item in keys
            if isinstance(item, str) and str(item).strip()
        ]
        for family_id, keys in (anchor_signal_keys_by_family or {}).items()
        if isinstance(family_id, str) and family_id.strip() and isinstance(keys, list)
    }
    spherical_signals = dict(signal_values.get("spherical_recess") or {})
    explicit_signals = signal_values.get("explicit_anchor_hole")
    if isinstance(explicit_signals, dict):
        for key in ("expected_local_centers", "realized_centers", "host_face", "bbox"):
            if key in explicit_signals and key not in spherical_signals:
                spherical_signals[key] = explicit_signals[key]
    general_signals = signal_values.get("general_geometry")
    if isinstance(general_signals, dict):
        for key in (
            "required_shapes",
            "observed_post_solid_shapes",
            "observed_snapshot_profile_shapes",
            "missing_post_solid_profile_window",
            "execute_build123d_geometry_fallback",
        ):
            if key in general_signals and key not in spherical_signals:
                spherical_signals[key] = general_signals[key]
    if not spherical_signals:
        return feature_anchor_summary
    signal_values["spherical_recess"] = spherical_signals
    anchor_keys["spherical_recess"] = sorted(spherical_signals.keys())
    merged = dict(feature_anchor_summary)
    merged["signal_values_by_family"] = signal_values
    merged["anchor_signal_keys_by_family"] = anchor_keys
    return merged

def _latest_active_binding(
    graph: DomainKernelState,
    *,
    binding_kind: str | None = None,
    require_geometry: bool = False,
    require_feature_anchor: bool = False,
) -> KernelBinding | None:
    for binding in reversed(list(graph.bindings.values())):
        if binding.stale:
            continue
        if binding_kind is not None and binding.binding_kind != binding_kind:
            continue
        if require_geometry and not _has_meaningful_geometry_summary(binding.geometry_summary):
            continue
        if require_feature_anchor and not binding.feature_anchor_summary:
            continue
        return binding
    return None

def _latest_family_feature_anchor_binding(
    graph: DomainKernelState,
    *,
    family_id: str | None,
) -> KernelBinding | None:
    family_key = str(family_id or "").strip()
    fallback: KernelBinding | None = None
    for binding in reversed(list(graph.bindings.values())):
        if not binding.feature_anchor_summary:
            continue
        if family_key:
            family_signal_values = _family_signal_values_from_binding(
                binding,
                family_id=family_key,
            )
            if not family_signal_values and family_key not in binding.family_ids:
                continue
        if not binding.stale:
            return binding
        if fallback is None:
            fallback = binding
    return fallback

def _binding_from_tool_result(
    *,
    graph: DomainKernelState,
    tool_name: str,
    payload: dict[str, Any],
    round_no: int,
    active_node_ids: list[str],
) -> KernelBinding | None:
    feature_node_ids = [
        node.node_id
        for node in graph.nodes.values()
        if node.kind == "feature" and isinstance(node.node_id, str) and node.node_id.strip()
    ]
    feature_family_ids = list(
        dict.fromkeys(
            [
                node_id.replace("feature.", "").replace("feature:", "")
                for node_id in feature_node_ids
                if node_id.startswith("feature.")
                or node_id.startswith("feature:")
            ]
        )
    )
    if tool_name in {"apply_cad_action", "execute_build123d", "execute_repair_packet"}:
        binding_kind = "execution"
    elif tool_name == "validate_requirement":
        binding_kind = "validation"
    else:
        binding_kind = "observation"
    payload_digest = _stable_hash(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    )
    summary = None
    family_ids: list[str] = []
    blocker_ids: list[str] = []
    primary_feature_ids: list[str] = []
    evidence_source: str | None = None
    completeness_relevance: str | None = None
    severity: str | None = None
    recommended_repair_lane: str | None = None
    geometry_summary: dict[str, Any] = {}
    feature_anchor_summary: dict[str, Any] = {}
    for field in ("summary", "error", "error_message", "tool"):
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            summary = value.strip()
            break
    if summary is None:
        summary = f"{tool_name} round {round_no}"
    ordinal = (
        sum(
            1
            for current in graph.bindings.values()
            if current.source_tool == tool_name and current.round_no == round_no
        )
        + 1
    )
    if tool_name == "validate_requirement":
        validation_taxonomy = _validation_blocker_taxonomy(
            payload,
            graph=graph,
        )
        family_ids = [
            family_id
            for taxonomy in validation_taxonomy
            for family_id in (taxonomy.get("family_ids") or [])
            if isinstance(family_id, str) and family_id.strip()
        ]
        blocker_ids = [
            blocker_id
            for blocker_id in (
                taxonomy.get("blocker_id")
                for taxonomy in validation_taxonomy
                if isinstance(taxonomy, dict)
            )
            if isinstance(blocker_id, str) and blocker_id.strip()
        ]
        primary_feature_ids = [
            primary_feature_id
            for primary_feature_id in (
                taxonomy.get("primary_feature_id")
                for taxonomy in validation_taxonomy
                if isinstance(taxonomy, dict)
            )
            if isinstance(primary_feature_id, str) and primary_feature_id.strip()
        ]
        for taxonomy in validation_taxonomy:
            if evidence_source is None:
                lane_source = str(taxonomy.get("evidence_source") or "").strip()
                evidence_source = lane_source or "validation"
            if completeness_relevance is None:
                lane = str(taxonomy.get("completeness_relevance") or "").strip()
                completeness_relevance = lane or "core"
            if severity is None:
                severity_text = str(taxonomy.get("severity") or "").strip()
                severity = severity_text or "blocking"
            lane = str(taxonomy.get("recommended_repair_lane") or "").strip()
            if lane:
                recommended_repair_lane = (
                    _canonical_recommended_repair_lane(
                        lane,
                        family_ids=[
                            str(family_id).strip()
                            for family_id in (taxonomy.get("family_ids") or [])
                            if isinstance(family_id, str) and str(family_id).strip()
                        ],
                        primary_feature_id=str(
                            taxonomy.get("primary_feature_id") or ""
                        ).strip(),
                    )
                    or recommended_repair_lane
                )
                break
        if not validation_taxonomy and bool(payload.get("is_complete")):
            family_ids = list(feature_family_ids)
            primary_feature_ids = list(feature_node_ids)
            evidence_source = "validation"
            completeness_relevance = "core"
            severity = "informational"
            recommended_repair_lane = None
        latest_execution_binding = _latest_active_binding(
            graph,
            binding_kind="execution",
            require_geometry=True,
        )
        if latest_execution_binding is not None and not geometry_summary:
            geometry_summary = dict(latest_execution_binding.geometry_summary)
        validation_anchor_summary = _extract_validation_feature_anchor_summary(payload)
        latest_anchor_binding = _latest_active_binding(
            graph,
            require_feature_anchor=True,
        )
        if validation_anchor_summary:
            feature_anchor_summary = dict(validation_anchor_summary)
        if latest_anchor_binding is not None:
            feature_anchor_summary = _merge_feature_anchor_summaries(
                feature_anchor_summary,
                dict(latest_anchor_binding.feature_anchor_summary),
            )
        feature_anchor_summary = _contextualize_feature_anchor_summary_for_graph(
            graph=graph,
            feature_anchor_summary=feature_anchor_summary,
        )
    elif tool_name == "query_feature_probes":
        family_ids = [
            str(family_id).strip()
            for family_id in (payload.get("detected_families") or [])
            if isinstance(family_id, str) and str(family_id).strip()
        ]
        probe_blockers = [
            str(blocker_id).strip()
            for probe in (payload.get("probes") or [])
            if isinstance(probe, dict)
            for blocker_id in (probe.get("blockers") or [])
            if isinstance(blocker_id, str) and str(blocker_id).strip()
        ]
        probe_taxonomy = classify_blocker_taxonomy_many(
            probe_blockers,
            evidence_source="probe",
            completeness_relevance="diagnostic",
        )
        if probe_taxonomy:
            family_ids = list(
                dict.fromkeys(
                    [
                        *family_ids,
                        *[
                            family_id
                            for taxonomy in probe_taxonomy
                            for family_id in taxonomy.family_ids
                            if isinstance(family_id, str) and family_id.strip()
                        ],
                    ]
                )
            )
            blocker_ids = [
                taxonomy.blocker_id
                for taxonomy in probe_taxonomy
                if isinstance(taxonomy.blocker_id, str) and taxonomy.blocker_id.strip()
            ]
            primary_feature_ids = [
                taxonomy.primary_feature_id
                for taxonomy in probe_taxonomy
                if isinstance(taxonomy.primary_feature_id, str)
                and taxonomy.primary_feature_id.strip()
            ]
            for taxonomy in probe_taxonomy:
                lane = str(taxonomy.recommended_repair_lane or "").strip()
                if lane:
                    recommended_repair_lane = (
                        _canonical_recommended_repair_lane(
                            lane,
                            family_ids=list(taxonomy.family_ids),
                            primary_feature_id=taxonomy.primary_feature_id,
                        )
                        or recommended_repair_lane
                    )
                    break
        evidence_source = "probe"
        completeness_relevance = "diagnostic"
        severity = "diagnostic"
        feature_anchor_summary = _extract_feature_anchor_summary(payload)
    elif tool_name == "execute_build123d_probe":
        evidence_source = "probe"
        completeness_relevance = "diagnostic"
        severity = "diagnostic"
        geometry_summary = _extract_geometry_summary(payload)
        probe_summary = payload.get("probe_summary")
        if isinstance(probe_summary, dict):
            actionable_families = [
                str(family_id).strip()
                for family_id in (probe_summary.get("actionable_family_ids") or [])
                if isinstance(family_id, str) and str(family_id).strip()
            ]
            if actionable_families:
                family_ids = actionable_families
            lane = str(probe_summary.get("recommended_repair_lane") or "").strip()
            if lane:
                recommended_repair_lane = (
                    _canonical_recommended_repair_lane(
                        lane,
                        family_ids=list(family_ids),
                    )
                    or recommended_repair_lane
                )
            feature_anchor_summary = _extract_execute_probe_feature_anchor_summary(payload)
        if not family_ids:
            family_ids = list(feature_family_ids)
        if not primary_feature_ids:
            primary_feature_ids = [
                feature_id
                for feature_id in feature_node_ids
                if feature_id.replace("feature.", "").replace("feature:", "") in family_ids
            ] or list(feature_node_ids)
    elif tool_name in {"apply_cad_action", "execute_build123d", "execute_repair_packet"}:
        evidence_source = "execution"
        completeness_relevance = "runtime"
        severity = "informational"
        geometry_summary = _extract_geometry_summary(payload)
        failure_taxonomy = _classify_failed_execute_build123d_binding(
            payload=payload,
            feature_family_ids=feature_family_ids,
        )
        if failure_taxonomy:
            family_ids = list(failure_taxonomy.get("family_ids") or [])
            blocker_ids = list(failure_taxonomy.get("blocker_ids") or [])
            primary_feature_ids = list(failure_taxonomy.get("primary_feature_ids") or [])
            recommended_repair_lane = (
                _canonical_recommended_repair_lane(
                    str(failure_taxonomy.get("recommended_repair_lane") or "").strip(),
                    family_ids=list(family_ids),
                    primary_feature_id=primary_feature_ids[0] if primary_feature_ids else None,
                )
                or recommended_repair_lane
            )
            severity = "blocking"
        if not primary_feature_ids:
            primary_feature_ids = list(feature_node_ids)
        if not family_ids:
            family_ids = list(feature_family_ids)
    return KernelBinding(
        binding_id=f"{tool_name}:round_{round_no:02d}:{ordinal:02d}:{payload_digest[:10]}",
        binding_kind=binding_kind,
        source_tool=tool_name,
        round_no=round_no,
        summary=summary,
        payload_digest=payload_digest,
        node_ids=list(active_node_ids[:8]),
        family_ids=list(dict.fromkeys(family_ids)),
        blocker_ids=list(dict.fromkeys(blocker_ids)),
        primary_feature_ids=list(dict.fromkeys(primary_feature_ids)),
        evidence_source=evidence_source,
        completeness_relevance=completeness_relevance,
        severity=severity,
        recommended_repair_lane=recommended_repair_lane,
        geometry_summary=geometry_summary,
        feature_anchor_summary=feature_anchor_summary,
    )

def _summarize_geometry(geometry: dict[str, Any]) -> str:
    solids = int(geometry.get("solids", 0) or 0)
    volume = float(geometry.get("volume", 0.0) or 0.0)
    step_file = geometry.get("step_file")
    persisted = bool(geometry.get("persisted", False))
    return (
        f"solids={solids}, volume={volume:.3f}, "
        f"step_file={bool(step_file)}, persisted={persisted}"
    )

def _count_nodes_by_kind(nodes: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for node in nodes:
        kind = str(getattr(node, "kind", "unknown") or "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    return counts
