from __future__ import annotations

from sub_agent_runtime.semantic_kernel.models import DomainKernelState


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


__all__ = ["build_domain_kernel_digest"]
