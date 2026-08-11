from __future__ import annotations

import json
import unittest
from pathlib import Path

from handlers.domain_onboarding.config import DomainOnboardingConfig
from handlers.domain_onboarding.ranking import WeightedPaperRanker
from handlers.domain_onboarding.ranking_benchmark import evaluate_ranking
from handlers.domain_onboarding.schemas import (
    DomainResearchPlan,
    PaperCandidate,
    ResearchPerspective,
)
from handlers.domain_onboarding.text_similarity import CachedEmbeddingTextVectorizer


FIXTURE = Path(__file__).with_name("fixtures") / "ranking_benchmark.json"


class EmbeddingVectorizerTests(unittest.TestCase):
    def test_batches_deduplicates_and_caches_embeddings(self) -> None:
        class Provider:
            def __init__(self) -> None:
                self.batches: list[list[str]] = []

            def embed(self, texts: list[str]) -> list[list[float]]:
                self.batches.append(list(texts))
                return [[float(len(text)), 1.0] for text in texts]

        provider = Provider()
        vectorizer = CachedEmbeddingTextVectorizer(provider, batch_size=2)

        first = vectorizer.vectorize(["alpha", "beta", "alpha", "gamma"])
        second = vectorizer.vectorize(["gamma", "alpha"])

        self.assertEqual(provider.batches, [["alpha", "beta"], ["gamma"]])
        self.assertEqual(first[0], first[2])
        self.assertEqual(second, [first[3], first[0]])

    def test_ranker_falls_back_to_multilingual_tfidf_when_embedding_fails(self) -> None:
        class OfflineProvider:
            def embed(self, texts: list[str]) -> list[list[float]]:
                raise RuntimeError("embedding service offline")

        plan = self._plan("retrieval augmented generation", ["retrieval", "generation"])
        papers = [
            PaperCandidate(
                paper_id="rag",
                title="Retrieval Augmented Generation Method",
                abstract="retrieval augmented generation",
                year=2024,
                url="https://example.org/rag",
                source="test",
            )
        ]
        ranker = WeightedPaperRanker(
            DomainOnboardingConfig(),
            vectorizer=CachedEmbeddingTextVectorizer(OfflineProvider()),
        )

        result = ranker.rank(papers, plan, limit=1)

        self.assertEqual([paper.paper_id for paper in result.papers], ["rag"])
        self.assertTrue(result.stats.vectorizer_fallback_used)
        self.assertEqual(result.stats.vectorizer_backend, "multilingual_tfidf")

    @staticmethod
    def _plan(domain: str, subdirections: list[str]) -> DomainResearchPlan:
        return DomainResearchPlan(
            normalized_domain=domain,
            perspectives=[
                ResearchPerspective(name="foundation", description="foundation", questions=["what"]),
                ResearchPerspective(name="method", description="method", questions=["how"]),
                ResearchPerspective(name="evaluation", description="evaluation", questions=["measure"]),
            ],
            search_queries=[f"{domain} survey", f"{domain} benchmark"],
            expected_subdirections=[*subdirections, "frontier"][: max(3, len(subdirections))],
        )


class RankingBenchmarkTests(unittest.TestCase):
    def test_fixed_relevance_benchmark(self) -> None:
        cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
        ranker = WeightedPaperRanker(DomainOnboardingConfig())

        for case in cases:
            with self.subTest(case=case["name"]):
                plan = EmbeddingVectorizerTests._plan(
                    case["domain"],
                    case["subdirections"],
                ).model_copy(update={"search_queries": case["search_queries"]})
                papers = [
                    PaperCandidate(
                        **paper,
                        url=f"https://example.org/{paper['paper_id']}",
                        source="benchmark",
                    )
                    for paper in case["papers"]
                ]
                ranked = ranker.rank(papers, plan, limit=case["k"])
                metrics = evaluate_ranking(
                    ranked.papers,
                    case["relevance_grades"],
                    k=case["k"],
                )

                self.assertGreaterEqual(metrics.precision_at_k, case["minimum_precision"])
                self.assertGreaterEqual(metrics.ndcg_at_k, case["minimum_ndcg"])
                self.assertGreaterEqual(metrics.role_coverage, 2)


if __name__ == "__main__":
    unittest.main()
