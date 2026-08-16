from __future__ import annotations

import unittest

from handlers.domain_onboarding.config import DomainOnboardingConfig
from handlers.domain_onboarding.paper_recommendations import SurveyRecommendationPolicy
from handlers.domain_onboarding.ranking import WeightedPaperRanker
from handlers.domain_onboarding.schemas import PaperCandidate

from .fakes import make_plan


def candidate(
    paper_id: str,
    title: str,
    *,
    year: int,
    citations: int,
    survey_sources: list[str] | None = None,
) -> PaperCandidate:
    return PaperCandidate(
        paper_id=paper_id,
        title=title,
        abstract=(
            "retrieval augmented generation methods architectures evaluation benchmark"
        ),
        year=year,
        url=f"https://example.org/{paper_id}",
        citation_count=citations,
        source="semantic_scholar",
        survey_source_ids=survey_sources or [],
    )


class SurveyRecommendationPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = DomainOnboardingConfig(
            recommendation_survey_limit=2,
            recommendation_reference_limit=1,
            enforce_core_paper_coverage=False,
        )
        self.policy = SurveyRecommendationPolicy(
            WeightedPaperRanker(self.config),
            self.config,
        )
        self.plan = make_plan()

    def test_recent_surveys_are_visible_before_older_influential_surveys(self) -> None:
        surveys, candidate_count = self.policy.select_surveys(
            [
                candidate(
                    "recent-survey",
                    "A Recent Survey of Retrieval Augmented Generation",
                    year=2025,
                    citations=20,
                ),
                candidate(
                    "old-survey",
                    "A Survey of Retrieval Augmented Generation",
                    year=2019,
                    citations=10_000,
                ),
            ],
            self.plan,
            language="zh-CN",
        )

        self.assertEqual(candidate_count, 2)
        self.assertEqual(surveys[0].paper_id, "recent-survey")
        self.assertEqual(surveys[0].recommendation_category, "recent_survey")
        self.assertTrue(surveys[0].recommendation_reason)

    def test_survey_references_keep_provenance_and_merge_as_both_when_needed(self) -> None:
        references, candidate_count = self.policy.select_references(
            [
                candidate(
                    "method-paper",
                    "Retrieval Augmented Generation Method Architecture",
                    year=2023,
                    citations=500,
                    survey_sources=["recent-survey"],
                )
            ],
            self.plan,
            language="zh-CN",
        )
        evidence = WeightedPaperRanker(self.config).rank(
            [
                candidate(
                    "method-paper",
                    "Retrieval Augmented Generation Method Architecture",
                    year=2023,
                    citations=500,
                )
            ],
            self.plan,
            limit=1,
        ).papers

        merged = self.policy.merge_with_evidence(evidence, references)

        self.assertEqual(candidate_count, 1)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].paper_usage, "both")
        self.assertEqual(merged[0].recommendation_category, "survey_reference")
        self.assertEqual(merged[0].survey_source_ids, ["recent-survey"])


if __name__ == "__main__":
    unittest.main()
