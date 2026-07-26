from __future__ import annotations

import unittest
from typing import Any

from handlers.domain_onboarding.config import DomainOnboardingConfig
from handlers.domain_onboarding.planner import StormLitePlanner
from handlers.domain_onboarding.ranking import WeightedPaperRanker
from handlers.domain_onboarding.retrieval import (
    ArxivRetriever,
    CompositePaperRetriever,
    CrossrefRetriever,
    PaperRetrievalError,
    SemanticScholarRetriever,
)
from handlers.domain_onboarding.schemas import PaperCandidate, RetrievalResult, RetrievalStats

from .fakes import FakeJSONModel, make_candidates, make_plan, make_profile


class PlannerTests(unittest.TestCase):
    def test_valid_plan_uses_single_model_call(self) -> None:
        model = FakeJSONModel(
            [
                {
                    "normalized_domain": "retrieval-augmented generation",
                    "perspectives": [
                        {"name": "基础", "description": "问题定义", "questions": ["是什么"]},
                        {"name": "方法", "description": "方法演进", "questions": ["怎么做"]},
                        {"name": "评测", "description": "评测前沿", "questions": ["如何评测"]},
                    ],
                    "search_queries": [
                        "retrieval augmented generation survey",
                        "RAG foundational method",
                        "RAG benchmark recent advances",
                    ],
                    "expected_subdirections": ["retrieval", "generation", "evaluation"],
                }
            ]
        )
        planner = StormLitePlanner(model, DomainOnboardingConfig())
        result = planner.plan("RAG", make_profile())
        plan = result.plan
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(len(plan.perspectives), 3)
        self.assertIn("survey", plan.search_queries[0])

    def test_invalid_model_output_falls_back_without_fabricating_papers(self) -> None:
        planner = StormLitePlanner(FakeJSONModel(["not json"]), DomainOnboardingConfig())
        result = planner.plan("图神经网络", make_profile())
        plan = result.plan
        self.assertGreaterEqual(len(plan.perspectives), 3)
        self.assertTrue(any("graph neural networks" in query for query in plan.search_queries))
        self.assertEqual(result.stats.model_calls, 1)
        self.assertEqual(result.stats.total_tokens, 50)


class FakeResponse:
    def __init__(
        self,
        payload: dict[str, Any],
        error: Exception | None = None,
        text: str = "",
    ):
        self.payload = payload
        self.error = error
        self.text = text

    def raise_for_status(self) -> None:
        if self.error:
            raise self.error

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeHTTPClient:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]


class RetrievalTests(unittest.TestCase):
    def test_crossref_response_converts_to_candidate(self) -> None:
        client = FakeHTTPClient(
            [
                FakeResponse(
                    {
                        "message": {
                            "items": [
                                {
                                    "DOI": "10.1000/rag",
                                    "title": ["Grounded Retrieval"],
                                    "author": [{"given": "Ada", "family": "Lovelace"}],
                                    "published-online": {"date-parts": [[2025, 1, 2]]},
                                    "URL": "https://doi.org/10.1000/rag",
                                    "is-referenced-by-count": 7,
                                    "type": "journal-article",
                                }
                            ]
                        }
                    }
                )
            ]
        )
        papers = CrossrefRetriever(client=client).search(["RAG"], limit_per_query=2).papers
        self.assertEqual(papers[0].paper_id, "doi:10.1000/rag")
        self.assertEqual(papers[0].authors, ["Ada Lovelace"])
        self.assertEqual(papers[0].year, 2025)
        self.assertEqual(papers[0].publication_types, ["journal-article"])

    def test_crossref_filters_non_paper_work_types(self) -> None:
        client = FakeHTTPClient(
            [
                FakeResponse(
                    {
                        "message": {
                            "items": [
                                {
                                    "DOI": "10.1000/grant",
                                    "title": ["Research Grant"],
                                    "URL": "https://doi.org/10.1000/grant",
                                    "type": "grant",
                                },
                                {
                                    "DOI": "10.1000/paper",
                                    "title": ["Research Paper"],
                                    "URL": "https://doi.org/10.1000/paper",
                                    "type": "proceedings-article",
                                },
                            ]
                        }
                    }
                )
            ]
        )

        papers = CrossrefRetriever(client=client).search(["RAG"], limit_per_query=2).papers

        self.assertEqual([paper.paper_id for paper in papers], ["doi:10.1000/paper"])

    def test_arxiv_atom_feed_converts_to_candidate(self) -> None:
        feed = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <id>https://arxiv.org/abs/2401.00001v2</id>
            <title> A Grounded RAG Paper </title>
            <summary> Retrieval evidence. </summary>
            <published>2024-01-02T00:00:00Z</published>
            <author><name>Ada Lovelace</name></author>
            <link rel="alternate" href="https://arxiv.org/abs/2401.00001v2" />
          </entry>
        </feed>"""
        retriever = ArxivRetriever(client=FakeHTTPClient([FakeResponse({}, text=feed)]))
        papers = retriever.search(["RAG"], limit_per_query=2).papers
        self.assertEqual(papers[0].paper_id, "arxiv:2401.00001")
        self.assertEqual(papers[0].year, 2024)
        self.assertEqual(papers[0].authors, ["Ada Lovelace"])

    def test_semantic_scholar_response_converts_to_candidate(self) -> None:
        client = FakeHTTPClient(
            [
                FakeResponse(
                    {
                        "data": [
                            {
                                "paperId": "abc",
                                "title": "A Real Paper",
                                "abstract": "Evidence",
                                "year": 2024,
                                "url": "https://example.org/abc",
                                "citationCount": 12,
                                "authors": [{"name": "Ada"}],
                                "externalIds": {"DOI": "10.1000/abc", "ArXiv": "2401.00001"},
                                "publicationTypes": ["JournalArticle"],
                            }
                        ]
                    }
                )
            ]
        )
        retriever = SemanticScholarRetriever(client=client)
        papers = retriever.search(["query"], limit_per_query=5).papers
        self.assertEqual(papers[0].paper_id, "abc")
        self.assertEqual(papers[0].matched_queries, ["query"])
        self.assertEqual(papers[0].publication_types, ["JournalArticle"])
        self.assertEqual(client.calls[0]["params"]["limit"], 5)

    def test_single_query_failure_does_not_abort_other_queries(self) -> None:
        import httpx

        client = FakeHTTPClient(
            [
                FakeResponse({}, httpx.HTTPError("temporary")),
                FakeResponse({"data": [{"paperId": "ok", "title": "Valid", "url": "https://x/ok", "authors": []}]}),
            ]
        )
        retriever = SemanticScholarRetriever(client=client)
        result = retriever.search(["bad", "good"], limit_per_query=2)
        self.assertEqual([paper.paper_id for paper in result.papers], ["ok"])
        self.assertEqual(len(result.stats.errors), 1)

    def test_all_query_failures_raise_clear_error(self) -> None:
        import httpx

        retriever = SemanticScholarRetriever(
            client=FakeHTTPClient([FakeResponse({}, httpx.HTTPError("down"))])
        )
        with self.assertRaises(PaperRetrievalError):
            retriever.search(["a", "b"], limit_per_query=2)

    def test_composite_keeps_results_when_one_source_fails(self) -> None:
        class Failing:
            def search(self, queries: list[str], *, limit_per_query: int) -> RetrievalResult:
                raise PaperRetrievalError(
                    "failed",
                    stats=RetrievalStats(errors=["rate limited"]),
                )

        class Working:
            def search(self, queries: list[str], *, limit_per_query: int) -> RetrievalResult:
                return RetrievalResult(papers=make_candidates(1))

        retriever = CompositePaperRetriever([Failing(), Working()])
        result = retriever.search(["RAG"], limit_per_query=2)
        self.assertEqual(len(result.papers), 1)
        self.assertTrue(result.stats.errors)


class RankingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ranker = WeightedPaperRanker(DomainOnboardingConfig())

    def test_deduplicates_by_id_and_title(self) -> None:
        papers = make_candidates(3)
        duplicate = papers[0].model_copy(update={"paper_id": "different-id", "matched_queries": ["second"]})
        result = self.ranker.rank([*papers, duplicate], make_plan(), limit=10)
        titles = [paper.title for paper in result.papers]
        self.assertEqual(titles.count(papers[0].title), 1)
        self.assertEqual(result.stats.deduplicated_count, 3)

    def test_invalid_url_is_filtered(self) -> None:
        invalid = PaperCandidate(
            paper_id="bad",
            title="Bad",
            url="not-a-url",
            source="fake",
        )
        result = self.ranker.rank([invalid, *make_candidates(2)], make_plan(), limit=10)
        self.assertNotIn("bad", [paper.paper_id for paper in result.papers])
        self.assertEqual(result.stats.invalid_count, 1)

    def test_scores_are_bounded_and_explainable(self) -> None:
        ranked = self.ranker.rank(make_candidates(), make_plan(), limit=6).papers
        self.assertTrue(ranked)
        for paper in ranked:
            self.assertGreaterEqual(paper.final_score, 0.0)
            self.assertLessEqual(paper.final_score, 1.0)
            self.assertIn(paper.paper_role, {"survey", "foundational", "method", "evaluation", "frontier", "other"})

    def test_candidate_limit_is_shared_fairly_across_sources(self) -> None:
        config = DomainOnboardingConfig(candidate_paper_limit=6, selected_paper_limit=6)
        ranker = WeightedPaperRanker(config)
        papers = [
            PaperCandidate(
                paper_id=f"{source}-{index}",
                title=f"RAG method from {source} number {index}",
                abstract="retrieval augmented generation method",
                year=2024,
                url=f"https://example.org/{source}/{index}",
                source=source,
            )
            for source, count in (("source_a", 8), ("source_b", 2), ("source_c", 2))
            for index in range(count)
        ]

        result = ranker.rank(papers, make_plan(), limit=6)

        self.assertEqual(
            result.stats.candidate_source_counts,
            {"source_a": 2, "source_b": 2, "source_c": 2},
        )

    def test_source_specific_identity_mismatch_is_filtered(self) -> None:
        papers = [
            PaperCandidate(
                paper_id="wrong-crossref-id",
                title="Crossref Paper",
                url="https://doi.org/10.1000/paper",
                source="crossref",
                doi="10.1000/paper",
            ),
            PaperCandidate(
                paper_id="wrong-arxiv-id",
                title="arXiv Paper",
                url="https://arxiv.org/abs/2401.00001",
                source="arxiv",
                arxiv_id="2401.00001",
            ),
            PaperCandidate(
                paper_id="doi:10.1000/other",
                title="Crossref URL mismatch",
                url="https://doi.org/10.1000/different",
                source="crossref",
                doi="10.1000/other",
            ),
            PaperCandidate(
                paper_id="valid",
                title="Valid Semantic Scholar Paper",
                url="https://example.org/valid",
                source="semantic_scholar",
                publication_types=["JournalArticle"],
            ),
        ]

        result = self.ranker.rank(papers, make_plan(), limit=3)

        self.assertEqual([paper.paper_id for paper in result.papers], ["valid"])
        self.assertEqual(result.stats.invalid_count, 3)

    def test_non_paper_semantic_scholar_type_is_filtered(self) -> None:
        paper = PaperCandidate(
            paper_id="dataset-1",
            title="RAG Dataset",
            url="https://example.org/dataset-1",
            source="semantic_scholar",
            publication_types=["Dataset"],
        )

        result = self.ranker.rank([paper], make_plan(), limit=1)
        self.assertEqual(result.papers, [])
        self.assertEqual(result.stats.invalid_count, 1)


if __name__ == "__main__":
    unittest.main()
