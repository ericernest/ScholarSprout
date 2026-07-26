from __future__ import annotations

import unittest

from handlers.domain_onboarding.config import DomainOnboardingConfig
from handlers.domain_onboarding.ranking import WeightedPaperRanker
from handlers.domain_onboarding.schemas import PaperCandidate
from handlers.domain_onboarding.text_similarity import TfidfTextVectorizer, cosine_similarity

from .fakes import make_plan


class TextSimilarityTests(unittest.TestCase):
    def test_tfidf_cosine_prefers_related_text(self) -> None:
        vectors = TfidfTextVectorizer().vectorize(
            [
                "retrieval augmented generation",
                "retrieval augmented generation with evidence",
                "graph convolutional networks",
            ]
        )

        self.assertGreater(
            cosine_similarity(vectors[0], vectors[1]),
            cosine_similarity(vectors[0], vectors[2]),
        )


class MMRRankingTests(unittest.TestCase):
    def test_mmr_chooses_relevant_novel_paper_over_near_duplicate(self) -> None:
        class FixedVectorizer:
            def vectorize(self, texts: list[str]) -> list[dict[str, float]]:
                self.assert_text_count = len(texts)
                return [
                    {"topic": 1.0},
                    {"topic": 1.0},
                    {"topic": 0.99, "duplicate": 0.14},
                    {"topic": 0.8, "evaluation": 0.6},
                ]

        config = DomainOnboardingConfig(
            candidate_paper_limit=3,
            selected_paper_limit=2,
            relevance_weight=1.0,
            citation_weight=0.0,
            recency_weight=0.0,
            diversity_weight=0.0,
            mmr_lambda=0.4,
            mmr_role_bonus=0.0,
        )
        ranker = WeightedPaperRanker(config, vectorizer=FixedVectorizer())
        papers = [
            PaperCandidate(
                paper_id=paper_id,
                title=title,
                abstract=abstract,
                url=f"https://example.org/{paper_id}",
                source="test",
            )
            for paper_id, title, abstract in (
                ("primary", "RAG Method", "retrieval augmented generation"),
                ("duplicate", "RAG Method Variant", "retrieval augmented generation"),
                ("evaluation", "RAG Evaluation", "evaluation and factuality"),
            )
        ]

        result = ranker.rank(papers, make_plan(), limit=2)

        self.assertEqual(
            [paper.paper_id for paper in result.papers],
            ["primary", "evaluation"],
        )
        self.assertEqual(set(result.stats.mmr_scores), {"primary", "evaluation"})

    def test_diversity_scores_do_not_depend_on_input_order(self) -> None:
        papers = [
            PaperCandidate(
                paper_id=f"paper-{index}",
                title=title,
                abstract=abstract,
                url=f"https://example.org/{index}",
                source="test",
            )
            for index, (title, abstract) in enumerate(
                (
                    ("RAG Retrieval", "dense retrieval evidence"),
                    ("RAG Generation", "grounded text generation"),
                    ("RAG Evaluation", "factuality benchmark evaluation"),
                )
            )
        ]
        ranker = WeightedPaperRanker(DomainOnboardingConfig(selected_paper_limit=3))

        forward = {
            paper.paper_id: paper.diversity_score
            for paper in ranker.rank(papers, make_plan(), limit=3).papers
        }
        reverse = {
            paper.paper_id: paper.diversity_score
            for paper in ranker.rank(list(reversed(papers)), make_plan(), limit=3).papers
        }

        self.assertEqual(forward, reverse)


if __name__ == "__main__":
    unittest.main()
