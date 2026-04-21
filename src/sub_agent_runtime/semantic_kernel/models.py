from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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
        from sub_agent_runtime.semantic_kernel import _core

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
        active_packets = _core._sorted_active_repair_packets(self.repair_packets.values())
        latest_packet = active_packets[0] if active_packets else None
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
            "node_counts": _core._count_nodes_by_kind(self.nodes.values()),
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
                _core._feature_instance_digest(feature_instance, max_nodes=max_nodes)
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
                _core._feature_instance_digest(feature_instance, max_nodes=max_nodes)
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
            "latest_packet_anchor_summary": (
                dict(latest_packet.target_anchor_summary) if latest_packet else {}
            ),
            "latest_packet_recipe_skeleton": (
                dict(latest_packet.recipe_skeleton) if latest_packet else {}
            ),
            "grounding_blocker_ids": (
                list(latest_binding.blocker_ids[:max_nodes]) if latest_binding else []
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
        from sub_agent_runtime.semantic_kernel import _core

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
            active_repair_packets = _core._sorted_active_repair_packets(
                self.repair_packets.values()
            )
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
                for packet in active_repair_packets[:max_bindings]
            ]
        if include_revision_history:
            payload["revision_history"] = [
                entry.to_dict()
                for entry in self.revision_history[-max_revisions:]
            ]
        return payload


FeatureGraphState = DomainKernelState


__all__ = [
    "DomainKernelPatch",
    "DomainKernelState",
    "FeatureGraphEdge",
    "FeatureGraphNode",
    "FeatureGraphNodeStore",
    "FeatureGraphState",
    "FeatureInstance",
    "FamilyRepairPacket",
    "KernelBinding",
    "KernelRevisionEntry",
    "PatchFeatureGraphInput",
    "QueryGraphStateInput",
]
