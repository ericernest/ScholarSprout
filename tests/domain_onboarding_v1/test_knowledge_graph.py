from __future__ import annotations

import unittest

from handlers.domain_onboarding.config import DomainOnboardingConfig
from handlers.domain_onboarding.graph_path_planner import GraphBasedPathPlanner
from handlers.domain_onboarding.graph_validator import DomainKnowledgeGraphValidator
from handlers.domain_onboarding.metrics import DomainOnboardingRequestTrace
from handlers.domain_onboarding.schemas import (
    DomainOnboardingRequest,
    GraphValidationReport,
    KnowledgeGraphEdge,
    KnowledgeGraphNode,
    KnowledgeGraphSnapshot,
)

from .fakes import make_candidates, make_generation_payload
from .test_pipeline_handler_metrics import make_pipeline


class KnowledgeGraphTests(unittest.TestCase):
    def test_pipeline_builds_grounded_graph_after_hard_gates_pass(self) -> None:
        paper_ids = [paper.paper_id for paper in make_candidates()]
        config = DomainOnboardingConfig(knowledge_graph_enabled=True)
        pipeline = make_pipeline([make_generation_payload(paper_ids)], config=config)
        trace = DomainOnboardingRequestTrace()

        result = pipeline.run(DomainOnboardingRequest(query="RAG"), trace)

        self.assertTrue(result.quality.passed_hard_gates)
        self.assertIsNotNone(result.knowledge_graph)
        graph = result.knowledge_graph
        self.assertTrue(graph.validation.valid)
        self.assertFalse(graph.path_plan.fallback_used)
        graph_papers = {
            node.paper_id for node in graph.nodes if node.node_type == "paper"
        }
        self.assertEqual(graph_papers, set(graph.selected_paper_ids))
        self.assertEqual(trace.knowledge_graph_node_count, len(graph.nodes))

    def test_graph_is_disabled_by_default(self) -> None:
        paper_ids = [paper.paper_id for paper in make_candidates()]
        result = make_pipeline([make_generation_payload(paper_ids)]).run(
            DomainOnboardingRequest(query="RAG"), DomainOnboardingRequestTrace()
        )
        self.assertIsNone(result.knowledge_graph)
        self.assertNotIn("knowledge_graph", result.to_response())

    def test_validator_rejects_dangling_edges_unknown_papers_and_cycles(self) -> None:
        graph = KnowledgeGraphSnapshot(
            request_id="request",
            quality_policy_version="domain-quality-v1.0.0",
            selected_paper_ids=["paper-allowed"],
            nodes=[
                KnowledgeGraphNode(
                    node_id="stage-a",
                    node_type="development_stage",
                    label="A",
                    source_path="development_stages.0",
                ),
                KnowledgeGraphNode(
                    node_id="stage-b",
                    node_type="development_stage",
                    label="B",
                    source_path="development_stages.1",
                ),
                KnowledgeGraphNode(
                    node_id="paper-other",
                    node_type="paper",
                    label="Other",
                    source_path="papers.0",
                    paper_id="paper-other",
                ),
            ],
            edges=[
                KnowledgeGraphEdge(
                    source_id="stage-a",
                    target_id="stage-b",
                    edge_type="precedes",
                    source_path="development_stages",
                ),
                KnowledgeGraphEdge(
                    source_id="stage-b",
                    target_id="stage-a",
                    edge_type="precedes",
                    source_path="development_stages",
                ),
                KnowledgeGraphEdge(
                    source_id="stage-a",
                    target_id="missing",
                    edge_type="references",
                    source_path="development_stages.0.related_paper_ids",
                ),
            ],
            validation=GraphValidationReport(valid=False),
        )

        report = DomainKnowledgeGraphValidator().validate(graph)

        self.assertFalse(report.valid)
        issue_types = {issue.issue_type for issue in report.issues}
        self.assertEqual(
            issue_types,
            {"dangling_edge", "unknown_paper", "dependency_cycle"},
        )
        graph.validation = report
        plan = GraphBasedPathPlanner().plan(graph)
        self.assertTrue(plan.fallback_used)

    def test_active_graph_mode_is_rejected_until_promotion(self) -> None:
        with self.assertRaisesRegex(ValueError, "shadow mode only"):
            DomainOnboardingConfig(
                knowledge_graph_enabled=True,
                knowledge_graph_shadow_mode=False,
            )


if __name__ == "__main__":
    unittest.main()
