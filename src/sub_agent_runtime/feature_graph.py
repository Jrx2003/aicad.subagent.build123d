from __future__ import annotations

import ast
from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any

from common.blocker_taxonomy import (
    classify_blocker_taxonomy_many,
    taxonomy_records_from_validation_payload,
)
from pydantic import BaseModel, ConfigDict, Field
from sandbox_mcp_server.registry import (
    analyze_requirement_semantics,
    infer_requirement_probe_families,
    requirement_requests_path_sweep,
)


_NODE_STATUSES = {
    "planned",
    "active",
    "observed",
    "blocked",
    "satisfied",
    "resolved",
    "failed",
}


class FeatureGraphNodeStore(dict[str, "FeatureGraphNode"]):
    def __iter__(self):  # type: ignore[override]
        return iter(self.values())


class QueryGraphStateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_nodes: bool = Field(default=True)
    include_edges: bool = Field(default=False)
    include_bindings: bool = Field(default=False)
    include_revision_history: bool = Field(default=False)
    max_nodes: int = Field(default=20, ge=1, le=128)
    max_edges: int = Field(default=20, ge=1, le=256)
    max_bindings: int = Field(default=8, ge=1, le=64)
    max_revisions: int = Field(default=8, ge=1, le=64)


class PatchFeatureGraphInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(description="Short reason for changing semantic graph state.")
    update_mode: str = Field(default="merge", description="Currently only merge is supported.")
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    active_node_ids: list[str] = Field(default_factory=list)
    blocked_node_ids: list[str] = Field(default_factory=list)
    completed_node_ids: list[str] = Field(default_factory=list)
    feature_instances: list[dict[str, Any]] = Field(default_factory=list)
    repair_patches: list[dict[str, Any]] = Field(default_factory=list)


@dataclass(slots=True)
class KernelBinding:
    binding_id: str
    binding_kind: str
    source_tool: str
    round_no: int | None = None
    summary: str | None = None
    payload_digest: str | None = None
    node_ids: list[str] = field(default_factory=list)
    family_ids: list[str] = field(default_factory=list)
    blocker_ids: list[str] = field(default_factory=list)
    primary_feature_ids: list[str] = field(default_factory=list)
    evidence_source: str | None = None
    completeness_relevance: str | None = None
    severity: str | None = None
    recommended_repair_lane: str | None = None
    geometry_summary: dict[str, Any] = field(default_factory=dict)
    feature_anchor_summary: dict[str, Any] = field(default_factory=dict)
    stale: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "binding_kind": self.binding_kind,
            "source_tool": self.source_tool,
            "round_no": self.round_no,
            "summary": self.summary,
            "payload_digest": self.payload_digest,
            "node_ids": self.node_ids,
            "family_ids": self.family_ids,
            "blocker_ids": self.blocker_ids,
            "primary_feature_ids": self.primary_feature_ids,
            "evidence_source": self.evidence_source,
            "completeness_relevance": self.completeness_relevance,
            "severity": self.severity,
            "recommended_repair_lane": self.recommended_repair_lane,
            "geometry_summary": self.geometry_summary,
            "feature_anchor_summary": self.feature_anchor_summary,
            "stale": self.stale,
        }


@dataclass(slots=True)
class KernelRevisionEntry:
    revision: int
    reason: str
    active_node_ids: list[str] = field(default_factory=list)
    binding_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "reason": self.reason,
            "active_node_ids": self.active_node_ids,
            "binding_ids": self.binding_ids,
        }


@dataclass(slots=True)
class FeatureInstance:
    instance_id: str
    family_id: str
    primary_feature_id: str
    label: str
    status: str = "planned"
    summary: str | None = None
    host_ids: list[str] = field(default_factory=list)
    blocker_ids: list[str] = field(default_factory=list)
    anchor_keys: list[str] = field(default_factory=list)
    parameter_bindings: dict[str, Any] = field(default_factory=dict)
    linked_binding_ids: list[str] = field(default_factory=list)
    latest_repair_mode: str | None = None
    repair_intent: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "family_id": self.family_id,
            "primary_feature_id": self.primary_feature_id,
            "label": self.label,
            "status": self.status,
            "summary": self.summary,
            "host_ids": self.host_ids,
            "blocker_ids": self.blocker_ids,
            "anchor_keys": self.anchor_keys,
            "parameter_bindings": self.parameter_bindings,
            "linked_binding_ids": self.linked_binding_ids,
            "latest_repair_mode": self.latest_repair_mode,
            "repair_intent": self.repair_intent,
        }


@dataclass(slots=True)
class DomainKernelPatch:
    patch_id: str
    repair_mode: str
    reason: str
    feature_instance_ids: list[str] = field(default_factory=list)
    affected_host_ids: list[str] = field(default_factory=list)
    anchor_keys: list[str] = field(default_factory=list)
    parameter_keys: list[str] = field(default_factory=list)
    source_binding_id: str | None = None
    source_blocker_ids: list[str] = field(default_factory=list)
    repair_intent: str | None = None
    stale: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "repair_mode": self.repair_mode,
            "reason": self.reason,
            "feature_instance_ids": self.feature_instance_ids,
            "affected_host_ids": self.affected_host_ids,
            "anchor_keys": self.anchor_keys,
            "parameter_keys": self.parameter_keys,
            "source_binding_id": self.source_binding_id,
            "source_blocker_ids": self.source_blocker_ids,
            "repair_intent": self.repair_intent,
            "stale": self.stale,
        }


@dataclass(slots=True)
class FamilyRepairPacket:
    packet_id: str
    family_id: str
    feature_instance_id: str
    repair_mode: str
    repair_intent: str | None = None
    affected_host_ids: list[str] = field(default_factory=list)
    anchor_keys: list[str] = field(default_factory=list)
    parameter_keys: list[str] = field(default_factory=list)
    host_frame: dict[str, Any] = field(default_factory=dict)
    target_anchor_summary: dict[str, Any] = field(default_factory=dict)
    realized_anchor_summary: dict[str, Any] = field(default_factory=dict)
    recipe_id: str | None = None
    recipe_summary: str | None = None
    recipe_skeleton: dict[str, Any] = field(default_factory=dict)
    source_binding_id: str | None = None
    source_blocker_ids: list[str] = field(default_factory=list)
    stale: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "packet_id": self.packet_id,
            "family_id": self.family_id,
            "feature_instance_id": self.feature_instance_id,
            "repair_mode": self.repair_mode,
            "repair_intent": self.repair_intent,
            "affected_host_ids": self.affected_host_ids,
            "anchor_keys": self.anchor_keys,
            "parameter_keys": self.parameter_keys,
            "host_frame": self.host_frame,
            "target_anchor_summary": self.target_anchor_summary,
            "realized_anchor_summary": self.realized_anchor_summary,
            "recipe_id": self.recipe_id,
            "recipe_summary": self.recipe_summary,
            "recipe_skeleton": self.recipe_skeleton,
            "source_binding_id": self.source_binding_id,
            "source_blocker_ids": self.source_blocker_ids,
            "stale": self.stale,
        }


@dataclass(slots=True)
class FeatureGraphNode:
    node_id: str
    kind: str
    label: str
    status: str = "planned"
    summary: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    linked_evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "kind": self.kind,
            "label": self.label,
            "status": self.status,
            "summary": self.summary,
            "attributes": self.attributes,
            "linked_evidence": self.linked_evidence,
        }


@dataclass(slots=True)
class FeatureGraphEdge:
    source_id: str
    target_id: str
    relation: str
    summary: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relation": self.relation,
            "summary": self.summary,
        }


@dataclass(slots=True)
class DomainKernelState:
    graph_id: str
    revision: int = 1
    latest_sync_reason: str | None = None
    nodes: FeatureGraphNodeStore = field(default_factory=FeatureGraphNodeStore)
    edges: list[FeatureGraphEdge] = field(default_factory=list)
    active_node_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    bindings: dict[str, KernelBinding] = field(default_factory=dict)
    feature_instances: dict[str, FeatureInstance] = field(default_factory=dict)
    repair_patches: dict[str, DomainKernelPatch] = field(default_factory=dict)
    repair_packets: dict[str, FamilyRepairPacket] = field(default_factory=dict)
    revision_history: list[KernelRevisionEntry] = field(default_factory=list)

    def upsert_node(self, node: FeatureGraphNode) -> None:
        if node.status not in _NODE_STATUSES:
            node.status = "planned"
        self.nodes[node.node_id] = node

    def upsert_edge(self, edge: FeatureGraphEdge) -> None:
        for index, current in enumerate(self.edges):
            if (
                current.source_id == edge.source_id
                and current.target_id == edge.target_id
                and current.relation == edge.relation
            ):
                self.edges[index] = edge
                return
        self.edges.append(edge)

    def set_active(self, node_ids: list[str]) -> None:
        self.active_node_ids = [
            node_id for node_id in node_ids if isinstance(node_id, str) and node_id in self.nodes
        ]

    def seed_revision(self, reason: str) -> None:
        self.latest_sync_reason = reason
        if self.revision_history:
            return
        self.revision_history.append(
            KernelRevisionEntry(
                revision=self.revision,
                reason=reason,
                active_node_ids=list(self.active_node_ids),
                binding_ids=[],
            )
        )

    def record_binding(self, binding: KernelBinding) -> None:
        if binding.binding_kind == "execution":
            stale_kinds = {"execution", "validation", "observation"}
            for current in self.bindings.values():
                if current.binding_kind in stale_kinds:
                    current.stale = True
            for patch in self.repair_patches.values():
                patch.stale = True
            for packet in self.repair_packets.values():
                packet.stale = True
        elif binding.binding_kind == "validation":
            for current in self.bindings.values():
                if current.binding_kind == "validation":
                    current.stale = True
        elif binding.binding_kind == "observation":
            for current in self.bindings.values():
                if current.binding_kind == "observation" and current.source_tool == binding.source_tool:
                    current.stale = True
        self.bindings[binding.binding_id] = binding

    def upsert_feature_instance(self, feature_instance: FeatureInstance) -> None:
        self.feature_instances[feature_instance.instance_id] = feature_instance

    def record_repair_patch(self, patch: DomainKernelPatch) -> None:
        if self._is_generic_fallback_patch(patch) and self._has_active_specific_patch():
            patch.stale = True
            self.repair_patches[patch.patch_id] = patch
            return
        for current in self.repair_patches.values():
            current.stale = True
        self.repair_patches[patch.patch_id] = patch

    def replace_repair_packets(self, packets: list[FamilyRepairPacket]) -> None:
        for current in self.repair_packets.values():
            current.stale = True
        for packet in packets:
            self.repair_packets[packet.packet_id] = packet

    def _has_active_specific_patch(self) -> bool:
        return any(
            not current.stale and not self._is_generic_fallback_patch(current)
            for current in self.repair_patches.values()
        )

    def _is_generic_fallback_patch(self, patch: DomainKernelPatch) -> bool:
        if patch.anchor_keys or patch.parameter_keys:
            return False
        if str(patch.repair_mode or "").strip() != "whole_part_rebuild":
            return False
        if not patch.feature_instance_ids:
            return True
        for instance_id in patch.feature_instance_ids:
            feature_instance = self.feature_instances.get(instance_id)
            family_id = ""
            primary_feature_id = ""
            if feature_instance is not None:
                family_id = str(feature_instance.family_id or "").strip()
                primary_feature_id = str(feature_instance.primary_feature_id or "").strip()
            if not family_id:
                if instance_id.startswith("instance.general_geometry"):
                    family_id = "general_geometry"
                elif instance_id.startswith("instance.path_sweep"):
                    family_id = "path_sweep"
            if family_id and family_id != "general_geometry":
                return False
            if primary_feature_id and primary_feature_id != "feature.core_geometry":
                return False
        return True

    def increment_revision(self, reason: str, *, binding_ids: list[str] | None = None) -> None:
        self.revision += 1
        self.latest_sync_reason = reason
        self.revision_history.append(
            KernelRevisionEntry(
                revision=self.revision,
                reason=reason,
                active_node_ids=list(self.active_node_ids),
                binding_ids=list(binding_ids or []),
            )
        )

    def digest(self, *, max_nodes: int = 8) -> dict[str, Any]:
        blocked = [
            node.node_id
            for node in self.nodes.values()
            if node.kind == "blocker" and node.status == "blocked"
        ]
        unsatisfied = [
            node.node_id
            for node in self.nodes.values()
            if node.kind == "feature" and node.status not in {"satisfied", "resolved"}
        ]
        completed = [
            node.node_id
            for node in self.nodes.values()
            if node.kind == "feature" and node.status in {"satisfied", "resolved"}
        ]
        evidence = [
            node.label
            for node in self.nodes.values()
            if node.kind == "evidence"
        ][:max_nodes]
        active_bindings = [binding for binding in self.bindings.values() if not binding.stale]
        latest_binding = active_bindings[-1] if active_bindings else None
        active_feature_instances = [
            feature_instance
            for feature_instance in self.feature_instances.values()
            if feature_instance.status in {"active", "blocked", "observed"}
        ]
        active_patches = [patch for patch in self.repair_patches.values() if not patch.stale]
        latest_patch = active_patches[-1] if active_patches else None
        active_packets = [packet for packet in self.repair_packets.values() if not packet.stale]
        latest_packet = active_packets[-1] if active_packets else None
        latest_patch_feature_instances = [
            self.feature_instances[instance_id]
            for instance_id in (latest_patch.feature_instance_ids if latest_patch else [])
            if instance_id in self.feature_instances
        ]
        requirement_tags = [
            node.node_id.split(".", 1)[1]
            for node in self.nodes.values()
            if node.kind == "feature" and "." in node.node_id
        ]
        return {
            "graph_id": self.graph_id,
            "revision": self.revision,
            "latest_sync_reason": self.latest_sync_reason,
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "active_node_ids": self.active_node_ids[:max_nodes],
            "blocked_node_ids": blocked[:max_nodes],
            "completed_node_ids": completed[:max_nodes],
            "unsatisfied_feature_ids": unsatisfied[:max_nodes],
            "requirement_tags": requirement_tags[:max_nodes],
            "evidence_summary": evidence,
            "node_counts": _count_nodes_by_kind(self.nodes.values()),
            "kernel_kind": "DomainKernelState",
            "kernel_revision_count": len(self.revision_history),
            "kernel_binding_count": len(active_bindings),
            "kernel_stale_binding_count": sum(
                1 for binding in self.bindings.values() if binding.stale
            ),
            "kernel_binding_kinds": sorted(
                {binding.binding_kind for binding in active_bindings}
            ),
            "feature_instance_count": len(self.feature_instances),
            "active_feature_instance_ids": [
                feature_instance.instance_id for feature_instance in active_feature_instances[:max_nodes]
            ],
            "active_feature_instances": [
                _feature_instance_digest(feature_instance, max_nodes=max_nodes)
                for feature_instance in active_feature_instances[:max_nodes]
            ],
            "kernel_patch_count": len(active_patches),
            "kernel_patch_kinds": sorted(
                {patch.repair_mode for patch in active_patches if patch.repair_mode}
            ),
            "repair_packet_count": len(active_packets),
            "repair_packet_kinds": sorted(
                {packet.repair_mode for packet in active_packets if packet.repair_mode}
            ),
            "latest_binding_families": (
                list(latest_binding.family_ids[:max_nodes]) if latest_binding else []
            ),
            "latest_binding_blocker_ids": (
                list(latest_binding.blocker_ids[:max_nodes]) if latest_binding else []
            ),
            "latest_binding_primary_feature_ids": (
                list(latest_binding.primary_feature_ids[:max_nodes])
                if latest_binding
                else []
            ),
            "latest_binding_evidence_source": (
                latest_binding.evidence_source if latest_binding else None
            ),
            "latest_binding_completeness_relevance": (
                latest_binding.completeness_relevance if latest_binding else None
            ),
            "latest_binding_severity": (
                latest_binding.severity if latest_binding else None
            ),
            "latest_binding_repair_lane": (
                latest_binding.recommended_repair_lane if latest_binding else None
            ),
            "latest_binding_geometry_summary": (
                dict(latest_binding.geometry_summary) if latest_binding else {}
            ),
            "latest_binding_feature_anchor_summary": (
                dict(latest_binding.feature_anchor_summary) if latest_binding else {}
            ),
            "latest_patch_repair_mode": (
                latest_patch.repair_mode if latest_patch else None
            ),
            "latest_patch_feature_instance_ids": (
                list(latest_patch.feature_instance_ids[:max_nodes]) if latest_patch else []
            ),
            "latest_patch_affected_hosts": (
                list(latest_patch.affected_host_ids[:max_nodes]) if latest_patch else []
            ),
            "latest_patch_anchor_keys": (
                list(latest_patch.anchor_keys[:max_nodes]) if latest_patch else []
            ),
            "latest_patch_parameter_keys": (
                list(latest_patch.parameter_keys[:max_nodes]) if latest_patch else []
            ),
            "latest_patch_feature_instances": [
                _feature_instance_digest(feature_instance, max_nodes=max_nodes)
                for feature_instance in latest_patch_feature_instances[:max_nodes]
            ],
            "latest_patch_repair_intent": (
                latest_patch.repair_intent if latest_patch else None
            ),
            "latest_repair_packet_family_id": (
                latest_packet.family_id if latest_packet else None
            ),
            "latest_repair_packet_feature_instance_id": (
                latest_packet.feature_instance_id if latest_packet else None
            ),
            "latest_repair_packet_repair_mode": (
                latest_packet.repair_mode if latest_packet else None
            ),
            "latest_repair_packet_host_frame": (
                dict(latest_packet.host_frame) if latest_packet else {}
            ),
            "latest_repair_packet_target_anchor_summary": (
                dict(latest_packet.target_anchor_summary) if latest_packet else {}
            ),
            "latest_repair_packet_realized_anchor_summary": (
                dict(latest_packet.realized_anchor_summary) if latest_packet else {}
            ),
            "latest_repair_packet_recipe_id": (
                latest_packet.recipe_id if latest_packet else None
            ),
            "latest_repair_packet_recipe_summary": (
                latest_packet.recipe_summary if latest_packet else None
            ),
            "latest_repair_packet_recipe_skeleton": (
                dict(latest_packet.recipe_skeleton) if latest_packet else {}
            ),
        }

    def to_query_payload(
        self,
        *,
        include_nodes: bool = True,
        include_edges: bool = True,
        include_bindings: bool = False,
        include_revision_history: bool = False,
        max_nodes: int = 40,
        max_edges: int | None = None,
        max_bindings: int = 12,
        max_revisions: int = 12,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "success": True,
            "graph_id": self.graph_id,
            "revision": self.revision,
            "latest_sync_reason": self.latest_sync_reason,
        }
        payload.update(self.digest(max_nodes=min(max_nodes, 12)))
        if include_nodes:
            payload["nodes"] = [
                node.to_dict()
                for node in list(self.nodes.values())[:max_nodes]
            ]
        if include_edges:
            payload["edges"] = [
                edge.to_dict()
                for edge in self.edges[: (max_edges if isinstance(max_edges, int) else max_nodes)]
            ]
        if include_bindings:
            payload["bindings"] = [
                binding.to_dict()
                for binding in list(self.bindings.values())[:max_bindings]
            ]
            payload["feature_instances"] = [
                feature_instance.to_dict()
                for feature_instance in list(self.feature_instances.values())[:max_bindings]
            ]
            payload["repair_patches"] = [
                patch.to_dict()
                for patch in list(self.repair_patches.values())[:max_bindings]
            ]
            payload["repair_packets"] = [
                packet.to_dict()
                for packet in list(self.repair_packets.values())[:max_bindings]
            ]
        if include_revision_history:
            payload["revision_history"] = [
                entry.to_dict()
                for entry in self.revision_history[-max_revisions:]
            ]
        return payload


FeatureGraphState = DomainKernelState


def build_initial_domain_kernel_state(requirements: dict[str, Any]) -> DomainKernelState:
    requirement_text = _requirements_text(requirements)
    semantics = analyze_requirement_semantics(requirements, requirement_text)
    graph = DomainKernelState(
        graph_id=f"fg-{_stable_hash(requirement_text)[:12]}",
        latest_sync_reason="run_start",
        metadata={
            "requirements_digest": _stable_hash(requirement_text),
            "requirement_text_preview": requirement_text[:240],
        },
    )
    graph.upsert_node(
        FeatureGraphNode(
            node_id="intent.root",
            kind="intent",
            label="Overall CAD objective",
            status="active",
            summary="Primary intent derived from the current requirement payload.",
            attributes={"requirements": requirements},
        )
    )
    graph.upsert_node(
        FeatureGraphNode(
            node_id="body.primary",
            kind="body",
            label="Primary body target",
            status="active",
            summary="Authoritative geometry still lives in the sandbox session.",
        )
    )
    graph.upsert_edge(
        FeatureGraphEdge(
            source_id="intent.root",
            target_id="body.primary",
            relation="targets",
            summary="The requirement ultimately targets a primary build body.",
        )
    )

    feature_nodes = _seed_feature_nodes(requirement_text, semantics=semantics)
    for node in feature_nodes:
        graph.upsert_node(node)
        graph.upsert_edge(
            FeatureGraphEdge(
                source_id="intent.root",
                target_id=node.node_id,
                relation="requires",
                summary="The overall objective requires this semantic feature family.",
            )
        )
        graph.upsert_edge(
            FeatureGraphEdge(
                source_id=node.node_id,
                target_id="body.primary",
                relation="realizes",
                summary="This feature family is realized on the primary body.",
            )
        )
        graph.upsert_feature_instance(
            FeatureInstance(
                instance_id=_default_feature_instance_id(node.node_id),
                family_id=node.node_id.replace("feature.", "").replace("feature:", ""),
                primary_feature_id=node.node_id,
                label=node.label,
                status="active",
                summary=node.summary,
                host_ids=["body.primary"],
            )
        )
    graph.set_active(
        ["body.primary", *[node.node_id for node in feature_nodes]] or ["intent.root", "body.primary"]
    )
    graph.seed_revision("run_start")
    return graph


def build_initial_feature_graph(requirements: dict[str, Any]) -> DomainKernelState:
    return build_initial_domain_kernel_state(requirements)


def initialize_domain_kernel_state(requirements: dict[str, Any]) -> DomainKernelState:
    return build_initial_domain_kernel_state(requirements)


def initialize_feature_graph(requirements: dict[str, Any]) -> DomainKernelState:
    return initialize_domain_kernel_state(requirements)


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
) -> dict[str, Any]:
    if graph is None:
        return {}
    payload = graph.to_query_payload(
        include_nodes=include_nodes,
        include_edges=include_edges,
        include_bindings=include_bindings,
        include_revision_history=include_revision_history,
        max_nodes=max_nodes,
        max_edges=max_edges,
        max_bindings=max_bindings,
        max_revisions=max_revisions,
    )
    if not include_edges and "edges" in payload:
        payload.pop("edges", None)
    if include_edges and "edges" in payload:
        payload["edges"] = payload["edges"][:max_edges]
    payload["node_count"] = len(graph.nodes)
    payload["edge_count"] = len(graph.edges)
    payload["active_node_ids"] = list(graph.active_node_ids)
    payload["feature_node_ids"] = [
        node.node_id for node in graph.nodes.values() if node.kind == "feature"
    ][:max_nodes]
    payload["requirement_tags"] = [
        node_id.split("feature:", 1)[1]
        for node_id in payload["feature_node_ids"]
        if node_id.startswith("feature:")
    ]
    payload["blocked_node_ids"] = [
        node.node_id
        for node in graph.nodes.values()
        if node.status == "blocked"
    ][:max_nodes]
    payload["completed_node_ids"] = [
        node.node_id
        for node in graph.nodes.values()
        if node.kind == "feature" and node.status in {"satisfied", "resolved"}
    ][:max_nodes]
    payload["requirement_tags"] = sorted(
        {
            node.node_id.replace("feature.", "").replace("feature:", "")
            for node in graph.nodes.values()
            if node.kind == "feature"
        }
    )
    return payload
def _seed_feature_nodes(
    requirement_text: str,
    *,
    semantics: Any | None = None,
) -> list[FeatureGraphNode]:
    lowered = requirement_text.lower()
    specs: list[tuple[str, str, str]] = []
    semantics = semantics or analyze_requirement_semantics(
        {"description": requirement_text},
        requirement_text,
    )
    probe_families = set(
        infer_requirement_probe_families(
            requirements={"description": requirement_text},
            requirement_text=requirement_text,
            semantics=semantics,
        )
    )
    if "annular_groove" in probe_families:
        specs.append(("feature.annular_groove", "Annular groove family", "Axisymmetric groove or revolve-cut semantics remain important."))
    if "nested_hollow_section" in probe_families:
        specs.append(("feature.nested_hollow_section", "Nested hollow section family", "Requirement implies outer and inner profile coordination."))
    if "explicit_anchor_hole" in probe_families:
        specs.append(("feature.explicit_anchor_hole", "Explicit-anchor hole family", "Requirement likely names local or global anchor coordinates."))
    if "path_sweep" in probe_families or requirement_requests_path_sweep(semantics, requirement_text):
        specs.append(
            (
                "feature.path_sweep",
                "Path sweep family",
                "Requirement explicitly defines a rail/profile sweep workflow that should stay visible from round 0.",
            )
        )
    if "orthogonal_union" in probe_families:
        specs.append(("feature.orthogonal_union", "Orthogonal union family", "Requirement likely benefits from explicit global primitive composition."))
    if "spherical_recess" in probe_families:
        specs.append(("feature.spherical_recess", "Spherical recess family", "Requirement depends on hemispherical or spherical recess geometry on a host face."))
    if "pattern_distribution" in probe_families:
        specs.append(("feature.pattern_distribution", "Pattern distribution family", "Requirement depends on repeated feature layout or centered pattern spacing."))
    if "axisymmetric_profile" in probe_families or "cylind" in lowered:
        specs.append(("feature.axisymmetric_profile", "Axisymmetric profile family", "Requirement likely depends on a shared rotation axis or axial segmentation."))
    if "polygon" in lowered or "triangle" in lowered or "hexagon" in lowered:
        specs.append(("feature.regular_polygon_profile", "Regular polygon profile family", "Requirement likely needs scale-aware polygon semantics."))
    if (
        bool(getattr(semantics, "mentions_face_edit", False))
        and any(token in lowered for token in ("fillet", "chamfer", "pocket", "cut", "hole"))
        and not bool(getattr(semantics, "mentions_pattern", False))
        and not bool(getattr(semantics, "mentions_spherical_recess", False))
        and not bool(getattr(semantics, "mentions_revolved_groove_cut", False))
    ):
        specs.append(("feature.named_face_local_edit", "Named-face local edit family", "Requirement may be cheaper as a local topology-anchored edit."))
    if not specs:
        specs.append(("feature.core_geometry", "Core geometry completion", "Build a valid primary body that satisfies the requirement."))
    return [
        FeatureGraphNode(
            node_id=node_id,
            kind="feature",
            label=label,
            status="planned",
            summary=summary,
        )
        for node_id, label, summary in specs
    ]


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
            recommended_repair_lane = str(
                taxonomy_record.get("recommended_repair_lane") or "code_repair"
            ).strip() or "code_repair"
            feature_ids = _feature_ids_from_taxonomy_record(taxonomy_record)
        else:
            blocker_taxonomy_fallback = classify_blocker_taxonomy_many([blocker])
            blocker_family_ids = (
                blocker_taxonomy_fallback[0].family_ids if blocker_taxonomy_fallback else []
            )
            recommended_repair_lane = (
                blocker_taxonomy_fallback[0].recommended_repair_lane
                if blocker_taxonomy_fallback
                else "code_repair"
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
) -> None:
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


def _validation_uses_only_general_geometry_lane(
    *,
    blockers: list[str],
    taxonomy_by_blocker: dict[str, dict[str, Any]],
) -> bool:
    if not blockers:
        return True
    for blocker in blockers:
        record = taxonomy_by_blocker.get(blocker) or {}
        family_ids = [
            str(item).strip()
            for item in (record.get("family_ids") or [])
            if isinstance(item, str) and str(item).strip()
        ]
        feature_ids = [
            str(item).strip()
            for item in (record.get("feature_ids") or [])
            if isinstance(item, str) and str(item).strip()
        ]
        if any(family_id != "general_geometry" for family_id in family_ids):
            return False
        if any(feature_id != "feature.core_geometry" for feature_id in feature_ids):
            return False
    return True


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
    if latest_execution_binding is not None and latest_execution_binding.geometry_summary:
        parameter_bindings["geometry_summary"] = dict(latest_execution_binding.geometry_summary)
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

    parameter_bindings = dict(existing.parameter_bindings)
    parameter_bindings.update(incoming.parameter_bindings)

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


def _default_feature_instance_id(feature_id: str) -> str:
    normalized = str(feature_id or "").strip()
    suffix = normalized.replace("feature.", "").replace("feature:", "")
    return f"instance.{suffix}.primary"


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
    for feature_instance in sorted(
        feature_instances,
        key=_repair_priority_for_feature_instance,
    ):
        repair_intent = str(feature_instance.repair_intent or "").strip()
        if repair_intent:
            return repair_intent
    return None


def _family_repair_priority_rank(family_id: str) -> int:
    family_priority = {
        "path_sweep": 0,
        "axisymmetric_profile": 1,
        "orthogonal_union": 2,
        "nested_hollow_section": 2,
        "spherical_recess": 3,
        "pattern_distribution": 3,
        "explicit_anchor_hole": 4,
        "named_face_local_edit": 5,
        "general_geometry": 6,
    }
    return family_priority.get(str(family_id or "").strip(), 99)


def _repair_packet_priority(packet: FamilyRepairPacket) -> tuple[int, int, int, str]:
    specificity = 0
    if packet.target_anchor_summary:
        specificity += 3
    if packet.realized_anchor_summary:
        specificity += 2
    if packet.host_frame:
        specificity += 2
    if packet.recipe_id:
        specificity += 1
    blocker_rank = 3
    instance_id = str(packet.feature_instance_id or "").strip()
    if ".feature_hole_position_alignment" in instance_id or ".feature_local_anchor_alignment" in instance_id:
        blocker_rank = 0
    elif ".feature_hole_exact_center_set" in instance_id or ".feature_pattern_seed_alignment" in instance_id:
        blocker_rank = 1
    elif ".feature_countersink" in instance_id:
        blocker_rank = 2
    return (
        _family_repair_priority_rank(packet.family_id),
        blocker_rank,
        -specificity,
        0 if packet.repair_mode == "local_edit" else 1,
        packet.packet_id,
    )


def _coerce_xy_points(value: Any) -> list[list[float]]:
    points: list[list[float]] = []
    if not isinstance(value, list):
        return points
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        try:
            x = float(item[0])
            y = float(item[1])
        except (TypeError, ValueError):
            continue
        points.append([x, y])
    return points


def _build_centered_bbox_host_frame(
    *,
    host_ids: list[str],
    geometry_summary: dict[str, Any],
) -> dict[str, Any]:
    bbox = geometry_summary.get("bbox")
    bbox_min = geometry_summary.get("bbox_min")
    bbox_max = geometry_summary.get("bbox_max")
    if not (
        isinstance(bbox, list)
        and len(bbox) >= 2
        and isinstance(bbox_min, list)
        and len(bbox_min) >= 2
        and isinstance(bbox_max, list)
        and len(bbox_max) >= 2
    ):
        return {}
    try:
        width = float(bbox[0])
        depth = float(bbox[1])
        min_x = float(bbox_min[0])
        min_y = float(bbox_min[1])
        max_x = float(bbox_max[0])
        max_y = float(bbox_max[1])
    except (TypeError, ValueError):
        return {}
    if not (min_x < 0.0 < max_x and min_y < 0.0 < max_y):
        return {}
    return {
        "frame_kind": "centered_bbox_xy",
        "host_face": host_ids[0] if host_ids else "body.primary",
        "bbox": [width, depth],
        "bbox_min": [min_x, min_y],
        "bbox_max": [max_x, max_y],
        "origin": [0.0, 0.0],
        "translation_from_corner_frame": [-round(width / 2.0, 6), -round(depth / 2.0, 6)],
    }


def _normalize_points_for_host_frame(
    *,
    points: list[list[float]],
    geometry_summary: dict[str, Any],
) -> tuple[list[list[float]], bool]:
    bbox = geometry_summary.get("bbox")
    bbox_min = geometry_summary.get("bbox_min")
    bbox_max = geometry_summary.get("bbox_max")
    if not (
        points
        and isinstance(bbox, list)
        and len(bbox) >= 2
        and isinstance(bbox_min, list)
        and len(bbox_min) >= 2
        and isinstance(bbox_max, list)
        and len(bbox_max) >= 2
    ):
        return points, False
    try:
        width = float(bbox[0])
        depth = float(bbox[1])
        min_x = float(bbox_min[0])
        min_y = float(bbox_min[1])
        max_x = float(bbox_max[0])
        max_y = float(bbox_max[1])
    except (TypeError, ValueError):
        return points, False
    if not (min_x < 0.0 < max_x and min_y < 0.0 < max_y):
        return points, False
    half_width = width / 2.0
    half_depth = depth / 2.0
    requires_shift = any(
        point[0] > half_width + 1e-6 or point[1] > half_depth + 1e-6
        for point in points
    )
    if not requires_shift:
        return points, False
    normalized = [
        [round(point[0] - half_width, 6), round(point[1] - half_depth, 6)]
        for point in points
    ]
    return normalized, True


def _explicit_anchor_hole_recipe_packet(
    *,
    feature_instance: FeatureInstance,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str | None, str | None, dict[str, Any]]:
    parameter_bindings = (
        feature_instance.parameter_bindings
        if isinstance(feature_instance.parameter_bindings, dict)
        else {}
    )
    geometry_summary = (
        parameter_bindings.get("geometry_summary")
        if isinstance(parameter_bindings.get("geometry_summary"), dict)
        else {}
    )
    expected_centers = _coerce_xy_points(parameter_bindings.get("expected_local_centers"))
    realized_centers = _coerce_xy_points(parameter_bindings.get("realized_centers"))
    host_frame = _build_centered_bbox_host_frame(
        host_ids=feature_instance.host_ids,
        geometry_summary=geometry_summary,
    )
    normalized_expected, normalization_applied = _normalize_points_for_host_frame(
        points=expected_centers,
        geometry_summary=geometry_summary,
    )
    target_anchor_summary = (
        {
            "requested_centers": expected_centers,
            "normalized_local_centers": normalized_expected,
            "normalization_applied": normalization_applied,
        }
        if expected_centers
        else {}
    )
    realized_anchor_summary = {"realized_centers": realized_centers} if realized_centers else {}
    recipe_id = None
    recipe_summary = None
    recipe_skeleton: dict[str, Any] = {}
    if expected_centers:
        recipe_id = (
            "explicit_anchor_hole_centered_host_frame_array"
            if normalization_applied
            else "explicit_anchor_hole_local_anchor_array"
        )
        recipe_summary = (
            "Select the host face workplane, push the normalized center set, and rebuild the "
            "hole array with the countersink recipe on that local frame."
        )
        recipe_skeleton = {
            "host_face": feature_instance.host_ids[0] if feature_instance.host_ids else "top",
            "workplane_frame": host_frame.get("frame_kind", "host_face_local"),
            "point_strategy": "pushPoints",
            "center_source_key": "normalized_local_centers"
            if normalization_applied
            else "requested_centers",
            "hole_call": (
                "cskHole"
                if any("countersink" in blocker for blocker in feature_instance.blocker_ids)
                else "hole"
            ),
        }
    return (
        host_frame,
        target_anchor_summary,
        realized_anchor_summary,
        recipe_id,
        recipe_summary,
        recipe_skeleton,
    )


def _spherical_recess_recipe_packet(
    *,
    feature_instance: FeatureInstance,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str | None, str | None, dict[str, Any]]:
    parameter_bindings = (
        feature_instance.parameter_bindings
        if isinstance(feature_instance.parameter_bindings, dict)
        else {}
    )
    geometry_summary = (
        parameter_bindings.get("geometry_summary")
        if isinstance(parameter_bindings.get("geometry_summary"), dict)
        else {}
    )
    expected_centers = _coerce_xy_points(parameter_bindings.get("expected_local_centers"))
    realized_centers = _coerce_xy_points(parameter_bindings.get("realized_centers"))
    host_frame = _build_centered_bbox_host_frame(
        host_ids=feature_instance.host_ids,
        geometry_summary=geometry_summary,
    )
    host_face = str(
        parameter_bindings.get("host_face")
        or host_frame.get("host_face")
        or (feature_instance.host_ids[0] if feature_instance.host_ids else "top")
    ).strip() or "top"
    if host_frame:
        host_frame = {
            **host_frame,
            "host_face": host_face,
        }
    target_anchor_summary = {}
    if expected_centers:
        target_anchor_summary = {
            "expected_local_centers": expected_centers,
            "host_face": host_face,
        }
    realized_anchor_summary = {"realized_centers": realized_centers} if realized_centers else {}
    recipe_id = None
    recipe_summary = None
    recipe_skeleton: dict[str, Any] = {}
    if expected_centers:
        recipe_id = "spherical_recess_host_face_center_set"
        recipe_summary = (
            "Keep the host solid, place the full local center set on the requested host face plane, "
            "build one sphere cutter per center, union the cutters, and subtract them from the host body."
        )
        recipe_skeleton = {
            "mode": "subtree_rebuild_via_execute_build123d",
            "host_face": host_face,
            "workplane_frame": host_frame.get("frame_kind", "host_face_local"),
            "center_source_key": "expected_local_centers",
            "cutter_kind": "sphere_array_subtract",
            "sphere_center_z_strategy": "host_face_plane",
        }
    return (
        host_frame,
        target_anchor_summary,
        realized_anchor_summary,
        recipe_id,
        recipe_summary,
        recipe_skeleton,
    )


def _half_shell_profile_recipe_packet(
    *,
    feature_instance: FeatureInstance,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str | None, str | None, dict[str, Any]]:
    parameter_bindings = (
        feature_instance.parameter_bindings
        if isinstance(feature_instance.parameter_bindings, dict)
        else {}
    )
    split_axis = str(parameter_bindings.get("likely_split_axis") or "Y").strip().upper() or "Y"
    observed_bounds = parameter_bindings.get("likely_split_bounds")
    observed_spans = parameter_bindings.get("observed_spans")
    expected_half_profile_span = parameter_bindings.get("expected_half_profile_span")
    expected_length = parameter_bindings.get("expected_length")
    host_frame = {
        "frame_kind": "global_half_shell_split_frame",
        "split_axis": split_axis,
        "half_plane": "positive",
        "hole_center_frame": "global_xz",
    }
    if isinstance(observed_bounds, list) and len(observed_bounds) >= 2:
        host_frame["observed_split_bounds"] = observed_bounds
    target_anchor_summary: dict[str, Any] = {}
    if isinstance(expected_half_profile_span, (int, float)):
        target_anchor_summary["expected_half_profile_span"] = float(expected_half_profile_span)
    if isinstance(expected_length, (int, float)):
        target_anchor_summary["expected_length"] = float(expected_length)
    realized_anchor_summary: dict[str, Any] = {}
    if isinstance(observed_bounds, list) and len(observed_bounds) >= 2:
        realized_anchor_summary["observed_split_bounds"] = observed_bounds
    if isinstance(observed_spans, list) and len(observed_spans) >= 3:
        realized_anchor_summary["observed_spans"] = observed_spans
    recipe_id = None
    recipe_summary = None
    recipe_skeleton: dict[str, Any] = {}
    if (
        str(feature_instance.repair_intent or "").strip()
        == "rebuild_half_shell_profile_envelope"
        or "feature_half_shell_profile_envelope" in feature_instance.blocker_ids
    ):
        recipe_id = "half_shell_profile_global_xz_lug_hole_recipe"
        recipe_summary = (
            "Rebuild the half-shell as a positive-half-plane semi-annulus, merge the pad before "
            "the bore cut, then place the lug-hole cutters from global X/Z anchors so Y-direction "
            "holes do not drift when workplane offsets are ambiguous."
        )
        recipe_skeleton = {
            "mode": "subtree_rebuild_via_execute_build123d",
            "profile_kind": "semi_annulus_shell",
            "split_axis": split_axis,
            "half_plane": "positive",
            "pad_strategy": "merge_then_bore_cut",
            "hole_axis": "Y",
            "hole_center_frame": "global_xz",
            "cutter_kind": "y_axis_cylinder_array",
        }
    return (
        host_frame,
        target_anchor_summary,
        realized_anchor_summary,
        recipe_id,
        recipe_summary,
        recipe_skeleton,
    )


def _family_repair_packet_from_feature_instance(
    feature_instance: FeatureInstance,
) -> FamilyRepairPacket | None:
    family_id = str(feature_instance.family_id or "").strip()
    if not family_id:
        return None
    parameter_bindings = (
        feature_instance.parameter_bindings
        if isinstance(feature_instance.parameter_bindings, dict)
        else {}
    )
    parameter_keys = [
        key
        for key in parameter_bindings.keys()
        if isinstance(key, str) and key and key != "geometry_summary"
    ]
    host_frame: dict[str, Any] = {}
    target_anchor_summary: dict[str, Any] = {}
    realized_anchor_summary: dict[str, Any] = {}
    recipe_id: str | None = None
    recipe_summary: str | None = None
    recipe_skeleton: dict[str, Any] = {}
    if family_id == "spherical_recess":
        (
            host_frame,
            target_anchor_summary,
            realized_anchor_summary,
            recipe_id,
            recipe_summary,
            recipe_skeleton,
        ) = _spherical_recess_recipe_packet(feature_instance=feature_instance)
    if (
        family_id == "axisymmetric_profile"
        and str(feature_instance.repair_intent or "").strip()
        == "rebuild_half_shell_profile_envelope"
    ):
        (
            host_frame,
            target_anchor_summary,
            realized_anchor_summary,
            recipe_id,
            recipe_summary,
            recipe_skeleton,
        ) = _half_shell_profile_recipe_packet(feature_instance=feature_instance)
    if family_id in {"explicit_anchor_hole", "pattern_distribution"}:
        (
            host_frame,
            target_anchor_summary,
            realized_anchor_summary,
            recipe_id,
            recipe_summary,
            recipe_skeleton,
        ) = _explicit_anchor_hole_recipe_packet(feature_instance=feature_instance)
    if not (
        feature_instance.anchor_keys
        or parameter_keys
        or target_anchor_summary
        or realized_anchor_summary
        or recipe_id
    ):
        return None
    return FamilyRepairPacket(
        packet_id=f"repair_packet.{_slugify(feature_instance.instance_id)}",
        family_id=family_id,
        feature_instance_id=feature_instance.instance_id,
        repair_mode=feature_instance.latest_repair_mode or "subtree_rebuild",
        repair_intent=feature_instance.repair_intent,
        affected_host_ids=list(feature_instance.host_ids or ["body.primary"]),
        anchor_keys=list(feature_instance.anchor_keys),
        parameter_keys=parameter_keys,
        host_frame=host_frame,
        target_anchor_summary=target_anchor_summary,
        realized_anchor_summary=realized_anchor_summary,
        recipe_id=recipe_id,
        recipe_summary=recipe_summary,
        recipe_skeleton=recipe_skeleton,
        source_binding_id=feature_instance.linked_binding_ids[-1]
        if feature_instance.linked_binding_ids
        else None,
        source_blocker_ids=list(feature_instance.blocker_ids),
    )


def _replace_repair_packets_from_active_instances(
    graph: DomainKernelState,
    active_instances: list[FeatureInstance],
) -> None:
    packets = [
        packet
        for packet in (
            _family_repair_packet_from_feature_instance(feature_instance)
            for feature_instance in active_instances
        )
        if packet is not None
    ]
    if packets:
        packets = sorted(packets, key=_repair_packet_priority)[:1]
    graph.replace_repair_packets(packets)


def _feature_instance_digest(
    feature_instance: FeatureInstance,
    *,
    max_nodes: int,
) -> dict[str, Any]:
    return {
        "instance_id": feature_instance.instance_id,
        "family_id": feature_instance.family_id,
        "primary_feature_id": feature_instance.primary_feature_id,
        "status": feature_instance.status,
        "summary": feature_instance.summary,
        "host_ids": list(feature_instance.host_ids[:max_nodes]),
        "blocker_ids": list(feature_instance.blocker_ids[:max_nodes]),
        "anchor_keys": list(feature_instance.anchor_keys[:max_nodes]),
        "parameter_bindings": _compact_parameter_bindings(
            feature_instance.parameter_bindings
        ),
        "linked_binding_ids": list(feature_instance.linked_binding_ids[:max_nodes]),
        "latest_repair_mode": feature_instance.latest_repair_mode,
        "repair_intent": feature_instance.repair_intent,
    }


def _repair_priority_for_feature_instance(feature_instance: FeatureInstance) -> tuple[int, str]:
    family_id = str(feature_instance.family_id or "").strip()
    instance_id = str(feature_instance.instance_id or "").strip()
    blocker_priority = 99
    if family_id == "path_sweep":
        if ".feature_path_sweep_rail" in instance_id:
            blocker_priority = 0
        elif ".feature_path_sweep_profile" in instance_id:
            blocker_priority = 1
        elif ".feature_path_sweep_frame" in instance_id:
            blocker_priority = 2
        elif ".feature_path_sweep_result" in instance_id:
            blocker_priority = 3
    elif family_id == "axisymmetric_profile":
        if ".feature_half_shell_profile_envelope" in instance_id:
            blocker_priority = 0
        elif ".feature_named_plane_positive_extrude_span" in instance_id:
            blocker_priority = 1
    return (_family_repair_priority_rank(family_id), blocker_priority, instance_id)


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


def _compact_parameter_bindings(bindings: dict[str, Any]) -> dict[str, Any]:
    compacted: dict[str, Any] = {}
    for key, value in list(bindings.items())[:8]:
        if not isinstance(key, str) or not key.strip():
            continue
        compacted[key] = _sanitize_anchor_signal_value(value)
    return compacted


def _validation_blockers(latest_validation: dict[str, Any] | None) -> list[str]:
    if not isinstance(latest_validation, dict):
        return []
    blockers = latest_validation.get("blockers")
    if isinstance(blockers, list):
        normalized = [item for item in blockers if isinstance(item, str)]
        if normalized:
            return normalized
    taxonomy = _validation_blocker_taxonomy(latest_validation)
    return [
        blocker_id
        for blocker_id in (
            item.get("blocker_id")
            for item in taxonomy
            if isinstance(item, dict)
        )
        if isinstance(blocker_id, str) and blocker_id.strip()
    ]


def _validation_blocker_taxonomy(
    latest_validation: dict[str, Any] | None,
    *,
    graph: DomainKernelState | None = None,
) -> list[dict[str, Any]]:
    taxonomy = [
        {
            "blocker_id": item.blocker_id,
            "normalized_blocker_id": item.normalized_blocker_id,
            "family_ids": item.family_ids,
            "feature_ids": item.feature_ids,
            "primary_feature_id": item.primary_feature_id,
            "recommended_repair_lane": item.recommended_repair_lane,
            "evidence_source": item.evidence_source,
            "completeness_relevance": item.completeness_relevance,
            "severity": item.severity,
        }
        for item in taxonomy_records_from_validation_payload(latest_validation)
        if str(getattr(item, "completeness_relevance", "") or "core").strip().lower()
        != "diagnostic"
    ]
    blockers = [
        blocker_id
        for blocker_id in (
            (latest_validation or {}).get("blockers")
            if isinstance((latest_validation or {}).get("blockers"), list)
            else []
        )
        if isinstance(blocker_id, str) and blocker_id.strip()
    ]
    if not blockers:
        blockers = [
            str(item.get("blocker_id")).strip()
            for item in taxonomy
            if isinstance(item, dict)
            and isinstance(item.get("blocker_id"), str)
            and str(item.get("blocker_id")).strip()
        ]
    return _contextualize_validation_blocker_taxonomy(
        blocker_taxonomy=taxonomy,
        blockers=blockers,
        graph=graph,
    )


def _contextualize_validation_blocker_taxonomy(
    *,
    blocker_taxonomy: list[dict[str, Any]],
    blockers: list[str],
    graph: DomainKernelState | None,
) -> list[dict[str, Any]]:
    if graph is None:
        return blocker_taxonomy
    requirement_feature_ids = {
        node.node_id
        for node in graph.nodes.values()
        if node.kind == "feature"
        and isinstance(node.node_id, str)
        and node.node_id.startswith("feature.")
    }
    if "feature.spherical_recess" not in requirement_feature_ids:
        return blocker_taxonomy
    taxonomy_by_blocker = {
        str(item.get("blocker_id")).strip(): item
        for item in blocker_taxonomy
        if isinstance(item, dict)
        and isinstance(item.get("blocker_id"), str)
        and str(item.get("blocker_id")).strip()
    }
    normalized_blockers = [
        blocker_id.strip()
        for blocker_id in blockers
        if isinstance(blocker_id, str) and blocker_id.strip()
    ]
    if not normalized_blockers:
        normalized_blockers = list(taxonomy_by_blocker.keys())
    contextualized: list[dict[str, Any]] = []
    for blocker_id in normalized_blockers:
        record = taxonomy_by_blocker.get(blocker_id)
        contextualized.append(
            _contextualize_blocker_taxonomy_record_for_graph(
                graph=graph,
                blocker_id=blocker_id,
                taxonomy_record=record,
            )
        )
    return contextualized or blocker_taxonomy


def _contextualize_blocker_taxonomy_record_for_graph(
    *,
    graph: DomainKernelState,
    blocker_id: str,
    taxonomy_record: dict[str, Any] | None,
) -> dict[str, Any]:
    requirement_feature_ids = {
        node.node_id
        for node in graph.nodes.values()
        if node.kind == "feature"
        and isinstance(node.node_id, str)
        and node.node_id.startswith("feature.")
    }
    requirement_family_ids = {
        feature_id.replace("feature.", "")
        for feature_id in requirement_feature_ids
    }
    blocker_id = str(blocker_id or "").strip()
    if not blocker_id:
        return dict(taxonomy_record or {})
    if not _should_prefer_spherical_recess_taxonomy(
        blocker_id=blocker_id,
        requirement_family_ids=requirement_family_ids,
    ):
        return dict(taxonomy_record or {})
    family_ids = ["spherical_recess"]
    if "pattern_distribution" in requirement_family_ids:
        family_ids.append("pattern_distribution")
    if blocker_id in {"feature_hole", "feature_local_anchor_alignment"}:
        family_ids.extend(["explicit_anchor_hole", "named_face_local_edit"])
    family_ids = list(dict.fromkeys(family_ids))
    feature_ids = _feature_ids_for_runtime_family_ids(family_ids)
    existing = dict(taxonomy_record or {})
    return {
        "blocker_id": blocker_id,
        "normalized_blocker_id": str(
            existing.get("normalized_blocker_id") or blocker_id
        ).strip()
        or blocker_id,
        "family_ids": family_ids,
        "feature_ids": feature_ids,
        "primary_feature_id": "feature.spherical_recess",
        "recommended_repair_lane": str(
            existing.get("recommended_repair_lane") or "code_repair"
        ).strip()
        or "code_repair",
        "evidence_source": str(existing.get("evidence_source") or "validation").strip()
        or "validation",
        "completeness_relevance": str(
            existing.get("completeness_relevance") or "core"
        ).strip()
        or "core",
        "severity": str(existing.get("severity") or "blocking").strip() or "blocking",
    }


def _should_prefer_spherical_recess_taxonomy(
    *,
    blocker_id: str,
    requirement_family_ids: set[str],
) -> bool:
    if "spherical_recess" not in requirement_family_ids:
        return False
    return blocker_id in {
        "feature_hole",
        "feature_local_anchor_alignment",
        "feature_profile_shape_alignment",
        "feature_pattern",
        "feature_pattern_seed",
        "feature_pattern_seed_alignment",
        "feature_spherical_recess",
    }


def _feature_ids_for_runtime_family_ids(family_ids: list[str]) -> list[str]:
    feature_ids: list[str] = []
    seen: set[str] = set()
    for family_id in family_ids:
        normalized = str(family_id or "").strip()
        if not normalized:
            continue
        candidates = [f"feature.{normalized}"]
        if normalized == "explicit_anchor_hole":
            candidates.append("feature.named_face_local_edit")
        for feature_id in candidates:
            if feature_id in seen:
                continue
            seen.add(feature_id)
            feature_ids.append(feature_id)
    return feature_ids or ["feature.core_geometry"]


def _feature_ids_from_taxonomy_record(taxonomy_record: dict[str, Any] | None) -> list[str]:
    if not isinstance(taxonomy_record, dict):
        return []
    return [
        feature_id
        for feature_id in (taxonomy_record.get("feature_ids") or [])
        if isinstance(feature_id, str) and feature_id.strip()
    ]


def _blocker_to_feature_ids(blocker: str) -> list[str]:
    if blocker == "feature_profile_shape_alignment":
        return ["feature.core_geometry"]
    taxonomy = classify_blocker_taxonomy_many([blocker])
    if taxonomy:
        feature_ids = [
            feature_id
            for feature_id in taxonomy[0].feature_ids
            if isinstance(feature_id, str) and feature_id.strip()
        ]
        if feature_ids:
            return feature_ids
    lowered = blocker.lower()
    if "polygon" in lowered or "triangle" in lowered or "hexagon" in lowered:
        return ["feature.regular_polygon_profile"]
    if "profile_shape" in lowered:
        return ["feature.core_geometry"]
    return ["feature.core_geometry"]


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
    anchor_signal_keys_by_family: dict[str, list[str]] = {}
    signal_values_by_family: dict[str, dict[str, Any]] = {}
    for item in probes:
        if not isinstance(item, dict):
            continue
        family = str(item.get("family") or "").strip()
        if not family:
            continue
        if bool(item.get("success")) and family not in successful_families:
            successful_families.append(family)
        signals = item.get("signals")
        if not isinstance(signals, dict):
            continue
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
        if anchor_keys:
            anchor_signal_keys_by_family[family] = anchor_keys
            signal_values_by_family[family] = {
                key: _sanitize_anchor_signal_value(signals.get(key))
                for key in anchor_keys
                if key in signals
            }
    return {
        "probe_count": sum(1 for item in probes if isinstance(item, dict)),
        "successful_probe_count": len(successful_families),
        "successful_families": successful_families,
        "anchor_signal_keys_by_family": anchor_signal_keys_by_family,
        "signal_values_by_family": signal_values_by_family,
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
    if value.startswith(("[", "{", "(")):
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
        if "realized_centers" in signals:
            normalized["realized_centers"] = signals["realized_centers"]
        elif "actual_snapshot_centers" in signals:
            normalized["realized_centers"] = signals["actual_snapshot_centers"]
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
        if require_geometry and not binding.geometry_summary:
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


def _requirements_text(requirements: dict[str, Any]) -> str:
    description = requirements.get("description")
    if isinstance(description, str) and description.strip():
        return description.strip()
    return json.dumps(requirements, ensure_ascii=False, sort_keys=True)


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
                recommended_repair_lane = lane
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
                    recommended_repair_lane = lane
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
                recommended_repair_lane = lane
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
            recommended_repair_lane = str(
                failure_taxonomy.get("recommended_repair_lane") or ""
            ).strip() or recommended_repair_lane
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


def _stable_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower())
    return normalized.strip("_") or "unknown"


def _requirement_has_explicit_xy_coordinate_pair(requirement_text: str) -> bool:
    if not isinstance(requirement_text, str) or not requirement_text.strip():
        return False
    if re.search(
        r"\(\s*[-+]?[0-9]+(?:\.[0-9]+)?\s*,\s*[-+]?[0-9]+(?:\.[0-9]+)?\s*\)",
        requirement_text,
    ):
        return True
    return bool(
        re.search(
            r"\b[xyz]\s*=\s*(?:±\s*)?[-+]?[0-9]+(?:\.[0-9]+)?[^.]{0,80}\b[xyz]\s*=\s*(?:±\s*)?[-+]?[0-9]+(?:\.[0-9]+)?",
            requirement_text,
            re.IGNORECASE,
        )
    )
