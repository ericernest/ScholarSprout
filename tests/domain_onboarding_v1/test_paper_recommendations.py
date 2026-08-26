from __future__ import annotations

import unittest

from handlers.domain_onboarding.config import DomainOnboardingConfig
from handlers.domain_onboarding.paper_recommendations import SurveyRecommendationPolicy
from handlers.domain_onboarding.ranking import WeightedPaperRanker
from handlers.domain_onboarding.schemas import PaperCandidate
from handlers.domain_onboarding.schemas import DomainResearchPlan, ResearchPerspective

from .fakes import make_plan


def candidate(
    paper_id: str,
    title: str,
    *,
    year: int,
    citations: int,
    survey_sources: list[str] | None = None,
    matched_queries: list[str] | None = None,
    abstract: str | None = None,
) -> PaperCandidate:
    return PaperCandidate(
        paper_id=paper_id,
        title=title,
        abstract=abstract or "retrieval augmented generation methods architectures evaluation benchmark",
        year=year,
        url=f"https://example.org/{paper_id}",
        citation_count=citations,
        source="semantic_scholar",
        survey_source_ids=survey_sources or [],
        matched_queries=matched_queries or [],
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

    def test_candidate_queries_use_dynamic_terms_and_skip_generic_fallback_branches(self) -> None:
        plan = DomainResearchPlan(
            normalized_domain="多智能体",
            translated_domain="multi-agent systems",
            expanded_terms=["LLM-based multi-agent collaboration"],
            perspectives=[
                ResearchPerspective(name="基础", description="基础", questions=[]),
                ResearchPerspective(name="方法", description="方法", questions=[]),
                ResearchPerspective(name="评测", description="评测", questions=[]),
            ],
            search_queries=["multi-agent systems"],
            expected_subdirections=["理论与基础", "核心方法", "评测与前沿"],
        )

        queries = [query.query for query in self.policy.queries(plan)]

        self.assertTrue(any("LLM-based multi-agent collaboration" in query for query in queries))
        self.assertTrue(all('"multi-agent systems"' in query for query in queries))
        self.assertFalse(any("theoretical foundations" in query for query in queries))

    def test_query_validation_keeps_only_queries_with_real_survey_results(self) -> None:
        good = '"LLM-based multi-agent systems" survey systematic review taxonomy'
        bad = '"multi-agent systems" survey systematic review taxonomy'
        queries = [
            self.policy.queries(self.plan)[0].model_copy(update={"query": good}),
            self.policy.queries(self.plan)[0].model_copy(update={"query": bad}),
        ]
        papers = [
            candidate(
                "survey",
                "A Survey of LLM-Based Multi-Agent Systems",
                year=2025,
                citations=50,
                matched_queries=[good],
            ),
            candidate(
                "method",
                "Multi-Agent Coordination Method",
                year=2024,
                citations=10,
                matched_queries=[bad],
            ),
        ]

        selected, audit = self.policy.validate_queries(queries, papers, self.plan)

        self.assertEqual([query.query for query in selected], [good])
        self.assertEqual(sum(item["selected"] for item in audit), 1)
        self.assertEqual(next(item for item in audit if item["query"] == bad)["reason"], "no_verified_survey")

    def test_real_paper_text_bootstraps_long_tail_query_terms(self) -> None:
        plan = DomainResearchPlan(
            normalized_domain="多智能体",
            translated_domain="multi-agent systems",
            perspectives=[
                ResearchPerspective(name="基础", description="基础", questions=[]),
                ResearchPerspective(name="方法", description="方法", questions=[]),
                ResearchPerspective(name="评测", description="评测", questions=[]),
            ],
            search_queries=["multi-agent systems"],
            expected_subdirections=["理论与基础", "核心方法", "评测与前沿"],
        )
        papers = [
            candidate(
                "camel",
                "LLM Based Multi Agent Collaboration",
                year=2023,
                citations=100,
                abstract="Large language models enable LLM based multi agent collaboration.",
            ),
            candidate(
                "autogen",
                "Large Language Model Multi Agent Conversation",
                year=2023,
                citations=100,
                abstract="LLM based multi agent collaboration supports autonomous agents.",
            ),
        ]

        terms = self.policy.discover_terms(plan, papers)

        self.assertTrue(any("multi agent" in term for term in terms), terms)

        queries, discovered_terms = self.policy.discovered_queries(plan, papers)
        self.assertEqual(discovered_terms, terms)
        self.assertTrue(queries)
        self.assertTrue(all('"multi-agent systems"' in query.query for query in queries))

    def test_chinese_only_fallback_plan_stops_survey_queries(self) -> None:
        plan = DomainResearchPlan(
            normalized_domain="具身智能",
            translated_domain="具身智能",
            expanded_terms=["具身智能"],
            perspectives=[
                ResearchPerspective(name="基础", description="基础", questions=[]),
                ResearchPerspective(name="方法", description="方法", questions=[]),
                ResearchPerspective(name="评测", description="评测", questions=[]),
            ],
            search_queries=["具身智能"],
            expected_subdirections=["理论与基础", "核心方法", "评测与前沿"],
            planning_mode="fallback",
        )

        queries = self.policy.queries(plan)

        self.assertEqual(queries, [])

    def test_chinese_only_domain_does_not_infer_an_english_anchor_from_results(self) -> None:
        plan = DomainResearchPlan(
            normalized_domain="具身智能",
            translated_domain="具身智能",
            perspectives=[
                ResearchPerspective(name="基础", description="基础", questions=[]),
                ResearchPerspective(name="方法", description="方法", questions=[]),
                ResearchPerspective(name="评测", description="评测", questions=[]),
            ],
            search_queries=["具身智能"],
            expected_subdirections=["理论与基础", "核心方法", "评测与前沿"],
        )
        papers = [
            candidate(
                "survey-a",
                "A Comprehensive Survey on Embodied AI",
                year=2025,
                citations=20,
                abstract="Embodied AI connects perception planning and robot action.",
            ),
            candidate(
                "survey-b",
                "Embodied AI with Foundation Models: A Systematic Review",
                year=2025,
                citations=10,
                abstract="Embodied AI foundation models support robot action.",
            ),
        ]

        terms = self.policy.discover_terms(plan, papers)

        self.assertEqual(terms, [])

    def test_verified_evidence_survey_can_be_revalidated_for_recommendation(self) -> None:
        papers = [
            candidate(
                "survey",
                "A Comprehensive Survey on Embodied AI",
                year=2025,
                citations=20,
                abstract="Embodied AI connects perception planning and robot action.",
            ),
            candidate(
                "method",
                "An Embodied Agent Method",
                year=2025,
                citations=5,
            ),
        ]

        surveys = self.policy.evidence_survey_candidates(papers)

        self.assertEqual([paper.paper_id for paper in surveys], ["survey"])

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

    def test_default_policy_caps_display_list_at_three_surveys_and_three_references(self) -> None:
        config = DomainOnboardingConfig(enforce_core_paper_coverage=False)
        policy = SurveyRecommendationPolicy(WeightedPaperRanker(config), config)
        surveys, _ = policy.select_surveys(
            [
                candidate(
                    f"survey-{index}",
                    f"Retrieval Augmented Generation Survey {index}",
                    year=2025,
                    citations=100 - index,
                )
                for index in range(5)
            ],
            self.plan,
            language="zh-CN",
        )
        references, _ = policy.select_references(
            [
                candidate(
                    f"reference-{index}",
                    f"Retrieval Augmented Generation Method {index}",
                    year=2023,
                    citations=80 - index,
                    survey_sources=[f"survey-{index % 3}"],
                )
                for index in range(7)
            ],
            self.plan,
            language="zh-CN",
        )

        self.assertEqual(len(surveys), 3)
        self.assertEqual(len(references), 3)
        self.assertTrue(all(paper.recommendation_category for paper in surveys))
        self.assertTrue(
            all(paper.recommendation_category == "survey_reference" for paper in references)
        )


if __name__ == "__main__":
    unittest.main()
