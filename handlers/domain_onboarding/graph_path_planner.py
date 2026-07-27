"""Produce a deterministic graph-constrained learning order with safe fallback."""

from __future__ import annotations

from collections import defaultdict

from .schemas import GraphPathPlan, KnowledgeGraphSnapshot


class GraphBasedPathPlanner:
    def plan(self, graph: KnowledgeGraphSnapshot) -> GraphPathPlan:
        if not graph.validation.valid:
            return GraphPathPlan(
                fallback_used=True,
                reason="graph validation failed; retain the generated learning path",
            )
        eligible = {
            node.node_id
            for node in graph.nodes
            if node.node_type in {"prerequisite", "development_stage"}
        }
        adjacency: dict[str, list[str]] = defaultdict(list)
        indegree = {node_id: 0 for node_id in eligible}
        for edge in graph.edges:
            if edge.edge_type not in {"precedes", "requires"}:
                continue
            if edge.source_id not in eligible or edge.target_id not in eligible:
                continue
            # A requires B means B must precede A.
            source_id, target_id = (
                (edge.target_id, edge.source_id)
                if edge.edge_type == "requires"
                else (edge.source_id, edge.target_id)
            )
            adjacency[source_id].append(target_id)
            indegree[target_id] += 1
        label_order = {node.node_id: index for index, node in enumerate(graph.nodes)}
        ready = sorted(
            (node_id for node_id, degree in indegree.items() if degree == 0),
            key=lambda node_id: label_order[node_id],
        )
        ordered: list[str] = []
        while ready:
            node_id = ready.pop(0)
            ordered.append(node_id)
            for target_id in adjacency[node_id]:
                indegree[target_id] -= 1
                if indegree[target_id] == 0:
                    ready.append(target_id)
                    ready.sort(key=lambda item: label_order[item])
        if len(ordered) != len(eligible):
            return GraphPathPlan(fallback_used=True, reason="topological planning failed")
        return GraphPathPlan(ordered_node_ids=ordered)
