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
from handlers.domain_onboarding.schemas import PaperCandidate


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
            papers = retriever.search(["RAG"], limit_per_query=2)

        self.assertEqual(len(papers), 1)
        self.assertEqual(calls, 2)
        self.assertEqual(retriever.last_retry_count, 1)
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
            with self.assertRaises(PaperRetrievalError):
                retriever.search(["bad"], limit_per_query=2)

        self.assertEqual(calls, 1)
        self.assertEqual(retriever.last_retry_count, 0)

    def test_query_cache_avoids_duplicate_external_request(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json=semantic_scholar_payload())

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            retriever = SemanticScholarRetriever(client=client, cache_ttl_seconds=60)
            first = retriever.search(["RAG"], limit_per_query=2)
            first[0].title = "caller mutation"
            second = retriever.search(["RAG"], limit_per_query=2)

        self.assertEqual(calls, 1)
        self.assertEqual(retriever.last_cache_hits, 1)
        self.assertEqual(second[0].title, "Reliable Retrieval")

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
            self.assertEqual(retriever.search(["one", "two"], limit_per_query=1), [])

        self.assertEqual(retriever.last_request_count, 2)
        self.assertEqual(sleeps, [3.0])


class CompositeConcurrencyTests(unittest.TestCase):
    def test_sources_execute_concurrently_and_merge_in_declared_order(self) -> None:
        barrier = Barrier(2)

        class BarrierRetriever:
            def __init__(self, paper_id: str) -> None:
                self.paper_id = paper_id
                self.last_errors: list[str] = []
                self.last_retry_count = 0
                self.last_cache_hits = 0
                self.last_request_count = 1

            def search(self, queries: list[str], *, limit_per_query: int) -> list[PaperCandidate]:
                barrier.wait(timeout=2)
                return [
                    PaperCandidate(
                        paper_id=self.paper_id,
                        title=f"Paper {self.paper_id}",
                        url=f"https://example.org/{self.paper_id}",
                        source=self.paper_id,
                    )
                ]

        retriever = CompositePaperRetriever(
            [BarrierRetriever("first"), BarrierRetriever("second")],
            max_workers=2,
        )
        papers = retriever.search(["query"], limit_per_query=1)

        self.assertEqual([paper.paper_id for paper in papers], ["first", "second"])
        self.assertEqual(retriever.last_source_success_count, 2)
        self.assertEqual(retriever.last_source_failure_count, 0)
        self.assertEqual(retriever.last_request_count, 2)


if __name__ == "__main__":
    unittest.main()
