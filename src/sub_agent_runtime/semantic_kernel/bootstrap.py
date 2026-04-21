from __future__ import annotations

import hashlib
import json
from typing import Any

from sandbox_mcp_server.registry import (
    analyze_requirement_semantics,
    infer_requirement_probe_families,
    requirement_requests_path_sweep,
)

from sub_agent_runtime.semantic_kernel.models import (
    DomainKernelState,
    FeatureGraphEdge,
    FeatureGraphNode,
    FeatureInstance,
)

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
        ["body.primary", *[node.node_id for node in feature_nodes]]
        or ["intent.root", "body.primary"]
    )
    graph.seed_revision("run_start")
    return graph


def build_initial_feature_graph(requirements: dict[str, Any]) -> DomainKernelState:
    return build_initial_domain_kernel_state(requirements)


def initialize_domain_kernel_state(requirements: dict[str, Any]) -> DomainKernelState:
    return build_initial_domain_kernel_state(requirements)


def initialize_feature_graph(requirements: dict[str, Any]) -> DomainKernelState:
    return initialize_domain_kernel_state(requirements)

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

def _requirements_text(requirements: dict[str, Any]) -> str:
    description = requirements.get("description")
    if isinstance(description, str) and description.strip():
        return description.strip()
    return json.dumps(requirements, ensure_ascii=False, sort_keys=True)

def _stable_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()

def _default_feature_instance_id(feature_id: str) -> str:
    normalized = str(feature_id or "").strip()
    suffix = normalized.replace("feature.", "").replace("feature:", "")
    return f"instance.{suffix}.primary"
