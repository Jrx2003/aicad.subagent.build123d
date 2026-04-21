from __future__ import annotations

import re
from typing import Any

from common.blocker_taxonomy import classify_blocker_taxonomy_many
from sub_agent_runtime.semantic_kernel.bootstrap import _default_feature_instance_id
from sub_agent_runtime.semantic_kernel.bindings import (
    _extract_geometry_summary,
    _family_signal_values_from_binding,
    _geometry_anchor_overrides_from_execution_binding,
    _has_meaningful_geometry_summary,
    _host_ids_from_binding,
    _anchor_keys_from_binding,
    _latest_active_binding,
    _latest_family_feature_anchor_binding,
    _sanitize_anchor_signal_value,
    _summarize_geometry,
)
from sub_agent_runtime.semantic_kernel.models import (
    DomainKernelPatch,
    DomainKernelState,
    FeatureGraphEdge,
    FeatureGraphNode,
    FeatureInstance,
    KernelBinding,
)
from sub_agent_runtime.semantic_kernel.taxonomy import (
    _blocker_to_feature_ids,
    _canonical_recommended_repair_lane,
    _feature_ids_from_taxonomy_record,
    _feature_node_family_ids,
    _validation_blockers,
    _validation_family_status_hints,
    _validation_uses_only_general_geometry_lane,
)

_GEOMETRY_DERIVED_PARAMETER_BINDING_KEYS = {
    "geometry_summary",
    "bbox",
    "bbox_min",
    "bbox_max",
    "bbox_min_span",
    "bbox_max_span",
    "realized_bbox",
    "realized_centers",
    "actual_snapshot_centers",
    "observed_bounds",
    "observed_spans",
    "observed_split_bounds",
}

def _sync_blocker_nodes(
    graph: DomainKernelState,
    blockers: list[str],
    *,
    blocker_taxonomy: list[dict[str, Any]] | None = None,
) -> None:
    active_blocker_ids = {
        f"blocker.{_slugify(blocker)}"
        for blocker in blockers
    }
    taxonomy_by_blocker = {
        item["blocker_id"]: item
        for item in (blocker_taxonomy or [])
        if isinstance(item, dict)
        and isinstance(item.get("blocker_id"), str)
        and str(item.get("blocker_id")).strip()
    }
    for node in graph.nodes.values():
        if node.kind == "blocker" and node.node_id not in active_blocker_ids:
            node.status = "resolved"
    for blocker in blockers:
        taxonomy_record = taxonomy_by_blocker.get(blocker)
        if isinstance(taxonomy_record, dict):
            blocker_family_ids = [
                family_id
                for family_id in (taxonomy_record.get("family_ids") or [])
                if isinstance(family_id, str) and family_id.strip()
            ]
            recommended_repair_lane = (
                _canonical_recommended_repair_lane(
                    taxonomy_record.get("recommended_repair_lane") or "code_repair",
                    family_ids=blocker_family_ids,
                    primary_feature_id=str(
                        taxonomy_record.get("primary_feature_id") or ""
                    ).strip(),
                )
                or "code_repair"
            )
            feature_ids = _feature_ids_from_taxonomy_record(taxonomy_record)
        else:
            blocker_taxonomy_fallback = classify_blocker_taxonomy_many([blocker])
            blocker_family_ids = (
                blocker_taxonomy_fallback[0].family_ids if blocker_taxonomy_fallback else []
            )
            recommended_repair_lane = (
                _canonical_recommended_repair_lane(
                    blocker_taxonomy_fallback[0].recommended_repair_lane
                    if blocker_taxonomy_fallback
                    else "code_repair",
                    family_ids=list(blocker_family_ids),
                    primary_feature_id=(
                        blocker_taxonomy_fallback[0].primary_feature_id
                        if blocker_taxonomy_fallback
                        else None
                    ),
                )
                or "code_repair"
            )
            feature_ids = _blocker_to_feature_ids(blocker)
        node_id = f"blocker.{_slugify(blocker)}"
        blocker_node = graph.nodes.get(node_id) or FeatureGraphNode(
            node_id=node_id,
            kind="blocker",
            label=blocker,
        )
        blocker_node.status = "blocked"
        blocker_node.summary = "Current unresolved blocker from validation/runtime evidence."
        blocker_node.attributes = {
            **blocker_node.attributes,
            "family_ids": blocker_family_ids,
            "recommended_repair_lane": recommended_repair_lane,
        }
        graph.upsert_node(blocker_node)
        for feature_id in feature_ids:
            if feature_id not in graph.nodes:
                graph.upsert_node(
                    FeatureGraphNode(
                        node_id=feature_id,
                        kind="feature",
                        label=feature_id.replace("feature.", "").replace("_", " "),
                        status="planned",
                        summary="Runtime-synthesized feature placeholder for conservative blocker routing.",
                    )
                )
            graph.upsert_edge(
                FeatureGraphEdge(
                    source_id=node_id,
                    target_id=feature_id,
                    relation="blocks",
                    summary="This blocker currently blocks the feature family.",
                )
            )

def _sync_evidence_nodes(
    graph: DomainKernelState,
    *,
    latest_write_payload: dict[str, Any] | None,
    latest_validation: dict[str, Any] | None,
    previous_error: str | None,
    evidence_by_tool: dict[str, dict[str, Any]],
    blocker_taxonomy: list[dict[str, Any]] | None = None,
) -> None:
    if isinstance(latest_write_payload, dict) and latest_write_payload:
        geometry = _extract_geometry_summary(latest_write_payload)
        graph.upsert_node(
            FeatureGraphNode(
                node_id="evidence.latest_write",
                kind="evidence",
                label="Latest write evidence",
                status="observed",
                summary=_summarize_geometry(geometry),
                attributes={
                    "tool": latest_write_payload.get("tool") or latest_write_payload.get("action_type"),
                    "geometry": geometry,
                },
                linked_evidence=["latest_write_payload"],
            )
        )
    if isinstance(latest_validation, dict) and latest_validation:
        validation_taxonomy = blocker_taxonomy or [
            {
                "blocker_id": item.blocker_id,
                "family_ids": item.family_ids,
                "feature_ids": item.feature_ids,
                "primary_feature_id": item.primary_feature_id,
                "recommended_repair_lane": item.recommended_repair_lane,
                "evidence_source": item.evidence_source,
                "completeness_relevance": item.completeness_relevance,
                "severity": item.severity,
            }
            for item in classify_blocker_taxonomy_many(_validation_blockers(latest_validation))
        ]
        graph.upsert_node(
            FeatureGraphNode(
                node_id="evidence.latest_validation",
                kind="evidence",
                label="Latest validation evidence",
                status="observed",
                summary=str(latest_validation.get("summary") or "validation updated"),
                attributes={
                    "is_complete": bool(latest_validation.get("is_complete")),
                    "blockers": _validation_blockers(latest_validation),
                    "blocker_taxonomy": validation_taxonomy,
                },
                linked_evidence=["validate_requirement"],
            )
        )
    if isinstance(previous_error, str) and previous_error.strip():
        graph.upsert_node(
            FeatureGraphNode(
                node_id="evidence.latest_error",
                kind="evidence",
                label="Latest runtime error",
                status="failed",
                summary=previous_error.strip()[:240],
                linked_evidence=["runtime_error"],
            )
        )
    feature_probe_payload = evidence_by_tool.get("query_feature_probes")
    if isinstance(feature_probe_payload, dict) and feature_probe_payload:
        graph.upsert_node(
            FeatureGraphNode(
                node_id="evidence.feature_probes",
                kind="evidence",
                label="Latest feature probes",
                status="observed",
                summary=str(feature_probe_payload.get("summary") or "feature probes updated"),
                attributes={
                    "detected_families": feature_probe_payload.get("detected_families") or [],
                },
                linked_evidence=["query_feature_probes"],
            )
        )

def _sync_feature_instances_and_patches(
    graph: DomainKernelState,
    *,
    blockers: list[str],
    blocker_taxonomy: list[dict[str, Any]] | None,
    latest_validation: dict[str, Any] | None,
    blocked_feature_ids: set[str] | None = None,
) -> None:
    from sub_agent_runtime.semantic_kernel.recipes import (
        _repair_priority_for_feature_instance,
        _replace_repair_packets_from_active_instances,
    )

    blocked_feature_ids = {
        feature_id
        for feature_id in (blocked_feature_ids or set())
        if isinstance(feature_id, str) and feature_id.strip()
    }
    validation_family_status_hints = _validation_family_status_hints(latest_validation)
    taxonomy_by_blocker = {
        item["blocker_id"]: item
        for item in (blocker_taxonomy or [])
        if isinstance(item, dict)
        and isinstance(item.get("blocker_id"), str)
        and str(item.get("blocker_id")).strip()
    }
    latest_execution_binding = _latest_active_binding(
        graph,
        binding_kind="execution",
        require_geometry=True,
    )
    if _validation_uses_only_general_geometry_lane(
        blockers=blockers,
        taxonomy_by_blocker=taxonomy_by_blocker,
    ):
        binding_taxonomy = _taxonomy_records_from_latest_specific_binding(graph)
        if binding_taxonomy:
            blockers = [
                str(item.get("blocker_id")).strip()
                for item in binding_taxonomy
                if isinstance(item.get("blocker_id"), str) and str(item.get("blocker_id")).strip()
            ]
            taxonomy_by_blocker = {
                item["blocker_id"]: item
                for item in binding_taxonomy
                if isinstance(item, dict)
                and isinstance(item.get("blocker_id"), str)
                and str(item.get("blocker_id")).strip()
            }

    if isinstance(latest_validation, dict) and bool(latest_validation.get("is_complete")):
        for feature_instance in graph.feature_instances.values():
            feature_instance.status = "satisfied"
            feature_instance.blocker_ids = []
            feature_instance.latest_repair_mode = None
        for patch in graph.repair_patches.values():
            patch.stale = True
        return

    current_blocker_ids = {
        blocker_id
        for blocker_id in blockers
        if isinstance(blocker_id, str) and blocker_id.strip()
    }
    for feature_instance in graph.feature_instances.values():
        family_statuses = validation_family_status_hints.get(feature_instance.family_id, set())
        if feature_instance.blocker_ids:
            remaining_blockers = [
                blocker_id
                for blocker_id in feature_instance.blocker_ids
                if blocker_id in current_blocker_ids
            ]
            if remaining_blockers:
                feature_instance.blocker_ids = remaining_blockers
                continue
            feature_instance.status = "resolved"
            feature_instance.blocker_ids = []
            feature_instance.latest_repair_mode = None
            feature_instance.repair_intent = None
            continue
        if "contradicted" in family_statuses:
            feature_instance.status = "blocked"
            continue
        if (
            "insufficient_evidence" in family_statuses
            or feature_instance.primary_feature_id in blocked_feature_ids
        ):
            if feature_instance.status in {"resolved", "satisfied", "blocked"}:
                feature_instance.status = "active"
            continue
        if latest_validation is not None and feature_instance.status in {"active", "blocked", "observed"}:
            feature_instance.status = "resolved"
            feature_instance.blocker_ids = []
            feature_instance.latest_repair_mode = None
            feature_instance.repair_intent = None

    _refresh_active_general_geometry_instances(
        graph,
        latest_execution_binding=latest_execution_binding,
    )

    active_instance_ids: list[str] = []
    patch_blocker_ids: list[str] = []
    patch_binding_ids: list[str] = []
    active_instances: list[FeatureInstance] = []
    for blocker in blockers:
        taxonomy_record = taxonomy_by_blocker.get(blocker)
        if not isinstance(taxonomy_record, dict):
            fallback = classify_blocker_taxonomy_many([blocker])
            if fallback:
                taxonomy_item = fallback[0]
                taxonomy_record = {
                    "blocker_id": taxonomy_item.blocker_id,
                    "family_ids": taxonomy_item.family_ids,
                    "feature_ids": taxonomy_item.feature_ids,
                    "primary_feature_id": taxonomy_item.primary_feature_id,
                    "recommended_repair_lane": taxonomy_item.recommended_repair_lane,
                }
        family_ids = [
            str(item).strip()
            for item in ((taxonomy_record or {}).get("family_ids") or [])
            if isinstance(item, str) and str(item).strip()
        ]
        latest_anchor_binding = _latest_family_feature_anchor_binding(
            graph,
            family_id=family_ids[0] if family_ids else None,
        )
        feature_instance = _feature_instance_from_taxonomy_record(
            taxonomy_record=taxonomy_record,
            latest_anchor_binding=latest_anchor_binding,
            latest_execution_binding=latest_execution_binding,
        )
        feature_instance = _merge_feature_instance(
            graph.feature_instances.get(feature_instance.instance_id),
            feature_instance,
        )
        graph.upsert_feature_instance(feature_instance)
        active_instance_ids.append(feature_instance.instance_id)
        patch_blocker_ids.extend(feature_instance.blocker_ids)
        patch_binding_ids.extend(feature_instance.linked_binding_ids)
        active_instances.append(feature_instance)

    _refresh_feature_instances_with_latest_execution_geometry(
        graph,
        latest_execution_binding=latest_execution_binding,
    )

    if not active_instance_ids:
        return

    active_instances = sorted(
        active_instances,
        key=_repair_priority_for_feature_instance,
    )
    active_instance_ids = [feature_instance.instance_id for feature_instance in active_instances]
    repair_modes = [
        graph.feature_instances[instance_id].latest_repair_mode or "whole_part_rebuild"
        for instance_id in active_instance_ids
        if instance_id in graph.feature_instances
    ]
    affected_host_ids = list(
        dict.fromkeys(
            host_id
            for instance_id in active_instance_ids
            for host_id in graph.feature_instances[instance_id].host_ids
            if host_id
        )
    ) or ["body.primary"]
    anchor_keys = list(
        dict.fromkeys(
            anchor_key
            for instance_id in active_instance_ids
            for anchor_key in graph.feature_instances[instance_id].anchor_keys
            if anchor_key
        )
    )
    parameter_keys = list(
        dict.fromkeys(
            key
            for feature_instance in active_instances
            for key in feature_instance.parameter_bindings.keys()
            if key and key != "geometry_summary"
        )
    )
    graph.record_repair_patch(
        DomainKernelPatch(
            patch_id=(
                f"kernel_patch.r{graph.revision + 1:02d}."
                f"{_slugify('-'.join(patch_blocker_ids) or '-'.join(active_instance_ids) or 'repair')}"
            ),
            repair_mode=_aggregate_repair_mode(repair_modes),
            reason="Repair latest blocked feature instances before another broad retry.",
            feature_instance_ids=list(dict.fromkeys(active_instance_ids)),
            affected_host_ids=affected_host_ids,
            anchor_keys=anchor_keys,
            parameter_keys=parameter_keys,
            source_binding_id=patch_binding_ids[-1] if patch_binding_ids else None,
            source_blocker_ids=list(dict.fromkeys(patch_blocker_ids)),
            repair_intent=_aggregate_repair_intent(active_instances),
        )
    )
    _replace_repair_packets_from_active_instances(graph, active_instances)

def _taxonomy_records_from_latest_specific_binding(
    graph: DomainKernelState,
) -> list[dict[str, Any]]:
    binding = _latest_specific_binding_with_blockers(graph)
    if binding is None:
        return []
    family_ids = [
        family_id
        for family_id in binding.family_ids
        if isinstance(family_id, str) and family_id.strip() and family_id != "general_geometry"
    ]
    primary_feature_ids = [
        feature_id
        for feature_id in binding.primary_feature_ids
        if isinstance(feature_id, str)
        and feature_id.strip()
        and feature_id != "feature.core_geometry"
    ]
    if not family_ids:
        return []
    if not primary_feature_ids:
        primary_feature_ids = [f"feature.{family_ids[0]}"]
    records: list[dict[str, Any]] = []
    for blocker_id in binding.blocker_ids:
        if not isinstance(blocker_id, str) or not blocker_id.strip():
            continue
        records.append(
            {
                "blocker_id": blocker_id,
                "family_ids": family_ids,
                "feature_ids": primary_feature_ids,
                "primary_feature_id": primary_feature_ids[0],
                "recommended_repair_lane": binding.recommended_repair_lane or "code_repair",
                "evidence_source": binding.evidence_source or "execution",
                "completeness_relevance": binding.completeness_relevance or "runtime",
                "severity": binding.severity or "blocking",
            }
        )
    return records

def _latest_specific_binding_with_blockers(
    graph: DomainKernelState,
) -> KernelBinding | None:
    for binding in reversed(list(graph.bindings.values())):
        if binding.stale or not binding.blocker_ids:
            continue
        family_ids = [
            family_id
            for family_id in binding.family_ids
            if isinstance(family_id, str) and family_id.strip()
        ]
        if not family_ids:
            continue
        if all(family_id == "general_geometry" for family_id in family_ids):
            continue
        return binding
    return None

def _feature_instance_from_taxonomy_record(
    *,
    taxonomy_record: dict[str, Any] | None,
    latest_anchor_binding: KernelBinding | None,
    latest_execution_binding: KernelBinding | None,
) -> FeatureInstance:
    blocker_id = str((taxonomy_record or {}).get("blocker_id") or "").strip()
    family_ids = [
        str(item).strip()
        for item in ((taxonomy_record or {}).get("family_ids") or [])
        if isinstance(item, str) and str(item).strip()
    ]
    family_id = family_ids[0] if family_ids else "general_geometry"
    primary_feature_id = str(
        (taxonomy_record or {}).get("primary_feature_id") or "feature.core_geometry"
    ).strip() or "feature.core_geometry"
    parameter_bindings: dict[str, Any] = {}
    anchor_signal_values = _family_signal_values_from_binding(
        latest_anchor_binding,
        family_id=family_id,
    )
    for key, value in anchor_signal_values.items():
        if key in {"host_face", "host_faces"}:
            continue
        parameter_bindings[key] = _sanitize_anchor_signal_value(value)
    if (
        latest_execution_binding is not None
        and _has_meaningful_geometry_summary(latest_execution_binding.geometry_summary)
    ):
        parameter_bindings["geometry_summary"] = dict(latest_execution_binding.geometry_summary)
    geometry_anchor_overrides = _geometry_anchor_overrides_from_execution_binding(
        latest_execution_binding
    )
    if geometry_anchor_overrides:
        anchor_summary = (
            dict(parameter_bindings.get("anchor_summary"))
            if isinstance(parameter_bindings.get("anchor_summary"), dict)
            else {}
        )
        for key, value in geometry_anchor_overrides.items():
            parameter_bindings[key] = value
            anchor_summary[key] = value
        if anchor_summary:
            parameter_bindings["anchor_summary"] = anchor_summary
    linked_binding_ids: list[str] = []
    if latest_anchor_binding is not None and latest_anchor_binding.binding_id:
        linked_binding_ids.append(latest_anchor_binding.binding_id)
    if latest_execution_binding is not None and latest_execution_binding.binding_id:
        linked_binding_ids.append(latest_execution_binding.binding_id)
    repair_intent = _repair_intent_for_feature_instance(
        family_id=family_id,
        blocker_id=blocker_id,
        parameter_bindings=parameter_bindings,
    )
    return FeatureInstance(
        instance_id=_feature_instance_id_from_taxonomy_record(taxonomy_record),
        family_id=family_id,
        primary_feature_id=primary_feature_id,
        label=primary_feature_id.replace("feature.", "").replace("_", " "),
        status="blocked",
        summary=blocker_id or f"{family_id} requires repair",
        host_ids=_host_ids_from_binding(latest_anchor_binding, family_id=family_id) or ["body.primary"],
        blocker_ids=[blocker_id] if blocker_id else [],
        anchor_keys=_anchor_keys_from_binding(latest_anchor_binding, family_id=family_id),
        parameter_bindings=parameter_bindings,
        linked_binding_ids=linked_binding_ids,
        latest_repair_mode=_repair_mode_from_taxonomy_record(taxonomy_record),
        repair_intent=repair_intent,
    )

def _merge_feature_instance(
    existing: FeatureInstance | None,
    incoming: FeatureInstance,
) -> FeatureInstance:
    if existing is None:
        return incoming

    parameter_bindings = _merge_feature_parameter_bindings(
        existing.parameter_bindings,
        incoming.parameter_bindings,
    )

    host_ids = list(dict.fromkeys([*incoming.host_ids, *existing.host_ids]))
    if any(host_id != "body.primary" for host_id in host_ids):
        host_ids = [host_id for host_id in host_ids if host_id != "body.primary"]

    anchor_keys = list(dict.fromkeys([*incoming.anchor_keys, *existing.anchor_keys]))
    linked_binding_ids = list(
        dict.fromkeys([*incoming.linked_binding_ids, *existing.linked_binding_ids])
    )
    blocker_ids = list(dict.fromkeys([*incoming.blocker_ids, *existing.blocker_ids]))

    return FeatureInstance(
        instance_id=incoming.instance_id,
        family_id=incoming.family_id or existing.family_id,
        primary_feature_id=incoming.primary_feature_id or existing.primary_feature_id,
        label=incoming.label or existing.label,
        status=incoming.status or existing.status,
        summary=incoming.summary or existing.summary,
        host_ids=host_ids,
        blocker_ids=blocker_ids,
        anchor_keys=anchor_keys,
        parameter_bindings=parameter_bindings,
        linked_binding_ids=linked_binding_ids,
        latest_repair_mode=incoming.latest_repair_mode or existing.latest_repair_mode,
        repair_intent=incoming.repair_intent or existing.repair_intent,
    )

def _merge_feature_parameter_bindings(
    existing: dict[str, Any],
    incoming: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(existing)
    incoming_bindings = dict(incoming)
    if _parameter_bindings_include_fresh_geometry(incoming_bindings):
        _drop_stale_geometry_derived_binding_keys(merged)
        existing_anchor_summary = merged.get("anchor_summary")
        if isinstance(existing_anchor_summary, dict):
            refreshed_anchor_summary = dict(existing_anchor_summary)
            _drop_stale_geometry_derived_binding_keys(refreshed_anchor_summary)
            if refreshed_anchor_summary:
                merged["anchor_summary"] = refreshed_anchor_summary
            else:
                merged.pop("anchor_summary", None)
    incoming_anchor_summary = incoming_bindings.get("anchor_summary")
    existing_anchor_summary = merged.get("anchor_summary")
    if isinstance(existing_anchor_summary, dict) and isinstance(incoming_anchor_summary, dict):
        merged_anchor_summary = dict(existing_anchor_summary)
        merged_anchor_summary.update(incoming_anchor_summary)
        merged["anchor_summary"] = merged_anchor_summary
        incoming_bindings.pop("anchor_summary", None)
    merged.update(incoming_bindings)
    return merged

def _refresh_feature_instances_with_latest_execution_geometry(
    graph: DomainKernelState,
    *,
    latest_execution_binding: KernelBinding | None,
) -> None:
    execution_geometry_bindings = _fresh_geometry_parameter_bindings_from_execution_binding(
        latest_execution_binding
    )
    if not execution_geometry_bindings:
        return
    latest_binding_id = (
        latest_execution_binding.binding_id
        if latest_execution_binding is not None and latest_execution_binding.binding_id
        else None
    )
    for feature_instance in graph.feature_instances.values():
        if feature_instance.status in {"resolved", "satisfied"}:
            continue
        if not _feature_instance_tracks_geometry(feature_instance):
            continue
        feature_instance.parameter_bindings = _merge_feature_parameter_bindings(
            feature_instance.parameter_bindings,
            execution_geometry_bindings,
        )
        if latest_binding_id:
            feature_instance.linked_binding_ids = list(
                dict.fromkeys([latest_binding_id, *feature_instance.linked_binding_ids])
            )

def _fresh_geometry_parameter_bindings_from_execution_binding(
    binding: KernelBinding | None,
) -> dict[str, Any]:
    if binding is None or not _has_meaningful_geometry_summary(binding.geometry_summary):
        return {}
    refreshed_bindings: dict[str, Any] = {
        "geometry_summary": dict(binding.geometry_summary),
    }
    geometry_anchor_overrides = _geometry_anchor_overrides_from_execution_binding(binding)
    if geometry_anchor_overrides:
        refreshed_bindings.update(geometry_anchor_overrides)
        refreshed_bindings["anchor_summary"] = dict(geometry_anchor_overrides)
    return refreshed_bindings

def _feature_instance_tracks_geometry(feature_instance: FeatureInstance) -> bool:
    if any(
        key in _GEOMETRY_DERIVED_PARAMETER_BINDING_KEYS
        for key in feature_instance.parameter_bindings.keys()
    ):
        return True
    if isinstance(feature_instance.parameter_bindings.get("anchor_summary"), dict):
        return True
    return any(
        key == "anchor_summary" or key in _GEOMETRY_DERIVED_PARAMETER_BINDING_KEYS
        for key in feature_instance.anchor_keys
    )

def _parameter_bindings_include_fresh_geometry(bindings: dict[str, Any]) -> bool:
    geometry_summary = bindings.get("geometry_summary")
    if isinstance(geometry_summary, dict) and _has_meaningful_geometry_summary(geometry_summary):
        return True
    strong_geometry_keys = {
        "bbox",
        "bbox_min",
        "bbox_max",
        "realized_bbox",
        "realized_centers",
        "actual_snapshot_centers",
        "observed_bounds",
        "observed_spans",
        "observed_split_bounds",
    }
    return any(key in bindings for key in strong_geometry_keys)

def _drop_stale_geometry_derived_binding_keys(bindings: dict[str, Any]) -> None:
    for key in list(bindings.keys()):
        if key in _GEOMETRY_DERIVED_PARAMETER_BINDING_KEYS:
            bindings.pop(key, None)

def _refresh_active_general_geometry_instances(
    graph: DomainKernelState,
    *,
    latest_execution_binding: KernelBinding | None,
) -> None:
    if latest_execution_binding is None or not _has_meaningful_geometry_summary(
        latest_execution_binding.geometry_summary
    ):
        return

    incoming_bindings: dict[str, Any] = {
        "geometry_summary": dict(latest_execution_binding.geometry_summary)
    }
    geometry_anchor_overrides = _geometry_anchor_overrides_from_execution_binding(
        latest_execution_binding
    )
    if geometry_anchor_overrides:
        incoming_bindings.update(geometry_anchor_overrides)
        incoming_bindings["anchor_summary"] = dict(geometry_anchor_overrides)

    if not incoming_bindings:
        return

    for feature_instance in graph.feature_instances.values():
        if feature_instance.family_id != "general_geometry":
            continue
        if feature_instance.status not in {"active", "blocked", "observed"}:
            continue
        feature_instance.parameter_bindings = _merge_feature_parameter_bindings(
            feature_instance.parameter_bindings,
            incoming_bindings,
        )
        incoming_anchor_keys = [
            key
            for key in geometry_anchor_overrides.keys()
            if isinstance(key, str) and key.strip()
        ]
        if incoming_anchor_keys:
            feature_instance.anchor_keys = list(
                dict.fromkeys([*feature_instance.anchor_keys, *incoming_anchor_keys])
            )
        if (
            latest_execution_binding.binding_id
            and latest_execution_binding.binding_id not in feature_instance.linked_binding_ids
        ):
            feature_instance.linked_binding_ids.append(latest_execution_binding.binding_id)

def _feature_instance_id_from_taxonomy_record(taxonomy_record: dict[str, Any] | None) -> str:
    blocker_id = str((taxonomy_record or {}).get("blocker_id") or "").strip()
    primary_feature_id = str((taxonomy_record or {}).get("primary_feature_id") or "").strip()
    family_ids = [
        str(item).strip()
        for item in ((taxonomy_record or {}).get("family_ids") or [])
        if isinstance(item, str) and str(item).strip()
    ]
    if blocker_id and family_ids:
        return f"instance.{_slugify(family_ids[0])}.{_slugify(blocker_id)}"
    if primary_feature_id:
        return _default_feature_instance_id(primary_feature_id)
    if family_ids:
        return f"instance.{_slugify(family_ids[0])}.primary"
    return "instance.general_geometry.primary"

def _repair_mode_from_taxonomy_record(taxonomy_record: dict[str, Any] | None) -> str:
    lane = str((taxonomy_record or {}).get("recommended_repair_lane") or "").strip()
    family_ids = [
        str(item).strip()
        for item in ((taxonomy_record or {}).get("family_ids") or [])
        if isinstance(item, str) and str(item).strip()
    ]
    primary_feature_id = str((taxonomy_record or {}).get("primary_feature_id") or "").strip()
    if lane == "local_finish":
        return "local_edit"
    if lane == "probe_first":
        return "subtree_rebuild"
    if "named_face_local_edit" in family_ids and primary_feature_id == "feature.named_face_local_edit":
        return "local_edit"
    if not family_ids or "general_geometry" in family_ids or primary_feature_id == "feature.core_geometry":
        return "whole_part_rebuild"
    return "subtree_rebuild"

def _aggregate_repair_mode(repair_modes: list[str]) -> str:
    if not repair_modes:
        return "whole_part_rebuild"
    if all(mode == "local_edit" for mode in repair_modes):
        return "local_edit"
    if any(mode == "whole_part_rebuild" for mode in repair_modes):
        return "whole_part_rebuild"
    return "subtree_rebuild"

def _repair_intent_for_feature_instance(
    *,
    family_id: str,
    blocker_id: str,
    parameter_bindings: dict[str, Any],
) -> str | None:
    if family_id == "explicit_anchor_hole":
        if (
            "expected_local_centers" in parameter_bindings
            and "realized_centers" in parameter_bindings
        ):
            return "realign_local_feature_centers"
        if "expected_local_centers" in parameter_bindings:
            return "create_missing_local_feature_centers"
        if blocker_id == "feature_countersink":
            return "restore_explicit_anchor_countersink"
    if family_id == "axisymmetric_profile":
        if blocker_id == "feature_half_shell_profile_envelope":
            return "rebuild_half_shell_profile_envelope"
        if blocker_id == "feature_named_plane_positive_extrude_span":
            return "restore_positive_plane_anchored_span"
    if family_id == "spherical_recess":
        if (
            "expected_local_centers" in parameter_bindings
            and "realized_centers" in parameter_bindings
        ):
            return "realign_spherical_recess_center_set_on_host_face"
        if "expected_local_centers" in parameter_bindings:
            return "create_spherical_recess_center_set_on_host_face"
        if blocker_id == "feature_profile_shape_alignment":
            return "restore_spherical_recess_circle_profile_equivalence"
        if blocker_id == "feature_pattern":
            return "restore_spherical_recess_pattern_distribution"
    if family_id == "path_sweep":
        if blocker_id == "feature_path_sweep_rail":
            return "rebuild_sweep_rail_with_explicit_line_arc_line_path"
        if blocker_id == "feature_path_sweep_profile":
            return "rebuild_annular_sweep_profile_face"
        if blocker_id == "feature_path_sweep_frame":
            return "align_sweep_profile_frame_to_rail_start"
        if blocker_id == "feature_path_sweep_result":
            return "rebuild_hollow_path_sweep_result"
    if family_id == "named_face_local_edit":
        return "retarget_local_face_edit"
    return None

def _aggregate_repair_intent(feature_instances: list[FeatureInstance]) -> str | None:
    from sub_agent_runtime.semantic_kernel.recipes import (
        _repair_priority_for_feature_instance,
    )

    for feature_instance in sorted(
        feature_instances,
        key=_repair_priority_for_feature_instance,
    ):
        repair_intent = str(feature_instance.repair_intent or "").strip()
        if repair_intent:
            return repair_intent
    return None

def _reconcile_feature_node_statuses(
    graph: DomainKernelState,
    *,
    blocked_feature_ids: set[str],
    latest_validation: dict[str, Any] | None,
) -> None:
    if isinstance(latest_validation, dict) and bool(latest_validation.get("is_complete")):
        for node in graph.nodes.values():
            if node.kind == "feature":
                node.status = "satisfied"
        return

    validation_family_status_hints = _validation_family_status_hints(latest_validation)
    active_instance_statuses_by_feature: dict[str, set[str]] = {}
    feature_ids_with_any_instances: set[str] = set()
    for feature_instance in graph.feature_instances.values():
        primary_feature_id = str(feature_instance.primary_feature_id or "").strip()
        if not primary_feature_id:
            continue
        feature_ids_with_any_instances.add(primary_feature_id)
        if feature_instance.status not in {"active", "blocked", "observed"}:
            continue
        active_instance_statuses_by_feature.setdefault(primary_feature_id, set()).add(
            feature_instance.status
        )

    for node in graph.nodes.values():
        if node.kind != "feature":
            continue
        family_statuses: set[str] = set()
        for family_id in _feature_node_family_ids(node.node_id):
            family_statuses.update(validation_family_status_hints.get(family_id, set()))
        instance_statuses = active_instance_statuses_by_feature.get(node.node_id, set())
        if (
            node.node_id in blocked_feature_ids
            or "blocked" in instance_statuses
            or "contradicted" in family_statuses
        ):
            node.status = "blocked"
            continue
        if (
            "active" in instance_statuses
            or "observed" in instance_statuses
            or "insufficient_evidence" in family_statuses
        ):
            node.status = "active"
            continue
        if (
            latest_validation is not None
            and (
                node.node_id in feature_ids_with_any_instances
                or node.status in {"blocked", "failed"}
            )
        ):
            node.status = "resolved"
            continue
        if node.status == "planned":
            node.status = "active"

def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower())
    return normalized.strip("_") or "unknown"
