from __future__ import annotations

import unittest
from threading import Barrier

import httpx

from handlers.domain_onboarding.retrieval import (
    ArxivRetriever,
    CompositePaperRetriever,
    PaperRetrievalError,
    SemanticScholarRetriever,
)
from handlers.domain_onboarding.retrieval_resilience import RetrievalRetryPolicy
from handlers.domain_onboarding.schemas import PaperCandidate, RetrievalResult, RetrievalStats


def semantic_scholar_payload(paper_id: str = "paper-1") -> dict:
    return {
        "data": [
            {
                "paperId": paper_id,
                "title": "Reliable Retrieval",
                "authors": [{"name": "Ada"}],
                "abstract": "retrieval reliability",
                "year": 2025,
                "url": f"https://www.semanticscholar.org/paper/{paper_id}",
                "citationCount": 10,
                "externalIds": {},
            }
        ]
    }


class RetrievalRetryTests(unittest.TestCase):
    def test_semantic_scholar_reference_expansion_records_survey_source(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "GET")
            self.assertIn("/references", str(request.url))
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "citedPaper": {
                                "paperId": "method-paper",
                                "title": "A Grounded Retrieval Method",
                                "abstract": "retrieval augmented generation method",
                                "year": 2024,
                                "url": "https://www.semanticscholar.org/paper/method-paper",
                                "citationCount": 12,
                                "authors": [{"name": "Ada"}],
                                "externalIds": {},
                                "publicationTypes": ["JournalArticle"],
                            }
                        }
                    ]
                },
            )

        survey = PaperCandidate(
            paper_id="survey-paper",
            title="A Survey",
            url="https://www.semanticscholar.org/paper/survey-paper",
            source="semantic_scholar",
        )
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            retriever = SemanticScholarRetriever(client=client)
            references, stats = retriever.fetch_references([survey])

        self.assertEqual(stats.request_count, 1)
        self.assertEqual(len(references), 1)
        self.assertEqual(references[0].survey_source_ids, ["survey-paper"])
        self.assertEqual(references[0].citation_count, 12)

    def test_citation_enrichment_failure_preserves_original_papers(self) -> None:
        class FailingCitationProvider:
            source_name = "semantic_scholar"

            def enrich_citations(self, papers, *, batch_size):
                raise httpx.ConnectError("temporary outage")

        paper = PaperCandidate(
            paper_id="arxiv:2501.00003",
            title="Preserved paper",
            url="https://arxiv.org/abs/2501.00003",
            source="arxiv",
            arxiv_id="2501.00003",
        )
        retriever = CompositePaperRetriever([FailingCitationProvider()])

        result = retriever.enrich_citations([paper])

        self.assertEqual([item.paper_id for item in result.papers], [paper.paper_id])
        self.assertEqual(result.papers[0].citation_status, "unknown")
        self.assertEqual(result.stats.source_failure_count, 1)
        self.assertIn("temporary outage", result.stats.errors[0])

    def test_citation_enrichment_distinguishes_known_zero_from_unknown(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            self.assertEqual(request.method, "POST")
            return httpx.Response(
                200,
                json=[
                    {
                        "paperId": "zero-paper",
                        "citationCount": 0,
                        "influentialCitationCount": 0,
                        "referenceCount": 12,
                    },
                    None,
                ],
            )

        papers = [
            PaperCandidate(
                paper_id="arxiv:2501.00001",
                title="New paper",
                url="https://arxiv.org/abs/2501.00001",
                source="arxiv",
                arxiv_id="2501.00001",
            ),
            PaperCandidate(
                paper_id="arxiv:2501.00002",
                title="Unknown paper",
                url="https://arxiv.org/abs/2501.00002",
                source="arxiv",
                arxiv_id="2501.00002",
            ),
        ]
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            retriever = SemanticScholarRetriever(client=client)
            enriched, stats = retriever.enrich_citations(papers, batch_size=50)
            cached, cached_stats = retriever.enrich_citations(papers, batch_size=50)

        self.assertEqual(len(requests), 2)
        self.assertEqual(stats.request_count, 1)
        self.assertEqual(enriched[0].citation_count, 0)
        self.assertEqual(enriched[0].citation_status, "known")
        self.assertEqual(enriched[0].citation_source, "semantic_scholar")
        self.assertEqual(enriched[0].reference_count, 12)
        self.assertIsNone(enriched[1].citation_count)
        self.assertEqual(enriched[1].citation_status, "unknown")
        self.assertEqual(cached[0].citation_status, "known")
        self.assertEqual(cached_stats.cache_hit_count, 1)

    def test_retries_429_and_honors_retry_after(self) -> None:
        calls = 0
        sleeps: list[float] = []

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(429, headers={"Retry-After": "0.25"})
            return httpx.Response(200, json=semantic_scholar_payload())

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            retriever = SemanticScholarRetriever(
                client=client,
                retry_policy=RetrievalRetryPolicy(max_attempts=2, base_backoff_seconds=0.1),
                cache_ttl_seconds=0,
                sleep_func=sleeps.append,
            )
            result = retriever.search(["RAG"], limit_per_query=2)

        self.assertEqual(len(result.papers), 1)
        self.assertEqual(calls, 2)
        self.assertEqual(result.stats.retry_count, 1)
        self.assertEqual(result.stats.rate_limit_count, 1)
        self.assertEqual(sleeps, [0.25])

    def test_does_not_retry_non_retriable_client_error(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(400, json={"error": "bad query"})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            retriever = SemanticScholarRetriever(
                client=client,
                retry_policy=RetrievalRetryPolicy(max_attempts=3, base_backoff_seconds=0),
                cache_ttl_seconds=0,
            )
            with self.assertRaises(PaperRetrievalError) as raised:
                retriever.search(["bad"], limit_per_query=2)

        self.assertEqual(calls, 1)
        self.assertEqual(raised.exception.stats.retry_count, 0)

    def test_query_cache_avoids_duplicate_external_request(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json=semantic_scholar_payload())

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            retriever = SemanticScholarRetriever(client=client, cache_ttl_seconds=60)
            first = retriever.search(["RAG"], limit_per_query=2)
            first.papers[0].title = "caller mutation"
            second = retriever.search(["RAG"], limit_per_query=2)

        self.assertEqual(calls, 1)
        self.assertEqual(second.stats.cache_hit_count, 1)
        self.assertEqual(second.papers[0].title, "Reliable Retrieval")

    def test_arxiv_enforces_minimum_interval_between_queries(self) -> None:
        now = [0.0]
        sleeps: list[float] = []
        feed = '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'

        def clock() -> float:
            return now[0]

        def fake_sleep(delay: float) -> None:
            sleeps.append(delay)
            now[0] += delay

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=feed)

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            retriever = ArxivRetriever(
                client=client,
                cache_ttl_seconds=0,
                min_interval_seconds=3,
                sleep_func=fake_sleep,
                clock=clock,
            )
            result = retriever.search(["one", "two"], limit_per_query=1)

        self.assertEqual(result.papers, [])
        self.assertEqual(result.stats.request_count, 2)
        self.assertEqual(sleeps, [3.0])

    def test_stale_cache_is_used_when_live_provider_fails(self) -> None:
        now = [0.0]
        should_fail = [False]

        def clock() -> float:
            return now[0]

        def handler(request: httpx.Request) -> httpx.Response:
            if should_fail[0]:
                return httpx.Response(503, json={"error": "offline"})
            return httpx.Response(200, json=semantic_scholar_payload("cached-paper"))

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            provider = SemanticScholarRetriever(
                client=client,
                retry_policy=RetrievalRetryPolicy(max_attempts=1),
                cache_ttl_seconds=1,
                clock=clock,
            )
            retriever = CompositePaperRetriever(
                [provider],
                stale_cache_seconds=60,
                clock=clock,
            )
            fresh = retriever.search(["RAG"], limit_per_query=1)
            now[0] = 2.0
            should_fail[0] = True
            stale = retriever.search(["RAG"], limit_per_query=1)

        self.assertEqual(fresh.papers[0].paper_id, "cached-paper")
        self.assertEqual(stale.papers[0].paper_id, "cached-paper")
        self.assertEqual(stale.stats.stale_cache_hit_count, 1)
        self.assertTrue(stale.stats.providers["semantic_scholar"].stale_cache_used)
        self.assertFalse(stale.stats.providers["semantic_scholar"].success)

    def test_circuit_opens_and_allows_probe_after_cooldown(self) -> None:
        now = [0.0]

        class FlakyRetriever:
            source_name = "flaky"

            def __init__(self) -> None:
                self.calls = 0
                self.recovered = False

            def search(self, queries: list[str], *, limit_per_query: int) -> RetrievalResult:
                self.calls += 1
                if not self.recovered:
                    raise PaperRetrievalError(
                        "offline",
                        stats=RetrievalStats(errors=["offline"], request_count=1),
                    )
                return RetrievalResult(
                    papers=[
                        PaperCandidate(
                            paper_id="recovered",
                            title="Recovered provider",
                            url="https://example.org/recovered",
                            source="flaky",
                        )
                    ],
                    stats=RetrievalStats(request_count=1),
                )

        provider = FlakyRetriever()
        retriever = CompositePaperRetriever(
            [provider],
            circuit_failure_threshold=2,
            circuit_cooldown_seconds=10,
            stale_cache_seconds=0,
            clock=lambda: now[0],
        )

        with self.assertRaises(PaperRetrievalError):
            retriever.search(["query"], limit_per_query=1)
        with self.assertRaises(PaperRetrievalError) as opened:
            retriever.search(["query"], limit_per_query=1)
        with self.assertRaises(PaperRetrievalError) as skipped:
            retriever.search(["query"], limit_per_query=1)

        self.assertTrue(opened.exception.stats.providers["flaky"].circuit_open)
        self.assertTrue(skipped.exception.stats.providers["flaky"].circuit_skipped)
        self.assertEqual(provider.calls, 2)

        now[0] = 10.0
        provider.recovered = True
        recovered = retriever.search(["query"], limit_per_query=1)

        self.assertEqual(provider.calls, 3)
        self.assertTrue(recovered.stats.providers["flaky"].success)
        self.assertFalse(recovered.stats.providers["flaky"].circuit_open)


class CompositeConcurrencyTests(unittest.TestCase):
    def test_sources_execute_concurrently_and_merge_in_declared_order(self) -> None:
        barrier = Barrier(2)

        class BarrierRetriever:
            def __init__(self, paper_id: str) -> None:
                self.paper_id = paper_id

            def search(self, queries: list[str], *, limit_per_query: int) -> RetrievalResult:
                barrier.wait(timeout=2)
                return RetrievalResult(
                    papers=[PaperCandidate(
                        paper_id=self.paper_id,
                        title=f"Paper {self.paper_id}",
                        url=f"https://example.org/{self.paper_id}",
                        source=self.paper_id,
                    )],
                    stats=RetrievalStats(request_count=1),
                )

        retriever = CompositePaperRetriever(
            [BarrierRetriever("first"), BarrierRetriever("second")],
            max_workers=2,
        )
        result = retriever.search(["query"], limit_per_query=1)

        self.assertEqual([paper.paper_id for paper in result.papers], ["first", "second"])
        self.assertEqual(result.stats.source_success_count, 2)
        self.assertEqual(result.stats.source_failure_count, 0)
        self.assertEqual(result.stats.request_count, 2)

    def test_sources_are_interleaved_instead_of_concatenated(self) -> None:
        class BatchRetriever:
            def __init__(self, source: str, count: int) -> None:
                self.source = source
                self.count = count
            def search(self, queries: list[str], *, limit_per_query: int) -> RetrievalResult:
                return RetrievalResult(papers=[
                    PaperCandidate(
                        paper_id=f"{self.source}-{index}",
                        title=f"{self.source} paper {index}",
                        url=f"https://example.org/{self.source}/{index}",
                        source=self.source,
                    )
                    for index in range(self.count)
                ])

        retriever = CompositePaperRetriever(
            [BatchRetriever("semantic", 3), BatchRetriever("arxiv", 2)],
        )
        papers = retriever.search(["query"], limit_per_query=3).papers

        self.assertEqual(
            [paper.paper_id for paper in papers],
            ["semantic-0", "arxiv-0", "semantic-1", "arxiv-1", "semantic-2"],
        )


if __name__ == "__main__":
    unittest.main()
