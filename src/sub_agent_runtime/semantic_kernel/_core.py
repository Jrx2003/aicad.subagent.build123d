from __future__ import annotations

from typing import Any

from sub_agent_runtime.semantic_kernel.bootstrap import (
    _default_feature_instance_id,
    _requirements_text,
    _seed_feature_nodes,
    _stable_hash,
    build_initial_domain_kernel_state,
    build_initial_feature_graph,
    initialize_domain_kernel_state,
    initialize_feature_graph,
)
from sub_agent_runtime.semantic_kernel.bindings import (
    _anchor_keys_from_binding,
    _binding_from_tool_result,
    _classify_failed_execute_build123d_binding,
    _contextualize_feature_anchor_summary_for_graph,
    _count_nodes_by_kind,
    _extract_execute_probe_feature_anchor_summary,
    _extract_feature_anchor_summary,
    _extract_geometry_summary,
    _extract_structured_signals_from_evidence_text,
    _extract_validation_feature_anchor_summary,
    _family_signal_values_from_binding,
    _geometry_anchor_overrides_from_execution_binding,
    _has_meaningful_geometry_summary,
    _host_ids_from_binding,
    _latest_active_binding,
    _latest_family_feature_anchor_binding,
    _merge_feature_anchor_summaries,
    _normalize_validation_signals_for_family,
    _parse_structured_signal_value,
    _sanitize_anchor_signal_value,
    _summarize_geometry,
)
from sub_agent_runtime.semantic_kernel.instances import (
    _aggregate_repair_intent,
    _aggregate_repair_mode,
    _drop_stale_geometry_derived_binding_keys,
    _feature_instance_from_taxonomy_record,
    _feature_instance_id_from_taxonomy_record,
    _feature_instance_tracks_geometry,
    _fresh_geometry_parameter_bindings_from_execution_binding,
    _latest_specific_binding_with_blockers,
    _merge_feature_instance,
    _merge_feature_parameter_bindings,
    _parameter_bindings_include_fresh_geometry,
    _reconcile_feature_node_statuses,
    _refresh_active_general_geometry_instances,
    _refresh_feature_instances_with_latest_execution_geometry,
    _repair_intent_for_feature_instance,
    _repair_mode_from_taxonomy_record,
    _slugify,
    _sync_blocker_nodes,
    _sync_evidence_nodes,
    _sync_feature_instances_and_patches,
    _taxonomy_records_from_latest_specific_binding,
)
from sub_agent_runtime.semantic_kernel.models import (
    DomainKernelState,
    PatchFeatureGraphInput,
)
from sub_agent_runtime.semantic_kernel.recipes import (
    _build_centered_bbox_host_frame,
    _coerce_xy_points,
    _compact_parameter_bindings,
    _explicit_anchor_hole_recipe_packet,
    _family_repair_packet_from_feature_instance,
    _family_repair_priority_rank,
    _feature_instance_digest,
    _half_shell_profile_recipe_packet,
    _normalize_points_for_host_frame,
    _repair_packet_geometry_summary,
    _repair_packet_priority,
    _repair_priority_for_feature_instance,
    _replace_repair_packets_from_active_instances,
    _sorted_active_repair_packets,
    _spherical_recess_recipe_packet,
)
from sub_agent_runtime.semantic_kernel.taxonomy import (
    _blocker_to_feature_ids,
    _canonical_recommended_repair_lane,
    _contextualize_blocker_taxonomy_record_for_graph,
    _contextualize_validation_blocker_taxonomy,
    _feature_ids_for_runtime_family_ids,
    _feature_ids_from_taxonomy_record,
    _feature_node_family_ids,
    _should_prefer_spherical_recess_taxonomy,
    _validation_blocker_taxonomy,
    _validation_blockers,
    _validation_family_status_hints,
    _validation_uses_only_general_geometry_lane,
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
    from sub_agent_runtime.semantic_kernel.sync import sync_domain_kernel_state as _impl

    return _impl(
        graph,
        requirements=requirements,
        latest_write_payload=latest_write_payload,
        latest_validation=latest_validation,
        previous_error=previous_error,
        evidence_by_tool=evidence_by_tool,
        reason=reason,
        binding_ids=binding_ids,
    )


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
    from sub_agent_runtime.semantic_kernel.sync import sync_feature_graph as _impl

    return _impl(
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
    from sub_agent_runtime.semantic_kernel.sync import (
        sync_domain_kernel_state_from_tool_result as _impl,
    )

    return _impl(
        graph,
        tool_name=tool_name,
        payload=payload,
        round_no=round_no,
        fallback_latest_validation=fallback_latest_validation,
    )


def sync_feature_graph_from_tool_result(
    graph: DomainKernelState,
    *,
    tool_name: str,
    payload: dict[str, Any],
    round_no: int,
    fallback_latest_validation: dict[str, Any] | None = None,
) -> tuple[DomainKernelState, dict[str, Any]]:
    from sub_agent_runtime.semantic_kernel.sync import sync_feature_graph_from_tool_result as _impl

    return _impl(
        graph,
        tool_name=tool_name,
        payload=payload,
        round_no=round_no,
        fallback_latest_validation=fallback_latest_validation,
    )


def apply_domain_kernel_patch(
    graph: DomainKernelState,
    patch: PatchFeatureGraphInput,
) -> tuple[DomainKernelState, dict[str, Any]]:
    from sub_agent_runtime.semantic_kernel.patches import apply_domain_kernel_patch as _impl

    return _impl(graph, patch)


def apply_feature_graph_patch(
    graph: DomainKernelState,
    patch: PatchFeatureGraphInput,
) -> tuple[DomainKernelState, dict[str, Any]]:
    from sub_agent_runtime.semantic_kernel.patches import apply_feature_graph_patch as _impl

    return _impl(graph, patch)


def build_domain_kernel_digest(
    graph: DomainKernelState | None,
    *,
    include_nodes: bool = True,
    include_edges: bool = False,
    include_bindings: bool = False,
    include_revision_history: bool = False,
    max_nodes: int = 20,
    max_edges: int = 20,
    max_bindings: int = 8,
    max_revisions: int = 8,
) -> dict[str, object]:
    from sub_agent_runtime.semantic_kernel.digest import build_domain_kernel_digest as _impl

    return _impl(
        graph,
        include_nodes=include_nodes,
        include_edges=include_edges,
        include_bindings=include_bindings,
        include_revision_history=include_revision_history,
        max_nodes=max_nodes,
        max_edges=max_edges,
        max_bindings=max_bindings,
        max_revisions=max_revisions,
    )


__all__ = [name for name in globals() if not name.startswith('__')]
