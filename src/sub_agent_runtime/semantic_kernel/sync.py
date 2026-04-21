from __future__ import annotations

from typing import Any

from sub_agent_runtime.semantic_kernel.bindings import _binding_from_tool_result
from sub_agent_runtime.semantic_kernel.bootstrap import (
    build_initial_domain_kernel_state,
    build_initial_feature_graph,
    initialize_domain_kernel_state,
    initialize_feature_graph,
)
from sub_agent_runtime.semantic_kernel.instances import (
    _reconcile_feature_node_statuses,
    _sync_blocker_nodes,
    _sync_evidence_nodes,
    _sync_feature_instances_and_patches,
)
from sub_agent_runtime.semantic_kernel.models import (
    DomainKernelState,
    FeatureGraphNode,
)
from sub_agent_runtime.semantic_kernel.taxonomy import (
    _blocker_to_feature_ids,
    _feature_ids_from_taxonomy_record,
    _validation_blocker_taxonomy,
    _validation_blockers,
)


def sync_domain_kernel_state(
    graph: DomainKernelState,
    *,
    requirements: dict[str, Any],
    latest_write_payload: dict[str, Any] | None,
    latest_validation: dict[str, Any] | None,
    previous_error: str | None,
    evidence_by_tool: dict[str, dict[str, Any]] | None = None,
    reason: str,
    binding_ids: list[str] | None = None,
) -> DomainKernelState:
    blockers = _validation_blockers(latest_validation)
    blocker_taxonomy = _validation_blocker_taxonomy(
        latest_validation,
        graph=graph,
    )
    taxonomy_by_blocker = {
        item["blocker_id"]: item
        for item in blocker_taxonomy
        if isinstance(item, dict)
        and isinstance(item.get("blocker_id"), str)
        and str(item.get("blocker_id")).strip()
    }
    blocked_feature_ids = {
        feature_id
        for blocker in blockers
        for feature_id in (
            _feature_ids_from_taxonomy_record(taxonomy_by_blocker.get(blocker))
            or _blocker_to_feature_ids(blocker)
        )
        if feature_id in graph.nodes
    }

    for node in graph.nodes.values():
        if node.kind != "feature":
            continue
        if latest_validation and bool(latest_validation.get("is_complete")):
            node.status = "satisfied"
        elif node.node_id in blocked_feature_ids:
            node.status = "blocked"
        elif node.status in {"failed", "blocked"} and node.node_id not in blocked_feature_ids:
            node.status = "active"
        elif node.status == "planned":
            node.status = "active"

    _sync_blocker_nodes(
        graph,
        blockers,
        blocker_taxonomy=blocker_taxonomy,
    )
    _sync_feature_instances_and_patches(
        graph,
        blockers=blockers,
        blocker_taxonomy=blocker_taxonomy,
        latest_validation=latest_validation,
        blocked_feature_ids=blocked_feature_ids,
    )
    _reconcile_feature_node_statuses(
        graph,
        blocked_feature_ids=blocked_feature_ids,
        latest_validation=latest_validation,
    )
    _sync_evidence_nodes(
        graph,
        latest_write_payload=latest_write_payload,
        latest_validation=latest_validation,
        previous_error=previous_error,
        evidence_by_tool=evidence_by_tool or {},
        blocker_taxonomy=blocker_taxonomy,
    )

    active_ids = list(blocked_feature_ids)
    if not active_ids:
        active_ids = [
            node.node_id
            for node in graph.nodes.values()
            if node.kind == "feature" and node.status in {"active", "planned"}
        ]
    graph.set_active(active_ids[:8] or ["intent.root", "body.primary"])
    graph.increment_revision(reason, binding_ids=binding_ids)
    return graph


def sync_feature_graph(
    graph: DomainKernelState,
    *,
    requirements: dict[str, Any],
    latest_write_payload: dict[str, Any] | None,
    latest_validation: dict[str, Any] | None,
    previous_error: str | None,
    evidence_by_tool: dict[str, dict[str, Any]] | None = None,
    reason: str,
    binding_ids: list[str] | None = None,
) -> DomainKernelState:
    return sync_domain_kernel_state(
        graph,
        requirements=requirements,
        latest_write_payload=latest_write_payload,
        latest_validation=latest_validation,
        previous_error=previous_error,
        evidence_by_tool=evidence_by_tool,
        reason=reason,
        binding_ids=binding_ids,
    )


def sync_domain_kernel_state_from_tool_result(
    graph: DomainKernelState,
    *,
    tool_name: str,
    payload: dict[str, Any],
    round_no: int,
    fallback_latest_validation: dict[str, Any] | None = None,
) -> tuple[DomainKernelState, dict[str, Any]]:
    latest_write_payload = None
    latest_validation = None
    previous_error = None
    evidence_by_tool: dict[str, dict[str, Any]] = {}

    if tool_name in {"apply_cad_action", "execute_build123d", "execute_repair_packet"}:
        latest_write_payload = dict(payload)
        if "tool" not in latest_write_payload:
            latest_write_payload["tool"] = tool_name
        if not bool(payload.get("success", True)) and isinstance(payload.get("error"), str):
            previous_error = str(payload.get("error"))
    elif tool_name == "validate_requirement":
        latest_validation = dict(payload)
    else:
        evidence_by_tool[tool_name] = dict(payload)
        if isinstance(payload.get("error"), str):
            previous_error = str(payload.get("error"))
    effective_latest_validation = latest_validation
    if effective_latest_validation is None and isinstance(fallback_latest_validation, dict):
        effective_latest_validation = dict(fallback_latest_validation)
    binding = _binding_from_tool_result(
        graph=graph,
        tool_name=tool_name,
        payload=payload,
        round_no=round_no,
        active_node_ids=list(graph.active_node_ids),
    )
    if binding is not None:
        graph.record_binding(binding)

    synced = sync_domain_kernel_state(
        graph,
        requirements=(graph.nodes.get("intent.root") or FeatureGraphNode("intent.root", "intent", "Overall CAD objective")).attributes.get("requirements", {}),
        latest_write_payload=latest_write_payload,
        latest_validation=effective_latest_validation,
        previous_error=previous_error,
        evidence_by_tool=evidence_by_tool,
        reason=f"tool_result:{tool_name}:round_{round_no:02d}",
        binding_ids=[binding.binding_id] if binding is not None else None,
    )
    return synced, {
        "ok": True,
        "revision": synced.revision,
        "round_no": round_no,
        "tool_name": tool_name,
        "binding_id": binding.binding_id if binding is not None else None,
        "binding_kind": binding.binding_kind if binding is not None else None,
    }


def sync_feature_graph_from_tool_result(
    graph: DomainKernelState,
    *,
    tool_name: str,
    payload: dict[str, Any],
    round_no: int,
    fallback_latest_validation: dict[str, Any] | None = None,
) -> tuple[DomainKernelState, dict[str, Any]]:
    return sync_domain_kernel_state_from_tool_result(
        graph,
        tool_name=tool_name,
        payload=payload,
        round_no=round_no,
        fallback_latest_validation=fallback_latest_validation,
    )


__all__ = [
    "build_initial_domain_kernel_state",
    "build_initial_feature_graph",
    "initialize_domain_kernel_state",
    "initialize_feature_graph",
    "sync_domain_kernel_state",
    "sync_domain_kernel_state_from_tool_result",
    "sync_feature_graph",
    "sync_feature_graph_from_tool_result",
]
