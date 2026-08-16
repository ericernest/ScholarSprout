from __future__ import annotations

import unittest

from handlers.domain_onboarding.config import DomainOnboardingConfig
from handlers.domain_onboarding.schemas import RankingResult, RankingStats, RankedPaper
from handlers.domain_onboarding.subdirection_research import (
    SubdirectionPaperRanker,
    SubdirectionResearchPolicy,
)

from .fakes import make_plan


def ranked_paper(
    paper_id: str,
    *,
    role: str,
    abstract: str = "branch-specific method and evaluation evidence",
) -> RankedPaper:
    return RankedPaper(
        paper_id=paper_id,
        title=f"Paper {paper_id}",
        abstract=abstract,
        year=2025,
        url=f"https://example.org/{paper_id}",
        source="test",
        relevance_score=0.9,
        recency_score=0.9,
        final_score=0.85,
        paper_role=role,
    )


class SubdirectionPlanningTests(unittest.TestCase):
    def test_legacy_expected_directions_gain_two_retrieval_queries_each(self) -> None:
        plan = make_plan()

        self.assertEqual(len(plan.subdirection_plans), 3)
        self.assertEqual(
            [branch.name_zh for branch in plan.subdirection_plans],
            plan.expected_subdirections,
        )
        self.assertTrue(
            all(len(branch.search_queries) == 2 for branch in plan.subdirection_plans)
        )
        self.assertTrue(
            all(
                query.path_id == branch.subdirection_id
                for branch in plan.subdirection_plans
                for query in branch.search_queries
            )
        )


class SubdirectionEvidencePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = DomainOnboardingConfig(
            subdirection_min_papers=3,
            subdirection_min_abstract_papers=2,
        )
        self.policy = SubdirectionResearchPolicy(self.config)
        self.branch = make_plan().subdirection_plans[0]

    def test_evidence_requires_count_abstracts_method_and_review_or_evaluation(self) -> None:
        limited = self.policy.assess(
            self.branch,
            [ranked_paper("method", role="method")],
            query_count=2,
            supplemental_query_count=0,
        )
        sufficient = self.policy.assess(
            self.branch,
            [
                ranked_paper("method", role="method"),
                ranked_paper("evaluation", role="evaluation"),
                ranked_paper("frontier", role="frontier"),
            ],
            query_count=2,
            supplemental_query_count=1,
        )

        self.assertEqual(limited.status, "limited")
        self.assertIn("insufficient_paper_count", limited.warnings)
        self.assertIn("missing_survey_or_evaluation_paper", limited.warnings)
        self.assertEqual(sufficient.status, "sufficient")
        self.assertEqual(sufficient.warnings, [])

    def test_merge_round_robins_direction_papers_before_global_fill(self) -> None:
        bundles = [
            self.policy.assess(
                branch,
                [
                    ranked_paper(f"direction-{index}-a", role="method"),
                    ranked_paper(f"direction-{index}-b", role="evaluation"),
                    ranked_paper(f"direction-{index}-c", role="frontier"),
                ],
                query_count=2,
                supplemental_query_count=0,
            )
            for index, branch in enumerate(make_plan().subdirection_plans, start=1)
        ]
        global_papers = [
            ranked_paper(f"global-{index}", role="method") for index in range(6)
        ]

        merged = self.policy.merge(global_papers, bundles)

        self.assertEqual(len(merged), 12)
        for index in range(1, 4):
            self.assertTrue(
                any(
                    paper.paper_id.startswith(f"direction-{index}-")
                    for paper in merged
                )
            )


class _FixedBaseRanker:
    def __init__(self, papers: list[RankedPaper]) -> None:
        self.papers = papers

    def rank(self, papers, plan, *, limit):
        return RankingResult(
            papers=[paper.model_copy(deep=True) for paper in self.papers[:limit]],
            stats=RankingStats(),
        )


class SubdirectionPaperRankerTests(unittest.TestCase):
    def test_large_citation_count_does_not_override_branch_relevance(self) -> None:
        relevant = ranked_paper("relevant", role="method")
        relevant.relevance_score = 0.95
        relevant.final_score = 0.9
        relevant.citation_count = 0
        relevant.citation_status = "known"
        weak = ranked_paper("weak", role="method")
        weak.relevance_score = 0.25
        weak.final_score = 0.2
        weak.citation_count = 100_000
        weak.citation_status = "known"
        config = DomainOnboardingConfig(
            subdirection_papers_per_direction=2,
            subdirection_min_papers=2,
        )
        ranker = SubdirectionPaperRanker(
            _FixedBaseRanker([weak, relevant]),
            config,
        )
        plan = make_plan()

        result = ranker.rank(
            [],
            plan,
            plan.subdirection_plans[0],
            limit=2,
        )

        self.assertEqual(result.papers[0].paper_id, "relevant")
        self.assertEqual(
            result.stats.ranking_strategy,
            "subdirection_unified_score_role_gate",
        )


if __name__ == "__main__":
    unittest.main()
