"""Build a provenance-preserving graph only from grounded onboarding output."""

from __future__ import annotations

from .schemas import (
    DomainOnboardingOutput,
    KnowledgeGraphEdge,
    KnowledgeGraphNode,
    KnowledgeGraphSnapshot,
    GraphValidationReport,
    stable_id,
)


class DomainKnowledgeGraphBuilder:
    def build(
        self,
        output: DomainOnboardingOutput,
        *,
        request_id: str,
        quality_policy_version: str,
    ) -> KnowledgeGraphSnapshot:
        nodes: list[KnowledgeGraphNode] = []
        edges: list[KnowledgeGraphEdge] = []
        domain_id = stable_id("domain", output.domain)
        nodes.append(KnowledgeGraphNode(
            node_id=domain_id,
            node_type="domain",
            label=output.domain,
            source_path="domain",
        ))
        for index, prerequisite in enumerate(output.prerequisites):
            node_id = str(prerequisite.prerequisite_id)
            nodes.append(KnowledgeGraphNode(
                node_id=node_id,
                node_type="prerequisite",
                label=prerequisite.name,
                source_path=f"prerequisites.{index}",
            ))
            edges.append(KnowledgeGraphEdge(
                source_id=domain_id,
                target_id=node_id,
                edge_type="has_prerequisite",
                source_path=f"prerequisites.{index}",
            ))
        stage_ids: list[str] = []
        for index, stage in enumerate(output.development_stages):
            node_id = str(stage.stage_id)
            stage_ids.append(node_id)
            nodes.append(KnowledgeGraphNode(
                node_id=node_id,
                node_type="development_stage",
                label=stage.name,
                source_path=f"development_stages.{index}",
            ))
            edges.append(KnowledgeGraphEdge(
                source_id=domain_id,
                target_id=node_id,
                edge_type="has_stage",
                source_path=f"development_stages.{index}",
            ))
            for prerequisite_id in stage.prerequisite_ids:
                edges.append(KnowledgeGraphEdge(
                    source_id=node_id,
                    target_id=prerequisite_id,
                    edge_type="requires",
                    source_path=f"development_stages.{index}.prerequisite_ids",
                ))
            for paper_id in stage.related_paper_ids:
                edges.append(KnowledgeGraphEdge(
                    source_id=node_id,
                    target_id=paper_id,
                    edge_type="references",
                    source_path=f"development_stages.{index}.related_paper_ids",
                ))
        for source_id, target_id in zip(stage_ids, stage_ids[1:]):
            edges.append(KnowledgeGraphEdge(
                source_id=source_id,
                target_id=target_id,
                edge_type="precedes",
                source_path="development_stages",
            ))
        for index, name in enumerate(output.current_landscape.subdirections):
            node_id = output.current_landscape.subdirection_ids[name]
            nodes.append(KnowledgeGraphNode(
                node_id=node_id,
                node_type="subdirection",
                label=name,
                source_path=f"current_landscape.subdirections.{index}",
            ))
            edges.append(KnowledgeGraphEdge(
                source_id=domain_id,
                target_id=node_id,
                edge_type="has_subdirection",
                source_path=f"current_landscape.subdirections.{index}",
            ))
        graph_papers = list(
            {
                paper.paper_id: paper
                for paper in [*output.evidence_papers, *output.papers]
            }.values()
        )
        for index, paper in enumerate(graph_papers):
            nodes.append(KnowledgeGraphNode(
                node_id=paper.paper_id,
                node_type="paper",
                label=paper.title,
                source_path=f"evidence_papers_or_papers.{index}",
                paper_id=paper.paper_id,
            ))
        for index, claim in enumerate(output.evidence_claims):
            claim_id = str(claim.claim_id)
            nodes.append(KnowledgeGraphNode(
                node_id=claim_id,
                node_type="claim",
                label=claim.claim,
                source_path=f"evidence_claims.{index}",
            ))
            for paper_id in claim.supporting_paper_ids:
                edges.append(KnowledgeGraphEdge(
                    source_id=claim_id,
                    target_id=paper_id,
                    edge_type="supported_by",
                    source_path=f"evidence_claims.{index}.supporting_paper_ids",
                ))
        unique_nodes = list(dict.fromkeys(node.node_id for node in nodes))
        node_by_id = {node.node_id: node for node in nodes}
        deduplicated_nodes = [node_by_id[node_id] for node_id in unique_nodes]
        edge_keys = list(
            dict.fromkeys((edge.source_id, edge.target_id, edge.edge_type) for edge in edges)
        )
        edge_by_key = {
            (edge.source_id, edge.target_id, edge.edge_type): edge for edge in edges
        }
        deduplicated_edges = [edge_by_key[key] for key in edge_keys]
        return KnowledgeGraphSnapshot(
            request_id=request_id,
            quality_policy_version=quality_policy_version,
            selected_paper_ids=[paper.paper_id for paper in graph_papers],
            nodes=deduplicated_nodes,
            edges=deduplicated_edges,
            validation=GraphValidationReport(valid=False),
        )
