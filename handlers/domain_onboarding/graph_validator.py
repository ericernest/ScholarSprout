"""Deterministic integrity checks for domain onboarding knowledge graphs."""

from __future__ import annotations

from collections import Counter, defaultdict

from .schemas import GraphValidationIssue, GraphValidationReport, KnowledgeGraphSnapshot


class DomainKnowledgeGraphValidator:
    def validate(self, graph: KnowledgeGraphSnapshot) -> GraphValidationReport:
        issues: list[GraphValidationIssue] = []
        counts = Counter(node.node_id for node in graph.nodes)
        for node_id, count in counts.items():
            if count > 1:
                issues.append(GraphValidationIssue(
                    issue_type="duplicate_node",
                    target_id=node_id,
                    message=f"node id appears {count} times",
                ))
        node_ids = set(counts)
        allowed_papers = set(graph.selected_paper_ids)
        for node in graph.nodes:
            label = node.label.strip()
            if (
                (label.startswith("{") and label.endswith("}"))
                or (label.startswith("[") and label.endswith("]"))
            ):
                issues.append(GraphValidationIssue(
                    issue_type="malformed_label",
                    target_id=node.node_id,
                    message="node label looks like a serialized container",
                ))
            if node.node_type == "paper" and node.node_id not in allowed_papers:
                issues.append(GraphValidationIssue(
                    issue_type="unknown_paper",
                    target_id=node.node_id,
                    message="paper node is not in the selected grounded paper set",
                ))
        for edge in graph.edges:
            for node_id in (edge.source_id, edge.target_id):
                if node_id not in node_ids:
                    issues.append(GraphValidationIssue(
                        issue_type="dangling_edge",
                        target_id=node_id,
                        message=f"{edge.edge_type} edge references a missing node",
                    ))
        if _has_dependency_cycle(node_ids, graph.edges):
            issues.append(GraphValidationIssue(
                issue_type="dependency_cycle",
                message="learning dependencies contain a cycle",
            ))
        return GraphValidationReport(valid=not issues, issues=issues)


def _has_dependency_cycle(node_ids: set[str], edges: list[object]) -> bool:
    adjacency: dict[str, list[str]] = defaultdict(list)
    indegree = {node_id: 0 for node_id in node_ids}
    for edge in edges:
        if edge.edge_type not in {"precedes", "requires"}:
            continue
        source_id, target_id = (
            (edge.target_id, edge.source_id)
            if edge.edge_type == "requires"
            else (edge.source_id, edge.target_id)
        )
        adjacency[source_id].append(target_id)
        indegree[target_id] = indegree.get(target_id, 0) + 1
    ready = [node_id for node_id, degree in indegree.items() if degree == 0]
    visited = 0
    while ready:
        node_id = ready.pop()
        visited += 1
        for target_id in adjacency[node_id]:
            indegree[target_id] -= 1
            if indegree[target_id] == 0:
                ready.append(target_id)
    return visited != len(indegree)
