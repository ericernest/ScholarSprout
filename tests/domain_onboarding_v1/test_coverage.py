from __future__ import annotations

import unittest

from handlers.domain_onboarding.config import DomainOnboardingConfig
from handlers.domain_onboarding.coverage import PaperCoverageAnalyzer
from handlers.domain_onboarding.schemas import RankedPaper

from .fakes import make_plan


def ranked_paper(paper_id: str, title: str, abstract: str, role: str) -> RankedPaper:
    return RankedPaper(
        paper_id=paper_id,
        title=title,
        abstract=abstract,
        url=f"https://example.org/{paper_id}",
        source="test",
        relevance_score=0.8,
        recency_score=0.5,
        diversity_score=0.5,
        final_score=0.7,
        paper_role=role,
    )


class CoverageAnalyzerTests(unittest.TestCase):
    def test_reports_only_uncovered_subdirection_with_targeted_query(self) -> None:
        plan = make_plan().model_copy(
            update={"expected_subdirections": ["retrieval", "evaluation"]}
        )
        papers = [
            ranked_paper(
                "method",
                "Dense Retrieval Method",
                "retrieval indexing and passage ranking",
                "method",
            ),
            ranked_paper(
                "survey",
                "Retrieval Survey",
                "survey of retrieval methods",
                "survey",
            ),
        ]

        analysis = PaperCoverageAnalyzer(DomainOnboardingConfig()).analyze(plan, papers)

        uncovered = [gap for gap in analysis.gaps if gap.subdirection == "evaluation"]
        self.assertEqual(len(uncovered), 1)
        self.assertEqual(uncovered[0].missing_roles, ["method"])
        self.assertIn('"evaluation"', uncovered[0].supplemental_queries[0])
        self.assertFalse(
            any(
                gap.subdirection == "retrieval" and gap.missing_roles == ["method"]
                for gap in analysis.gaps
            )
        )

    def test_reports_missing_global_paper_roles(self) -> None:
        analysis = PaperCoverageAnalyzer(DomainOnboardingConfig()).analyze(
            make_plan(),
            [
                ranked_paper(
                    "method",
                    "RAG Retrieval Method",
                    "retrieval generation evaluation",
                    "method",
                )
            ],
        )

        missing = {
            gap.missing_roles[0]
            for gap in analysis.gaps
            if gap.subdirection == make_plan().normalized_domain
        }
        self.assertEqual(missing, {"survey", "foundational", "evaluation", "frontier"})


if __name__ == "__main__":
    unittest.main()
