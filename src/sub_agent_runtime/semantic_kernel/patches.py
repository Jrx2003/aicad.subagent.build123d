from __future__ import annotations

from typing import Any

from sub_agent_runtime.semantic_kernel.models import (
    DomainKernelPatch,
    DomainKernelState,
    FeatureGraphEdge,
    FeatureGraphNode,
    FeatureInstance,
    PatchFeatureGraphInput,
)


def apply_domain_kernel_patch(
    graph: DomainKernelState,
    patch: PatchFeatureGraphInput,
) -> tuple[DomainKernelState, dict[str, Any]]:
    changed_node_ids: list[str] = []
    if patch.update_mode not in {"merge"}:
        return graph, {"ok": False, "errors": [f"unsupported_update_mode:{patch.update_mode}"]}
    for payload in patch.nodes or []:
        node_id = str(payload.get("node_id") or "").strip()
        kind = str(payload.get("kind") or "").strip()
        label = str(payload.get("label") or "").strip()
        if not node_id or not kind or not label:
            continue
        node = graph.nodes.get(node_id) or FeatureGraphNode(
            node_id=node_id,
            kind=kind,
            label=label,
        )
        node.kind = kind
        node.label = label
        node.status = str(payload.get("status") or node.status or "planned")
        node.summary = (
            str(payload.get("summary"))
            if isinstance(payload.get("summary"), str)
            else node.summary
        )
        attributes = payload.get("attributes")
        if isinstance(attributes, dict):
            node.attributes.update(attributes)
        graph.upsert_node(node)
        changed_node_ids.append(node_id)
    for payload in patch.edges or []:
        source_id = str(payload.get("source_id") or "").strip()
        target_id = str(payload.get("target_id") or "").strip()
        relation = str(payload.get("relation") or "").strip()
        if not source_id or not target_id or not relation:
            continue
        graph.upsert_edge(
            FeatureGraphEdge(
                source_id=source_id,
                target_id=target_id,
                relation=relation,
                summary=(
                    str(payload.get("summary"))
                    if isinstance(payload.get("summary"), str)
                    else None
                ),
            )
        )
    for node_id in patch.blocked_node_ids or []:
        if node_id in graph.nodes:
            graph.nodes[node_id].status = "blocked"
            changed_node_ids.append(node_id)
    for node_id in patch.completed_node_ids or []:
        if node_id in graph.nodes:
            graph.nodes[node_id].status = "satisfied"
            changed_node_ids.append(node_id)
    for payload in patch.feature_instances or []:
        instance_id = str(payload.get("instance_id") or "").strip()
        family_id = str(payload.get("family_id") or "").strip()
        primary_feature_id = str(payload.get("primary_feature_id") or "").strip()
        label = str(payload.get("label") or "").strip()
        if not instance_id or not family_id or not primary_feature_id or not label:
            continue
        feature_instance = graph.feature_instances.get(instance_id) or FeatureInstance(
            instance_id=instance_id,
            family_id=family_id,
            primary_feature_id=primary_feature_id,
            label=label,
        )
        feature_instance.status = str(payload.get("status") or feature_instance.status or "planned")
        feature_instance.summary = (
            str(payload.get("summary"))
            if isinstance(payload.get("summary"), str)
            else feature_instance.summary
        )
        host_ids = payload.get("host_ids")
        if isinstance(host_ids, list):
            feature_instance.host_ids = [
                str(item).strip() for item in host_ids if isinstance(item, str) and str(item).strip()
            ]
        blocker_ids = payload.get("blocker_ids")
        if isinstance(blocker_ids, list):
            feature_instance.blocker_ids = [
                str(item).strip()
                for item in blocker_ids
                if isinstance(item, str) and str(item).strip()
            ]
        anchor_keys = payload.get("anchor_keys")
        if isinstance(anchor_keys, list):
            feature_instance.anchor_keys = [
                str(item).strip()
                for item in anchor_keys
                if isinstance(item, str) and str(item).strip()
            ]
        parameter_bindings = payload.get("parameter_bindings")
        if isinstance(parameter_bindings, dict):
            feature_instance.parameter_bindings.update(parameter_bindings)
        linked_binding_ids = payload.get("linked_binding_ids")
        if isinstance(linked_binding_ids, list):
            feature_instance.linked_binding_ids = [
                str(item).strip()
                for item in linked_binding_ids
                if isinstance(item, str) and str(item).strip()
            ]
        latest_repair_mode = str(payload.get("latest_repair_mode") or "").strip()
        if latest_repair_mode:
            feature_instance.latest_repair_mode = latest_repair_mode
        repair_intent = str(payload.get("repair_intent") or "").strip()
        if repair_intent:
            feature_instance.repair_intent = repair_intent
        graph.upsert_feature_instance(feature_instance)
    for payload in patch.repair_patches or []:
        patch_id = str(payload.get("patch_id") or "").strip()
        repair_mode = str(payload.get("repair_mode") or "").strip()
        reason = str(payload.get("reason") or "").strip()
        if not patch_id or not repair_mode or not reason:
            continue
        repair_patch = DomainKernelPatch(
            patch_id=patch_id,
            repair_mode=repair_mode,
            reason=reason,
            feature_instance_ids=[
                str(item).strip()
                for item in (payload.get("feature_instance_ids") or [])
                if isinstance(item, str) and str(item).strip()
            ],
            affected_host_ids=[
                str(item).strip()
                for item in (payload.get("affected_host_ids") or [])
                if isinstance(item, str) and str(item).strip()
            ],
            anchor_keys=[
                str(item).strip()
                for item in (payload.get("anchor_keys") or [])
                if isinstance(item, str) and str(item).strip()
            ],
            parameter_keys=[
                str(item).strip()
                for item in (payload.get("parameter_keys") or [])
                if isinstance(item, str) and str(item).strip()
            ],
            source_binding_id=(
                str(payload.get("source_binding_id")).strip()
                if isinstance(payload.get("source_binding_id"), str)
                and str(payload.get("source_binding_id")).strip()
                else None
            ),
            source_blocker_ids=[
                str(item).strip()
                for item in (payload.get("source_blocker_ids") or [])
                if isinstance(item, str) and str(item).strip()
            ],
            repair_intent=(
                str(payload.get("repair_intent")).strip()
                if isinstance(payload.get("repair_intent"), str)
                and str(payload.get("repair_intent")).strip()
                else None
            ),
            stale=bool(payload.get("stale")),
        )
        graph.record_repair_patch(repair_patch)
    if patch.active_node_ids:
        graph.set_active(patch.active_node_ids)
    graph.increment_revision(f"manual_patch:{patch.reason}")
    return graph, {
        "ok": True,
        "summary": patch.reason,
        "changed_node_ids": list(dict.fromkeys(changed_node_ids)),
        "graph_revision": graph.revision,
    }


def apply_feature_graph_patch(
    graph: DomainKernelState,
    patch: PatchFeatureGraphInput,
) -> tuple[DomainKernelState, dict[str, Any]]:
    return apply_domain_kernel_patch(graph, patch)


__all__ = ["apply_domain_kernel_patch", "apply_feature_graph_patch"]
